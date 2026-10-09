"""原文剪報：把文章裡的一句（或幾句）排成一張剪報圖（PNG），給 Vox 分鏡貼上桌面。

規則：
- 剪到整句：引文（quote）落在哪幾句，就剪那幾句——從第一句的開頭到最後一句的句號（。！？，後面的」』）也算）。
  沒寫引文＝整段。
- 只排剪下來的這幾句，從行首開始、每行靠左（字級、最大行寬固定）。行數用最少的，各行盡量一樣長、能在標點後面斷就在
  標點斷（不會第一行只剩兩個字、把一個詞拆成兩半）；框要找的字（keep）和短的「…」詞不拆到兩行。
  （以前照整段排、再把同一行別句的殘字遮掉：第一行的字擠在右邊，看起來像靠右對齊、版面破掉——真實試跑 #8。）
- 圖用外掛附帶的字型（Noto Sans CJK TC）畫，2 倍解析度，相機推近也清楚。檔名是內容雜湊，內容沒變就不重畫。
- 太長（超過 MAX_LINES 行）擋下：剪報是拿來指證據的，一兩句就好。

框（box 的 find）：在剪報裡找那幾個字，回傳它們在圖上的像素範圍（char_rect）。
"""

from __future__ import annotations

import functools
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PIL import __version__ as PIL_VERSION

from article_to_video.clips import TEXT_FONT

__all__ = ["MAX_LINES", "Excerpt", "ExcerptError", "make_excerpt", "sentence_span"]

EXCERPT_VERSION = "2"
"""2：只排剪下來的句子、靠左、平均分行（1 是整段排版再遮殘字）。"""
FONT_FILE = TEXT_FONT
FONT_PX = 88
LINE_PX = 150
MAX_LINE_PX = 20 * FONT_PX
"""一行最寬 20 個全形字。"""
PAD_X, PAD_Y = 96, 64
MAX_LINES = 6
PAPER = (251, 249, 244)
INK = (34, 34, 34)

SENTENCE_END = "。！？!?"
CLOSERS = "」』”’）)〕】》"
NO_LINE_START = "，。、．！？；：」』”’）)〕】》%％…‥・ー"
"""不放行首的字（排不下就掛在上一行行尾）。"""
NO_LINE_END = "「『“‘（(〔【《"
"""不放行尾的字（移到下一行）。"""


class ExcerptError(ValueError):
    """剪不出來（繁中說明）。"""


@dataclass(frozen=True)
class Excerpt:
    paragraph: int
    text: str
    """剪下來的句子（圖上看得到的字）。"""
    start: int
    end: int
    """在段落文字裡的範圍 [start, end)。"""
    image: Path
    width: int
    height: int
    boxes: dict[int, tuple[int, int, int, int]] = field(default_factory=dict)
    """段落字元位置 → 那個字在圖上的範圍 (x0, y0, x1, y1)，只有看得到的字。"""

    def char_rect(self, find: str, occurrence: int | None = None) -> tuple[int, int, int, int]:
        """剪報裡某幾個字在圖上的範圍 (x, y, w, h)。找不到、出現多次又沒指定第幾次、被換行切開 → ExcerptError。"""
        hits = [i for i in range(len(self.text)) if self.text.startswith(find, i)]
        if not hits:
            raise ExcerptError(f"剪報裡沒有「{find}」（剪到的是：{self.text}）")
        if occurrence is None and len(hits) > 1:
            raise ExcerptError(f"「{find}」在剪報裡出現 {len(hits)} 次，加 \"occurrence\": 1～{len(hits)} 指定第幾次")
        if occurrence is not None and not 1 <= occurrence <= len(hits):
            raise ExcerptError(f"「{find}」在剪報裡只出現 {len(hits)} 次，occurrence 要是 1～{len(hits)}")
        first = self.start + hits[(occurrence or 1) - 1]
        boxes = [self.boxes[i] for i in range(first, first + len(find)) if i in self.boxes]
        if len({b[1] for b in boxes}) > 1:
            raise ExcerptError(f"「{find}」在剪報裡被換行切成兩半，框不起來：框短一點（例如只框數字）")
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        return x0, y0, x1 - x0, y1 - y0

    def line_rect(self, find: str, occurrence: int | None = None) -> tuple[int, int, int, int]:
        """find 所在的那一整行在圖上的範圍 (x, y, w, h)：框推近時這一行要整行留在畫面裡（真實試跑 #6）。"""
        _, y, _, _ = self.char_rect(find, occurrence)
        row = [b for b in self.boxes.values() if b[1] == y]
        x0, x1 = min(b[0] for b in row), max(b[2] for b in row)
        return x0, y, x1 - x0, max(b[3] for b in row) - y


def sentence_span(text: str, q0: int, q1: int) -> tuple[int, int]:
    """[q0, q1) 擴大到整句：從 q0 所在那句的開頭，到 q1−1 所在那句的結尾（句號與後面的」』）也算）。"""
    ends: list[int] = []
    i = 0
    while i < len(text):
        if text[i] in SENTENCE_END:
            j = i + 1
            while j < len(text) and (text[j] in CLOSERS or text[j] in SENTENCE_END):
                j += 1
            ends.append(j)
            i = j
        else:
            i += 1
    if not ends or ends[-1] < len(text):
        ends.append(len(text))
    s = max([0] + [e for e in ends if e <= q0])
    e = min(e for e in ends if e >= q1)
    while s < e and text[s].isspace():
        s += 1
    return s, e


@functools.cache
def _font() -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_FILE), FONT_PX)


@functools.cache
def _font_digest() -> str:
    return hashlib.sha256(FONT_FILE.read_bytes()).hexdigest()


def _tokens(text: str) -> list[tuple[int, int]]:
    """斷行單位 [i, j)：英數（含小數點、%、逗號）連成一塊不拆，空白一塊，其他一字一塊。"""
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        ch = text[i]
        j = i + 1
        if ch.isascii() and (ch.isalnum() or ch in ".%$,+-/"):
            while j < len(text) and text[j].isascii() and (text[j].isalnum() or text[j] in ".%$,+-/"):
                j += 1
        elif ch.isspace():
            while j < len(text) and text[j].isspace():
                j += 1
        out.append((i, j))
        i = j
    return out


QUOTE_PAIRS = {"「": "」", "『": "』", "“": "”"}
SHORT_TERM = 8
"""「…」裡面這麼多字以內＝一個詞（例「警報解除」），不拆到兩行。"""
BREAK_AFTER = "，、；：」』”）)…"
"""在這些字後面斷行不扣分；句號（SENTENCE_END）後面斷行還加分（下一句從行首開始）。"""
PENALTY_PLAIN, PENALTY_ASCII, BONUS_SENTENCE = 16.0, 4.0, 4.0
"""在字與字中間斷行（可能把詞拆開，例「判／斷」）、在英數或空白旁斷行的扣分；句號後面斷行的加分
（單位：差幾個字寬的平方）。"""
SHORT_LINE, ORPHAN, EVEN, EXTRA_LINE = 0.7, 0.5, 0.1, 10.0
"""非最後一行短於平均行長的 SHORT_LINE 倍、最後一行短於 ORPHAN 倍才重扣分；其餘只按離平均多遠輕扣（EVEN）。
多用一行（最少行數＋1）扣 EXTRA_LINE：換來全部在標點斷行時才值得。"""


def _units(text: str, keep: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """不能從中間斷行的單位 [i, j)：英數串、空白；框要找的字（keep）與短的「…」詞整串；
    不放行首的標點黏在前一個單位、不放行尾的括號黏在後一個單位。"""
    spans = list(keep)
    for k, ch in enumerate(text):
        if ch in QUOTE_PAIRS:
            j = text.find(QUOTE_PAIRS[ch], k + 1)
            if 0 < j - k - 1 <= SHORT_TERM:
                spans.append((k, j + 1))
    units: list[list[int]] = []
    for i, j in _tokens(text):
        inside = any(a < i < b for a, b in spans)  # 這個單位的開頭在要整串留著的字中間
        if units and (inside or text[i] in NO_LINE_START or text[units[-1][1] - 1] in NO_LINE_END):
            units[-1][1] = j
        else:
            units.append([i, j])
    return [(i, j) for i, j in units]


def _layout(text: str, keep: list[tuple[int, int]] = ()) -> list[list[tuple[int, float, float]]]:
    """只排 text（剪下來的句子），從行首開始、靠左：每行是 [(字元位置, x0, x1)]，x 從 0 起算。
    行數用最少的，再挑「各行跟平均行長差得最少、在標點後面斷行」的斷法（動態規劃，決定性）。"""
    font = _font()
    units = _units(text, list(keep))
    widths = [[font.getlength(c) for c in text[i:j]] for i, j in units]
    space = [text[i:j].isspace() for i, j in units]
    pre = [0.0]
    for w in widths:
        pre.append(pre[-1] + sum(w))

    def line(a: int, b: int) -> tuple[float, float] | None:
        """units[a:b] 排成一行：(看得到的寬度, 量行寬用的寬度＝不算行尾掛出去的標點)；放不下回 None。"""
        lo, hi = a, b
        while lo < hi and space[lo]:
            lo += 1
        while hi > lo and space[hi - 1]:
            hi -= 1
        if lo == hi:
            return None
        w = pre[hi] - pre[lo]
        i, j = units[hi - 1]
        hang = 0.0
        for c, cw in zip(reversed(text[i:j]), reversed(widths[hi - 1])):
            if c not in NO_LINE_START:
                break
            hang += cw
        fit = w - hang
        return (w, fit) if fit <= MAX_LINE_PX or hi - lo == 1 else None

    def ends(a: int) -> list[int]:
        """從第 a 個單位起一行可以排到哪裡（寬度只增不減，超過就停）。"""
        out = []
        for b in range(a + 1, len(units) + 1):
            if pre[b] - pre[a] > MAX_LINE_PX + 3 * FONT_PX and out:
                break
            if line(a, b):
                out.append(b)
        return out

    def penalty(k: int) -> float:
        """在第 k 個單位後面斷行的扣分。"""
        i, j = units[k]
        tail = text[i:j].rstrip(CLOSERS) or text[i:j]
        if tail[-1] in SENTENCE_END:
            return -BONUS_SENTENCE
        if text[j - 1] in BREAK_AFTER:
            return 0.0
        if text[j - 1].isascii() or (j < len(text) and (text[j].isascii() or text[j].isspace())):
            return PENALTY_ASCII
        return PENALTY_PLAIN

    n = len(units)
    INF = float("inf")
    nexts = [ends(a) for a in range(n)]
    # 最少幾行：reach＝用 m 行能排到第幾個單位為止
    best_lines, reach = 0, {0}
    while n not in reach:
        reach = {b for a in reach if a < n for b in nexts[a]}
        best_lines += 1
        if best_lines > MAX_LINES or not reach:
            raise ExcerptError(
                f"剪報太長：「{text[:30]}…」排出來超過 {MAX_LINES} 行，一張剪報最多 {MAX_LINES} 行。"
                "引文只選要指給觀眾看的那一兩句"
            )
    total = sum(pre[k + 1] - pre[k] for k in range(n) if not space[k])

    def solve(lines_n: int) -> tuple[float, list[tuple[int, int]]] | None:
        """剛好 lines_n 行的最好斷法：(扣分, [(a, b)…])。cost[m][b]＝前 b 個單位排成 m 行的最小扣分。"""
        target = total / lines_n
        cost = [[INF] * (n + 1) for _ in range(lines_n + 1)]
        back = [[-1] * (n + 1) for _ in range(lines_n + 1)]
        cost[0][0] = 0.0
        for m in range(1, lines_n + 1):
            last = m == lines_n
            for a in range(n):
                if cost[m - 1][a] == INF:
                    continue
                for b in nexts[a]:
                    if last and b != n:
                        continue
                    w = line(a, b)[0]
                    dev = (target - w) / FONT_PX
                    short = max(0.0, ((ORPHAN if last else SHORT_LINE) * target - w) / FONT_PX)
                    c = cost[m - 1][a] + EVEN * dev * dev + short * short + (0.0 if last else penalty(b - 1))
                    if c < cost[m][b]:
                        cost[m][b], back[m][b] = c, a
        if cost[lines_n][n] == INF:
            return None
        cuts, b = [], n
        for m in range(lines_n, 0, -1):
            a = back[m][b]
            cuts.append((a, b))
            b = a
        return cost[lines_n][n], cuts

    tries = [(c + EXTRA_LINE * (m - best_lines), cuts) for m in (best_lines, best_lines + 1)
             if m <= MAX_LINES and (r := solve(m)) for c, cuts in [r]]
    cuts = min(tries, key=lambda t: t[0])[1]
    lines: list[list[tuple[int, float, float]]] = []
    for a, b in reversed(cuts):
        row: list[tuple[int, float, float]] = []
        x = 0.0
        for k in range(a, b):
            i, j = units[k]
            if text[i:j].isspace() and (not row or k == b - 1):
                continue  # 行首、行尾不放空白
            for c, cw in zip(range(i, j), widths[k]):
                row.append((c, x, x + cw))
                x += cw
        lines.append(row)
    return lines


def make_excerpt(paragraph: int, text: str, quote: str | None, out_dir: Path, *, occurrence: int | None = None,
                 keep: list[str] | tuple[str, ...] = ()) -> Excerpt:
    """剪報圖（有快取）。quote 要在 text 裡出現剛好一次（或用 occurrence 指定第幾次）；沒寫就整段。
    keep：之後框要找的字（box 的 find），排版時不拆到兩行。"""
    if quote:
        hits = [i for i in range(len(text)) if text.startswith(quote, i)]
        if not hits:
            raise ExcerptError(f"第 {paragraph} 段裡沒有「{quote}」（這段是：{text}）")
        if occurrence is None and len(hits) > 1:
            raise ExcerptError(f"「{quote}」在第 {paragraph} 段出現 {len(hits)} 次：引文寫長一點，讓它只出現一次")
        q0 = hits[(occurrence or 1) - 1]
        s, e = sentence_span(text, q0, q0 + len(quote))
    else:
        s, e = 0, len(text.rstrip())
    clip = text[s:e]
    spans = sorted({(i, i + len(f)) for f in keep if f for i in range(len(clip)) if clip.startswith(f, i)
                    and _font().getlength(f) <= MAX_LINE_PX})
    kept = _layout(clip, spans)
    if len(kept) > MAX_LINES:
        raise ExcerptError(
            f"剪報太長：「{clip[:30]}…」排出來有 {len(kept)} 行，一張剪報最多 {MAX_LINES} 行。"
            "引文只選要指給觀眾看的那一兩句"
        )
    right = max(b for ln in kept for _, _, b in ln)
    width = int(round(right)) + 2 * PAD_X
    height = len(kept) * LINE_PX + 2 * PAD_Y
    asc, desc = _font().getmetrics()
    boxes: dict[int, tuple[int, int, int, int]] = {}
    glyphs: list[tuple[str, float, float]] = []
    for n, ln in enumerate(kept):
        top = PAD_Y + n * LINE_PX
        for k, a, b in ln:
            x0 = PAD_X + a
            y0 = top + (LINE_PX - FONT_PX) // 2 - 10  # 框住字的高度（再多留一點），不是整個行高
            boxes[s + k] = (int(round(x0)), y0, int(round(PAD_X + b)), y0 + FONT_PX + 20)
            glyphs.append((clip[k], x0, top + (LINE_PX - asc - desc) / 2))

    key = hashlib.sha256(json.dumps(
        [EXCERPT_VERSION, PIL_VERSION, _font_digest(), FONT_PX, LINE_PX, MAX_LINE_PX, clip, spans], ensure_ascii=False,
    ).encode()).hexdigest()[:16]
    out = out_dir / f"excerpt-{key}.png"
    if not out.is_file():
        out_dir.mkdir(parents=True, exist_ok=True)
        im = Image.new("RGB", (width, height), PAPER)
        d = ImageDraw.Draw(im)
        for ch, x, y in glyphs:
            if not ch.isspace():
                d.text((x, y), ch, font=_font(), fill=INK)
        tmp = out.with_suffix(".tmp.png")
        im.save(tmp, optimize=False)
        tmp.replace(out)
    return Excerpt(paragraph=paragraph, text=clip, start=s, end=e, image=out, width=width, height=height, boxes=boxes)

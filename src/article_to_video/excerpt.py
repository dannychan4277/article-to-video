"""原文剪報：把文章裡的一句（或幾句）排成一張剪報圖（PNG），給 Vox 分鏡貼上桌面。

規則：
- 剪到整句：引文（quote）落在哪幾句，就剪那幾句——從第一句的開頭到最後一句的句號（。！？，後面的」』）也算）。
  沒寫引文＝整段。
- 版面照整段排（字級、行寬固定），只留這幾句所在的幾行；第一行句子前面、最後一行句子後面那些別句的殘字遮掉（不畫）。
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

from article_to_video.clips import FONTS_DIR

__all__ = ["MAX_LINES", "Excerpt", "ExcerptError", "make_excerpt", "sentence_span"]

EXCERPT_VERSION = "1"
FONT_FILE = FONTS_DIR / "NotoSansTC-500.woff"
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
    before: str
    """第一行在句子前面、被遮掉的別句殘字。"""
    after: str
    """最後一行在句子後面、被遮掉的別句殘字。"""
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


def _layout(text: str) -> list[list[tuple[int, float, float]]]:
    """整段排版：每行是 [(字元位置, x0, x1)]，x 從 0 起算。"""
    font = _font()
    lines: list[list[tuple[int, float, float]]] = [[]]
    x = 0.0
    for i, j in _tokens(text):
        chunk = text[i:j]
        if chunk.isspace() and not lines[-1]:
            continue  # 行首不放空白
        width = sum(font.getlength(c) for c in chunk)
        hang = chunk[0] in NO_LINE_START
        if lines[-1] and x + width > MAX_LINE_PX and not hang:
            carry: list[tuple[int, float, float]] = []
            while lines[-1] and text[lines[-1][-1][0]] in NO_LINE_END:  # 「（ 不留在行尾
                carry.insert(0, lines[-1].pop())
            lines.append([])
            x = 0.0
            for k, a, b in carry:
                lines[-1].append((k, x, x + (b - a)))
                x += b - a
            if chunk.isspace():
                continue
        for k in range(i, j):
            w = font.getlength(text[k])
            lines[-1].append((k, x, x + w))
            x += w
    return [ln for ln in lines if ln]


def make_excerpt(paragraph: int, text: str, quote: str | None, out_dir: Path, *, occurrence: int | None = None) -> Excerpt:
    """剪報圖（有快取）。quote 要在 text 裡出現剛好一次（或用 occurrence 指定第幾次）；沒寫就整段。"""
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
    lines = _layout(text)
    first = next(n for n, ln in enumerate(lines) if any(k >= s for k, _, _ in ln))
    last = max(n for n, ln in enumerate(lines) if any(k < e for k, _, _ in ln))
    kept = lines[first:last + 1]
    if len(kept) > MAX_LINES:
        raise ExcerptError(
            f"剪報太長：「{text[s:e][:30]}…」排出來有 {len(kept)} 行，一張剪報最多 {MAX_LINES} 行。"
            "引文只選要指給觀眾看的那一兩句"
        )
    visible = [(n, k, a, b) for n, ln in enumerate(kept) for k, a, b in ln if s <= k < e]
    left = min(a for _, _, a, _ in visible)
    right = max(b for _, _, _, b in visible)
    width = int(round(right - left)) + 2 * PAD_X
    height = len(kept) * LINE_PX + 2 * PAD_Y
    asc, desc = _font().getmetrics()
    boxes: dict[int, tuple[int, int, int, int]] = {}
    glyphs: list[tuple[str, float, float]] = []
    for n, k, a, b in visible:
        top = PAD_Y + n * LINE_PX
        x0 = PAD_X + a - left
        y0 = top + (LINE_PX - FONT_PX) // 2 - 10  # 框住字的高度（再多留一點），不是整個行高
        boxes[k] = (int(round(x0)), y0, int(round(PAD_X + b - left)), y0 + FONT_PX + 20)
        glyphs.append((text[k], x0, top + (LINE_PX - asc - desc) / 2))

    key = hashlib.sha256(json.dumps(
        [EXCERPT_VERSION, PIL_VERSION, _font_digest(), FONT_PX, LINE_PX, MAX_LINE_PX, text, s, e], ensure_ascii=False,
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
    before = "".join(text[k] for k, _, _ in kept[0] if k < s)
    after = "".join(text[k] for k, _, _ in kept[-1] if k >= e)
    return Excerpt(paragraph=paragraph, text=text[s:e], start=s, end=e, before=before, after=after,
                   image=out, width=width, height=height, boxes=boxes)

"""字幕：依「，。？！；」切句、對上配音時間點，寫 .srt（給平台上傳）與 .ass（燒錄用，深底框白字）。

字型隨外掛附帶（引擎的 Noto Sans CJK TC Medium 子集），燒錄時用 ffmpeg subtitles 的 fontsdir 指過去。
注意：這個字型檔的「家族名」是 "Noto Sans CJK TC Medium"（不是 "Noto Sans CJK TC"）。ASS 寫錯成
"Noto Sans CJK TC" 的話，libass 會改用系統裝的同名字型（Danny 的 Mac 有、使用者的 Mac 沒有）——實測過。
所以燒完會讀 libass 的 verbose 紀錄（check_libass_log），只要它選了系統字型或缺字換字型，就算失敗。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from article_to_video.clips import TEXT_FONT
from article_to_video.fonts import font_codepoints
from article_to_video.timeline import SegmentTiming, VideoFormat

__all__ = [
    "SUBTITLE_FONT_FAMILY",
    "SUBTITLE_FONT_FILE",
    "SUBTITLE_FONT_PS_NAME",
    "SubtitleLine",
    "check_libass_log",
    "missing_subtitle_glyphs",
    "subtitle_lines",
    "wrap_subtitle",
    "write_ass",
    "write_srt",
]

SUBTITLE_FONT_FILE = TEXT_FONT
SUBTITLE_FONT_FAMILY = "Noto Sans CJK TC Medium"
SUBTITLE_FONT_PS_NAME = "NotoSansCJKtc-Medium"
PUNCT = "，。？！；"
_CHUNK_RE = re.compile(rf"[^{PUNCT}]+[{PUNCT}]?")


@dataclass(frozen=True)
class SubtitleLine:
    start: float
    end: float
    text: str
    segment: str


def subtitle_lines(segments: Iterable[SegmentTiming]) -> list[SubtitleLine]:
    """每段旁白依「，。？！；」切成幾塊，一塊一行字幕。

    一塊從「它第一個字開始唸」顯示到「下一塊開始唸」；段落最後一塊顯示到這段音檔結束。句尾標點拿掉。
    """
    out: list[SubtitleLine] = []
    for seg in segments:
        text, au = seg.audio.text, seg.audio
        chunks = [(m.start(), m.group()) for m in _CHUNK_RE.finditer(text)]
        for j, (i, raw) in enumerate(chunks):
            shown = raw.strip(PUNCT + " 　\n")
            if not shown:
                continue
            a = seg.start + au.char_start(i + (len(raw) - len(raw.lstrip())))
            b = seg.start + (au.char_start(chunks[j + 1][0]) if j + 1 < len(chunks) else au.duration)
            out.append(SubtitleLine(a, max(b, a + 0.1), " ".join(shown.split()), seg.key))
    return out


def _srt_time(t: float) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def _ass_time(t: float) -> str:
    cs = round(t * 100)
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def write_srt(lines: Iterable[SubtitleLine], path: Path) -> None:
    body = "".join(f"{n}\n{_srt_time(x.start)} --> {_srt_time(x.end)}\n{x.text}\n\n" for n, x in enumerate(lines, 1))
    path.write_text(body, encoding="utf-8")


def _ass_text(s: str) -> str:
    return s.replace("\\", "＼").replace("{", "｛").replace("}", "｝")


_NO_LINE_START = set("、，。．・：；？！）」』】》〉…%％")
"""這些標點不能放在一行開頭（跟著前一個字）。"""


_EM_PER_SIZE = 1000 / 1448
"""ASS 字級＝行高：libass 把字型的 ascender＋descender（Noto Sans CJK：1160＋288，每 em 1000）縮放成字級，
所以一個中文字（1 em）寬約字級的 0.69 倍（實測 58 號字 13 個字寬 520 px）。"""


def _em(ch: str) -> float:
    """估一個字的寬度（em）：中日韓與全形字 1；其他（英數、半形標點）抓寬一點 0.6。"""
    return 1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.6


def wrap_subtitle(text: str, max_em: float) -> list[str]:
    """一塊字幕放不進一行（max_em 個中文字寬）就自己折成幾行、每行差不多長。

    libass 只會在空白處折行，中文整句沒有空白就直接衝出畫面（直式一行只放得下 17 個字）。
    英數連在一起的（例 2024、DGS10、4.58%）不拆開；「、」「）」「%」這類不放在行首。
    """
    units: list[str] = []
    for ch in text:
        ascii_run = ch.isascii() and not ch.isspace() and units and units[-1][-1].isascii() and not units[-1][-1].isspace()
        if units and (ascii_run or ch in _NO_LINE_START):
            units[-1] += ch
        else:
            units.append(ch)
    width = [sum(_em(c) for c in u) for u in units]
    total = sum(width)
    if total <= max_em:
        return [text]
    n = math.ceil(total / max_em)
    while True:
        lines: list[str] = []
        cur, cur_w, target = "", 0.0, total / n
        for u, w in zip(units, width):
            if cur and (cur_w + w > max_em or (cur_w + w / 2 > target and len(lines) < n - 1)):
                lines.append(cur.strip())
                cur, cur_w = "", 0.0
            if not cur and u.isspace():
                continue  # 行首不留空白
            cur += u
            cur_w += w
        lines.append(cur.strip())
        if len(lines) <= n or n > len(units):
            return lines
        n += 1


def write_ass(lines: Iterable[SubtitleLine], path: Path, fmt: VideoFormat) -> None:
    """深底框白字：BorderStyle 3（不透明底框），框色 #141414、約 25% 透明，Outline＝框的內距。
    一行放不下的字幕自己折行（wrap_subtitle）：可用寬度＝畫面寬－左右邊距。"""
    max_em = (fmt.width - 2 * fmt.sub_margin_lr) / (fmt.sub_size * _EM_PER_SIZE)
    box = "&H40141414"
    style = ",".join(str(x) for x in [
        "Default", SUBTITLE_FONT_FAMILY, fmt.sub_size, "&H00FFFFFF", "&H00FFFFFF", box, box,
        0, 0, 0, 0, 100, 100, 0, 0, 3, fmt.sub_pad, 0, 2, fmt.sub_margin_lr, fmt.sub_margin_lr, fmt.sub_margin_v, 1,
    ])
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {fmt.width}\nPlayResY: {fmt.height}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: {style}\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = "".join(
        f"Dialogue: 0,{_ass_time(x.start)},{_ass_time(x.end)},Default,,0,0,0,,"
        + r"\N".join(_ass_text(t) for t in wrap_subtitle(x.text, max_em)) + "\n"
        for x in lines
    )
    path.write_text(head + events, encoding="utf-8")


def missing_subtitle_glyphs(segments: Mapping[str, str], font: Path = SUBTITLE_FONT_FILE) -> dict[str, str]:
    """{旁白段: 字幕字型沒有的字}；全部都有就回空 dict。空白不算。"""
    cps = font_codepoints(font)
    out: dict[str, str] = {}
    for key, text in segments.items():
        miss = "".join(dict.fromkeys(ch for ch in text if not ch.isspace() and ord(ch) not in cps))
        if miss:
            out[key] = miss
    return out


_SELECT_RE = re.compile(r"fontselect: \((?P<req>[^)]*)\) -> (?P<target>.*?), (?P<index>-?\d+), (?P<ps>\S.*)$")


def check_libass_log(log: str) -> list[str]:
    """讀 ffmpeg -v verbose 的 libass 紀錄，回傳問題清單（空＝字幕全用外掛附帶的字型）。

    libass 選系統字型時 target 是絕對路徑；選 fontsdir 載入的記憶體字型時是字型名。
    "Glyph … not found, selecting one more font" 表示有字不在那個字型裡、換了別的字型補。
    """
    problems: list[str] = []
    selected = 0
    for line in log.splitlines():
        m = _SELECT_RE.search(line)
        if m:
            selected += 1
            target = m.group("target")
            if target.startswith("/") or m.group("ps").strip() != SUBTITLE_FONT_PS_NAME:
                problems.append(f"字幕字型被換成 {target}（{m.group('ps').strip()}），不是外掛附帶的 {SUBTITLE_FONT_PS_NAME}")
        elif "not found, selecting one more font" in line:
            problems.append("字幕有字不在附帶字型裡，libass 換了別的字型補：" + line.split("]", 1)[-1].strip())
    if not selected:
        problems.append("ffmpeg 的紀錄裡看不到 libass 選字型（fontselect）的訊息，沒辦法確認字幕用的是附帶字型")
    return problems

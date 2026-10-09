"""Markdown 文章（Google 文件「下載 → Markdown」）：去掉標記符號，抽出圖片。

Google 文件匯出的 Markdown 長這樣：圖片寫成 `![][image1]`，文末用 `[image1]: <data:image/png;base64,…>` 附上整張圖；
標點前面常有跳脫的反斜線（`4\\.58`）。一般寫法 `![說明](圖檔路徑)` 也收（路徑相對 .md 檔所在資料夾）。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from article_to_video.article_common import Block, image_from_bytes, image_source, join_lines

_REF_DEF = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*<?(\S+?)>?\s*$")
_IMG_REF = re.compile(r"!\[[^\]]*\]\[([^\]]*)\]")
_IMG_INLINE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_ITEM_START = re.compile(r"^\s{0,3}(?:#{1,6}\s|[*+-]\s|\d+[.)]\s|>)")


def read_markdown(text: str, load: Callable[[str], bytes | None]) -> tuple[list[Block], list[dict]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    refs: dict[str, str] = {}
    body = []
    for line in text.split("\n"):
        m = _REF_DEF.match(line)
        if m:
            refs[m.group(1).lower()] = m.group(2)
        else:
            body.append(line)
    blocks: list[Block] = []
    skipped: list[dict] = []
    seen: set[str] = set()

    def add_image(src: str) -> str:
        if src.startswith(("http://", "https://")):
            skipped.append({"page": None, "width": None, "height": None, "reason": "網路上的圖（沒有下載）"})
        elif src not in seen:
            data, ext = image_source(src, load)
            if data is None:
                skipped.append({"page": None, "width": None, "height": None, "reason": "找不到圖檔"})
            else:
                seen.add(src)
                blocks.append(Block(image=image_from_bytes(data, ext)))
        return ""

    for para in _paragraph_lines("\n".join(body)):
        raw = "\n".join(para)
        raw = _IMG_REF.sub(lambda m: add_image(refs.get((m.group(1) or "").lower(), "")), raw)
        raw = _IMG_INLINE.sub(lambda m: add_image(m.group(1)), raw)
        text_ = _strip_markup(join_lines(raw.split("\n")))
        if text_:
            blocks.append(Block(text=text_))
    return blocks, skipped


def _paragraph_lines(text: str) -> list[list[str]]:
    """空行分段；標題、清單項目、引言每一行各自一段。"""
    paras: list[list[str]] = []
    cur: list[str] = []
    for line in text.split("\n"):
        if not line.strip():
            if cur:
                paras.append(cur)
            cur = []
        elif _ITEM_START.match(line):
            if cur:
                paras.append(cur)
            cur = [line]
        else:
            cur.append(line)
    if cur:
        paras.append(cur)
    return paras


def _strip_markup(s: str) -> str:
    s = re.sub(r"^\s{0,3}(?:#{1,6}\s+|[*+-]\s+|\d+[.)]\s+|>\s?)", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)  # 連結留文字
    s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|~<>$%&=:;,'\"])", r"\1", s)  # 跳脫的標點
    return s.strip()

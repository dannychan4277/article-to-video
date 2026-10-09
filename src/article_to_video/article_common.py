"""文章擷取各格式共用的東西：讀出來的中間結果（Block）、文字整理、圖片格式整理。"""

from __future__ import annotations

import base64
import binascii
import io
import posixpath
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass


class ArticleReadError(Exception):
    """某個格式的讀取器讀不下去；article.py 會把它換成給作者看的繁中說明。"""


@dataclass
class ImageData:
    data: bytes
    ext: str  # ".png" / ".jpg" …（決定 images/圖N 的副檔名）
    width: int | None
    height: int | None


@dataclass
class Block:
    """閱讀順序上的一塊：一段文字，或一張圖。"""

    text: str | None = None
    image: ImageData | None = None
    page: int | None = None


# 瀏覽器（也就是渲片段的引擎）直接顯示得了的格式；其他點陣格式轉成 PNG（無損）
_WEB_FORMATS = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp", "BMP": ".bmp"}


def image_from_bytes(data: bytes, ext: str) -> ImageData:
    """原圖位元組 → ImageData：瀏覽器顯示得了的格式原封不動；TIFF 之類轉 PNG；Pillow 讀不了的（EMF、SVG）照存、大小不明。"""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as im:
            size = im.size
            fmt = im.format or ""
            if fmt in _WEB_FORMATS:
                return ImageData(data, _WEB_FORMATS[fmt], *size)
            im.load()
            buf = io.BytesIO()
            im.save(buf, "PNG")
            return ImageData(buf.getvalue(), ".png", *size)
    except (UnidentifiedImageError, OSError, ValueError):
        return ImageData(data, ext.lower() or ".bin", None, None)

# 中日韓文字、全形符號、中文標點：這些字之間換行接回時不補空白
_CJK_RE = re.compile("[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]")

# 圖說開頭：圖／表／資料來源／Source…，可接編號，再接冒號、句點、頓號或空白
CAPTION_RE = re.compile(
    r"^(?:圖|表|图|資料來源|資料出處|来源|來源|出處|註|Figure|Fig\.?|Chart|Table|Source|Note)"
    r"\s*[0-9０-９一二三四五六七八九十]*\s*[：:．.、\s]",
    re.IGNORECASE,
)
CAPTION_MAX_CHARS = 120


def join_lines(lines: list[str]) -> str:
    """把同一段被換行切開的幾行接回去：中文之間不加空白，英數之間補一個空白。"""
    out = ""
    for line in (ln.strip() for ln in lines):
        if not line:
            continue
        if out and not _CJK_RE.match(out[-1]) and not _CJK_RE.match(line[0]):
            out += " "
        out += line
    return out


def looks_like_caption(text: str) -> bool:
    return len(text) <= CAPTION_MAX_CHARS and bool(CAPTION_RE.match(text))


def paragraphs_from_plain_text(text: str) -> list[str]:
    """純文字分段：有空行就用空行分段（段內換行接回）；完全沒有空行就一行一段（從網頁複製貼上常見）。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"\n[ \t\u3000]*\n", text.strip()):
        paras = [join_lines(block.split("\n")) for block in re.split(r"\n[ \t\u3000]*\n", text)]
    else:
        paras = [ln.strip() for ln in text.split("\n")]
    return [p for p in paras if p]


def image_source(src: str, load: Callable[[str], bytes | None]) -> tuple[bytes | None, str]:
    if src.startswith("data:"):
        head, _, payload = src.partition(",")
        try:
            data = base64.b64decode(payload) if head.endswith(";base64") else urllib.parse.unquote_to_bytes(payload)
        except (binascii.Error, ValueError):
            return None, ""
        subtype = head[5:].split(";")[0].split("/")[-1]
        return data, "." + (subtype or "bin")
    path = urllib.parse.unquote(src.split("?")[0].split("#")[0])
    return load(path), posixpath.splitext(path)[1]

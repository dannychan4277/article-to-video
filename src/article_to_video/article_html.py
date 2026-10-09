"""網頁格式的文章：Google 文件「下載 → 網頁（.html，已壓縮）」的 zip，或單一 .html 檔。只用標準庫。

段落＝p、h1–h6、li、td/th、blockquote、pre、figcaption…這些區塊標籤裡的字；head、style、script 不算。
圖：zip 裡的相對路徑、.html 旁邊的檔案、或 data: URI。網路上的圖（http）不下載（記在略過清單）。
圖夾在段落中間時，放在那一段前面（跟 Word 一樣）。
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path

from article_to_video.article_common import ArticleReadError, Block, image_from_bytes, image_source

_BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th", "blockquote", "pre", "div", "figcaption",
    "caption", "dt", "dd", "section", "article", "header", "footer", "ul", "ol", "table", "tr", "figure", "hr",
}
_SKIP_TAGS = {"head", "style", "script", "noscript", "template", "title", "svg"}
_CJK = "\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f"


def read_html_zip(path: Path) -> tuple[list[Block], list[dict]]:
    try:
        with zipfile.ZipFile(path) as z:
            pages = [n for n in z.namelist() if n.lower().endswith((".html", ".htm")) and not n.startswith("__MACOSX")]
            if len(pages) != 1:
                raise ArticleReadError("zip 裡要剛好一個 .html")
            html_name = pages[0]
            html = z.read(html_name).decode("utf-8", errors="replace")
            folder = posixpath.dirname(html_name)

            def load(src: str) -> bytes | None:
                try:
                    return z.read(posixpath.normpath(posixpath.join(folder, src)))
                except KeyError:
                    return None

            return _parse(html, load)
    except (zipfile.BadZipFile, OSError) as e:
        raise ArticleReadError(str(e)) from e


def read_html_file(path: Path) -> tuple[list[Block], list[dict]]:
    html = path.read_bytes().decode("utf-8", errors="replace")
    base = path.parent.resolve()

    def load(src: str) -> bytes | None:
        f = (base / src).resolve()
        if base not in f.parents or not f.is_file():
            return None
        return f.read_bytes()

    return _parse(html, load)


def _parse(html: str, load: Callable[[str], bytes | None]) -> tuple[list[Block], list[dict]]:
    p = _Parser(load)
    p.feed(html)
    p.close()
    p.flush()
    return p.blocks, p.skipped


class _Parser(HTMLParser):
    def __init__(self, load: Callable[[str], bytes | None]) -> None:
        super().__init__(convert_charrefs=True)
        self.load = load
        self.blocks: list[Block] = []
        self.skipped: list[dict] = []
        self.buf: list[str] = []
        self.pending_images: list[Block] = []
        self.skip_depth = 0
        self.pre_depth = 0
        self.seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in _BLOCK_TAGS:
            self.flush()
        if tag == "pre":
            self.pre_depth += 1
        elif tag == "br":
            self.buf.append("\n")
        elif tag == "img":
            self._image(dict(attrs).get("src") or "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag in _SKIP_TAGS:
            self.skip_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag in _BLOCK_TAGS:
            self.flush()
        if tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.buf.append(data if self.pre_depth else re.sub(r"\s+", " ", data))

    def flush(self) -> None:
        text = "".join(self.buf)
        self.buf = []
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
        text = " ".join(ln for ln in lines if ln)
        text = re.sub(f"(?<=[{_CJK}]) (?=[{_CJK}])", "", text)  # 原始碼換行造成的中文字間空白
        self.blocks += self.pending_images
        self.pending_images = []
        if text:
            self.blocks.append(Block(text=text))

    def _image(self, src: str) -> None:
        if src.startswith(("http://", "https://", "//")):
            self.skipped.append({"page": None, "width": None, "height": None, "reason": "網路上的圖（沒有下載）"})
            return
        if src in self.seen:
            return
        data, ext = image_source(src, self.load)
        if data is None:
            self.skipped.append({"page": None, "width": None, "height": None, "reason": "找不到圖檔"})
            return
        self.seen.add(src)
        self.pending_images.append(Block(image=image_from_bytes(data, ext)))

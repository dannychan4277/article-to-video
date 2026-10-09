"""Word（.docx，含 Google 文件「下載 → Microsoft Word」）文章擷取：只用標準庫讀 zip 裡的 XML。

依文件順序走過 word/document.xml：每個 w:p 是一段（表格每格、文字方塊裡的段落也是）；
段落裡遇到圖（DrawingML a:blip、舊式 VML v:imagedata）就在那段之前放一張圖。
mc:AlternateContent 只讀 mc:Choice（mc:Fallback 是同一張圖的舊格式副本）。
"""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from article_to_video.article_common import ArticleReadError, Block, image_from_bytes, join_lines

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_V = "{urn:schemas-microsoft-com:vml}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFFICE_DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"


def read_docx(path: Path) -> list[Block]:
    try:
        with zipfile.ZipFile(path) as z:
            main = _main_part(z)
            root = ET.fromstring(z.read(main))
            rels = _relationships(z, main)
            return _Walker(z, rels).run(root)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as e:
        raise ArticleReadError(str(e)) from e


def _main_part(z: zipfile.ZipFile) -> str:
    try:
        rels = ET.fromstring(z.read("_rels/.rels"))
        for rel in rels.iter(f"{_PR}Relationship"):
            if rel.get("Type") == _OFFICE_DOCUMENT:
                return rel.get("Target", "word/document.xml").lstrip("/")
    except KeyError:
        pass
    return "word/document.xml"


def _relationships(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    """rId → zip 裡的路徑（外部連結的圖不算）。"""
    folder, name = posixpath.split(part)
    try:
        rels = ET.fromstring(z.read(posixpath.join(folder, "_rels", name + ".rels")))
    except KeyError:
        return {}
    out = {}
    for rel in rels.iter(f"{_PR}Relationship"):
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External" or not target:
            continue
        resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        out[rel.get("Id", "")] = resolved
    return out


class _Walker:
    def __init__(self, z: zipfile.ZipFile, rels: dict[str, str]) -> None:
        self.z = z
        self.rels = rels
        self.blocks: list[Block] = []
        self.paras: list[list[str]] = []
        self.seen_images: set[str] = set()

    def run(self, root: ET.Element) -> list[Block]:
        self._walk(root)
        return self.blocks

    def _walk(self, el: ET.Element) -> None:
        tag = el.tag
        if tag == f"{_MC}Fallback":
            return
        if tag == f"{_W}p":
            self.paras.append([])
            for child in el:
                self._walk(child)
            text = join_lines("".join(self.paras.pop()).split("\n"))
            if text:
                self.blocks.append(Block(text=text))
            return
        if self.paras:
            buf = self.paras[-1]
            if tag == f"{_W}t":
                buf.append(el.text or "")
            elif tag == f"{_W}tab":
                buf.append(" ")
            elif tag in (f"{_W}br", f"{_W}cr"):
                buf.append("\n")
            elif tag == f"{_W}noBreakHyphen":
                buf.append("-")
        if tag == f"{_A}blip":
            self._image(el.get(f"{_R}embed"))
        elif tag == f"{_V}imagedata":
            self._image(el.get(f"{_R}id"))
        for child in el:
            self._walk(child)

    def _image(self, rid: str | None) -> None:
        target = self.rels.get(rid or "")
        if not target or target in self.seen_images:
            return
        try:
            data = self.z.read(target)
        except KeyError:
            return
        self.seen_images.add(target)
        self.blocks.append(Block(image=image_from_bytes(data, posixpath.splitext(target)[1])))

"""PDF 文章擷取：文字層（pdfminer.six）＋嵌入原圖（pypdf 解碼）＋截字偵測。

截字怎麼抓：網頁用瀏覽器印成 PDF 時，比紙寬的那一行會被「裁切範圍」切掉——Chrome 只留下跨在邊界上的那個字，
完全在外面的字直接丟掉（實測 Chromium 印出來的 PDF）。所以這裡一邊解讀內容串流、一邊追蹤目前的裁切範圍
（頁面可見範圍 ∩ 每個 `re W n`），只要有字的外框橫向超出夠寬的裁切範圍，就算截字。

Chrome 的字型子集會把「文」「一」這類字對到康熙部首（⽂ ⼀），同時用 /ActualText 標出原字；
pdfminer 不讀 ActualText，所以這裡自己套回去，最後再把殘留的康熙部首正規化成一般漢字。
"""

from __future__ import annotations

import io
import logging
import math
import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import LAParams, LTAnno, LTChar, LTFigure, LTImage, LTPage, LTTextBox
from pdfminer.pdfdocument import PDFDocument, PDFPasswordIncorrect
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.psparser import PSException
from pdfminer.utils import apply_matrix_pt, apply_matrix_rect, decode_text
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from article_to_video.article_common import Block, ImageData, join_lines

logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pypdf").setLevel(logging.ERROR)

Rect = tuple[float, float, float, float]

# 裁切範圍至少要有頁寬的這個比例才算數（小按鈕、卡片自己的 overflow:hidden 不算截字）
MIN_CLIP_WIDTH_RATIO = 0.5
# 頁首／頁尾帶：頁高上下各這個比例
HEADER_FOOTER_BAND = 0.07
# 兩邊都小於這個像素的圖當成小圖示（頭像、分享鈕），不算文章原圖
MIN_IMAGE_SIDE = 100


@dataclass
class TruncatedLine:
    page: int
    visible_tail: str


@dataclass
class PdfBlock:
    """閱讀順序上的一個單位：一段文字或一張圖。"""

    page: int
    top: float
    bottom: float
    text: str | None = None
    image: ImageData | None = None


@dataclass
class PdfContent:
    pages: int
    blocks: list[Block]  # 閱讀順序
    truncated: list[TruncatedLine] = field(default_factory=list)
    skipped_images: list[dict] = field(default_factory=list)
    char_count: int = 0
    image_count: int = 0


class PdfOpenError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------- pdfminer：追裁切範圍＋ActualText


def _intersect(a: Rect, b: Rect) -> Rect:
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


class _Aggregator(PDFPageAggregator):
    def __init__(self, rsrcmgr: PDFResourceManager, laparams: LAParams) -> None:
        super().__init__(rsrcmgr, laparams=laparams)
        self.clip: Rect = (0.0, 0.0, 0.0, 0.0)
        self.page_box: Rect = (0.0, 0.0, 0.0, 0.0)
        self.clipped: set[int] = set()
        self._tags: list[tuple[Any, int, str] | None] = []

    def begin_page(self, page: PDFPage, ctm: Any) -> None:
        super().begin_page(page, ctm)
        visible = _intersect(page.cropbox, page.mediabox)
        x0, y0, x1, y1 = apply_matrix_rect(ctm, visible)
        self.page_box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.clip = self.page_box
        self.clipped = set()
        self._tags = []

    def render_char(self, *args: Any, **kwargs: Any) -> float:
        adv = super().render_char(*args, **kwargs)
        ch = self.cur_item._objs[-1]
        if isinstance(ch, LTChar) and ch.upright and ch.get_text().strip():
            cx0, _, cx1, _ = self.clip
            page_w = self.page_box[2] - self.page_box[0]
            if cx1 - cx0 >= MIN_CLIP_WIDTH_RATIO * page_w:
                overflow = max(ch.x1 - cx1, cx0 - ch.x0)
                if overflow > max(0.5, 0.25 * ch.width):
                    self.clipped.add(id(ch))
        return adv

    def begin_tag(self, tag: Any, props: Any = None) -> None:
        actual = None
        if isinstance(props, dict) and "ActualText" in props:
            raw = props["ActualText"]
            actual = decode_text(raw) if isinstance(raw, bytes) else str(raw)
        self._tags.append((self.cur_item, len(self.cur_item._objs), actual) if actual is not None else None)

    def end_tag(self) -> None:
        if not self._tags:
            return
        rec = self._tags.pop()
        if rec is None:
            return
        container, start, actual = rec
        if container is not self.cur_item:
            return
        chars = [o for o in container._objs[start:] if isinstance(o, LTChar)]
        if chars:
            chars[0]._text = actual
            for c in chars[1:]:
                c._text = ""


class _Interpreter(PDFPageInterpreter):
    """多追蹤一個「目前裁切範圍」（矩形外框；q/Q 會存回）。"""

    def init_state(self, ctm: Any) -> None:
        super().init_state(ctm)
        self._clip = self.device.clip
        self._clip_stack: list[Rect] = []
        self._clip_pending = False

    def _set_clip(self, clip: Rect) -> None:
        self._clip = clip
        self.device.clip = clip

    def do_q(self) -> None:
        super().do_q()
        self._clip_stack.append(self._clip)

    def do_Q(self) -> None:
        super().do_Q()
        if self._clip_stack:
            self._set_clip(self._clip_stack.pop())

    def do_W(self) -> None:
        self._clip_pending = True

    def do_W_a(self) -> None:
        self._clip_pending = True

    def _apply_pending_clip(self) -> None:
        if not self._clip_pending:
            return
        self._clip_pending = False
        pts = []
        for seg in self.curpath:
            nums = [v for v in seg[1:] if isinstance(v, (int, float))]
            pts += [apply_matrix_pt(self.ctm, (nums[i], nums[i + 1])) for i in range(0, len(nums) - 1, 2)]
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            self._set_clip(_intersect(self._clip, (min(xs), min(ys), max(xs), max(ys))))

    def do_n(self) -> None:
        self._apply_pending_clip()
        super().do_n()

    def do_f(self) -> None:
        self._apply_pending_clip()
        super().do_f()

    def do_f_a(self) -> None:
        self._apply_pending_clip()
        super().do_f_a()

    def do_S(self) -> None:
        self._apply_pending_clip()
        super().do_S()

    def do_B(self) -> None:
        self._apply_pending_clip()
        super().do_B()

    def do_B_a(self) -> None:
        self._apply_pending_clip()
        super().do_B_a()

    def do_Do(self, xobjid_arg: Any) -> None:
        saved = self._clip
        super().do_Do(xobjid_arg)
        self._set_clip(saved)


# ---------------------------------------------------------------- 文字整理

_RADICALS_RE = re.compile("[\u2e80-\u2ef3\u2f00-\u2fd5]")  # 部首補充、康熙部首
_TERMINAL = set("。！？!?．.…」』”\"）)】：:；;")


def normalize_radicals(text: str) -> str:
    """康熙部首／部首補充 → 一般漢字（只動這兩區，全形標點不受影響）。"""
    return _RADICALS_RE.sub(lambda m: unicodedata.normalize("NFKC", m.group()), text)


def _wide(ch: str) -> bool:
    """中日韓文字、全形標點（排版上一個字一格的那種）。"""
    return unicodedata.east_asian_width(ch) in ("W", "F")


def _line_text(line: Any) -> str:
    """一行的文字。PDF 裡真的有的空白字照留（連續的併成一個）；pdfminer 看字距自己補的空白（LTAnno）
    只留在兩個英數字之間（有些 PDF 的英文字之間沒有空白字，靠字距分詞）。

    兩端對齊（text-align: justify）會把每個字距都撐開，pdfminer 就在每兩個中文字之間補一個空白
    （試跑：「兩 年 期 從   4.58%」）；中文字旁邊補的空白一律不要。"""
    parts: list[str] = []  # 真的字；None 表示 pdfminer 補的空白
    for obj in line:
        if isinstance(obj, LTChar):
            t = obj.get_text()
            if t:
                parts.append(t)
        elif isinstance(obj, LTAnno) and obj.get_text() == " ":
            parts.append(None)  # type: ignore[arg-type]
    out = ""
    for i, t in enumerate(parts):
        if t is not None:
            if t.isspace():
                if out and not out.endswith(" "):
                    out += " "
            else:
                out += t
            continue
        before = out[-1:] if out else ""
        after = next((x for x in parts[i + 1:] if x is not None), "")[:1]
        if before and after and not before.isspace() and not after.isspace() and not _wide(before) and not _wide(after):
            out += " "
    return out


# ---------------------------------------------------------------- 主流程


def read_pdf(path: Path) -> PdfContent:
    try:
        with path.open("rb") as f:
            parser = PDFParser(f)
            doc = PDFDocument(parser)
            pages_layout = _layout_pages(doc)
        reader = PdfReader(str(path))
    except PDFPasswordIncorrect as e:
        raise PdfOpenError("password") from e
    except (PSException, PyPdfError, ValueError, KeyError, TypeError, OSError) as e:
        raise PdfOpenError("broken") from e

    content = PdfContent(pages=len(pages_layout), blocks=[])
    for pageno, (ltpage, clipped) in enumerate(pages_layout, 1):
        content.truncated += _truncated_lines(ltpage, clipped, pageno)
    texts = _text_blocks(pages_layout)
    content.char_count = sum(len(b.text or "") for b in texts)
    images = _image_blocks(pages_layout, reader, content)
    content.blocks = [Block(text=b.text, image=b.image, page=b.page) for b in _reading_order(texts + images)]
    return content


def _layout_pages(doc: PDFDocument) -> list[tuple[LTPage, set[int]]]:
    rsrcmgr = PDFResourceManager()
    device = _Aggregator(rsrcmgr, LAParams(all_texts=True))
    interp = _Interpreter(rsrcmgr, device)
    out = []
    for page in PDFPage.create_pages(doc):
        interp.process_page(page)
        out.append((device.get_result(), set(device.clipped)))
    return out


def _walk(obj: Any):
    yield obj
    if isinstance(obj, (LTPage, LTFigure)):
        for child in obj:
            yield from _walk(child)


def _truncated_lines(ltpage: LTPage, clipped: set[int], pageno: int) -> list[TruncatedLine]:
    if not clipped:
        return []
    out = []
    for obj in _walk(ltpage):
        if not isinstance(obj, LTTextBox):
            continue
        for line in obj:
            chars = [c for c in line if isinstance(c, LTChar)]
            if any(id(c) in clipped for c in chars):
                visible = "".join(c.get_text() for c in line if id(c) not in clipped).strip()
                out.append(TruncatedLine(page=pageno, visible_tail=normalize_radicals(visible[-14:])))
    if not out:  # 字沒被分進文字框（極少見）：至少回報頁碼
        out.append(TruncatedLine(page=pageno, visible_tail=""))
    return out


@dataclass
class _Line:
    page: int
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    text: str


def _page_lines(ltpage: LTPage, pageno: int) -> list[_Line]:
    """一頁的文字行，由上到下（同一列由左到右）。"""
    lines = []
    for obj in _walk(ltpage):
        if not isinstance(obj, LTTextBox):
            continue
        for ln in obj:
            chars = [c for c in ln if isinstance(c, LTChar) and c.get_text().strip()]
            text = normalize_radicals(_line_text(ln).replace("\n", "")).strip()
            if chars and text:
                lines.append(_Line(pageno, ln.x0, ln.x1, ln.y1, ln.y0, statistics.median(c.size for c in chars), text))
    lines.sort(key=lambda ln: -ln.top)
    rows: list[list[_Line]] = []
    for ln in lines:
        if rows and abs(rows[-1][0].top - ln.top) < 0.3 * ln.size:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    return [ln for row in rows for ln in sorted(row, key=lambda x: x.x0)]


def _drop_headers_footers(pages: list[list[_Line]], heights: list[float]) -> list[list[_Line]]:
    """每頁都出現在頁首／頁尾帶、只差數字（頁碼、日期）的字，是瀏覽器加的頁首頁尾，拿掉。"""
    npages = len(pages)
    if npages < 2:
        return pages

    def key(ln: _Line) -> str | None:
        h = heights[ln.page - 1]
        if ln.bottom >= h * (1 - HEADER_FOOTER_BAND) or ln.top <= h * HEADER_FOOTER_BAND:
            return re.sub(r"\d+", "#", re.sub(r"\s+", "", ln.text))
        return None

    seen: dict[str, set[int]] = {}
    for lines in pages:
        for ln in lines:
            k = key(ln)
            if k is not None:
                seen.setdefault(k, set()).add(ln.page)
    need = max(2, math.ceil(npages / 2))
    repeated = {k for k, ps in seen.items() if len(ps) >= need}
    return [[ln for ln in lines if key(ln) not in repeated] for lines in pages]


def _line_pitches(pages: list[list[_Line]]) -> dict[int, float]:
    """每種字級的行距（上一行底到下一行底）：同字級相鄰兩行最常見的最小距離。段落之間會多空一些，比它大。"""
    seen: dict[int, Counter] = {}
    for lines in pages:
        for a, b in zip(lines, lines[1:]):
            d = a.bottom - b.bottom
            if abs(a.size - b.size) <= 0.15 * a.size and 0.95 * a.size <= d <= 2.1 * a.size:
                seen.setdefault(round(a.size), Counter())[round(d * 2) / 2] += 1
    out = {}
    for size, counts in seen.items():
        common = [d for d, n in counts.items() if n >= 2] or list(counts)
        out[size] = min(common)
    return out


def _text_blocks(pages_layout: list[tuple[LTPage, set[int]]]) -> list[PdfBlock]:
    """文字行 → 段落：字級一樣、行距沒有比平常大、下一行沒有往右縮排，就是同一段。"""
    pages = [_page_lines(lt, i) for i, (lt, _) in enumerate(pages_layout, 1)]
    pages = _drop_headers_footers(pages, [lt.height for lt, _ in pages_layout])
    pitches = _line_pitches(pages)
    blocks: list[PdfBlock] = []
    for lines in pages:
        para: list[_Line] = []
        for ln in lines:
            if para and _same_paragraph(para, ln, pitches):
                para.append(ln)
                continue
            if para:
                blocks.append(_paragraph_block(para))
            para = [ln]
        if para:
            blocks.append(_paragraph_block(para))
    return blocks


def _same_paragraph(para: list[_Line], ln: _Line, pitches: dict[int, float]) -> bool:
    last = para[-1]
    if abs(last.size - ln.size) > 0.15 * last.size:
        return False
    d = last.bottom - ln.bottom
    pitch = pitches.get(round(last.size), 1.75 * last.size)
    if d <= 0.3 * last.size or d > pitch * 1.25 + 0.5:
        return False
    return ln.x0 <= min(x.x0 for x in para) + 0.8 * ln.size  # 往右縮排＝新的一段


def _paragraph_block(para: list[_Line]) -> PdfBlock:
    return PdfBlock(page=para[0].page, top=para[0].top, bottom=para[-1].bottom,
                    text=join_lines([ln.text for ln in para]))


def _image_blocks(pages_layout, reader, content: PdfContent) -> list[PdfBlock]:
    blocks: list[PdfBlock] = []
    seen: set[int] = set()
    for pageno, (ltpage, _) in enumerate(pages_layout, 1):
        decoded: dict[int, Any] | None = None
        for obj in _walk(ltpage):
            if not isinstance(obj, LTImage):
                continue
            objid = getattr(obj.stream, "objid", None)
            if objid is None or objid in seen:
                continue
            seen.add(objid)
            sw, sh = (obj.srcsize or (0, 0))
            if max(sw or 0, sh or 0) < MIN_IMAGE_SIDE:
                content.skipped_images.append({"page": pageno, "width": sw, "height": sh, "reason": "小圖示"})
                continue
            if decoded is None:
                decoded = _decode_page_images(reader, pageno)
            img = _image_data(decoded.get(objid))
            if img is None:
                content.skipped_images.append({"page": pageno, "width": sw, "height": sh, "reason": "解不開的圖片格式"})
                continue
            blocks.append(PdfBlock(page=pageno, top=obj.y1, bottom=obj.y0, image=img))
    content.image_count = len(blocks)
    return blocks


def _decode_page_images(reader, pageno: int) -> dict[int, Any]:
    out: dict[int, Any] = {}
    try:
        images = reader.pages[pageno - 1].images
        for i in range(len(images)):
            try:
                img = images[i]
            except Exception:  # noqa: BLE001 — pypdf 解不開某張圖時跳過那張
                continue
            ref = img.indirect_reference
            if ref is not None:
                out[ref.idnum] = img
    except Exception:  # noqa: BLE001
        pass
    return out


def _original_jpeg(xobj: Any) -> bytes | None:
    """單純的 JPEG（DCTDecode、沒有透明遮罩、沒有反相）：直接拿 PDF 裡的原檔位元組，不重壓。

    pypdf 的 ImageFile.data 有時會把 JPEG 重新編碼（實測：Chromium 印的 PDF 裡一張漸層 JPEG，data 跟 PDF 裡的原檔不同），
    所以單純的 JPEG 不經過它。
    """
    filters = xobj.get("/Filter")
    filters = list(filters) if isinstance(filters, list) else [filters]
    if not filters or filters[-1] != "/DCTDecode" or any(k in xobj for k in ("/SMask", "/Mask", "/Decode")):
        return None
    data = xobj.get_data()
    return data if data[:2] == b"\xff\xd8" else None


def _image_data(pdf_img: Any) -> ImageData | None:
    if pdf_img is None:
        return None
    try:
        pil = pdf_img.image
        ext = Path(pdf_img.name).suffix.lower()
        jpeg = _original_jpeg(pdf_img.indirect_reference.get_object()) if pdf_img.indirect_reference else None
        if jpeg is not None:
            data, ext = jpeg, ".jpg"
        elif ext in (".jpg", ".jpeg", ".png"):
            data, ext = pdf_img.data, (".png" if ext == ".png" else ".jpg")
        else:  # JPEG 2000、TIFF…瀏覽器不一定能顯示，轉成 PNG（無損）
            buf = io.BytesIO()
            pil.save(buf, "PNG")
            data, ext = buf.getvalue(), ".png"
        return ImageData(data, ext, *pil.size)
    except Exception:  # noqa: BLE001 — 任何一張解不開都只略過那張
        return None


def _reading_order(blocks: list[PdfBlock]) -> list[PdfBlock]:
    """單欄文章：每頁由上到下；被換頁切開的段落（上一頁最後一塊是沒結束的句子、下一頁第一塊是文字）接回一段。"""
    ordered = sorted(blocks, key=lambda b: (b.page, -b.top))
    out: list[PdfBlock] = []
    prev_raw: PdfBlock | None = None
    for b in ordered:
        if (
            prev_raw is not None and b.page == prev_raw.page + 1
            and b.text is not None and prev_raw.text is not None
            and out[-1].text and out[-1].text[-1] not in _TERMINAL
        ):
            out[-1].text = join_lines([out[-1].text, b.text])
        else:
            out.append(PdfBlock(page=b.page, top=b.top, bottom=b.bottom, text=b.text, image=b.image))
        prev_raw = b
    return out

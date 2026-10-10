"""文章擷取：作者丟來的文章（PDF、Word、Google 文件匯出檔、貼上的文字）→ <專案>/article/。

產出（檔名寫死在這裡，其他模組透過 ArticlePaths 拿路徑）：

    <專案>/article/
      source.<副檔名>   原檔，原封不動複製（貼上的文字存成 source.txt）
      text.txt          全文（UTF-8），一段一行、段與段之間空一行
      images/圖1.png …  文章裡的原圖，完整解析度，依出現順序編號（JPEG 原檔位元組照搬）
      article.json      清單：來源、段落（編號＋文字＋頁碼）、每張圖的編號／檔案／像素大小／頁碼／圖說／前後段落

重跑會整個換掉 article/ 裡這幾樣（其他檔案不動）；Claude 讀上一份的圖記下的數字（figure_numbers.json，原文圖表）
也一起拿掉，圖換了就要重讀。擷取不下去時丟 ArticleError（繁中、直接給作者看），
這時 article/ 完全不動。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from article_to_video.article_common import ArticleReadError, Block, looks_like_caption, paragraphs_from_plain_text

__all__ = [
    "ArticleError",
    "ArticleImage",
    "ArticlePaths",
    "ExtractedArticle",
    "Paragraph",
    "TruncatedPdfError",
    "extract_article",
    "run_extract",
]

MANIFEST_VERSION = 1
MIN_TEXT_CHARS = 20  # PDF 文字層少於這麼多字＝沒有文字層（掃描檔）


class ArticleError(ValueError):
    """文章擷取不下去（繁中，直接給作者看）。"""


class TruncatedPdfError(ArticleError):
    """PDF 文字層有行被頁面邊界切掉。pages：{頁碼: 被切的行數}。"""

    def __init__(self, message: str, pages: dict[int, int]) -> None:
        super().__init__(message)
        self.pages = pages


# ---------------------------------------------------------------- 產出


@dataclass(frozen=True)
class ArticlePaths:
    root: Path

    @property
    def text(self) -> Path:
        return self.root / "text.txt"

    @property
    def images(self) -> Path:
        return self.root / "images"

    @property
    def manifest(self) -> Path:
        return self.root / "article.json"

    @property
    def figure_numbers(self) -> Path:
        """Claude 看圖讀到的數字（原文圖表，numbers.load_figure_numbers）。不是擷取產生的，但重跑擷取會拿掉：圖換了就不算數。"""
        return self.root / "figure_numbers.json"

    def source(self, suffix: str) -> Path:
        return self.root / f"source{suffix}"


@dataclass(frozen=True)
class Paragraph:
    n: int
    text: str
    page: int | None = None


@dataclass(frozen=True)
class ArticleImage:
    n: int
    path: Path
    width: int | None
    height: int | None
    page: int | None = None
    caption: str | None = None
    before: int | None = None  # 圖前面最近的段落編號
    after: int | None = None  # 圖後面最近的段落編號

    @property
    def label(self) -> str:
        return f"圖{self.n}"


@dataclass
class ExtractedArticle:
    paths: ArticlePaths
    kind: str
    paragraphs: list[Paragraph]
    images: list[ArticleImage]
    manifest: dict


# ---------------------------------------------------------------- 讀進來的中間結果


@dataclass
class _Doc:
    kind: str
    suffix: str
    raw: bytes
    name: str | None
    blocks: list[Block]
    pages: int | None = None
    skipped_images: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- 主入口


def extract_article(
    project_dir: str | os.PathLike,
    source: str | os.PathLike | None = None,
    *,
    text: str | None = None,
) -> ExtractedArticle:
    """擷取文章到 <專案>/article/。source 是文章檔；作者直接貼的文字用 text=。"""
    if (source is None) == (text is None):
        raise ArticleError("要給文章檔案，或貼上的文字，二擇一")
    if text is not None:
        doc = _read_pasted(text)
    else:
        path = Path(source)
        if not path.is_file():
            raise ArticleError(f"找不到文章檔：{path}")
        reader = _READERS.get(path.suffix.lower())
        if reader is None:
            raise ArticleError(_unsupported_message(path))
        doc = reader(path)
    if not any(b.text for b in doc.blocks):
        what = "貼上的內容" if doc.name is None else f"「{doc.name}」"
        raise ArticleError(f"{what}裡面沒有文字可以抓。請確認給的是文章本身，或直接把文章文字貼給我。")
    return _write(ArticlePaths(Path(project_dir) / "article"), doc)


def run_extract(project_dir: str, source: str) -> int:
    """指令列 `extract`：成功印摘要、回 0；擷取不下去印繁中說明到 stderr、回 1。source 是 - 時從標準輸入讀。"""
    from article_to_video.textio import STDIN, TextInputError, read_stdin

    try:
        if source == STDIN:
            art = extract_article(project_dir, text=read_stdin())
        else:
            art = extract_article(project_dir, source)
    except (ArticleError, TextInputError) as e:
        print(f"擷取失敗：{e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"擷取失敗：檔案讀寫出錯（{e.strerror or e}：{e.filename or ''}）。請確認專案資料夾可以寫入、磁碟還有空間。",
              file=sys.stderr)
        return 1
    print_extract_summary(art)
    return 0


def print_extract_summary(art: ExtractedArticle, where: Path | None = None) -> None:
    """擷取成功後印的摘要（段數、字數、每張原圖）。where：article/ 搬過家時給新的位置。"""
    print(f"已擷取到 {where or art.paths.root}")
    chars = sum(len(p.text) for p in art.paragraphs)
    print(f"全文：{len(art.paragraphs)} 段、{chars} 字 → {art.paths.text.name}")
    print(f"原圖：{len(art.images)} 張" + ("" if art.images else "（這份文章沒有抽到圖）"))
    for im in art.images:
        size = f"{im.width}×{im.height}" if im.width else "大小不明"
        page = f"  第 {im.page} 頁" if im.page else ""
        cap = f"  圖說：{im.caption}" if im.caption else ""
        print(f"  {im.label}  {size}{page}  images/{im.path.name}{cap}")
    skipped = art.manifest.get("skipped_images") or []
    if skipped:
        print(f"（略過 {len(skipped)} 張：{'、'.join(sorted({s['reason'] for s in skipped}))}）")


_CONVERT_HINTS = {
    ".doc": "這是舊版 Word 檔，請在 Word 打開後用「檔案 → 另存新檔」，格式選「Word 文件 (.docx)」。",
    ".pages": "這是 Pages 檔，請在 Pages 用「檔案 → 輸出至 → Word…」存成 .docx。",
    ".odt": "請用「檔案 → 下載 → Microsoft Word (.docx)」或另存成 .docx。",
    ".rtf": "請用「文字編輯」或 Word 另存成 .docx。",
}


def _unsupported_message(path: Path) -> str:
    hint = _CONVERT_HINTS.get(path.suffix.lower(), "")
    return (f"「{path.name}」這種檔案還不能讀。{hint}"
            "可以給 PDF、Word（.docx）、純文字（.txt）、Markdown（.md），或 Google 文件下載的網頁 zip；"
            "Google 文件最方便的是「檔案 → 下載 → Microsoft Word (.docx)」。也可以直接把文章文字貼給我（但這樣抓不到文章裡的圖）。")


def _read_pasted(text: str) -> _Doc:
    return _Doc(kind="text", suffix=".txt", raw=text.encode("utf-8"), name=None,
                blocks=[Block(text=p) for p in paragraphs_from_plain_text(text)])


# ---------------------------------------------------------------- PDF


def _read_pdf(path: Path) -> _Doc:
    from article_to_video.article_pdf import PdfOpenError, read_pdf

    try:
        content = read_pdf(path)
    except PdfOpenError as e:
        if e.reason == "password":
            raise ArticleError(f"「{path.name}」有設密碼，打不開。請存一份沒有密碼的 PDF 再給我。") from None
        raise ArticleError(f"「{path.name}」打不開，檔案可能壞了。請重新存一次 PDF，或改給 Word（.docx）檔。") from None
    if content.truncated:
        raise _truncation_error(path, content.truncated)
    if content.char_count < MIN_TEXT_CHARS:
        kind = "整份都是圖片（像掃描檔或截圖存的 PDF）" if content.image_count else "是空的"
        raise ArticleError(f"「{path.name}」裡面沒有文字可以抓：{kind}。請給有文字的 PDF（例如從網頁或 Word「存成 PDF」的那種），"
                           "或改給 Word（.docx）檔，或直接把文章文字貼給我。")
    return _Doc(kind="pdf", suffix=".pdf", raw=path.read_bytes(), name=path.name, blocks=content.blocks,
                pages=content.pages, skipped_images=content.skipped_images)


def _truncation_error(path: Path, truncated: list) -> TruncatedPdfError:
    pages: dict[int, int] = {}
    for t in truncated:
        pages[t.page] = pages.get(t.page, 0) + 1
    where = "、".join(f"第 {p} 頁 {n} 行" for p, n in sorted(pages.items()))
    examples = [t for t in truncated if t.visible_tail][:3]
    lines = [
        f"「{path.name}」有字被頁面切掉了：{where}，行尾超出紙張邊界，超出去的字沒有存進 PDF。",
    ]
    for t in examples:
        lines.append(f"  例如第 {t.page} 頁「……{t.visible_tail}」後面就斷了。")
    lines += [
        "照這份做影片會漏內容、講錯話。請重存一份 PDF，存的時候「縮放到符合頁面」（把縮放比例調小，讓每一行都印得進紙裡）：",
        "  ・用 Chrome 存的：回到原本的文章網頁，按 ⌘P 打開列印視窗 →「目的地」選「另存為 PDF」→ 點「顯示更多設定」→"
        "「縮放比例」改成「自訂」，填 80（還是被切就再調小）→ 按「儲存」。",
        "  ・用 Safari 存的：按 ⌘P →「縮放比例」改小（例如 80%）→ 左下角「PDF」選單 →「儲存為PDF⋯」。",
        "  ・從 Word、Pages 印的：按 ⌘P → 在「紙張處理」裡勾「縮放來符合紙張大小」→ 左下角「PDF」選單 →「儲存為PDF⋯」。",
        "文章如果在 Google 文件或 Word 裡，直接給我 .docx 檔最省事（「檔案 → 下載 → Microsoft Word (.docx)」）。",
    ]
    return TruncatedPdfError("\n".join(lines), pages)


# ---------------------------------------------------------------- Word


def _read_docx(path: Path) -> _Doc:
    from article_to_video.article_docx import read_docx

    try:
        blocks = read_docx(path)
    except ArticleReadError:
        raise ArticleError(f"「{path.name}」打不開，檔案可能壞了，或不是真的 Word 檔。請在 Word 或 Google 文件重新存一次 .docx 再給我。") from None
    return _Doc(kind="docx", suffix=".docx", raw=path.read_bytes(), name=path.name, blocks=blocks)


# ---------------------------------------------------------------- 網頁（Google 文件「下載 → 網頁」的 zip，或 .html）


def _read_html(path: Path) -> _Doc:
    from article_to_video.article_html import read_html_file, read_html_zip

    try:
        blocks, skipped = (read_html_zip if path.suffix.lower() == ".zip" else read_html_file)(path)
    except ArticleReadError:
        raise ArticleError(f"「{path.name}」讀不出文章。zip 檔只收 Google 文件「檔案 → 下載 → 網頁（.html，已壓縮）」"
                           "下載的那種；不然請改給 Word（.docx）或 PDF。") from None
    return _Doc(kind="html", suffix=path.suffix.lower(), raw=path.read_bytes(), name=path.name, blocks=blocks,
                skipped_images=skipped)


# ---------------------------------------------------------------- 純文字、Markdown


def _decode_text(path: Path, raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ArticleError(f"「{path.name}」不是 UTF-8 的文字檔，讀出來會變亂碼。請用「文字編輯」打開，"
                           "「檔案 → 儲存」時把「純文字編碼」選「Unicode (UTF-8)」再給我；或直接把文字貼給我。") from None


def _read_text_file(path: Path) -> _Doc:
    raw = path.read_bytes()
    blocks = [Block(text=p) for p in paragraphs_from_plain_text(_decode_text(path, raw))]
    return _Doc(kind="text", suffix=".txt", raw=raw, name=path.name, blocks=blocks)


def _read_markdown_file(path: Path) -> _Doc:
    from article_to_video.article_markdown import read_markdown

    raw = path.read_bytes()
    base = path.parent.resolve()

    def load(src: str) -> bytes | None:
        f = (base / src).resolve()
        return f.read_bytes() if base in f.parents and f.is_file() else None

    blocks, skipped = read_markdown(_decode_text(path, raw), load)
    return _Doc(kind="markdown", suffix=".md", raw=raw, name=path.name, blocks=blocks, skipped_images=skipped)


_READERS: dict[str, Callable[[Path], _Doc]] = {
    ".pdf": _read_pdf,
    ".docx": _read_docx,
    ".zip": _read_html,
    ".html": _read_html,
    ".htm": _read_html,
    ".txt": _read_text_file,
    ".md": _read_markdown_file,
    ".markdown": _read_markdown_file,
}


# ---------------------------------------------------------------- 寫出


def _write(paths: ArticlePaths, doc: _Doc) -> ExtractedArticle:
    paragraphs: list[Paragraph] = []
    placed: list[tuple[int, Block, int | None]] = []  # (在 blocks 的位置, 圖, 圖前面最近的段落編號)
    para_of_block: dict[int, Paragraph] = {}
    for i, b in enumerate(doc.blocks):
        if b.text is not None:
            para = Paragraph(n=len(paragraphs) + 1, text=b.text, page=b.page)
            paragraphs.append(para)
            para_of_block[i] = para
        elif b.image is not None:
            placed.append((i, b, paragraphs[-1].n if paragraphs else None))

    root = paths.root
    root.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".article-", dir=root.parent))  # 先寫到暫存資料夾，全部成功才換上去
    try:
        stage = ArticlePaths(tmp)
        stage.source(doc.suffix).write_bytes(doc.raw)
        stage.text.write_text("\n\n".join(p.text for p in paragraphs) + "\n", encoding="utf-8")
        stage.images.mkdir()
        images: list[ArticleImage] = []
        for n, (i, b, before) in enumerate(placed, 1):
            assert b.image is not None
            nxt = doc.blocks[i + 1] if i + 1 < len(doc.blocks) else None
            after = next((para_of_block[j].n for j in range(i + 1, len(doc.blocks)) if j in para_of_block), None)
            caption = None
            if (nxt is not None and nxt.text is not None and nxt.page == b.page and looks_like_caption(nxt.text)):
                caption = nxt.text
            name = f"圖{n}{b.image.ext}"
            (stage.images / name).write_bytes(b.image.data)
            images.append(ArticleImage(n=n, path=paths.images / name, width=b.image.width, height=b.image.height,
                                       page=b.page, caption=caption, before=before, after=after))
        manifest = {
            "version": MANIFEST_VERSION,
            "source": {"file": stage.source(doc.suffix).name, "name": doc.name, "kind": doc.kind,
                       "pages": doc.pages, "sha256": hashlib.sha256(doc.raw).hexdigest()},
            "text": stage.text.name,
            "paragraphs": [{"n": p.n, "text": p.text, "page": p.page} for p in paragraphs],
            "images": [{"n": im.n, "label": im.label, "file": f"images/{im.path.name}", "width": im.width,
                        "height": im.height, "page": im.page, "caption": im.caption, "before": im.before,
                        "after": im.after} for im in images],
            "skipped_images": doc.skipped_images,
        }
        stage.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _replace_outputs(stage, paths)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ExtractedArticle(paths=paths, kind=doc.kind, paragraphs=paragraphs, images=images, manifest=manifest)


def _replace_outputs(stage: ArticlePaths, paths: ArticlePaths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    for old in paths.root.glob("source.*"):
        old.unlink()
    shutil.rmtree(paths.images, ignore_errors=True)
    for f in (paths.text, paths.manifest, paths.figure_numbers):  # 讀舊圖記下的數字：圖換了就不算數
        f.unlink(missing_ok=True)
    for item in stage.root.iterdir():
        os.replace(item, paths.root / item.name)

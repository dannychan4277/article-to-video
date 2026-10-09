"""文章擷取（#6）：作者丟來的文章 → <專案>/article/（原檔、全文、原圖、article.json）。

測試素材全部在這裡自己做：PDF 用 Chromium 把自寫的 HTML 印出來（跟作者用 Chrome「另存為 PDF」同一條路），
docx 用 python-docx 產生，圖用 Pillow 畫。沒有任何作者的文章內容，也不連網。
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

from article_to_video.article import ArticleError, extract_article
from article_to_video.clips import FONTS_DIR


def _manifest(project: Path) -> dict:
    return json.loads((project / "article" / "article.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 自製素材


def _chart_png() -> bytes:
    """1200×800 的自製「圖表」：每個像素都不一樣，抽出來只要被縮放或重壓過就對不上。"""
    img = Image.new("RGB", (1200, 800))
    img.putdata([((x * 7) % 256, (y * 5) % 256, (x + y) % 256) for y in range(800) for x in range(1200)])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _photo_jpeg() -> bytes:
    img = Image.new("RGB", (900, 600))
    img.putdata([((x // 3) % 256, (y // 2) % 256, 128) for y in range(600) for x in range(900)])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def _icon_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (220, 120, 0)).save(buf, "PNG")
    return buf.getvalue()


def _data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


TITLE = "自製測試文章：利率與時間"
P1 = ("這是一篇自己寫的測試文章，用來確認擷取出來的文字一字不差。這一段故意寫得比較長，"
      "長到在紙上一定會換行，換行之後兩行要接回同一段，中間不能多出空白。")
CAPTION1 = "圖一：自製的測試圖表，每個像素顏色都不一樣"
P2 = "第二段在第一張圖的下面，說明短天期和長天期的差別。"
P3 = "第二頁從第二張圖開始，這一段在圖的下面。"
LONG = "長天期利率反映市場對未來的預期，" * 110 + "這一段很長、會跨過兩頁，擷取後要接回一段。"
END = "最後一段，全文結束。"
NORMAL_PARAGRAPHS = [TITLE, P1, CAPTION1, P2, P3, LONG, END]

_FONT_CSS = f"@font-face {{ font-family: T; src: url('{(FONTS_DIR / 'NotoSansTC-500.woff').as_uri()}'); }}"


def _html(body: str, *, title: str = "列印測試頁") -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><style>{_FONT_CSS}"
            "body { font-family: T; font-size: 13pt; line-height: 1.6; } p { margin: 0 0 14pt; }"
            "</style></head><body>" + body + "</body></html>")


def _normal_html() -> str:
    return _html(
        f"<h1><img src='{_data_uri(_icon_png(), 'image/png')}' style='width:24px'> {TITLE}</h1>"
        f"<p>{P1}</p>"
        f"<p><img src='{_data_uri(_chart_png(), 'image/png')}' style='width:420px'></p>"
        f"<p>{CAPTION1}</p>"
        f"<p>{P2}</p>"
        f"<p style='break-before: page'><img src='{_data_uri(_photo_jpeg(), 'image/jpeg')}' style='width:360px'></p>"
        f"<p>{P3}</p>"
        f"<p>{LONG}</p>"
        f"<p>{END}</p>"
    )


TRUNCATED_TEXT = ("這一段放在固定寬度的版面裡，版面比紙張能印的範圍還寬，所以每一行最後幾個字都會被切掉，"
                  "存成 PDF 之後那些字就不見了。") * 6


def _truncated_html() -> str:
    """像有些網站把內文欄寬寫死：欄寬比 A4 扣掉邊界寬太多，Chrome 縮到極限還放不下，行尾就被切掉。"""
    return _html(f"<div style='width: 1800px'><h2>{TITLE}</h2><p>{TRUNCATED_TEXT}</p><p>{END}</p></div>")


# Chrome 列印預設的頁首頁尾：日期＋標題、網址＋頁碼（每頁都有，擷取時要拿掉）
_HEADER = "<div style='font-size:9px; width:100%; padding:0 28px'><span class='date'></span>　<span class='title'></span></div>"
_FOOTER = ("<div style='font-size:9px; width:100%; padding:0 28px'>file:///測試/文章.html　"
           "<span class='pageNumber'></span>/<span class='totalPages'></span></div>")


@dataclass(frozen=True)
class SamplePdfs:
    normal: Path
    truncated: Path
    scanned: Path  # 整頁只有一張圖、沒有文字層（像掃描檔）


@pytest.fixture(scope="session")
def sample_pdfs(tmp_path_factory) -> SamplePdfs:
    """用 Chromium 把自寫的 HTML 印成 PDF（跟作者在 Chrome 按「另存為 PDF」同一條路）。"""
    from playwright.sync_api import sync_playwright

    out = tmp_path_factory.mktemp("pdf")
    margin = {"top": "40px", "bottom": "40px", "left": "38px", "right": "38px"}
    wide_margin = {"top": "60px", "bottom": "60px", "left": "60px", "right": "60px"}
    jobs = {  # 名稱: (HTML, 邊界, 要不要 Chrome 的頁首頁尾)
        "normal": (_normal_html(), margin, True),
        "truncated": (_truncated_html(), wide_margin, True),
        "scanned": (_html(f"<img src='{_data_uri(_photo_jpeg(), 'image/jpeg')}' style='width:100%'>"), margin, False),
    }
    paths = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for name, (html, page_margin, header_footer) in jobs.items():
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                page.evaluate("document.fonts.ready")
                paths[name] = out / f"{name}.pdf"
                page.pdf(path=str(paths[name]), format="A4", display_header_footer=header_footer,
                         header_template=_HEADER, footer_template=_FOOTER, margin=page_margin)
                page.close()
        finally:
            browser.close()
    return SamplePdfs(**paths)


# ---------------------------------------------------------------- PDF


def test_pdf_text_layer_becomes_clean_paragraphs(tmp_path, sample_pdfs):
    project = tmp_path / "專案"
    extract_article(project, sample_pdfs.normal)

    article = project / "article"
    assert (article / "source.pdf").read_bytes() == sample_pdfs.normal.read_bytes()
    manifest = _manifest(project)
    # 頁首頁尾（日期、標題、網址、頁碼）拿掉；換行接回；跨頁的段落接回一段
    assert [p["text"] for p in manifest["paragraphs"]] == NORMAL_PARAGRAPHS
    assert [p["page"] for p in manifest["paragraphs"]][:5] == [1, 1, 1, 1, 2]
    text = (article / "text.txt").read_text(encoding="utf-8")
    assert text == "\n\n".join(NORMAL_PARAGRAPHS) + "\n"
    # Chrome 的字型子集會把「文」「一」對到康熙部首（⽂ ⼀），要還原成一般漢字
    assert not [c for c in text if unicodedata.name(c, "").startswith("KANGXI RADICAL")]


def test_pdf_images_are_full_resolution_originals_numbered_in_order(tmp_path, sample_pdfs):
    project = tmp_path / "專案"
    extract_article(project, sample_pdfs.normal)

    article = project / "article"
    manifest = _manifest(project)
    images = manifest["images"]
    assert [im["label"] for im in images] == ["圖1", "圖2"]  # 24px 的小圖示不算文章原圖
    first, second = images
    assert (first["file"], first["width"], first["height"], first["page"]) == ("images/圖1.png", 1200, 800, 1)
    assert (second["file"], second["width"], second["height"], second["page"]) == ("images/圖2.jpg", 900, 600, 2)
    # 完整解析度的原圖：PNG 每個像素都一樣，JPEG 連位元組都一樣（沒有重壓）
    with Image.open(article / first["file"]) as got, Image.open(io.BytesIO(_chart_png())) as want:
        assert got.size == want.size
        assert got.convert("RGB").tobytes() == want.convert("RGB").tobytes()
    assert (article / second["file"]).read_bytes() == _photo_jpeg()
    # 圖說與前後段落（段落編號對到 paragraphs 的 n）
    n = {p["text"]: p["n"] for p in manifest["paragraphs"]}
    assert (first["caption"], first["before"], first["after"]) == (CAPTION1, n[P1], n[CAPTION1])
    assert (second["caption"], second["before"], second["after"]) == (None, n[P2], n[P3])


def test_pasted_text_is_saved_as_paragraphs(tmp_path):
    project = tmp_path / "影片" / "2026-10-09-測試"
    pasted = "短天期利率比較高。\n\n長天期利率比較低，\n這一行是同一段的後半。\n"

    extract_article(project, text=pasted)

    article = project / "article"
    assert (article / "source.txt").read_text(encoding="utf-8") == pasted
    assert (article / "text.txt").read_text(encoding="utf-8") == (
        "短天期利率比較高。\n\n長天期利率比較低，這一行是同一段的後半。\n"
    )
    manifest = _manifest(project)
    assert [p["text"] for p in manifest["paragraphs"]] == [
        "短天期利率比較高。",
        "長天期利率比較低，這一行是同一段的後半。",
    ]
    assert [p["n"] for p in manifest["paragraphs"]] == [1, 2]
    assert manifest["images"] == []
    assert manifest["source"]["kind"] == "text"


def test_pdf_with_text_cut_off_at_the_page_edge_is_rejected_with_how_to_resave(tmp_path, sample_pdfs):
    project = tmp_path / "專案"
    extract_article(project, text="先前擷取的文章。")  # 已經有一份擷取結果

    with pytest.raises(ArticleError) as err:
        extract_article(project, sample_pdfs.truncated)

    msg = str(err.value)
    assert "縮放到符合頁面" in msg
    assert "第 1 頁" in msg
    # 舉的例子是被切掉那一行還看得到的部分，真的是原文的一段
    examples = re.findall(r"「……(.+?)」後面就斷了", msg)
    assert examples and all(e in TRUNCATED_TEXT for e in examples)
    assert set(err.value.pages) == {1} and err.value.pages[1] >= 3
    # 出錯時 article/ 完全不動
    article = project / "article"
    assert (article / "text.txt").read_text(encoding="utf-8") == "先前擷取的文章。\n"
    assert sorted(f.name for f in article.iterdir()) == ["article.json", "images", "source.txt", "text.txt"]


def test_pdf_without_a_text_layer_is_rejected(tmp_path, sample_pdfs):
    with pytest.raises(ArticleError, match="沒有文字"):
        extract_article(tmp_path / "專案", sample_pdfs.scanned)
    assert not (tmp_path / "專案" / "article").exists()


@pytest.mark.parametrize(("name", "hint"), [("文章.pages", "輸出至"), ("文章.doc", "另存新檔"), ("文章.key", ".docx")])
def test_unsupported_files_say_what_to_send_instead(tmp_path, name, hint):
    src = tmp_path / name
    src.write_bytes(b"not really")
    with pytest.raises(ArticleError, match=hint):
        extract_article(tmp_path / "專案", src)


def test_nothing_to_read_is_an_error_not_an_empty_article(tmp_path):
    empty = tmp_path / "空白.txt"
    empty.write_text("\n\n  \n", encoding="utf-8")
    for kwargs in ({"source": empty}, {"text": "   \n"}):
        with pytest.raises(ArticleError, match="沒有文字"):
            extract_article(tmp_path / "專案", **kwargs)
    assert not (tmp_path / "專案" / "article").exists()


# ---------------------------------------------------------------- Word／Google 文件匯出的 .docx


def _make_docx(path: Path) -> None:
    """用 python-docx 做一份 Word 檔：標題、內文、圖＋圖說、表格、段落中間夾一張圖。"""
    import docx
    from docx.shared import Inches

    d = docx.Document()
    d.add_heading(TITLE, level=1)
    d.add_paragraph(P1)
    d.add_picture(io.BytesIO(_chart_png()), width=Inches(4))
    d.add_paragraph(CAPTION1)
    d.add_paragraph(P2)
    table = d.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "兩年期"
    table.cell(0, 1).text = "十年期"
    p = d.add_paragraph("圖片前面的字，")
    p.add_run().add_picture(io.BytesIO(_photo_jpeg()), width=Inches(3))
    p.add_run("圖片後面的字。")
    d.add_paragraph(END)
    d.save(str(path))


def test_docx_text_and_original_images(tmp_path):
    src = tmp_path / "我的文章.docx"
    _make_docx(src)
    project = tmp_path / "專案"

    extract_article(project, src)

    article = project / "article"
    assert (article / "source.docx").read_bytes() == src.read_bytes()
    manifest = _manifest(project)
    assert manifest["source"]["kind"] == "docx" and manifest["source"]["name"] == "我的文章.docx"
    texts = [p["text"] for p in manifest["paragraphs"]]
    assert texts == [TITLE, P1, CAPTION1, P2, "兩年期", "十年期", "圖片前面的字，圖片後面的字。", END]
    n = {t: i + 1 for i, t in enumerate(texts)}
    first, second = manifest["images"]
    assert (first["file"], first["width"], first["height"], first["page"]) == ("images/圖1.png", 1200, 800, None)
    assert (first["caption"], first["before"], first["after"]) == (CAPTION1, n[P1], n[CAPTION1])
    assert (second["file"], second["width"], second["height"]) == ("images/圖2.jpg", 900, 600)
    assert (second["before"], second["after"], second["caption"]) == (n["十年期"], n["圖片前面的字，圖片後面的字。"], None)
    # Word 檔裡存的就是原圖：位元組照搬
    assert (article / "images" / "圖1.png").read_bytes() == _chart_png()
    assert (article / "images" / "圖2.jpg").read_bytes() == _photo_jpeg()


# ---------------------------------------------------------------- Google 文件的其他匯出格式


def _google_docs_html(img1: str, img2: str) -> str:
    """仿 Google 文件「下載 → 網頁」的樣子：head 裡一大段 CSS、每段是 p＞span、圖包在 span 裡、空段落當間距。"""
    return (
        "<html><head><meta content='text/html; charset=UTF-8' http-equiv='content-type'>"
        "<style type='text/css'>.c1{font-size:11pt}.c2{height:11pt}</style></head>"
        f"<body class='c3 doc-content'><h1 class='c4'><span class='c1'>{TITLE}</span></h1>"
        f"<p class='c5'><span class='c1'>{P1[:30]}</span><span class='c1'>{P1[30:]}</span></p>"
        "<p class='c2'><span class='c1'></span></p>"
        f"<p class='c5'><span style='overflow: hidden; display: inline-block;'><img alt='' src='{img1}' "
        "style='width: 624.00px; height: 416.00px;' title=''></span></p>"
        f"<p class='c5'><span class='c1'>{CAPTION1}</span></p>"
        f"<ul class='c6'><li class='c7'><span class='c1'>{P2}</span></li></ul>"
        "<table class='c8'><tr><td class='c9'><p class='c5'><span class='c1'>兩年期</span></p></td>"
        "<td class='c9'><p class='c5'><span class='c1'>十年期</span></p></td></tr></table>"
        f"<p class='c5'><span><img alt='' src='{img2}'></span></p>"
        f"<p class='c5'><span class='c1'>{END}&nbsp;</span></p></body></html>"
    )


def test_google_docs_web_page_zip(tmp_path):
    src = tmp_path / "我的文章.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("我的文章.html", _google_docs_html("images/image1.png", "images/image2.jpg"))
        z.writestr("images/image1.png", _chart_png())
        z.writestr("images/image2.jpg", _photo_jpeg())
    project = tmp_path / "專案"

    extract_article(project, src)

    manifest = _manifest(project)
    assert manifest["source"]["kind"] == "html"
    texts = [p["text"] for p in manifest["paragraphs"]]
    assert texts == [TITLE, P1, CAPTION1, P2, "兩年期", "十年期", END]
    first, second = manifest["images"]
    assert (first["width"], first["height"], first["caption"]) == (1200, 800, CAPTION1)
    assert (project / "article" / first["file"]).read_bytes() == _chart_png()
    assert (project / "article" / second["file"]).read_bytes() == _photo_jpeg()
    assert (second["before"], second["after"]) == (texts.index("十年期") + 1, texts.index(END) + 1)


def test_google_docs_plain_text_export(tmp_path):
    """Google 文件「下載 → 純文字」：開頭有 BOM、換行是 CRLF、段落之間空一行。"""
    src = tmp_path / "我的文章.txt"
    src.write_bytes(("﻿" + f"{TITLE}\r\n\r\n{P1}\r\n\r\n{P2}\r\n").encode("utf-8"))
    project = tmp_path / "專案"

    extract_article(project, src)

    assert (project / "article" / "source.txt").read_bytes() == src.read_bytes()
    assert [p["text"] for p in _manifest(project)["paragraphs"]] == [TITLE, P1, P2]


def test_google_docs_markdown_export_with_embedded_image(tmp_path):
    """Google 文件「下載 → Markdown」：標記符號、跳脫字元、圖片以 data URI 附在文末。"""
    md = (f"# {TITLE}\n\n{P1}\n\n![][image1]\n\n{CAPTION1}\n\n**{P2}**\n\n"
          f"* 第一點：利率 4\\.58%\n\n[image1]: <{_data_uri(_chart_png(), 'image/png')}>\n")
    src = tmp_path / "我的文章.md"
    src.write_text(md, encoding="utf-8")
    project = tmp_path / "專案"

    extract_article(project, src)

    manifest = _manifest(project)
    assert [p["text"] for p in manifest["paragraphs"]] == [TITLE, P1, CAPTION1, P2, "第一點：利率 4.58%"]
    (image,) = manifest["images"]
    assert (image["width"], image["height"], image["caption"], image["before"]) == (1200, 800, CAPTION1, 2)
    assert (project / "article" / image["file"]).read_bytes() == _chart_png()


# ---------------------------------------------------------------- 指令列


def _cli(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", "article_to_video", "extract", *args], input=stdin,
                          capture_output=True, text=True, encoding="utf-8", env=env)


def test_cli_extracts_a_pdf_and_reports_what_it_found(tmp_path, sample_pdfs):
    project = tmp_path / "影片" / "解說影片 測試" / "2026-10-09-利率"
    proc = _cli(str(project), str(sample_pdfs.normal))

    assert proc.returncode == 0, proc.stderr
    assert "圖1" in proc.stdout and "1200×800" in proc.stdout and "圖2" in proc.stdout
    assert (project / "article" / "images" / "圖1.png").is_file()


def test_cli_rejects_a_cut_off_pdf_in_plain_chinese(tmp_path, sample_pdfs):
    proc = _cli(str(tmp_path / "專案"), str(sample_pdfs.truncated))

    assert proc.returncode == 1
    assert "縮放到符合頁面" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not (tmp_path / "專案" / "article").exists()


def test_cli_reads_pasted_text_from_stdin(tmp_path):
    project = tmp_path / "專案"
    proc = _cli(str(project), "-", stdin="第一段。\n第二段。\n")

    assert proc.returncode == 0, proc.stderr
    assert (project / "article" / "text.txt").read_text(encoding="utf-8") == "第一段。\n\n第二段。\n"

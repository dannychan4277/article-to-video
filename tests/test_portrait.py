"""直式 9:16：作者說「直式」時，整條流程改出 1080×1920（建置、分鏡靜態圖、審片頁、字幕位置）。

主切點同 test_build.py：CLI 子行程 `build <專案> --aspect 9:16 --tts fake`。為了省時間，直接拿 session 已經建好
16:9 的測試專案複製一份再出直式（配音快取沿用），順便驗證兩種比例的產物並存、互不覆蓋。

平台介面區（短影音平台蓋在畫面上的地方）的數字是這裡獨立寫死的「規格」，不從被測程式讀：
頂端 240 px、底部 540 px、右側 140 px（理由見 timeline.FORMAT_9x16 的說明）。字幕與圖表的字都不能落進去。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest
from playwright.sync_api import sync_playwright

from article_to_video.clips import render_stills
from article_to_video.ffmpeg import find_ffmpeg
from article_to_video.subtitles import SUBTITLE_FONT_FILE, SubtitleLine, check_libass_log, write_ass
from article_to_video.timeline import FORMAT_9x16

from conftest import MINI_PROJECT, TEST_CHANNEL, run_cli
from test_build import DUR, FPS, GAP, TAIL, ffprobe, frames_rgb
from test_render_clip import png_size
from test_review_page import _Parser

W, H = 1080, 1920
UI_TOP, UI_BOTTOM, UI_RIGHT = 240, 540, 140


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def portrait(built_project, tmp_path_factory):
    """把 session 建好 16:9 的測試專案複製一份，再用 CLI 出直式。"""
    root = tmp_path_factory.mktemp("影片") / "解說影片 直式" / built_project.dir.name
    shutil.copytree(built_project.dir, root)
    landscape = sha(root / "成片.mp4")
    proc = run_cli("build", str(root), "--aspect", "9:16", "--tts", "fake")
    assert proc.returncode == 0, f"直式建置失敗：\n{proc.stdout}\n{proc.stderr}"
    return {"dir": root, "video": root / "成片-9x16.mp4", "landscape_sha": landscape, "stdout": proc.stdout}


def test_unknown_aspect_is_refused_in_plain_chinese(tmp_path):
    proc = run_cli("preview", str(tmp_path), "--aspect", "4:3")
    assert proc.returncode != 0
    assert "畫面比例「4:3」看不懂，只能是 16:9（橫式）或 9:16（直式）" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_portrait_build_is_1080x1920_h264_with_audio(portrait):
    out = ffprobe("-show_entries", "stream=codec_type,codec_name,width,height", "-of", "json", str(portrait["video"]))
    streams = {s["codec_type"]: s for s in json.loads(out)["streams"]}
    assert streams["video"]["codec_name"] == "h264"
    assert (streams["video"]["width"], streams["video"]["height"]) == (W, H)
    assert streams["audio"]["codec_name"] == "aac"
    assert (portrait["dir"] / "字幕-9x16.srt").read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------- 字幕：位置、字級、折行


def burn_subtitle(tmp_path: Path, text: str, fmt) -> bytes:
    """用跟組裝一樣的 libass（subtitles 濾鏡＋外掛附帶字型）把一行字幕燒在白底上，回傳 RGB24 像素。"""

    (tmp_path / "fonts").mkdir()
    shutil.copyfile(SUBTITLE_FONT_FILE, tmp_path / "fonts" / SUBTITLE_FONT_FILE.name)
    write_ass([SubtitleLine(0.0, 1.0, text, "s01")], tmp_path / "subs.ass", fmt)
    proc = subprocess.run(
        [str(find_ffmpeg()), "-v", "error", "-f", "lavfi", "-i", f"color=white:s={fmt.width}x{fmt.height}:d=1:r=1",
         "-vf", "subtitles=subs.ass:fontsdir=fonts", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    assert len(proc.stdout) == fmt.width * fmt.height * 3
    return proc.stdout


def dark_box(px: bytes, w: int, h: int, y0: int = 0, y1: int | None = None, x0: int = 0, x1: int | None = None):
    """深色像素（字幕深底框、圖表的黑字）的外框 (x_min, y_min, x_max, y_max)，沒有就 None。"""
    xs, ys = [], []
    for y in range(y0, h if y1 is None else y1):
        row = px[y * w * 3: (y + 1) * w * 3]
        for x in range(x0, w if x1 is None else x1):
            i = x * 3
            if max(row[i], row[i + 1], row[i + 2]) < 110:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def test_a_long_portrait_subtitle_wraps_inside_the_safe_area(tmp_path):
    """直式一行放不下（20 多個字、沒有標點）：要折成兩行，整個框都在平台介面區以內，也不超出畫面。"""
    px = burn_subtitle(tmp_path, "這一句故意寫得很長中間沒有任何標點符號看會不會折行", FORMAT_9x16)
    x_min, y_min, x_max, y_max = dark_box(px, W, H)
    assert x_min > 0 and x_max < W - UI_RIGHT, (x_min, x_max)  # 沒有被畫面切掉，也沒壓到右側按鈕欄
    assert y_max < H - UI_BOTTOM, y_max  # 底部的帳號、說明、音樂列之上
    assert y_max - y_min > 1.5 * (64 + 2 * 12), (y_min, y_max)  # 兩行（一行的框＝字級 64＋上下內距 12）
    assert y_min >= H - FORMAT_9x16.safe_bottom  # 兩行也還在留給字幕的那一條裡（圖表的字都在它上面）


def test_portrait_subtitles_sit_above_the_platform_ui_and_clear_of_the_buttons(portrait):
    """成片 2.0 秒（「平均四點五八」）：字幕框在底部平台介面區之上、右側按鈕欄之左，一行的高度；
    3.73 秒（兩段之間，沒有字幕）：圖表的字也沒有掉進字幕那一條。"""
    top = 1100  # 圖表的來源列在這上面
    during, between = (frames_rgb(portrait["video"], n, n, (0, top, W, H - top))[0] for n in (60, 112))
    x_min, y_min, x_max, y_max = dark_box(during, W, H - top)
    y_min, y_max = y_min + top, y_max + top
    assert y_max < H - UI_BOTTOM, y_max
    assert x_min > 0 and x_max < W - UI_RIGHT, (x_min, x_max)
    assert 80 <= y_max - y_min <= 100, (y_min, y_max)  # 一行：直式字級 64＋上下內距
    assert dark_box(between, W, H - top) is None


def test_portrait_cues_land_on_the_same_frames_as_landscape(portrait):
    """換比例只換排版，時間軸一幀都不動：每個分鏡的起訖、每個 cue 的出現幀、片尾卡都跟 16:9 一樣。"""
    def timing(variant: str) -> dict:
        tl = json.loads((portrait["dir"] / "build" / variant / "timeline.json").read_text(encoding="utf-8"))
        return {"total": tl["totalFrames"], "outro": tl["outro"],
                "shots": [(s["id"], s["startFrame"], s["endFrame"], [(c["kind"], c["frame"]) for c in s["cues"]])
                          for s in tl["shots"]]}

    assert timing("9x16") == timing("16x9")


def test_portrait_length_is_narration_plus_gaps_plus_outro(portrait):
    expected = DUR["s01"] + GAP + DUR["s02"] + TAIL  # 8.4 秒，跟 16:9 同一條公式
    frames = int(ffprobe("-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
                         "-of", "csv=p=0", str(portrait["video"])).strip())
    assert abs(frames - expected * FPS) <= 1


def test_portrait_subtitles_use_the_bundled_font(portrait):
    log = (portrait["dir"] / "build" / "9x16" / "logs" / "assemble.log").read_text(encoding="utf-8")
    assert "Loading font file 'fonts/NotoSansTC-500.woff'" in log
    assert check_libass_log(log) == []  # 只選到附帶字型（記憶體字型），沒有系統字型、沒有缺字換字型


def test_reporting_an_issue_after_a_portrait_build_packs_portrait_stills(portrait, tmp_path):
    """最後一次是出直式時回報問題：打包的出錯分鏡靜態圖要是直式的（直式才有的排版問題才看得到）。"""
    import zipfile

    proc = run_cli("report-issue", str(portrait["dir"]), "--shot", "1", "--out-dir", str(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    [zpath] = tmp_path.glob("*.zip")
    with zipfile.ZipFile(zpath) as z:
        pngs = [n for n in z.namelist() if n.startswith("stills/SH01/") and n.endswith(".png")]
        assert pngs
        for n in pngs:
            (tmp_path / "x.png").write_bytes(z.read(n))
            assert png_size(tmp_path / "x.png") == (W, H), n


def test_portrait_and_landscape_outputs_live_side_by_side(portrait):
    d = portrait["dir"]
    assert sha(d / "成片.mp4") == portrait["landscape_sha"]  # 出直式沒有動到 16:9 的成片
    for name in ("成片.mp4", "字幕.srt", "審片頁.html", "成片-9x16.mp4", "字幕-9x16.srt", "審片頁-9x16.html"):
        assert (d / name).is_file(), name
    assert (d / "build" / "16x9" / "clips").is_dir() and (d / "build" / "9x16" / "clips").is_dir()


# ---------------------------------------------------------------- 圖表與片尾卡的直式排版


def _ink(px: bytes) -> int:
    """深色字（< 110）或柱子的藍（B 明顯大於 R）的像素數。"""
    return sum(1 for i in range(0, len(px), 3)
               if max(px[i], px[i + 1], px[i + 2]) < 110 or (px[i + 2] - px[i] >= 12 and px[i] <= 215))


def test_portrait_charts_keep_text_out_of_the_platform_ui(portrait):
    """每個分鏡的最後一幀（慢推推得最大的時候）：標題、刻度、柱子、來源都不在頂端、底部、右側按鈕欄的平台介面區。"""
    tl = json.loads((portrait["dir"] / "build" / "9x16" / "timeline.json").read_text(encoding="utf-8"))
    for shot in tl["shots"]:
        n = shot["endFrame"] - 1
        top, right, bottom, title = (frames_rgb(portrait["video"], n, n, crop)[0] for crop in (
            (0, 0, W, UI_TOP), (W - UI_RIGHT, UI_TOP, UI_RIGHT, H - UI_TOP - UI_BOTTOM),
            (0, H - UI_BOTTOM, W, UI_BOTTOM), (0, UI_TOP, W - UI_RIGHT, 300)))
        assert _ink(top) == 0, f"{shot['id']} 有字跑到頂端平台介面區"
        assert _ink(right) == 0, f"{shot['id']} 有字或柱子壓到右側按鈕欄"
        assert _ink(bottom) == 0, f"{shot['id']} 有字跑到底部平台介面區"
        assert _ink(title) > 2000, f"{shot['id']} 標題不在平台頂端介面下面"


def test_portrait_end_card_stays_inside_the_platform_safe_area(portrait):
    """成片最後一幀是片尾卡：名字、來源、兩行聲明都在平台介面區以內（不會被帳號列、按鈕蓋住）。"""
    last = round((DUR["s01"] + GAP + DUR["s02"] + TAIL) * FPS) - 1
    top, right, bottom, middle = (frames_rgb(portrait["video"], last, last, crop)[0] for crop in (
        (0, 0, W, UI_TOP), (W - UI_RIGHT, 0, UI_RIGHT, H), (0, H - UI_BOTTOM, W, UI_BOTTOM),
        (0, UI_TOP, W - UI_RIGHT, H - UI_TOP - UI_BOTTOM)))
    assert _ink(top) == _ink(right) == _ink(bottom) == 0
    assert _ink(middle) > 5000  # 卡上的字都在中間這塊


def test_portrait_checks_look_at_the_portrait_subtitle_band_and_platform_ui(portrait, tmp_path):
    """自動檢查（#11）照直式查：圖表的字幕帶＝平台底部介面＋兩行字幕框＋間隔；片尾卡沒有字幕，查平台底部介面區。"""
    root = tmp_path / portrait["dir"].name
    shutil.copytree(portrait["dir"], root)
    proc = run_cli("checks", str(root), "--aspect", "9:16", "--tts", "fake")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads((root / "build" / "9x16" / "checks.json").read_text(encoding="utf-8"))
    sub_top = H - UI_BOTTOM - (2 * 64 + 2 * 12) - 24  # 兩行 64 號字幕的深底框＋框上下內距＋間隔
    assert {c["clip"]: c["band"].get("top") for c in summary["clips"]} == {
        "SH01": sub_top, "SH02": sub_top, "end.card": H - UI_BOTTOM}
    assert all(c["band"]["checked"] and c["qa"]["checked"] for c in summary["clips"])
    assert summary["blocking"] == [] and (root / "審片頁-9x16.html").is_file()


def write_article_png(path: Path, w: int = 900, h: int = 600) -> Path:
    """自製「文章裡的圖」：淺色紙底，右邊一塊深色數字塊（x 0.55–0.85、y 0.40–0.52）。只用標準庫寫 PNG。"""
    import struct
    import zlib

    rows = []
    for y in range(h):
        dark_row = 0.40 < y / h < 0.52
        rows.append(b"\x00" + b"".join(
            bytes((25, 25, 25)) if dark_row and 0.55 < x / w < 0.85 else bytes((240, 238, 230)) for x in range(w)))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))
    return path


def test_portrait_vox_circles_the_number_inside_the_safe_area(tmp_path):
    """Vox 剪報（#8 會接進建置）：用直式的 spec 共用部分，原圖貼上桌、紅圈推近之後，紅圈整圈都在
    平台介面區與字幕帶以內，不會被右側按鈕或底部帳號列蓋住。"""
    write_article_png(tmp_path / "文章圖.png")
    spec = {
        "grammar": "y2_vox", "duration": 4, **FORMAT_9x16.spec_base(), "data": {"drift": 0.008},
        "cues": [{"at": 0, "kind": "image", "image": "文章圖.png", "text": "自製文章圖"},
                 {"at": 1.0, "kind": "highlight", "data": {"rect": [0.55, 0.40, 0.30, 0.12]}}],
    }
    [still] = render_stills(spec, tmp_path / "stills", [3.5], base_dir=tmp_path).paths
    px = subprocess.run([str(find_ffmpeg()), "-v", "error", "-i", str(still), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                        capture_output=True, check=True).stdout
    assert len(px) == W * H * 3
    xs, ys = [], []
    for i in range(0, len(px), 3):
        if px[i] > 150 and px[i + 1] < 100 and px[i + 2] < 100:  # 紅圈（#b8433f）
            xs.append(i // 3 % W)
            ys.append(i // 3 // W)
    assert len(xs) > 2000, "沒看到紅圈"
    assert max(xs) < W - UI_RIGHT and min(xs) > 0, (min(xs), max(xs))
    assert min(ys) > UI_TOP and max(ys) < H - FORMAT_9x16.safe_bottom, (min(ys), max(ys))


# ---------------------------------------------------------------- 預設比例（使用者設定）與審片頁


@pytest.fixture(scope="module")
def portrait_preview(tmp_path_factory):
    """使用者設定的預設比例是直式：新專案 preview 不加 --aspect，也該出直式的分鏡靜態圖與審片頁。"""
    data = tmp_path_factory.mktemp("使用者資料-直式")
    proc = run_cli("settings", "set", "--channel", TEST_CHANNEL, "--aspect", "直式", "--data-dir", str(data))
    assert proc.returncode == 0, proc.stderr
    root = tmp_path_factory.mktemp("影片") / "解說影片 直式預覽" / "2026-10-09-殖利率曲線"
    shutil.copytree(MINI_PROJECT, root)
    proc = run_cli("preview", str(root), "--tts", "fake", "--data-dir", str(data))
    assert proc.returncode == 0, f"預覽失敗：\n{proc.stdout}\n{proc.stderr}"
    return root


def read_page(path: Path):
    p = _Parser()
    p.feed(path.read_text(encoding="utf-8"))
    return p.page


def test_preview_falls_back_to_the_default_aspect_in_the_settings(portrait_preview):
    assert (portrait_preview / "審片頁-9x16.html").is_file()
    assert not (portrait_preview / "審片頁.html").exists()
    assert json.loads((portrait_preview / "settings.json").read_text(encoding="utf-8"))["aspect"] == "9x16"
    assert (portrait_preview / "build" / "9x16" / "stills").is_dir()
    assert not (portrait_preview / "build" / "16x9").exists()


def test_portrait_review_page_has_portrait_thumbnails(portrait_preview):
    page = read_page(portrait_preview / "審片頁-9x16.html")
    assert page.attrs.get("data-aspect") == "9x16"
    assert [c.id for c in page.cards] == ["SH01", "SH02"]
    for card in page.cards:
        assert card.stills
        for src, _ in card.stills:
            assert png_size(portrait_preview / unquote(src)) == (W, H)


def test_portrait_thumbnails_are_shown_upright_in_the_browser(portrait_preview):
    """瀏覽器裡看：每張卡的大圖、過程小圖都是直的（高 > 寬），不是塞在 16:9 的框裡左右留白。"""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto((portrait_preview / "審片頁-9x16.html").as_uri())
            page.wait_for_function("[...document.images].every(i => i.complete)")
            boxes = page.eval_on_selector_all(
                "img.still", "els => els.map(e => { const r = e.getBoundingClientRect(); return [r.width, r.height]; })")
        finally:
            browser.close()
    assert boxes
    for w, h in boxes:
        assert h > w * 1.6, (w, h)  # 9:16 ≈ 1.78

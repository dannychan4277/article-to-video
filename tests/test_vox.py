"""Vox 分鏡：文章原圖（圖N）與原文剪報貼在桌面上、標籤、接續不切、停留慢漂、跟財經圖表交錯。

測試專案全部在這裡現做：自己寫的假文章（Markdown＋一張 Pillow 畫的圖，經 extract 擷取）、旁白、分鏡設定。
成片用小畫面（640×360）建置，Vox 片段在 1080p 渲太久；比例不影響這裡驗的事（標籤、切點、慢漂、交錯）。
預期時間一律照假配音層的規則在這裡自己算（每段開頭 0.1 秒、每字 0.2 秒、標點 0.25 秒、結尾 0.3 秒），
段間留白 0.4、錨點開頭提早 0.15、fps 30。
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import zipfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from article_to_video.article import extract_article
from article_to_video.boxes import box_statuses, confirm_boxes, project_boxes
from article_to_video.build import BlockedBuild, build_project, plan_build
from article_to_video.clips import FONTS_DIR
from article_to_video.end_card import END_CARD_ID
from article_to_video.ffmpeg import find_ffmpeg
from article_to_video.issue_report import create_report, reproduce_report
from article_to_video.numbers import check_numbers, reconcile
from article_to_video.project import ProjectError
from article_to_video.timeline import FORMAT_9x16, VideoFormat

pytestmark = pytest.mark.usefixtures("internal_vox")
"""Vox 分鏡只在內部開關打開時讀得到（docs/adr/0001-只用財經圖表風格.md）；這個模組測的就是 Vox 本身。"""

FPS = 30
GAP = 0.4
LEAD = 0.15
SMALL = VideoFormat(name="test360", width=640, height=360, fps=FPS, safe_bottom=47, sub_size=18,
                    sub_margin_v=15, sub_margin_lr=20, sub_pad=4)

NARRATION = {
    "s01": "先看兩年期。",
    "s02": "第一張圖，十年期三點九六。",
    "s03": "原文這樣寫，差距零點六二而已。",
    "s04": "曲線倒掛。",
}

ARTICLE_MD = """# 自製測試文章：殖利率與時間

這是一篇為了測試而寫的假文章，內容跟投資無關，數字取自公有領域的美國公債殖利率。

![](chart.png)

圖一：自製的兩年期與十年期殖利率示意圖。

這一段是為了測試剪報而寫的。兩年期公債殖利率去年平均 4.58%，十年期平均 3.96%。兩者的差距只剩 0.62 個百分點，曲線仍然倒掛。最後這一句只是為了讓段落再長一點，好測試遮蓋。
"""

CHART = {"title": "兩年期殖利率", "unit": "美國公債殖利率，2023 年平均，%", "source": "來源：FRED（DGS2）",
         "chart": "bar", "decimals": 2, "suffix": "%", "series": [{"label": "兩年期", "value": 4.58}]}


def fake_times(text: str) -> tuple[list[float | None], float]:
    """假配音層規則 → 每個字開始唸的秒數（標點是 None）與整段長度。"""
    starts: list[float | None] = []
    t = 0.1
    for ch in text:
        if unicodedata.category(ch).startswith("P"):
            starts.append(None)
            t += 0.25
        else:
            starts.append(t)
            t += 0.2
    return starts, t + 0.3


SEG_START: dict[str, float] = {}
_t = 0.0
for _k, _v in NARRATION.items():
    SEG_START[_k] = _t
    _t += fake_times(_v)[1] + GAP


def word_time(seg: str, word: str) -> float:
    """錨點（詞的開頭）在成片裡的秒數。"""
    starts, _ = fake_times(NARRATION[seg])
    return SEG_START[seg] + starts[NARRATION[seg].index(word)] - LEAD


def frame(t: float) -> int:
    """秒 → 幀：四捨五入（.5 進位；浮點誤差 2.05×30＝61.4999… 也算 .5）。"""
    return math.floor(t * FPS + 0.5 + 1e-9)


# ---------------------------------------------------------------- 測試專案


def _font() -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS_DIR / "NotoSansTC-500.woff"), 44)


def _number_rect() -> list[int]:
    """自製原圖上「3.96」那幾個字的範圍（框要框的地方），四邊各多留 6 像素。"""
    x0, y0, x1, y1 = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((720, 330), "3.96", font=_font())
    return [x0 - 6, y0 - 6, x1 - x0 + 12, y1 - y0 + 12]


NUMBER_RECT = _number_rect()
SWATCH_RECT = [330, 130, 50, 40]
"""圖例「十年期」的色塊。"""


def draw_chart_png(path: Path) -> None:
    """自製的「文章原圖」：淡色底、圖例色塊、兩根柱、柱上數字（深藍字，整張圖沒有接近黑色的像素）。"""
    im = Image.new("RGB", (1200, 800), (247, 244, 236))
    d = ImageDraw.Draw(im)
    font = _font()
    ink = (31, 58, 147)
    d.text((60, 40), "殖利率示意圖", font=font, fill=ink)
    d.rectangle([60, 130, 110, 170], fill=(230, 140, 40))   # 圖例色塊：兩年期
    d.text((125, 122), "兩年期", font=font, fill=ink)
    d.rectangle([330, 130, 380, 170], fill=(40, 150, 150))  # 圖例色塊：十年期
    d.text((395, 122), "十年期", font=font, fill=ink)
    d.rectangle([250, 330, 450, 740], fill=(230, 140, 40))
    d.rectangle([700, 400, 900, 740], fill=(40, 150, 150))
    d.text((270, 260), "4.58", font=font, fill=ink)
    d.text((720, 330), "3.96", font=font, fill=ink)
    im.save(path)


def write_article(root: Path, article_md: str = ARTICLE_MD) -> None:
    src = root.parent / f"{root.name}-原稿"
    src.mkdir(parents=True, exist_ok=True)
    draw_chart_png(src / "chart.png")
    (src / "文章.md").write_text(article_md, encoding="utf-8")
    extract_article(root, src / "文章.md")


def chart_shot(sid: str, seg: str, title: str) -> dict:
    return {"id": sid, "start": {"segment": seg},
            "chart": {"data": {**CHART, "title": title}, "cues": [{"at": 0, "kind": "title"}, {"at": 0.2, "kind": "bar"}]}}


def scenario_shots() -> list[dict]:
    return [
        {**chart_shot("SH01", "s01", "兩年期殖利率"), "start": {"segment": "s01"}},
        {"id": "SH02", "start": {"segment": "s02"},
         "vox": {"cues": [
             {"at": 0, "kind": "image", "image": "圖1"},
             {"at": {"segment": "s02", "word": "三點九六"}, "kind": "box", "rect": NUMBER_RECT}]}},
        {"id": "SH03", "start": {"segment": "s03"}, "continues": True,
         "vox": {"cues": [
             {"at": {"segment": "s03", "word": "差距"}, "kind": "excerpt", "quote": "差距只剩"},
             {"at": {"segment": "s03", "word": "零點六二"}, "kind": "box", "find": "0.62"}]}},
        chart_shot("SH04", "s04", "曲線倒掛"),
    ]


def write_project(root: Path, shots: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "narration.json").write_text(json.dumps({"segments": NARRATION}, ensure_ascii=False), encoding="utf-8")
    (root / "settings.json").write_text(json.dumps({"gap": GAP, "tail": 1.0}), encoding="utf-8")
    (root / "shots.json").write_text(json.dumps({"shots": shots}, ensure_ascii=False, indent=1), encoding="utf-8")


def new_project(base: Path, shots: list[dict] | None = None, article_md: str = ARTICLE_MD) -> Path:
    root = base / "影片 Vox" / "2026-10-09-剪報"
    root.mkdir(parents=True)
    write_article(root, article_md)
    write_project(root, scenario_shots() if shots is None else shots)
    return root


def cli(*args: object) -> subprocess.CompletedProcess:
    from conftest import run_cli

    return run_cli(*args)


TOKEN_RE = re.compile(r"\b(SH\d+:\d+@[0-9a-f]{8})\b")


@dataclass(frozen=True)
class Scenario:
    dir: Path
    video: Path
    specs: Path
    plan: object
    preview: subprocess.CompletedProcess
    blocked: subprocess.CompletedProcess
    listing: subprocess.CompletedProcess
    confirmed: subprocess.CompletedProcess


@pytest.fixture(scope="module")
def scenario(tmp_path_factory) -> Scenario:
    """作者那邊的流程跑一次：預覽 → 正式出片（框還沒核對，被擋）→ 列出框與靜態圖 → Claude 核對 → 出片。
    最後那次出片用小畫面（Vox 片段在 1080p 渲太久），擋不擋出片跟畫面大小無關。"""
    root = new_project(tmp_path_factory.mktemp("vox"))
    preview = cli("preview", root, "--tts", "fake")
    assert preview.returncode == 0, preview.stderr
    blocked = cli("build", root, "--tts", "fake")
    listing = cli("boxes", root, "--tts", "fake")
    assert listing.returncode == 0, listing.stderr
    confirmed = cli("confirm-box", root, *TOKEN_RE.findall(listing.stdout))
    res = build_project(root, tts="fake", fmt=SMALL)
    return Scenario(dir=root, video=res.video, specs=res.plan.paths.specs, plan=res.plan, preview=preview, blocked=blocked,
                    listing=listing, confirmed=confirmed)


# ---------------------------------------------------------------- 讀成片的幀


def video_frames(video: Path, frames: list[int]) -> dict[int, np.ndarray]:
    """成片的指定幀（RGB，int16 好做差值）。"""
    w, h = SMALL.width, SMALL.height
    sel = "+".join(f"eq(n\\,{f})" for f in frames)
    proc = subprocess.run(
        [str(find_ffmpeg()), "-v", "error", "-i", str(video), "-vf", f"select={sel}",
         "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    )
    data = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(-1, h, w, 3).astype(np.int16)
    assert len(data) == len(frames)
    return dict(zip(sorted(frames), data))


def picture(fr: np.ndarray) -> np.ndarray:
    """拿掉底部字幕帶，只看畫面。"""
    return fr[: SMALL.height - SMALL.safe_bottom]


def is_desk(fr: np.ndarray) -> bool:
    """Vox 桌面（牛皮紙色）還是財經圖表（白底）：看畫面中位數顏色。"""
    r, g, b = np.median(picture(fr).reshape(-1, 3), axis=0)
    return r < 240 and r - b > 8


# ---------------------------------------------------------------- 測試


def test_vox_shot_shows_the_article_image_with_its_label(scenario):
    spec = json.loads((scenario.specs / "SH02.json").read_text(encoding="utf-8"))
    labels = {c["text"]: Path(c["image"]) for c in spec["cues"] if c.get("image")}
    assert set(labels) == {"圖1", "原文段落"}
    assert labels["圖1"].read_bytes() == (scenario.dir / "article" / "images" / "圖1.png").read_bytes()
    # 圖貼上桌面後：自製的圖沒有接近黑色的像素，桌面也沒有——畫面上的黑色只會是圖下方的標籤條
    f = frame(SEG_START["s02"] + 1.0)
    fr = picture(video_frames(scenario.video, [f])[f])
    dark = (fr.max(axis=2) < 45).sum()
    assert dark > 200, f"看不到圖下的黑底標籤（只有 {dark} 個深色像素）"


def test_charts_and_vox_shots_interleave_in_one_video(scenario):
    marks = {"SH01": SEG_START["s01"] + 1.0, "SH02": SEG_START["s02"] + 1.0, "SH04": SEG_START["s04"] + 1.0}
    frames = video_frames(scenario.video, [frame(t) for t in marks.values()])
    kinds = {sid: is_desk(frames[frame(t)]) for sid, t in marks.items()}
    assert kinds == {"SH01": False, "SH02": True, "SH04": False}


def mean_diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(picture(a) - picture(b)).mean())


def test_continued_vox_shot_has_no_cut(scenario):
    """SH03 接續 SH02：同一張桌面，切點前後一幀幾乎一樣；對照組：財經圖表切到 Vox 是真的切。"""
    cut, hard = frame(SEG_START["s03"]), frame(SEG_START["s02"])
    fr = video_frames(scenario.video, [cut - 1, cut, hard - 1, hard])
    assert mean_diff(fr[cut - 1], fr[cut]) < 1.0
    assert mean_diff(fr[hard - 1], fr[hard]) > 20


def test_article_material_is_credited_on_the_end_card(scenario):
    [card] = [c for c in scenario.plan.clips if c.id == END_CARD_ID]
    assert "作者原文（圖1、第 4 段）" in card.spec["data"]["sources"]


def test_vox_camera_drifts_slowly_while_holding_on_an_image(scenario):
    """圖貼好之後、下一件素材來之前，鏡頭停在圖上慢慢推近：畫面在動，但動得很少。"""
    a, b = frame(SEG_START["s02"] + 0.7), frame(SEG_START["s02"] + 1.4)  # 框在 1.6 秒才來
    fr = video_frames(scenario.video, [a, b])
    assert 0.5 < mean_diff(fr[a], fr[b]) < 8


# ---------------------------------------------------------------- 框：每個框一張靜態圖，Claude 看過、核對過才放行

BOX_IDS = ["SH02:1", "SH03:1"]


def still_paths(text: str) -> list[Path]:
    return [Path(m) for m in re.findall(r"(?:畫面靜態圖|原圖對照)：(.+?\.png)", text)]


def yellow_pixels(png: Path) -> int:
    """螢光筆（黃色、藍色分量很低）的像素數。桌面、紙、紅線都不是這個顏色。"""
    with Image.open(png) as im:
        a = np.asarray(im.convert("RGB")).astype(int)
    return int(((a[..., 0] > 150) & (a[..., 1] > 130) & (a[..., 2] < 90)).sum())


def test_every_box_gets_a_still_showing_the_box(scenario):
    out = scenario.listing.stdout
    for box in BOX_IDS:
        assert box in out
    stills = still_paths(out)
    assert len(stills) == 2 * len(BOX_IDS)  # 每個框：畫面靜態圖＋原圖對照
    for png in stills:
        assert png.is_file()
    screens = [p for p in stills if "畫面" in p.name]
    assert len(screens) == len(BOX_IDS)
    for png in screens:  # 畫面靜態圖是框畫好、鏡頭推近之後那一刻：看得到螢光筆
        assert yellow_pixels(png) > 2000, png


def test_the_highlighter_does_not_change_the_colour_of_the_text_it_covers(scenario):
    """螢光筆刷在深藍色的數字上：數字還是藍的（不會被正片疊底染成綠字；表格紅跌藍漲的意思才不會亂），紙變黃。"""
    with Image.open(scenario.dir / "build" / "16x9" / "boxes" / "SH02-框1-畫面.png") as im:
        a = np.asarray(im.convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    ys, xs = np.nonzero((r > 150) & (g > 130) & (b < 90))
    assert len(xs) > 2000  # 螢光筆
    box = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    blue = (box[..., 2] > box[..., 0] + 50) & (box[..., 2] > box[..., 1] + 30)
    assert blue.sum() > 1000, f"螢光筆底下的藍字只剩 {blue.sum()} 個藍色像素"


def test_preview_is_never_blocked_by_unchecked_boxes(scenario):
    assert scenario.preview.returncode == 0
    page = (scenario.dir / "審片頁.html").read_text(encoding="utf-8")
    assert "還沒核對" in page


def test_final_build_is_blocked_until_every_box_is_checked(scenario):
    b = scenario.blocked
    assert b.returncode == 1
    assert "Traceback" not in b.stderr
    for box in BOX_IDS:
        assert box in b.stderr
    stills = still_paths(b.stderr)
    assert stills and all(p.is_file() for p in stills)  # 訊息裡說靜態圖在哪，而且真的有
    assert "confirm-box" in b.stderr


def test_checking_every_box_unblocks_the_final_build(scenario):
    assert scenario.confirmed.returncode == 0, scenario.confirmed.stderr
    assert scenario.video.is_file()


def test_after_the_last_box_is_checked_claude_is_told_the_real_next_step(scenario):
    """核完框還不能出片：接著是建置審片用的成片、自動檢查、獨立審片、給作者看；作者點頭才出片。"""
    out = scenario.confirmed.stdout
    assert "所有 Vox 框都核對過了" in out
    assert "可以正式出片" not in out and "可以出片" not in out
    assert "build" in out and "作者" in out and "publish" in out


@pytest.mark.parametrize("excerpt_at", [0, 0.6])
def test_box_followed_too_soon_by_the_next_item_blocks_the_final_build(tmp_path, excerpt_at):
    """框推近（0.9 秒）之後鏡頭至少停 1.2 秒，再花 0.8 秒移到下一件素材：框到下一件至少隔 2.9 秒。
    SH03 的剪報改成切進來 0 秒／0.6 秒就貼，SH02 的框之後只隔 1.9／2.5 秒 → 正式出片擋下（以前的 2 秒規則會放過 2.5 秒）。
    SH03 的框到切走隔 2.3 秒：切走不用移鏡頭，2.1 秒就夠。"""
    shots = scenario_shots()
    shots[2]["vox"]["cues"][0]["at"] = excerpt_at
    root = new_project(tmp_path, shots)
    confirm_boxes(root, [b.token for b in project_boxes(root)[1]])  # 框本身核對過，只剩時間不夠
    gaps = {s.box.id: s.gap for s in box_statuses(plan_build(root, tts="fake"))}
    expected = SEG_START["s03"] + excerpt_at - word_time("s02", "三點九六")
    assert expected < 2.9 and abs(gaps["SH02:1"] - expected) <= 1 / FPS
    assert 2.1 <= gaps["SH03:1"] < 2.9
    with pytest.raises(BlockedBuild) as e:
        build_project(root, tts="fake", fmt=SMALL)
    msg = str(e.value)
    assert "SH02:1" in msg and "1.2 秒" in msg and "2.9 秒" in msg
    assert "SH03:1" not in msg and "✗ 還沒核對" not in msg


def test_a_box_right_after_another_needs_less_time_when_the_camera_need_not_move(tmp_path):
    """同一張表上連著框兩格、隔 2.45 秒：第二格在第一格推近後的畫面裡 → 鏡頭原地推過去，2.0 秒就夠；
    第一格推得很近（zoom 6），第二格不在畫面裡 → 鏡頭要先移過去，要 2.9 秒 → 擋下。"""
    root, where = new_table_project(tmp_path, {})

    def shots(zoom: float | None) -> list[dict]:
        rect = lambda k: [where[k][0] - 8, where[k][1] - 8, where[k][2] + 16, where[k][3] + 16]  # noqa: E731
        first = {"at": {"segment": "s02", "word": "十年期"}, "kind": "box", "rect": rect("c1"),
                 **({"zoom": zoom} if zoom else {})}
        return [{"id": "SH01", "start": {"segment": "s01"}, "vox": {"cues": [
            {"at": 0, "kind": "image", "image": "圖1"}, first,
            {"at": {"segment": "s03", "word": "原文"}, "kind": "box", "rect": rect("c0")}]}}]

    gap = word_time("s03", "原文") - word_time("s02", "十年期")
    assert abs(gap - 2.45) < 0.01
    write_project(root, shots(None))
    ok = {s.box.id: s for s in box_statuses(plan_build(root, tts="fake", fmt=SMALL))}
    assert ok["SH01:1"].gap_ok and ok["SH01:1"].next_what == "下一個框"
    write_project(root, shots(6))
    far = {s.box.id: s for s in box_statuses(plan_build(root, tts="fake", fmt=SMALL))}
    assert not far["SH01:1"].gap_ok and "移過去" in far["SH01:1"].next_what


def edit_box(root: Path, shot_id: str, **changes: object) -> None:
    doc = json.loads((root / "shots.json").read_text(encoding="utf-8"))
    for shot in doc["shots"]:
        if shot["id"] == shot_id:
            [box] = [c for c in shot["vox"]["cues"] if c["kind"] == "box"]
            box.update(changes)
    (root / "shots.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_changing_a_checked_box_blocks_the_build_again(scenario, tmp_path):
    root = tmp_path / "改框"
    shutil.copytree(scenario.dir, root)
    x, y, w, h = NUMBER_RECT
    edit_box(root, "SH02", rect=[x + 40, y, w, h])
    with pytest.raises(ProjectError) as e:
        build_project(root, tts="fake", fmt=SMALL)
    msg = str(e.value)
    assert "SH02:1" in msg and "SH03:1" not in msg  # 只有改過的那個框要重新核對
    # 改框之前看的代號不能拿來核對改過的框
    old = [t for t in TOKEN_RE.findall(scenario.listing.stdout) if t.startswith("SH02:1@")]
    again = cli("confirm-box", root, *old)
    assert again.returncode == 1
    assert "SH02:1" in again.stderr and "Traceback" not in again.stderr


def test_box_over_a_legend_swatch_gets_a_warning(tmp_path):
    """螢光筆刷到圖例色塊會變色（審片學到的規則）：框到色塊時提醒；框彩色的字不提醒。"""
    shots = scenario_shots()
    shots[1]["vox"]["cues"].insert(1, {"at": {"segment": "s02", "word": "十年期"}, "kind": "box", "rect": SWATCH_RECT})
    plan = plan_build(new_project(tmp_path, shots), tts="fake")
    warned = {s.box.id: s.warnings for s in box_statuses(plan)}
    assert any("圖例色塊" in w for w in warned["SH02:1"])
    assert warned["SH02:2"] == []  # 深藍色的數字


def test_portrait_vox_keeps_the_boxes_clear_of_the_platform_ui(scenario, tmp_path):
    """直式：框的靜態圖照直式渲（1080×1920），螢光筆在平台介面區與字幕帶之外。核對過的框不用因為換比例重核
    （核對的是「框在原圖的哪裡」，跟比例無關）。"""
    root = tmp_path / "直式"
    shutil.copytree(scenario.dir, root)
    out = cli("boxes", root, "--aspect", "9:16", "--tts", "fake")
    assert out.returncode == 0, out.stderr
    assert "✗ 還沒核對" not in out.stdout and out.stdout.count("✓ 已核對") == len(BOX_IDS)
    f = FORMAT_9x16
    screens = [p for p in still_paths(out.stdout) if "畫面" in p.name]
    assert len(screens) == len(BOX_IDS) and all("9x16" in str(p) for p in screens)
    for png in screens:
        with Image.open(png) as im:
            assert im.size == (1080, 1920)
            a = np.asarray(im.convert("RGB")).astype(int)
        # 螢光筆的黃（綠色分量也高；自製圖的橘色柱子 (230,140,40) 不算——推近不再切掉旁邊的柱子，它會入鏡）
        ys, xs = np.nonzero((a[..., 0] > 150) & (a[..., 1] > 160) & (a[..., 2] < 90))
        assert len(xs) > 2000, png
        assert xs.max() < f.width - f.safe_right and f.safe_top <= ys.min() and ys.max() < f.height - f.safe_bottom, png


def test_reporting_an_issue_packs_the_vox_material_but_not_the_whole_article(scenario, tmp_path):
    """回報問題：Vox 分鏡貼出來的原圖和剪報那一段要打包（不然維護者那台排不出 Vox 片段），文章全文不打包。"""
    rep = create_report(scenario.dir, shots=["SH03"], out_dir=tmp_path)
    with zipfile.ZipFile(rep.zip_path) as z:
        names = set(z.namelist())
        article = json.loads(z.read("project/article/article.json"))
        assert z.read("project/article/images/圖1.png") == (scenario.dir / "article/images/圖1.png").read_bytes()
    assert "project/article/text.txt" not in names and not any("source" in n for n in names)
    assert [p["n"] for p in article["paragraphs"]] == [4]  # 只有剪報那一段
    res = reproduce_report(rep.zip_path, out_dir=tmp_path / "重現", shots=["SH01"])
    assert res.errors == []  # 解開就排得出整支片（含 Vox 分鏡）的計畫


# ---------------------------------------------------------------- 數字對帳：Vox 畫面上的字

def test_numbers_on_vox_cards_and_clippings_are_reconciled(tmp_path):
    """索引卡、標題卡、圖說、原文剪報上的數字都要對帳：剪報本來就是原文；索引卡寫了文章沒有的數字 → 擋正式出片。"""
    shots = [{"id": "SH01", "vox": {"cues": [
        {"at": 0, "kind": "title", "text": "差距 0.62 個百分點"},
        {"at": 0.5, "kind": "image", "image": "圖1", "caption": "十年期 3.96%"},
        {"at": 1.2, "kind": "excerpt", "quote": "差距只剩"},
        {"at": 2.0, "kind": "point", "text": "一年後 3.1 個百分點"}]}}]
    root = new_project(tmp_path, shots)
    report = reconcile(plan_build(root, tts="fake"))
    found = report.shots["SH01"]

    def kind(where: str, number: str) -> str:
        [n] = [n for n in found if n.where == where and n.shown.startswith(number)]
        return n.kind

    assert kind("標題卡", "0.62") == "article"
    assert kind("圖說", "3.96") == "article"
    assert kind("原文剪報", "0.62") == "article"  # 剪報就是原文
    assert kind("索引卡", "3.1") == "none"
    assert not any(n.shown == "1" for n in found)  # 標籤「圖1」是編號，不是主張
    with pytest.raises(BlockedBuild) as e:
        build_project(root, tts="fake", fmt=SMALL, preflight=[check_numbers])
    assert "索引卡" in str(e.value) and "3.1" in str(e.value)


# ---------------------------------------------------------------- 寫錯的 Vox 分鏡設定：繁中說明哪個分鏡哪裡錯


def _cues(shots: list[dict], i: int) -> list[dict]:
    return shots[i]["vox"]["cues"]


def _box_alone(sh: list[dict]) -> None:
    _cues(sh, 1)[:] = [_cues(sh, 1)[1]]


BAD_SETUPS = {
    "沒有這張圖": (lambda sh: _cues(sh, 1)[0].update(image="圖9"), "文章裡沒有「圖9」"),
    "引文不在文章裡": (lambda sh: _cues(sh, 2)[0].update(quote="不存在的句子"), "找不到「不存在的句子」"),
    "框前面沒有圖": (_box_alone, "前面沒有圖或剪報"),
    "原圖用 find": (lambda sh: (_cues(sh, 1)[1].pop("rect"), _cues(sh, 1)[1].update(find="3.96")), "find 只能找剪報"),
    "框沒錨在詞上": (lambda sh: _cues(sh, 1)[1].update(at=1.0), "錨在旁白唸到那個數字的詞上"),
    "框超出原圖": (lambda sh: _cues(sh, 1)[1].update(rect=[1150, 700, 100, 100]), "超出 圖1 的範圍"),
    "框和圖中間插文字卡": (lambda sh: _cues(sh, 1).insert(1, {"at": 0.5, "kind": "point", "text": "看這裡"}),
                    "中間不能插文字卡"),
    "沒照出場順序": (lambda sh: _cues(sh, 1).append(
        {"at": {"segment": "s02", "word": "第一張圖"}, "kind": "point", "text": "第一張圖"}), "照出場順序"),
    "Vox 接續財經圖表": (lambda sh: sh[1].update(continues=True), "Vox 分鏡只能接續 Vox 分鏡"),
    "負的時間": (lambda sh: _cues(sh, 1)[0].update(at=-1), "不能是負的"),
    "寫成 highlight": (lambda sh: _cues(sh, 1)[1].update(kind="highlight"), "kind\": \"box"),
}


@pytest.mark.parametrize("case", list(BAD_SETUPS))
def test_mistakes_in_a_vox_shot_are_explained_in_plain_chinese(tmp_path, case):
    change, expected = BAD_SETUPS[case]
    shots = scenario_shots()
    change(shots)
    with pytest.raises(ProjectError) as e:
        plan_build(new_project(tmp_path, shots), tts="fake")
    assert expected in str(e.value)
    assert "分鏡 SH0" in str(e.value)


# ---------------------------------------------------------------- 原文剪報

SENTENCES = [  # 剪報那一段（ARTICLE_MD 第 4 段）的四句
    "這一段是為了測試剪報而寫的。",
    "兩年期公債殖利率去年平均 4.58%，十年期平均 3.96%。",
    "兩者的差距只剩 0.62 個百分點，曲線仍然倒掛。",
    "最後這一句只是為了讓段落再長一點，好測試遮蓋。",
]
assert "".join(SENTENCES) in ARTICLE_MD


def disguised(sentence: str) -> str:
    """同一句換成別的字：漢字換成別的漢字（字寬一樣，排版不變），英數、標點、空白不動。"""
    pool = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
    out, k = [], 0
    for ch in sentence:
        if "\u4e00" <= ch <= "\u9fff":
            ch = pool[k % len(pool)] if pool[k % len(pool)] != ch else pool[(k + 1) % len(pool)]
            k += 1
        out.append(ch)
    return "".join(out)


def excerpt_png(base: Path, quote: str, article_md: str = ARTICLE_MD) -> Path:
    """只有一個 Vox 分鏡、貼一張原文剪報的專案 → 那張剪報圖（引擎 spec 裡標籤「原文段落」那張）。"""
    shots = [{"id": "SH01", "vox": {"cues": [{"at": 0, "kind": "excerpt", "quote": quote}]}}]
    plan = plan_build(new_project(base, shots, article_md), tts="fake")
    [img] = [c for p in plan.clips for c in p.spec["cues"] if c.get("text") == "原文段落"]
    return Path(img["image"])


@pytest.mark.parametrize("target,quote", [(1, "十年期平均"), (2, "差距只剩")])
def test_excerpt_is_cut_at_sentence_ends_and_masks_the_partial_sentences(tmp_path, target, quote):
    """引文在句子中間 → 剪那一整句；同一段的前後句（殘句）看不到。"""
    clip = excerpt_png(tmp_path / "a", quote)
    # 其他句子全換成別的字：剪報一個像素都不變（只排剪下來的句子）
    others = ARTICLE_MD
    for i, sent in enumerate(SENTENCES):
        if i != target:
            others = others.replace(sent, disguised(sent))
    assert excerpt_png(tmp_path / "b", quote, others).read_bytes() == clip.read_bytes()
    # 引文寫整句也是同一張
    assert excerpt_png(tmp_path / "c", SENTENCES[target]).read_bytes() == clip.read_bytes()
    # 這一句自己的字換了，剪報就不一樣：剪到的是這一句，而且整句看得到
    mine = ARTICLE_MD.replace(SENTENCES[target], disguised(SENTENCES[target]))
    assert excerpt_png(tmp_path / "d", disguised(SENTENCES[target]), mine).read_bytes() != clip.read_bytes()
    with Image.open(clip) as im:
        assert im.width > im.height  # 剪的是兩三行字，不是整段


# 自製的兩段。第一段：前一句剛好寫滿 18 個字，下一句（要剪的）在整段排版裡只剩 2 個字擠在第一行最右邊。
# 第二段：一行 20 個字的話，第 20 個字剛好落在「美國十年期公債殖利率」中間；平均分兩行也會切在它中間。
LAYOUT_MD = ARTICLE_MD + """
這是前面一句故意寫滿十八個字的句子。殖利率曲線在倒掛之後轉正，靠的是短天期利率下降得比較快。

這是自己寫來測試框的一句話看美國十年期公債殖利率怎麼換行才對。
"""


def text_lines(png: Path) -> list[tuple[int, int, int]]:
    """剪報圖裡每一行字：(上緣 y, 最左邊的墨 x, 墨的寬度)。用列的投影分行。"""
    with Image.open(png) as im:
        ink = np.asarray(im.convert("L")) < 128
    rows = ink.any(axis=1)
    bands, y = [], 0
    while y < len(rows):
        if rows[y]:
            y0 = y
            while y < len(rows) and rows[y]:
                y += 1
            bands.append((y0, y))
        y += 1
    out = []
    for y0, y1 in bands:
        xs = np.nonzero(ink[y0:y1].any(axis=0))[0]
        out.append((y0, int(xs.min()), int(xs.max() - xs.min())))
    return out


def test_a_clipping_is_set_flush_left_in_balanced_lines(tmp_path):
    """剪下來的句子從行首重新排：每一行都靠左對齊（不會因為遮掉前一句殘字，第一行看起來靠右），
    行長平均（不會第一行只剩「殖利」兩個字）。"""
    clip = excerpt_png(tmp_path, "殖利率曲線在倒掛之後轉正", LAYOUT_MD)
    lines = text_lines(clip)
    assert len(lines) == 2, lines
    lefts = [x for _, x, _ in lines]
    assert max(lefts) - min(lefts) <= 12, f"行首沒有對齊：{lefts}"
    widths = [w for _, _, w in lines]
    assert widths[0] >= 0.6 * max(widths), f"第一行太短：{widths}"


def test_a_boxed_phrase_is_never_split_by_a_line_break(tmp_path):
    """框剪報裡的一段字（find）：排版時這段字不拆到兩行，就不會因為換行剛好落在中間而框不起來。"""
    shots = [{"id": "SH01", "start": {"segment": "s01"}, "vox": {"cues": [
        {"at": 0, "kind": "excerpt", "quote": "看美國十年期公債殖利率"},
        {"at": {"segment": "s01", "word": "兩年期"}, "kind": "box", "find": "美國十年期公債殖利率"}]}}]
    root = new_project(tmp_path, shots, LAYOUT_MD)
    [box] = project_boxes(root)[1]
    x, y, w, h = box.rect
    assert h < 150 and w > 5 * 88  # 一行字的高度、十個字的寬度


# ---------------------------------------------------------------- 框推近多深：正在唸的那一行、欄名留在畫面裡


TABLE_SIZE = (1600, 820)


def draw_table_png(path: Path) -> dict[str, list[int]]:
    """自製的表格圖（自己編的示意數字）：表頭一列（淺灰底）、三列資料、最左邊是列名、變化那欄藍漲紅跌。
    回傳幾個字在圖上的範圍 [x, y, 寬, 高]。"""
    im = Image.new("RGB", TABLE_SIZE, (255, 255, 255))
    d = ImageDraw.Draw(im)
    font = _font()
    d.rectangle([60, 200, 1540, 300], fill=(241, 242, 245))
    where: dict[str, list[int]] = {}

    def put(key: str, xy: tuple[int, int], text: str, fill=(20, 20, 20)) -> None:
        d.text(xy, text, font=font, fill=fill)
        x0, y0, x1, y1 = d.textbbox(xy, text, font=font)
        where[key] = [x0, y0, x1 - x0, y1 - y0]

    put("title", (80, 60), "自製表格：兩個年份的示意數字")
    for k, (x, head) in enumerate([(640, "甲年"), (960, "乙年"), (1300, "變化")]):
        put(f"head{k}", (x, 225), head)
    rows = [("兩年期", "4.10", "3.70", "-0.40"), ("十年期", "3.80", "4.05", "+0.25"), ("利差", "-0.30", "0.35", "+0.65")]
    for r, (label, a, b, ch) in enumerate(rows):
        y = 345 + r * 130
        d.line([60, y - 25, 1540, y - 25], fill=(205, 205, 205), width=2)
        put(f"label{r}", (100, y), label)
        put(f"a{r}", (640, y), a)
        put(f"b{r}", (960, y), b)
        put(f"c{r}", (1300, y), ch, fill=(200, 50, 40) if ch.startswith("-") else (40, 100, 180))
    im.save(path)
    return where


def new_table_project(base: Path, box: dict) -> tuple[Path, dict[str, list[int]]]:
    """文章裡只有一張自製表格圖的專案；SH01 貼圖1、旁白唸到「兩年期」時框 box（rect）。"""
    root = base / "影片 表格" / "2026-10-09-表格"
    src = root.parent / "表格-原稿"
    src.mkdir(parents=True)
    where = draw_table_png(src / "table.png")
    (src / "文章.md").write_text("# 自製表格文章\n\n這一段只是為了測試表格推近而寫的。\n\n![](table.png)\n", encoding="utf-8")
    root.mkdir(parents=True)
    extract_article(root, src / "文章.md")
    write_project(root, [{"id": "SH01", "start": {"segment": "s01"}, "vox": {"cues": [
        {"at": 0, "kind": "image", "image": "圖1"},
        {"at": {"segment": "s01", "word": "兩年期"}, **box}]}}])
    return root, where


def box_frames(plan) -> dict[str, list[float]]:
    """建置計畫裡每個框（highlight cue）推近後要留在畫面裡的範圍（相對原圖 0..1）。"""
    return {c["box"]: c["data"]["frame"] for cp in plan.clips for c in cp.spec["cues"] if c.get("kind") == "highlight"}


def covers(frame: list[float], rect: list[int], size: tuple[int, int]) -> bool:
    fx, fy, fw, fh = frame
    x, y, w, h = rect
    W, H = size
    return fx <= x / W and fy <= y / H and x / W + w / W <= fx + fw and y / H + h / H <= fy + fh


@pytest.mark.parametrize("fmt", [SMALL, FORMAT_9x16], ids=["16x9", "9x16"])
def test_a_box_on_a_table_keeps_its_row_label_and_column_header_in_frame(tmp_path, fmt):
    """框表格裡的一格（「+0.25」）：推近後這一列的列名（十年期）和這一欄的欄名（變化）都還在畫面裡，
    但不會只是整張表原地不動（還是有推近：畫面比整張圖小）。"""
    root, where = new_table_project(tmp_path, {})
    x, y, w, h = where["c1"]
    write_project(root, [{"id": "SH01", "start": {"segment": "s01"}, "vox": {"cues": [
        {"at": 0, "kind": "image", "image": "圖1"},
        {"at": {"segment": "s01", "word": "兩年期"}, "kind": "box", "rect": [x - 8, y - 8, w + 16, h + 16]}]}}])
    frame = box_frames(plan_build(root, tts="fake", fmt=fmt))["SH01:1"]
    assert covers(frame, where["label1"], TABLE_SIZE), frame
    assert covers(frame, where["head2"], TABLE_SIZE), frame
    assert frame[2] < 1 or frame[3] < 1  # 有推近


def test_a_box_on_a_clipping_keeps_the_whole_line_in_frame(scenario):
    """框剪報裡的「0.62」：推近後它所在的那一整行都在畫面裡（不會只剩「差距只剩 0.62」幾個字、兩邊被切掉）。"""
    frames = box_frames(scenario.plan)
    box = next(b for b in scenario.plan.vox.boxes if b.id == "SH03:1")
    png = box.target.image
    with Image.open(png) as im:
        size = im.size
    bx, by, bw, bh = box.rect
    line = next((y0, x, w) for y0, x, w in text_lines(png) if y0 <= by + bh and by <= y0 + 100)
    assert covers(frames["SH03:1"], [line[1], line[0], line[2], 60], size), (frames["SH03:1"], line, size)


def test_the_author_can_set_how_far_a_box_zooms(tmp_path):
    """shots.json 的框可以寫 "zoom"：1＝整張圖都留在畫面裡（不推近），2＝推到大約半張圖。"""
    shots = scenario_shots()
    shots[1]["vox"]["cues"][1]["zoom"] = 1
    plan = plan_build(new_project(tmp_path, shots), tts="fake", fmt=SMALL)
    assert covers(box_frames(plan)["SH02:1"], [0, 0, 1200, 800], (1200, 800))
    shots[1]["vox"]["cues"][1]["zoom"] = 0.5
    with pytest.raises(ProjectError) as e:
        plan_build(new_project(tmp_path / "b", shots), tts="fake")
    assert "zoom" in str(e.value) and "SH02" in str(e.value)

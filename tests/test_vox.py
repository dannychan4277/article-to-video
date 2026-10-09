"""Vox 分鏡：文章原圖（圖N）與原文剪報貼在桌面上、標籤、接續不切、停留慢漂、跟財經圖表交錯。

測試專案全部在這裡現做：自己寫的假文章（Markdown＋一張 Pillow 畫的圖，經 extract 擷取）、旁白、分鏡設定。
成片用小畫面（640×360）建置，Vox 片段在 1080p 渲太久；比例不影響這裡驗的事（標籤、切點、慢漂、交錯）。
預期時間一律照假配音層的規則在這裡自己算（每段開頭 0.1 秒、每字 0.2 秒、標點 0.25 秒、結尾 0.3 秒），
段間留白 0.4、錨點開頭提早 0.15、fps 30。
"""

from __future__ import annotations

import json
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from article_to_video.article import extract_article
from article_to_video.build import build_project, plan_build
from article_to_video.clips import FONTS_DIR
from article_to_video.ffmpeg import find_ffmpeg
from article_to_video.timeline import VideoFormat

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
    return int(t * FPS + 0.5)


# ---------------------------------------------------------------- 測試專案


def draw_chart_png(path: Path) -> None:
    """自製的「文章原圖」：淡色底、圖例色塊、兩根柱、柱上數字（深藍字，整張圖沒有接近黑色的像素）。"""
    im = Image.new("RGB", (1200, 800), (247, 244, 236))
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(FONTS_DIR / "NotoSansTC-500.woff"), 44)
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
         "vox": {"cues": [{"at": 0, "kind": "image", "image": "圖1"}]}},
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


@dataclass(frozen=True)
class Scenario:
    dir: Path
    video: Path
    specs: Path


@pytest.fixture(scope="module")
def scenario(tmp_path_factory) -> Scenario:
    root = new_project(tmp_path_factory.mktemp("vox"))
    res = build_project(root, tts="fake", fmt=SMALL)
    return Scenario(dir=root, video=res.video, specs=res.plan.paths.specs)


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
    [img] = [c for c in spec["cues"] if c.get("image")]
    assert Path(img["image"]).read_bytes() == (scenario.dir / "article" / "images" / "圖1.png").read_bytes()
    assert img["text"] == "圖1"
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
    """引文在句子中間 → 剪那一整句；跟它共用一行的前後句（殘句）遮住看不到。"""
    clip = excerpt_png(tmp_path / "a", quote)
    # 其他句子全換成別的字（版面不變）：被遮住的殘字換了，剪報一個像素都不變
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

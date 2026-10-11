"""圖表版面：大數字卡、兩數對比、柱寬隨根數調整（16:9、9:16 都要排得好）。

切點：建置計畫（`plan_build`，假配音層，不渲片段）→ 每個分鏡實際給引擎的 spec → 引擎渲的靜態圖（`render_stills`）。
一個自製的測試專案（自製示意數字，不是任何文章的），兩種比例各渲一次、所有測試共用：

- SH01：大數字卡（「300 天」，說明「連續倒掛」）。
- SH02：兩數對比（甲組 1.2%、乙組 0.8%），唸到「甲高」時強調甲組；SH02B 接續它，唸到「個百分點」時標出差距。
- SH03：兩根柱的柱狀圖；SH04：七根柱的柱狀圖。

整支片寫 push 0（不慢推），同一串裡不同時刻的畫面可以逐像素比；鏡頭推近時字不出框另有測試（兩種鏡頭）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from article_to_video.build import plan_build
from article_to_video.clips import default_jobs, render_stills, run_parallel
from article_to_video.timeline import FORMATS

FPS = 30
NARRATION = {
    "s01": "這一段一共連續倒掛三百天。",
    "s02": "甲組是一點二，乙組是零點八，甲高零點四個百分點。",
    "s03": "只有兩根柱的時候。",
    "s04": "七根柱的時候。",
}
SOURCE = "來源：測試用自製數字"


def _a(seg: str, word: str | None = None, edge: str = "start") -> dict:
    out: dict = {"segment": seg}
    if word:
        out["word"] = word
    if edge != "start":
        out["edge"] = edge
    return out


SEVEN = [{"label": f"{2019 + i}", "value": v} for i, v in enumerate([0.2, 0.5, 1.1, -0.1, -0.6, -0.2, 0.5])]
SHOTS = [
    {"id": "SH01", "start": _a("s01"), "chart": {
        "data": {"title": "這一段倒掛了很久", "unit": "示意數據：利差小於 0 的天數", "source": SOURCE, "chart": "number",
                 "push": 0},
        "cues": [{"at": 0, "kind": "title"},
                 {"at": _a("s01", "三百天", "end"), "kind": "number", "text": "連續倒掛",
                  "data": {"value": 300, "suffix": " 天", "decimals": 0}}]}},
    {"id": "SH02", "start": _a("s02"), "chart": {
        "data": {"title": "甲組比乙組高", "unit": "示意數據，%", "source": SOURCE, "chart": "compare", "push": 0,
                 "decimals": 1, "suffix": "%",
                 "series": [{"label": "甲組", "value": 1.2}, {"label": "乙組", "value": 0.8}],
                 "highlight": {"index": 0}},
        "cues": [{"at": 0, "kind": "title"},
                 {"at": _a("s02", "一點二", "end"), "kind": "number", "data": {"index": 0}},
                 {"at": _a("s02", "零點八", "end"), "kind": "number", "data": {"index": 1}},
                 {"at": _a("s02", "甲高"), "kind": "highlight"}]}},
    {"id": "SH02B", "start": _a("s02", "零點四"), "continues": True, "chart": {
        "data": {},
        "cues": [{"at": _a("s02", "個百分點", "end"), "kind": "gap", "text": "高", "data": {"suffix": " 個百分點"}}]}},
    {"id": "SH03", "start": _a("s03"), "chart": {
        "data": {"title": "兩根柱", "unit": "示意數據，百分點", "source": SOURCE, "chart": "bar", "decimals": 2, "push": 0,
                 "series": [{"label": "兩年期下降", "value": 0.77}, {"label": "十年期上升", "value": 0.33}]},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s03"), "kind": "bar"}]}},
    {"id": "SH04", "start": _a("s04"), "chart": {
        "data": {"title": "七根柱", "unit": "示意數據，百分點", "source": SOURCE, "chart": "bar", "decimals": 2, "push": 0,
                 "series": SEVEN},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s04"), "kind": "bar"}]}},
]

LAST = -1.0
"""時刻寫 LAST＝這個分鏡的最後一幀。"""
BEFORE_HL = -2.0
"""時刻寫 BEFORE_HL＝這個分鏡自己的 highlight cue 前兩幀（兩個數字都落定了、還沒強調）。"""
STILLS = {
    "SH01": [0.0, LAST],
    "SH02": [BEFORE_HL, LAST],
    "SH02B": [0.0, LAST],
}


def _time(spec: dict, t: float) -> float:
    if t == LAST:
        return round(spec["duration"] - 1 / FPS, 4)
    if t == BEFORE_HL:
        return round(next(q["at"] for q in spec["cues"] if q["kind"] == "highlight" and q["at"] >= 0) - 2 / FPS, 4)
    return t


def write_project(root: Path, shots: list[dict] = SHOTS, settings: dict | None = None) -> Path:
    root.mkdir(parents=True)
    (root / "narration.json").write_text(json.dumps({"segments": NARRATION}, ensure_ascii=False), encoding="utf-8")
    (root / "shots.json").write_text(json.dumps({"shots": shots}, ensure_ascii=False), encoding="utf-8")
    (root / "settings.json").write_text(json.dumps({"gap": 0.4, "camera": "slow", **(settings or {})}), encoding="utf-8")
    return root


@dataclass
class Layouts:
    aspect: str
    specs: dict[str, dict]
    frames: dict[tuple[str, float], np.ndarray]

    @property
    def fmt(self):
        return FORMATS[self.aspect]

    def at(self, sid: str, t: float) -> np.ndarray:
        return self.frames[(sid, t)]

    def last(self, sid: str) -> np.ndarray:
        return self.frames[(sid, LAST)]


@pytest.fixture(scope="module", params=["16x9", "9x16"])
def layouts(request, tmp_path_factory) -> Layouts:
    aspect = request.param
    root = write_project(tmp_path_factory.mktemp(f"版面 {aspect}") / "專案")
    plan = plan_build(root, tts="fake", fmt=FORMATS[aspect])
    specs = {c.id: c.spec for c in plan.clips}

    def work(sid: str) -> list[tuple[tuple[str, float], np.ndarray]]:
        spec = specs[sid]
        times = [_time(spec, t) for t in STILLS[sid]]
        paths = render_stills(spec, root / "stills" / sid, times).paths
        return [((sid, key), np.asarray(Image.open(p).convert("RGB")).astype(int)) for key, p in zip(STILLS[sid], paths)]

    frames = dict(pair for out in run_parallel(list(STILLS), work, jobs=default_jobs()) for pair in out)
    return Layouts(aspect, specs, frames)


# ---------------------------------------------------------------- 量畫面的小工具


def gray(img: np.ndarray) -> np.ndarray:
    return img.mean(axis=-1)


def content_rows(lay: Layouts) -> tuple[int, int]:
    """標題、副標以下到字幕帶以上（數字所在的那一區）的列範圍：副標的字最下面一列＋一點，到字幕帶上緣。"""
    fmt = lay.fmt
    return int(fmt.height * (0.30 if fmt.height < fmt.width else 0.27)), fmt.height - fmt.safe_bottom


def bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """mask 裡為真的像素外框 (x0, y0, x1, y1)；沒有就 None。"""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def brand_mask(img: np.ndarray, brand=(0xE3, 0x12, 0x0B), tol: int = 40) -> np.ndarray:
    return (np.abs(img - np.array(brand)).max(axis=-1) < tol)


# ---------------------------------------------------------------- 大數字卡


def test_a_big_number_card_lands_one_big_number_in_the_middle_of_the_frame(layouts):
    """大數字卡：一個大數字（品牌色），落定前不在畫面上；字高至少是短邊的一成三，整個在內容區裡。"""
    y0, y1 = content_rows(layouts)
    before = brand_mask(layouts.at("SH01", 0.0))[y0:y1]
    after = brand_mask(layouts.last("SH01"))[y0:y1]
    assert bbox(before) is None, "數字唸到之前就出現了"
    box = bbox(after)
    assert box is not None, "大數字卡上看不到大數字"
    x0, top, x1, bottom = box
    assert bottom - top >= 0.13 * min(layouts.fmt.width, layouts.fmt.height), f"數字太小：字高 {bottom - top} px"
    assert x1 - x0 >= 0.2 * layouts.fmt.width


# ---------------------------------------------------------------- 兩數對比


def dark(img: np.ndarray, below: int = 60) -> np.ndarray:
    return gray(img) < below


def runs(profile: np.ndarray, min_gap: int) -> list[tuple[int, int]]:
    """一維輪廓裡有東西的區段（中間空白少於 min_gap 的併成一段）。"""
    idx = np.nonzero(profile)[0]
    out: list[list[int]] = []
    for i in idx:
        if out and i - out[-1][1] <= min_gap:
            out[-1][1] = int(i)
        else:
            out.append([int(i), int(i)])
    return [(a, b + 1) for a, b in out]


def number_boxes(lay: Layouts, img: np.ndarray) -> list[tuple[int, int, int, int]]:
    """內容區裡兩個數字（最大的兩塊深色字）的外框，照畫面上的順序（左到右、上到下）。"""
    y0, y1 = content_rows(lay)
    m = dark(img)[y0:y1]
    rows = runs(m.any(axis=1), 6)
    tall = sorted(sorted(rows, key=lambda r: r[1] - r[0])[-2:])   # 每一列裡最高的兩段＝數字（說明的字矮很多）
    if lay.fmt.width > lay.fmt.height:   # 橫式：兩個數字在同一列，左右分開
        (a, b), = [max(rows, key=lambda r: r[1] - r[0])]
        cols = runs(m[a:b].any(axis=0), int(0.05 * lay.fmt.width))   # 一個數字裡的字距（「1」左右的空）比兩格之間小很多
        return [(c0, a + y0, c1, b + y0) for c0, c1 in cols]
    out = []
    for a, b in tall:
        x0, _, x1, _ = bbox(m[a:b])
        out.append((x0, a + y0, x1, b + y0))
    return out


def test_two_numbers_sit_side_by_side_in_landscape_and_one_above_the_other_in_portrait(layouts):
    boxes = number_boxes(layouts, layouts.at("SH02", BEFORE_HL))
    assert len(boxes) == 2, boxes
    (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = boxes
    for x0, y0, x1, y1 in boxes:
        assert y1 - y0 >= 0.08 * min(layouts.fmt.width, layouts.fmt.height), f"數字太小：{boxes}"
    if layouts.fmt.width > layouts.fmt.height:
        assert bx0 - ax1 >= 0.05 * layouts.fmt.width, "兩個數字要左右分開"
        tol = 0.1 * (ay1 - ay0)   # 圓的字（0、8）比平的字（1、2）上下多出一點點
        assert abs(ay0 - by0) <= tol and abs(ay1 - by1) <= tol, "兩個數字要在同一列、一樣大"
        assert ax0 == pytest.approx(layouts.fmt.width * 90 / 1920, abs=12), "左邊的數字對齊標題的左邊界"
    else:
        assert by0 - ay1 >= 0.03 * layouts.fmt.height, "兩個數字要上下分開"
        assert abs(ax0 - bx0) <= 4, "兩個數字靠左對齊"
        assert abs((ay1 - ay0) - (by1 - by0)) <= 0.1 * (ay1 - ay0), "兩個數字一樣大（圓的字上下多出一點點）"


def test_the_emphasised_number_turns_brand_colour_and_the_other_one_greys_out(layouts):
    before, after = layouts.at("SH02", BEFORE_HL), layouts.last("SH02")
    a, b = number_boxes(layouts, before)

    def crop(img, box):
        x0, y0, x1, y1 = box
        return img[y0:y1, x0:x1]

    assert dark(crop(before, a)).sum() > 500 and dark(crop(before, b)).sum() > 500, "強調之前兩個數字都是黑字"
    assert brand_mask(crop(after, a)).sum() > 500, "被強調的甲組換成品牌色"
    assert dark(crop(after, b)).sum() == 0, "乙組要淡掉（不再是黑字）"
    assert (gray(crop(after, b)) < 200).sum() > 500, "淡掉的乙組還讀得到"


def test_a_continued_compare_starts_where_the_last_one_ended_and_adds_the_gap_below(layouts):
    end, start, last = layouts.last("SH02"), layouts.at("SH02B", 0.0), layouts.last("SH02B")
    assert changed(end, start) < 400, "接續分鏡第一幀要跟上一個分鏡最後一幀一樣"
    diff = (np.abs(last - start).max(axis=-1) > 40)
    box = bbox(diff)
    assert box is not None, "差距沒有出現"
    numbers_bottom = max(y1 for _, _, _, y1 in number_boxes(layouts, start))
    assert box[1] > numbers_bottom, f"差距要在兩個數字下面：差距 {box}，數字底 {numbers_bottom}"
    assert brand_mask(last[box[1]:box[3], box[0]:box[2]]).sum() > 200, "差距的數字用品牌色"


def changed(a: np.ndarray, b: np.ndarray, thresh: int = 40) -> int:
    """兩張圖有幾個像素明顯不一樣（任一色版差 > thresh）。"""
    return int((np.abs(a - b).max(axis=-1) > thresh).sum())

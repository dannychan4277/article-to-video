"""財經圖表的一串接續分鏡：切點看不出來、整串的軸與刻度不跳、軸距合理、第一幀就讀得到。

切點：建置計畫（`plan_build`，假配音層，不渲片段）→ 每個分鏡實際給引擎的 spec → 引擎渲的靜態圖（`render_stills`）。
一個自製的測試專案（數字是自製示意數字，不是任何文章的），兩種比例各渲一次、所有測試共用：

- SH01～SH03：柱狀圖一串。SH01 標「甲」（正數柱），SH02 從 s02 開頭切入、到「跌最深」才換標「丁」（負數柱），
  SH03 從 s03 開頭切入、切點就換標「乙」。乙自己寫了顏色（綠）。
- SH04～SH06：折線一串（有大數字）。SH04 標谷底（不在 xTicks 上），SH05 換標最後一點，SH06 同一點只換字。
- SH07：同一組折線數字，自己寫縱軸範圍 yRange [-2, 2]。
- SH08～SH09：柱狀圖一串、一句長一根柱（計時實測的卡點）。甲、乙在 SH08 唸到時長（乙比甲低：「降到」），丙的 bar cue
  寫在接續分鏡 SH09。SH08 唸完乙標「不到一半」（乙旁邊有高一倍多的甲：標註放在乙的正上方）。
- SH10～SH11：第一個分鏡沒寫 bar cue（柱在版式之後自己長），接續分鏡才寫第三根柱的 bar cue。
- SH12：先長「子」（帶 index 的 bar cue），其他柱等沒帶 index 的 bar cue（寫在後面）才一起長。
- SH13～SH14：先標甲，切點前 0.2 秒才換標乙（還在換），下一句就切到接續分鏡（什麼 cue 都沒寫）。

整串的第一個分鏡寫 push 0（不慢推），同一串裡不同時刻的畫面可以逐像素比；慢推的連續性另有測試（test_build.py）。
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
    "s01": "四組數字裡，甲最高。",
    "s02": "丁是負的，而且跌最深。",
    "s03": "乙排第二。",
    "s04": "利差先倒掛，再慢慢轉正。",
    "s05": "最後一個月，回到正的。",
    "s06": "這就是轉正。",
    "s07": "換一種刻度來看。",
    "s08": "甲組三十，乙組降到十二點五。",
    "s09": "再看丙組，回到二十。",
    "s10": "先放兩組。",
    "s11": "第三組，最後才來。",
    "s12": "先看子組，其他幾組隨後。",
    "s13": "先看甲，再看乙最高，接著換下一句。",
}
# 自製的月資料（跟 test_render_clip.py 的 DIP 同一組）：先倒掛、谷底在第 9 個月、回正
DIP = [0.80, 0.55, 0.30, 0.10, -0.15, -0.40, -0.60, -0.75, -0.85, -0.80, -0.70, -0.55,
       -0.40, -0.20, 0.05, 0.25, 0.40, 0.45, 0.50, 0.55, 0.60, 0.62, 0.48, 0.35]
SOURCE = "來源：測試用自製數字"


def _a(seg: str, word: str | None = None, edge: str = "start") -> dict:
    out: dict = {"segment": seg}
    if word:
        out["word"] = word
    if edge != "start":
        out["edge"] = edge
    return out


SHOTS = [
    {"id": "SH01", "start": _a("s01"), "chart": {
        "data": {"title": "甲組最高，丁組是負的", "unit": "示意數據，百分點", "source": SOURCE, "chart": "bar",
                 "decimals": 1, "push": 0,
                 "series": [{"label": "甲", "value": 1.2}, {"label": "乙", "value": 0.8, "color": "#2E8B57"},
                            {"label": "丙", "value": 0.5}, {"label": "丁", "value": -0.9}],
                 "highlight": {"index": 0, "text": "甲最高"}},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s01"), "kind": "bar"},
                 {"at": _a("s01", "甲最高", "end"), "kind": "highlight"}]}},
    {"id": "SH02", "start": _a("s02"), "continues": True, "chart": {
        "data": {"highlight": {"index": 3, "text": "跌最深"}},
        "cues": [{"at": _a("s02", "跌最深"), "kind": "highlight"}]}},
    {"id": "SH03", "start": _a("s03"), "continues": True, "chart": {
        "data": {"highlight": {"index": 1, "text": "排第二"}},
        "cues": [{"at": _a("s03"), "kind": "highlight"}]}},
    {"id": "SH04", "start": _a("s04"), "chart": {
        "data": {"title": "利差先倒掛再轉正", "unit": "示意數據，百分點", "source": SOURCE, "chart": "line",
                 "decimals": 2, "push": 0, "xTicks": [0, 12, 23], "colors": {"main": "#7B2CBF"},
                 "series": [{"label": f"{2024 + i // 12}-{i % 12 + 1:02d}", "value": v} for i, v in enumerate(DIP)],
                 "highlight": {"index": 8, "text": "倒掛最深"}},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s04"), "kind": "line"},
                 {"at": _a("s04", "倒掛", "end"), "kind": "number", "text": "觀察",
                  "data": {"value": 24, "suffix": " 個月"}},
                 {"at": _a("s04", "轉正"), "kind": "highlight"}]}},
    {"id": "SH05", "start": _a("s05"), "continues": True, "chart": {
        "data": {"highlight": {"index": 23, "text": "回到正的"}},
        "cues": [{"at": _a("s05", "正的"), "kind": "highlight"}]}},
    {"id": "SH06", "start": _a("s06"), "continues": True, "chart": {
        "data": {"highlight": {"index": 23, "text": "這就是轉正"}},
        "cues": [{"at": _a("s06"), "kind": "highlight"}]}},
    {"id": "SH07", "start": _a("s07"), "chart": {
        "data": {"title": "同一組數字，縱軸寫死", "unit": "示意數據，百分點", "source": SOURCE, "chart": "line",
                 "decimals": 2, "push": 0, "xTicks": [0, 12, 23], "colors": {"main": "#7B2CBF"}, "yRange": [-2, 2],
                 "series": [{"label": f"{2024 + i // 12}-{i % 12 + 1:02d}", "value": v} for i, v in enumerate(DIP)]},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s07"), "kind": "line"}]}},
    # 一句長一根柱：甲、乙在 SH08 唸到時長，丙的 bar cue 寫在接續分鏡 SH09（唸到「回到」才長）。乙比甲低（「降到」）
    {"id": "SH08", "start": _a("s08"), "chart": {
        "data": {"title": "三組數字，一句一根", "unit": "示意數據", "source": SOURCE, "chart": "bar", "decimals": 1, "push": 0,
                 "series": [{"label": "甲", "value": 30}, {"label": "乙", "value": 12.5}, {"label": "丙", "value": 20}],
                 "highlight": {"index": 1, "text": "不到一半"}},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s08", "甲組"), "kind": "bar", "data": {"index": 0}},
                 {"at": _a("s08", "乙組"), "kind": "bar", "data": {"index": 1}},
                 {"at": _a("s08", "十二點五", "end"), "kind": "highlight"}]}},
    {"id": "SH09", "start": _a("s09"), "continues": True, "chart": {
        "cues": [{"at": _a("s09", "回到"), "kind": "bar", "data": {"index": 2}}]}},
    # 第一個分鏡沒寫 bar cue（柱在版式之後自己長），接續分鏡才寫第三根的 bar cue
    {"id": "SH10", "start": _a("s10"), "chart": {
        "data": {"title": "沒寫 bar cue 的柱", "unit": "示意數據", "source": SOURCE, "chart": "bar", "decimals": 1, "push": 0,
                 "series": [{"label": "子", "value": 3}, {"label": "丑", "value": 4}, {"label": "寅", "value": 5}]},
        "cues": [{"at": 0, "kind": "title"}]}},
    {"id": "SH11", "start": _a("s11"), "continues": True, "chart": {
        "cues": [{"at": _a("s11", "最後"), "kind": "bar", "data": {"index": 2}}]}},
    # 先長一根（帶 index），其他的稍後一起長（沒帶 index 的 bar cue 寫在後面）
    {"id": "SH12", "start": _a("s12"), "chart": {
        "data": {"title": "先一根，其他隨後", "unit": "示意數據", "source": SOURCE, "chart": "bar", "decimals": 1, "push": 0,
                 "series": [{"label": "子", "value": 2}, {"label": "丑", "value": 3}, {"label": "寅", "value": 4}]},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s12", "子組"), "kind": "bar", "data": {"index": 0}},
                 {"at": _a("s12", "其他"), "kind": "bar"}]}},
    # 標註在切點前 0.4 秒才開始（還沒長完），下一句就切到接續分鏡
    {"id": "SH13", "start": _a("s13"), "chart": {
        "data": {"title": "標註還沒長完就切", "unit": "示意數據", "source": SOURCE, "chart": "bar", "decimals": 1, "push": 0,
                 "series": [{"label": "甲", "value": 2}, {"label": "乙", "value": 3}],
                 "highlight": {"index": 1, "text": "乙最高"}},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s13", "甲"), "kind": "bar"},
                 {"at": _a("s13", "先看甲", "end"), "kind": "highlight", "text": "先看甲", "data": {"index": 0}},
                 {"at": _a("s13", "最高"), "kind": "highlight"}]}},
    {"id": "SH14", "start": _a("s13", "高"), "continues": True, "chart": {"cues": []}},
]

LAST = -1.0
"""時刻寫 LAST＝這個分鏡的最後一幀。"""
NEXT = -2.0
"""時刻寫 NEXT＝這個分鏡照自己的時間再往後一幀（＝下一個分鏡第一幀的那一刻）：切點時還在動的東西，拿它跟下一個分鏡的第一幀比。"""
AFTER_BAR = {"乙剛長": (1, 0.15), "乙長完": (1, 0.9)}
"""時刻寫這些名字＝那根柱（index）的 bar cue 之後幾秒：乙在 0.7 秒裡長完，0.15 秒時柱上的數字（照往上數）才到八成。"""
STILLS = {
    "SH01": [0.0, LAST],
    "SH02": [0.0, 0.7, LAST],   # 0.7 秒：還沒唸到「跌最深」
    "SH03": [0.0, 0.2, LAST],
    "SH04": [0.0, 1.2, 1.3, 1.4, LAST],   # 1.2～1.4 秒：折線快長完（線頭跑到右邊、靠近刻度值）
    "SH05": [0.0, LAST],
    "SH06": [0.0, LAST],
    "SH07": [LAST],
    "SH08": ["乙剛長", "乙長完", LAST],
    "SH09": [0.0, 0.7, LAST],   # 0.7 秒：還沒唸到「回到」
    "SH10": [LAST],
    "SH11": [0.0, LAST],
    "SH12": [0.85, LAST],   # 0.85 秒：唸過「子組」、還沒唸到「其他」
    "SH13": [NEXT],
    "SH14": [0.0],
}


@dataclass
class Chain:
    aspect: str
    specs: dict[str, dict]
    frames: dict[tuple[str, float], np.ndarray]

    def at(self, sid: str, t: float) -> np.ndarray:
        return self.frames[(sid, t)]

    def last(self, sid: str) -> np.ndarray:
        return self.frames[(sid, LAST)]


@pytest.fixture(scope="module", params=["16x9", "9x16"])
def chain(request, tmp_path_factory) -> Chain:
    aspect = request.param
    root = tmp_path_factory.mktemp(f"接續 {aspect}") / "專案"
    root.mkdir()
    (root / "narration.json").write_text(json.dumps({"segments": NARRATION}, ensure_ascii=False), encoding="utf-8")
    (root / "shots.json").write_text(json.dumps({"shots": SHOTS}, ensure_ascii=False), encoding="utf-8")
    (root / "settings.json").write_text(json.dumps({"gap": 0.4}), encoding="utf-8")
    plan = plan_build(root, tts="fake", fmt=FORMATS[aspect])
    specs = {c.id: c.spec for c in plan.clips}

    def when(spec: dict, t: float | str) -> float:
        if t == LAST:
            return round(spec["duration"] - 1 / FPS, 4)
        if t == NEXT:
            return round(spec["duration"], 4)
        if isinstance(t, str):
            index, dt = AFTER_BAR[t]
            [at] = [q["at"] for q in spec["cues"] if q["kind"] == "bar" and (q.get("data") or {}).get("index") == index]
            return round(at + dt, 4)
        return t

    def work(sid: str) -> list[tuple[tuple[str, float], np.ndarray]]:
        spec = specs[sid]
        times = [when(spec, t) for t in STILLS[sid]]
        paths = render_stills(spec, root / "stills" / sid, times).paths
        return [((sid, key), np.asarray(Image.open(p).convert("RGB")).astype(int)) for key, p in zip(STILLS[sid], paths)]

    frames = dict(pair for out in run_parallel(list(STILLS), work, jobs=default_jobs()) for pair in out)
    return Chain(aspect, specs, frames)


def changed(a: np.ndarray, b: np.ndarray, thresh: int = 40) -> int:
    """兩張圖有幾個像素明顯不一樣（任一色版差 > thresh）。"""
    return int((np.abs(a - b).max(axis=-1) > thresh).sum())


AXIS_RIGHT = {"16x9": 1810, "9x16": 900}
"""刻度值右對齊在圖區右緣（x 像素）：16:9 是畫面寬減右邊距 90、再減 20；9:16 讓開右側按鈕欄（右邊距 160）。"""


def grid_span(chain: Chain, img: np.ndarray) -> tuple[int, int]:
    """最上面、最下面那條格線的 y：刻度值左邊、折線碰不到的那一小段裡，整列都是格線灰（或零軸黑）的列。"""
    r = AXIS_RIGHT[chain.aspect]
    win = img[:, r - 75: r - 2]
    grey = (win.max(axis=-1) <= 240) & (np.ptp(win, axis=-1) <= 20)
    rows = np.nonzero(grey.mean(axis=1) >= 0.9)[0]
    assert len(rows) >= 2, "找不到格線"
    return int(rows.min()), int(rows.max())


def tick_column(chain: Chain, img: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """右側刻度值那一欄（照 ref 那張圖的格線範圍）。"""
    r = AXIS_RIGHT[chain.aspect]
    top, bottom = grid_span(chain, ref)
    return img[top - 45: bottom + 5, r - 75: r + 5]


# ---------------------------------------------------------------- 3. 整串的軸不跳


@pytest.mark.parametrize("shots", [("SH01", "SH02", "SH03"), ("SH04", "SH05", "SH06")])
def test_axis_stays_put_through_a_continued_chain(chain, shots):
    """同一串接續分鏡，不管標的是正數柱（字在上面）、負數柱（字在下面）還是折線哪一點，刻度欄一個像素都不動。
    原本：每個分鏡只照自己的標註留空間，標到負數柱時底下多一格刻度，整張圖縮一截。"""
    ref = chain.last(shots[0])
    for sid in shots[1:]:
        n = changed(tick_column(chain, chain.last(sid), ref), tick_column(chain, ref, ref))
        assert n == 0, f"{sid} 的刻度欄跟 {shots[0]} 不一樣（{n} 個像素）"
# ---------------------------------------------------------------- 1. 切點看不出來


@pytest.mark.parametrize("before, after", [("SH01", "SH02"), ("SH02", "SH03"), ("SH04", "SH05"), ("SH05", "SH06")])
def test_continued_chart_looks_the_same_across_the_cut(chain, before, after):
    """上一個分鏡最後一幀 vs 接續分鏡第一幀：標註、淡掉的柱、線尾數值都要一樣（只有這句要換的東西才開始動，
    第一幀頂多動一點點）。原本：標註在切點消失、淡掉的柱閃回原色、線尾數值跳出來。"""
    n = changed(chain.last(before), chain.at(after, 0.0))
    assert n < 400, f"{before}→{after} 切點前後有 {n} 個像素變了"


def test_previous_label_stays_until_the_new_one_is_spoken(chain):
    """SH02 從 s02 開頭切入，到「跌最深」才換標註：在那之前畫面跟切點一樣（SH01 的標註、淡掉的柱都還在）。"""
    n = changed(chain.at("SH02", 0.0), chain.at("SH02", 0.7))
    assert n < 50, f"還沒唸到新標註，畫面就變了 {n} 個像素"


def dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """mask 往外擴 r 像素（方形）。"""
    m = np.pad(mask.astype(np.int64), ((r + 1, r), (r + 1, r)))
    c = m.cumsum(0).cumsum(1)
    k = 2 * r + 1
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) > 0


def test_only_what_the_new_label_changes_moves_during_the_switch(chain):
    """SH03 一切入就換標註（丁→乙）：換的那 0.4 秒裡，新舊兩個樣子都一樣的地方（例如兩次都淡掉的「丙」）一直不動。
    原本：切點先閃回「全部沒淡掉」，再一起淡下去。比對範圍去掉新舊標註附近 50 像素（圓點彈出的光圈）。"""
    old, new, mid = chain.last("SH02"), chain.last("SH03"), chain.at("SH03", 0.2)
    same = ~dilate(np.abs(old - new).max(axis=-1) > 8, 50)
    moved = int(((np.abs(mid - old).max(axis=-1) > 40) & same).sum())
    assert moved < 150, f"換標註時，新舊都一樣的地方動了 {moved} 個像素"


# ---------------------------------------------------------------- 4. x 軸名字不跳、線頭數值不壓刻度


def x_label_row(chain: Chain, img: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """x 軸名字那一列（最下面那條格線底下）。"""
    _, bottom = grid_span(chain, ref)
    return img[bottom + 8: bottom + 90]


def test_x_axis_labels_stay_put_through_a_continued_chain(chain):
    """折線一串：SH04 標谷底（不在 xTicks 上）、SH05／SH06 標最後一點，x 軸寫名字的位置從頭到尾一樣。
    原本：被標註的那一點硬塞一個名字（擠掉旁邊原本有的），換標註時 x 軸名字跟著變。"""
    ref = chain.last("SH04")
    for sid in ("SH05", "SH06"):
        n = changed(x_label_row(chain, chain.last(sid), ref), x_label_row(chain, ref, ref))
        assert n == 0, f"{sid} 的 x 軸名字跟 SH04 不一樣（{n} 個像素）"


@pytest.mark.parametrize("t", [1.2, 1.3, 1.4])
def test_moving_line_value_never_covers_the_axis_numbers(chain, t):
    """折線長出來時，線頭旁邊跟著跑的數值快到右邊時不能壓到右側的刻度值：刻度欄跟長完之後一樣。"""
    ref = chain.last("SH04")
    n = changed(tick_column(chain, chain.at("SH04", t), ref), tick_column(chain, ref, ref))
    assert n == 0, f"{t} 秒線頭數值壓到刻度欄（{n} 個像素）"


# ---------------------------------------------------------------- 2. 折線的縱軸不被大數字、標註撐得太開


PURPLE = np.array([0x7B, 0x2C, 0xBF])


def line_share(chain: Chain, img: np.ndarray) -> float:
    """折線（紫色）的上下範圍占格線上下範圍幾成。"""
    top, bottom = grid_span(chain, img)
    plot = img[top: bottom + 1, : AXIS_RIGHT[chain.aspect] - 80]
    ys = np.nonzero((np.abs(plot - PURPLE).max(axis=-1) <= 40).any(axis=1))[0]
    return (ys.max() - ys.min()) / (bottom - top)


def test_line_chart_with_a_big_number_and_a_label_is_not_squashed(chain):
    """數字 −0.85～0.80、有大數字、有標註：原本為了標註在最高點上方硬留 170，圖區又被大數字那一條吃掉，
    縱軸被撐到 −1～3，線只占四成高。標註放得下就不用多留，線至少占六成高。"""
    share = line_share(chain, chain.last("SH04"))
    assert share >= 0.6, f"線只占格線範圍的 {share:.0%}"


def test_y_range_written_in_the_chart_is_used_as_is(chain):
    """yRange [-2, 2]：縱軸就是 −2～2，數字 −0.85～0.80 的線大約占四成高（1.65 ÷ 4）。"""
    share = line_share(chain, chain.last("SH07"))
    assert 0.36 <= share <= 0.47, f"線占格線範圍的 {share:.0%}，不像是 −2～2"


# ---------------------------------------------------------------- 5. 自己寫的柱顏色不被標註淡掉


GREEN = np.array([0x2E, 0x8B, 0x57])


@pytest.mark.parametrize("sid", ["SH01", "SH02"])
def test_a_bar_with_its_own_colour_keeps_it_when_another_bar_is_labelled(chain, sid):
    """「乙」寫了 color（綠）：標註在別根柱上時，沒寫顏色的柱淡成灰，乙還是綠的（顏色是作者指定的身分，不是強調）。"""
    green = int((np.abs(chain.last(sid) - GREEN).max(axis=-1) <= 12).sum())
    assert green > 1500, f"{sid} 標註出現後，乙的綠色只剩 {green} 個像素"


# ---------------------------------------------------------------- 6. 影片第一幀就讀得到


SAFE_TOP = {"16x9": 0, "9x16": 240}


def title_ink(chain: Chain, img: np.ndarray) -> int:
    """標題那一帶（內容框頂端往下 200 像素）的深色字有幾個像素。"""
    top = SAFE_TOP[chain.aspect]
    return int((img[top: top + 200].max(axis=-1) < 80).sum())


def test_first_frame_of_the_video_already_shows_the_title(chain):
    """成片第 0 幀就是第一個分鏡的第 0 幀：標題、副標已經在（原本標題正在淡入，第一幀幾乎全白）。"""
    first, settled = title_ink(chain, chain.at("SH01", 0.0)), title_ink(chain, chain.last("SH01"))
    assert first > 2000, f"第一幀標題那一帶只有 {first} 個深色像素"
    assert first >= 0.95 * settled


def test_later_charts_still_animate_their_title_in(chain):
    """不是第一個、也不接續的分鏡（SH04），標題照舊在切進來之後才淡入（第 0 幀還沒有字）。"""
    assert title_ink(chain, chain.at("SH04", 0.0)) < 200


# ---------------------------------------------------------------- 計時實測：接續分鏡裡的 bar cue


def test_a_bar_cued_in_a_continuation_grows_there_and_not_in_the_shot_before(chain):
    """「一句長一根柱、下一句換一個標註」要能並存（計時實測的卡點 1）：丙的 bar cue 寫在接續分鏡 SH09。
    SH08 從頭到尾沒有丙（原本沒在第一個分鏡寫 cue 的柱一開場就長好），切點不跳，唸到「回到」丙才長出來。"""
    cut = changed(chain.last("SH08"), chain.at("SH09", 0.0))
    assert cut < 400, f"SH08→SH09 切點前後有 {cut} 個像素變了"
    early = changed(chain.at("SH09", 0.0), chain.at("SH09", 0.7))
    assert early < 50, f"還沒唸到「回到」，畫面就變了 {early} 個像素"
    grown = changed(chain.at("SH09", 0.7), chain.last("SH09"))
    assert grown > 5000, f"唸到「回到」之後丙沒長出來（只變了 {grown} 個像素）"


def test_bars_without_a_cue_stay_grown_when_a_continuation_cues_another_bar(chain):
    """第一個分鏡沒寫 bar cue（子、丑在版式之後自己長好），接續分鏡 SH11 才寫寅的 bar cue：切點子、丑不會變回 0 重長。"""
    cut = changed(chain.last("SH10"), chain.at("SH11", 0.0))
    assert cut < 400, f"SH10→SH11 切點前後有 {cut} 個像素變了"
    assert changed(chain.at("SH11", 0.0), chain.last("SH11")) > 5000, "寅沒在 SH11 長出來"


BLUE = np.array([0x00, 0x6B, 0xA2])


def visible_bars(img: np.ndarray) -> int:
    """畫面上有幾根（預設藍色的）柱：有藍色像素的直欄連成幾段。"""
    cols = (np.abs(img - BLUE).max(axis=-1) <= 40).any(axis=0)
    return int(np.count_nonzero(np.diff(cols.astype(int)) == 1) + cols[0])


def test_bars_without_an_index_wait_for_their_own_cue_written_after_an_indexed_one(chain):
    """先長一根（帶 index 的 bar cue）、其他的等後面那個沒帶 index 的 bar cue 一起長：唸到「其他」之前只有「子」。
    原本沒帶 index 的柱跟著時間最早的 bar cue 長（後面那個 cue 寫了也沒作用），「子」一出來就全部跟著出來。"""
    early, settled = visible_bars(chain.at("SH12", 0.85)), visible_bars(chain.last("SH12"))
    assert early == 1, f"唸到「其他」之前畫面上就有 {early} 根柱"
    assert settled == 3, f"最後只有 {settled} 根柱"


# ---------------------------------------------------------------- 計時實測 R2-7：標註的圓點接在引線上


RED = np.array([0xE3, 0x12, 0x0B])
"""品牌色沒設時的標註圓點（引擎原版的紅）。"""


def label_dot(chain: Chain, img: np.ndarray) -> tuple[int, int, int]:
    """標註圓點的中心 (x, y) 與半徑（像素）。頂端色條、小旗也是紅的：只看標題以下。"""
    top = SAFE_TOP[chain.aspect] + 100
    ys, xs = np.nonzero(np.abs(img[top:] - RED).max(axis=-1) <= 60)
    assert len(xs) > 100, "找不到標註的圓點"
    return round(xs.mean()), round(ys.mean()) + top, round(float(np.sqrt(len(xs) / np.pi)))


def ink(img: np.ndarray) -> np.ndarray:
    """黑色的字和引線（不是彩色的柱、不是淺灰的格線）。"""
    return (img.max(axis=-1) < 160) & (np.ptp(img, axis=-1) < 40)


@pytest.mark.parametrize("sid, way", [("SH08", "上"), ("SH02", "下")])
def test_label_dot_sits_at_the_end_of_its_leader(chain, sid, way):
    """標註放在柱的正上方（SH08：乙旁邊有高一倍多的甲）或負數柱底下（SH02：丁）時，圓點要接在引線上，
    往文字那一頭緊接著就是引線（計時實測 R2-7：圓點落在柱角、引線從柱中間拉出去，兩個沒連在一起，像多出一個點）。"""
    img = chain.last(sid)
    x, y, r = label_dot(chain, img)
    rows = range(y - r - 14, y - r - 2) if way == "上" else range(y + r + 2, y + r + 14)
    leader = int(ink(img)[rows.start: rows.stop, x - 3: x + 4].sum())
    assert leader >= 8, f"{sid} 的圓點（{x}, {y}）往{way}沒有接著引線（只有 {leader} 個黑色像素）"


# ---------------------------------------------------------------- 計時實測 R1-5：往下降的柱，柱上的數字不往上滾


def bar_label_width(img: np.ndarray, k: int) -> int:
    """從左數第 k 根（藍色的）柱，柱頂上那個數字有多寬（黑色字的左右範圍）。"""
    cols = (np.abs(img - BLUE).max(axis=-1) <= 40).any(axis=0)
    starts = np.nonzero(np.diff(cols.astype(int)) == 1)[0] + 1
    ends = np.nonzero(np.diff(cols.astype(int)) == -1)[0] + 1
    x0, x1 = int(starts[k]), int(ends[k])
    top = int(np.nonzero((np.abs(img[:, x0:x1] - BLUE).max(axis=-1) <= 40).any(axis=1))[0].min())
    xs = np.nonzero(ink(img[top - 70: top - 4, x0 - 40: x1 + 40]).any(axis=0))[0]
    assert len(xs), "柱頂上沒有數字"
    return int(xs.max() - xs.min())


def test_a_bar_lower_than_the_one_before_shows_its_number_without_counting_up(chain):
    """「乙組降到十二點五」：乙比左邊先長的甲（30）低，柱上的數字不從 0 往上數（往上數跟「降到」打架，觀眾看到的是
    9.8、11.6…，計時實測 R1-5），長出來時就寫 12.5。量的是剛長出來時（往上數的話才到 9.8）數字的寬度。"""
    early, settled = bar_label_width(chain.at("SH08", "乙剛長"), 1), bar_label_width(chain.at("SH08", "乙長完"), 1)
    assert early >= settled - 3, f"乙剛長出來時柱上的數字比長完窄（{early} vs {settled} 像素）：還在往上數"


def test_a_label_still_animating_at_the_cut_keeps_going_in_the_continuation(chain):
    """切點前 0.2 秒才從甲換標乙（舊的還在淡出、柱還在換顏色），下一句就切到接續分鏡：接續分鏡第一幀接著上一幀。
    原本沿用過來的 cue 一律放在 −3 秒（第一幀已經畫完），而且只沿用最後一個標註：切點前還在動的標註直接跳成長完的樣子，
    淡掉的柱從「沒有前一個標註」重來、閃回原色（計時實測的專案改成一句一個分鏡時量到）。"""
    # 還在動，相鄰兩幀本來就不一樣：拿 SH13 照自己的時間再往後一幀（同一刻）跟 SH14 第一幀比
    cut = changed(chain.at("SH13", NEXT), chain.at("SH14", 0.0))
    assert cut < 400, f"SH13 切點那一刻跟 SH14 第一幀有 {cut} 個像素不一樣"

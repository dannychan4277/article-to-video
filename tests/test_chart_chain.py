"""財經圖表的一串接續分鏡：切點看不出來、整串的軸與刻度不跳、軸距合理、第一幀就讀得到。

切點：建置計畫（`plan_build`，假配音層，不渲片段）→ 每個分鏡實際給引擎的 spec → 引擎渲的靜態圖（`render_stills`）。
一個自製的測試專案（數字是自製示意數字，不是任何文章的），兩種比例各渲一次、所有測試共用：

- SH01～SH03：柱狀圖一串。SH01 標「甲」（正數柱），SH02 從 s02 開頭切入、到「跌最深」才換標「丁」（負數柱），
  SH03 從 s03 開頭切入、切點就換標「乙」。乙自己寫了顏色（綠）。
- SH04～SH06：折線一串（有大數字）。SH04 標谷底（不在 xTicks 上），SH05 換標最後一點，SH06 同一點只換字。

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
]

LAST = -1.0
"""時刻寫 LAST＝這個分鏡的最後一幀。"""
STILLS = {
    "SH01": [0.0, LAST],
    "SH02": [0.0, 0.7, LAST],   # 0.7 秒：還沒唸到「跌最深」
    "SH03": [0.0, LAST],
    "SH04": [0.0, LAST],
    "SH05": [0.0, LAST],
    "SH06": [0.0, LAST],
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

    def work(sid: str) -> list[tuple[tuple[str, float], np.ndarray]]:
        spec = specs[sid]
        times = [round(spec["duration"] - 1 / FPS, 4) if t == LAST else t for t in STILLS[sid]]
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

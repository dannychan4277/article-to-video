"""財經圖表的鏡頭 B（設定 camera = snap）：畫面平常停住，標註出現的那一刻快推一下，然後停住。

切點：
- 建置計畫（`plan_build`，假配音層）給引擎的 spec：快推排在哪些時刻、推多少。
- 引擎照 spec 渲的靜態圖（`render_stills`）：快推前後的像素變化、切點、推到最近時字有沒有被切或壓進字幕帶。
- 自動檢查（`build_project`＋`run_checks`，小畫面）：停住不算「沒在動」、快推不算「跳變」，真的跳變照樣抓。

測試專案是自製的（數字是自製示意數字）：
- SH01～SH02：柱狀圖一串。SH01 先滾大數字（不推），唸到「甲最高」標甲；SH02 接續，唸到「跌最深」換標丙。
- SH03：折線，長完之後唸到「回到正的」標最後一點，之後一直停著（停住的比例遠超過 A 的「沒在動」門檻）。
- SH04：柱子在一幀裡全部跳出來（真的跳變），之後才標註。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from article_to_video.build import plan_build
from article_to_video.timeline import FORMATS

FPS = 30
NARRATION = {
    "s01": "三組數字裡，甲最高。",
    "s02": "丙是負的，而且跌最深。",
    "s03": "換成折線來看，最後回到正的，之後就一直停在這裡讓大家看清楚。",
    "s04": "六根柱子一起跳出來，第三根最高。",
}
SOURCE = "來源：測試用自製數字"
DIP = [0.80, 0.55, 0.30, 0.10, -0.15, -0.40, -0.60, -0.75, -0.85, -0.80, -0.70, -0.55,
       -0.40, -0.20, 0.05, 0.25, 0.40, 0.45, 0.50, 0.55, 0.60, 0.62, 0.48, 0.35]


def _a(seg: str, word: str | None = None, edge: str = "start") -> dict:
    out: dict = {"segment": seg}
    if word:
        out["word"] = word
    if edge != "start":
        out["edge"] = edge
    return out


SHOTS = [
    {"id": "SH01", "start": _a("s01"), "chart": {
        "data": {"title": "甲組最高，丙組是負的", "unit": "示意數據，百分點", "source": SOURCE, "chart": "bar",
                 "decimals": 1,
                 "series": [{"label": "甲", "value": 1.2}, {"label": "乙", "value": 0.8}, {"label": "丙", "value": -0.9}],
                 "highlight": {"index": 0, "text": "甲最高"}},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s01"), "kind": "bar"},
                 {"at": _a("s01", "數字", "end"), "kind": "number", "text": "三組", "data": {"value": 3}},
                 {"at": _a("s01", "甲最高", "end"), "kind": "highlight"}]}},
    {"id": "SH02", "start": _a("s02"), "continues": True, "chart": {
        "data": {"highlight": {"index": 2, "text": "跌最深"}},
        "cues": [{"at": _a("s02", "跌最深"), "kind": "highlight"}]}},
    {"id": "SH03", "start": _a("s03"), "chart": {
        "data": {"title": "利差先倒掛再轉正", "unit": "示意數據，百分點", "source": SOURCE, "chart": "line",
                 "decimals": 2, "xTicks": [0, 12, 23],
                 "series": [{"label": f"{2024 + i // 12}-{i % 12 + 1:02d}", "value": v} for i, v in enumerate(DIP)],
                 "highlight": {"text": "回到正的"}},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s03"), "kind": "line", "dur": 1.2},
                 {"at": _a("s03", "回到正的"), "kind": "highlight"}]}},
    {"id": "SH04", "start": _a("s04"), "chart": {
        "data": {"title": "六根柱子一起跳出來", "unit": "示意數據，%", "source": SOURCE, "chart": "bar", "decimals": 1,
                 "series": [{"label": f"第{i + 1}", "value": v} for i, v in enumerate([3.0, 3.4, 4.6, 3.8, 3.2, 3.5])],
                 "highlight": {"index": 2, "text": "最高"}},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s04", "一起"), "kind": "bar", "dur": 0.034},
                 {"at": _a("s04", "最高"), "kind": "highlight"}]}},
]


def write_project(root: Path, camera: str | None = "snap", shots: list[dict] = SHOTS) -> Path:
    root.mkdir(parents=True)
    (root / "narration.json").write_text(json.dumps({"segments": NARRATION}, ensure_ascii=False), encoding="utf-8")
    (root / "shots.json").write_text(json.dumps({"shots": shots}, ensure_ascii=False, indent=1), encoding="utf-8")
    settings = {"tts": "fake", "gap": 0.4, "tail": 1.2, "music": "none"}
    if camera:
        settings["camera"] = camera
    (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return root


def specs_of(root: Path, aspect: str = "16x9") -> dict[str, dict]:
    return {c.id: c.spec for c in plan_build(root, tts="fake", fmt=FORMATS[aspect]).clips}


def cue_at(spec: dict, kind: str, *, carried: bool = False) -> list[float]:
    return [q["at"] for q in spec["cues"] if q["kind"] == kind and bool((q.get("data") or {}).get("carried")) == carried]


@pytest.fixture(scope="module")
def snap_specs(tmp_path_factory) -> dict[str, dict]:
    return specs_of(write_project(tmp_path_factory.mktemp("快推") / "專案"))


# ---------------------------------------------------------------- spec：快推排在哪裡、推多少


def test_the_camera_snaps_exactly_when_each_label_appears_and_never_drifts(snap_specs):
    sh01 = snap_specs["SH01"]["data"]
    assert "push" not in sh01 and "pushFrom" not in sh01  # 沒有等速慢推
    (label_at,) = cue_at(snap_specs["SH01"], "highlight")
    assert [s["at"] for s in sh01["camera"]["snaps"]] == [label_at]  # 大數字落定不推，標註出現才推
    assert sh01["camera"]["dur"] == pytest.approx(0.3)


def test_a_continued_shot_replays_the_chain_so_far_and_its_carried_label_does_not_snap_again(snap_specs):
    a, b = snap_specs["SH01"], snap_specs["SH02"]
    (own_at,) = cue_at(b, "highlight")
    assert cue_at(b, "highlight", carried=True)  # 上一個分鏡的標註沿用過來（第一幀就在）
    snaps = b["data"]["camera"]["snaps"]
    # 上一個分鏡的快推照真正的時刻排（相對這個分鏡是負的），這個分鏡自己的標註再推一次；沿用的標註不推
    assert [s["at"] for s in snaps] == pytest.approx([cue_at(a, "highlight")[0] - a["duration"], own_at], abs=1e-6)
    assert [s["index"] for s in snaps] == [0, 2]
    # 整串預設總共推近 8%，兩個標註各推一半；SH01 只看得到第一下
    assert [s["zoom"] for s in snaps] == pytest.approx([1.04, 1.08])
    assert [s["zoom"] for s in a["data"]["camera"]["snaps"]] == pytest.approx([1.04])


def test_push_on_the_first_shot_sets_the_total_and_zero_keeps_the_camera_still(tmp_path):
    shots = json.loads(json.dumps(SHOTS))
    shots[0]["chart"]["data"]["push"] = 0.03
    shots[2]["chart"]["data"]["push"] = 0
    specs = specs_of(write_project(tmp_path / "專案", shots=shots))
    assert [s["zoom"] for s in specs["SH02"]["data"]["camera"]["snaps"]] == pytest.approx([1.015, 1.03])
    assert specs["SH03"]["data"]["camera"]["snaps"] == []


def test_the_slow_push_is_still_what_a_video_gets_with_camera_set_to_slow(tmp_path):
    specs = specs_of(write_project(tmp_path / "專案", camera="slow"))
    a, b = specs["SH01"]["data"], specs["SH02"]["data"]
    assert "camera" not in a and a["pushFrom"] == 0 and a["push"] > 0
    assert b["pushFrom"] == pytest.approx(a["push"], abs=1e-6)

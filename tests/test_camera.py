"""財經圖表的快推（設定 camera = snap）：畫面平常停住，標註出現的那一刻快推一下，然後停住。

切點：
- 建置計畫（`plan_build`，假配音層）給引擎的 spec：快推排在哪些時刻、推多少。
- 引擎照 spec 渲的靜態圖（`render_stills`）：快推前後的像素變化、切點、推到最近時字有沒有被切或壓進字幕帶。
- 自動檢查（`build_project`＋`run_checks`，小畫面）：停住不算「沒在動」、快推不算「跳變」，真的跳變照樣抓。

測試專案是自製的（數字是自製示意數字）：
- SH01～SH02：柱狀圖一串。SH01 先滾大數字（不推），唸到「甲最高」標甲；SH02 接續，唸到「跌最深」換標丙。
- SH03：折線，長完之後唸到「回到正的」標最後一點，之後一直停著（停住的比例遠超過慢推的「沒在動」門檻）。
- SH04：柱子在一幀裡全部跳出來（真的跳變），之後才標註。
- SH07：分鏡設定寫了第二個畫面事件，引擎卻什麼都沒畫（快推片段的「動態」要抓到）。
- SQ1～SQ2：柱狀圖一串、四個標註（SQ1 三個、SQ2 一個）。整串預設推 6%，每下至少 2%：只推三下，
  每個分鏡的第一個標註先保留（換分鏡＝換重點），剩下的照時間順序，用完就停——SQ1 的第三個標註不推。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from article_to_video.auto_checks import CheckReport, run_checks
from article_to_video.build import BuildResult, build_project, plan_build
from article_to_video.clips import default_jobs, render_stills, run_parallel
from article_to_video.timeline import FORMATS, VideoFormat

SMALL = VideoFormat(name="test360", width=640, height=360, fps=30, safe_bottom=47, sub_size=18,
                    sub_margin_v=15, sub_margin_lr=20, sub_pad=4)
"""跟 test_auto_checks.py 同一個小畫面：建置、qa 都快；量的是比例。"""

FPS = 30
NARRATION = {
    "s01": "三組數字裡，甲最高，比另外兩組都高出不少。",
    "s02": "丙是負的，而且跌最深。",
    "s03": "換成折線來看，這條線先往下再往上，最後回到正的，之後停在這裡。",
    "s04": "六根柱子一起跳出來，第三根最高。",
    "s05": "這一段連續倒掛三百天。",
    "s06": "甲組一點二，乙組零點八，甲高零點四個百分點。",
    "s07": "先看這兩根柱子長出來，停一下之後，第二次說要出現的東西其實沒畫出來。",
    "s08": "按季來看，第一季的數字小幅上漲，到了第二季明顯回落，第三季又再一次大幅上漲一大段，第四季更是創下了新高。",
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
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s03"), "kind": "line", "dur": 1.0},
                 {"at": _a("s03", "回到正的"), "kind": "highlight"}]}},
    {"id": "SH04", "start": _a("s04"), "chart": {
        "data": {"title": "六根柱子一起跳出來", "unit": "示意數據，%", "source": SOURCE, "chart": "bar", "decimals": 1,
                 "series": [{"label": f"第{i + 1}", "value": v} for i, v in enumerate([3.0, 3.4, 4.6, 3.8, 3.2, 3.5])],
                 "highlight": {"index": 2, "text": "最高"}},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s04", "一起"), "kind": "bar", "dur": 0.034},
                 {"at": _a("s04", "最高"), "kind": "highlight"}]}},
    {"id": "SH05", "start": _a("s05"), "chart": {
        "data": {"title": "倒掛了很久", "unit": "示意數據", "source": SOURCE, "chart": "number"},
        "cues": [{"at": 0, "kind": "title"},
                 {"at": _a("s05", "三百天"), "kind": "number", "text": "連續倒掛", "data": {"value": 300, "suffix": " 天"}}]}},
    {"id": "SH06", "start": _a("s06"), "chart": {
        "data": {"title": "甲組比乙組高", "unit": "示意數據，%", "source": SOURCE, "chart": "compare", "decimals": 1,
                 "suffix": "%", "series": [{"label": "甲組", "value": 1.2}, {"label": "乙組", "value": 0.8}],
                 "highlight": {"index": 0}},
        "cues": [{"at": 0, "kind": "title"},
                 {"at": _a("s06", "一點二"), "kind": "number", "data": {"index": 0}},
                 {"at": _a("s06", "零點八"), "kind": "number", "data": {"index": 1}},
                 {"at": _a("s06", "甲高"), "kind": "highlight"}]}},
    # 從「零點四」切進來、差距晚 0.5 秒落定：差距的動畫在落定前 0.55 秒開始，寫在詞的開頭會在切點前就畫一半、切點跳出來
    {"id": "SH06B", "start": _a("s06", "零點四"), "continues": True, "chart": {
        "data": {}, "cues": [{"at": {**_a("s06", "零點四"), "offset": 0.5}, "kind": "gap", "text": "高", "data": {"suffix": " 個百分點"}}]}},
    # 引擎沒畫出來：第二個 bar cue 沒寫 index，引擎只照第一個 bar cue 長柱子，這一個什麼都不畫（分鏡設定說這一刻有東西）
    {"id": "SH07", "start": _a("s07"), "chart": {
        "data": {"title": "第二下什麼都沒畫", "unit": "示意數據，%", "source": SOURCE, "chart": "bar", "decimals": 1,
                 "series": [{"label": "甲", "value": 2.4}, {"label": "乙", "value": 1.6}]},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s07"), "kind": "bar"}, {"at": _a("s07", "第二次"), "kind": "bar"}]}},
    # 一串四個標註：只推得了三下（6% ÷ 每下 2%）。照時間順序會推前三個；改成先保留每個分鏡的第一個（小幅、新高），
    # 剩下一下給時間最早的（回落）——第三季那個標註只換字、鏡頭不動
    {"id": "SQ1", "start": _a("s08"), "chart": {
        "data": {"title": "四季的漲跌", "unit": "示意數據，%", "source": SOURCE, "chart": "bar", "decimals": 1,
                 "series": [{"label": f"第{q}季", "value": v} for q, v in zip("一二三四", [1.2, -0.6, 2.4, 3.1])]},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s08"), "kind": "bar"},
                 {"at": _a("s08", "小幅"), "kind": "highlight", "text": "小漲", "data": {"index": 0}},
                 {"at": _a("s08", "回落"), "kind": "highlight", "text": "回落", "data": {"index": 1}},
                 {"at": _a("s08", "大幅"), "kind": "highlight", "text": "大漲", "data": {"index": 2}}]}},
    {"id": "SQ2", "start": _a("s08", "第四季"), "continues": True, "chart": {
        "data": {}, "cues": [{"at": _a("s08", "新高"), "kind": "highlight", "text": "新高", "data": {"index": 3}}]}},
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
    assert sh01["camera"]["dur"] == pytest.approx(0.45)


def test_a_continued_shot_replays_the_chain_so_far_and_its_carried_label_does_not_snap_again(snap_specs):
    a, b = snap_specs["SH01"], snap_specs["SH02"]
    (own_at,) = cue_at(b, "highlight")
    assert cue_at(b, "highlight", carried=True)  # 上一個分鏡的標註沿用過來（第一幀就在）
    snaps = b["data"]["camera"]["snaps"]
    # 上一個分鏡的快推照真正的時刻排（相對這個分鏡是負的），這個分鏡自己的標註再推一次；沿用的標註不推
    assert [s["at"] for s in snaps] == pytest.approx([cue_at(a, "highlight")[0] - a["duration"], own_at], abs=1e-6)
    assert [s["index"] for s in snaps] == [0, 2]
    # 整串預設總共推近 6%，兩個標註各推一半；SH01 只看得到第一下
    assert [s["zoom"] for s in snaps] == pytest.approx([1.03, 1.06])
    assert [s["zoom"] for s in a["data"]["camera"]["snaps"]] == pytest.approx([1.03])


def test_push_on_the_first_shot_sets_the_total_and_zero_keeps_the_camera_still(tmp_path):
    shots = json.loads(json.dumps(SHOTS))
    shots[0]["chart"]["data"]["push"] = 0.03
    shots[2]["chart"]["data"]["push"] = 0
    specs = specs_of(write_project(tmp_path / "專案", shots=shots))
    # 3% 分給兩個標註每下只有 1.5%，看起來像畫面抽一下：只推一下（第一個分鏡的標註），一下推完 3%
    assert [(s["index"], s["zoom"]) for s in specs["SH02"]["data"]["camera"]["snaps"]] == [(0, pytest.approx(1.03))]
    assert specs["SH03"]["data"]["camera"]["snaps"] == []


def test_a_chain_with_more_labels_than_visible_pushes_snaps_fewer_times_starting_with_each_shots_first_label(snap_specs):
    """整串 6%、四個標註：平均分每下只有 1.5%（1080p 畫面邊角才移十幾個像素，盲評說像畫面抽一下）。每下至少推 2%，
    分不到就少推幾下：先保留每個分鏡的第一個新標註（換分鏡＝換重點），剩下的照時間順序，用完就停。"""
    q1, q2 = snap_specs["SQ1"], snap_specs["SQ2"]
    labels = cue_at(q1, "highlight")
    (fourth,) = cue_at(q2, "highlight")
    snaps = q2["data"]["camera"]["snaps"]
    assert [s["index"] for s in snaps] == [0, 1, 3]  # 第三季（index 2）那個標註不推
    assert [s["at"] for s in snaps] == pytest.approx([labels[0] - q1["duration"], labels[1] - q1["duration"], fourth],
                                                     abs=1e-6)
    assert [s["zoom"] for s in snaps] == pytest.approx([1.02, 1.04, 1.06])
    assert [s["index"] for s in q1["data"]["camera"]["snaps"]] == [0, 1]


def test_the_slow_push_is_still_what_a_video_gets_with_camera_set_to_slow(tmp_path):
    specs = specs_of(write_project(tmp_path / "專案", camera="slow"))
    a, b = specs["SH01"]["data"], specs["SH02"]["data"]
    assert "camera" not in a and a["pushFrom"] == 0 and a["push"] > 0
    assert b["pushFrom"] == pytest.approx(a["push"], abs=1e-6)


# ---------------------------------------------------------------- 引擎：渲出來的畫面


@dataclass(frozen=True)
class Lab:
    root: Path
    build: BuildResult
    report: CheckReport
    stills: dict[tuple[str, float], np.ndarray]

    def spec(self, sid: str) -> dict:
        return next(c.spec for c in self.build.plan.clips if c.id == sid)

    def frame(self, sid: str, t: float) -> np.ndarray:
        return self.stills[(sid, round(t, 4))]


def label_at(spec: dict) -> float:
    (at,) = cue_at(spec, "highlight")
    return at


def frame_t(at: float, k: int = 0) -> float:
    """at（cue 的秒數，量化到幀、小數 6 位）之後第 k 幀的時刻，跟成片裡的幀一樣是 幀號 ÷ fps。"""
    return (round(at * FPS) + k) / FPS


def _still_times(spec: dict, sid: str) -> list[float]:
    """每個分鏡要渲哪幾幀（都在 30 fps 的幀上）：標註前 0.3 秒、前一幀（還沒動）、出現那一幀起的前五幀、9、14 幀與 0.8、1.2 秒；
    SQ 那串每個標註的前一幀與 14 幀後（早就停穩）；第一幀、最後一幀。"""
    last = spec["duration"] - 1 / FPS
    out = [0.0, last]
    if sid in ("SH01", "SH03"):
        at = label_at(spec)
        out += [frame_t(at, k) for k in (-9, -2, -1, 0, 1, 2, 3, 4, 9, 14, 24, 36)]
    if sid in ("SQ1", "SQ2"):
        out += [frame_t(at, k) for at in cue_at(spec, "highlight") for k in (-1, 14)]
    return sorted(set(round(t, 4) for t in out if 0 <= t <= last + 1e-9))


@pytest.fixture(scope="module")
def lab(tmp_path_factory) -> Lab:
    root = write_project(tmp_path_factory.mktemp("快推實驗室") / "專案")
    res = build_project(root, fmt=SMALL)
    report = run_checks(res.plan)
    specs = {c.id: c.spec for c in res.plan.clips}

    def work(sid: str) -> list[tuple[tuple[str, float], np.ndarray]]:
        times = _still_times(specs[sid], sid)
        paths = render_stills(specs[sid], root / "stills" / sid, times).paths
        return [((sid, round(t, 4)), np.asarray(Image.open(p).convert("RGB")).astype(int)) for t, p in zip(times, paths)]

    stills = dict(pair for out in run_parallel(["SH01", "SH02", "SH03", "SQ1", "SQ2"], work, jobs=default_jobs()) for pair in out)
    return Lab(root, res, report, stills)


def changed(a: np.ndarray, b: np.ndarray, thresh: int = 40) -> int:
    return int((np.abs(a - b).max(axis=-1) > thresh).sum())


def title_box(img: np.ndarray) -> tuple[int, int, int, int]:
    """標題的外框 (x0, y0, x1, y1)：畫面上方兩成裡近黑色的字（標題是 #0C0C0C；品牌色條、小旗、副標都比它淺）。"""
    top = img[: int(img.shape[0] * 0.22)]
    ys, xs = np.nonzero(top.max(axis=-1) < 90)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def test_the_chart_holds_still_snaps_in_when_the_label_appears_then_holds_still_again(lab):
    spec = lab.spec("SH03")
    at = label_at(spec)
    # 標註出現前：停住（A 這時候一直在慢推）。比到前兩幀：cue 的時刻存到小數 6 位，有時會比幀早一點點，
    # 標註的光圈就在前一幀先閃出來（引擎原本就這樣，跟鏡頭無關）
    assert changed(lab.frame("SH03", frame_t(at, -9)), lab.frame("SH03", frame_t(at, -2))) == 0
    unzoomed = lab.frame("SH03", frame_t(at, -1))
    w0 = title_box(unzoomed)[2] - title_box(unzoomed)[0]
    w = {k: title_box(lab.frame("SH03", frame_t(at, k)))[2] - title_box(lab.frame("SH03", frame_t(at, k)))[0]
         for k in (2, 24)}
    assert w[24] / w0 == pytest.approx(1.04, abs=0.01)  # 推近 4%（整串只有這一個標註，一下推到每下的上限）
    assert w[2] - w0 > 0.5 * (w[24] - w0)  # 快：出現後兩幀就推了一半以上（先快後慢）
    assert title_box(lab.frame("SH03", frame_t(at, 14))) == title_box(lab.frame("SH03", frame_t(at, 24)))  # 0.45 秒內推完

    settled, later = lab.frame("SH03", frame_t(at, 24)), lab.frame("SH03", frame_t(at, 36))
    assert changed(settled, later) == 0  # 標註畫完之後：又停住


def test_the_snap_gets_going_over_a_few_frames_instead_of_jumping_on_the_first_one(lab):
    """快推不能第一幀就走掉一大半（原本 0.3 秒＋強 ease-out：第一幀就走 44%，1080p 推近 4% 時畫面邊角一幀跳 17 像素，
    兩位盲評都說像「頓一下」）。量離被標註的點（折線最後一點，在右邊）最遠的標題左緣：標註出現那一幀就開始動、
    但只走一兩成，之後沒有哪一幀跳超過三成多，前快後慢，0.45 秒內停穩。"""
    spec = lab.spec("SH03")
    at = label_at(spec)
    x = {k: title_box(lab.frame("SH03", frame_t(at, k)))[0] for k in (-1, 0, 1, 2, 3, 4, 14, 24)}
    total = x[-1] - x[24]
    assert total >= 15, x  # 推近 4%，標題左緣往左移二十多像素
    done = {k: (x[-1] - v) / total for k, v in x.items()}
    assert 0.08 <= done[0] <= 0.3, done  # 出現那一幀（第 1 幀）
    steps = [done[k] - done[k - 1] for k in (0, 1, 2, 3, 4)]
    assert max(steps) <= 0.35, steps  # 沒有哪一幀跳一大段
    assert done[4] >= 0.7 and steps[-1] < max(steps), done  # 前快後慢：五幀走完七成以上，之後越走越慢
    assert x[14] == x[24]  # 0.45 秒內停穩


def bar_top_center(img: np.ndarray, color=(0, 107, 162)) -> tuple[float, int]:
    """主色（#006BA2）柱子裡最左邊那根的中心 x、頂端 y。"""
    mask = (np.abs(img - np.array(color)).max(axis=-1) < 30)
    ys, xs = np.nonzero(mask)
    left = xs.min()
    col = xs < left + 6  # 最左那根柱的左緣附近
    xs_row = xs[ys == ys[col].min() + 4]
    first = xs_row[xs_row < left + 60]
    return (first.min() + first.max()) / 2, int(ys[col].min())


def test_the_snap_zooms_toward_the_labelled_bar_which_stays_put(lab):
    spec = lab.spec("SH01")
    at = label_at(spec)
    before, after = lab.frame("SH01", frame_t(at, -1)), lab.frame("SH01", frame_t(at, 9))
    (bx0, by0), (bx1, by1) = bar_top_center(before), bar_top_center(after)
    assert abs(bx1 - bx0) <= 1 and abs(by1 - by0) <= 1  # 被標註的柱（甲）頂端在畫面上不動
    t0, t1 = title_box(before), title_box(after)
    # 其他東西往外推開（標題變寬）。量寬度：柱寬隨根數之後三根柱擠到圖區中間，甲柱頂離標題右端很近，右端只移一兩個像素
    assert t1[0] < t0[0] - 2 and (t1[2] - t1[0]) - (t0[2] - t0[0]) > 4


def test_a_continued_shot_starts_exactly_where_the_previous_one_left_the_camera(lab):
    for prev, sid in (("SH01", "SH02"), ("SQ1", "SQ2")):   # SQ 那串中間有一個標註沒推
        a = lab.spec(prev)
        assert changed(lab.frame(prev, round(a["duration"] - 1 / FPS, 4)), lab.frame(sid, 0.0)) < 30, sid


def test_a_label_left_without_a_snap_only_swaps_the_label_while_the_camera_stays_put(lab):
    """SQ 那串推不了每一個標註（見 spec 的測試）：第三季那個標註照樣換上去，鏡頭一點都不動；推的那兩下標題往外推開。"""
    q1 = lab.spec("SQ1")
    first, second, third = cue_at(q1, "highlight")
    for at in (first, second):
        assert title_box(lab.frame("SQ1", frame_t(at, -1))) != title_box(lab.frame("SQ1", frame_t(at, 14)))
    before, after = lab.frame("SQ1", frame_t(third, -1)), lab.frame("SQ1", frame_t(third, 14))
    top = slice(0, int(before.shape[0] * 0.2))   # 標題、副標
    assert changed(before[top], after[top]) == 0
    assert changed(before, after) > 0  # 標註換過去了


# ---------------------------------------------------------------- 推到最近：字不出畫面、不進字幕帶與平台介面區


LONG = [round(0.6 * ((i % 24) / 12 - 1) ** 2 - 0.3 + i * 0.01, 2) for i in range(60)]
"""自製的 60 個月數字：點多，x 軸最左、最右的名字（以點為中心）會超出左右邊距一截。"""
LINE_CHAIN = [
    {"id": "SL1", "start": _a("s03"), "chart": {
        "data": {"title": "五年的利差，最後回到正的", "unit": "示意數據，百分點", "source": SOURCE, "chart": "line",
                 "decimals": 2, "xTicks": [0, 12, 24, 36, 48, 59],
                 "series": [{"label": f"{2020 + i // 12}-{i % 12 + 1:02d}", "value": v} for i, v in enumerate(LONG)],
                 "highlight": {"text": "回到正的"}},
        "cues": [{"at": 0, "kind": "title"}, {"at": _a("s03"), "kind": "line", "dur": 1.0},
                 {"at": _a("s03", "回到正的"), "kind": "highlight"}]}},
    {"id": "SL2", "start": _a("s04"), "continues": True, "chart": {
        "data": {"highlight": {"text": "最高"}},
        "cues": [{"at": _a("s04", "六根"), "kind": "highlight", "text": "又往上"},
                 {"at": _a("s04", "最高"), "kind": "highlight"}]}},
]


@pytest.fixture(scope="module", params=["16x9", "9x16"])
def closest(request, tmp_path_factory):
    """兩串都在第一個分鏡寫 push 0.2（建置允許的最大值）：柱那串兩下快推要推到 4%、8%；60 個點的折線那串三下都標最後一點
    （換字），要推到 4%、8%、12%（每下最多 4%），引擎照版面夾住。往右邊的最後一點推近，左邊的 x 軸名字被推向畫面左緣。
    每串渲第一個標註之前（還沒推）跟整串最後一幀；兩串另外渲每一下快推的前一幀與開始後 14 幀（停穩）。
    回傳 (畫面比例, 兩串的 (推之前, 最後一幀), 折線每一下的 (前, 後), 柱每一下的 (前, 後))。"""
    fmt = FORMATS[request.param]
    shots = json.loads(json.dumps(SHOTS[:2] + LINE_CHAIN))
    for sh in (shots[0], shots[2]):
        sh["chart"]["data"]["push"] = 0.2
    root = write_project(tmp_path_factory.mktemp(f"推到最近 {fmt.name}") / "專案", shots=shots)
    specs = specs_of(root, fmt.name)
    jobs = [(a, frame_t(label_at(specs[a]), -1)) for a in ("SH01", "SL1")]
    jobs += [(b, specs[b]["duration"] - 1 / FPS) for b in ("SH02", "SL2")]
    snap_ats = [("SL1", label_at(specs["SL1"]))] + [("SL2", at) for at in cue_at(specs["SL2"], "highlight")]
    snap_ats += [("SH01", label_at(specs["SH01"])), ("SH02", label_at(specs["SH02"]))]
    jobs += [(sid, frame_t(at, k)) for sid, at in snap_ats for k in (-1, 14)]

    def work(job):
        sid, t = job
        (path,) = render_stills(specs[sid], root / "stills" / sid, [t]).paths
        return np.asarray(Image.open(path).convert("RGB")).astype(int)

    b1, b2, a1, a2, *snaps = run_parallel(jobs, work, jobs=4)
    pairs = list(zip(snaps[::2], snaps[1::2]))
    return fmt, [(b1, a1), (b2, a2)], pairs[:3], pairs[3:]


def test_even_the_closest_snap_keeps_every_word_on_screen_and_out_of_the_subtitle_band(closest):
    fmt, chains, _, _ = closest
    for before, after in chains:
        w0, w1 = (title_box(im)[2] - title_box(im)[0] for im in (before, after))
        # 要嘛看得出推近了（至少 1.5%），要嘛完全不動；折線那串要 12% 也被夾在版面放得下的範圍。直式的折線：被標註的點靠右邊
        # 又不能動，左邊的 x 軸名字一下就頂到邊，只推得了約 1%——推一點點看起來像畫面抽一下，所以那幾下都不推
        assert w1 == w0 or 1.013 < w1 / w0 < 1.085, w1 / w0
        dark = after.mean(axis=-1) < 160  # 白底上看得見的字、線、柱
        h, w = dark.shape
        assert not dark[h - fmt.safe_bottom:].any()  # 字幕帶裡沒東西
        below_bar = slice(fmt.ui_top + 12, h - fmt.safe_bottom)  # 頂端色條（裝飾）推出去無妨
        assert not dark[below_bar, :3].any() and not dark[below_bar, w - 3:].any()  # 左右邊緣沒有被切到的字（含 x 軸兩端的名字）
        assert not (after[:3].max(axis=-1) < 90).any()  # 上緣沒有被切到的標題
        if fmt.ui_right:
            assert not dark[below_bar, w - fmt.ui_right:].any()  # 直式：右側按鈕欄底下沒有字
        if fmt.ui_top:
            assert not (after[: fmt.ui_top].max(axis=-1) < 90).any()  # 直式：頂端分頁列底下沒有標題


def line_head(img: np.ndarray, fmt: VideoFormat, color=(0xE3, 0x12, 0x0B)) -> tuple[float, float]:
    """折線最後一點（線頭圓點）在畫面上的中心：主色（預設紅）像素的最右緣往左一個半徑；y＝最右緣那幾欄的平均。"""
    h, w = img.shape[:2]
    m = np.abs(img - np.array(color)).max(axis=-1) < 40
    m[h - fmt.safe_bottom:] = False
    m[: int(h * 0.15)] = False  # 頂端色條、小旗
    ys, xs = np.nonzero(m)
    right = xs.max()
    return right - 9 * min(w, h) / 1080, float(ys[xs >= right - 2].mean())


def test_near_the_closest_the_labelled_point_still_stays_put_instead_of_sliding_to_the_edge(closest):
    """被標註的點靠右邊、推近會把左邊的 x 軸名字推出範圍時：寧可少推一點，被標註的點也不能被整張圖往右邊推過去
    （示範片 1:03.1、1:26.4 兩位評審量到線頭往右緣跑了約 25 像素：原本是先推再整張平移夾回範圍裡）。"""
    fmt, _, snaps, _ = closest
    for before, after in snaps:
        (x0, y0), (x1, y1) = line_head(before, fmt), line_head(after, fmt)
        assert abs(x1 - x0) <= 2 and abs(y1 - y0) <= 2, ((x0, y0), (x1, y1))


def test_each_snap_near_the_closest_either_visibly_pushes_in_or_does_not_move_at_all(closest):
    """被標註的點靠畫面邊、引擎照版面只推得了一點點時（不到 1.5%：1080p 邊角移十來個像素，像畫面抽一下），那一下就不推，
    畫面一個像素都不動。推得動的那幾下推它自己那一份（每下最多 4%）：前一下少推或沒推，下一下也不補（不會突然推一大段）。"""
    fmt, _, line_snaps, bar_snaps = closest
    for before, after in line_snaps + bar_snaps:
        t0, t1 = title_box(before), title_box(after)
        if t1 == t0:
            top = slice(0, int(before.shape[0] * 0.2))
            assert changed(before[top], after[top]) == 0  # 不推就是完全不動
            continue
        ratio = (t1[2] - t1[0]) / (t0[2] - t0[0])
        assert 1.013 < ratio < 1.045, (fmt.name, ratio)


# ---------------------------------------------------------------- 自動檢查


def findings(report: CheckReport, shot: str, check: str | None = None) -> list:
    return [f for f in report.findings if shot in f.shot_ids and (check is None or f.check == check)]


def test_holding_still_and_snapping_in_pass_every_check(lab):
    for sid in ("SH01", "SH02", "SH03"):
        assert findings(lab.report, sid) == [], sid
    sh03 = next(c for c in lab.report.clips if c.clip_id == "SH03").qa
    assert sh03["still_pairs_pct"] >= 60  # 慢推的判法會把這麼多靜止判成「沒在動」


def test_a_big_number_card_and_a_comparison_pass_every_check_with_the_snap_camera(lab):
    """大數字卡沒有標註：鏡頭不推（數字自己滾出來就是這一拍的動作）。兩數對比的強調跟標註一樣快推一下（往被強調的數字推），
    差距、數字落定不推。停住、快推、數字滾動和淡入都不算問題。"""
    for sid in ("SH05", "SH06", "SH06B"):
        assert findings(lab.report, sid) == [], sid
    assert lab.spec("SH05")["data"]["camera"]["snaps"] == []
    assert [s["index"] for s in lab.spec("SH06")["data"]["camera"]["snaps"]] == [0]
    assert [s["index"] for s in lab.spec("SH06B")["data"]["camera"]["snaps"]] == [0]   # 接續分鏡：只重播上一下，差距不推


def test_a_bar_popping_in_one_frame_is_still_a_jump_with_the_snap_camera(lab):
    jump = findings(lab.report, "SH04", "jump")
    assert [f.status for f in jump] == ["fail"]
    st = next(st for st in lab.build.plan.timeline.shots if st.shot.id == "SH04")
    pop = st.start_frame / FPS + cue_at(lab.spec("SH04"), "bar")[0]
    (t0, t1), = jump[0].times
    assert t0 - 0.1 <= pop <= t1 + 0.1  # 抓到的是柱子跳出來那一刻，不是後面標註的快推


def test_a_moment_the_engine_never_draws_fails_the_motion_check_with_the_snap_camera(lab):
    """快推片段停住是設計，不看整段有幾成幀沒動；改看分鏡設定寫了畫面事件的那一刻：該長出東西、畫面卻幾乎沒變＝動畫沒畫出來，
    必修。正常的快推片段（SH01～SH06B，上面）照樣全過。"""
    motion = findings(lab.report, "SH07", "motion")
    assert [f.status for f in motion] == ["fail"]
    st = next(st for st in lab.build.plan.timeline.shots if st.shot.id == "SH07")
    first, second = (st.start_frame / FPS + at for at in cue_at(lab.spec("SH07"), "bar"))
    (t0, t1), = motion[0].times
    assert t0 - 0.05 <= second < t1 and not t0 <= first < t1  # 指的是沒畫出來的那一下，不是正常長出來的柱子

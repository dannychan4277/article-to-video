"""自動檢查（#11）：每個片段跑 qa（確定性、動態、跳變、框景）、白底片段跑字幕帶檢查，彙整到分鏡；
fail 的沒處理（改掉或明確接受）就擋正式出片。

測試專案在這裡現做（「實驗室」）：四個財經圖表分鏡，一個正常、一個故意不動、一個故意跳、一個推太近壓進字幕帶。
用小畫面（640×360）建置，qa 也就跑得快；門檻是比例，跟畫面大小無關。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from article_to_video.auto_checks import CheckReport, require_checks_cleared, run_checks
from article_to_video.build import BlockedBuild, BuildResult, build_project
from article_to_video.timeline import VideoFormat

SMALL = VideoFormat(name="test360", width=640, height=360, fps=30, safe_bottom=47, sub_size=18,
                    sub_margin_v=15, sub_margin_lr=20, sub_pad=4)

NARRATION = {
    "s01": "兩年期殖利率平均四點五八。",
    "s02": "這張圖先放著不動，讓大家慢慢看清楚兩根柱子的高低差別。",
    "s03": "六根柱子一起跳出來。",
    "s04": "最後這張推得太近。",
}


def _chart(title: str, series: list[dict], **extra) -> dict:
    return {"title": title, "unit": "自製測試數字，%", "source": "來源：自製測試資料", "chart": "bar", "decimals": 2,
            "suffix": "%", "series": series, **extra}


TWO_BARS = [{"label": "兩年期", "value": 4.58}, {"label": "十年期", "value": 3.96}]
SIX_BARS = [{"label": f"第{i + 1}", "value": round(3 + i * 0.4, 2)} for i in range(6)]

SHOTS = [
    {"id": "SH01", "note": "正常：慢推、柱子跟著旁白長出來",
     "start": {"segment": "s01"},
     "chart": {"data": _chart("正常的圖", TWO_BARS, highlight={"index": 0, "text": "較高"}),
               "cues": [{"at": 0, "kind": "title"},
                        {"at": {"segment": "s01", "word": "殖利率"}, "kind": "bar", "data": {"index": 0}},
                        {"at": {"segment": "s01", "word": "四點五八"}, "kind": "highlight"},
                        {"at": {"segment": "s01", "word": "平均"}, "kind": "bar", "data": {"index": 1}}]}},
    {"id": "SH02", "note": "故意不動：不慢推，一開始就全部畫完，之後六秒都停著",
     "start": {"segment": "s02"},
     "chart": {"data": _chart("不動的圖", TWO_BARS, push=0, highlight={"index": 1, "text": "較低"}),
               "cues": [{"at": 0, "kind": "title"}, {"at": 0.3, "kind": "bar"}, {"at": 0.9, "kind": "highlight"}]}},
    {"id": "SH03", "note": "故意跳：六根柱子在一幀裡全部跳出來",
     "start": {"segment": "s03"},
     "chart": {"data": _chart("會跳的圖", SIX_BARS),
               "cues": [{"at": 0, "kind": "title"},
                        {"at": {"segment": "s03", "word": "一起"}, "kind": "bar", "dur": 0.034}]}},
    {"id": "SH04", "note": "推太近：整張圖放大兩成，來源列和 x 軸掉進字幕帶",
     "start": {"segment": "s04"},
     "chart": {"data": _chart("推太近的圖", TWO_BARS, push=0.2),
               "cues": [{"at": 0, "kind": "title"}, {"at": 0.2, "kind": "bar"}]}},
]


@dataclass(frozen=True)
class Lab:
    dir: Path
    build: BuildResult
    report: CheckReport


def write_lab(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "narration.json").write_text(json.dumps({"segments": NARRATION}, ensure_ascii=False), encoding="utf-8")
    (root / "shots.json").write_text(json.dumps({"shots": SHOTS}, ensure_ascii=False, indent=1), encoding="utf-8")
    (root / "settings.json").write_text(json.dumps({"tts": "fake", "gap": 0.4, "tail": 1.2, "music": "none"}),
                                        encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def lab(tmp_path_factory) -> Lab:
    root = write_lab(tmp_path_factory.mktemp("自動檢查") / "實驗室 專案")
    res = build_project(root, fmt=SMALL)
    return Lab(dir=root, build=res, report=run_checks(res.plan))


def findings(report: CheckReport, shot: str, check: str | None = None) -> list:
    return [f for f in report.findings if shot in f.shot_ids and (check is None or f.check == check)]


def test_a_normal_chart_passes_every_check(lab):
    assert findings(lab.report, "SH01") == []


def test_a_chart_that_never_moves_fails_the_motion_check(lab):
    motion = findings(lab.report, "SH02", "motion")
    assert [f.status for f in motion] == ["fail"]


def test_a_chart_whose_bars_pop_in_one_frame_fails_the_jump_check(lab):
    jump = findings(lab.report, "SH03", "jump")
    assert [f.status for f in jump] == ["fail"]
    # 跳的那一刻在成片裡：落在 SH03 的時段、「一起」那個詞附近
    a, b = _shot_span(lab, "SH03")
    t0, t1 = jump[0].times[0]
    assert a <= t0 <= t1 < b


def _shot_span(lab: Lab, shot: str) -> tuple[float, float]:
    st = next(st for st in lab.build.plan.timeline.shots if st.shot.id == shot)
    return st.start_frame / 30, st.end_frame / 30


def test_a_chart_pushed_into_the_subtitle_band_fails_the_band_check(lab):
    band = [f for f in lab.report.findings if f.check == "band"]
    assert [(f.shot_ids, f.status) for f in band] == [(("SH04",), "fail")]
    a, b = _shot_span(lab, "SH04")
    for t0, t1 in band[0].times:
        assert a <= t0 < t1 <= b + 0.1
    # qa 的框景也抓到同一件事：來源列的字掉進字幕帶、被畫面邊緣切掉
    assert {f.check for f in findings(lab.report, "SH04") if f.status == "fail"} >= {"band", "text-in-band", "cut-text"}


def test_more_than_five_seconds_without_anything_new_is_flagged_but_does_not_block(lab):
    holds = [f for f in lab.report.findings if f.check == "hold"]
    assert [(f.shot_ids, f.status) for f in holds] == [(("SH02",), "warn")]
    # 最後一個畫面事件（標註，分鏡開始後 0.9 秒）到下一個分鏡切進來
    a, b = _shot_span(lab, "SH02")
    (t0, t1), = holds[0].times
    assert abs(t0 - (a + 0.9)) <= 1 / 30 and abs(t1 - b) <= 1 / 30


def test_only_clips_that_changed_are_checked_again(lab, tmp_path):
    again = run_checks(lab.build.plan)
    assert again.ran == [] and sorted(again.cached) == sorted(c.id for c in lab.build.plan.clips)
    assert [(f.token, f.status) for f in again.findings] == [(f.token, f.status) for f in lab.report.findings]

    copy = tmp_path / "改過的實驗室"
    shutil.copytree(lab.dir, copy)
    shots = json.loads((copy / "shots.json").read_text(encoding="utf-8"))
    shots["shots"][2]["chart"]["cues"][1].pop("dur")  # SH03 的柱子改回慢慢長出來
    (copy / "shots.json").write_text(json.dumps(shots, ensure_ascii=False), encoding="utf-8")
    res = build_project(copy, fmt=SMALL)
    fixed = run_checks(res.plan)
    assert fixed.ran == ["SH03"]
    assert findings(fixed, "SH03") == []
    assert [f.check for f in findings(fixed, "SH02")] == [f.check for f in findings(lab.report, "SH02")]


def test_the_end_card_has_no_subtitles_so_its_low_text_is_not_a_band_problem(lab):
    assert [f for f in lab.report.findings if f.clip_id == "end.card"] == []


def test_unhandled_failures_block_the_final_build_until_they_are_accepted(lab, tmp_path):
    copy = tmp_path / "要出片的實驗室"
    shutil.copytree(lab.dir, copy)
    fails = [f.token for f in lab.report.findings if f.status == "fail"]
    warns = [f.token for f in lab.report.findings if f.status == "warn"]
    assert fails and warns

    with pytest.raises(BlockedBuild) as blocked:
        build_project(copy, fmt=SMALL, preflight=[require_checks_cleared])
    msg = str(blocked.value)
    assert all(t in msg for t in fails) and not any(t in msg for t in warns)  # 該修只提醒，不擋
    assert "resolve" in msg and "--accept" in msg

    wrong = run_cli("resolve", str(copy), fails[0], "qa:SH09:jump@00000000", "--accept", "打錯的代號")
    assert wrong.returncode == 1 and "SH09" in wrong.stderr and "Traceback" not in wrong.stderr
    assert not (copy / "check_log.json").exists()  # 有一個不對就全部不記

    ok = run_cli("resolve", str(copy), *fails, "--accept", "測試專案：這幾段是故意做壞的", "--by", "作者")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    build_project(copy, fmt=SMALL, preflight=[require_checks_cleared])  # 不再擋
    log = json.loads((copy / "check_log.json").read_text(encoding="utf-8"))
    assert {t: (r["status"], r["by"]) for t, r in log["resolutions"].items()} == {t: ("accepted", "作者") for t in fails}


# ---------------------------------------------------------------- 指令列（1080p 的 session 測試專案）


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", "article_to_video", *args], capture_output=True, text=True,
                          encoding="utf-8", env=env)


@pytest.fixture(scope="module")
def checked(built_project, tmp_path_factory) -> tuple[Path, subprocess.CompletedProcess]:
    """session 建好的測試專案複製一份，跑一次 checks。"""
    root = tmp_path_factory.mktemp("檢查過") / "2026-10-09-殖利率曲線"
    shutil.copytree(built_project.dir, root)
    proc = run_cli("checks", str(root), "--tts", "fake")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return root, proc


def test_checks_command_reports_every_shot_and_writes_the_summary(checked):
    root, proc = checked
    summary = json.loads((root / "build" / "16x9" / "checks.json").read_text(encoding="utf-8"))
    assert [(s["number"], s["id"], s["status"]) for s in summary["shots"]] == [(1, "SH01", "ok"), (2, "SH02", "ok")]
    assert sorted(c["clip"] for c in summary["clips"]) == ["SH01", "SH02", "end.card"]
    assert all(c["qa"]["checked"] for c in summary["clips"])
    assert [c["clip"] for c in summary["clips"] if c["band"]["checked"]] == ["SH01", "SH02"]  # 白底、有字幕的片段
    assert summary["findings"] == [] and summary["blocking"] == []
    assert "分鏡 1" in proc.stdout and "分鏡 2" in proc.stdout


def test_the_review_page_shows_the_checks_on_every_card_and_on_top(checked):
    from test_review_page import read_review

    root, _ = checked
    page = read_review(root)
    assert page.attrs["data-stage"] == "final" and page.videos  # 成片還是最新的：照樣內嵌
    for card in page.cards:
        assert "qa" in card.slots.get("checks", ""), card.slots
        assert "字幕帶" in card.slots["checks"]
    assert "自動檢查" in page.page_slots.get("checks", "")

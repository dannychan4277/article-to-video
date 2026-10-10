"""建置指令遇到設定錯誤：不出片，丟繁中錯誤、點名是哪個分鏡的哪個欄位（參考管線這些情況都是默默算錯或英文 assert）。

每個案例從 mini_project 複製一份、改一處，跑 build_project(tts="fake")。錯誤都在渲染前就擋下來，所以很快。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from article_to_video.build import build_project
from article_to_video.project import ProjectError

from conftest import MINI_PROJECT


def make_project(tmp_path: Path, edit: Callable[[dict, dict], None]) -> Path:
    root = tmp_path / "專案"
    shutil.copytree(MINI_PROJECT, root)
    shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
    narration = json.loads((root / "narration.json").read_text(encoding="utf-8"))
    edit(shots, narration)
    (root / "shots.json").write_text(json.dumps(shots, ensure_ascii=False), encoding="utf-8")
    (root / "narration.json").write_text(json.dumps(narration, ensure_ascii=False), encoding="utf-8")
    return root


def sh(shots: dict, sid: str) -> dict:
    return next(s for s in shots["shots"] if s["id"] == sid)


def word_not_found(shots, _):
    sh(shots, "SH01")["chart"]["cues"][2]["at"]["word"] = "五點"


def ambiguous_word(shots, narration):
    narration["segments"]["s01"] = "兩年期殖利率，兩年期平均四點五八。"
    sh(shots, "SH01")["chart"]["cues"][1]["at"]["word"] = "兩年期"


def anchor_before_its_shot(shots, _):
    sh(shots, "SH02")["chart"]["cues"][0]["at"] = {"segment": "s01", "word": "殖利率"}


def anchor_after_its_shot(shots, _):
    sh(shots, "SH01")["chart"]["cues"][2]["at"] = {"segment": "s02", "word": "倒掛"}


def zero_length_shot(shots, _):
    sh(shots, "SH02")["start"] = {"segment": "s01"}


def missing_segment(shots, _):
    sh(shots, "SH02")["start"] = {"segment": "s09"}


def continuation_changes_series(shots, _):
    sh(shots, "SH02")["chart"]["data"]["series"] = [{"label": "五年期", "value": 4.0}]


def continuation_without_its_own_highlight_cue(shots, _):
    """接續分鏡沒寫自己的 highlight cue（上一個分鏡的標註沿用下來）。"""
    sh(shots, "SH02")["chart"]["cues"] = []


def unknown_cue_kind(shots, _):
    sh(shots, "SH01")["chart"]["cues"][0]["kind"] = "titel"


def number_cue_past_shot_end(shots, _):
    sh(shots, "SH02")["chart"]["cues"].append({"at": 9.0, "kind": "highlight"})


def narration_char_missing_from_subtitle_font(_, narration):
    narration["segments"]["s01"] = "兩年期殖利率𠀋，平均四點五八。"


CASES = {
    "詞找不到": (word_not_found, ["分鏡 SH01", "chart.cues[2].at", "s01", "找不到「五點」"]),
    "詞重複出現": (ambiguous_word, ["分鏡 SH01", "chart.cues[1].at", "「兩年期」", "出現 2 次", "occurrence"]),
    "錨點在分鏡開始前": (anchor_before_its_shot, ["分鏡 SH02", "chart.cues[0].at", "之前"]),
    "錨點在分鏡結束後": (anchor_after_its_shot, ["分鏡 SH01", "chart.cues[2].at", "之後"]),
    "分鏡長度是零": (zero_length_shot, ["分鏡 SH02", "SH01", "切入點"]),
    "沒有這個旁白段": (missing_segment, ["分鏡 SH02", "start", "「s09」"]),
    "接續分鏡改了數列": (continuation_changes_series, ["分鏡 SH02", "接續", "series"]),
    "不認得的 cue": (unknown_cue_kind, ["分鏡 SH01", "chart.cues[0]", "titel"]),
    "cue 時間超出分鏡": (number_cue_past_shot_end, ["分鏡 SH02", "chart.cues[1]", "超出"]),
    "旁白有字幕字型沒有的字": (narration_char_missing_from_subtitle_font, ["旁白段 s01", "𠀋"]),
}


@pytest.mark.parametrize("case", list(CASES))
def test_bad_project_is_blocked_with_a_clear_message(tmp_path, case):
    edit, needles = CASES[case]
    root = make_project(tmp_path, edit)
    with pytest.raises(ProjectError) as err:
        build_project(root, tts="fake")
    msg = str(err.value)
    for needle in needles:
        assert needle in msg, f"錯誤訊息少了「{needle}」：\n{msg}"
    assert not (root / "成片.mp4").exists()
    assert not (root / "build" / "16x9" / "clips").exists()  # 渲染前就擋下


def _sub_on_continuation_only(shots, _):
    sh(shots, "SH02")["chart"]["data"]["highlight"]["sub"] = "多一行說明"


def _no_label_on_chain_head(shots, _):
    cues = sh(shots, "SH01")["chart"]["cues"]
    cues[:] = [c for c in cues if c["kind"] != "highlight"]


def _number_on_continuation_only(shots, _):
    sh(shots, "SH02")["chart"]["cues"].append(
        {"at": {"segment": "s02", "word": "倒掛"}, "kind": "number", "text": "差", "data": {"value": 1}})


@pytest.mark.parametrize("edit", [_sub_on_continuation_only, _no_label_on_chain_head, _number_on_continuation_only,
                                  continuation_without_its_own_highlight_cue])
def test_a_continuation_may_differ_in_labels_or_big_number(tmp_path, edit):
    """整串接續分鏡的版面由建置一次算好（照整串所有的標註、大數字留空間），所以接續分鏡多一行標註、
    整串第一個分鏡沒有標註、只有後面的分鏡有大數字，刻度都不會跳，不用擋。
    接續分鏡沒寫自己的標註 cue 也可以：上一個分鏡的標註會沿用下來（畫面不變，只是切成兩個分鏡）。"""
    from article_to_video.build import plan_build

    plan_build(make_project(tmp_path, edit), tts="fake")


def test_repeated_word_resolves_with_occurrence(tmp_path):
    def edit(shots, narration):
        ambiguous_word(shots, narration)
        sh(shots, "SH01")["chart"]["cues"][1]["at"]["occurrence"] = 2

    from article_to_video.build import plan_build

    plan = plan_build(make_project(tmp_path, edit), tts="fake")
    bar = plan.timeline.shots[0].cues[1]
    # 第二次「兩年期」在第 7 個字：0.1 + 6 個字 × 0.2 + 1 個標點 × 0.25 = 1.55 秒，提早 0.15
    assert abs(bar.frame - (1.55 - 0.15) * 30) <= 1


def test_all_problems_are_listed_at_once(tmp_path):
    def edit(shots, narration):
        word_not_found(shots, narration)
        unknown_cue_kind(shots, narration)

    with pytest.raises(ProjectError) as err:
        build_project(make_project(tmp_path, edit), tts="fake")
    assert len(err.value.problems) == 2


def test_preflight_check_blocks_the_build_before_rendering(tmp_path):
    """渲染前的檢查（數字對帳會接在這裡）丟 ProjectError 就不出片。它拿得到完整的建置計畫。"""
    seen = {}

    def blocker(plan):
        seen["shots"] = [c.id for c in plan.clips]
        raise ProjectError("分鏡 SH01 的標題有一個沒有出處的數字", title="數字對帳沒過")

    root = make_project(tmp_path, lambda shots, narration: None)
    with pytest.raises(ProjectError, match="數字對帳沒過"):
        build_project(root, tts="fake", preflight=[blocker])
    assert seen["shots"] == ["SH01", "SH02", "end.card"]
    assert not (root / "成片.mp4").exists()
    assert not (root / "build" / "16x9" / "clips").exists()


def test_cli_prints_error_in_chinese_without_traceback(tmp_path):
    root = make_project(tmp_path, word_not_found)
    from conftest import run_cli

    proc = run_cli("build", root, "--tts", "fake")
    assert proc.returncode == 1
    assert "建置失敗" in proc.stderr and "找不到「五點」" in proc.stderr
    assert "Traceback" not in proc.stderr

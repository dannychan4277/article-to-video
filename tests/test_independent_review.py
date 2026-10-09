"""獨立審片（#11）：Claude Code 另外派一個只看成片的 agent 審一輪。程式負責三件事：
從成片準備審片素材（review-prep、review-frames）、記下 agent 回報的 JSON（review-record）、顯示在審片頁，
以及必修沒處理時擋正式出片（resolve 記下改好了／接受不改）。

測試用 session 建好的測試專案（1080p 成片）複製一份；agent 的回報是這裡寫的假 JSON。
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

KIT = Path("build") / "16x9" / "independent-review"


def run_cli(*args: str, data_dir: Path | None = None) -> subprocess.CompletedProcess:
    from conftest import run_cli as run

    return run(*args, env={"ARTICLE_TO_VIDEO_DATA": str(data_dir)} if data_dir is not None else None)


@pytest.fixture(scope="module")
def prepped(built_project, tmp_path_factory) -> tuple[Path, subprocess.CompletedProcess]:
    root = tmp_path_factory.mktemp("獨立審片") / "2026-10-09-殖利率曲線"
    shutil.copytree(built_project.dir, root)
    proc = run_cli("review-prep", str(root), "--tts", "fake")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return root, proc


def timeline(root: Path) -> dict:
    return json.loads((root / "build" / "16x9" / "timeline.json").read_text(encoding="utf-8"))


def test_review_material_is_prepared_from_the_finished_video(prepped):
    root, proc = prepped
    kit = root / KIT
    m = json.loads((kit / "material.json").read_text(encoding="utf-8"))
    tl = timeline(root)
    assert abs(m["duration"] - tl["duration"]) < 0.05
    assert [(s["number"], s["start"], s["end"]) for s in m["shots"]] == [
        (i + 1, round(s["startFrame"] / 30, 3), round(s["endFrame"] / 30, 3)) for i, s in enumerate(tl["shots"])]

    # 每秒一格的總覽（4×4，一張 16 秒）＋每個畫面事件剛出現的那一刻
    seconds = math.floor(tl["duration"] - 0.05) + 1
    assert len(m["sheets"]["overview"]) == math.ceil(seconds / 16)
    own_cues = [c for s in tl["shots"] for c in s["cues"] if not c.get("carried")]
    assert len(m["cues"]) == len(own_cues)
    for name in m["sheets"]["overview"] + m["sheets"]["cues"]:
        with Image.open(kit / name) as im:
            assert im.width == 4 * 480
    # 字幕檔原封不動（含時間碼）；不帶專案設定檔、旁白稿、文章
    assert (kit / "subtitles.srt").read_bytes() == (root / "字幕.srt").read_bytes()
    assert not {"shots.json", "narration.json", "article", "settings.json"} & {p.name for p in kit.rglob("*")}
    assert isinstance(m["freeze"], list)

    # 給 agent 的說明與派工的 prompt
    prompt = (kit / "prompt.txt").read_text(encoding="utf-8")
    assert str(kit / "brief.md") in prompt and "review.json" in prompt
    brief = (kit / "brief.md").read_text(encoding="utf-8")
    for word in ("必修", "該修", "可改", "實測", "分鏡", '"severity"'):
        assert word in brief
    assert str(kit / "prompt.txt") in proc.stdout


def test_the_frame_command_given_to_the_reviewer_is_preapproved(prepped):
    """審片 agent 照 prompt 抽幀：那一行指令要被做片、改片兩支 skill 預先核准，作者才不會在「自動」那一步被問權限。"""
    from test_skills import assert_preapproved, printed_commands

    root, proc = prepped
    m = json.loads((root / KIT / "material.json").read_text(encoding="utf-8"))
    prompt = (root / KIT / "prompt.txt").read_text(encoding="utf-8")
    [cmd] = printed_commands(m["frameCommand"])
    assert cmd == m["frameCommand"] and " review-frames " in cmd and cmd in prompt
    assert_preapproved(cmd + " --at 6.0")
    assert_preapproved(cmd + " --from 5.5 --to 6.0 --step 0.25")
    for cmd in printed_commands(proc.stdout):  # review-prep 印的下一步（review-record）
        assert_preapproved(cmd)


def test_the_reviewer_can_pull_full_resolution_frames_from_the_video(prepped):
    root, _ = prepped
    one = run_cli("review-frames", str(root), "--at", "0:06.0")
    assert one.returncode == 0, one.stderr
    (path,) = [Path(p) for p in one.stdout.split() if p.endswith(".png")]
    with Image.open(path) as im:
        assert im.size == (1920, 1080)
    burst = run_cli("review-frames", str(root), "--from", "5.5", "--to", "6.0", "--step", "0.25")
    assert burst.returncode == 0, burst.stderr
    frames = [ln for ln in burst.stdout.splitlines() if ln.endswith(".png") and "拼在一起" not in ln]
    assert len(frames) == 3 and "拼在一起" in burst.stdout
    late = run_cli("review-frames", str(root), "--at", "99")
    assert late.returncode == 1 and "不在成片裡" in late.stderr


# ---------------------------------------------------------------- 記下 agent 的回答（假 JSON）

REVIEW = {
    "score": 7,
    "summary": "節奏順，但第二個分鏡的標註壓到柱子上的數值。",
    "items": [
        {"severity": "必修", "shot": 2, "time": "0:06.0", "end": "0:07.0", "title": "標註壓到柱上的數值",
         "detail": "「長天期較低」的引線穿過 3.96% 的字。", "fix": "標註往上移", "evidence": "實測"},
        {"severity": "該修", "time": 2.0, "title": "第一根柱子比旁白早出現", "evidence": "推論"},
        {"severity": "可改", "shot": None, "time": None, "title": "片尾卡停久了一點", "evidence": "猜測"},
    ],
}


@pytest.fixture
def project(prepped, tmp_path) -> Path:
    """準備好審片素材的專案，每個測試一份（記錄會改 check_log.json）。"""
    dst = tmp_path / "2026-10-09-殖利率曲線"
    shutil.copytree(prepped[0], dst)
    return dst


def record(root: Path, answer: dict) -> subprocess.CompletedProcess:
    (root / KIT / "review.json").write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    return run_cli("review-record", str(root), "--tts", "fake")


def test_recorded_findings_show_on_the_review_page(project):
    from test_review_page import read_review

    proc = record(project, REVIEW)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "R1-1" in proc.stdout and "必修 1、該修 1、可改 1" in proc.stdout

    log = json.loads((project / "check_log.json").read_text(encoding="utf-8"))
    (rnd,) = log["rounds"]
    assert [(it["id"], it["severity"], it["shot"]) for it in rnd["items"]] == [
        ("R1-1", "必修", 2), ("R1-2", "該修", 1), ("R1-3", "可改", None)]  # 只寫時間的對到分鏡 1

    page = read_review(project)
    assert page.attrs["data-stage"] == "review" and page.videos
    top = page.page_slots["review"]
    assert "第 1 輪" in top and "7 分" in top and "片尾卡停久了一點" in top and "必修" in top
    cards = {c.id: c for c in page.cards}
    assert "標註壓到柱上的數值" in cards["SH02"].slots["review"]
    assert "標註往上移" in cards["SH02"].slots["review"]  # 建議怎麼改：作者看得懂
    assert "第一根柱子比旁白早出現" in cards["SH01"].slots["review"]
    assert "標註壓到柱上的數值" not in cards["SH01"].slots.get("review", "")
    # 代號（R1-1）和證據等級（實測／推論）是給 Claude 的：收起來，作者看到的地方沒有
    for where in (cards["SH02"].slots["review"], top):
        assert "R1-" not in where and "實測" not in where and "推論" not in where, where
    assert "R1-1" in cards["SH02"].codes and "R1-3" in page.page_codes


def test_an_answer_piped_in_is_read_as_utf8_whatever_the_macs_language_setting(project):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env.update(PYTHONUTF8="0", LC_ALL="en_US.ISO8859-1", LANG="en_US.ISO8859-1")
    proc = subprocess.run([sys.executable, "-m", "article_to_video", "review-record", str(project), "--file", "-",
                           "--tts", "fake"], input=json.dumps(REVIEW, ensure_ascii=False).encode("utf-8"),
                          capture_output=True, env=env)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    log = json.loads((project / "check_log.json").read_text(encoding="utf-8"))
    assert log["rounds"][0]["items"][0]["title"] == "標註壓到柱上的數值"


def test_a_malformed_answer_is_explained_and_nothing_is_recorded(project):
    bad = {"items": [{"severity": "超級必修", "title": "x"}, {"severity": "該修", "shot": 9, "title": "y"},
                     {"severity": "可改", "time": "下午", "title": "z"}],
           "previous": [{"id": "R9-9", "status": "沒修好"}]}
    proc = record(project, bad)
    assert proc.returncode == 1 and "Traceback" not in proc.stderr
    for where in ("items[0].severity", "items[1].shot", "items[2].time", "previous[0].id"):
        assert where in proc.stderr
    assert not (project / "check_log.json").exists()


def test_an_unhandled_must_fix_blocks_the_final_build_until_fixed_or_accepted(project):
    video = project / "成片.mp4"
    before = video.stat().st_mtime_ns
    assert record(project, REVIEW).returncode == 0

    blocked = run_cli("build", str(project), "--tts", "fake")
    assert blocked.returncode == 1 and "Traceback" not in blocked.stderr
    assert "R1-1" in blocked.stderr and "標註壓到柱上的數值" in blocked.stderr and "resolve" in blocked.stderr
    assert "R1-2" not in blocked.stderr  # 該修只提醒，不擋
    assert video.stat().st_mtime_ns == before

    fixed = run_cli("resolve", str(project), "R1-1", "--fixed", "標註改放柱子上方")
    assert fixed.returncode == 0, fixed.stderr
    assert run_cli("build", str(project), "--tts", "fake").returncode == 0
    from test_review_page import read_review

    page = read_review(project)  # 處理過的收起來（點開才看），還沒處理的照樣攤開
    sh02 = next(c for c in page.cards if c.id == "SH02")
    assert "標註壓到柱上的數值" in sh02.folded and "標註改放柱子上方" in sh02.folded
    assert "第一根柱子比旁白早出現" in page.page_slots["review"] and "第一根柱子比旁白早出現" not in page.page_folded
    assert "標註壓到柱上的數值" in page.page_folded

    # 下一輪審片說沒修好：重新打開，又擋；作者決定不改就記下接受
    assert run_cli("review-prep", str(project), "--tts", "fake").returncode == 0
    material = json.loads((project / KIT / "material.json").read_text(encoding="utf-8"))
    assert material["round"] == 2 and {p["id"]: p["status"] for p in material["previous"]}["R1-1"] == "做片的說改好了"
    again = record(project, {"items": [], "previous": [{"id": "R1-1", "status": "沒修好", "note": "還是壓到"}]})
    assert again.returncode == 0, again.stderr
    assert run_cli("build", str(project), "--tts", "fake").returncode == 1
    assert run_cli("resolve", str(project), "R1-1", "--accept", "作者覺得看得清楚", "--by", "作者").returncode == 0
    assert run_cli("build", str(project), "--tts", "fake").returncode == 0
    log = json.loads((project / "check_log.json").read_text(encoding="utf-8"))
    assert log["resolutions"]["R1-1"]["status"] == "accepted" and log["resolutions"]["R1-1"]["by"] == "作者"


def test_independent_review_can_be_turned_off_in_the_user_settings(project, tmp_path):
    from test_review_page import read_review

    data = tmp_path / "使用者資料"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"channel": "測試頻道"}, ensure_ascii=False), encoding="utf-8")
    shown = json.loads(run_cli("settings", "show", "--json", data_dir=data).stdout)
    assert shown["independent_review"] is True  # 預設開

    assert run_cli("settings", "set", "--independent-review", "關", data_dir=data).returncode == 0
    assert json.loads(run_cli("settings", "show", "--json", data_dir=data).stdout)["independent_review"] is False
    no_project = run_cli("settings", "set", "--project", str(project), "--independent-review", "開", data_dir=data)
    assert no_project.returncode == 1 and "全部影片共用" in no_project.stderr

    assert run_cli("build", str(project), "--tts", "fake", data_dir=data).returncode == 0
    assert "已關掉" in read_review(project).page_slots["review"]


def test_after_a_build_claude_is_told_to_run_the_checks_and_the_review(built_project):
    assert "下一步" in built_project.stdout
    assert "checks" in built_project.stdout and "review-prep" in built_project.stdout


def test_reporting_an_issue_packs_the_review_rounds(project, tmp_path):
    import zipfile

    from article_to_video.issue_report import create_report

    assert record(project, REVIEW).returncode == 0
    rep = create_report(project, out_dir=tmp_path)
    with zipfile.ZipFile(rep.zip_path) as z:
        log = json.loads(z.read("project/check_log.json").decode("utf-8"))
    assert [it["id"] for it in log["rounds"][0]["items"]] == ["R1-1", "R1-2", "R1-3"]

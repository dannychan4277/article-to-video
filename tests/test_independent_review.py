"""獨立審片（#11）：Claude Code 另外派一個只看成片的 agent 審一輪。程式負責三件事：
從成片準備審片素材（review-prep、review-frames）、記下 agent 回報的 JSON（review-record）、顯示在審片頁，
以及必修沒處理時擋正式出片（resolve 記下改好了／接受不改）。

測試用 session 建好的測試專案（1080p 成片）複製一份；agent 的回報是這裡寫的假 JSON。
"""

from __future__ import annotations

import json
import math
import os
import re
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
    assert m["scope"] == "full" and m["round"] == 1 and "整支片" in proc.stdout  # 第一輪審整支片
    assert m["ranges"] == [{"start": 0, "end": m["duration"]}] and m["previous"] == []
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


def test_an_unhandled_must_fix_is_reported_by_build_and_blocks_only_publishing(project, tmp_path):
    """示範：必修還開著時 build 拒跑，Claude 只好先把必修標成改好了才能重建驗證。改成：build（審片用的成片）照跑、
    清楚列出還沒處理的必修；擋的只有 publish（出片），改掉或接受之前不出片。"""
    video = project / "成片.mp4"
    before = video.stat().st_mtime_ns
    assert record(project, REVIEW).returncode == 0

    built = run_cli("build", str(project), "--tts", "fake")  # 改了分鏡要重建來驗證：不擋
    assert built.returncode == 0, built.stdout + built.stderr
    assert "1 項必修還沒處理" in built.stdout and "R1-1" in built.stdout and "標註壓到柱上的數值" in built.stdout
    assert "publish" in built.stdout and "resolve" in built.stdout  # 出片會被擋、處理完怎麼記
    assert "R1-2" not in built.stdout  # 該修只在審片頁提醒

    out = tmp_path / "出片"
    blocked = run_cli("publish", str(project), "--version", "1", "--tts", "fake", "--out", str(out))
    assert blocked.returncode == 1 and "Traceback" not in blocked.stderr
    assert "R1-1" in blocked.stderr and "標註壓到柱上的數值" in blocked.stderr and "resolve" in blocked.stderr
    assert "R1-2" not in blocked.stderr
    assert not out.exists() and video.stat().st_mtime_ns == before  # 渲染前就擋下

    fixed = run_cli("resolve", str(project), "R1-1", "--fixed", "標註改放柱子上方")
    assert fixed.returncode == 0, fixed.stderr
    rebuilt = run_cli("build", str(project), "--tts", "fake")
    assert rebuilt.returncode == 0 and "R1-1" not in rebuilt.stdout and "必修還沒處理" not in rebuilt.stdout
    from test_review_page import read_review

    page = read_review(project)  # 處理過的收起來（點開才看），還沒處理的照樣攤開
    sh02 = next(c for c in page.cards if c.id == "SH02")
    assert "標註壓到柱上的數值" in sh02.folded and "標註改放柱子上方" in sh02.folded
    assert "第一根柱子比旁白早出現" in page.page_slots["review"] and "第一根柱子比旁白早出現" not in page.page_folded
    assert "標註壓到柱上的數值" in page.page_folded

    # 下一輪審片說沒修好：重新打開，出片又擋；作者決定不改就記下接受，才出得了片
    assert run_cli("review-prep", str(project), "--tts", "fake").returncode == 0
    material = json.loads((project / KIT / "material.json").read_text(encoding="utf-8"))
    assert material["round"] == 2 and {p["id"]: p["status"] for p in material["previous"]}["R1-1"] == "做片的說改好了"
    again = record(project, {"items": [], "previous": [{"id": "R1-1", "status": "沒修好", "note": "還是壓到"}]})
    assert again.returncode == 0, again.stderr
    reopened = run_cli("publish", str(project), "--version", "1", "--tts", "fake", "--out", str(out))
    assert reopened.returncode == 1 and "R1-1" in reopened.stderr
    assert run_cli("resolve", str(project), "R1-1", "--accept", "作者覺得看得清楚", "--by", "作者").returncode == 0
    published = run_cli("publish", str(project), "--version", "1", "--tts", "fake", "--out", str(out))
    assert published.returncode == 0, published.stdout + published.stderr
    assert any(out.glob("*.mp4"))
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


# ---------------------------------------------------------------- 審片變輕：整片幾輪、之後只看改過的分鏡
#
# 示範片三輪整片審了 28 分鐘。規則：第一輪審整支片；那一輪有必修，改好之後整支片再審一輪核對（整片最多 2 輪）；
# 其他時候再審只看改過的分鏡（跟上一輪審的成片比，分鏡自己的畫面、長度或旁白變了才算改過）。


def material(root: Path) -> dict:
    return json.loads((root / KIT / "material.json").read_text(encoding="utf-8"))


def edit_highlight(root: Path, text: str) -> None:
    """改分鏡 2 的標註文字（只動那個分鏡的畫面）。"""
    shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
    shots["shots"][1]["chart"]["data"]["highlight"]["text"] = text
    (root / "shots.json").write_text(json.dumps(shots, ensure_ascii=False, indent=1), encoding="utf-8")


@pytest.fixture(scope="module")
def edited(prepped, tmp_path_factory) -> Path:
    """審片素材是改之前的成片做的；之後 Claude 改了分鏡 2 的標註、重建（只重渲那個片段）。
    在這份上記下的審片輪次＝審的是改之前的成片，所以「改過的分鏡」只有分鏡 2。"""
    root = tmp_path_factory.mktemp("改過分鏡") / "2026-10-09-殖利率曲線"
    shutil.copytree(prepped[0], root)
    edit_highlight(root, "長天期反而低")
    built = run_cli("build", str(root), "--tts", "fake")
    assert built.returncode == 0, built.stdout + built.stderr
    return root


@pytest.fixture
def edited_project(edited, tmp_path) -> Path:
    dst = tmp_path / "2026-10-09-殖利率曲線"
    shutil.copytree(edited, dst)
    return dst


def test_after_a_round_without_must_fixes_only_the_changed_shots_are_reviewed_again(edited_project):
    root = edited_project
    first = {"items": [
        {"severity": "該修", "shot": 2, "time": 6.0, "title": "標註字太小", "evidence": "實測"},
        {"severity": "該修", "shot": 1, "time": 1.0, "title": "第一根柱子早出現", "evidence": "推論"},
    ]}
    assert record(root, first).returncode == 0

    # 上一輪沒有必修：build 之後不再叫 Claude 派審片員（改過的分鏡自己看靜態圖確認）
    rebuilt = run_cli("build", str(root), "--tts", "fake", "--no-open")
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    assert "review-prep" not in rebuilt.stdout and "不用再審" in rebuilt.stdout and "靜態圖" in rebuilt.stdout

    # Claude 判斷改得多、還是要審：只看改過的分鏡
    prep = run_cli("review-prep", str(root), "--tts", "fake")
    assert prep.returncode == 0, prep.stdout + prep.stderr
    assert "只看改過的分鏡" in prep.stdout and "分鏡 2" in prep.stdout
    m = material(root)
    tl = timeline(root)
    shot2 = tl["shots"][1]
    start, end = shot2["startFrame"] / 30, shot2["endFrame"] / 30
    assert m["scope"] == "changed" and m["round"] == 2
    assert [s["number"] for s in m["shots"]] == [2]
    assert m["cues"] and {c["shot"] for c in m["cues"]} == {2}
    # 總覽只做改過的分鏡那一段（前後各多 1 秒看換鏡）
    assert m["ranges"] and all(start - 1.01 <= r["start"] and r["end"] <= end + 1.01 for r in m["ranges"])
    assert m["ranges"][0]["start"] > 0.5  # 分鏡 1 的開頭沒有重看
    # 上一輪的問題只帶這個分鏡的
    assert [p["id"] for p in m["previous"]] == ["R1-1"]
    prompt = (root / KIT / "prompt.txt").read_text(encoding="utf-8")
    assert "只看改過的分鏡" in prompt and "分鏡 2" in prompt

    second = {"items": [{"severity": "可改", "time": end - 0.5, "title": "標註停太久", "evidence": "實測"}],
              "previous": [{"id": "R1-1", "status": "已修好", "note": "字放大了"}]}
    assert record(root, second).returncode == 0
    log = json.loads((root / "check_log.json").read_text(encoding="utf-8"))
    rnd = log["rounds"][1]
    assert rnd["scope"] == "changed" and [it["id"] for it in rnd["items"]] == ["R2-1"]
    assert rnd["items"][0]["shot"] == 2
    assert log["resolutions"]["R1-1"]["status"] == "fixed" and log["resolutions"]["R1-1"]["by"] == "獨立審片第 2 輪"

    # 這版成片審過了、之後沒再改：沒有改過的分鏡可以審
    again = run_cli("review-prep", str(root), "--tts", "fake")
    assert again.returncode == 1 and "Traceback" not in again.stderr
    assert "沒有改過的分鏡" in again.stderr and "--full" in again.stderr


def test_a_must_fix_brings_back_the_whole_film_for_at_most_two_rounds(edited_project, tmp_path):
    root = edited_project
    must = {"severity": "必修", "shot": 2, "time": 6.0, "title": "標註指錯柱", "evidence": "實測"}
    assert record(root, {"items": [must]}).returncode == 0

    # 整片審的那一輪有必修：改好之後整支片再審一輪核對（上一輪之後改過的分鏡點出來）
    prep = run_cli("review-prep", str(root), "--tts", "fake")
    assert prep.returncode == 0, prep.stdout + prep.stderr
    m = material(root)
    assert m["scope"] == "full" and [s["number"] for s in m["shots"]] == [1, 2] and "必修" in prep.stdout
    assert {c["shot"] for c in m["cues"]} == {1, 2} and m["changedShots"] == [2]
    assert [p["id"] for p in m["previous"]] == ["R1-1"]
    assert "分鏡 2" in (root / KIT / "prompt.txt").read_text(encoding="utf-8")
    second = {"items": [{**must, "title": "標註壓到線"}], "previous": [{"id": "R1-1", "status": "已修好"}]}
    assert record(root, second).returncode == 0

    # 整片已經審了兩輪：還有必修，改好（又改了分鏡 2）之後這輪只看改過的分鏡；已經核過的 R1-1 不再拿來核
    edit_highlight(root, "長天期比較低")
    assert run_cli("build", str(root), "--tts", "fake").returncode == 0
    prep = run_cli("review-prep", str(root), "--tts", "fake")
    assert prep.returncode == 0, prep.stdout + prep.stderr
    m = material(root)
    assert m["scope"] == "changed" and m["round"] == 3 and [s["number"] for s in m["shots"]] == [2]
    assert [p["id"] for p in m["previous"]] == ["R2-1"]
    third = {"items": [{**must, "title": "新標註被字幕蓋住"}], "previous": [{"id": "R2-1", "status": "已修好"}]}
    assert record(root, third).returncode == 0
    log = json.loads((root / "check_log.json").read_text(encoding="utf-8"))
    assert [(r["round"], r["scope"]) for r in log["rounds"]] == [(1, "full"), (2, "full"), (3, "changed")]

    # 只看改過分鏡的那一輪找到的必修照樣擋出片（渲染前就擋）
    shown = run_cli("open-review", str(root), "--tts", "fake")
    assert shown.returncode == 0
    version = re.search(r"第 (\d+) 版", shown.stdout).group(1)
    blocked = run_cli("publish", str(root), "--version", version, "--tts", "fake", "--out", str(tmp_path / "出片"))
    assert blocked.returncode == 1 and "R3-1" in blocked.stderr and "新標註被字幕蓋住" in blocked.stderr
    assert "R1-1" not in blocked.stderr and "R2-1" not in blocked.stderr
    assert not (tmp_path / "出片").exists()

    # 硬要整支片也可以
    full = run_cli("review-prep", str(root), "--tts", "fake", "--full")
    assert full.returncode == 0 and material(root)["scope"] == "full" and material(root)["round"] == 4


def test_rounds_recorded_before_this_change_fall_back_to_the_whole_film(project):
    """舊專案的審片紀錄沒有記分鏡內容：比不出改了哪些，就審整支片；作者接受不改的列在 known（不用核、不要再報）。"""
    legacy = {"version": 1, "rounds": [{
        "round": 1, "aspect": "16x9", "videoKey": "舊的成片", "score": 7, "summary": "", "previous": [],
        "items": [{"id": "R1-1", "severity": "該修", "shot": 2, "shotId": "SH02", "time": 6.0, "title": "標註太小"},
                  {"id": "R1-2", "severity": "該修", "shot": 1, "shotId": "SH01", "time": 1.0, "title": "柱子早出現"}]}],
        "resolutions": {"R1-2": {"status": "accepted", "note": "作者說可以", "by": "作者", "at": "2026-10-09T12:00:00+0800"}}}
    (project / "check_log.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    changed = run_cli("review-prep", str(project), "--tts", "fake", "--changed")
    assert changed.returncode == 1 and "比不出改了哪些分鏡" in changed.stderr and "Traceback" not in changed.stderr
    prep = run_cli("review-prep", str(project), "--tts", "fake")
    assert prep.returncode == 0, prep.stdout + prep.stderr
    m = material(project)
    assert m["scope"] == "full" and m["round"] == 2 and "比不出" in prep.stdout
    assert [p["id"] for p in m["previous"]] == ["R1-1"] and [k["id"] for k in m["known"]] == ["R1-2"]


def test_reporting_an_issue_packs_the_review_rounds(project, tmp_path):
    import zipfile

    from article_to_video.issue_report import create_report

    assert record(project, REVIEW).returncode == 0
    rep = create_report(project, out_dir=tmp_path)
    with zipfile.ZipFile(rep.zip_path) as z:
        log = json.loads(z.read("project/check_log.json").decode("utf-8"))
    assert [it["id"] for it in log["rounds"][0]["items"]] == ["R1-1", "R1-2", "R1-3"]

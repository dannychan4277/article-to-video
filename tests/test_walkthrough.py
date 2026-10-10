"""照主 skill 的順序把指令列走一遍（#13）：new → lint → narrate（配音，寫分鏡之前）→ 寫分鏡 → preview → 給作者看 →
審片留言 → 改 → 只重渲改到的 → 作者點頭 → publish（建置＋自動檢查＋放進「影片／解說影片／日期-標題」）。

Claude 寫 JSON 的那幾步由測試代寫（照 skills/make-video/references/formats.md 的格式）。配音用假配音層。
文章是自己寫的；數字是 FRED DGS2／DGS10 2023 年日資料的年平均（4.58／3.96，相差 0.62，跟其他測試同一組）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest

from conftest import run_cli

ARTICLE = """殖利率曲線倒掛：短天期為什麼比較高

2023 年，美國兩年期公債殖利率平均是 4.58%，十年期平均只有 3.96%，兩者相差 0.62 個百分點。

短天期利率反而比長天期高，殖利率曲線倒掛。這通常代表市場預期之後會降息。
"""
NARRATION = {
    "s01": "兩年期殖利率，平均四點五八。十年期只有三點九六，曲線倒掛。",
    "s02": "短天期反而比長天期高，相差零點六二個百分點。這通常代表市場預期降息。",
}
SOURCE = "來源：FRED（DGS2、DGS10），年平均自算"
SHOTS = {"shots": [
    {"id": "SH01", "start": {"segment": "s01"},
     "chart": {"data": {"title": "短天期殖利率反而比較高", "unit": "美國公債殖利率，2023 年平均，%", "source": SOURCE,
                        "chart": "bar", "decimals": 2, "suffix": "%",
                        "series": [{"label": "兩年期", "value": 4.58}, {"label": "十年期", "value": 3.96}],
                        "highlight": {"index": 0, "text": "短天期較高"}},
               "cues": [{"at": 0, "kind": "title"},
                        {"at": {"segment": "s01", "word": "殖利率"}, "kind": "bar", "data": {"index": 0}},
                        {"at": {"segment": "s01", "word": "四點五八", "edge": "end"}, "kind": "highlight"},
                        {"at": {"segment": "s01", "word": "十年期"}, "kind": "bar", "data": {"index": 1}}]}},
    {"id": "SH02", "start": {"segment": "s02"},
     "chart": {"data": {"title": "短天期比長天期高", "unit": "兩年期減十年期，2023 年平均，個百分點", "source": SOURCE,
                        "chart": "bar", "decimals": 2, "series": [{"label": "利差", "value": 0.62}],
                        "highlight": {"index": 0, "text": "曲線倒掛"}},
               "cues": [{"at": 0, "kind": "title"},
                        {"at": {"segment": "s02", "word": "相差"}, "kind": "bar"},
                        {"at": {"segment": "s02", "word": "百分點", "edge": "end"}, "kind": "highlight"}]}},
]}
PASTED = """審片留言｜{folder}｜第 1 版
分鏡 2（SH02）：標註改成「市場預期降息」
  旁白最後一句改成「市場預期之後會降息」
整體意見：其他都可以
"""
NEW_S02 = "短天期反而比長天期高，相差零點六二個百分點。市場預期之後會降息。"
TITLE = "殖利率倒掛"


@dataclass
class Step:
    code: int
    out: str
    err: str


@dataclass
class Walk:
    root: Path
    folder: Path
    project: Path
    steps: dict[str, Step] = field(default_factory=dict)
    mtimes: dict[str, dict[str, float]] = field(default_factory=dict)
    opened: list[str] = field(default_factory=list)
    pages: dict[str, str] = field(default_factory=dict)
    """那一步做完時的審片頁（HTML），或版本紀錄（review.json，名字結尾 -state）。"""


def _files(d: Path) -> dict[str, float]:
    return {str(p.relative_to(d)): p.stat().st_mtime_ns for p in d.rglob("*") if p.is_file()}


@pytest.fixture(scope="module")
def walk(tmp_path_factory, videos_dir) -> Walk:
    tmp = tmp_path_factory.mktemp("走一遍")
    data = tmp / "使用者資料"  # 這位作者關掉了獨立審片（測試不派 agent）；頻道名照填
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"channel": "測試頻道", "independent_review": False},
                                                   ensure_ascii=False), encoding="utf-8")
    fakebin = tmp / "bin"  # 假的 macOS open：記下要打開什麼
    fakebin.mkdir()
    opened_log = tmp / "opened.txt"
    (fakebin / "open").write_text(f"#!/bin/sh\necho \"$@\" >> '{opened_log}'\n", encoding="utf-8")
    (fakebin / "open").chmod(0o755)
    opening = {"env": {"PATH": f"{fakebin}{os.pathsep}{os.environ['PATH']}"}, "unset": ("ARTICLE_TO_VIDEO_NO_OPEN",)}

    def run(name: str, *args: str, stdin: str | None = None, env: dict | None = None) -> Step:
        proc = run_cli(*args, stdin=stdin, **(env or {}))
        w.steps[name] = Step(proc.returncode, proc.stdout, proc.stderr)
        return w.steps[name]

    article = tmp / "下載" / "殖利率倒掛.md"
    article.parent.mkdir()
    article.write_text(ARTICLE, encoding="utf-8")
    folder = videos_dir / f"{date.today().isoformat()}-{TITLE}"
    w = Walk(root=videos_dir, folder=folder, project=folder / "專案")

    # 1. 收文章（截字檢查在擷取裡）
    assert run("new", "new", str(article), "--title", TITLE).code == 0, w.steps["new"].err
    p = w.project
    # 2. Claude 寫旁白 → 旁白規則檢查
    (p / "narration.json").write_text(json.dumps({"segments": NARRATION}, ensure_ascii=False), encoding="utf-8")
    run("lint", "lint", str(p))
    # 3. 配音（測試專案：假配音、片尾短一點）：還沒有分鏡，拿到總長與每個詞的秒數
    (p / "settings.json").write_text(json.dumps({"tts": "fake", "tail": 1.2}), encoding="utf-8")
    run("narrate", "narrate", str(p), "--data-dir", str(data))
    # 4. Claude 照秒數寫分鏡 → 預覽：分鏡靜態圖＋數字對帳（配音用第 3 步的），先不給作者看
    (p / "shots.json").write_text(json.dumps(SHOTS, ensure_ascii=False), encoding="utf-8")
    run("preview", "preview", str(p), "--no-open", "--data-dir", str(data))
    w.pages["preview"] = (p / "審片頁.html").read_text(encoding="utf-8")
    # 4. 檢查都做完了，打開審片頁給作者看
    run("open-review", "open-review", str(p), "--data-dir", str(data), env=opening)
    w.mtimes["before"] = {**{f"stills/{k}": v for k, v in _files(p / "build" / "16x9" / "stills").items()},
                          **{f"narration/{k}": v for k, v in _files(p / "build" / "narration").items()}}
    # 5. 作者貼回審片留言 → 對到分鏡與旁白段 → Claude 改 JSON → 旁白規則檢查 → 預覽（只重渲改到的）
    run("feedback", "feedback", str(p), stdin=PASTED.format(folder=folder.name))
    shots = json.loads((p / "shots.json").read_text(encoding="utf-8"))
    shots["shots"][1]["chart"]["data"]["highlight"]["text"] = "市場預期降息"
    (p / "shots.json").write_text(json.dumps(shots, ensure_ascii=False), encoding="utf-8")
    (p / "narration.json").write_text(json.dumps({"segments": {**NARRATION, "s02": NEW_S02}}, ensure_ascii=False),
                                      encoding="utf-8")
    run("lint2", "lint", str(p))
    run("preview2", "preview", str(p), "--no-open", "--data-dir", str(data))
    w.pages["preview2-state"] = (p / "build" / "16x9" / "review.json").read_text(encoding="utf-8")
    w.mtimes["after"] = {**{f"stills/{k}": v for k, v in _files(p / "build" / "16x9" / "stills").items()},
                         **{f"narration/{k}": v for k, v in _files(p / "build" / "narration").items()}}
    # 6. 作者點頭之前不能出片：版本對不上、這支片還沒做過獨立審片（預設開著的使用者設定）
    run("publish-wrong-version", "publish", str(p), "--version", "1", "--data-dir", str(data))
    run("publish-no-review", "publish", str(p), "--version", "2")
    # 7. 作者看了第 2 版說可以 → 出片
    run("publish", "publish", str(p), "--version", "2", "--data-dir", str(data), env=opening)
    w.pages["publish"] = (p / "審片頁.html").read_text(encoding="utf-8")
    run("projects", "projects", "--json")
    # 8. 出片之後又動了分鏡（沒給作者看）→ 不能直接再出片
    shots["shots"][0]["chart"]["data"]["highlight"]["text"] = "短天期高"
    (p / "shots.json").write_text(json.dumps(shots, ensure_ascii=False), encoding="utf-8")
    run("publish-unseen", "publish", str(p), "--version", "2", "--data-dir", str(data))
    # 9. 改過的給作者看：審片頁回到預覽，頁首不再說已出片（影片資料夾裡的是上一版）
    run("preview3", "preview", str(p), "--no-open", "--data-dir", str(data))
    w.pages["preview3"] = (p / "審片頁.html").read_text(encoding="utf-8")
    w.opened = opened_log.read_text(encoding="utf-8").splitlines() if opened_log.is_file() else []
    return w


def test_new_puts_the_project_under_the_dated_video_folder(walk):
    assert (walk.project / "article" / "article.json").is_file()
    assert f"專案資料夾：{walk.project}" in walk.steps["new"].out


def test_narration_passes_the_rules_before_voicing(walk):
    for name in ("lint", "lint2"):
        assert walk.steps[name].code == 0, walk.steps[name].out
        assert "要改 0 項" in walk.steps[name].out


def test_narration_is_voiced_before_the_storyboard_is_written(walk):
    st = walk.steps["narrate"]
    assert st.code == 0, st.err
    assert "總長" in st.out and "s01（0.00 秒起" in st.out and "s02（" in st.out
    assert "2 段新配" in st.out
    assert "0 段新配、2 段用快取" in walk.steps["preview"].out  # 預覽用的就是那份配音


def test_first_preview_is_checked_before_the_author_sees_it(walk):
    pv = walk.steps["preview"]
    assert pv.code == 0, pv.err
    assert "第 1 版" in pv.out
    assert "數字對帳：畫面上" in pv.out and "無出處" not in pv.out
    for sid in ("SH01", "SH02"):  # 每個分鏡的靜態圖路徑都印出來，Claude 逐張看
        assert f"（{sid}）：" in pv.out
    review = str(walk.project / "審片頁.html")
    assert walk.opened.count(review) == 1, walk.opened  # 只有 open-review 打開給作者看；preview 加了 --no-open


def test_pasted_comments_point_at_the_shot_and_its_narration_segment(walk):
    fb = walk.steps["feedback"]
    assert fb.code == 0, fb.err
    out = json.loads(fb.out)
    assert out["version"] == 1 and out["problems"] == []
    [c] = out["shots"]
    assert (c["number"], c["id"], c["segments"]) == (2, "SH02", ["s02"])
    assert c["narration"] == {"s02": NARRATION["s02"]}
    assert out["overall"] == "其他都可以"


def test_second_preview_redraws_only_the_changed_shot_and_revoices_only_the_changed_segment(walk):
    pv = walk.steps["preview2"]
    assert pv.code == 0, pv.err
    assert "第 2 版" in pv.out and "（SH02）：" in pv.out and "（SH01）：" not in pv.out
    state = json.loads(walk.pages["preview2-state"])
    assert state["version"] == 2 and state["changed"] == ["SH02"]
    before, after = walk.mtimes["before"], walk.mtimes["after"]
    sh01 = [k for k in before if k.startswith("stills/SH01/")]
    assert sh01 and all(after.get(k) == before[k] for k in sh01)  # SH01 的靜態圖沒重渲
    assert any(k.startswith("stills/SH02/") and after[k] != before.get(k) for k in after)
    old_wavs = {k for k in before if k.endswith(".wav")}
    new_wavs = {k for k in after if k.endswith(".wav")}
    assert len(old_wavs) == len(new_wavs) == 2
    kept = old_wavs & new_wavs
    assert len(kept) == 1 and after[next(iter(kept))] == before[next(iter(kept))]  # s01 沒重配；s02 新配一段


def test_publishing_waits_for_the_version_the_author_approved(walk):
    st = walk.steps["publish-wrong-version"]
    assert st.code == 1 and "第 2 版" in st.err and "Traceback" not in st.err
    st = walk.steps["publish-unseen"]
    assert st.code == 1 and "preview" in st.err and "Traceback" not in st.err


def test_publishing_needs_one_round_of_independent_review_when_it_is_turned_on(walk):
    st = walk.steps["publish-no-review"]
    assert st.code == 1 and "獨立審片" in st.err and "Traceback" not in st.err


def test_published_video_and_subtitles_land_in_the_dated_folder(walk):
    st = walk.steps["publish"]
    assert st.code == 0, st.out + st.err
    video, srt = walk.folder / f"{TITLE}.mp4", walk.folder / f"字幕-{TITLE}.srt"
    assert video.read_bytes() == (walk.project / "成片.mp4").read_bytes()
    assert srt.read_bytes() == (walk.project / "字幕.srt").read_bytes()
    assert "市場預期之後會降息" in srt.read_text(encoding="utf-8")
    assert sorted(p.name for p in walk.folder.iterdir()) == sorted([video.name, srt.name, "專案"])
    assert str(video) in st.out
    assert f"-R {video}" in walk.opened  # 在 Finder 裡把成片指給作者看
    page = (walk.project / "審片頁.html").read_text(encoding="utf-8")
    assert "已出片" in page and video.name in page


def test_published_video_ran_the_automatic_checks(walk):
    checks = json.loads((walk.project / "build" / "16x9" / "checks.json").read_text(encoding="utf-8"))
    assert checks["unchecked"] == [] and checks["blocking"] == []
    assert {c["clip"] for c in checks["clips"]} >= {"SH01", "SH02"}


def test_old_projects_list_shows_it_was_published(walk):
    listed = json.loads(walk.steps["projects"].out)
    me = next(p for p in listed if p["project"] == str(walk.project))
    assert me["stage"] == "已出片" and me["outputs"]["16x9"]["reviewVersion"] == 2
    assert listed[0]["project"] == str(walk.project)


def test_the_review_page_is_titled_with_the_video_not_the_project_folder(walk):
    """專案資料夾一律叫「專案」，片名在上一層「日期-片名」：審片頁的標題、頁首、複製給 Claude 的標頭都寫片名。"""
    from test_review_page import parse_review

    html = walk.pages["preview"]
    page = parse_review(html)
    assert page.h1.strip() == TITLE
    assert TITLE in page.title and "專案" not in page.title
    data = json.loads(html.split('<script id="review-data" type="application/json">')[1].split("</script>")[0])
    assert data["project"] == TITLE  # review.js 拿它寫「審片留言｜片名｜第 N 版」


def test_the_review_page_says_published_only_after_publishing_and_where_it_went(walk):
    """頁首照實說：預覽、審片用的影片（還沒出片）、已出片（放在哪）。出片之後又改過，就不再說已出片。"""
    from test_review_page import parse_review

    before = parse_review(walk.pages["preview"])
    assert before.attrs["data-stage"] == "preview" and "已出片" not in before.stage_text
    after = parse_review(walk.pages["publish"])
    assert after.attrs["data-stage"] == "published"
    assert "已出片" in after.stage_text and walk.folder.name in after.stage_text, after.stage_text
    changed = parse_review(walk.pages["preview3"])
    assert walk.steps["preview3"].code == 0, walk.steps["preview3"].err
    assert changed.attrs["data-stage"] == "preview" and "已出片" not in changed.stage_text
    assert "出片之後又改過" in walk.pages["preview3"]

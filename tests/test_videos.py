"""影片資料夾（#13）：新專案放「影片／解說影片／日期-標題」、舊專案找得到、出片的檔名。

  <影片資料夾根目錄>/                 預設 ~/Movies/解說影片；ARTICLE_TO_VIDEO_VIDEOS 或 --root 可改（測試一律放 tmp）
    2026-10-09-殖利率倒掛/           一支片一個資料夾：作者在 Finder 看到的
      殖利率倒掛.mp4、字幕-殖利率倒掛.srt   作者點頭、出片之後才出現
      專案/                          做片用的檔案（文章、旁白稿、分鏡設定、建置產物、審片頁）
"""

from __future__ import annotations

import io
import json
import os
from datetime import date
from pathlib import Path

import pytest

from article_to_video import cli

ARTICLE = """殖利率曲線倒掛了：短天期為什麼比較高

2023 年，美國兩年期公債殖利率平均是 4.58%，十年期平均只有 3.96%。

短天期利率反而比長天期高，殖利率曲線倒掛。
"""
TODAY = date.today().isoformat()


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    r = tmp_path / "影片" / "解說影片"
    monkeypatch.setenv("ARTICLE_TO_VIDEO_VIDEOS", str(r))
    return r


def new(capsys, *args: str, stdin: str | None = None, monkeypatch=None) -> tuple[int, str, str]:
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")), encoding="utf-8"))
    code = cli.main(["new", *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def article_file(tmp_path: Path, name: str = "我的文章.md") -> Path:
    f = tmp_path / "下載" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(ARTICLE, encoding="utf-8")
    return f


def test_new_project_lives_in_a_dated_folder_under_the_videos_folder(root, tmp_path, capsys):
    code, out, err = new(capsys, str(article_file(tmp_path)), "--title", "殖利率倒掛")
    assert code == 0, err
    folder = root / f"{TODAY}-殖利率倒掛"
    project = folder / "專案"
    assert (project / "article" / "text.txt").read_text(encoding="utf-8").startswith("殖利率曲線倒掛了")
    assert (project / "article" / "article.json").is_file()
    assert sorted(p.name for p in folder.iterdir()) == ["專案"]  # 出片前，作者看到的資料夾裡只有「專案」
    assert f"專案資料夾：{project}" in out
    assert "全文：" in out  # 擷取的摘要照印（Claude 接著讀全文寫旁白）


def test_title_defaults_to_the_article_heading_and_is_made_safe_for_finder(root, tmp_path, capsys):
    code, out, err = new(capsys, str(article_file(tmp_path)))
    assert code == 0, err
    assert (root / f"{TODAY}-殖利率曲線倒掛了" / "專案").is_dir()
    code, out, err = new(capsys, str(article_file(tmp_path)), "--title", "利率/通膨: 誰先動？")
    assert code == 0, err
    names = sorted(p.name for p in root.iterdir())
    assert f"{TODAY}-利率-通膨- 誰先動？" in names, names


def test_same_title_on_the_same_day_gets_its_own_folder(root, tmp_path, capsys):
    for _ in range(2):
        code, _, err = new(capsys, str(article_file(tmp_path)), "--title", "殖利率倒掛")
        assert code == 0, err
    assert sorted(p.name for p in root.iterdir()) == [f"{TODAY}-殖利率倒掛", f"{TODAY}-殖利率倒掛-2"]


def test_pasted_text_makes_a_project_too(root, capsys, monkeypatch):
    code, out, err = new(capsys, "-", "--title", "貼上的文章", stdin=ARTICLE, monkeypatch=monkeypatch)
    assert code == 0, err
    text = (root / f"{TODAY}-貼上的文章" / "專案" / "article" / "text.txt").read_text(encoding="utf-8")
    assert "4.58%" in text


def test_an_article_that_cannot_be_read_leaves_no_folder_behind(root, tmp_path, capsys):
    bad = tmp_path / "舊文章.doc"
    bad.write_bytes(b"\xd0\xcf\x11\xe0 not really a doc")
    code, out, err = new(capsys, str(bad), "--title", "讀不了")
    assert code == 1
    assert "擷取失敗" in err and ".docx" in err  # 轉述給作者的說明照原樣（#6）
    assert not root.exists() or list(root.iterdir()) == []
    assert "Traceback" not in err


def touch(path: Path, when: float) -> None:
    os.utime(path, (when, when))


def test_projects_lists_videos_newest_first_with_where_each_one_stands(root, tmp_path, capsys):
    """「把上次那支第 5 張圖改成…」：最近動過的排第一；每支列出分鏡數、審片頁第幾版。"""
    for title in ("殖利率倒掛", "通膨預期", "期限溢價"):
        assert new(capsys, str(article_file(tmp_path)), "--title", title)[0] == 0
    old = root / f"{TODAY}-殖利率倒掛" / "專案"
    (old / "narration.json").write_text(json.dumps({"segments": {"s01": "一段。"}}, ensure_ascii=False), encoding="utf-8")
    shot = {"chart": {"data": {"title": "標題", "series": [{"label": "甲", "value": 1}]}, "cues": []}}
    (old / "shots.json").write_text(json.dumps({"shots": [{"id": f"SH0{i}", **shot} for i in range(1, 6)]}),
                                    encoding="utf-8")
    review = old / "build" / "16x9" / "review.json"
    review.parent.mkdir(parents=True)
    review.write_text(json.dumps({"version": 3, "shots": []}), encoding="utf-8")
    base = 1_700_000_000
    for i, title in enumerate(("通膨預期", "期限溢價")):
        for p in (root / f"{TODAY}-{title}" / "專案").rglob("*"):
            touch(p, base + i)
    for p in old.rglob("*"):
        touch(p, base + 100)  # 最近改的

    assert cli.main(["projects"]) == 0
    out = capsys.readouterr().out
    first = out.index("殖利率倒掛")
    assert first < out.index("期限溢價") < out.index("通膨預期")
    block = out[first:out.index("期限溢價")]
    assert "5 個分鏡" in block and "審片頁第 3 版" in block and str(old) in block
    assert "還沒寫旁白" in out[out.index("期限溢價"):]

    assert cli.main(["projects", "通膨"]) == 0
    out = capsys.readouterr().out
    assert "通膨預期" in out and "殖利率倒掛" not in out

    assert cli.main(["projects", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [p["title"] for p in listed] == ["殖利率倒掛", "期限溢價", "通膨預期"]
    assert listed[0]["project"] == str(old) and listed[0]["shots"] == 5 and listed[0]["date"] == TODAY


def test_no_projects_yet_is_said_plainly(root, capsys):
    assert cli.main(["projects"]) == 0
    assert "還沒有" in capsys.readouterr().out


def test_output_files_are_named_after_the_title(tmp_path):
    from article_to_video.videos import output_paths

    project = tmp_path / "解說影片" / "2026-10-09-殖利率倒掛" / "專案"
    video, srt = output_paths(project, "16x9")
    assert video == project.parent / "殖利率倒掛.mp4"
    assert srt == project.parent / "字幕-殖利率倒掛.srt"  # 檔名不跟成片同名：播放器才不會再疊一層字幕
    video, srt = output_paths(project, "9x16")
    assert (video.name, srt.name) == ("殖利率倒掛-直式.mp4", "字幕-殖利率倒掛-直式.srt")

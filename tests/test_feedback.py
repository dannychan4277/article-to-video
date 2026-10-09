"""審片頁「複製給 Claude」的文字 → 結構化留言（主 skill 用這個解析作者貼回來的東西）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from article_to_video.feedback import ShotComment, parse_feedback

PASTED = """審片留言｜2026-10-09-殖利率曲線｜第 2 版
分鏡 1（SH01）：標題太長，改短一點
分鏡 3（SH03）：這根柱子出來太早
  要等旁白唸到「十年期」才出來
  分鏡 2：這行是留言內容，不是新的一則
整體意見：節奏有點快
  片尾想停久一點
"""


def test_each_line_becomes_a_comment_on_that_shot_plus_the_overall_comment():
    fb = parse_feedback(PASTED)
    assert fb.project == "2026-10-09-殖利率曲線"
    assert fb.version == 2
    assert fb.shots == [
        ShotComment(1, "SH01", "標題太長，改短一點"),
        ShotComment(3, "SH03", "這根柱子出來太早\n要等旁白唸到「十年期」才出來\n分鏡 2：這行是留言內容，不是新的一則"),
    ]
    assert fb.overall == "節奏有點快\n片尾想停久一點"


def test_hand_typed_variants_are_understood():
    fb = parse_feedback("分鏡3:字太小\n分鏡 4 ：數字錯了\n\n整體意見:OK\n")
    assert fb.project is None and fb.version is None
    assert fb.shots == [ShotComment(3, None, "字太小"), ShotComment(4, None, "數字錯了")]
    assert fb.overall == "OK"


def test_text_without_any_comment_has_nothing_to_do():
    fb = parse_feedback("審片留言｜某專案｜第 1 版\n")
    assert fb.shots == [] and fb.overall == ""
    assert fb.is_empty


def test_comments_from_an_outdated_page_are_flagged():
    fb = parse_feedback("分鏡 1（SH01）：好\n分鏡 2（SH09）：這張拿掉\n分鏡 7：太快\n")
    problems = fb.check(["SH01", "SH02", "SH03"])
    assert len(problems) == 2
    assert "分鏡 2" in problems[0] and "SH09" in problems[0] and "SH02" in problems[0]
    assert "分鏡 7" in problems[1] and "3 個分鏡" in problems[1]
    assert parse_feedback(PASTED.replace("SH03", "SH02").replace("分鏡 3", "分鏡 2")).check(["SH01", "SH02"]) == []


def test_cli_turns_pasted_text_into_json_with_shot_ids(tmp_path):
    root = tmp_path / "專案"
    root.mkdir()
    (root / "narration.json").write_text(json.dumps({"segments": {"s01": "一句話。"}}, ensure_ascii=False), encoding="utf-8")
    shot = {"chart": {"data": {"title": "標題", "series": [{"label": "甲", "value": 1}]}, "cues": [{"at": 0, "kind": "title"}]}}
    (root / "shots.json").write_text(json.dumps({"shots": [{"id": "A1", **shot}, {"id": "B2", "start": {"segment": "s01"}, **shot}]},
                                                ensure_ascii=False), encoding="utf-8")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "article_to_video", "feedback", str(root)],
        input="審片留言｜專案｜第 1 版\n分鏡 2：換成折線圖\n整體意見：可以\n",
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert [{k: c[k] for k in ("number", "id", "text")} for c in out["shots"]] == [{"number": 2, "id": "B2", "text": "換成折線圖"}]
    assert out["overall"] == "可以"
    assert out["version"] == 1
    assert out["problems"] == []


# ---------------------------------------------------------------- 留言對到分鏡與旁白段（#13）

NARRATION = {"s01": "第一段。", "s02": "第二段，曲線倒掛。", "s03": "第三段，原文寫得很清楚。", "s04": "第四段。"}


def chart(title: str) -> dict:
    return {"data": {"title": title, "series": [{"label": "甲", "value": 1}]}, "cues": [{"at": 0, "kind": "title"}]}


SHOTS = [
    {"id": "SH01", "start": {"segment": "s01"}, "chart": chart("短天期比較高")},
    {"id": "SH02", "start": {"segment": "s02", "word": "曲線"}, "continues": True,
     "chart": {"data": {"highlight": {"index": 0, "text": "倒掛"}}, "cues": []}},
    {"id": "SH03", "start": {"segment": "s03"},
     "vox": {"cues": [{"at": 0, "kind": "image", "image": "圖1"}, {"at": 1, "kind": "box", "rect": [1, 2, 3, 4]}]}},
    {"id": "SH04", "start": {"segment": "s03", "word": "原文"}, "continues": True,
     "vox": {"cues": [{"at": 0, "kind": "excerpt", "quote": "很清楚"}]}},
]


def make_project(tmp_path):
    root = tmp_path / "專案"
    root.mkdir()
    (root / "narration.json").write_text(json.dumps({"segments": NARRATION}, ensure_ascii=False), encoding="utf-8")
    (root / "shots.json").write_text(json.dumps({"shots": SHOTS}, ensure_ascii=False), encoding="utf-8")
    return root


def test_each_comment_says_which_narration_segments_its_shot_plays_over(tmp_path, capsys, monkeypatch):
    """分鏡從某段開頭切入就從那段算起；下一個分鏡從段中間切入，那段兩個分鏡都有份。改旁白時照這個找旁白段。"""
    import io

    from article_to_video import cli

    root = make_project(tmp_path)
    pasted = ("審片留言｜專案｜第 3 版\n分鏡 1（SH01）：標題改短\n分鏡 2（SH02）：旁白改成「曲線又倒掛了」\n"
              "分鏡 4（SH04）：剪報換一句\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(pasted))
    assert cli.main(["feedback", str(root)]) == 0
    out = json.loads(capsys.readouterr().out)
    got = {c["id"]: (c["number"], c["kind"], c["segments"]) for c in out["shots"]}
    assert got == {
        "SH01": (1, "財經圖表", ["s01", "s02"]),
        "SH02": (2, "財經圖表（接續）", ["s02"]),
        "SH04": (4, "Vox（接續）", ["s03", "s04"]),
    }
    sh02 = next(c for c in out["shots"] if c["id"] == "SH02")
    assert sh02["narration"] == {"s02": NARRATION["s02"]}  # 改旁白要改的就是這段


def test_outline_lists_every_shot_with_its_segments_for_editing_by_number(tmp_path, capsys):
    """打開舊專案「第 3 張圖改成…」：outline 列出每個分鏡的編號、id、種類、畫什麼、唸哪幾段。"""
    from article_to_video import cli

    root = make_project(tmp_path)
    assert cli.main(["outline", str(root)]) == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("分鏡 ")]
    assert len(lines) == 4
    assert lines[0].startswith("分鏡 1（SH01）財經圖表") and "短天期比較高" in lines[0]
    assert lines[2].startswith("分鏡 3（SH03）Vox") and "圖1" in lines[2] and "框" in lines[2]
    assert "旁白段 s03、s04" in lines[3]
    assert NARRATION["s03"] in out  # 唸的旁白也列出來

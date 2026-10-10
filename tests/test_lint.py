"""旁白規則檢查（#13）：`lint <專案>` 照 explainer-video 的「簡化技術中文」可量化規則查 narration.json。

規則與門檻（移植自 explainer-video 的 lint_script.py）：
  要改（結束碼 1）：整段 25–60 字、一口氣（，。？！；切開）≤12 字、一句（。？！切開）≤25 字、不寫阿拉伯數字與 %、
                    禁用套話、第一段開場直接講主題
  看一下（不擋）：模糊量詞、虛動詞、雙重否定、指代、英文縮寫，以及全長不在 2–4 分鐘
字數：中文一字一個；全大寫縮寫逐字母；其他英文字約三個字母一個；標點不算。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from article_to_video import cli

GOOD = "兩年期殖利率，平均四點五八。十年期只有三點九六，曲線倒掛。"  # 25 字：剛好在下限
GOOD2 = "短天期反而比長天期高，相差零點六二個百分點。這通常代表市場預期降息。"


def lint(tmp_path: Path, capsys, **segments: str) -> tuple[int, str]:
    proj = tmp_path / "專案"
    proj.mkdir(exist_ok=True)
    (proj / "narration.json").write_text(json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")
    code = cli.main(["lint", str(proj)])
    out = capsys.readouterr()
    return code, out.out + out.err


def test_narration_that_follows_every_rule_passes(tmp_path, capsys):
    code, out = lint(tmp_path, capsys, s01=GOOD, s02=GOOD2)
    assert code == 0, out
    assert "✓ 旁白段 s01（25 字" in out
    assert "✓ 旁白段 s02（31 字" in out
    assert "要改 0 項" in out


@pytest.mark.parametrize("text, why", [
    ("兩年期殖利率，平均四點五八。十年期三點九六。", "整段 19 字"),
    ("兩年期殖利率，平均四點五八。" * 6, "整段 72 字"),
    ("兩年期殖利率平均是四點五八左右，十年期只有三點九六，曲線倒掛了。", "一口氣 15 字"),
    ("兩年期殖利率，去年平均四點五八，十年期只有三點九六，曲線倒掛了喔。", "一句 29 字"),
    ("兩年期殖利率，平均是 4.58%。十年期只有三點九六，曲線倒掛。", "阿拉伯數字"),
    ("兩年期殖利率，平均四點五八％。十年期只有三點九六，曲線倒掛。", "阿拉伯數字或 %：％"),  # 只有全形百分號
    ("兩年期殖利率，平均是四點五八。總的來說，十年期只有三點九六。", "套話「總的來說」"),
])
def test_each_must_fix_rule_names_the_segment_and_fails(tmp_path, capsys, text, why):
    code, out = lint(tmp_path, capsys, s01=GOOD, s02=text)
    assert code == 1, out
    assert "✓ 旁白段 s01" in out
    assert "✗ 旁白段 s02" in out
    assert "要改｜" in out and why in out


def test_thresholds_are_the_reference_numbers(tmp_path, capsys):
    """邊界：25、60 字的整段可以；一口氣剛好 12 字可以、13 字不行；一句剛好 25 字可以、26 字不行。"""
    twelve = "一二三四五六七八九十甲乙"
    assert len(twelve) == 12
    code, out = lint(tmp_path, capsys, s01=f"{twelve}，{twelve}，丙。")  # 25 字、一句 25 字、每口氣 ≤12
    assert code == 0, out
    code, out = lint(tmp_path, capsys, s01=f"{twelve}丙，{twelve}。")  # 一口氣 13 字
    assert code == 1 and "一口氣 13 字" in out, out
    code, out = lint(tmp_path, capsys, s01=f"{twelve}，{twelve}，丙丁。")  # 一句 26 字
    assert code == 1 and "一句 26 字" in out, out
    sixty = f"{twelve}，{twelve}。{twelve}，{twelve}。{twelve}。"  # 60 字，每句 ≤25
    code, out = lint(tmp_path, capsys, s01=sixty)
    assert code == 0 and "60 字" in out, out
    code, out = lint(tmp_path, capsys, s01=sixty + "丙。")
    assert code == 1 and "整段 61 字" in out, out


def test_opening_must_go_straight_to_the_topic(tmp_path, capsys):
    """第一段開頭（或前 12 字內）不能寒暄；同樣的話放在後面的段落不算。"""
    hello = "大家好，兩年期殖利率平均四點五八。十年期只有三點九六，曲線倒掛。"
    code, out = lint(tmp_path, capsys, s01=hello, s02=GOOD2)
    assert code == 1 and "✗ 旁白段 s01" in out and "大家好" in out, out
    code, out = lint(tmp_path, capsys, s01=GOOD, s02=hello)
    assert code == 0, out


def test_counting_english_words_and_acronyms(tmp_path, capsys):
    """全大寫縮寫逐字母唸（FRED＝4）；一般英文字約三個字母一個音（yield＝2）；縮寫提醒 TTS 可能逐字母唸。"""
    text = "FRED 的資料，十年期殖利率走高。yield 這個字，市場天天在看，曲線倒掛。"
    code, out = lint(tmp_path, capsys, s01=text)
    # FRED 4＋的資料 3＋十年期殖利率走高 8＋yield 2＋這個字 3＋市場天天在看 6＋曲線倒掛 4＝30
    assert "旁白段 s01（30 字" in out, out
    assert "FRED" in out and "看一下｜" in out
    assert code == 0  # 只是提醒


@pytest.mark.parametrize("word", ["一些", "進行", "不得不"])
def test_soft_rules_only_warn(tmp_path, capsys, word):
    text = f"兩年期殖利率，平均四點五八。十年期{word}只有三點九六，曲線倒掛。"
    code, out = lint(tmp_path, capsys, s01=text)
    assert code == 0, out
    assert f"△ 旁白段 s01" in out and word in out and "看一下｜" in out


def test_total_length_is_estimated_against_two_to_four_minutes(tmp_path, capsys):
    """全長用每秒 4.4 字估（預設語速 +15%、頭尾空白修掉之後，示範片實測 500 字 114 秒）：太短只提醒（不擋），
    讓 Claude 知道離 2–4 分鐘還差多少。"""
    code, out = lint(tmp_path, capsys, s01=GOOD, s02=GOOD2)
    assert "合計 56 字，估計約 13 秒" in out
    assert "2–4 分鐘" in out
    assert code == 0


def test_missing_or_broken_narration_is_explained(tmp_path, capsys):
    proj = tmp_path / "空的"
    proj.mkdir()
    assert cli.main(["lint", str(proj)]) == 1
    assert "narration.json" in capsys.readouterr().err
    (proj / "narration.json").write_text("{\"segments\": ", encoding="utf-8")
    assert cli.main(["lint", str(proj)]) == 1
    assert "不是合法的 JSON" in capsys.readouterr().err

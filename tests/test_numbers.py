"""數字對帳（#5）：畫面上的每個數字對回出處（原文、公有數據、宣告過的推算），對不到的擋下正式出片。

切點：
1. reconcile(plan_build(專案)) → 每個分鏡上畫面的數字與出處（不渲染，很快）。
2. 正式出片：CLI build 被擋（繁中訊息點名分鏡與數字）、審片頁卡片標出來；預覽照樣能出。

測試專案的文章（tests/fixtures/mini_project/article/text.txt）是自己寫的假文章，數字是 FRED 公有領域數據
DGS2、DGS10 2023 年日資料平均。預期值在這裡照文章手寫，不呼叫被測程式算。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date
from collections.abc import Callable
from pathlib import Path

import pytest

from article_to_video.build import plan_build
from article_to_video.numbers import number_review, reconcile
from article_to_video.project import ProjectError

from conftest import MINI_PROJECT
from test_public_data import DGS10_MONTHLY, fred  # noqa: F401 — 127.0.0.1 上的假 FRED（#7 的 fixture）
from test_public_data import chart_shot as public_chart_shot
from test_public_data import make_project as make_public_project
from test_review_page import read_review


def make_project(tmp_path: Path, edit: Callable[[dict], None] | None = None, *, article: str | None = None) -> Path:
    root = tmp_path / "專案"
    shutil.copytree(MINI_PROJECT, root)
    if edit is not None:
        shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
        edit(shots)
        (root / "shots.json").write_text(json.dumps(shots, ensure_ascii=False), encoding="utf-8")
    if article is not None:
        (root / "article" / "text.txt").write_text(article, encoding="utf-8")
    return root


def sources(root: Path) -> dict[str, set[tuple[str, str, str]]]:
    """分鏡 id → {(哪裡, 畫面上的數字, 出處種類)}。"""
    rep = reconcile(plan_build(root, tts="fake"))
    return {sid: {(n.where, n.shown, n.kind) for n in items} for sid, items in rep.shots.items()}


def test_every_number_on_screen_is_traced_to_the_article(tmp_path):
    root = make_project(tmp_path)
    rep = reconcile(plan_build(root, tts="fake"))
    # 畫面上（SH01）：副標「美國公債殖利率，2023 年平均，%」、柱上數值 4.58% 3.96%、類目名「兩年期」「十年期」
    assert {(n.where, n.shown, n.kind) for n in rep.shots["SH01"]} == {
        ("副標", "2023", "article"),
        ("數值", "4.58%", "article"),
        ("數值", "3.96%", "article"),
        ("類目名", "兩", "article"),
        ("類目名", "十", "article"),
    }
    by_shown = {n.shown: n for n in rep.shots["SH01"]}
    assert "原文第 1 段" in by_shown["4.58%"].detail and "4.58%" in by_shown["4.58%"].detail
    # SH02 接續 SH01：同一張圖的數字記在 SH01；它自己只換了標註「長天期較低」，沒有數字
    assert rep.shots["SH02"] == []
    assert rep.unsourced == []


def test_numbers_match_after_normalizing_how_they_are_written(tmp_path):
    """文章和畫面寫法不同也對得上：全形、中文數字、百分之、千分位、萬、小數尾巴的 0；畫面比文章精確就對不上。
    文章用文章擷取（#6）的 article.json（段落＋頁碼）。"""
    def edit(shots: dict) -> None:
        sh01 = shots["shots"][0]["chart"]
        sh01["data"]["highlight"]["text"] = "1.2 萬檔、4.5% 與 4.585"
        sh01["cues"].append({"at": 1, "kind": "number", "data": {"value": 12345, "decimals": 0}})

    root = make_project(tmp_path, edit)
    (root / "article" / "text.txt").unlink()
    paragraphs = [
        {"n": 1, "text": "從二〇二三年說起：兩年期殖利率平均是百分之四點五八，十年期是３．９６％。", "page": 1},
        {"n": 2, "text": "這一年新發行 12345 檔，共約一萬兩千檔還在市場上，平均票面利率 4.50%。", "page": 2},
    ]
    (root / "article" / "article.json").write_text(
        json.dumps({"version": 1, "text": "text.txt", "paragraphs": paragraphs, "images": []}, ensure_ascii=False),
        encoding="utf-8")

    rep = reconcile(plan_build(root, tts="fake"))
    got = {(n.where, n.shown): n for n in rep.shots["SH01"]}
    expected = {
        ("副標", "2023"): "article",      # 二〇二三
        ("數值", "4.58%"): "article",     # 百分之四點五八
        ("數值", "3.96%"): "article",     # ３．９６％
        ("類目名", "兩"): "article",
        ("類目名", "十"): "article",
        ("標註", "1.2 萬"): "article",    # 一萬兩千
        ("標註", "4.5%"): "article",      # 4.50%
        ("標註", "4.585"): "none",        # 比文章的 4.58 多一位：不是文章寫的數字
        ("大數字", "12,345"): "article",  # 12345
    }
    assert {k: n.kind for k, n in got.items()} == expected
    assert "原文第 2 段（第 2 頁）" in got[("大數字", "12,345")].detail


# ---------------------------------------------------------------- 主切點：正式出片被擋、審片頁標出來


def cli(command: str, root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", "article_to_video", command, str(root), "--tts", "fake"],
                          capture_output=True, text=True, encoding="utf-8", env=env)


def unsourced_highlight(shots: dict) -> None:
    """SH02 的標註寫了文章裡沒有的數字：0.7（文章是 4.58 和 3.96，差 0.62）。"""
    shots["shots"][1]["chart"]["data"]["highlight"]["text"] = "長天期低 0.7"


def test_unsourced_number_blocks_the_final_build_but_not_the_preview(tmp_path):
    root = make_project(tmp_path, unsourced_highlight)

    preview = cli("preview", root)
    assert preview.returncode == 0, preview.stderr  # 預覽照樣出
    assert "數字對帳" in preview.stdout and "分鏡 SH02" in preview.stdout and "0.7" in preview.stdout  # 給 Claude 看
    card = read_review(root).cards[1]
    assert "0.7" in card.slots["numbers"] and "無出處" in card.slots["numbers"]

    build = cli("build", root)
    assert build.returncode == 1
    assert "分鏡 2" in build.stderr and "0.7" in build.stderr and "Traceback" not in build.stderr
    assert not (root / "成片.mp4").exists()
    assert not list((root / "build" / "16x9").glob("clips/*.mp4"))  # 渲染前就擋下
    page = read_review(root)  # 擋下之後審片頁也是這一版的樣子：沒有成片、無出處的數字標在卡上
    assert page.attrs["data-stage"] == "preview" and not page.videos
    assert "0.7" in page.cards[1].slots["numbers"] and "擋下出片" in page.cards[1].slots["numbers"]
    assert "分鏡 2：0.7" in page.page_slots["numbers"]  # 頁首也列出來：哪個分鏡的哪個數字擋住出片


def test_with_every_number_sourced_the_build_passes_and_each_card_shows_the_sources(built_project):
    """session 的測試專案（每個數字都在文章裡）照常出片；卡片上列出數字與出處。"""
    page = read_review(built_project.dir)
    assert page.attrs["data-stage"] == "final"
    sh01, sh02 = (c.slots["numbers"] for c in page.cards)
    for needle in ("2023", "4.58%", "3.96%", "原文", "原文第 1 段"):
        assert needle in sh01, f"分鏡 1 的數字對帳少了「{needle}」：{sh01}"
    assert "無出處" not in sh01 and "沿用" in sh02
    assert "都有出處" in page.page_slots["numbers"]


# ---------------------------------------------------------------- 白名單公有數據


def test_public_data_numbers_cite_the_series_and_the_dates(tmp_path, fred):
    shot = public_chart_shot(DGS10_MONTHLY, highlight={"index": 1, "text": "1 月平均 4.65%"})
    shot["chart"]["cues"].append({"at": 1, "kind": "highlight"})
    shot["derived"] = [{"value": "0.12", "formula": "4.6543 - 4.5355", "note": "一個月漲多少"}]
    shot["chart"]["cues"].append({"at": 1.5, "kind": "number", "data": {"value": 0.12, "decimals": 2}})
    rep = reconcile(plan_build(make_public_project(tmp_path, [shot]), tts="fake"))
    got = {(n.where, n.shown): n for n in rep.shots["SH01"]}
    today = date.today().isoformat()
    # 圖上兩個點：2024-12 月平均 4.5355、2025-01 月平均 4.6543（#7 測試手算），畫面寫到小數兩位
    for shown, month in (("4.54", "2024-12"), ("4.65", "2025-01")):
        n = got[("數值", shown)]
        assert n.kind == "public" and "DGS10" in n.detail and month in n.detail and today in n.detail
    for year in ("2024", "2025"):
        assert got[("類目名", year)].kind == "public"
    # 標註引用圖上的值：4.65% 就是 2025-01 那個點；「1 月」的 1 是圖上的期間
    assert got[("標註", "4.65%")].kind == "public" and "2025-01" in got[("標註", "4.65%")].detail
    assert got[("標註", "1")].kind == "public"
    # 大數字 0.12 是推算，輸入是圖上兩個點的完整值（4.6543 − 4.5355 = 0.1188，畫面寫到小數兩位）
    big = got[("大數字", "0.12")]
    assert big.kind == "derived" and "4.6543 − 4.5355" in big.detail and "DGS10" in big.detail
    assert rep.unsourced == []


def test_a_long_public_series_is_one_line_on_the_card(tmp_path, fred):
    # DGS10 2024-12-16～2025-01-10 每個交易日：fixture 裡 18 個有值的日子 → 18 個數值＋18 個日期
    shot = public_chart_shot({"series": "DGS10", "from": "2024-12-16", "to": "2025-01-10", "every": "day"})
    plan = plan_build(make_public_project(tmp_path, [shot]), tts="fake")
    [section] = number_review(plan).cards["SH01"]
    lines = {(it.label, it.text) for it in section.items}
    assert ("公有數據", "數值 18 個") in lines and ("公有數據", "類目名 18 個") in lines
    assert len(section.items) == 2


# ---------------------------------------------------------------- 推算：分鏡設定宣告算式


def highlight_with(text: str, *derived: dict) -> Callable[[dict], None]:
    def edit(shots: dict) -> None:
        sh02 = shots["shots"][1]
        sh02["chart"]["data"]["highlight"]["text"] = text
        if derived:
            sh02["derived"] = list(derived)
    return edit


def test_derived_number_shows_its_formula_and_where_each_input_came_from(tmp_path):
    edit = highlight_with("長天期低 0.62 個百分點",
                          {"value": "0.62", "formula": "4.58 - 3.96", "note": "兩年期減十年期"})
    plan = plan_build(make_project(tmp_path, edit), tts="fake")
    rep = reconcile(plan)
    [n] = rep.shots["SH02"]
    assert (n.where, n.kind) == ("標註", "derived")
    assert n.shown.startswith("0.62")
    # 審片頁看得到算式、結果、每個輸入的出處（4.58、3.96 都在文章第 1 段）
    assert "4.58 − 3.96 = 0.62" in n.detail and "兩年期減十年期" in n.detail
    assert "4.58：原文第 1 段" in n.detail and "3.96：原文第 1 段" in n.detail
    assert rep.unsourced == []
    # 審片頁的卡片：標「推算」、看得到算式
    [section] = number_review(plan).cards["SH02"]
    [item] = section.items
    assert item.label == "推算" and item.status == "ok" and "4.58 − 3.96 = 0.62" in item.detail


def test_derived_number_counts_only_if_the_formula_really_gives_it(tmp_path):
    # 算式算出來是 0.62，畫面上寫 0.7：宣告不算數，0.7 還是無出處
    edit = highlight_with("長天期低 0.7", {"value": "0.7", "formula": "4.58 - 3.96"})
    rep = reconcile(plan_build(make_project(tmp_path, edit), tts="fake"))
    [n] = rep.shots["SH02"]
    assert n.kind == "none" and "0.62" in n.detail and "對不上" in n.detail


def test_derived_number_needs_every_input_to_have_a_source(tmp_path):
    # 4.08 文章裡沒有：算得出 0.5 也不行
    edit = highlight_with("長天期低 0.5", {"value": "0.5", "formula": "4.58 - 4.08"})
    rep = reconcile(plan_build(make_project(tmp_path, edit), tts="fake"))
    [n] = rep.shots["SH02"]
    assert n.kind == "none" and "4.08" in n.detail and "找不到出處" in n.detail


def test_derived_formula_can_round_to_what_the_screen_shows(tmp_path):
    # (4.58 − 3.96) ÷ 3.96 × 100 = 15.656…%，畫面寫到小數一位 15.7%
    edit = highlight_with("長天期低 15.7%", {"value": "15.7%", "formula": "(4.58 - 3.96) / 3.96 * 100"})
    rep = reconcile(plan_build(make_project(tmp_path, edit), tts="fake"))
    [n] = rep.shots["SH02"]
    assert n.kind == "derived" and "(4.58 − 3.96) ÷ 3.96 × 100" in n.detail


@pytest.mark.parametrize("derived, needles", [
    ({"value": "0.62", "formula": "4.58 - 3.96"}, ["derived", "清單"]),
    ([{"value": "0.62"}], ["derived[0]", "formula"]),
    ([{"value": "0.62", "formula": "4.58 -"}], ["derived[0]", "算式", "4.58 -"]),
    ([{"value": "0.62", "formula": "4.58"}], ["derived[0]", "運算"]),
    ([{"value": "很多", "formula": "4.58 - 3.96"}], ["derived[0]", "value", "很多"]),
    ([{"value": "0.62", "formula": "4.58 / (3.96 - 3.96)"}], ["derived[0]", "除以 0"]),
    ([{"value": "0.62", "formula": "4.58 - 3.96", "formular": "x"}], ["derived[0]", "formular"]),
])
def test_bad_derived_declarations_are_explained_in_chinese(tmp_path, derived, needles):
    def edit(shots: dict) -> None:
        shots["shots"][1]["derived"] = derived
    with pytest.raises(ProjectError) as e:
        plan_build(make_project(tmp_path, edit), tts="fake")
    msg = str(e.value)
    for needle in ["分鏡 SH02", *needles]:
        assert needle in msg, f"錯誤訊息少了「{needle}」：{msg}"

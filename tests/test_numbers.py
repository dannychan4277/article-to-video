"""數字對帳（#5）：畫面上的每個數字對回出處（原文、公有數據、宣告過的推算），對不到的擋下正式出片。

切點：
1. reconcile(plan_build(專案)) → 每個分鏡上畫面的數字與出處（不渲染，很快）；數字只在文章的圖上時，
   Claude 讀圖記下的數字（article/figure_numbers.json）讓它對到「原文圖表」，審片頁請作者對照原圖。
2. 正式出片：CLI build 被擋（繁中訊息點名分鏡與數字）、審片頁卡片標出來；預覽照樣能出。

測試專案的文章（tests/fixtures/mini_project/article/text.txt）是自己寫的假文章，數字是 FRED 公有領域數據
DGS2、DGS10 2023 年日資料平均。預期值在這裡照文章手寫，不呼叫被測程式算。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from collections.abc import Callable
from pathlib import Path

import pytest

from article_to_video.build import plan_build
from article_to_video.numbers import check_numbers, number_review, reconcile, summary_line
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
    # 畫面上（SH01）：副標「美國公債殖利率，2023 年平均，%」、柱上數值 4.58% 3.96%；類目名「兩年期」「十年期」是天期的名字，
    # 詞裡的中文數字不算數字
    assert {(n.where, n.shown, n.kind) for n in rep.shots["SH01"]} == {
        ("副標", "2023", "article"),
        ("數值", "4.58%", "article"),
        ("數值", "3.96%", "article"),
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
        sh01["data"]["title"] = "短天期反而比較高：規模 3.2（兆美元）"
        sh01["cues"].append({"at": 1, "kind": "number", "data": {"value": 12345, "decimals": 0}})
        # 引擎（JS toFixed）把剛好在中間的 0.125 進位成 0.13；Python 的 format 會變 0.12
        sh01["cues"].append({"at": 1.2, "kind": "number", "data": {"value": 0.125, "decimals": 2}})

    root = make_project(tmp_path, edit)
    (root / "article" / "text.txt").unlink()
    paragraphs = [
        {"n": 1, "text": "從二〇二三年說起：兩年期殖利率平均是百分之四點五八，十年期是３．９６％。", "page": 1},
        {"n": 2, "text": "這一年新發行 12345 檔，共約一萬兩千檔還在市場上，平均票面利率 4.50%，規模約 3.2 兆美元，利差 0.13。",
         "page": 2},
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
        ("標註", "1.2 萬"): "article",    # 一萬兩千
        ("標註", "4.5%"): "article",      # 4.50%
        ("標註", "4.585"): "none",        # 比文章的 4.58 多一位：不是文章寫的數字
        ("標題", "3.2"): "article",       # 文章寫 3.2 兆：單位寫在別處（括號、圖的單位行）也算
        ("大數字", "12,345"): "article",  # 12345
        ("大數字", "0.13"): "article",    # 畫面上的樣子跟引擎一樣
    }
    assert {k: n.kind for k, n in got.items()} == expected
    assert "原文第 2 段（第 2 頁）" in got[("大數字", "12,345")].detail


def test_later_screen_texts_like_vox_index_cards_are_reconciled_too(tmp_path):
    """Vox 分鏡（#8）的索引卡字：寫一個 ScreenTextSource 加進 default_sources() 就會一起對帳。"""
    from article_to_video.numbers import ScreenText, default_sources

    def index_cards(plan, st) -> list:
        return [ScreenText("索引卡", "兩年期 4.58%，比去年多 3.1 個百分點")] if st.shot.id == "SH01" else []

    rep = reconcile(plan_build(make_project(tmp_path), tts="fake"), sources=[*default_sources(), index_cards])
    assert {(n.shown, n.kind) for n in rep.shots["SH01"] if n.where == "索引卡"} == {
        ("4.58%", "article"), ("3.1 個百分點", "none")}


def sh02_numbers(tmp_path: Path, text: str, *, article: str | None = None) -> set[tuple[str, str]]:
    """SH02 的標註改成 text（文章照 mini_project，或給 article），回傳 {(畫面上的數字, 出處種類)}。"""
    def edit(shots: dict) -> None:
        shots["shots"][1]["chart"]["data"]["highlight"]["text"] = text
    rep = reconcile(plan_build(make_project(tmp_path, edit, article=article), tts="fake"))
    return {(n.shown, n.kind) for n in rep.shots["SH02"]}


@pytest.mark.parametrize("text, shown", [
    ("長天期低 10%", "10%"),           # 文章只有「十年期」：詞裡的「十」不是數字
    ("跌了 2%", "2%"),                # 文章只有「兩年期」
    ("差了 5 個百分點", "5 個百分點"),   # 文章的 4.58% 四捨五入到整數是 5：只寫一位數的數字要原文一字不差
    ("-4.6", "-4.6"),                 # 文章的 4.58 是正的：正負號要對
    ("約 4%", "4%"),                  # 3.96 四捨五入是 4，跟上面一樣：整數不比小數好對
    ("−3.96%", "−3.96%"),             # 數值一樣、正負號相反
    ("4.6 個百分點", "4.6 個百分點"),    # 4.58% 是殖利率（%），不是差幾個百分點：單位不同就不能四捨五入過去
])
def test_numbers_that_only_look_like_the_article_are_still_unsourced(tmp_path, text, shown):
    """mini_project 的文章：「2023 年，美國兩年期公債殖利率平均是 4.58%，十年期平均只有 3.96%。」"""
    assert sh02_numbers(tmp_path, text) == {(shown, "none")}


@pytest.mark.parametrize("text, article, shown", [
    ("長天期 4.6%", None, "4.6%"),                         # 4.58 四捨五入到一位小數：留兩位有效數字就可以
    ("實質殖利率 −0.5%", "實質殖利率一度是 -0.5%。", "−0.5%"),  # 負數對負數
    ("實質殖利率 −0.5%", "實質殖利率一度是負 0.5%。", "−0.5%"),
    ("降息 3 碼", "聯準會降息三碼。", "3"),                 # 中文數字帶著單位（碼、倍、%…）才是數字
    ("十年期", None, None),                                # 詞裡的中文數字不是數字，不用對帳
])
def test_numbers_that_really_are_in_the_article_keep_matching(tmp_path, text, article, shown):
    base = (MINI_PROJECT / "article" / "text.txt").read_text(encoding="utf-8")
    got = sh02_numbers(tmp_path, text, article=None if article is None else base + "\n" + article)
    assert got == (set() if shown is None else {(shown, "article")})


# ---------------------------------------------------------------- 主切點：正式出片被擋、審片頁標出來


def cli(command: str, root: Path) -> subprocess.CompletedProcess:
    from conftest import run_cli

    return run_cli(command, root, "--tts", "fake")


def edit_json(path: Path, fn: Callable[[dict], None]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def unsourced_highlight(shots: dict) -> None:
    """SH02 的標註寫了文章裡沒有的數字：0.7（文章是 4.58 和 3.96，差 0.62）。"""
    shots["shots"][1]["chart"]["data"]["highlight"]["text"] = "長天期低 0.7"


def test_unsourced_number_blocks_the_final_build_but_not_the_preview(built_project, tmp_path):
    """出過片的專案，改出一個無出處的數字：正式出片被擋（不渲染、成片不動），審片頁改回預覽的樣子並標出那個數字。"""
    root = tmp_path / "2026-10-09-殖利率曲線"
    shutil.copytree(built_project.dir, root)
    assert read_review(root).attrs["data-stage"] == "review"
    video_mtime = (root / "成片.mp4").stat().st_mtime_ns
    clip_mtime = (root / "build" / "16x9" / "clips" / "SH02.mp4").stat().st_mtime_ns
    edit_json(root / "shots.json", unsourced_highlight)

    build = cli("build", root)
    assert build.returncode == 1
    assert "分鏡 SH02（審片頁的分鏡 2）" in build.stderr and "0.7" in build.stderr and "Traceback" not in build.stderr
    assert (root / "成片.mp4").stat().st_mtime_ns == video_mtime  # 沒有重組成片
    assert (root / "build" / "16x9" / "clips" / "SH02.mp4").stat().st_mtime_ns == clip_mtime  # 渲染前就擋下
    page = read_review(root)  # 審片頁跟著這一版：不再內嵌上一版的成片、無出處的數字標在卡上
    assert page.attrs["data-stage"] == "preview" and not page.videos
    assert "0.7" in page.cards[1].slots["numbers"] and "擋下出片" in page.cards[1].slots["numbers"]
    assert "分鏡 2：0.7" in page.page_slots["numbers"]  # 頁首也列出來：哪個分鏡的哪個數字擋住出片

    preview = cli("preview", root)
    assert preview.returncode == 0, preview.stderr  # 預覽照樣出
    assert "數字對帳" in preview.stdout and "分鏡 SH02" in preview.stdout and "0.7" in preview.stdout  # 給 Claude 看
    card = read_review(root).cards[1]
    assert "0.7" in card.slots["numbers"] and "無出處" in card.slots["numbers"]


def test_with_every_number_sourced_the_build_passes_and_each_card_shows_the_sources(built_project):
    """session 的測試專案（每個數字都在文章裡）照常出片；卡片上列出數字與出處。"""
    page = read_review(built_project.dir)
    assert page.attrs["data-stage"] == "review"
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


# ---------------------------------------------------------------- 原文圖表：數字只在文章的圖上，Claude 讀圖記下來


def with_figure(root: Path, figures: dict | None) -> Path:
    """文章擷取的清單（article.json）：一段文字＋兩張圖；figures 不是 None 就寫 article/figure_numbers.json（Claude 讀圖記的）。"""
    paragraphs = [{"n": 1, "text": "2023 年，美國兩年期公債殖利率平均是 4.58%，十年期平均只有 3.96%。", "page": 1}]
    images = [{"n": n, "label": f"圖{n}", "file": f"images/圖{n}.png", "width": 800, "height": 600} for n in (1, 2)]
    (root / "article" / "article.json").write_text(
        json.dumps({"version": 1, "text": "text.txt", "paragraphs": paragraphs, "images": images}, ensure_ascii=False),
        encoding="utf-8")
    if figures is not None:
        (root / "article" / "figure_numbers.json").write_text(json.dumps(figures, ensure_ascii=False), encoding="utf-8")
    return root


def redrawn_figure(shots: dict) -> None:
    """SH01 改成重畫文章的圖1：兩根柱的數字（5.12、4.21）只在圖上，文章文字裡沒有。"""
    data = shots["shots"][0]["chart"]["data"]
    data["series"] = [{"label": "兩年期", "value": 5.12}, {"label": "十年期", "value": 4.21}]
    data["unit"] = "美國公債殖利率，%"
    data["source"] = "來源：作者原文（圖1）"


FIG1 = {"figures": {"圖1": [{"value": "5.12", "label": "兩年期殖利率，去年高點"},
                            {"value": "4.21", "label": "十年期殖利率，去年高點"}]}}


def test_numbers_claude_read_off_an_article_figure_are_sourced_to_that_figure(tmp_path):
    root = with_figure(make_project(tmp_path, redrawn_figure), FIG1)
    plan = plan_build(root, tts="fake")
    check_numbers(plan)  # 不擋出片
    assert "原文圖表 2" in summary_line(plan)  # preview 印給 Claude 看的那行
    rep = reconcile(plan)
    got = {n.shown: n for n in rep.shots["SH01"]}
    assert got["5.12%"].kind == "figure" and got["4.21%"].kind == "figure"
    assert got["5.12%"].label == "原文圖表"
    assert "原文圖表（圖1，Claude 讀圖）" in got["5.12%"].detail and "兩年期殖利率，去年高點" in got["5.12%"].detail
    assert rep.unsourced == []


def test_without_the_figure_reading_those_numbers_still_block(tmp_path):
    root = with_figure(make_project(tmp_path, redrawn_figure), None)
    plan = plan_build(root, tts="fake")
    assert {n.shown for n in reconcile(plan).unsourced} == {"5.12%", "4.21%"}
    with pytest.raises(ProjectError) as e:
        check_numbers(plan)
    assert "figure_numbers.json" in str(e.value)  # 修法告訴 Claude：讀圖、記下來


def test_the_review_card_asks_the_author_to_check_figure_numbers_against_his_figure(tmp_path):
    """讀圖是 Claude 讀的，作者要對一下：那一區攤開（不縮成一行）、每個數字標「原文圖表」、寫是哪張圖、圖上的什麼。"""
    root = with_figure(make_project(tmp_path, redrawn_figure), FIG1)
    review = number_review(plan_build(root, tts="fake"))
    [section] = review.cards["SH01"]
    assert section.summary == ""  # 不收起來
    assert "圖1" in section.note and "對照" in section.note
    figure_items = [it for it in section.items if it.label == "原文圖表"]
    assert {it.text for it in figure_items} == {"5.12%", "4.21%"}
    assert all("圖1" in it.detail and "Claude 讀圖" in it.detail for it in figure_items)
    assert all(it.status == "warn" for it in figure_items)  # 要看一下，不是擋下出片
    [page] = review.page
    assert "原文圖表" in page.summary + page.note + page.title


def test_only_the_numbers_the_author_must_check_are_spread_out_on_the_card(tmp_path):
    """示範片：一張重畫圖1 的柱狀圖，卡片上攤開十幾行出處，作者要對照的只有三個讀圖的數字。
    攤開的只有要作者看的（原文圖表、無出處）；其他對得到出處的收成一行，點開才看。"""
    def edit(shots: dict) -> None:
        redrawn_figure(shots)
        shots["shots"][0]["chart"]["data"]["title"] = "2023 年的殖利率"  # 2023 文章文字裡有

    root = with_figure(make_project(tmp_path, edit), FIG1)
    [section] = number_review(plan_build(root, tts="fake")).cards["SH01"]
    assert {it.text for it in section.items} == {"5.12%", "4.21%"}
    assert "2023" in {it.text for it in section.rest}
    assert all(it.status == "ok" for it in section.rest)
    assert "1 個" in section.rest_summary and "出處" in section.rest_summary


def test_years_on_a_redrawn_figures_axis_point_to_the_figure_reading(tmp_path):
    """示範：重畫圖1 的柱狀圖，類目名（橫軸年份）2020 文章文字裡沒有 → 無出處。Claude 要知道它也是「圖上的字」，
    該讀圖記進 figure_numbers.json 的圖1——提示要點名那張圖、說軸上的年份也算。"""
    def edit(shots: dict) -> None:
        redrawn_figure(shots)
        shots["shots"][0]["chart"]["data"]["series"] = [{"label": "2019", "value": 5.12}, {"label": "2023", "value": 4.21}]

    root = with_figure(make_project(tmp_path, edit), FIG1)
    plan = plan_build(root, tts="fake")
    [n] = reconcile(plan).unsourced  # 2023 文章裡有；2019 只在圖上
    assert (n.where, n.shown) == ("類目名", "2019")
    assert "圖1" in n.detail and "年份" in n.detail  # 審片頁卡片上：這張是重畫圖1，軸上的年份也是圖上的字
    line = summary_line(plan)  # preview 印給 Claude 的那行
    assert "2019" in line and "figure_numbers.json" in line and "「圖1」" in line
    with pytest.raises(ProjectError) as e:
        check_numbers(plan)
    assert "2019" in str(e.value) and "「圖1」" in str(e.value) and "類目名" in str(e.value)

    with_figure(root, {"figures": {"圖1": [*FIG1["figures"]["圖1"], {"value": "2019", "label": "橫軸年份（第一根柱）"}]}})
    got = {x.shown: x for x in reconcile(plan_build(root, tts="fake")).shots["SH01"]}
    assert got["2019"].kind == "figure"


def test_a_difference_of_two_figure_numbers_can_be_declared(tmp_path):
    def edit(shots: dict) -> None:
        redrawn_figure(shots)
        highlight_with("長天期低 0.91 個百分點", {"value": "0.91", "formula": "5.12 - 4.21"})(shots)

    root = with_figure(make_project(tmp_path, edit), FIG1)
    [n] = reconcile(plan_build(root, tts="fake")).shots["SH02"]
    assert n.kind == "derived" and "5.12 − 4.21 = 0.91" in n.detail
    assert "5.12：原文圖表（圖1，Claude 讀圖）" in n.detail


@pytest.mark.parametrize("figures, needles", [
    ({"圖1": [{"value": "5.12", "label": "兩年期"}]}, ["figures"]),                       # 少了外層 figures
    ({"figures": {"圖3": [{"value": "5.12", "label": "兩年期"}]}}, ["圖3", "圖1、圖2"]),  # 文章沒有這張圖
    ({"figures": {"第一張": [{"value": "5.12", "label": "兩年期"}]}}, ["第一張", "圖號"]),
    ({"figures": {"圖1": [{"value": "5.12"}]}}, ["圖1[0]", "label"]),                       # 作者要知道這是什麼數字
    ({"figures": {"圖1": [{"value": "很高", "label": "兩年期"}]}}, ["圖1[0]", "value", "很高"]),
    ({"figures": {"圖1": [{"value": "5.12", "label": "兩年期", "unit": "%"}]}}, ["圖1[0]", "unit"]),
    ({"figures": {"圖1": []}}, ["圖1", "清單"]),
])
def test_mistakes_in_the_figure_reading_are_explained_in_chinese(tmp_path, figures, needles):
    root = with_figure(make_project(tmp_path, redrawn_figure), figures)
    with pytest.raises(ProjectError) as e:
        plan_build(root, tts="fake")
    msg = str(e.value)
    for needle in ["article/figure_numbers.json", *needles]:
        assert needle in msg, f"錯誤訊息少了「{needle}」：{msg}"

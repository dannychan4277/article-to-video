"""片尾卡的內容：本片用到的資料來源彙整、頻道名沒填時的排法。多半用 plan_build（不渲染，很快）看片尾卡的片段計畫。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from article_to_video.build import build_project, plan_build
from article_to_video.end_card import END_CARD_ID, merge_sources
from article_to_video.project import ProjectError

from conftest import MINI_PROJECT
from test_public_data import fred  # noqa: F401（假 FRED 伺服器 fixture）


def project_with_two_charts(tmp_path: Path) -> Path:
    root = tmp_path / "專案"
    shutil.copytree(MINI_PROJECT, root)
    shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
    sh1, sh2 = shots["shots"]
    sh1["chart"]["data"]["source"] = "來源：FRED（DGS2）"
    sh2["continues"] = False
    sh2["chart"] = {
        "data": {"title": "長天期比較低", "unit": "%", "source": "資料來源：FRED（DGS10）",
                 "series": [{"label": "十年期", "value": 3.96}]},
        "cues": [{"at": 0, "kind": "title"}, {"at": 0.1, "kind": "bar"}],
    }
    (root / "shots.json").write_text(json.dumps(shots, ensure_ascii=False), encoding="utf-8")
    return root


def end_card(plan):
    [card] = [c for c in plan.clips if c.id == END_CARD_ID]
    assert plan.clips[-1] is card  # 片尾卡是最後一個片段
    return card


def test_end_card_merges_the_sources_of_every_chart(tmp_path):
    plan = plan_build(project_with_two_charts(tmp_path), tts="fake")
    assert end_card(plan).spec["data"]["sources"] == ["FRED（DGS2、DGS10）"]


def test_end_card_starts_when_the_narration_ends(tmp_path):
    plan = plan_build(project_with_two_charts(tmp_path), tts="fake")
    card = end_card(plan)
    assert card.start_frame == plan.clips[-2].end_frame == plan.timeline.outro.start_frame
    assert card.end_frame == plan.timeline.total_frames


def test_merge_sources_examples():
    """Vox、公有數據那幾張票把出處放進 ClipPlan.sources，片尾卡用這個規則彙整。"""
    assert merge_sources([
        "來源：FRED（DGS10）", "資料來源：FRED（DFII10）", "FRED（DGS10）",
        "作者文章圖 2", "來源：作者文章圖 2", "財政部（10 年期、2 年期），月資料",
    ]) == ["FRED（DGS10、DFII10）", "作者文章圖 2", "財政部（10 年期、2 年期），月資料"]
    # 公有數據自動寫的來源行（public_data.py）：一列裡用「；」分欄，整列是一個出處
    assert merge_sources([
        "來源：FRED，聯準會（DGS10）；月平均；2026-10-09 擷取",
        "來源：FRED，聯準會（DFII10）；月平均；2026-10-09 擷取",
    ]) == ["FRED，聯準會（DGS10、DFII10）；月平均；2026-10-09 擷取"]


def test_chart_source_notes_stay_whole_and_article_references_group_cleanly():
    """試跑的片尾卡：圖表自己寫的來源「作者原文（FRED 日資料年平均）」被當成段號，跟 Vox 的「作者原文（第 4 段）」
    「作者原文（圖1）」併成一串。括號裡的說明不能當成段號併進去；圖號、段號才併，而且圖、段各自照順序排好。
    同一個出處只列一行（第三次示範）：說明文字不是每一列都有，就不上片尾卡（圖上的來源行自己寫著）。"""
    sources = [  # 照片段順序：公有數據圖、Vox、作者自算的圖、Vox、公有數據圖×2、Vox、作者自算的圖
        "FRED，聯準會（DGS10、DGS2）；十年期減兩年期＝DGS10 減 DGS2 自算，月平均；2026-10-09 擷取",
        "作者原文（第 4 段）",
        "來源：作者原文（FRED 日資料年平均）",
        "作者原文（圖1）", "作者原文（圖2）", "作者原文（第 19 段）",
        "FRED，聯準會（THREEFYTP10）；年平均；2026-10-09 擷取",
        "FRED，聯準會（DGS10）；月平均；2026-10-09 擷取",
        "作者原文（第 16 段）", "作者原文（圖2）",
        "來源：作者原文（FRED 日資料月平均）",
    ]
    assert merge_sources(sources) == [
        "FRED，聯準會（DGS10、DGS2、THREEFYTP10）；2026-10-09 擷取",
        "作者原文（圖1、圖2、第 4、16、19 段）",
    ]
    assert merge_sources(sources[2:3]) == ["作者原文（FRED 日資料年平均）"]  # 只有一種寫法：照原樣


def test_charts_redrawn_from_article_figures_are_credited_together():
    """文章的圖重畫成圖表（ADR 0001）：來源行寫「來源：作者原文（圖N）」，片尾卡併成一行；寫成「資料：」也一樣。"""
    assert merge_sources(["來源：作者原文（圖2）", "資料：作者原文（圖1）", "來源：FRED（DGS10）", "作者原文（圖2）"]) == [
        "作者原文（圖1、圖2）", "FRED（DGS10）"]


def test_each_source_is_credited_once_even_with_different_notes():
    """第三次示範的片尾卡：「作者原文（圖1、圖2）」和「作者原文」各一行、FRED 的利差圖（月平均、年平均兩種算法）
    各一行，再加 THREEFYTP10、DGS10 一行。同一個出處只列一行：代號、圖號全部併進括號；每一行都有的說明（擷取日期）
    留著，各張圖不一樣的算法說明拿掉（圖上的來源行自己有寫）。光寫「作者原文」不另成一行。"""
    spread = "來源：FRED，聯準會（DGS10、DGS2）；十年期減兩年期＝DGS10 減 DGS2 自算，{}；2026-10-10 擷取"
    sources = [  # 照分鏡順序，一個分鏡一行
        spread.format("月平均"), "來源：作者原文（圖1）", "來源：作者原文（圖2）", spread.format("年平均"),
        "來源：FRED，聯準會（THREEFYTP10）；月平均；2026-10-10 擷取", "來源：作者原文",
        "來源：FRED，聯準會（DGS10）；月平均；2026-10-10 擷取", "來源：作者原文",
    ]
    assert merge_sources(sources) == [
        "FRED，聯準會（DGS10、DGS2、THREEFYTP10）；2026-10-10 擷取",
        "作者原文（圖1、圖2）",
    ]
    assert merge_sources(["作者原文", "FRED（DGS10）", "作者原文（第 3 段）"]) == ["作者原文（第 3 段）", "FRED（DGS10）"]
    # 擷取日期不一樣（不同天抓的）：日期也不是每一行都有，就只留出處和代號
    assert merge_sources(["FRED，聯準會（DGS10）；月平均；2026-10-09 擷取", "FRED，聯準會（DGS2）；月平均；2026-10-10 擷取"]) == [
        "FRED，聯準會（DGS10、DGS2）；月平均"]


def test_a_redrawn_figure_that_names_its_original_data_is_credited_once_per_source():
    """計時實測的片尾卡（獨立審片 R1-16「來源三行內容重疊」）：重畫文章的圖，來源行照分鏡規則寫
    「作者原文（圖N）；原始資料：FRED（…）」，旁邊是公有數據自動寫的「FRED，聯準會（…）；…；擷取日期」。
    原本「；原始資料：…」被當成作者原文的說明；沒寫圖號的那列（「作者原文；原始資料：FRED（…）」）整列被當成
    一個叫「作者原文；原始資料：FRED」的出處，卡上三行互相重疊。一列裡「原始資料：」後面是另一個出處，各自併進
    自己那一行；「FRED」跟「FRED，聯準會」是同一個出處。（來源列照實測專案的寫法，日期換成自製的。）"""
    sources = [  # 照片段順序
        "來源：作者原文；原始資料：FRED（DGS10、DGS2）",
        "來源：FRED，聯準會（DGS10、DGS2）；十年期減兩年期＝DGS10 減 DGS2 自算，月平均；2026-09-30 擷取",
        "來源：作者原文",
        "來源：作者原文（圖1）；原始資料：FRED（DGS10、DGS2）",
        "來源：作者原文（圖2）；原始資料：FRED（DGS2、DGS10）",
        "來源：作者原文（圖2）；原始資料：FRED（DGS2）",
        "來源：作者原文；原始資料：FRED（THREEFYTP10）",
        "來源：FRED，聯準會（THREEFYTP10）；月平均；2026-09-30 擷取",
        "來源：作者原文（圖2）；原始資料：FRED（DGS10）",
    ]
    assert merge_sources(sources) == [
        "作者原文（圖1、圖2）",
        "FRED，聯準會（DGS10、DGS2、THREEFYTP10）；2026-09-30 擷取",
    ]
    # 只有一列：原始出處也是自己一行（片尾卡的小標就是「資料來源」）
    assert merge_sources(["來源：作者原文（圖2）；原始資料：聯準會"]) == ["作者原文（圖2）", "聯準會"]


def test_public_data_charts_show_up_on_the_end_card(tmp_path, fred):
    """公有數據自動寫的來源行也會進片尾卡；同出處、同算法的兩個系列併成一行。"""
    from datetime import date

    from test_public_data import chart_shot, make_project

    second = chart_shot({"series": "DFII10", "from": "2024-12-01", "to": "2025-01-31", "every": "month"}, sid="SH02")
    second["start"] = {"segment": "s02"}
    root = make_project(tmp_path, [
        chart_shot({"series": "DGS10", "from": "2024-12-01", "to": "2025-01-31", "every": "month"}), second,
    ])
    sources = end_card(plan_build(root, tts="fake")).spec["data"]["sources"]
    assert sources == [f"FRED，聯準會（DGS10、DFII10）；月平均；{date.today().isoformat()} 擷取"]


def test_report_zip_carries_the_end_card_logo_so_it_reproduces(tmp_path):
    """回報問題的 zip 要帶著設定快照裡的 logo，不然維護者那台重現（plan_build）時片尾卡會說 logo 不見了。"""
    import zipfile

    from test_report_issue import cli
    from test_user_settings import _png

    root = tmp_path / "專案"
    shutil.copytree(MINI_PROJECT, root)
    (root / "logo.png").write_bytes(_png(8, 8))
    snap = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    (root / "settings.json").write_text(json.dumps({**snap, "logo": "logo.png"}), encoding="utf-8")
    desk = tmp_path / "桌面"
    assert cli("report-issue", str(root), "--out-dir", str(desk)).returncode == 0
    [zpath] = desk.glob("*.zip")
    with zipfile.ZipFile(zpath) as z:
        assert z.read("project/logo.png") == _png(8, 8)  # 重現時解開就在 settings.json 指的位置


def project_without_a_channel(tmp_path: Path, *, logo: bool = False) -> Path:
    """只想自己看懂文章的人：安裝時頻道名跳過了（設定快照裡是空的）；logo 可有可無。"""
    from test_user_settings import _png

    root = tmp_path / "專案"
    shutil.copytree(MINI_PROJECT, root)
    snap = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    extra = {"channel": ""}
    if logo:
        (root / "logo.png").write_bytes(_png(64, 64, LOGO_RGB))
        extra["logo"] = "logo.png"
    (root / "settings.json").write_text(json.dumps({**snap, **extra}), encoding="utf-8")
    return root


LOGO_RGB = (20, 90, 180)


def test_without_a_channel_name_the_build_goes_on_and_the_card_has_no_name(tmp_path):
    """頻道名是選填：沒填照樣排得出整支片，片尾卡上沒有名字，資料來源和聲明照放。"""
    data = end_card(plan_build(project_without_a_channel(tmp_path), tts="fake")).spec["data"]
    assert not data.get("channel")
    assert data["sources"] == ["FRED（DGS2、DGS10），年平均自算"]
    assert data["notes"] == ["僅供教育，非投資建議", "配音為 AI 合成"]


def _pixels(path: Path, mode: str):
    import numpy as np
    from PIL import Image

    return np.asarray(Image.open(path).convert(mode), dtype=np.int16)


@pytest.mark.parametrize("aspect, logo", [("16x9", False), ("9x16", False), ("16x9", True)])
def test_card_without_a_channel_name_leaves_no_hole(tmp_path, aspect, logo):
    """沒有名字那一塊：有 logo 就只放 logo；都沒有時資料來源、聲明重新排。不管哪種，淡入的內容整塊都在
    頂端小旗和底部聲明之間置中（上下留白差不多），名字原本的位置不會空一塊。

    第一幀只有頂端色條和小旗（內容都還沒淡入），最後一幀減掉第一幀＝淡入的內容；最大的那道空隙分開內容和聲明。"""
    import numpy as np

    from article_to_video.clips import render_stills
    from article_to_video.timeline import FORMATS

    root = project_without_a_channel(tmp_path, logo=logo)
    spec = end_card(plan_build(root, tts="fake", fmt=FORMATS[aspect])).spec
    first, last = render_stills(spec, tmp_path / "stills", [0, spec["duration"] - 0.02], base_dir=root).paths
    a0, a1 = _pixels(first, "L"), _pixels(last, "L")
    deco_bottom = int(np.nonzero((a0 < 180).any(axis=1))[0].max())  # 小旗的底
    rows = np.nonzero((np.abs(a1 - a0) > 60).any(axis=1))[0]
    assert len(rows) and rows.min() > deco_bottom
    split = int(np.argmax(np.diff(rows)))
    content_top, content_bottom, notes_top = rows[0], rows[split], rows[split + 1]
    above, below = content_top - deco_bottom, notes_top - content_bottom
    assert abs(above - below) <= 0.05 * (above + below), (above, below)
    if logo:
        logo_px = (np.abs(_pixels(last, "RGB") - np.array(LOGO_RGB)).max(axis=-1) <= 12).sum()
        assert logo_px > 1000  # logo 有畫出來

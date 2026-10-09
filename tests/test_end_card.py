"""片尾卡的內容：本片用到的資料來源彙整、頻道名沒設定時擋下。用 plan_build（不渲染，很快）看片尾卡的片段計畫。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from article_to_video.build import build_project, plan_build
from article_to_video.end_card import END_CARD_ID, merge_sources
from article_to_video.project import ProjectError

from conftest import MINI_PROJECT


def project_with_two_charts(tmp_path: Path) -> Path:
    root = tmp_path / "專案"
    shutil.copytree(MINI_PROJECT, root)
    shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
    sh1, sh2 = shots["shots"]
    sh1["chart"]["data"]["source"] = "來源：FRED（DGS2）"
    sh2["continues"] = False
    sh2["chart"] = {
        "data": {"title": "長天期比較低", "unit": "%", "source": "資料來源：FRED（DGS10）；作者整理",
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
    assert end_card(plan).spec["data"]["sources"] == ["FRED（DGS2、DGS10）", "作者整理"]


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


def test_without_a_channel_name_the_build_stops_and_says_how_to_set_it(tmp_path):
    root = tmp_path / "專案"
    shutil.copytree(MINI_PROJECT, root)
    snap = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    (root / "settings.json").write_text(json.dumps({**snap, "channel": ""}), encoding="utf-8")
    with pytest.raises(ProjectError) as err:
        build_project(root, tts="fake")
    assert "頻道名" in str(err.value) and "settings set --channel" in str(err.value)
    assert not (root / "build" / "16x9" / "clips").exists()  # 渲染前就擋下

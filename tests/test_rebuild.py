"""重建：只重渲改到的分鏡；拿掉的分鏡不留舊檔；什麼都沒改就不重做。

每個測試把 session 建好的專案（含 build/ 快取）複製一份再改，不動共用的那份。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from article_to_video.build import build_project


@pytest.fixture
def copy_of_built(built_project, tmp_path) -> Path:
    dst = tmp_path / "搬家後的專案"
    shutil.copytree(built_project.dir, dst)
    return dst


def edit_shots(root: Path, fn) -> None:
    shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
    fn(shots)
    (root / "shots.json").write_text(json.dumps(shots, ensure_ascii=False, indent=1), encoding="utf-8")


def test_unchanged_project_reuses_every_clip_and_the_final_video(copy_of_built):
    res = build_project(copy_of_built, tts="fake")
    assert res.rendered == []
    assert res.cached == ["SH01", "SH02", "end.card"]
    assert res.assembled is False
    assert res.video.is_file() and res.srt.is_file()
    assert (copy_of_built / "build" / "16x9" / "logs" / "assemble.log").is_file()  # 上次組裝的紀錄還在（回報問題要用）


def test_only_the_changed_shot_is_rendered_again(copy_of_built):
    def change(shots):
        shots["shots"][1]["chart"]["data"]["highlight"]["text"] = "長天期偏低"

    edit_shots(copy_of_built, change)
    res = build_project(copy_of_built, tts="fake")
    assert res.rendered == ["SH02"]
    assert res.cached == ["SH01", "end.card"]
    assert res.assembled is True


def test_changing_a_chart_source_also_updates_the_end_card(copy_of_built):
    """片尾卡列的是圖上的來源：改了來源，那個分鏡和片尾卡都要重渲。"""
    def change(shots):
        shots["shots"][0]["chart"]["data"]["source"] = "來源：FRED（DGS2、DGS10），月平均自算"

    edit_shots(copy_of_built, change)
    res = build_project(copy_of_built, tts="fake")
    assert sorted(res.rendered) == ["SH01", "SH02", "end.card"]  # SH02 接續 SH01，沿用它的來源列
    card = json.loads((copy_of_built / "build" / "16x9" / "specs" / "end.card.json").read_text(encoding="utf-8"))
    assert "FRED（DGS2、DGS10），月平均自算" in json.dumps(card, ensure_ascii=False)


def test_removed_shot_leaves_no_stale_outputs(copy_of_built):
    def drop(shots):
        del shots["shots"][1]

    edit_shots(copy_of_built, drop)
    res = build_project(copy_of_built, tts="fake")
    assert res.rendered == ["SH01"]  # 變長了（一路到片尾），要重渲
    vdir = copy_of_built / "build" / "16x9"
    leftovers = [p.name for d in ("specs", "clips", "logs") for p in (vdir / d).iterdir() if "SH02" in p.name]
    assert leftovers == []
    timeline = json.loads((vdir / "timeline.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in timeline["shots"]] == ["SH01"]


def test_turning_music_off_reassembles_without_rendering_clips(copy_of_built):
    """這支片不要配樂：片段全用快取，只重新組裝；成片裡（假配音層是靜音）就沒有聲音了。"""
    from test_build import audio_db

    snap = json.loads((copy_of_built / "settings.json").read_text(encoding="utf-8"))
    (copy_of_built / "settings.json").write_text(json.dumps({**snap, "music": "none"}), encoding="utf-8")
    res = build_project(copy_of_built, tts="fake")
    assert res.rendered == []
    assert res.assembled is True
    assert audio_db(res.video, 1.0, 3.0) < -80

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
    assert res.cached == ["SH01", "SH02"]
    assert res.assembled is False
    assert res.video.is_file() and res.srt.is_file()
    assert (copy_of_built / "build" / "16x9" / "logs" / "assemble.log").is_file()  # 上次組裝的紀錄還在（回報問題要用）


def test_only_the_changed_shot_is_rendered_again(copy_of_built):
    def change(shots):
        shots["shots"][1]["chart"]["data"]["highlight"]["text"] = "長天期偏低"

    edit_shots(copy_of_built, change)
    res = build_project(copy_of_built, tts="fake")
    assert res.rendered == ["SH02"]
    assert res.cached == ["SH01"]
    assert res.assembled is True


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

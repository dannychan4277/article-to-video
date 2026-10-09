"""品牌色：套到財經圖表的主色（柱、線、頂端色條與小旗、標註圓點、大數字）與片尾卡；沒設定時用預設紅黑白。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

from article_to_video.build import plan_build
from article_to_video.clips import render_stills
from article_to_video.end_card import END_CARD_ID
from article_to_video.ffmpeg import find_ffmpeg

from conftest import MINI_PROJECT
from test_build import frames_rgb

BRAND = (0x1A, 0x5F, 0xB4)  # #1A5FB4
ECONOMIST_RED = (0xE3, 0x12, 0x0B)


def project(tmp_path: Path, **settings) -> Path:
    root = tmp_path / "專案"
    shutil.copytree(MINI_PROJECT, root)
    snap = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    (root / "settings.json").write_text(json.dumps({**snap, **settings}), encoding="utf-8")
    return root


def png_rgb(path: Path) -> np.ndarray:
    raw = subprocess.run([str(find_ffmpeg()), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(1080, 1920, 3).astype(int)


def share_of(px: np.ndarray, rgb: tuple[int, int, int], tol: int = 12) -> float:
    return float((np.abs(px - np.array(rgb)).max(axis=-1) <= tol).mean())


def test_brand_color_paints_the_chart_and_the_end_card(tmp_path):
    plan = plan_build(project(tmp_path, brand_color="#1A5FB4"), tts="fake")
    chart = plan.clips[0]
    early, still = (png_rgb(p) for p in render_stills(chart.spec, tmp_path / "stills",
                                                      [1.0, chart.spec["duration"] - 0.05]).paths)
    assert share_of(early[0:3, 400:1800], BRAND) > 0.9  # 頂端色條（慢推會把它往畫外推，看前段）
    bars = still[300:900, 200:1700]
    assert share_of(bars, BRAND) > 0.03  # 柱子是品牌色
    assert share_of(still, ECONOMIST_RED, tol=30) < 0.0005  # 預設紅不見了
    [card] = [c for c in plan.clips if c.id == END_CARD_ID]
    assert card.spec["data"]["brand"] == "#1A5FB4"


def test_a_shot_can_still_pick_its_own_bar_color(tmp_path):
    root = project(tmp_path, brand_color="#1A5FB4")
    shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
    shots["shots"][0]["chart"]["data"]["colors"] = {"main": "#2E7D32"}
    (root / "shots.json").write_text(json.dumps(shots, ensure_ascii=False), encoding="utf-8")
    colors = plan_build(root, tts="fake").clips[0].spec["data"]["colors"]
    assert colors["main"] == "#2E7D32"
    assert colors["brand"] == "#1A5FB4"


def test_without_a_brand_color_charts_keep_the_default_red_black_white(built_project):
    """成片第 30 幀（1 秒）：頂端色條是預設紅、背景白、標題黑。"""
    [top] = frames_rgb(built_project.video, 30, 30, (400, 0, 1400, 4))
    px = np.frombuffer(top, dtype=np.uint8).reshape(-1, 3).astype(int)
    assert share_of(px, ECONOMIST_RED, tol=24) > 0.9
    [title] = frames_rgb(built_project.video, 30, 30, (90, 100, 900, 90))
    px = np.frombuffer(title, dtype=np.uint8).reshape(-1, 3).astype(int)
    assert share_of(px, (255, 255, 255), tol=10) > 0.5 and share_of(px, (12, 12, 12), tol=30) > 0.05

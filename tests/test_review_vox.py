"""審片頁上的 Vox 接續分鏡：一串接續的 Vox 分鏡共用一個片段（同一張桌面），但審片頁是一個分鏡一張卡。

試跑抓到的兩件事：
- 卡片的大圖（完成狀態）取在下一個分鏡切進來前一幀，那時鏡頭已經在往下一件素材移動，拍到的是空桌面或動態模糊。
- 「這版改過」照片段算：同一串只改一個分鏡，整串都標改過。

測試專案沿用 test_vox 的假文章（Pillow 畫的圖＋自寫的段落），只做預覽（靜態圖），小畫面（640×360）。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from article_to_video.build import plan_build
from article_to_video.review import ReviewResult, make_review

from test_vox import NUMBER_RECT, SMALL, new_project

CHAIN = [  # 一串四個 Vox 分鏡：原圖 → 框原圖 → 換剪報再框 → 索引卡
    {"id": "SH01", "start": {"segment": "s01"}, "vox": {"cues": [{"at": 0, "kind": "image", "image": "圖1"}]}},
    {"id": "SH02", "start": {"segment": "s02"}, "continues": True,
     "vox": {"cues": [{"at": {"segment": "s02", "word": "三點九六"}, "kind": "box", "rect": NUMBER_RECT}]}},
    {"id": "SH03", "start": {"segment": "s03"}, "continues": True,
     "vox": {"cues": [{"at": 0, "kind": "excerpt", "quote": "差距只剩"},
                      {"at": {"segment": "s03", "word": "零點六二"}, "kind": "box", "find": "0.62"}]}},
    {"id": "SH04", "start": {"segment": "s04"}, "continues": True,
     "vox": {"cues": [{"at": 0, "kind": "point", "text": "曲線倒掛"},
                      {"at": {"segment": "s04", "word": "長天期"}, "kind": "point", "text": "短天期比較高"}]}},
]
S04 = "曲線倒掛，代表短天期比長天期還高。"
"""第四段寫長一點：SH04 先一張索引卡、隔一秒多再一張（中間那張過程圖要拍到第一張卡，不是空桌面）。"""

DESK = np.array([228, 221, 207])
"""桌面（牛皮紙）的顏色（引擎 y2_vox 的 C.desk）。"""


def content_ratio(png: Path) -> float:
    """畫面中央那一半（鏡頭停住時素材在這裡）有多少像素不是桌面的顏色。空桌面＋紅線幾乎是 0；
    停在原圖、剪報、索引卡上至少好幾 %（試跑的 98 張靜態圖：空桌面 0～0.6%，停住的 2.4% 以上）。"""
    with Image.open(png) as im:
        a = np.asarray(im.convert("RGB")).astype(int)
    a = a[: SMALL.height - SMALL.safe_bottom]
    h, w, _ = a.shape
    center = a[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    return float((np.abs(center - DESK).max(axis=2) > 40).mean())


@dataclass(frozen=True)
class Chain:
    dir: Path
    review: ReviewResult


def preview(root: Path) -> ReviewResult:
    return make_review(plan_build(root, tts="fake", fmt=SMALL))


def chain_project(base: Path) -> Path:
    root = new_project(base, CHAIN)
    doc = json.loads((root / "narration.json").read_text(encoding="utf-8"))
    doc["segments"]["s04"] = S04
    (root / "narration.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def chain(tmp_path_factory) -> Chain:
    root = chain_project(tmp_path_factory.mktemp("Vox 接續"))
    return Chain(root, preview(root))


def test_the_chain_is_one_clip_with_one_card_per_shot(chain):
    plan = chain.review.plan
    assert [cp.shot_ids for cp in plan.clips if cp.grammar == "y2_vox"] == [["SH01", "SH02", "SH03", "SH04"]]
    assert sorted(chain.review.stills) == ["SH01", "SH02", "SH03", "SH04"]


def test_every_card_shows_the_material_the_camera_settled_on_not_an_empty_desk(chain):
    assert len(chain.review.stills["SH04"]) == 2  # 完成狀態＋第二張索引卡出現之前
    for sid, stills in chain.review.stills.items():
        main = stills[0]
        assert main.final
        assert content_ratio(main.path) > 0.02, f"{sid} 的大圖是空桌面：{main.path}（{main.video_time:.2f} 秒）"
        for s in stills[1:]:  # 過程圖也一樣：拍的是鏡頭移走之前停住的樣子
            assert content_ratio(s.path) > 0.02, f"{sid} 的過程圖是空桌面：{s.path}（{s.video_time:.2f} 秒）"


def test_every_card_still_lies_inside_its_own_shot(chain):
    plan = chain.review.plan
    fps = plan.timeline.fps
    for st in plan.timeline.shots:
        for s in chain.review.stills[st.shot.id]:
            assert st.start_frame / fps - 1e-6 <= s.video_time < st.end_frame / fps, (st.shot.id, s.video_time)

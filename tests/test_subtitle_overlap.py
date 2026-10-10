"""自動檢查的「字進字幕帶」在 Vox 片段上：只有字幕真的蓋得到的才算。

試跑抓到的誤報：鏡頭推近剪報時，剪報左下角的小標籤「原文段落」掉進畫面底部那一條（qa 只看高度），被判成必修；
可是字幕是置中的一個框，根本碰不到左下角。這裡用同一支 Vox 片段、兩種字幕位置驗：
- 字幕照平常放在底部置中：qa 說標籤進了字幕帶，但字幕框裡只有桌面 → 不算。
- 測試用的格式把字幕框放到畫面中間、剛好壓在剪報的字上 → 算，而且持續超過 1 秒是必修。

qa（引擎的 qa.py）換成假的：吐出寫死的框景結果（qa 本身的量測不是這裡要驗的；它只給「哪段時間、哪串字
掉進底部那一條」，沒有左右位置）。片段、字幕位置、判斷都是真的。財經圖表的字幕帶檢查不受影響（test_auto_checks）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from article_to_video import auto_checks
from article_to_video.auto_checks import run_checks
from article_to_video.build import build_project
from article_to_video.timeline import VideoFormat

from test_vox import SMALL, new_project

pytestmark = pytest.mark.usefixtures("internal_vox")
"""Vox 分鏡只在內部開關打開時讀得到（docs/adr/0001-只用財經圖表風格.md）；這個模組測的就是 Vox 本身。"""


HIGH_SUBS = VideoFormat(name="test360hi", width=640, height=360, fps=30, safe_bottom=47, sub_size=18,
                        sub_margin_v=224, sub_margin_lr=20, sub_pad=4)
"""跟 SMALL 一樣的畫面（同一份片段 spec），只有燒字幕的位置往上移到剪報第一行字上。"""

NARRATION = {"s01": "原文這樣寫。", "s02": "差距零點六二而已，曲線仍然倒掛。"}
SHOTS = [{"id": "SH01", "vox": {"cues": [{"at": 0, "kind": "excerpt", "quote": "差距只剩"}]}}]
WINDOW = (1.2, 3.6)
"""假 qa 說「原文段落」掉進字幕帶的時段（片段內秒數）：剪報貼好、鏡頭停住，字幕一直都在。"""

FAKE_QA = """import json, sys
from pathlib import Path
args = sys.argv[1:]
out = Path(args[args.index("--out") + 1])
a, b = json.loads(%r)
seg = {"id": out.name, "frames": 10, "deterministic": True, "det_maxdiff": 0, "motion_pct": 5.0, "motion_max": 9.0,
       "still_pairs_pct": 20.0, "spikes_at_lt": [],
       "framing": [{"kind": "进字幕带", "text": "原文段落", "lt": [a, b], "t": [a, b]}] if out.name == "SH01" else []}
(out / "qa.json").write_text(json.dumps({"segments": [seg], "page_errors": []}, ensure_ascii=False), encoding="utf-8")
"""


@pytest.fixture
def fake_qa(tmp_path, monkeypatch):
    script = tmp_path / "fake_qa.py"
    script.write_text(FAKE_QA % json.dumps(WINDOW), encoding="utf-8")
    monkeypatch.setattr(auto_checks, "QA_SCRIPT", script)
    return script


def checked(base: Path, fmt: VideoFormat) -> list:
    root = new_project(base, SHOTS)
    (root / "narration.json").write_text(json.dumps({"segments": NARRATION}, ensure_ascii=False), encoding="utf-8")
    res = build_project(root, tts="fake", fmt=fmt)
    return [f for f in run_checks(res.plan).findings if f.check == "text-in-band"]


def test_a_label_in_the_bottom_strip_that_the_subtitles_cannot_reach_is_not_flagged(tmp_path, fake_qa):
    assert checked(tmp_path, SMALL) == []


def test_text_right_under_the_drawn_subtitles_is_still_flagged(tmp_path, fake_qa):
    [f] = checked(tmp_path, HIGH_SUBS)
    assert f.status == "fail" and f.clip_id == "SH01"
    a, b = WINDOW
    assert all(a - 0.11 <= t0 < t1 <= b + 0.11 for t0, t1 in f.times)  # 只標字幕真的蓋到的時段
    assert max(t1 - t0 for t0, t1 in f.times) >= 1.0

"""只用財經圖表風格（docs/adr/0001-只用財經圖表風格.md）：作者的流程裡不會出現 Vox 剪報畫面。

切點：指令列（Claude 跑的 preview、build）。分鏡設定裡混進一個 Vox 分鏡 → 擋下來，繁中說明這個外掛現在只用財經圖表、
文章裡的圖要重畫成圖表。Vox 的程式還留著，只有內部開關（INTERNAL_VOX_ENV＝1，只給 Vox 模組自己的測試用）打開才讀得到。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from article_to_video.project import INTERNAL_VOX_ENV

from conftest import MINI_PROJECT, run_cli

VOX_SHOT = {"id": "SH03", "start": {"segment": "s02", "word": "倒掛"},
            "vox": {"cues": [{"at": 0, "kind": "image", "image": "圖1"}]}}


def project_with_a_vox_shot(tmp_path: Path) -> Path:
    root = tmp_path / "專案"
    shutil.copytree(MINI_PROJECT, root)
    shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
    shots["shots"].append(VOX_SHOT)
    (root / "shots.json").write_text(json.dumps(shots, ensure_ascii=False), encoding="utf-8")
    return root


@pytest.mark.parametrize("command", ["preview", "build"])
def test_a_stray_vox_shot_is_refused_in_plain_chinese(tmp_path, command):
    root = project_with_a_vox_shot(tmp_path)
    proc = run_cli(command, root, "--tts", "fake", "--no-open", unset=(INTERNAL_VOX_ENV,))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    assert "分鏡 SH03" in proc.stderr
    assert "只用財經圖表" in proc.stderr
    assert "重畫" in proc.stderr  # 告訴 Claude 怎麼辦：文章裡的圖重畫成圖表
    assert not (root / "成片.mp4").exists() and not (root / "審片頁.html").exists()

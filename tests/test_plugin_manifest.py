"""外掛殼：`/plugin marketplace add dannychan4277/article-to-video` 要讀得到的兩個檔。

完整驗證用 `claude plugin validate .`（要裝 Claude Code CLI）；這裡只守住彼此對得上的部分。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_marketplace_lists_this_repo_as_the_plugin():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "article-to-video"
    assert market["owner"]["name"]
    [entry] = market["plugins"]
    assert entry["name"] == plugin["name"]
    assert entry["source"] == "."


def test_every_pushed_commit_reaches_the_author_as_an_update():
    """plugin.json 寫了 version，作者就只在版號改變時拿得到更新；不寫，git marketplace 用 commit 當版本，
    每個推上去的 commit 都是一次更新（使用者故事 48：他按一下就能升級）。維護者怎麼發、作者怎麼升級寫在 docs/maintaining.md。"""
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    [entry] = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))["plugins"]
    assert "version" not in plugin and "version" not in entry
    doc = (ROOT / "docs" / "maintaining.md").read_text(encoding="utf-8")
    for step in ("/plugin marketplace update article-to-video", "Installed", "Update now", "--keep-data"):
        assert step in doc, step


def test_the_installed_commit_is_what_an_issue_report_calls_the_plugin_version(tmp_path):
    """從 marketplace 裝的外掛放在以版本（沒寫 version 時＝commit）命名的資料夾：回報問題記下它，維護者才切得到同一版。"""
    from article_to_video.environment import plugin_version

    installed = tmp_path / "cache" / "article-to-video" / "article-to-video" / "3f2a9c1b7d4e"
    installed.mkdir(parents=True)
    assert "3f2a9c1b7d4e" in plugin_version(installed)
    assert plugin_version(tmp_path / "article-to-video")  # 開發中的 repo：Python 套件的版本


def test_the_plugin_is_mit_and_the_embedded_engine_keeps_its_own_license():
    """外掛本身 MIT（版權人 Danny Chan）；內嵌引擎維持花叔的 MIT、字型維持 OFL，不能被外掛的授權蓋掉。"""
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["license"] == "MIT"
    ours = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert ours.startswith("MIT License") and "Copyright (c) 2026 Danny Chan" in ours
    engine = ROOT / "src" / "article_to_video" / "engine"
    assert "alchaincyf" in (engine / "LICENSE").read_text(encoding="utf-8")
    assert (engine / "lib" / "fonts" / "OFL.txt").is_file()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "授權待定" not in readme and "[`LICENSE`](LICENSE)" in readme

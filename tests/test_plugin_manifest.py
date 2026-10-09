"""外掛殼：`/plugin marketplace add dannychan4277/article-to-video` 要讀得到的兩個檔。

完整驗證用 `claude plugin validate .`（要裝 Claude Code CLI）；這裡只守住彼此對得上的部分。
"""

from __future__ import annotations

import json
import tomllib
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
    assert "version" not in entry  # 版號只放 plugin.json


def test_plugin_version_matches_python_package():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert plugin["version"] == project["project"]["version"]

"""檢查紀錄：<專案>/check_log.json。放獨立審片的每一輪結果，和每個問題的處理（改好了／接受不改）。
不在 build/ 裡，刪 build/ 不會掉；回報問題時打包。

    {"version": 1,
     "rounds": [獨立審片的每一輪（independent_review.py 寫，格式見那裡）],
     "resolutions": {
        "R1-2": {"status": "fixed", "note": "框改到下一列了", "by": "Claude", "at": "2026-10-09T14:03:11+0800"},
        "qa:SH03:jump@1a2b3c4d": {"status": "accepted", "note": "這裡本來就要一次跳出來", "by": "作者", "at": "…"}}}

status：fixed＝改好了（只用在獨立審片的問題；自動檢查的問題改了片段就會重查，不用標）、accepted＝明確接受不改、
reopened＝獨立審片下一輪說沒修好（又算沒處理）。代號：獨立審片 R<輪>-<第幾項>；自動檢查的代號見 auto_checks.py
（含片段雜湊前 8 碼：片段改了代號就變，舊的接受不會套到新的畫面上）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from article_to_video.project import ProjectPaths

__all__ = ["LOG_VERSION", "RESOLVED", "load_log", "now", "resolution", "save_log"]

LOG_VERSION = 1
RESOLVED = ("fixed", "accepted")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def load_log(project_dir: str | os.PathLike) -> dict[str, Any]:
    path = ProjectPaths(Path(project_dir)).check_log
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    rounds = raw.get("rounds") if isinstance(raw.get("rounds"), list) else []
    res = raw.get("resolutions") if isinstance(raw.get("resolutions"), dict) else {}
    return {"version": LOG_VERSION, "rounds": rounds, "resolutions": res}


def save_log(project_dir: str | os.PathLike, log: dict[str, Any]) -> None:
    path = ProjectPaths(Path(project_dir)).check_log
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def resolution(log: dict[str, Any], token: str) -> dict[str, Any] | None:
    """這個問題的處理紀錄（改好了或接受了）；沒處理、或被下一輪審片重新打開的是 None。"""
    r = log["resolutions"].get(token)
    return r if isinstance(r, dict) and r.get("status") in RESOLVED else None

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

__all__ = ["LOG_VERSION", "RESOLVED", "known_tokens", "load_log", "now", "record_resolutions", "resolution",
           "save_log"]

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


AUTO_PREFIXES = ("qa:", "band:", "hold:")


def known_tokens(project_dir: str | os.PathLike) -> dict[str, str]:
    """認得的代號 → 是什麼：獨立審片每一輪的每一項，加上各比例最近一次自動檢查（build/<比例>/checks.json）查到的。"""
    root = Path(project_dir)
    out: dict[str, str] = {}
    for rnd in load_log(root)["rounds"]:
        for it in rnd.get("items") or []:
            if isinstance(it, dict) and it.get("id"):
                out[str(it["id"])] = f"獨立審片第 {rnd.get('round')} 輪 {it.get('severity', '')}：{it.get('title', '')}"
    for path in sorted(ProjectPaths(root).build.glob("*/checks.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for f in data.get("findings") or []:
            if isinstance(f, dict) and f.get("token"):
                out.setdefault(str(f["token"]), f"自動檢查 {f.get('label', '')}：{f.get('message', '')}")
    return out


def record_resolutions(project_dir: str | os.PathLike, tokens: list[str], *, status: str, note: str,
                       by: str = "Claude") -> list[str]:
    """記下這幾個問題的處理（status：fixed＝改好了、accepted＝接受不改）。有一個代號不對就全部不記，丟 ProjectError。"""
    from article_to_video.project import ProjectError

    root = Path(project_dir)
    if not root.is_dir():
        raise ProjectError(f"找不到專案資料夾 {root}", title="記不了")
    problems: list[str] = []
    if not tokens:
        problems.append("沒有給代號（代號在 checks、build 的輸出或審片頁上，例 R1-2、qa:SH03:jump@1a2b3c4d）")
    if not note.strip():
        problems.append("要寫一句說明：--fixed 寫怎麼改的，--accept 寫為什麼不改")
    known = known_tokens(root)
    for t in tokens:
        if t not in known:
            problems.append(f"不認得的代號 {t}（重跑 checks 看現在的代號；分鏡改過的話代號會變）")
        elif status == "fixed" and t.startswith(AUTO_PREFIXES):
            problems.append(f"{t} 是自動檢查的問題，不用標改好：改了分鏡、重新 build 再跑 checks，就會重查")
    if problems:
        raise ProjectError(problems, title="沒有記下任何一項")
    log = load_log(root)
    for t in tokens:
        log["resolutions"][t] = {"status": status, "note": note.strip(), "by": by, "at": now()}
    save_log(root, log)
    return [known[t] for t in tokens]

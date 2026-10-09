"""用 macOS 的 open 打開東西給作者看：審片頁（預設瀏覽器）、測試片（QuickTime）、出片後在 Finder 指出成片、
安裝時開「終端機」跑 Homebrew 安裝腳本。打不開就回 False，由呼叫端說一句「請在 Finder 找到…」，不當成失敗。"""

from __future__ import annotations

import os
import subprocess

__all__ = ["open_path"]


def open_path(path: str | os.PathLike, *, reveal: bool = False, app: str | None = None, timeout: float = 20) -> bool:
    """open <path>；reveal＝在 Finder 裡選取它（open -R）；app＝用指定的程式打開（open -a <app>）。"""
    args = ["open", *(["-R"] if reveal else []), *(["-a", app] if app else []), str(path)]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0

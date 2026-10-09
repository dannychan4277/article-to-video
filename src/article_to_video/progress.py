"""進度訊息：花時間的步驟都收一個 log（一次一行繁中）。指令列把它印出來、RunRecord 記下來給回報問題用；
函式庫直接呼叫或測試時用 quiet（不印）。"""

from __future__ import annotations

from collections.abc import Callable

__all__ = ["Log", "quiet"]

Log = Callable[[str], None]


def quiet(_: str) -> None:
    """什麼都不印。"""

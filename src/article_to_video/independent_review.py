"""獨立審片：出片之後，Claude Code 另外派一個 agent，只看成片審一輪（Python 不派 agent，這裡只準備素材、記結果）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from article_to_video.check_log import load_log, resolution
from article_to_video.review_page import timecode

if TYPE_CHECKING:
    from article_to_video.build import BuildPlan

__all__ = ["unresolved_must"]


def unresolved_must(plan: BuildPlan) -> list[tuple[str, str]]:
    """每一輪獨立審片裡還沒處理（沒標改好、也沒接受）的必修：(代號, 一行說明)。"""
    book = load_log(plan.project.root)
    out: list[tuple[str, str]] = []
    for rnd in book["rounds"]:
        for it in rnd.get("items") or []:
            if it.get("severity") != "必修" or resolution(book, it["id"]) is not None:
                continue
            where = f"分鏡 {it['shot']}" if it.get("shot") else "整支片"
            if it.get("time") is not None:
                where += f"（{timecode(float(it['time']))}）"
            out.append((it["id"], f"{where} 獨立審片第 {rnd.get('round')} 輪：{it.get('title', '')}（代號 {it['id']}）"))
    return out

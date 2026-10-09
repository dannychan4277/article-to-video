"""品牌色套到財經圖表：設定快照有 brand_color 時，圖表的 data.colors 補上
  brand  作者的原色：頂端色條、小旗（裝飾用的色塊，不用讀）
  ink    同色相、對白底至少 MIN_CONTRAST 的版本：角標字、標註圓點、大數字（要看得清楚的字和記號）
  main   同 ink：柱、線
分鏡自己寫了 colors.main 就尊重它。沒設定品牌色時什麼都不改，引擎用預設的紅黑白
（紅 #E3120B 色條、黑字、白底；柱預設藍 #006BA2、線預設紅）。

品牌色一律收（作者真正的品牌色可能很淺，例如淡黃），可讀性由這裡處理：原色對白底不到 MIN_CONTRAST 時，
readable_on_white 把它加深到夠（色相、彩度不變，只降亮度），字、線、柱用加深的版本，色條和小旗還是原色。
設定的時候 brand_note 會印一句話告訴作者。

引擎那邊的 colors.brand、colors.ink 是 article-to-video 內嵌修改（engine/clips/t3_finance_chart.js，見 engine/NOTICE.md）。
片尾卡只拿品牌色畫色條和小旗（裝飾），用原色。
"""

from __future__ import annotations

import colorsys
from collections.abc import Mapping
from typing import Any

__all__ = ["DEFAULT_BRAND", "MIN_CONTRAST", "apply_brand", "brand_note", "contrast_on_white", "readable_on_white"]

DEFAULT_BRAND = "#E3120B"
MIN_CONTRAST = 3.0
"""對白底的最低對比：WCAG 的非文字圖形（柱、線、圓點）與大字（圖表上的大數字、角標都是粗體大字）門檻。"""


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def contrast_on_white(color: str) -> float:
    """#RRGGBB 對白色的對比（WCAG 2 相對亮度公式，1～21）。"""
    def lin(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in _rgb(color))
    return 1.05 / (0.2126 * r + 0.7152 * g + 0.0722 * b + 0.05)


def readable_on_white(color: str) -> str:
    """同色相加深到對白底至少 MIN_CONTRAST：本來就夠深就原樣回傳。"""
    if contrast_on_white(color) >= MIN_CONTRAST:
        return color
    h, lightness, s = colorsys.rgb_to_hls(*(c / 255 for c in _rgb(color)))
    while lightness > 0:
        lightness = max(0.0, lightness - 0.01)
        out = "#" + "".join(f"{round(c * 255):02X}" for c in colorsys.hls_to_rgb(h, lightness, s))
        if contrast_on_white(out) >= MIN_CONTRAST:
            return out
    return "#000000"


def brand_note(color: str | None) -> str | None:
    """設定品牌色時給作者的一句話：太淺的顏色在圖表上怎麼處理。夠深就不用說。"""
    if not color or contrast_on_white(color) >= MIN_CONTRAST:
        return None
    return (f"品牌色 {color} 比較淺：頂端色條和小旗用原色；圖表上的柱、線、大數字和角標會自動用加深的 "
            f"{readable_on_white(color)}，白底上才看得清楚。")


def apply_brand(data: Mapping[str, Any], brand_color: str | None) -> dict[str, Any]:
    out = dict(data)
    if not brand_color:
        return out
    ink = readable_on_white(brand_color)
    colors = dict(out.get("colors") or {})
    colors.setdefault("brand", brand_color)
    if ink != brand_color:  # 夠深的品牌色不加這個鍵：spec 跟以前一樣，舊片不會白白重渲
        colors.setdefault("ink", ink)
    colors.setdefault("main", ink)
    out["colors"] = colors
    return out

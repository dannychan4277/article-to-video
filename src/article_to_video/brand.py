"""品牌色套到財經圖表：設定快照有 brand_color 時，圖表的 data.colors 補上 brand（頂端色條、小旗、標註圓點、
大數字、角標）與 main（柱、線）。分鏡自己寫了 colors.main 就尊重它。沒設定品牌色時什麼都不改，引擎用預設的紅黑白
（紅 #E3120B 色條、黑字、白底；柱預設藍 #006BA2、線預設紅）。

引擎那邊的 colors.brand 是 article-to-video 內嵌修改（engine/clips/t3_finance_chart.js，見 engine/NOTICE.md）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["DEFAULT_BRAND", "apply_brand"]

DEFAULT_BRAND = "#E3120B"


def apply_brand(data: Mapping[str, Any], brand_color: str | None) -> dict[str, Any]:
    out = dict(data)
    if not brand_color:
        return out
    colors = dict(out.get("colors") or {})
    colors.setdefault("brand", brand_color)
    colors.setdefault("main", brand_color)
    out["colors"] = colors
    return out

"""讀文字：使用者給的（標準輸入：作者貼上的文章、審片留言、審片 agent 的回答；或檔案）一律照 UTF-8 讀；
我們自己寫的 JSON（快取戳記之類）用 read_json_or_none，壞了就當沒有。

sys.stdin.read() 會照這台電腦的語系設定解碼；作者的 Mac 語系不是 UTF-8 時，中文就變成亂碼，所以這裡一律
讀位元組再用 UTF-8 解（開頭的 BOM 拿掉）。不是 UTF-8 的內容丟 TextInputError（繁中，OSError 的子類：
呼叫端原本處理「讀不到檔」的地方一起接得到）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

__all__ = ["STDIN", "TextInputError", "read_json_or_none", "read_stdin", "read_text_input"]

STDIN = "-"
"""指令列寫「-」＝從標準輸入讀。"""


class TextInputError(OSError):
    """讀不到、或不是 UTF-8 的文字（繁中訊息）。"""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "讀不到文字"


def _decode(data: bytes, what: str) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise TextInputError(f"{what}不是 UTF-8 編碼的文字，看不懂。請另存成 UTF-8，或直接把文字貼給我。") from None


def read_stdin() -> str:
    """標準輸入的全部文字（UTF-8，不看這台電腦的語系設定）。"""
    return _decode(sys.stdin.buffer.read(), "貼上的內容")


def read_text_input(source: str | os.PathLike) -> str:
    """source 是「-」就讀標準輸入，不然讀那個檔；一律 UTF-8。讀不到丟 TextInputError。"""
    if str(source) == STDIN:
        return read_stdin()
    path = Path(source).expanduser()
    try:
        data = path.read_bytes()
    except OSError as e:
        raise TextInputError(f"讀不到 {path}（{e.strerror or e}）") from None
    return _decode(data, f"「{path.name}」")


def read_json_or_none(path: str | os.PathLike) -> Any | None:
    """讀我們自己寫的 JSON（快取戳記、核對紀錄、審片頁版本…）：檔案不在、讀不到、不是合法 JSON 都回 None，
    呼叫端當成「沒有」處理（重渲、重查）。使用者寫的設定檔不要用這個——那要報出哪一行壞了。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

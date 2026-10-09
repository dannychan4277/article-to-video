"""印給 Claude 照抄的外掛指令（下一步提示、審片 agent 的抽幀指令）：前綴只在這裡組。

skill 的 allowed-tools 預先核准的是這兩種寫法（${CLAUDE_PLUGIN_ROOT} 在規則裡會換成外掛的絕對路徑）：
    uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video <子指令> *
    ~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video <子指令> *
印出來的指令要一字不差對得上，Claude 照抄才不會被問權限、也才會在外掛的 uv 環境裡跑：
  - uv：PATH 上找得到就寫 uv；找不到但 ~/.local/bin/uv 在（官方安裝程式剛裝好、這次對話的 PATH 還沒更新），
    寫 ~/.local/bin/uv（字面上的 ~，跟 skill 的規則一樣）。
  - --project：外掛根目錄（這個套件往上兩層＝${CLAUDE_PLUGIN_ROOT}），不加引號（跟 skill 規則替換後一樣）。
  - 子指令後面的路徑加雙引號（中文、空白都安全）；--data-dir 有給才帶（主 skill 傳 ${CLAUDE_PLUGIN_DATA}）。
用到的子指令都要在 make-video、edit-video 的 allowed-tools 裡（tests/test_skills.py 會檢查）。
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

__all__ = ["LOCAL_UV", "MAINTAINER_PREFIX", "PLUGIN_ROOT", "aspect_args", "cli_command", "cli_prefix", "uv_command"]

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
LOCAL_UV = "~/.local/bin/uv"
MAINTAINER_PREFIX = "uv run --project <repo> python -m article_to_video"
"""維護者（Danny）在自己的 repo 裡跑的寫法：回報問題的 zip 裡的說明用。"""


def uv_command() -> str:
    """這台 Mac 上 Claude 叫得到的 uv：PATH 上有就是 uv，不然是剛裝好的 ~/.local/bin/uv。"""
    if shutil.which("uv"):
        return "uv"
    if (Path.home() / ".local" / "bin" / "uv").is_file():
        return LOCAL_UV
    return "uv"


def cli_prefix() -> str:
    """「uv run --project <外掛根目錄> python -m article_to_video」。"""
    return f"{uv_command()} run --project {shlex.quote(str(PLUGIN_ROOT))} python -m article_to_video"


def _arg(part: str | os.PathLike) -> str:
    return f'"{part}"' if isinstance(part, os.PathLike) else str(part)


def cli_command(sub: str, *parts: str | os.PathLike, data_dir: str | os.PathLike | None = None) -> str:
    """一行完整的指令：sub＝子指令（可以帶固定的字，例 "settings set"），parts 裡的 Path 加雙引號、字串照寫。"""
    words = [cli_prefix(), sub, *(_arg(p) for p in parts if str(p))]
    if data_dir is not None and str(data_dir) and "${" not in str(data_dir):  # 沒被替換的 ${CLAUDE_PLUGIN_DATA} 不帶
        words += ["--data-dir", _arg(Path(data_dir))]
    return " ".join(words)


def aspect_args(fmt_name: str) -> tuple[str, ...]:
    """橫式（預設）不用寫；直式加 --aspect 9:16（跟 skill 裡的寫法一樣）。"""
    return () if fmt_name == "16x9" else ("--aspect", fmt_name.replace("x", ":"))

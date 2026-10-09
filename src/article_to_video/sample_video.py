"""安裝測試片：安裝最後一步，用外掛自帶的範例專案渲一支約 10 秒的片並打開，確認整條管線（配音、圖表、配樂、
字幕、片尾卡）在這台 Mac 上跑得通。

範例專案在 sample_project/：兩句自己寫的旁白、一張 FRED DGS2（公有領域）年平均的柱狀圖（一個分鏡＋一個接續分鏡），
加一段自己寫的「文章」（article/text.txt）讓數字對帳對得上——測試片跟正式出片走同一道數字對帳。不是任何人的文章。
每次做都把範例的旁白稿、分鏡設定、設定快照蓋回去（快照只留 tail），所以會套用「現在」的使用者設定
（頻道名、聲音、配樂、品牌色）；build/ 留著，沒改的片段直接用快取。

預設放 ~/Movies/解說影片/安裝測試片（Finder 裡的「影片」），讓作者順便知道以後的片會出現在哪裡。
資料夾裡有一個「這是安裝測試片.txt」記號；不是空的、又沒有記號的資料夾一律不碰（不會蓋掉真的專案）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

__all__ = ["MARKER", "SAMPLE_PROJECT", "SampleVideoError", "default_test_video_dir", "open_file",
           "prepare_test_project"]

SAMPLE_PROJECT = Path(__file__).parent / "sample_project"
MARKER = "這是安裝測試片.txt"
MARKER_TEXT = "這個資料夾是安裝時做的測試片，用來確認做片的環境沒問題。看完可以整個刪掉。\n"


class SampleVideoError(RuntimeError):
    """測試片的資料夾不能用（繁中訊息）。"""


def default_test_video_dir() -> Path:
    from article_to_video.videos import videos_root  # 跟正式的片放在同一個「解說影片」資料夾

    return videos_root() / "安裝測試片"


def prepare_test_project(out_dir: str | os.PathLike | None = None) -> Path:
    """把範例專案放進 out_dir（預設 default_test_video_dir()），回傳那個資料夾。"""
    out = Path(out_dir).expanduser().absolute() if out_dir else default_test_video_dir()
    if out.exists() and not out.is_dir():
        raise SampleVideoError(f"{out} 是一個檔案，不是資料夾；測試片要放在資料夾裡")
    if out.is_dir() and any(out.iterdir()) and not (out / MARKER).is_file():
        raise SampleVideoError(f"{out} 裡已經有別的東西，不是測試片的資料夾，不會動它。請換一個空資料夾（--out）。")
    out.mkdir(parents=True, exist_ok=True)
    for f in SAMPLE_PROJECT.iterdir():
        if f.is_file():
            shutil.copyfile(f, out / f.name)
        elif f.is_dir():  # article/：範例的「文章」，數字對帳拿它當原文
            shutil.copytree(f, out / f.name, dirs_exist_ok=True)
    (out / MARKER).write_text(MARKER_TEXT, encoding="utf-8")
    return out


def open_file(path: str | os.PathLike) -> bool:
    """用 macOS 的 open 打開（.mp4 交給預設的播放程式，通常是 QuickTime Player）。打不開回 False。"""
    try:
        proc = subprocess.run(["open", str(path)], capture_output=True, text=True, encoding="utf-8", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0

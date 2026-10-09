"""找 ffmpeg：要的是能燒字幕（有 libass、subtitles 濾鏡）的那一支。

Homebrew 的 `ffmpeg` 沒編 libass；`ffmpeg-full` 有，但它是 keg-only，不會出現在 PATH 上。
所以不能只信 PATH，要把候選逐一拿來問 `-filters`，挑第一支有 subtitles 的。
"""

from __future__ import annotations

import functools
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

from article_to_video.homebrew import PREFIXES, keg_ffmpeg

__all__ = ["FFmpegNotFoundError", "default_ffmpeg_candidates", "find_ffmpeg"]

_KEG_FALLBACKS = tuple(keg_ffmpeg(prefix) for prefix in PREFIXES.values())
"""Apple Silicon、Intel 的 Homebrew 裡 ffmpeg-full 的位置（homebrew.py 是唯一記這兩個位置的地方）。"""


class FFmpegNotFoundError(RuntimeError):
    """找不到有 subtitles 濾鏡的 ffmpeg。"""


def default_ffmpeg_candidates() -> list[Path]:
    """依序：PATH 上的 ffmpeg、`<brew --prefix>/opt/ffmpeg-full/bin/ffmpeg`、兩個常見的 Homebrew 位置。"""
    found: list[Path] = []
    on_path = shutil.which("ffmpeg")
    if on_path:
        found.append(Path(on_path))
    brew = shutil.which("brew")
    if brew:
        try:
            prefix = subprocess.run(
                [brew, "--prefix"], capture_output=True, text=True, encoding="utf-8", timeout=15, check=True
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            prefix = ""
        if prefix:
            found.append(Path(prefix) / "opt" / "ffmpeg-full" / "bin" / "ffmpeg")
    found.extend(_KEG_FALLBACKS)

    unique: list[Path] = []
    seen: set[str] = set()
    for p in found:
        key = str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _why_unusable(ffmpeg: Path) -> str | None:
    """能用就回 None；不能用就回一句原因。"""
    if not ffmpeg.exists():
        return "找不到檔案"
    try:
        proc = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-filters"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return f"執行失敗（{e}）"
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "subtitles":
            return None
    return "沒有 subtitles 濾鏡（這版 ffmpeg 沒編 libass，燒不了字幕）"


def find_ffmpeg(candidates: Iterable[str | Path] | None = None) -> Path:
    """回傳第一支有 subtitles 濾鏡的 ffmpeg。都不行就丟 FFmpegNotFoundError（繁中說明＋修法）。

    candidates 不給就用 default_ffmpeg_candidates()，結果會快取。
    """
    if candidates is None:
        return _find_default()
    return _find(list(candidates))


@functools.cache
def _find_default() -> Path:
    return _find(default_ffmpeg_candidates())


def _find(candidates: list[str | Path]) -> Path:
    tried: list[str] = []
    for c in candidates:
        path = Path(c)
        reason = _why_unusable(path)
        if reason is None:
            return path
        tried.append(f"  - {path}：{reason}")
    checked = "\n".join(tried) if tried else "  （沒有任何候選）"
    raise FFmpegNotFoundError(
        "找不到能燒字幕的 ffmpeg（需要有 libass 的 subtitles 濾鏡）。\n"
        f"查過這些：\n{checked}\n"
        "修法：在終端機執行 `brew install ffmpeg-full`。裝好就行，不用改 PATH，外掛會自己找到它。"
    )

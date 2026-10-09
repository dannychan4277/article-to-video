"""環境檢查：這台電腦能不能做片（macOS、CPU、Python、ffmpeg＋字幕濾鏡、Playwright＋Chromium、edge-tts…）。

回報問題把結果打包進 zip；「檢查環境」那張票（#12）在這上面加項目和修法。

加一項檢查：
    @env_check("homebrew", "Homebrew")
    def check_homebrew() -> EnvCheck:
        ...
        return EnvCheck("homebrew", "Homebrew", ok=True, value="4.6.0", detail={...}, fix="")
    collect_environment([*DEFAULT_CHECKS, check_homebrew])

每項檢查自己丟例外也沒關係：collect_environment 會把它記成「檢查時出錯」，其他項照樣跑完。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_CHECKS",
    "EnvCheck",
    "EnvironmentReport",
    "collect_environment",
    "env_check",
]


@dataclass(frozen=True)
class EnvCheck:
    key: str
    """穩定的英文鍵（程式用）：macos、cpu、python、ffmpeg、playwright、chromium、edge-tts…"""
    name: str
    """給人看的名稱。"""
    ok: bool | None
    """True 能用、False 有問題、None 只是資訊（例如 macOS 版本）。"""
    value: str
    """一行摘要：版本、路徑或問題。"""
    detail: dict[str, Any] = field(default_factory=dict)
    fix: str = ""
    """修法（繁中）。#12 環境檢查補上。"""


Check = Callable[[], EnvCheck]


def env_check(key: str, name: str) -> Callable[[Check], Check]:
    """標記一個檢查函式的鍵與名稱，檢查本身丟例外時用來記錄是哪一項。"""

    def mark(fn: Check) -> Check:
        fn.env_key = key  # type: ignore[attr-defined]
        fn.env_name = name  # type: ignore[attr-defined]
        return fn

    return mark


@dataclass(frozen=True)
class EnvironmentReport:
    checks: list[EnvCheck]

    @property
    def ok(self) -> bool:
        return all(c.ok is not False for c in self.checks)

    def get(self, key: str) -> EnvCheck | None:
        return next((c for c in self.checks if c.key == key), None)

    def to_json(self) -> dict[str, Any]:
        return {"checks": [asdict(c) for c in self.checks]}

    def to_text(self) -> str:
        lines = []
        for c in self.checks:
            mark = {True: "✓", False: "✗", None: "・"}[c.ok]
            lines.append(f"{mark} {c.name}：{c.value}")
            if c.fix:
                lines.append(f"    修法：{c.fix}")
        return "\n".join(lines) + "\n"


def collect_environment(checks: Sequence[Check] | None = None) -> EnvironmentReport:
    out = []
    for fn in checks if checks is not None else DEFAULT_CHECKS:
        try:
            out.append(fn())
        except Exception as e:  # noqa: BLE001 — 一項壞掉不影響其他項
            key = getattr(fn, "env_key", getattr(fn, "__name__", "unknown"))
            out.append(EnvCheck(key, getattr(fn, "env_name", key), False, f"檢查時出錯：{e}"))
    return EnvironmentReport(out)


def _run(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


# ---------------------------------------------------------------- 各項檢查


@env_check("macos", "macOS")
def check_macos() -> EnvCheck:
    ver = platform.mac_ver()[0]
    if not ver:
        return EnvCheck("macos", "macOS", False, f"不是 macOS（{platform.platform()}）", fix="這個外掛只支援 Mac")
    return EnvCheck("macos", "macOS", None, ver, {"platform": platform.platform()})


@env_check("cpu", "CPU")
def check_cpu() -> EnvCheck:
    machine = platform.machine()
    kind = {"arm64": "Apple Silicon", "x86_64": "Intel"}.get(machine, "其他")
    # 用 Rosetta 跑的 x86 Python 在 Apple Silicon 上會回報 x86_64
    translated = _run(["sysctl", "-n", "sysctl.proc_translated"]).stdout.strip() == "1"
    detail = {"machine": machine, "cpus": os.cpu_count(), "rosetta": translated}
    return EnvCheck("cpu", "CPU", None, f"{machine}（{kind}{'，透過 Rosetta' if translated else ''}）", detail)


@env_check("python", "Python")
def check_python() -> EnvCheck:
    ver = platform.python_version()
    ok = sys.version_info >= (3, 11)
    return EnvCheck("python", "Python", ok, ver,
                    {"version": ver, "implementation": platform.python_implementation(), "executable": sys.executable},
                    fix="" if ok else "需要 Python 3.11 以上（用 uv 執行會自動帶對的版本）")


def _ffmpeg_info(path: Path) -> dict[str, Any]:
    from article_to_video.ffmpeg import FFmpegNotFoundError, find_ffmpeg

    info: dict[str, Any] = {"path": str(path), "exists": path.exists(), "version": None, "libass": None,
                            "subtitles": False}
    if not path.exists():
        return info
    try:
        out = _run([str(path), "-hide_banner", "-version"]).stdout
    except (OSError, subprocess.SubprocessError) as e:
        info["error"] = str(e)
        return info
    first = out.splitlines()[0] if out else ""
    parts = first.split()
    info["version"] = parts[2] if len(parts) >= 3 and parts[:2] == ["ffmpeg", "version"] else first
    info["libass"] = "--enable-libass" in out
    try:
        find_ffmpeg([path])
        info["subtitles"] = True
    except FFmpegNotFoundError:
        info["subtitles"] = False
    return info


@env_check("ffmpeg", "ffmpeg（要能燒字幕）")
def check_ffmpeg() -> EnvCheck:
    from article_to_video.ffmpeg import default_ffmpeg_candidates

    cands = [_ffmpeg_info(p) for p in default_ffmpeg_candidates()]
    chosen = next((c for c in cands if c["subtitles"]), None)
    detail = {"chosen": chosen["path"] if chosen else None, "candidates": cands}
    if chosen is None:
        return EnvCheck("ffmpeg", "ffmpeg（要能燒字幕）", False, "找不到有 subtitles 濾鏡的 ffmpeg", detail,
                        fix="在終端機執行 `brew install ffmpeg-full`")
    return EnvCheck("ffmpeg", "ffmpeg（要能燒字幕）", True, f"{chosen['version']}（{chosen['path']}）", detail)


@env_check("playwright", "Playwright")
def check_playwright() -> EnvCheck:
    ver = _version("playwright")
    return EnvCheck("playwright", "Playwright", ver is not None, ver or "沒有安裝", {"version": ver})


@env_check("chromium", "Chromium（Playwright 用的瀏覽器）")
def check_chromium() -> EnvCheck:
    """真的開一次無頭 Chromium 再關掉（約一秒）：檔案在不在、能不能啟動，一次看清楚。"""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    name = "Chromium（Playwright 用的瀏覽器）"
    detail: dict[str, Any] = {}
    try:
        with sync_playwright() as p:
            exe = Path(p.chromium.executable_path)
            detail["executable"] = str(exe)
            detail["installed"] = exe.exists()
            browser = p.chromium.launch(timeout=30_000)
            try:
                version = browser.version
            finally:
                browser.close()
    except PlaywrightError as e:
        msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        return EnvCheck("chromium", name, False, f"開不起來：{msg}", detail,
                        fix="在外掛資料夾執行 `uv run playwright install chromium`")
    detail["version"] = version
    return EnvCheck("chromium", name, True, version, detail)


@env_check("edge-tts", "edge-tts（配音）")
def check_edge_tts() -> EnvCheck:
    ver = _version("edge-tts")
    return EnvCheck("edge-tts", "edge-tts（配音）", ver is not None, ver or "沒有安裝", {"version": ver})


@env_check("plugin", "外掛版本")
def check_plugin() -> EnvCheck:
    ver = _version("article-to-video") or "unknown"
    return EnvCheck("plugin", "外掛版本", None, ver, {"version": ver})


@env_check("uv", "uv")
def check_uv() -> EnvCheck:
    exe = shutil.which("uv")
    if exe is None:
        return EnvCheck("uv", "uv", None, "不在 PATH 上", {"path": None})
    ver = _run([exe, "--version"]).stdout.strip()
    return EnvCheck("uv", "uv", True, ver, {"path": exe, "version": ver})


@env_check("disk", "硬碟剩餘空間")
def check_disk() -> EnvCheck:
    free = shutil.disk_usage(Path.home()).free / 1e9
    ok = free >= 2
    return EnvCheck("disk", "硬碟剩餘空間", ok, f"{free:.1f} GB", {"freeGB": round(free, 1)},
                    fix="" if ok else "清出至少 2 GB（片段和成片都要寫到硬碟）")


DEFAULT_CHECKS: tuple[Check, ...] = (
    check_macos, check_cpu, check_python, check_plugin, check_ffmpeg, check_playwright, check_chromium,
    check_edge_tts, check_uv, check_disk,
)

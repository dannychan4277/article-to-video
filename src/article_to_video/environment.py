"""環境檢查：這台電腦能不能做片（macOS、CPU、Python、Homebrew、ffmpeg＋字幕濾鏡、Playwright＋Chromium、
edge-tts 與它的連線、uv、附帶字型、硬碟、頻道設定）。

兩個地方用：
- 「檢查環境」（`check-env` 指令，#12）：collect_environment(environment_checks(資料位置))，逐項印 ✓／✗／・＋修法。
- 回報問題（#14）：collect_environment()＝DEFAULT_CHECKS，打包進 zip（不含頻道設定：打包時不知道資料位置）。
安裝流程（installer.py）也拿這裡的檢查判斷「已經有了，略過」。

修法寫給作者看：口語、說「跟 Claude 說『安裝』」這類他做得到的事，技術指令放括號裡給 Danny 看。
每項檢查都不改系統（只讀、開關一次無頭 Chromium、edge-tts 試唸兩個字）。

加一項檢查（寫好加進 DEFAULT_CHECKS）：
    @env_check("git", "git")
    def check_git() -> EnvCheck:
        ...
        return EnvCheck("git", "git", ok=True, value="2.50.1", detail={...}, fix="")  # ok=False 時 fix 必填
    collect_environment([*DEFAULT_CHECKS, check_git])

每項檢查自己丟例外也沒關係：collect_environment 會把它記成「檢查時出錯」，其他項照樣跑完。
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from article_to_video.homebrew import BrewLayout

__all__ = [
    "DEFAULT_CHECKS",
    "EDGE_PROXY_ENV",
    "EnvCheck",
    "EnvironmentReport",
    "check_chromium",
    "check_edge_tts_online",
    "check_ffmpeg",
    "check_homebrew",
    "check_uv",
    "collect_environment",
    "env_check",
    "environment_checks",
    "settings_check",
]

EDGE_PROXY_ENV = "ARTICLE_TO_VIDEO_EDGE_TTS_PROXY"
"""edge-tts 連線檢查走的 HTTP proxy（測試時 conftest 指到 127.0.0.1 的死埠＝保證不連網；平常不用設）。"""
EDGE_PROBE_TEXT = "測試"
EDGE_PROBE_VOICE = "zh-TW-HsiaoChenNeural"

SAY_INSTALL = "跟 Claude 說「安裝」，它會幫你補裝"
SAY_REINSTALL = "外掛的 Python 環境不完整：跟 Claude 說「安裝」重新整理一次；還是不行就說「回報問題」傳給 Danny"
SAY_UPDATE_PLUGIN = ("外掛檔案不完整：在 Claude 輸入 /plugin，到 Installed 找到 article-to-video，按 Update now 重新下載外掛"
                     "（設定存在另外的地方，更新不會動到）。不要解除安裝：解除安裝會把頻道名、logo 這些設定一起刪掉。"
                     "更新完還是不行，說「回報問題」傳給 Danny")
"""外掛自己的檔案壞了的修法。設定在 ${CLAUDE_PLUGIN_DATA}：更新外掛不會動到，但最後一個 scope 解除安裝時會被刪掉
（除非 claude plugin uninstall 加 --keep-data，那是維護者的事，不叫作者打指令）。"""


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
            if c.fix and c.ok is not True:
                lines.append(f"    修法：{c.fix}")
        return "\n".join(lines) + "\n"

    def summary(self) -> str:
        problems = [c for c in self.checks if c.ok is False]
        if not problems:
            return "全部正常，可以開始做片。"
        names = "、".join(c.name for c in problems)
        return (f"有 {len(problems)} 項要處理：{names}。照上面每一項的「修法」做；"
                "工具沒裝好的，跟 Claude 說「安裝」就會補裝。")


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
                        fix=f"{SAY_INSTALL}（會跑 brew install ffmpeg-full，要先有 Homebrew）")
    return EnvCheck("ffmpeg", "ffmpeg（要能燒字幕）", True, f"{chosen['version']}（{chosen['path']}）", detail)


@env_check("playwright", "Playwright")
def check_playwright() -> EnvCheck:
    ver = _version("playwright")
    return EnvCheck("playwright", "Playwright", ver is not None, ver or "沒有安裝", {"version": ver},
                    fix="" if ver else SAY_REINSTALL)


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
                        fix=f"{SAY_INSTALL}（會跑 playwright install chromium，下載約 150 MB）")
    detail["version"] = version
    return EnvCheck("chromium", name, True, version, detail)


@env_check("edge-tts", "edge-tts（配音）")
def check_edge_tts() -> EnvCheck:
    ver = _version("edge-tts")
    return EnvCheck("edge-tts", "edge-tts（配音）", ver is not None, ver or "沒有安裝", {"version": ver},
                    fix="" if ver else SAY_REINSTALL)


@env_check("plugin", "外掛版本")
def check_plugin() -> EnvCheck:
    ver = _version("article-to-video") or "unknown"
    return EnvCheck("plugin", "外掛版本", None, ver, {"version": ver})


def _uv_candidates() -> list[str]:
    """uv run 會把自己的路徑放在環境變數 UV；剛用官方安裝程式裝好、PATH 還沒更新時在 ~/.local/bin。"""
    found = [os.environ.get("UV"), shutil.which("uv"), str(Path.home() / ".local" / "bin" / "uv"),
             "/opt/homebrew/bin/uv", "/usr/local/bin/uv"]
    out: list[str] = []
    for c in found:
        if c and c not in out:
            out.append(c)
    return out


@env_check("uv", "uv")
def check_uv() -> EnvCheck:
    for exe in _uv_candidates():
        if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
            continue
        try:
            ver = _run([exe, "--version"]).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if ver:
            return EnvCheck("uv", "uv", True, f"{ver}（{exe}）",
                            {"path": exe, "version": ver, "onPath": shutil.which("uv") is not None})
    return EnvCheck("uv", "uv", False, "找不到", {"path": None},
                    fix=f"{SAY_INSTALL}（官方安裝程式，不用密碼）")


@env_check("disk", "硬碟剩餘空間")
def check_disk() -> EnvCheck:
    free = shutil.disk_usage(Path.home()).free / 1e9
    ok = free >= 2
    return EnvCheck("disk", "硬碟剩餘空間", ok, f"{free:.1f} GB", {"freeGB": round(free, 1)},
                    fix="" if ok else "清出至少 2 GB（片段和成片都要寫到硬碟）")


@env_check("homebrew", "Homebrew（用來裝 ffmpeg）")
def check_homebrew(layout: BrewLayout | None = None) -> EnvCheck:
    from article_to_video.homebrew import default_layout, find_brew

    name = "Homebrew（用來裝 ffmpeg）"
    layout = layout or default_layout()
    info = find_brew(layout)
    detail: dict[str, Any] = {"arch": layout.arch, "expectedPrefix": str(layout.prefix)}
    if info is None:
        return EnvCheck("homebrew", name, False, f"沒有安裝（這台是 {layout.label}，應該在 {layout.prefix}）", detail,
                        fix=f"{SAY_INSTALL}（會打開「終端機」，請你輸入一次登入 Mac 的密碼）")
    detail.update(path=str(info.path), version=info.version, prefix=str(info.prefix))
    note = "" if info.prefix == layout.prefix else "（以前用 Rosetta 裝的 Intel 版，可以用）"
    return EnvCheck("homebrew", name, True, f"{info.version}（{info.path}）{note}", detail)


@env_check("edge-tts-online", "edge-tts 連線（配音要連網）")
def check_edge_tts_online(timeout: float = 20.0) -> EnvCheck:
    """真的請 edge-tts 用曉臻唸「測試」兩個字（約一秒），有收到聲音才算連得上。"""
    import edge_tts

    name = "edge-tts 連線（配音要連網）"
    proxy = os.environ.get(EDGE_PROXY_ENV) or None
    detail: dict[str, Any] = {"voice": EDGE_PROBE_VOICE, "proxy": proxy}

    async def probe() -> int:
        com = edge_tts.Communicate(EDGE_PROBE_TEXT, EDGE_PROBE_VOICE, proxy=proxy, connect_timeout=10,
                                   receive_timeout=15)
        got = 0
        async for chunk in com.stream():
            if chunk.get("type") == "audio":
                got += len(chunk.get("data") or b"")
        return got

    t0 = time.monotonic()
    try:
        got = asyncio.run(asyncio.wait_for(probe(), timeout))
    except Exception as e:  # noqa: BLE001 — 網路、服務端、逾時，一律當「連不上」
        msg = (str(e).strip().splitlines() or [""])[0]
        detail["error"] = f"{type(e).__name__}: {msg}"
        return EnvCheck("edge-tts-online", name, False, f"連不上（{type(e).__name__}）", detail,
                        fix="edge-tts 要連網才能配音：先確認 Wi-Fi 有連上、Safari 打得開網頁，再說「檢查環境」重試。"
                            "網路正常還是連不上，可能是微軟改了服務，說「回報問題」傳給 Danny")
    secs = time.monotonic() - t0
    detail.update(seconds=round(secs, 2), bytes=got)
    if got == 0:
        return EnvCheck("edge-tts-online", name, False, "連上了，但沒有回傳聲音", detail,
                        fix="可能是微軟改了服務，說「回報問題」傳給 Danny")
    return EnvCheck("edge-tts-online", name, True, f"連得上（曉臻試唸「{EDGE_PROBE_TEXT}」{secs:.1f} 秒回來）", detail)


@env_check("fonts", "字型（外掛附帶）")
def check_fonts() -> EnvCheck:
    """字幕與圖表的字型隨外掛附帶（作者的 Mac 沒有 Noto Sans CJK TC），檔案要在。"""
    from article_to_video.clips import FONTS_DIR
    from article_to_video.subtitles import SUBTITLE_FONT_FILE

    name = "字型（外掛附帶）"
    woffs = sorted(p.name for p in FONTS_DIR.glob("*.woff"))
    detail = {"dir": str(FONTS_DIR), "files": woffs, "subtitle": SUBTITLE_FONT_FILE.name}
    if not SUBTITLE_FONT_FILE.is_file() or not woffs:
        return EnvCheck("fonts", name, False, f"少了字型檔（{SUBTITLE_FONT_FILE.name}）", detail,
                        fix=SAY_UPDATE_PLUGIN)
    return EnvCheck("fonts", name, True, f"{len(woffs)} 個字型檔，字幕用 {SUBTITLE_FONT_FILE.name}", detail)


def settings_check(data_dir: str | os.PathLike | None = None) -> Check:
    """頻道設定（使用者設定）：沒有頻道名就做不了片（片尾卡要用）。資料位置要由呼叫的人給（${CLAUDE_PLUGIN_DATA}）。"""

    @env_check("settings", "頻道設定")
    def check_settings() -> EnvCheck:
        from article_to_video.music import TRACKS
        from article_to_video.project import ZH_TW_VOICES
        from article_to_video.user_settings import load_user_settings, resolve_data_dir

        us = load_user_settings(data_dir)
        detail = {"dataDir": str(resolve_data_dir(data_dir)), **us.to_json()}
        music = "不要配樂" if us.music == "none" else TRACKS[us.music].label if us.music in TRACKS else us.music
        summary = (f"聲音 {ZH_TW_VOICES.get(us.voice, us.voice)}｜配樂 {music}｜"
                   f"品牌色 {us.brand_color or '預設紅黑白'}｜logo {'有' if us.logo else '沒有'}")
        if not us.channel:
            return EnvCheck("settings", "頻道設定", False, f"還沒設定頻道名（片尾卡要用）｜{summary}", detail,
                            fix="跟 Claude 說「設定」，它會問你頻道名、logo、品牌色，順便讓你挑聲音和配樂")
        return EnvCheck("settings", "頻道設定", True, f"{us.channel}｜{summary}", detail)

    return check_settings


DEFAULT_CHECKS: tuple[Check, ...] = (
    check_macos, check_cpu, check_python, check_plugin, check_homebrew, check_ffmpeg, check_playwright,
    check_chromium, check_edge_tts, check_edge_tts_online, check_uv, check_fonts, check_disk,
)
"""回報問題打包的那一份（不知道資料位置，所以不含頻道設定）。"""


def environment_checks(data_dir: str | os.PathLike | None = None) -> tuple[Check, ...]:
    """「檢查環境」的完整清單：DEFAULT_CHECKS＋頻道設定。"""
    return (*DEFAULT_CHECKS, settings_check(data_dir))

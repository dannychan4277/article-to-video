"""支援的 Mac：Apple Silicon＋macOS 15 以上（docs/adr/0002-只支援-Apple-Silicon-macOS-15.md）。

ffmpeg-full 和它的上百個依賴，Homebrew 只給 Apple Silicon 的 macOS 15 以上做了現成安裝包（bottle）。
Intel Mac、macOS 14 以前的 Mac 上 `brew install ffmpeg-full` 不會報錯，而是默默改成從原始碼編譯：
八十幾個套件、好幾個小時、常常失敗——對不熟電腦的作者是最糟的陷阱。所以「安裝」的第一步（還沒裝 Homebrew、
什麼都還沒動）和「檢查環境」的第一項都先看這台 Mac；不支援就用白話說清楚、停下來。

看的是硬體（homebrew.machine_arch：Rosetta 下的 x86_64 Python 也認得出 Apple Silicon）和 `sw_vers` 的 macOS 版本。
這是靜態規則；Homebrew 的支援範圍以後會往前移，裝 ffmpeg-full 之前另外問 Homebrew 這台拿不拿得到現成安裝包
（homebrew.check_bottles）。
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass

from article_to_video.homebrew import ARCH_LABELS, Runner, machine_arch

__all__ = ["MIN_MACOS", "MacPlatform", "PlatformVerdict", "assess", "detect_platform"]

MIN_MACOS = 15
"""最低支援的 macOS 主版本（Sequoia）。"""


@dataclass(frozen=True)
class MacPlatform:
    arch: str
    """硬體架構："arm64"（Apple Silicon）或 "x86_64"（Intel）。"""
    macos: str
    """macOS 版本，例如 "26.2"；看不出來（不是 macOS）就是空字串。"""

    @property
    def major(self) -> int | None:
        head = self.macos.split(".")[0]
        if not head.isdigit():
            return None
        # macOS 11 用舊 SDK 編的程式看到的版本是 10.16（Homebrew 也這樣換算）
        return 11 if self.macos.startswith("10.16") else int(head)

    @property
    def supported(self) -> bool:
        return self.arch == "arm64" and self.major is not None and self.major >= MIN_MACOS

    @property
    def label(self) -> str:
        """給人看的：「Apple Silicon、macOS 26.2」。"""
        return f"{ARCH_LABELS.get(self.arch, self.arch)}、macOS {self.macos or '（看不出版本）'}"


@dataclass(frozen=True)
class PlatformVerdict:
    ok: bool
    summary: str
    """一行：支援的話是這台 Mac 是什麼；不支援的話是哪裡不對、為什麼。"""
    fix: str = ""
    """不支援時：怎麼辦，和怎麼自己確認（蘋果選單 →「關於這台 Mac」）。"""


WHY = (f"做影片要用的 ffmpeg（和它要用的上百個套件）只有給 Apple Silicon、macOS {MIN_MACOS} 以上的現成安裝包，"
       "其他 Mac 得在自己的電腦上從頭編譯好幾個小時，而且常常失敗")
ABOUT_THIS_MAC = "點螢幕左上角的蘋果選單 →「關於這台 Mac」"


def assess(p: MacPlatform) -> PlatformVerdict:
    """這台 Mac 支不支援；不支援就附上給作者看的白話說明（作者不熟電腦，Claude 會照這段轉述）。"""
    if p.supported:
        return PlatformVerdict(True, p.label)
    if p.major is None:
        return PlatformVerdict(
            False, "這不是 Mac（或看不出 macOS 版本），這個外掛只能在 Mac 上用",
            fix=f"這個外掛只支援 Apple Silicon、macOS {MIN_MACOS} 以上的 Mac。這明明是 Mac 的話，請說「回報問題」傳給 Danny。",
        )
    if p.arch != "arm64":
        return PlatformVerdict(
            False, f"這台是 Intel 晶片的 Mac（macOS {p.macos}），這個外掛裝不起來：{WHY}",
            fix=f"這台 Mac 沒辦法簡單裝好這個外掛，請聯絡 Danny。想自己確認：{ABOUT_THIS_MAC}，"
                "「晶片」那一行寫 Apple M1、M2、M3 這類才是 Apple Silicon；寫的是「處理器」、後面有 Intel，就是 Intel Mac。",
        )
    # macOS 13 以後叫「系統設定」，12 以前叫「系統偏好設定」
    where = "「系統設定」→「一般」→「軟體更新」" if p.major >= 13 else "「系統偏好設定」→「軟體更新」"
    return PlatformVerdict(
        False, f"這台 Mac 的系統是 macOS {p.macos}，太舊了，這個外掛要 macOS {MIN_MACOS} 以上：{WHY}",
        fix=f"先把 macOS 更新到 {MIN_MACOS} 以上：點螢幕左上角的蘋果選單 →{where}，照畫面更新"
            "（要一段時間，記得接上電源）。更新完再跟 Claude 說「安裝」。"
            f"想自己確認現在的版本：{ABOUT_THIS_MAC}，看「macOS」那一行。更新不了的話，請聯絡 Danny。",
    )


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)


def detect_platform(run: Runner = _run, machine: str | None = None) -> MacPlatform:
    """這台 Mac 的硬體架構和 macOS 版本。run／machine 給測試換成假的。"""
    arch = machine_arch(run, machine)
    version = ""
    try:
        proc = run(["sw_vers", "-productVersion"])
        if proc.returncode == 0:
            version = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return MacPlatform(arch, version or platform.mac_ver()[0])

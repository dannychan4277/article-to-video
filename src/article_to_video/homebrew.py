"""Homebrew 在哪裡：Apple Silicon 裝在 /opt/homebrew，Intel 裝在 /usr/local（Homebrew 官方只支援這兩個位置）。

看的是硬體，不是這個 Python 的架構：Apple Silicon 上用 Rosetta 跑的 x86_64 Python，platform.machine()
會說 x86_64，但這台 Mac 的 Homebrew 該在 /opt/homebrew。以前用 Rosetta 裝在 /usr/local 的舊 Homebrew 也認得，
不會再裝一套。

測試把位置整個搬到 tmp：BrewLayout.for_arch(arch, root=tmp_path)。
"""

from __future__ import annotations

import json
import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "FFMPEG_FORMULA",
    "INSTALL_SCRIPT_URL",
    "PREFIXES",
    "BottleCheck",
    "BrewInfo",
    "BrewLayout",
    "bottle_fits",
    "check_bottles",
    "default_layout",
    "find_brew",
    "machine_arch",
]

PREFIXES = {"arm64": Path("/opt/homebrew"), "x86_64": Path("/usr/local")}
ARCH_LABELS = {"arm64": "Apple Silicon", "x86_64": "Intel"}
INSTALL_SCRIPT_URL = "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
"""Homebrew 官方安裝程式（brew.sh 首頁那一行用的就是它）。"""
FFMPEG_FORMULA = "ffmpeg-full"
"""有 libass（subtitles 濾鏡）的 ffmpeg；keg-only，不會出現在 PATH 上，ffmpeg.find_ffmpeg() 會自己找到。"""

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)


@dataclass(frozen=True)
class BrewLayout:
    arch: str
    """"arm64"（Apple Silicon）或 "x86_64"（Intel）。"""
    prefix: Path
    root: Path = Path("/")

    @classmethod
    def for_arch(cls, arch: str, root: str | Path = "/") -> BrewLayout:
        if arch not in PREFIXES:
            raise ValueError(f"不認得的架構：{arch}（只支援 arm64、x86_64）")
        root = Path(root)
        return cls(arch, root / PREFIXES[arch].relative_to("/"), root)

    @property
    def label(self) -> str:
        return ARCH_LABELS[self.arch]

    @property
    def brew(self) -> Path:
        return self.prefix / "bin" / "brew"

    @property
    def ffmpeg_full(self) -> Path:
        return keg_ffmpeg(self.prefix)

    def arch_of(self, info: BrewInfo) -> str:
        """找到的這支 brew 是哪個架構的 Homebrew（Apple Silicon 上 /usr/local 那套是 Rosetta 跑的 Intel 版）。"""
        return self.arch if info.prefix == self.prefix else ("x86_64" if self.arch == "arm64" else "arm64")

    def brew_candidates(self) -> list[Path]:
        """先找這台 Mac 該在的位置，再找另一個（Apple Silicon 上 Rosetta 裝的舊 Homebrew 在 /usr/local）。"""
        other = "x86_64" if self.arch == "arm64" else "arm64"
        return [self.brew, BrewLayout.for_arch(other, self.root).brew]


def keg_ffmpeg(prefix: Path) -> Path:
    return prefix / "opt" / FFMPEG_FORMULA / "bin" / "ffmpeg"


def machine_arch(run: Runner = _run, machine: str | None = None) -> str:
    """這台 Mac 的硬體架構："arm64"（Apple Silicon，就算這個 Python 是透過 Rosetta 跑的）或 "x86_64"（Intel）。"""
    try:
        proc = run(["sysctl", "-n", "hw.optional.arm64"])
        if proc.returncode == 0 and proc.stdout.strip() == "1":
            return "arm64"
    except (OSError, subprocess.SubprocessError):
        pass
    m = machine if machine is not None else platform.machine()
    return "arm64" if m == "arm64" else "x86_64"


def default_layout(arch: str | None = None) -> BrewLayout:
    return BrewLayout.for_arch(arch or machine_arch())


@dataclass(frozen=True)
class BrewInfo:
    path: Path
    version: str
    """`brew --version` 第一行，例如 "Homebrew 4.6.0"。"""

    @property
    def prefix(self) -> Path:
        return self.path.parent.parent


MACOS_TAG_VERSIONS = {"golden_gate": 27, "tahoe": 26, "sequoia": 15, "sonoma": 14, "ventura": 13, "monterey": 12,
                      "big_sur": 11, "catalina": 10, "mojave": 10, "high_sierra": 10}
"""Homebrew 現成安裝包（bottle）標籤裡的 macOS 名字 → 主版本（Homebrew 的 macos_version.rb）。"""


@dataclass(frozen=True)
class BottleCheck:
    """裝一個 formula 之前問 Homebrew：要下載的套件，這台 Mac 有沒有現成安裝包（沒有＝brew 會從原始碼編）。"""

    looked_at: int = 0
    """看了幾個套件（formula 本身＋它的依賴）。"""
    from_source: tuple[str, ...] = ()
    """要裝（沒裝或版本舊了）、卻沒有這台 Mac 能用的現成安裝包的套件。"""
    error: str = ""
    """查不了的原因（離線、brew 出錯）；空字串＝查到了。"""

    @property
    def known(self) -> bool:
        return not self.error


def bottle_fits(tag: str, arch: str, macos_major: int | None) -> bool:
    """這個現成安裝包標籤（arm64_tahoe、sequoia、all…）這台 Mac 用不用得上。

    Homebrew 7 的 brew info 只列「這台 Mac 會用的那一個」（已經照架構和版本挑好），這裡只是保險：
    沒走 API 的 Homebrew 會列出全部標籤。舊版 macOS 的安裝包新版可以用；不認得的名字（以後的新 macOS）就信它。"""
    if tag == "all":
        return True
    if tag.endswith("_linux"):
        return False
    tag_arch, name = ("arm64", tag[len("arm64_"):]) if tag.startswith("arm64_") else ("x86_64", tag)
    if tag_arch != arch:
        return False
    version = MACOS_TAG_VERSIONS.get(name)
    return version is None or macos_major is None or version <= macos_major


def _run_slow(args: list[str]) -> subprocess.CompletedProcess:
    # 剛裝好的 Homebrew 第一次查要先下載套件清單（十幾 MB）
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")


def check_bottles(brew: BrewInfo, *, arch: str, macos_major: int | None, formula: str = FFMPEG_FORMULA,
                  run: Runner = _run_slow) -> BottleCheck:
    """`brew deps` 列出整棵依賴樹、`brew info --json=v2` 看每個套件的現成安裝包。只讀，不裝任何東西。

    arch 是這支 brew 的架構（Apple Silicon 上用 Rosetta 裝在 /usr/local 的 Homebrew 是 x86_64，只拿得到 Intel 的）。"""
    try:
        deps = run([str(brew.path), "deps", "--formula", formula])
        if deps.returncode != 0:
            return BottleCheck(error=_first_line(deps.stderr or deps.stdout) or f"brew deps 結束碼 {deps.returncode}")
        info = run([str(brew.path), "info", "--json=v2", "--formula", formula, *deps.stdout.split()])
        if info.returncode != 0:
            return BottleCheck(error=_first_line(info.stderr or info.stdout) or f"brew info 結束碼 {info.returncode}")
        formulae = json.loads(info.stdout)["formulae"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as e:
        return BottleCheck(error=f"{type(e).__name__}: {e}")
    if not formulae:
        return BottleCheck(error="brew info 沒有回任何套件")
    from_source = []
    for f in formulae:
        if f.get("installed") and not f.get("outdated"):
            continue  # 已經裝好、不用更新：不用下載
        files = ((f.get("bottle") or {}).get("stable") or {}).get("files") or {}
        if not any(bottle_fits(tag, arch, macos_major) for tag in files):
            from_source.append(f.get("name", "?"))
    return BottleCheck(len(formulae), tuple(from_source))


def find_brew(layout: BrewLayout, run: Runner = _run) -> BrewInfo | None:
    """找一支跑得起來的 brew（`brew --version` 成功）；都沒有就回 None。不看 PATH（官方只支援這兩個位置）。"""
    for brew in layout.brew_candidates():
        if not brew.exists():
            continue
        try:
            proc = run([str(brew), "--version"])
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            return BrewInfo(brew, proc.stdout.strip().splitlines()[0])
    return None

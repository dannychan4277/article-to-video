"""Homebrew 在哪裡：Apple Silicon 裝在 /opt/homebrew，Intel 裝在 /usr/local（Homebrew 官方只支援這兩個位置）。

看的是硬體，不是這個 Python 的架構：Apple Silicon 上用 Rosetta 跑的 x86_64 Python，platform.machine()
會說 x86_64，但這台 Mac 的 Homebrew 該在 /opt/homebrew。以前用 Rosetta 裝在 /usr/local 的舊 Homebrew 也認得，
不會再裝一套。

測試把位置整個搬到 tmp：BrewLayout.for_arch(arch, root=tmp_path)。
"""

from __future__ import annotations

import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "FFMPEG_FORMULA",
    "INSTALL_SCRIPT_URL",
    "PREFIXES",
    "BrewInfo",
    "BrewLayout",
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

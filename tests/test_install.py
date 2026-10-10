"""安裝流程（#12）：Homebrew、能燒字幕的 ffmpeg、Playwright Chromium、uv、edge-tts 連線，已經有的略過。

這台 Mac 什麼都裝好了：「已經有就略過」的路徑用真的系統跑（但任何安裝動作一叫就讓測試失敗）。
缺東西的路徑全部是假的：Homebrew 位置換成 tmp 底下的資料夾、假 brew／curl／sudo、假終端機，
不連網、不真的裝任何東西。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from article_to_video.environment import EnvCheck
from article_to_video.homebrew import BrewLayout, find_brew, machine_arch
from article_to_video.installer import Installer
from article_to_video.mac_platform import MacPlatform

from test_ffmpeg import FILTERS_WITH_SUBTITLES, FILTERS_WITHOUT_SUBTITLES

HOMEBREW_INSTALLER = "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"


def completed(args, stdout="", returncode=0):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


FAKE_BREW = '''
# 假 brew：--version 印版本；install ffmpeg-full 在 keg 裡放一支假 ffmpeg（被問 -filters 就印濾鏡表）；
# deps／info --json=v2 照 CATALOG 回答 ffmpeg-full 的依賴樹和每個套件的現成安裝包（CATALOG 是 None＝連不上網）
import json
import sys
from pathlib import Path

prefix = Path(__file__).resolve().parent.parent
args = sys.argv[1:]
if args == ["--version"]:
    print(VERSION)
elif args == ["install", "ffmpeg-full"]:
    print("==> Downloading ffmpeg-full")
    ff = prefix / "opt" / "ffmpeg-full" / "bin" / "ffmpeg"
    ff.parent.mkdir(parents=True, exist_ok=True)
    ff.write_text(FFMPEG_SRC, encoding="utf-8")
    ff.chmod(0o755)
    print("==> Pouring ffmpeg-full")
elif args[:1] in (["deps"], ["info"]) and CATALOG is None:
    sys.exit("Error: Failed to download https://formulae.brew.sh/api/internal/packages.arm64_tahoe.jws.json")
elif args == ["deps", "--formula", "ffmpeg-full"]:
    for name in CATALOG:
        if name != "ffmpeg-full":
            print(name)
elif args[:3] == ["info", "--json=v2", "--formula"]:
    def one(name):
        f = CATALOG[name]
        files = dict((tag, {"cellar": ":any"}) for tag in f["files"])
        return {"name": name, "installed": [{"version": "1.0"}] if f["installed"] else [], "outdated": False,
                "bottle": {"stable": {"files": files}} if files else {}}
    print(json.dumps({"formulae": [one(n) for n in args[3:]], "casks": []}))
else:
    sys.exit("假 brew 不會做這件事：" + " ".join(args))
'''


def catalog(tag: str = "arm64_tahoe", *, unbottled: tuple[str, ...] = (), installed: tuple[str, ...] = ()) -> dict:
    """假 Homebrew 眼中的 ffmpeg-full 依賴樹：每個套件有哪些現成安裝包（bottle tag）、這台裝了沒。
    預設像 Apple Silicon＋macOS 26 那樣全部都有（Homebrew 7 的 brew info 只列這台 Mac 用得到的那一個）。"""
    names = ["ffmpeg-full", "libass", "x264", "ca-certificates"]
    return {n: {"files": [] if n in unbottled else ["all" if n == "ca-certificates" else tag],
                "installed": n in installed} for n in names}


def fake_brew_source(version: str = "Homebrew 4.9.9", filters: str = FILTERS_WITH_SUBTITLES,
                     catalog: dict | None = catalog()) -> str:
    ffmpeg_src = f"#!{sys.executable}\nimport sys\nsys.stdout.write({filters!r})\n"
    head = f"#!{sys.executable}\nVERSION = {version!r}\nFFMPEG_SRC = {ffmpeg_src!r}\nCATALOG = {catalog!r}\n"
    return head + FAKE_BREW


def fake_brew(prefix: Path, version: str = "Homebrew 4.9.9", filters: str = FILTERS_WITH_SUBTITLES,
              catalog: dict | None = catalog()) -> Path:
    """在 <prefix>/bin/brew 放一支假 brew。"""
    brew = prefix / "bin" / "brew"
    brew.parent.mkdir(parents=True, exist_ok=True)
    brew.write_text(fake_brew_source(version, filters, catalog), encoding="utf-8")
    brew.chmod(0o755)
    return brew


# ---------------------------------------------------------------- Homebrew 在哪裡


def test_apple_silicon_and_intel_use_their_own_homebrew_prefix():
    arm = BrewLayout.for_arch("arm64")
    intel = BrewLayout.for_arch("x86_64")
    assert arm.brew == Path("/opt/homebrew/bin/brew")
    assert arm.ffmpeg_full == Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    assert intel.brew == Path("/usr/local/bin/brew")
    assert intel.ffmpeg_full == Path("/usr/local/opt/ffmpeg-full/bin/ffmpeg")


def test_hardware_decides_the_arch_even_under_rosetta():
    # Apple Silicon 上用 Rosetta 跑的 x86_64 Python，platform.machine() 會說 x86_64；要看硬體
    assert machine_arch(run=lambda args: completed(args, "1\n"), machine="x86_64") == "arm64"
    # Intel 沒有這個 sysctl 鍵（印空的、結束碼非零）
    assert machine_arch(run=lambda args: completed(args, "", 1), machine="x86_64") == "x86_64"
    assert machine_arch(run=lambda args: completed(args, "0\n"), machine="x86_64") == "x86_64"


@pytest.mark.parametrize("arch", ["arm64", "x86_64"])
def test_find_brew_looks_in_the_prefix_for_this_arch(tmp_path, arch):
    layout = BrewLayout.for_arch(arch, root=tmp_path)
    assert find_brew(layout) is None
    brew = fake_brew(layout.prefix)
    info = find_brew(layout)
    assert info is not None and info.path == brew and info.version == "Homebrew 4.9.9"
    assert info.prefix == layout.prefix


def test_find_brew_also_accepts_an_intel_homebrew_on_apple_silicon(tmp_path):
    """Apple Silicon 上以前用 Rosetta 裝的 Homebrew 在 /usr/local：也能用，不要再裝一套。"""
    layout = BrewLayout.for_arch("arm64", root=tmp_path)
    fake_brew(BrewLayout.for_arch("x86_64", root=tmp_path).prefix)
    info = find_brew(layout)
    assert info is not None and info.prefix == tmp_path / "usr" / "local"


def test_this_mac_has_homebrew_where_the_layout_says():
    from article_to_video.homebrew import default_layout

    layout = default_layout()
    info = find_brew(layout)
    assert info is not None, "這台 Mac 有 Homebrew"
    assert info.version.startswith("Homebrew ")
    expected = "/opt/homebrew" if machine_arch() == "arm64" else "/usr/local"
    assert str(layout.prefix) == expected


# ---------------------------------------------------------------- 安裝流程


def forbidden(*args, **kwargs):
    raise AssertionError(f"這台 Mac 都裝好了，不該執行任何安裝動作：{args}")


class SpeakingCommunicate:
    def __init__(self, text, voice, **kwargs):
        pass

    async def stream(self):
        yield {"type": "audio", "data": b"\xff\xf3" * 100}


@pytest.fixture
def edge_ok(monkeypatch):
    import edge_tts

    monkeypatch.setattr(edge_tts, "Communicate", SpeakingCommunicate)


def test_everything_already_installed_is_skipped_on_this_mac(tmp_path, edge_ok, monkeypatch, capsys):
    """這台 Mac 真的跑一次「安裝」：每一項都是「已經有了，略過」，任何安裝動作一叫就失敗。"""
    from article_to_video import cli, installer

    monkeypatch.setattr(installer, "run_streaming", forbidden)
    monkeypatch.setattr(installer, "open_in_terminal", forbidden)
    code = cli.main(["install", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    for name, says in [("Homebrew", "已經有了"), ("ffmpeg", "已經有了"), ("Chromium", "已經有了"), ("uv", "已經有了"),
                       ("edge-tts", "連得上")]:
        line = next(ln for ln in out.splitlines() if name in ln and ln[:1] in "✓✗")
        assert line.startswith("✓") and says in line, line
    assert "全部裝好了" in out
    assert code == 0
    # 給 Danny 看的安裝紀錄
    assert "全部裝好了" in (tmp_path / "install" / "install.log").read_text(encoding="utf-8")


class FakeMac:
    """一台假的「全新 Mac」：Homebrew 位置在 tmp 底下（照架構分 /opt/homebrew 或 /usr/local）、
    PATH 上有假 sudo 和假 curl（curl 抓到的「官方安裝程式」會在那個位置放一支假 brew），
    「終端機」用背景行程跑安裝腳本。系統是 macOS 26.2（換別的：installer(platform=…)）。
    Intel 不在支援範圍（docs/adr/0002）：假 Intel Mac 要裝得下去得加 allow_source_build=True（維護者模式）。
    """

    def __init__(self, tmp_path: Path, arch: str, *, sudo_exit: int = 0, sudo_sleep: float = 0,
                 filters: str = FILTERS_WITH_SUBTITLES, bottles: dict | None | str = "default"):
        self.layout = BrewLayout.for_arch(arch, root=tmp_path / "mac")
        self.data = tmp_path / "資料 位置"  # 真的資料位置可能在 Application Support，路徑有空白
        self.bin = tmp_path / "fakebin"
        self.bin.mkdir(parents=True)
        self.scripts: list[str] = []
        self.procs: list[subprocess.Popen] = []
        # bottles：假 Homebrew 眼中的依賴樹（catalog()；None＝查的時候連不上網）
        if bottles == "default":
            bottles = catalog("arm64_tahoe" if arch == "arm64" else "tahoe")
        self.staged = tmp_path / "brew-to-install"
        self.staged.write_text(fake_brew_source(filters=filters, catalog=bottles), encoding="utf-8")
        install_sh = (f"mkdir -p '{self.layout.prefix}/bin' && cp '{self.staged}' '{self.layout.brew}' "
                      f"&& chmod 755 '{self.layout.brew}' && echo '==> Installation successful!'")
        self._exe("curl", f"print({install_sh!r})\n")
        self._exe("sudo", f"import sys, time\ntime.sleep({sudo_sleep})\nsys.exit({sudo_exit})\n")

    def brew_already_installed(self, prefix: Path | None = None) -> Path:
        """這台本來就有 Homebrew（不用開終端機）；prefix 給另一個位置＝以前用 Rosetta 裝在 /usr/local 的那種。"""
        brew = (prefix or self.layout.prefix) / "bin" / "brew"
        brew.parent.mkdir(parents=True, exist_ok=True)
        brew.write_text(self.staged.read_text(encoding="utf-8"), encoding="utf-8")
        brew.chmod(0o755)
        return brew

    def _exe(self, name: str, body: str) -> None:
        f = self.bin / name
        f.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        f.chmod(0o755)

    def open_terminal(self, script: Path) -> bool:
        self.scripts.append(script.read_text(encoding="utf-8"))
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}
        proc = subprocess.Popen(["/bin/bash", str(script)], env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        self.procs.append(proc)
        threading.Thread(target=proc.wait, daemon=True).start()  # 真的終端機會收屍；測試裡自己收
        return True

    def installer(self, **kw) -> Installer:
        # homebrew_timeout：哪天輪詢壞了，測試失敗而不是卡 45 分鐘
        # Chromium 不是這些測試的重點：真的開一次要半秒多，換成「開得起來」（真的那條在別的測試）
        opts = dict(layout=self.layout, platform=MacPlatform(self.layout.arch, "26.2"),
                    open_terminal=self.open_terminal, is_admin=lambda: True,
                    ffmpeg_candidates=(), poll_interval=0.05, homebrew_timeout=15, log=lambda m: None,
                    chromium_check=lambda: EnvCheck("chromium", "Chromium", True, "153.0（假）"))
        opts.update(kw)
        return Installer(self.data, **opts)


@pytest.mark.parametrize("arch", ["arm64", "x86_64"])
def test_fresh_mac_gets_homebrew_through_terminal_then_ffmpeg_full(tmp_path, arch, edge_ok):
    """Apple Silicon 是作者的路徑；Intel 只剩維護者模式（--allow-source-build），順便守住 /usr/local 的位置。"""
    mac = FakeMac(tmp_path, arch)
    lines: list[str] = []
    report = mac.installer(log=lines.append, allow_source_build=arch == "x86_64").run()
    steps = {s.key: s for s in report.steps}

    assert steps["homebrew"].status == "installed", steps["homebrew"]
    assert steps["ffmpeg"].status == "installed", steps["ffmpeg"]
    assert steps["chromium"].status == "present" and steps["uv"].status == "present"
    assert report.ok
    # 終端機跑的是官方安裝程式，先問一次密碼
    [script] = mac.scripts
    assert HOMEBREW_INSTALLER in script
    assert "sudo -v" in script and "密碼" in script
    # 告訴作者要做什麼：去終端機、輸入密碼、畫面不會出現字
    told = "\n".join(lines)
    assert "終端機" in told and "密碼" in told and "不會出現" in told
    # ffmpeg-full 裝在這個架構的位置，而且有字幕濾鏡
    assert mac.layout.ffmpeg_full.exists()
    assert str(mac.layout.ffmpeg_full) in steps["ffmpeg"].message


def test_wrong_password_stops_homebrew_and_says_what_to_do(tmp_path, edge_ok):
    mac = FakeMac(tmp_path, "arm64", sudo_exit=1)
    report = mac.installer().run()
    steps = {s.key: s for s in report.steps}
    assert steps["homebrew"].status == "failed"
    assert "密碼" in steps["homebrew"].fix and "安裝" in steps["homebrew"].fix
    assert steps["ffmpeg"].status == "failed" and "Homebrew" in steps["ffmpeg"].message
    assert steps["chromium"].status == "present"  # 不靠 Homebrew 的照樣檢查
    assert not report.ok


def test_closing_the_terminal_window_is_noticed(tmp_path, edge_ok):
    mac = FakeMac(tmp_path, "arm64", sudo_sleep=60)  # 卡在等密碼
    inst = mac.installer()

    def close_window_soon():
        while not mac.procs:
            time.sleep(0.02)
        time.sleep(0.3)
        mac.procs[0].kill()

    threading.Thread(target=close_window_soon, daemon=True).start()
    t0 = time.monotonic()
    report = inst.run()
    homebrew = {s.key: s for s in report.steps}["homebrew"]
    assert homebrew.status == "failed" and "視窗" in homebrew.message
    assert time.monotonic() - t0 < 20


def test_non_admin_account_is_told_before_opening_terminal(tmp_path, edge_ok):
    mac = FakeMac(tmp_path, "arm64")
    report = mac.installer(is_admin=lambda: False, open_terminal=forbidden).run()
    homebrew = {s.key: s for s in report.steps}["homebrew"]
    assert homebrew.status == "failed" and "管理員" in homebrew.message


def test_terminal_that_never_starts_gives_up_with_manual_steps(tmp_path, edge_ok):
    mac = FakeMac(tmp_path, "arm64")
    report = mac.installer(open_terminal=lambda script: True, start_timeout=0.3).run()
    homebrew = {s.key: s for s in report.steps}["homebrew"]
    assert homebrew.status == "failed"
    assert HOMEBREW_INSTALLER in homebrew.fix


def test_ffmpeg_without_subtitles_after_install_is_a_failure(tmp_path, edge_ok):
    mac = FakeMac(tmp_path, "arm64", filters=FILTERS_WITHOUT_SUBTITLES)
    report = mac.installer().run()
    ffmpeg = {s.key: s for s in report.steps}["ffmpeg"]
    assert ffmpeg.status == "failed" and "字幕" in ffmpeg.message


def test_missing_chromium_is_downloaded_through_the_project_python(tmp_path, edge_ok, monkeypatch):
    """Chromium 不在（PLAYWRIGHT_BROWSERS_PATH 指到空資料夾）→ 用專案環境的 Python 跑 playwright install chromium。"""
    from article_to_video.homebrew import default_layout

    real = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "空的"))
    ran: list[list[str]] = []

    def act(args, *, env=None, log):
        ran.append(list(args))
        # 「下載完成」：換回真的瀏覽器位置
        if real is None:
            monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
        else:
            monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", real)
        return 0

    report = Installer(tmp_path / "data", layout=default_layout(), act=act, open_terminal=forbidden,
                       log=lambda m: None).run()
    steps = {s.key: s for s in report.steps}
    assert ran == [[sys.executable, "-m", "playwright", "install", "chromium"]]
    assert steps["chromium"].status == "installed"
    assert steps["homebrew"].status == steps["ffmpeg"].status == "present"


def test_edge_tts_unreachable_is_reported_but_does_not_stop_the_rest(tmp_path):
    """測試裡沒換掉 Communicate：連不上（conftest 擋網）。其他項照樣檢查完，結果是「沒全部裝好」。"""
    from article_to_video.homebrew import default_layout

    report = Installer(tmp_path / "data", layout=default_layout(), act=forbidden, open_terminal=forbidden,
                       log=lambda m: None).run()
    steps = {s.key: s for s in report.steps}
    assert steps["edge-tts"].status == "failed" and "網路" in steps["edge-tts"].fix
    assert all(s.status == "present" for k, s in steps.items() if k != "edge-tts")
    assert not report.ok

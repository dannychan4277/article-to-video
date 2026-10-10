"""安裝：把做片要用的工具裝好，已經有的略過。`install` 指令跑的就是 Installer(資料位置).run()。

順序（一項沒裝好不影響不靠它的項目）：
  0. 這台 Mac：只支援 Apple Silicon＋macOS 15 以上（mac_platform.py、docs/adr/0002）。不支援就白話說明、
     什麼都不裝就停（連 Homebrew 都不裝）。維護者可以用 allow_source_build（`install --allow-source-build`）硬裝。
  1. Homebrew：官方安裝程式要 sudo 密碼和真的終端機（Claude 的 Bash 沒有 TTY），所以寫一個 .command 腳本
     用「終端機」打開：先 `sudo -v` 問一次密碼（作者自己在終端機打，Claude 碰不到），再用 NONINTERACTIVE=1
     跑官方那一行。這邊每幾秒看一次：腳本寫下的結束碼、腳本的 PID 還在不在（視窗被關掉）、等太久。
     不是管理員帳號就先說清楚，不開終端機。
  2. 能燒字幕的 ffmpeg：find_ffmpeg 找得到有 subtitles 濾鏡的就略過；不然先問 Homebrew 整棵依賴樹在這台 Mac
     有沒有現成安裝包（homebrew.check_bottles；沒有就停，不讓 brew 默默從原始碼編好幾個小時；查不了就照靜態規則），
     再 `brew install ffmpeg-full`（keg-only，不用改 PATH），裝完再驗一次濾鏡。
  3. Chromium：真的開一次無頭 Chromium；開不起來就用專案環境的 Python 跑 `playwright install chromium`
     （版本跟 pyproject 釘的 playwright 對得上），裝完再開一次。
  4. uv：我們就是用 uv 跑起來的，只回報位置與版本（第一次安裝 uv 是 skill 用官方安裝程式做的）。
  5. edge-tts 連線：請 edge-tts 試唸兩個字；連不上不能「裝」，只能說怎麼處理網路。

每一行輸出同時寫進 <資料位置>/install/install.log（作者說「回報問題」時可以請他附上）。
所有安裝動作都經過注入的 act／open_terminal，測試換成假的；探測（brew --version、ffmpeg -filters、
開 Chromium）是真的，所以「這台 Mac 已經裝好就略過」的路徑可以在真機上測。
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from article_to_video.progress import Log
from article_to_video.environment import (
    EnvCheck, SAY_INSTALL, check_chromium, check_edge_tts_online, check_uv,
)
from article_to_video.ffmpeg import FFmpegNotFoundError, default_ffmpeg_candidates, find_ffmpeg
from article_to_video import mac_platform
from article_to_video.mac_platform import MIN_MACOS, MacPlatform, assess
from article_to_video.macos import open_path
from article_to_video.homebrew import (
    FFMPEG_FORMULA, INSTALL_SCRIPT_URL, BrewInfo, BrewLayout, check_bottles, default_layout, find_brew, keg_ffmpeg,
)
from article_to_video.user_settings import resolve_data_dir

__all__ = ["InstallReport", "Installer", "StepResult", "open_in_terminal", "run_streaming", "user_is_admin"]

Act = Callable[..., int]
"""act(args, *, env=None, log) -> 結束碼：執行一個安裝指令，輸出逐行交給 log。"""

HOMEBREW_TIMEOUT = 45 * 60
"""等 Homebrew 裝好最多等多久（全新的 Mac 還要先裝 Xcode 指令列工具，慢的網路要二三十分鐘）。"""
START_TIMEOUT = 120
"""開了終端機之後，腳本多久內要開始跑（寫下 PID）。"""
HOMEBREW_MANUAL = (
    "也可以自己裝：打開「終端機」（在「應用程式」→「工具程式」裡），貼上下面這行按 return，照它說的輸入密碼：\n"
    f'/bin/bash -c "$(curl -fsSL {INSTALL_SCRIPT_URL})"\n'
    "裝好之後再跟 Claude 說一次「安裝」。"
)


# ---------------------------------------------------------------- 對外的動作（測試換掉）


def run_streaming(args: Sequence[str], *, env: dict[str, str] | None = None, log: Log) -> int:
    """跑一個指令，輸出一行一行交給 log（brew 裝很久，要看得到進度）。"""
    proc = subprocess.Popen(list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", env=env)
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.strip():
            log(f"    │ {line.rstrip()}")
    return proc.wait()


def open_in_terminal(script: Path) -> bool:
    """用「終端機」打開 .command 腳本（不需要「自動化」權限；本機寫的檔沒有隔離屬性，不會跳警告）。"""
    return open_path(script, app="Terminal", timeout=30)


def user_is_admin() -> bool:
    """這個 Mac 帳號是不是管理員（Homebrew 要用 sudo 建資料夾）。"""
    try:
        out = subprocess.run(["id", "-Gn"], capture_output=True, text=True, encoding="utf-8", timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return True  # 判斷不了就讓 sudo 自己說
    return "admin" in out.split()


# ---------------------------------------------------------------- 結果


@dataclass(frozen=True)
class StepResult:
    key: str
    name: str
    status: str
    """"present"（本來就有，略過）、"installed"（這次裝好）、"failed"。"""
    message: str
    fix: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "failed"

    def text(self) -> str:
        mark = "✓" if self.ok else "✗"
        line = f"{mark} {self.name}：{self.message}"
        return line + (f"\n    修法：{self.fix}" if self.fix and not self.ok else "")


@dataclass(frozen=True)
class InstallReport:
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    def summary(self) -> str:
        bad = [s for s in self.steps if not s.ok]
        if not bad:
            return "全部裝好了。"
        if bad[0].key == "platform":
            return "沒有安裝任何東西（Homebrew 也沒裝）：這台 Mac 不在支援範圍，請照上面的說明處理。"
        return f"有 {len(bad)} 項沒裝好：{'、'.join(s.name for s in bad)}。照上面的「修法」處理完，再說一次「安裝」。"


# ---------------------------------------------------------------- 安裝流程


class Installer:
    def __init__(
        self,
        data_dir: str | os.PathLike | None = None,
        *,
        layout: BrewLayout | None = None,
        platform: MacPlatform | None = None,
        allow_source_build: bool = False,
        act: Act | None = None,
        open_terminal: Callable[[Path], bool] | None = None,
        is_admin: Callable[[], bool] | None = None,
        ffmpeg_candidates: Sequence[Path] | None = None,
        chromium_check: Callable[[], EnvCheck] | None = None,
        homebrew_timeout: float = HOMEBREW_TIMEOUT,
        start_timeout: float = START_TIMEOUT,
        poll_interval: float = 5.0,
        log: Log = print,
    ) -> None:
        self.data_dir = resolve_data_dir(data_dir)
        self.platform = platform or mac_platform.detect_platform()  # 叫的當下才查（測試換掉整個模組的函式）
        self.allow_source_build = allow_source_build
        """維護者用：不支援的 Mac 也照裝、ffmpeg-full 沒有現成安裝包也讓 brew 從原始碼編（docs/maintaining.md）。"""
        self.layout = layout or default_layout(self.platform.arch)
        # 不給就用模組層的函式（叫的當下才查，測試可以整個模組換掉）
        self.act: Act = act or (lambda *a, **k: run_streaming(*a, **k))
        self.open_terminal = open_terminal or (lambda script: open_in_terminal(script))
        self.is_admin = is_admin or (lambda: user_is_admin())
        self.extra_ffmpeg = list(ffmpeg_candidates) if ffmpeg_candidates is not None else None
        self.chromium_check = chromium_check or check_chromium
        self.homebrew_timeout = homebrew_timeout
        self.start_timeout = start_timeout
        self.poll_interval = poll_interval
        self._log = log
        self.work = self.data_dir / "install"
        self.log_file = self.work / "install.log"

    def log(self, msg: str) -> None:
        """印出來，同時一行一行接進 install.log（中途被中斷也留得下紀錄）。"""
        self._log(msg)
        try:
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except OSError:
            pass

    def run(self) -> InstallReport:
        self.work.mkdir(parents=True, exist_ok=True)
        self.log_file.write_text(time.strftime("安裝紀錄 %Y-%m-%d %H:%M:%S\n"), encoding="utf-8")
        self.log("開始安裝檢查")
        steps: list[StepResult] = []
        # 0. 這台 Mac：不支援就什麼都不動（Homebrew 都還沒裝）
        gate = self._platform()
        steps.append(gate)
        self.log(gate.text())
        if not gate.ok:
            report = InstallReport(steps)
            self.log(report.summary())
            return report
        self.log(f"（這台是 {self.layout.label}，Homebrew 該在 {self.layout.prefix}）")
        names = ["Homebrew", "能燒字幕的 ffmpeg", "Chromium", "uv", "edge-tts 連線"]

        def step(i: int, run: Callable[[], StepResult]) -> StepResult:
            self.log(f"[{i}/{len(names)}] {names[i - 1]}：檢查中…")
            result = run()
            steps.append(result)
            self.log(result.text())
            return result

        brew: list[BrewInfo | None] = [None]

        def homebrew() -> StepResult:
            result, brew[0] = self._homebrew()
            return result

        step(1, homebrew)
        step(2, lambda: self._ffmpeg(brew[0]))
        step(3, self._chromium)
        step(4, self._uv)
        step(5, self._edge)
        report = InstallReport(steps)
        self.log(report.summary())
        return report

    # ------------------------------------------------------------ 0. 這台 Mac

    def _platform(self) -> StepResult:
        verdict = assess(self.platform)
        if verdict.ok:
            return StepResult("platform", "這台 Mac", "present", verdict.summary)
        if self.allow_source_build:
            return StepResult("platform", "這台 Mac", "present",
                              f"{self.platform.label}，不在支援範圍；維護者模式（--allow-source-build）照樣裝，"
                              "ffmpeg-full 可能要從原始碼編好幾個小時")
        return StepResult("platform", "這台 Mac", "failed", verdict.summary, fix=verdict.fix)

    # ------------------------------------------------------------ 1. Homebrew

    def _homebrew(self) -> tuple[StepResult, BrewInfo | None]:
        name = "Homebrew"
        info = find_brew(self.layout)
        if info is not None:
            return StepResult("homebrew", name, "present", f"已經有了，略過（{info.version}，{info.path}）"), info
        if not self.is_admin():
            return StepResult(
                "homebrew", name, "failed", "這個 Mac 帳號不是管理員，裝不了 Homebrew",
                fix="Homebrew 要管理員密碼才能裝。請改用管理員帳號登入這台 Mac 再說「安裝」，"
                    "或請有管理員密碼的人在「系統設定」→「使用者與群組」把你的帳號設成管理員。",
            ), None

        script, status, pid_file = (self.work / "install-homebrew.command", self.work / "homebrew.status",
                                    self.work / "homebrew.pid")
        for f in (status, pid_file):
            f.unlink(missing_ok=True)
        script.write_text(_homebrew_script(status, pid_file), encoding="utf-8")
        script.chmod(0o755)
        if not self.open_terminal(script):
            return StepResult("homebrew", name, "failed", "打不開「終端機」", fix=HOMEBREW_MANUAL), None
        self.log(
            "  已經打開「終端機」視窗在裝 Homebrew。請到那個視窗：\n"
            "    1. 看到「Password:」時，輸入你登入這台 Mac 的密碼，再按 return。\n"
            "       打字時畫面不會出現任何字或星號，這是正常的，照打就好。\n"
            "    2. 然後讓它自己跑（全新的 Mac 大約 5～20 分鐘），看到「Homebrew 裝好了」就可以關掉那個視窗。"
        )
        return self._wait_for_homebrew(status, pid_file)

    def _wait_for_homebrew(self, status: Path, pid_file: Path) -> tuple[StepResult, BrewInfo | None]:
        name = "Homebrew"
        start = last_note = time.monotonic()
        while True:
            code = _read_int(status)
            if code is None:
                pid = _read_int(pid_file)
                now = time.monotonic()
                if pid is not None and not _alive(pid):
                    code = _read_int(status)  # 剛好在這兩次之間寫完、結束
                    if code is None:
                        return StepResult(
                            "homebrew", name, "failed", "安裝還沒跑完，終端機視窗就被關掉了（或被中斷）",
                            fix="再說一次「安裝」重來；這次請等終端機寫「Homebrew 裝好了」再關視窗。",
                        ), None
                elif pid is None and now - start > self.start_timeout:
                    return StepResult("homebrew", name, "failed", "終端機沒有開始安裝",
                                      fix=HOMEBREW_MANUAL), None
                elif now - start > self.homebrew_timeout:
                    return StepResult(
                        "homebrew", name, "failed", f"等了 {int(self.homebrew_timeout // 60)} 分鐘還沒裝好",
                        fix="看一下終端機視窗停在哪裡：在等密碼就輸入密碼；還在跑就再說一次「安裝」繼續等；"
                            "出現紅字錯誤就截圖，說「回報問題」傳給 Danny。",
                    ), None
                if code is None:
                    if now - last_note >= 60:
                        self.log(f"  還在等 Homebrew 裝好（已經 {int((now - start) // 60)} 分鐘）…")
                        last_note = now
                    time.sleep(self.poll_interval)
                    continue
            info = find_brew(self.layout)
            if code == 0 and info is not None:
                return StepResult("homebrew", name, "installed", f"裝好了（{info.version}，{info.path}）"), info
            if code == 0:
                return StepResult("homebrew", name, "failed", "安裝程式說裝好了，但找不到 brew",
                                  fix="說「回報問題」傳給 Danny"), None
            return StepResult(
                "homebrew", name, "failed", f"沒裝成功（終端機裡的安裝程式結束碼 {code}）",
                fix="如果是密碼打錯，再說一次「安裝」重來就好（打密碼時畫面不會出現字）。"
                    "不是密碼的問題，就把終端機視窗截圖，說「回報問題」傳給 Danny。",
            ), None

    # ------------------------------------------------------------ 2. ffmpeg-full

    def _ffmpeg_candidates(self, brew: BrewInfo | None) -> list[Path]:
        cands = [keg_ffmpeg(brew.prefix)] if brew else []
        cands.append(self.layout.ffmpeg_full)
        cands.extend(default_ffmpeg_candidates() if self.extra_ffmpeg is None else self.extra_ffmpeg)
        return list(dict.fromkeys(cands))

    def _ffmpeg(self, brew: BrewInfo | None) -> StepResult:
        name = "能燒字幕的 ffmpeg"
        try:
            found = find_ffmpeg(self._ffmpeg_candidates(brew))
            return StepResult("ffmpeg", name, "present", f"已經有了，略過（{found}）")
        except FFmpegNotFoundError:
            pass
        if brew is None:
            return StepResult("ffmpeg", name, "failed", "要先裝好 Homebrew 才能裝",
                              fix="先處理上面 Homebrew 那一項，再說一次「安裝」")
        stop = self._bottles(brew)
        if stop is not None:
            return stop
        self.log(f"  正在裝 {FFMPEG_FORMULA}（brew install {FFMPEG_FORMULA}）。要下載不少東西，大約 5～20 分鐘…")
        env = {**os.environ, "HOMEBREW_NO_ENV_HINTS": "1", "NONINTERACTIVE": "1"}
        code = self.act([str(brew.path), "install", FFMPEG_FORMULA], env=env, log=self.log)
        try:
            found = find_ffmpeg(self._ffmpeg_candidates(brew))
        except FFmpegNotFoundError:
            return StepResult(
                "ffmpeg", name, "failed",
                f"brew install {FFMPEG_FORMULA} 跑完（結束碼 {code}），還是找不到有字幕濾鏡（subtitles）的 ffmpeg",
                fix="再說一次「安裝」試一次；還是不行就說「回報問題」傳給 Danny",
            )
        return StepResult("ffmpeg", name, "installed", f"裝好了（{found}）")

    def _bottles(self, brew: BrewInfo) -> StepResult | None:
        """brew install 之前問 Homebrew：要下載的套件這台 Mac 都有現成安裝包嗎？沒有＝brew 會默默從原始碼編好幾個小時，
        先停下來（回傳失敗的那一步）。靜態規則（mac_platform）擋得住今天的情況；這裡接住以後 Homebrew 支援範圍往前移。"""
        brew_arch = self.layout.arch_of(brew)
        check = check_bottles(brew, arch=brew_arch, macos_major=self.platform.major)
        if not check.known:
            self.log(f"  沒辦法先確認 {FFMPEG_FORMULA} 有沒有這台 Mac 能用的現成安裝包（{check.error}），"
                     f"照「Apple Silicon、macOS {MIN_MACOS} 以上」的規則繼續裝")
            return None
        if not check.from_source:
            self.log(f"  {FFMPEG_FORMULA} 和它要用的套件都有這台 Mac 能用的現成安裝包（看了 {check.looked_at} 個）")
            return None
        some = "、".join(check.from_source[:5]) + ("…" if len(check.from_source) > 5 else "")
        what = (f"Homebrew 沒有這台 Mac（{self.platform.label}）能用的 {FFMPEG_FORMULA} 現成安裝包："
                f"要裝的套件裡有 {len(check.from_source)} 個得從原始碼編譯（{some}）")
        if self.allow_source_build:
            self.log(f"  {what}。維護者模式（--allow-source-build）：照樣讓 brew 從原始碼編，可能要好幾個小時")
            return None
        if brew_arch != self.platform.arch:
            fix = (f"這台 Mac 的 Homebrew 是以前用 Rosetta 裝的 Intel 版（在 {brew.prefix}，多半是從舊 Mac 搬過來的），"
                   "拿不到 Apple Silicon 的現成安裝包。這要 Danny 幫忙處理：請說「回報問題」傳給 Danny。")
        else:
            fix = ("從原始碼編譯要好幾個小時、常常失敗，所以先停下來沒有裝。多半是 Homebrew 已經不支援這個 macOS 版本："
                   "點螢幕左上角的蘋果選單 →「系統設定」→「一般」→「軟體更新」，把 macOS 更新到最新，再跟 Claude 說「安裝」。"
                   "已經是最新的還是這樣，請說「回報問題」傳給 Danny。")
        return StepResult("ffmpeg", "能燒字幕的 ffmpeg", "failed", what + "，所以先停下來、沒有開始裝", fix=fix)

    # ------------------------------------------------------------ 3. Chromium

    def _chromium(self) -> StepResult:
        name = "Chromium（畫圖表用的瀏覽器）"
        before = self.chromium_check()
        if before.ok:
            return StepResult("chromium", name, "present", f"已經有了，略過（{before.value}）")
        self.log("  正在下載 Playwright 用的 Chromium（約 150 MB）…")
        code = self.act([sys.executable, "-m", "playwright", "install", "chromium"], env=None, log=self.log)
        after = self.chromium_check()
        if after.ok:
            return StepResult("chromium", name, "installed", f"裝好了（{after.value}）")
        return StepResult("chromium", name, "failed", f"下載跑完（結束碼 {code}）還是開不起來：{after.value}",
                          fix="確認網路正常後再說一次「安裝」；還是不行就說「回報問題」傳給 Danny")

    # ------------------------------------------------------------ 4. uv

    def _uv(self) -> StepResult:
        c: EnvCheck = check_uv()
        if c.ok:
            return StepResult("uv", "uv", "present", f"已經有了，略過（{c.value}）")
        return StepResult("uv", "uv", "failed", c.value, fix=c.fix or SAY_INSTALL)

    # ------------------------------------------------------------ 5. edge-tts

    def _edge(self) -> StepResult:
        name = "edge-tts 連線（配音）"
        c = check_edge_tts_online()
        if c.ok:
            return StepResult("edge-tts", name, "present", c.value)
        return StepResult("edge-tts", name, "failed", c.value, fix=c.fix)


# ---------------------------------------------------------------- 小工具


def _homebrew_script(status: Path, pid_file: Path) -> str:
    """終端機跑的腳本：Homebrew 官方安裝程式要 bash 和 TTY，這是整個外掛唯一的 shell 腳本（由這裡產生）。"""
    st, pid = shlex.quote(str(status)), shlex.quote(str(pid_file))
    return f"""#!/bin/bash
# article-to-video 產生的：在終端機裡安裝 Homebrew（官方安裝程式）。跑完可以關掉這個視窗。
echo $$ > {pid}
clear
echo "【article-to-video】正在安裝 Homebrew（做影片要用的工具都靠它裝）"
echo
echo "下一步會出現「Password:」：請輸入你登入這台 Mac 的密碼，再按 return。"
echo "打字的時候畫面不會出現任何字或星號，這是正常的，照打就好。"
echo
if ! sudo -v; then
  echo 1 > {st}
  echo
  echo "密碼沒有通過（或這個帳號不是管理員）。請關掉這個視窗，回到 Claude 再說一次「安裝」。"
  exit 1
fi
NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL {INSTALL_SCRIPT_URL})"
code=$?
echo $code > {st}
echo
if [ $code -eq 0 ]; then
  echo "Homebrew 裝好了。可以關掉這個視窗，回到 Claude。"
else
  echo "Homebrew 沒裝成功（錯誤碼 $code）。請把這個視窗截圖，回到 Claude 說「回報問題」。"
fi
exit $code
"""


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

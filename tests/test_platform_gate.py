"""支援的 Mac（docs/adr/0002）：只支援 Apple Silicon＋macOS 15 以上。

ffmpeg-full 和它的依賴只有這些 Mac 有現成安裝包（Homebrew 的 bottle）；其他 Mac 上 `brew install ffmpeg-full`
會默默改成從原始碼編譯好幾個小時，常常失敗。所以「安裝」第一步、「檢查環境」第一項都先看這台 Mac，
裝 ffmpeg-full 之前再問一次 Homebrew 這台 Mac 拿不拿得到現成安裝包。

平台一律注入（假的 sysctl／sw_vers、假的 Homebrew），不連網、不真的裝任何東西。
"""

from __future__ import annotations

import subprocess

import pytest

from article_to_video.mac_platform import MacPlatform, detect_platform

from test_install import edge_ok  # noqa: F401 — fixture：edge-tts 試唸換成假的


def fake_system(arm64: str | None, version: str):
    """假的 sysctl／sw_vers：arm64＝`sysctl -n hw.optional.arm64` 印的字（None＝Intel 沒有這個鍵，結束碼 1）。"""

    def run(args: list[str]) -> subprocess.CompletedProcess:
        if args[:1] == ["sysctl"] and args[-1] == "hw.optional.arm64":
            if arm64 is None:
                return subprocess.CompletedProcess(args, 1, "", "sysctl: unknown oid 'hw.optional.arm64'")
            return subprocess.CompletedProcess(args, 0, arm64 + "\n", "")
        if args == ["sw_vers", "-productVersion"]:
            return subprocess.CompletedProcess(args, 0, version + "\n", "")
        raise AssertionError(f"沒想到會跑：{args}")

    return run


# ---------------------------------------------------------------- 看出這台 Mac


def test_rosetta_python_on_apple_silicon_still_counts_as_apple_silicon():
    """uv 帶的 Python 如果是 x86_64（Rosetta 翻譯），platform.machine() 會說 x86_64；看的要是硬體。"""
    p = detect_platform(run=fake_system("1", "15.1"), machine="x86_64")
    assert p == MacPlatform("arm64", "15.1")
    assert p.supported


def test_intel_and_old_macos_are_detected():
    assert detect_platform(run=fake_system(None, "15.6"), machine="x86_64") == MacPlatform("x86_64", "15.6")
    old = detect_platform(run=fake_system("1", "14.7.2"), machine="arm64")
    assert old == MacPlatform("arm64", "14.7.2") and old.major == 14
    assert not old.supported


def test_this_mac_is_supported():
    p = detect_platform()
    assert p.arch == "arm64" and p.major is not None and p.major >= 15
    assert p.supported


# ---------------------------------------------------------------- 安裝的第一步


def test_intel_mac_is_stopped_before_homebrew_is_installed(tmp_path):
    from test_install import FakeMac, forbidden

    mac = FakeMac(tmp_path, "x86_64")
    lines: list[str] = []
    report = mac.installer(platform=MacPlatform("x86_64", "15.6"), open_terminal=forbidden, act=forbidden,
                           log=lines.append).run()
    assert not report.ok
    assert [s.key for s in report.steps] == ["platform"]  # 後面的步驟都沒跑
    assert not mac.layout.brew.exists()
    told = "\n".join(lines)
    # 哪裡不對、怎麼自己確認、怎麼辦
    assert "Intel" in told
    assert "蘋果選單" in told and "關於這台 Mac" in told and "晶片" in told
    assert "聯絡 Danny" in told
    assert "沒有安裝任何東西" in told
    assert "Intel" in (mac.data / "install" / "install.log").read_text(encoding="utf-8")


def test_old_macos_on_apple_silicon_is_told_to_update_first(tmp_path):
    from test_install import FakeMac, forbidden

    mac = FakeMac(tmp_path, "arm64")
    lines: list[str] = []
    report = mac.installer(platform=MacPlatform("arm64", "14.7.2"), open_terminal=forbidden, act=forbidden,
                           log=lines.append).run()
    assert not report.ok and [s.key for s in report.steps] == ["platform"]
    told = "\n".join(lines)
    assert "macOS 14.7.2" in told and "15" in told
    assert "系統設定" in told and "一般" in told and "軟體更新" in told
    assert "關於這台 Mac" in told
    assert "安裝" in report.steps[0].fix  # 更新完再說一次「安裝」


def test_install_command_exits_nonzero_on_an_unsupported_mac(monkeypatch, tmp_path, capsys):
    from article_to_video import cli, installer, mac_platform
    from test_install import forbidden

    monkeypatch.setattr(mac_platform, "detect_platform", lambda: MacPlatform("x86_64", "15.6"))
    monkeypatch.setattr(installer, "run_streaming", forbidden)
    monkeypatch.setattr(installer, "open_in_terminal", forbidden)
    code = cli.main(["install", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "Intel" in out and "聯絡 Danny" in out
    assert "檢查中" not in out  # Homebrew 那些步驟都沒開始


def test_maintainers_can_force_the_install_on_an_unsupported_mac(monkeypatch, tmp_path, capsys, edge_ok):
    """維護者的逃生門：install --allow-source-build。作者看不到（--help 不列、skill 不提）。"""
    from article_to_video import cli, installer, mac_platform
    from test_install import forbidden

    monkeypatch.setattr(mac_platform, "detect_platform", lambda: MacPlatform("arm64", "14.7.2"))
    monkeypatch.setattr(installer, "run_streaming", forbidden)  # 這台其實都裝好了：只會略過
    monkeypatch.setattr(installer, "open_in_terminal", forbidden)
    code = cli.main(["install", "--allow-source-build", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "--allow-source-build" in out and "全部裝好了" in out
    assert code == 0
    with pytest.raises(SystemExit):
        cli.main(["install", "--help"])
    assert "allow-source-build" not in capsys.readouterr().out


# ---------------------------------------------------------------- 檢查環境的第一項


@pytest.mark.parametrize("mac, says, fix", [
    (MacPlatform("x86_64", "15.6"), "Intel", "聯絡 Danny"),
    (MacPlatform("arm64", "14.7.2"), "macOS 14.7.2", "軟體更新"),
])
def test_check_env_puts_this_mac_first_with_the_fix(monkeypatch, mac, says, fix):
    from article_to_video import mac_platform
    from article_to_video.environment import EnvironmentReport, environment_checks

    monkeypatch.setattr(mac_platform, "detect_platform", lambda: mac)
    first = environment_checks(None)[0]()
    assert first.key == "platform" and first.ok is False
    assert says in first.value
    assert fix in first.fix and "關於這台 Mac" in first.fix
    text = EnvironmentReport([first]).to_text()
    assert text.startswith("✗ 這台 Mac") and "修法：" in text
    # 總結不要叫作者說「安裝」補裝：這台 Mac 裝不起來
    summary = EnvironmentReport([first]).summary()
    assert "這台 Mac" in summary and "「安裝」就會補裝" not in summary


# ---------------------------------------------------------------- 裝 ffmpeg-full 之前：Homebrew 有沒有這台的現成安裝包


def install_on(mac, **kw):
    lines: list[str] = []
    report = mac.installer(log=lines.append, **kw).run()
    return {s.key: s for s in report.steps}, "\n".join(lines)


def test_without_ready_made_packages_brew_install_never_starts(tmp_path, edge_ok):
    """Homebrew 哪天不支援 macOS 15 了：靜態規則還放行，但問 Homebrew 就知道會從原始碼編，先停下來。"""
    from test_install import FakeMac, catalog

    mac = FakeMac(tmp_path, "arm64", bottles=catalog(unbottled=("ffmpeg-full", "libass")))
    mac.brew_already_installed()
    steps, told = install_on(mac, platform=MacPlatform("arm64", "15.7"))
    ff = steps["ffmpeg"]
    assert ff.status == "failed"
    assert not mac.layout.ffmpeg_full.exists()  # brew install ffmpeg-full 沒跑
    assert "原始碼" in ff.message and "ffmpeg-full" in ff.message and "libass" in ff.message
    assert "軟體更新" in ff.fix and "Danny" in ff.fix
    assert steps["chromium"].ok  # 不靠 ffmpeg 的照樣檢查


def test_ready_made_packages_let_brew_install_go_ahead(tmp_path, edge_ok):
    """裝好了、版本也是新的套件不用下載，沒有現成安裝包也沒關係。"""
    from test_install import FakeMac, catalog

    mac = FakeMac(tmp_path, "arm64", bottles=catalog(unbottled=("x264",), installed=("x264",)))
    mac.brew_already_installed()
    steps, told = install_on(mac)
    assert steps["ffmpeg"].status == "installed", steps["ffmpeg"]
    assert "現成安裝包" in told


def test_when_homebrew_cannot_be_asked_the_static_rule_decides(tmp_path, edge_ok):
    """查不了（離線之類）：照「Apple Silicon、macOS 15 以上」的規則繼續裝，紀錄裡說一聲。"""
    from test_install import FakeMac

    mac = FakeMac(tmp_path, "arm64", bottles=None)
    mac.brew_already_installed()
    steps, told = install_on(mac)
    assert steps["ffmpeg"].status == "installed", steps["ffmpeg"]
    assert "沒辦法先確認" in told and "macOS 15 以上" in told
    assert "沒辦法先確認" in (mac.data / "install" / "install.log").read_text(encoding="utf-8")


def test_maintainers_can_build_from_source_on_purpose(tmp_path, edge_ok):
    from test_install import FakeMac, catalog

    mac = FakeMac(tmp_path, "arm64", bottles=catalog(unbottled=("ffmpeg-full",)))
    mac.brew_already_installed()
    steps, told = install_on(mac, allow_source_build=True)
    assert steps["ffmpeg"].status == "installed"
    assert "原始碼" in told


def test_an_old_rosetta_homebrew_on_apple_silicon_is_explained(tmp_path, edge_ok):
    """從舊 Intel Mac 搬過來的 /usr/local Homebrew（Rosetta）只拿得到 Intel 的安裝包：ffmpeg-full 沒有。"""
    from article_to_video.homebrew import BrewLayout
    from test_install import FakeMac, catalog

    mac = FakeMac(tmp_path, "arm64", bottles=catalog("tahoe", unbottled=("ffmpeg-full",)))
    mac.brew_already_installed(BrewLayout.for_arch("x86_64", root=tmp_path / "mac").prefix)
    steps, told = install_on(mac)
    ff = steps["ffmpeg"]
    assert ff.status == "failed" and not mac.layout.ffmpeg_full.exists()
    assert "Rosetta" in ff.fix and "Danny" in ff.fix


# ---------------------------------------------------------------- skill：先看這台 Mac，什麼都還沒裝


def test_setup_skill_checks_this_mac_before_anything_else():
    from article_to_video.mac_platform import MIN_MACOS
    from test_setup_skills import allowed, commands_in, parse_skill

    _, rules, body = parse_skill("setup")
    cmds = commands_in(body)
    # 不用 uv、不用 Homebrew 就能跑的兩行，預先核准（不用作者按允許）
    assert cmds[:2] == ["sysctl -n hw.optional.arm64", "sw_vers -productVersion"]
    assert all(allowed(c, rules) for c in cmds[:2])
    gate = body[:body.index("sw_vers -productVersion") + 1500]
    assert body.index("sw_vers") < body.index("開始前先跟作者說") < body.index("uv --version")
    assert f"{MIN_MACOS}" in gate and "Apple Silicon" in gate and "Intel" in gate
    assert "關於這台 Mac" in gate and "軟體更新" in gate and "聯絡 Danny" in gate
    assert "停" in gate and "Homebrew" in gate
    # install 自己也會擋：印出「這台 Mac」✗ 時照它說的轉述、停下來
    assert "✗ 這台 Mac" in body


def test_skills_never_tell_the_author_about_the_maintainer_escape_hatch():
    from test_setup_skills import SKILLS

    for skill in SKILLS.glob("*/SKILL.md"):
        assert "allow-source-build" not in skill.read_text(encoding="utf-8"), skill


def test_check_env_skill_handles_an_unsupported_mac():
    from test_setup_skills import parse_skill

    _, _, body = parse_skill("check-env")
    start = body.index("- **這台 Mac")  # 第 3 步「依 ✗ 的種類處理」的第一種
    bullet = body[start:body.index("\n   - **", start + 1)]
    assert "軟體更新" in bullet and "聯絡 Danny" in bullet
    assert "不要" in bullet and "「安裝」" in bullet  # 不要叫他說「安裝」補裝：裝不起來


@pytest.mark.parametrize("files, macos, fits", [
    (["arm64_tahoe"], 26, True),
    (["arm64_sequoia"], 26, True),  # 舊版 macOS 的安裝包新版可以用
    (["all"], 15, True),
    (["arm64_tahoe", "arm64_golden_gate"], 15, False),  # 只有比這台新的
    (["tahoe", "sequoia"], 26, False),  # Intel 的
    (["arm64_linux", "x86_64_linux"], 26, False),
    ([], 26, False),
    (["arm64_some_future_name"], 28, True),  # 不認得的名字：Homebrew 列給這台看的，就信它
])
def test_which_ready_made_packages_fit_this_mac(files, macos, fits):
    import json
    from pathlib import Path

    from article_to_video.homebrew import BrewInfo, check_bottles

    def run(args):
        if args[1] == "deps":
            return subprocess.CompletedProcess(args, 0, "", "")
        info = {"name": "ffmpeg-full", "installed": [], "outdated": False,
                "bottle": {"stable": {"files": {t: {} for t in files}}} if files else {}}
        return subprocess.CompletedProcess(args, 0, json.dumps({"formulae": [info], "casks": []}), "")

    result = check_bottles(BrewInfo(Path("/opt/homebrew/bin/brew"), "Homebrew 7.0.7"), arch="arm64",
                           macos_major=macos, run=run)
    assert result.known
    assert result.from_source == (() if fits else ("ffmpeg-full",))

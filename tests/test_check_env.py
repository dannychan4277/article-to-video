"""「檢查環境」（#12）：逐項列出狀態與修法（含 edge-tts 連線），結束碼說有沒有問題。

這台 Mac 上真的跑（只讀＋開關一次無頭 Chromium）；edge-tts 連線一律不連網：
pytest 行程裡換成假的 Communicate，子行程靠 conftest 把 edge-tts 的 proxy 指到 127.0.0.1 的死埠。
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from article_to_video.environment import (
    check_edge_tts_online, check_homebrew, collect_environment, settings_check,
)
from article_to_video.homebrew import BrewLayout

from test_install import fake_brew

ALL_ITEMS = ["這台 Mac", "macOS", "CPU", "Python", "外掛版本", "Homebrew", "ffmpeg", "Playwright", "Chromium", "edge-tts（配音）",
             "edge-tts 連線", "uv", "字型", "硬碟", "頻道設定"]


def check_env(*args: str) -> subprocess.CompletedProcess:
    # 環境變數要在叫的當下才拿：conftest 的擋網設定（edge-tts proxy 指死埠）是 session fixture 才放進去的
    from conftest import run_cli

    assert os.environ.get("ARTICLE_TO_VIDEO_EDGE_TTS_PROXY", "").startswith("http://127.0.0.1:")
    return run_cli("check-env", *args, timeout=120)


class SpeakingCommunicate:
    """假的 edge_tts.Communicate：回一小段「聲音」。記下被叫的參數。"""

    calls: list[dict] = []

    def __init__(self, text, voice, **kwargs):
        SpeakingCommunicate.calls.append({"text": text, "voice": voice, **kwargs})

    async def stream(self):
        yield {"type": "audio", "data": b"\xff\xf3" * 200}


class UnreachableCommunicate:
    def __init__(self, text, voice, **kwargs):
        pass

    async def stream(self):
        raise OSError("Cannot connect to host speech.platform.bing.com:443")
        yield  # pragma: no cover


@pytest.fixture(scope="module")
def offline_run(tmp_path_factory):
    """這台 Mac 的「檢查環境」：edge-tts 連不上（測試不准連網）、資料位置是空的（還沒設定頻道名）。"""
    empty = tmp_path_factory.mktemp("還沒設定")
    return check_env("--data-dir", str(empty))


def test_check_env_lists_every_item_with_status_and_fix(offline_run):
    out = offline_run.stdout
    for item in ALL_ITEMS:
        assert item in out, f"少了「{item}」這一項：\n{out}"
    lines = out.splitlines()

    def line_of(name: str) -> str:
        return next(ln for ln in lines if ln[:1] in "✓✗・" and ln[2:].startswith(name))

    # 第一項是這台 Mac 支不支援（Apple Silicon＋macOS 15 以上，docs/adr/0002）
    first = next(ln for ln in lines if ln[:1] in "✓✗・")
    assert first.startswith("✓ 這台 Mac") and "Apple Silicon" in first and "macOS" in first, first
    # 這台 Mac 都裝好了
    for name in ["Homebrew", "ffmpeg", "Playwright", "Chromium", "edge-tts（配音）", "uv", "字型"]:
        assert line_of(name).startswith("✓"), line_of(name)
    # 有問題的兩項：打勾打叉、下一行就是修法
    for name, hint in [("edge-tts 連線", "網路"), ("頻道設定", "設定")]:
        ln = line_of(name)
        assert ln.startswith("✗"), ln
        fix = lines[lines.index(ln) + 1]
        assert fix.strip().startswith("修法：") and hint in fix, fix
    assert "有 2 項要處理" in out
    assert offline_run.returncode == 1


def test_check_env_json_gives_a_fix_for_every_problem(tmp_path):
    proc = check_env("--json", "--data-dir", str(tmp_path))
    checks = json.loads(proc.stdout)["checks"]
    keys = {c["key"] for c in checks}
    assert {"homebrew", "ffmpeg", "chromium", "edge-tts", "edge-tts-online", "uv", "fonts", "settings"} <= keys
    problems = [c for c in checks if c["ok"] is False]
    assert {c["key"] for c in problems} == {"edge-tts-online", "settings"}
    assert all(c["fix"] for c in problems)
    assert proc.returncode == 1


def test_check_env_exits_zero_when_everything_works(monkeypatch, capsys, user_data_dir):
    import edge_tts

    from article_to_video import cli

    monkeypatch.setattr(edge_tts, "Communicate", SpeakingCommunicate)
    code = cli.main(["check-env", "--data-dir", str(user_data_dir)])
    out = capsys.readouterr().out
    assert not [ln for ln in out.splitlines() if ln.startswith("✗")], out
    assert "全部正常" in out
    assert code == 0


def test_edge_tts_check_passes_when_a_voice_comes_back(monkeypatch):
    import edge_tts

    SpeakingCommunicate.calls.clear()
    monkeypatch.setattr(edge_tts, "Communicate", SpeakingCommunicate)
    c = check_edge_tts_online()
    assert c.ok is True and "連得上" in c.value
    [call] = SpeakingCommunicate.calls
    assert call["voice"].startswith("zh-TW-")  # 用的是台灣的聲音


def test_edge_tts_check_explains_the_fix_when_unreachable(monkeypatch):
    import edge_tts

    monkeypatch.setattr(edge_tts, "Communicate", UnreachableCommunicate)
    c = check_edge_tts_online()
    assert c.ok is False and "連不上" in c.value
    assert "網路" in c.fix and "回報問題" in c.fix


def test_edge_tts_check_never_reaches_the_internet_in_tests():
    """沒換掉 Communicate：conftest 的 proxy（127.0.0.1 死埠）＋擋網，只會得到「連不上」。"""
    c = check_edge_tts_online()
    assert c.ok is False


@pytest.mark.parametrize("arch", ["arm64", "x86_64"])
def test_homebrew_check_on_both_prefixes(tmp_path, arch):
    layout = BrewLayout.for_arch(arch, root=tmp_path)
    missing = check_homebrew(layout)
    assert missing.ok is False and "安裝" in missing.fix
    brew = fake_brew(layout.prefix)
    found = check_homebrew(layout)
    assert found.ok is True and "Homebrew 4.9.9" in found.value and str(brew) in found.value
    assert found.detail["arch"] == arch


def test_settings_check_reads_the_channel_from_the_data_dir(tmp_path, user_data_dir):
    assert settings_check(user_data_dir)().ok is True
    assert "測試頻道" in settings_check(user_data_dir)().value
    empty = settings_check(tmp_path)()
    assert empty.ok is False and "頻道名" in empty.value


def test_one_broken_check_does_not_stop_the_others():
    from article_to_video.environment import env_check

    @env_check("boom", "會壞的檢查")
    def boom():
        raise RuntimeError("壞了")

    report = collect_environment([boom, settings_check(None)])
    assert [c.key for c in report.checks] == ["boom", "settings"]
    assert report.checks[0].ok is False and "壞了" in report.checks[0].value

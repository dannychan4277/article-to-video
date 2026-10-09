"""測試共用設定：測試不准連網，只允許連 127.0.0.1（本機伺服器）。

擋的是這個 pytest 行程裡的 socket；被測程式另開的子行程（render.py、Chromium）不受這裡管，
所以測試用的分鏡設定一律不放 http 圖片。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
MINI_PROJECT = FIXTURES / "mini_project"

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}

# 建置完不要在 Danny 的瀏覽器裡打開審片頁（CLI 子行程會繼承這個環境變數）
os.environ["ARTICLE_TO_VIDEO_NO_OPEN"] = "1"


class NetworkBlockedError(RuntimeError):
    pass


def _check_host(host: object) -> None:
    if isinstance(host, bytes):
        host = host.decode()
    if host not in _LOCAL_HOSTS:
        raise NetworkBlockedError(f"測試不准連網：想連 {host!r}，只允許 127.0.0.1")


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


def _guarded_connect(self: socket.socket, address):  # type: ignore[no-untyped-def]
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _check_host(address[0])
    return _real_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address):  # type: ignore[no-untyped-def]
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _check_host(address[0])
    return _real_connect_ex(self, address)


def _guarded_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
    if host is not None:
        _check_host(host)
    return _real_getaddrinfo(host, *args, **kwargs)


@pytest.fixture(autouse=True, scope="session")
def _block_network():
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[assignment]
    # 公有數據（FRED）預設指到本機沒人聽的埠：子行程（CLI 建置）不受上面的 socket 擋網管，靠這個保證不會真的連 FRED。
    # 要假 FRED 的測試用 test_public_data.py 的 fred fixture 蓋掉。
    # 環境檢查的 edge-tts 連線測試（回報問題、檢查環境的子行程都會跑）同理：proxy 指到死埠，只會得到「連不上」。
    dead_ends = {
        "ARTICLE_TO_VIDEO_FRED_URL": "http://127.0.0.1:9/graph/fredgraph.csv",
        "ARTICLE_TO_VIDEO_EDGE_TTS_PROXY": "http://127.0.0.1:9",
    }
    saved = {k: os.environ.get(k) for k in dead_ends}
    os.environ.update(dead_ends)
    try:
        yield
    finally:
        socket.socket.connect = _real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = _real_connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = _real_getaddrinfo  # type: ignore[assignment]
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


TEST_CHANNEL = "測試頻道"


def cli_env(**extra: str) -> dict[str, str]:
    """跑指令列子行程用的環境：輸出一律 UTF-8，再蓋上 extra。"""
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **extra}


def run_cli(*args: str, env: dict[str, str] | None = None, stdin: str | None = None,
            cwd: Path | None = None) -> subprocess.CompletedProcess:
    """用子行程跑指令列（`python -m article_to_video …`，跟 Claude 叫的一樣），stdout、stderr 照 UTF-8 解。
    env 是要加上去／蓋掉的環境變數。"""
    return subprocess.run([sys.executable, "-m", "article_to_video", *args], input=stdin, capture_output=True,
                          text=True, encoding="utf-8", env=cli_env(**(env or {})), cwd=cwd)


@pytest.fixture(autouse=True, scope="session")
def videos_dir(tmp_path_factory):
    """影片資料夾（新專案、出片的位置）一律放 tmp，絕不寫進真正的 ~/Movies/解說影片；子行程也吃得到。"""
    d = tmp_path_factory.mktemp("影片") / "解說影片"
    saved = os.environ.get("ARTICLE_TO_VIDEO_VIDEOS")
    os.environ["ARTICLE_TO_VIDEO_VIDEOS"] = str(d)
    try:
        yield d
    finally:
        if saved is None:
            os.environ.pop("ARTICLE_TO_VIDEO_VIDEOS", None)
        else:
            os.environ["ARTICLE_TO_VIDEO_VIDEOS"] = saved


@pytest.fixture(autouse=True, scope="session")
def user_data_dir(tmp_path_factory):
    """使用者設定一律放 tmp，絕不讀寫真正的 ~/Library/Application Support/article-to-video。

    用環境變數 ARTICLE_TO_VIDEO_DATA 指過去，被測程式另開的子行程也吃得到。
    裡面先放一份「裝好之後」的使用者設定：只填了頻道名（片尾卡要用），其他用預設。
    """
    d = tmp_path_factory.mktemp("使用者資料")
    (d / "settings.json").write_text(json.dumps({"channel": TEST_CHANNEL}, ensure_ascii=False), encoding="utf-8")
    saved = {k: os.environ.get(k) for k in ("ARTICLE_TO_VIDEO_DATA", "CLAUDE_PLUGIN_DATA")}
    os.environ["ARTICLE_TO_VIDEO_DATA"] = str(d)
    os.environ.pop("CLAUDE_PLUGIN_DATA", None)
    try:
        yield d
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@dataclass(frozen=True)
class BuiltProject:
    dir: Path
    video: Path
    srt: Path
    stdout: str


@pytest.fixture(scope="session")
def built_project(tmp_path_factory, user_data_dir) -> BuiltProject:
    """把 tests/fixtures/mini_project 複製到 tmp（路徑故意帶中文和空白），用 CLI＋假配音層建置一次，整個 session 共用。"""
    root = tmp_path_factory.mktemp("影片") / "解說影片 測試" / "2026-10-09-殖利率曲線"
    shutil.copytree(MINI_PROJECT, root)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "article_to_video", "build", str(root), "--tts", "fake"],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert proc.returncode == 0, f"建置失敗：\n{proc.stdout}\n{proc.stderr}"
    return BuiltProject(dir=root, video=root / "成片.mp4", srt=root / "字幕.srt", stdout=proc.stdout)


@dataclass(frozen=True)
class PreviewedProject:
    dir: Path
    stdout: str
    seconds: float


@pytest.fixture(scope="session")
def previewed_project(tmp_path_factory) -> PreviewedProject:
    """把 mini_project 複製到 tmp，用 CLI 的預覽模式（只出靜態圖＋審片頁）跑一次，整個 session 共用。"""
    import time

    root = tmp_path_factory.mktemp("影片") / "解說影片 預覽" / "2026-10-09-殖利率曲線"
    shutil.copytree(MINI_PROJECT, root)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "article_to_video", "preview", str(root), "--tts", "fake"],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    seconds = time.monotonic() - t0
    assert proc.returncode == 0, f"預覽失敗：\n{proc.stdout}\n{proc.stderr}"
    return PreviewedProject(dir=root, stdout=proc.stdout, seconds=seconds)

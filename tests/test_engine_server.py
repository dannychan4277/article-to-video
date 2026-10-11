"""內嵌引擎的本機網頁伺服器（render.py、qa.py 開給 Chromium 載入 clip.html、腳本、字型用的那一個）。

Chromium 並行載入時可能先開好連線、還沒送請求（閒置連線）。單執行緒的伺服器會卡在那條連線上，其他檔案都載不到
（上游 huashu-art-motion 57d6760 修的就是這個，Windows 上會發生）。這裡不開瀏覽器：照上游測試的做法，從兩支腳本裡
拿出伺服器的那幾行（處理器 Q、伺服器類別、srv＝…）原樣執行，只連 127.0.0.1。

多執行緒之後，Chromium 同時載入三個 7 MB 多的字型，macOS 在機器很忙時偶爾回「No buffer space available」（ENOBUFS），
那個字型檔沒送完、整個片段報「字型缺字」失敗（實測：同時渲 8 個、6 輪，多執行緒 48 次失敗 5 次，單執行緒 0 次）。
所以檔案內容一次只送一個（請求、閒置連線照樣各自一條執行緒），遇到 ENOBUFS 等一下再送剩下的
（send 失敗時一個位元組都沒送出，重送不會重複）。
"""

from __future__ import annotations

import ast
import errno
import functools
import http.server
import socket
import socketserver
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent / "src" / "article_to_video" / "engine"
PAYLOAD = b"window.assetLoaded = true;"


def _server_from(script: Path, root: Path) -> socketserver.TCPServer:
    """執行腳本裡定義伺服器的那幾個頂層敘述（class Q、class LocalServer、srv = …），不跑指令列、不開瀏覽器。"""
    tree = ast.parse(script.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body
             if isinstance(n, ast.ClassDef) and n.name in ("Q", "LocalServer")
             or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "srv" for t in n.targets)]
    ns = {"http": http, "socketserver": socketserver, "functools": functools, "urllib": urllib, "errno": errno,
          "time": time, "threading": threading, "root": root, "spec": None, "ALLOWED": set()}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(script), "exec"), ns)
    return ns["srv"]


@pytest.mark.parametrize("script", ["render.py", "qa.py"])
def test_an_idle_browser_connection_does_not_stall_the_engine_page_server(script, tmp_path):
    (tmp_path / "asset.js").write_bytes(PAYLOAD)
    srv = _server_from(ENGINE / script, tmp_path)
    host, port = srv.server_address
    assert host == "127.0.0.1"  # 只在本機
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    idle = socket.create_connection((host, port), timeout=3)  # 先開好、什麼都不送的連線
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 不走系統 proxy

    def fetch(_: int) -> tuple[int, bytes]:
        with opener.open(f"http://127.0.0.1:{port}/asset.js", timeout=2) as r:
            return r.status, r.read()

    stopper = threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True)
    try:
        with ThreadPoolExecutor(8) as pool:
            got = list(pool.map(fetch, range(32)))
        assert got == [(200, PAYLOAD)] * 32  # 32 個檔案同時載入，全部拿到
    finally:
        stopper.start()
        stopper.join(2)
        idle.close()
    assert not stopper.is_alive()  # 閒置連線還開著，伺服器也收得掉（渲染完不會卡住）
    assert socketserver.ThreadingTCPServer.daemon_threads is False  # 只改自己的伺服器類別，不動標準庫的預設


@pytest.mark.parametrize("script", ["render.py", "qa.py"])
def test_a_moment_without_socket_buffers_does_not_lose_part_of_a_font_file(script, tmp_path, monkeypatch):
    big = bytes(range(256)) * (3 * 1024 * 16)  # 12 MB，比外掛附帶的字型還大
    (tmp_path / "font.woff").write_bytes(big)
    srv = _server_from(ENGINE / script, tmp_path)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    calls = {"n": 0}
    real_send, real_sendall = socket.socket.send, socket.socket.sendall

    def flaky(real):
        def call(self, data, *a):  # 送大塊資料（檔案內容）時，每三次有一次系統沒有緩衝區可用
            if len(data) > 32 * 1024:
                calls["n"] += 1
                if calls["n"] % 3 == 1:
                    raise OSError(errno.ENOBUFS, "No buffer space available")
            return real(self, data, *a)
        return call

    monkeypatch.setattr(socket.socket, "send", flaky(real_send))
    monkeypatch.setattr(socket.socket, "sendall", flaky(real_sendall))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/font.woff", timeout=10) as r:
            got = r.read()
    finally:
        srv.shutdown()
        srv.server_close()
    assert calls["n"] > 3  # 真的遇到好幾次
    assert got == big  # 一個位元組都沒少、沒重複

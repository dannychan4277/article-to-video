"""測試共用設定：測試不准連網，只允許連 127.0.0.1（本機伺服器）。

擋的是這個 pytest 行程裡的 socket；被測程式另開的子行程（render.py、Chromium）不受這裡管，
所以測試用的分鏡設定一律不放 http 圖片。
"""

from __future__ import annotations

import socket

import pytest

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


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
    try:
        yield
    finally:
        socket.socket.connect = _real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = _real_connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = _real_getaddrinfo  # type: ignore[assignment]

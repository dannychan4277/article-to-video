import socket
import threading

import pytest


def test_outside_connection_is_blocked():
    with pytest.raises(RuntimeError, match="測試不准連網"):
        socket.create_connection(("example.com", 80), timeout=1)


def test_outside_ip_is_blocked():
    with pytest.raises(RuntimeError, match="測試不准連網"):
        socket.create_connection(("93.184.215.14", 80), timeout=1)


def test_localhost_server_still_works():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        conn.sendall(b"ok")
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    with socket.create_connection(("127.0.0.1", port), timeout=2) as c:
        assert c.recv(2) == b"ok"
    t.join(2)
    srv.close()

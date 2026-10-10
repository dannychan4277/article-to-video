"""公有數據抓取（#7）：分鏡設定寫系列名稱，建置自動抓白名單裡的公有領域數據畫圖、自動帶出處。

HTTP 用 127.0.0.1 上的假 FRED：伺服器照 FRED 的 fredgraph.csv 介面回 tests/fixtures/fred/*.csv 裡
落在 cosd～coed 的那幾行。fixture 是 2026-10-09 從 fredgraph.csv 錄下的 2024-12-16～2025-01-10
（DGS10、DGS2、THREEFYTP10 是現行格式：表頭 observation_date、缺值空白；DFII10 改成 FRED 舊格式：
表頭 DATE、缺值寫「.」）。測試不連網。

預期值都是對著 fixture 手算的（每個測試旁邊寫算式），不是拿程式算出來再抄回來。
"""

from __future__ import annotations

import json
import shutil
import socket
import threading
from dataclasses import dataclass, field
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from article_to_video.build import build_project, plan_build
from article_to_video.project import ProjectError

from conftest import FIXTURES, MINI_PROJECT

FRED_FIXTURES = FIXTURES / "fred"


@dataclass
class FakeFred:
    url: str = ""
    requests: list[dict[str, str]] = field(default_factory=list)
    status: int | None = None
    """設了就每個請求都回這個 HTTP 狀態（模擬 FRED 出錯）。"""


@pytest.fixture
def fred(monkeypatch):
    state = FakeFred()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            state.requests.append(q)
            f = FRED_FIXTURES / f"{q.get('id', '')}.csv"
            if state.status is None and u.path == "/graph/fredgraph.csv" and f.is_file():
                lines = f.read_text(encoding="utf-8").splitlines()
                rows = [ln for ln in lines[1:] if q["cosd"] <= ln.split(",")[0] <= q["coed"]]
                code, ctype, body = 200, "application/csv", "\n".join([lines[0], *rows]) + "\n"
            else:
                code, ctype, body = state.status or 404, "text/html", "<!DOCTYPE html><html>Error</html>"
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}/graph/fredgraph.csv"
    monkeypatch.setenv("ARTICLE_TO_VIDEO_FRED_URL", state.url)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def make_project(tmp_path: Path, shots: list[dict]) -> Path:
    root = tmp_path / "專案"
    shutil.copytree(MINI_PROJECT, root)
    (root / "shots.json").write_text(json.dumps({"shots": shots}, ensure_ascii=False), encoding="utf-8")
    return root


def chart_shot(public: dict, *, sid: str = "SH01", chart: str = "line", **data) -> dict:
    return {
        "id": sid,
        "start": {"segment": "s01"},
        "chart": {
            "data": {"title": "殖利率走高", "unit": "%", "chart": chart, "decimals": 2, "publicData": public, **data},
            "cues": [{"at": 0, "kind": "title"}, {"at": 0.5, "kind": chart}],
        },
    }


def engine_data(root: Path, shot: int = 0) -> dict:
    return plan_build(root, tts="fake").clips[shot].spec["data"]


def test_line_chart_gets_monthly_averages_of_the_named_series(tmp_path, fred):
    root = make_project(tmp_path, [chart_shot({"series": "DGS10", "from": "2024-12-01", "to": "2025-01-31", "every": "month"})])
    data = engine_data(root)
    # 2024-12：4.39 4.40 4.50 4.57 4.52 4.59 4.59 4.58 4.62 4.55 4.58（12-25 缺值不算）＝ 49.89 ÷ 11 ＝ 4.53545…
    # 2025-01：4.57 4.60 4.62 4.67 4.67 4.68 4.77（01-01 缺值不算）＝ 32.58 ÷ 7 ＝ 4.65428…
    assert data["series"] == [{"label": "2024-12", "value": 4.5355}, {"label": "2025-01", "value": 4.6543}]
    assert "publicData" not in data


def test_cli_builds_a_video_from_a_public_data_chart(tmp_path, fred):
    root = make_project(tmp_path, [chart_shot({"series": "THREEFYTP10", "from": "2024-12-16", "to": "2024-12-20", "every": "day"})])
    from conftest import run_cli

    proc = run_cli("build", root, "--tts", "fake")
    assert proc.returncode == 0, f"建置失敗：\n{proc.stdout}\n{proc.stderr}"
    assert (root / "成片.mp4").is_file()
    spec = json.loads((root / "build" / "16x9" / "specs" / "SH01.json").read_text(encoding="utf-8"))
    # THREEFYTP10 2024-12-16～20 五個交易日，原樣畫（fixture 值）
    assert [p["value"] for p in spec["data"]["series"]] == [0.5798, 0.5854, 0.6412, 0.6778, 0.6682]
    assert "THREEFYTP10" in spec["data"]["source"]
    assert [r["id"] for r in fred.requests] == ["THREEFYTP10"]


def test_source_line_names_the_series_the_publisher_and_the_fetch_date(tmp_path, fred):
    root = make_project(tmp_path, [chart_shot({"series": "DGS10", "from": "2024-12-01", "to": "2025-01-31", "every": "month"})])
    source = engine_data(root)["source"]
    for needle in ("FRED", "聯準會", "DGS10", "月平均", f"{date.today().isoformat()} 擷取"):
        assert needle in source, f"來源行少了「{needle}」：{source}"


DGS10_MONTHLY = {"series": "DGS10", "from": "2024-12-01", "to": "2025-01-31", "every": "month"}


def test_inflation_expectation_is_ten_year_minus_real_yield_citing_both_series(tmp_path, fred):
    root = make_project(tmp_path, [chart_shot({"series": "DGS10-DFII10", "from": "2025-01-06", "to": "2025-01-10", "every": "day"})])
    data = engine_data(root)
    # DGS10 − DFII10（DFII10 的 fixture 是 FRED 舊格式）：
    # 01-06 4.62−2.28＝2.34；01-07 4.67−2.29＝2.38；01-08 4.67−2.27＝2.40；01-09 4.68−2.29＝2.39；01-10 4.77−2.34＝2.43
    assert data["series"] == [
        {"label": "2025-01-06", "value": 2.34}, {"label": "2025-01-07", "value": 2.38},
        {"label": "2025-01-08", "value": 2.40}, {"label": "2025-01-09", "value": 2.39},
        {"label": "2025-01-10", "value": 2.43},
    ]
    for needle in ("DGS10", "DFII10", "通膨預期", "自算", f"{date.today().isoformat()} 擷取"):
        assert needle in data["source"], f"來源行少了「{needle}」：{data['source']}"
    assert {p.name for p in (root / "public_data").iterdir()} == {"DGS10.json", "DFII10.json"}


def offline(monkeypatch) -> None:
    """把 FRED 網址指到 127.0.0.1 上一個沒人聽的埠：等於沒網路。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("ARTICLE_TO_VIDEO_FRED_URL", f"http://127.0.0.1:{port}/graph/fredgraph.csv")


def test_fetched_numbers_are_kept_in_the_project_so_rebuilds_work_offline(tmp_path, fred, monkeypatch):
    root = make_project(tmp_path, [chart_shot(DGS10_MONTHLY)])
    first = engine_data(root)
    assert len(fred.requests) == 1
    assert (root / "public_data" / "DGS10.json").is_file()

    offline(monkeypatch)
    again = engine_data(root)
    assert again["series"] == first["series"]
    assert again["source"] == first["source"]


def test_source_line_keeps_the_date_the_numbers_were_fetched(tmp_path, fred):
    root = make_project(tmp_path, [chart_shot(DGS10_MONTHLY)])
    engine_data(root)
    snap = root / "public_data" / "DGS10.json"
    saved = json.loads(snap.read_text(encoding="utf-8"))
    assert saved["fetched"] == date.today().isoformat()
    snap.write_text(json.dumps({**saved, "fetched": "2025-01-11"}, ensure_ascii=False), encoding="utf-8")

    assert "2025-01-11 擷取" in engine_data(root)["source"]
    assert len(fred.requests) == 1


def assert_blocked(root: Path, needles: list[str]) -> str:
    with pytest.raises(ProjectError) as err:
        build_project(root, tts="fake")
    msg = str(err.value)
    for needle in needles:
        assert needle in msg, f"錯誤訊息少了「{needle}」：\n{msg}"
    assert not (root / "成片.mp4").exists()
    assert not (root / "build" / "16x9" / "clips").exists()  # 渲染前就擋下，沒有空圖
    return msg


def test_no_network_stops_the_build_with_a_clear_message(tmp_path, monkeypatch):
    offline(monkeypatch)
    root = make_project(tmp_path, [chart_shot(DGS10_MONTHLY)])
    assert_blocked(root, ["分鏡 SH01", "DGS10", "連不上 FRED", "網路"])
    assert not (root / "public_data" / "DGS10.json").exists()


def test_fred_error_stops_the_build(tmp_path, fred):
    fred.status = 500
    root = make_project(tmp_path, [chart_shot(DGS10_MONTHLY)])
    assert_blocked(root, ["分鏡 SH01", "DGS10", "HTTP 500"])


def test_a_range_with_no_data_is_an_error_not_an_empty_chart(tmp_path, fred):
    root = make_project(tmp_path, [chart_shot({**DGS10_MONTHLY, "from": "2030-01-01", "to": "2030-12-31"})])
    assert_blocked(root, ["分鏡 SH01", "DGS10", "2030-01-01～2030-12-31", "沒有數據"])
    assert not (root / "public_data" / "DGS10.json").exists()


def test_each_drawn_value_says_which_series_and_dates_it_came_from(tmp_path, fred):
    """數字對帳（#5）用這份把畫面上的數字歸成「白名單公有數據」：每個值是哪個系列、哪幾天、哪天抓的。"""
    shots = [
        chart_shot(DGS10_MONTHLY),
        {"id": "SH02", "start": {"segment": "s02"}, "continues": True, "chart": {"data": {}, "cues": []}},
    ]
    plan = plan_build(make_project(tmp_path, shots), tts="fake")
    today = date.today()
    sh01 = plan.public_data["SH01"]
    assert sh01.series == ("DGS10",)
    jan = sh01.values[1]
    # 2025-01 那個點：01-02～01-10 共 7 個交易日的平均（01-01 缺值）
    assert (jan.label, jan.value, jan.first, jan.last, jan.count) == ("2025-01", 4.6543, date(2025, 1, 2), date(2025, 1, 10), 7)
    assert sh01.fetched == {"DGS10": today}
    assert sh01.source == plan.clips[0].spec["data"]["source"]
    text = sh01.describe(1)
    for needle in ("DGS10", "2025-01-02", "2025-01-10", "7", today.isoformat()):
        assert needle in text, f"出處說明少了「{needle}」：{text}"
    assert plan.public_data["SH02"] == sh01  # 接續分鏡畫的是同一組數字


@pytest.mark.parametrize("series, needles", [
    ("SP500", ["「SP500」", "指數", "授權", "文章裡"]),
    ("NASDAQCOM", ["「NASDAQCOM」", "指數"]),
    ("BAMLH0A0HYM2", ["「BAMLH0A0HYM2」", "指數"]),
    ("T10YIE", ["「T10YIE」", "版權", "DGS10-DFII10"]),
    ("UNRATE", ["「UNRATE」", "白名單", "DGS10", "DGS2", "DFII10", "THREEFYTP10", "文章裡"]),
])
def test_index_and_unlisted_series_are_refused_with_a_reason(tmp_path, fred, series, needles):
    root = make_project(tmp_path, [chart_shot({**DGS10_MONTHLY, "series": series})])
    assert_blocked(root, ["分鏡 SH01", *needles])
    assert fred.requests == []


MISUSE = {
    "series 和 publicData 都寫": (dict(series=[{"label": "a", "value": 1}]), ["分鏡 SH01", "series", "publicData", "擇一"]),
    "自己寫 source": (dict(source="來源：我自己"), ["分鏡 SH01", "source", "自動"]),
    "K 線": (dict(chart="candle"), ["分鏡 SH01", "candle"]),
    "every 打錯": (dict(publicData={**DGS10_MONTHLY, "every": "week"}), ["分鏡 SH01", "every", "week"]),
    "日期反了": (dict(publicData={**DGS10_MONTHLY, "from": "2025-02-01"}), ["分鏡 SH01", "from", "晚於"]),
    "xTicks 超過抓到的點數": (dict(xTicks=[0, 5]), ["分鏡 SH01", "xTicks", "2 個點"]),
    "yRange 沒包住抓到的數字": (dict(yRange=[4, 4.6]), ["分鏡 SH01", "yRange", "沒包住", "4.6543"]),
}


@pytest.mark.parametrize("case", list(MISUSE))
def test_misused_public_data_is_blocked_with_a_clear_message(tmp_path, fred, case):
    edit, needles = MISUSE[case]
    shot = chart_shot(DGS10_MONTHLY)
    shot["chart"]["data"].update(edit)
    assert_blocked(make_project(tmp_path, [shot]), needles)


def test_widening_the_date_range_fetches_again(tmp_path, fred):
    root = make_project(tmp_path, [chart_shot({**DGS10_MONTHLY, "to": "2024-12-31"})])
    assert [p["label"] for p in engine_data(root)["series"]] == ["2024-12"]
    (root / "shots.json").write_text(json.dumps({"shots": [chart_shot(DGS10_MONTHLY)]}, ensure_ascii=False), encoding="utf-8")
    assert [p["label"] for p in engine_data(root)["series"]] == ["2024-12", "2025-01"]
    assert len(fred.requests) == 2

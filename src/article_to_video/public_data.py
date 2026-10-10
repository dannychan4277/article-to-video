"""公有數據：分鏡設定寫系列名稱，建置時抓白名單裡的公有領域數據，換成圖表的數列並自動寫來源行。

分鏡設定（財經圖表分鏡的 chart.data 裡，取代 series 與 source；接續分鏡沿用上一個，不能寫）：

    "publicData": {"series": "DGS10", "from": "2024-01-01", "to": "2024-12-31", "every": "month"}

- series：白名單系列代號（WHITELIST：DGS10、DGS2、DFII10、THREEFYTP10），或兩個相減 "A-B"（我們自算），
  例 "DGS10-DFII10"＝通膨預期、"DGS10-DGS2"＝十年期減兩年期。指數類（SP500…）、有版權的（T10YIE）、
  白名單以外的一律拒絕，錯誤訊息說明原因（refusal）。
- from／to：日期（YYYY-MM-DD），含頭含尾。
- every：一個點代表多久——day（每個交易日原值）、month／quarter／year（期間內交易日的平均）。
  標籤：2025-01-03、2025-01、2025Q1、2025。缺值（假日）不算。
- 來源行自動寫成「來源：FRED，聯準會（系列）；［自算算式，］月平均；擷取日期 擷取」。

快照：每個系列存成專案的 public_data/<代號>.json（期間、擷取日期、網址、每天的值）。快照涵蓋所有分鏡要的期間就
直接用（重建不用網路、數字不會變）；不夠就抓一次涵蓋全部期間的數據覆寫快照（擷取日期跟著更新）。想更新數據就刪掉快照。
抓不到（沒網路、FRED 出錯、那段期間沒數據）一律 ProjectError，建置在配音與渲染之前就停，不會產生空圖。

抓取：FRED 免金鑰的 fredgraph.csv。環境變數 ARTICLE_TO_VIDEO_FRED_URL 可改網址（測試指到 127.0.0.1 的假 FRED）。
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import certifi

from article_to_video.project import ChartConfig, Project, ProjectError, y_range_covers

__all__ = [
    "FRED_CSV_URL",
    "WHITELIST",
    "PublicChart",
    "PublicRequest",
    "PublicValue",
    "parse_request",
    "refusal",
    "resolve_public_data",
]

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
URL_ENV = "ARTICLE_TO_VIDEO_FRED_URL"
TIMEOUT = 30
# FRED 前面有擋機器人的 CDN：實測 User-Agent 只寫 "article-to-video" 會被晾到逾時，「名稱/版本 (+網址)」就正常（2026-10-09）
USER_AGENT = "article-to-video/0.1 (+https://github.com/dannychan4277/article-to-video)"


@dataclass(frozen=True)
class SeriesInfo:
    id: str
    name: str
    short: str


# FRED 系列頁標示「Public Domain: Citation Requested」、來源是聯準會理事會的系列（2026-10-09 逐頁查過）。
WHITELIST: dict[str, SeriesInfo] = {s.id: s for s in (
    SeriesInfo("DGS10", "美國十年期公債殖利率", "十年期"),
    SeriesInfo("DGS2", "美國兩年期公債殖利率", "兩年期"),
    SeriesInfo("DFII10", "美國十年期實質殖利率（抗通膨公債）", "實質殖利率"),
    SeriesInfo("THREEFYTP10", "美國十年期公債期限溢價（Kim-Wright 模型）", "期限溢價"),
)}

# 指數類：數據屬於指數公司、有版權（FRED 上 SP500 註明沒有 S&P 書面許可不得轉載）。代號 → 名稱；前綴 → 名稱。
INDEX_SERIES = {
    "SP500": "標普五百", "DJIA": "道瓊工業指數", "NASDAQCOM": "那斯達克綜合指數", "NASDAQ100": "那斯達克一百指數",
    "VIXCLS": "VIX 波動率指數", "NIKKEI225": "日經 225",
}
INDEX_PREFIXES = {"WILL": "威爾夏指數", "BAML": "ICE 美銀債券指數"}
# 不是指數、但 FRED 標示有版權的（2026-10-09 查過系列頁）。代號 → 建議改用的寫法。
COPYRIGHTED = {"T10YIE": '通膨預期請改寫 "DGS10-DFII10"（十年期減實質殖利率，用兩個公有領域系列自算）'}


def refusal(sid: str) -> str:
    """不抓這個系列的原因（繁中，給作者看）。"""
    alt = "要用這個數字，請用文章裡寫的數字或文章裡的圖。"
    name = INDEX_SERIES.get(sid) or next((n for p, n in INDEX_PREFIXES.items() if sid.startswith(p)), None)
    if name:
        return (f"「{sid}」是指數（{name}）：指數的數據屬於指數公司、有版權，FRED 也註明要先取得授權才能轉載，"
                f"所以不自動抓。{alt}")
    if sid in COPYRIGHTED:
        return f"「{sid}」在 FRED 上標示有版權，不自動抓。{COPYRIGHTED[sid]}。"
    listed = "、".join(f"{s.id}（{s.short}）" for s in WHITELIST.values())
    return (f"「{sid}」不在公有領域白名單裡，不自動抓。FRED 上很多系列屬於數據商或其他機構，有版權或授權限制，"
            f"這裡只抓確認是公有領域的：{listed}。{alt}")


# 兩個白名單系列相減（我們自己算）："A-B"。有慣用名字的寫在這裡，其他叫「A 的簡稱減 B 的簡稱」。
DERIVED_NAMES = {("DGS10", "DFII10"): "通膨預期"}

EVERY = {"day", "month", "quarter", "year"}
_REQUEST_KEYS = ("series", "from", "to", "every")


@dataclass(frozen=True)
class PublicRequest:
    parts: tuple[str, ...]
    """一個白名單系列，或兩個（相減：parts[0] − parts[1]）。"""
    start: date
    end: date
    every: str

    @property
    def series(self) -> str:
        return "-".join(self.parts)

    @property
    def name(self) -> str:
        if len(self.parts) == 1:
            return WHITELIST[self.parts[0]].short
        a, b = self.parts
        return DERIVED_NAMES.get((a, b)) or f"{WHITELIST[a].short}減{WHITELIST[b].short}"


def parse_request(raw: Any, where: str) -> tuple[PublicRequest | None, list[str]]:
    """驗證分鏡設定裡的 publicData（不連網）。回傳 (請求, 問題清單)；有問題時請求是 None。"""
    problems: list[str] = []
    add = problems.append
    if not isinstance(raw, dict):
        add(f'{where} 要是物件，例 {{"series": "DGS10", "from": "2024-01-01", "to": "2024-12-31", "every": "month"}}')
        return None, problems
    bad = [k for k in raw if k not in _REQUEST_KEYS]
    if bad:
        add(f"{where} 有不認得的欄位「{'、'.join(bad)}」（只認 {'、'.join(_REQUEST_KEYS)}）")
    parts = _parse_series(raw.get("series"), where, add)
    start, end = (_parse_date(raw.get(k), f"{where} 的 {k}", add) for k in ("from", "to"))
    if start and end and start > end:
        add(f"{where} 的 from（{start}）晚於 to（{end}）")
    every = raw.get("every")
    if every not in EVERY:
        add(f"{where} 的 every 只能是 {'、'.join(sorted(EVERY))}，現在是 {every!r}")
    if problems:
        return None, problems
    return PublicRequest(parts=parts, start=start, end=end, every=every), problems


def _parse_series(sid: Any, where: str, add: Callable[[str], None]) -> tuple[str, ...]:
    if not isinstance(sid, str) or not sid.strip():
        add(f'{where} 少了 series（系列代號，例 "DGS10"；通膨預期寫 "DGS10-DFII10"）')
        return ()
    parts = tuple(p for p in re.split(r"[-−－]", re.sub(r"\s+", "", sid).upper()))
    if len(parts) > 2 or not all(parts):
        add(f'{where} 的 series「{sid}」看不懂：寫一個系列代號（例 "DGS10"），或兩個相減（例 "DGS10-DFII10"）')
        return ()
    for p in parts:
        if p not in WHITELIST:
            add(f"{where}：{refusal(p)}")
    if len(parts) == 2 and parts[0] == parts[1]:
        add(f"{where} 的 series「{sid}」是自己減自己")
    return parts


def _parse_date(v: Any, where: str, add: Callable[[str], None]) -> date | None:
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        try:
            return date.fromisoformat(v)
        except ValueError:
            pass
    add(f"{where} 要是日期，寫成 YYYY-MM-DD（例 2024-01-31），現在是 {v!r}")
    return None


# ---------------------------------------------------------------- 抓取


class FetchError(Exception):
    pass


def fred_url() -> str:
    return os.environ.get(URL_ENV) or FRED_CSV_URL


def fred_csv_url(series: str, start: date, end: date) -> str:
    return f"{fred_url()}?id={series}&cosd={start.isoformat()}&coed={end.isoformat()}"


def fetch_fred(series: str, start: date, end: date) -> list[tuple[date, float]]:
    """抓 FRED 的 fredgraph.csv。缺值（舊格式「.」、新格式空白）略過。"""
    url = fred_csv_url(series, start, end)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    # uv 帶的 Python 不一定找得到 macOS 的根憑證（實測 python.org 3.13 會 CERTIFICATE_VERIFY_FAILED），用 certifi 的
    tls = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=tls) as resp:
            text = resp.read().decode("utf-8-sig")
    except urllib.error.HTTPError as e:
        raise FetchError(f"FRED 回應錯誤（HTTP {e.code}），可能是網站暫時出問題，晚點再試") from None
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, TimeoutError):
            raise FetchError(f"連不上 FRED（等了 {TIMEOUT} 秒沒有回應，可能網路很慢或斷線）") from None
        raise FetchError(f"連不上 FRED（可能沒有網路；技術細節：{reason}）") from None
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows[0]) < 2 or rows[0][1].strip().upper() != series:
        raise FetchError("FRED 回來的不是數據表（可能網站改版或被擋）")
    out: list[tuple[date, float]] = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        try:
            d, v = date.fromisoformat(row[0].strip()), float(row[1])
        except ValueError:
            continue
        if math.isfinite(v):
            out.append((d, v))
    return out


# ---------------------------------------------------------------- 取樣


def _period(d: date, every: str) -> str:
    if every == "day":
        return d.isoformat()
    if every == "month":
        return f"{d.year}-{d.month:02d}"
    if every == "quarter":
        return f"{d.year}Q{(d.month - 1) // 3 + 1}"
    return str(d.year)


def _daily(req: PublicRequest, snaps: dict[str, Snapshot]) -> list[tuple[date, float]]:
    """請求期間內每天的值；兩個系列相減時只取兩邊都有值的日子。"""
    cols = [{d: v for d, v in snaps[p].observations if req.start <= d <= req.end} for p in req.parts]
    if len(cols) == 1:
        return sorted(cols[0].items())
    a, b = cols
    return [(d, round(a[d] - b[d], 4)) for d in sorted(a.keys() & b.keys())]


@dataclass(frozen=True)
class PublicValue:
    """圖上的一個點：從公有數據算出來的值，以及它涵蓋哪幾天。"""

    label: str
    """x 軸／類目名（期間）：日 2025-01-03、月 2025-01、季 2025Q1、年 2025。"""
    value: float
    first: date
    """這個值用到的第一個觀測日。"""
    last: date
    """最後一個觀測日（every=day 時跟 first 一樣）。"""
    count: int
    """平均了幾個觀測值（交易日）。"""


def _sample(daily: list[tuple[date, float]], every: str) -> list[PublicValue]:
    groups: dict[str, list[tuple[date, float]]] = {}
    for d, v in daily:
        groups.setdefault(_period(d, every), []).append((d, v))
    return [
        PublicValue(label=k, value=round(sum(v for _, v in g) / len(g), 4), first=g[0][0], last=g[-1][0], count=len(g))
        for k, g in groups.items()
    ]


_EVERY_WORDS = {"day": "日資料", "month": "月平均", "quarter": "季平均", "year": "年平均"}


@dataclass(frozen=True)
class PublicChart:
    """一個圖表分鏡畫的公有數據：數字對帳（#5）與審片頁拿這個標出處。"""

    request: PublicRequest
    values: tuple[PublicValue, ...]
    """跟引擎 data.series 一一對應（同順序）。"""
    fetched: dict[str, date]
    """每個用到的系列是哪天抓的（＝專案 public_data/<代號>.json 的 fetched）。"""
    source: str
    """自動寫進圖表的來源行。"""

    @property
    def series(self) -> tuple[str, ...]:
        """用到的白名單系列代號（通膨預期這類自算的會有兩個）。"""
        return self.request.parts

    @property
    def formula(self) -> str | None:
        """自算的算式（例「DGS10 − DFII10」）；單一系列是 None。"""
        return " − ".join(self.request.parts) if len(self.request.parts) == 2 else None

    def describe(self, i: int) -> str:
        """第 i 個點的出處，一行繁中（例：FRED DGS10，2025-01 月平均：2025-01-02～2025-01-10 共 7 個交易日，2026-10-09 擷取）。"""
        v = self.values[i]
        what = f"{self.request.name}（{self.formula} 自算）" if self.formula else self.request.parts[0]
        days = v.first.isoformat() if v.count == 1 else f"{v.first}～{v.last} 共 {v.count} 個交易日"
        when = "、".join(sorted({d.isoformat() for d in self.fetched.values()}))
        return f"FRED {what}，{v.label} {_EVERY_WORDS[self.request.every]}：{days}，{when} 擷取"


def _source_line(req: PublicRequest, snaps: dict[str, Snapshot]) -> str:
    fetched = "、".join(sorted({snaps[p].fetched.isoformat() for p in req.parts}))
    how = _EVERY_WORDS[req.every]
    if len(req.parts) == 2:
        how = f"{req.name}＝{req.parts[0]} 減 {req.parts[1]} 自算，{how}"
    return f"來源：FRED，聯準會（{'、'.join(req.parts)}）；{how}；{fetched} 擷取"


# ---------------------------------------------------------------- 建置入口


@dataclass(frozen=True)
class Snapshot:
    """一個系列抓下來的數字，存在專案的 public_data/<代號>.json。"""

    series: str
    start: date
    end: date
    fetched: date
    observations: list[tuple[date, float]]

    def covers(self, start: date, end: date) -> bool:
        return self.start <= start and end <= self.end


def _read_snapshot(path: Path) -> Snapshot | None:
    """讀快照；檔案不存在或壞了就當沒有（會重抓）。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Snapshot(
            series=raw["series"],
            start=date.fromisoformat(raw["from"]),
            end=date.fromisoformat(raw["to"]),
            fetched=date.fromisoformat(raw["fetched"]),
            observations=[(date.fromisoformat(d), float(v)) for d, v in raw["observations"].items()],
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _write_snapshot(path: Path, snap: Snapshot, url: str) -> None:
    info = WHITELIST[snap.series]
    raw = {
        "series": snap.series,
        "name": info.name,
        "from": snap.start.isoformat(),
        "to": snap.end.isoformat(),
        "fetched": snap.fetched.isoformat(),
        "url": url,
        "observations": {d.isoformat(): v for d, v in snap.observations},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


NETWORK_HINT = (
    "第一次用到某個系列時要連得上網路（FRED）；抓過一次會存進專案的 public_data/ 資料夾，之後重建不用網路。"
    "請確認網路正常後再建置一次。"
)


def _load_series(project: Project, wanted: list[tuple[str, PublicRequest]]) -> tuple[dict[str, Snapshot], list[str]]:
    """每個用到的系列：快照涵蓋所有分鏡要的期間就直接用，不然抓一次涵蓋全部期間的數據、覆寫快照。

    wanted：(分鏡標籤, 請求)。抓不到、或整段沒有數據時回傳問題清單（點名是哪些分鏡要用的），不寫快照。
    """
    spans: dict[str, tuple[date, date]] = {}
    users: dict[str, list[str]] = {}
    for label, r in wanted:
        for sid in r.parts:
            s0, e0 = spans.get(sid, (r.start, r.end))
            spans[sid] = (min(s0, r.start), max(e0, r.end))
            users.setdefault(sid, []).append(label)
    out: dict[str, Snapshot] = {}
    problems: list[str] = []
    for sid, (start, end) in spans.items():
        who = "、".join(users[sid])
        path = project.paths.public_data / f"{sid}.json"
        snap = _read_snapshot(path)
        if snap is None or snap.series != sid or not snap.covers(start, end):
            try:
                obs = fetch_fred(sid, start, end)
            except FetchError as e:
                problems.append(f"{who} 要用的 {sid}（{start}～{end}）抓不到：{e}")
                continue
            if not obs:
                problems.append(f"{who} 要用的 {sid} 在 {start}～{end} 沒有數據（FRED 回來是空的），請確認日期範圍")
                continue
            snap = Snapshot(series=sid, start=start, end=end, fetched=date.today(), observations=obs)
            _write_snapshot(path, snap, fred_csv_url(sid, start, end))
        out[sid] = snap
    return out, problems


def resolve_public_data(project: Project) -> tuple[Project, dict[str, PublicChart]]:
    """把每個寫了 publicData 的圖表分鏡換成實際數列與來源行。抓不到就丟 ProjectError（不會產生空圖）。

    回傳 (換好數列的專案, 分鏡 id → PublicChart)。接續分鏡沿用上一個分鏡的圖，所以也對到同一個 PublicChart。
    """
    wanted = [(i, s.chart.public) for i, s in enumerate(project.shots) if s.chart is not None and s.chart.public]
    if not wanted:
        return project, {}
    snaps, problems = _load_series(project, [(project.shots[i].label, r) for i, r in wanted])
    if problems:
        raise ProjectError(problems, title="公有數據抓不到", hint=NETWORK_HINT)
    shots = list(project.shots)
    charts: dict[str, PublicChart] = {}
    for i, req in wanted:
        shot = shots[i]
        values = _sample(_daily(req, snaps), req.every)
        if not values:
            problems.append(f"{shot.label}：{req.series} 在 {req.start}～{req.end} 沒有數據，請確認日期範圍")
            continue
        xt = shot.chart.data.get("xTicks")
        if xt is not None and not (isinstance(xt, list) and all(
                isinstance(x, int) and not isinstance(x, bool) and 0 <= x < len(values) for x in xt)):
            problems.append(f"{shot.label} 的 chart.data.xTicks 要是索引清單（0～{len(values) - 1}）："
                            f"公有數據抓到 {len(values)} 個點（{values[0].label}～{values[-1].label}）")
        chart = PublicChart(request=req, values=tuple(values), fetched={p: snaps[p].fetched for p in req.parts},
                            source=_source_line(req, snaps))
        data = {**shot.chart.data, "series": [{"label": v.label, "value": v.value} for v in values], "source": chart.source}
        problems.extend(y_range_covers(data, f"{shot.label} 的 chart.data"))
        shots[i] = replace(shot, chart=ChartConfig(data=data, cues=shot.chart.cues))
        charts[shot.id] = chart
    if problems:
        raise ProjectError(problems, title="公有數據畫不出圖")
    for i, shot in enumerate(shots):
        if shot.continues and i > 0 and shots[i - 1].id in charts:
            charts[shot.id] = charts[shots[i - 1].id]
    return replace(project, shots=shots), charts

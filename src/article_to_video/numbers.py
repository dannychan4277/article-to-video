"""數字對帳：列出每個分鏡上畫面的數字，對回出處；對不到的擋下正式出片。

上畫面的字（screen_texts，每個分鏡一份）：
  財經圖表：標題、副標（單位那行；自訂的縱軸說明也算）、角標、數值（柱上／線頭的數字，照 decimals、prefix、
  suffix 格式化成畫面上的樣子；柱上文字覆寫生效時看覆寫的字）、類目名（x 軸標籤，全部都算）、標註（text、sub）、
  大數字（number cue 的值與說明）。接續分鏡只算它自己新出現的字（沿用的圖記在上一個分鏡）。
  不算：來源行（那是出處本身）、預設的縱軸說明「縱軸未從 0 開始」、刻度值（引擎依數據範圍自己排的尺，不是主張）。
  Vox 索引卡（#8）：寫一個 ScreenTextSource 加進 default_sources()。

出處分類（每個數字照順序找，找到就停）：
  public   白名單公有數據：這個分鏡的公有數據圖畫的值、期間標籤、系列名稱（附系列、期間、擷取日期）。
  derived  推算：分鏡設定的 derived 宣告了算式，算式的每個輸入都找得到出處，而且算出來四捨五入到畫面的位數＝畫面上的數字。
  article  原文出現：文章裡有一個數字，四捨五入到畫面的位數＝畫面上的數字。
  none     無出處：以上都不是。有任何一個 → check_numbers 擋下正式出片（預覽照樣出，審片頁標出來）。

比對前的正規化（find_numbers，文章和畫面用同一套）：全形轉半形、千分位、小數（4.50＝4.5）、百分比（4.58%＝
百分之四點五八＝4.58，百分號不影響比對）、中文數字（四點五八、兩年期、一萬兩千、二〇二三）、萬／億／兆（1.2 萬＝12,000）。
正負號不看（「下跌 0.62」對得上畫面的 −0.62）。

文章文字只從 read_article() 讀（#6 文章擷取的產出：<專案>/article/article.json 的段落，沒有就讀 article/text.txt，
段與段之間空一行）。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

from article_to_video.article import ArticlePaths
from article_to_video.project import ProjectError, ProjectPaths
from article_to_video.public_data import WHITELIST, PublicChart
from article_to_video.timeline import ShotTiming, effective_chart

if TYPE_CHECKING:
    from article_to_video.build import BuildPlan
    from article_to_video.review import ReviewExtras

__all__ = [
    "Article",
    "FoundNumber",
    "NumberReport",
    "NumberSource",
    "ScreenText",
    "check_numbers",
    "default_sources",
    "find_numbers",
    "number_review",
    "summary_line",
    "read_article",
    "reconcile",
]


KIND_LABELS = {"article": "原文", "public": "公有數據", "derived": "推算", "none": "無出處"}


# ---------------------------------------------------------------- 找數字（正規化）


@dataclass(frozen=True)
class FoundNumber:
    text: str
    """原文寫的樣子（例「4.58%」「四點五八」「1.2 萬」）。"""
    value: Fraction
    """數值（絕對值；百分號、千分位、萬億都換算過）。"""
    quantum: Fraction
    """寫到哪一位（4.58 → 0.01；1.2 萬 → 1000）：比對時，對方四捨五入到這一位要等於它。"""
    start: int
    end: int

    def matches(self, other: Fraction) -> bool:
        """other 四捨五入到這個數字寫的位數，是不是就是它（四捨五入的進位方式不拘）。"""
        return abs(abs(other) - self.value) * 2 <= self.quantum


_MINUS = {"−": "-", "‒": "-", "–": "-", "－": "-"}


def _normalize(text: str) -> str:
    """一個字換一個字（位置不變）：全形數字與符號轉半形，各種減號換成 -。"""
    out = []
    for ch in text:
        if ch in _MINUS:
            out.append(_MINUS[ch])
            continue
        n = unicodedata.normalize("NFKC", ch)
        out.append(n if len(n) == 1 else ch)
    return "".join(out)


_CN_DIGITS = {"零": 0, "〇": 0, "○": 0, "一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_SMALL = {"十": 10, "百": 100, "千": 1000}
_CN_BIG = {"萬": 10**4, "万": 10**4, "億": 10**8, "亿": 10**8, "兆": 10**12}
_CN_CHARS = "".join(_CN_DIGITS) + "".join(_CN_SMALL) + "".join(_CN_BIG)
_MULT = {**{k: v for k, v in _CN_SMALL.items() if k != "十"}, **_CN_BIG}

_ARABIC_RE = re.compile(
    r"(?<![\d.])(?P<int>\d{1,3}(?:,\d{3})+(?!\d)|\d+)(?:\.(?P<frac>\d+))?"
    r"(?:\s*(?P<mult>[百千萬万億亿兆]+)(?!分))?"
    r"(?P<pct>\s*(?:%|個百分點|个百分点|百分點))?"
)
_CHINESE_RE = re.compile(
    rf"(?P<pct>百分之)?(?P<int>[{_CN_CHARS}]+)(?:[點点](?P<frac>[{''.join(_CN_DIGITS)}]+))?"
)


def _chinese_int(s: str) -> tuple[int, int] | None:
    """中文整數 → (值, 結尾的萬／億／兆單位，沒有就 1)。沒有任何數字字（例「千萬」）回 None。"""
    if not any(c in _CN_DIGITS or c == "十" for c in s):
        return None
    if all(c in _CN_DIGITS for c in s):  # 二〇二三：一個字一位
        return int("".join(str(_CN_DIGITS[c]) for c in s)), 1
    total, section, digit, unit = 0, 0, None, 1
    for c in s:
        if c in _CN_DIGITS:
            digit = _CN_DIGITS[c]
            unit = 1
        elif c in _CN_SMALL:
            section += (1 if digit is None else digit) * _CN_SMALL[c]
            digit = None
            unit = 1
        else:  # 萬、億、兆
            section += digit or 0
            total = (total + section) * _CN_BIG[c] if total < _CN_BIG[c] else total + section * _CN_BIG[c]
            section, digit, unit = 0, None, _CN_BIG[c]
    return total + section + (digit or 0), unit


def find_numbers(text: str) -> list[FoundNumber]:
    """找出一段字裡的每個數字（阿拉伯數字、中文數字），照出現順序。"""
    norm = _normalize(text)
    found: list[FoundNumber] = []
    taken = [False] * len(norm)
    for m in _ARABIC_RE.finditer(norm):
        frac = m.group("frac") or ""
        value = Fraction(m.group("int").replace(",", "") + ("." + frac if frac else ""))
        quantum = Fraction(1, 10 ** len(frac))
        mult = 1
        for c in m.group("mult") or "":
            mult *= _MULT[c]
        end = m.end()
        found.append(FoundNumber(text[m.start():end].strip(), value * mult, quantum * mult, m.start(), end))
        for i in range(m.start(), end):
            taken[i] = True
    for m in _CHINESE_RE.finditer(norm):
        if any(taken[m.start():m.end()]):
            continue
        parsed = _chinese_int(m.group("int"))
        if parsed is None:
            continue
        value, unit = parsed
        frac = m.group("frac") or ""
        if frac:
            value = Fraction(f"{value}.{''.join(str(_CN_DIGITS[c]) for c in frac)}")
            quantum = Fraction(1, 10 ** len(frac))
        else:
            value, quantum = Fraction(value), Fraction(unit)
        found.append(FoundNumber(text[m.start():m.end()], value, quantum, m.start(), m.end()))
    return sorted(found, key=lambda f: f.start)


# ---------------------------------------------------------------- 文章（唯一的讀取入口）


@dataclass(frozen=True)
class Paragraph:
    n: int
    text: str
    page: int | None = None


@dataclass(frozen=True)
class Article:
    paragraphs: list[Paragraph]
    path: Path | None
    """讀到的檔案；None＝專案裡沒有文章。"""

    def where(self, para: Paragraph) -> str:
        return f"原文第 {para.n} 段" + (f"（第 {para.page} 頁）" if para.page else "")


def read_article(paths: ProjectPaths) -> Article:
    """專案的文章文字。先讀文章擷取（article.py）寫的 article/article.json（段落＋頁碼），沒有就讀 article/text.txt。"""
    ap = ArticlePaths(paths.article)
    manifest = ap.manifest
    if manifest.is_file():
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            paras = [Paragraph(int(p["n"]), str(p["text"]), p.get("page")) for p in raw["paragraphs"]]
            return Article(paras, manifest)
        except (ValueError, KeyError, TypeError):
            pass  # 清單壞了就退回讀全文
    text = ap.text
    if text.is_file():
        blocks = [b.strip() for b in re.split(r"\n\s*\n", text.read_text(encoding="utf-8"))]
        return Article([Paragraph(i, b) for i, b in enumerate((b for b in blocks if b), 1)], text)
    return Article([], None)


def _snippet(text: str, start: int, end: int, pad: int = 12) -> str:
    a, b = max(0, start - pad), min(len(text), end + pad)
    return ("…" if a > 0 else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")


# ---------------------------------------------------------------- 推算（分鏡設定的 derived）

DERIVED_KEYS = ("value", "formula", "note")
DERIVED_EXAMPLE = '"derived": [{"value": "0.62", "formula": "4.58 - 3.96", "note": "兩年期減十年期"}]'


@dataclass(frozen=True)
class Derivation:
    """分鏡設定宣告的一個推算：畫面上的 value 是 formula 算出來的。"""

    value: FoundNumber
    """畫面上那個數字（宣告寫的）。"""
    formula: str
    """給人看的算式（減號、乘除號換成 − × ÷），例「4.58 − 3.96」。"""
    result: Fraction
    inputs: tuple[FoundNumber, ...]
    """算式裡的每個數字（每個都要找得到出處）。"""
    note: str = ""
    where: str = ""
    """「分鏡 SH02 的 derived[0]」。"""

    @property
    def result_text(self) -> str:
        """算出來的值：除得盡（6 位以內）就寫「= 0.62」，除不盡寫「≈ 15.6566」。"""
        for places in range(7):
            scaled = self.result * 10**places
            if scaled.denominator == 1:
                return "= " + _plain(self.result, places)
        return "≈ " + _plain(self.result, 4)


def _plain(v: Fraction, places: int) -> str:
    q = round(v * 10**places)
    sign, q = ("−" if q < 0 else ""), abs(q)
    s = str(q).rjust(places + 1, "0")
    return sign + (s[:-places] + "." + s[-places:] if places else s)


_FORMULA_TOKEN = re.compile(r"\s*(?:(?P<num>(?:\d{1,3}(?:,\d{3})+(?!\d)|\d+)(?:\.\d+)?%?)|(?P<op>[-+*/×÷xX()]))")
_OPS = {"+": "+", "-": "−", "*": "×", "x": "×", "X": "×", "×": "×", "/": "÷", "÷": "÷"}


class _FormulaError(ValueError):
    pass


def _parse_formula(text: str) -> tuple[Fraction, tuple[FoundNumber, ...], str, int]:
    """四則運算（＋括號）→ (結果, 輸入的數字, 給人看的算式, 運算個數)。看不懂丟 _FormulaError（繁中原因）。"""
    norm = _normalize(text).strip()
    tokens: list[tuple[str, str, int]] = []  # (種類, 字, 位置)
    pos = 0
    while pos < len(norm):
        m = _FORMULA_TOKEN.match(norm, pos)
        if m is None or m.end() == pos:
            raise _FormulaError(f"看不懂「{norm[pos:pos + 6]}」")
        tokens.append(("num", m.group("num"), m.start("num")) if m.group("num") else ("op", m.group("op"), m.start("op")))
        pos = m.end()
        while pos < len(norm) and norm[pos].isspace():
            pos += 1
    inputs: list[FoundNumber] = []
    shown: list[str] = []
    ops = 0
    i = 0

    def peek() -> tuple[str, str, int] | None:
        return tokens[i] if i < len(tokens) else None

    def take() -> tuple[str, str, int]:
        nonlocal i
        t = peek()
        if t is None:
            raise _FormulaError("算式後面少了數字")
        i += 1
        return t

    def factor() -> Fraction:
        nonlocal ops
        t = take()
        if t[0] == "op" and t[1] == "-":
            shown.append("−")
            return -factor()
        if t[0] == "op" and t[1] == "(":
            shown.append("(")
            v = expr()
            if take()[1] != ")":
                raise _FormulaError("括號沒有關起來")
            shown.append(")")
            return v
        if t[0] != "num":
            raise _FormulaError(f"「{t[1]}」前面少了數字")
        raw = t[1].removesuffix("%")
        digits = raw.replace(",", "")
        frac = digits.split(".")[1] if "." in digits else ""
        n = FoundNumber(t[1], Fraction(digits), Fraction(1, 10 ** len(frac)), t[2], t[2] + len(t[1]))
        inputs.append(n)
        shown.append(t[1])
        return n.value

    def term() -> Fraction:
        nonlocal ops
        v = factor()
        while (t := peek()) is not None and t[0] == "op" and _OPS.get(t[1]) in ("×", "÷"):
            take()
            shown.append(f" {_OPS[t[1]]} ")
            ops += 1
            rhs = factor()
            if _OPS[t[1]] == "÷":
                if rhs == 0:
                    raise _FormulaError("算式裡除以 0")
                v /= rhs
            else:
                v *= rhs
        return v

    def expr() -> Fraction:
        nonlocal ops
        v = term()
        while (t := peek()) is not None and t[0] == "op" and t[1] in "+-":
            take()
            shown.append(f" {_OPS[t[1]]} ")
            ops += 1
            v = v + term() if t[1] == "+" else v - term()
        return v

    if not tokens:
        raise _FormulaError("是空的")
    result = expr()
    if i < len(tokens):
        raise _FormulaError(f"「{tokens[i][1]}」放的位置不對")
    return result, tuple(inputs), "".join(shown), ops


def parse_derived(raw: Any, label: str) -> tuple[tuple[Derivation, ...], list[str]]:
    """驗證分鏡設定的 derived（推算宣告）。回傳 (宣告, 問題清單)；problems 是繁中、點名分鏡與欄位。"""
    problems: list[str] = []
    if not isinstance(raw, list):
        problems.append(f"{label} 的 derived 要是清單，例 {DERIVED_EXAMPLE}")
        return (), problems
    out: list[Derivation] = []
    for k, item in enumerate(raw):
        where = f"{label} 的 derived[{k}]"
        if not isinstance(item, dict):
            problems.append(f"{where} 要是物件，例 {DERIVED_EXAMPLE[12:-1]}")
            continue
        bad = [key for key in item if key not in DERIVED_KEYS]
        if bad:
            problems.append(f"{where} 有不認得的欄位「{'、'.join(bad)}」（只認 value、formula、note）")
        value_raw = item.get("value")
        nums = find_numbers(str(value_raw)) if isinstance(value_raw, (str, int, float)) and not isinstance(value_raw, bool) else []
        if len(nums) != 1:
            problems.append(f"{where} 的 value 要是畫面上的那個數字（例 \"0.62\"），現在是 {value_raw!r}")
        formula = item.get("formula")
        if not isinstance(formula, str) or not formula.strip():
            problems.append(f"{where} 少了 formula（算式，例 \"4.58 - 3.96\"；數字要是找得到出處的）")
            continue
        note = item.get("note", "")
        if not isinstance(note, str):
            problems.append(f"{where} 的 note 要是文字")
            note = ""
        try:
            result, inputs, shown, ops = _parse_formula(formula)
        except _FormulaError as e:
            problems.append(f"{where} 的算式「{formula}」看不懂：{e}（只能用數字、+ − × ÷ 和括號）")
            continue
        if ops == 0:
            problems.append(f"{where} 的算式「{formula}」至少要有一個運算（+ − × ÷）；文章裡本來就有的數字不用宣告")
            continue
        if len(nums) == 1:
            out.append(Derivation(nums[0], shown, result, inputs, note, where))
    return tuple(out), problems


# ---------------------------------------------------------------- 上畫面的字


@dataclass(frozen=True)
class ScreenText:
    """一個分鏡上畫面的一段字。"""

    where: str
    """在哪裡：標題、副標、角標、數值、柱上文字、類目名、標註、大數字、大數字說明、索引卡…"""
    text: str
    """畫面上的字（數值是格式化之後的樣子，例「4.58%」）。"""
    of: str = ""
    """屬於哪一個（例：數值屬於類目「兩年期」）；給人看的補充。"""
    whole: bool = False
    """整段就是一個數字（數值、大數字）：畫面上顯示的就是 text 本身。"""
    public: int | None = None
    """這是公有數據圖的第幾個點（數值、類目名）；出處直接是那個點。"""


ScreenTextSource = Callable[["BuildPlan", ShotTiming], list[ScreenText]]
"""一個分鏡 → 它上畫面的字。財經圖表是 chart_texts；Vox 分鏡（#8）寫一個加進 default_sources()。"""


def _fmt(v: float, decimals: int) -> str:
    """跟引擎 TY.fmt 一樣：固定小數位、千分位、負號用 −。"""
    s = f"{abs(v):,.{decimals}f}"
    return ("−" if v < 0 else "") + s


DEFAULT_AXIS_NOTES = {"縱軸未從 0 開始", "纵轴未从 0 开始"}


def chart_texts(plan: BuildPlan, st: ShotTiming) -> list[ScreenText]:
    """財經圖表分鏡上畫面的字（引擎 t3_finance_chart 實際會畫的）。"""
    shots = plan.project.shots
    i = st.shot.number - 1
    if st.shot.chart is None:
        return []
    data, cues = effective_chart(shots, i)
    out: list[ScreenText] = []
    for key, where in (("title", "標題"), ("unit", "副標"), ("badge", "角標")):
        if isinstance(data.get(key), str) and data[key]:
            out.append(ScreenText(where, data[key]))
    note = data.get("axisNote")
    if isinstance(note, str) and note and note not in DEFAULT_AXIS_NOTES:
        out.append(ScreenText("副標", note))

    kinds = [c.kind for c in cues]
    kind = next((k for k in kinds if k in ("bar", "line", "candle")), data.get("chart", "bar"))
    series = data.get("series") or []
    decimals = data.get("decimals", 1)
    pre, suf = data.get("prefix", "") or "", data.get("suffix", "") or ""
    public = st.shot.id in plan.public_data
    per_bar = kind == "bar" and (
        any(c.kind == "bar" and isinstance(c.fields.get("data"), dict) and "index" in c.fields["data"] for c in cues)
        or any(isinstance(r, dict) and r.get("color") for r in series)
    )
    for n, r in enumerate(series):
        if not isinstance(r, dict):
            continue
        label = str(r.get("label", ""))
        idx = n if public else None
        if kind == "candle":
            for k in ("o", "h", "l", "c"):
                if isinstance(r.get(k), (int, float)):
                    out.append(ScreenText("數值", pre + _fmt(r[k], decimals) + suf, of=f"{label} {k}", whole=True, public=idx))
        elif per_bar and r.get("text") is not None:
            out.append(ScreenText("柱上文字", str(r["text"]), of=label, public=idx))
        elif isinstance(r.get("value"), (int, float)):
            out.append(ScreenText("數值", pre + _fmt(r["value"], decimals) + suf, of=label, whole=True, public=idx))
        if label:
            out.append(ScreenText("類目名", label, public=idx))

    hq = next((c for c in cues if c.kind == "highlight"), None)
    if hq is not None:  # 引擎只畫第一個 highlight cue；字以 cue 自己的為準，沒寫就用 data.highlight
        h = data.get("highlight") or {}
        for k in ("text", "sub"):
            v = hq.fields.get(k) if hq.fields.get(k) not in (None, "") else h.get(k)
            if isinstance(v, str) and v:
                out.append(ScreenText("標註", v))
    for c in cues:
        if c.kind != "number":
            continue
        nd = c.fields.get("data") or {}
        if isinstance(nd.get("value"), (int, float)):
            shown = (nd.get("prefix") or "") + _fmt(nd["value"], nd.get("decimals", 0)) + (nd.get("suffix") or "")
            out.append(ScreenText("大數字", shown, whole=True))
        cap = c.fields.get("text") if c.fields.get("text") is not None else c.fields.get("sub")
        if isinstance(cap, str) and cap:
            out.append(ScreenText("大數字說明", cap))
    return out


def default_sources() -> list[ScreenTextSource]:
    """數字對帳預設看哪些字。Vox 索引卡（#8）做好後加在這裡。"""
    return [chart_texts]


def _own_texts(plan: BuildPlan, sources: Sequence[ScreenTextSource]) -> dict[str, list[ScreenText]]:
    """每個分鏡自己新出現的字：接續分鏡跟上一個分鏡一模一樣的字不重複算（記在上一個分鏡）。"""
    out: dict[str, list[ScreenText]] = {}
    prev: list[ScreenText] = []
    for st in plan.timeline.shots:
        texts = [t for src in sources for t in src(plan, st)]
        if st.shot.continues:
            seen = {(t.where, t.text) for t in prev}
            out[st.shot.id] = [t for t in texts if (t.where, t.text) not in seen]
        else:
            out[st.shot.id] = texts
        prev = texts
    return out


# ---------------------------------------------------------------- 對帳


@dataclass(frozen=True)
class NumberSource:
    """畫面上的一個數字和它的出處。"""

    shot_id: str
    shot_number: int
    where: str
    """在畫面哪裡（標題、副標、數值、類目名、標註、大數字…）。"""
    of: str
    """屬於哪一個（數值屬於哪個類目），沒有就空字串。"""
    context: str
    """那段字的全文（例類目名「兩年期」）。"""
    shown: str
    """畫面上的那個數字（例「4.58%」「兩」）。"""
    kind: str
    """article（原文出現）／public（白名單公有數據）／derived（推算）／none（無出處）。"""
    detail: str
    """出處說明（原文第幾段＋前後文、公有數據的系列與日期、算式）或找不到的原因。"""

    @property
    def label(self) -> str:
        return KIND_LABELS[self.kind]

    @property
    def place(self) -> str:
        """給人看的位置，例「數值（兩年期）」「標題「通膨預期…」」。"""
        if self.of:
            return f"{self.where}（{self.of}）"
        if self.context and self.context != self.shown:
            ctx = self.context if len(self.context) <= 24 else self.context[:23] + "…"
            return f"{self.where}「{ctx}」"
        return self.where

    @property
    def shot_label(self) -> str:
        """錯誤訊息用：分鏡 id（Claude 改 shots.json 用；回報問題也靠「分鏡 <id>」找出錯的分鏡）＋審片頁上的編號（作者看的）。"""
        return f"分鏡 {self.shot_id}（審片頁的分鏡 {self.shot_number}）"


@dataclass(frozen=True)
class NumberReport:
    shots: dict[str, list[NumberSource]]
    """分鏡 id → 這個分鏡上畫面的數字（照畫面上出現的欄位順序）。"""
    article: Article
    notes: dict[str, list[str]] = field(default_factory=dict)
    """分鏡 id → 給作者看的補充提醒。"""

    @property
    def unsourced(self) -> list[NumberSource]:
        return [n for items in self.shots.values() for n in items if n.kind == "none"]


class _Sources:
    """數字可以對到的出處：文章（全專案共用）、這個分鏡畫的公有數據、這個分鏡宣告的推算。"""

    def __init__(self, article: Article, public: Mapping[str, PublicChart]) -> None:
        self.article = article
        self.entries = [(p, f) for p in article.paragraphs for f in find_numbers(p.text)]
        self.public = public

    def in_article(self, num: FoundNumber) -> str | None:
        """文章裡有沒有一個數字，四捨五入到 num 寫的位數就是 num；有就回「原文第 N 段：…前後文…」。"""
        for p, f in self.entries:
            if num.matches(f.value):
                return f"{self.article.where(p)}：{_snippet(p.text, f.start, f.end)}"
        return None

    def in_public(self, num: FoundNumber, shot_id: str, point: int | None = None) -> str | None:
        """這個分鏡的公有數據圖：圖上的點（point＝這段字就是第幾個點的數值或期間）、圖上任何一個值、期間標籤、系列名稱。"""
        chart = self.public.get(shot_id)
        if chart is None:
            return None
        if point is not None:
            return chart.describe(point)
        for i, v in enumerate(chart.values):
            if num.matches(Fraction(repr(v.value))):
                return chart.describe(i)
        for v in chart.values:
            if any(num.matches(f.value) for f in find_numbers(v.label)):
                first, last = chart.values[0].label, chart.values[-1].label
                return f"公有數據的期間（圖上 {first}～{last}）：FRED {chart.request.series}"
        names = [chart.request.name, *(WHITELIST[s].name for s in chart.series)]
        for name in names:
            if any(num.matches(f.value) for f in find_numbers(name)):
                return f"公有數據的系列名稱：{name}（FRED {chart.request.series}）"
        return None

    def lookup(self, num: FoundNumber, shot_id: str) -> tuple[str, str] | None:
        """(種類, 出處說明)：先看這張圖的公有數據，再看文章。"""
        if (hit := self.in_public(num, shot_id)) is not None:
            return "public", hit
        if (hit := self.in_article(num)) is not None:
            return "article", hit
        return None

    def derived(self, num: FoundNumber, shown: str, shot_id: str,
                decls: Sequence[Derivation]) -> tuple[str | None, str | None]:
        """宣告的推算：(成立時的說明, 宣告了但不成立的原因)。"""
        failure = None
        for d in decls:
            if not num.matches(d.value.value):
                continue
            found = {x.text: self.lookup(x, shot_id) for x in d.inputs}
            missing = [x.text for x in d.inputs if found[x.text] is None and not _is_unit_constant(x)]
            if missing:
                failure = (f"宣告的推算 {d.formula} 裡，{'、'.join(missing)} 找不到出處"
                           "（算式裡的數字要在文章裡，或是這張圖的公有數據）")
                continue
            if not num.matches(d.result):
                got = d.result_text.removeprefix("= ").removeprefix("≈ ")
                failure = f"宣告的算式 {d.formula} 算出來 {got}，跟畫面上的 {shown} 對不上"
                continue
            inputs = "；".join(f"{x.text}：{hit[1]}" for x in d.inputs if (hit := found[x.text]) is not None)
            note = f"（{d.note}）" if d.note else ""
            return f"推算：{d.formula} {d.result_text}{note}" + (f"；{inputs}" if inputs else ""), None
        return None, failure


def _is_unit_constant(x: FoundNumber) -> bool:
    """10、100、1000…：換算單位用的常數（例 ×100 變成百分比），算式裡用不必有出處。"""
    v = x.value
    return v.denominator == 1 and v >= 10 and str(v.numerator).strip("0") == "1"


def _shown_number(t: ScreenText, num: FoundNumber) -> str:
    return t.text if t.whole else num.text


def reconcile(plan: BuildPlan, *, sources: Sequence[ScreenTextSource] | None = None) -> NumberReport:
    """數字對帳：每個分鏡上畫面的每個數字 → 出處。不渲染、不連網。

    sources：要看哪些上畫面的字（預設 default_sources()：財經圖表；Vox 索引卡 #8 加進去）。"""
    article = read_article(plan.project.paths)
    src = _Sources(article, plan.public_data)
    texts = _own_texts(plan, default_sources() if sources is None else sources)
    shots: dict[str, list[NumberSource]] = {}
    notes: dict[str, list[str]] = {}
    for st in plan.timeline.shots:
        shot = st.shot
        decls: tuple[Derivation, ...] = tuple(shot.derived)
        used: set[int] = set()
        items: list[NumberSource] = []
        for t in texts[shot.id]:
            for num in find_numbers(t.text):
                shown = _shown_number(t, num)
                kind, detail = "none", ""
                used.update(k for k, d in enumerate(decls) if num.matches(d.value.value))
                if t.public is not None or (hit := src.in_public(num, shot.id)) is not None:
                    kind, detail = "public", src.in_public(num, shot.id, t.public) or ""
                elif (ok := src.derived(num, shown, shot.id, decls))[0] is not None:
                    kind, detail = "derived", ok[0]
                elif (found := src.in_article(num)) is not None:
                    kind, detail = "article", found
                    if ok[1]:
                        notes.setdefault(shot.id, []).append(f"{shown}：{ok[1]}（文章裡有這個數字，以原文為準）")
                else:
                    detail = ok[1] or _why_unsourced(article)
                items.append(NumberSource(shot.id, shot.number, t.where, t.of, t.text, shown, kind, detail))
                if t.whole:
                    break
        for k, d in enumerate(decls):
            if k not in used:
                notes.setdefault(shot.id, []).append(
                    f"{d.where} 宣告了 {d.value.text}（{d.formula}），但這個分鏡畫面上沒有這個數字")
        shots[shot.id] = items
    return NumberReport(shots=shots, article=article, notes=notes)


def _why_unsourced(article: Article) -> str:
    if article.path is None:
        return "專案裡還沒有文章文字（article/text.txt），對不到原文；也不是公有數據或宣告過的推算"
    return "文章裡找不到這個數字，也不是公有數據或宣告過的推算"


# ---------------------------------------------------------------- 擋出片（build 的 preflight）


FIX_HINT = (
    "修法：改成文章裡寫的數字；推算出來的數字（差值、一年前的值…），在那個分鏡加 "
    '"derived": [{"value": "0.62", "formula": "4.58 - 3.96"}] 宣告算式（算式裡的數字要找得到出處）。'
    "預覽照樣可以出，審片頁上會標出這些數字。"
)


def summary_line(plan: BuildPlan) -> str:
    """一行摘要（指令列預覽印出來，給 Claude 看）：幾個數字、各種出處幾個、哪些對不到。"""
    rep = reconcile(plan)
    items = [n for v in rep.shots.values() for n in v]
    counts = "、".join(f"{label} {sum(1 for n in items if n.kind == k)}" for k, label in KIND_LABELS.items()
                       if any(n.kind == k for n in items))
    line = f"數字對帳：畫面上 {len(items)} 個數字" + (f"（{counts}）" if counts else "")
    bad = rep.unsourced
    if bad:
        line += "；找不到出處、正式出片會被擋下：" + "；".join(f"{n.shot_label} 的{n.place}：{n.shown}" for n in bad)
    return line


def check_numbers(plan: BuildPlan) -> None:
    """正式出片前的數字對帳：有無出處的數字就丟 ProjectError（繁中，點名分鏡與數字），不渲染、不出片。"""
    rep = reconcile(plan)
    bad = rep.unsourced
    if not bad:
        return
    hint = FIX_HINT
    if rep.article.path is None:
        hint = "專案裡還沒有文章文字（article/text.txt）：先用 extract 擷取文章，數字才對得到原文。\n" + hint
    raise ProjectError(
        [f"{n.shot_label} 的{n.place}：{n.shown} 找不到出處" for n in bad],
        title=f"數字對帳沒過，不能出片：畫面上有 {len(bad)} 個數字找不到出處",
        hint=hint,
    )


# ---------------------------------------------------------------- 審片頁


CARD_GROUP = 6
"""同一個位置（例：數值）同一種出處的字超過這麼多個，卡片上併成一行（長的數列不要洗版）；無出處的一律逐個列。"""


def _card_items(nums: Sequence[NumberSource], public: PublicChart | None, CheckItem: Any) -> list:
    groups: dict[tuple[str, str], list[NumberSource]] = {}
    for n in nums:
        if n.kind != "none":
            groups.setdefault((n.where, n.kind), []).append(n)
    big = {k for k, g in groups.items() if len({(n.context, n.of) for n in g}) > CARD_GROUP}
    out, done = [], set()
    for n in nums:
        key = (n.where, n.kind)
        if key not in big:
            out.append(CheckItem(n.shown, detail=f"{n.place}｜{n.detail}", status="block" if n.kind == "none" else "ok",
                                 label=n.label))
        elif key not in done:
            done.add(key)
            g = groups[key]
            count = len({(m.context, m.of) for m in g})
            if n.kind == "public" and public is not None:
                detail = f"圖上每個點都是公有數據：{public.source.removeprefix('來源：')}"
            else:
                detail = f"每個都對得到出處，例：{g[0].place}｜{g[0].detail}"
            out.append(CheckItem(f"{n.where} {count} 個", detail=detail, status="ok", label=n.label))
    return out


def number_review(plan: BuildPlan) -> ReviewExtras:
    """審片頁的數字對帳：每張卡列出這個分鏡畫面上的數字與出處；有無出處的，頁首也列出來。"""
    from article_to_video.review import CardSection, CheckItem, ReviewExtras  # 避免循環匯入

    rep = reconcile(plan)
    cards: dict[str, list[CardSection]] = {}
    for st in plan.timeline.shots:
        sid = st.shot.id
        items = _card_items(rep.shots[sid], plan.public_data.get(sid), CheckItem)
        notes = list(rep.notes.get(sid, []))
        n_bad = sum(1 for n in rep.shots[sid] if n.kind == "none")
        if n_bad:
            notes.insert(0, f"有 {n_bad} 個數字找不到出處，擋下出片。")
        if not items:
            notes.append("沿用上一張的圖，數字記在上一張；這一張沒有新的數字。" if st.shot.continues
                         else "畫面上沒有數字。")
        cards[sid] = [CardSection("numbers", "畫面上的數字與出處", items, note=" ".join(notes))]
    bad = rep.unsourced
    if bad:
        page = [CardSection(
            "numbers", f"數字對帳：{len(bad)} 個數字找不到出處，不能出片",
            [CheckItem(f"分鏡 {n.shot_number}：{n.shown}", detail=n.place, status="block", label=n.label) for n in bad],
            note="預覽可以先看，正式出片會被擋下。" + FIX_HINT.removesuffix("預覽照樣可以出，審片頁上會標出這些數字。"),
        )]
    else:
        total = sum(len(v) for v in rep.shots.values())
        page = [CardSection("numbers", "數字對帳", [CheckItem(f"畫面上 {total} 個數字都有出處", status="ok")])]
    return ReviewExtras(cards=cards, page=page)

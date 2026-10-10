"""數字對帳：列出每個分鏡上畫面的數字，對回出處；對不到的擋下正式出片。

  reconcile(plan) → NumberReport        每個分鏡的數字與出處（不渲染、不連網，plan_build 之後幾毫秒）
  check_numbers(plan)                   build 的 preflight：有無出處就丟 ProjectError（繁中，點名分鏡 id、審片頁編號、數字）
  number_review(plan)                   審片頁的 Contributor：每張卡的「numbers」欄＋頁首摘要
  summary_line(plan)                    指令列預覽印的一行摘要（給 Claude 看）

上畫面的字（ScreenTextSource，每個分鏡一份；預設 default_sources()）：
  財經圖表（chart_texts）：標題、副標（單位那行；自訂的縱軸說明也算）、角標、數值（柱上／線頭的數字，照 decimals、
  prefix、suffix 格式化成畫面上的樣子；柱上文字覆寫生效時看覆寫的字；折線、K 線每個點都算）、類目名（x 軸標籤，
  全部都算，不管引擎有沒有抽稀）、標註（text、sub）、大數字（number cue 的值與說明）。
  接續分鏡只算它自己新出現的字（沿用的圖記在上一個分鏡）。
  不算：來源行（data.source，那是出處本身）、預設的縱軸說明「縱軸未從 0 開始」、刻度值（引擎依數據範圍排的尺，
  不是主張；數據本身已經對過帳）、片尾卡（不是分鏡：頻道名、彙整的來源行、固定的免責聲明，都是出處或固定字）。
  Vox 分鏡（內部開關打開時才有，ADR 0001）的標題卡、索引卡、圖說、剪報：vox.vox_texts，也在 default_sources()。

出處分類（每個數字照順序找，找到就停）：
  public   白名單公有數據：這個分鏡的公有數據圖畫的值、期間標籤、系列名稱（附系列、期間、擷取日期）。
  derived  推算：分鏡設定的 derived 宣告了算式，算式的每個輸入都找得到出處（原文或這張圖的公有數據；10、100、1000…
           換算單位的常數免），而且算出來四捨五入到畫面的位數＝畫面上的數字。
  article  原文出現：文章裡有一個數字，照下面的比對規則就是畫面上的數字。
  figure   原文圖表：數字只畫在文章的圖上（文字裡沒有），Claude 看過圖N、把讀到的數字記在
           article/figure_numbers.json（load_figure_numbers 驗證）；照同一套比對規則對上就算。審片頁標「原文圖表
           （圖N，Claude 讀圖）」、標成要看一下，請作者對照原圖。推算的輸入也可以是讀圖記下的數字。
  none     無出處：以上都不是。有任何一個 → check_numbers 擋下正式出片（預覽照樣出，審片頁標出來）。

讀圖記下的數字（article/figure_numbers.json；Claude 寫，重跑文章擷取會拿掉）：
    {"figures": {"圖1": [{"value": "4.58", "label": "兩年期殖利率，2023 年平均"}, …], "圖2": […]}}
  圖號要是 article.json 列的圖；value＝圖上那一個數字（照圖上的寫法，字串）；label＝它在圖上是什麼（作者對照用，必填）。

分鏡設定的推算宣告（shots.json 每個分鏡可選的 derived；parse_derived 驗證，錯誤繁中點名「分鏡 SH02 的 derived[0]」）：
    "derived": [{"value": "0.62", "formula": "4.58 - 3.96", "note": "兩年期減十年期"}]
  value＝畫面上那個數字（字串或數字）；formula＝四則運算＋括號（- − * × x / ÷，數字可帶千分位與 %）；note 選填。

找數字（find_numbers，文章和畫面用同一套）：
  - 全形轉半形、千分位（12,345＝12345）、小數尾巴的 0（4.50＝4.5）、萬／億／兆（1.2 萬＝12,000；文章寫「3.2 兆」時，
    畫面只寫 3.2 也對得上）、百分之（百分之四點五八＝4.58%）。
  - 正負號是數字的一部分：緊貼在數字前面的 −／-（前面不是數字或英文字母；「2019-2024」是範圍），或「負」。
  - 中文數字只有像數量的才算：有小數（四點五八）、百分之、萬億兆（一萬兩千）、一串像年份的數字（二〇二三），
    或後面接了單位（%、個百分點、基點、碼、倍、成、元、點）。其他是詞的一部分，不是數字：十年期、兩年期、一季、一年前。
  - 「10 年期」「2 年期」（阿拉伯數字＋年期／天期）是天期的名字，也不算數字。
  - 單位：數字旁邊寫的「%」（含百分之）或「個百分點」記下來比對用；其他單位不比。

比對規則（畫面上的數字 s 對文章裡的數字 a；公有數據圖上的值、期間、系列名稱也照這套）：
  1. 一模一樣（正規化之後數值相等，正負號也一樣）→ 對得上。不管有沒有寫單位（作者常把「個百分點」寫成 %）。
  2. 四捨五入：a 四捨五入到 s 寫的那一位等於 s，正負號一樣，而且
     - s 至少寫了兩位有效數字（MIN_ROUNDED_DIGITS）：4.58 → 4.6 可以，→ 5 不行。只有一位有效數字的，不管是整數（4、5）
       還是小數（0.6、0.7），都要文章裡一模一樣——一位數太容易「剛好」四捨五入到文章裡某個不相干的數；整數跟小數同一套，
       整數不會比小數好對；
     - 兩邊都寫了單位時要一樣：文章的 4.58%（殖利率）不能四捨五入成畫面的「4.6 個百分點」（差幾個百分點）。
  3. 畫面寫得比文章精確（4.585 對 4.58）→ 對不上。
  推算（derived）的算式結果只要四捨五入到畫面的位數就算：算式是明寫的，不會是巧合。

文章文字只從 read_article() 讀：文章擷取（article.py）寫的 <專案>/article/article.json 段落（有頁碼），
沒有就讀 article/text.txt（段與段之間空一行）。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

from article_to_video.article import ArticlePaths
from article_to_video.project import ProjectError, ProjectPaths
from article_to_video.public_data import WHITELIST, PublicChart
from article_to_video.timeline import ShotTiming, effective_chart, resolve_highlight

if TYPE_CHECKING:
    from article_to_video.build import BuildPlan
    from article_to_video.review import ReviewExtras

__all__ = [
    "Article",
    "Derivation",
    "FigureNumber",
    "FoundNumber",
    "NumberReport",
    "NumberSource",
    "ScreenText",
    "ScreenTextSource",
    "chart_texts",
    "check_numbers",
    "default_sources",
    "find_numbers",
    "load_figure_numbers",
    "number_review",
    "parse_derived",
    "read_article",
    "reconcile",
    "summary_line",
]


KIND_LABELS = {"article": "原文", "figure": "原文圖表", "public": "公有數據", "derived": "推算", "none": "無出處"}


# ---------------------------------------------------------------- 找數字（正規化）


@dataclass(frozen=True)
class FoundNumber:
    text: str
    """原文寫的樣子（例「4.58%」「四點五八」「1.2 萬」「−0.5%」）。"""
    value: Fraction
    """數值（帶正負號；百分號、千分位、萬億都換算過）。"""
    quantum: Fraction
    """寫到哪一位（4.58 → 0.01；1.2 萬 → 1000）。"""
    start: int
    end: int
    bare: Fraction | None = None
    """後面接萬／億／兆（或百、千）時，乘上去之前的數（「3.2 兆」→ 3.2）：圖的單位行寫「兆美元」、柱上只寫 3.2 也對得上。"""
    unit: str = ""
    """寫在數字旁邊的單位：「%」（含百分之）、「個百分點」，沒寫就空字串。"""

    @property
    def digits(self) -> int:
        """寫出來的有效位數：4.58 → 3、4.6 → 2、5 → 1、0.7 → 1、10 → 2、1.2 萬 → 2、2023 → 4。"""
        n = round(abs(self.value) / self.quantum)
        return len(str(n)) if n else 1

    def rounds_from(self, other: Fraction) -> bool:
        """other 四捨五入到這個數字寫的位數就是它（正負號要一樣；剛好在中間往哪邊進位都算）。"""
        return (other < 0) == (self.value < 0) and abs(other - self.value) * 2 <= self.quantum

    def sourced_by_value(self, other: Fraction, unit: str = "") -> bool:
        """畫面上的這個數字能不能算是 other（文章或公有數據裡的數）：比對規則見模組說明。"""
        if other == self.value:
            return True
        return self.digits >= MIN_ROUNDED_DIGITS and self.rounds_from(other) and not (
            self.unit and unit and self.unit != unit)

    def sourced_by(self, other: FoundNumber) -> bool:
        """對方（文章裡的數字）不管帶不帶萬億單位，照比對規則就是畫面上的這個數字。"""
        return self.sourced_by_value(other.value, other.unit) or (
            other.bare is not None and self.sourced_by_value(other.bare, other.unit))


MIN_ROUNDED_DIGITS = 2
"""畫面上的數字至少寫了幾位有效數字，才能是文章數字四捨五入來的（4.58 → 4.6 可以，→ 5 不行）。"""


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

_SIGN = r"(?P<sign>(?<![0-9A-Za-z.])-|[負负]\s*)?"
"""負號：緊貼在數字前面、前面不是數字或英文字母的 -（「2019-2024」的 - 是範圍）；或「負」。"""
_ARABIC_RE = re.compile(
    _SIGN + r"(?<![\d.])(?P<int>\d{1,3}(?:,\d{3})+(?!\d)|\d+)(?:\.(?P<frac>\d+))?"
    r"(?:\s*(?P<mult>[百千萬万億亿兆]+)(?!分))?"
    r"(?P<pct>\s*(?:%|個百分點|个百分点|百分點|百分点))?"
)
_CHINESE_RE = re.compile(
    _SIGN + rf"(?P<pct>百分之)?(?P<int>[{_CN_CHARS}]+)(?:[點点](?P<frac>[{''.join(_CN_DIGITS)}]+))?"
)
_TENOR_RE = re.compile(r"\s*[年天]期")
"""「10 年期」「2 年期」：天期的名字，不是數量。"""
_CN_UNIT_RE = re.compile(r"\s*(?:%|個百分點|个百分点|百分點|百分点|基點|基点|碼|码|倍|成|美元|元|點|点)")
"""中文數字後面接這些單位才是數量（「降息三碼」）；沒有單位、沒有小數點的中文整數是詞的一部分（十年期、兩年期、一季）。"""


def _unit(written: str | None) -> str:
    """數字旁邊寫的單位，換成比對用的兩種：「%」或「個百分點」；其他（碼、倍…）不比。"""
    w = (written or "").strip()
    if w in ("%", "百分之"):
        return "%"
    if w in ("個百分點", "个百分点", "百分點", "百分点"):
        return "個百分點"
    return ""


def _chinese_is_number(m: re.Match[str], norm: str) -> bool:
    """中文數字是數量（要對帳）還是詞的一部分：有小數、百分之、萬億兆、像年份的一串數字（二〇二三），
    或後面接了單位（%、個百分點、碼、倍、成、元…）才算數量。「萬一」這種不是數字開頭的也不算。"""
    run = m.group("int")
    if run[0] not in _CN_DIGITS and run[0] != "十":
        return False
    if m.group("frac") or m.group("pct") or any(c in _CN_BIG for c in run):
        return True
    if len(run) >= 3 and all(c in _CN_DIGITS for c in run):
        return True
    return _CN_UNIT_RE.match(norm, m.end()) is not None


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
        end = m.end()
        for i in range(m.start(), end):
            taken[i] = True
        if not m.group("mult") and not m.group("pct") and _TENOR_RE.match(norm, end):
            continue  # 「10 年期」是天期的名字
        frac = m.group("frac") or ""
        sign = -1 if m.group("sign") else 1
        value = Fraction(m.group("int").replace(",", "") + ("." + frac if frac else ""))
        quantum = Fraction(1, 10 ** len(frac))
        mult = 1
        for c in m.group("mult") or "":
            mult *= _MULT[c]
        found.append(FoundNumber(text[m.start():end].strip(), sign * value * mult, quantum * mult, m.start(), end,
                                 bare=sign * value if mult != 1 else None, unit=_unit(m.group("pct"))))
    for m in _CHINESE_RE.finditer(norm):
        if any(taken[m.start():m.end()]) or not _chinese_is_number(m, norm):
            continue
        parsed = _chinese_int(m.group("int"))
        if parsed is None:
            continue
        value, unit = parsed
        sign = -1 if m.group("sign") else 1
        frac = m.group("frac") or ""
        if frac:
            value = Fraction(f"{value}.{''.join(str(_CN_DIGITS[c]) for c in frac)}")
            quantum = Fraction(1, 10 ** len(frac))
        else:
            value, quantum = Fraction(value), Fraction(unit)
        after = _CN_UNIT_RE.match(norm, m.end())
        found.append(FoundNumber(text[m.start():m.end()], sign * value, quantum, m.start(), m.end(),
                                 bare=sign * value / unit if unit != 1 else None,
                                 unit=_unit(m.group("pct") or (after.group(0) if after else ""))))
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


# ---------------------------------------------------------------- 原文圖表（Claude 讀圖記下的數字）

FIGURE_NUMBERS_EXAMPLE = ('{"figures": {"圖1": [{"value": "4.58", "label": "兩年期殖利率，2023 年平均"}, '
                          '{"value": "3.96", "label": "十年期殖利率，2023 年平均"}]}}')
FIGURE_NUMBER_KEYS = ("value", "label")
_FIGURE_KEY_RE = re.compile(r"^圖([1-9]\d*)$")
_ITEM_EXAMPLE = '{"value": "4.58", "label": "兩年期殖利率，2023 年平均"}'


@dataclass(frozen=True)
class FigureNumber:
    """Claude 看文章的某張圖（圖N）讀到的一個數字：文章文字裡沒有、只畫在圖上的數字，靠它對到出處（原文圖表）。"""

    figure: str
    """哪張圖：「圖1」（article.json 的圖號）。"""
    number: FoundNumber
    """讀到的數字（寫成圖上的樣子：圖上寫 4.50 就寫 "4.50"）。"""
    label: str
    """這個數字在圖上是什麼（例「兩年期殖利率，2023 年平均」），作者對照原圖用。"""

    @property
    def detail(self) -> str:
        return f"原文圖表（{self.figure}，Claude 讀圖）：{self.label}，讀到 {self.number.text}"


def _figure_labels(paths: ProjectPaths) -> list[str] | None:
    """文章擷取清單（article.json）列的圖號；沒有清單（或讀不了）＝ None。"""
    manifest = ArticlePaths(paths.article).manifest
    if not manifest.is_file():
        return None
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        return [str(im.get("label") or f"圖{im['n']}") for im in raw.get("images", [])]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def load_figure_numbers(paths: ProjectPaths) -> tuple[tuple[FigureNumber, ...], list[str]]:
    """讀並驗證 article/figure_numbers.json（Claude 讀圖記的數字）。沒有這個檔＝沒有讀圖的數字。
    回傳 (數字, 問題清單)；問題是繁中、點名「article/figure_numbers.json 的 圖1[0]」。"""
    path = paths.figure_numbers
    name = "article/figure_numbers.json"
    if not path.is_file():
        return (), []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return (), [f"{name} 第 {e.lineno} 行第 {e.colno} 個字附近不是合法的 JSON（{e.msg}）"]
    if not isinstance(raw, dict) or not isinstance(raw.get("figures"), dict):
        return (), [f"{name} 要長這樣：{FIGURE_NUMBERS_EXAMPLE}"]
    problems: list[str] = []
    bad = [k for k in raw if k != "figures"]
    if bad:
        problems.append(f"{name} 有不認得的欄位「{'、'.join(bad)}」（只認 figures）")
    labels = _figure_labels(paths)
    out: list[FigureNumber] = []
    for fig, items in raw["figures"].items():
        where = f"{name} 的 {fig}"
        if not _FIGURE_KEY_RE.match(fig):
            problems.append(f"{name} 的「{fig}」不是圖號：照文章擷取的圖號寫，例「圖1」")
            continue
        if labels is None:
            problems.append(f"{name} 寫了「{fig}」，可是專案裡沒有文章擷取的清單（article/article.json），對不到文章的圖")
            continue
        if fig not in labels:
            have = "、".join(labels) if labels else "文章裡沒有圖"
            problems.append(f"{name} 寫了「{fig}」，可是文章沒有這張圖（現有：{have}）")
            continue
        if not isinstance(items, list) or not items:
            problems.append(f"{where} 要是讀到的數字清單，例 [{_ITEM_EXAMPLE}]")
            continue
        for k, item in enumerate(items):
            at = f"{where}[{k}]"
            if not isinstance(item, dict):
                problems.append(f"{at} 要是物件，例 {_ITEM_EXAMPLE}")
                continue
            extra = [key for key in item if key not in FIGURE_NUMBER_KEYS]
            if extra:
                problems.append(f"{at} 有不認得的欄位「{'、'.join(extra)}」（只認 value、label）")
            value_raw = item.get("value")
            ok_type = isinstance(value_raw, (str, int, float)) and not isinstance(value_raw, bool)
            nums = find_numbers(str(value_raw)) if ok_type else []
            if len(nums) != 1:
                problems.append(f"{at} 的 value 要是圖上的那一個數字，照圖上的寫法（例 \"4.58\"），現在是 {value_raw!r}")
            label = item.get("label")
            if not isinstance(label, str) or not label.strip():
                problems.append(f"{at} 少了 label：這個數字在圖上是什麼（例「兩年期殖利率，2023 年平均」），作者要對照原圖")
                continue
            if len(nums) == 1:
                out.append(FigureNumber(fig, nums[0], label.strip()))
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
    """跟引擎 TY.fmt 一樣：絕對值 toFixed（照浮點數的精確值四捨五入，剛好在中間就進位）、千分位、負號用 −。"""
    q = Decimal(abs(v)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return ("−" if v < 0 else "") + format(q, f",.{decimals}f")


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

    seen: set[str] = set()
    for hq in cues:  # 標註照時間一個換一個：接續分鏡沿用上一個分鏡的標註（換之前還在畫面上），再換成自己的
        if hq.kind != "highlight":
            continue
        h = resolve_highlight(hq.fields, data)
        for k in ("text", "sub"):
            v = h[k]
            if isinstance(v, str) and v and v not in seen:
                seen.add(v)
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
    """數字對帳預設看哪些字：財經圖表、Vox 分鏡（標題卡、索引卡、圖說、原文剪報；vox.vox_texts）。"""
    from article_to_video.vox import vox_texts  # 避免循環匯入

    return [chart_texts, vox_texts]


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
    figure: str = ""
    """原文圖表：讀自哪張圖（「圖1」）；其他種類是空字串。"""

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
    """數字可以對到的出處：文章（全專案共用）、Claude 讀文章的圖記下的數字、這個分鏡畫的公有數據、這個分鏡宣告的推算。"""

    def __init__(self, article: Article, public: Mapping[str, PublicChart],
                 figures: Sequence[FigureNumber] = ()) -> None:
        self.article = article
        self.entries = [(p, f) for p in article.paragraphs for f in find_numbers(p.text)]
        self.public = public
        self.figures = figures

    def figure_of(self, num: FoundNumber) -> FigureNumber | None:
        """Claude 讀文章的圖記下的數字裡，照比對規則就是 num 的那一個。"""
        return next((f for f in self.figures if num.sourced_by(f.number)), None)

    def in_figure(self, num: FoundNumber) -> str | None:
        """有讀圖記下的數字對得上 num 就回「原文圖表（圖N，Claude 讀圖）：…」。"""
        f = self.figure_of(num)
        return None if f is None else f.detail

    def in_article(self, num: FoundNumber) -> str | None:
        """文章裡有沒有一個數字，四捨五入到 num 寫的位數就是 num；有就回「原文第 N 段：…前後文…」。"""
        for p, f in self.entries:
            if num.sourced_by(f):
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
            if num.sourced_by_value(Fraction(repr(v.value))):
                return chart.describe(i)
        for v in chart.values:
            if any(num.sourced_by(f) for f in find_numbers(v.label)):
                first, last = chart.values[0].label, chart.values[-1].label
                return f"公有數據的期間（圖上 {first}～{last}）：FRED {chart.request.series}"
        names = [chart.request.name, *(WHITELIST[s].name for s in chart.series)]
        for name in names:
            if any(num.sourced_by(f) for f in find_numbers(name)):
                return f"公有數據的系列名稱：{name}（FRED {chart.request.series}）"
        return None

    def lookup(self, num: FoundNumber, shot_id: str) -> tuple[str, str] | None:
        """(種類, 出處說明)：先看這張圖的公有數據，再看文章。"""
        if (hit := self.in_public(num, shot_id)) is not None:
            return "public", hit
        if (hit := self.in_article(num)) is not None:
            return "article", hit
        if (hit := self.in_figure(num)) is not None:
            return "figure", hit
        return None

    def derived(self, num: FoundNumber, shown: str, shot_id: str,
                decls: Sequence[Derivation]) -> tuple[str | None, str | None]:
        """宣告的推算：(成立時的說明, 宣告了但不成立的原因)。"""
        failure = None
        for d in decls:
            if not num.rounds_from(d.value.value):
                continue
            found = {x.text: self.lookup(x, shot_id) for x in d.inputs}
            missing = [x.text for x in d.inputs if found[x.text] is None and not _is_unit_constant(x)]
            if missing:
                failure = (f"宣告的推算 {d.formula} 裡，{'、'.join(missing)} 找不到出處"
                           "（算式裡的數字要在文章裡、是讀圖記下的數字，或是這張圖的公有數據）")
                continue
            if not num.rounds_from(d.result):
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
    src = _Sources(article, plan.public_data, plan.project.figure_numbers)
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
                kind, detail, figure = "none", "", ""
                used.update(k for k, d in enumerate(decls) if num.rounds_from(d.value.value))
                if (pub := src.in_public(num, shot.id, t.public)) is not None:
                    kind, detail = "public", pub
                elif (ok := src.derived(num, shown, shot.id, decls))[0] is not None:
                    kind, detail = "derived", ok[0]
                elif (found := src.in_article(num)) is not None:
                    kind, detail = "article", found
                    if ok[1]:
                        notes.setdefault(shot.id, []).append(f"{shown}：{ok[1]}（文章裡有這個數字，以原文為準）")
                elif (fig := src.figure_of(num)) is not None:
                    kind, detail, figure = "figure", fig.detail, fig.figure
                    if ok[1]:
                        notes.setdefault(shot.id, []).append(f"{shown}：{ok[1]}（文章的圖上有這個數字，以原圖為準）")
                else:
                    detail = ok[1] or _why_unsourced(article)
                items.append(NumberSource(shot.id, shot.number, t.where, t.of, t.text, shown, kind, detail, figure))
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
    return "文章裡找不到這個數字，也不是讀圖記下的數字、公有數據或宣告過的推算"


# ---------------------------------------------------------------- 擋出片（build 的 preflight）


FIX_HINT = (
    "修法：改成文章裡寫的數字；數字只在文章的圖上（文字裡沒有）：Read 那張圖（article/images/圖N.*），把讀到的數字記在 "
    'article/figure_numbers.json，例 {"figures": {"圖1": [{"value": "4.58", "label": "兩年期殖利率，2023 年平均"}]}}；'
    "推算出來的數字（差值、一年前的值…），在那個分鏡加 "
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
        line += "；找不到出處、正式出片會被擋下：" + "；".join(f"{n.shot_label}的{n.place}：{n.shown}" for n in bad)
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
        [f"{n.shot_label}的{n.place}：{n.shown} 找不到出處" for n in bad],
        title=f"數字對帳沒過，不能出片：畫面上有 {len(bad)} 個數字找不到出處",
        hint=hint,
    )


# ---------------------------------------------------------------- 審片頁


CARD_GROUP = 6
"""同一個位置（例：數值）同一種出處的字超過這麼多個，卡片上併成一行（長的數列不要洗版）；無出處、讀圖的一律逐個列。"""
_CARD_STATUS = {"none": "block", "figure": "warn"}
"""卡片上小標籤的顏色：無出處＝擋下出片；原文圖表＝要看一下（Claude 讀圖讀的，請作者對照原圖）；其他＝沒問題。"""


def _figures_note(nums: Sequence[NumberSource]) -> str:
    """給作者的一句：哪幾個數字是 Claude 從他文章的哪幾張圖讀的，請他對照原圖。沒有就空字串。"""
    figs = list(dict.fromkeys(n.figure for n in nums if n.kind == "figure"))
    if not figs:
        return ""
    count = sum(1 for n in nums if n.kind == "figure")
    return (f"其中 {count} 個數字是 Claude 看你文章的{'、'.join(figs)}讀出來的（標「原文圖表」）："
            f"請對照你的原圖，數字和它是什麼都要對。")


def _card_items(nums: Sequence[NumberSource], public: PublicChart | None, CheckItem: Any) -> list:
    groups: dict[tuple[str, str], list[NumberSource]] = {}
    for n in nums:
        if n.kind not in ("none", "figure"):  # 無出處、讀圖的數字一律逐個列：作者要一個一個看
            groups.setdefault((n.where, n.kind), []).append(n)
    big = {k for k, g in groups.items() if len({(n.context, n.of) for n in g}) > CARD_GROUP}
    out, done = [], set()
    for n in nums:
        key = (n.where, n.kind)
        if key not in big:
            out.append(CheckItem(n.shown, detail=f"{n.place}｜{n.detail}", status=_CARD_STATUS.get(n.kind, "ok"),
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
        figures = _figures_note(rep.shots[sid])
        if figures:
            notes.insert(0, figures)
        if n_bad:
            notes.insert(0, f"有 {n_bad} 個數字找不到出處，擋下出片。")
        if not items:
            notes.append("沿用上一張的圖，數字記在上一張；這一張沒有新的數字。" if st.shot.continues
                         else "畫面上沒有數字。")
        # 每個數字都對得到出處：安靜的一行，點開看每個數字的出處；有找不到出處、或有 Claude 讀圖讀的數字才整區攤開
        summary = "" if n_bad or figures else (
            f"畫面上 {len(rep.shots[sid])} 個數字都對得到出處" if items else " ".join(notes))
        cards[sid] = [CardSection("numbers", "畫面上的數字與出處", items, note=" ".join(notes), summary=summary)]
    bad = rep.unsourced
    if bad:
        page = [CardSection(
            "numbers", f"數字對帳：{len(bad)} 個數字找不到出處，不能出片",
            [CheckItem(f"分鏡 {n.shot_number}：{n.shown}", detail=n.place, status="block", label=n.label) for n in bad],
            note="這些數字在你的文章裡找不到（文字裡沒有，也不在 Claude 讀過的圖上），也不是公有數據或寫明算式的推算。"
                 "預覽可以先看，正式出片會被擋下。要怎麼改寫在那張卡上（例如改成文章裡的數字）；"
                 "如果是你文章某張圖上的數字，告訴 Claude 是哪張圖；如果是算出來的，告訴 Claude 怎麼算的就好。",
        )]
    else:
        total = sum(len(v) for v in rep.shots.values())
        read = [n for v in rep.shots.values() for n in v if n.kind == "figure"]
        summary = f"數字對帳：畫面上 {total} 個數字都有出處"
        if read:
            summary += f"；其中 {len(read)} 個是 Claude 從你文章的圖讀的（卡片上標「原文圖表」），請對照你的原圖"
        page = [CardSection("numbers", "數字對帳", [], summary=summary)]
    return ReviewExtras(cards=cards, page=page)

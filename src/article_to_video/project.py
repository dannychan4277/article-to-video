"""專案模型：一支影片一個資料夾。

專案資料夾放在影片資料夾裡：~/Movies/解說影片/<日期>-<片名>/專案/（videos.py；作者點頭、出片之後，
成片和字幕檔複製到上一層「日期-片名」資料夾，publish.py）。

資料夾長這樣（檔名寫死在這裡，其他模組一律透過 ProjectPaths 拿路徑）：

    <專案>/
      narration.json     旁白稿：{"segments": {"s01": "…", "s02": "…"}}，物件順序＝播放順序
      shots.json         分鏡設定：{"shots": [ {分鏡}, … ]}
      settings.json      設定快照（選填）：建置參數（配音層、段間留白、片尾卡秒數…）＋第一次建置時從使用者設定
                         複製來的頻道名、logo、品牌色、聲音、語速、配樂、預設比例（user_settings.py）
      logo.png           片尾卡的 logo（設定快照的一部分，從使用者資料位置複製進來；沒設 logo 就沒有）
      article/           文章原檔、擷取文字與原圖（文章擷取那張票放）
      public_data/       公有數據快照（<系列代號>.json：數字＋擷取日期）；抓過之後重建不用網路，刪掉就重抓
      box_checks.json    Claude 核對過的 Vox 框（依框的內容雜湊記，見 boxes.py）；不在 build/ 裡，刪 build/ 不會掉
      check_log.json     獨立審片每一輪的結果、每個問題的處理（改好了／接受不改；見 check_log.py）；刪 build/ 不會掉
      output.json        每個比例最近一次出片：放到哪裡、作者點頭的是審片頁第幾版（publish.py）
      審片留言.txt       作者貼回來的審片留言（Claude 存下來給 feedback 讀；可有可無）
      成片.mp4           燒好字幕的成片（16:9）
      審片頁.html        給作者看的審片頁（預覽與出片都會更新）
      字幕.srt           字幕檔（檔名故意跟成片不同：同名 .srt 會被播放器自動疊上去，變成兩層字幕）
      成片-9x16.mp4、字幕-9x16.srt、審片頁-9x16.html   直式（9:16）版，跟 16:9 的並存（--aspect 9:16 或預設比例設直式）
      build/             建置產物，可整個刪掉重來
        narration/       配音層快取（依「配音層｜聲音｜語速｜文字」）與 timing.json
        vox/             原文剪報圖（excerpt-<內容雜湊>.png，跟比例無關；excerpt.py）
        16x9/、9x16/     各畫面比例的產物：specs/、clips/、logs/、fonts/、stills/、boxes/（Vox 框的靜態圖）、
                         timeline.json、review.json、subs.*、narration.wav、soundtrack.wav、
                         checks/（自動檢查：qa、字幕帶的快取）、checks.json（自動檢查彙整）、
                         independent-review/（給獨立審片 agent 的素材，見 independent_review.py）

設定檔都是 UTF-8 JSON，由 Claude 寫、這裡驗證。驗證錯誤一次列出全部問題，每條都指出哪個分鏡、哪個欄位。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

__all__ = [
    "CHART_CUE_KINDS",
    "CHART_DATA_KEYS",
    "SETTINGS_KEYS",
    "ZH_TW_VOICES",
    "Anchor",
    "ChartConfig",
    "Cue",
    "Project",
    "ProjectError",
    "ProjectPaths",
    "Settings",
    "Shot",
    "VariantPaths",
    "load_project",
]

NARRATION_FILE = "narration.json"
SHOTS_FILE = "shots.json"
SETTINGS_FILE = "settings.json"

ZH_TW_VOICES = {
    "zh-TW-HsiaoChenNeural": "曉臻",
    "zh-TW-HsiaoYuNeural": "曉雨",
    "zh-TW-YunJheNeural": "雲哲",
}

# t3_finance_chart 認得的 data 欄位（pushFrom 由建置自己算，不讓人寫）＋ publicData（公有數據：
# 寫系列名稱取代 series 與 source，建置時抓數據換成實際數列、自動寫來源行，見 public_data.py）
CHART_DATA_KEYS = frozenset({
    "title", "unit", "source", "badge", "chart", "series", "decimals", "prefix", "suffix",
    "highlight", "colors", "upDown", "xAxis", "xTicks", "axisNote", "push", "publicData",
})
CHART_CUE_KINDS = frozenset({"title", "bar", "line", "candle", "highlight", "number"})
VOX_CUE_KINDS = frozenset({"image", "excerpt", "box", "title", "point"})
"""Vox 分鏡的 cue：image＝文章原圖（圖N）、excerpt＝原文剪報、box＝框（螢光筆＋紅圈，框住旁白正在唸的數字）、
title＝黑底白字標題條、point＝橫線索引卡。"""
SETTINGS_KEYS = frozenset({"voice", "rate", "tts", "gap", "lead", "tail",
                           "channel", "logo", "brand_color", "music", "aspect"})

_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_RATE_RE = re.compile(r"^[+-]\d{1,3}%$")
_SHOT_KEYS = frozenset({"id", "note", "start", "continues", "chart", "vox", "derived"})
_ANCHOR_KEYS = frozenset({"segment", "word", "edge", "offset", "occurrence"})
_EDGES = {"start": "start", "end": "end", "開頭": "start", "唸完": "end"}


class ProjectError(ValueError):
    """專案設定有問題（繁中訊息，一次列出全部）。"""

    def __init__(self, problems: list[str] | str, *, title: str = "專案設定有問題", hint: str = "") -> None:
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        if len(self.problems) == 1:
            msg = f"{title}：{self.problems[0]}"
        else:
            msg = f"{title}（{len(self.problems)} 個）：\n" + "\n".join(f"- {p}" for p in self.problems)
        if hint:
            msg += "\n" + hint
        super().__init__(msg)


# ---------------------------------------------------------------- 路徑


@dataclass(frozen=True)
class VariantPaths:
    """某個畫面比例（16x9、9x16；見 timeline.FORMATS）的建置產物。"""

    root: Path

    @property
    def specs(self) -> Path:
        return self.root / "specs"

    @property
    def clips(self) -> Path:
        return self.root / "clips"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def fonts(self) -> Path:
        return self.root / "fonts"

    @property
    def timeline(self) -> Path:
        return self.root / "timeline.json"

    @property
    def ass(self) -> Path:
        return self.root / "subs.ass"

    @property
    def srt(self) -> Path:
        return self.root / "subs.srt"

    @property
    def narration_wav(self) -> Path:
        return self.root / "narration.wav"

    @property
    def soundtrack(self) -> Path:
        """旁白＋配樂混好的立體聲音軌（設定不要配樂時沒有這個檔）。"""
        return self.root / "soundtrack.wav"

    @property
    def concat_list(self) -> Path:
        return self.root / "clips.txt"

    @property
    def final_stamp(self) -> Path:
        return self.root / "final.sha256"

    @property
    def stills(self) -> Path:
        """審片頁的分鏡靜態圖：stills/<片段>/t<秒>.png ＋ stamp.json（快取戳記）。"""
        return self.root / "stills"

    @property
    def boxes(self) -> Path:
        """Vox 框的靜態圖：<分鏡>-框<n>-畫面.png（引擎畫面）、<分鏡>-框<n>-原圖.png（原圖對照）＋ stamp.json。"""
        return self.root / "boxes"

    @property
    def review_state(self) -> Path:
        """審片頁的版本紀錄：第幾版、每個分鏡的內容雜湊、這版改了哪些分鏡。"""
        return self.root / "review.json"


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def narration(self) -> Path:
        return self.root / NARRATION_FILE

    @property
    def shots(self) -> Path:
        return self.root / SHOTS_FILE

    @property
    def settings(self) -> Path:
        return self.root / SETTINGS_FILE

    @property
    def article(self) -> Path:
        return self.root / "article"

    @property
    def public_data(self) -> Path:
        """公有數據快照：每個系列一個 <代號>.json（數字＋擷取日期），抓過一次之後重建不用網路。"""
        return self.root / "public_data"

    @property
    def box_checks(self) -> Path:
        """Claude 核對過的 Vox 框（依框的內容雜湊記；框改了雜湊就變，要重新核對）。"""
        return self.root / "box_checks.json"

    @property
    def check_log(self) -> Path:
        """檢查紀錄：獨立審片每一輪的結果，和每個問題的處理（改好了／接受不改）；見 check_log.py。"""
        return self.root / "check_log.json"

    @property
    def build(self) -> Path:
        return self.root / "build"

    @property
    def narration_cache(self) -> Path:
        return self.build / "narration"

    def variant(self, name: str) -> VariantPaths:
        return VariantPaths(self.build / name)

    def video(self, variant: str = "16x9") -> Path:
        return self.root / ("成片.mp4" if variant == "16x9" else f"成片-{variant}.mp4")

    def srt(self, variant: str = "16x9") -> Path:
        return self.root / ("字幕.srt" if variant == "16x9" else f"字幕-{variant}.srt")

    def review_page(self, variant: str = "16x9") -> Path:
        """審片頁（作者在 Finder 點兩下打開的那個檔）。"""
        return self.root / ("審片頁.html" if variant == "16x9" else f"審片頁-{variant}.html")


# ---------------------------------------------------------------- 模型


@dataclass(frozen=True)
class Settings:
    """設定快照。優先序：CLI 參數 > 專案 settings.json > 這裡的預設。"""

    voice: str = "zh-TW-HsiaoChenNeural"
    rate: str = "+5%"
    tts: str = "edge"
    gap: float = 0.35
    """每段旁白之後的留白秒數（下一段的起點＝上一段起點＋長度＋留白）。"""
    lead: float = 0.15
    """錨點對到詞的「開頭」時，畫面提早這麼多秒出現，看起來才同步。"""
    tail: float = 5.0
    """片尾卡秒數：最後一段旁白唸完之後接片尾卡，停這麼久（算進成片長度）。"""
    # 以下是從使用者設定複製來的快照（user_settings.SNAPSHOT_KEYS，加上 voice、rate）
    channel: str = ""
    """頻道名（片尾卡上的名字）。"""
    logo: str | None = None
    """logo 檔（相對專案資料夾）。"""
    brand_color: str | None = None
    """品牌色 #RRGGBB，套到財經圖表主色；None＝預設紅黑白。"""
    music: str = "calm"
    """配樂（music.TRACKS 的名字）或 "none"。"""
    aspect: str = "16x9"
    """預設比例（直式那張票用）。"""


@dataclass(frozen=True)
class Anchor:
    """錨點：旁白段＋詞＋開頭或唸完＋位移。word 為 None 時指整段（開頭＝段落起點，唸完＝最後一個詞唸完）。"""

    segment: str
    word: str | None = None
    edge: str = "start"
    offset: float = 0.0
    span: tuple[int, int] | None = None
    """word 在旁白段文字裡的字元範圍 [first, last]（含），載入時算好。"""


@dataclass(frozen=True)
class Cue:
    kind: str
    at: float | Anchor
    fields: dict[str, Any]
    """at 以外、原樣傳給引擎的欄位（kind、text、sub、data、dur…）。"""
    where: str
    """給錯誤訊息用：例「分鏡 SH01 的 chart.cues[2]」。"""
    carried: bool = False
    """接續分鏡從上一個分鏡沿用過來的 cue（時間已改成負數）。"""


@dataclass(frozen=True)
class ChartConfig:
    data: dict[str, Any]
    cues: list[Cue]
    public: Any = None
    """公有數據請求（public_data.PublicRequest）；建置時換成 data.series 與 data.source 之後就是 None。"""


@dataclass(frozen=True)
class VoxConfig:
    cues: list[Cue]
    """照出場順序。載入時 fields 是分鏡設定原樣；建置時（vox.resolve_vox）換成給引擎的欄位（圖片絕對路徑、標籤…）。"""
    sources: list[str] = field(default_factory=list)
    """這個分鏡畫面上素材的出處（建置時 resolve_vox 填，片尾卡彙整）。"""
    texts: list[tuple[str, str]] = field(default_factory=list)
    """這個分鏡上畫面的字（在哪裡, 字）：標題卡、索引卡、圖說、原文剪報（建置時 resolve_vox 填，數字對帳用）。"""


@dataclass(frozen=True)
class Shot:
    number: int
    """第幾個分鏡（1 起算），審片頁上的編號。"""
    id: str
    start: Anchor | None
    continues: bool = False
    """接續上一個分鏡：沿用上一個的圖（第一幀就畫好），只換這一鏡自己的 cue；同一串共用一段連續慢推。
    Vox 分鏡接續上一個 Vox 分鏡＝同一張桌面不切，併成一個片段。"""
    chart: ChartConfig | None = None
    vox: VoxConfig | None = None
    """Vox 分鏡：文章原圖、原文剪報、文字卡、框（見 vox.py）。"""
    note: str = ""
    derived: tuple = ()
    """推算宣告（numbers.Derivation）：畫面上哪個數字是用什麼算式算出來的（數字對帳用，見 numbers.py）。"""

    @property
    def label(self) -> str:
        return f"分鏡 {self.id}"


@dataclass(frozen=True)
class Project:
    paths: ProjectPaths
    segments: dict[str, str]
    shots: list[Shot]
    settings: Settings = field(default_factory=Settings)

    @property
    def root(self) -> Path:
        return self.paths.root


# ---------------------------------------------------------------- 載入與驗證


class _Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, msg: str) -> None:
        self.items.append(msg)

    def raise_if_any(self) -> None:
        if self.items:
            raise ProjectError(self.items)


def _read_json(path: Path, what: str, *, required: bool = True) -> Any:
    if not path.is_file():
        if not required:
            return None
        raise ProjectError(f"找不到{what} {path.name}（應該在 {path.parent}）")
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ProjectError(f"{path.name} 第 {e.lineno} 行第 {e.colno} 個字附近不是合法的 JSON（{e.msg}）") from None


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def load_project(root: str | Path, *, overrides: Mapping[str, Any] | None = None) -> Project:
    """讀專案資料夾、驗證全部設定。有問題就丟 ProjectError（繁中，一次列出全部）。

    overrides：蓋過 settings.json 的設定（CLI 參數用，例 {"tts": "fake"}）。
    """
    paths = ProjectPaths(Path(root).absolute())
    if not paths.root.is_dir():
        raise ProjectError(f"找不到專案資料夾 {paths.root}")
    problems = _Problems()

    segments = _load_segments(_read_json(paths.narration, "旁白稿"), problems)
    settings = _load_settings(_read_json(paths.settings, "設定快照", required=False), overrides or {}, problems)
    shots = _load_shots(_read_json(paths.shots, "分鏡設定"), segments, problems)
    problems.raise_if_any()
    _check_continuations(shots, problems)
    _check_vox_boxes(shots, problems)
    problems.raise_if_any()
    return Project(paths=paths, segments=segments, shots=shots, settings=settings)


def load_narration(root: str | Path, *, overrides: Mapping[str, Any] | None = None) -> Project:
    """只讀旁白稿與設定快照（配音在寫分鏡之前，那時還沒有 shots.json）：shots 是空的。錯誤同 load_project。"""
    paths = ProjectPaths(Path(root).absolute())
    if not paths.root.is_dir():
        raise ProjectError(f"找不到專案資料夾 {paths.root}")
    problems = _Problems()
    segments = _load_segments(_read_json(paths.narration, "旁白稿"), problems)
    settings = _load_settings(_read_json(paths.settings, "設定快照", required=False), overrides or {}, problems)
    problems.raise_if_any()
    return Project(paths=paths, segments=segments, shots=[], settings=settings)


def _load_segments(raw: Any, problems: _Problems) -> dict[str, str]:
    if not isinstance(raw, dict) or not isinstance(raw.get("segments"), dict):
        problems.add('narration.json 要長這樣：{"segments": {"s01": "第一段旁白", "s02": "…"}}')
        return {}
    segs: dict[str, str] = {}
    for key, text in raw["segments"].items():
        if not _KEY_RE.match(key):
            problems.add(f"旁白段「{key}」的名字只能用英數字、底線、連字號（例 s01）")
        if not isinstance(text, str) or not text.strip():
            problems.add(f"旁白段 {key} 的文字是空的")
            continue
        segs[key] = text
    if not raw["segments"]:
        problems.add("narration.json 裡一段旁白都沒有")
    return segs


def _load_settings(raw: Any, overrides: Mapping[str, Any], problems: _Problems) -> Settings:
    from article_to_video.narration import TTS_ENGINES  # 避免循環匯入

    merged: dict[str, Any] = {}
    if raw is not None:
        if not isinstance(raw, dict):
            problems.add("settings.json 要是一個物件，例 {\"voice\": \"zh-TW-HsiaoChenNeural\"}")
            raw = {}
        for k in raw:
            if k not in SETTINGS_KEYS:
                problems.add(f"settings.json 有不認得的欄位「{k}」（認得的：{'、'.join(sorted(SETTINGS_KEYS))}）")
        merged.update({k: v for k, v in raw.items() if k in SETTINGS_KEYS})
    merged.update({k: v for k, v in overrides.items() if v is not None})
    s = replace(Settings(), **merged)

    if s.voice not in ZH_TW_VOICES:
        names = "、".join(f"{v}（{n}）" for v, n in ZH_TW_VOICES.items())
        problems.add(f"聲音「{s.voice}」不在支援清單裡，請從這三個挑：{names}")
    if not isinstance(s.rate, str) or not _RATE_RE.match(s.rate):
        problems.add(f"語速「{s.rate}」格式不對，要寫成像 \"+5%\"、\"-10%\" 這樣")
    if s.tts not in TTS_ENGINES:
        problems.add(f"配音層「{s.tts}」不存在（可用：{'、'.join(TTS_ENGINES)}）")
    for name, lo, hi in (("gap", 0.0, 5.0), ("lead", 0.0, 1.0), ("tail", 1.0, 30.0)):
        v = getattr(s, name)
        if not _is_number(v) or not lo <= v <= hi:
            problems.add(f"settings.json 的 {name} 要是 {lo:g}～{hi:g} 之間的秒數（現在是 {v!r}）")
    from article_to_video.user_settings import SettingsError, normalize_value  # 避免循環匯入

    for name in ("channel", "brand_color", "music", "aspect"):
        v = getattr(s, name)
        try:
            if normalize_value(name, v) != v:
                problems.add(f"settings.json 的 {name} 要寫成 {normalize_value(name, v)!r}（現在是 {v!r}）")
        except SettingsError as e:
            problems.add(f"settings.json 的 {name}：{e}")
    if s.logo is not None and not isinstance(s.logo, str):
        problems.add("settings.json 的 logo 要是專案資料夾裡的圖檔名（例 \"logo.png\"），或 null")
    return s


def _load_anchor(raw: Any, where: str, segments: Mapping[str, str], problems: _Problems) -> Anchor | None:
    if not isinstance(raw, dict):
        problems.add(f"{where} 要是錨點物件，例 {{\"segment\": \"s01\", \"word\": \"降到\"}}")
        return None
    bad = [k for k in raw if k not in _ANCHOR_KEYS]
    if bad:
        problems.add(f"{where} 有不認得的欄位「{'、'.join(bad)}」（錨點只認 segment、word、edge、offset、occurrence）")
    seg = raw.get("segment")
    if not isinstance(seg, str) or seg not in segments:
        have = "、".join(segments) or "（沒有）"
        problems.add(f"{where}：沒有旁白段「{seg}」（現有：{have}）")
        return None
    edge = _EDGES.get(raw.get("edge", "start"))
    if edge is None:
        problems.add(f"{where} 的 edge 只能是 \"start\"（開頭）或 \"end\"（唸完），現在是 {raw.get('edge')!r}")
        return None
    offset = raw.get("offset", 0.0)
    if not _is_number(offset):
        problems.add(f"{where} 的 offset 要是秒數，現在是 {offset!r}")
        return None
    word = raw.get("word")
    if word is None:
        if "occurrence" in raw:
            problems.add(f"{where} 有 occurrence 但沒有 word")
        return Anchor(segment=seg, edge=edge, offset=float(offset))
    if not isinstance(word, str) or not word:
        problems.add(f"{where} 的 word 要是旁白裡的一段字")
        return None
    text = segments[seg]
    hits = [m.start() for m in re.finditer(re.escape(word), text)]
    if not hits:
        problems.add(f"{where}：旁白段 {seg} 裡找不到「{word}」（這段是：{text}）")
        return None
    occ = raw.get("occurrence")
    if occ is None:
        if len(hits) > 1:
            problems.add(
                f"{where}：「{word}」在旁白段 {seg} 出現 {len(hits)} 次，不知道是哪一次。"
                f"請加 \"occurrence\": 1～{len(hits)} 指定第幾次，或把 word 寫長一點讓它只出現一次"
            )
            return None
        occ = 1
    if not isinstance(occ, int) or isinstance(occ, bool) or not 1 <= occ <= len(hits):
        problems.add(f"{where} 的 occurrence 要是 1～{len(hits)}（「{word}」在 {seg} 出現 {len(hits)} 次），現在是 {occ!r}")
        return None
    first = hits[occ - 1]
    return Anchor(segment=seg, word=word, edge=edge, offset=float(offset), span=(first, first + len(word) - 1))


def _load_shots(raw: Any, segments: Mapping[str, str], problems: _Problems) -> list[Shot]:
    if not isinstance(raw, dict) or not isinstance(raw.get("shots"), list):
        problems.add('shots.json 要長這樣：{"shots": [ {"id": "SH01", "chart": {…}}, … ]}')
        return []
    if not raw["shots"]:
        problems.add("shots.json 裡一個分鏡都沒有")
    shots: list[Shot] = []
    seen: set[str] = set()
    for n, item in enumerate(raw["shots"], 1):
        if not isinstance(item, dict):
            problems.add(f"第 {n} 個分鏡要是一個物件")
            continue
        sid = item.get("id")
        if not isinstance(sid, str) or not _KEY_RE.match(sid):
            problems.add(f"第 {n} 個分鏡的 id 只能用英數字、底線、連字號，1～40 個字（例 SH01），現在是 {sid!r}")
            continue
        if sid in seen:
            problems.add(f"分鏡 id「{sid}」重複了（第 {n} 個），每個分鏡要有自己的 id")
            continue
        seen.add(sid)
        label = f"分鏡 {sid}"
        bad = [k for k in item if k not in _SHOT_KEYS]
        if bad:
            problems.add(f"{label} 有不認得的欄位「{'、'.join(bad)}」（分鏡只認 {'、'.join(sorted(_SHOT_KEYS))}）")

        start = None
        if "start" in item:
            start = _load_anchor(item["start"], f"{label} 的 start", segments, problems)
        elif n > 1:
            problems.add(f"{label} 少了 start（切入點）：寫 {{\"segment\": \"s03\"}} 從段落開頭切，或加 word 從某個詞切")

        continues = item.get("continues", False)
        if not isinstance(continues, bool):
            problems.add(f"{label} 的 continues 只能是 true 或 false")
            continues = False
        if continues and n == 1:
            problems.add(f"{label} 是第一個分鏡，不能接續上一個（continues 拿掉）")
            continues = False

        has_chart, has_vox = item.get("chart") is not None, item.get("vox") is not None
        if has_chart == has_vox:
            problems.add(f"{label} 要有 chart（財經圖表）或 vox（Vox 剪報）其中一個，而且只能一個")
        chart = (
            _load_chart(item["chart"], label, segments, problems, continues=continues) if has_chart else None
        )
        vox = _load_vox(item["vox"], label, segments, problems, continues=continues) if has_vox and not has_chart else None
        note = item.get("note", "")
        if not isinstance(note, str):
            problems.add(f"{label} 的 note 要是文字")
            note = ""
        derived: tuple = ()
        if "derived" in item:
            from article_to_video.numbers import parse_derived  # 避免循環匯入

            derived, found = parse_derived(item["derived"], label)
            for msg in found:
                problems.add(msg)
        shots.append(Shot(number=n, id=sid, start=start, continues=continues, chart=chart, vox=vox, note=note,
                          derived=derived))
    return shots


def _load_chart(raw: Any, label: str, segments: Mapping[str, str], problems: _Problems, *, continues: bool) -> ChartConfig | None:
    if not isinstance(raw, dict):
        problems.add(f"{label} 的 chart 要是物件：{{\"data\": {{…}}, \"cues\": […]}}")
        return None
    bad = [k for k in raw if k not in ("data", "cues")]
    if bad:
        problems.add(f"{label} 的 chart 有不認得的欄位「{'、'.join(bad)}」（只認 data、cues）")
    data = raw.get("data", {} if continues else None)
    if not isinstance(data, dict):
        problems.add(f"{label} 少了 chart.data（標題、單位、來源、圖型、數列…）")
        data = {}
    if continues:
        extra = [k for k in data if k != "highlight"]
        if extra:
            problems.add(
                f"{label} 是接續分鏡，chart.data 只能改 highlight；「{'、'.join(extra)}」要沿用上一個分鏡"
                "（改了標題、數列這些，切點前後的圖就對不上了）"
            )
    else:
        _check_chart_data(data, label, problems)
    if "highlight" in data:
        _check_highlight(data["highlight"], f"{label} 的 chart.data.highlight", problems)
    public = None
    if "publicData" in data and not continues:
        from article_to_video.public_data import parse_request  # 避免循環匯入

        public, found = parse_request(data["publicData"], f"{label} 的 chart.data.publicData")
        for p in found:
            problems.add(p)
        data = {k: v for k, v in data.items() if k != "publicData"}

    cues_raw = raw.get("cues")
    if not isinstance(cues_raw, list):
        problems.add(f"{label} 少了 chart.cues（cue 清單，例 [{{\"at\": 0, \"kind\": \"title\"}}]）")
        cues_raw = []
    cues: list[Cue] = []
    for i, q in enumerate(cues_raw):
        where = f"{label} 的 chart.cues[{i}]"
        if not isinstance(q, dict):
            problems.add(f"{where} 要是物件，例 {{\"at\": 0, \"kind\": \"title\"}}")
            continue
        kind = q.get("kind")
        if kind not in CHART_CUE_KINDS:
            problems.add(f"{where} 的 kind「{kind}」不認得（財經圖表只認 {'、'.join(sorted(CHART_CUE_KINDS))}）")
            continue
        if "at" not in q:
            problems.add(f"{where} 少了 at（片段內秒數，或錨點物件）")
            continue
        at_raw = q["at"]
        if _is_number(at_raw):
            at: float | Anchor = float(at_raw)
        else:
            anchor = _load_anchor(at_raw, f"{where}.at", segments, problems)
            if anchor is None:
                continue
            at = anchor
        if "dur" in q and (not _is_number(q["dur"]) or q["dur"] <= 0):
            problems.add(f"{where} 的 dur 要是正的秒數")
        if kind == "number":
            d = q.get("data")
            if not isinstance(d, dict) or not _is_number(d.get("value")):
                problems.add(f"{where} 是大數字（number），要有 data.value")
        if kind == "bar" and isinstance(q.get("data"), dict) and "index" in q["data"]:
            idx = q["data"]["index"]
            idxs = idx if isinstance(idx, list) else [idx]
            if not idxs or not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in idxs):
                problems.add(f"{where} 的 data.index 要是第幾根柱（0 起算），或這種數字的清單")
        fields = {k: v for k, v in q.items() if k != "at"}
        cues.append(Cue(kind=kind, at=at, fields=fields, where=where))
    return ChartConfig(data=data, cues=cues, public=public)


_VOX_FIELDS = {
    "image": ({"image"}, {"caption"}),
    "excerpt": (set(), {"paragraph", "quote", "occurrence", "caption"}),
    "box": (set(), {"rect", "find", "occurrence", "zoom"}),
    "title": ({"text"}, set()),
    "point": ({"text"}, set()),
}
"""每種 Vox cue 的（必填欄位, 選填欄位）；at、kind 另外處理。"""
MAX_BOX_ZOOM = 6
"""框的 zoom（推近倍數，1＝整張圖）最多幾倍：再近原圖就糊了。"""
_FIGURE_RE = re.compile(r"^圖([1-9]\d*)$")


def _load_vox(raw: Any, label: str, segments: Mapping[str, str], problems: _Problems, *, continues: bool) -> VoxConfig | None:
    if not isinstance(raw, dict):
        problems.add(f"{label} 的 vox 要是物件：{{\"cues\": [{{\"at\": 0, \"kind\": \"image\", \"image\": \"圖1\"}}, …]}}")
        return None
    bad = [k for k in raw if k != "cues"]
    if bad:
        problems.add(f"{label} 的 vox 有不認得的欄位「{'、'.join(bad)}」（只認 cues）")
    cues_raw = raw.get("cues")
    if not isinstance(cues_raw, list):
        problems.add(f"{label} 少了 vox.cues（素材清單，照出場順序，例 [{{\"at\": 0, \"kind\": \"image\", \"image\": \"圖1\"}}]）")
        return None
    if not cues_raw and not continues:
        problems.add(f"{label} 的 vox.cues 是空的：Vox 分鏡至少要貼一件素材（原圖、剪報或文字卡）")
    cues: list[Cue] = []
    for i, q in enumerate(cues_raw):
        where = f"{label} 的 vox.cues[{i}]"
        if not isinstance(q, dict):
            problems.add(f"{where} 要是物件，例 {{\"at\": 0, \"kind\": \"image\", \"image\": \"圖1\"}}")
            continue
        kind = q.get("kind")
        if kind not in VOX_CUE_KINDS:
            hint = "（Vox 的框寫 \"kind\": \"box\"）" if kind == "highlight" else ""
            problems.add(f"{where} 的 kind「{kind}」不認得（Vox 分鏡只認 {'、'.join(sorted(VOX_CUE_KINDS))}）{hint}")
            continue
        if "at" not in q:
            problems.add(f"{where} 少了 at（從這個分鏡開始算的秒數，或錨點物件）")
            continue
        at_raw = q["at"]
        if _is_number(at_raw):
            if at_raw < 0:
                problems.add(f"{where} 的 at 不能是負的（Vox 分鏡要接續就寫 \"continues\": true，不用負時間）")
                continue
            at: float | Anchor = float(at_raw)
        else:
            anchor = _load_anchor(at_raw, f"{where}.at", segments, problems)
            if anchor is None:
                continue
            at = anchor
        required, optional = _VOX_FIELDS[kind]
        fields = {k: v for k, v in q.items() if k not in ("at", "kind")}
        extra = [k for k in fields if k not in required | optional]
        if extra:
            allowed = "、".join(sorted(required | optional))
            problems.add(f"{where}（{kind}）有不認得的欄位「{'、'.join(extra)}」（認得的：{allowed}）")
        missing = [k for k in sorted(required) if k not in fields]
        if missing:
            problems.add(f"{where}（{kind}）少了 {'、'.join(missing)}")
            continue
        if kind == "image" and not (isinstance(fields["image"], str) and _FIGURE_RE.match(fields["image"])):
            problems.add(f"{where} 的 image 要寫文章原圖的編號，例 \"圖1\"（看 article/article.json 的 images），現在是 {fields['image']!r}")
            continue
        if kind in ("title", "point") and not (isinstance(fields["text"], str) and fields["text"].strip()):
            problems.add(f"{where}（{'標題卡' if kind == 'title' else '索引卡'}）的 text 要是卡上的字")
            continue
        if kind == "excerpt" and not _check_excerpt(fields, where, problems):
            continue
        if kind == "box" and not _check_box(fields, at, where, problems):
            continue
        if "caption" in fields and not (isinstance(fields["caption"], str) and fields["caption"].strip()):
            problems.add(f"{where} 的 caption 要是文字（接在標籤後面，例「圖1｜十年期殖利率」）")
            continue
        cues.append(Cue(kind=kind, at=at, fields=fields, where=where))
    return VoxConfig(cues=cues)


def _positive_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def _check_excerpt(fields: dict, where: str, problems: _Problems) -> bool:
    if "paragraph" not in fields and "quote" not in fields:
        problems.add(f"{where}（原文剪報）要寫 quote（要剪的那句裡的幾個字），或 paragraph（第幾段，整段剪）")
        return False
    ok = True
    if "paragraph" in fields and not _positive_int(fields["paragraph"]):
        problems.add(f"{where} 的 paragraph 要是第幾段（1 起算，看 article/article.json 的 paragraphs），現在是 {fields['paragraph']!r}")
        ok = False
    if "quote" in fields and not (isinstance(fields["quote"], str) and fields["quote"].strip()):
        problems.add(f"{where} 的 quote 要是原文裡的一段字（會剪到它所在的整句）")
        ok = False
    if "occurrence" in fields and not ("quote" in fields and _positive_int(fields["occurrence"])):
        problems.add(f"{where} 的 occurrence 要配 quote，寫第幾次（1 起算）")
        ok = False
    return ok


def _check_box(fields: dict, at: float | Anchor, where: str, problems: _Problems) -> bool:
    ok = True
    if not isinstance(at, Anchor) or at.word is None:
        problems.add(f"{where}（框）的 at 要是錨點，錨在旁白唸到那個數字的詞上，例 {{\"segment\": \"s03\", \"word\": \"三點九六\"}}")
        ok = False
    if ("rect" in fields) == ("find" in fields):
        problems.add(f"{where}（框）要寫 rect（框原圖：[x, y, 寬, 高]，原圖的像素）或 find（框原文剪報裡的幾個字），只能一個")
        return False
    if "zoom" in fields and not (_is_number(fields["zoom"]) and 1 <= fields["zoom"] <= MAX_BOX_ZOOM):
        problems.add(f"{where} 的 zoom 是推近倍數：1＝整張圖都留在畫面裡（不推近），2＝推到大約半張圖，"
                     f"最多 {MAX_BOX_ZOOM:g}；現在是 {fields['zoom']!r}（不寫＝自動：正在唸的那一行、欄名留在畫面裡）")
        ok = False
    if "rect" in fields:
        r = fields["rect"]
        if not (isinstance(r, list) and len(r) == 4 and all(_is_number(v) for v in r) and r[0] >= 0 and r[1] >= 0
                and r[2] > 0 and r[3] > 0):
            problems.add(f"{where} 的 rect 要寫 [x, y, 寬, 高]（原圖的像素，左上角是 0, 0），現在是 {r!r}")
            ok = False
        if "occurrence" in fields:
            problems.add(f"{where} 的 occurrence 只配 find 用")
            ok = False
    else:
        if not (isinstance(fields["find"], str) and fields["find"].strip()):
            problems.add(f"{where} 的 find 要是剪報裡要框的那幾個字（例 \"0.62\"）")
            ok = False
        if "occurrence" in fields and not _positive_int(fields["occurrence"]):
            problems.add(f"{where} 的 occurrence 要是第幾次（1 起算）")
            ok = False
    return ok


def _check_chart_data(data: dict, label: str, problems: _Problems) -> None:
    where = f"{label} 的 chart.data"
    bad = [k for k in data if k not in CHART_DATA_KEYS]
    if bad:
        problems.add(f"{where} 有不認得的欄位「{'、'.join(bad)}」（認得的：{'、'.join(sorted(CHART_DATA_KEYS))}）")
    if not isinstance(data.get("title"), str) or not data.get("title"):
        problems.add(f"{where} 少了 title（標題，一句結論）")
    kind = data.get("chart", "bar")
    if kind not in ("bar", "line", "candle"):
        problems.add(f"{where} 的 chart 只能是 bar、line、candle，現在是 {kind!r}")
    if "push" in data and (not _is_number(data["push"]) or not 0 <= data["push"] <= 0.2):
        problems.add(f"{where} 的 push 要是 0～0.2（整串分鏡總共推近多少，例 0.03）")
    if "publicData" in data:  # 數列由建置抓（public_data.py）；xTicks 等抓到之後才查
        if "series" in data:
            problems.add(f"{where} 的 series 和 publicData 只能二擇一：用公有數據就拿掉 series（數列由建置抓）")
        if "source" in data:
            problems.add(f"{where} 用了 publicData，來源行會自動寫（系列、來源、擷取日期），請拿掉 source")
        if kind == "candle":
            problems.add(f"{where} 的公有數據只有每天一個值，畫不了 K 線（candle），請改用 line 或 bar")
        return
    series = data.get("series")
    if not isinstance(series, list) or not series:
        problems.add(f"{where} 少了 series（數列，例 [{{\"label\": \"2023\", \"value\": 4.58}}]）")
        return
    for i, r in enumerate(series):
        if not isinstance(r, dict):
            problems.add(f"{where}.series[{i}] 要是物件")
            continue
        keys = ("o", "h", "l", "c") if kind == "candle" else ("value",)
        for k in keys:
            if not _is_number(r.get(k)):
                problems.add(f"{where}.series[{i}] 的 {k} 要是數字，現在是 {r.get(k)!r}")
        if "label" not in r:
            problems.add(f"{where}.series[{i}] 少了 label")
    xt = data.get("xTicks")
    if xt is not None and not (isinstance(xt, list) and all(isinstance(x, int) and 0 <= x < len(series) for x in xt)):
        problems.add(f"{where} 的 xTicks 要是索引清單（0～{len(series) - 1}）")


def _check_highlight(h: Any, where: str, problems: _Problems) -> None:
    if not isinstance(h, dict):
        problems.add(f"{where} 要是物件：{{\"index\": 1, \"text\": \"…\", \"sub\": \"…\"}}")
        return
    if "index" in h and (not isinstance(h["index"], int) or isinstance(h["index"], bool) or h["index"] < 0):
        problems.add(f"{where} 的 index 要是第幾個資料點（0 起算）")
    for k in ("text", "sub"):
        if k in h and not isinstance(h[k], str):
            problems.add(f"{where} 的 {k} 要是文字")


def _highlight_lines(data: Mapping, cues: list[Cue]) -> int:
    """引擎為標註留幾行的空間：只看這個分鏡自己的 highlight cue（接續分鏡不沿用上一個的標註 cue）；
    沒有 highlight cue＝0 行，就算 data.highlight 寫了字也一樣。有 cue 時 text、sub 各算一行。"""
    q = next((c for c in cues if c.kind == "highlight"), None)
    if q is None:
        return 0
    h = data.get("highlight") or {}
    text = q.fields.get("text") if q.fields.get("text") is not None else h.get("text")
    sub = q.fields.get("sub") if q.fields.get("sub") is not None else h.get("sub")
    return sum(1 for s in (text, sub) if s)


_HIGHLIGHT_CUE = '{"at": 錨點, "kind": "highlight"}'


def _highlight_mismatch(shot: Shot, prev: Shot, lp: int, lc: int, cue_p: bool, cue_c: bool) -> str:
    """接續分鏡前後標註留的空間不一樣：照真正的原因說（少了標註 cue，還是 text／sub 行數不同）。
    lp、lc：兩個分鏡標註的行數（_highlight_lines）；cue_p、cue_c：有沒有自己的 highlight cue。"""
    head = (f"{shot.label} 接續 {prev.id}，但兩個分鏡為標註留的空間不一樣，切點前後刻度會跳。"
            "引擎只替有自己標註 cue 的分鏡留標註的空間（data.highlight 寫了字但沒有 cue 不算）")
    if not cue_p and cue_c:
        return (f"{head}：{prev.id} 沒有標註 cue，{shot.id} 有。修法：{prev.id} 也加一個 {_HIGHLIGHT_CUE}"
                f"（標註的字寫在 data.highlight 或 cue 的 text），或 {shot.id} 拿掉 continues、自己畫一張圖")
    if cue_p and not cue_c:
        return (f"{head}：{shot.id} 沒有自己的標註 cue，上一個分鏡的標註不會沿用過來。"
                f"修法：{shot.id} 加一個 {_HIGHLIGHT_CUE}（要換的標註字寫在 data.highlight 或 cue 的 text）")
    return (f"{head}：兩個都有標註 cue，但標註行數不一樣（{prev.id} {lp} 行、{shot.id} {lc} 行）。"
            "text 一行、sub 一行（寫在 data.highlight 或 cue 上都算）：兩個分鏡的 text、sub 要寫得一樣多")


def _check_vox_boxes(shots: list[Shot], problems: _Problems) -> None:
    """框畫在它前面最近的那件圖上（同一張桌面，接續分鏡算同一張）：前面要有圖或剪報；
    rect 框原圖、find 框剪報。"""
    target: str | None = None
    for shot in shots:
        if shot.vox is None:
            target = None
            continue
        if not shot.continues:
            target = None
        for cue in shot.vox.cues:
            if cue.kind in ("image", "excerpt", "title", "point"):
                target = cue.kind
            elif cue.kind == "box":
                if target in ("title", "point"):
                    problems.add(f"{cue.where}（框）要緊跟在它要框的原圖或剪報後面，中間不能插文字卡"
                                 "（鏡頭已經移到文字卡上，框會畫在看不到的圖上）")
                elif target is None:
                    problems.add(f"{cue.where}（框）前面沒有圖或剪報：框要寫在它要框的原圖（image）或原文剪報（excerpt）後面")
                elif "rect" in cue.fields and target != "image":
                    problems.add(f"{cue.where}（框）要框的是原文剪報：剪報用 find 寫要框的字（例 \"find\": \"0.62\"），不用 rect")
                elif "find" in cue.fields and target != "excerpt":
                    problems.add(f"{cue.where}（框）要框的是文章原圖：原圖用 rect 寫像素範圍 [x, y, 寬, 高]，find 只能找剪報裡的字")


def _check_continuations(shots: list[Shot], problems: _Problems) -> None:
    """接續分鏡：上一個要是財經圖表；同一串的標註行數、大數字有無要一樣，不然引擎排出來的刻度和版面會跳。"""
    from article_to_video.timeline import effective_chart  # 避免循環匯入

    for i, shot in enumerate(shots):
        if not shot.continues:
            continue
        prev = shots[i - 1]
        if shot.vox is not None or prev.vox is not None:
            if shot.vox is None or prev.vox is None:
                kind = "Vox 分鏡" if shot.vox is not None else "財經圖表分鏡"
                problems.add(
                    f"{shot.label} 設了 continues，但它是{kind}、上一個分鏡 {prev.id} 不是：Vox 分鏡只能接續 Vox 分鏡"
                    "（同一張桌面不切），財經圖表只能接續財經圖表"
                )
            continue
        if shot.chart is None or prev.chart is None:
            problems.add(f"{shot.label} 設了 continues，但它和上一個分鏡 {prev.id} 都要是財經圖表分鏡才能接續")
            continue
        p_data, p_cues = effective_chart(shots, i - 1)
        data, cues = effective_chart(shots, i)
        lp, lc = _highlight_lines(p_data, p_cues), _highlight_lines(data, cues)
        if lp != lc:
            problems.add(_highlight_mismatch(shot, prev, lp, lc, any(c.kind == "highlight" for c in p_cues),
                                             any(c.kind == "highlight" for c in cues)))
        has_p = any(c.kind == "number" for c in p_cues)
        has_c = any(c.kind == "number" for c in shot.chart.cues)
        if has_c and not has_p:
            problems.add(
                f"{shot.label} 接續 {prev.id}，但只有它有大數字（number），圖區會往下讓、切點會跳。"
                "大數字要從這串的第一個分鏡就開始有"
            )

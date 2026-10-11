"""時間軸與錨點：旁白長度 → 每段起點 → 每個分鏡的起訖幀 → 每個 cue 在片段內的秒數 → 引擎 spec。

規則（全部量化到幀，fps 預設 30）：
- 段落起點：第一段 0；下一段＝上一段起點＋上一段音檔長度＋段間留白（settings.gap）。
- 旁白結束＝最後一段起點＋長度；片尾卡從這裡開始，長 settings.tail 秒。總幀數＝round((旁白結束＋片尾) × fps)。
- 錨點 → 秒數：
    沒寫 word：開頭＝段落起點；唸完＝這段最後一個詞唸完。
    有 word、edge=start：包含 word 第一個字的那個 TTS 詞開始唸的時刻，再提早 settings.lead（預設 0.15 秒，畫面先出來看起來才同步）。
    有 word、edge=end：包含 word 最後一個字的那個 TTS 詞唸完的時刻（不提早）。
  最後加上 offset。
- 分鏡起訖：第一個分鏡從 0 開始；其他從自己的 start 錨點；結束＝下一個分鏡的起點，最後一個分鏡到旁白結束
  （接著是片尾卡，自己一個片段，見 end_card.py）。相鄰分鏡用同一個幀號接，首尾相接沒有縫。
- cue 的 at：數字＝片段內秒數原樣（可為負：接續分鏡用）；錨點＝換成片段內的幀再除以 fps。
  錨點落在分鏡開始前一點點（不超過 lead＋1 幀，通常是段落開頭的詞被提早）就夾到 0；更早或落在分鏡結束之後都報錯。
- 接續分鏡（continues: true）：沿用上一個分鏡的 data（自己只能改 highlight），上一個分鏡的 cue 除了標註（highlight）
  全部改成負時間（第一幀就畫好），再加上自己的 cue。一個不接續的分鏡加上後面接續它的分鏡＝一串，共用一個鏡頭：
  - 設定 camera = "slow"（慢推，等速）：每秒推近 min(0.4%, 6% ÷ 整串秒數)（或整串第一個分鏡 data.push 指定的總量），
    每個片段的 pushFrom 從上一個推到的地方接著推。
  - 設定 camera = "snap"（快推，停住、標註出現時推一下）：鏡頭平常不動；整串每個「新的」標註（highlight cue，不算沿用過來的、
    跟上一個一模一樣的）出現那一刻推近一下（SNAP_DUR 秒，引擎用強 ease-out），推完停住。整串總共推近 SNAP_TOTAL
    （或第一個分鏡 data.push），平均分給每個標註、每下最多 SNAP_STEP。大數字（number）不推。每個片段的 data.camera.snaps
    列出整串到這個分鏡為止的每一下（時刻相對這個片段，之前分鏡的是負的），引擎從頭重算，切點不跳。
    推近的固定點是被標註的那一點（它在畫面上不動，其他東西往外推開），再夾到「內容不出 camera.box」：標題、來源行、
    刻度值不會被切掉、不會推進字幕帶或平台介面區（box 見 VideoFormat.camera_box）；引擎另外照版面算最大放大量。
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from article_to_video.brand import apply_brand
from article_to_video.clips import GRAMMAR_CHART
from article_to_video.end_card import END_CARD_ID, plan_end_card
from article_to_video.narration import SegmentAudio
from article_to_video.project import Anchor, Cue, Project, ProjectError, Shot

__all__ = [
    "FORMATS",
    "FORMAT_16x9",
    "FORMAT_9x16",
    "ClipPlan",
    "CueTiming",
    "Outro",
    "SegmentTiming",
    "ShotTiming",
    "Timeline",
    "VideoFormat",
    "build_timeline",
    "effective_chart",
    "plan_clips",
    "segment_timings",
    "video_format",
]

CONTINUATION_AT = -3.0
"""接續分鏡沿用的 cue 改成這個時間（或更早，見 _carry）：引擎在 t=0 之前就把它們畫完。"""
OPENING_TITLE_AT = -1.0
"""影片第一個分鏡的 title cue（寫在第 0 秒的）改成這個時間：版式、標題、格線在第一幀就到位（引擎的版式 0.6 秒、
格線再 0.6 秒），成片第一幀不是一片空白。後面的分鏡照舊在切進來之後淡入。"""
DEFAULT_AXIS_NOTE = "縱軸未從 0 開始"
NUMBER_HOLD = 1.5
"""數字（number cue：大數字卡、兩數對比的數字、圖上右上角的大數字）落定之後至少要在畫面上停這麼久（秒）。
計時實測：大數字卡的 at 寫在數字唸完、切點緊跟在後，數字落定 0.4 秒就切走，觀眾看到的是滾到一半的數（獨立審片兩項必修）。
跟 Vox 的框推近停住後要停 vox.MIN_HOLD 同一類規則。"""
SNAP_DUR = 0.3
"""快推（camera = snap）每一下推多久（秒）。標註的圓點 0.45 秒彈出、字 0.3 秒後才開始淡入：鏡頭在字出來之前就停穩，
讀字時畫面不動。0.3 秒＝30 fps 的 9 幀；曲線（強 ease-out）在引擎 t3_finance_chart.js 的 SNAP_EASE。"""
SNAP_STEP = 0.04
"""快推每一下最多推近多少（4%）。"""
SNAP_TOTAL = 0.06
"""快推整串總共推近多少（6%，跟慢推整串最多推的一樣；第一個分鏡寫了 data.push 就用它）。引擎另外照版面夾一個上限
（內容框推近後不出 camera.box，左右再留一點邊）：一般約 7～9%；直式又是點很多的折線時，x 軸兩端的名字超出邊距，只剩約 3%。
再多標題、來源行、x 軸名字就會被切掉、貼到畫面邊，或推進字幕帶與平台介面區。"""


@dataclass(frozen=True)
class VideoFormat:
    """畫面比例：尺寸、字幕樣式、畫面上哪些地方不能放字。產物放 build/<name>/，成片檔名見 ProjectPaths.video。

    兩種「讓開的邊」（px）：
    - ui_*：平台介面區。短影音平台（YouTube Shorts、Reels、TikTok）蓋在畫面上的地方：頂端分頁列、底部帳號／說明／
      音樂列與導覽列、右側按讚留言分享按鈕欄。沒有字幕的片段（片尾卡）只讓這些（card_safe）。
    - safe_*：旁白期間的片段（財經圖表、Vox）要讓的邊＝平台介面區＋燒錄字幕帶（＋慢推會往外推的一點），
      圖表的標題、刻度、來源列都排在裡面（clip_safe）。
    字幕：sub_* 是 ASS 樣式（字級、底邊距、左右邊距、深底框內距），底邊距 sub_margin_v 要讓字幕框整個在 ui_bottom 之上。
    """

    name: str = "16x9"
    width: int = 1920
    height: int = 1080
    fps: int = 30
    safe_bottom: int = 140
    """底部讓給燒錄字幕的高度（px）：圖表的來源列會排在它上面。"""
    sub_size: int = 54
    sub_margin_v: int = 46
    sub_margin_lr: int = 60
    sub_pad: int = 12
    safe_top: int = 0
    safe_right: int = 0
    ui_top: int = 0
    ui_bottom: int = 0
    ui_right: int = 0
    label: str = "橫式 16:9"

    @staticmethod
    def _nonzero(**edges: int) -> dict[str, int]:
        # 只寫有讓的邊：16:9 的 spec 跟加這些欄位之前一模一樣（不會白白讓所有片段重渲）
        return {k: v for k, v in edges.items() if v}

    def clip_safe(self) -> dict[str, int]:
        """旁白期間的片段（有字幕）給引擎的 spec.safe。"""
        return self._nonzero(top=self.safe_top, bottom=self.safe_bottom, right=self.safe_right)

    def card_safe(self) -> dict[str, int]:
        """沒有字幕的片段（片尾卡）給引擎的 spec.safe：只讓平台介面區。"""
        return self._nonzero(top=self.ui_top, bottom=self.ui_bottom, right=self.ui_right)

    def camera_box(self) -> list[float]:
        """快推推近時內容可以到的範圍（佔畫面寬高的比例 [左, 上, 右, 下]）：畫面扣掉平台介面區（ui_*）與字幕帶
        （safe_bottom）。用比例：qa 縮小渲的時候不用跟著換算。"""
        w, h = self.width, self.height
        return [0.0, round(self.ui_top / h, 6), round(1 - self.ui_right / w, 6), round(1 - self.safe_bottom / h, 6)]

    def spec_base(self) -> dict[str, Any]:
        """旁白期間的片段 spec 共用的部分（fps、寬高、safe）。財經圖表用它；Vox 分鏡（#8）也要用它。"""
        return {"fps": self.fps, "width": self.width, "height": self.height, "safe": self.clip_safe()}


FORMAT_16x9 = VideoFormat()

FORMAT_9x16 = VideoFormat(
    name="9x16", width=1080, height=1920, label="直式 9:16",
    # 平台介面區：各平台沒有官方像素規格，第三方量測／廣告範本差很多（頂 8–15%、底 20–35%、右 120–200 px），
    # 取中間偏保守：頂 240（12.5%，分頁列／搜尋鈕）、底 540（28%，帳號＋兩行說明＋音樂列＋導覽列）、右 140（按鈕欄）。
    ui_top=240, ui_bottom=540, ui_right=140,
    # 字幕：字級 64（ASS 的字級是行高；這套字型的中文字寬約字級的 0.69 倍＝44 px。手機直拿看約 2.9 mm，
    # 跟 16:9 橫放全螢幕的 54 號字差不多大）。左右各留 160：置中，框的右緣（字＋內距 12）在按鈕欄左邊；
    # 一行最多 17 個中文字，一句 25 字以內（旁白規則）最多折兩行。框的底邊貼著 ui_bottom 上緣：libass 的 MarginV
    # 量到字的底，深底框再往外多 sub_pad，所以 sub_margin_v＝540＋12（實測一行的框 y 1292–1379）。
    sub_size=64, sub_margin_v=540 + 12, sub_margin_lr=160, sub_pad=12,
    # 圖表要讓的邊：頂＝平台頂端；底＝平台底部＋兩行字幕的框（2×64＋2×12）＋24 間隔；右＝按鈕欄＋20（慢推最多
    # 放大 6%，右緣會往外推約 20 px）。
    safe_top=240, safe_bottom=540 + 2 * 64 + 2 * 12 + 24, safe_right=140 + 20,
)

FORMATS = {f.name: f for f in (FORMAT_16x9, FORMAT_9x16)}


def video_format(aspect: str) -> VideoFormat:
    """「16:9」「9:16」「16x9」「9x16」「橫式」「直式」→ VideoFormat。看不懂丟 ProjectError（繁中）。"""
    from article_to_video.user_settings import SettingsError, normalize_value  # 避免循環匯入

    try:
        return FORMATS[normalize_value("aspect", aspect)]
    except SettingsError:
        raise ProjectError(f"畫面比例「{aspect}」看不懂，只能是 16:9（橫式）或 9:16（直式）") from None


@dataclass(frozen=True)
class SegmentTiming:
    key: str
    start: float
    audio: SegmentAudio

    @property
    def duration(self) -> float:
        return self.audio.duration

    @property
    def end(self) -> float:
        return self.start + self.audio.duration


@dataclass(frozen=True)
class CueTiming:
    where: str
    kind: str
    at: float
    """片段內秒數（給引擎的值）。"""
    frame: int
    """在成片裡第一次出現的幀（片段起點幀＋片段內幀）。負時間的 cue 是片段的第一幀。"""
    anchor: Anchor | None
    fields: dict[str, Any] = field(default_factory=dict)
    """at 以外、原樣給引擎的欄位。"""
    carried: bool = False
    """接續分鏡從上一個分鏡沿用過來的 cue。"""


@dataclass(frozen=True)
class ShotTiming:
    shot: Shot
    start_frame: int
    end_frame: int
    cues: list[CueTiming] = field(default_factory=list)

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


@dataclass(frozen=True)
class Outro:
    """片尾時段：旁白唸完之後的片尾卡（kind="card"），自己一個片段（id＝clip）。"""

    start_frame: int
    frames: int
    kind: str = "card"
    clip: str = END_CARD_ID
    """片尾卡的片段 id（end_card.END_CARD_ID）。"""


@dataclass(frozen=True)
class Timeline:
    fps: int
    segments: dict[str, SegmentTiming]
    shots: list[ShotTiming]
    narration_end: float
    outro: Outro
    total_frames: int

    @property
    def duration(self) -> float:
        return self.total_frames / self.fps

    def to_json(self) -> dict[str, Any]:
        def anchor(a: Anchor | None) -> dict | None:
            if a is None:
                return None
            return {"segment": a.segment, "word": a.word, "edge": a.edge, "offset": a.offset}

        return {
            "fps": self.fps,
            "totalFrames": self.total_frames,
            "duration": round(self.duration, 6),
            "narrationEnd": round(self.narration_end, 6),
            "outro": {"kind": self.outro.kind, "clip": self.outro.clip, "startFrame": self.outro.start_frame,
                      "frames": self.outro.frames},
            "segments": {k: {"start": round(s.start, 6), "duration": round(s.duration, 6), "text": s.audio.text}
                         for k, s in self.segments.items()},
            "shots": [
                {"id": st.shot.id, "number": st.shot.number, "startFrame": st.start_frame, "endFrame": st.end_frame,
                 "start": round(st.start_frame / self.fps, 6), "end": round(st.end_frame / self.fps, 6),
                 "continues": st.shot.continues,
                 "cues": [{"where": c.where, "kind": c.kind, "at": c.at, "frame": c.frame,
                           "carried": c.carried, "anchor": anchor(c.anchor)} for c in st.cues]}
                for st in self.shots
            ],
        }


def _frame(t: float, fps: int) -> int:
    """秒 → 幀，四捨五入（.5 一律進位）。"""
    return math.floor(t * fps + 0.5 + 1e-9)


# ---------------------------------------------------------------- 接續分鏡：實際生效的 data 與 cue


def _carry(cue: Cue) -> Cue:
    dur = cue.fields.get("dur")
    at = min(CONTINUATION_AT, -(float(dur) + 1.0)) if isinstance(dur, (int, float)) else CONTINUATION_AT
    return Cue(kind=cue.kind, at=at, fields=dict(cue.fields), where=cue.where + "（沿用）", carried=True)


def _carry_highlight(cue: Cue, data: Mapping[str, Any]) -> Cue:
    """上一個分鏡最後的標註沿用過來：字和 index 照上一個分鏡解好寫死（data.carried），接續分鏡自己的 data.highlight 不會蓋到它。"""
    h = resolve_highlight(cue.fields, data)
    q: dict[str, Any] = {"carried": True}
    if h["index"] is not None:
        q["index"] = h["index"]
    fields: dict[str, Any] = {"kind": "highlight", "data": q}
    fields.update({k: h[k] for k in ("text", "sub") if h[k] is not None})
    return Cue(kind="highlight", at=CONTINUATION_AT, fields=fields, where=cue.where + "（沿用）", carried=True)


DATA_KINDS = ("bar", "line", "candle")
"""畫資料的 cue：引擎照第一個（時間最早的）決定圖型與「沒有自己時刻的柱」什麼時候長。"""


def effective_chart(shots: list[Shot], i: int) -> tuple[dict[str, Any], list[Cue]]:
    """第 i 個分鏡實際生效的圖表 data 與 cue（接續分鏡會沿用上一個）。沿用的 cue 排在前面、時間已改成負數。
    上一個分鏡最後的標註也沿用（第一幀就在、淡掉的柱照樣淡著），到這個分鏡自己的 highlight cue 才換。
    接續分鏡自己的 bar cue（帶 data.index）＝那幾根柱在這一句才長（前面的分鏡不畫它們，見 _bars_later）。"""
    shot = shots[i]
    assert shot.chart is not None
    if not shot.continues or i == 0 or shots[i - 1].chart is None:
        return dict(shot.chart.data), list(shot.chart.cues)
    p_data, p_cues = effective_chart(shots, i - 1)
    own_number = any(c.kind == "number" for c in shot.chart.cues)
    carried = [_carry(c) for c in p_cues if c.kind != "highlight" and not (own_number and c.kind == "number")]
    if any(c.kind == "bar" for c in shot.chart.cues) and not any(c.kind in DATA_KINDS for c in p_cues):
        # 前面的分鏡沒寫 bar cue（柱在版式之後自己長好）：補一個沿用的，不然引擎拿這個分鏡的 bar cue 當主生長，
        # 沒有自己時刻的柱在切點變回 0、重長一次
        carried.append(Cue(kind="bar", at=CONTINUATION_AT, fields={"kind": "bar"},
                           where=f"{shots[i - 1].label} 的柱（沿用）", carried=True))
    p_labels = [c for c in p_cues if c.kind == "highlight"]
    if p_labels:
        carried.append(_carry_highlight(p_labels[-1], p_data))
    return {**p_data, **shot.chart.data}, carried + list(shot.chart.cues)


def bar_indices(cue: Cue | CueTiming) -> list[int]:
    """bar cue 的 data.index（一個或清單）→ 這個 cue 長哪幾根柱；沒寫是空的（＝沒有自己時刻的柱跟著它長）。"""
    q = cue.fields.get("data")
    idx = q.get("index") if isinstance(q, Mapping) else None
    if idx is None:
        return []
    return [x for x in (idx if isinstance(idx, list) else [idx]) if isinstance(x, int) and not isinstance(x, bool)]


def _bars_later(project: Project, members: list[int]) -> dict[int, list[int]]:
    """一串分鏡裡，每個分鏡「後面的分鏡才長」的柱（bar cue 寫在後面那個分鏡、帶 data.index）：這個分鏡不畫它們。
    整串一起算：唸到那一句之前，那根柱在每個分鏡都不在，切點不跳。只列有的分鏡（其他分鏡的 spec 不變、不重渲）。"""
    out: dict[int, list[int]] = {}
    later: set[int] = set()
    for n in reversed(members):
        if later:
            out[n] = sorted(later)
        later |= {i for c in project.shots[n].chart.cues if c.kind == "bar" for i in bar_indices(c)}
    return out


# ---------------------------------------------------------------- 時間軸


def _anchor_time(a: Anchor, seg: SegmentTiming, lead: float) -> float:
    au = seg.audio
    if a.span is None:
        t = 0.0 if a.edge == "start" else au.speech_end
    elif a.edge == "start":
        t = au.char_start(a.span[0]) - lead
    else:
        t = au.char_end(a.span[1])
    return seg.start + t + a.offset


def _describe(a: Anchor) -> str:
    if a.word is None:
        return f"{a.segment} {'開頭' if a.edge == 'start' else '唸完'}"
    return f"{a.segment}「{a.word}」{'開頭' if a.edge == 'start' else '唸完'}"


def segment_timings(project: Project, audio: Mapping[str, SegmentAudio]) -> dict[str, SegmentTiming]:
    """每段旁白在成片裡的起點：第一段 0，下一段＝上一段起點＋長度＋段間留白（settings.gap）。"""
    segs: dict[str, SegmentTiming] = {}
    t = 0.0
    for key in project.segments:
        segs[key] = SegmentTiming(key, t, audio[key])
        t += audio[key].duration + project.settings.gap
    return segs


def build_timeline(project: Project, audio: Mapping[str, SegmentAudio], fmt: VideoFormat = FORMAT_16x9) -> Timeline:
    s, fps = project.settings, fmt.fps
    segs = segment_timings(project, audio)
    last = segs[list(segs)[-1]]
    narration_end = last.end
    total = _frame(narration_end + s.tail, fps)
    outro_start = _frame(narration_end, fps)
    problems: list[str] = []

    starts: list[int] = []
    for n, shot in enumerate(project.shots):
        if n == 0:
            if shot.start is not None:
                g = _anchor_time(shot.start, segs[shot.start.segment], s.lead)
                if _frame(g, fps) > 1:
                    problems.append(
                        f"{shot.label} 是第一個分鏡，要從影片開頭開始（它的 start 在 {g:.2f} 秒）。"
                        "把 start 拿掉，或改成第一段旁白的開頭"
                    )
            starts.append(0)
            continue
        assert shot.start is not None
        starts.append(_frame(_anchor_time(shot.start, segs[shot.start.segment], s.lead), fps))

    shots: list[ShotTiming] = []
    last_vox: CueTiming | None = None
    """同一張 Vox 桌面上一個 cue（檢查照出場順序寫）。"""
    for n, shot in enumerate(project.shots):
        f0 = starts[n]
        f1 = starts[n + 1] if n + 1 < len(starts) else outro_start
        if f1 <= f0:
            if n + 1 < len(starts):
                nxt = project.shots[n + 1]
                problems.append(
                    f"{nxt.label} 的切入點（{f1 / fps:.2f} 秒，{_describe(nxt.start)}）沒有晚於上一個分鏡 {shot.id} 的"
                    f"切入點（{f0 / fps:.2f} 秒），{shot.id} 的長度變成 {(f1 - f0) / fps:.2f} 秒。分鏡要照旁白順序排、切入點要往後"
                )
            else:
                problems.append(
                    f"{shot.label} 的切入點（{f0 / fps:.2f} 秒）沒有早於旁白唸完（{outro_start / fps:.2f} 秒）；"
                    "旁白唸完就接片尾卡，最後一個分鏡要在那之前切入"
                )
            shots.append(ShotTiming(shot, f0, max(f0, f1)))
            continue
        cues: list[CueTiming] = []
        if shot.chart is not None:
            _, eff = effective_chart(project.shots, n)
            for cue in eff:
                ct = _cue_timing(cue, f0, f1, segs, s.lead, fps, problems, shot,
                                 shot_at=lambda f: project.shots[bisect.bisect_right(starts, f) - 1])
                if ct is not None:
                    cues.append(ct)
        elif shot.vox is not None:
            head = vox_head(project.shots, n)
            clip_f0 = starts[head]
            if head == n:
                last_vox = None
            for cue in shot.vox.cues:
                ct = _cue_timing(cue, f0, f1, segs, s.lead, fps, problems, shot, clip_f0=clip_f0)
                if ct is None:
                    continue
                if last_vox is not None and ct.frame < last_vox.frame:
                    problems.append(
                        f"{cue.where} 比寫在它前面的 {last_vox.where}（{last_vox.frame / fps:.2f} 秒）還早出現"
                        f"（{ct.frame / fps:.2f} 秒）：Vox 分鏡的素材要照出場順序寫（框會畫在寫在它前面的那張圖上）"
                    )
                last_vox = ct
                cues.append(ct)
        shots.append(ShotTiming(shot, f0, f1, cues))

    if not problems:
        _check_number_holds(shots, fps, outro_start, problems)
    if problems:
        raise ProjectError(problems, title="時間軸對不起來")
    return Timeline(fps=fps, segments=segs, shots=shots, narration_end=narration_end,
                    outro=Outro(start_frame=outro_start, frames=total - outro_start),
                    total_frames=total)


def _check_number_holds(shots: list[ShotTiming], fps: int, outro_start: int, problems: list[str]) -> None:
    """每個數字（number cue）落定之後在畫面上停滿 NUMBER_HOLD 秒。數字留到它那一串結束（接續分鏡沿用）；
    圖上右上角的大數字被後面接續分鏡自己的大數字換掉時，留到那個分鏡切入。"""
    i = 0
    while i < len(shots):
        if shots[i].shot.chart is None:
            i += 1
            continue
        j = i + 1
        while j < len(shots) and shots[j].shot.continues and shots[j].shot.chart is not None:
            j += 1
        chain, layout = shots[i:j], shots[i].shot.chart.data.get("chart", "bar")
        for k, st in enumerate(chain):
            for c in st.cues:
                if c.kind != "number" or c.carried:
                    continue
                end = chain[-1].end_frame
                if layout not in ("number", "compare"):
                    nxt = next((x for x in chain[k + 1:] if any(q.kind == "number" for q in x.shot.chart.cues)), None)
                    end = nxt.start_frame if nxt is not None else end
                hold = (end - c.frame) / fps
                if hold < NUMBER_HOLD - 1e-9:
                    then = "旁白就唸完了" if end >= outro_start else "就切走"
                    problems.append(
                        f"{c.where}（數字）在 {c.frame / fps:.2f} 秒落定，{end / fps:.2f} 秒{then}，只停 {max(hold, 0):.2f} 秒："
                        f"落定後至少要停 {NUMBER_HOLD:g} 秒，觀眾才看得清楚（不然看到的是滾到一半的數）。"
                        "把 at 改到唸到這個數字的那個詞的開頭（edge 用 start，數字在 at 之前就開始滾，唸到時已經落定），"
                        "或把下一個分鏡的切入點往後移、讓旁白在數字後面多半句")
        i = j


def vox_head(shots: list[Shot], i: int) -> int:
    """第 i 個（Vox）分鏡所在那一串的第一個分鏡：Vox 接續分鏡跟它前面的 Vox 分鏡併成同一個片段。"""
    while i > 0 and shots[i].continues and shots[i - 1].vox is not None:
        i -= 1
    return i


def _later_bar_hint(cue: Cue, owner: Shot) -> str:
    """柱的錨點落在後面的分鏡：告訴做片的人把 bar cue 搬過去（接續分鏡的 bar cue＝那根柱在那一句才長）。"""
    if cue.kind != "bar":
        return ""
    if owner.continues:
        return f"。要這根柱在那一句才長，把這個 bar cue 搬到接續分鏡 {owner.id} 的 chart.cues（前面的分鏡就不會先畫它）"
    return (f"。要這根柱在那一句才長，把 {owner.id} 改成接續分鏡（\"continues\": true），再把這個 bar cue 搬到"
            "它的 chart.cues")


def _cue_timing(cue: Cue, f0: int, f1: int, segs: Mapping[str, SegmentTiming], lead: float, fps: int,
                problems: list[str], shot: Shot, *, clip_f0: int | None = None,
                shot_at: Callable[[int], Shot] | None = None) -> CueTiming | None:
    """cue → 在成片裡的幀與片段內秒數。錨點與數字 at 都要落在這個分鏡自己的時段裡；
    clip_f0：片段的起點幀（Vox 接續分鏡跟前面的分鏡同一個片段，片段內秒數從那裡算）；
    shot_at：成片的幀 → 那時候在畫面上的分鏡（錯誤訊息指路用）。"""
    frames = f1 - f0
    base = f0 if clip_f0 is None else clip_f0
    if not isinstance(cue.at, Anchor):
        if cue.carried:
            return CueTiming(cue.where, cue.kind, cue.at, f0, None, dict(cue.fields), True)
        local = _frame(cue.at, fps)
        if local >= frames:
            problems.append(
                f"{cue.where}.at（{cue.at:g} 秒）超出這個分鏡的長度（{frames / fps:.2f} 秒），不會出現。"
                "改小一點，或改用錨點"
            )
            return None
        return CueTiming(cue.where, cue.kind, round((f0 + local - base) / fps, 6), f0 + max(0, local), None,
                         dict(cue.fields))
    g = _anchor_time(cue.at, segs[cue.at.segment], lead)
    local = _frame(g, fps) - f0
    if local < 0:
        if -local <= _frame(lead, fps) + 1:
            local = 0
        else:
            problems.append(
                f"{cue.where}.at 的錨點（{_describe(cue.at)}，{g:.2f} 秒）在 {shot.id} 開始（{f0 / fps:.2f} 秒）之前。"
                "錨點要落在這個分鏡自己的時段裡：改指這個分鏡唸到的詞，或把切入點往前移"
            )
            return None
    if local >= frames:
        owner = shot_at(f0 + local) if shot_at is not None else None
        problems.append(
            f"{cue.where}.at 的錨點（{_describe(cue.at)}，{g:.2f} 秒）在 {shot.id} 結束（{f1 / fps:.2f} 秒）之後，"
            "會被下一個分鏡切掉、根本不會出現。改指這個分鏡唸到的詞，或把下一個分鏡的切入點往後移"
            + (_later_bar_hint(cue, owner) if owner is not None and owner is not shot else "")
        )
        return None
    return CueTiming(cue.where, cue.kind, round((f0 + local - base) / fps, 6), f0 + local, cue.at, dict(cue.fields))


# ---------------------------------------------------------------- 片段計畫（引擎 spec）


@dataclass(frozen=True)
class ClipPlan:
    """一個片段＝一次引擎渲染。財經圖表一個分鏡一個片段；Vox 接續分鏡之後會併成一個片段（Vox 那張票）。"""

    id: str
    shot_ids: list[str]
    start_frame: int
    end_frame: int
    spec: dict[str, Any]
    images: list[Path] = field(default_factory=list)
    """spec 引用的圖片（算快取雜湊用）。"""
    sources: list[str] = field(default_factory=list)
    """這個片段畫面上用到的資料出處，片尾卡會彙整列出（見 end_card.merge_sources）。"""

    @property
    def grammar(self) -> str:
        return self.spec["grammar"]

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


def _push_chains(timeline: Timeline) -> dict[str, tuple[int, int, str]]:
    """每個圖表分鏡 → (整串起點幀, 整串終點幀, 整串第一個分鏡 id)。"""
    out: dict[str, tuple[int, int, str]] = {}
    chain: list[ShotTiming] = []

    def flush() -> None:
        if chain:
            for st in chain:
                out[st.shot.id] = (chain[0].start_frame, chain[-1].end_frame, chain[0].shot.id)
            chain.clear()

    for st in timeline.shots:
        if st.shot.chart is None:
            flush()
            continue
        if not st.shot.continues:
            flush()
        chain.append(st)
    flush()
    return out


def resolve_highlight(fields: Mapping[str, Any], data: Mapping[str, Any]) -> dict[str, Any]:
    """一個 highlight cue 實際標哪一點、寫什麼（跟引擎 t3_finance_chart 的 hlOf 同一套規則）：cue 自己寫的 text、sub、
    data.index 優先，沒寫（或空字串）用 data.highlight 的；index 都沒寫是 None（引擎當最後一點）。
    建置沿用過來的標註（data.carried）字和 index 已經寫死，不看 data.highlight。"""
    q = fields.get("data") if isinstance(fields.get("data"), Mapping) else {}
    h = data.get("highlight") if isinstance(data.get("highlight"), Mapping) else {}
    fixed = bool(q.get("carried"))

    def pick(key: str) -> Any:
        own = fields.get(key)
        if fixed or own not in (None, ""):
            return own if own not in (None, "") else None
        return h.get(key) if h.get(key) not in (None, "") else None

    index = q.get("index") if q.get("index") is not None else (None if fixed else h.get("index"))
    return {"index": index, "text": pick("text"), "sub": pick("sub")}


def _chain_layout(project: Project, members: list[int]) -> dict[str, Any]:
    """一串接續分鏡的版面要用到的東西（給引擎 data.chainLayout）：整串每個分鏡自己的標註標哪一點、幾行字
    （折線另外帶字：標註擺哪、要不要多留空間看字多寬），整串有沒有大數字、差距（兩數對比）。引擎照整串的需要留白，每個分鏡的
    刻度、圖區都一樣，切點不跳。只帶版面用得到的：柱狀圖改後面分鏡的標註字，前面的分鏡不用重渲。"""
    highlights: list[dict[str, Any]] = []
    number = gap = False
    for n in members:
        shot = project.shots[n]
        data, cues = effective_chart(project.shots, n)
        kind = next((c.kind for c in cues if c.kind in ("bar", "line", "candle")), data.get("chart", "bar"))
        for cue in shot.chart.cues:
            if cue.kind == "highlight":
                h = resolve_highlight(cue.fields, data)
                item: dict[str, Any] = {"lines": sum(1 for k in ("text", "sub") if h[k])}
                if h["index"] is not None:
                    item["index"] = h["index"]
                if kind == "line":
                    item.update({k: h[k] for k in ("text", "sub") if h[k]})
                highlights.append(item)
            number = number or cue.kind == "number"
            gap = gap or cue.kind == "gap"
    out: dict[str, Any] = {"highlights": highlights, "number": number}
    if gap:  # 兩數對比：整串有差距就先留好它的位置（只在有的時候寫：其他圖的 spec 跟原本一樣，不會白白重渲）
        out["gap"] = True
    return out


def _bars_no_roll(project: Project, timeline: Timeline, members: list[int]) -> dict[int, list[int]]:
    """一串柱狀圖裡，柱上數字不從 0 往上滾的柱（給引擎 data.barsNoRoll，只寫在那根柱長出來的分鏡）：自己有時刻的柱
    （帶 index 的 bar cue）跟左邊那根（比它早長）比，變化的方向跟「從 0 滾到自己」的方向相反——比左邊低的正數柱
    （「降到 3.8」）、比左邊高的負數柱（「回升到 −0.2」）。往上滾會跟旁白講的「降」打架，滾動中觀眾看到的又是一串假的數字，
    所以長出來時就寫最後的數字。一起長的柱、跟左邊同方向的照原版往上滾。"""
    data, _ = effective_chart(project.shots, members[0])
    series = data.get("series") if isinstance(data.get("series"), list) else []
    if data.get("chart", "bar") != "bar" or not series:
        return {}
    head = timeline.shots[members[0]]
    moment: dict[int, int] = {}
    owner: dict[int, int] = {}
    main: int | None = None
    first_data: int | None = None
    for n in members:
        for c in sorted(timeline.shots[n].cues, key=lambda c: c.frame):
            if c.carried or c.kind not in DATA_KINDS:
                continue
            first_data = c.frame if first_data is None else first_data
            if c.kind != "bar":
                continue
            idx = bar_indices(c)
            for i in idx:
                moment.setdefault(i, c.frame)
                owner.setdefault(i, n)
            if not idx and main is None:
                main = c.frame
    default = main if main is not None else first_data if first_data is not None else head.start_frame
    out: dict[int, list[int]] = {}
    for i, n in owner.items():
        j = i - 1
        if j < 0 or i >= len(series) or moment.get(j, default) >= moment[i]:
            continue
        v, w = series[i].get("value"), series[j].get("value")
        if isinstance(v, (int, float)) and isinstance(w, (int, float)) and (v - w) * v < 0:
            out.setdefault(n, []).append(i)
    return {n: sorted(bars) for n, bars in out.items()}


def _check_highlight_points(project: Project) -> None:
    """每個標註標的點要在數列裡（公有數據抓到之後才知道有幾個點，所以在這裡查）。引擎遇到不存在的點會略過，不能默默少一個標註。"""
    problems: list[str] = []
    for n, shot in enumerate(project.shots):
        if shot.chart is None:
            continue
        data, _ = effective_chart(project.shots, n)
        series = data.get("series") if isinstance(data.get("series"), list) else []
        for cue in shot.chart.cues:
            if cue.kind != "highlight":
                continue
            i = resolve_highlight(cue.fields, data)["index"]
            if isinstance(i, int) and not 0 <= i < len(series):
                problems.append(f"{shot.label} 的標註（{cue.where}）標第 {i} 個點（0 起算），但數列只有 {len(series)} 個點"
                                f"（0～{len(series) - 1}）：改 data.highlight.index 或 cue 的 data.index")
    if problems:
        raise ProjectError(problems, title="標註標到不存在的點")
    for n, shot in enumerate(project.shots):
        if shot.chart is None:
            continue
        data, _ = effective_chart(project.shots, n)
        series = data.get("series") if isinstance(data.get("series"), list) else []
        if data.get("chart", "bar") != "bar":
            continue
        for cue in shot.chart.cues:
            past = [i for i in bar_indices(cue) if i >= len(series)] if cue.kind == "bar" else []
            if past:
                problems.append(f"{cue.where} 沒有作用：要長第 {'、'.join(map(str, past))} 根柱（0 起算），但這張圖只有 "
                                f"{len(series)} 根（0～{len(series) - 1}）：改 data.index")
    if problems:
        raise ProjectError(problems, title="bar cue 標到不存在的柱")


def _opening_at(cue: CueTiming, st: ShotTiming) -> float:
    """給引擎的 at：影片開頭那個分鏡的 title cue 落在第一幀時，提早到 OPENING_TITLE_AT（第一幀就讀得到）。"""
    if st.start_frame == 0 and cue.kind == "title" and not cue.carried and cue.at <= 0:
        return OPENING_TITLE_AT
    return cue.at


def _snap_events(project: Project, timeline: Timeline, members: list[int]) -> list[tuple[int, float, int]]:
    """一串分鏡裡會快推的標註：(哪個分鏡（timeline.shots 的位置）, 在成片裡的秒數, 標哪一點)。
    只算分鏡自己寫的 highlight cue（沿用過來的不算）；跟上一個標註一模一樣（同一點、同樣的字）的不算——引擎不會重畫它。"""
    fps = timeline.fps
    out: list[tuple[int, float, int]] = []
    last: tuple[int, Any, Any] | None = None
    for n in members:
        st = timeline.shots[n]
        data, _ = effective_chart(project.shots, n)
        points = len(data.get("series") or []) if isinstance(data.get("series"), list) else 0
        for c in st.cues:
            if c.kind != "highlight" or c.carried:
                continue
            h = resolve_highlight(c.fields, data)
            index = h["index"] if isinstance(h["index"], int) else points - 1
            key = (index, h["text"], h["sub"])
            if key != last:
                out.append((n, st.start_frame / fps + c.at, index))
            last = key
    return out


def _snap_camera(st: ShotTiming, events: list[tuple[int, float, int]], upto: int, total: float | None,
                 fmt: VideoFormat) -> dict[str, Any]:
    """快推給引擎的 data.camera：整串到第 upto 個分鏡為止的每一下快推（時刻相對這個片段）。"""
    total = SNAP_TOTAL if total is None else float(total)
    step = min(SNAP_STEP, total / len(events)) if events else 0.0
    t0 = st.start_frame / fmt.fps
    snaps = [{"at": round(t - t0, 6), "zoom": round(1 + (k + 1) * step, 6), "index": index}
             for k, (n, t, index) in enumerate(events) if n <= upto and step > 0]
    return {"dur": SNAP_DUR, "box": fmt.camera_box(), "snaps": snaps}


def plan_clips(project: Project, timeline: Timeline, fmt: VideoFormat = FORMAT_16x9) -> list[ClipPlan]:
    fps = fmt.fps
    chains = _push_chains(timeline)
    head_push = {st.shot.id: st.shot.chart.data.get("push") for st in timeline.shots if st.shot.chart}
    members: dict[str, list[int]] = {}
    for n, st in enumerate(timeline.shots):
        if st.shot.chart is not None:
            members.setdefault(chains[st.shot.id][2], []).append(n)
    layouts = {head: _chain_layout(project, ns) for head, ns in members.items() if len(ns) > 1}
    later = {n: bars for ns in members.values() if len(ns) > 1 for n, bars in _bars_later(project, ns).items()}
    no_roll = {n: bars for ns in members.values() for n, bars in _bars_no_roll(project, timeline, ns).items()}
    _check_highlight_points(project)
    snap = project.settings.camera == "snap"
    events = {head: _snap_events(project, timeline, ns) for head, ns in members.items()} if snap else {}
    plans: list[ClipPlan] = []
    for n, st in enumerate(timeline.shots):
        shot = st.shot
        if shot.vox is not None:
            if vox_head(project.shots, n) == n:
                plans.append(_plan_vox_clip(timeline, n, fmt))
            continue
        data, _ = effective_chart(project.shots, n)
        data = apply_brand(prepare_chart_data(data), project.settings.brand_color)
        g0, g1, head = chains[shot.id]
        if head in layouts:
            data["chainLayout"] = layouts[head]
        if n in later:
            data["barsLater"] = later[n]
        if n in no_roll:
            data["barsNoRoll"] = no_roll[n]
        total_push = head_push.get(head)
        if snap:
            data.pop("push", None)
            data["camera"] = _snap_camera(st, events[head], n, total_push, fmt)
        else:
            chain_secs = (g1 - g0) / fps
            rate = total_push / chain_secs if total_push is not None else min(0.004, 0.06 / chain_secs)
            data["pushFrom"] = round(rate * (st.start_frame - g0) / fps, 6)
            data["push"] = round(rate * st.frames / fps, 6)
        cues = [{**c.fields, "at": _opening_at(c, st)} for c in st.cues]
        spec = {
            "grammar": GRAMMAR_CHART,
            "duration": st.frames / fps,
            **fmt.spec_base(),
            "data": data,
            "cues": cues,
        }
        source = data.get("source")
        plans.append(ClipPlan(id=shot.id, shot_ids=[shot.id], start_frame=st.start_frame, end_frame=st.end_frame, spec=spec,
                              sources=[source] if isinstance(source, str) and source.strip() else []))
    plans.append(plan_end_card(project, timeline, fmt, plans))
    return plans


def _plan_vox_clip(timeline: Timeline, head: int, fmt: VideoFormat) -> ClipPlan:
    """一串 Vox 分鏡（第一個＋後面接續它的）＝一個 y2_vox 片段：同一張桌面，切點不切。"""
    from article_to_video.vox import vox_clip_spec  # 避免循環匯入

    chain = [timeline.shots[head]]
    for st in timeline.shots[head + 1:]:
        if not (st.shot.continues and st.shot.vox is not None):
            break
        chain.append(st)
    f0, f1 = chain[0].start_frame, chain[-1].end_frame
    cues = [{**c.fields, "at": c.at} for st in chain for c in st.cues]
    images = [Path(c["image"]) for c in cues if c.get("image")]
    sources = list(dict.fromkeys(src for st in chain for src in st.shot.vox.sources))
    return ClipPlan(id=chain[0].shot.id, shot_ids=[st.shot.id for st in chain], start_frame=f0, end_frame=f1,
                    spec=vox_clip_spec(cues, (f1 - f0) / fmt.fps, fmt), images=images, sources=sources)


def prepare_chart_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """分鏡設定的 chart.data → 給引擎的 data。公有數據那張票在這裡把數列來源換成實際數字。"""
    out = dict(data)
    out.setdefault("axisNote", DEFAULT_AXIS_NOTE)
    return out

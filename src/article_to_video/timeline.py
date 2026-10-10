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
  全部改成負時間（第一幀就畫好），再加上自己的 cue。一個不接續的分鏡加上後面接續它的分鏡＝一串，共用一段連續慢推：
  每秒推近 min(0.4%, 6% ÷ 整串秒數)（或整串第一個分鏡 data.push 指定的總量），每個片段的 pushFrom 從上一個推到的地方接著推。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
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
DEFAULT_AXIS_NOTE = "縱軸未從 0 開始"


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


def effective_chart(shots: list[Shot], i: int) -> tuple[dict[str, Any], list[Cue]]:
    """第 i 個分鏡實際生效的圖表 data 與 cue（接續分鏡會沿用上一個）。沿用的 cue 排在前面、時間已改成負數。
    上一個分鏡最後的標註也沿用（第一幀就在、淡掉的柱照樣淡著），到這個分鏡自己的 highlight cue 才換。"""
    shot = shots[i]
    assert shot.chart is not None
    if not shot.continues or i == 0 or shots[i - 1].chart is None:
        return dict(shot.chart.data), list(shot.chart.cues)
    p_data, p_cues = effective_chart(shots, i - 1)
    own_number = any(c.kind == "number" for c in shot.chart.cues)
    carried = [_carry(c) for c in p_cues if c.kind != "highlight" and not (own_number and c.kind == "number")]
    p_labels = [c for c in p_cues if c.kind == "highlight"]
    if p_labels:
        carried.append(_carry_highlight(p_labels[-1], p_data))
    return {**p_data, **shot.chart.data}, carried + list(shot.chart.cues)


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
                ct = _cue_timing(cue, f0, f1, segs, s.lead, fps, problems, shot)
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

    if problems:
        raise ProjectError(problems, title="時間軸對不起來")
    return Timeline(fps=fps, segments=segs, shots=shots, narration_end=narration_end,
                    outro=Outro(start_frame=outro_start, frames=total - outro_start),
                    total_frames=total)


def vox_head(shots: list[Shot], i: int) -> int:
    """第 i 個（Vox）分鏡所在那一串的第一個分鏡：Vox 接續分鏡跟它前面的 Vox 分鏡併成同一個片段。"""
    while i > 0 and shots[i].continues and shots[i - 1].vox is not None:
        i -= 1
    return i


def _cue_timing(cue: Cue, f0: int, f1: int, segs: Mapping[str, SegmentTiming], lead: float, fps: int,
                problems: list[str], shot: Shot, *, clip_f0: int | None = None) -> CueTiming | None:
    """cue → 在成片裡的幀與片段內秒數。錨點與數字 at 都要落在這個分鏡自己的時段裡；
    clip_f0：片段的起點幀（Vox 接續分鏡跟前面的分鏡同一個片段，片段內秒數從那裡算）。"""
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
        problems.append(
            f"{cue.where}.at 的錨點（{_describe(cue.at)}，{g:.2f} 秒）在 {shot.id} 結束（{f1 / fps:.2f} 秒）之後，"
            "會被下一個分鏡切掉、根本不會出現。改指這個分鏡唸到的詞，或把下一個分鏡的切入點往後移"
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
    （折線另外帶字：標註擺哪、要不要多留空間看字多寬），整串有沒有大數字。引擎照整串的需要留白，每個分鏡的
    刻度、圖區都一樣，切點不跳。只帶版面用得到的：柱狀圖改後面分鏡的標註字，前面的分鏡不用重渲。"""
    highlights: list[dict[str, Any]] = []
    number = False
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
    return {"highlights": highlights, "number": number}


def plan_clips(project: Project, timeline: Timeline, fmt: VideoFormat = FORMAT_16x9) -> list[ClipPlan]:
    fps = fmt.fps
    chains = _push_chains(timeline)
    head_push = {st.shot.id: st.shot.chart.data.get("push") for st in timeline.shots if st.shot.chart}
    members: dict[str, list[int]] = {}
    for n, st in enumerate(timeline.shots):
        if st.shot.chart is not None:
            members.setdefault(chains[st.shot.id][2], []).append(n)
    layouts = {head: _chain_layout(project, ns) for head, ns in members.items() if len(ns) > 1}
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
        chain_secs = (g1 - g0) / fps
        total_push = head_push.get(head)
        rate = total_push / chain_secs if total_push is not None else min(0.004, 0.06 / chain_secs)
        data["pushFrom"] = round(rate * (st.start_frame - g0) / fps, 6)
        data["push"] = round(rate * st.frames / fps, 6)
        cues = [{**c.fields, "at": c.at} for c in st.cues]
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

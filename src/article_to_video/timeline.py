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
from article_to_video.end_card import END_CARD_ID, plan_end_card
from article_to_video.narration import SegmentAudio
from article_to_video.project import Anchor, Cue, Project, ProjectError, Shot

__all__ = [
    "FORMAT_16x9",
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
]

T3 = "t3_finance_chart"
CONTINUATION_AT = -3.0
"""接續分鏡沿用的 cue 改成這個時間（或更早，見 _carry）：引擎在 t=0 之前就把它們畫完。"""
DEFAULT_AXIS_NOTE = "縱軸未從 0 開始"


@dataclass(frozen=True)
class VideoFormat:
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


FORMAT_16x9 = VideoFormat()


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


def effective_chart(shots: list[Shot], i: int) -> tuple[dict[str, Any], list[Cue]]:
    """第 i 個分鏡實際生效的圖表 data 與 cue（接續分鏡會沿用上一個）。沿用的 cue 排在前面、時間已改成負數。"""
    shot = shots[i]
    assert shot.chart is not None
    if not shot.continues or i == 0 or shots[i - 1].chart is None:
        return dict(shot.chart.data), list(shot.chart.cues)
    p_data, p_cues = effective_chart(shots, i - 1)
    own_number = any(c.kind == "number" for c in shot.chart.cues)
    carried = [_carry(c) for c in p_cues if c.kind != "highlight" and not (own_number and c.kind == "number")]
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


def build_timeline(project: Project, audio: Mapping[str, SegmentAudio], fmt: VideoFormat = FORMAT_16x9) -> Timeline:
    s, fps = project.settings, fmt.fps
    segs: dict[str, SegmentTiming] = {}
    t = 0.0
    for key in project.segments:
        segs[key] = SegmentTiming(key, t, audio[key])
        t += audio[key].duration + s.gap
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
        shots.append(ShotTiming(shot, f0, f1, cues))

    if problems:
        raise ProjectError(problems, title="時間軸對不起來")
    return Timeline(fps=fps, segments=segs, shots=shots, narration_end=narration_end,
                    outro=Outro(start_frame=outro_start, frames=total - outro_start),
                    total_frames=total)


def _cue_timing(cue: Cue, f0: int, f1: int, segs: Mapping[str, SegmentTiming], lead: float, fps: int,
                problems: list[str], shot: Shot) -> CueTiming | None:
    frames = f1 - f0
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
        return CueTiming(cue.where, cue.kind, round(local / fps, 6), f0 + max(0, local), None, dict(cue.fields))
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
    return CueTiming(cue.where, cue.kind, round(local / fps, 6), f0 + local, cue.at, dict(cue.fields))


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


def plan_clips(project: Project, timeline: Timeline, fmt: VideoFormat = FORMAT_16x9) -> list[ClipPlan]:
    fps = fmt.fps
    chains = _push_chains(timeline)
    head_push = {st.shot.id: st.shot.chart.data.get("push") for st in timeline.shots if st.shot.chart}
    plans: list[ClipPlan] = []
    for n, st in enumerate(timeline.shots):
        shot = st.shot
        if shot.chart is None:
            raise ProjectError(f"{shot.label} 是 Vox 分鏡，這版的建置還不支援 Vox，先改成財經圖表分鏡")
        data, _ = effective_chart(project.shots, n)
        data = apply_brand(prepare_chart_data(data), project.settings.brand_color)
        g0, g1, head = chains[shot.id]
        chain_secs = (g1 - g0) / fps
        total_push = head_push.get(head)
        rate = total_push / chain_secs if total_push is not None else min(0.004, 0.06 / chain_secs)
        data["pushFrom"] = round(rate * (st.start_frame - g0) / fps, 6)
        data["push"] = round(rate * st.frames / fps, 6)
        cues = [{**c.fields, "at": c.at} for c in st.cues]
        spec = {
            "grammar": T3,
            "duration": st.frames / fps,
            "fps": fps,
            "width": fmt.width,
            "height": fmt.height,
            "safe": {"bottom": fmt.safe_bottom},
            "data": data,
            "cues": cues,
        }
        source = data.get("source")
        plans.append(ClipPlan(id=shot.id, shot_ids=[shot.id], start_frame=st.start_frame, end_frame=st.end_frame, spec=spec,
                              sources=[source] if isinstance(source, str) and source.strip() else []))
    plans.append(plan_end_card(project, timeline, fmt, plans))
    return plans


def prepare_chart_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """分鏡設定的 chart.data → 給引擎的 data。公有數據那張票在這裡把數列來源換成實際數字。"""
    out = dict(data)
    out.setdefault("axisNote", DEFAULT_AXIS_NOTE)
    return out

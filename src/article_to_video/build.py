"""建置：專案資料夾 → 成片＋字幕檔。分兩段，後面的票各自接在對的地方：

  plan_build()   讀專案＋驗證 → 公有數據（讀專案快照或抓 FRED，換成實際數列與來源行）→ 字幕字型涵蓋檢查 → 配音層 → 時間軸（錨點、分鏡起訖、接續分鏡）→ 每個片段的引擎 spec。
                 不渲任何東西，幾秒內完成。審片頁的靜態圖（render_stills(plan.clips[i].spec, …)）從這裡接。
  build_project() plan_build() → Vox 框核對（沒核對過的框擋正式出片，boxes.py）→ preflight 檢查（數字對帳在這裡擋正式出片）→ 片段（快取、並行）→ 旁白音軌 →
                 配樂墊在旁白下（soundtrack.py）→ 字幕 → 接片＋燒字幕＋混音 → 成片.mp4、字幕.srt。

產物位置見 project.py 開頭。
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from article_to_video.assemble import (
    ASSEMBLY_VERSION, assemble_video, file_digest, final_key, write_narration_track,
)
from article_to_video.boxes import require_boxes_ready
from article_to_video.clip_cache import RenderReport, render_planned_clips
from article_to_video.end_card import missing_channel_error
from article_to_video.ffmpeg import find_ffmpeg
from article_to_video.music import TRACKS, ensure_track
from article_to_video.narration import SegmentAudio, get_tts_engine, narrate, write_timing
from article_to_video.project import Project, ProjectError, VariantPaths, load_narration, load_project
from article_to_video.public_data import PublicChart, resolve_public_data
from article_to_video.subtitles import (
    SUBTITLE_FONT_FAMILY, missing_subtitle_glyphs, subtitle_lines, write_ass, write_srt,
)
from article_to_video.soundtrack import mix_soundtrack
from article_to_video.timeline import (
    FORMATS, ClipPlan, SegmentTiming, Timeline, VideoFormat, build_timeline, plan_clips, segment_timings,
)
from article_to_video.user_settings import snapshot_into_project
from article_to_video.vox import VoxPlan, resolve_vox

__all__ = ["BlockedBuild", "BuildPlan", "BuildResult", "Voicing", "build_project", "format_voicing", "plan_build",
           "voice_narration"]

Log = Callable[[str], None]


@dataclass(frozen=True)
class BuildPlan:
    project: Project
    format: VideoFormat
    audio: dict[str, SegmentAudio]
    timeline: Timeline
    clips: list[ClipPlan]
    public_data: dict[str, PublicChart] = field(default_factory=dict)
    """分鏡 id → 這個圖表畫的公有數據（每個點的系列、日期、擷取日；數字對帳與審片頁標出處用）。"""
    vox: VoxPlan = field(default_factory=VoxPlan)
    """Vox 分鏡解析完的素材（原圖、原文剪報、框）。"""

    @property
    def paths(self) -> VariantPaths:
        return self.project.paths.variant(self.format.name)


class BlockedBuild(ProjectError):
    """渲染前檢查（例如數字對帳）擋下了正式出片。訊息照原本的檢查；plan＝已經算好的建置計畫
    （指令列拿它更新審片頁，讓頁面也標出被擋的原因）。"""

    def __init__(self, cause: ProjectError, plan: BuildPlan) -> None:
        ValueError.__init__(self, str(cause))
        self.problems = list(cause.problems)
        self.plan = plan


@dataclass(frozen=True)
class BuildResult:
    plan: BuildPlan
    video: Path
    srt: Path
    rendered: list[str]
    """這次真的重渲的片段 id。"""
    cached: list[str]
    """沿用快取的片段 id。"""
    assembled: bool
    """這次有沒有重新組裝成片（什麼都沒變就不重做）。"""

    @property
    def duration(self) -> float:
        return self.plan.timeline.duration


def _quiet(_: str) -> None:
    pass


def _snapshot(project_dir: str | os.PathLike, data_dir: str | os.PathLike | None, log: Log) -> None:
    """專案還沒有設定快照（或缺欄位）就從使用者設定補上。"""
    added = snapshot_into_project(project_dir, data_dir)
    if added:
        log(f"設定快照：從使用者設定補上 {'、'.join(added)}（之後改使用者設定不會影響這支片）")


def _voice(project: Project, ffmpeg: Path | None, log: Log) -> dict[str, SegmentAudio]:
    """配音層：字幕字型涵蓋檢查 → 每段配音（有快取）→ timing.json。配音（narrate）和建置計畫共用這一段。"""
    missing = missing_subtitle_glyphs(project.segments)
    if missing:
        raise ProjectError(
            [f"旁白段 {k} 有字幕字型沒有的字：{chars}" for k, chars in missing.items()],
            title="旁白有字幕打不出來的字",
            hint=f"字幕字型是外掛附帶的 {SUBTITLE_FONT_FAMILY}（常用繁體、簡體字都有）。修法：把這些字換成常用字，或改寫那句旁白。",
        )
    s = project.settings
    engine = get_tts_engine(s.tts)
    cached_n = 0

    def seg_done(key: str, cached: bool) -> None:
        nonlocal cached_n
        cached_n += cached

    audio = narrate(project.segments, engine=engine, voice=s.voice, rate=s.rate,
                    cache_dir=project.paths.narration_cache, ffmpeg=ffmpeg, on_segment=seg_done)
    write_timing(audio, project.paths.narration_cache / "timing.json")
    log(f"配音（{engine.name}，{s.voice}，語速 {s.rate}）：{len(audio)} 段，{len(audio) - cached_n} 段新配、{cached_n} 段用快取")
    return audio


@dataclass(frozen=True)
class Voicing:
    """配音的結果（寫分鏡之前）：每段在成片裡的起點、長度、每個詞的時間點。"""

    project: Project
    segments: dict[str, SegmentTiming]

    @property
    def narration_end(self) -> float:
        return list(self.segments.values())[-1].end

    @property
    def duration(self) -> float:
        """成片大概多長：旁白＋片尾（分鏡不影響長度）。"""
        return self.narration_end + self.project.settings.tail


def voice_narration(
    project_dir: str | os.PathLike,
    *,
    tts: str | None = None,
    data_dir: str | os.PathLike | None = None,
    log: Log = _quiet,
) -> Voicing:
    """配音（指令列 narrate）：寫完旁白稿、還沒寫分鏡就配。只要 narration.json 與設定快照（沒有就從使用者設定補）；
    跟預覽、建置同一個快取，之後 preview／build 不會重配。"""
    _snapshot(project_dir, data_dir, log)
    project = load_narration(project_dir, overrides={"tts": tts})
    audio = _voice(project, None, log)
    return Voicing(project, segment_timings(project, audio))


def _clock(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:04.1f}"


def format_voicing(v: Voicing) -> str:
    """narrate 印給 Claude 的：總長、每段的起點與長度、每個詞在成片裡開始唸的秒數（寫錨點、算框和下一件的間隔用）。"""
    s = v.project.settings
    lines = [f"總長 {v.duration:.1f} 秒（{_clock(v.duration)}）：旁白 {v.narration_end:.1f} 秒＋片尾 {s.tail:g} 秒。"
             "不在 2–4 分鐘就先改旁白，再寫分鏡。",
             "每段在成片裡從幾秒起、多長；底下是每個詞開始唸的秒數（成片裡的時間）。"
             f"每個詞的起訖（每段從 0 算）在 {v.project.paths.narration_cache / 'timing.json'}："]
    for key, seg in v.segments.items():
        a = seg.audio
        lines.append(f"{key}（{seg.start:.2f} 秒起，{seg.duration:.2f} 秒）{a.text}")
        lines.append("  " + "｜".join(f"{a.text[w.start_char:w.end_char]} {seg.start + w.start:.2f}" for w in a.words))
    return "\n".join(lines)


def plan_build(
    project_dir: str | os.PathLike,
    *,
    tts: str | None = None,
    fmt: VideoFormat | None = None,
    ffmpeg: Path | None = None,
    data_dir: str | os.PathLike | None = None,
    log: Log = _quiet,
) -> BuildPlan:
    """data_dir：使用者資料位置（見 user_settings.py）。專案還沒有設定快照時，從那裡的使用者設定補進 settings.json。

    fmt：畫面比例（timeline.FORMATS；指令列 --aspect）。不給就用設定快照的 aspect（使用者設定的預設比例），
    都沒有就是 16:9。產物分開放在 build/<比例>/，兩種比例可以並存。
    """
    _snapshot(project_dir, data_dir, log)
    project = load_project(project_dir, overrides={"tts": tts})
    if not project.settings.channel.strip():  # 片尾卡要用；配音、抓公有數據之前就先講
        raise missing_channel_error(project.root, data_dir)
    project, public_data = resolve_public_data(project)
    project, vox = resolve_vox(project)
    s = project.settings
    if fmt is None:
        fmt = FORMATS[s.aspect]

    audio = _voice(project, ffmpeg, log)
    timeline = build_timeline(project, audio, fmt)
    clips = plan_clips(project, timeline, fmt)
    vp = project.paths.variant(fmt.name)
    vp.root.mkdir(parents=True, exist_ok=True)
    vp.timeline.write_text(json.dumps(timeline.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
    log(
        f"時間軸（{fmt.label}）：{len(timeline.shots)} 個分鏡，總長 {timeline.duration:.2f} 秒"
        f"（旁白到 {timeline.narration_end:.2f} 秒，片尾 {timeline.outro.frames / timeline.fps:.2f} 秒）"
    )
    return BuildPlan(project=project, format=fmt, audio=audio, timeline=timeline, clips=clips, public_data=public_data,
                     vox=vox)


def build_project(
    project_dir: str | os.PathLike,
    *,
    tts: str | None = None,
    jobs: int | None = None,
    fmt: VideoFormat | None = None,
    preflight: Sequence[Callable[[BuildPlan], None]] = (),
    data_dir: str | os.PathLike | None = None,
    log: Log = _quiet,
) -> BuildResult:
    """建置成片。preflight：渲染前的檢查（數字對帳 numbers.check_numbers），丟 ProjectError 就不出片——
    改丟 BlockedBuild（帶著算好的 plan，審片頁照樣能用它更新）。"""
    ffmpeg = find_ffmpeg()
    plan = plan_build(project_dir, tts=tts, fmt=fmt, ffmpeg=ffmpeg, data_dir=data_dir, log=log)
    fmt = plan.format
    # 正式出片前的檢查：Vox 框都要 Claude 看過靜態圖、核對過（boxes.py），再加呼叫端給的（數字對帳…）。
    # 全部跑完再一起報，作者／Claude 一次看到所有要修的地方。
    errors: list[ProjectError] = []
    for check in (lambda p: require_boxes_ready(p, jobs=jobs, log=log), *preflight):
        try:
            check(plan)
        except ProjectError as e:
            errors.append(e)
    if errors:
        cause = errors[0] if len(errors) == 1 else ProjectError([str(e) for e in errors], title="正式出片前的檢查沒過")
        raise BlockedBuild(cause, plan) from errors[0]

    vp = plan.paths
    n_jobs = jobs or min(3, max(1, (os.cpu_count() or 2) // 2))

    def clip_done(cid: str, cached: bool) -> None:
        log(f"  {'＝' if cached else '✓'} {cid}{'（快取）' if cached else ''}")

    log(f"片段：{len(plan.clips)} 個（最多同時渲 {n_jobs} 個）")
    report: RenderReport = render_planned_clips(plan.clips, vp, ffmpeg=ffmpeg, jobs=n_jobs, on_clip=clip_done)

    tl = plan.timeline
    write_narration_track(tl.segments.values(), tl.duration, vp.narration_wav)
    audio = _soundtrack(plan, data_dir, log)
    lines = subtitle_lines(tl.segments.values())
    write_srt(lines, vp.srt)
    write_ass(lines, vp.ass, fmt)

    paths = plan.project.paths
    video, srt = paths.video(fmt.name), paths.srt(fmt.name)
    key = final_key({
        "version": ASSEMBLY_VERSION,
        "clips": [report.hashes[c.id] for c in plan.clips],
        "audio": file_digest(audio),
        "ass": vp.ass.read_text(encoding="utf-8"),
        "fps": fmt.fps,
    })
    assembled = False
    if not (video.is_file() and vp.final_stamp.is_file() and vp.final_stamp.read_text(encoding="utf-8") == key):
        log("組裝成片（接片、混音、燒字幕）…")
        vp.final_stamp.unlink(missing_ok=True)
        assemble_video(
            workdir=vp.root, clips=[report.clips[c.id] for c in plan.clips], narration=audio,
            ass=vp.ass, fonts_dir=vp.fonts, out=video, ffmpeg=ffmpeg, fps=fmt.fps,
            log_path=vp.logs / "assemble.log",
        )
        vp.final_stamp.write_text(key, encoding="utf-8")
        assembled = True
    else:
        log("成片沒有變動，沿用上次組裝的結果")
    shutil.copyfile(vp.srt, srt)
    return BuildResult(plan=plan, video=video, srt=srt, rendered=report.rendered, cached=report.cached,
                       assembled=assembled)


def _soundtrack(plan: BuildPlan, data_dir: str | os.PathLike | None, log: Log) -> Path:
    """配樂墊在旁白下面，回傳成片要用的音軌。設定不要配樂（music: "none"）就直接用旁白音軌。"""
    vp, tl, music = plan.paths, plan.timeline, plan.project.settings.music
    if music == "none":
        vp.soundtrack.unlink(missing_ok=True)
        log("配樂：不要配樂")
        return vp.narration_wav
    speech = []
    for seg in tl.segments.values():
        words = seg.audio.words
        speech.append((seg.start + (words[0].start if words else 0.0), seg.start + seg.audio.speech_end))
    rep = mix_soundtrack(vp.narration_wav, ensure_track(music, data_dir), speech=speech,
                         outro_start=tl.outro.start_frame / tl.fps, out=vp.soundtrack)
    log(f"配樂：{TRACKS[music].label}（旁白 {rep.voice_db} dBFS；配樂說話時 {rep.music_under_voice_db}、"
        f"片尾卡 {rep.music_end_card_db} dBFS）")
    return vp.soundtrack

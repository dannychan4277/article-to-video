"""建置：專案資料夾 → 成片＋字幕檔。分兩段，後面的票各自接在對的地方：

  plan_build()   讀專案＋驗證 → 公有數據（讀專案快照或抓 FRED，換成實際數列與來源行）→ 字幕字型涵蓋檢查 → 配音層 → 時間軸（錨點、分鏡起訖、接續分鏡）→ 每個片段的引擎 spec。
                 不渲任何東西，幾秒內完成。審片頁的靜態圖（render_stills(plan.clips[i].spec, …)）從這裡接。
  build_project() plan_build() → preflight 檢查（數字對帳在這裡擋正式出片）→ 片段（快取、並行）→ 旁白音軌 →
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
from article_to_video.clip_cache import RenderReport, render_planned_clips
from article_to_video.ffmpeg import find_ffmpeg
from article_to_video.music import TRACKS, ensure_track
from article_to_video.narration import SegmentAudio, get_tts_engine, narrate, write_timing
from article_to_video.project import Project, ProjectError, VariantPaths, load_project
from article_to_video.public_data import PublicChart, resolve_public_data
from article_to_video.subtitles import (
    SUBTITLE_FONT_FAMILY, missing_subtitle_glyphs, subtitle_lines, write_ass, write_srt,
)
from article_to_video.soundtrack import mix_soundtrack
from article_to_video.timeline import FORMAT_16x9, ClipPlan, Timeline, VideoFormat, build_timeline, plan_clips
from article_to_video.user_settings import snapshot_into_project
from article_to_video.vox import VoxPlan, resolve_vox

__all__ = ["BuildPlan", "BuildResult", "build_project", "plan_build"]

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


def plan_build(
    project_dir: str | os.PathLike,
    *,
    tts: str | None = None,
    fmt: VideoFormat = FORMAT_16x9,
    ffmpeg: Path | None = None,
    data_dir: str | os.PathLike | None = None,
    log: Log = _quiet,
) -> BuildPlan:
    """data_dir：使用者資料位置（見 user_settings.py）。專案還沒有設定快照時，從那裡的使用者設定補進 settings.json。"""
    added = snapshot_into_project(project_dir, data_dir)
    if added:
        log(f"設定快照：從使用者設定補上 {'、'.join(added)}（之後改使用者設定不會影響這支片）")
    project = load_project(project_dir, overrides={"tts": tts})
    project, public_data = resolve_public_data(project)
    project, vox = resolve_vox(project)
    s = project.settings

    missing = missing_subtitle_glyphs(project.segments)
    if missing:
        raise ProjectError(
            [f"旁白段 {k} 有字幕字型沒有的字：{chars}" for k, chars in missing.items()],
            title="旁白有字幕打不出來的字",
            hint=f"字幕字型是外掛附帶的 {SUBTITLE_FONT_FAMILY}（常用繁體、簡體字都有）。修法：把這些字換成常用字，或改寫那句旁白。",
        )

    engine = get_tts_engine(s.tts)
    cached_n = 0

    def seg_done(key: str, cached: bool) -> None:
        nonlocal cached_n
        cached_n += cached

    audio = narrate(project.segments, engine=engine, voice=s.voice, rate=s.rate,
                    cache_dir=project.paths.narration_cache, ffmpeg=ffmpeg, on_segment=seg_done)
    write_timing(audio, project.paths.narration_cache / "timing.json")
    log(f"配音（{engine.name}，{s.voice}，語速 {s.rate}）：{len(audio)} 段，{len(audio) - cached_n} 段新配、{cached_n} 段用快取")

    timeline = build_timeline(project, audio, fmt)
    clips = plan_clips(project, timeline, fmt)
    vp = project.paths.variant(fmt.name)
    vp.root.mkdir(parents=True, exist_ok=True)
    vp.timeline.write_text(json.dumps(timeline.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
    log(
        f"時間軸：{len(timeline.shots)} 個分鏡，總長 {timeline.duration:.2f} 秒"
        f"（旁白到 {timeline.narration_end:.2f} 秒，片尾 {timeline.outro.frames / timeline.fps:.2f} 秒）"
    )
    return BuildPlan(project=project, format=fmt, audio=audio, timeline=timeline, clips=clips, public_data=public_data,
                     vox=vox)


def build_project(
    project_dir: str | os.PathLike,
    *,
    tts: str | None = None,
    jobs: int | None = None,
    fmt: VideoFormat = FORMAT_16x9,
    preflight: Sequence[Callable[[BuildPlan], None]] = (),
    data_dir: str | os.PathLike | None = None,
    log: Log = _quiet,
) -> BuildResult:
    """建置成片。preflight：渲染前的檢查，丟 ProjectError 就不出片（數字對帳那張票接在這裡）。"""
    ffmpeg = find_ffmpeg()
    plan = plan_build(project_dir, tts=tts, fmt=fmt, ffmpeg=ffmpeg, data_dir=data_dir, log=log)
    for check in preflight:
        check(plan)

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

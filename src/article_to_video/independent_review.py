"""獨立審片：build 好審片用的成片之後、作者看審片頁之前（出片之前），Claude Code 另外派一個 agent，只看成片審一輪。Python 不派 agent，這裡只做三件事：

  prepare_review_kit(plan)     從成片準備審片素材（指令列 review-prep）：build/<比例>/independent-review/
                                 material.json   目錄：這輪審的範圍、成片長度、要審的分鏡編號與時段、字幕帶位置、
                                                 畫面事件時刻、靜止片段（freezedetect）、上一輪要核的問題、抽幀指令
                                 overview-NN.png 每秒一格（4×4，一張 16 秒），格子下面是時間碼、分鏡、那一刻的字幕
                                 cues-NN.png     每個畫面事件出現後 CUE_SETTLE 秒那一格
                                 subtitles.srt   字幕檔（＝旁白逐句與時間碼）
                                 brief.md        審片說明（skills/make-video/references/independent-review.md 的副本）
                                 prompt.txt      派 agent 時給它的 prompt
                                 frames/         agent 用 review-frames 抽的全解析度幀
                                 review.json     agent 寫的回答（格式見 brief.md）
                               素材只從成片、字幕檔、時間軸（分鏡編號與時段）來：不放分鏡設定、旁白稿、文章。
  extract_review_frames(...)   抽全解析度單幀、或一段每 0.25 秒一幀（指令列 review-frames，agent 自己用）。
  record_review(plan, data)    驗證 agent 的回答、記成新的一輪（指令列 review-record）：<專案>/check_log.json 的 rounds，
                               每一項編號 R<輪>-<第幾項>；previous 裡「沒修好／部分修好」的舊問題重新打開。

每一輪審多少（next_round；示範片三輪整片審了 28 分鐘，所以變輕）：
  - 第一輪審整支片（scope "full"）。
  - 整片審的那一輪有必修：改好之後整支片再審一輪核對——整片最多 MAX_FULL_ROUNDS 輪。
  - 其他時候再審只看改過的分鏡（scope "changed"）：跟上一輪審的那份成片比，分鏡內容雜湊（review._shot_hashes：
    分鏡自己的畫面＋長度＋它唸的旁白；前面變長、它往後挪不算）不一樣、新加的、或前一個分鏡換了（換鏡變了）。
    素材只做這幾個分鏡前後各 CONTEXT_SECONDS 秒的總覽、它們的畫面事件；上一輪的問題只帶這幾個分鏡的。
  - 上一輪沒有必修（或必修都核過了）就不用再審（needed=False）：Claude 改了該修，自己看改到的靜態圖、checks 確認。
    作者要求大改時 Claude 照 skill 再審一輪，這時也只看改過的分鏡。
  每一輪記下審的範圍（scope）和那份成片每個分鏡的內容雜湊（shotHashes），下一輪拿來比「改過」。
  舊的紀錄沒有 shotHashes：比不出改了哪些，就整支片審。

審片頁：review_contributor(data_dir) 每張卡列這個分鏡的問題，頁首一區總評。必修沒處理（resolve 沒標改好、也沒接受）
擋出片：publish 的 auto_checks.require_checks_cleared 會問 unresolved_must()（build 只列出來，不擋）。
預設開著；使用者設定 independent_review 可以關（settings set --independent-review 關）。
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from article_to_video.progress import Log, quiet
from article_to_video.check_log import load_log, now, resolution, save_log
from article_to_video.clips import text_font
from article_to_video.commands import PLUGIN_ROOT, aspect_args, cli_command
from article_to_video.environment import SAY_UPDATE_PLUGIN
from article_to_video.ffmpeg import find_ffmpeg
from article_to_video.project import ProjectError, ProjectPaths
from article_to_video.review_page import timecode

if TYPE_CHECKING:
    from article_to_video.build import BuildPlan
    from article_to_video.review import ReviewExtras

__all__ = [
    "BRIEF",
    "MAX_FULL_ROUNDS",
    "SCOPES",
    "SEVERITIES",
    "NextRound",
    "ReviewKit",
    "extract_review_frames",
    "find_kit",
    "item_where",
    "next_round",
    "parse_time_arg",
    "prepare_review_kit",
    "record_review",
    "review_contributor",
    "review_enabled",
    "reviewed_current_video",
    "unresolved_must",
]

BRIEF = PLUGIN_ROOT / "skills" / "make-video" / "references" / "independent-review.md"
KIT_DIR = "independent-review"
SEVERITIES = ("必修", "該修", "可改")
EVIDENCE = ("實測", "推論", "猜測")
PREVIOUS = {"已修好": "fixed", "部分修好": "reopened", "沒修好": "reopened"}
CUE_SETTLE = 0.5
"""畫面事件的那一格取在事件出現後幾秒（動畫大致跑完、還沒換下一件）。"""
SHEET_COLS = 4
SHEET_ROWS = 4
FREEZE_NOISE = "-35dB"
FREEZE_SECONDS = 5
"""靜止片段：字幕帶以外連續這麼久幾乎不動（審片學到：−35 dB 才抓得到慢推的靜止，−40 dB 會被騙）。"""
FULL, CHANGED = "full", "changed"
SCOPES = {FULL: "整支片", CHANGED: "只看改過的分鏡"}
MAX_FULL_ROUNDS = 2
"""整支片最多審幾輪（第一輪＋有必修時的核對輪）；之後的必修改好只審改過的分鏡。"""
CONTEXT_SECONDS = 1.0
"""只看改過的分鏡時，總覽多做前後各幾秒（看換鏡那一下有沒有閃、接不接得上）。"""


# ---------------------------------------------------------------- 設定


def review_enabled(data_dir: str | os.PathLike | None = None) -> bool:
    """使用者設定 independent_review（預設開）。設定檔壞掉時當作開著。"""
    from article_to_video.user_settings import SettingsError, load_user_settings

    try:
        return load_user_settings(data_dir).independent_review
    except SettingsError:
        return True


# ---------------------------------------------------------------- 素材


@dataclass(frozen=True)
class ReviewKit:
    dir: Path
    material: dict[str, Any]

    @property
    def prompt(self) -> Path:
        return self.dir / "prompt.txt"

    @property
    def answer(self) -> Path:
        return self.dir / "review.json"


def kit_dir(plan: BuildPlan) -> Path:
    return plan.paths.root / KIT_DIR


def _final_key(plan: BuildPlan) -> str:
    try:
        return plan.paths.final_stamp.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _parse_srt(path: Path) -> list[tuple[float, float, str]]:
    def secs(s: str) -> float:
        h, m, rest = s.strip().split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000

    out = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip()):
        lines = block.strip().splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            a, b = lines[1].split("-->")
            out.append((secs(a), secs(b), " ".join(lines[2:])))
    return out


def _subtitle_at(subs: Sequence[tuple[float, float, str]], t: float) -> str:
    return next((text for a, b, text in subs if a <= t < b), "")


def _extract_small(video: Path, frames: Sequence[int], out_dir: Path, *, size: tuple[int, int],
                   ffmpeg: Path) -> dict[int, Path]:
    """照幀號抽小圖：整支成片解碼一次、縮成 size，邊讀邊挑要的幀（幾百張也只解碼一次；
    ffmpeg 的 select 運算式太長會失敗，實測 3 分半的片 300 多個幀號就不行）。"""
    from PIL import Image

    want = set(frames)
    out: dict[int, Path] = {}
    if not want:
        return out
    out_dir.mkdir(parents=True, exist_ok=True)
    w, h = size
    proc = subprocess.Popen([str(ffmpeg), "-v", "error", "-i", str(video), "-vf", f"scale={w}:{h}", "-f", "rawvideo",
                             "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.stdout is not None
        last, n = max(want), 0
        while n <= last:
            buf = proc.stdout.read(w * h * 3)
            if len(buf) < w * h * 3:
                break
            if n in want:
                out[n] = out_dir / f"{n:06d}.png"
                Image.frombytes("RGB", (w, h), buf).save(out[n])
            n += 1
    finally:
        proc.kill()
        err = proc.communicate()[1].decode("utf-8", "replace")
    if len(out) != len(want):
        raise ProjectError(f"從成片抽圖失敗：{err.strip()[-300:] or '成片比預期短'}", title="審片素材做不出來")
    return out


BURST_GAP = 60
"""抽全解析度幀時，幀號相隔不到這麼多幀的併成一段、只跳一次、一路解碼挑出來（連續幀每張各跳一次太慢：
審片員要 4 秒的連續幀＝17 次跳轉）；隔得遠的各跳一次，免得為了兩張圖解碼一大段。"""


def _extract_full(video: Path, frames: Sequence[int], fps: int, out_dir: Path, ffmpeg: Path) -> dict[int, Path]:
    """照幀號抽全解析度圖。每一段跳一次（-ss 在 -i 前面＝跳到那個時間點之後的第一幀，往前讓 0.25 幀確保是那一段的
    第一幀），之後解碼出來的第 k 幀就是第「段首＋k」幀，用 select 挑要的。"""
    out: dict[int, Path] = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    groups: list[list[int]] = []
    for n in sorted(set(frames)):
        if groups and n - groups[-1][-1] <= BURST_GAP:
            groups[-1].append(n)
        else:
            groups.append([n])
    for group in groups:
        first = group[0]
        pick = "+".join(f"eq(n\\,{n - first})" for n in group)
        pattern = out_dir / f"g{first:06d}-%03d.png"
        proc = subprocess.run([str(ffmpeg), "-v", "error", "-y", "-ss", f"{max(0.0, (first - 0.25) / fps):.4f}",
                               "-i", str(video), "-vf", f"select='{pick}'", "-fps_mode", "passthrough",
                               "-frames:v", str(len(group)), str(pattern)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        for k, n in enumerate(group, start=1):
            dest = out_dir / f"g{first:06d}-{k:03d}.png"
            if proc.returncode != 0 or not dest.is_file():
                raise ProjectError(f"從成片抽圖失敗（第 {n} 幀）：{proc.stderr.strip()[-300:]}", title="抽不了幀")
            out[n] = dest
    return out


def _fit(draw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _sheets(tiles: Sequence[tuple[Path, str, str]], out_dir: Path, prefix: str, tile_w: int) -> list[str]:
    """把 (圖, 第一行, 第二行) 拼成 4×4 的總覽圖，回傳檔名。"""
    from PIL import Image, ImageDraw

    names: list[str] = []
    per = SHEET_COLS * SHEET_ROWS
    f1, f2 = text_font(18), text_font(16)
    for k in range(math.ceil(len(tiles) / per)):
        chunk = tiles[k * per:(k + 1) * per]
        with Image.open(chunk[0][0]) as im0:
            tile_h = round(im0.height * tile_w / im0.width)
        cell_h = tile_h + 52
        rows = math.ceil(len(chunk) / SHEET_COLS)
        sheet = Image.new("RGB", (SHEET_COLS * tile_w, rows * cell_h), "white")
        draw = ImageDraw.Draw(sheet)
        for i, (path, line1, line2) in enumerate(chunk):
            x, y = (i % SHEET_COLS) * tile_w, (i // SHEET_COLS) * cell_h
            with Image.open(path) as im:
                sheet.paste(im.convert("RGB").resize((tile_w, tile_h)), (x, y))
            draw.rectangle((x, y, x + tile_w - 1, y + cell_h - 1), outline=(200, 200, 200))
            draw.text((x + 6, y + tile_h + 4), _fit(draw, line1, f1, tile_w - 12), fill=(20, 20, 20), font=f1)
            draw.text((x + 6, y + tile_h + 28), _fit(draw, line2, f2, tile_w - 12), fill=(90, 90, 90), font=f2)
        name = f"{prefix}-{k + 1:02d}.png"
        sheet.save(out_dir / name)
        names.append(name)
    return names


def _freeze(video: Path, band_top: int, width: int, ffmpeg: Path) -> list[dict[str, float]]:
    """字幕帶以外連續 FREEZE_SECONDS 秒以上幾乎不動的片段（ffmpeg freezedetect）。"""
    proc = subprocess.run(
        [str(ffmpeg), "-v", "info", "-i", str(video), "-an", "-vf",
         f"crop={width}:{band_top}:0:0,scale=480:-2,freezedetect=n={FREEZE_NOISE}:d={FREEZE_SECONDS}", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out: list[dict[str, float]] = []
    for key, val in re.findall(r"lavfi\.freezedetect\.freeze_(start|end): ([\d.]+)", proc.stderr):
        if key == "start":
            out.append({"start": round(float(val), 2)})
        elif out and "end" not in out[-1]:
            out[-1]["end"] = round(float(val), 2)
    for f in out:
        f.setdefault("end", None)
        f["seconds"] = round(f["end"] - f["start"], 2) if f["end"] is not None else None
    return out


def _shot_of(plan: BuildPlan, t: float) -> int | None:
    fps = plan.timeline.fps
    for st in plan.timeline.shots:
        if st.start_frame / fps - 1e-6 <= t < st.end_frame / fps:
            return st.shot.number
    return None


# ---------------------------------------------------------------- 這一輪審多少


def _shot_content_hashes(plan: BuildPlan) -> dict[str, str]:
    """每個分鏡的內容雜湊（跟審片頁「這版改過」同一套：分鏡自己的畫面＋長度＋它唸的旁白）。"""
    from article_to_video.review import _shot_hashes, shot_narration  # 避免循環匯入

    return _shot_hashes(plan, shot_narration(plan.timeline))


def _rounds_of(book: dict[str, Any], aspect: str) -> list[dict[str, Any]]:
    return [r for r in book["rounds"] if isinstance(r, dict) and r.get("aspect") == aspect]


def _scope_of(rnd: dict[str, Any]) -> str:
    return CHANGED if rnd.get("scope") == CHANGED else FULL  # 舊紀錄沒有 scope：當時都是整支片


def _confirmed(res: dict[str, Any] | None) -> bool:
    """獨立審片核過、說已修好。"""
    return res is not None and res.get("status") == "fixed" and str(res.get("by", "")).startswith("獨立審片")


def _pending_must(book: dict[str, Any], rounds: Sequence[dict[str, Any]]) -> list[tuple[dict, dict]]:
    """(那一輪, 必修)：還沒有被下一輪審片核過的必修（沒處理、被重新打開、或只是做片的說改好了）。接受不改的不算。"""
    out = []
    for rnd in rounds:
        for it in rnd.get("items") or []:
            if it.get("severity") != "必修":
                continue
            res = resolution(book, it["id"])
            if res is not None and (res.get("status") == "accepted" or _confirmed(res)):
                continue
            out.append((rnd, it))
    return out


@dataclass(frozen=True)
class NextRound:
    """下一輪獨立審片審多少（next_round）。"""

    scope: str
    """FULL 整支片、CHANGED 只看改過的分鏡。"""
    reason: str
    needed: bool
    """照規則要不要再審；False＝可以不審（Claude 自己看改到的靜態圖確認），硬要審就照 scope。"""
    changed: tuple[str, ...] = ()
    """跟上一輪審的成片比，改過的分鏡 id（照片中順序）。"""
    since: int | None = None
    """拿來比的是第幾輪。"""


def _changed_shots(plan: BuildPlan, hashes: dict[str, str], baseline: list[dict[str, Any]]) -> tuple[str, ...]:
    """改過的分鏡：內容雜湊不一樣、新加的，或前一個分鏡換了（拿掉、插入、換順序：換鏡那一下變了）。"""
    then = {s.get("id"): s.get("hash") for s in baseline if isinstance(s, dict)}
    order_then = [s.get("id") for s in baseline if isinstance(s, dict)]
    prev_then = {sid: (order_then[i - 1] if i else None) for i, sid in enumerate(order_then)}
    order = [st.shot.id for st in plan.timeline.shots]
    out = []
    for i, sid in enumerate(order):
        before = order[i - 1] if i else None
        if then.get(sid) != hashes[sid] or prev_then.get(sid, "?") != before:
            out.append(sid)
    return tuple(out)


def next_round(plan: BuildPlan) -> NextRound:
    """照規則（見模組說明）決定下一輪審整支片還是只看改過的分鏡、需不需要再審。不寫任何檔案。"""
    book = load_log(plan.project.root)
    rounds = _rounds_of(book, plan.format.name)
    if not rounds:
        return NextRound(FULL, "第一輪審整支片", True)
    fulls = sum(1 for r in rounds if _scope_of(r) == FULL)
    pending = _pending_must(book, rounds)
    from_full = [it["id"] for rnd, it in pending if _scope_of(rnd) == FULL]
    last = rounds[-1]
    baseline = last.get("shotHashes")
    since = last.get("round") if isinstance(baseline, list) else None
    changed = _changed_shots(plan, _shot_content_hashes(plan), baseline) if since is not None else ()
    if from_full and fulls < MAX_FULL_ROUNDS:
        return NextRound(FULL, f"整支片審的那一輪有必修（{'、'.join(from_full)}），改好之後整支片再審一輪核對", True,
                         changed, since)
    if since is None:
        return NextRound(FULL, f"第 {last.get('round')} 輪的紀錄沒有記分鏡內容，比不出改了哪些，整支片審", bool(pending))
    if pending:
        ids = "、".join(it["id"] for _, it in pending)
        why = (f"還有必修要核對（{ids}）；整支片已經審過 {fulls} 輪，這輪只看改過的分鏡" if fulls >= MAX_FULL_ROUNDS
               else f"還有必修要核對（{ids}），只看改過的分鏡")
        return NextRound(CHANGED, why, bool(changed), changed, since)
    return NextRound(CHANGED, f"第 {since} 輪之後沒有必修要核對：改過的分鏡自己看靜態圖確認就好；"
                              "改得多（約三分之一以上的分鏡、或整段旁白重寫）才審一輪改過的分鏡",
                     False, changed, since)


def _shot_label(plan: BuildPlan, ids: Sequence[str]) -> str:
    nums = {st.shot.id: st.shot.number for st in plan.timeline.shots}
    return "、".join(f"分鏡 {nums[i]}" for i in ids if i in nums)


def _ranges(plan: BuildPlan, ids: Sequence[str]) -> list[tuple[float, float]]:
    """改過的分鏡在成片裡的時段，前後各多 CONTEXT_SECONDS 秒，重疊的併起來。"""
    fps, duration = plan.timeline.fps, plan.timeline.duration
    spans = sorted((max(0.0, st.start_frame / fps - CONTEXT_SECONDS), min(duration, st.end_frame / fps + CONTEXT_SECONDS))
                   for st in plan.timeline.shots if st.shot.id in set(ids))
    out: list[tuple[float, float]] = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _seconds(a: float, b: float, duration: float) -> list[float]:
    """總覽格子的時刻：從 a 起每秒一格，到 b（不含最後 0.05 秒）。"""
    end = min(b, duration) - 0.05
    return [round(a + k, 3) for k in range(math.floor(end - a) + 1)] if end >= a else []


def prepare_review_kit(plan: BuildPlan, *, scope: str | None = None, log: Log = quiet) -> ReviewKit:
    """從成片做審片素材（見模組說明）。scope：None＝照 next_round 的規則；FULL／CHANGED＝硬要這樣審。
    成片不是現在這份分鏡設定做出來的（改過還沒重新出片）、或只看改過的分鏡卻沒有改過的，丟 ProjectError。"""
    from article_to_video.auto_checks import current_video

    video = current_video(plan)
    root, aspect = plan.project.root, aspect_args(plan.format.name)
    if video is None:
        raise ProjectError(
            "成片還沒做、或分鏡改過之後還沒重新出片",
            title="獨立審片要看最新的成片",
            hint=f"先跑 build（{cli_command('build', root, *aspect, '--no-open')}），"
                 "出片成功再準備審片素材。",
        )
    nr = next_round(plan)
    if scope == CHANGED and nr.since is None:
        raise ProjectError("還沒有能拿來比的審片紀錄（第一輪、或舊的紀錄沒記分鏡內容）", title="比不出改了哪些分鏡",
                           hint=f"這輪審整支片：{cli_command('review-prep', root, *aspect)}")
    scope = scope or nr.scope
    hashes = _shot_content_hashes(plan)
    if scope == CHANGED:
        if not nr.changed:
            raise ProjectError(f"這份成片跟第 {nr.since} 輪審的比，沒有改過的分鏡", title="不用再審",
                               hint=f"真的要整支片再審一輪：{cli_command('review-prep', root, *aspect, '--full')}")
        covered = set(nr.changed)
        why = nr.reason if nr.scope == CHANGED else f"指定只看改過的分鏡（照規則是{SCOPES[nr.scope]}：{nr.reason}）"
    else:
        covered = {st.shot.id for st in plan.timeline.shots}
        why = nr.reason if nr.scope == FULL else "指定審整支片"
    ffmpeg = find_ffmpeg()
    fmt, tl = plan.format, plan.timeline
    fps = tl.fps
    kit = kit_dir(plan)
    if kit.exists():
        shutil.rmtree(kit)
    kit.mkdir(parents=True)
    srt = plan.project.paths.srt(fmt.name)
    shutil.copyfile(srt, kit / "subtitles.srt")
    subs = _parse_srt(srt)
    if not BRIEF.is_file():
        raise ProjectError(f"找不到審片說明 {BRIEF}", title="審片素材做不出來", hint=SAY_UPDATE_PLUGIN)
    shutil.copyfile(BRIEF, kit / "brief.md")

    duration = tl.duration
    ranges = [(0.0, duration)] if scope == FULL else _ranges(plan, sorted(covered))
    overview = [t for a, b in ranges for t in _seconds(a, b, duration)]
    cues: list[tuple[float, int]] = []
    for st in tl.shots:
        if st.shot.id not in covered:
            continue
        last = st.end_frame - 1
        for c in st.cues:
            if not c.carried:
                f = min(c.frame + round(CUE_SETTLE * fps), last)
                cues.append((f / fps, st.shot.number))
    cues.sort()
    landscape = fmt.width >= fmt.height
    tile_w = 480 if landscape else 270
    tile_h = round(fmt.height * tile_w / fmt.width / 2) * 2
    frames = [round(t * fps) for t in overview] + [round(t * fps) for t, _ in cues]
    with tempfile.TemporaryDirectory(prefix="atv-review-") as tmp:
        by_frame = _extract_small(video, frames, Path(tmp), size=(tile_w, tile_h), ffmpeg=ffmpeg)

        def tile(t: float, shot: int | None) -> tuple[Path, str, str]:
            where = f"分鏡 {shot}" if shot else "片尾卡"
            sub = _subtitle_at(subs, t)
            return by_frame[round(t * fps)], f"{timecode(t)}｜{where}", f"字幕：{sub}" if sub else "（沒有字幕）"

        overview_sheets = _sheets([tile(t, _shot_of(plan, t)) for t in overview], kit, "overview", tile_w)
        cue_sheets = _sheets([tile(t, n) for t, n in cues], kit, "cues", tile_w)

    band_top = fmt.height - fmt.safe_bottom
    freeze = [f for f in _freeze(video, band_top, fmt.width, ffmpeg)
              if any(f["start"] < b and (f["end"] is None or f["end"] > a) for a, b in ranges)]
    book = load_log(root)
    numbers = {st.shot.id: st.shot.number for st in tl.shots}

    def in_scope(it: dict[str, Any]) -> bool:
        if scope == FULL:
            return True
        sid = it.get("shotId")
        if sid:
            return sid in covered
        t = it.get("time")
        return t is not None and any(a <= float(t) <= b for a, b in ranges)

    previous, known = [], []
    for rnd in book["rounds"]:
        for it in rnd.get("items") or []:
            if it.get("severity") not in ("必修", "該修") or not in_scope(it):
                continue
            res = resolution(book, it["id"])
            if _confirmed(res):
                continue  # 之前的審片核過、已修好：不用再核
            row = {"id": it["id"], "round": rnd.get("round"), "severity": it.get("severity"),
                   "shot": numbers.get(it.get("shotId"), it.get("shot")), "time": it.get("time"),
                   "title": it.get("title")}
            if res is not None and res.get("status") == "accepted":
                known.append(row)
            else:
                previous.append({**row, "status": "做片的說改好了" if res is not None else "還沒處理"})
    material = {
        "version": 1,
        "preparedAt": now(),
        "round": len(book["rounds"]) + 1,
        "scope": scope,
        "scopeNote": SCOPES[scope] + "：" + why,
        "aspect": fmt.name,
        "video": str(video),
        "videoKey": _final_key(plan),
        "duration": round(duration, 3),
        "fps": fps,
        "width": fmt.width,
        "height": fmt.height,
        "subtitleBand": {"top": band_top, "note": f"燒錄字幕在 y ≥ {band_top} px 這一條（含直式的平台介面區）"},
        "shots": [{"number": st.shot.number, "start": round(st.start_frame / fps, 3), "end": round(st.end_frame / fps, 3)}
                  for st in tl.shots if st.shot.id in covered],
        "ranges": [{"start": round(a, 3), "end": round(b, 3)} for a, b in ranges],
        "changedShots": [numbers[i] for i in nr.changed if i in numbers] if nr.since is not None else None,
        "endCard": {"start": round(tl.outro.start_frame / fps, 3), "end": round(duration, 3)},
        "cues": [{"time": round(t, 3), "shot": n} for t, n in cues],
        "sheets": {"overview": overview_sheets, "cues": cue_sheets},
        "subtitles": "subtitles.srt",
        "freeze": freeze,
        "previous": previous,
        "known": known,
        "frameCommand": cli_command("review-frames", root, *aspect),
        "brief": "brief.md",
        "answer": "review.json",
        "shotHashes": [{"id": st.shot.id, "hash": hashes[st.shot.id]} for st in tl.shots],
    }
    (kit / "material.json").write_text(json.dumps(material, ensure_ascii=False, indent=1), encoding="utf-8")
    (kit / "prompt.txt").write_text(_prompt(kit, material), encoding="utf-8")
    what = "整支片" if scope == FULL else f"只看改過的分鏡（{_shot_label(plan, [st.shot.id for st in tl.shots if st.shot.id in covered])}）"
    log(f"審片素材（{fmt.label}，第 {material['round']} 輪，{what}）：總覽 {len(overview_sheets)} 張、"
        f"畫面事件 {len(cue_sheets)} 張、靜止片段 {len(freeze)} 段、要核的上一輪問題 {len(previous)} 項 → {kit}")
    log(f"為什麼{SCOPES[scope]}：{why}")
    return ReviewKit(kit, material)


def _span(m: dict[str, Any]) -> str:
    return "、".join(f"{timecode(r['start'])}–{timecode(r['end'])}" for r in m["ranges"])


def _prompt(kit: Path, m: dict[str, Any]) -> str:
    if m.get("scope") == CHANGED:
        nums = "、".join(f"分鏡 {s['number']}" for s in m["shots"])
        scope = (f"這是第 {m['round']} 輪，只看改過的分鏡：{nums}（成片 {_span(m)}，前後各多 1 秒看換鏡）。"
                 "其他分鏡上一輪審過、之後沒改，不用看。"
                 + ("先逐條核 material.json 的 previous（這幾個分鏡上一輪的問題），再看這幾段有沒有新問題——修 A 常帶出新問題 B。"
                    if m["previous"] else "看這幾段有沒有新問題——修 A 常帶出新問題 B。") + "\n")
    elif m["previous"]:
        changed = m.get("changedShots")
        scope = (f"這是第 {m['round']} 輪（整支片）：先逐條核 material.json 的 previous（上一輪的問題），再找新問題。"
                 + (f"上一輪之後改過的是{'、'.join(f'分鏡 {n}' for n in changed)}，那幾段看仔細一點。" if changed else "")
                 + "\n")
    else:
        scope = ""
    return (
        "你是這支財經解說影片的獨立審片員，只看成片，用觀眾的眼睛找問題。\n\n"
        f"1. 先讀審片說明：{kit / 'brief.md'}\n"
        f"2. 素材在 {kit}（material.json 是目錄）；成片是 {m['video']}。\n"
        f"3. 要看全解析度單幀或每 0.25 秒一幀：{m['frameCommand']} --at 秒數（或 --from 秒 --to 秒 --step 0.25），"
        f"抽出來的圖在 {kit / 'frames'}。\n"
        f"{scope}"
        f"4. 審完照說明裡的 JSON 格式寫到 {kit / 'review.json'}，最後只回一行「審完了：必修 N、該修 N、可改 N」。\n\n"
        "只讀上面這個素材資料夾和成片；不要讀專案資料夾裡的其他檔案（分鏡設定、旁白稿、文章），也不要改任何檔案"
        "（review.json 除外）。\n"
    )


def find_kit(project_dir: str | os.PathLike, aspect: str | None = None) -> ReviewKit:
    """找審片素材：指定比例的，或最近準備的那一份。"""
    build = ProjectPaths(Path(project_dir)).build
    kits = [build / aspect / KIT_DIR] if aspect else sorted(build.glob(f"*/{KIT_DIR}"),
                                                           key=lambda p: (p / "material.json").stat().st_mtime
                                                           if (p / "material.json").is_file() else 0)
    for kit in reversed(kits):
        try:
            material = json.loads((kit / "material.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return ReviewKit(kit, material)
    raise ProjectError("找不到審片素材", title="還沒準備審片素材",
                       hint=f"先跑 review-prep（{cli_command('review-prep', Path(project_dir), *aspect_args(aspect or '16x9'))}）。")


def _parse_time(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    m = re.fullmatch(r"(?:(\d+):)?(\d+(?:\.\d+)?)", s)
    if not m:
        raise ValueError
    return int(m.group(1) or 0) * 60 + float(m.group(2))


def parse_time_arg(s: str) -> float:
    """指令列給的時刻：秒數（12.4）或時間碼（0:12.4）。"""
    try:
        t = _parse_time(s)
    except ValueError:
        t = None
    if t is None:
        raise ProjectError(f"時間「{s}」看不懂：寫秒數 12.4 或時間碼 0:12.4", title="抽不了幀")
    return t


def extract_review_frames(kit: ReviewKit, times: Sequence[float]) -> tuple[list[Path], list[Path]]:
    """成片的全解析度幀（frames/t<秒>.png）；超過一張另外拼總覽（frames/burst-<起>-<迄>-NN.png，16 格一張，一格都不漏）。"""
    m = kit.material
    video, fps, duration = Path(m["video"]), int(m["fps"]), float(m["duration"])
    if not video.is_file():
        raise ProjectError(f"找不到成片 {video}", title="抽不了幀")
    bad = [t for t in times if not 0 <= t < duration]
    if bad:
        raise ProjectError([f"{t:g} 秒不在成片裡（成片長 {duration:.1f} 秒）" for t in bad], title="抽不了幀")
    out = kit.dir / "frames"
    out.mkdir(parents=True, exist_ok=True)
    nums = [min(round(t * fps), round(duration * fps) - 1) for t in times]
    with tempfile.TemporaryDirectory(prefix="atv-frames-") as tmp:
        by_frame = _extract_full(video, nums, fps, Path(tmp), find_ffmpeg())
        paths = []
        for n in nums:
            dest = out / f"t{n / fps:07.2f}.png"
            shutil.copyfile(by_frame[n], dest)
            paths.append(dest)
    sheets: list[Path] = []
    if len(paths) > 1:
        subs = _parse_srt(kit.dir / m.get("subtitles", "subtitles.srt")) if (kit.dir / "subtitles.srt").is_file() else []
        tiles = [(p, f"{timecode(n / fps)}（{n / fps:.2f} 秒）", _subtitle_at(subs, n / fps)) for p, n in zip(paths, nums)]
        names = _sheets(tiles, out, f"burst-{nums[0] / fps:.2f}-{nums[-1] / fps:.2f}",
                        480 if int(m["width"]) >= int(m["height"]) else 270)
        sheets = [out / name for name in names]
    return paths, sheets


# ---------------------------------------------------------------- 記錄


def _severity(v: Any) -> str | None:
    """必修／該修／可改；前面帶 🔴🟡⚪ 也收（審片紀錄的習慣寫法）。"""
    s = str(v or "").strip()
    for sev, emoji in zip(SEVERITIES, ("🔴", "🟡", "⚪")):
        if s in (sev, emoji, emoji + sev, f"{emoji} {sev}"):
            return sev
    return None


def record_review(plan: BuildPlan, answer: Any, kit: ReviewKit) -> dict[str, Any]:
    """驗證 agent 的回答（格式見 brief.md），記成新的一輪，回傳這一輪。格式不對丟 ProjectError（繁中、列出全部）。"""
    problems: list[str] = []
    if not isinstance(answer, dict):
        raise ProjectError("review.json 要是一個 JSON 物件（{…}）", title="審片結果格式不對")
    numbers = {st.shot.number: st for st in plan.timeline.shots}
    fps = plan.timeline.fps
    duration = plan.timeline.duration
    book = load_log(plan.project.root)
    rnd_no = len(book["rounds"]) + 1

    score = answer.get("score")
    if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 10):
        problems.append(f"score「{score}」要是 0～10 的數字")
    raw_items = answer.get("items")
    if not isinstance(raw_items, list):
        problems.append("items 要是一個清單（沒有問題就寫 []）")
        raw_items = []
    items: list[dict[str, Any]] = []
    for i, it in enumerate(raw_items):
        where = f"items[{i}]"
        if not isinstance(it, dict):
            problems.append(f"{where} 要是一個物件")
            continue
        sev = _severity(it.get("severity"))
        if sev is None:
            problems.append(f"{where}.severity「{it.get('severity')}」看不懂：只能是 必修、該修、可改")
        title = it.get("title")
        if not isinstance(title, str) or not title.strip():
            problems.append(f"{where}.title 要寫一句話講問題")
        shot = it.get("shot")
        if shot is not None and (isinstance(shot, bool) or not isinstance(shot, int) or shot not in numbers):
            problems.append(f"{where}.shot「{shot}」不是這支片的分鏡編號（1～{len(numbers)}，整支片的問題寫 null）")
            shot = None
        times: dict[str, float | None] = {}
        for key in ("time", "end"):
            try:
                t = _parse_time(it.get(key))
            except ValueError:
                problems.append(f"{where}.{key}「{it.get(key)}」看不懂：寫 \"0:12.4\" 或秒數 12.4")
                t = None
            if t is not None and not 0 <= t <= duration + 0.05:
                problems.append(f"{where}.{key}「{it.get(key)}」不在成片裡（成片長 {timecode(duration)}）")
                t = None
            times[key] = t
        ev = it.get("evidence")
        if ev is not None and ev not in EVIDENCE:
            problems.append(f"{where}.evidence「{ev}」只能是 實測、推論、猜測")
        if shot is None and times["time"] is not None:
            shot = _shot_of(plan, times["time"])
        items.append({
            "id": f"R{rnd_no}-{len(items) + 1}", "severity": sev, "shot": shot,
            "shotId": numbers[shot].shot.id if shot in numbers else None,
            "time": times["time"], "end": times["end"], "title": (title or "").strip(),
            "detail": str(it.get("detail") or "").strip(), "fix": str(it.get("fix") or "").strip(),
            "evidence": ev or "",
        })
    known = {it["id"] for r in book["rounds"] for it in r.get("items") or []}
    previous = answer.get("previous") or []
    if not isinstance(previous, list):
        problems.append("previous 要是一個清單")
        previous = []
    prev_out = []
    for i, p in enumerate(previous):
        where = f"previous[{i}]"
        if not isinstance(p, dict) or p.get("id") not in known:
            problems.append(f"{where}.id「{(p or {}).get('id') if isinstance(p, dict) else p}」不是之前審片的問題代號")
            continue
        if p.get("status") not in PREVIOUS:
            problems.append(f"{where}.status「{p.get('status')}」只能是 已修好、部分修好、沒修好")
            continue
        prev_out.append({"id": p["id"], "status": p["status"], "note": str(p.get("note") or "").strip()})
    if problems:
        raise ProjectError(problems, title="審片結果格式不對，沒有記下來",
                           hint=f"格式見 {kit.dir / 'brief.md'} 的「回答格式」。改好 review.json 再跑一次 review-record。")

    m = kit.material
    rnd = {
        "round": rnd_no, "aspect": plan.format.name, "recordedAt": now(),
        "videoKey": m.get("videoKey", ""), "preparedAt": m.get("preparedAt"),
        "scope": CHANGED if m.get("scope") == CHANGED else FULL,
        "shots": [s.get("number") for s in m.get("shots") or [] if isinstance(s, dict)],
        "score": score, "summary": str(answer.get("summary") or "").strip(), "items": items, "previous": prev_out,
    }
    if isinstance(m.get("shotHashes"), list):  # 審的那份成片每個分鏡的內容雜湊：下一輪拿來比「改過」
        rnd["shotHashes"] = m["shotHashes"]
    book["rounds"].append(rnd)
    for p in prev_out:
        book["resolutions"][p["id"]] = {"status": PREVIOUS[p["status"]], "note": p["note"] or p["status"],
                                        "by": f"獨立審片第 {rnd_no} 輪", "at": now()}
    save_log(plan.project.root, book)
    return rnd


def reviewed_current_video(plan: BuildPlan) -> bool:
    """這個比例現在的成片審過了沒（有一輪審的就是這份成片）。"""
    key = _final_key(plan)
    return bool(key) and any(r.get("videoKey") == key and r.get("aspect") == plan.format.name
                             for r in load_log(plan.project.root)["rounds"])


def unresolved_must(plan: BuildPlan) -> list[tuple[str, str]]:
    """每一輪獨立審片裡還沒處理（沒標改好、也沒接受）的必修：(代號, 一行說明)。"""
    book = load_log(plan.project.root)
    out: list[tuple[str, str]] = []
    for rnd in book["rounds"]:
        for it in rnd.get("items") or []:
            if it.get("severity") != "必修" or resolution(book, it["id"]) is not None:
                continue
            out.append((it["id"], f"{item_where(it)} 獨立審片第 {rnd.get('round')} 輪：{it.get('title', '')}"
                                  f"（代號 {it['id']}）"))
    return out


def item_where(it: dict[str, Any]) -> str:
    where = f"分鏡 {it['shot']}" if it.get("shot") else "整支片"
    if it.get("time") is not None:
        where += f"（{timecode(float(it['time']))}" + (f"–{timecode(float(it['end']))}" if it.get("end") is not None
                                                     else "") + "）"
    return where


# ---------------------------------------------------------------- 審片頁


def review_contributor(data_dir: str | os.PathLike | None = None) -> Callable[[BuildPlan], ReviewExtras]:
    """審片頁的獨立審片：頁首一區（第幾輪、分數、總評、每一項與處理狀態），每張卡列這個分鏡的問題。"""

    def contribute(plan: BuildPlan) -> ReviewExtras:
        from article_to_video.review import CardSection, CheckItem, ReviewExtras  # 避免循環匯入

        book = load_log(plan.project.root)
        rounds = book["rounds"]
        if not rounds:
            if review_enabled(data_dir):
                item = CheckItem("還沒審", detail="做好審片用的影片之後、給你看之前，Claude 會另外派一個只看影片的 agent 審一輪，結果會出現在這裡。",
                                 status="info")
            else:
                item = CheckItem("已關掉（使用者設定）", detail="要打開跟 Claude 說「獨立審片打開」。", status="info")
            return ReviewExtras(page=[CardSection("review", "獨立審片", [item])])

        def check_item(it: dict[str, Any]) -> CheckItem:
            """作者看到：問題、怎麼了、建議怎麼改。代號（R1-2）、證據等級（實測／推論／猜測）收在給 Claude 的那一區。"""
            res = resolution(book, it["id"])
            text = it["title"]
            parts = (it.get("detail"), f"建議：{it['fix']}" if it.get("fix") else "")
            detail = "；".join(x.rstrip("。；") for x in parts if x)
            code = it["id"] + (f"（證據：{it['evidence']}）" if it.get("evidence") else "") + f"　{it['title']}"
            if res is not None:
                label = "已修好" if res["status"] == "fixed" else "已接受"
                who = "" if res.get("by", "").startswith("獨立審片") else f"（{res.get('by', '')}）"
                return CheckItem(text, detail=f"{label}{who}：{res.get('note', '')}",
                                 status="ok" if res["status"] == "fixed" else "info", label=label, code=code)
            status = {"必修": "block", "該修": "warn"}.get(it["severity"], "info")
            return CheckItem(text, detail=detail, status=status, label=it["severity"], code=code)

        def sections(items: list, title: str, note: str = "") -> list:
            """還沒處理的攤開；已經處理（改好、接受）的收成一行「已處理 N 項」，點開才看。"""
            open_ = [ci for ci in items if ci.label not in ("已修好", "已接受")]
            done = [ci for ci in items if ci.label in ("已修好", "已接受")]
            out = [CardSection("review", title, open_, note=note)] if open_ or note else []
            if done:
                out.append(CardSection("review", title, done, summary=f"{title}：已處理 {len(done)} 項（點開看）"))
            return out

        cards: dict[str, list] = {}
        page_items = []
        for rnd in rounds:
            for it in rnd.get("items") or []:
                ci = check_item(it)
                page_items.append(CheckItem(f"{item_where(it)}　{ci.text}", ci.detail, ci.status, ci.label, ci.code))
                if it.get("shotId"):
                    cards.setdefault(it["shotId"], []).append(ci)
        last = rounds[-1]
        must = [it for r in rounds for it in r.get("items") or [] if it["severity"] == "必修"
                and resolution(book, it["id"]) is None]
        counts = "、".join(f"{s} {sum(1 for it in last['items'] if it['severity'] == s)}" for s in SEVERITIES)
        note = f"第 {last['round']} 輪：{counts}。" + (f"{last['summary']}" if last.get("summary") else "")
        if must:
            note += f" 還有 {len(must)} 項必修還沒處理，處理完才能出片。"
        if last.get("videoKey") and last["videoKey"] != _final_key(plan):
            note += " 這一輪審的是之前的成片，之後改過的地方還沒審。"
        score = f"，{last['score']:g} 分" if isinstance(last.get("score"), (int, float)) else ""
        title = f"獨立審片（第 {last['round']} 輪{score}）"
        if not page_items:
            page = [CardSection("review", title, [], summary=f"{title}：沒有找到問題。{last.get('summary') or ''}".strip())]
        else:
            page = sections(page_items, title, note)
        return ReviewExtras(cards={sid: sections(items, "獨立審片") for sid, items in cards.items()}, page=page)

    return contribute

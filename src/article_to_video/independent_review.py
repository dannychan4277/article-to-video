"""獨立審片：出片之後，Claude Code 另外派一個 agent，只看成片審一輪。Python 不派 agent，這裡只做三件事：

  prepare_review_kit(plan)     從成片準備審片素材（指令列 review-prep）：build/<比例>/independent-review/
                                 material.json   目錄：成片長度、每個分鏡的編號與時段、字幕帶位置、畫面事件時刻、
                                                 靜止片段（freezedetect）、上一輪的問題、抽幀指令
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

審片頁：review_contributor(data_dir) 每張卡列這個分鏡的問題，頁首一區總評。必修沒處理（resolve 沒標改好、也沒接受）
擋正式出片：auto_checks.require_checks_cleared 會問 unresolved_must()。
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

from article_to_video.check_log import load_log, now, resolution, save_log
from article_to_video.clips import FONTS_DIR
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
    "SEVERITIES",
    "ReviewKit",
    "extract_review_frames",
    "find_kit",
    "item_where",
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

Log = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


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


def _extract_full(video: Path, frames: Sequence[int], fps: int, out_dir: Path, ffmpeg: Path) -> dict[int, Path]:
    """照幀號抽全解析度圖：每張各跳一次（-ss 在 -i 前面＝跳到那個時間點之後的第一幀，往前讓 0.25 幀確保是這一幀）。"""
    out: dict[int, Path] = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    for n in sorted(set(frames)):
        dest = out_dir / f"{n:06d}.png"
        proc = subprocess.run([str(ffmpeg), "-v", "error", "-y", "-ss", f"{max(0.0, (n - 0.25) / fps):.4f}",
                               "-i", str(video), "-frames:v", "1", str(dest)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not dest.is_file():
            raise ProjectError(f"從成片抽圖失敗（第 {n} 幀）：{proc.stderr.strip()[-300:]}", title="抽不了幀")
        out[n] = dest
    return out


def _font(size: int):
    from PIL import ImageFont

    return ImageFont.truetype(str(FONTS_DIR / "NotoSansTC-500.woff"), size)


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
    f1, f2 = _font(18), _font(16)
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


def prepare_review_kit(plan: BuildPlan, *, log: Log = _quiet) -> ReviewKit:
    """從成片做審片素材（見模組說明）。成片不是現在這份分鏡設定做出來的（改過還沒重新出片）就丟 ProjectError。"""
    from article_to_video.auto_checks import current_video

    video = current_video(plan)
    if video is None:
        raise ProjectError(
            "成片還沒做、或分鏡改過之後還沒重新出片",
            title="獨立審片要看最新的成片",
            hint=f"先跑 build（{cli_command('build', plan.project.root, *aspect_args(plan.format.name), '--no-open')}），"
                 "出片成功再準備審片素材。",
        )
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
    overview = list(range(0, math.floor(duration - 0.05) + 1))
    cues: list[tuple[float, int]] = []
    for st in tl.shots:
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
    freeze = _freeze(video, band_top, fmt.width, ffmpeg)
    book = load_log(plan.project.root)
    said = {"fixed": "做片的說改好了", "accepted": "作者或做片的決定不改"}
    previous = [
        {"id": it["id"], "round": rnd.get("round"), "severity": it.get("severity"), "shot": it.get("shot"),
         "time": it.get("time"), "title": it.get("title"),
         "status": said.get((resolution(book, it["id"]) or {}).get("status"), "還沒處理")}
        for rnd in book["rounds"] for it in rnd.get("items") or [] if it.get("severity") in ("必修", "該修")
    ]
    material = {
        "version": 1,
        "preparedAt": now(),
        "round": len(book["rounds"]) + 1,
        "aspect": fmt.name,
        "video": str(video),
        "videoKey": _final_key(plan),
        "duration": round(duration, 3),
        "fps": fps,
        "width": fmt.width,
        "height": fmt.height,
        "subtitleBand": {"top": band_top, "note": f"燒錄字幕在 y ≥ {band_top} px 這一條（含直式的平台介面區）"},
        "shots": [{"number": st.shot.number, "start": round(st.start_frame / fps, 3), "end": round(st.end_frame / fps, 3)}
                  for st in tl.shots],
        "endCard": {"start": round(tl.outro.start_frame / fps, 3), "end": round(duration, 3)},
        "cues": [{"time": round(t, 3), "shot": n} for t, n in cues],
        "sheets": {"overview": overview_sheets, "cues": cue_sheets},
        "subtitles": "subtitles.srt",
        "freeze": freeze,
        "previous": previous,
        "frameCommand": cli_command("review-frames", plan.project.root, *aspect_args(fmt.name)),
        "brief": "brief.md",
        "answer": "review.json",
    }
    (kit / "material.json").write_text(json.dumps(material, ensure_ascii=False, indent=1), encoding="utf-8")
    (kit / "prompt.txt").write_text(_prompt(kit, material), encoding="utf-8")
    log(f"審片素材（{fmt.label}，第 {material['round']} 輪）：總覽 {len(overview_sheets)} 張、畫面事件 {len(cue_sheets)} 張、"
        f"靜止片段 {len(freeze)} 段 → {kit}")
    return ReviewKit(kit, material)


def _prompt(kit: Path, m: dict[str, Any]) -> str:
    second = "這是第 {0} 輪：先逐條核 material.json 的 previous（上一輪的問題），再找新問題。\n".format(m["round"]) \
        if m["previous"] else ""
    return (
        "你是這支財經解說影片的獨立審片員，只看成片，用觀眾的眼睛找問題。\n\n"
        f"1. 先讀審片說明：{kit / 'brief.md'}\n"
        f"2. 素材在 {kit}（material.json 是目錄）；成片是 {m['video']}。\n"
        f"3. 要看全解析度單幀或每 0.25 秒一幀：{m['frameCommand']} --at 秒數（或 --from 秒 --to 秒 --step 0.25），"
        f"抽出來的圖在 {kit / 'frames'}。\n"
        f"{second}"
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


def extract_review_frames(kit: ReviewKit, times: Sequence[float]) -> tuple[list[Path], Path | None]:
    """成片的全解析度幀（frames/t<秒>.png）；超過一張另外拼一張總覽（frames/burst-<起>-<迄>.png）。"""
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
    sheet = None
    if len(paths) > 1:
        subs = _parse_srt(kit.dir / m.get("subtitles", "subtitles.srt")) if (kit.dir / "subtitles.srt").is_file() else []
        tiles = [(p, f"{timecode(n / fps)}（{n / fps:.2f} 秒）", _subtitle_at(subs, n / fps)) for p, n in zip(paths, nums)]
        name = _sheets(tiles[:SHEET_COLS * SHEET_ROWS], out, f"burst-{nums[0] / fps:.2f}-{nums[-1] / fps:.2f}",
                       480 if int(m["width"]) >= int(m["height"]) else 270)[0]
        sheet = out / name
    return paths, sheet


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

    rnd = {
        "round": rnd_no, "aspect": plan.format.name, "recordedAt": now(),
        "videoKey": kit.material.get("videoKey", ""), "preparedAt": kit.material.get("preparedAt"),
        "score": score, "summary": str(answer.get("summary") or "").strip(), "items": items, "previous": prev_out,
    }
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
                item = CheckItem("還沒審", detail="出片之後 Claude 會另外派一個只看成片的 agent 審一輪，結果會出現在這裡。",
                                 status="info")
            else:
                item = CheckItem("已關掉（使用者設定）", detail="要打開跟 Claude 說「獨立審片打開」。", status="info")
            return ReviewExtras(page=[CardSection("review", "獨立審片", [item])])

        def check_item(it: dict[str, Any]) -> CheckItem:
            res = resolution(book, it["id"])
            text = f"{it['title']}（{it['id']}）"
            parts = (it.get("detail"), f"建議：{it['fix']}" if it.get("fix") else "",
                     f"證據：{it['evidence']}" if it.get("evidence") else "")
            detail = "；".join(x.rstrip("。；") for x in parts if x)
            if res is not None:
                label = "已修好" if res["status"] == "fixed" else "已接受"
                return CheckItem(text, detail=f"{label}（{res.get('by', '')}）：{res.get('note', '')}",
                                 status="ok" if res["status"] == "fixed" else "info", label=label)
            status = {"必修": "block", "該修": "warn"}.get(it["severity"], "info")
            return CheckItem(text, detail=detail, status=status, label=it["severity"])

        cards: dict[str, list] = {}
        page_items = []
        for rnd in rounds:
            for it in rnd.get("items") or []:
                ci = check_item(it)
                page_items.append(CheckItem(f"{item_where(it)}　{ci.text}", ci.detail, ci.status, ci.label))
                if it.get("shotId"):
                    cards.setdefault(it["shotId"], []).append(ci)
        last = rounds[-1]
        must = [it for r in rounds for it in r.get("items") or [] if it["severity"] == "必修"
                and resolution(book, it["id"]) is None]
        counts = "、".join(f"{s} {sum(1 for it in last['items'] if it['severity'] == s)}" for s in SEVERITIES)
        note = f"第 {last['round']} 輪：{counts}。" + (f"{last['summary']}" if last.get("summary") else "")
        if must:
            note += f" 還有 {len(must)} 項必修沒處理，正式出片會被擋下。"
        if last.get("videoKey") and last["videoKey"] != _final_key(plan):
            note += " 這一輪審的是之前的成片，之後改過的地方還沒審。"
        if not page_items:
            page_items = [CheckItem("沒有找到問題", status="ok")]
        score = f"，{last['score']:g} 分" if isinstance(last.get("score"), (int, float)) else ""
        page = [CardSection("review", f"獨立審片（第 {last['round']} 輪{score}）", page_items, note=note)]
        return ReviewExtras(cards={sid: [CardSection("review", "獨立審片", items)] for sid, items in cards.items()},
                            page=page)

    return contribute

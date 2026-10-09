"""審片頁：每個分鏡的關鍵靜態圖＋一頁本機 HTML，給作者看、留言、複製給 Claude。

兩種時機都會寫同一頁（專案資料夾裡的「審片頁.html」）：
  preview_project()       預覽模式：plan_build() → 只渲靜態圖（不渲片段、不出成片）→ 審片頁。幾秒內完成。
  make_review(plan, video=成片)   出片之後：同一頁再內嵌成片。

靜態圖：每個分鏡一張「完成狀態」（分鏡最後一幀）。快取在 build/<比例>/stills/<片段>/，
鍵＝片段雜湊（spec＋引擎檔）＋要渲的時刻，內容沒變就不重渲。
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from article_to_video.build import BuildPlan, plan_build
from article_to_video.clip_cache import clip_hash
from article_to_video.clips import ClipRenderError, render_stills
from article_to_video.timeline import ClipPlan, Timeline

__all__ = [
    "NarrationPiece",
    "ReviewResult",
    "Still",
    "make_review",
    "preview_project",
    "shot_narration",
]

STILLS_VERSION = "1"
MOMENT_GAP = 0.8
"""畫面事件跟上一個事件至少隔這麼多秒，才另外出一張「這個事件之前」的靜態圖。"""
MAX_MOMENTS = 3
"""每個分鏡最多幾張過程靜態圖（不含完成狀態那張）。"""
_ASSETS = Path(__file__).resolve().parent / "review_assets"

Log = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


# ---------------------------------------------------------------- 每個分鏡的旁白


@dataclass(frozen=True)
class NarrationPiece:
    """一個分鏡唸到的某個旁白段的一部分。"""

    segment: str
    text: str
    starts_mid: bool
    """不是從這段開頭唸起（前面的字屬於上一個分鏡）。"""
    ends_mid: bool
    """沒唸到這段結尾（後面的字屬於下一個分鏡）。"""


def shot_narration(timeline: Timeline) -> dict[str, list[NarrationPiece]]:
    """每個分鏡期間唸到的旁白：一個字歸給「開始唸它的那一刻」所在的分鏡；標點跟著前一個字走。"""
    fps = timeline.fps
    bounds = [(st.shot.id, st.start_frame / fps, st.end_frame / fps) for st in timeline.shots]

    def owner(t: float) -> str:
        for sid, a, b in bounds:
            if a - 1e-9 <= t < b:
                return sid
        return bounds[-1][0] if t >= bounds[-1][2] else bounds[0][0]

    out: dict[str, list[NarrationPiece]] = {sid: [] for sid, _, _ in bounds}
    for seg in timeline.segments.values():
        au = seg.audio
        in_word = [False] * len(au.text)
        for w in au.words:
            for i in range(w.start_char, min(w.end_char, len(au.text))):
                in_word[i] = True
        owners: list[str] = []
        for i in range(len(au.text)):
            if in_word[i] or not owners:
                owners.append(owner(seg.start + au.char_start(i)))
            else:
                owners.append(owners[-1])
        # 連續同一個分鏡的字併成一塊
        i = 0
        while i < len(owners):
            j = i
            while j < len(owners) and owners[j] == owners[i]:
                j += 1
            text = au.text[i:j]
            if text.strip():
                out[owners[i]].append(NarrationPiece(seg.key, text, starts_mid=i > 0, ends_mid=j < len(owners)))
            i = j
    return out


# ---------------------------------------------------------------- 靜態圖


@dataclass(frozen=True)
class Still:
    path: Path
    clip_time: float
    """片段內秒數。"""
    video_time: float
    """在成片裡的秒數（審片頁顯示的時間碼）。"""
    final: bool
    """這個分鏡的完成狀態（分鏡最後一幀）。"""


def _still_frames(plan: BuildPlan) -> dict[str, list[tuple[int, bool]]]:
    """每個分鏡要渲的成片幀號（, 是否完成狀態）。

    - 完成狀態：分鏡最後一幀（下一個分鏡切進來之前，作者最後看到的畫面）。
    - 過程：這個分鏡自己的每個畫面事件（cue）出現的前一幀＝上一句講完、畫面停在那裡的樣子；
      跟上一個事件隔不到 MOMENT_GAP 秒的不算（上一個動畫還沒跑完，看了也沒意義）。
      接續分鏡的第一個事件也算（前一幀＝沿用的圖）。最多 MAX_MOMENTS 張。
    """
    fps = plan.timeline.fps
    gap = round(MOMENT_GAP * fps)
    out: dict[str, list[tuple[int, bool]]] = {}
    for st in plan.timeline.shots:
        final = st.end_frame - 1
        own = sorted({c.frame for c in st.cues if not c.carried})
        prev = st.start_frame if st.shot.continues else None
        moments: list[int] = []
        for f in own:
            if prev is not None and f - prev >= gap and st.start_frame < f - 1 < final:
                moments.append(f - 1)
            prev = f
        out[st.shot.id] = [(final, True)] + [(f, False) for f in moments[:MAX_MOMENTS]]
    return out


def _clip_of(plan: BuildPlan) -> dict[str, ClipPlan]:
    return {sid: cp for cp in plan.clips for sid in cp.shot_ids}


def _still_name(t: float) -> str:
    return f"t{t:06.2f}.png"  # 跟 render_stills 的檔名規則一樣


def render_review_stills(plan: BuildPlan, *, jobs: int | None = None, log: Log = _quiet) -> dict[str, list[Still]]:
    """渲（或從快取拿）每個分鏡的靜態圖。回傳分鏡 id → 靜態圖（完成狀態排第一張，其餘照時間）。"""
    fps = plan.timeline.fps
    root = plan.paths.stills
    root.mkdir(parents=True, exist_ok=True)
    clip_of = _clip_of(plan)
    frames = _still_frames(plan)

    per_clip: dict[str, list[float]] = {cp.id: [] for cp in plan.clips}
    for sid, items in frames.items():
        cp = clip_of[sid]
        per_clip[cp.id].extend(round((f - cp.start_frame) / fps, 6) for f, _ in items)
    for cid in per_clip:
        per_clip[cid] = sorted(set(per_clip[cid]))

    for d in root.iterdir():
        if d.is_dir() and d.name not in per_clip:
            shutil.rmtree(d)

    todo: list[tuple[ClipPlan, str]] = []
    for cp in plan.clips:
        key = hashlib.sha256(
            json.dumps([STILLS_VERSION, clip_hash(cp), per_clip[cp.id]]).encode()
        ).hexdigest()
        stamp = root / cp.id / "stamp.json"
        have = all((root / cp.id / _still_name(t)).is_file() for t in per_clip[cp.id])
        if have and stamp.is_file() and json.loads(stamp.read_text(encoding="utf-8")).get("key") == key:
            continue
        todo.append((cp, key))

    def one(cp: ClipPlan, key: str) -> None:
        d = root / cp.id
        with tempfile.TemporaryDirectory(prefix="atv-stills-", dir=root) as tmp:
            try:
                render_stills(cp.spec, tmp, per_clip[cp.id], base_dir=plan.project.root)
            except ClipRenderError as e:
                shots = "、".join(cp.shot_ids)
                raise ClipRenderError(f"分鏡 {shots} 的靜態圖渲染失敗：{e}", page_errors=e.page_errors, log=e.log) from e
            if d.exists():
                shutil.rmtree(d)
            d.mkdir()
            for f in Path(tmp).glob("*.png"):
                f.replace(d / f.name)
        (d / "stamp.json").write_text(json.dumps({"key": key}), encoding="utf-8")

    if todo:
        n_jobs = jobs or min(4, max(1, (os.cpu_count() or 2) // 2))
        log(f"分鏡靜態圖：{len(todo)} 個片段要渲（{len(plan.clips) - len(todo)} 個用快取）")
        with ThreadPoolExecutor(max_workers=max(1, min(n_jobs, len(todo)))) as pool:
            futures = [pool.submit(one, cp, key) for cp, key in todo]
            errors = []
            for f in futures:
                try:
                    f.result()
                except ClipRenderError as e:
                    errors.append(e)
            if errors:
                if len(errors) == 1:
                    raise errors[0]
                raise ClipRenderError("\n\n".join(str(e) for e in errors), log="\n".join(e.log for e in errors))
    else:
        log("分鏡靜態圖：全部用快取")

    out: dict[str, list[Still]] = {}
    for sid, items in frames.items():
        cp = clip_of[sid]
        stills = []
        for f, final in items:
            t = round((f - cp.start_frame) / fps, 6)
            stills.append(Still(root / cp.id / _still_name(t), t, f / fps, final))
        out[sid] = sorted(stills, key=lambda s: (not s.final, s.video_time))
    return out


# ---------------------------------------------------------------- 審片頁


@dataclass(frozen=True)
class ReviewResult:
    page: Path
    stills: dict[str, list[Still]] = field(default_factory=dict)


def timecode(seconds: float) -> str:
    """成片時間碼，給作者看：分:秒.十分之一秒（例 1:05.3）。"""
    tenths = round(seconds * 10)
    return f"{tenths // 600}:{tenths % 600 / 10:04.1f}"


def _rel(page: Path, target: Path) -> str:
    return quote(os.path.relpath(target, page.parent).replace(os.sep, "/"))


def _asset(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


def _render_page(plan: BuildPlan, page: Path, stills: dict[str, list[Still]], video: Path | None) -> str:
    e = html.escape
    project_name = plan.project.root.name
    narration = shot_narration(plan.timeline)
    fps = plan.timeline.fps
    cards: list[str] = []
    for st in plan.timeline.shots:
        shot = st.shot
        imgs = "".join(
            f'<img class="still" src="{_rel(page, s.path)}" data-t="{s.video_time:.3f}" alt="分鏡 {shot.number} 的畫面" loading="lazy">'
            for s in stills.get(shot.id, [])
        )
        pieces = "".join(
            f'<p>{"…" if p.starts_mid else ""}{e(p.text)}{"…" if p.ends_mid else ""}</p>' for p in narration[shot.id]
        )
        cards.append(
            f'<article class="card" id="shot-{e(shot.id)}" data-shot-id="{e(shot.id)}" data-shot-number="{shot.number}">'
            f'<h2>分鏡 {shot.number}</h2>'
            f'<span class="timecode" data-start="{st.start_frame / fps:.3f}">'
            f'{timecode(st.start_frame / fps)}–{timecode(st.end_frame / fps)}</span>'
            f'<div class="stills">{imgs}</div>'
            f'<div class="narration">{pieces}</div>'
            f'<label>想改什麼？<textarea class="comment" data-shot-id="{e(shot.id)}"></textarea></label>'
            f'</article>'
        )
    video_html = f'<video controls preload="metadata" src="{_rel(page, video)}"></video>' if video else ""
    return (
        '<!doctype html>\n<html lang="zh-Hant-TW">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>審片頁｜{e(project_name)}</title>\n<style>\n{_asset("review.css")}\n</style>\n</head>\n<body>\n'
        f'<header><h1>審片頁</h1><p>{e(project_name)}</p></header>\n'
        f'{video_html}\n<main>\n' + "\n".join(cards) +
        '\n<section class="overall-box"><label>整體意見<textarea class="overall"></textarea></label></section>\n'
        '</main>\n</body>\n</html>\n'
    )


def make_review(
    plan: BuildPlan,
    *,
    video: Path | None = None,
    jobs: int | None = None,
    log: Log = _quiet,
) -> ReviewResult:
    """渲靜態圖（有快取）、寫審片頁。video＝成片（出片後才有）。"""
    stills = render_review_stills(plan, jobs=jobs, log=log)
    page = plan.project.paths.review_page(plan.format.name)
    page.write_text(_render_page(plan, page, stills, video), encoding="utf-8")
    log(f"審片頁：{page}")
    return ReviewResult(page=page, stills=stills)


def preview_project(
    project_dir: str | os.PathLike,
    *,
    tts: str | None = None,
    jobs: int | None = None,
    log: Log = _quiet,
) -> ReviewResult:
    """預覽模式：配音＋時間軸＋分鏡靜態圖＋審片頁，不渲片段、不出成片。"""
    plan = plan_build(project_dir, tts=tts, log=log)
    return make_review(plan, jobs=jobs, log=log)

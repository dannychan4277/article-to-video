"""審片頁：每個分鏡的分鏡靜態圖＋一頁本機 HTML，給作者看、留言、複製給 Claude。

兩種時機都會寫同一頁（專案資料夾裡的「審片頁.html」，9:16 是「審片頁-9x16.html」）：
  preview_project()               預覽模式：plan_build() → 只渲靜態圖（不渲片段、不出成片）→ 審片頁。
  make_review(plan, video=成片)   build 之後（CLI build 在 build_project 之後呼叫）：同一頁再內嵌審片用的成片。

分鏡靜態圖（_still_frames）：每個分鏡一張完成狀態（分鏡最後一幀；Vox 接續分鏡取鏡頭移走之前），加上這個分鏡
每個畫面事件出現前一幀（跟上一個事件隔 ≥ MOMENT_GAP 秒才算，最多 MAX_MOMENTS 張）。快取在 build/<比例>/stills/<片段>/，
鍵＝片段雜湊（spec＋引擎檔＋圖片）＋要渲的時刻，內容沒變就不重渲；並行渲。

版本（build/<比例>/review.json）：每次寫審片頁都比對每個分鏡的內容雜湊（它自己畫面上的東西＋長度＋它唸的旁白，
見 _shot_hashes；照分鏡算，不照片段算）。
跟上次一樣就沿用版本號與「這版改過」的標記；不一樣就加一版，標出改過／新加的分鏡、列出拿掉的分鏡。
瀏覽器裡的留言照版本存：新的一版開頭會問要不要放回上一版還沒複製出去的留言（見 review_assets/review.js）。

其他檢查（數字對帳 #5、自動檢查 #11）的結果：寫一個 Contributor（BuildPlan → ReviewExtras），
加進 default_contributors()；它回傳的 CardSection 會排在對應分鏡卡的 numbers／checks 欄位。

指令列做完會用 open_in_browser()（macOS open）打開；auto_open_allowed() 看 --no-open 以外的開關。
「複製給 Claude」的文字格式見 feedback.py。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from article_to_video.build import BuildPlan, plan_build
from article_to_video.clip_cache import clip_hash, content_fields, engine_fingerprint
from article_to_video.clips import GRAMMAR_VOX, ClipRenderError, default_jobs, render_stills, run_parallel
from article_to_video.macos import open_path
from article_to_video.progress import Log, quiet
from article_to_video.textio import read_json_or_none
from article_to_video.review_page import CardModel, CardSection, CheckItem, PageModel, StillModel, render_review_html
from article_to_video.timeline import ClipPlan, Timeline, VideoFormat
from article_to_video.videos import video_title

__all__ = [
    "NO_OPEN_ENV",
    "CardSection",
    "CheckItem",
    "Contributor",
    "NarrationPiece",
    "ReviewExtras",
    "ReviewResult",
    "Still",
    "auto_open_allowed",
    "default_contributors",
    "make_review",
    "open_in_browser",
    "preview_project",
    "render_review_stills",
    "seen_version",
    "shot_narration",
]

STILLS_VERSION = "1"
MOMENT_GAP = 0.8
"""畫面事件跟上一個事件至少隔這麼多秒，才另外出一張「這個事件之前」的靜態圖。"""
MAX_MOMENTS = 3
"""每個分鏡最多幾張過程靜態圖（不含完成狀態那張）。"""
VOX_LEAVE = 1.0
"""Vox 鏡頭最早在下一件素材出現前多久開始移過去（引擎 y2_vox：素材 at 前 0.95 秒、畫面外的框 at 前 0.8 秒）。
分鏡靜態圖取在這之前，拍的是鏡頭還停在素材上的樣子。"""

NO_OPEN_ENV = "ARTICLE_TO_VIDEO_NO_OPEN"
"""設成 1 就不自動打開審片頁（測試、CI 用）；指令列也可以加 --no-open。"""

@dataclass(frozen=True)
class ReviewExtras:
    """其他檢查要放上審片頁的結果。cards：分鏡 id → 那張卡上的幾區（數字對帳放 slot "numbers"、
    自動檢查放 "checks"）；page：整片層級的幾區（例：獨立審片的總評），排在分鏡卡前面。"""

    cards: dict[str, list[CardSection]] = field(default_factory=dict)
    page: list[CardSection] = field(default_factory=list)


Contributor = Callable[[BuildPlan], ReviewExtras]
"""審片頁的資料來源：拿到建置計畫（分鏡、時間軸、每個片段的 spec），回傳要加在卡片上的東西。預覽和出片都會呼叫。"""


def default_contributors(data_dir: str | os.PathLike | None = None) -> list[Contributor]:
    """指令列（preview、build、checks、review-record、publish）預設用的資料來源：數字對帳（#5）、Vox 框核對（#8）、
    自動檢查與獨立審片（#11；data_dir＝使用者資料位置，看獨立審片有沒有關掉）、出片到哪裡（#13）。
    函式內 import，免得循環匯入。"""
    from article_to_video.auto_checks import checks_contributor
    from article_to_video.boxes import boxes_contributor
    from article_to_video.independent_review import review_contributor
    from article_to_video.numbers import number_review
    from article_to_video.publish import output_contributor

    return [number_review, boxes_contributor, checks_contributor, review_contributor(data_dir), output_contributor]


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


def _next_cue_frames(plan: BuildPlan) -> dict[str, int]:
    """Vox 分鏡 → 同一個片段裡、它後面第一個畫面事件（下一件素材或框）的幀。片段在它之後就結束的不列。"""
    clip_of = _clip_of(plan)
    shots = plan.timeline.shots
    out: dict[str, int] = {}
    for i, st in enumerate(shots):
        cp = clip_of[st.shot.id]
        later = [c.frame for nxt in shots[i + 1:] if clip_of[nxt.shot.id] is cp for c in nxt.cues if not c.carried]
        if later:
            out[st.shot.id] = min(later)
    return out


def _still_frames(plan: BuildPlan) -> dict[str, list[tuple[int, bool]]]:
    """每個分鏡要渲的成片幀號（, 是否完成狀態）。

    - 完成狀態：分鏡最後一幀（下一個分鏡切進來之前，作者最後看到的畫面）。
      Vox 接續分鏡例外：同一張桌面上，鏡頭在下一件素材（或框）出現前 VOX_LEAVE 秒內就開始移過去、
      到了才有素材滑進來，最後一幀拍到的是空桌面或動態模糊（試跑實測）。所以取鏡頭還停在這個分鏡最後一件素材上的
      最後一刻：下一個畫面事件前 VOX_LEAVE 秒；但不早於自己最後一件素材出現的下一幀。
    - 過程：這個分鏡自己的每個畫面事件（cue）出現的前一幀＝上一句講完、畫面停在那裡的樣子（Vox 一樣改成
      事件前 VOX_LEAVE 秒，鏡頭還沒離開的時候）；跟上一個事件隔不到 MOMENT_GAP 秒的不算（上一個動畫還沒跑完，
      看了也沒意義）。接續分鏡的第一個事件也算（前一幀＝沿用的圖）。最多 MAX_MOMENTS 張。
    """
    fps = plan.timeline.fps
    gap = round(MOMENT_GAP * fps)
    clip_of = _clip_of(plan)
    next_cue = _next_cue_frames(plan)
    out: dict[str, list[tuple[int, bool]]] = {}
    for st in plan.timeline.shots:
        vox = clip_of[st.shot.id].grammar == GRAMMAR_VOX
        lead = round(VOX_LEAVE * fps) if vox else 1
        final = st.end_frame - 1
        own = sorted({c.frame for c in st.cues if not c.carried})
        if vox and st.shot.id in next_cue:
            final = min(final, next_cue[st.shot.id] - lead)
            if own:
                final = max(final, min(own[-1] + 1, st.end_frame - 1))
            final = max(final, st.start_frame)
        prev = st.start_frame if st.shot.continues else None
        moments: list[int] = []
        for f in own:
            m = f - lead
            if prev is not None and (m if vox else f) - prev >= gap and st.start_frame < m < final:
                moments.append(m)
            prev = f
        out[st.shot.id] = [(final, True)] + [(f, False) for f in moments[:MAX_MOMENTS]]
    return out


def _clip_of(plan: BuildPlan) -> dict[str, ClipPlan]:
    return {sid: cp for cp in plan.clips for sid in cp.shot_ids}


def _still_name(t: float) -> str:
    return f"t{t:06.2f}.png"  # 跟 render_stills 的檔名規則一樣


def _stamp_key(path: Path) -> str | None:
    doc = read_json_or_none(path)
    return doc.get("key") if isinstance(doc, dict) else None


def render_review_stills(plan: BuildPlan, *, jobs: int | None = None, log: Log = quiet) -> dict[str, list[Still]]:
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
        if not per_clip[cp.id]:
            continue  # 不屬於任何分鏡的片段（例如之後的片尾卡）不上審片頁
        key = hashlib.sha256(
            json.dumps([STILLS_VERSION, clip_hash(cp), per_clip[cp.id]]).encode()
        ).hexdigest()
        if _stamp_key(root / cp.id / "stamp.json") == key and all(
            (root / cp.id / _still_name(t)).is_file() for t in per_clip[cp.id]
        ):
            continue
        todo.append((cp, key))

    def one(item: tuple[ClipPlan, str]) -> None:
        cp, key = item
        d = root / cp.id
        with tempfile.TemporaryDirectory(prefix="atv-stills-", dir=root) as tmp:
            try:
                render_stills(cp.spec, tmp, per_clip[cp.id], base_dir=plan.project.root)
            except ClipRenderError as e:
                raise e.about(cp.shot_ids, "靜態圖") from e
            if d.exists():
                shutil.rmtree(d)
            d.mkdir()
            for f in Path(tmp).glob("*.png"):
                f.replace(d / f.name)
        (d / "stamp.json").write_text(json.dumps({"key": key}), encoding="utf-8")

    if todo:
        log(f"分鏡靜態圖：{len(todo)} 個片段要渲（{len(plan.clips) - len(todo)} 個用快取）")
        run_parallel(todo, one, jobs=jobs or default_jobs())
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
    version: int
    """審片頁第幾版：分鏡內容（畫面或旁白）有變才加一。"""
    changed: list[str]
    """這版跟上一版比，內容改過的分鏡 id（新加的也算）。"""
    removed: list[str]
    """上一版有、這版拿掉的分鏡 id。"""
    stills: dict[str, list[Still]] = field(default_factory=dict)
    plan: BuildPlan | None = None
    """寫這一頁用的建置計畫（指令列拿它印數字對帳的摘要）。"""


# ---------------------------------------------------------------- 版本：這版改了哪些分鏡


@dataclass(frozen=True)
class _Version:
    number: int
    changed: list[str]
    removed: list[tuple[str, int]]


_VOX_MATERIAL = ("image", "excerpt", "title", "point")
"""Vox 桌面上的素材（cue 的 kind）；框（box）畫在它前面最近的那件原圖或剪報上。"""


def _chart_picture(cp: ClipPlan) -> list:
    """財經圖表分鏡（一個分鏡一個片段）畫面上的東西：整份 spec（接續分鏡沿用的 data、cue 都在裡面）＋引擎檔。
    慢推（push、pushFrom）不算：同一串前面的分鏡變長，後面的推近量跟著變，但作者看不出差別。"""
    data = {k: v for k, v in (cp.spec.get("data") or {}).items() if k not in ("push", "pushFrom")}
    spec = {**{k: v for k, v in cp.spec.items() if k not in ("duration", "data", "cues")},
            "data": content_fields(data), "cues": [content_fields(c) for c in cp.spec.get("cues") or []]}
    return [engine_fingerprint(cp.grammar), spec]


def _vox_picture(plan: BuildPlan, index: int, cp: ClipPlan) -> list:
    """Vox 分鏡（一串接續分鏡共用一個片段）畫面上的東西：只算這個分鏡自己的素材與框（給引擎的欄位、圖片內容、
    在這個分鏡裡的時間），加上它開場時桌上那件素材——如果它沒有一開場就換上自己的素材（例：只有框的接續分鏡，
    框的是上一個分鏡貼的圖，連同那張圖上已經畫好的框）。同一串其他分鏡改了，這個分鏡的畫面沒變就不算改過。"""
    shots = plan.timeline.shots
    st = shots[index]
    fps = plan.timeline.fps
    own = [[c.kind, content_fields(c.fields), c.frame - st.start_frame] for c in st.cues]
    opens_with_own = bool(st.cues) and st.cues[0].kind in _VOX_MATERIAL and \
        st.cues[0].frame - st.start_frame <= round(VOX_LEAVE * fps)
    context: list = []
    if not opens_with_own:
        earlier = [c for prev in shots[:index] if prev.shot.id in cp.shot_ids for c in prev.cues]
        last = max((i for i, c in enumerate(earlier) if c.kind in _VOX_MATERIAL), default=None)
        if last is not None:
            context = [content_fields(c.fields) for c in earlier[last:]]  # 那件素材＋之後畫在它上面的框
    base = {k: v for k, v in cp.spec.items() if k not in ("duration", "cues")}
    return [engine_fingerprint(cp.grammar), base, own, context]


def _shot_hashes(plan: BuildPlan, narration: dict[str, list[NarrationPiece]]) -> dict[str, str]:
    """每個分鏡的內容雜湊＝它自己畫面上的東西（_chart_picture／_vox_picture）＋長度＋它唸的旁白。
    照分鏡算、不照片段算：一串接續的 Vox 分鏡共用一個片段，只改其中一個，只有它（和畫面真的跟著變的）算改過。
    在成片裡的絕對時間不算：前面的分鏡變長，後面的分鏡往後挪，但畫面沒變就不算改過。"""
    clip_of = _clip_of(plan)
    out: dict[str, str] = {}
    for i, st in enumerate(plan.timeline.shots):
        cp = clip_of[st.shot.id]
        picture = _vox_picture(plan, i, cp) if cp.grammar == GRAMMAR_VOX else _chart_picture(cp)
        payload = [picture, st.frames, [[p.segment, p.text] for p in narration[st.shot.id]]]
        out[st.shot.id] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return out


def _update_version(state_path: Path, plan: BuildPlan, hashes: dict[str, str]) -> _Version:
    """跟上一次寫審片頁時比：一樣就沿用版本號與標記（重跑不會把標記洗掉），不一樣就加一版、重算標記。"""
    shots = [{"id": st.shot.id, "number": st.shot.number, "hash": hashes[st.shot.id]} for st in plan.timeline.shots]
    prev = None
    if state_path.is_file():
        try:
            prev = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prev = None
    if not isinstance(prev, dict) or not isinstance(prev.get("shots"), list) or not isinstance(prev.get("version"), int):
        version = _Version(1, [], [])
    elif [(s.get("id"), s.get("hash")) for s in prev["shots"]] == [(s["id"], s["hash"]) for s in shots]:
        version = _Version(prev["version"], list(prev.get("changed", [])),
                           [(r[0], r[1]) for r in prev.get("removed", [])])
    else:
        old = {s.get("id"): s for s in prev["shots"]}
        changed = [s["id"] for s in shots if s["id"] not in old or old[s["id"]].get("hash") != s["hash"]]
        now = {s["id"] for s in shots}
        removed = [(s.get("id"), s.get("number")) for s in prev["shots"] if s.get("id") not in now]
        version = _Version(prev["version"] + 1, changed, removed)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "version": version.number,
        "shots": shots,
        "changed": version.changed,
        "removed": [list(r) for r in version.removed],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return version


def seen_version(plan: BuildPlan) -> tuple[int | None, bool]:
    """上次寫的審片頁是第幾版，以及現在的分鏡與旁白還是不是那一版（出片前確認作者看過的就是要出的）。
    還沒寫過審片頁＝(None, False)。不寫任何檔案。"""
    try:
        prev = json.loads(plan.paths.review_state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, False
    if not isinstance(prev, dict) or not isinstance(prev.get("version"), int) or not isinstance(prev.get("shots"), list):
        return None, False
    hashes = _shot_hashes(plan, shot_narration(plan.timeline))
    same = [(s.get("id"), s.get("hash")) for s in prev["shots"] if isinstance(s, dict)] == [
        (st.shot.id, hashes[st.shot.id]) for st in plan.timeline.shots]
    return prev["version"], same


def _rel(page: Path, target: Path) -> str:
    return quote(os.path.relpath(target, page.parent).replace(os.sep, "/"))


def _storage_key(plan: BuildPlan) -> str:
    """瀏覽器存留言用的鍵：同一個專案資料夾、同一個比例共用（file:// 的頁面共用同一個 localStorage）。"""
    raw = f"{plan.project.root}|{plan.format.name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _published(plan: BuildPlan) -> dict | None:
    """這個比例最近一次出片的紀錄（publish 寫的 output.json），而且出的就是現在這份成片；不然 None。"""
    from article_to_video.videos import load_outputs

    rec = load_outputs(plan.project.root).get(plan.format.name)
    stamp = plan.paths.final_stamp
    current = stamp.read_text(encoding="utf-8") if stamp.is_file() else None
    return rec if rec and current and rec.get("videoKey") == current else None


def _finder_path(video: str) -> str:
    """成片所在資料夾，用作者在 Finder 看到的說法（~/Movies 叫「影片」）。"""
    folder = Path(video).parent
    try:
        rel = folder.relative_to(Path.home() / "Movies")
        parts = ["影片", *rel.parts]
    except ValueError:
        parts = [folder.parent.name, folder.name]
    return " → ".join(p for p in parts if p)


def _page_model(plan: BuildPlan, page: Path, stills: dict[str, list[Still]], video: Path | None,
                narration: dict[str, list[NarrationPiece]], version: _Version, extras: ReviewExtras) -> PageModel:
    fps = plan.timeline.fps
    published = _published(plan) if video else None
    cards = [
        CardModel(
            id=st.shot.id,
            number=st.shot.number,
            start=st.start_frame / fps,
            end=st.end_frame / fps,
            narration=[(p.text, p.starts_mid, p.ends_mid) for p in narration[st.shot.id]],
            stills=[StillModel(_rel(page, s.path), s.video_time, s.final) for s in stills.get(st.shot.id, [])],
            continues=st.shot.continues,
            changed=st.shot.id in version.changed,
            sections=list(extras.cards.get(st.shot.id, [])),
        )
        for st in plan.timeline.shots
    ]
    return PageModel(
        title=video_title(plan.project.root),
        storage_key=_storage_key(plan),
        version=version.number,
        stage="published" if published else "review" if video else "preview",
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        duration=plan.timeline.duration,
        cards=cards,
        video_src=_rel(page, video) if video else None,
        removed=version.removed,
        page_sections=list(extras.page),
        aspect=plan.format.name,
        published_to=_finder_path(published["video"]) if published and published.get("video") else "",
        published_version=published.get("reviewVersion") if published else None,
    )


def make_review(
    plan: BuildPlan,
    *,
    video: Path | None = None,
    contributors: Sequence[Contributor] = (),
    jobs: int | None = None,
    log: Log = quiet,
) -> ReviewResult:
    """渲靜態圖（有快取）、寫審片頁。video＝審片用的成片（build 之後才有）；contributors＝要加在卡片上的檢查結果。"""
    extras = ReviewExtras()
    for contribute in contributors:
        more = contribute(plan)
        for sid, secs in more.cards.items():
            extras.cards.setdefault(sid, []).extend(secs)
        extras.page.extend(more.page)
    stills = render_review_stills(plan, jobs=jobs, log=log)
    narration = shot_narration(plan.timeline)
    version = _update_version(plan.paths.review_state, plan, _shot_hashes(plan, narration))
    page = plan.project.paths.review_page(plan.format.name)
    model = _page_model(plan, page, stills, video, narration, version, extras)
    page.write_text(render_review_html(model), encoding="utf-8")
    if version.changed or version.removed:
        log(f"審片頁第 {version.number} 版：改了 {len(version.changed)} 個分鏡"
            + (f"、拿掉 {len(version.removed)} 個" if version.removed else ""))
    return ReviewResult(page=page, version=version.number, changed=list(version.changed),
                        removed=[r[0] for r in version.removed], stills=stills, plan=plan)


def preview_project(
    project_dir: str | os.PathLike,
    *,
    tts: str | None = None,
    contributors: Sequence[Contributor] = (),
    jobs: int | None = None,
    data_dir: str | os.PathLike | None = None,
    fmt: VideoFormat | None = None,
    log: Log = quiet,
) -> ReviewResult:
    """預覽模式：配音＋時間軸＋分鏡靜態圖＋審片頁，不渲片段、不出成片。data_dir：使用者資料位置（設定快照用）。
    fmt：畫面比例（不給就照設定快照的預設比例，見 plan_build）。"""
    plan = plan_build(project_dir, tts=tts, fmt=fmt, data_dir=data_dir, log=log)
    return make_review(plan, contributors=contributors, jobs=jobs, log=log)


# ---------------------------------------------------------------- 在瀏覽器打開


def auto_open_allowed() -> bool:
    """建置完要不要自動打開審片頁：只在 macOS，而且沒設 ARTICLE_TO_VIDEO_NO_OPEN、不在 CI 裡。"""
    if os.environ.get(NO_OPEN_ENV, "").strip() not in ("", "0"):
        return False
    if os.environ.get("CI"):
        return False
    return sys.platform == "darwin"


def open_in_browser(page: Path) -> bool:
    """用 macOS 的 open 打開審片頁（.html 交給預設瀏覽器）。打不開就回傳 False，不當成建置失敗。"""
    return open_path(page)

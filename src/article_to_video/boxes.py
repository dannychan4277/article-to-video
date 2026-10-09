"""Vox 框核對：每個框先出一張靜態圖，Claude 看過、確認框在旁白講的那個數字上，才放行正式出片。

流程（主 skill 照這個走）：
  1. preview（或 boxes 指令）幫每個框渲兩張圖，放在 build/<比例>/boxes/：
       <分鏡>-框<n>-畫面.png   框畫好、鏡頭推近之後那一刻的成片畫面（觀眾看到的樣子）
       <分鏡>-框<n>-原圖.png   原圖（或剪報）加 10% 格線與像素座標，紅框是 rect 的位置（改 rect 時對座標用）
  2. Claude 打開每張圖看：框（螢光筆＋紅圈）在旁白唸到的那個數字上、沒圈進別的數字或欄位名、沒刷到圖例色塊。
  3. 沒問題就 confirm-box <專案> <核對代號>…（代號 SH02:1@1a2b3c4d＝分鏡 SH02 第 1 個框＋框內容雜湊前 8 碼）。
     核對紀錄存在 <專案>/box_checks.json，依框的內容雜湊記：原圖（或剪報）的位元組、框的位置、錨點的詞、
     那句旁白——任何一樣改了，雜湊就變，要重新看、重新核對。拿舊代號核對改過的框會被拒絕。
  4. 正式出片（build）前檢查（require_boxes_ready）：有框沒核對過、或框跟下一件素材隔不到 2 秒，就擋下，
     訊息列出哪些分鏡的哪個框、靜態圖在哪。預覽永遠不擋。

框跟下一件素材至少隔 MIN_GAP（2）秒：框出現後鏡頭要推近、停住讓觀眾看清楚，太快換下一件，推近只停一下就甩走
（審片學到的規則）。下一件＝同一張桌面上下一張圖、剪報或文字卡，或這個片段結束（切到下一個分鏡）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from article_to_video.clip_cache import clip_hash
from article_to_video.clips import FONTS_DIR, ClipRenderError, render_stills
from article_to_video.commands import cli_command
from article_to_video.project import ProjectError, ProjectPaths, load_project
from article_to_video.vox import VoxBox, resolve_vox

if TYPE_CHECKING:
    from article_to_video.build import BuildPlan
    from article_to_video.review import ReviewExtras
    from article_to_video.timeline import ClipPlan

__all__ = [
    "MIN_GAP",
    "BoxStatus",
    "box_statuses",
    "boxes_contributor",
    "confirm_boxes",
    "format_box_listing",
    "project_boxes",
    "render_box_stills",
    "require_boxes_ready",
]

MIN_GAP = 2.0
"""框跟下一件素材（或片段結束）至少隔幾秒。"""
SETTLE = 1.25
"""框出現後幾秒出畫面靜態圖：螢光筆掃完、紅圈畫完、鏡頭推近到位（引擎：推近 at+0.2～at+1.2）。"""
STILLS_VERSION = "1"
CHECKS_VERSION = 1

Log = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


# ---------------------------------------------------------------- 核對紀錄


def _load_checks(paths: ProjectPaths) -> dict[str, dict]:
    try:
        doc = json.loads(paths.box_checks.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    confirmed = doc.get("confirmed") if isinstance(doc, dict) else None
    return confirmed if isinstance(confirmed, dict) else {}


def _save_checks(paths: ProjectPaths, confirmed: dict[str, dict]) -> None:
    doc = {"version": CHECKS_VERSION, "confirmed": confirmed}
    tmp = paths.box_checks.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(paths.box_checks)


def project_boxes(project_dir: str | os.PathLike) -> tuple[ProjectPaths, list[VoxBox]]:
    """不配音、不排時間，只讀分鏡設定＋文章，算出每個框（核對用；幾乎不花時間）。"""
    project = load_project(project_dir)
    _, vox = resolve_vox(project)
    return project.paths, vox.boxes


@dataclass(frozen=True)
class ConfirmResult:
    confirmed: list[VoxBox]
    remaining: list[VoxBox]
    """還沒核對的框（這次之後）。"""


def confirm_boxes(project_dir: str | os.PathLike, tokens: Sequence[str], *, note: str = "") -> ConfirmResult:
    """記下 Claude 核對過的框。代號要跟目前的框對得上（框改過就拒絕，要重看靜態圖）；有一個不對就全部不記。"""
    paths, boxes = project_boxes(project_dir)
    by_id = {b.id: b for b in boxes}
    problems: list[str] = []
    chosen: list[VoxBox] = []
    if not tokens:
        problems.append("沒有給核對代號（例 SH02:1@1a2b3c4d，跑 boxes 指令會列出來）")
    for tok in tokens:
        box_id, _, digest = tok.strip().partition("@")
        box = by_id.get(box_id)
        if box is None:
            have = "、".join(by_id) or "（這個專案沒有框）"
            problems.append(f"沒有「{box_id}」這個框（現有：{have}）")
        elif not digest:
            problems.append(f"{box_id} 要寫完整的核對代號（{box_id}@…，跑 boxes 指令會列出來）")
        elif not box.hash.startswith(digest):
            problems.append(
                f"{box_id} 在你看過靜態圖之後改過了（現在的代號是 {box.token}）。"
                "重跑 boxes 指令，看新的靜態圖，確認沒問題再核對"
            )
        else:
            chosen.append(box)
    if problems:
        raise ProjectError(problems, title="沒有記下核對")
    confirmed = _load_checks(paths)
    now = datetime.now().isoformat(timespec="seconds")
    for box in chosen:
        confirmed[box.hash] = {"box": box.id, "what": box.describe(), "confirmedAt": now, **({"note": note} if note else {})}
    _save_checks(paths, confirmed)
    remaining = [b for b in boxes if b.hash not in confirmed]
    return ConfirmResult(confirmed=chosen, remaining=remaining)


# ---------------------------------------------------------------- 框的狀態（含時間）


@dataclass(frozen=True)
class BoxStatus:
    box: VoxBox
    clip: ClipPlan
    frame: int
    """框出現在成片的第幾幀。"""
    gap: float
    """框出現到下一件素材（或片段結束）的秒數。"""
    next_what: str
    """下一件是什麼（給訊息用）。"""
    confirmed: bool
    screen: Path
    """畫面靜態圖。"""
    overlay: Path
    """原圖對照。"""
    still_time: float
    """畫面靜態圖在片段裡的秒數。"""
    warnings: list[str] = field(default_factory=list)

    @property
    def gap_ok(self) -> bool:
        return self.gap >= MIN_GAP - 1e-6

    @property
    def ready(self) -> bool:
        return self.confirmed and self.gap_ok


def _file_stem(box: VoxBox) -> str:
    return f"{box.shot_id}-框{box.index}"


def box_statuses(plan: BuildPlan) -> list[BoxStatus]:
    """每個框的狀態：出現在哪一幀、跟下一件隔多久、核對過沒、靜態圖放哪。"""
    boxes = plan.vox.boxes
    if not boxes:
        return []
    fps = plan.timeline.fps
    confirmed = _load_checks(plan.project.paths)
    clip_of = {sid: cp for cp in plan.clips for sid in cp.shot_ids}
    cues = {c.where: c for st in plan.timeline.shots for c in st.cues}
    shots_of_clip: dict[str, list[str]] = {}
    for st in plan.timeline.shots:
        shots_of_clip.setdefault(clip_of[st.shot.id].id, []).append(st.shot.id)
    out: list[BoxStatus] = []
    for box in boxes:
        cp = clip_of[box.shot_id]
        ct = cues[box.where]
        later = sorted(
            (c.frame, c.kind) for st in plan.timeline.shots if st.shot.id in shots_of_clip[cp.id]
            for c in st.cues if c.kind != "box" and c.frame > ct.frame
        )
        nxt, what = later[0] if later else (cp.end_frame, "")
        what = {"image": "下一張原圖", "excerpt": "下一張剪報", "title": "下一張標題卡", "point": "下一張索引卡"}.get(
            what, "下一個分鏡（切走）")
        settle = min(ct.frame + round(SETTLE * fps), cp.end_frame - 1)
        warnings = _swatch_warning(box)
        out.append(BoxStatus(
            box=box, clip=cp, frame=ct.frame, gap=(nxt - ct.frame) / fps, next_what=what,
            confirmed=box.hash in confirmed,
            screen=plan.paths.boxes / f"{_file_stem(box)}-畫面.png",
            overlay=plan.paths.boxes / f"{_file_stem(box)}-原圖.png",
            still_time=round((settle - cp.start_frame) / fps, 6),
            warnings=warnings,
        ))
    return out


def _swatch_warning(box: VoxBox) -> list[str]:
    """框裡有一塊實心的鮮豔顏色：可能是圖例色塊，螢光筆（黃色、正片疊底）刷過去會把它染成別的顏色。
    只算「實心」的色塊（上下左右幾個像素也都是同樣鮮豔的），彩色的字筆畫細，不算。"""
    if box.target.kind != "image":
        return []
    x, y, w, h = box.rect
    with Image.open(box.target.image) as im:
        hsv = np.asarray(im.convert("RGB").crop((x, y, x + w, y + h)).convert("HSV")).astype(int)
    k = 4
    if hsv.shape[0] <= 2 * k or hsv.shape[1] <= 2 * k:
        return []
    vivid = (hsv[..., 1] >= 100) & (hsv[..., 2] >= 80)
    solid = (vivid[k:-k, k:-k] & vivid[:-2 * k, k:-k] & vivid[2 * k:, k:-k]
             & vivid[k:-k, :-2 * k] & vivid[k:-k, 2 * k:])
    share = float(solid.sum()) / (w * h)
    if solid.sum() < 100 or share < 0.03:
        return []
    return [f"框裡有一塊實心的彩色（約 {share:.0%}）：如果是圖例色塊，螢光筆會把它染成別的顏色、觀眾會看錯是哪一條。"
            "框要從色塊右邊的字開始，或改框圖裡對應的那個數字"]


# ---------------------------------------------------------------- 靜態圖


def _stamp(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def render_box_stills(plan: BuildPlan, statuses: Sequence[BoxStatus], *, jobs: int | None = None,
                      log: Log = _quiet) -> None:
    """渲每個框的畫面靜態圖（同一個片段的框一次渲完）＋畫原圖對照。內容沒變就用快取。"""
    if not statuses:
        return
    root = plan.paths.boxes
    root.mkdir(parents=True, exist_ok=True)
    stamp_path = root / "stamp.json"
    stamps = _stamp(stamp_path)
    per_clip: dict[str, list[BoxStatus]] = {}
    for st in statuses:
        per_clip.setdefault(st.clip.id, []).append(st)

    todo: list[tuple[ClipPlan, list[BoxStatus], str]] = []
    for sts in per_clip.values():
        cp = sts[0].clip
        key = hashlib.sha256(json.dumps(
            [STILLS_VERSION, clip_hash(cp), [(s.box.id, s.still_time) for s in sts]]).encode()).hexdigest()
        if stamps.get(cp.id) == key and all(s.screen.is_file() for s in sts):
            continue
        todo.append((cp, sts, key))

    def one(cp: ClipPlan, sts: list[BoxStatus]) -> None:
        times = sorted({s.still_time for s in sts})
        with tempfile.TemporaryDirectory(prefix="atv-boxes-", dir=root) as tmp:
            try:
                res = render_stills(cp.spec, tmp, times, base_dir=plan.project.root)
            except ClipRenderError as e:
                raise ClipRenderError(f"分鏡 {'、'.join(cp.shot_ids)} 的框靜態圖渲染失敗：{e}",
                                      page_errors=e.page_errors, log=e.log) from e
            by_time = dict(zip(times, res.paths))
            for s in sts:
                shutil.copyfile(by_time[s.still_time], s.screen)

    if todo:
        log(f"框的靜態圖：{sum(len(s) for _, s, _ in todo)} 個框要渲")
        n_jobs = jobs or min(4, max(1, (os.cpu_count() or 2) // 2))
        with ThreadPoolExecutor(max_workers=max(1, min(n_jobs, len(todo)))) as pool:
            for f in [pool.submit(one, cp, sts) for cp, sts, _ in todo]:
                f.result()
        for cp, _, key in todo:
            stamps[cp.id] = key
        stamp_path.write_text(json.dumps(stamps), encoding="utf-8")
    for st in statuses:
        _draw_overlay(st)


def _overlay_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS_DIR / "NotoSansTC-500.woff"), size)


def _draw_overlay(st: BoxStatus) -> None:
    """原圖對照：原圖＋每 10% 一條格線（標像素座標）＋紅色外框＝框的位置。框的內容雜湊沒變就不重畫。"""
    marker = st.overlay.with_suffix(".sha256")
    if st.overlay.is_file() and marker.is_file() and marker.read_text(encoding="utf-8") == st.box.hash:
        return
    t = st.box.target
    with Image.open(t.image) as src:
        im = src.convert("RGB")
    d = ImageDraw.Draw(im, "RGBA")
    size = max(14, min(t.width, t.height) // 40)
    font = _overlay_font(size)
    for k in range(1, 10):
        x, y = round(t.width * k / 10), round(t.height * k / 10)
        d.line([(x, 0), (x, t.height)], fill=(0, 120, 255, 110), width=1)
        d.line([(0, y), (t.width, y)], fill=(0, 120, 255, 110), width=1)
        d.text((x + 3, 2), str(x), font=font, fill=(0, 90, 220, 230))
        d.text((3, y + 2), str(y), font=font, fill=(0, 90, 220, 230))
    x, y, w, h = st.box.rect
    lw = max(3, min(t.width, t.height) // 200)
    d.rectangle([x, y, x + w, y + h], outline=(230, 30, 30, 255), width=lw)
    d.text((x, max(0, y - size - 6)), f"{st.box.id} {list(st.box.rect)}", font=font, fill=(230, 30, 30, 255))
    tmp = st.overlay.with_suffix(".tmp.png")
    im.save(tmp)
    tmp.replace(st.overlay)
    marker.write_text(st.box.hash, encoding="utf-8")


# ---------------------------------------------------------------- 給人看的文字


def _status_lines(st: BoxStatus) -> list[str]:
    b = st.box
    head = "✓ 已核對" if st.confirmed else "✗ 還沒核對"
    lines = [f"{head}：分鏡 {b.shot_number}（{b.shot_id}）框 {b.index}　核對代號 {b.token}",
             f"    框住：{b.describe()}",
             f"    畫面靜態圖：{st.screen}",
             f"    原圖對照：{st.overlay}"]
    if not st.gap_ok:
        lines.append(f"    ✗ 框出現後 {st.gap:.1f} 秒就換{st.next_what}，至少要隔 {MIN_GAP:g} 秒（鏡頭推近要停住讓人看清楚）："
                     "把下一件的錨點往後挪、框的錨點往前挪，或把這句旁白寫長一點")
    for w in st.warnings:
        lines.append(f"    注意：{w}")
    return lines


CHECKLIST = ("看每張「畫面靜態圖」（必要時對照「原圖對照」），確認：① 螢光筆和紅圈剛好在旁白唸到的那個數字上，"
             "沒圈進別的數字、別的欄位或欄位名；② 螢光筆沒刷到圖例色塊；③ 數字沒被畫面邊緣或字幕帶切到。")


def format_box_listing(statuses: Sequence[BoxStatus], project_dir: Path) -> str:
    """boxes 指令印的清單。"""
    if not statuses:
        return "這個專案沒有 Vox 框。"
    todo = [s for s in statuses if not s.confirmed]
    out = [f"Vox 框核對：共 {len(statuses)} 個框，{len(todo)} 個還沒核對。"]
    for st in statuses:
        out.extend(_status_lines(st))
    if todo:
        out.append(CHECKLIST)
        out.append("都沒問題就執行（只寫你看過、確認過的框）：")
        out.append("  " + cli_command("confirm-box", project_dir, *(s.box.token for s in todo)))
        out.append("有問題就改 shots.json 裡那個框的 rect／find，或錨點，再跑一次 boxes。")
    return "\n".join(out)


def require_boxes_ready(plan: BuildPlan, *, jobs: int | None = None, log: Log = _quiet) -> None:
    """正式出片前：每個框都核對過、跟下一件隔夠久，才放行。不然丟 ProjectError（列出框與靜態圖位置）。"""
    statuses = box_statuses(plan)
    bad = [s for s in statuses if not s.ready]
    if not bad:
        if statuses:
            log(f"Vox 框：{len(statuses)} 個都核對過了")
        return
    render_box_stills(plan, [s for s in bad if not s.confirmed], jobs=jobs, log=log)
    problems = ["\n".join(_status_lines(s)) for s in bad]
    unconfirmed = [s for s in bad if not s.confirmed]
    hint = []
    if unconfirmed:
        hint.append(CHECKLIST)
        hint.append("都沒問題就執行：" + cli_command("confirm-box", plan.project.root, *(s.box.token for s in unconfirmed)))
    hint.append("預覽（preview）不受影響，照樣可以跑。")
    raise ProjectError(problems, title="Vox 框還沒核對好，先不出片", hint="\n".join(hint))


# ---------------------------------------------------------------- 審片頁


def boxes_contributor(plan: BuildPlan) -> ReviewExtras:
    """審片頁：每個 Vox 分鏡卡的「自動檢查」欄列出它的框核對過沒、隔得夠不夠（預覽也會渲框的靜態圖）。"""
    from article_to_video.review import CardSection, CheckItem, ReviewExtras

    statuses = box_statuses(plan)
    render_box_stills(plan, statuses)
    cards: dict[str, list[CardSection]] = {}
    for st in statuses:
        b = st.box
        items = cards.setdefault(b.shot_id, [CardSection("checks", "Vox 框核對", [])])[0].items
        if st.confirmed:
            items.append(CheckItem(f"框 {b.index}：{b.describe()}", "Claude 看過靜態圖，框在旁白講的數字上", "ok", "已核對"))
        else:
            items.append(CheckItem(f"框 {b.index}：{b.describe()}", f"靜態圖：{st.screen.name}（出片前 Claude 要看過、核對）",
                                   "block", "還沒核對"))
        if not st.gap_ok:
            items.append(CheckItem(f"框 {b.index} 之後 {st.gap:.1f} 秒就換{st.next_what}",
                                   f"至少要隔 {MIN_GAP:g} 秒，鏡頭推近才停得住", "block"))
        for w in st.warnings:
            items.append(CheckItem(f"框 {b.index}", w, "warn"))
    return ReviewExtras(cards=cards)

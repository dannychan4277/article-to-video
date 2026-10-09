"""片段快取＋並行渲染：只重渲內容有變的片段，刪掉已經不存在的分鏡留下的舊檔。

快取鍵（sha256）＝ 快取版本 ＋ spec（cue.image、data.image 的路徑換成圖片內容的雜湊，所以專案搬家不會失效）
               ＋ 這個片段用到的引擎檔（render.py、clip.html、clip.js、clips/<語法>.js、lib/*.js、字型）。
檔案：specs/<片段>.json（實際給引擎的 spec）、clips/<片段>.mp4、clips/<片段>.sha256（渲成功才寫）、logs/render-<片段>.log。
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from article_to_video.clips import ENGINE_DIR, ClipRenderError, render_clip, run_parallel
from article_to_video.project import VariantPaths
from article_to_video.timeline import ClipPlan

__all__ = ["CACHE_VERSION", "RenderReport", "clip_hash", "clip_is_current", "clip_stamp", "content_fields",
           "engine_fingerprint", "render_planned_clips"]

CACHE_VERSION = "1"
_OUR_SUFFIXES = {".json", ".mp4", ".sha256", ".log"}


@dataclass(frozen=True)
class RenderReport:
    rendered: list[str]
    cached: list[str]
    clips: dict[str, Path]
    """片段 id → mp4，照時間順序。"""
    hashes: dict[str, str]


@functools.cache
def engine_fingerprint(grammar: str) -> str:
    files = [ENGINE_DIR / "render.py", ENGINE_DIR / "clip.html", ENGINE_DIR / "clip.js",
             ENGINE_DIR / "clips" / f"{grammar}.js"]
    files += sorted((ENGINE_DIR / "lib").glob("*.js"))
    files += sorted((ENGINE_DIR / "lib" / "fonts").glob("*.woff"))
    h = hashlib.sha256()
    for f in files:
        h.update(f.relative_to(ENGINE_DIR).as_posix().encode())
        h.update(b"\0")
        h.update(f.read_bytes())
    return h.hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_fields(fields: dict) -> dict:
    """一份 cue（或 data）的副本，image 的路徑換成圖片內容的雜湊：專案搬家不算改，圖換了才算。"""
    out = copy.deepcopy(fields)
    img = out.get("image")
    if isinstance(img, str) and img and not img.startswith(("data:", "http:", "https:")):
        p = Path(img)
        out["image"] = "sha256:" + (_file_digest(p) if p.is_file() else f"missing:{img}")
    return out


def clip_hash(plan: ClipPlan) -> str:
    spec = copy.deepcopy(plan.spec)
    spec["cues"] = [content_fields(cue) if isinstance(cue, dict) else cue for cue in spec.get("cues") or []]
    if isinstance(spec.get("data"), dict):
        spec["data"] = content_fields(spec["data"])  # data.image：片尾卡的 logo
    payload = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256()
    for part in (CACHE_VERSION, payload, engine_fingerprint(plan.grammar)):
        h.update(part.encode())
        h.update(b"\0")
    return h.hexdigest()


def clip_stamp(paths: VariantPaths, clip_id: str) -> Path:
    """片段的戳記：渲成功才寫，內容是快取鍵（clip_hash）。"""
    return paths.clips / f"{clip_id}.sha256"


def _stamped(mp4: Path, stamp: Path, key: str) -> bool:
    try:
        return mp4.is_file() and stamp.read_text(encoding="utf-8").strip() == key
    except OSError:
        return False


def clip_is_current(plan: ClipPlan, paths: VariantPaths) -> bool:
    """這個片段的 mp4 是不是照現在這份 spec（含引擎檔、圖片）渲出來的。"""
    return _stamped(paths.clips / f"{plan.id}.mp4", clip_stamp(paths, plan.id), clip_hash(plan))


def render_planned_clips(
    plans: Sequence[ClipPlan],
    paths: VariantPaths,
    *,
    ffmpeg: Path,
    jobs: int = 3,
    on_clip: Callable[[str, bool], None] | None = None,
) -> RenderReport:
    """渲（或從快取拿）每個片段。任一片段失敗就丟 ClipRenderError，訊息點名是哪個分鏡。"""
    for d in (paths.specs, paths.clips, paths.logs):
        d.mkdir(parents=True, exist_ok=True)
    keep = {p.id for p in plans}
    _remove_stale(paths, keep)

    hashes = {p.id: clip_hash(p) for p in plans}
    todo: list[ClipPlan] = []
    cached: list[str] = []
    for p in plans:
        spec_path = paths.specs / f"{p.id}.json"
        spec_path.write_text(json.dumps(p.spec, ensure_ascii=False, indent=1), encoding="utf-8")
        mp4, stamp = paths.clips / f"{p.id}.mp4", clip_stamp(paths, p.id)
        if _stamped(mp4, stamp, hashes[p.id]):
            cached.append(p.id)
            if on_clip:
                on_clip(p.id, True)
        else:
            stamp.unlink(missing_ok=True)
            todo.append(p)

    def one(p: ClipPlan) -> str:
        mp4 = paths.clips / f"{p.id}.mp4"
        log = paths.logs / f"render-{p.id}.log"
        try:
            res = render_clip(paths.specs / f"{p.id}.json", mp4, ffmpeg=ffmpeg)
        except ClipRenderError as e:
            log.write_text(e.log, encoding="utf-8")
            raise e.about(p.shot_ids, "片段") from e
        log.write_text("\n".join(res.warnings), encoding="utf-8")
        clip_stamp(paths, p.id).write_text(hashes[p.id], encoding="utf-8")
        return p.id

    rendered = run_parallel(todo, one, jobs=jobs,
                            on_done=(lambda _p, pid: on_clip(pid, False)) if on_clip else None)

    return RenderReport(rendered=rendered, cached=cached,
                        clips={p.id: paths.clips / f"{p.id}.mp4" for p in plans}, hashes=hashes)


def _remove_stale(paths: VariantPaths, keep: set[str]) -> None:
    """刪掉已經不在分鏡設定裡的片段、spec、戳記、紀錄（不然 qa 會掃到舊的）。"""
    for d in (paths.specs, paths.clips, paths.logs):
        for f in d.iterdir():
            if not f.is_file() or f.suffix not in _OUR_SUFFIXES:
                continue
            if d == paths.logs:
                if not f.name.startswith("render-"):
                    continue  # assemble.log 這類不是片段的紀錄，留著
                stem = f.stem[len("render-"):]
            else:
                stem = f.stem
            if stem not in keep:
                f.unlink()

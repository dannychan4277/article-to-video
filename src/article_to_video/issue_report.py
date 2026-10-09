"""回報問題：作者那台打包 zip（預設放桌面，用 LINE 傳給 Danny）；維護者那台從 zip 重渲出錯的分鏡。

zip 裡只放重現需要的東西：

    report.json            這包的說明：格式版本、外掛版本、專案名、出錯的分鏡、配音層、靜態圖時刻、略過了什麼
    問題描述.txt            作者怎麼說的（有給 --note 才有）
    project/               重現用的專案（解開就是一個能建置的專案資料夾）
      narration.json       旁白稿
      shots.json           分鏡設定
      settings.json        設定快照
      build/narration/<鍵>.json   配音層的時間點（沒有音檔：重現時補同長度的靜音，不用重新配音、不用連網）
    stills/<分鏡>/t<秒>.png      出錯分鏡的靜態圖（渲不出來就寫原因在 report.json，不會讓打包失敗）

不放：文章全文與原檔（article/）、成片、片段、配音音檔、其他檔案。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import wave
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import Any

from article_to_video.environment import collect_environment
from article_to_video.project import ProjectError, ProjectPaths, load_project

__all__ = [
    "REPORT_FORMAT",
    "RunRecord",
    "ReportError",
    "ReportResult",
    "ReproduceResult",
    "create_report",
    "default_report_dir",
    "redact",
    "reproduce_report",
]

REPORT_FORMAT = 1
NOTE_FILE = "問題描述.txt"
MANIFEST_FILE = "report.json"
README_FILE = "讀我.txt"
ENV_JSON = "environment.json"
ENV_TEXT = "environment.txt"
PROJECT_DIR = "project"
STILLS_DIR = "stills"
VARIANT = "16x9"

Log = Callable[[str], None]


class ReportError(RuntimeError):
    """打包或重現本身出了問題（不是被回報的那個問題）。"""


def default_report_dir() -> Path:
    return Path.home() / "Desktop"


def _quiet(_: str) -> None:
    pass


def _plugin_version() -> str:
    try:
        return metadata.version("article-to-video")
    except metadata.PackageNotFoundError:
        return "unknown"


# ---------------------------------------------------------------- 最後一次執行的紀錄


LAST_RUN_JSON = "last-run.json"
LAST_RUN_LOG = "last-run.log"
_LOG_LIMIT = 1_000_000
"""每份紀錄最多打包這麼多位元組（留最後面），zip 才不會大到 LINE 傳不動。"""


class RunRecord:
    """記下一次指令（例如 build）印出來的每一行和結果，寫在 <專案>/build/last-run.{log,json}，回報問題時打包。

    用法（cli.py 的 build）：
        rec = RunRecord(專案, "build", {"tts": …})
        build_project(…, log=rec.tee(print))
        rec.finish()            # 成功
        rec.finish(e)           # 已知錯誤（繁中訊息）
        rec.finish(e, unexpected=True)   # 程式錯誤：連 traceback 一起記
    專案資料夾不存在就什麼都不記；寫紀錄失敗也不影響指令本身。
    """

    def __init__(self, project_dir: str | os.PathLike | None, command: str, options: dict[str, Any] | None = None):
        root = Path(project_dir).absolute() if project_dir else None
        self.build_dir = ProjectPaths(root).build if root is not None and root.is_dir() else None
        self.command = command
        self.options = {k: v for k, v in (options or {}).items() if v is not None}
        self.started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.lines: list[str] = []

    def tee(self, fn: Log) -> Log:
        def log(msg: str) -> None:
            self.lines.append(msg)
            fn(msg)

        return log

    def finish(self, error: BaseException | None = None, *, unexpected: bool = False) -> None:
        if self.build_dir is None:
            return
        text = list(self.lines)
        info: dict[str, Any] = {
            "command": self.command, "options": self.options, "started": self.started,
            "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ok": error is None,
            "pluginVersion": _plugin_version(),
        }
        if error is not None:
            info["errorType"] = type(error).__name__
            info["error"] = str(error)
            text.append(f"失敗：{error}")
            if unexpected:
                import traceback

                info["unexpected"] = True
                text.append("".join(traceback.format_exception(error)))
        try:
            self.build_dir.mkdir(parents=True, exist_ok=True)
            (self.build_dir / LAST_RUN_LOG).write_text("\n".join(text) + "\n", encoding="utf-8")
            (self.build_dir / LAST_RUN_JSON).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass


def _last_run(paths: ProjectPaths) -> dict[str, Any]:
    try:
        info = json.loads((paths.build / LAST_RUN_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def _shots_named_in(text: str, ids: Sequence[str]) -> list[str]:
    """錯誤訊息裡點名的分鏡（訊息一律寫成「分鏡 <id>」），照分鏡順序。"""
    named = set(re.findall(r"分鏡 ([A-Za-z0-9_-]+)", text))
    return [i for i in ids if i in named]


def _read_log(path: Path) -> str:
    data = path.read_bytes()
    if len(data) > _LOG_LIMIT:
        data = "（前面太長，截掉了）\n".encode() + data[-_LOG_LIMIT:]
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 配音層的時間點


def _narration_metas(paths: ProjectPaths) -> list[tuple[Path, dict]]:
    out = []
    cache = paths.narration_cache
    if not cache.is_dir():
        return out
    for meta in sorted(cache.glob("*.json")):
        if meta.name == "timing.json":
            continue
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(info, dict) and "engine" in info:
            out.append((meta, info))
    return out


def _engine_with_full_cache(paths: ProjectPaths) -> tuple[str | None, list[Path]]:
    """找一個「每段旁白都有快取」的配音層：先看設定裡的，再看快取裡出現過的。回傳 (名字, 那些段的時間點檔)。

    打包與重現都只用快取裡的時間點，不會重新配音（不連網、跟作者那台的時間一模一樣）。
    """
    from article_to_video.narration import _cache_key

    try:
        project = load_project(paths.root)
    except ProjectError:
        return None, []
    s = project.settings
    metas = _narration_metas(paths)
    names = [s.tts] + sorted({info["engine"] for _, info in metas} - {s.tts})
    cache = paths.narration_cache
    for name in names:
        files = [cache / f"{_cache_key(name, s.voice, s.rate, text)}.json" for text in project.segments.values()]
        if all(f.is_file() and f.with_suffix(".wav").is_file() for f in files):
            return name, files
    return None, []


def _write_silence(path: Path, seconds: float, rate: int = 48000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * round(seconds * rate))


# ---------------------------------------------------------------- 個資：路徑、使用者名稱、只帶用得到的圖

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif", ".bmp", ".tif", ".tiff"})
EXTERNAL_IMAGES = "_外部圖片"
_USERS_RE = re.compile(r"/Users/(?!Shared(?:/|$))[^/\s\"'\\:]+")


def redact(text: str, *, username: bool = True) -> str:
    """家目錄換成 ~、其他 /Users/<名字> 換成 /Users/<使用者>；username=True 時連單獨出現的使用者名稱也換掉。"""
    import getpass

    home = str(Path.home())
    for h in sorted({home, os.path.realpath(home)}, key=len, reverse=True):
        if len(h) > 1:
            text = text.replace(h, "~")
    text = _USERS_RE.sub("/Users/<使用者>", text)
    if username:
        try:
            user = getpass.getuser()
        except Exception:  # noqa: BLE001
            user = ""
        if len(user) >= 3:
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])", "<使用者>", text)
    return text


def _raw_segment_texts(paths: ProjectPaths) -> set[str]:
    try:
        segs = json.loads(paths.narration.read_text(encoding="utf-8")).get("segments")
    except (OSError, ValueError, AttributeError):
        return set()
    return {v for v in segs.values() if isinstance(v, str)} if isinstance(segs, dict) else set()


def _pack_images(paths: ProjectPaths, target: Sequence[str]) -> tuple[str, dict[str, Path], dict[str, str]]:
    """出錯分鏡引用到的圖片（任何指到圖片檔的字串）→ 要打包的圖。

    專案裡的圖照原本的相對路徑放；專案外的圖放進 project/_外部圖片/，打包的分鏡設定改指那裡。
    回傳 (要打包的 shots.json 文字, {project/ 底下的路徑: 原檔}, {新路徑: 原路徑（遮過）})。
    """
    original = paths.shots.read_text(encoding="utf-8")
    if not target:
        return original, {}, {}
    try:
        doc = json.loads(original)
        shots = doc["shots"]
    except (ValueError, KeyError, TypeError):
        return original, {}, {}
    root = paths.root.resolve()
    images: dict[str, Path] = {}
    external: dict[str, str] = {}
    changed = False

    def visit(v: Any) -> Any:
        nonlocal changed
        if isinstance(v, dict):
            return {k: visit(x) for k, x in v.items()}
        if isinstance(v, list):
            return [visit(x) for x in v]
        if not isinstance(v, str) or not v or "\n" in v or len(v) > 1024:
            return v
        p = Path(v).expanduser()
        p = p if p.is_absolute() else paths.root / p
        try:
            if p.suffix.lower() not in IMAGE_SUFFIXES or not p.is_file():
                return v
            real = p.resolve()
        except OSError:
            return v
        if real.is_relative_to(root):
            rel = real.relative_to(root).as_posix()
            images[rel] = real
            if rel != v:
                changed = True
            return rel
        rel = f"{EXTERNAL_IMAGES}/{len(external) + 1}-{real.name}"
        images[rel] = real
        external[rel] = redact(str(real))
        changed = True
        return rel

    for i, shot in enumerate(shots):
        if isinstance(shot, dict) and str(shot.get("id")) in target:
            shots[i] = visit(shot)
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n" if changed else original
    return text, images, external


# ---------------------------------------------------------------- 出錯的分鏡


def _raw_shots(paths: ProjectPaths) -> list[dict]:
    try:
        data = json.loads(paths.shots.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    shots = data.get("shots") if isinstance(data, dict) else None
    return [s for s in shots if isinstance(s, dict)] if isinstance(shots, list) else []


def _resolve_shots(wanted: Sequence[str], raw: list[dict], warnings: list[str]) -> list[str]:
    """分鏡 id（SH03）或編號（3）→ 分鏡 id，照分鏡順序、不重複。"""
    ids = [str(s.get("id")) for s in raw]
    found: set[str] = set()
    for w in wanted:
        w = str(w).strip()
        if w in ids:
            found.add(w)
        elif w.isdigit() and 1 <= int(w) <= len(ids):
            found.add(ids[int(w) - 1])
        else:
            warnings.append(f"找不到分鏡「{w}」（分鏡設定裡有：{'、'.join(ids) or '沒有分鏡'}）")
    return [i for i in ids if i in found]


def _still_times(offset: int, frames: int, fps: int) -> list[float]:
    """一個分鏡取三張：四分之一、一半、最後一幀（片段內秒數，取到小數兩位＝靜態圖檔名的精度）。"""
    picks = [offset + frames * 0.25, offset + frames * 0.5, offset + frames - 1]
    return sorted({round(f / fps, 2) for f in picks})


def _shot_clips(plan: Any, shot_ids: Sequence[str]) -> dict[str, tuple[Any, list[float]]]:
    """分鏡 id → (它所在的片段計畫, 這個分鏡的靜態圖時刻)。"""
    timings = {st.shot.id: st for st in plan.timeline.shots}
    out = {}
    for sid in shot_ids:
        clip = next((c for c in plan.clips if sid in c.shot_ids), None)
        st = timings.get(sid)
        if clip is None or st is None:
            continue
        out[sid] = (clip, _still_times(st.start_frame - clip.start_frame, st.end_frame - st.start_frame, plan.timeline.fps))
    return out


# ---------------------------------------------------------------- 作者那台：打包


@dataclass(frozen=True)
class ReportResult:
    zip_path: Path
    shots: list[str]
    """出錯的分鏡 id。"""
    stills: dict[str, list[str]] = field(default_factory=dict)
    """分鏡 id → 這包裡的靜態圖檔名。"""
    contents: list[str] = field(default_factory=list)
    """給作者看的「裡面有什麼」。"""
    warnings: list[str] = field(default_factory=list)

    @property
    def size_mb(self) -> float:
        return self.zip_path.stat().st_size / 1e6


def create_report(
    project_dir: str | os.PathLike | None,
    *,
    shots: Sequence[str] = (),
    note: str = "",
    out_dir: str | os.PathLike | None = None,
    log: Log = _quiet,
) -> ReportResult:
    """打包回報用的 zip，回傳它在哪裡。除了寫不進 zip，什麼錯都不會讓打包失敗（錯誤本身就是要回報的東西）。

    shots：出錯的分鏡（id 或編號）；不給就從最後一次建置的錯誤訊息找。note：作者怎麼描述這個問題。
    project_dir 為 None（例如安裝就失敗了）時只打包環境檢查。
    """
    out = Path(out_dir) if out_dir is not None else default_report_dir()
    out.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    paths = ProjectPaths(Path(project_dir).absolute()) if project_dir else None
    if paths is not None and not paths.root.is_dir():
        warnings.append(f"找不到專案資料夾「{redact(str(paths.root))}」，只打包了環境檢查")
        paths = None
    name = paths.root.name if paths else "沒有專案"
    zip_path = out / f"回報問題-{name}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    manifest: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "pluginVersion": _plugin_version(),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "project": name if paths else None,
        "shots": [],
        "tts": None,
        "stills": {},
        "warnings": warnings,
    }
    files: dict[str, bytes] = {}

    def add_text(arcname: str, text: str) -> None:
        # 專案檔是內容（旁白、標題），只遮路徑；紀錄、環境這些連單獨出現的使用者名稱也遮
        files[arcname] = redact(text, username=not arcname.startswith(f"{PROJECT_DIR}/")).encode("utf-8")

    stills_out: dict[str, list[str]] = {}
    target: list[str] = []
    if paths is not None:
        raw = _raw_shots(paths)
        last = _last_run(paths)
        if shots:
            target = _resolve_shots(shots, raw, warnings)
        elif last.get("ok") is False:
            target = _shots_named_in(str(last.get("error", "")), [str(s.get("id")) for s in raw])
        manifest["shots"] = target
        manifest["lastRun"] = last or None
        for f in (paths.narration, paths.settings):
            if f.is_file():
                add_text(f"{PROJECT_DIR}/{f.name}", f.read_text(encoding="utf-8"))
        if paths.shots.is_file():
            shots_text, images, external = _pack_images(paths, target)
            add_text(f"{PROJECT_DIR}/{paths.shots.name}", shots_text)
            for arcname, src in images.items():
                files[f"{PROJECT_DIR}/{arcname}"] = src.read_bytes()
            if external:
                manifest["externalImages"] = external
        for variant in sorted(paths.build.glob("*/timeline.json")) if paths.build.is_dir() else []:
            vdir = variant.parent
            add_text(f"{PROJECT_DIR}/build/{vdir.name}/timeline.json", variant.read_text(encoding="utf-8"))
            for spec in sorted((vdir / "specs").glob("*.json")):
                add_text(f"{PROJECT_DIR}/build/{vdir.name}/specs/{spec.name}", spec.read_text(encoding="utf-8"))

        # 錯誤紀錄：最後一次執行的輸出＋每個畫面比例的建置紀錄（片段渲染、組裝）
        if (paths.build / LAST_RUN_LOG).is_file():
            add_text(f"errors/{LAST_RUN_LOG}", _read_log(paths.build / LAST_RUN_LOG))
        if paths.build.is_dir():
            for logs in sorted(paths.build.glob("*/logs")):
                for f in sorted(logs.glob("*.log")):
                    add_text(f"errors/logs-{logs.parent.name}/{f.name}", _read_log(f))

        engine, metas = _engine_with_full_cache(paths)
        manifest["tts"] = engine
        if engine is None:  # 專案本身讀不進來：把跟旁白對得上的時間點都帶著，維護者修好設定後還用得到
            texts = _raw_segment_texts(paths)
            metas = [m for m, info in _narration_metas(paths) if info.get("text") in texts]
        for meta in metas:
            add_text(f"{PROJECT_DIR}/build/narration/{meta.name}", meta.read_text(encoding="utf-8"))

        if target:
            with tempfile.TemporaryDirectory(prefix="atv-report-") as tmp:
                stills_out = _render_report_stills(paths, engine, target, Path(tmp), files, manifest, log)

    log("檢查環境…")
    env = collect_environment()
    add_text(ENV_JSON, json.dumps(env.to_json(), ensure_ascii=False, indent=1))
    add_text(ENV_TEXT, env.to_text())

    if note:
        add_text(NOTE_FILE, note.strip() + "\n")
    for sid in target:
        err = (manifest["stills"].get(sid) or {}).get("error") or manifest.get("stillsError")
        if sid not in stills_out and err:
            warnings.append(f"分鏡 {sid} 的靜態圖渲不出來（原因記在 zip 裡）：{err.splitlines()[0]}")
    add_text(MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False, indent=1))
    add_text(README_FILE, _readme(manifest))

    tmp_zip = zip_path.with_name(zip_path.name + ".part")
    with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for arcname, data in files.items():
            z.writestr(arcname, data)
    os.replace(tmp_zip, zip_path)
    return ReportResult(zip_path=zip_path, shots=target, stills=stills_out,
                        contents=_summary(files, stills_out), warnings=warnings)


def _summary(files: dict[str, bytes], stills: dict[str, list[str]]) -> list[str]:
    have = set(files)
    out = []
    for arc, label in ((f"{PROJECT_DIR}/narration.json", "旁白稿"), (f"{PROJECT_DIR}/shots.json", "分鏡設定"),
                       (f"{PROJECT_DIR}/settings.json", "設定")):
        if arc in have:
            out.append(label)
    logs = [n for n in have if n.startswith("errors/")]
    if logs:
        out.append(f"錯誤紀錄 {len(logs)} 份")
    for sid, names in stills.items():
        out.append(f"分鏡 {sid} 的靜態圖 {len(names)} 張")
    images = [n for n in have if n.startswith(f"{PROJECT_DIR}/") and Path(n).suffix.lower() in IMAGE_SUFFIXES]
    if images:
        out.append(f"出錯分鏡用到的圖 {len(images)} 張")
    out.append("環境檢查")
    if NOTE_FILE in have:
        out.append("問題描述")
    return out


def _readme(manifest: dict[str, Any]) -> str:
    tts = manifest.get("tts")
    build_tts = f" --tts {tts}" if tts else ""
    shots = "、".join(manifest.get("shots") or []) or "（沒有指定）"
    return f"""這是 article-to-video「回報問題」打包的檔案，給維護者重現問題用。

出錯的分鏡：{shots}
外掛版本：{manifest.get("pluginVersion")}（要完全一樣的結果，先切到這個版本）

重現（在外掛的 repo 裡跑）：
  uv run --project <repo> python -m article_to_video reproduce-issue <這個 zip>
會解開到 zip 旁邊的同名資料夾：
  project/   作者的專案；旁白時間點用作者那台的紀錄（補上同長度的靜音），不重新配音、不用連網
  重現/      重渲出來的片段（clips/）和靜態圖（stills/），可以跟 zip 裡 stills/ 的作者那台比對
整支重建：
  uv run --project <repo> python -m article_to_video build <解開的資料夾>/project{build_tts}

其他檔案：
  report.json        這包的說明（出錯分鏡、靜態圖時刻、最後一次建置的結果、略過了什麼）
  問題描述.txt        作者怎麼說的
  errors/            最後一次執行的輸出（last-run.log）與建置紀錄（片段渲染、組裝）
  environment.*      作者那台的環境檢查
  stills/            作者那台渲的出錯分鏡靜態圖

沒放：文章全文與原檔、成片、片段、配音音檔；路徑裡的家目錄與使用者名稱已遮掉。
"""


def _render_report_stills(
    paths: ProjectPaths, engine: str | None, target: list[str], tmp: Path,
    files: dict[str, bytes], manifest: dict[str, Any], log: Log,
) -> dict[str, list[str]]:
    """渲出錯分鏡的靜態圖。渲不出來（可能正是要回報的問題）就把原因寫進 report.json，不讓打包失敗。"""
    from article_to_video.build import plan_build
    from article_to_video.clips import render_stills

    if engine is None:
        manifest["stillsError"] = "配音還沒做完（或旁白改過後還沒重建），算不出分鏡的時間，所以沒有靜態圖"
        return {}
    try:
        plan = plan_build(paths.root, tts=engine)
    except Exception as e:  # noqa: BLE001 — 什麼錯都記下來，打包照樣完成
        manifest["stillsError"] = f"算分鏡時間時出錯：{e}"
        return {}

    out: dict[str, list[str]] = {}
    for sid, (clip, times) in _shot_clips(plan, target).items():
        manifest["stills"][sid] = {"clip": clip.id, "times": times}
        log(f"渲 {sid} 的靜態圖…")
        try:
            res = render_stills(clip.spec, tmp / sid, times, base_dir=paths.root)
        except Exception as e:  # noqa: BLE001
            manifest["stills"][sid]["error"] = str(e)
            continue
        names = []
        for p in res.paths:
            files[f"{STILLS_DIR}/{sid}/{p.name}"] = p.read_bytes()
            names.append(p.name)
        out[sid] = names
    return out


# ---------------------------------------------------------------- 維護者那台：重現


@dataclass(frozen=True)
class ReproduceResult:
    out_dir: Path
    project_dir: Path
    clips: dict[str, Path] = field(default_factory=dict)
    stills: dict[str, list[Path]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    """重現出來的錯誤（建置會報的那些繁中訊息）。"""
    notes: list[str] = field(default_factory=list)
    """維護者要注意的事：版本不同、spec 跟作者那台不同、改用假配音……"""
    report_note: str = ""
    """作者的問題描述。"""
    author_environment: str = ""
    """作者那台的環境檢查（文字版）。"""

    @property
    def ok(self) -> bool:
        return not self.errors


def _comparable(spec: Any) -> Any:
    """比對 spec 用：圖片路徑只看檔名（兩台電腦的路徑本來就不同）。"""
    if isinstance(spec, dict):
        return {k: (Path(v).name if k == "image" and isinstance(v, str) else _comparable(v)) for k, v in spec.items()}
    if isinstance(spec, list):
        return [_comparable(x) for x in spec]
    return spec


def _safe_extract(z: zipfile.ZipFile, dest: Path) -> None:
    for info in z.infolist():
        p = PurePosixPath(info.filename)
        if p.is_absolute() or ".." in p.parts or info.filename.startswith("\\"):
            raise ReportError(f"zip 裡有不安全的路徑：{info.filename}")
    z.extractall(dest)


def reproduce_report(
    zip_path: str | os.PathLike,
    *,
    out_dir: str | os.PathLike | None = None,
    shots: Sequence[str] = (),
    log: Log = _quiet,
) -> ReproduceResult:
    """解開回報的 zip，用作者那台的旁白時間點重算分鏡，重渲出錯分鏡的片段與靜態圖（全程不連網）。"""
    from article_to_video.build import plan_build
    from article_to_video.clips import render_clip, render_stills

    zpath = Path(zip_path)
    if not zpath.is_file():
        raise ReportError(f"找不到回報檔：{zpath}")
    out = Path(out_dir) if out_dir is not None else zpath.with_suffix("")
    if out.exists() and any(out.iterdir()):
        if not (out / MANIFEST_FILE).is_file():
            raise ReportError(f"{out} 已經有別的東西了，換一個 --out 資料夾")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        _safe_extract(z, out)
    try:
        manifest = json.loads((out / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ReportError(f"這不是回報問題打包的 zip（讀不到 {MANIFEST_FILE}：{e}）") from e
    project_dir = out / PROJECT_DIR
    paths = ProjectPaths(project_dir)

    def read(name: str) -> str:
        f = out / name
        return f.read_text(encoding="utf-8") if f.is_file() else ""

    errors: list[str] = []
    notes: list[str] = []
    base = {"report_note": read(NOTE_FILE).strip(), "author_environment": read(ENV_TEXT).strip()}
    if manifest.get("format") != REPORT_FORMAT:
        notes.append(f"這包的格式版本是 {manifest.get('format')}，這版的重現認得 {REPORT_FORMAT}")
    if manifest.get("pluginVersion") != _plugin_version():
        notes.append(f"作者那台的外掛是 {manifest.get('pluginVersion')}，這台是 {_plugin_version()}：結果可能不同")
    if not paths.shots.is_file():
        errors.append("這包沒有專案（作者打包時沒有專案資料夾），只能看環境檢查與紀錄")
        return ReproduceResult(out, project_dir, errors=errors, notes=notes, **base)

    for meta, info in _narration_metas(paths):
        _write_silence(meta.with_suffix(".wav"), float(info["duration"]))
    engine, _ = _engine_with_full_cache(paths)
    if engine is None:
        engine = "fake"
        notes.append("這包沒有完整的配音時間點，改用假配音層：分鏡時間可能跟作者那台不一樣")
    try:
        plan = plan_build(project_dir, tts=engine, log=log)
    except Exception as e:  # noqa: BLE001 — 這就是重現出來的錯
        errors.append(str(e))
        return ReproduceResult(out, project_dir, errors=errors, notes=notes, **base)

    warnings: list[str] = []
    target = _resolve_shots(shots, _raw_shots(paths), warnings) if shots else list(manifest.get("shots") or [])
    notes += warnings
    if not target:
        target = [st.shot.id for st in plan.timeline.shots]
        notes.append("回報裡沒有指定出錯的分鏡，全部重渲")

    work = out / "重現"
    clips: dict[str, Path] = {}
    failed: set[str] = set()
    stills: dict[str, list[Path]] = {}
    recorded_times = manifest.get("stills") or {}
    for sid, (clip, times) in _shot_clips(plan, target).items():
        times = (recorded_times.get(sid) or {}).get("times") or times
        if clip.id in failed:
            continue
        if clip.id not in clips:
            recorded = paths.variant(plan.format.name).specs / f"{clip.id}.json"
            try:
                if recorded.is_file() and _comparable(json.loads(recorded.read_text(encoding="utf-8"))) != _comparable(clip.spec):
                    notes.append(f"片段 {clip.id} 的 spec 跟作者那台最後一次建置的不一樣（外掛版本不同，或作者改過分鏡後還沒重建）")
            except ValueError:
                pass
            mp4 = work / "clips" / f"{clip.id}.mp4"
            mp4.parent.mkdir(parents=True, exist_ok=True)
            log(f"重渲片段 {clip.id}…")
            try:
                render_clip(clip.spec, mp4, base_dir=project_dir)
                clips[clip.id] = mp4
            except Exception as e:  # noqa: BLE001 — 這就是重現出來的錯
                failed.add(clip.id)
                errors.append(f"分鏡 {'、'.join(clip.shot_ids)} 的片段渲染失敗：{e}")
                continue
        try:
            res = render_stills(clip.spec, work / "stills" / sid, times, base_dir=project_dir)
            stills[sid] = res.paths
        except Exception as e:  # noqa: BLE001
            errors.append(f"分鏡 {sid} 的靜態圖渲染失敗：{e}")
    return ReproduceResult(out, project_dir, clips=clips, stills=stills, errors=errors, notes=notes, **base)

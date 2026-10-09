"""自動檢查：出片之後、作者看審片頁之前，機器先把每個片段看一遍。

  run_checks(plan)            每個片段跑 qa、白底片段跑字幕帶檢查（有快取、並行），彙整成 CheckReport，
                              寫 build/<比例>/checks.json。指令列：python -m article_to_video checks <專案>。

qa（內嵌引擎的 engine/qa.py）：在 Chromium 裡把片段的 spec 逐幀重渲一次（30 fps 取樣），量確定性、動態、
跳變、框景（半截字、疊字、字進字幕帶、近空白）。qa 自己只在頁面報錯時失敗，門檻在這裡判（THRESHOLDS 那張表）。
為了快，qa 照比例縮小渲（短邊 QA_SHORT_SIDE＝540 px；引擎依畫面寬度等比排版，量的是比例，結果跟 1080p 一樣）。

每項結果是 ok／warn／fail：fail＝必修（沒處理就擋正式出片），warn＝該修（審片頁標出來，不擋）。

快取：qa 的結果放 build/<比例>/checks/qa/<片段>/（qa.json、qa.md、<片段>.jpg 四幀拼圖＋運動熱圖、stamp.json），
鍵＝QA_VERSION＋片段雜湊（spec＋引擎檔＋圖片）＋取樣設定＋qa.py 本身；片段沒變就不重跑。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from article_to_video.clip_cache import clip_hash
from article_to_video.clips import QA_SCRIPT, _absolutize_images
from article_to_video.ffmpeg import find_ffmpeg

if TYPE_CHECKING:
    from article_to_video.build import BuildPlan
    from article_to_video.timeline import ClipPlan

__all__ = ["CHECK_LABELS", "CheckReport", "ClipCheck", "Finding", "run_checks"]

Log = Callable[[str], None]

QA_VERSION = "1"
QA_SHORT_SIDE = 540
"""qa 渲的畫面短邊（px）：1080p 的片段縮一半渲，快 2～3 倍，量到的東西一樣（比例）。"""
QA_FPS = 30

CHART = "t3_finance_chart"
VOX = "y2_vox"
CARD = "a2v_end_card"

CHECK_LABELS = {
    "page-error": "頁面報錯",
    "determinism": "確定性",
    "motion": "動態",
    "jump": "跳變",
    "cut-text": "半截字",
    "overlap": "疊字",
    "text-in-band": "字進字幕帶",
    "blank": "近空白",
    "band": "字幕帶",
    "hold": "停太久",
}

# 動態：相鄰兩幀幾乎沒變（< 0.05% 像素）的幀對占幾 %。財經圖表平常一直在慢推，實測 3～17%；
# 不推、畫完就停的圖 80%。Vox（一拍二，12 fps 動畫）平常就 40～50%，片尾卡本來就是停著的：這兩種不判。
STILL_FAIL = 60.0
STILL_WARN = 40.0
DET_FAIL = 24
"""同一幀渲三次，像素最多差多少以內只算「要看一下」（GPU 反鋸齒的小差異）；超過＝畫面內容本身會變。"""
FRAMING_FAIL = 0.5
"""框景（半截字、疊字、字進字幕帶）持續這麼久以上算 fail；qa 只報 ≥ 0.3 秒的，0.3～0.5 秒是過場時一閃，算 warn。"""
FRAMING_LONG = 1.0
"""近空白、Vox 的字進字幕帶：持續這麼久以上才算 fail。"""

# 字幕帶（subzone_gate.py 的做法，改成任何畫面大小都能用）：每秒 10 幀，取字幕帶那一條、縮一半、轉灰階，
# 灰階 < 160 的像素超過字幕帶面積的 0.195%（原版：縮半後 960×80 的帶子超過 150 個像素）就算這幀有東西壓進去。
# 原版門檻灰階 < 95 是給白板深色筆畫用的；財經圖表的來源列是灰字（#7c868b，灰階約 131），推太近掉進字幕帶時
# 原版抓不到（實測），所以放寬到 160：白底上看得見的字和圖都算，格線（白底上約 223）不算。
# 只查白底的片段：財經圖表（字幕帶＝spec.safe.bottom）、片尾卡（沒有字幕，只在直式查平台介面區）。
# Vox 的牛皮紙紋理會大量誤報，靠 qa 的框景。
BAND_VERSION = "1"
BAND_FPS = 10
BAND_DARK = 160
BAND_RATIO = 150 / (960 * 80)
BAND_FAIL = 0.3
"""壓進字幕帶累計這麼多秒以上算 fail（過場一閃 0.1～0.2 秒算 warn）。"""
HOLD_WARN = 5.0
"""旁白期間超過這麼多秒沒有新的畫面事件就提醒（審片規則：一句旁白一個畫面事件，6 秒沒新資訊就算靜止）。"""


def _quiet(_: str) -> None:
    pass


# ---------------------------------------------------------------- 結果


@dataclass(frozen=True)
class Finding:
    """一項檢查沒過（fail）或要看一下（warn）。"""

    token: str
    """代號（接受這一項時用）：qa:<片段>:<檢查>@<片段雜湊前 8 碼>。片段改了代號就變。"""
    source: str
    """"qa"、"band"（字幕帶）、"hold"（停太久）。"""
    check: str
    """CHECK_LABELS 的鍵。"""
    status: str
    """"fail"（必修，擋出片）或 "warn"（該修）。"""
    clip_id: str
    shot_ids: tuple[str, ...]
    times: tuple[tuple[float, float], ...]
    """成片裡的時段（秒）。"""
    message: str

    @property
    def label(self) -> str:
        return CHECK_LABELS[self.check]


@dataclass(frozen=True)
class ClipCheck:
    clip_id: str
    shot_ids: tuple[str, ...]
    grammar: str
    start: float
    end: float
    hash: str
    qa: dict[str, Any] | None
    """qa.json 裡這個片段的數字（加上 page_errors）；qa 沒跑完時有 "error"。"""
    qa_dir: Path
    band: dict[str, Any] | None = None
    """字幕帶檢查：{"top": 帶子上緣 px, "hits": [[起, 迄, 最多幾個深色像素], …]（片段內秒）, "seconds": 累計秒數}；
    不用查的片段（Vox、橫式片尾卡）是 None；片段還沒渲時有 "error"。"""


@dataclass(frozen=True)
class CheckReport:
    format: str
    clips: list[ClipCheck]
    findings: list[Finding]
    seconds: float = 0.0
    ran: list[str] = field(default_factory=list)
    """這次真的跑了 qa 的片段。"""
    cached: list[str] = field(default_factory=list)
    """沿用快取的片段。"""


# ---------------------------------------------------------------- qa


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _qa_scale(spec: dict[str, Any]) -> float:
    short = min(int(spec.get("width", 1920)), int(spec.get("height", 1080)))
    return min(1.0, QA_SHORT_SIDE / short)


def _qa_spec(cp: ClipPlan, project_root: Path) -> dict[str, Any]:
    spec = _absolutize_images(copy.deepcopy(cp.spec), project_root)
    s = _qa_scale(spec)
    if s < 1.0:
        spec["width"] = round(int(spec.get("width", 1920)) * s / 2) * 2
        spec["height"] = round(int(spec.get("height", 1080)) * s / 2) * 2
        if isinstance(spec.get("safe"), dict):
            spec["safe"] = {k: round(v * s) for k, v in spec["safe"].items()}
    return spec


def _qa_key(cp: ClipPlan) -> str:
    h = hashlib.sha256()
    for part in (QA_VERSION, clip_hash(cp), str(_qa_scale(cp.spec)), str(QA_FPS), _file_digest(QA_SCRIPT)):
        h.update(part.encode())
        h.update(b"\0")
    return h.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_qa(out: Path) -> dict[str, Any] | None:
    report = _read_json(out / "qa.json")
    if not isinstance(report, dict) or not report.get("segments"):
        return None
    rec = dict(report["segments"][0])
    rec["page_errors"] = list(report.get("page_errors") or [])
    return rec


def _run_qa(cp: ClipPlan, out: Path, project_root: Path, key: str) -> dict[str, Any]:
    """跑一次 qa.py（spec 放在以片段為名的資料夾，qa 用資料夾名當段名）。"""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    spec_path = out / "clip.json"
    spec_path.write_text(json.dumps(_qa_spec(cp, project_root), ensure_ascii=False, indent=1), encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        proc = subprocess.run(
            [sys.executable, str(QA_SCRIPT), "--spec", str(spec_path), "--out", str(out), "--fps", str(QA_FPS)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=1800,
        )
        log = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as e:
        log = f"qa 跑超過 {e.timeout:.0f} 秒沒結束"
    (out / "qa.log").write_text(log, encoding="utf-8")
    rec = _load_qa(out)
    if rec is None:
        tail = "\n".join(log.strip().splitlines()[-6:])
        rec = {"error": tail or "qa 沒有產出結果"}
    (out / "stamp.json").write_text(json.dumps({"key": key, "result": rec}, ensure_ascii=False), encoding="utf-8")
    return rec


def _qa_dir(plan: BuildPlan) -> Path:
    return plan.paths.root / "checks" / "qa"


def _cached_qa(out: Path, key: str) -> dict[str, Any] | None:
    stamp = _read_json(out / "stamp.json")
    if isinstance(stamp, dict) and stamp.get("key") == key and isinstance(stamp.get("result"), dict):
        return stamp["result"]
    return None


# ---------------------------------------------------------------- 字幕帶


def _band_px(cp: ClipPlan) -> int:
    """這個片段要查的底部帶子高度（px，成片尺寸）；0＝不查。"""
    if cp.grammar not in (CHART, CARD):
        return 0
    return int((cp.spec.get("safe") or {}).get("bottom", 0) or 0)


def _band_key(cp: ClipPlan) -> str:
    payload = [BAND_VERSION, clip_hash(cp), _band_px(cp), BAND_FPS, BAND_DARK, BAND_RATIO]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def _clip_is_current(plan: BuildPlan, cp: ClipPlan) -> bool:
    mp4, stamp = plan.paths.clips / f"{cp.id}.mp4", plan.paths.clips / f"{cp.id}.sha256"
    try:
        return mp4.is_file() and stamp.read_text(encoding="utf-8").strip() == clip_hash(cp)
    except OSError:
        return False


def _scan_band(plan: BuildPlan, cp: ClipPlan, ffmpeg: Path) -> dict[str, Any]:
    import numpy as np

    W, H = int(cp.spec["width"]), int(cp.spec["height"])
    band = _band_px(cp)
    top = H - band
    w, h = max(2, W // 2 // 2 * 2), max(2, band // 2 // 2 * 2)
    proc = subprocess.run(
        [str(ffmpeg), "-v", "error", "-i", str(plan.paths.clips / f"{cp.id}.mp4"),
         "-vf", f"fps={BAND_FPS},crop={W}:{band}:0:{top},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return {"top": top, "error": proc.stderr.decode("utf-8", "replace").strip()[-300:]}
    frames = np.frombuffer(proc.stdout, np.uint8).reshape(-1, h, w)
    limit = BAND_RATIO * w * h
    counts = [int((f < BAND_DARK).sum()) for f in frames]
    hits: list[list[float]] = []
    for i, n in enumerate(counts):
        if n <= limit:
            continue
        t = i / BAND_FPS
        if hits and t - hits[-1][1] <= 1.5 / BAND_FPS:
            hits[-1][1] = t
            hits[-1][2] = max(hits[-1][2], n)
        else:
            hits.append([t, t, n])
    spans = [[round(a, 3), round(b + 1 / BAND_FPS, 3), int(n)] for a, b, n in hits]
    return {"top": top, "hits": spans, "seconds": round(sum(1 for n in counts if n > limit) / BAND_FPS, 2),
            "frames": len(counts)}


def _check_band(plan: BuildPlan, cp: ClipPlan, ffmpeg: Path | None) -> dict[str, Any] | None:
    if not _band_px(cp):
        return None
    path = plan.paths.root / "checks" / "band" / f"{cp.id}.json"
    key = _band_key(cp)
    cached = _read_json(path)
    if isinstance(cached, dict) and cached.get("key") == key and isinstance(cached.get("result"), dict):
        return cached["result"]
    if ffmpeg is None or not _clip_is_current(plan, cp):
        return {"top": int(cp.spec["height"]) - _band_px(cp), "error": "這個片段還沒渲好（先出片再檢查）"}
    result = _scan_band(plan, cp, ffmpeg)
    if "error" not in result:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"key": key, "result": result}, ensure_ascii=False), encoding="utf-8")
    return result


def _band_findings(cc: ClipCheck) -> list[tuple[str, str, list[tuple[float, float]], str]]:
    b = cc.band
    if not b:
        return []
    if b.get("error"):
        return [("band", "fail", [], f"字幕帶檢查沒跑成：{b['error']}")]
    hits = b.get("hits") or []
    if not hits:
        return []
    secs = float(b.get("seconds", 0))
    where = "平台介面區（直式底部會被帳號、說明列蓋住）" if cc.grammar == CARD else "字幕帶"
    return [("band", "fail" if secs >= BAND_FAIL else "warn", [(h[0], h[1]) for h in hits],
             f"畫面上的東西壓進{where}，累計 {secs:.1f} 秒（y ≥ {b['top']} px 的那一條有深色的字或圖）")]


# ---------------------------------------------------------------- 判讀


def _clip_time(cp: ClipPlan, fps: int, t: float) -> float:
    return round(cp.start_frame / fps + t, 3)


def _qa_findings(cc: ClipCheck) -> list[tuple[str, str, list[tuple[float, float]], str]]:
    """(檢查, fail|warn, 片段內時段, 說明)。"""
    rec = cc.qa or {}
    out: list[tuple[str, str, list[tuple[float, float]], str]] = []
    if rec.get("error"):
        return [("page-error", "fail", [], f"qa 沒跑完：{rec['error']}")]
    errors = rec.get("page_errors") or []
    if errors:
        out.append(("page-error", "fail", [], f"引擎重渲這段時頁面報錯 {len(errors)} 條：{errors[0][:120]}"))
    det = int(rec.get("det_maxdiff", 0) or 0)
    if det > 0:
        out.append(("determinism", "fail" if det > DET_FAIL else "warn", [],
                    f"同一幀渲三次結果不一樣（像素最多差 {det}）：分鏡靜態圖可能跟成片對不上"))
    if cc.grammar == CHART:
        still = float(rec.get("still_pairs_pct", 0.0))
        if still >= STILL_WARN:
            status = "fail" if still >= STILL_FAIL else "warn"
            out.append(("motion", status, [], f"畫面幾乎沒在動：{still:.0f}% 的相鄰兩幀沒有變化（慢推也沒有）"))
    spikes = [float(t) for t in rec.get("spikes_at_lt") or []]
    if spikes and cc.grammar in (CHART, CARD):
        spans = _merge_spans([(t - 1 / QA_FPS, t) for t in spikes], gap=0.2)
        out.append(("jump", "fail" if cc.grammar == CHART else "warn", spans,
                    f"有 {len(spans)} 處畫面在一幀裡突然變了一大塊（最多 {float(rec.get('motion_max', 0)):.0f}% 的畫面），"
                    "看起來會閃一下；財經圖表的動畫應該是慢慢長出來的"))
    out.extend(_framing_findings(cc.grammar, rec.get("framing") or []))
    return out


_FRAMING_KINDS = {"半截字": "cut-text", "叠字": "overlap", "进字幕带": "text-in-band", "近空白": "blank"}
_FRAMING_WHAT = {
    "cut-text": "字被畫面邊緣切掉一截",
    "overlap": "兩串字疊在一起",
    "text-in-band": "字掉進字幕帶，燒上去的字幕會蓋住它",
    "blank": "整個畫面幾乎是空的",
}


def _framing_status(grammar: str, check: str, longest: float) -> str:
    if check == "blank":
        return "fail" if longest >= FRAMING_LONG else "warn"
    if grammar == VOX:  # 鏡頭在桌面上移動，經過的卡片本來就會被切到、疊到；只有字幕蓋字算數
        return "fail" if check == "text-in-band" and longest >= FRAMING_LONG else "warn"
    return "fail" if longest >= FRAMING_FAIL else "warn"


def _framing_findings(grammar: str, runs: Sequence[dict[str, Any]]) -> list[tuple[str, str, list[tuple[float, float]], str]]:
    by_check: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        check = _FRAMING_KINDS.get(str(r.get("kind")))
        if check:
            by_check.setdefault(check, []).append(r)
    out = []
    for check, rs in by_check.items():
        spans = [(float(r["lt"][0]), float(r["lt"][1])) for r in rs]
        longest = max(b - a for a, b in spans)
        texts = "、".join(dict.fromkeys(t for r in rs for t in str(r.get("text") or "").split("、") if t))
        msg = _FRAMING_WHAT[check] + f"（最長 {longest:.1f} 秒）" + (f"：{texts[:60]}" if texts else "")
        out.append((check, _framing_status(grammar, check, longest), spans, msg))
    return out


def _merge_spans(spans: Sequence[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def _token(source: str, cc: ClipCheck, check: str) -> str:
    return f"{source}:{cc.clip_id}:{check}@{cc.hash[:8]}"


def _findings(plan: BuildPlan, clips: Sequence[ClipCheck]) -> list[Finding]:
    fps = plan.timeline.fps
    by_id = {cp.id: cp for cp in plan.clips}
    out: list[Finding] = []
    for cc in clips:
        cp = by_id[cc.clip_id]
        found = [("qa", *f) for f in (_qa_findings(cc) if cc.qa is not None else [])]
        found += [("band", *f) for f in _band_findings(cc)]
        for source, check, status, spans, msg in found:
            times = tuple((_clip_time(cp, fps, a), _clip_time(cp, fps, b)) for a, b in spans)
            token = _token(source, cc, check) if source == "qa" else f"band:{cc.clip_id}@{cc.hash[:8]}"
            out.append(Finding(token, source, check, status, cc.clip_id, _shots_at(plan, cc, times), times, msg))
    return out + _hold_findings(plan, clips)


def _hold_findings(plan: BuildPlan, clips: Sequence[ClipCheck]) -> list[Finding]:
    """停太久：旁白期間連續 HOLD_WARN 秒以上沒有新的畫面事件（換圖、新的柱／線／標註／卡片／框）。
    慢推、慢漂不算新資訊（審片學到的：觀眾一樣覺得畫面停住了）。只提醒，不擋出片。"""
    tl = plan.timeline
    fps = tl.fps
    events: list[tuple[int, str]] = []
    for st in tl.shots:
        if not st.shot.continues:
            events.append((st.start_frame, st.shot.id))  # 切到新的一張圖
        events += [(c.frame, st.shot.id) for c in st.cues if not c.carried]
    events.sort()
    end = tl.outro.start_frame
    clip_of = {sid: cc for cc in clips for sid in cc.shot_ids}
    out: list[Finding] = []
    for (f0, sid), nxt in zip(events, events[1:] + [(end, "")]):
        f1 = min(nxt[0], end)
        secs = (f1 - f0) / fps
        if secs <= HOLD_WARN or sid not in clip_of:
            continue
        cc = clip_of[sid]
        times = ((round(f0 / fps, 3), round(f1 / fps, 3)),)
        out.append(Finding(f"hold:{sid}@{cc.hash[:8]}", "hold", "hold", "warn", cc.clip_id, (sid,), times,
                           f"{secs:.1f} 秒沒有新的畫面（慢推不算新資訊）：一句旁白至少配一個新東西，"
                           "或把這段拆成兩個分鏡"))
    return out


def _shots_at(plan: BuildPlan, cc: ClipCheck, times: Sequence[tuple[float, float]]) -> tuple[str, ...]:
    """有時段的問題歸給那個時段所在的分鏡（Vox 一個片段涵蓋好幾個分鏡）；沒有時段的歸給整個片段的分鏡。"""
    if not times or len(cc.shot_ids) <= 1:
        return cc.shot_ids
    fps = plan.timeline.fps
    hit: list[str] = []
    for st in plan.timeline.shots:
        if st.shot.id not in cc.shot_ids:
            continue
        a, b = st.start_frame / fps, st.end_frame / fps
        if any(t0 < b and t1 > a for t0, t1 in times):
            hit.append(st.shot.id)
    return tuple(hit) or cc.shot_ids


# ---------------------------------------------------------------- 執行


def run_checks(plan: BuildPlan, *, jobs: int | None = None, log: Log = _quiet) -> CheckReport:
    """每個片段跑 qa（有快取、並行），彙整成 CheckReport。"""
    t0 = time.monotonic()
    fps = plan.timeline.fps
    root = _qa_dir(plan)
    root.mkdir(parents=True, exist_ok=True)
    keep = {cp.id for cp in plan.clips}
    for d in root.iterdir():
        if d.is_dir() and d.name not in keep:
            shutil.rmtree(d)

    results: dict[str, dict[str, Any]] = {}
    todo: list[tuple[ClipPlan, str]] = []
    cached: list[str] = []
    for cp in plan.clips:
        key = _qa_key(cp)
        rec = _cached_qa(root / cp.id, key)
        if rec is not None:
            results[cp.id] = rec
            cached.append(cp.id)
        else:
            todo.append((cp, key))

    if todo:
        n_jobs = jobs or min(4, max(1, (os.cpu_count() or 2) // 2))
        log(f"qa：{len(todo)} 個片段要跑（{len(cached)} 個用快取，最多同時 {n_jobs} 個）")
        with ThreadPoolExecutor(max_workers=max(1, min(n_jobs, len(todo)))) as pool:
            futures = {cp.id: pool.submit(_run_qa, cp, root / cp.id, plan.project.root, key) for cp, key in todo}
            for cid, fut in futures.items():
                results[cid] = fut.result()
    ffmpeg = find_ffmpeg()
    clips = [
        ClipCheck(clip_id=cp.id, shot_ids=tuple(cp.shot_ids), grammar=cp.grammar, start=cp.start_frame / fps,
                  end=cp.end_frame / fps, hash=clip_hash(cp), qa=results.get(cp.id), qa_dir=root / cp.id,
                  band=_check_band(plan, cp, ffmpeg))
        for cp in plan.clips
    ]
    return CheckReport(format=plan.format.name, clips=clips, findings=_findings(plan, clips),
                       seconds=round(time.monotonic() - t0, 2), ran=[cp.id for cp, _ in todo], cached=cached)

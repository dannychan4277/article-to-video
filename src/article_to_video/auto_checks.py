"""自動檢查：build 好審片用的成片之後、作者看審片頁之前（都在出片之前），機器先把每個片段看一遍。

  run_checks(plan)            每個片段跑 qa、白底片段跑字幕帶檢查（有快取、並行），彙整成 CheckReport，
                              寫 build/<比例>/checks.json。指令列：python -m article_to_video checks <專案>。
  load_check_report(plan)     只讀快取（不跑）：審片頁（checks_contributor）與出片前的檢查用。
  require_checks_cleared(plan) build 的 preflight：自動檢查的 fail、獨立審片的必修，沒處理就擋正式出片。

qa（內嵌引擎的 engine/qa.py，原樣使用）：在 Chromium 裡把片段的 spec 逐幀重渲一次（30 fps 取樣），量確定性、動態、
跳變、框景（半截字、疊字、字進字幕帶、近空白）。qa 自己只在頁面報錯時失敗，門檻在這裡判。
為了快，qa 照比例縮小渲（短邊 QA_SHORT_SIDE＝540 px；引擎依畫面寬度等比排版，框景用的是比例座標。
實測同一份 spec 1080p 與 540p：跳變、框景、確定性一樣，Vox 片段快 3.4 倍）。

每項結果是 ok／warn／fail：fail＝必修（沒處理就擋正式出片），warn＝該修（審片頁標出來，不擋）。

門檻（財經圖表＝t3_finance_chart、Vox＝y2_vox、片尾卡＝a2v_end_card）：
  檢查        財經圖表                           Vox                               片尾卡
  頁面報錯    fail                               fail                              fail
  確定性      像素差 > 24 fail，> 0 warn          同左                              同左
  動態        靜止幀對 ≥ 60% fail，≥ 40% warn     不判（一拍二動畫平常就 40～50%）  不判（本來就停著）
  跳變        有就 fail                          不判（滑入、推鏡就是這樣）        有就 warn
  半截字、疊字 持續 ≥ 0.5 秒 fail，其他 warn       warn（鏡頭經過的卡片本來會被切到）  同財經圖表
  字進字幕帶  持續 ≥ 0.5 秒 fail，其他 warn       字幕框真的蓋到字才算（見下）；     同財經圖表（直式才有帶子）
                                                 持續 ≥ 1 秒 fail，其他 warn
  近空白      持續 ≥ 1 秒 fail，其他 warn         同左                              同左
  字幕帶      壓進去累計 ≥ 0.3 秒 fail，其他 warn  不查（牛皮紙紋理會誤報）           直式查平台介面區；橫式沒字幕不查
  停太久      旁白期間 > 5 秒沒有新畫面事件 warn（換圖、新的柱／線／標註／卡片／框；慢推不算）
理由寫在各常數旁邊。

快取：qa 的結果放 build/<比例>/checks/qa/<片段>/（clip.json、qa.json、qa.md、qa.log、<片段>.jpg 四幀拼圖＋運動熱圖、
stamp.json），鍵＝QA_VERSION＋片段雜湊（spec＋引擎檔＋圖片）＋取樣設定＋qa.py 本身；片段沒變就不重跑。
字幕帶放 build/<比例>/checks/band/<片段>.json，看的是渲好的片段（無字幕），所以要 build 之後才查得到。

Vox 的「字進字幕帶」：qa 只看字有沒有掉進底部那一條（整條、不分左右），剪報左下角的小標籤「原文段落」「圖N」
推近時常常掉進去，可是字幕是置中的一個框，根本蓋不到（試跑：0:13.8、0:16.0 被判必修的就是這個）。所以 qa 報的時段裡，
再用渲好的片段看每一幀「那時候燒上去的字幕框」（subtitles.subtitle_box）裡有沒有深色的字或標籤：有才算，時段縮成
真的蓋到的那幾段；字幕框裡只有桌面、紙的就不算。放 build/<比例>/checks/subzone/<片段>.json。
財經圖表不套這個（白底上的字一掉進那一條就算，字幕帶的像素檢查照舊）。

代號（接受不改時用，見 check_log.py）：qa:<片段>:<檢查>@<片段雜湊前 8 碼>、band:<片段>@…、hold:<分鏡>@…。
片段改了代號就變，舊的接受不會套到新畫面上。
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
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from article_to_video.progress import Log, quiet
from article_to_video.check_log import load_log, now, resolution
from article_to_video.clip_cache import clip_hash, clip_is_current, clip_stamp
from article_to_video.commands import cli_command
from article_to_video.end_card import END_CARD_ID
from article_to_video.clips import (
    GRAMMAR_CHART, GRAMMAR_END_CARD, GRAMMAR_VOX, QA_SCRIPT, absolutize_images, default_jobs, run_parallel,
)
from article_to_video.ffmpeg import find_ffmpeg
from article_to_video.project import ProjectError
from article_to_video.review_page import timecode
from article_to_video.textio import read_json_or_none

if TYPE_CHECKING:
    from article_to_video.build import BuildPlan
    from article_to_video.review import ReviewExtras
    from article_to_video.timeline import ClipPlan

__all__ = [
    "CHECK_LABELS",
    "CheckReport",
    "ClipCheck",
    "Finding",
    "blocking_problems",
    "checks_contributor",
    "checks_json",
    "clips_not_built",
    "current_video",
    "format_report",
    "load_check_report",
    "report_json",
    "require_checks_cleared",
    "run_checks",
]


QA_VERSION = "1"
QA_SHORT_SIDE = 540
"""qa 渲的畫面短邊（px）：1080p 的片段縮一半渲，快 2～3 倍，量到的東西一樣（比例）。"""
QA_FPS = 30

CHART, VOX, CARD = GRAMMAR_CHART, GRAMMAR_VOX, GRAMMAR_END_CARD
"""門檻表的三欄（財經圖表、Vox、片尾卡）。"""

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

# 字幕帶（花叔引擎 scripts/subzone_gate.py 的做法，沒帶進外掛，這裡改寫成任何畫面大小都能用）：每秒 10 幀，取字幕帶那一條、縮一半、轉灰階，
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
SUBZONE_VERSION = "1"
SUBZONE_FPS = 10
SUBZONE_DARK = 70
"""Vox 畫面上的字與標籤是近黑色（引擎 C.ink #171716、剪報的字）；牛皮紙纖維、暗角都在灰階 95 以上（實測），不會被當成字。"""
SUBZONE_RATIO = 0.003
"""字幕框裡深色像素超過框面積的這個比例，就算字幕蓋到了東西。"""
HOLD_WARN = 5.0
"""旁白期間超過這麼多秒沒有新的畫面事件就提醒（審片規則：一句旁白一個畫面事件，6 秒沒新資訊就算靜止）。"""


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
    resolution: dict[str, Any] | None = None
    """check_log.json 裡的處理紀錄（接受不改）；None＝還沒處理。"""

    @property
    def label(self) -> str:
        return CHECK_LABELS[self.check]

    @property
    def severity(self) -> str:
        return "必修" if self.status == "fail" else "該修"

    @property
    def blocking(self) -> bool:
        """fail 而且還沒處理：擋正式出片。"""
        return self.status == "fail" and self.resolution is None


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
    subzone: dict[str, Any] | None = None
    """Vox 片段 qa 報了「字進字幕帶」時，字幕框真的蓋到東西的時段：{"spans": [[起, 迄], …]}（片段內秒）；
    還沒看過（片段還沒渲、只讀快取）是 {"pending": True}，照 qa 原本的結果判；用不到是 None。"""


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

    @property
    def unchecked(self) -> list[str]:
        """還沒有 qa 結果的片段（片段改過、還沒重跑 checks）。"""
        return [c.clip_id for c in self.clips if c.qa is None]

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking]

    def for_shot(self, shot_id: str) -> list[Finding]:
        return [f for f in self.findings if shot_id in f.shot_ids]


# ---------------------------------------------------------------- qa


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _qa_scale(spec: dict[str, Any]) -> float:
    short = min(int(spec.get("width", 1920)), int(spec.get("height", 1080)))
    return min(1.0, QA_SHORT_SIDE / short)


def _qa_spec(cp: ClipPlan, project_root: Path) -> dict[str, Any]:
    spec = absolutize_images(copy.deepcopy(cp.spec), project_root)
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


def _load_qa(out: Path) -> dict[str, Any] | None:
    report = read_json_or_none(out / "qa.json")
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
    stamp = read_json_or_none(out / "stamp.json")
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
    """ffmpeg＝None：只讀快取（審片頁、出片前的檢查用），沒查過的回 {"pending": True}。"""
    if not _band_px(cp):
        return None
    path = plan.paths.root / "checks" / "band" / f"{cp.id}.json"
    key = _band_key(cp)
    cached = read_json_or_none(path)
    if isinstance(cached, dict) and cached.get("key") == key and isinstance(cached.get("result"), dict):
        return cached["result"]
    if ffmpeg is None:
        return {"top": int(cp.spec["height"]) - _band_px(cp), "pending": True}
    if not clip_is_current(cp, plan.paths):
        return {"top": int(cp.spec["height"]) - _band_px(cp), "error": "這個片段還沒渲好（先 build 再檢查）"}
    result = _scan_band(plan, cp, ffmpeg)
    if "error" not in result:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"key": key, "result": result}, ensure_ascii=False), encoding="utf-8")
    return result


def _band_findings(cc: ClipCheck) -> list[tuple[str, str, list[tuple[float, float]], str]]:
    b = cc.band
    if not b or b.get("pending"):
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


# ---------------------------------------------------------------- Vox：字幕框真的蓋到的字


def _qa_band_runs(rec: dict[str, Any] | None) -> list[tuple[float, float]]:
    """qa 報「字進字幕帶」的時段（片段內秒）。"""
    if not rec or rec.get("error"):
        return []
    return [(float(r["lt"][0]), float(r["lt"][1])) for r in rec.get("framing") or [] if r.get("kind") == "进字幕带"]


def _subtitle_boxes(plan: BuildPlan, cp: ClipPlan) -> list[tuple[float, float, tuple[int, int, int, int]]]:
    """這個片段期間每句燒上去的字幕：(片段內起, 迄, 框 (x0, y0, x1, y1))。"""
    from article_to_video.subtitles import subtitle_box, subtitle_lines

    fps = plan.timeline.fps
    t0, t1 = cp.start_frame / fps, cp.end_frame / fps
    return [(round(max(ln.start, t0) - t0, 3), round(min(ln.end, t1) - t0, 3), subtitle_box(ln.text, plan.format))
            for ln in subtitle_lines(plan.timeline.segments.values()) if ln.end > t0 and ln.start < t1]


def _scan_subzone(plan: BuildPlan, cp: ClipPlan, runs: Sequence[tuple[float, float]],
                  boxes: Sequence[tuple[float, float, tuple[int, int, int, int]]], ffmpeg: Path) -> dict[str, Any]:
    import numpy as np

    W, H = int(cp.spec["width"]), int(cp.spec["height"])
    top = max(0, min((b[1] for _, _, b in boxes), default=H)) // 2 * 2  # yuv420p 只能裁偶數
    if top >= H:
        return {"spans": []}
    hits: list[float] = []
    for a, b in runs:
        proc = subprocess.run(
            [str(ffmpeg), "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a + 1 / SUBZONE_FPS:.3f}",
             "-i", str(plan.paths.clips / f"{cp.id}.mp4"),
             "-vf", f"fps={SUBZONE_FPS},crop={W}:{H - top}:0:{top}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr.decode("utf-8", "replace").strip()[-300:]}
        frames = np.frombuffer(proc.stdout, np.uint8).reshape(-1, H - top, W)
        for k, f in enumerate(frames):
            t = round(a + k / SUBZONE_FPS, 3)
            if t > b:
                break
            for s0, s1, (x0, y0, x1, y1) in boxes:
                if not s0 <= t < s1:
                    continue
                region = f[max(0, y0 - top):max(0, y1 - top), max(0, x0):min(W, x1)]
                if region.size and (region < SUBZONE_DARK).sum() > SUBZONE_RATIO * region.size:
                    hits.append(t)
                    break
    spans: list[list[float]] = []
    for t in hits:
        if spans and t - spans[-1][1] <= 1.5 / SUBZONE_FPS:
            spans[-1][1] = t
        else:
            spans.append([t, t])
    return {"spans": [[round(x, 3), round(y + 1 / SUBZONE_FPS, 3)] for x, y in spans]}


def _check_subzone(plan: BuildPlan, cp: ClipPlan, rec: dict[str, Any] | None,
                   ffmpeg: Path | None) -> dict[str, Any] | None:
    """Vox 片段：qa 報「字進字幕帶」的時段裡，字幕框真的蓋到東西的那幾段。ffmpeg＝None：只讀快取。"""
    runs = _qa_band_runs(rec) if cp.grammar == VOX else []
    if not runs:
        return None
    boxes = _subtitle_boxes(plan, cp)
    path = plan.paths.root / "checks" / "subzone" / f"{cp.id}.json"
    key = hashlib.sha256(json.dumps([SUBZONE_VERSION, clip_hash(cp), runs, boxes, SUBZONE_FPS, SUBZONE_DARK,
                                     SUBZONE_RATIO]).encode()).hexdigest()
    cached = read_json_or_none(path)
    if isinstance(cached, dict) and cached.get("key") == key and isinstance(cached.get("result"), dict):
        return cached["result"]
    if ffmpeg is None or not clip_is_current(cp, plan.paths):
        return {"pending": True}
    result = _scan_subzone(plan, cp, runs, boxes, ffmpeg)
    if "error" in result:
        return {"pending": True, "error": result["error"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"key": key, "result": result}, ensure_ascii=False), encoding="utf-8")
    return result


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
    out.extend(_framing_findings(cc.grammar, rec.get("framing") or [], cc.subzone))
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


def _framing_findings(grammar: str, runs: Sequence[dict[str, Any]],
                      subzone: dict[str, Any] | None = None) -> list[tuple[str, str, list[tuple[float, float]], str]]:
    by_check: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        check = _FRAMING_KINDS.get(str(r.get("kind")))
        if check:
            by_check.setdefault(check, []).append(r)
    out = []
    for check, rs in by_check.items():
        spans = [(float(r["lt"][0]), float(r["lt"][1])) for r in rs]
        if check == "text-in-band" and grammar == VOX and subzone and "spans" in subzone:
            covered = [(float(a), float(b)) for a, b in subzone["spans"]]  # 字幕框真的蓋到的時段
            if not covered:
                continue
            rs = [r for r in rs if any(a < float(r["lt"][1]) and b > float(r["lt"][0]) for a, b in covered)]
            spans = covered
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


def _collect(plan: BuildPlan, *, run: bool, jobs: int | None, log: Log) -> CheckReport:
    t0 = time.monotonic()
    fps = plan.timeline.fps
    root = _qa_dir(plan)
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

    if run:
        root.mkdir(parents=True, exist_ok=True)
        keep = {cp.id for cp in plan.clips}
        for d in root.iterdir():
            if d.is_dir() and d.name not in keep:
                shutil.rmtree(d)
        if todo:
            n_jobs = jobs or default_jobs()
            log(f"qa：{len(todo)} 個片段要跑（{len(cached)} 個用快取，最多同時 {n_jobs} 個）…")
            done = run_parallel(todo, lambda t: _run_qa(t[0], root / t[0].id, plan.project.root, t[1]), jobs=n_jobs)
            results.update((cp.id, rec) for (cp, _), rec in zip(todo, done))
        else:
            log(f"qa：{len(cached)} 個片段都沒變，用快取")
    ffmpeg = find_ffmpeg() if run else None
    clips = [
        ClipCheck(clip_id=cp.id, shot_ids=tuple(cp.shot_ids), grammar=cp.grammar, start=cp.start_frame / fps,
                  end=cp.end_frame / fps, hash=clip_hash(cp), qa=results.get(cp.id), qa_dir=root / cp.id,
                  band=_check_band(plan, cp, ffmpeg), subzone=_check_subzone(plan, cp, results.get(cp.id), ffmpeg))
        for cp in plan.clips
    ]
    book = load_log(plan.project.root)
    found = [replace(f, resolution=resolution(book, f.token)) for f in _findings(plan, clips)]
    return CheckReport(format=plan.format.name, clips=clips, findings=found, seconds=round(time.monotonic() - t0, 2),
                       ran=[cp.id for cp, _ in todo] if run else [], cached=cached)


def run_checks(plan: BuildPlan, *, jobs: int | None = None, log: Log = quiet) -> CheckReport:
    """每個片段跑 qa、白底片段跑字幕帶檢查（有快取、並行），彙整成 CheckReport，寫 build/<比例>/checks.json。
    字幕帶檢查看的是渲好的片段（build 之後才有）；片段還沒渲好的，那一項記成 fail「先 build 再檢查」。"""
    report = _collect(plan, run=True, jobs=jobs, log=log)
    checks_json(plan).write_text(json.dumps(report_json(plan, report), ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def load_check_report(plan: BuildPlan) -> CheckReport:
    """只讀快取、不跑任何東西（審片頁、出片前的檢查用）：片段沒變的拿上次的結果，改過的算還沒檢查。"""
    return _collect(plan, run=False, jobs=None, log=quiet)


def checks_json(plan: BuildPlan) -> Path:
    return plan.paths.root / "checks.json"


def clips_not_built(plan: BuildPlan) -> list[str]:
    """還沒渲、或渲的是舊內容的片段 id（自動檢查要在 build 之後跑）。"""
    return [cp.id for cp in plan.clips if not clip_is_current(cp, plan.paths)]


def current_video(plan: BuildPlan) -> Path | None:
    """成片還是不是現在這份分鏡設定做出來的：每個片段都是最新的、成片比它們都新。是就回傳成片路徑。"""
    video = plan.project.paths.video(plan.format.name)
    if not video.is_file() or not plan.paths.final_stamp.is_file() or clips_not_built(plan):
        return None
    newest = max(clip_stamp(plan.paths, cp.id).stat().st_mtime for cp in plan.clips)
    return video if video.stat().st_mtime >= newest else None


# ---------------------------------------------------------------- 輸出


def _shot_numbers(plan: BuildPlan) -> dict[str, int]:
    return {st.shot.id: st.shot.number for st in plan.timeline.shots}


def _where(plan: BuildPlan, f: Finding) -> str:
    nums = _shot_numbers(plan)
    shots = "、".join(f"分鏡 {nums[s]}" for s in f.shot_ids if s in nums) or "片尾卡"
    if f.times:
        shots += "（" + "、".join(f"{timecode(a)}–{timecode(b)}" for a, b in f.times[:3]) + "）"
    return shots


def _shot_status(findings: Sequence[Finding], checked: bool) -> str:
    if not checked:
        return "unchecked"
    if any(f.blocking for f in findings):
        return "fail"
    return "warn" if findings else "ok"


def report_json(plan: BuildPlan, report: CheckReport) -> dict[str, Any]:
    """checks.json 的內容（給主 skill 讀）。"""
    nums = _shot_numbers(plan)
    root = plan.project.root
    fps = plan.timeline.fps

    def rel(p: Path) -> str:
        return os.path.relpath(p, root).replace(os.sep, "/")

    clips = []
    for c in report.clips:
        qa: dict[str, Any] = {"checked": c.qa is not None}
        if c.qa is not None:
            if c.qa.get("error"):
                qa["error"] = c.qa["error"]
            else:
                qa.update({
                    "motionPct": c.qa.get("motion_pct"), "stillPairsPct": c.qa.get("still_pairs_pct"),
                    "spikes": len(c.qa.get("spikes_at_lt") or []), "deterministic": c.qa.get("deterministic"),
                    "framing": len(c.qa.get("framing") or []), "pageErrors": len(c.qa.get("page_errors") or []),
                })
            sheet = c.qa_dir / f"{c.clip_id}.jpg"
            if sheet.is_file():
                qa["sheet"] = rel(sheet)
            if c.subzone is not None:  # Vox：qa 說字進了字幕帶，字幕框真的蓋到的時段（片段內秒；pending＝還沒看）
                qa["subtitleOverlap"] = c.subzone
        band: dict[str, Any] = {"checked": bool(c.band) and not c.band.get("pending") and "error" not in c.band}
        if c.band:
            band.update({k: v for k, v in c.band.items() if k in ("top", "hits", "seconds", "error")})
        else:
            band["skipped"] = ("Vox 剪報的牛皮紙紋理不適合，看 qa 的框景" if c.grammar == VOX
                               else "這段沒有字幕" if c.grammar == CARD else "不用查")
        clips.append({"clip": c.clip_id, "shots": list(c.shot_ids), "grammar": c.grammar,
                      "start": round(c.start, 3), "end": round(c.end, 3), "hash": c.hash, "qa": qa, "band": band})
    shots = []
    for st in plan.timeline.shots:
        fs = report.for_shot(st.shot.id)
        checked = all(c.qa is not None for c in report.clips if st.shot.id in c.shot_ids)
        shots.append({"number": st.shot.number, "id": st.shot.id, "start": round(st.start_frame / fps, 3),
                      "end": round(st.end_frame / fps, 3), "status": _shot_status(fs, checked),
                      "findings": [f.token for f in fs]})
    findings = [{
        "token": f.token, "source": f.source, "check": f.check, "label": f.label, "status": f.status,
        "severity": f.severity, "clip": f.clip_id, "shots": [nums[s] for s in f.shot_ids if s in nums],
        "shotIds": list(f.shot_ids), "times": [list(t) for t in f.times], "message": f.message,
        "resolution": f.resolution,
    } for f in report.findings]
    return {
        "version": 1, "aspect": report.format, "checkedAt": now(), "seconds": report.seconds,
        "qaRan": report.ran, "qaCached": report.cached, "unchecked": report.unchecked,
        "clips": clips, "shots": shots, "findings": findings, "blocking": [f.token for f in report.blocking],
    }


def format_report(plan: BuildPlan, report: CheckReport) -> str:
    """指令列印給 Claude 看的彙整：每個分鏡一行，有問題的下面逐條列（含代號、對照圖）。"""
    root = plan.project.root
    fps = plan.timeline.fps
    lines = [f"自動檢查（{plan.format.label}）：{len(report.clips)} 個片段，qa 這次跑了 {len(report.ran)} 個、"
             f"{len(report.cached)} 個用快取，花 {report.seconds:.1f} 秒"]

    def block(fs: Sequence[Finding], sheets: Sequence[Path]) -> None:
        for f in fs:
            mark = "已接受" if f.resolution else f.severity + ("，擋出片" if f.blocking else "")
            when = "、".join(f"{timecode(a)}–{timecode(b)}" for a, b in f.times[:3])
            lines.append(f"    {f.label}（{mark}）{when + '：' if when else '：'}{f.message}")
            lines.append(f"      代號 {f.token}")
        for s in sheets:
            lines.append(f"    對照圖（四幀＋運動熱圖）：{s}")

    for st in plan.timeline.shots:
        fs = report.for_shot(st.shot.id)
        clips = [c for c in report.clips if st.shot.id in c.shot_ids]
        status = _shot_status(fs, all(c.qa is not None for c in clips))
        mark = {"ok": "✓", "warn": "!", "fail": "✗", "unchecked": "?"}[status]
        lines.append(f"  {mark} 分鏡 {st.shot.number}（{st.shot.id}）{timecode(st.start_frame / fps)}–"
                     f"{timecode(st.end_frame / fps)}" + ("　還沒檢查" if status == "unchecked" else ""))
        block(fs, [c.qa_dir / f"{c.clip_id}.jpg" for c in clips if fs and (c.qa_dir / f"{c.clip_id}.jpg").is_file()])
    loose = [c for c in report.clips if not c.shot_ids]
    for c in loose:
        fs = [f for f in report.findings if f.clip_id == c.clip_id]
        mark = {"ok": "✓", "warn": "!", "fail": "✗", "unchecked": "?"}[_shot_status(fs, c.qa is not None)]
        lines.append(f"  {mark} 片尾卡 {timecode(c.start)}–{timecode(c.end)}")
        block(fs, [c.qa_dir / f"{c.clip_id}.jpg"] if fs and (c.qa_dir / f"{c.clip_id}.jpg").is_file() else [])
    bad = report.blocking
    if bad:
        lines.append(f"有 {len(bad)} 項必修沒處理，正式出片會被擋下。先打開對照圖看：真的有問題就改分鏡再出片"
                     "（改過的片段會自動重查）；看過確定沒問題、要保留，就記下接受的理由：")
        lines.append("  " + cli_command("resolve", root, *(f.token for f in bad), "--accept", '"理由"'))
    else:
        lines.append("沒有擋出片的問題。" if report.findings or not report.unchecked else "")
    lines.append(f"彙整：{checks_json(plan)}")
    return "\n".join(ln for ln in lines if ln)


# ---------------------------------------------------------------- 擋出片


def _resolve_hint(root: Path, tokens: Sequence[str]) -> str:
    review = [t for t in tokens if not t.startswith(("qa:", "band:", "hold:"))]
    lines = ["要改：改分鏡設定再出片（自動檢查的問題，改過的片段會自動重查）。"]
    if review:
        lines.append("  獨立審片的問題改好之後記下來：" + cli_command("resolve", root, *review, "--fixed", '"怎麼改的"'))
    lines.append("不改：看過畫面、確定要保留，記下理由（作者決定不改的加 --by 作者）：")
    lines.append("  " + cli_command("resolve", root, *tokens, "--accept", '"理由"'))
    return "\n".join(lines)


def blocking_problems(plan: BuildPlan) -> list[tuple[str, str]]:
    """還沒處理的必修：(代號, 一行說明)。自動檢查（片段沒變、上次查出 fail 的）＋獨立審片（每一輪的必修）。"""
    out = [(f.token, f"{_where(plan, f)} {f.label}：{f.message}（代號 {f.token}）")
           for f in load_check_report(plan).blocking]
    from article_to_video.independent_review import unresolved_must  # 避免循環匯入

    out += unresolved_must(plan)
    return out


def require_checks_cleared(plan: BuildPlan) -> None:
    """正式出片前（build 的 preflight）：還有必修沒處理（沒改掉、也沒明確接受）就丟 ProjectError，不渲、不出片。"""
    bad = blocking_problems(plan)
    if not bad:
        return
    raise ProjectError(
        [line for _, line in bad],
        title=f"還有 {len(bad)} 項必修沒處理，先不出片",
        hint=_resolve_hint(plan.project.root, [t for t, _ in bad]),
    )


_PLAIN = {
    "page-error": "這段畫面做的時候出錯了",
    "determinism": "同一個畫面每次做出來不太一樣，靜態圖可能跟影片對不上",
    "motion": "畫面幾乎沒在動，看起來像停住了",
    "jump": "畫面會突然閃一下",
    "cut-text": "有字被畫面邊緣切掉一截",
    "overlap": "有兩串字疊在一起",
    "text-in-band": "有字掉到字幕的位置，會被字幕蓋住",
    "blank": "畫面幾乎是空的",
    "band": "畫面上的字或圖壓到字幕的位置，會被字幕蓋住",
    "hold": "超過 5 秒沒有新的畫面，觀眾會覺得停住了",
}
"""審片頁上給作者看的白話（檢查名稱、像素位置這些給 Claude 的寫在代號那一區）。"""


def _plain(f: Finding) -> str:
    if f.check == "band" and f.clip_id == END_CARD_ID:
        return "片尾卡的字壓到直式影片底部會被平台蓋住的地方"
    return _PLAIN[f.check]


def checks_contributor(plan: BuildPlan) -> ReviewExtras:
    """審片頁：每張卡的「checks」欄列這個分鏡的問題（qa、字幕帶、停太久）；都沒問題就安靜的一行。
    頁首一區自動檢查的總結。作者看到白話；代號、檢查名稱、技術說明收在「給 Claude 的代號」裡。只讀快取。"""
    from article_to_video.review import CardSection, CheckItem, ReviewExtras  # 避免循環匯入

    report = load_check_report(plan)

    def item(f: Finding, where: str = "") -> CheckItem:
        when = "、".join(f"{timecode(a)}–{timecode(b)}" for a, b in f.times[:3])
        code = f"{f.token}　{f.label}：{f.message}"
        text = f"{where}　{_plain(f)}" if where else _plain(f)
        if f.resolution:
            return CheckItem(text, detail=f"看過、決定不改：{f.resolution.get('note', '')}" + (f"（{when}）" if when else ""),
                             status="info", label="已接受", code=code)
        return CheckItem(text, detail="" if where else when, status="block" if f.blocking else "warn",
                         label=f.severity, code=code)

    cards: dict[str, list] = {}
    for st in plan.timeline.shots:
        sid = st.shot.id
        clips = [c for c in report.clips if sid in c.shot_ids]
        fs = report.for_shot(sid)
        if any(c.qa is None for c in clips):
            sec = CardSection("checks", "自動檢查", [CheckItem(
                "還沒跑自動檢查", detail="做好審片用的影片之後 Claude 會跑；改過的分鏡要重跑", status="info")])
        elif not fs:
            sec = CardSection("checks", "自動檢查", [], summary="自動檢查沒發現問題")
        else:
            sec = CardSection("checks", "自動檢查", [item(f) for f in fs])
        cards[sid] = [sec]

    bad, warn = report.blocking, [f for f in report.findings if not f.blocking]
    nums = _shot_numbers(plan)
    note = "機器把每個片段重做一遍，看畫面有沒有卡住、閃一下、字被切掉或被字幕蓋住。"
    if report.unchecked and len(report.unchecked) == len(report.clips):
        sec = CardSection("checks", "自動檢查", [CheckItem(
            "還沒跑", detail="做好審片用的影片之後、給你看之前，Claude 會跑", status="info")], note=note)
    else:
        items = [item(f, _where(plan, f)) for f in bad] + [item(f, _where(plan, f)) for f in warn]
        if report.unchecked:
            names = "、".join(f"分鏡 {nums[s]}" for c in report.clips if c.qa is None for s in c.shot_ids if s in nums)
            items.append(CheckItem(f"還沒檢查：{names or '片尾卡'}", detail="改過之後還沒重跑", status="info"))
        if items:
            head = f"有 {len(bad)} 項必修還沒處理，處理完才能出片。" if bad else ""
            sec = CardSection("checks", "自動檢查", items, note=head + note)
        else:
            sec = CardSection("checks", "自動檢查", [], summary=f"自動檢查：{len(report.clips)} 個片段都沒發現問題")
    return ReviewExtras(cards=cards, page=[sec])

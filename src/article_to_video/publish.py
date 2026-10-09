"""出片：作者看過審片頁、點頭之後，把成片和字幕檔放進「影片／解說影片／日期-標題」（videos.py）。

publish_project() 照順序（前面的檢查不用渲染，幾秒內就擋下）：
  1. 作者點頭的版本：審片頁現在是第幾版、分鏡與旁白還是不是那一版（review.seen_version）。
     作者說「可以」的是第 N 版，就要 --version N；之後 Claude 又改過（版本變了、或改了還沒預覽）→ 擋下，先給作者看。
  2. 獨立審片：使用者設定開著，這支片卻一輪都沒審過 → 擋下（主流程在作者第一次看審片頁之前就要審一輪）。
     之後作者要求的修改不用每次重審：作者自己看過那些改動了。
  3. 建置成片（build_project：數字對帳、Vox 框核對、還沒處理的必修照樣擋；片段沒變的用快取）。
  4. 自動檢查（auto_checks.run_checks：只查這次新渲、還沒查過的片段）；查出新的必修 → 擋下。
  5. 複製成片、字幕檔到影片資料夾（先寫暫存檔再換名，Finder 裡不會出現一半的檔），
     記在 <專案>/output.json，審片頁標「已出片」與位置。

被擋時丟 BlockedBuild（帶 plan，指令列拿它更新審片頁）或 ProjectError（1、2：還沒建置，不用更新）。
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from article_to_video.build import BlockedBuild, BuildPlan, build_project, plan_build
from article_to_video.project import ProjectError
from article_to_video.timeline import VideoFormat
from article_to_video.videos import OUTPUT_RECORD, load_outputs, output_paths, video_folder, video_title

__all__ = ["PublishResult", "output_contributor", "publish_project"]

Log = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


@dataclass(frozen=True)
class PublishResult:
    plan: BuildPlan
    folder: Path
    video: Path
    srt: Path
    version: int
    project_video: Path
    """專案資料夾裡那份成片（審片頁內嵌的）。"""

    @property
    def duration(self) -> float:
        return self.plan.timeline.duration


def _check_approved(plan: BuildPlan, version: int) -> None:
    from article_to_video.review import seen_version

    root, aspect = plan.project.root, plan.format.name
    flag = "" if aspect == "16x9" else f" --aspect {aspect}"
    stored, same = seen_version(plan)
    if stored is None:
        raise ProjectError("這支片還沒有審片頁，作者還沒看過", title="還不能出片",
                           hint=f"先跑 preview（python -m article_to_video preview \"{root}\"{flag}），給作者看過、他點頭再出片。")
    if not same:
        raise ProjectError(
            f"審片頁第 {stored} 版之後，分鏡或旁白又改過了，作者還沒看過改過的樣子",
            title="還不能出片",
            hint=f"先跑 preview（python -m article_to_video preview \"{root}\"{flag}，會變成第 {stored + 1} 版），"
                 "把改了什麼告訴作者、打開審片頁給他看，他點頭再用那一版的版號出片。",
        )
    if stored != version:
        raise ProjectError(
            f"作者點頭的是第 {version} 版，但審片頁現在是第 {stored} 版",
            title="還不能出片",
            hint=f"把第 {stored} 版給作者看（審片頁頁首寫著第幾版），他說可以再出片：publish … --version {stored}。",
        )


def _check_reviewed(plan: BuildPlan, data_dir: str | os.PathLike | None) -> None:
    from article_to_video.check_log import load_log
    from article_to_video.independent_review import review_enabled

    if not review_enabled(data_dir) or load_log(plan.project.root)["rounds"]:
        return
    root = plan.project.root
    raise ProjectError(
        "這支片還沒做過獨立審片（使用者設定是開著的）",
        title="還不能出片",
        hint="照 checks-and-review.md 做一輪：build → checks → review-prep → 派審片 agent → review-record，"
             f"處理完必修再出片（python -m article_to_video review-prep \"{root}\"）。"
             "作者說不要獨立審片的話：settings set --independent-review 關。",
    )


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.part")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def publish_project(
    project_dir: str | os.PathLike,
    *,
    version: int,
    tts: str | None = None,
    jobs: int | None = None,
    fmt: VideoFormat | None = None,
    data_dir: str | os.PathLike | None = None,
    out_dir: str | os.PathLike | None = None,
    log: Log = _quiet,
) -> PublishResult:
    from article_to_video.auto_checks import format_report, require_checks_cleared, run_checks
    from article_to_video.numbers import check_numbers

    plan = plan_build(project_dir, tts=tts, fmt=fmt, data_dir=data_dir)  # 先擋：不渲染；建置時會再印一次配音、時間軸
    _check_approved(plan, version)
    _check_reviewed(plan, data_dir)
    fmt = plan.format

    res = build_project(project_dir, tts=tts, jobs=jobs, fmt=fmt, data_dir=data_dir,
                        preflight=[check_numbers, require_checks_cleared], log=log)
    plan = res.plan
    report = run_checks(plan, jobs=jobs, log=log)
    if report.findings:
        log(format_report(plan, report))
    else:
        log(f"自動檢查：{len(report.clips)} 個片段都沒問題（這次新查 {len(report.ran)} 個）")
    try:
        require_checks_cleared(plan)
    except ProjectError as e:
        raise BlockedBuild(e, plan) from None

    video, srt = output_paths(plan.project.root, fmt.name)
    if out_dir:
        video, srt = Path(out_dir).absolute() / video.name, Path(out_dir).absolute() / srt.name
    _copy(res.video, video)
    _copy(res.srt, srt)

    record_path = plan.project.root / OUTPUT_RECORD
    outputs = load_outputs(plan.project.root)
    outputs[fmt.name] = {
        "video": str(video), "srt": str(srt), "reviewVersion": version,
        "videoKey": plan.paths.final_stamp.read_text(encoding="utf-8"),
        "at": datetime.now().isoformat(timespec="seconds"), "duration": round(res.duration, 2),
    }
    record_path.write_text(json.dumps({"version": 1, "outputs": outputs}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    return PublishResult(plan=plan, folder=video.parent, video=video, srt=srt, version=version,
                         project_video=res.video)


def output_contributor(plan: BuildPlan) -> Any:
    """審片頁頁首一區「已出片」：這個比例最近一次出片放在哪裡、是第幾版；出片之後又改過就提醒。"""
    from article_to_video.review import CardSection, CheckItem, ReviewExtras  # 避免循環匯入

    rec = load_outputs(plan.project.root).get(plan.format.name)
    if not rec:
        return ReviewExtras()
    items = [
        CheckItem(f"成片：{rec.get('video', '')}", detail=f"審片頁第 {rec.get('reviewVersion', '?')} 版，"
                  f"{str(rec.get('at', '')).replace('T', ' ')} 出片", status="ok", label="已出片"),
        CheckItem(f"字幕檔：{rec.get('srt', '')}", detail="上傳 YouTube 時可以一起給（影片裡已經燒好字幕）",
                  status="info"),
    ]
    stamp = plan.paths.final_stamp
    current = stamp.read_text(encoding="utf-8") if stamp.is_file() else None
    from article_to_video.auto_checks import current_video

    if current != rec.get("videoKey") or current_video(plan) is None:
        items.append(CheckItem("出片之後又改過", detail="影片資料夾裡的還是舊的那一版；作者看過這一版、點頭之後再出片一次",
                               status="warn"))
    where = video_folder(plan.project.root)
    return ReviewExtras(page=[CardSection("output", "已出片", items,
                                          note=f"在 Finder：影片 → {where.parent.name} → {where.name}"
                                               f"（{video_title(plan.project.root)}）")])

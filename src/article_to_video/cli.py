"""指令列：`uv run --project <外掛根目錄> python -m article_to_video <子指令> …`

子指令：
  preview <專案資料夾> [--tts edge|fake] [--jobs N] [--no-open]   預覽：只出分鏡靜態圖＋審片頁（不渲整支片）
  build <專案資料夾> [--tts edge|fake] [--jobs N] [--no-open]     建置成片＋字幕檔，審片頁內嵌成片
  feedback <專案資料夾> [--file 檔]                   審片頁複製出來的留言（stdin）→ JSON（格式見 feedback.py）
  report-issue [專案資料夾] [--shot 分鏡]… [--note 文字] [--out-dir 資料夾]
                                                  回報問題：打包 zip（預設放桌面）
  reproduce-issue <zip> [--out 資料夾] [--shot 分鏡]…   （維護者用）從回報的 zip 重渲出錯的分鏡

preview、build 做完會用 macOS 的 open 在預設瀏覽器打開審片頁；--no-open 或環境變數
ARTICLE_TO_VIDEO_NO_OPEN=1（測試、CI）就不打開。preview、build 的輸出與錯誤記在
<專案>/build/last-run.{log,json}（只留最後一次），回報問題時打包。

錯誤一律印繁中說明到 stderr、結束碼 1（不印 Python traceback）。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from article_to_video.assemble import AssemblyError
from article_to_video.clips import ClipRenderError
from article_to_video.ffmpeg import FFmpegNotFoundError
from article_to_video.narration import TTS_ENGINES, NarrationError
from article_to_video.project import ProjectError

KNOWN_ERRORS = (ProjectError, NarrationError, ClipRenderError, FFmpegNotFoundError, AssemblyError)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="article_to_video", description="把財經文章做成解說影片")
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="建置成片與字幕檔")
    b.add_argument("project", help="專案資料夾（裡面有 narration.json、shots.json）")
    b.add_argument("--tts", choices=sorted(TTS_ENGINES), help="配音層（預設看 settings.json，沒寫就是 edge）")
    b.add_argument("--jobs", type=int, help="最多同時渲幾個片段（預設 3）")
    b.add_argument("--no-open", action="store_true", help="做完不要自動在瀏覽器打開審片頁")
    v = sub.add_parser("preview", help="只出分鏡靜態圖與審片頁（不渲整支片）")
    v.add_argument("project", help="專案資料夾（裡面有 narration.json、shots.json）")
    v.add_argument("--tts", choices=sorted(TTS_ENGINES), help="配音層（預設看 settings.json，沒寫就是 edge）")
    v.add_argument("--jobs", type=int, help="最多同時渲幾個分鏡的靜態圖（預設 4）")
    v.add_argument("--no-open", action="store_true", help="做完不要自動在瀏覽器打開審片頁")
    f = sub.add_parser("feedback", help="把審片頁「複製給 Claude」的文字（從 stdin 讀）解析成 JSON")
    f.add_argument("project", help="專案資料夾（用來把分鏡編號對到分鏡 id）")
    f.add_argument("--file", help="從這個檔讀，不從 stdin 讀")
    r = sub.add_parser("report-issue", help="回報問題：把錯誤紀錄、分鏡設定、出錯畫面、環境檢查打包成 zip（預設放桌面）")
    r.add_argument("project", nargs="?", help="出問題的專案資料夾（沒有專案就只打包環境檢查）")
    r.add_argument("--shot", action="append", default=[], help="出錯的分鏡：id（SH03）或編號（3），可重複；不給就從最後一次建置的錯誤找")
    r.add_argument("--note", default="", help="問題描述（作者怎麼說的）")
    r.add_argument("--out-dir", help="zip 放哪裡（預設桌面）")
    x = sub.add_parser("reproduce-issue", help="（維護者用）解開回報的 zip，重渲出錯的分鏡")
    x.add_argument("zip", help="作者傳來的回報 zip")
    x.add_argument("--out", help="解開到哪裡（預設 zip 旁邊、同名資料夾）")
    x.add_argument("--shot", action="append", default=[], help="只重渲這些分鏡（預設是回報裡出錯的分鏡）")
    return p


def _show_review(page, *, no_open: bool) -> None:
    from article_to_video.review import auto_open_allowed, open_in_browser

    print(f"審片頁：{page}")
    if no_open or not auto_open_allowed():
        return
    if not open_in_browser(page):
        print("沒能自動打開審片頁，請在 Finder 找到上面這個檔案，點兩下打開。")


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)
    if args.command in ("build", "preview"):
        from article_to_video.issue_report import RunRecord
        from article_to_video.review import default_contributors

        # 回報問題時打包：preview、build（含審片頁）出的錯都記進 last-run
        rec = RunRecord(args.project, args.command, {"tts": args.tts, "jobs": args.jobs})
        log = rec.tee(lambda m: print(m, flush=True))
        failed = "建置失敗" if args.command == "build" else "預覽失敗"
        try:
            if args.command == "build":
                from article_to_video.build import build_project
                from article_to_video.review import make_review

                res = build_project(args.project, tts=args.tts, jobs=args.jobs, log=log)
                print(f"完成：{res.video}（{res.duration:.2f} 秒）")
                print(f"字幕檔：{res.srt}")
                failed = "成片做好了，但審片頁沒更新"
                review = make_review(res.plan, video=res.video, contributors=default_contributors(), jobs=args.jobs,
                                     log=log)
            else:
                from article_to_video.review import preview_project

                review = preview_project(args.project, tts=args.tts, contributors=default_contributors(),
                                         jobs=args.jobs, log=log)
        except KNOWN_ERRORS as e:
            rec.finish(e)
            print(f"{failed}：{e}", file=sys.stderr)
            return 1
        except BaseException as e:
            rec.finish(e, unexpected=True)
            raise
        rec.finish()
        _show_review(review.page, no_open=args.no_open)
        return 0
    if args.command == "feedback":
        import json
        from pathlib import Path

        from article_to_video.feedback import parse_feedback, project_shot_ids

        try:
            ids = project_shot_ids(args.project)
        except ProjectError as e:
            print(f"讀不到分鏡設定：{e}", file=sys.stderr)
            return 1
        text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
        fb = parse_feedback(text)
        out = {
            "project": fb.project,
            "version": fb.version,
            "shots": [{"number": c.number, "id": sid, "text": c.text} for c, sid in zip(fb.shots, fb.resolve(ids))],
            "overall": fb.overall,
            "problems": fb.check(ids),
        }
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if args.command == "report-issue":
        from article_to_video.issue_report import create_report

        try:
            rep = create_report(args.project, shots=args.shot, note=args.note, out_dir=args.out_dir,
                                log=lambda m: print(m, flush=True))
        except OSError as e:
            print(f"打包失敗：{e}", file=sys.stderr)
            return 1
        for w in rep.warnings:
            print(f"注意：{w}")
        print(f"已打包：{rep.zip_path}（{rep.size_mb:.1f} MB）")
        print(f"裡面有：{'、'.join(rep.contents)}")
        print("沒放：文章全文、成片、配音檔；路徑裡的使用者名稱已遮掉")
        print("請把這個檔案用 LINE 傳給 Danny。")
        return 0
    if args.command == "reproduce-issue":
        from article_to_video.issue_report import ReportError, reproduce_report

        try:
            res = reproduce_report(args.zip, out_dir=args.out, shots=args.shot, log=lambda m: print(m, flush=True))
        except ReportError as e:
            print(f"沒辦法重現：{e}", file=sys.stderr)
            return 1
        print(f"解開在：{res.out_dir}")
        if res.report_note:
            print(f"作者說：{res.report_note}")
        if res.author_environment:
            print("作者那台的環境：\n" + res.author_environment)
        for n in res.notes:
            print(f"注意：{n}")
        for cid, mp4 in res.clips.items():
            print(f"片段 {cid}：{mp4}")
        for sid, ps in res.stills.items():
            print(f"分鏡 {sid} 靜態圖：{ps[0].parent}（{len(ps)} 張）")
        if res.errors:
            print("重現出錯誤：\n" + "\n\n".join(res.errors), file=sys.stderr)
            return 1
        return 0
    return 2

"""指令列：`uv run --project <外掛根目錄> python -m article_to_video <子指令> …`

子指令：
  build <專案資料夾> [--tts edge|fake] [--jobs N]   建置成片＋字幕檔
  extract <專案資料夾> <文章檔|->                   擷取文章到 <專案>/article/（- ＝從標準輸入讀貼上的文字）
  report-issue [專案資料夾] [--shot 分鏡]… [--note 文字] [--out-dir 資料夾]
                                                  回報問題：打包 zip（預設放桌面）
  reproduce-issue <zip> [--out 資料夾] [--shot 分鏡]…   （維護者用）從回報的 zip 重渲出錯的分鏡

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
    e = sub.add_parser("extract", help="擷取文章全文與原圖到 <專案>/article/")
    e.add_argument("project", help="專案資料夾（沒有會自動建立）")
    e.add_argument("source", help="文章檔（.pdf .docx .txt .md .html 或 Google 文件的網頁 .zip）；- ＝從標準輸入讀貼上的文字")
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


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)
    if args.command == "build":
        from article_to_video.build import build_project
        from article_to_video.issue_report import RunRecord

        rec = RunRecord(args.project, "build", {"tts": args.tts, "jobs": args.jobs})  # 回報問題時打包
        try:
            res = build_project(args.project, tts=args.tts, jobs=args.jobs, log=rec.tee(lambda m: print(m, flush=True)))
        except KNOWN_ERRORS as e:
            rec.finish(e)
            print(f"建置失敗：{e}", file=sys.stderr)
            return 1
        except BaseException as e:
            rec.finish(e, unexpected=True)
            raise
        rec.finish()
        print(f"完成：{res.video}（{res.duration:.2f} 秒）")
        print(f"字幕檔：{res.srt}")
        return 0
    if args.command == "extract":
        from article_to_video.article import run_extract

        return run_extract(args.project, args.source)
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

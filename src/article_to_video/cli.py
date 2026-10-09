"""指令列：`uv run --project <外掛根目錄> python -m article_to_video <子指令> …`

子指令：
  build <專案資料夾> [--tts edge|fake] [--jobs N]   建置成片＋字幕檔
  extract <專案資料夾> <文章檔|->                   擷取文章到 <專案>/article/（- ＝從標準輸入讀貼上的文字）

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

        try:
            res = build_project(args.project, tts=args.tts, jobs=args.jobs, log=lambda m: print(m, flush=True))
        except KNOWN_ERRORS as e:
            print(f"建置失敗：{e}", file=sys.stderr)
            return 1
        print(f"完成：{res.video}（{res.duration:.2f} 秒）")
        print(f"字幕檔：{res.srt}")
        return 0
    if args.command == "extract":
        from article_to_video.article import run_extract

        return run_extract(args.project, args.source)
    return 2

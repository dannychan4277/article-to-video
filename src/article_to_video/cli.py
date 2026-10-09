"""指令列：`uv run --project <外掛根目錄> python -m article_to_video <子指令> …`

子指令：
  preview <專案資料夾> [--tts edge|fake] [--jobs N] [--no-open]   預覽：只出分鏡靜態圖＋審片頁（不渲整支片）
  build <專案資料夾> [--tts edge|fake] [--jobs N] [--no-open]     建置成片＋字幕檔，審片頁內嵌成片
  feedback <專案資料夾> [--file 檔]                   審片頁複製出來的留言（stdin）→ JSON（格式見 feedback.py）

preview、build 做完會用 macOS 的 open 在預設瀏覽器打開審片頁；--no-open 或環境變數
ARTICLE_TO_VIDEO_NO_OPEN=1（測試、CI）就不打開。

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
    if args.command == "build":
        from article_to_video.build import build_project

        try:
            res = build_project(args.project, tts=args.tts, jobs=args.jobs, log=lambda m: print(m, flush=True))
        except KNOWN_ERRORS as e:
            print(f"建置失敗：{e}", file=sys.stderr)
            return 1
        print(f"完成：{res.video}（{res.duration:.2f} 秒）")
        print(f"字幕檔：{res.srt}")
        from article_to_video.review import default_contributors, make_review

        try:
            review = make_review(res.plan, video=res.video, contributors=default_contributors(), jobs=args.jobs,
                                 log=lambda m: print(m, flush=True))
        except KNOWN_ERRORS as e:
            print(f"成片做好了，但審片頁沒更新：{e}", file=sys.stderr)
            return 1
        _show_review(review.page, no_open=args.no_open)
        return 0
    if args.command == "preview":
        from article_to_video.review import default_contributors, preview_project

        try:
            review = preview_project(args.project, tts=args.tts, contributors=default_contributors(), jobs=args.jobs,
                                     log=lambda m: print(m, flush=True))
        except KNOWN_ERRORS as e:
            print(f"預覽失敗：{e}", file=sys.stderr)
            return 1
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
    return 2

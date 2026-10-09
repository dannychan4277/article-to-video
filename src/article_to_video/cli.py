"""指令列：`uv run --project <外掛根目錄> python -m article_to_video <子指令> …`

子指令：
  preview <專案資料夾> [--aspect 16:9|9:16] [--tts edge|fake] [--jobs N] [--no-open] [--data-dir D]
                                                  預覽：只出分鏡靜態圖＋審片頁（不渲整支片）
  build <專案資料夾> [--aspect 16:9|9:16] [--tts edge|fake] [--jobs N] [--no-open] [--data-dir D]
                                                  建置成片＋字幕檔，審片頁內嵌成片
  boxes <專案資料夾> [--aspect 16:9|9:16] [--tts edge|fake] [--jobs N] [--data-dir D]
                                                  列出 Vox 框、渲每個框的靜態圖（照這個比例）、印核對代號
  confirm-box <專案資料夾> <核對代號>… [--note 文字]   記下 Claude 看過靜態圖、確認過的框（見 boxes.py）
  feedback <專案資料夾> [--file 檔]                   審片頁複製出來的留言（stdin）→ JSON（格式見 feedback.py）
  extract <專案資料夾> <文章檔|->                   擷取文章到 <專案>/article/（- ＝從標準輸入讀貼上的文字）
  report-issue [專案資料夾] [--shot 分鏡]… [--note 文字] [--out-dir 資料夾]
                                                  回報問題：打包 zip（預設放桌面）
  reproduce-issue <zip> [--out 資料夾] [--shot 分鏡]…   （維護者用）從回報的 zip 重渲出錯的分鏡
  settings show [--json] [--data-dir D] [--project P]               看使用者設定（或某支片的設定快照）
  settings set [--channel …] [--logo 圖檔|none] [--brand-color #RRGGBB|none] [--voice 曉臻|曉雨|雲哲]
               [--rate +5%] [--music calm|pulse|minimal|none] [--aspect 16:9|9:16] [--data-dir D] [--project P]
                                                                     改使用者設定；加 --project 只改那支片的快照
  samples music --out 資料夾 [--seconds 20] [--data-dir D]           每首配樂一個試聽檔 music-<名字>.wav
  samples voice --out 資料夾 [--rate +5%] [--text …] [--tts edge|fake] 每個聲音一個試聽檔 voice-<代號>.wav（edge 要連網）

preview、build 做完會用 macOS 的 open 在預設瀏覽器打開審片頁；--no-open 或環境變數
ARTICLE_TO_VIDEO_NO_OPEN=1（測試、CI）就不打開。preview、build 的輸出與錯誤記在
<專案>/build/last-run.{log,json}（只留最後一次），回報問題時打包。

--aspect：畫面比例。作者說「直式」就給 9:16（也收「直式」「9x16」）；不給就用設定快照裡的預設比例
（使用者設定 aspect，沒設就是 16:9）。直式的產物：成片-9x16.mp4、字幕-9x16.srt、審片頁-9x16.html、build/9x16/，
跟 16:9 的並存、互不覆蓋。

--data-dir：使用者資料位置，主 skill 傳 ${CLAUDE_PLUGIN_DATA}；沒給就看環境變數，再沒有就用
~/Library/Application Support/article-to-video（見 user_settings.py）。

錯誤一律印繁中說明到 stderr、結束碼 1（不印 Python traceback）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from article_to_video.assemble import AssemblyError
from article_to_video.clips import ClipRenderError
from article_to_video.ffmpeg import FFmpegNotFoundError
from article_to_video.narration import TTS_ENGINES, NarrationError
from article_to_video.project import ZH_TW_VOICES, ProjectError
from article_to_video.user_settings import SettingsError

KNOWN_ERRORS = (ProjectError, NarrationError, ClipRenderError, FFmpegNotFoundError, AssemblyError, SettingsError)

_SET_OPTIONS = {  # 選項 → 設定鍵
    "channel": "頻道名（片尾卡上的名字）",
    "logo": "logo 圖檔路徑（PNG／JPG／WebP），none＝拿掉",
    "brand_color": "品牌色色碼，例 #1A5FB4；none＝圖表用預設紅黑白",
    "voice": "聲音：曉臻、曉雨、雲哲（或 zh-TW-…Neural 代號）",
    "rate": "語速，例 +5%、-10%",
    "music": "配樂：calm（沉穩鋼琴）、pulse（新聞脈動）、minimal（極簡電子）、none",
    "aspect": "預設比例：16:9（橫式）或 9:16（直式）",
}
_DATA_DIR_HELP = "使用者資料位置（主 skill 傳 ${CLAUDE_PLUGIN_DATA}）"
VOICE_SAMPLE = "大家好，歡迎來到我的頻道。今天用三分鐘，帶你看懂殖利率曲線在說什麼。"


_ASPECT_HELP = "畫面比例：16:9（橫式）或 9:16（直式，也收「直式」「9x16」）；不給就用設定裡的預設比例"


def _aspect(value: str):
    from article_to_video.timeline import video_format

    try:
        return video_format(value)
    except ProjectError as e:
        raise argparse.ArgumentTypeError(e.problems[0]) from None


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="article_to_video", description="把財經文章做成解說影片")
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="建置成片與字幕檔")
    b.add_argument("project", help="專案資料夾（裡面有 narration.json、shots.json）")
    b.add_argument("--tts", choices=sorted(TTS_ENGINES), help="配音層（預設看 settings.json，沒寫就是 edge）")
    b.add_argument("--jobs", type=int, help="最多同時渲幾個片段（預設 3）")
    b.add_argument("--no-open", action="store_true", help="做完不要自動在瀏覽器打開審片頁")
    b.add_argument("--data-dir", help=_DATA_DIR_HELP)
    b.add_argument("--aspect", type=_aspect, help=_ASPECT_HELP)
    v = sub.add_parser("preview", help="只出分鏡靜態圖與審片頁（不渲整支片）")
    v.add_argument("project", help="專案資料夾（裡面有 narration.json、shots.json）")
    v.add_argument("--tts", choices=sorted(TTS_ENGINES), help="配音層（預設看 settings.json，沒寫就是 edge）")
    v.add_argument("--jobs", type=int, help="最多同時渲幾個分鏡的靜態圖（預設 4）")
    v.add_argument("--no-open", action="store_true", help="做完不要自動在瀏覽器打開審片頁")
    v.add_argument("--data-dir", help=_DATA_DIR_HELP)
    v.add_argument("--aspect", type=_aspect, help=_ASPECT_HELP)
    bx = sub.add_parser("boxes", help="列出 Vox 框、渲每個框的靜態圖、看哪些還沒核對")
    bx.add_argument("project", help="專案資料夾")
    bx.add_argument("--aspect", type=_aspect, help=_ASPECT_HELP)
    bx.add_argument("--tts", choices=sorted(TTS_ENGINES), help="配音層（預設看 settings.json，沒寫就是 edge）")
    bx.add_argument("--jobs", type=int, help="最多同時渲幾個片段的框靜態圖（預設 4）")
    bx.add_argument("--data-dir", help=_DATA_DIR_HELP)
    cb = sub.add_parser("confirm-box", help="記下 Claude 看過靜態圖、確認沒問題的框（核對代號從 boxes 指令來）")
    cb.add_argument("project", help="專案資料夾")
    cb.add_argument("tokens", nargs="*", help="核對代號，例 SH02:1@1a2b3c4d（可以一次給好幾個）")
    cb.add_argument("--note", default="", help="備註（例：框在「十年期」那一列的 3.96 上）")
    f = sub.add_parser("feedback", help="把審片頁「複製給 Claude」的文字（從 stdin 讀）解析成 JSON")
    f.add_argument("project", help="專案資料夾（用來把分鏡編號對到分鏡 id）")
    f.add_argument("--file", help="從這個檔讀，不從 stdin 讀")
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

    s = sub.add_parser("settings", help="看或改使用者設定")
    ss = s.add_subparsers(dest="action", required=True)
    show = ss.add_parser("show", help="看設定")
    show.add_argument("--json", action="store_true", help="印 JSON（給程式讀）")
    show.add_argument("--data-dir", help=_DATA_DIR_HELP)
    show.add_argument("--project", help="看這支片的設定快照")
    st = ss.add_parser("set", help="改設定")
    for key, help_ in _SET_OPTIONS.items():
        st.add_argument("--" + key.replace("_", "-"), dest=key, help=help_)
    st.add_argument("--data-dir", help=_DATA_DIR_HELP)
    st.add_argument("--project", help="只改這支片的設定快照（不動使用者設定）")

    sm = sub.add_parser("samples", help="產生試聽檔（安裝時挑聲音、挑配樂用）")
    sms = sm.add_subparsers(dest="what", required=True)
    pm = sms.add_parser("music", help="每首配樂一個試聽檔")
    pm.add_argument("--out", required=True, help="試聽檔放哪個資料夾")
    pm.add_argument("--seconds", type=float, default=20.0, help="每個試聽檔幾秒（預設 20）")
    pm.add_argument("--data-dir", help=_DATA_DIR_HELP)
    pvv = sms.add_parser("voice", help="每個聲音一個試聽檔（edge-tts 要連網）")
    pvv.add_argument("--out", required=True, help="試聽檔放哪個資料夾")
    pvv.add_argument("--rate", default=None, help="語速（預設用使用者設定）")
    pvv.add_argument("--text", default=VOICE_SAMPLE, help="試聽唸的句子")
    pvv.add_argument("--tts", choices=sorted(TTS_ENGINES), default="edge")
    pvv.add_argument("--data-dir", help=_DATA_DIR_HELP)
    return p


def _show_review(page, *, no_open: bool) -> None:
    from article_to_video.review import auto_open_allowed, open_in_browser

    print(f"審片頁：{page}")
    if no_open or not auto_open_allowed():
        return
    if not open_in_browser(page):
        print("沒能自動打開審片頁，請在 Finder 找到上面這個檔案，點兩下打開。")


def _samples(args: argparse.Namespace) -> int:
    from pathlib import Path

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.what == "music":
        from article_to_video.music import TRACKS, write_preview

        for name, track in TRACKS.items():
            f = write_preview(name, out / f"music-{name}.wav", args.seconds, args.data_dir)
            print(f"{track.label}（{name}）：{track.mood}\n  {f}")
        return 0

    from article_to_video.narration import get_tts_engine, narrate
    from article_to_video.user_settings import load_user_settings, normalize_value, resolve_data_dir

    rate = normalize_value("rate", args.rate) if args.rate else load_user_settings(args.data_dir).rate
    cache = resolve_data_dir(args.data_dir) / "previews" / "voice-cache"
    for vid, name in ZH_TW_VOICES.items():
        audio = narrate({"sample": args.text}, engine=get_tts_engine(args.tts), voice=vid, rate=rate, cache_dir=cache)
        f = out / f"voice-{vid}.wav"
        f.write_bytes(audio["sample"].audio.read_bytes())
        print(f"{name}（{vid}）：\n  {f}")
    return 0


def _boxes(args: argparse.Namespace) -> int:
    """boxes：列出每個 Vox 框＋渲靜態圖；confirm-box：記下核對過的框。"""
    from pathlib import Path

    from article_to_video.boxes import box_statuses, confirm_boxes, format_box_listing, render_box_stills

    if args.command == "confirm-box":
        try:
            res = confirm_boxes(args.project, args.tokens, note=args.note)
        except ProjectError as e:
            print(f"核對失敗：{e}", file=sys.stderr)
            return 1
        print("已記下核對：" + "、".join(b.id for b in res.confirmed))
        if res.remaining:
            print("還沒核對：" + "、".join(b.id for b in res.remaining))
        else:
            print("所有 Vox 框都核對過了，可以正式出片。")
        return 0

    from article_to_video.build import plan_build
    from article_to_video.issue_report import RunRecord

    rec = RunRecord(args.project, "boxes",
                    {"tts": args.tts, "jobs": args.jobs, "aspect": args.aspect.name if args.aspect else None})
    log = rec.tee(lambda m: print(m, flush=True))
    try:
        plan = plan_build(args.project, tts=args.tts, fmt=args.aspect, data_dir=args.data_dir, log=log)
        statuses = box_statuses(plan)
        render_box_stills(plan, statuses, jobs=args.jobs, log=log)
    except KNOWN_ERRORS as e:
        rec.finish(e)
        print(f"列不出框：{e}", file=sys.stderr)
        return 1
    except BaseException as e:
        rec.finish(e, unexpected=True)
        raise
    log(format_box_listing(statuses, Path(args.project).absolute()))
    rec.finish()
    return 0


def _describe(values: dict) -> str:
    from article_to_video.music import TRACKS
    from article_to_video.user_settings import ASPECTS

    music = values.get("music")
    lines = [
        f"頻道名：{values.get('channel') or '（還沒設定：片尾卡要用，請設定）'}",
        f"logo：{values.get('logo') or '（沒有）'}",
        f"品牌色：{values.get('brand_color') or '（沒設定：圖表用預設紅黑白）'}",
        f"聲音：{ZH_TW_VOICES.get(values.get('voice'), '?')}（{values.get('voice')}）",
        f"語速：{values.get('rate')}",
        f"配樂：{'不要配樂' if music == 'none' else f'{TRACKS[music].label}（{music}）' if music in TRACKS else music}",
        f"預設比例：{ASPECTS.get(values.get('aspect'), values.get('aspect'))}",
    ]
    return "\n".join("  " + ln for ln in lines)


def _settings(args: argparse.Namespace) -> int:
    from pathlib import Path

    from article_to_video.project import SETTINGS_FILE
    from article_to_video.user_settings import (
        SNAPSHOT_KEYS, load_user_settings, resolve_data_dir, update_project_settings, update_user_settings,
    )

    if args.action == "set":
        changes = {k: getattr(args, k) for k in _SET_OPTIONS if getattr(args, k) is not None}
        if not changes:
            print("沒有要改的設定（例：settings set --voice 曉雨）", file=sys.stderr)
            return 1
        if args.project:
            snap = update_project_settings(args.project, changes)
            print(f"已更新這支片的設定快照：{Path(args.project) / SETTINGS_FILE}")
            print(_describe({k: snap.get(k) for k in SNAPSHOT_KEYS}))
        else:
            us = update_user_settings(changes, args.data_dir)
            print(f"已更新使用者設定：{resolve_data_dir(args.data_dir) / 'settings.json'}")
            print(_describe(us.to_json()))
        return 0

    if args.project:
        path = Path(args.project) / SETTINGS_FILE
        values = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        where = f"這支片的設定快照（{path}）"
    else:
        values = load_user_settings(args.data_dir).to_json()
        where = f"使用者設定（{resolve_data_dir(args.data_dir) / 'settings.json'}）"
    if args.json:
        print(json.dumps(values, ensure_ascii=False, indent=1))
    else:
        print(where)
        print(_describe(values))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)
    if args.command in ("build", "preview"):
        from article_to_video.build import BlockedBuild
        from article_to_video.issue_report import RunRecord
        from article_to_video.numbers import check_numbers, summary_line
        from article_to_video.review import default_contributors, make_review

        # 回報問題時打包：preview、build（含審片頁）出的錯都記進 last-run
        rec = RunRecord(args.project, args.command,
                        {"tts": args.tts, "jobs": args.jobs, "aspect": args.aspect.name if args.aspect else None})
        log = rec.tee(lambda m: print(m, flush=True))
        failed = "建置失敗" if args.command == "build" else "預覽失敗"
        try:
            if args.command == "build":
                from article_to_video.build import build_project

                # 正式出片前先數字對帳：有無出處的數字就擋下（不渲染、不出片）
                res = build_project(args.project, tts=args.tts, jobs=args.jobs, fmt=args.aspect, data_dir=args.data_dir,
                                    preflight=[check_numbers], log=log)
                print(f"完成：{res.video}（{res.duration:.2f} 秒）")
                print(f"字幕檔：{res.srt}")
                failed = "成片做好了，但審片頁沒更新"
                review = make_review(res.plan, video=res.video, contributors=default_contributors(), jobs=args.jobs,
                                     log=log)
            else:
                from article_to_video.review import preview_project

                review = preview_project(args.project, tts=args.tts, contributors=default_contributors(),
                                         jobs=args.jobs, data_dir=args.data_dir, fmt=args.aspect, log=log)
                if review.plan is not None:
                    log(summary_line(review.plan))
        except BlockedBuild as e:
            # 擋下正式出片：審片頁照這一版重寫（沒有成片、被擋的數字標在卡上），作者打開看到的就是現況
            rec.finish(e)
            print(f"{failed}：{e}", file=sys.stderr)
            try:
                review = make_review(e.plan, contributors=default_contributors(), jobs=args.jobs, log=log)
                print(f"審片頁已更新（被擋下的原因標在卡片上）：{review.page}")
            except KNOWN_ERRORS as e2:
                print(f"審片頁也沒更新：{e2}", file=sys.stderr)
            return 1
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
    if args.command in ("boxes", "confirm-box"):
        return _boxes(args)
    if args.command == "feedback":
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
    if args.command == "settings":
        try:
            return _settings(args)
        except SettingsError as e:
            print(f"設定失敗：{e}", file=sys.stderr)
            return 1
    if args.command == "samples":
        try:
            return _samples(args)
        except KNOWN_ERRORS as e:
            print(f"試聽檔產生失敗：{e}", file=sys.stderr)
            return 1
    return 2

"""指令列：`uv run --project <外掛根目錄> python -m article_to_video <子指令> …`

子指令：
  build <專案資料夾> [--tts edge|fake] [--jobs N] [--data-dir D]   建置成片＋字幕檔
  settings show [--json] [--data-dir D] [--project P]               看使用者設定（或某支片的設定快照）
  settings set [--channel …] [--logo 圖檔|none] [--brand-color #RRGGBB|none] [--voice 曉臻|曉雨|雲哲]
               [--rate +5%] [--music calm|pulse|minimal|none] [--aspect 16:9|9:16] [--data-dir D] [--project P]
                                                                     改使用者設定；加 --project 只改那支片的快照

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


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="article_to_video", description="把財經文章做成解說影片")
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="建置成片與字幕檔")
    b.add_argument("project", help="專案資料夾（裡面有 narration.json、shots.json）")
    b.add_argument("--tts", choices=sorted(TTS_ENGINES), help="配音層（預設看 settings.json，沒寫就是 edge）")
    b.add_argument("--jobs", type=int, help="最多同時渲幾個片段（預設 3）")
    b.add_argument("--data-dir", help="使用者資料位置（主 skill 傳 ${CLAUDE_PLUGIN_DATA}）")

    s = sub.add_parser("settings", help="看或改使用者設定")
    ss = s.add_subparsers(dest="action", required=True)
    show = ss.add_parser("show", help="看設定")
    show.add_argument("--json", action="store_true", help="印 JSON（給程式讀）")
    show.add_argument("--data-dir")
    show.add_argument("--project", help="看這支片的設定快照")
    st = ss.add_parser("set", help="改設定")
    for key, help_ in _SET_OPTIONS.items():
        st.add_argument("--" + key.replace("_", "-"), dest=key, help=help_)
    st.add_argument("--data-dir")
    st.add_argument("--project", help="只改這支片的設定快照（不動使用者設定）")
    return p


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
    if args.command == "build":
        from article_to_video.build import build_project

        try:
            res = build_project(args.project, tts=args.tts, jobs=args.jobs, data_dir=args.data_dir,
                                log=lambda m: print(m, flush=True))
        except KNOWN_ERRORS as e:
            print(f"建置失敗：{e}", file=sys.stderr)
            return 1
        print(f"完成：{res.video}（{res.duration:.2f} 秒）")
        print(f"字幕檔：{res.srt}")
        return 0
    if args.command == "settings":
        try:
            return _settings(args)
        except SettingsError as e:
            print(f"設定失敗：{e}", file=sys.stderr)
            return 1
    return 2

"""指令列：`uv run --project <外掛根目錄> python -m article_to_video <子指令> …`

子指令：
  new <文章檔|-> [--title 片名] [--root 影片資料夾]  開新專案：~/Movies/解說影片/<日期>-<片名>/專案/，擷取文章（含截字檢查）
  projects [關鍵字] [--root 影片資料夾] [--json]       列出做過的影片（最近動過的在前面）
  preview <專案資料夾> [--aspect 16:9|9:16] [--tts 配音層] [--jobs N] [--no-open] [--data-dir D]
                                                  預覽：只出分鏡靜態圖＋審片頁（不渲整支片）
  build <專案資料夾> [--aspect 16:9|9:16] [--tts 配音層] [--jobs N] [--no-open] [--data-dir D]
                                                  建置成片＋字幕檔，審片頁內嵌成片
  open-review <專案資料夾> [--aspect …] [--tts …] [--no-open] [--data-dir D]
                                                  更新審片頁（最新的檢查結果、還是最新的成片就內嵌）並在瀏覽器打開給作者看
                                                  （--no-open：只更新、不打開）
  publish <專案資料夾> --version N [--aspect …] [--tts …] [--jobs N] [--no-open] [--data-dir D] [--out 資料夾]
                                                  出片：作者點頭（第 N 版）之後，建置成片、自動檢查，把成片和字幕檔放進
                                                  「影片／解說影片／日期-標題」（見 publish.py、videos.py）
  boxes <專案資料夾> [--aspect 16:9|9:16] [--tts 配音層] [--jobs N] [--data-dir D]
                                                  （內部用）列出 Vox 框、渲每個框的靜態圖（照這個比例）、印核對代號
  confirm-box <專案資料夾> <核對代號>… [--note 文字] [--data-dir D]
                                                  （內部用）記下 Claude 看過靜態圖、確認過的框（見 boxes.py）
                                                  作者的流程只用財經圖表（docs/adr/0001-只用財經圖表風格.md）：Vox 分鏡只有
                                                  內部開關 ARTICLE_TO_VIDEO_INTERNAL_VOX=1 才讀得到，這兩個指令平常用不到
  checks <專案資料夾> [--aspect …] [--tts …] [--jobs N] [--data-dir D] [--json]
                                                  自動檢查（build 之後、給作者看之前）：每個片段跑 qa、白底片段查字幕帶，彙整到分鏡，
                                                  寫 build/<比例>/checks.json、更新審片頁（見 auto_checks.py）
  review-prep <專案資料夾> [--aspect …] [--tts …] [--data-dir D]
                                                  獨立審片：從成片準備素材、印出派 agent 用的 prompt（見 independent_review.py）
  review-frames <專案資料夾> [--aspect …] (--at 秒… | --from 秒 --to 秒 [--step 0.25])
                                                  （審片 agent 用）從成片抽全解析度幀
  review-record <專案資料夾> [--aspect …] [--file 檔|-] [--tts …] [--data-dir D]
                                                  記下審片 agent 的回答（預設讀素材資料夾的 review.json），更新審片頁
  resolve <專案資料夾> <代號>… (--fixed 說明 | --accept 理由) [--by 作者|Claude] [--data-dir D]
                                                  記下必修的處理：改好了（獨立審片）或接受不改；記了就不再擋出片
  lint <專案資料夾>                                 旁白規則檢查（narration.json；規則見 lint.py）；有要改的結束碼 1
  narrate <專案資料夾> [--tts 配音層] [--data-dir D]
                                                  配音（寫分鏡之前）：每段配好音，印總長、每段起點與長度、每個詞開始唸的秒數
  feedback <專案資料夾> [--file 檔]                   審片頁複製出來的留言（stdin）→ JSON（格式見 feedback.py）：
                                                  每則對到分鏡 id、種類、畫面上的字、唸到的旁白段與文字
  outline <專案資料夾>                              每個分鏡的編號、種類、畫什麼、唸哪幾個旁白段（照編號改舊片時用）
  extract <專案資料夾> <文章檔|->                   擷取文章到 <專案>/article/（- ＝從標準輸入讀貼上的文字）
  report-issue [專案資料夾] [--shot 分鏡]… [--note 文字] [--out-dir 資料夾]
                                                  回報問題：打包 zip（預設放桌面）
  reproduce-issue <zip> [--out 資料夾] [--shot 分鏡]…   （維護者用）從回報的 zip 重渲出錯的分鏡
  settings show [--json] [--data-dir D] [--project P]               看使用者設定（或某支片的設定快照）
  settings set [--channel …] [--logo 圖檔|none] [--brand-color #RRGGBB|none] [--voice 曉臻|曉雨|雲哲]
               [--rate +5%] [--music calm|pulse|minimal|none] [--aspect 16:9|9:16] [--independent-review 開|關]
               [--tts edge|elevenlabs|google] [--elevenlabs-voice 代號] [--elevenlabs-model 模型] [--google-voice 聲音]
               [--data-dir D] [--project P]
                                                                     改使用者設定；加 --project 只改那支片的快照
  samples music --out 資料夾 [--seconds 20] [--data-dir D]           每首配樂一個試聽檔 music-<名字>.wav
  samples voice --out 資料夾 [--rate +5%] [--text …] [--tts 配音層] [--voice 代號]…
                                                     每個聲音一個試聽檔 voice-<代號>.wav（要連網；印出送出幾個字）
  check-env [--json] [--try-voice] [--data-dir D]    檢查環境：逐項列出狀態與修法；有問題結束碼 1
                                                     （--try-voice：付費配音真的試唸兩個字，確認 API key 能用）
  install [--data-dir D]                             安裝 Homebrew（開終端機請作者輸入密碼）、ffmpeg-full、Chromium，
                                                     已經有的略過；最後試 edge-tts 連線。沒全部裝好結束碼 1
  test-video [--out 資料夾] [--tts 配音層] [--no-open] [--data-dir D]
                                                     用外掛自帶的範例做一支約 10 秒的測試片並打開（安裝最後一步）

preview、build、open-review 做完會用 macOS 的 open 在預設瀏覽器打開審片頁；--no-open 或環境變數
ARTICLE_TO_VIDEO_NO_OPEN=1（測試、CI）就不打開。會渲染或改審片頁的子指令（preview、build、publish、open-review、
narrate、boxes、checks、review-prep、review-record、test-video）的輸出與錯誤記在 <專案>/build/last-run.{log,json}
（只留最後一次），回報問題時打包。

結構：每個子指令一個處理函式（HANDLERS），共用的選項（--tts、--jobs、--no-open、--data-dir、--aspect、--json）
各寫一次（_shared_options，argparse 的 parents），留紀錄、印錯誤、被擋下時重寫審片頁都在 _recorded。

--tts：配音層 edge（免費，預設）、elevenlabs、google（付費，API key 放哪裡見 tts_providers.py、docs/maintaining.md）、
fake（測試）。不給就用設定快照的 tts。配音之後印「這次送出 N 字給配音服務」（付費配音照字數算錢；全用快取是 0）。

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
from collections.abc import Callable, Sequence

from article_to_video.assemble import AssemblyError
from article_to_video.clips import ClipRenderError
from article_to_video.commands import aspect_args, cli_command
from article_to_video.ffmpeg import FFmpegNotFoundError
from article_to_video.narration import TTS_ENGINES, NarrationError
from article_to_video.progress import Log
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
    "tts": "配音：edge（微軟，免費，預設）、elevenlabs、google（這兩家付費、要 API key，key 放哪裡見 docs/maintaining.md）",
    "elevenlabs_voice": "ElevenLabs 的聲音代號（voice_id，一串英數字）",
    "elevenlabs_model": "ElevenLabs 的模型（預設 eleven_multilingual_v2）",
    "google_voice": "Google 的聲音：cmn-TW-Wavenet-A（女，預設）、Wavenet-B／C（男）、Standard-A／B／C（較便宜）",
    "independent_review": "建好審片用的成片、作者看審片頁之前，派一個只看成片的 agent 獨立審片一輪：開（預設）或 關"
                          "（全部影片共用，不能加 --project）",
}
_TTS_LABELS = {"edge": "edge（微軟，免費）", "elevenlabs": "ElevenLabs（付費）", "google": "Google（付費，每月有免費額度）",
               "fake": "fake（測試用，沒有聲音）"}
VOICE_SAMPLE = "大家好，歡迎來到我的頻道。今天用三分鐘，帶你看懂殖利率曲線在說什麼。"
_PROJECT = "專案資料夾"
_PROJECT_FILES = "專案資料夾（裡面有 narration.json、shots.json）"
_ARTICLE_SOURCE = "文章檔（.pdf .docx .txt .md .html 或 Google 文件的網頁 .zip）；- ＝從標準輸入讀貼上的文字"


# ---------------------------------------------------------------- 選項


def _aspect(value: str):
    from article_to_video.timeline import video_format

    try:
        return video_format(value)
    except ProjectError as e:
        raise argparse.ArgumentTypeError(e.problems[0]) from None


def _shared_options() -> dict[str, argparse.ArgumentParser]:
    """好幾個子指令共用的選項（argparse 的 parents）：說明只寫一次。"""
    def option(*flags: str, **kw) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument(*flags, **kw)
        return p

    return {
        "tts": option("--tts", choices=sorted(TTS_ENGINES), help="配音層（預設看 settings.json，沒寫就是 edge）"),
        "jobs": option("--jobs", type=int,
                       help="最多同時開幾個 Chromium 渲片段、靜態圖或跑 qa（預設照這台電腦的核心數，最多 3～4 個）"),
        "no_open": option("--no-open", action="store_true", help="做完不要自動打開（審片頁、成片）"),
        "data_dir": option("--data-dir", help="使用者資料位置（主 skill 傳 ${CLAUDE_PLUGIN_DATA}）"),
        "aspect": option("--aspect", type=_aspect,
                         help="畫面比例：16:9（橫式）或 9:16（直式，也收「直式」「9x16」）；不給就用設定裡的預設比例"),
        "json": option("--json", action="store_true", help="印 JSON（給程式讀）"),
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="article_to_video", description="把財經文章做成解說影片")
    sub = p.add_subparsers(dest="command", required=True)
    o = _shared_options()

    def add(name: str, help_: str, *shared: str, project: str | None = _PROJECT, into=sub) -> argparse.ArgumentParser:
        sp = into.add_parser(name, help=help_, parents=[o[k] for k in shared])
        if project:
            sp.add_argument("project", help=project)
        return sp

    add("build", "建置成片與字幕檔", "tts", "jobs", "no_open", "data_dir", "aspect", project=_PROJECT_FILES)
    add("preview", "只出分鏡靜態圖與審片頁（不渲整支片）", "tts", "jobs", "no_open", "data_dir", "aspect",
        project=_PROJECT_FILES)
    pb = add("publish", "出片：作者點頭之後，建置成片、跑自動檢查，把成片和字幕檔放進「影片／解說影片／日期-標題」",
             "tts", "jobs", "no_open", "data_dir", "aspect")
    pb.add_argument("--version", type=int, required=True, help="作者點頭的是審片頁第幾版（頁首寫的）")
    pb.add_argument("--out", help="成片放哪個資料夾（預設：這支片的「日期-標題」資料夾）")
    add("open-review", "更新審片頁（帶上最新的檢查結果）並在瀏覽器打開給作者看；--no-open 只更新、不打開",
        "tts", "no_open", "data_dir", "aspect")
    add("narrate", "配音（寫分鏡之前）：每段旁白配好音，印出每段的起點、長度與每個詞開始唸的秒數", "tts", "data_dir",
        project="專案資料夾（裡面有 narration.json；還不用有 shots.json）")
    add("boxes", "（內部用，作者的流程只用財經圖表）列出 Vox 框、渲每個框的靜態圖、看哪些還沒核對", "tts", "jobs", "data_dir", "aspect")
    add("checks", "自動檢查（build 之後、給作者看之前跑）：每個片段跑 qa、白底片段查字幕帶，結果寫進審片頁",
        "tts", "jobs", "data_dir", "aspect", "json")
    add("review-prep", "獨立審片：從成片準備審片素材（總覽圖、畫面事件圖、字幕檔），印出派 agent 的 prompt",
        "tts", "data_dir", "aspect")
    rf = add("review-frames", "（審片 agent 用）從成片抽全解析度幀：--at 秒（可重複），或 --from --to --step")
    rf.add_argument("--aspect", type=_aspect, help="哪個比例的成片（預設：最近準備審片素材的那個）")
    rf.add_argument("--at", action="append", default=[], help="秒數或時間碼（例 12.4、0:12.4），可重複")
    rf.add_argument("--from", dest="start", help="一段連續幀的起點（秒或時間碼）")
    rf.add_argument("--to", dest="end", help="一段連續幀的終點")
    rf.add_argument("--step", type=float, default=0.25, help="連續幀的間隔秒數（預設 0.25）")
    rr = add("review-record", "記下獨立審片 agent 的回答（review.json），寫進審片頁", "tts", "data_dir", "aspect")
    rr.add_argument("--file", help="回答檔（預設：審片素材資料夾裡的 review.json；- ＝從標準輸入讀）")
    rs = add("resolve", "記下必修／該修的處理：--fixed 改好了（獨立審片的問題）、--accept 看過、接受不改", "data_dir")
    rs.add_argument("tokens", nargs="*", help="代號，例 R1-2（獨立審片）、qa:SH03:jump@1a2b3c4d（自動檢查），可以一次給好幾個")
    how = rs.add_mutually_exclusive_group(required=True)
    how.add_argument("--fixed", metavar="說明", help="改好了：寫怎麼改的（只用在獨立審片的問題）")
    how.add_argument("--accept", metavar="理由", help="接受不改：寫為什麼（作者決定的加 --by 作者）")
    rs.add_argument("--by", default="Claude", help="誰決定的：Claude（預設）或 作者")
    cb = add("confirm-box", "（內部用，作者的流程只用財經圖表）記下 Claude 看過靜態圖、確認沒問題的框（核對代號從 boxes 指令來）",
             "data_dir")
    cb.add_argument("tokens", nargs="*", help="核對代號，例 SH02:1@1a2b3c4d（可以一次給好幾個）")
    cb.add_argument("--note", default="", help="備註（例：框在「十年期」那一列的 3.96 上）")
    nw = add("new", "開新專案：在「影片／解說影片」建「日期-標題/專案」資料夾，擷取文章（含截字檢查）", project=None)
    nw.add_argument("source", help=_ARTICLE_SOURCE)
    nw.add_argument("--title", help="片名（資料夾與成片的檔名；不給就用文章第一段）")
    nw.add_argument("--root", help="影片資料夾（預設 ~/Movies/解說影片）")
    pj = add("projects", "列出做過的影片（最近動過的在前面），找舊專案用", "json", project=None)
    pj.add_argument("query", nargs="?", default="", help="只列標題含這幾個字的")
    pj.add_argument("--root", help="影片資料夾（預設 ~/Movies/解說影片）")
    add("lint", "旁白規則檢查：narration.json 每個旁白段的字數、斷句、數字寫法、套話、開場",
        project="專案資料夾（裡面有 narration.json）")
    add("outline", "列出每個分鏡：編號、種類、畫什麼、唸哪幾個旁白段（照分鏡編號改的時候用）")
    fb = add("feedback", "把審片頁「複製給 Claude」的文字（從 stdin 讀）解析成 JSON",
             project="專案資料夾（用來把分鏡編號對到分鏡 id）")
    fb.add_argument("--file", help="從這個檔讀，不從 stdin 讀")
    ex = add("extract", "擷取文章全文與原圖到 <專案>/article/", project="專案資料夾（沒有會自動建立）")
    ex.add_argument("source", help=_ARTICLE_SOURCE)
    ri = add("report-issue", "回報問題：把錯誤紀錄、分鏡設定、出錯畫面、環境檢查打包成 zip（預設放桌面）", project=None)
    ri.add_argument("project", nargs="?", help="出問題的專案資料夾（沒有專案就只打包環境檢查）")
    ri.add_argument("--shot", action="append", default=[], help="出錯的分鏡：id（SH03）或編號（3），可重複；不給就從最後一次建置的錯誤找")
    ri.add_argument("--note", default="", help="問題描述（作者怎麼說的）")
    ri.add_argument("--out-dir", help="zip 放哪裡（預設桌面）")
    rx = add("reproduce-issue", "（維護者用）解開回報的 zip，重渲出錯的分鏡", project=None)
    rx.add_argument("zip", help="作者傳來的回報 zip")
    rx.add_argument("--out", help="解開到哪裡（預設 zip 旁邊、同名資料夾）")
    rx.add_argument("--shot", action="append", default=[], help="只重渲這些分鏡（預設是回報裡出錯的分鏡）")

    st = add("settings", "看或改使用者設定", project=None).add_subparsers(dest="action", required=True)
    show = add("show", "看設定", "json", "data_dir", project=None, into=st)
    show.add_argument("--project", help="看這支片的設定快照")
    change = add("set", "改設定", "data_dir", project=None, into=st)
    for key, help_ in _SET_OPTIONS.items():
        # argparse 會拿 help 做 % 格式化：「+5%、-10%」不跳脫的話 settings set --help 直接當掉
        change.add_argument("--" + key.replace("_", "-"), dest=key, help=help_.replace("%", "%%"))
    change.add_argument("--project", help="只改這支片的設定快照（不動使用者設定）")

    sm = add("samples", "產生試聽檔（安裝時挑聲音、挑配樂用）", project=None).add_subparsers(dest="what", required=True)
    music = add("music", "每首配樂一個試聽檔", "data_dir", project=None, into=sm)
    music.add_argument("--out", required=True, help="試聽檔放哪個資料夾")
    music.add_argument("--seconds", type=float, default=20.0, help="每個試聽檔幾秒（預設 20）")
    voice = add("voice", "每個聲音一個試聽檔（edge-tts 要連網）", "data_dir", project=None, into=sm)
    voice.add_argument("--out", required=True, help="試聽檔放哪個資料夾")
    voice.add_argument("--rate", default=None, help="語速（預設用使用者設定）")
    voice.add_argument("--text", default=VOICE_SAMPLE, help="試聽唸的句子")
    voice.add_argument("--tts", choices=sorted(TTS_ENGINES), default="edge",
                       help="配音層（預設 edge，要連網）；google 給六個台灣口音的聲音，elevenlabs 給設定的聲音")
    voice.add_argument("--voice", action="append", default=[],
                       help="只試這幾個聲音（可重複；ElevenLabs 填聲音代號，挑聲庫的台灣口音聲音用）")

    ce = add("check-env", "檢查環境：逐項列出做片要用的工具狀態與修法（含 edge-tts 連線、付費配音有沒有 API key）",
             "json", "data_dir", project=None)
    ce.add_argument("--try-voice", action="store_true",
                    help="付費配音（ElevenLabs、Google）真的試唸兩個字，確認 key 能用（會用掉幾個字的額度）")
    add("install", "安裝做片要用的工具（Homebrew、能燒字幕的 ffmpeg、Chromium），已經有的略過", "data_dir", project=None)
    tv = add("test-video", "做一支約 10 秒的測試片並打開（安裝最後一步，確認做片的環境沒問題）", "no_open", "data_dir",
             project=None)
    tv.add_argument("--out", help="測試片放哪個資料夾（預設 ~/Movies/解說影片/安裝測試片，Finder 裡的「影片」）")
    tv.add_argument("--tts", choices=sorted(TTS_ENGINES), help="配音層（預設 edge，用設定好的聲音；要連網）")
    return p


# ---------------------------------------------------------------- 共用：留紀錄地跑、建置計畫、審片頁


def _failure(label: str, e: BaseException) -> str:
    """錯誤訊息前面加上哪一步失敗；訊息自己已經說了「還不能出片」就不重複。"""
    msg = str(e)
    return msg if msg.startswith(("還不能出片", f"{label}：")) else f"{label}：{msg}"


def _recorded(args: argparse.Namespace, failed: str | Callable[[], str], body: Callable[[Log], int], *, project=None,
              blocked: str | None = None, after_error: Callable[[], None] | None = None) -> int:
    """跑一個會留紀錄的子指令：印出來的每一行和結果記在 <專案>/build/last-run.{log,json}（回報問題時打包）。

    已知錯誤印繁中到 stderr（前面加上 failed；可以是函式，跑到哪一步再決定怎麼說）、結束碼 1；
    程式錯誤連 traceback 一起記下再往外丟。
    blocked：渲染前的檢查擋下正式出片（BlockedBuild）時印的開頭；審片頁照這一版重寫，被擋的原因標在卡上。
    project：紀錄放哪個專案（預設 args.project）；after_error：已知錯誤印完之後再補一句。"""
    from article_to_video.build import BlockedBuild
    from article_to_video.issue_report import RunRecord

    aspect = getattr(args, "aspect", None)
    options = {k: getattr(args, k, None) for k in ("tts", "jobs", "version")}
    options["aspect"] = aspect.name if aspect is not None else None
    rec = RunRecord(project if project is not None else getattr(args, "project", None), args.command, options)
    log = rec.tee(lambda m: print(m, flush=True))
    label = failed if isinstance(failed, str) else None
    try:
        code = body(log)
    except BlockedBuild as e:
        rec.finish(e)
        print(_failure(blocked or label or failed(), e), file=sys.stderr)
        try:
            _refresh_review(e.plan, args, log, what="審片頁已更新（被擋下的原因標在卡片上）")
        except KNOWN_ERRORS as e2:
            print(f"審片頁也沒更新：{e2}", file=sys.stderr)
        return 1
    except KNOWN_ERRORS as e:
        rec.finish(e)
        print(_failure(label or failed(), e), file=sys.stderr)
        if after_error is not None:
            after_error()
        return 1
    except BaseException as e:
        rec.finish(e, unexpected=True)
        raise
    rec.finish()
    return code


def _plan(args: argparse.Namespace, log: Log | None = None):
    """這個子指令的建置計畫（配音、時間軸、每個片段的 spec；不渲染）。"""
    from article_to_video.build import plan_build

    kw = {"log": log} if log is not None else {}
    return plan_build(args.project, tts=args.tts, fmt=args.aspect, data_dir=args.data_dir, **kw)


def _refresh_review(plan, args: argparse.Namespace, log: Log, *, what: str | None = "審片頁已更新"):
    """審片頁照這份計畫重寫（帶上最新的檢查結果）：成片還是這份分鏡做的就照樣內嵌，不然是預覽的樣子。"""
    from article_to_video.auto_checks import current_video
    from article_to_video.review import default_contributors, make_review

    review = make_review(plan, video=current_video(plan), contributors=default_contributors(args.data_dir),
                         jobs=getattr(args, "jobs", None), log=log)
    if what:
        log(f"{what}：{review.page}")
    return review


def _show_review(page, *, no_open: bool, version: int | None = None) -> None:
    from article_to_video.review import auto_open_allowed, open_in_browser

    print(f"審片頁：{page}" + (f"（第 {version} 版）" if version else ""))
    if no_open or not auto_open_allowed():
        return
    if not open_in_browser(page):
        print("沒能自動打開審片頁，請在 Finder 找到上面這個檔案，點兩下打開。")


# ---------------------------------------------------------------- 做片：預覽、建置、出片


def _list_stills(review) -> str:
    """給 Claude 逐張看的分鏡靜態圖（完成狀態那張）：第 1 版列全部，之後只列這版改過的分鏡。"""
    shots = review.plan.timeline.shots if review.plan is not None else []
    if review.version == 1:
        pick, head = shots, f"第 1 版：{len(shots)} 個分鏡的靜態圖（完成狀態）"
    else:
        changed = set(review.changed)
        pick = [st for st in shots if st.shot.id in changed]
        head = f"第 {review.version} 版：改了 {len(pick)} 個分鏡" + ("，靜態圖（完成狀態）" if pick else "")
    lines = [head]
    for st in pick:
        finals = [x.path for x in review.stills.get(st.shot.id, []) if x.final]
        if finals:
            lines.append(f"  分鏡 {st.shot.number}（{st.shot.id}）：{finals[0]}")
    if review.removed:
        lines.append("  拿掉了：" + "、".join(review.removed))
    return "\n".join(lines)


def _preview(args: argparse.Namespace) -> int:
    """預覽：配音（有快取就不重配）、分鏡靜態圖、審片頁；印數字對帳的摘要與每張靜態圖給 Claude 看。"""
    from article_to_video.numbers import summary_line
    from article_to_video.review import default_contributors, preview_project

    def body(log: Log) -> int:
        review = preview_project(args.project, tts=args.tts, contributors=default_contributors(args.data_dir),
                                 jobs=args.jobs, data_dir=args.data_dir, fmt=args.aspect, log=log)
        if review.plan is not None:
            log(summary_line(review.plan))
            log(_list_stills(review))
        _show_review(review.page, no_open=args.no_open, version=review.version)
        return 0

    return _recorded(args, "預覽失敗", body)


def _after_build_hint(plan, data_dir) -> str:
    """build 之後的下一步（給 Claude 看）：自動檢查有沒有跑過、獨立審片審過這版沒有。"""
    from article_to_video.auto_checks import load_check_report
    from article_to_video.independent_review import review_enabled, reviewed_current_video

    root, aspect = plan.project.root, aspect_args(plan.format.name)
    steps = []
    unchecked = load_check_report(plan).unchecked
    if unchecked:
        steps.append(f"自動檢查（{len(unchecked)} 個片段還沒查）：{cli_command('checks', root, *aspect, data_dir=data_dir)}")
    if review_enabled(data_dir) and not reviewed_current_video(plan):
        steps.append(f"獨立審片（這版成片還沒審）：{cli_command('review-prep', root, *aspect, data_dir=data_dir)}"
                     " → 用印出來的 prompt 派一個 agent → review-record")
    if steps:
        return "下一步：\n" + "\n".join(f"  {s}" for s in steps)
    return (f"自動檢查、獨立審片都跑過這版了。下一步：處理完必修，{cli_command('open-review', root, *aspect, data_dir=data_dir)}"
            " 打開審片頁給作者看；作者點頭之後 publish --version <他看的那一版> 才是出片。")


def _build(args: argparse.Namespace) -> int:
    """建置審片用的成片（還不是出片）。正式出片前先數字對帳；自動檢查、獨立審片查出的必修還沒處理（沒改掉、也沒明確
    接受）也擋。被擋下時審片頁照這一版重寫（沒有成片、被擋的原因標在卡上），作者打開看到的就是現況。"""
    from article_to_video.auto_checks import require_checks_cleared
    from article_to_video.build import build_project
    from article_to_video.numbers import check_numbers
    from article_to_video.review import default_contributors, make_review

    failed = ["建置失敗"]

    def body(log: Log) -> int:
        res = build_project(args.project, tts=args.tts, jobs=args.jobs, fmt=args.aspect, data_dir=args.data_dir,
                            preflight=[check_numbers, require_checks_cleared], log=log)
        print(f"完成：{res.video}（{res.duration:.2f} 秒）")
        print(f"字幕檔：{res.srt}")
        failed[0] = "成片做好了，但審片頁沒更新"
        review = make_review(res.plan, video=res.video, contributors=default_contributors(args.data_dir),
                             jobs=args.jobs, log=log)
        print(_after_build_hint(res.plan, args.data_dir))
        _show_review(review.page, no_open=args.no_open, version=review.version)
        return 0

    return _recorded(args, lambda: failed[0], body, blocked="建置失敗")


def _publish(args: argparse.Namespace) -> int:
    from article_to_video.macos import open_path
    from article_to_video.publish import publish_project
    from article_to_video.review import auto_open_allowed, default_contributors, make_review

    def body(log: Log) -> int:
        res = publish_project(args.project, version=args.version, tts=args.tts, jobs=args.jobs, fmt=args.aspect,
                              data_dir=args.data_dir, out_dir=args.out, log=log)
        review = make_review(res.plan, video=res.project_video, contributors=default_contributors(args.data_dir),
                             jobs=args.jobs, log=log)
        minutes, secs = divmod(res.duration, 60)
        print(f"出片完成（審片頁第 {res.version} 版，{int(minutes)} 分 {secs:.0f} 秒）：")
        print(f"  成片：{res.video}")
        print(f"  字幕檔：{res.srt}")
        print(f"審片頁：{review.page}")
        if not args.no_open and auto_open_allowed() and not open_path(res.video, reveal=True):
            print("沒能在 Finder 打開，請到「影片」→「解說影片」找這個資料夾。")
        return 0

    return _recorded(args, "出片失敗", body, blocked="還不能出片")


def _open_review(args: argparse.Namespace) -> int:
    """更新審片頁並打開給作者看。會渲分鏡靜態圖，出錯要跟 build、preview 一樣記進 last-run。"""
    def body(log: Log) -> int:
        review = _refresh_review(_plan(args), args, log, what=None)
        _show_review(review.page, no_open=args.no_open, version=review.version)
        return 0

    return _recorded(args, "審片頁打不開", body)


def _narrate(args: argparse.Namespace) -> int:
    """配音（寫分鏡之前）：印總長、每段起點與長度、每個詞開始唸的秒數。"""
    from article_to_video.build import format_voicing, voice_narration

    def body(log: Log) -> int:
        log(format_voicing(voice_narration(args.project, tts=args.tts, data_dir=args.data_dir, log=log)))
        return 0

    return _recorded(args, "配音失敗", body)


# ---------------------------------------------------------------- 檢查：框、自動檢查、獨立審片、處理紀錄


def _boxes(args: argparse.Namespace) -> int:
    """列出每個 Vox 框＋渲靜態圖。"""
    from pathlib import Path

    from article_to_video.boxes import box_statuses, format_box_listing, render_box_stills

    def body(log: Log) -> int:
        plan = _plan(args, log)
        statuses = box_statuses(plan)
        render_box_stills(plan, statuses, jobs=args.jobs, log=log)
        log(format_box_listing(statuses, Path(args.project).absolute()))
        return 0

    return _recorded(args, "列不出框", body)


def _confirm_box(args: argparse.Namespace) -> int:
    from article_to_video.boxes import confirm_boxes

    try:
        res = confirm_boxes(args.project, args.tokens, note=args.note)
    except ProjectError as e:
        print(f"核對失敗：{e}", file=sys.stderr)
        return 1
    print("已記下核對：" + "、".join(b.id for b in res.confirmed))
    if res.remaining:
        print("還沒核對：" + "、".join(b.id for b in res.remaining))
    else:
        print("所有 Vox 框都核對過了，build 不會再被框擋下。")
        print("下一步（都在出片之前）：build 做審片用的成片 → checks 自動檢查 → 獨立審片 → open-review 給作者看；"
              "作者點頭之後才 publish 出片。")
    return 0


def _checks(args: argparse.Namespace) -> int:
    from article_to_video.auto_checks import clips_not_built, format_report, report_json, run_checks

    def body(log: Log) -> int:
        plan = _plan(args, log)
        missing = clips_not_built(plan)
        if missing:
            raise ProjectError(
                [f"片段 {cid} 還沒渲，或分鏡改過之後還沒重新出片" for cid in missing],
                title="自動檢查要在 build 之後跑",
                hint=f"先跑 build（{cli_command('build', plan.project.root, *aspect_args(plan.format.name), '--no-open', data_dir=args.data_dir)}），"
                     "build 成功再跑 checks。",
            )
        report = run_checks(plan, jobs=args.jobs, log=log)
        log(format_report(plan, report))
        _refresh_review(plan, args, log)
        if args.json:
            print(json.dumps(report_json(plan, report), ensure_ascii=False, indent=1))
        return 0

    return _recorded(args, "自動檢查失敗", body)


def _review_prep(args: argparse.Namespace) -> int:
    from article_to_video import independent_review as ir

    def body(log: Log) -> int:
        plan = _plan(args, log)
        if not ir.review_enabled(args.data_dir):
            log("注意：使用者設定把獨立審片關掉了（"
                f"{cli_command('settings set', '--independent-review', '開', data_dir=args.data_dir)} 可以打開）；照樣準備素材。")
        kit = ir.prepare_review_kit(plan, log=log)
        log(f"派審片 agent 時把這個檔的內容當 prompt：{kit.prompt}")
        log("agent 寫完 review.json 之後跑："
            + cli_command("review-record", plan.project.root, *aspect_args(plan.format.name), data_dir=args.data_dir))
        return 0

    return _recorded(args, "審片素材做不出來", body)


def _review_frames(args: argparse.Namespace) -> int:
    """審片 agent 自己叫的：從成片抽幀。"""
    from article_to_video import independent_review as ir

    try:
        kit = ir.find_kit(args.project, args.aspect.name if args.aspect else None)
        times = [ir.parse_time_arg(t) for t in args.at]
        if args.start is not None or args.end is not None:
            if args.start is None or args.end is None or args.step <= 0:
                raise ProjectError("連續幀要同時給 --from 和 --to（--step 要大於 0）", title="抽不了幀")
            a, b = ir.parse_time_arg(args.start), ir.parse_time_arg(args.end)
            n = int(round((b - a) / args.step))
            times += [round(a + k * args.step, 3) for k in range(n + 1)]
        if not times:
            raise ProjectError("要給 --at 秒數，或 --from／--to", title="抽不了幀")
        paths, sheet = ir.extract_review_frames(kit, times)
    except ProjectError as e:
        print(f"抽幀失敗：{e}", file=sys.stderr)
        return 1
    print("\n".join(str(p) for p in paths))
    if sheet:
        print(f"拼在一起：{sheet}")
    return 0


def _review_record(args: argparse.Namespace) -> int:
    from article_to_video import independent_review as ir
    from article_to_video.textio import TextInputError, read_text_input

    def body(log: Log) -> int:
        plan = _plan(args, log)
        kit = ir.find_kit(args.project, plan.format.name)
        src = args.file or str(kit.answer)
        try:
            text = read_text_input(src)
        except TextInputError as e:
            raise ProjectError(str(e), title="沒有審片結果",
                               hint="審片 agent 要把結果寫到審片素材資料夾的 review.json。") from None
        try:
            answer = json.loads(text)
        except json.JSONDecodeError as e:
            raise ProjectError(f"{src} 第 {e.lineno} 行附近不是合法的 JSON", title="審片結果格式不對") from None
        rnd = ir.record_review(plan, answer, kit)
        counts = "、".join(f"{s} {sum(1 for it in rnd['items'] if it['severity'] == s)}" for s in ir.SEVERITIES)
        log(f"已記下獨立審片第 {rnd['round']} 輪：{counts}")
        for it in rnd["items"]:
            log(f"  {it['id']} {it['severity']} {ir.item_where(it)}：{it['title']}")
        for p in rnd["previous"]:
            log(f"  上一輪 {p['id']}：{p['status']}")
        must = ir.unresolved_must(plan)
        if must:
            root = plan.project.root
            log(f"還有 {len(must)} 項必修沒處理，正式出片會被擋下：改好之後 "
                + cli_command("resolve", root, "<代號>", "--fixed", '"怎麼改的"') + "；作者決定不改的 "
                + cli_command("resolve", root, "<代號>", "--accept", '"理由"', "--by", "作者"))
        _refresh_review(plan, args, log)
        return 0

    return _recorded(args, "審片結果沒記下來", body)


def _resolve(args: argparse.Namespace) -> int:
    from article_to_video.check_log import record_resolutions

    status, note = ("fixed", args.fixed) if args.fixed is not None else ("accepted", args.accept)
    try:
        what = record_resolutions(args.project, args.tokens, status=status, note=note, by=args.by)
    except ProjectError as e:
        print(f"處理紀錄失敗：{e}", file=sys.stderr)
        return 1
    verb = "改好了" if status == "fixed" else f"接受不改（{args.by}）"
    print(f"已記下 {len(what)} 項{verb}：")
    print("\n".join(f"  {t}　{w}" for t, w in zip(args.tokens, what)))
    print("審片頁下次 build／checks 時更新；正式出片不會再被這幾項擋下。")
    return 0


# ---------------------------------------------------------------- 專案：開新片、找舊片、旁白、留言、文章


def _new(args: argparse.Namespace) -> int:
    from article_to_video.videos import new_project

    return new_project(args.source, title=args.title, root=args.root)


def _projects(args: argparse.Namespace) -> int:
    from article_to_video.videos import format_projects, list_projects, videos_root

    infos = list_projects(args.root, args.query)
    if args.json:
        print(json.dumps([i.to_json() for i in infos], ensure_ascii=False, indent=1))
    else:
        print(format_projects(infos, videos_root(args.root)))
    return 0


def _outline(args: argparse.Namespace) -> int:
    from article_to_video.feedback import format_outline, project_outline

    try:
        outline, narration = project_outline(args.project)
    except ProjectError as e:
        print(f"讀不到分鏡設定：{e}", file=sys.stderr)
        return 1
    print(format_outline(outline, narration))
    return 0


def _lint(args: argparse.Namespace) -> int:
    from article_to_video.lint import format_lint, lint_project

    try:
        report = lint_project(args.project)
    except ProjectError as e:
        print(f"旁白檢查失敗：{e}", file=sys.stderr)
        return 1
    print(format_lint(report))
    return 0 if report.ok else 1


def _feedback(args: argparse.Namespace) -> int:
    from article_to_video.feedback import parse_feedback, project_outline
    from article_to_video.textio import STDIN, TextInputError, read_text_input

    try:
        outline, narration = project_outline(args.project)
    except ProjectError as e:
        print(f"讀不到分鏡設定：{e}", file=sys.stderr)
        return 1
    try:
        text = read_text_input(args.file or STDIN)
    except TextInputError as e:
        print(f"讀不到留言：{e}", file=sys.stderr)
        return 1
    fb = parse_feedback(text)
    ids = [o.id for o in outline]
    by_id = {o.id: o for o in outline}

    def comment(c, sid):
        item = {"number": c.number, "id": sid, "text": c.text}
        o = by_id.get(sid)
        if o is not None:  # 要改的地方：畫面是哪種、唸到哪幾段旁白（改旁白就改這幾段）
            item.update(kind=o.kind, shows=o.shows, segments=o.segments,
                        narration={k: narration[k] for k in o.segments})
        return item

    print(json.dumps({
        "project": fb.project,
        "version": fb.version,
        "shots": [comment(c, sid) for c, sid in zip(fb.shots, fb.resolve(ids))],
        "overall": fb.overall,
        "problems": fb.check(ids),
    }, ensure_ascii=False, indent=1))
    return 0


def _extract(args: argparse.Namespace) -> int:
    from article_to_video.article import run_extract

    return run_extract(args.project, args.source)


# ---------------------------------------------------------------- 回報問題


def _report_issue(args: argparse.Namespace) -> int:
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


def _reproduce_issue(args: argparse.Namespace) -> int:
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


# ---------------------------------------------------------------- 設定、安裝、環境


def _describe(values: dict) -> str:
    from article_to_video.music import TRACKS
    from article_to_video.user_settings import ASPECTS

    from article_to_video.tts_providers import GOOGLE_TW_VOICES

    music = values.get("music")
    tts = values.get("tts") or "edge"
    voice = {
        "elevenlabs": f"{values.get('elevenlabs_voice')}（模型 {values.get('elevenlabs_model')}）",
        "google": f"{values.get('google_voice')}（{GOOGLE_TW_VOICES.get(values.get('google_voice'), '')}）",
    }.get(tts, f"{ZH_TW_VOICES.get(values.get('voice'), '?')}（{values.get('voice')}）")
    lines = [
        f"頻道名：{values.get('channel') or '（還沒設定：片尾卡要用，請設定）'}",
        f"logo：{values.get('logo') or '（沒有）'}",
        f"品牌色：{values.get('brand_color') or '（沒設定：圖表用預設紅黑白）'}",
        f"配音：{_TTS_LABELS.get(tts, tts)}",
        f"聲音：{voice}",
        f"語速：{values.get('rate')}",
        f"配樂：{'不要配樂' if music == 'none' else f'{TRACKS[music].label}（{music}）' if music in TRACKS else music}",
        f"預設比例：{ASPECTS.get(values.get('aspect'), values.get('aspect'))}",
    ]
    if "independent_review" in values:  # 只有使用者設定有（不進設定快照）
        on = values["independent_review"]
        lines.append(f"獨立審片：{'開（作者看審片頁之前，派一個只看成片的 agent 審一輪）' if on else '關'}")
    return "\n".join("  " + ln for ln in lines)


def _settings(args: argparse.Namespace) -> int:
    try:
        return _settings_set(args) if args.action == "set" else _settings_show(args)
    except SettingsError as e:
        print(f"設定失敗：{e}", file=sys.stderr)
        return 1


def _settings_set(args: argparse.Namespace) -> int:
    from pathlib import Path

    from article_to_video.brand import brand_note
    from article_to_video.project import SETTINGS_FILE
    from article_to_video.user_settings import (
        SNAPSHOT_KEYS, normalize_value, resolve_data_dir, update_project_settings, update_user_settings,
    )

    changes = {k: getattr(args, k) for k in _SET_OPTIONS if getattr(args, k) is not None}
    if not changes:
        print("沒有要改的設定（例：settings set --voice 曉雨）", file=sys.stderr)
        return 1
    if args.project:
        if "independent_review" in changes:
            print("獨立審片的開關是全部影片共用的，不用加 --project", file=sys.stderr)
            return 1
        snap = update_project_settings(args.project, changes)
        print(f"已更新這支片的設定快照：{Path(args.project) / SETTINGS_FILE}")
        print(_describe({k: snap.get(k) for k in SNAPSHOT_KEYS}))
    else:
        us = update_user_settings(changes, args.data_dir)
        print(f"已更新使用者設定：{resolve_data_dir(args.data_dir) / 'settings.json'}")
        print(_describe(us.to_json()))
    if "brand_color" in changes and (note := brand_note(normalize_value("brand_color", changes["brand_color"]))):
        print(note)  # 淺色照樣收：告訴作者圖表上的字和線用加深的版本
    if "tts" in changes:
        from article_to_video.tts_providers import KEY_SPECS, find_api_key, missing_key_message

        tts = normalize_value("tts", changes["tts"])
        if tts in KEY_SPECS and find_api_key(tts, args.data_dir) is None:
            print(f"注意：{missing_key_message(tts, args.data_dir)}")
    return 0


def _settings_show(args: argparse.Namespace) -> int:
    from pathlib import Path

    from article_to_video.project import SETTINGS_FILE
    from article_to_video.user_settings import load_user_settings, resolve_data_dir

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


def _samples(args: argparse.Namespace) -> int:
    from pathlib import Path

    out = Path(args.out)
    try:
        out.mkdir(parents=True, exist_ok=True)
        if args.what == "music":
            from article_to_video.music import TRACKS, write_preview

            for name, track in TRACKS.items():
                f = write_preview(name, out / f"music-{name}.wav", args.seconds, args.data_dir)
                print(f"{track.label}（{name}）：{track.mood}\n  {f}")
            return 0

        from article_to_video.narration import get_tts_engine, narrate
        from article_to_video.tts_providers import GOOGLE_TW_VOICES
        from article_to_video.user_settings import load_user_settings, normalize_value, resolve_data_dir

        us = load_user_settings(args.data_dir)
        rate = normalize_value("rate", args.rate) if args.rate else us.rate
        cache = resolve_data_dir(args.data_dir) / "previews" / "voice-cache"
        voices = {"google": GOOGLE_TW_VOICES, "elevenlabs": {us.elevenlabs_voice: "設定的聲音"}}.get(args.tts, ZH_TW_VOICES)
        if args.voice:
            voices = {v: GOOGLE_TW_VOICES.get(v) or ZH_TW_VOICES.get(v) or "指定的聲音" for v in args.voice}
        engine = get_tts_engine(args.tts, model=us.elevenlabs_model if args.tts == "elevenlabs" else "",
                                data_dir=args.data_dir)
        sent = 0
        for vid, name in voices.items():
            audio = narrate({"sample": args.text}, engine=engine, voice=vid, rate=rate, cache_dir=cache)
            sent += audio["sample"].sent_chars
            f = out / f"voice-{vid}.wav"
            f.write_bytes(audio["sample"].audio.read_bytes())
            print(f"{name}（{vid}）：\n  {f}")
        print(f"這次送出 {sent} 字給配音服務（{args.tts}）")
        return 0
    except KNOWN_ERRORS as e:
        print(f"試聽檔產生失敗：{e}", file=sys.stderr)
        return 1


def _check_env(args: argparse.Namespace) -> int:
    from article_to_video.environment import collect_environment, environment_checks

    report = collect_environment(environment_checks(args.data_dir, try_voice=args.try_voice))
    if args.json:
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=1))
    else:
        print("環境檢查（✓ 正常、✗ 要處理、・ 只是資訊）：")
        print(report.to_text(), end="")
        print(report.summary())
    return 0 if report.ok else 1


def _install(args: argparse.Namespace) -> int:
    from article_to_video.installer import Installer

    report = Installer(args.data_dir, log=lambda m: print(m, flush=True)).run()
    return 0 if report.ok else 1


def _test_video(args: argparse.Namespace) -> int:
    from article_to_video.build import build_project
    from article_to_video.numbers import check_numbers
    from article_to_video.review import auto_open_allowed
    from article_to_video.sample_video import SampleVideoError, open_file, prepare_test_project

    try:
        out = prepare_test_project(args.out)
    except (SampleVideoError, OSError) as e:
        print(f"測試片做不出來：{e}", file=sys.stderr)
        return 1

    def body(log: Log) -> int:
        # 跟正式出片一樣先數字對帳（範例的數字都寫在範例的 article/text.txt 裡）
        res = build_project(out, tts=args.tts, data_dir=args.data_dir, preflight=[check_numbers], log=log)
        print(f"測試片做好了：{res.video}（{res.duration:.1f} 秒）")
        if not args.no_open and auto_open_allowed() and not open_file(res.video):
            print("沒能自動打開，請在 Finder 找到上面這個檔案，點兩下播放。")
        return 0

    # 出錯時「回報問題」帶這個資料夾就拿得到紀錄
    return _recorded(args, "測試片做不出來", body, project=out,
                     after_error=lambda: print(f"（要回報問題時，出問題的專案資料夾是 {out}）", file=sys.stderr))


# ---------------------------------------------------------------- 進入點


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "new": _new,
    "projects": _projects,
    "lint": _lint,
    "narrate": _narrate,
    "preview": _preview,
    "build": _build,
    "open-review": _open_review,
    "publish": _publish,
    "boxes": _boxes,
    "confirm-box": _confirm_box,
    "checks": _checks,
    "review-prep": _review_prep,
    "review-frames": _review_frames,
    "review-record": _review_record,
    "resolve": _resolve,
    "outline": _outline,
    "feedback": _feedback,
    "extract": _extract,
    "report-issue": _report_issue,
    "reproduce-issue": _reproduce_issue,
    "settings": _settings,
    "samples": _samples,
    "check-env": _check_env,
    "install": _install,
    "test-video": _test_video,
}
"""子指令 → 處理函式（每個子指令一個）。"""


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)
    return HANDLERS[args.command](args)

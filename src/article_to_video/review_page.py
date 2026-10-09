"""審片頁的 HTML（一個檔、樣式與程式內嵌、不連網，作者在 Finder 點兩下就能看）。

頁面結構（測試與後面的票靠這些 class／id 找東西）：
  <html data-stage="preview|final" data-version="N">
  article.card[data-shot-id][data-shot-number][data-changed]   每個分鏡一張
    .timecode（出片後是 button.seek[data-start]）、img.still[data-t]（第一張＝完成狀態）、.narration
    .slot[data-slot="numbers"]   數字對帳（#5 填）      .slot[data-slot="checks"]  自動檢查（#11 填）
    textarea.comment[data-shot-id]
  .page-slots .slot[data-slot=…]   整片層級的檢查結果（例：獨立審片）
  textarea#overall.overall、#menu（能加什麼）、#copy-btn／#copy-status／#copy-out（複製給 Claude）
  <script id="review-data" type="application/json">  給 review.js 的資料（專案名、版本、分鏡編號與 id）

後面的票往卡片加東西：做一個 CardSection（slot、標題、幾個 CheckItem），經 review.make_review(contributors=…) 傳進來，
這裡負責排版與跳脫（不吃 HTML，只吃文字）。
"""

from __future__ import annotations

import html
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "MENU",
    "CardModel",
    "CardSection",
    "CheckItem",
    "PageModel",
    "StillModel",
    "render_review_html",
    "timecode",
]

_ASSETS = Path(__file__).resolve().parent / "review_assets"

STATUSES = {"ok": "沒問題", "warn": "要看一下", "block": "擋下出片", "info": "說明"}


@dataclass(frozen=True)
class CheckItem:
    """卡片上一行檢查結果。text 是主要那行（例「4.58%」），detail 是補充（出處、算式、原因）。"""

    text: str
    detail: str = ""
    status: str = "info"
    """ok／warn／block／info，決定前面那個小標籤的字和顏色。"""
    label: str | None = None
    """小標籤的字；不給就用 status 的預設（沒問題／要看一下／擋下出片／說明）。"""


@dataclass(frozen=True)
class CardSection:
    """卡片（或整頁）上的一區檢查結果。"""

    slot: str
    """"numbers"＝數字對帳（#5），"checks"＝自動檢查（#11）；其他名字會排在這兩個後面。"""
    title: str
    items: list[CheckItem] = field(default_factory=list)
    note: str = ""
    """標題下面的一句說明（選填）。"""


@dataclass(frozen=True)
class StillModel:
    src: str
    time: float
    """成片秒數。"""
    final: bool


@dataclass(frozen=True)
class CardModel:
    id: str
    number: int
    start: float
    end: float
    narration: list[tuple[str, bool, bool]]
    """(文字, 從段落中間開始, 在段落中間結束)。"""
    stills: list[StillModel]
    continues: bool = False
    changed: bool = False
    sections: list[CardSection] = field(default_factory=list)


@dataclass(frozen=True)
class PageModel:
    project: str
    storage_key: str
    version: int
    stage: str
    """"preview"（只有靜態圖）或 "final"（已出片）。"""
    generated: str
    duration: float
    cards: list[CardModel]
    video_src: str | None = None
    removed: list[tuple[str, int]] = field(default_factory=list)
    """這版拿掉的分鏡：(id, 上一版的編號)。"""
    page_sections: list[CardSection] = field(default_factory=list)
    aspect: str = "16x9"
    """畫面比例（timeline.VideoFormat.name）：9x16 時靜態圖、成片照直式排。"""


# ---------------------------------------------------------------- 能加什麼（照外掛實際做得到的事寫）

MENU: list[tuple[str, str, list[str]]] = [
    ("財經圖表",
     "柱狀圖、折線圖、K 線圖、右上角的大數字，還有指著某一根柱子或某一個點的標註。柱狀圖一律從 0 起；"
     "折線圖不從 0 起時，圖上會寫明。",
     ["分鏡 3 改成折線圖，畫 2019 到 2024 年的走勢",
      "分鏡 2 加一個標註，指著最高那根，寫「升息後的高點」",
      "分鏡 4 的右上角放一個大數字",
      "分鏡 5 的柱子跟著旁白，一根一根出來"]),
    ("公有數據的走勢圖",
     "聯準會、財政部公開的利率數據可以直接抓來畫，圖上自動標出處：十年期、兩年期公債殖利率、實質殖利率、期限溢價；"
     "通膨預期用十年期減實質殖利率算。指數類（例如標普五百）不抓，只能用你文章裡的數字或圖。",
     ["分鏡 6 換成十年期公債殖利率近五年的走勢",
      "加一張圖，比較十年期和兩年期殖利率"]),
    ("文章裡的原圖（剪報畫面）",
     "講到你文章裡的某張圖時，畫面切到桌面，把原圖貼上去，框出旁白正在講的那個數字。",
     ["講到第二張圖的時候，把原圖貼出來，框出旁白講的那個數字",
      "分鏡 7 的框框歪了，要框在「十年期」那一列",
      "分鏡 8 的剪報要截到句號"]),
    ("旁白",
     "可以改字、拆句、換順序。改了旁白，配音和畫面出現的時間會跟著重算。",
     ["分鏡 3 的旁白改成：（寫你要的句子）",
      "這句太長，拆成兩句",
      "這裡不要講「暴跌」，講「下跌」就好"]),
    ("數字與出處",
     "畫面上的每個數字都要對得到出處：你的文章、公有數據，或寫清楚的算式。對不到的數字會擋下來，不會出片。",
     ["分鏡 4 的數字要跟原文一樣，寫到小數點後一位",
      "分鏡 2 的出處寫錯了，應該是財政部"]),
    ("節奏與分鏡",
     "可以拿掉、新增、對調分鏡，或讓某個畫面早一點、晚一點出來。",
     ["分鏡 9 拿掉",
      "在分鏡 2 後面加一張，講（你要講的事）",
      "分鏡 5 和分鏡 6 對調",
      "分鏡 4 的標註晚一點出來，等旁白唸到那個數字"]),
    ("聲音、配樂、品牌",
     "聲音有曉臻、曉雨、雲哲三個；語速、配樂大小聲、片尾卡的頻道名和品牌色都能改。",
     ["換成雲哲的聲音",
      "語速慢一點",
      "配樂小聲一點",
      "片尾卡的頻道名改成（你的頻道名）"]),
    ("出片",
     "看完覺得可以了就說一聲；也可以另外出一支直式的給短影音用。",
     ["都可以了，出片",
      "再出一支直式（9:16）的"]),
]


# ---------------------------------------------------------------- 排版


def timecode(seconds: float) -> str:
    """成片時間碼，給作者看：分:秒.十分之一秒（例 1:05.3）。"""
    tenths = round(seconds * 10)
    return f"{tenths // 600}:{tenths % 600 / 10:04.1f}"


def _asset(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


_e = html.escape
_SLOT_ORDER = {"numbers": 0, "checks": 1}


def _section(sec: CardSection, heading: str = "h3") -> str:
    items = "".join(
        f'<li class="{_e(it.status)}"><span class="mark">{_e(it.label or STATUSES.get(it.status, it.status))}</span>'
        f'<span class="text">{_e(it.text)}</span>'
        + (f'<span class="detail">{_e(it.detail)}</span>' if it.detail else "")
        + "</li>"
        for it in sec.items
    )
    note = f'<p class="hint">{_e(sec.note)}</p>' if sec.note else ""
    return (f'<section class="slot" data-slot="{_e(sec.slot)}"><{heading}>{_e(sec.title)}</{heading}>{note}'
            f'<ul>{items}</ul></section>')


def _slots(sections: Sequence[CardSection]) -> str:
    """數字對帳、自動檢查兩格一定有（沒有內容時藏起來），後面的票往裡面填。"""
    by_slot: dict[str, list[CardSection]] = {}
    for s in sections:
        by_slot.setdefault(s.slot, []).append(s)
    out = []
    for name in ("numbers", "checks"):
        if name not in by_slot:
            out.append(f'<section class="slot" data-slot="{name}" hidden></section>')
    for name in sorted(by_slot, key=lambda n: (_SLOT_ORDER.get(n, 9), n)):
        out.extend(_section(s) for s in by_slot[name])
    return "".join(out)


def _card(c: CardModel, has_video: bool) -> str:
    span = f"{timecode(c.start)}–{timecode(c.end)}"
    if has_video:
        tc = (f'<button type="button" class="timecode seek" data-start="{c.start:.3f}" '
              f'title="成片跳到這一段">{span} ▶</button>')
    else:
        tc = f'<span class="timecode" data-start="{c.start:.3f}">{span}</span>'
    badges = ""
    if c.changed:
        badges += '<span class="badge changed">這版改過</span>'
    if c.continues:
        badges += '<span class="badge tag">接續上一張</span>'

    final = [s for s in c.stills if s.final]
    moments = [s for s in c.stills if not s.final]
    media = ""
    for s in final[:1]:
        media += (f'<a class="main-still" href="{s.src}" target="_blank" title="開大圖">'
                  f'<img class="still" src="{s.src}" data-t="{s.time:.3f}" '
                  f'alt="分鏡 {c.number} 最後的畫面（{timecode(s.time)}）" loading="lazy"></a>')
    if moments:
        figs = "".join(
            f'<figure class="moment"><a href="{s.src}" target="_blank" title="開大圖">'
            f'<img class="still" src="{s.src}" data-t="{s.time:.3f}" alt="分鏡 {c.number} 在 {timecode(s.time)} 的畫面" loading="lazy">'
            f'</a><figcaption>{timecode(s.time)}</figcaption></figure>'
            for s in moments
        )
        media += f'<p class="moments-title">過程（每個新畫面出現之前）</p><div class="moments">{figs}</div>'

    narr = "".join(
        f'<p>{"…" if mid_start else ""}{_e(text)}{"…" if mid_end else ""}</p>' for text, mid_start, mid_end in c.narration
    ) or '<p class="hint">（這段沒有旁白）</p>'
    sid = _e(c.id)
    return (
        f'<article class="card" id="shot-{sid}" data-shot-id="{sid}" data-shot-number="{c.number}" '
        f'data-changed="{"true" if c.changed else "false"}">'
        f'<div class="card-media">{media}</div>'
        f'<div class="card-body">'
        f'<header class="card-head"><h2>分鏡 {c.number}</h2>{tc}{badges}</header>'
        f'<div class="narration"><h3>旁白</h3>{narr}</div>'
        f'{_slots(c.sections)}'
        f'<label class="field" for="c-{sid}">想改什麼？</label>'
        f'<textarea id="c-{sid}" class="comment" data-shot-id="{sid}" rows="3" '
        f'placeholder="例：標題改短一點／這根柱子晚一點出來／旁白改成…"></textarea>'
        f'</div></article>'
    )


def _changes(m: PageModel) -> str:
    changed = [c for c in m.cards if c.changed]
    if not changed and not m.removed:
        return ""
    parts = []
    if changed:
        links = "、".join(f'<a href="#shot-{_e(c.id)}">分鏡 {c.number}</a>' for c in changed)
        parts.append(f"<p><strong>這版改了 {len(changed)} 個分鏡：</strong><span class=\"changed-list\">{links}</span>"
                     "（卡片上有「這版改過」）</p>")
    if m.removed:
        gone = "、".join(f"上一版的分鏡 {n}" for _, n in m.removed)
        parts.append(f"<p><strong>拿掉了：</strong>{_e(gone)}</p>")
    return f'<section class="panel notice" id="changes">{"".join(parts)}</section>'


def render_review_html(m: PageModel) -> str:
    has_video = m.video_src is not None
    stage = ('<span class="stage final">已出片</span>' if m.stage == "final"
             else '<span class="stage">預覽：只有靜態圖，還沒出片</span>')
    film = ""
    if has_video:
        film = (
            '<section class="panel film" id="film"><h2>成片</h2>'
            f'<video class="film-video" controls preload="metadata" src="{m.video_src}"></video>'
            '<p class="hint">點卡片上的時間，影片會跳到那一段。</p></section>'
        )
    page_slots = ""
    if m.page_sections:
        page_slots = ('<div class="page-slots">'
                      + "".join(f'<div class="panel">{_section(s, "h2")}</div>' for s in m.page_sections)
                      + "</div>")
    data = {
        "project": m.project,
        "storageKey": m.storage_key,
        "version": m.version,
        "stage": m.stage,
        "shots": [{"id": c.id, "number": c.number} for c in m.cards],
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    menu = "".join(
        f'<div class="menu-group"><h3>{_e(title)}</h3><p>{_e(desc)}</p><ul>'
        + "".join(f'<li><button type="button" class="example">{_e(x)}</button></li>' for x in examples)
        + "</ul></div>"
        for title, desc, examples in MENU
    )
    n_comments_hint = "在卡片下方的格子寫想改的地方，寫完按「複製給 Claude」。"
    return f"""<!doctype html>
<html lang="zh-Hant-TW" data-stage="{_e(m.stage)}" data-version="{m.version}" data-aspect="{_e(m.aspect)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>審片頁｜{_e(m.project)}（第 {m.version} 版）</title>
<style>
{_asset("review.css")}
</style>
</head>
<body>
<header class="top"><div class="wrap">
<p class="eyebrow">審片頁 · 第 {m.version} 版 {stage}</p>
<h1>{_e(m.project)}</h1>
<p class="meta">{len(m.cards)} 個分鏡 · 全長 {timecode(m.duration)} · {_e(m.generated)} 產生</p>
</div></header>
<main class="wrap">
<section class="panel howto"><h2>怎麼用這一頁</h2><ol>
<li>一張卡是一個分鏡：大圖是這個分鏡最後的畫面，小圖是過程，下面是那段旁白。點圖可以看大張。</li>
<li>想改哪裡，就寫在那張卡的「想改什麼？」；不屬於某一張的，寫在最下面的「整體意見」。</li>
<li>寫完按畫面最下方的「複製給 Claude」，回到 Claude 的對話框貼上送出。寫到一半關掉也沒關係，留言會留著。</li>
</ol></section>
<section class="panel notice" id="carry" hidden><p id="carry-text"></p>
<div class="actions"><button type="button" class="btn primary" id="carry-yes">放回來</button>
<button type="button" class="btn" id="carry-no">不要了</button></div></section>
{_changes(m)}
{film}
{page_slots}
<section class="cards" aria-label="分鏡">
{chr(10).join(_card(c, has_video) for c in m.cards)}
</section>
<section class="panel overall-box"><h2><label for="overall">整體意見</label></h2>
<p class="hint">不屬於某一張的意見寫這裡，例如整支片的節奏、聲音、配樂。</p>
<textarea id="overall" class="overall" rows="4" placeholder="例：整體節奏有點快／聲音想換一個"></textarea></section>
<section class="panel menu" id="menu"><h2>能加什麼？</h2>
<p>照下面的講法寫在留言格裡就行，不用講得很精確。點一下範例會放進「整體意見」，再改成你要的。</p>
<div class="menu-grid">{menu}</div></section>
</main>
<div class="dock" role="region" aria-label="複製留言給 Claude"><div class="wrap">
<div class="dock-row"><p class="status" id="copy-status" aria-live="polite">{n_comments_hint}</p>
<button type="button" class="btn primary" id="copy-btn">複製給 Claude</button></div>
<div class="copy-panel" id="copy-panel" hidden>
<label class="field" for="copy-out">要貼給 Claude 的文字（也可以自己選取複製）</label>
<textarea id="copy-out" readonly rows="6"></textarea>
<div class="actions"><button type="button" class="btn" id="copy-close">收起</button></div></div>
</div></div>
<script id="review-data" type="application/json">{data_json}</script>
<script>
{_asset("review.js")}
</script>
</body>
</html>
"""

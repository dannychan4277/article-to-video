"""審片頁的 HTML（一個檔、樣式與程式內嵌、不連網，作者在 Finder 點兩下就能看）。

頁面結構（測試與後面的票靠這些 class／id 找東西）：
  <html data-stage="preview|review|published" data-version="N">
  .stage（頁首）：預覽／審片用的影片（還沒出片）／已出片（放在哪）
  article.card[data-shot-id][data-shot-number][data-changed]   每個分鏡一張
    .timecode（有成片時是 button.seek[data-start]）、img.still[data-t]（第一張＝完成狀態）、.narration
    .slot[data-slot="numbers"]   數字對帳（#5 填）      .slot[data-slot="checks"]  自動檢查（#11 填）
    textarea.comment[data-shot-id]
  .page-slots .slot[data-slot=…]   整片層級的檢查結果（例：獨立審片）
  textarea#overall.overall、#menu（能加什麼；內容在預設收起來的 details.menu-fold 裡）、
  #copy-btn／#copy-status／#copy-out（複製給 Claude）
  <script id="review-data" type="application/json">  給 review.js 的資料（片名、版本、分鏡編號與 id）
  每一區裡的一行（li）：.mark（小標籤）＋.text（主要那行）＋.detail（補充）。有 more 或代號時 .text 是
  details.more 的 summary：點那一行才展開 more 與 .codes（「給 Claude：代號」）。
  沒問題的整區是 .slot.quiet（一行；有細節的是 details.fold）；攤開的幾行後面收起來的其他行是 details.fold.rest。

作者預設看得到的字要少（示範片從一萬三千字降到三千字以內，見 tests 的 SHOWN_LIMIT）：給 Claude 的代號、
審片員的長段說明、對得到出處的數字、已處理的問題都收起來；Claude 要細節讀 checks.json、check_log.json。

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
    more: str = ""
    """點開才看的補充（例：獨立審片員那一段「怎麼了、建議怎麼改」）。作者預設只看到 text（和 detail）；
    這一行底下有一個預設收起來的「細節」，點開才看得到 more（換行照留）。"""
    code: str = ""
    """給 Claude 的代號與技術說明（例「qa:SH03:jump@1a2b3c4d　跳變：…」）。不跟作者看的字放一起：
    收在這一行預設收起來的「細節」最後（標「給 Claude」）。"""


@dataclass(frozen=True)
class CardSection:
    """卡片（或整頁）上的一區檢查結果。"""

    slot: str
    """"numbers"＝數字對帳（#5），"checks"＝自動檢查（#11）；其他名字會排在這兩個後面。"""
    title: str
    items: list[CheckItem] = field(default_factory=list)
    note: str = ""
    """標題下面的一句說明（選填）。"""
    summary: str = ""
    """有寫就整區縮成安靜的一行（例「自動檢查沒發現問題」）：沒有 items 就只有這一行；有 items 的話收在這一行底下，
    點開才看得到。沒問題的檢查用它，作者不用一行一行讀。"""
    rest: list[CheckItem] = field(default_factory=list)
    """攤開的 items 後面、預設收起來的其他幾行（例：要作者對照的數字攤開，其他都對得到出處的收起來）。"""
    rest_summary: str = ""
    """rest 收起來時看得到的那一行（例「其他 11 個數字都對得到出處」）。"""
    rest_ok: bool = True
    """收起來的是沒問題的（那一行前面打勾）；False＝收起來的還要看（例「另外 16 項可改」），不打勾。"""


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
    title: str
    """片名（「日期-片名」資料夾去掉日期；專案資料夾自己一律叫「專案」）：頁面標題、頁首、複製給 Claude 的標頭都用它。"""
    storage_key: str
    version: int
    stage: str
    """"preview"（只有靜態圖）、"review"（審片用的影片做好了，還沒出片）、"published"（這一版已經出片）。"""
    generated: str
    duration: float
    cards: list[CardModel]
    video_src: str | None = None
    removed: list[tuple[str, int]] = field(default_factory=list)
    """這版拿掉的分鏡：(id, 上一版的編號)。"""
    page_sections: list[CardSection] = field(default_factory=list)
    aspect: str = "16x9"
    """畫面比例（timeline.VideoFormat.name）：9x16 時靜態圖、成片照直式排。"""
    published_to: str = ""
    """已出片時放在哪（給作者看的 Finder 路徑，例「影片 → 解說影片 → 2026-10-09-片名」）。"""
    published_version: int | None = None


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
    ("把文章裡的圖重畫成圖表",
     "講到你文章裡的某張圖時，照圖上的數字重畫成跟整支片一樣的圖表（柱狀、折線、大數字），來源寫「作者原文（圖幾）」。"
     "圖上的數字是 Claude 看圖讀的，卡片上標「原文圖表」，請對照你的原圖。",
     ["講到第二張圖的時候，把那張圖重畫成圖表",
      "分鏡 7 照我的圖 3 重畫成折線圖，只畫最近五年",
      "分鏡 5 的數字讀錯了，我圖上寫的是 3.96"]),
    ("旁白",
     "可以改字、拆句、換順序。改了旁白，配音和畫面出現的時間會跟著重算。",
     ["分鏡 3 的旁白改成：（寫你要的句子）",
      "這句太長，拆成兩句",
      "這裡不要講「暴跌」，講「下跌」就好"]),
    ("數字與出處",
     "畫面上的每個數字都要對得到出處：你文章裡寫的、你文章的圖上的（Claude 讀圖，標「原文圖表」）、公有數據，"
     "或寫清楚的算式。對不到的數字會擋下來，不會出片。",
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


def clock(seconds: float) -> str:
    """卡片上的時間：分:秒，四捨五入到整秒（例 65.5 → 1:06）。"""
    s = int(seconds + 0.5)
    return f"{s // 60}:{s % 60:02d}"


def _asset(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


_e = html.escape
_SLOT_ORDER = {"numbers": 0, "checks": 1}
CHANGED_LINKS = 8
"""「這版改了哪些分鏡」最多列幾個連結；改得更多就只寫幾個（卡片上有標記）。"""


def _item(it: CheckItem) -> str:
    """一行檢查結果：小標籤＋主要那行（＋補充）。有 more 或給 Claude 的代號時，主要那行本身就是可以點開的
    <details class="more">（預設收起來）：點了才看到 more 和代號（作者不用看代號；Claude 讀頁面原始碼或
    checks.json、check_log.json 都拿得到）。"""
    text = f'<span class="text">{_e(it.text)}</span>'
    if it.more or it.code:
        more = "".join(f"<p>{_e(line)}</p>" for line in it.more.splitlines() if line.strip())
        code = f'<p class="codes">給 Claude：<code>{_e(it.code)}</code></p>' if it.code else ""
        text = (f'<details class="more"><summary class="text" title="點一下看細節">{_e(it.text)}</summary>'
                f'{more}{code}</details>')
    return (f'<li class="{_e(it.status)}"><span class="mark">{_e(it.label or STATUSES.get(it.status, it.status))}</span>'
            + text + (f'<span class="detail">{_e(it.detail)}</span>' if it.detail else "") + "</li>")


def _items(items: Sequence[CheckItem]) -> str:
    return "<ul>" + "".join(_item(it) for it in items) + "</ul>" if items else ""


def _rest(sec: CardSection) -> str:
    """攤開的幾行後面，其他的收成一行（點開才看）。"""
    if not sec.rest:
        return ""
    cls = "quiet-line" if sec.rest_ok else "rest-line"
    return (f'<details class="fold rest"><summary class="{cls}">{_e(sec.rest_summary or "其他（點開看）")}'
            f'</summary>{_items(sec.rest)}</details>')


def _section(sec: CardSection, heading: str = "h3") -> str:
    note = f'<p class="hint">{_e(sec.note)}</p>' if sec.note else ""
    if sec.summary:  # 沒問題：安靜的一行，細節（有的話）點開才看
        if not sec.items and not sec.rest:
            return (f'<section class="slot quiet" data-slot="{_e(sec.slot)}">'
                    f'<p class="quiet-line">{_e(sec.summary)}</p></section>')
        return (f'<section class="slot quiet" data-slot="{_e(sec.slot)}"><details class="fold">'
                f'<summary class="quiet-line">{_e(sec.summary)}</summary>{note}{_items([*sec.items, *sec.rest])}'
                f'</details></section>')
    return (f'<section class="slot" data-slot="{_e(sec.slot)}"><{heading}>{_e(sec.title)}</{heading}>{note}'
            f'{_items(sec.items)}{_rest(sec)}</section>')


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


def _card(c: CardModel, has_video: bool, *, badge: bool = True) -> str:
    """一張分鏡卡。badge＝改過的卡要不要掛「這版改過」（每個分鏡都改過時頁首說一次就好，卡上不掛）。"""
    span = f"{clock(c.start)}–{clock(c.end)}"
    exact = f"{timecode(c.start)}–{timecode(c.end)}"  # 到十分之一秒：滑鼠停上去才看得到
    if has_video:
        tc = (f'<button type="button" class="timecode seek" data-start="{c.start:.3f}" '
              f'title="{exact}：成片跳到這一段">{span} ▶</button>')
    else:
        tc = f'<span class="timecode" data-start="{c.start:.3f}" title="{exact}">{span}</span>'
    badges = ""
    if c.changed and badge:
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
    if moments:  # 時間碼放在滑鼠停上去的提示與替代文字，不佔作者要讀的字
        figs = "".join(
            f'<figure class="moment"><a href="{s.src}" target="_blank" title="開大圖（{timecode(s.time)}）">'
            f'<img class="still" src="{s.src}" data-t="{s.time:.3f}" alt="分鏡 {c.number} 在 {timecode(s.time)} 的畫面" loading="lazy">'
            f'</a></figure>'
            for s in moments
        )
        media += f'<div class="moments" title="過程：每個新畫面出現之前的樣子">{figs}</div>'

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
        f'<div class="narration" role="group" aria-label="旁白">{narr}</div>'
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
    if changed and len(changed) == len(m.cards):
        parts.append(f"<p><strong>這版每個分鏡都改過（{len(changed)} 個）。</strong></p>")
    elif len(changed) > CHANGED_LINKS:  # 改了一大半：列一長串連結不如看卡片上的標記
        parts.append(f"<p><strong>這版改了 {len(changed)} 個分鏡</strong>（卡片上有「這版改過」）</p>")
    elif changed:
        links = "、".join(f'<a href="#shot-{_e(c.id)}">分鏡 {c.number}</a>' for c in changed)
        parts.append(f"<p><strong>這版改了 {len(changed)} 個分鏡：</strong><span class=\"changed-list\">{links}</span>"
                     "（卡片上有「這版改過」）</p>")
    if m.removed:
        gone = "、".join(f"上一版的分鏡 {n}" for _, n in m.removed)
        parts.append(f"<p><strong>拿掉了：</strong>{_e(gone)}</p>")
    return f'<section class="panel notice" id="changes">{"".join(parts)}</section>'


def _stage(m: PageModel) -> str:
    """頁首的階段：照實說作者現在看的是什麼。build 做的影片是給他審的，publish 之後才算出片。"""
    if m.stage == "published":
        v = f"（第 {m.published_version} 版）" if m.published_version else ""
        where = f"：在 Finder 的「{m.published_to}」" if m.published_to else ""
        return f'<span class="stage published">已出片{v}{_e(where)}</span>'
    if m.stage == "review":
        return '<span class="stage review">審片用的影片：還沒出片，你看過、點頭之後才出片</span>'
    return '<span class="stage">預覽：只有靜態圖，還沒做成影片、還沒出片</span>'


def render_review_html(m: PageModel) -> str:
    has_video = m.video_src is not None
    stage = _stage(m)
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
        "project": m.title,
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
    n_comments_hint = "寫好留言，按「複製給 Claude」。"
    return f"""<!doctype html>
<html lang="zh-Hant-TW" data-stage="{_e(m.stage)}" data-version="{m.version}" data-aspect="{_e(m.aspect)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>審片頁｜{_e(m.title)}（第 {m.version} 版）</title>
<style>
{_asset("review.css")}
</style>
</head>
<body>
<header class="top"><div class="wrap">
<p class="eyebrow">審片頁 · 第 {m.version} 版 {stage}</p>
<h1>{_e(m.title)}</h1>
<p class="meta">{len(m.cards)} 個分鏡 · 全長 {timecode(m.duration)} · {_e(m.generated)} 產生</p>
</div></header>
<main class="wrap">
<section class="panel howto"><h2>怎麼用這一頁</h2><ol>
<li>一張卡一個分鏡（大圖是最後的畫面，小圖是過程）。想改哪裡就寫在那張卡的「想改什麼？」，整支片的意見寫在最下面。</li>
<li>寫完按最下方的「複製給 Claude」，貼回 Claude 的對話框。寫到一半關掉，留言也會留著。</li>
</ol></section>
<section class="panel notice" id="carry" hidden><p id="carry-text"></p>
<div class="actions"><button type="button" class="btn primary" id="carry-yes">放回來</button>
<button type="button" class="btn" id="carry-no">不要了</button></div></section>
{_changes(m)}
{film}
{page_slots}
<section class="cards" aria-label="分鏡">
{chr(10).join(_card(c, has_video, badge=not all(x.changed for x in m.cards)) for c in m.cards)}
</section>
<section class="panel overall-box"><h2><label for="overall">整體意見</label></h2>
<p class="hint">例如整支片的節奏、聲音、配樂。</p>
<textarea id="overall" class="overall" rows="4" placeholder="例：整體節奏有點快／聲音想換一個"></textarea></section>
<section class="panel menu" id="menu"><details class="menu-fold">
<summary><h2>能加什麼？</h2><span class="hint">點開看範例講法</span></summary>
<p>照下面的講法寫在留言格裡就行，不用講得很精確。點一下範例會放進「整體意見」，再改成你要的。</p>
<div class="menu-grid">{menu}</div></details></section>
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

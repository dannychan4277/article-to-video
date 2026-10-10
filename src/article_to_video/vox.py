"""Vox 分鏡：把文章擷取出來的原圖（圖N）貼上牛皮紙桌面（引擎語法 y2_vox）。

分鏡設定（shots.json）裡 Vox 分鏡寫 "vox": {"cues": [...]}，cue 照出場順序：
  {"at": …, "kind": "image", "image": "圖1", "caption"?: "…"}     文章原圖，下方黑底標籤「圖1」（有 caption 就「圖1｜caption」）
  {"at": …, "kind": "excerpt", "quote"?: "原文裡的幾個字", "paragraph"?: 段號, "occurrence"?: n, "caption"?: "…"}
                                                                  原文剪報：剪到引文所在的整句、靠左重新分行（excerpt.py），標籤「原文段落」
  {"at": 錨點, "kind": "box", "rect": [x, y, 寬, 高]}               框原圖（原圖的像素）；"find": "0.62" 框剪報裡的字（位置自動算）
  {"at": …, "kind": "title" | "point", "text": "…"}                  黑底白字標題條／橫線索引卡（字要數字對帳）
框畫在它前面最近的那張原圖或剪報上（同一張桌面，接續分鏡也算），中間不能插文字卡；框要錨在旁白唸到那個數字的詞上。

at：數字＝從這個分鏡開始算的秒數（不能是負的），或錨點。

建置時 resolve_vox() 讀 article/article.json，把每個 cue 換成給引擎的欄位（圖片絕對路徑、標籤），
找不到的圖一次列出來（繁中）。Vox 片段用到的出處放進 ClipPlan.sources（片尾卡彙整）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from article_to_video.clips import GRAMMAR_VOX
from article_to_video.commands import cli_command
from article_to_video.excerpt import Excerpt, ExcerptError, make_excerpt
from article_to_video.project import Anchor, Cue, Project, ProjectError, Shot, VoxConfig

__all__ = ["ARTICLE_SOURCE", "EXCERPT_LABEL", "Target", "VoxBox", "VoxPlan", "resolve_vox", "vox_clip_spec", "vox_texts"]

DRIFT = 0.008
"""相機停在同一件素材超過 1.5 秒時，每秒慢慢推近 0.8%（停留慢漂）。"""
EXCERPT_LABEL = "原文段落"
"""原文剪報下方的標籤。"""
BOX_VERSION = "1"
"""框的雜湊版本（改了雜湊的算法才加一；所有框都要重新核對）。"""
ARTICLE_SOURCE = "作者原文"
"""片尾卡上，文章原圖、原文剪報的出處名稱（括號裡列用到的圖號）。"""


@dataclass(frozen=True)
class Figure:
    """文章原圖（article.json 的 images[]）。"""

    label: str
    path: Path
    width: int
    height: int
    caption: str | None = None


@dataclass(frozen=True)
class Target:
    """桌面上一件可以被框的素材：文章原圖或原文剪報。"""

    kind: str
    """"image" 或 "excerpt"。"""
    label: str
    """給人看的名字：「圖1」「原文第 4 段」。"""
    image: Path
    width: int
    height: int
    excerpt: Excerpt | None = None


@dataclass(frozen=True)
class VoxBox:
    """一個框（螢光筆＋紅圈）：框在哪張圖的哪裡、旁白唸到哪個詞時出現。核對用的雜湊見 box_hash。"""

    id: str
    """「SH02:1」＝分鏡 SH02 的第 1 個框。"""
    shot_id: str
    shot_number: int
    index: int
    where: str
    target: Target
    rect: tuple[int, int, int, int]
    """框在 target.image 上的像素範圍 (x, y, 寬, 高)。"""
    find: str | None
    """框剪報時要找的字（框原圖是 None）。"""
    word: str
    """錨點的詞（旁白唸到這裡時框出現）。"""
    sentence: str
    """錨點所在的那句旁白。"""
    hash: str
    context: tuple[int, int, int, int] = (0, 0, 0, 0)
    """推近後要跟框一起留在畫面裡的範圍（target.image 的像素 (x, y, 寬, 高)）：剪報是框所在的那一整行；
    原圖是框那一列（橫向）和那一欄（直向）有字的範圍——表格的列名與欄名、柱狀圖柱子底下的年份。"""
    zoom: float | None = None
    """作者指定的推近倍數（shots.json 的 zoom；1＝整張圖）。沒寫＝照 context 自動。"""

    @property
    def token(self) -> str:
        """核對代號：SH02:1@<雜湊前 8 碼>。框一改，代號就變。"""
        return f"{self.id}@{self.hash[:8]}"

    def describe(self) -> str:
        what = f"「{self.find}」" if self.find else f"{list(self.rect)}"
        return f"{self.target.label} 上的{what}，旁白唸到「{self.word}」時出現（「{self.sentence}」）"


@dataclass(frozen=True)
class VoxPlan:
    """Vox 分鏡解析完的結果（建置計畫的一部分）。"""

    boxes: list[VoxBox] = field(default_factory=list)


@dataclass
class _Article:
    figures: dict[str, Figure]
    paragraphs: dict[int, str]
    excerpt_dir: Path
    used: set[Path] = field(default_factory=set)
    """這次用到的剪報圖（其他舊的清掉）。"""


def _load_article(project: Project, problems: list[str]) -> _Article | None:
    manifest = project.paths.article / "article.json"
    if not manifest.is_file():
        problems.append(
            f"Vox 分鏡要用文章的原圖和段落，但這個專案還沒擷取文章（找不到 {manifest}）。"
            f"先擷取文章：{cli_command('extract', project.root, '"<文章檔>"')}"
        )
        return None
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        problems.append(f"讀不懂 {manifest}（{e}）：重跑一次 extract")
        return None
    figures: dict[str, Figure] = {}
    for img in doc.get("images") or []:
        label = img.get("label") or f"圖{img.get('n')}"
        figures[label] = Figure(label=label, path=(manifest.parent / img["file"]).absolute(),
                                width=img.get("width") or 0, height=img.get("height") or 0, caption=img.get("caption"))
    paragraphs = {p["n"]: p["text"] for p in doc.get("paragraphs") or [] if isinstance(p.get("text"), str)}
    return _Article(figures=figures, paragraphs=paragraphs, excerpt_dir=project.paths.build / "vox")


def resolve_vox(project: Project) -> tuple[Project, VoxPlan]:
    """把 Vox 分鏡的 cue 換成給引擎的欄位、算出每個框。沒有 Vox 分鏡就原樣回傳。問題一次列出（ProjectError）。"""
    if not any(s.vox for s in project.shots):
        return project, VoxPlan()
    problems: list[str] = []
    article = _load_article(project, problems)
    if article is None:
        raise ProjectError(problems, title="Vox 分鏡找不到文章")
    shots = []
    boxes: list[VoxBox] = []
    target: Target | None = None
    keep = _finds_per_excerpt(project)
    for shot in project.shots:
        if shot.vox is None:
            shots.append(shot)
            target = None
            continue
        if not shot.continues:
            target = None
        cues: list[Cue] = []
        sources: list[str] = []
        texts: list[tuple[str, str]] = []
        for cue in shot.vox.cues:
            if cue.kind == "box":
                box = _resolve_box(cue, shot, target, sum(b.shot_id == shot.id for b in boxes) + 1, project, problems)
                if box is None:
                    continue
                boxes.append(box)
                tw, th = box.target.width, box.target.height
                norm = lambda r: [round(r[0] / tw, 6), round(r[1] / th, 6), round(r[2] / tw, 6), round(r[3] / th, 6)]  # noqa: E731
                # FRAMING 是給 vox_clip_spec 排鏡頭用的（排好就拿掉，引擎看不到）
                cues.append(replace(cue, fields={"kind": "highlight", "box": box.id, "data": {"rect": norm(box.rect)},
                                                 FRAMING: {"keep": norm(box.context), "zoom": box.zoom, "size": [tw, th]}}))
                continue
            if cue.kind in ("title", "point"):
                texts.append(("標題卡" if cue.kind == "title" else "索引卡", cue.fields["text"]))
                cues.append(replace(cue, fields={"kind": cue.kind, "text": cue.fields["text"]}))
                target = None
                continue
            resolved = _resolve_material(cue, article, sources, problems, keep.get(id(cue), []))
            if resolved is None:
                target = None  # 已經報錯；後面框這件素材的框不用再報一次
            else:
                engine, target = resolved
                cues.append(replace(cue, fields=engine))
                if target and target.excerpt:
                    texts.append(("原文剪報", target.excerpt.text))
                if cue.fields.get("caption"):
                    texts.append(("圖說", cue.fields["caption"].strip()))
        shots.append(replace(shot, vox=VoxConfig(cues=cues, sources=list(dict.fromkeys(sources)), texts=texts)))
    if problems:
        raise ProjectError(problems, title="Vox 分鏡的素材有問題")
    if article.excerpt_dir.is_dir():
        for old in article.excerpt_dir.glob("excerpt-*.png"):
            if old not in article.used:
                old.unlink()
    return replace(project, shots=shots), VoxPlan(boxes=boxes)


def _finds_per_excerpt(project: Project) -> dict[int, list[str]]:
    """每張剪報（cue 的 id）之後要框的字（box 的 find）：排剪報時這些字不拆到兩行，框才框得起來。"""
    out: dict[int, list[str]] = {}
    current: int | None = None
    for shot in project.shots:
        if shot.vox is None or not shot.continues:
            current = None
        if shot.vox is None:
            continue
        for cue in shot.vox.cues:
            if cue.kind == "excerpt":
                current = id(cue)
                out[current] = []
            elif cue.kind != "box":
                current = None
            elif current is not None and cue.fields.get("find"):
                out[current].append(cue.fields["find"])
    return out


def _find_paragraph(quote: str, paragraphs: dict[int, str]) -> tuple[int | None, str]:
    """沒寫 paragraph 時，從全文找引文在哪一段。回傳 (段號, 錯誤說明)。"""
    found = [n for n, t in paragraphs.items() if quote in t]
    if len(found) == 1:
        return found[0], ""
    if not found:
        return None, f"文章裡找不到「{quote}」（引文要照原文一字不差，標點、空白也算）"
    return None, f"「{quote}」在第 {'、'.join(map(str, found))} 段都有：加 \"paragraph\" 指定哪一段，或把引文寫長一點"


def _resolve_material(cue: Cue, article: _Article, sources: list[str], problems: list[str],
                      keep: list[str]) -> tuple[dict[str, Any], Target | None] | None:
    """原圖、剪報 → (給引擎的欄位, 之後的框要框的那件素材)。"""
    f = cue.fields
    caption = f"｜{f['caption'].strip()}" if f.get("caption") else ""
    if cue.kind == "image":
        figures = article.figures
        fig = figures.get(f["image"])
        if fig is None:
            have = "、".join(figures) or "（文章裡沒有圖）"
            problems.append(f"{cue.where}：文章裡沒有「{f['image']}」（有的圖：{have}）")
            return None
        if not fig.path.is_file():
            problems.append(f"{cue.where}：{fig.label} 的圖檔不見了（{fig.path}）。重跑一次 extract")
            return None
        if not fig.width or not fig.height:
            problems.append(f"{cue.where}：{fig.label} 是瀏覽器顯示不了的圖檔格式（{fig.path.suffix}），請作者換成 PNG 或 JPEG")
            return None
        sources.append(f"{ARTICLE_SOURCE}（{fig.label}）")
        engine = {"kind": "image", "image": str(fig.path), "text": fig.label + caption}
        return engine, Target("image", fig.label, fig.path, fig.width, fig.height)
    if cue.kind == "excerpt":
        quote = f.get("quote")
        n = f.get("paragraph")
        if n is None:
            n, why = _find_paragraph(quote, article.paragraphs)
            if n is None:
                problems.append(f"{cue.where}：{why}")
                return None
        if n not in article.paragraphs:
            problems.append(f"{cue.where}：文章沒有第 {n} 段（共 {len(article.paragraphs)} 段）")
            return None
        try:
            ex = make_excerpt(n, article.paragraphs[n], quote, article.excerpt_dir, occurrence=f.get("occurrence"), keep=keep)
        except ExcerptError as e:
            problems.append(f"{cue.where}：{e}")
            return None
        article.used.add(ex.image)
        sources.append(f"{ARTICLE_SOURCE}（第 {n} 段）")
        engine = {"kind": "image", "image": str(ex.image), "text": EXCERPT_LABEL + caption}
        return engine, Target("excerpt", f"原文第 {n} 段", ex.image, ex.width, ex.height, ex)
    raise AssertionError(cue.kind)


_SENTENCE_RE = re.compile(r"[^。！？!?]*[。！？!?]*")


def _sentence_at(text: str, i: int) -> str:
    """旁白段裡第 i 個字所在的那一句。"""
    pos = 0
    for m in _SENTENCE_RE.finditer(text):
        if m.start() <= i < m.end():
            return m.group().strip()
        pos = m.end()
    return text.strip()


def _resolve_box(cue: Cue, shot: Shot, target: Target | None, index: int, project: Project,
                 problems: list[str]) -> VoxBox | None:
    if target is None:  # 載入時已經報過（框前面沒有圖）
        return None
    f = cue.fields
    find = f.get("find")
    if find is not None:
        assert target.excerpt is not None
        try:
            rect = target.excerpt.char_rect(find, f.get("occurrence"))
        except ExcerptError as e:
            problems.append(f"{cue.where}（框）：{e}")
            return None
    else:
        x, y, w, h = (int(round(v)) for v in f["rect"])
        if x + w > target.width or y + h > target.height:
            problems.append(
                f"{cue.where}（框）的 rect {f['rect']} 超出 {target.label} 的範圍（{target.width}×{target.height} 像素）"
            )
            return None
        rect = (x, y, w, h)
    anchor = cue.at
    assert isinstance(anchor, Anchor) and anchor.span is not None
    sentence = _sentence_at(project.segments[anchor.segment], anchor.span[0])
    zoom = f.get("zoom")
    parts: list[Any] = [BOX_VERSION, hashlib.sha256(target.image.read_bytes()).hexdigest(), list(rect), find, anchor.word, sentence]
    if zoom is not None:
        parts.append(["zoom", zoom])  # 推近倍數也是靜態圖看到的樣子；沒寫的框雜湊跟以前一樣
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()
    context = (target.excerpt.line_rect(find, f.get("occurrence")) if find is not None
               else _image_context(target.image, rect))
    return VoxBox(id=f"{shot.id}:{index}", shot_id=shot.id, shot_number=shot.number, index=index, where=cue.where,
                  target=target, rect=rect, find=find, word=anchor.word or "", sentence=sentence, hash=digest,
                  context=context, zoom=float(zoom) if zoom is not None else None)


INK_DIFF = 64
"""原圖上「有字」：跟底色（四邊的中位數）任一色版差這麼多以上。淺灰格線、表頭淺底色不算。"""
CONTEXT_GAP = 0.3
"""從框往外找同一列／同一欄的字，空白超過原圖寬（高）的這個比例就停：別的柱子的數值不算這一列的。"""


def _image_context(path: Path, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """框所在那一列（框的高度範圍內，橫向）與那一欄（框的寬度範圍內，直向）有字的範圍，連框一起的外接矩形。
    表格：列名＋同列的數字、欄名＋同欄的數字；柱狀圖：數值標籤、柱子、底下的年份。
    直向找的時候，橫越這一欄的一整行字（標題、副標、零軸）不算：那不是這一欄的欄名。"""
    with Image.open(path) as im:
        a = np.asarray(im.convert("RGB")).astype(np.int16)
    h, w = a.shape[:2]
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    ink = np.abs(a - np.median(border, axis=0)).max(axis=2) > INK_DIFF
    x, y, rw, rh = rect
    bx0, bx1, by0, by1 = max(0, x), min(w, x + rw), max(0, y), min(h, y + rh)
    x0, x1 = _extent(ink[by0:by1].any(axis=0), bx0, bx1, CONTEXT_GAP * w)
    col = ink[:, bx0:bx1].any(axis=1)
    reach, glue = max(bx1 - bx0, 0.05 * w), max(1, round(0.015 * w))
    kernel = np.ones(2 * glue + 1)
    crossing = np.zeros(h, dtype=bool)
    for yy in np.nonzero(col)[0]:
        runs = np.convolve(ink[yy].astype(float), kernel, mode="same") > 0  # 字距、詞距黏成一整串
        inside = np.nonzero(runs[bx0:bx1])[0]
        left, right = bx0 + int(inside[0]), bx0 + int(inside[-1]) + 1
        while left > 0 and runs[left - 1]:
            left -= 1
        while right < w and runs[right]:
            right += 1
        crossing[yy] = bx0 - left > reach or right - bx1 > reach  # 橫越這一欄的一整行（標題、副標、零軸、表格底線）
    # 一行字的上下緣筆畫稀疏、那幾列自己不算橫越：連在一起的幾列（一行字、一根柱）一起判，三成以上橫越就整塊不算
    keep_rows = col.copy()
    ys = np.nonzero(col)[0]
    for group in np.split(ys, np.nonzero(np.diff(ys) > 2)[0] + 1) if ys.size else []:
        if crossing[group].mean() >= 0.3 and not (group[0] < by1 and group[-1] >= by0):
            keep_rows[group] = False
    y0, y1 = _extent(keep_rows, by0, by1, CONTEXT_GAP * h)
    return x0, y0, x1 - x0, y1 - y0


def _extent(mask: np.ndarray, lo: int, hi: int, max_gap: float) -> tuple[int, int]:
    """從 [lo, hi) 往兩邊延伸到 mask 為真的位置，空白不超過 max_gap 就繼續。"""
    idx = np.nonzero(mask)[0]
    a, b = lo, hi
    for p in idx[idx < lo][::-1]:
        if a - p > max_gap:
            break
        a = int(p)
    for p in idx[idx >= hi]:
        if p - b >= max_gap:
            break
        b = int(p) + 1
    return a, b


def vox_texts(plan: Any, st: Any) -> list[Any]:
    """數字對帳（numbers.py）的資料來源：Vox 分鏡上畫面的字——標題卡、索引卡、圖說、原文剪報。
    「圖1」「原文段落」這種標籤是編號，不算；原圖裡的數字（圖片像素）是原文本身，也不算。"""
    from article_to_video.numbers import ScreenText  # 避免循環匯入

    if st.shot.vox is None:
        return []
    return [ScreenText(where, text) for where, text in st.shot.vox.texts]


def vox_clip_spec(cues: list[dict[str, Any]], duration: float, fmt: Any) -> dict[str, Any]:
    """一個 Vox 片段給引擎的 spec。cues：給引擎的欄位＋片段內秒數 at。fmt.spec_base()：寬高、fps、要讓開的邊
    （字幕帶；直式再加平台介面區，引擎的相機會讓開）。框的鏡頭（推近到哪、要不要先移過去）在這裡照這次的比例排好。"""
    base = fmt.spec_base()
    safe = base.get("safe", {})
    usable = ((base["width"] - safe.get("left", 0) - safe.get("right", 0))
              / (base["height"] - safe.get("top", 0) - safe.get("bottom", 0)))
    return {"grammar": GRAMMAR_VOX, "duration": duration, **base,
            "data": {"drift": DRIFT, "frameFill": FRAME_FILL, "camera": dict(CAMERA)},
            "cues": _frame_boxes(cues, usable)}


CAMERA = {"pushAt": 0.1, "push": 0.8, "travel": 0.8, "itemTravel": 0.75}
"""框的鏡頭時間（秒；寫進 spec 的 data.camera，引擎照這個排，boxes.py 照這個算停多久）：
框揭開後 pushAt 秒開始推近、推 push 秒（＝揭開後 0.9 秒停住）；要先移過去的框（data.travel），鏡頭在揭開前
travel 秒出發、揭開前一幀到；框之後換下一件素材（圖、剪報、文字卡），鏡頭在它出現前 itemTravel＋ARRIVE 秒出發。"""
ARRIVE = 0.05
"""移到下一件素材時，在它出現前多久到位（引擎原本的規矩）。"""
MIN_HOLD = 1.2
"""框推近停住之後，鏡頭至少停這麼久才離開（真實試跑 #5：原本只停 0.4～1 秒，看不清楚就甩走）。"""


def box_hold(gap: float, next_kind: str, travel: bool = False) -> tuple[float, float]:
    """框揭開後 gap 秒換下一件時：(鏡頭推近停住後停了幾秒, 框到下一件至少要隔幾秒)。
    next_kind："box"（下一個框；travel＝要先移過去）、"item"（下一張圖、剪報、文字卡）、"end"（片段結束、切走）。
    跟引擎 y2_vox 排相機關鍵幀的方式一樣（CAMERA）：停住＝揭開後 pushAt＋push 秒；離開＝下一個框推近開始、
    移向下一個框或下一件素材的出發時刻、或切走。"""
    settle = CAMERA["pushAt"] + CAMERA["push"]
    lead = {"box": CAMERA["travel"] if travel else -CAMERA["pushAt"], "item": CAMERA["itemTravel"] + ARRIVE}.get(next_kind, 0.0)
    return gap - settle - lead, settle + MIN_HOLD + lead


FRAMING = "_framing"
"""resolve_vox 放在框的 cue 上、給 vox_clip_spec 排鏡頭的資料（keep、zoom、原圖尺寸）；排好就拿掉。"""
MAX_AUTO_ZOOM = 2.5
"""自動推近最多推到「整張圖剛好塞滿畫面」的幾倍（框附近沒別的字時，不會推到原圖糊掉）。"""
FRAME_PAD = 0.03
"""要留在畫面裡的範圍四周再多留的邊（相對原圖寬高）。"""
FRAME_FILL = 0.94
"""引擎把 data.frame 推近到占可用畫面的這個比例（寫進 spec 的 data.frameFill，引擎照這個數推）。"""


def _frame_boxes(cues: list[dict[str, Any]], usable: float) -> list[dict[str, Any]]:
    """每個框推近後畫面要包住原圖的哪一塊（data.frame，相對原圖 0..1，寬高比＝可用畫面）、
    要不要先把鏡頭移過去（data.travel：框不在上一個框的畫面裡）。真實試跑 #6：只看框多大就推，
    正在唸的那一行、表格的欄名與年份常被切出畫面。"""
    out = [dict(c) for c in cues]
    prev: list[float] | None = None  # 同一張圖上一個框的畫面；中間貼了別的東西就重來
    for c in sorted(out, key=lambda c: c["at"]):  # 引擎也是照時間排（同時間照原順序，sorted 是穩定的）
        if c.get("kind") != "highlight" or FRAMING not in c:
            prev = None
            continue
        info = c.pop(FRAMING)
        (iw, ih), rect = info["size"], c["data"]["rect"]
        frame = _box_frame(rect, info["keep"], info["zoom"], usable * ih / iw)
        # 框揭開那一刻（螢光筆從左邊掃起）在不在上一個框的畫面裡：引擎把 frame 放到可用畫面的 FRAME_FILL，四周多看到一點
        seen = _grow(prev, (1 / FRAME_FILL - 1) / 2, 0.0) if prev else None
        travel = seen is not None and not _inside(_grow(rect, 0.1, 0.0), seen)
        c["data"] = {**c["data"], "frame": [round(v, 6) for v in frame], "travel": travel}
        prev = frame
    return out


def _expand(w: float, h: float, aspect: float) -> tuple[float, float]:
    """(w, h) 往外補到寬高比 aspect（相對原圖的單位）。"""
    return (w, w / aspect) if w / h > aspect else (h * aspect, h)


def _box_frame(rect: list[float], keep: list[float], zoom: float | None, aspect: float) -> list[float]:
    whole = _expand(1.0, 1.0, aspect)   # 整張圖剛好塞滿畫面
    cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    if zoom is not None:
        w, h = whole[0] / zoom, whole[1] / zoom
    else:
        x0, y0 = min(rect[0], keep[0]) - FRAME_PAD, min(rect[1], keep[1]) - FRAME_PAD
        x1 = max(rect[0] + rect[2], keep[0] + keep[2]) + FRAME_PAD
        y1 = max(rect[1] + rect[3], keep[1] + keep[3]) + FRAME_PAD
        w, h = _expand(x1 - x0, y1 - y0, aspect)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if w < whole[0] / MAX_AUTO_ZOOM:
            w, h = whole[0] / MAX_AUTO_ZOOM, whole[1] / MAX_AUTO_ZOOM
    x, y = _place(cx - w / 2, w), _place(cy - h / 2, h)
    if not _inside(rect, [x, y, w, h]):  # 框比指定的推近範圍還大：至少整個框看得到
        x0, y0 = min(x, rect[0]), min(y, rect[1])
        w, h = _expand(max(x + w, rect[0] + rect[2]) - x0, max(y + h, rect[1] + rect[3]) - y0, aspect)
        x, y = x0, y0
    return [x, y, w, h]


def _place(start: float, size: float) -> float:
    """畫面盡量落在原圖裡（少拍到桌面、多拍到圖邊的列名與欄名）；比整張圖還大就置中。"""
    return min(max(start, 0.0), 1.0 - size) if size <= 1 else (1.0 - size) / 2


def _grow(r: list[float], k: float, pad: float) -> list[float]:
    return [r[0] - r[2] * k - pad, r[1] - r[3] * k - pad, r[2] * (1 + 2 * k) + 2 * pad, r[3] * (1 + 2 * k) + 2 * pad]


def _inside(r: list[float], f: list[float]) -> bool:
    return f[0] <= r[0] + 1e-9 and f[1] <= r[1] + 1e-9 and r[0] + r[2] <= f[0] + f[2] + 1e-9 and r[1] + r[3] <= f[1] + f[3] + 1e-9

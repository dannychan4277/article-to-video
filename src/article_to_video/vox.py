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
                x, y, w, h = box.rect
                tw, th = box.target.width, box.target.height
                rect01 = [round(x / tw, 6), round(y / th, 6), round(w / tw, 6), round(h / th, 6)]
                cues.append(replace(cue, fields={"kind": "highlight", "data": {"rect": rect01}}))
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
    digest = hashlib.sha256(json.dumps(
        [BOX_VERSION, hashlib.sha256(target.image.read_bytes()).hexdigest(), list(rect), find, anchor.word, sentence],
        ensure_ascii=False,
    ).encode()).hexdigest()
    return VoxBox(id=f"{shot.id}:{index}", shot_id=shot.id, shot_number=shot.number, index=index, where=cue.where,
                  target=target, rect=rect, find=find, word=anchor.word or "", sentence=sentence, hash=digest)


def vox_texts(plan: Any, st: Any) -> list[Any]:
    """數字對帳（numbers.py）的資料來源：Vox 分鏡上畫面的字——標題卡、索引卡、圖說、原文剪報。
    「圖1」「原文段落」這種標籤是編號，不算；原圖裡的數字（圖片像素）是原文本身，也不算。"""
    from article_to_video.numbers import ScreenText  # 避免循環匯入

    if st.shot.vox is None:
        return []
    return [ScreenText(where, text) for where, text in st.shot.vox.texts]


def vox_clip_spec(cues: list[dict[str, Any]], duration: float, fmt: Any) -> dict[str, Any]:
    """一個 Vox 片段給引擎的 spec。cues：給引擎的欄位＋片段內秒數 at。fmt.spec_base()：寬高、fps、要讓開的邊
    （字幕帶；直式再加平台介面區，引擎的相機會讓開）。"""
    return {"grammar": GRAMMAR_VOX, "duration": duration, **fmt.spec_base(), "data": {"drift": DRIFT}, "cues": cues}

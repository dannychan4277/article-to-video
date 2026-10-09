"""Vox 分鏡：把文章擷取出來的原圖（圖N）貼上牛皮紙桌面（引擎語法 y2_vox）。

分鏡設定（shots.json）裡 Vox 分鏡寫 "vox": {"cues": [...]}，cue 照出場順序：
  {"at": …, "kind": "image", "image": "圖1", "caption"?: "…"}     文章原圖，下方黑底標籤「圖1」（有 caption 就「圖1｜caption」）
  {"at": …, "kind": "excerpt", "quote"?: "原文裡的幾個字", "paragraph"?: 段號, "occurrence"?: n, "caption"?: "…"}
                                                                  原文剪報：剪到引文所在的整句、殘句遮住（excerpt.py），標籤「原文段落」

at：數字＝從這個分鏡開始算的秒數（不能是負的），或錨點。

建置時 resolve_vox() 讀 article/article.json，把每個 cue 換成給引擎的欄位（圖片絕對路徑、標籤），
找不到的圖一次列出來（繁中）。Vox 片段用到的出處放進 ClipPlan.sources（片尾卡彙整）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from article_to_video.excerpt import ExcerptError, make_excerpt
from article_to_video.project import Cue, Project, ProjectError, VoxConfig

__all__ = ["ARTICLE_SOURCE", "EXCERPT_LABEL", "VoxPlan", "resolve_vox", "vox_clip_spec"]

Y2 = "y2_vox"
DRIFT = 0.008
"""相機停在同一件素材超過 1.5 秒時，每秒慢慢推近 0.8%（停留慢漂）。"""
EXCERPT_LABEL = "原文段落"
"""原文剪報下方的標籤。"""
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
class VoxPlan:
    """Vox 分鏡解析完的結果（建置計畫的一部分）。"""


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
            "先跑 python -m article_to_video extract <專案> <文章檔>"
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
    """把 Vox 分鏡的 cue 換成給引擎的欄位。沒有 Vox 分鏡就原樣回傳。問題一次列出（ProjectError）。"""
    if not any(s.vox for s in project.shots):
        return project, VoxPlan()
    problems: list[str] = []
    article = _load_article(project, problems)
    if article is None:
        raise ProjectError(problems, title="Vox 分鏡找不到文章")
    shots = []
    for shot in project.shots:
        if shot.vox is None:
            shots.append(shot)
            continue
        cues: list[Cue] = []
        sources: list[str] = []
        for cue in shot.vox.cues:
            engine = _engine_fields(cue, article, sources, problems)
            if engine is not None:
                cues.append(replace(cue, fields=engine))
        shots.append(replace(shot, vox=VoxConfig(cues=cues, sources=list(dict.fromkeys(sources)))))
    if problems:
        raise ProjectError(problems, title="Vox 分鏡的素材有問題")
    if article.excerpt_dir.is_dir():
        for old in article.excerpt_dir.glob("excerpt-*.png"):
            if old not in article.used:
                old.unlink()
    return replace(project, shots=shots), VoxPlan()


def _find_paragraph(quote: str, paragraphs: dict[int, str]) -> tuple[int | None, str]:
    """沒寫 paragraph 時，從全文找引文在哪一段。回傳 (段號, 錯誤說明)。"""
    found = [n for n, t in paragraphs.items() if quote in t]
    if len(found) == 1:
        return found[0], ""
    if not found:
        return None, f"文章裡找不到「{quote}」（引文要照原文一字不差，標點、空白也算）"
    return None, f"「{quote}」在第 {'、'.join(map(str, found))} 段都有：加 \"paragraph\" 指定哪一段，或把引文寫長一點"


def _engine_fields(cue: Cue, article: _Article, sources: list[str], problems: list[str]) -> dict[str, Any] | None:
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
        return {"kind": "image", "image": str(fig.path), "text": fig.label + caption}
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
            ex = make_excerpt(n, article.paragraphs[n], quote, article.excerpt_dir, occurrence=f.get("occurrence"))
        except ExcerptError as e:
            problems.append(f"{cue.where}：{e}")
            return None
        article.used.add(ex.image)
        sources.append(f"{ARTICLE_SOURCE}（第 {n} 段）")
        return {"kind": "image", "image": str(ex.image), "text": EXCERPT_LABEL + caption}
    raise AssertionError(cue.kind)


def vox_clip_spec(cues: list[dict[str, Any]], duration: float, fmt: Any) -> dict[str, Any]:
    """一個 Vox 片段給引擎的 spec。cues：給引擎的欄位＋片段內秒數 at。"""
    return {
        "grammar": Y2,
        "duration": duration,
        "fps": fmt.fps,
        "width": fmt.width,
        "height": fmt.height,
        "safe": {"bottom": fmt.safe_bottom},
        "data": {"drift": DRIFT},
        "cues": cues,
    }

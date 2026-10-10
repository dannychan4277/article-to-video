"""片尾卡：每支片最後一段旁白唸完之後接一張卡，長 settings.tail 秒（算進成片長度）。

卡上有：頻道名（＋logo）、本片資料來源（彙整）、「僅供教育，非投資建議」、「配音為 AI 合成」。
用內嵌引擎的自訂語法 engine/clips/a2v_end_card.js 渲（跟圖表同一組字型、同一套缺字檢查），跟分鏡一樣進片段快取。

資料來源怎麼來：每個片段計畫（timeline.ClipPlan）的 `sources`。財經圖表片段放圖上的來源列（data.source，
已經過 prepare_chart_data，所以公有數據那張票自動補的來源也會進來）；Vox 片段（Vox 那張票）把它用到的出處放進
自己的 ClipPlan.sources 就會出現在卡上。彙整規則見 merge_sources。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from article_to_video.clips import GRAMMAR_END_CARD
from article_to_video.commands import cli_command
from article_to_video.project import Project, ProjectError

if TYPE_CHECKING:
    from article_to_video.timeline import ClipPlan, Timeline, VideoFormat

__all__ = ["END_CARD_GRAMMAR", "END_CARD_ID", "NOTES", "merge_sources", "missing_channel_error", "plan_end_card"]

END_CARD_ID = "end.card"
"""片尾卡的片段 id（檔名 specs/end.card.json、clips/end.card.mp4）。有句點，分鏡 id 不會跟它撞名。"""
END_CARD_GRAMMAR = GRAMMAR_END_CARD
NOTES = ("僅供教育，非投資建議", "配音為 AI 合成")
_PREFIX_RE = re.compile(r"^\s*(?:資料來源|数据来源|資料出處|資料|资料|來源|来源|出處|出处|sources?)\s*[:：]\s*", re.IGNORECASE)
_GROUP_RE = re.compile(r"^(?P<name>[^（(]+?)\s*[（(](?P<items>[^（()）]+)[）)](?P<rest>.*)$")


_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)?$")
"""數列代號，例 DGS10、THREEFYTP10。"""
_FIGURE_RE = re.compile(r"^圖\s*(\d+)$")
_PARAGRAPH_RE = re.compile(r"^第\s*(\d+)\s*段$")


def _references(items: list[str]) -> bool:
    """括號裡每一項都是代號、圖號或段號（可以合併的那種），不是說明文字。"""
    return bool(items) and all(_CODE_RE.match(x) or _FIGURE_RE.match(x) or _PARAGRAPH_RE.match(x) for x in items)


def _format_references(items: list[str]) -> str:
    """代號照第一次出現的順序；圖號、段號各自從小排到大：「圖1、圖2、第 4、16、19 段」。"""
    codes = [x for x in items if _CODE_RE.match(x)]
    figures = sorted({int(m[1]) for x in items if (m := _FIGURE_RE.match(x))})
    paragraphs = sorted({int(m[1]) for x in items if (m := _PARAGRAPH_RE.match(x))})
    parts = codes + [f"圖{n}" for n in figures]
    if paragraphs:
        parts.append(f"第 {'、'.join(map(str, paragraphs))} 段")
    return "、".join(parts)


_NOTE_SPLIT_RE = re.compile(r"[；;]")


def _notes(rest: str) -> list[str]:
    """括號後面的說明，一欄一項：「；月平均；2026-10-10 擷取」→ ["月平均", "2026-10-10 擷取"]。"""
    return [x.strip(" ，,。") for x in _NOTE_SPLIT_RE.split(rest) if x.strip(" ，,。")]


@dataclass
class _Source:
    """同一個出處（括號前面的名字）的所有寫法彙整起來。"""

    name: str
    refs: list[str]
    notes: list[tuple[bool, str]] | None = None
    """每一列都有的說明，(是不是括號裡的說明, 文字)；None＝還沒有哪一列帶說明或代號（只有光寫名字的）。"""
    texts: list[str] = field(default_factory=list)
    """原文寫法（去掉前綴、重複的），只有一種寫法時照原樣印。"""

    def add(self, text: str, refs: list[str], notes: list[tuple[bool, str]], bare: bool) -> None:
        if text not in self.texts:
            self.texts.append(text)
        if bare:
            return
        self.refs.extend(x for x in refs if x not in self.refs)
        self.notes = list(notes) if self.notes is None else [n for n in self.notes if n in notes]

    def text(self) -> str:
        written = [t for t in self.texts if t != self.name] or self.texts
        if len(written) == 1:
            return written[0]
        out = self.name + (f"（{_format_references(self.refs)}）" if self.refs else "")
        for described, note in self.notes or []:
            out += f"（{note}）" if described and not self.refs and out == self.name else f"；{note}"
        return out


def merge_sources(texts: Iterable[str]) -> list[str]:
    """圖上的來源列 → 片尾卡的來源清單：**同一個出處只列一行**。

    - 拿掉「來源：」「資料來源：」「資料：」這類前綴（卡上統一有「資料來源」小標）。一列就是一個出處
      （公有數據自動寫的來源行用「；」分欄：「FRED，聯準會（DGS10）；月平均；2026-10-09 擷取」，不拆成好幾個出處）。
    - 出處＝括號前面的名字（「FRED，聯準會」「作者原文」）。同名的併成一行，順序照名字第一次出現：
      - 括號裡的代號／圖號／段號全部併進去：「FRED（DGS2）」＋「FRED（DGS10）」→「FRED（DGS2、DGS10）」；
        「作者原文（圖2）」＋「作者原文（第 4 段）」＋「作者原文（圖1）」→「作者原文（圖1、圖2、第 4 段）」。
      - 說明（括號後面「；」分開的欄，或括號裡寫的是說明文字）只留每一列都有的：擷取日期一樣就留，
        各張圖不同的算法（月平均、年平均、「十年期減兩年期＝…自算」）拿掉——圖上的來源行自己寫著。
      - 光寫名字的（「作者原文」）併進同名的那一行，不另成一行。
    - 一個出處只有一種寫法時照原樣（括號裡是說明文字的「財政部（10 年期、2 年期），月資料」不動）。
    """
    sources: dict[str, _Source] = {}
    for raw in texts:
        if not isinstance(raw, str):
            continue
        t = " ".join(_PREFIX_RE.sub("", raw).split()).strip("，,。； ")
        if not t:
            continue
        m = _GROUP_RE.match(t)
        if m is None:
            name, refs, notes, bare = t, [], [], True
        else:
            name = m["name"].strip()
            items = [x.strip() for x in re.split(r"[、,，]", m["items"]) if x.strip()]
            if _references(items):
                refs, notes = items, [(False, n) for n in _notes(m["rest"])]
            else:
                refs, notes = [], [(True, m["items"].strip()), *((False, n) for n in _notes(m["rest"]))]
            bare = False
        sources.setdefault(name, _Source(name, [])).add(t, refs, notes, bare)
    return [s.text() for s in sources.values()]


def missing_channel_error(project_root: Path, data_dir: str | os.PathLike | None = None) -> ProjectError:
    """還沒設定頻道名、片尾卡做不出來的錯誤。設定快照已經記了空的頻道名時，只改使用者設定救不了這支片，
    所以兩行指令都給：存進使用者設定（以後每支片）、補進這支片的設定快照。"""
    return ProjectError(
        "片尾卡要放你的名字，但還沒設定頻道名",
        title="還不能出片",
        hint="跟 Claude 說「頻道名叫○○」就會設定好。指令：以後每支片都用 "
             + cli_command("settings set", "--channel", '"<頻道名>"', data_dir=data_dir)
             + "；這支片也補上 " + cli_command("settings set", "--project", Path(project_root), "--channel", '"<頻道名>"'),
    )


def plan_end_card(project: Project, timeline: Timeline, fmt: VideoFormat, plans: Sequence[ClipPlan]) -> ClipPlan:
    """片尾卡的片段計畫。來源從前面所有片段計畫的 sources 彙整。"""
    from article_to_video.timeline import ClipPlan

    s = project.settings
    if not s.channel.strip():
        raise missing_channel_error(project.root)
    data: dict[str, Any] = {
        "channel": s.channel,
        "sourcesLabel": "資料來源",
        "sources": merge_sources(src for p in plans for src in p.sources),
        "notes": list(NOTES),
    }
    if s.brand_color:
        data["brand"] = s.brand_color
    images = []
    if s.logo:
        logo = (project.root / s.logo).absolute()
        if not logo.is_file():
            raise ProjectError(f"設定快照裡的 logo「{s.logo}」不在專案資料夾裡（{logo}）",
                               hint="重設 logo：" + cli_command("settings set", "--project", project.root,
                                                                "--logo", '"<圖檔>"'))
        data["image"] = str(logo)
        images.append(logo)
    o = timeline.outro
    spec = {
        "grammar": END_CARD_GRAMMAR,
        "duration": o.frames / fmt.fps,
        "fps": fmt.fps,
        "width": fmt.width,
        "height": fmt.height,
        "data": data,
        "cues": [],
    }
    if fmt.card_safe():  # 直式：名字、來源、聲明都排在平台介面區以內（片尾卡沒有字幕，不用讓字幕帶）
        spec["safe"] = fmt.card_safe()
    return ClipPlan(id=END_CARD_ID, shot_ids=[], start_frame=o.start_frame, end_frame=o.start_frame + o.frames,
                    spec=spec, images=images)

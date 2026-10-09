"""片尾卡：每支片最後一段旁白唸完之後接一張卡，長 settings.tail 秒（算進成片長度）。

卡上有：頻道名（＋logo）、本片資料來源（彙整）、「僅供教育，非投資建議」、「配音為 AI 合成」。
用內嵌引擎的自訂語法 engine/clips/a2v_end_card.js 渲（跟圖表同一組字型、同一套缺字檢查），跟分鏡一樣進片段快取。

資料來源怎麼來：每個片段計畫（timeline.ClipPlan）的 `sources`。財經圖表片段放圖上的來源列（data.source，
已經過 prepare_chart_data，所以公有數據那張票自動補的來源也會進來）；Vox 片段（Vox 那張票）把它用到的出處放進
自己的 ClipPlan.sources 就會出現在卡上。彙整規則見 merge_sources。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from article_to_video.project import Project, ProjectError

if TYPE_CHECKING:
    from article_to_video.timeline import ClipPlan, Timeline, VideoFormat

__all__ = ["END_CARD_GRAMMAR", "END_CARD_ID", "NOTES", "merge_sources", "plan_end_card"]

END_CARD_ID = "end.card"
"""片尾卡的片段 id（檔名 specs/end.card.json、clips/end.card.mp4）。有句點，分鏡 id 不會跟它撞名。"""
END_CARD_GRAMMAR = "a2v_end_card"
NOTES = ("僅供教育，非投資建議", "配音為 AI 合成")
_PREFIX_RE = re.compile(r"^\s*(?:資料來源|数据来源|資料出處|來源|来源|出處|出处|sources?)\s*[:：]\s*", re.IGNORECASE)
_GROUP_RE = re.compile(r"^(?P<name>[^（(]+?)\s*[（(](?P<items>[^（()）]+)[）)](?P<rest>.*)$")


def merge_sources(texts: Iterable[str]) -> list[str]:
    """圖上的來源列 → 片尾卡的來源清單。

    - 拿掉「來源：」「資料來源：」這類前綴（卡上統一有「資料來源」小標）。一列就是一個出處
      （公有數據自動寫的來源行用「；」分欄：「FRED，聯準會（DGS10）；月平均；2026-10-09 擷取」，不拆）。
    - 一模一樣的只留一個，順序照第一次出現。
    - 同一個出處、括號裡列代號的合併：「FRED（DGS2）」＋「FRED（DGS10）」→「FRED（DGS2、DGS10）」（括號後面的說明要一樣）。
    """
    entries: list[str] = []
    for raw in texts:
        if not isinstance(raw, str):
            continue
        t = " ".join(_PREFIX_RE.sub("", raw).split()).strip("，,。； ")
        if t and t not in entries:
            entries.append(t)
    merged: list[Any] = []
    groups: dict[tuple[str, str], list[str]] = {}
    for t in entries:
        m = _GROUP_RE.match(t)
        if not m:
            merged.append(t)
            continue
        key = (m["name"].strip(), m["rest"])
        items = [x.strip() for x in re.split(r"[、,，]", m["items"]) if x.strip()]
        if key in groups:
            groups[key].extend(x for x in items if x not in groups[key])
        else:
            groups[key] = list(dict.fromkeys(items))
            merged.append(key)
    return [f"{k[0]}（{'、'.join(groups[k])}）{k[1]}" if isinstance(k, tuple) else k for k in merged]


def plan_end_card(project: Project, timeline: Timeline, fmt: VideoFormat, plans: Sequence[ClipPlan]) -> ClipPlan:
    """片尾卡的片段計畫。來源從前面所有片段計畫的 sources 彙整。"""
    from article_to_video.timeline import ClipPlan

    s = project.settings
    if not s.channel.strip():
        raise ProjectError(
            "片尾卡要放你的名字，但還沒設定頻道名",
            title="還不能出片",
            hint="跟 Claude 說「頻道名叫○○」就會設定好"
                 "（指令：python -m article_to_video settings set --channel 你的頻道名）。",
        )
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
                               hint="重設 logo：python -m article_to_video settings set --project <專案> --logo <圖檔>")
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
    return ClipPlan(id=END_CARD_ID, shot_ids=[], start_frame=o.start_frame, end_frame=o.start_frame + o.frames,
                    spec=spec, images=images)

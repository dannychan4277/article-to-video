"""審片頁「複製給 Claude」的文字格式，以及把它解析回結構化留言。

格式（審片頁的 review.js 照這個產生；主 skill 收到作者貼回來的文字就用 parse_feedback() 或
`python -m article_to_video feedback <專案>`（從 stdin 讀）解析）：

    審片留言｜<專案資料夾名>｜第 <版本> 版
    分鏡 <編號>（<分鏡 id>）：<留言第一行>
      <留言第二行起，前面兩個半形空白>
    分鏡 <編號>（<分鏡 id>）：<留言>
    整體意見：<整體意見第一行>
      <第二行起…>

- 每則留言一行「分鏡編號：留言」，照分鏡順序；沒寫留言的分鏡不出現。整體意見放最後，沒寫就沒有這行。
- 分鏡編號＝審片頁卡片上的編號（shots.json 裡的位置，1 起算）；括號裡的 id 是 shots.json 的 id，
  用來發現「審片頁是舊的、編號已經對不上」的情況（ReviewFeedback.check）。
- 一則留言有好幾行時，第二行起縮排兩個空白；所以留言內容就算有一行寫「分鏡 2：…」也不會被當成新的一則。
- 留言裡的空白行會拿掉。
- 解析時寬鬆：半形冒號、編號前後有沒有空白、沒有 id、沒有標頭行都可以（作者可能手打）。
  不認得的行：在第一則留言之前的忽略；之後的當成上一則的續行。

留言對到要改的地方（project_outline，#13）：每個分鏡是財經圖表還是 Vox、是不是接續分鏡、畫面上主要的字，
以及它唸到哪幾個旁白段——只看 shots.json 的切入點與 narration.json 的段落順序，不用配音：
分鏡從某段開頭切入就從那段算起，到下一個分鏡切入為止；下一個分鏡從段中間（有寫 word）切入時，那段兩個分鏡都有份。
作者說「旁白改成…」就改這些旁白段；`outline <專案>` 把整份列出來（打開舊專案、照分鏡編號改的時候用）。

為什麼這裡自己讀 shots.json、不用 project.load_project：對留言的時候，分鏡設定常常正改到一半（作者一次留好幾則，
Claude 改完第一則就要對下一則），load_project 會因為任何一個欄位不對就整份拒絕（那是建置要的嚴格）。這裡只拿
編號、id、切入點和畫面上的字，看不懂的欄位就當沒有，讓留言照樣對得到分鏡；設定錯在哪由 preview／build 報。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from article_to_video.project import ProjectError, ProjectPaths
from article_to_video.textio import read_json_or_none

__all__ = ["HEADER_PREFIX", "ReviewFeedback", "ShotComment", "ShotOutline", "format_outline", "parse_feedback",
           "project_outline", "project_shot_ids"]

HEADER_PREFIX = "審片留言"

_HEADER_RE = re.compile(r"^審片留言\s*[｜|]\s*(.*?)\s*[｜|]\s*第\s*(\d+)\s*版\s*$")
_SHOT_RE = re.compile(r"^分鏡\s*(\d+)\s*(?:[（(]\s*([A-Za-z0-9_-]+)\s*[）)])?\s*[：:]\s*(.*)$")
_OVERALL_RE = re.compile(r"^整體意見\s*[：:]\s*(.*)$")


@dataclass(frozen=True)
class ShotComment:
    number: int
    """審片頁卡片上的分鏡編號（1 起算）。"""
    shot_id: str | None
    """複製時那張卡的分鏡 id（手打的留言可能沒有）。"""
    text: str


@dataclass(frozen=True)
class ReviewFeedback:
    project: str | None = None
    version: int | None = None
    shots: list[ShotComment] = field(default_factory=list)
    overall: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.shots and not self.overall

    def resolve(self, shot_ids: Sequence[str]) -> list[str | None]:
        """每則分鏡留言對到的分鏡 id：有寫 id 就用它，沒寫就照編號找；找不到是 None。"""
        out: list[str | None] = []
        for c in self.shots:
            if c.shot_id is not None:
                out.append(c.shot_id if c.shot_id in shot_ids else None)
            else:
                out.append(shot_ids[c.number - 1] if 1 <= c.number <= len(shot_ids) else None)
        return out

    def check(self, shot_ids: Sequence[str]) -> list[str]:
        """跟現在的分鏡設定比對；編號或 id 對不上（審片頁是舊的）就列出來（繁中）。"""
        problems: list[str] = []
        for c in self.shots:
            if not 1 <= c.number <= len(shot_ids):
                problems.append(
                    f"分鏡 {c.number} 的留言對不到：現在只有 {len(shot_ids)} 個分鏡。審片頁可能是舊的，先跟作者確認是哪一張"
                )
            elif c.shot_id is not None and shot_ids[c.number - 1] != c.shot_id:
                where = f"現在是第 {list(shot_ids).index(c.shot_id) + 1} 個分鏡" if c.shot_id in shot_ids else "已經不在分鏡設定裡"
                problems.append(
                    f"分鏡 {c.number} 的留言是寫給 {c.shot_id} 的，但現在的分鏡 {c.number} 是 {shot_ids[c.number - 1]}"
                    f"（{c.shot_id} {where}）。審片頁可能是舊的，先跟作者確認是哪一張"
                )
        return problems


def project_shot_ids(project_dir: str | os.PathLike) -> list[str]:
    """專案現在的分鏡 id（照順序＝審片頁的分鏡編號）。只讀 shots.json 的 id，不做完整驗證：
    作者貼留言時分鏡設定可能正改到一半，對編號用不著整份都合法。"""
    path = ProjectPaths(Path(project_dir).absolute()).shots
    if not path.is_file():
        raise ProjectError(f"找不到分鏡設定 {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProjectError(f"{path.name} 不是合法的 JSON（{e.msg}）") from None
    shots = raw.get("shots") if isinstance(raw, dict) else None
    if not isinstance(shots, list):
        raise ProjectError(f'{path.name} 要長這樣：{{"shots": [ … ]}}')
    return [s.get("id") if isinstance(s, dict) and isinstance(s.get("id"), str) else f"#{i}"
            for i, s in enumerate(shots, 1)]


@dataclass(frozen=True)
class ShotOutline:
    number: int
    id: str
    kind: str
    """財經圖表／Vox，接續分鏡加「（接續）」。"""
    start: str
    """從哪裡切入（給人看的一句）。"""
    segments: list[str]
    """這個分鏡唸到的旁白段（照播放順序）。"""
    shows: str
    """畫面上主要的東西：圖表標題或標註；Vox 的圖N、剪報、框、卡片。"""


def _shows(shot: dict) -> str:
    chart, vox = shot.get("chart"), shot.get("vox")
    if isinstance(chart, dict):
        data = chart.get("data") if isinstance(chart.get("data"), dict) else {}
        parts = []
        if data.get("title"):
            parts.append(f"標題「{data['title']}」")
        if isinstance(data.get("publicData"), dict):
            parts.append(f"公有數據 {data['publicData'].get('series', '')}")
        hl = data.get("highlight")
        if isinstance(hl, dict) and hl.get("text"):
            parts.append(f"標註「{hl['text']}」")
        return "，".join(parts)
    if isinstance(vox, dict) and isinstance(vox.get("cues"), list):
        parts = []
        for c in vox["cues"]:
            if not isinstance(c, dict):
                continue
            k = c.get("kind")
            if k == "image":
                parts.append(str(c.get("image", "原圖")))
            elif k == "excerpt":
                parts.append(f"剪報「{c.get('quote', '')}」" if c.get("quote") else f"剪報（第 {c.get('paragraph', '?')} 段）")
            elif k == "box":
                parts.append(f"框「{c['find']}」" if c.get("find") else "框")
            elif k == "title":
                parts.append(f"標題卡「{c.get('text', '')}」")
            elif k == "point":
                parts.append(f"索引卡「{c.get('text', '')}」")
        return "、".join(parts)
    return ""


def project_outline(project_dir: str | os.PathLike) -> tuple[list[ShotOutline], dict[str, str]]:
    """每個分鏡的大綱與它唸到的旁白段，加上全部旁白段的文字。只讀 JSON、寬鬆（設定改到一半也能用）。"""
    paths = ProjectPaths(Path(project_dir).absolute())
    ids = project_shot_ids(project_dir)
    raw = read_json_or_none(paths.shots)
    shots = [s if isinstance(s, dict) else {} for s in raw["shots"]]
    nraw = read_json_or_none(paths.narration)
    segs = nraw.get("segments") if isinstance(nraw, dict) else None
    narration = {k: v for k, v in segs.items() if isinstance(v, str)} if isinstance(segs, dict) else {}
    order = list(narration)

    def start_of(i: int) -> tuple[int | None, bool]:
        """(旁白段的位置, 是不是從段中間切入)。第一個分鏡沒寫切入點＝從第一段開頭。"""
        st = shots[i].get("start")
        if not isinstance(st, dict):
            return (0 if i == 0 and order else None), False
        seg = st.get("segment")
        if seg not in narration:
            return None, False
        return order.index(seg), bool(st.get("word"))

    starts = [start_of(i) for i in range(len(shots))]
    out: list[ShotOutline] = []
    for i, shot in enumerate(shots):
        first, mid = starts[i]
        covered: list[str] = []
        if first is not None:
            last = len(order) - 1
            nxt = next((starts[j] for j in range(i + 1, len(shots)) if starts[j][0] is not None), None)
            if nxt is not None:
                last = nxt[0] if nxt[1] else nxt[0] - 1
            covered = order[first:max(first, last) + 1]
        st = shot.get("start") if isinstance(shot.get("start"), dict) else {}
        if first is None:
            where = "切入點還沒寫好"
        elif st.get("word"):
            where = f"從旁白段 {order[first]} 的「{st['word']}」切入"
        else:
            where = f"從旁白段 {order[first]} 開頭切入"
        kind = "Vox" if isinstance(shot.get("vox"), dict) else "財經圖表"
        if shot.get("continues"):
            kind += "（接續）"
        out.append(ShotOutline(i + 1, ids[i], kind, where, covered, _shows(shot)))
    return out, narration


def format_outline(outline: Sequence[ShotOutline], narration: dict[str, str]) -> str:
    lines = [f"共 {len(outline)} 個分鏡（編號＝審片頁卡片上的編號），旁白 {len(narration)} 段"]
    for o in outline:
        segs = "、".join(o.segments) or "（對不到旁白段）"
        lines.append(f"分鏡 {o.number}（{o.id}）{o.kind}｜{o.start}｜旁白段 {segs}" + (f"｜{o.shows}" if o.shows else ""))
        lines += [f"    {k}：{narration[k]}" for k in o.segments]
    return "\n".join(lines)


def parse_feedback(text: str) -> ReviewFeedback:
    project: str | None = None
    version: int | None = None
    items: list[list] = []  # [kind, number, id, lines]
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        indented = line[:1].isspace()
        stripped = line.strip()
        if not indented:
            m = _HEADER_RE.match(stripped)
            if m and not items:
                project, version = m.group(1) or None, int(m.group(2))
                continue
            m = _SHOT_RE.match(stripped)
            if m:
                items.append(["shot", int(m.group(1)), m.group(2), [m.group(3).strip()]])
                continue
            m = _OVERALL_RE.match(stripped)
            if m:
                items.append(["overall", None, None, [m.group(1).strip()]])
                continue
        if items:
            items[-1][3].append(stripped)

    def joined(lines: list[str]) -> str:
        return "\n".join(s for s in lines if s)

    shots = [ShotComment(n, sid, joined(lines)) for kind, n, sid, lines in items if kind == "shot" and joined(lines)]
    overall = "\n\n".join(joined(lines) for kind, _, _, lines in items if kind == "overall" and joined(lines))
    return ReviewFeedback(project=project, version=version, shots=shots, overall=overall)

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
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from article_to_video.project import ProjectError, ProjectPaths

__all__ = ["HEADER_PREFIX", "ReviewFeedback", "ShotComment", "parse_feedback", "project_shot_ids"]

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

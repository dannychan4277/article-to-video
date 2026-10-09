"""旁白規則檢查：narration.json 的每個旁白段，照「簡化技術中文」裡數得出來的規則查一遍。

移植自 explainer-video 的 pipeline/lint_script.py（規則、門檻、詞表照抄），訊息改成繁中口語、點名旁白段：

  要改（有一項就結束碼 1）
    整段字數        每段 25–60 字（約 6–15 秒）
    一口氣          用「，。？！；」切開的每一塊 ≤ 12 字（字幕一行、AI 配音一口氣唸得完）
    一句            用「。？！」切開的每一句 ≤ 25 字
    阿拉伯數字      旁白不寫 0-9、％（寫成唸法：四點五八；畫面上才放阿拉伯數字）
    套話            賦能、打造、助力、全方位、深度、閉環、抓手、值得注意的是、總的來說、綜上所述、不僅
    開場            第一段開頭（或前 12 字內）不能是：你有沒有想過、今天要來聊、今天我們、大家好、哈囉
  看一下（只提醒）
    模糊量詞        一些、部分、相關、大幅、適當、一定程度
    虛動詞          進行、做出、予以、實現
    雙重否定        不是不、不能不、不會不、沒有不、非不、不得不
    指代            它、其、此、該（「其他、其實、尤其、因此、應該…」這些詞裡的不算）
    英文縮寫        全大寫或兩個字母以內的英文，AI 配音可能逐字母唸；同一個全稿只提醒一次
    全長            用每秒 4 字估，不在 2–4 分鐘（480–960 字）

字數（n_chars）：中文一字一個；全大寫縮寫逐字母唸（FRED＝4）；其他英文字約三個字母一個音；標點不算。
看語意的規則（用作者的詞、不加原文沒有的主張、一句一個畫面…）這裡查不到，見主 skill 的 narration-rules.md。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from article_to_video.project import NARRATION_FILE, ProjectError

__all__ = ["LintIssue", "LintReport", "SegmentLint", "format_lint", "lint_project", "lint_segments", "n_chars"]

PUNCT = "，。？！；：、「」『』（）()《》〈〉… ,.?!;:\"'"
ZH_PER_SEC = 4.0
SEGMENT_MIN, SEGMENT_MAX = 25, 60
BREATH_MAX = 12
SENTENCE_MAX = 25
TOTAL_MIN_SEC, TOTAL_MAX_SEC = 120, 240

CLICHES = ["賦能", "打造", "助力", "全方位", "深度", "閉環", "抓手", "值得注意的是", "總的來說", "綜上所述", "不僅"]
VAGUE = ["一些", "部分", "相關", "大幅", "適當", "一定程度"]
EMPTY_VERBS = ["進行", "做出", "予以", "實現"]
DOUBLE_NEG = ["不是不", "不能不", "不會不", "沒有不", "非不", "不得不"]
OPENERS = ["你有沒有想過", "今天要來聊", "今天我們", "大家好", "哈囉"]
POINTER_OK = ["其他", "其實", "尤其", "極其", "其中", "因此", "如此", "彼此", "此外", "應該", "該不該"]

MUST = "要改"
LOOK = "看一下"


def n_chars(s: str) -> int:
    """唸起來的長度：中文一字一單位；全大寫縮寫逐字母（MCP＝3）；其他英文字約 3 個字母一個音節；標點不算。"""
    n = 0
    for w in re.findall(r"[A-Za-z]+", s):
        n += len(w) if w.isupper() else -(-len(w) // 3)
    rest = re.sub(r"[A-Za-z]+", "", s)
    return n + len([c for c in rest if c not in PUNCT])


@dataclass(frozen=True)
class LintIssue:
    level: str
    """要改／看一下。"""
    message: str


@dataclass(frozen=True)
class SegmentLint:
    key: str
    chars: int
    issues: list[LintIssue] = field(default_factory=list)

    @property
    def mark(self) -> str:
        if any(i.level == MUST for i in self.issues):
            return "✗"
        return "△" if self.issues else "✓"


@dataclass(frozen=True)
class LintReport:
    segments: list[SegmentLint]
    notes: list[LintIssue] = field(default_factory=list)
    """整份稿的提醒（全長）。"""

    @property
    def total_chars(self) -> int:
        return sum(s.chars for s in self.segments)

    @property
    def must_fix(self) -> int:
        return sum(i.level == MUST for s in self.segments for i in s.issues)

    @property
    def look(self) -> int:
        return sum(i.level == LOOK for s in self.segments for i in s.issues) + len(self.notes)

    @property
    def ok(self) -> bool:
        return self.must_fix == 0


def lint_segments(segments: Mapping[str, str]) -> LintReport:
    out: list[SegmentLint] = []
    seen_acronyms: set[str] = set()
    for idx, (key, text) in enumerate(segments.items()):
        issues: list[LintIssue] = []
        n = n_chars(text)

        def must(msg: str) -> None:
            issues.append(LintIssue(MUST, msg))

        def look(msg: str) -> None:
            issues.append(LintIssue(LOOK, msg))

        if not SEGMENT_MIN <= n <= SEGMENT_MAX:
            must(f"整段 {n} 字，要在 {SEGMENT_MIN}–{SEGMENT_MAX} 字之間"
                 + ("（太短：跟前後段併起來，或多講一句）" if n < SEGMENT_MIN else "（太長：拆成兩段）"))
        for chunk in re.split(r"[，。？！；]", text):
            if n_chars(chunk) > BREATH_MAX:
                must(f"一口氣 {n_chars(chunk)} 字，超過 {BREATH_MAX} 字：「{chunk.strip()}」（加逗號斷短一點）")
        for sent in re.split(r"[。？！]", text):
            if n_chars(sent) > SENTENCE_MAX:
                must(f"一句 {n_chars(sent)} 字，超過 {SENTENCE_MAX} 字：「{sent.strip()}」（拆成兩句）")
        if re.search(r"[0-9０-９%％]", text):
            found = "".join(sorted(set(re.findall(r"[0-9０-９%％,.]+", text))))
            must(f"旁白有阿拉伯數字或 %：{found}（寫成唸法，例如「四點五八」「百分之五」；阿拉伯數字放在畫面上）")
        for w in CLICHES:
            if w in text:
                must(f"套話「{w}」：刪掉，或換成具體的說法")
        if idx == 0:
            for w in OPENERS:
                if text.startswith(w) or w in text[:12]:
                    must(f"開場用了「{w}」：第一句直接講主題（第一秒就是重點）")
        for w in VAGUE:
            if w in text:
                look(f"模糊量詞「{w}」：能換成數字或具體範圍就換")
        for w in EMPTY_VERBS:
            if w in text:
                look(f"「{w}」：動詞直接講（「進行調整」→「調整」）")
        for w in DOUBLE_NEG:
            if w in text:
                look(f"可能是雙重否定「{w}」：改成正面說法比較好懂")
        masked = text
        for ok in POINTER_OK:
            masked = masked.replace(ok, "＿" * len(ok))
        for p in re.findall(r"[它其此該]", masked):
            look(f"指代「{p}」：聽的人不確定指什麼的話，直接重複那個名詞")
        words = re.findall(r"[A-Za-z]+", text)
        for ab in sorted({w for w in words if (w.isupper() and len(w) > 1) or len(w) <= 2}):
            if ab.lower() not in seen_acronyms:
                seen_acronyms.add(ab.lower())
                look(f"英文「{ab}」AI 配音可能一個字母一個字母唸：預覽後聽一次，不順就改成中文")
        out.append(SegmentLint(key, n, issues))

    notes: list[LintIssue] = []
    total = sum(s.chars for s in out)
    secs = total / ZH_PER_SEC
    if not TOTAL_MIN_SEC <= secs <= TOTAL_MAX_SEC:
        lo, hi = int(TOTAL_MIN_SEC * ZH_PER_SEC), int(TOTAL_MAX_SEC * ZH_PER_SEC)
        notes.append(LintIssue(LOOK, f"全長約 {secs:.0f} 秒，不在 2–4 分鐘（約 {lo}–{hi} 字）：太短就多講文章的重點，"
                                     "太長就只留主線"))
    return LintReport(out, notes)


def format_lint(report: LintReport, *, source: str = NARRATION_FILE) -> str:
    lines = [f"旁白規則檢查：{source} 共 {len(report.segments)} 段"]
    for s in report.segments:
        lines.append(f"{s.mark} 旁白段 {s.key}（{s.chars} 字，約 {s.chars / ZH_PER_SEC:.0f} 秒）")
        lines += [f"    {i.level}｜{i.message}" for i in s.issues]
    lines += [f"{n.level}｜{n.message}" for n in report.notes]
    total = report.total_chars
    lines.append(f"合計 {total} 字，估計約 {total / ZH_PER_SEC:.0f} 秒（目標 2–4 分鐘）；"
                 f"要改 {report.must_fix} 項、看一下 {report.look} 項")
    lines.append("看語意的規則這裡查不到（用作者的詞、不加原文沒有的主張、一句一個畫面…），要逐句自己對照文章檢查。")
    return "\n".join(lines)


def lint_project(project_dir: str | os.PathLike) -> LintReport:
    """讀 <專案>/narration.json（只看旁白稿，不需要分鏡設定）。讀不到、格式不對就丟 ProjectError。"""
    path = Path(project_dir) / NARRATION_FILE
    if not path.is_file():
        raise ProjectError(f"找不到旁白稿 {path}", title="沒辦法檢查旁白",
                           hint='先寫 narration.json：{"segments": {"s01": "第一段旁白", "s02": "…"}}')
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProjectError(f"{path.name} 第 {e.lineno} 行第 {e.colno} 個字附近不是合法的 JSON（{e.msg}）",
                           title="沒辦法檢查旁白") from None
    segs = raw.get("segments") if isinstance(raw, dict) else None
    if not isinstance(segs, dict) or not segs or not all(isinstance(v, str) for v in segs.values()):
        raise ProjectError('narration.json 要長這樣：{"segments": {"s01": "第一段旁白", "s02": "…"}}',
                           title="沒辦法檢查旁白")
    return lint_segments(segs)

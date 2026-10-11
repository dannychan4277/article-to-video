"""標註對時：畫面上帶數字的字什麼時候看得清楚，跟旁白什麼時候唸到那個數字對一對。只看時間軸（錨點換算好的 cue 時刻＋配音層的
逐詞時間），不渲染，幾毫秒。

  timing_issues(project, timeline) → list[TimingIssue]   每一項：哪個分鏡的哪個 cue、旁白幾秒唸到哪個詞、畫面幾秒才看得清楚、建議的錨點
  timing_notes(project, timeline)  → list[str]           preview／build 印的「提醒：」（timeline.build_timeline 收進 Timeline.notes）
  自動檢查（auto_checks）把同一份結果記成該修（不擋出片），審片頁照樣顯示。

查的事（計時實測：第 1 輪獨立審片 5 項必修全是標註比旁白晚——做片的人照文件把標註錨在數字唸完、句尾，
旁白已經在講下一件事，畫面還是上一個標註，晚 1～2 秒）：
- 標註比旁白晚（late）：標註（highlight）、大數字（number）、差距（gap）、兩數對比的強調上的數字，旁白在它看得清楚之前
  LATE_TOLERANCE 秒以上就唸到了。只比「這個分鏡期間」唸的（從分鏡切入到切走），只比新出現的數字（上一個標註已經寫著的不算）。
- 數字比旁白早（early）：會滾的大數字落定比旁白唸到它早 NUMBER_EARLY 秒以上（審片：數字早就數完、卡片空等）。
- 標註停太短（short）：標註從出現到被換掉不到 LABEL_HOLD 秒（審片：閃一下、來不及讀）。
- 只改第二行（flash）：同一點、同一句字只加或改 sub，引擎把整個標註淡出再長出來（審片 R2-3：閃一下）。

數字怎麼比：畫面上的字用 numbers.find_numbers，旁白用 numbers.spoken_numbers（多認「七百八十二天」「九月」）；
絕對值一樣（「負零點六二」對「0.62」也算），或畫面上的數字（兩位有效數字以上）是旁白的數字四捨五入來的。
沒有數字的標註（「長天期比較低」）程式分不出它在講哪一句，只能靠規則文件（storyboard-rules.md「標註跟著旁白」）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from article_to_video.numbers import FoundNumber, _fmt, compare_gap, find_numbers, spoken_numbers
from article_to_video.project import Project
from article_to_video.timeline import CueTiming, ShotTiming, Timeline, effective_chart, resolve_highlight

__all__ = ["LABEL_HOLD", "LABEL_READ", "LATE_TOLERANCE", "NUMBER_EARLY", "TimingIssue", "timing_issues", "timing_notes"]

LABEL_READ = 0.5
"""標註從 at 起多久看得清楚（秒）：圓點 0.45 秒彈出、引線 0.12～0.45 秒、字 0.3 秒開始淡入（0.5 秒時已 97%，0.7 秒全滿）；
舊標註 0.25 秒淡出、柱 0.4 秒淡掉。兩數對比的強調 0.4 秒換完色。"""
LATE_TOLERANCE = 0.5
"""旁白唸到數字之後，畫面最晚幾秒內要看得清楚（秒）。一個數字唸起來要 0.6～1 秒（「四點五八」「二零二三年」）：錨在數字那個詞的
開頭（edge: start，提早 0.15 秒）時，標註在唸到之後 0.35 秒長好、數字還在唸，看起來是同步的——第 2 輪獨立審片沒有挑這種
（三處都錨在數字那個詞、或帶出它的詞的開頭）。第 1 輪判必修、該修的都晚 1 秒以上（錨在唸完、句尾）。"""
NUMBER_EARLY = 1.0
"""大數字落定最多比旁白唸到它早幾秒（秒）。獨立審片的標準（計時實測 R1-13）：錨在鋪陳的開頭，數字在唸到之前
約 2 秒就數完、卡片空等（可改）；錨在引出數字的詞（緊接在數字前面那幾個字）才剛好。只看會滾的大數字（大數字卡、圖上右上角的）：
兩數對比的數字、差距是淡入，先放上來等旁白比較是常見的排法。"""
LABEL_HOLD = 1.5
"""標註從出現到被換掉（下一個標註、切到別張圖）至少幾秒（秒）：前 LABEL_READ 秒在長，看得清楚的時間才有 1 秒。
計時實測：0.3 秒（R1-6、R2-2）判該修、約 0.9 秒（R2-8）判可改；一句換一個標註、隔 1.7 秒的沒有挑。
最後一個標註留到旁白唸完、接片尾卡的不算（旁白已經講完，後面沒有要讀的新東西）。"""


@dataclass(frozen=True)
class TimingIssue:
    kind: str
    """"late"（比旁白晚）、"early"（大數字比旁白早）、"short"（標註停太短）、"flash"（同一點只改第二行，閃一下）。"""
    shot_id: str
    where: str
    """cue 在分鏡設定裡的位置，例「分鏡 SH13 的 chart.cues[2]」。"""
    what: str
    """畫面上的那個東西，例「標註「高 0.62 個百分點」」。"""
    start: float
    end: float
    """出問題的時段（成片秒數）：late＝旁白唸到數字 → 畫面看得清楚；early＝數字落定 → 旁白唸到；short＝標註出現 → 被換掉；
    flash＝換標註的那半秒。"""
    message: str
    """提醒全文（不含「提醒：」）。"""

    @property
    def cue_index(self) -> int:
        m = re.search(r"cues\[(\d+)\]", self.where)
        return int(m.group(1)) if m else -1


@dataclass(frozen=True)
class _Spoken:
    t: float
    num: FoundNumber
    segment: str
    word: str
    """旁白稿裡唸這個數字的那幾個字（連單位、配音層同一個詞的字，例「三成」「九月」「四點五八」）。"""
    end: int
    """word 在旁白段裡的結尾（字元位置，不含）。"""


_UNIT_AFTER = re.compile(r"個百分點|百分點|基點|成|月|年|天|倍|碼|%|％")


def _spoken(timeline: Timeline) -> list[_Spoken]:
    out = []
    for key, seg in timeline.segments.items():
        text, words = seg.audio.text, seg.audio.words
        for num in spoken_numbers(text):
            end = max([num.end] + [w.end_char for w in words if w.start_char < num.end and w.end_char > num.start])
            if end == num.end and (m := _UNIT_AFTER.match(text, end)):
                end = m.end()
            out.append(_Spoken(seg.start + seg.audio.char_start(num.start), num, key, text[num.start:end], end))
    return sorted(out, key=lambda s: s.t)


def _same(screen: FoundNumber, said: FoundNumber) -> bool:
    """畫面上的數字是不是旁白唸的這個：絕對值一樣，或畫面上的（兩位有效數字以上）是旁白的四捨五入。"""
    a, b = abs(screen.value), abs(said.value)
    return a == b or (screen.digits >= 2 and abs(a - b) * 2 <= screen.quantum)


_CLAUSE_END = set("，。、；：？！,.;:?!「」『』（）()…—")


def _anchor_hint(timeline: Timeline, sp: _Spoken) -> tuple[dict[str, Any], float]:
    """建議的錨點：這句（逗號、句號之間）開始講它的詞＝從句首到那個數字（「相差零點六二」「到了九月」）；數字就在句首時
    是數字那個詞本身（「二零二三年」）。錨點對的是第一個字開始唸的時刻，太長就只寫前 6 個字。回傳 (錨點, 那個詞開始唸的秒數)。"""
    text = timeline.segments[sp.segment].audio.text
    i = sp.num.start
    while i > 0 and text[i - 1] not in _CLAUSE_END:
        i -= 1
    while i < sp.num.start and text[i].isspace():
        i += 1
    word = text[i:sp.end].strip()
    prefix = text[i:sp.num.start].strip()
    word = word if len(word) <= 8 else prefix if 0 < len(prefix) <= 8 else word[:6]
    anchor: dict[str, Any] = {"segment": sp.segment, "word": word}
    if text.count(word) > 1:
        anchor["occurrence"] = text[:i].count(word) + 1
    seg = timeline.segments[sp.segment]
    return anchor, seg.start + seg.audio.char_start(i)


def _value_text(data: Mapping[str, Any], i: Any) -> str:
    """兩數對比第 i 個數字在畫面上的樣子（照 decimals、prefix、suffix）。"""
    series = data.get("series") if isinstance(data.get("series"), list) else []
    if not isinstance(i, int) or not 0 <= i < len(series) or not isinstance(series[i].get("value"), (int, float)):
        return ""
    return (data.get("prefix") or "") + _fmt(series[i]["value"], data.get("decimals", 1)) + (data.get("suffix") or "")


def _shown(c: CueTiming, data: Mapping[str, Any]) -> tuple[Any, str, str]:
    """一個 cue 上畫面的東西：(比對「跟上一個一樣」用的鍵, 要比數字的字, 給人看的名字)；沒有要比的回傳空字串。
    標註：字（text、sub）；兩數對比的強調：被強調的那個數字；大數字：那個數字（不含說明：說明先淡入，年份之類的常在前面就唸了）；
    差距：程式算的差。"""
    compare = data.get("chart") == "compare"
    if c.kind == "highlight":
        h = resolve_highlight(c.fields, data)
        if compare:
            series = data.get("series") if isinstance(data.get("series"), list) else []
            i = h["index"] if h["index"] is not None else len(series) - 1
            text = _value_text(data, i)
            return ("強調", i), text, f"強調「{text}」"
        text = "／".join(h[k] for k in ("text", "sub") if isinstance(h[k], str) and h[k])
        return (h["index"], h["text"], h["sub"]), text, f"標註「{text}」"
    q = c.fields.get("data") if isinstance(c.fields.get("data"), Mapping) else {}
    if c.kind == "number" and compare:
        idx = q.get("index")
        text = " ".join(t for i in (idx if isinstance(idx, list) else [idx]) if (t := _value_text(data, i)))
        return None, text, f"數字「{text}」"
    if c.kind == "number" and isinstance(q.get("value"), (int, float)):
        text = (q.get("prefix") or "") + _fmt(q["value"], q.get("decimals", 0)) + (q.get("suffix") or "")
        return None, text, f"大數字「{text}」"
    if c.kind == "gap" and compare and len(data.get("series") or []) >= 2:
        try:
            text = compare_gap(data["series"], data.get("decimals", 1))
        except (KeyError, TypeError, ValueError):
            return None, "", ""
        verb = c.fields.get("text") if isinstance(c.fields.get("text"), str) and c.fields.get("text") else "差"
        return None, text, f"差距「{verb} {(q.get('prefix') or '')}{text}{q.get('suffix') or ''}」"
    return None, "", ""


def _chains(timeline: Timeline) -> list[list[int]]:
    """財經圖表的一串（第一個分鏡＋接續它的）：timeline.shots 的位置。"""
    out: list[list[int]] = []
    for n, st in enumerate(timeline.shots):
        if st.shot.chart is None:
            continue
        if st.shot.continues and out and out[-1][-1] == n - 1:
            out[-1].append(n)
        else:
            out.append([n])
    return out


def timing_issues(project: Project, timeline: Timeline) -> list[TimingIssue]:
    fps = timeline.fps
    spoken = _spoken(timeline)
    issues: list[TimingIssue] = []
    for chain in _chains(timeline):
        last_key: Any = None
        last_nums: list[FoundNumber] = []
        last_text = ""
        labels: list[tuple[float, CueTiming, ShotTiming, str]] = []
        settled: dict[Any, float] = {}
        """兩數對比：第幾個數字在幾秒落定（強調只比它上畫面之後旁白又唸到它的時刻）。"""
        for n in chain:
            st: ShotTiming = timeline.shots[n]
            data, _ = effective_chart(project.shots, n)
            t0, t1 = st.start_frame / fps, st.end_frame / fps
            compare = data.get("chart") == "compare"
            for c in sorted((c for c in st.cues if not c.carried), key=lambda c: c.frame):
                key, text, what = _shown(c, data)
                if not what:
                    continue
                nums = find_numbers(text)
                new = nums
                at = c.frame / fps
                since = t0
                if c.kind == "number" and compare:
                    idx = (c.fields.get("data") or {}).get("index")
                    settled.update({i: at for i in (idx if isinstance(idx, list) else [idx])})
                if c.kind == "highlight":
                    if compare:
                        # 強調的是畫面上已經有的數字：唸它、讓它落定的那一次不算（落定前後 LATE_TOLERANCE 秒內），
                        # 之後旁白再講到它（「甲高零點四」）才是強調該出來的時候
                        since = max(t0, settled.get(key[1], t0 - LATE_TOLERANCE) + LATE_TOLERANCE)
                    if key == last_key:
                        continue  # 跟上一個一模一樣：引擎不重畫
                    new = [x for x in nums if not any(_same(x, y) for y in last_nums)]
                    if not compare and last_key is not None and key[:2] == last_key[:2]:
                        issues.append(_flash(c, st, what, at, key[1]))
                    stale = last_text
                    last_key, last_nums, last_text = key, nums, what
                    labels.append((at, c, st, what))
                    shown = at + LABEL_READ
                else:
                    stale = ""
                    shown = at  # 數字、差距的 at 是落定的時刻（動畫在 at 之前）
                said = [s for s in spoken if since <= s.t < t1 and any(_same(x, s.num) for x in new)]
                if said and said[0].t < shown - LATE_TOLERANCE:
                    issues.append(_late(c, st, what, shown, said[0], timeline, stale=stale))
                elif said and c.kind == "number" and not compare and said[0].t - shown > NUMBER_EARLY:
                    issues.append(_early(c, st, what, shown, said[0], timeline))
        issues += _short(labels, timeline.shots[chain[-1]], timeline)
    return issues


def _late(c: CueTiming, st: ShotTiming, what: str, shown: float, sp: _Spoken, timeline: Timeline, *,
          stale: str = "") -> TimingIssue:
    anchor = json.dumps(_anchor_hint(timeline, sp)[0], ensure_ascii=False)
    late = shown - sp.t
    if c.kind == "highlight":
        how = (f"{shown:.2f} 秒才看得清楚（換標註要 {LABEL_READ:g} 秒才長好）"
               + (f"，這段時間畫面上還是上一個{stale}，聽到的跟看到的對不上" if stale else ""))
        fix = f"把錨點移到這句開始講它的詞：{anchor}（不要錨在數字唸完或句尾）"
    else:
        how = f"{shown:.2f} 秒才落定（觀眾聽到的時候畫面上還在滾、還沒出來）"
        fix = f"at 寫在唸到這個數字的那個詞的開頭（edge 用 start），或這句開始講它的詞：{anchor}"
    msg = f"{c.where}（{what}）比旁白晚 {late:.2f} 秒：旁白 {sp.t:.2f} 秒就唸到「{sp.word}」，畫面 {how}。{fix}"
    return TimingIssue("late", st.shot.id, c.where, what, sp.t, shown, msg)


def _early(c: CueTiming, st: ShotTiming, what: str, shown: float, sp: _Spoken, timeline: Timeline) -> TimingIssue:
    hint, t = _anchor_hint(timeline, sp)
    if t < sp.t - NUMBER_EARLY:  # 句首離數字太遠：錨在數字那個詞本身
        text = timeline.segments[sp.segment].audio.text
        hint = {"segment": sp.segment, "word": sp.word}
        if text.count(sp.word) > 1:
            hint["occurrence"] = text[:sp.num.start].count(sp.word) + 1
    anchor = json.dumps(hint, ensure_ascii=False)
    early = sp.t - shown
    msg = (f"{c.where}（{what}）比旁白早 {early:.2f} 秒落定：{shown:.2f} 秒就停在最後的數字，旁白 {sp.t:.2f} 秒才唸到「{sp.word}」，"
           f"畫面空等。落定不要早於唸到它 {NUMBER_EARLY:g} 秒：at 錨在引出這個數字的詞 {anchor}（或數字那個詞的開頭），"
           "不要提早到鋪陳的開頭")
    return TimingIssue("early", st.shot.id, c.where, what, shown, sp.t, msg)


def _flash(c: CueTiming, st: ShotTiming, what: str, at: float, first: str) -> TimingIssue:
    return TimingIssue("flash", st.shot.id, c.where, what, at, at + LABEL_READ,
        f"{c.where}（{what}）跟上一個標註同一點、第一行一樣是「{first}」，只改第二行（sub）：換標註時引擎把整個標註淡出再長出來，"
        "第一行的字消失一下又回來，看起來閃一下（審片判該修）。要補充就換一句新的字（例「2023 年」→「2023 年：4.58%」），"
        "或上一個標註一開始就兩行一起寫")


def _short(labels: list[tuple[float, CueTiming, ShotTiming, str]], last: ShotTiming, timeline: Timeline) -> list[TimingIssue]:
    """一串裡每個標註從出現到被換掉（下一個標註，或這一串結束、切到別張圖）不到 LABEL_HOLD 秒。"""
    fps = timeline.fps
    out = []
    for k, (t, c, st, what) in enumerate(labels):
        if k + 1 < len(labels):
            until, then = labels[k + 1][0], "換成下一個標註"
        elif last.end_frame < timeline.outro.start_frame:
            until, then = last.end_frame / fps, "切到下一張圖"
        else:
            continue  # 留到旁白唸完、接片尾卡
        hold = until - t
        if hold >= LABEL_HOLD - 1e-9:
            continue
        out.append(TimingIssue("short", st.shot.id, c.where, what, t, until,
            f"{c.where}（{what}）只停 {max(hold, 0):.2f} 秒就{then}：{t:.2f} 秒出現、{until:.2f} 秒就換掉，前 {LABEL_READ:g} 秒還在長，"
            f"觀眾來不及讀。標註從出現到換掉至少要 {LABEL_HOLD:g} 秒：把它提早到這句開始講它的詞，或讓下一個標註、下一個分鏡晚一點換"))
    return out


def timing_notes(project: Project, timeline: Timeline, skip: Sequence[str] = ()) -> list[str]:
    """preview／build 印的提醒（照時間排）。skip：已經有別的提醒的 cue（where），同一個 cue 只提醒一次。"""
    return [i.message for i in sorted(timing_issues(project, timeline), key=lambda i: i.start) if i.where not in skip]

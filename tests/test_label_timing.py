"""標註對時：畫面上帶數字的字（標註、大數字、差距、兩數對比的強調）要在旁白唸到那個數字時就看得清楚；標註至少停 1.5 秒才換。

計時實測第 1 輪獨立審片的 5 項必修都是「標註比旁白晚」：標註錨在數字唸完（edge: end）或句尾，旁白已經唸到新的數字，
畫面還是上一個標註。preview／build 印一行「提醒：」點名分鏡、cue、晚多少、建議的錨點（不擋），checks 記成該修。

假配音層（mini_project，每個字 0.2 秒、標點停 0.25 秒）：s01「兩年期殖利率，平均四點五八。」的「平」1.55 秒、「四」1.95 秒、
「八」唸完 2.75 秒；s02 從 3.7 秒起：「十」3.8 秒、「曲」5.85 秒、「倒」6.25 秒。
"""

from __future__ import annotations

from article_to_video.build import plan_build

from test_build_errors import make_project, sh


def reminders(root) -> list[str]:
    lines: list[str] = []
    plan_build(root, tts="fake", log=lines.append)
    return [line for line in lines if line.startswith("提醒：")]


def _label_with_the_number(shots, _):
    """SH01 的標註寫上「四點五八」這個數字，錨點照舊在數字唸完（edge: end）。"""
    sh(shots, "SH01")["chart"]["data"]["highlight"]["text"] = "平均 4.58%"


def test_a_label_whose_number_was_already_said_is_flagged_in_the_preview(tmp_path):
    """旁白 1.95 秒唸到「四點五八」，標註錨在唸完（2.77 秒）、再 0.5 秒才長好：晚 1.3 秒。提醒說哪個標註、旁白幾秒唸到哪個詞、
    標註幾秒才看得清楚，建議錨在這句開始講它的詞（「平均四點五八」的開頭）。"""
    notes = reminders(make_project(tmp_path, _label_with_the_number))
    assert len(notes) == 1, notes
    for needle in ["分鏡 SH01 的 chart.cues[2]", "「平均 4.58%」", "1.95 秒", "「四點五八」", "3.27 秒", "晚 1.3",
                   '"word": "平均四點五八"']:
        assert needle in notes[0], f"提醒少了「{needle}」：\n{notes[0]}"


def _anchor_label(at):
    def edit(shots, narration):
        _label_with_the_number(shots, narration)
        sh(shots, "SH01")["chart"]["cues"][2]["at"] = at
    return edit


def test_a_label_in_step_with_the_narration_is_not_flagged(tmp_path):
    """不誤報：錨在這句開始講它的詞（「平均」）＝唸到數字前就長好；錨在數字那個詞的開頭（edge: start）＝唸數字的時候長好
    （晚 0.35 秒，數字本身要唸 0.8 秒，看起來是同步的）；標註沒有數字（mini_project 原樣）沒得比。"""
    cases = {"這句開頭": _anchor_label({"segment": "s01", "word": "平均"}),
             "數字的開頭": _anchor_label({"segment": "s01", "word": "四點五八"}),
             "標註沒有數字": lambda shots, narration: None}
    for name, edit in cases.items():
        assert reminders(make_project(tmp_path / name, edit)) == [], name


def _next_label_right_after(shots, _):
    """SH02 改成唸到「十年期」就切進來、同一刻換標註（那根柱也搬過來）：SH01 的標註 2.77 秒出現、3.67 秒就換掉。"""
    sh1, sh2 = sh(shots, "SH01"), sh(shots, "SH02")
    bar = sh1["chart"]["cues"].pop(3)
    at = {"segment": "s02", "word": "十年期"}
    sh2["start"] = at
    sh2["chart"]["cues"] = [{**bar, "at": at}, {"at": at, "kind": "highlight"}]


def test_a_label_replaced_before_it_can_be_read_is_flagged(tmp_path):
    """計時實測 R1-6、R2-2：標註出現不到 1 秒就換下一個標註或切走，觀眾來不及讀。從出現到換掉要 1.5 秒以上
    （前 0.5 秒在長）；最後一個標註到旁白唸完接片尾卡不算（mini_project 原樣的 SH02 只停 1.1 秒，上面的測試不提醒）。"""
    notes = reminders(make_project(tmp_path, _next_label_right_after))
    assert len(notes) == 1, notes
    for needle in ["分鏡 SH01 的 chart.cues[2]", "標註「短天期較高」", "只停 0.90 秒", "1.5 秒"]:
        assert needle in notes[0], f"提醒少了「{needle}」：\n{notes[0]}"


def _card_settling_at(at):
    """SH01 改成大數字卡（4.58%，落定在 at），SH02 改成不接續的柱狀圖、從 s02 開頭切入。"""
    def edit(shots, _):
        sh(shots, "SH01")["chart"] = {
            "data": {"title": "短天期利率比較高", "unit": "美國兩年期公債殖利率", "source": "來源：FRED（DGS2），年平均自算",
                     "chart": "number"},
            "cues": [{"at": 0, "kind": "title"},
                     {"at": at, "kind": "number", "text": "2023 年平均", "data": {"value": 4.58, "decimals": 2, "suffix": "%"}}]}
        sh(shots, "SH02").update({"continues": False, "start": {"segment": "s02"}, "chart": {
            "data": {"title": "十年期比較低", "unit": "%", "source": "來源：FRED（DGS10），年平均自算", "decimals": 2,
                     "series": [{"label": "十年期", "value": 3.96}]},
            "cues": [{"at": 0, "kind": "title"}, {"at": {"segment": "s02", "word": "十年期"}, "kind": "bar"}]}})
    return edit


def test_a_big_number_settled_long_before_it_is_said_is_flagged(tmp_path):
    """計時實測 R1-13：大數字卡錨在鋪陳的開頭，數字唸到之前 2 秒就數完、卡片空等。落定不要早於唸到它 1 秒：
    錨在句首「兩年期」（0 秒落定、1.95 秒才唸到）提醒；錨在引出它的詞「平均」（早 0.55 秒）不提醒。"""
    notes = reminders(make_project(tmp_path / "句首", _card_settling_at({"segment": "s01", "word": "兩年期"})))
    assert len(notes) == 1, notes
    for needle in ["分鏡 SH01 的 chart.cues[1]", "大數字「4.58%」", "早 1.95 秒", "「四點五八」", '"word": "平均四點五八"']:
        assert needle in notes[0], f"提醒少了「{needle}」：\n{notes[0]}"
    assert reminders(make_project(tmp_path / "引出它的詞", _card_settling_at({"segment": "s01", "word": "平均"}))) == []


def _comparison_emphasised_at_the_end(shots, _):
    """SH02 改成兩數對比（4.58、3.96，s02 開頭就落定），唸完「倒掛」才強調十年期的 3.96。SH01 的標註提早到「平均」
    （不然 SH02 一切進來它就只停 0.9 秒）。"""
    sh(shots, "SH01")["chart"]["cues"][2]["at"] = {"segment": "s01", "word": "平均"}
    sh(shots, "SH02").update({"continues": False, "start": {"segment": "s02"}, "chart": {
        "data": {"title": "兩年期比十年期高", "unit": "美國公債殖利率，2023 年平均，%", "source": "來源：FRED（DGS2、DGS10），年平均自算",
                 "chart": "compare", "decimals": 2, "suffix": "%",
                 "series": [{"label": "兩年期", "value": 4.58}, {"label": "十年期", "value": 3.96}], "highlight": {"index": 1}},
        "cues": [{"at": 0, "kind": "title"}, {"at": 0.2, "kind": "number", "data": {"index": [0, 1]}},
                 {"at": {"segment": "s02", "word": "倒掛", "edge": "end"}, "kind": "highlight"}]}})
    sh(shots, "SH01")["chart"]["cues"].pop(3)


def _only_the_second_line_changes(shots, _):
    """SH02 跟 SH01 標同一點、同一句字，只多一行 sub。"""
    sh(shots, "SH02")["chart"]["data"]["highlight"] = {"index": 0, "text": "短天期較高", "sub": "比十年期高"}


def test_changing_only_the_second_line_is_flagged_as_a_flash(tmp_path):
    """計時實測 R2-3：同一點只加（或改）第二行，引擎把整個標註淡出再長出來，第一行的字消失一下又回來，看起來閃一下（該修）。
    提醒換一句新的字，或一開始兩行就一起寫。換一句不同的字（mini_project 原樣：換到別根柱）不算。"""
    notes = reminders(make_project(tmp_path, _only_the_second_line_changes))
    assert len(notes) == 1, notes
    for needle in ["分鏡 SH02 的 chart.cues[0]", "「短天期較高」", "第二行", "閃一下"]:
        assert needle in notes[0], f"提醒少了「{needle}」：\n{notes[0]}"


def test_the_checks_list_them_as_should_fix_without_blocking(tmp_path):
    """自動檢查（審片頁的「自動檢查」那一區）也列出來：該修、不擋出片，時段＝旁白唸到 → 畫面看得清楚（停太短：出現 → 換掉）。
    不用等 qa：只看時間軸，build 之前就算得出來。"""
    from article_to_video.auto_checks import load_check_report

    late = load_check_report(plan_build(make_project(tmp_path / "晚", _label_with_the_number), tts="fake"))
    [f] = [f for f in late.findings if f.check == "late-label"]
    assert (f.status, f.shot_ids, f.blocking, f.severity) == ("warn", ("SH01",), False, "該修")
    assert f.times == ((1.95, 3.267),) and "「四點五八」" in f.message
    short = load_check_report(plan_build(make_project(tmp_path / "短", _next_label_right_after), tts="fake"))
    [f] = [f for f in short.findings if f.check == "short-label"]
    assert (f.status, f.shot_ids, f.times) == ("warn", ("SH01",), ((2.767, 3.667),))
    assert f.token != [g for g in late.findings if g.check == "late-label"][0].token


def test_a_late_emphasis_in_a_comparison_is_flagged(tmp_path):
    """兩數對比的強調也算：旁白 4.8 秒唸到「三點九六」，強調 6.67＋0.5 秒才換好色。"""
    notes = reminders(make_project(tmp_path, _comparison_emphasised_at_the_end))
    assert len(notes) == 1, notes
    for needle in ["分鏡 SH02 的 chart.cues[2]", "強調「3.96%」", "4.80 秒", "「三點九六」"]:
        assert needle in notes[0], f"提醒少了「{needle}」：\n{notes[0]}"

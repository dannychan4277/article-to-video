"""主切點：「做一支片」的建置指令（`python -m article_to_video build <專案> --tts fake`）。

測試專案在 tests/fixtures/mini_project：兩段旁白、兩個財經圖表分鏡（第二個是接續分鏡），總長 8.4 秒。
預期時間一律從假配音層「寫在文件裡的規則」在這裡自己算，不呼叫被測程式：
  每段開頭靜音 0.1 秒；每個非標點字 0.2 秒（一個字＝一個詞）；每個標點停 0.25 秒；結尾靜音 0.3 秒。
建置規則（GLOSSARY／timeline.py 文件）：段間留白取 settings.json 的 gap（0.4）；片尾 tail（1.2）；
錨點對到詞的開頭時畫面提早 0.15 秒（唸完不提早）；fps 30。
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata

import pytest

from article_to_video.ffmpeg import find_ffmpeg

FPS = 30
GAP = 0.4
TAIL = 1.2
LEAD = 0.15
NARRATION = {
    "s01": "兩年期殖利率，平均四點五八。",
    "s02": "十年期只有三點九六，曲線倒掛。",
}


def fake_times(text: str) -> tuple[list[float | None], list[float | None], float]:
    """假配音層規則 → 每個字的開始、結束秒數（標點是 None）與整段長度。"""
    starts: list[float | None] = []
    ends: list[float | None] = []
    t = 0.1
    for ch in text:
        if unicodedata.category(ch).startswith("P"):
            starts.append(None)
            ends.append(None)
            t += 0.25
        else:
            starts.append(t)
            ends.append(t + 0.2)
            t += 0.2
    return starts, ends, t + 0.3


DUR = {k: fake_times(v)[2] for k, v in NARRATION.items()}
SEG_START = {"s01": 0.0, "s02": DUR["s01"] + GAP}


def word_time(seg: str, word: str, edge: str = "start", offset: float = 0.0) -> float:
    text = NARRATION[seg]
    starts, ends, _ = fake_times(text)
    i = text.index(word)
    if edge == "start":
        return SEG_START[seg] + starts[i] - LEAD + offset
    return SEG_START[seg] + ends[i + len(word) - 1] + offset


def ffprobe(*args: str) -> str:
    exe = find_ffmpeg().with_name("ffprobe")
    return subprocess.run([str(exe), "-v", "error", *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def frames_rgb(video, first: int, last: int, crop: tuple[int, int, int, int]) -> list[bytes]:
    """成片第 first～last 幀，裁 (x, y, w, h)，回傳每幀的 RGB24 位元組。"""
    x, y, w, h = crop
    proc = subprocess.run(
        [str(find_ffmpeg()), "-v", "error", "-i", str(video),
         "-vf", f"select=between(n\\,{first}\\,{last}),crop={w}:{h}:{x}:{y}",
         "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    )
    size = w * h * 3
    data = proc.stdout
    assert len(data) == size * (last - first + 1)
    return [data[i: i + size] for i in range(0, len(data), size)]


# ---------------------------------------------------------------- 產出


def test_build_writes_final_video_and_srt(built_project):
    assert built_project.video.is_file()
    assert built_project.video.stat().st_size > 0
    assert built_project.srt.is_file()
    assert built_project.srt.read_text(encoding="utf-8").strip()


def test_final_video_is_1080p_h264_with_audio(built_project):
    out = ffprobe("-show_entries", "stream=codec_type,codec_name,width,height", "-of", "json", str(built_project.video))
    streams = {s["codec_type"]: s for s in json.loads(out)["streams"]}
    assert streams["video"]["codec_name"] == "h264"
    assert (streams["video"]["width"], streams["video"]["height"]) == (1920, 1080)
    assert streams["audio"]["codec_name"] == "aac"


# ---------------------------------------------------------------- 長度


def test_final_length_is_narration_plus_gaps_plus_outro(built_project):
    expected = DUR["s01"] + GAP + DUR["s02"] + TAIL  # 8.4 秒
    frames = int(ffprobe("-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
                         "-of", "csv=p=0", str(built_project.video)).strip())
    assert abs(frames - expected * FPS) <= 1
    duration = float(ffprobe("-show_entries", "format=duration", "-of", "csv=p=0", str(built_project.video)))
    assert abs(duration - expected) <= 1 / FPS


# ---------------------------------------------------------------- cue 對上錨點


def _expected_cues() -> dict[str, list[tuple[str, float]]]:
    """每個分鏡自己的 cue（不含接續沿用的）應該在成片幾秒出現。"""
    return {
        "SH01": [
            ("title", 0.0),
            ("bar", word_time("s01", "殖利率")),
            ("highlight", word_time("s01", "四點五八", "end")),
            ("bar", word_time("s02", "十年期", offset=0.1)),
        ],
        "SH02": [("highlight", word_time("s02", "倒掛"))],
    }


def test_every_cue_appears_on_its_anchor_word(built_project):
    vdir = built_project.dir / "build" / "16x9"
    timeline = json.loads((vdir / "timeline.json").read_text(encoding="utf-8"))
    starts = {s["id"]: s["startFrame"] for s in timeline["shots"]}
    assert abs(starts["SH02"] - word_time("s02", "曲線") * FPS) <= 1  # 分鏡從「曲線」切入

    for shot, expected in _expected_cues().items():
        spec = json.loads((vdir / "specs" / f"{shot}.json").read_text(encoding="utf-8"))
        own = [c for c in spec["cues"] if c["at"] >= 0]
        assert [c["kind"] for c in own] == [k for k, _ in expected]
        for cue, (_, t) in zip(own, expected):
            frame = starts[shot] + cue["at"] * FPS
            assert abs(frame - t * FPS) <= 1, f"{shot} {cue}：在第 {frame} 幀，應該在 {t * FPS:.1f}"


def _bluish(px: bytes) -> int:
    """柱子（主色藍或淡化後的灰藍）像素數：B 明顯大於 R、而且不是白底。"""
    return sum(1 for i in range(0, len(px), 3) if px[i + 2] - px[i] >= 12 and px[i] <= 215)


def test_second_bar_grows_on_its_anchor_frame_in_final_video(built_project):
    # 第二根柱（十年期）錨在 s02「十年期」＋0.1 秒；它在畫面右半邊，那裡之前沒有任何藍色的東西
    expected = word_time("s02", "十年期", offset=0.1) * FPS  # 112.5
    first, last = 106, 119
    counts = [_bluish(f) for f in frames_rgb(built_project.video, first, last, (1100, 300, 450, 440))]
    appeared = next(first + i for i, n in enumerate(counts) if n > 500)
    assert counts[0] == 0
    assert abs(appeared - expected) <= 1, counts


# ---------------------------------------------------------------- 字幕


def _srt_entries(text: str) -> list[tuple[float, float, str]]:
    def sec(s: str) -> float:
        h, m, rest = s.split(":")
        sec_, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec_) + int(ms) / 1000

    out = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        a, b = lines[1].split(" --> ")
        out.append((sec(a), sec(b), "\n".join(lines[2:])))
    return out


def test_srt_has_one_line_per_phrase_timed_to_the_voice(built_project):
    got = _srt_entries(built_project.srt.read_text(encoding="utf-8"))
    s01, s02 = fake_times(NARRATION["s01"]), fake_times(NARRATION["s02"])
    expected = [
        (s01[0][0], s01[0][7], "兩年期殖利率"),
        (s01[0][7], DUR["s01"], "平均四點五八"),
        (SEG_START["s02"] + s02[0][0], SEG_START["s02"] + s02[0][10], "十年期只有三點九六"),
        (SEG_START["s02"] + s02[0][10], SEG_START["s02"] + DUR["s02"], "曲線倒掛"),
    ]
    assert [t for _, _, t in got] == [t for _, _, t in expected]
    for (a, b, _), (ea, eb, _) in zip(got, expected):
        assert abs(a - ea) < 0.002 and abs(b - eb) < 0.002


def test_subtitles_are_burned_as_white_text_on_dark_box(built_project):
    band = (300, 990, 1320, 84)  # 底部置中的字幕帶
    during, between = frames_rgb(built_project.video, 60, 60, band)[0], frames_rgb(built_project.video, 112, 112, band)[0]

    def dark(px: bytes) -> int:
        return sum(1 for i in range(0, len(px), 3) if max(px[i: i + 3]) < 110)

    def white(px: bytes) -> int:
        return sum(1 for i in range(0, len(px), 3) if min(px[i: i + 3]) > 235)

    assert dark(during) > 8000  # 2.0 秒：「平均四點五八」那塊深底框
    assert white(during) > 1000  # 框裡的白字
    assert dark(between) < 100  # 3.73 秒：兩段之間沒有字幕


def test_subtitles_use_the_bundled_font_not_a_system_font(built_project):
    """libass 紀錄：選到的是 fontsdir 載入的附帶字型（記憶體字型沒有路徑），不是系統裡的 Noto Sans CJK。"""
    from article_to_video.clips import FONTS_DIR

    vdir = built_project.dir / "build" / "16x9"
    log = (vdir / "logs" / "assemble.log").read_text(encoding="utf-8")
    assert "Loading font file 'fonts/NotoSansTC-500.woff'" in log
    selections = re.findall(r"fontselect: \((.*?)\) -> (.*?), (-?\d+), (.*)", log)
    assert selections
    for _req, target, _idx, ps in selections:
        assert not target.startswith("/"), f"選到系統字型：{target}"
        assert ps.strip() == "NotoSansCJKtc-Medium"
    assert "selecting one more font" not in log
    assert [p.name for p in (vdir / "fonts").iterdir()] == ["NotoSansTC-500.woff"]
    assert (vdir / "fonts" / "NotoSansTC-500.woff").read_bytes() == (FONTS_DIR / "NotoSansTC-500.woff").read_bytes()


# ---------------------------------------------------------------- 接續分鏡


def test_continuation_keeps_layout_and_ticks_across_the_cut(built_project):
    """SH01 最後一幀 vs SH02 第一幀：標題區與右側刻度欄要一樣（版式、刻度、慢推都沒跳）。"""
    cut = round(word_time("s02", "曲線") * FPS)  # 171
    regions = {"標題與副標": (0, 0, 1920, 230), "右側刻度": (1700, 230, 220, 700), "x 軸名稱": (0, 760, 1920, 70)}
    for name, crop in regions.items():
        before, after = frames_rgb(built_project.video, cut - 1, cut, crop)
        changed = sum(1 for i in range(len(before)) if abs(before[i] - after[i]) > 40) / len(before)
        assert changed < 0.002, f"{name} 在切點變了 {changed:.2%}"


def test_continuation_starts_fully_drawn(built_project):
    """接續分鏡的沿用 cue 都是負時間：片段第一幀標題、兩根柱都已畫好。"""
    spec = json.loads((built_project.dir / "build" / "16x9" / "specs" / "SH02.json").read_text(encoding="utf-8"))
    carried = [c for c in spec["cues"] if c["at"] < 0]
    assert sorted(c["kind"] for c in carried) == ["bar", "bar", "title"]
    assert all(c["at"] <= -3 for c in carried)
    assert not any(c["kind"] == "highlight" and c["at"] < 0 for c in spec["cues"])


def test_chart_specs_default_the_axis_note_to_traditional_chinese(built_project):
    """引擎內建的「纵轴未从 0 开始」是簡體；分鏡沒寫 axisNote 時建置要補繁中版。"""
    for shot in ("SH01", "SH02"):
        spec = json.loads((built_project.dir / "build" / "16x9" / "specs" / f"{shot}.json").read_text(encoding="utf-8"))
        assert spec["data"]["axisNote"] == "縱軸未從 0 開始"


# ---------------------------------------------------------------- 片尾卡


def _timeline(built_project) -> dict:
    return json.loads((built_project.dir / "build" / "16x9" / "timeline.json").read_text(encoding="utf-8"))


def test_end_card_follows_the_narration_and_counts_in_the_length(built_project):
    """片尾＝最後一段旁白唸完後的片尾卡，長 tail 秒；最後一個分鏡停在片尾卡開始的地方。"""
    tl = _timeline(built_project)
    outro = tl["outro"]
    narration_end = SEG_START["s02"] + DUR["s02"]  # 7.2 秒
    assert outro["kind"] == "card"
    assert abs(outro["startFrame"] - narration_end * FPS) <= 1
    assert abs(outro["frames"] - TAIL * FPS) <= 1
    assert outro["startFrame"] + outro["frames"] == tl["totalFrames"]
    assert tl["shots"][-1]["endFrame"] == outro["startFrame"]


def test_end_card_lists_name_sources_and_disclaimers(built_project):
    from conftest import TEST_CHANNEL

    spec_path = built_project.dir / "build" / "16x9" / "specs" / f"{_timeline(built_project)['outro']['clip']}.json"
    shown = json.dumps(json.loads(spec_path.read_text(encoding="utf-8")), ensure_ascii=False)
    for needle in (TEST_CHANNEL, "FRED（DGS2、DGS10），年平均自算", "僅供教育，非投資建議", "配音為 AI 合成"):
        assert needle in shown
    assert "來源：FRED" not in shown  # 圖上的「來源：」前綴拿掉，卡上統一寫「資料來源」


def _gray_small(rgb: bytes, w: int, h: int):
    import numpy as np

    a = np.frombuffer(rgb, dtype=np.uint8).reshape(h, w, 3).astype(np.float32)
    return a.reshape(h // 8, 8, w // 8, 8, 3).mean(axis=(1, 3))


def test_final_video_ends_on_the_end_card(built_project, tmp_path):
    """成片最後一幀＝片尾卡（跟引擎直接渲的靜幀比）；片尾卡開始前一幀還是圖表（有柱子）。"""
    import numpy as np

    from article_to_video.clips import render_stills

    tl = _timeline(built_project)
    outro = tl["outro"]
    spec = built_project.dir / "build" / "16x9" / "specs" / f"{outro['clip']}.json"
    t_local = (outro["frames"] - 1) / FPS
    still = render_stills(spec, tmp_path, [t_local]).path
    expected = subprocess.run([str(find_ffmpeg()), "-v", "error", "-i", str(still), "-f", "rawvideo",
                               "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    last = tl["totalFrames"] - 1
    [got] = frames_rgb(built_project.video, last, last, (0, 0, 1920, 1080))
    diff = np.abs(_gray_small(got, 1920, 1080) - _gray_small(expected, 1920, 1080))
    assert diff.mean() < 2.0 and diff.max() < 40, (diff.mean(), diff.max())

    bars = (1100, 300, 450, 440)
    before, after = frames_rgb(built_project.video, outro["startFrame"] - 1, outro["startFrame"] + 1, bars)[::2]
    assert _bluish(before) > 500
    assert _bluish(after) == 0


def test_continuation_group_shares_one_continuous_push(built_project):
    vdir = built_project.dir / "build" / "16x9"
    a = json.loads((vdir / "specs" / "SH01.json").read_text(encoding="utf-8"))["data"]
    b = json.loads((vdir / "specs" / "SH02.json").read_text(encoding="utf-8"))["data"]
    assert a["pushFrom"] == 0
    assert b["pushFrom"] == pytest.approx(a["pushFrom"] + a["push"], abs=1e-6)  # 第二段從第一段推到的地方接著推
    da = json.loads((vdir / "specs" / "SH01.json").read_text(encoding="utf-8"))["duration"]
    db = json.loads((vdir / "specs" / "SH02.json").read_text(encoding="utf-8"))["duration"]
    assert a["push"] / da == pytest.approx(b["push"] / db, rel=1e-3)  # 同一個速率

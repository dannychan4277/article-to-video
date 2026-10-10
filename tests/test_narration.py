"""配音層切點：narrate()＋可抽換的配音實作。不連網：edge-tts 用假的 Communicate 餵資料流。"""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from article_to_video.ffmpeg import find_ffmpeg
from article_to_video.narration import EdgeTTS, FakeTTS, Synthesis, WordTiming, narrate

from conftest import MINI_PROJECT


class CountingTTS:
    """測試替身：記下每次真的去配音的文字，產生一秒靜音、每個字一個詞。"""

    name = "counting"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def synthesize(self, text, *, voice, rate, out_stem):
        self.calls.append((text, voice, rate))
        return FakeTTS().synthesize(text, voice=voice, rate=rate, out_stem=out_stem)


def test_narrate_returns_audio_duration_and_char_times(tmp_path):
    out = narrate({"s01": "殖利率，走高。"}, engine=FakeTTS(), voice="zh-TW-HsiaoYuNeural", rate="+0%", cache_dir=tmp_path)
    a = out["s01"]
    # 假配音層規則：0.1 + 3 字 × 0.2 + 標點 0.25 + 2 字 × 0.2 + 標點 0.25 + 0.3
    assert a.duration == pytest.approx(0.1 + 0.6 + 0.25 + 0.4 + 0.25 + 0.3)
    with wave.open(str(a.audio)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (48000, 1, 2)
        assert w.getnframes() / 48000 == pytest.approx(a.duration)
    times = a.char_times()
    assert len(times) == len("殖利率，走高。")
    assert times[0] == pytest.approx(0.1)
    assert times[4] == pytest.approx(0.1 + 0.6 + 0.25)  # 「走」
    assert times[3] == pytest.approx(times[4])  # 標點對到下一個詞
    assert a.char_end(5) == pytest.approx(0.1 + 0.6 + 0.25 + 0.4)  # 「高」唸完


def test_same_voice_rate_and_text_is_not_synthesized_twice(tmp_path):
    tts = CountingTTS()
    segs = {"s01": "第一段。", "s02": "第二段。"}
    narrate(segs, engine=tts, voice="zh-TW-HsiaoChenNeural", rate="+5%", cache_dir=tmp_path)
    narrate(segs, engine=tts, voice="zh-TW-HsiaoChenNeural", rate="+5%", cache_dir=tmp_path)
    assert len(tts.calls) == 2

    narrate({"s01": "第一段。", "s02": "改過的第二段。"}, engine=tts, voice="zh-TW-HsiaoChenNeural", rate="+5%", cache_dir=tmp_path)
    assert [c[0] for c in tts.calls[2:]] == ["改過的第二段。"]

    narrate(segs, engine=tts, voice="zh-TW-HsiaoChenNeural", rate="+10%", cache_dir=tmp_path)
    narrate(segs, engine=tts, voice="zh-TW-YunJheNeural", rate="+10%", cache_dir=tmp_path)
    assert len(tts.calls) == 3 + 2 + 2


def test_segments_with_the_same_text_are_voiced_once(tmp_path):
    """兩段旁白一字不差（例如重複的一句結語）：只配一次、兩段都拿到同一份音檔（以前會並行寫同一個暫存檔而失敗）。"""
    tts = CountingTTS()
    segs = {f"s{i:02d}": "這句會重複。" for i in range(1, 7)} | {"s07": "只有一次。"}
    out = narrate(segs, engine=tts, voice="zh-TW-HsiaoChenNeural", rate="+5%", cache_dir=tmp_path, jobs=4)
    assert sorted(c[0] for c in tts.calls) == ["只有一次。", "這句會重複。"]
    assert list(out) == list(segs)
    assert len({out[k].audio for k in segs if k != "s07"}) == 1
    assert all(out[k].audio.is_file() and out[k].text == segs[k] for k in segs)


def test_renaming_a_segment_key_reuses_the_cache(tmp_path):
    tts = CountingTTS()
    narrate({"s01": "同一句話。"}, engine=tts, voice="zh-TW-HsiaoChenNeural", rate="+5%", cache_dir=tmp_path)
    narrate({"intro": "同一句話。"}, engine=tts, voice="zh-TW-HsiaoChenNeural", rate="+5%", cache_dir=tmp_path)
    assert len(tts.calls) == 1


def test_unused_cache_entries_are_cleaned_up(tmp_path):
    tts = CountingTTS()
    narrate({"s01": "舊的一段。"}, engine=tts, voice="zh-TW-HsiaoChenNeural", rate="+5%", cache_dir=tmp_path)
    narrate({"s01": "新的一段。"}, engine=tts, voice="zh-TW-HsiaoChenNeural", rate="+5%", cache_dir=tmp_path)
    metas = [json.loads(p.read_text(encoding="utf-8"))["text"] for p in tmp_path.glob("*.json")]
    assert metas == ["新的一段。"]
    assert len(list(tmp_path.glob("*.wav"))) == 1


# ---------------------------------------------------------------- 頭尾靜音（節奏）


class ToneTTS:
    """測試替身：聽得到聲音的配音。開頭靜音 0.6 秒、每個字一段 0.25 秒的 440 Hz 音（第 i 個字的音量各不相同，
    用來認出聽到的是哪個字）、標點停 0.3 秒、結尾靜音 1.0 秒——像 edge-tts 一樣頭尾帶一大段空白。"""

    name = "tone"
    HEAD, CHAR, PAUSE, TAIL = 0.6, 0.25, 0.3, 1.0

    def __init__(self) -> None:
        self.calls = 0

    @staticmethod
    def amp(i: int) -> int:
        return 2000 + 1500 * i

    def synthesize(self, text, *, voice, rate, out_stem):
        import math
        import struct

        self.calls += 1
        sr, t, words, samples = 48000, self.HEAD, [], []

        def silence(sec: float) -> None:
            samples.extend([0] * round(sec * sr))

        silence(self.HEAD)
        n = 0
        for i, ch in enumerate(text):
            if ch in "，。":
                silence(self.PAUSE)
                t += self.PAUSE
                continue
            k = round(self.CHAR * sr)
            samples.extend(round(self.amp(n) * math.sin(2 * math.pi * 440 * j / sr)) for j in range(k))
            words.append(WordTiming(i, i + 1, round(t, 6), round(t + self.CHAR, 6)))
            t += self.CHAR
            n += 1
        silence(self.TAIL)
        path = out_stem.with_suffix(".wav")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        return Synthesis(path, words)


def _rms(path: Path, start: float, end: float) -> float:
    import math
    import struct

    with wave.open(str(path)) as w:
        sr = w.getframerate()
        w.setpos(round(start * sr))
        raw = w.readframes(round((end - start) * sr))
    xs = struct.unpack(f"<{len(raw) // 2}h", raw)
    return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else 0.0


def test_long_silence_before_and_after_each_segment_is_trimmed_to_a_short_pad(tmp_path):
    """節奏：edge-tts 每段頭尾自帶約 0.2／0.8 秒空白，段與段之間停了 1.2 秒以上。配音層把頭尾的空白修成固定的一小段，
    詞的時間跟著挪——每個詞的時間點上，修過的音檔裡聽到的還是那個字（錨點照樣落在詞上）；段落中間的停頓不動。"""
    tts = ToneTTS()
    text = "殖利率，走高。"
    out = narrate({"s01": text}, engine=tts, voice="v", rate="+0%", cache_dir=tmp_path)
    a = out["s01"]
    assert 0 < a.words[0].start < 0.15  # 原本 0.6 秒
    assert 0.05 < a.duration - a.speech_end < 0.3  # 原本 1.0 秒
    with wave.open(str(a.audio)) as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(a.duration, abs=1e-4)
    for n, wt in enumerate(a.words):  # 每個詞的時間裡聽到的就是那個字的音量（正弦波 RMS＝振幅／√2）
        assert _rms(a.audio, wt.start + 0.03, wt.end - 0.03) == pytest.approx(ToneTTS.amp(n) / 2 ** 0.5, rel=0.03)
    assert _rms(a.audio, 0, a.words[0].start - 0.01) < 50  # 第一個字之前是安靜的
    assert a.words[3].start - a.words[2].end == pytest.approx(ToneTTS.PAUSE, abs=0.001)  # 逗號的停頓不動

    again = narrate({"s01": text}, engine=tts, voice="v", rate="+0%", cache_dir=tmp_path)["s01"]
    assert tts.calls == 1 and (again.duration, again.words) == (a.duration, a.words)

    # 回報問題重現時：快取裡只有時間點、音檔換成同長度的靜音——修剪的位置照記下來的，時間跟作者那台一樣
    [meta] = tmp_path.glob("*.json")
    raw = meta.with_suffix(".wav")
    frames = wave.open(str(raw)).getnframes()
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x00\x00" * frames)
    for f in tmp_path.glob("*.wav"):
        if f != raw:
            f.unlink()
    silent = narrate({"s01": text}, engine=tts, voice="v", rate="+0%", cache_dir=tmp_path)["s01"]
    assert tts.calls == 1 and (silent.duration, silent.words) == (a.duration, a.words)


# ---------------------------------------------------------------- edge-tts（假資料流）


def _mp3_bytes(tmp_path: Path, seconds: float) -> bytes:
    out = tmp_path / "tone.mp3"
    subprocess.run(
        [str(find_ffmpeg()), "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
         "-t", str(seconds), "-c:a", "libmp3lame", "-b:a", "48k", str(out)],
        check=True,
    )
    return out.read_bytes()


class StubCommunicate:
    """模仿 edge_tts.Communicate：記下建構參數，stream() 依序吐出事先準備好的 chunk。"""

    made: list[dict] = []
    chunks: list[dict] = []

    def __init__(self, text, voice, **kw):
        StubCommunicate.made.append({"text": text, "voice": voice, **kw})

    async def stream(self):
        for ch in StubCommunicate.chunks:
            yield ch


def test_edge_tts_asks_for_word_boundaries_and_maps_them_to_characters(tmp_path):
    text = "殖利率在2023年，走高。"
    audio = _mp3_bytes(tmp_path, 2.0)
    StubCommunicate.made = []
    StubCommunicate.chunks = [
        {"type": "audio", "data": audio[: len(audio) // 2]},
        {"type": "WordBoundary", "offset": 1_000_000, "duration": 4_000_000, "text": "殖利率"},
        {"type": "WordBoundary", "offset": 5_000_000, "duration": 1_000_000, "text": "在"},
        {"type": "WordBoundary", "offset": 6_000_000, "duration": 5_000_000, "text": "二零二三"},  # 正規化過、對不回原文
        {"type": "WordBoundary", "offset": 12_000_000, "duration": 2_000_000, "text": "年"},
        {"type": "audio", "data": audio[len(audio) // 2:]},
        {"type": "WordBoundary", "offset": 15_000_000, "duration": 3_000_000, "text": "走高"},
    ]
    out = narrate({"s01": text}, engine=EdgeTTS(communicate=StubCommunicate), voice="zh-TW-YunJheNeural",
                  rate="+15%", cache_dir=tmp_path / "cache")

    made = StubCommunicate.made[0]
    assert made == {"text": text, "voice": "zh-TW-YunJheNeural", "rate": "+15%", "boundary": "WordBoundary"}
    a = out["s01"]
    assert list(a.words) == [
        WordTiming(0, 3, 0.1, 0.5),   # 殖利率
        WordTiming(3, 4, 0.5, 0.6),   # 在
        WordTiming(8, 9, 1.2, 1.4),   # 年（「二零二三」對不回去，跳過）
        WordTiming(10, 12, 1.5, 1.8),  # 走高
    ]
    assert a.duration == pytest.approx(2.0, abs=0.06)  # mp3 轉成 48k WAV 後的長度
    with wave.open(str(a.audio)) as w:
        assert (w.getframerate(), w.getnchannels()) == (48000, 1)


def test_edge_tts_without_audio_is_a_clear_error(tmp_path):
    from article_to_video.narration import NarrationError

    StubCommunicate.chunks = [{"type": "WordBoundary", "offset": 0, "duration": 1, "text": "嗨"}]
    with pytest.raises(NarrationError, match="旁白段 s01"):
        narrate({"s01": "嗨。"}, engine=EdgeTTS(communicate=StubCommunicate), voice="zh-TW-HsiaoChenNeural",
                rate="+5%", cache_dir=tmp_path)


# ---------------------------------------------------------------- 不改程式就能換配音層


def test_settings_json_can_select_the_fake_engine(tmp_path):
    """settings.json 寫 "tts": "fake" 就不會去連網（這個 pytest 行程連外會被擋）。"""
    from article_to_video.build import plan_build

    root = tmp_path / "p"
    shutil.copytree(MINI_PROJECT, root)
    settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    (root / "settings.json").write_text(json.dumps({**settings, "tts": "fake"}), encoding="utf-8")
    plan = plan_build(root)
    assert plan.project.settings.tts == "fake"
    metas = [json.loads(p.read_text(encoding="utf-8")) for p in (root / "build" / "narration").glob("*.json")
             if p.name != "timing.json"]
    assert {m["engine"] for m in metas} == {"fake"}


def test_cli_flag_overrides_settings(tmp_path):
    from article_to_video.build import plan_build

    root = tmp_path / "p"
    shutil.copytree(MINI_PROJECT, root)
    assert plan_build(root, tts="fake").project.settings.tts == "fake"  # settings.json 沒寫 → 預設 edge，被 --tts 蓋過


def test_fake_engine_ignores_voice_but_returns_known_word_times(tmp_path):
    syn = FakeTTS().synthesize("走高。", voice="x", rate="+0%", out_stem=tmp_path / "a")
    assert isinstance(syn, Synthesis)
    assert syn.words == [WordTiming(0, 1, 0.1, 0.3), WordTiming(1, 2, 0.3, 0.5)]


# ---------------------------------------------------------------- 配音在分鏡之前（指令列 narrate）


def test_narration_is_voiced_before_any_storyboard_exists(tmp_path):
    """寫完旁白稿就配音：還沒有 shots.json，印出每段在成片裡的起點、長度和每個詞開始唸的秒數（寫錨點、算框的間隔用），
    之後預覽直接用這份配音、不再重配。預期時間照假配音層的規則（test_build.fake_times）自己算。"""
    from article_to_video.build import plan_build

    from conftest import run_cli
    from test_build import DUR, GAP, NARRATION, SEG_START, TAIL, fake_times

    root = tmp_path / "專案"
    shutil.copytree(MINI_PROJECT, root)
    (root / "shots.json").unlink()
    proc = run_cli("narrate", str(root), "--tts", "fake")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    total = DUR["s01"] + GAP + DUR["s02"] + TAIL
    assert f"總長 {total:.1f} 秒" in out
    for seg in ("s01", "s02"):
        assert f"{seg}（{SEG_START[seg]:.2f} 秒起，{DUR[seg]:.2f} 秒）" in out
    starts, _, _ = fake_times(NARRATION["s02"])
    i = NARRATION["s02"].index("倒")
    assert f"倒 {SEG_START['s02'] + starts[i]:.2f}" in out  # 詞在成片裡開始唸的秒數
    assert (root / "build" / "narration" / "timing.json").is_file()

    (root / "shots.json").write_text((MINI_PROJECT / "shots.json").read_text(encoding="utf-8"), encoding="utf-8")
    logged: list[str] = []
    plan_build(root, tts="fake", log=logged.append)
    assert any("0 段新配、2 段用快取" in line for line in logged), logged

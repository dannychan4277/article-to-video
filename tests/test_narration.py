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

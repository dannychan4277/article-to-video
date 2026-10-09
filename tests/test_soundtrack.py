"""音軌：配樂墊在旁白下面（旁白時明顯壓低、旁白中的短停頓不忽大忽小、片尾卡時拉高、結尾淡出）。

切點是 soundtrack.mix_soundtrack（WAV 進、WAV 出）。旁白用一段自製的「說話聲」：200 Hz 帶諧波、
每秒 4 次音節起伏，電平約 −18 dBFS。配樂成分＝混好的音軌減掉原本的旁白。
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from article_to_video.music import ensure_track
from article_to_video.soundtrack import mix_soundtrack

SR = 48000
TOTAL = 10.0
SPEECH = [(0.1, 3.0), (3.6, 7.0)]  # 兩段旁白，中間停 0.6 秒
OUTRO = 7.4


def write_mono(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.round(x * 32767).astype("<i2").tobytes())


def read(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SR
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
        return x.reshape(-1, w.getnchannels())


def db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-12)


def span(a: float, b: float) -> slice:
    return slice(round(a * SR), round(b * SR))


@pytest.fixture(scope="module")
def mixed(tmp_path_factory):
    d = tmp_path_factory.mktemp("音軌")
    t = np.arange(round(TOTAL * SR)) / SR
    voice = sum(np.sin(2 * np.pi * 200 * k * t) / k for k in range(1, 6)) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
    talking = np.zeros_like(t)
    for a, b in SPEECH:
        talking[span(a, b)] = 1
    narration = voice * talking
    narration *= 10 ** (-18 / 20) / np.sqrt(np.mean(np.square(narration[talking > 0])))
    write_mono(d / "narration.wav", narration)
    music = ensure_track("calm", d / "data")
    mix_soundtrack(d / "narration.wav", music, speech=SPEECH, outro_start=OUTRO, out=d / "mix.wav")
    out = read(d / "mix.wav")
    nar = read(d / "narration.wav")[:, 0]
    return out, nar, out - nar[:, None]


def test_soundtrack_is_stereo_and_as_long_as_the_narration(mixed):
    out, nar, _ = mixed
    assert out.shape == (len(nar), 2)


def test_narration_comes_through_untouched(mixed):
    out, nar, music = mixed
    for a, b in SPEECH:
        assert abs(db(out[span(a, b), 0]) - db(nar[span(a, b)])) < 0.5


def test_music_is_clearly_lower_than_the_voice(mixed):
    _, nar, music = mixed
    voice = db(nar[span(0.1, 7.0)][np.abs(nar[span(0.1, 7.0)]) > 0])
    for a, b in ((0.6, 2.8), (4.0, 6.8)):
        under = db(music[span(a, b)])
        assert under <= voice - 18, (a, b, under, voice)
        assert under > -70  # 但確實有配樂
    assert db(music[span(3.1, 3.5)]) <= voice - 15  # 短停頓不把配樂拉起來（不會一抽一抽）


def test_music_comes_up_on_the_end_card_and_fades_out(mixed):
    out, _, music = mixed
    under = db(music[span(4.0, 6.8)])
    assert db(music[span(7.9, 8.6)]) >= under + 6
    assert db(out[-SR // 50:]) < -50  # 最後 20ms 已經淡出
    assert db(out[: SR // 100]) < db(out[span(0.5, 1.0)])  # 開頭淡入

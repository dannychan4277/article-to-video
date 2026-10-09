"""配樂：用引擎的程式作曲範本做法產生 2–3 首原創配樂（程式碼產生，不放音檔進 repo），快取在使用者資料位置。"""

from __future__ import annotations

import itertools
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from article_to_video.music import TRACKS, ensure_track, render_track


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
        return x.reshape(-1, w.getnchannels()), w.getframerate()


def db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-12)


@pytest.fixture(scope="module")
def tracks(tmp_path_factory) -> dict[str, Path]:
    data = tmp_path_factory.mktemp("plugin-data")
    return {name: ensure_track(name, data) for name in TRACKS}


def test_two_or_three_tracks_render_as_usable_stereo_loops(tracks):
    assert 2 <= len(TRACKS) <= 3
    for name, path in tracks.items():
        x, sr = read_wav(path)
        assert sr == 48000 and x.shape[1] == 2
        assert 12 <= len(x) / sr <= 40, name  # 一段可循環的樂句
        assert np.abs(x).max() < 0.95, name  # 不破音
        assert -26 <= db(x) <= -14, name  # 存檔電平一致，墊在旁白下時再壓


def test_tracks_are_cached_in_the_data_dir(tracks):
    for name, path in tracks.items():
        assert path.parent.name == "music"
        before = path.stat().st_mtime_ns
        assert ensure_track(name, path.parent.parent) == path
        assert path.stat().st_mtime_ns == before  # 第二次直接用快取


def test_same_track_renders_bit_identical(tmp_path):
    a, b = render_track("minimal", tmp_path / "a.wav"), render_track("minimal", tmp_path / "b.wav")
    assert a.read_bytes() == b.read_bytes()


def _profile(x: np.ndarray, sr: int) -> np.ndarray:
    """頻譜分佈（對數頻帶能量，正規化）＋節奏（音量包絡的週期性）。"""
    mono = x.mean(axis=1)
    spec = np.abs(np.fft.rfft(mono[: sr * 12]))
    freqs = np.fft.rfftfreq(sr * 12, 1 / sr)
    edges = np.geomspace(40, 12000, 25)
    bands = np.array([spec[(freqs >= lo) & (freqs < hi)].sum() for lo, hi in itertools.pairwise(edges)])
    return bands / np.linalg.norm(bands)


def test_tracks_sound_different_from_each_other(tracks):
    profiles = {}
    tempos = {}
    for name, path in tracks.items():
        x, sr = read_wav(path)
        profiles[name] = _profile(x, sr)
        tempos[name] = len(x) / sr
    for a, b in itertools.combinations(tracks, 2):
        assert float(profiles[a] @ profiles[b]) < 0.97, (a, b)  # 配器不同：頻譜分佈不一樣
        assert abs(tempos[a] - tempos[b]) > 1.0, (a, b)  # 速度不同：一輪的長度不一樣


def test_loop_point_has_no_click(tracks):
    for name, path in tracks.items():
        x, _ = read_wav(path)
        looped = np.concatenate([x[-2000:], x[:2000]])
        jump = np.abs(np.diff(looped, axis=0)).max(axis=1)
        inside = np.abs(np.diff(x, axis=0)).max(axis=1)
        assert jump[1999] <= np.percentile(inside, 99.9), name


def samples(*args: str) -> subprocess.CompletedProcess:
    from conftest import run_cli

    return run_cli("samples", *args)


def test_music_samples_command_writes_one_file_per_track(tracks, tmp_path):
    """安裝流程讓作者試聽、挑一首：每首一個試聽檔，輸出列出名字、說明、檔案位置。"""
    data = next(iter(tracks.values())).parent.parent
    proc = samples("music", "--out", str(tmp_path / "試聽"), "--data-dir", str(data), "--seconds", "6")
    assert proc.returncode == 0, proc.stderr
    for name, track in TRACKS.items():
        f = tmp_path / "試聽" / f"music-{name}.wav"
        x, sr = read_wav(f)
        assert abs(len(x) / sr - 6) < 0.01
        assert db(x[-240:]) < -50  # 結尾淡出
        assert track.label in proc.stdout and str(f) in proc.stdout


def test_voice_samples_command_writes_one_file_per_voice(tmp_path):
    """安裝流程讓作者聽三個聲音挑一個（測試用假配音層，不連網）。"""
    from article_to_video.project import ZH_TW_VOICES

    proc = samples("voice", "--out", str(tmp_path / "試聽"), "--tts", "fake", "--data-dir", str(tmp_path / "data"))
    assert proc.returncode == 0, proc.stderr
    for vid, name in ZH_TW_VOICES.items():
        f = tmp_path / "試聽" / f"voice-{vid}.wav"
        assert f.is_file() and name in proc.stdout and str(f) in proc.stdout

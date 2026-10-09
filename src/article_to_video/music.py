"""配樂：用程式作曲產生幾首原創配樂（不用任何取樣、不放音檔進 repo）。

做法改寫自內嵌引擎的程式作曲範本 engine/audio/synth_template.py（花叔 huashu-art-motion，MIT）：
加法合成、撥弦（各次諧波各自衰減）、ADSR、鋸齒鋪底、合成殘響、電平對齊與軟限幅。範本是綁死 15 秒的
速通片配樂（還有貓叫、碎裂音效），這裡只拿樂器與混音的做法，編曲另寫成「8 小節可循環的樂句」：
- 濾波改用頻域（numpy FFT）做，不需要 scipy；殘響用循環摺積，樂句頭尾接起來沒有縫。
- 每首有固定的亂數種子，同一份程式碼兩次產生的檔案逐位元組相同。

產物：48 kHz、立體聲、16-bit WAV，RMS 對齊 −20 dBFS、峰值軟限幅在 −1 dBFS 以下。
快取在使用者資料位置的 music/<名字>-v<版本>-<程式碼雜湊>.wav（user_settings.py）；改了這份程式碼會自動重產。
成片裡怎麼墊在旁白下面見 soundtrack.py。
"""

from __future__ import annotations

import hashlib
import os
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

__all__ = ["SR", "TRACKS", "Track", "ensure_track", "read_track", "render_track", "soft_limit", "write_preview", "write_wav"]

SR = 48000
MUSIC_VERSION = "1"
_HERE = Path(__file__)


@dataclass(frozen=True)
class Track:
    name: str
    label: str
    """給作者看的名字。"""
    mood: str
    """一句話說明（安裝時試聽用）。"""
    bpm: float
    seed: int


TRACKS: dict[str, Track] = {
    "calm": Track("calm", "沉穩鋼琴", "鋼琴分解和弦配柔和鋪底，慢而穩，適合分析型內容", 76, 11),
    "pulse": Track("pulse", "新聞脈動", "撥弦配輕鼓點，有往前推的感覺", 100, 23),
    "minimal": Track("minimal", "極簡電子", "低音脈衝配電子撥奏，乾淨俐落", 112, 37),
}

# ---------------------------------------------------------------- 基礎構件（照範本改寫）

_NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def hz(name: str) -> float:
    """音名 → 頻率，例 "A4" → 440、"Bb2"、"F#3"。"""
    s, i = _NOTE[name[0]], 1
    while name[i] in "#b":
        s += 1 if name[i] == "#" else -1
        i += 1
    midi = 12 * (int(name[i:]) + 1) + s
    return 440.0 * 2 ** ((midi - 69) / 12)


def _tv(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR)) / SR


def _filter(x: np.ndarray, lo: float | None = None, hi: float | None = None, order: int = 2) -> np.ndarray:
    """零相位 Butterworth 振幅響應（頻域相乘）。lo＝高通截止、hi＝低通截止。前後補零避免循環摺疊。"""
    pad = min(len(x), SR // 10)
    n = _fast_len(len(x) + 2 * pad)
    spec = np.fft.rfft(np.pad(x, pad), n)
    f = np.fft.rfftfreq(n, 1 / SR)
    gain = np.ones_like(f)
    if hi is not None:
        gain /= np.sqrt(1 + (f / hi) ** (2 * order))
    if lo is not None:
        with np.errstate(divide="ignore"):
            gain /= np.sqrt(1 + (lo / np.maximum(f, 1e-9)) ** (2 * order))
    return np.fft.irfft(spec * gain, n)[pad: pad + len(x)]


def _fast_len(n: int) -> int:
    """FFT 長度取 2 的次方（任意長度的 FFT 慢很多）。"""
    return 1 << max(0, (n - 1).bit_length())


def _adsr(dur: float, a: float = 0.01, d: float = 0.05, s: float = 0.8, r: float = 0.1) -> np.ndarray:
    """dur＝按住時長，回傳長度 dur＋r 的包絡。"""
    t = _tv(dur + r)
    e = np.where(t < a, t / max(a, 1e-4), 0.0)
    m = (t >= a) & (t < a + d)
    e[m] = 1 - (1 - s) * (t[m] - a) / max(d, 1e-4)
    m = (t >= a + d) & (t < dur)
    e[m] = s
    held = e[t < dur]
    lvl = s if dur > a + d else (held[-1] if len(held) else 0.0)
    m = t >= dur
    e[m] = lvl * np.exp(-(t[m] - dur) / max(r / 4, 1e-4))
    return e


def _saw(n: int) -> list[float]:
    return [1 / k for k in range(1, n + 1)]


class _Voice:
    """一首曲子的合成器：固定種子的亂數＋左右聲道匯流排。"""

    def __init__(self, track: Track, bars: int = 8, tail: float = 3.0) -> None:
        self.rng = np.random.default_rng(track.seed)
        self.beat = 60 / track.bpm
        self.n = round(bars * 4 * self.beat * SR)
        self.L = np.zeros(self.n + int(tail * SR))
        self.R = np.zeros_like(self.L)
        self._memo: dict[tuple, np.ndarray] = {}

    def _once(self, key: tuple, make: Callable[[], np.ndarray]) -> np.ndarray:
        """同一個樂器、同一個音只合成一次，之後重複用（音量由 add 的 gain 決定）。"""
        if key not in self._memo:
            self._memo[key] = make()
        return self._memo[key]

    # 合成
    def additive(self, f: float | np.ndarray, amps: list[float], dur: float) -> np.ndarray:
        n = int(dur * SR) if np.isscalar(f) else len(f)
        fa = np.full(n, f) if np.isscalar(f) else f
        ph = 2 * np.pi * np.cumsum(fa) / SR
        out, fmax = np.zeros(n), fa.max()
        for k, a in enumerate(amps, 1):
            if a and k * fmax <= 18000:
                out += a * np.sin(k * ph + self.rng.uniform(0, 2 * np.pi))
        return out

    def pluck(self, f: float, dur: float, amps: list[float], d0: float = 3.0, slope: float = 0.6, b: float = 0.0,
              noise: float = 0.02, att: float = 0.010) -> np.ndarray:
        """撥弦／擊弦：各次諧波獨立指數衰減，高次衰減更快、起音更慢；b＝弦的非諧性。"""
        t = _tv(dur)
        out = np.zeros_like(t)
        ph = 2 * np.pi * f * t
        for k, a in enumerate(amps, 1):
            fk = f * k * np.sqrt(1 + b * k * k)
            if fk > 18000 or not a:
                continue
            ak = min(att * (1 + 0.6 * (k - 1)), 0.035)
            out += (a * np.exp(-t * d0 * (1 + slope * (k - 1))) * np.minimum(1, t / ak)
                    * np.sin(ph * fk / f + self.rng.uniform(0, 6.28)))
        if noise:
            nb = self.rng.standard_normal(len(t)) * np.exp(-t / 0.006) * noise * 0.5
            out += _filter(nb, lo=2000, hi=7000)
        fade = min(len(t), int(0.03 * SR))
        out[len(t) - fade:] *= np.linspace(1, 0, fade)  # 截斷處淡出，不留爆音
        return out

    # 樂器
    def piano(self, note: str, dur: float = 1.5) -> np.ndarray:
        return self._once(("piano", note, dur), lambda: self.pluck(
            hz(note), dur, [1, 0.16, 0.08, 0.04, 0.025, 0.015], d0=2.6, slope=0.45, b=0.0002, noise=0.03))

    def celesta(self, note: str, dur: float = 0.9) -> np.ndarray:
        return self._once(("celesta", note, dur), lambda: self.pluck(
            hz(note), dur, [1, 0.6, 0.12, 0.1, 0.12, 0.12, 0.2, 0.06], d0=7, slope=0.3, noise=0.02))

    def pizz(self, note: str, dur: float = 0.35) -> np.ndarray:
        return self._once(("pizz", note, dur), lambda: self.pluck(
            hz(note), dur, [1, 0.9, 0.6, 0.22, 0.35, 0.14, 0.09, 0.05], d0=14, slope=0.3, noise=0.04))

    def synth_pluck(self, note: str, dur: float = 0.22) -> np.ndarray:
        def make() -> np.ndarray:
            t = _tv(dur)
            raw = self.additive(hz(note), [1, 0.1, 0.38, 0.3, 0.22, 0.12, 0.14, 0.1, 0.08], dur)
            return _filter(raw * np.exp(-t / 0.1) * np.minimum(1, t / 0.014), hi=5000)
        return self._once(("synth_pluck", note, dur), make)

    def pad(self, note: str, dur: float) -> np.ndarray:
        def make() -> np.ndarray:
            f, tot = hz(note), dur + 0.8
            vib = 1 + 0.002 * np.sin(2 * np.pi * 4.7 * _tv(tot) + self.rng.uniform(0, 6.28))
            raw = self.additive(f * vib, _saw(8), tot)
            return _filter(raw, hi=1800) * _adsr(dur, a=0.6, d=0.3, s=0.75, r=0.8)[: len(raw)]
        return self._once(("pad", note, round(dur, 6)), make)

    def sub(self, note: str, dur: float) -> np.ndarray:
        def make() -> np.ndarray:
            f, tot = hz(note), dur + 0.12
            raw = _filter(self.additive(f, _saw(20), tot), hi=600, order=4) + 0.6 * np.sin(np.pi * f * _tv(tot))
            return raw * _adsr(dur, a=0.012, d=0.08, s=0.7, r=0.12)[: len(raw)]
        return self._once(("sub", note, round(dur, 6)), make)

    def kick(self, tau: float = 0.16) -> np.ndarray:
        def make() -> np.ndarray:
            t = _tv(0.35)
            f = 48 + (130 - 48) * np.exp(-t / 0.025)
            return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / tau)
        return self._once(("kick", tau), make)

    def hat(self, dur: float = 0.03) -> np.ndarray:
        t = _tv(dur)
        return _filter(self.rng.standard_normal(len(t)), lo=7500, order=4) * np.exp(-t / (dur / 3))

    # 匯流排
    def add(self, sig: np.ndarray, beat: float, gain: float = 1.0, pan: float = 0.0) -> None:
        """在第 beat 拍（0 起算，可為小數）放進去；等功率聲像 pan −1..1。"""
        s = round(beat * self.beat * SR)
        sig = sig[: len(self.L) - s] * gain
        a = (pan + 1) * np.pi / 4
        self.L[s: s + len(sig)] += sig * np.cos(a)
        self.R[s: s + len(sig)] += sig * np.sin(a)

    def finish(self, wet: float) -> np.ndarray:
        """尾音繞回開頭（可循環）→ 循環摺積殘響 → RMS −20 dBFS → 軟限幅。回傳 (樣本, 2)。"""
        n, tail = self.n, len(self.L) - self.n
        out = []
        ir_t = _tv(1.2)
        ir = self.rng.standard_normal(len(ir_t)) * np.exp(-ir_t / 0.32)
        ir = _filter(ir, hi=6000)
        ir[: int(0.012 * SR)] = 0  # 12ms 預延遲
        ir /= np.sqrt(np.sum(ir ** 2))
        m = _fast_len(n + len(ir))
        ir_f = np.fft.rfft(ir, m)
        for ch in (self.L, self.R):
            x = ch[:n].copy()
            x[:tail] += ch[n:]
            rev = np.fft.irfft(np.fft.rfft(x, m) * ir_f, m)
            rev[: m - n] += rev[n:]  # 殘響尾巴繞回開頭＝循環摺積
            out.append(x + wet * rev[:n])
        y = np.stack(out, axis=1)
        y *= 10 ** (-20 / 20) / (np.sqrt(np.mean(y ** 2)) + 1e-12)
        for _ in range(2):
            y = soft_limit(y, 10 ** (-1.5 / 20), loop=True)
        return y


def soft_limit(y: np.ndarray, ceil: float, *, loop: bool = False) -> np.ndarray:
    """只壓超過門限的瞬態（2ms 視窗的峰值包絡＋平滑）。loop=True：頭尾當成接在一起（可循環的樂句）。"""
    from numpy.lib.stride_tricks import sliding_window_view

    w, mode = 96, ("wrap" if loop else "edge")
    env = np.abs(y).max(axis=1)
    env = sliding_window_view(np.pad(env, (w // 2, w - w // 2 - 1), mode=mode), w).max(axis=1)
    g = np.minimum(1.0, ceil / np.maximum(env, 1e-9))
    g = np.convolve(np.pad(g, (w // 2, w - w // 2 - 1), mode=mode), np.ones(w) / w, mode="valid")
    return y * g[:, None]


# ---------------------------------------------------------------- 編曲（每首 8 小節、4/4，和弦每小節一個）


def _calm(v: _Voice) -> float:
    """A 小調 Am–F–C–G：鋼琴八分音符分解和弦、慢起音鋪底、低音根音；後四小節加鋼片琴的長音。"""
    chords = [("A2", ["A3", "C4", "E4", "A4"]), ("F2", ["F3", "A3", "C4", "F4"]),
              ("C3", ["C4", "E4", "G4", "C5"]), ("G2", ["G3", "B3", "D4", "G4"])]
    shape = [0, 1, 2, 3, 1, 2, 3, 2]
    bell = ["E5", "C5", "G5", "D5"]
    for bar in range(8):
        root, tones = chords[bar % 4]
        b0 = bar * 4
        for i, idx in enumerate(shape):
            vel = (1.0 if i == 0 else 0.62) * v.rng.uniform(0.9, 1.0)
            v.add(v.piano(tones[idx]), b0 + i * 0.5, 0.30 * vel, -0.25 if i % 2 else 0.2)
        for k, tone in enumerate(tones[:3]):
            v.add(v.pad(tone, 4 * v.beat), b0, 0.035, (-0.5, 0.0, 0.5)[k])
        v.add(v.sub(root, 3.6 * v.beat), b0, 0.10)
        if bar >= 4:
            v.add(v.celesta(bell[bar % 4]), b0 + 2, 0.09, 0.35)
    return 0.20


def _pulse(v: _Voice) -> float:
    """D 小調 Dm–B♭–F–C：撥弦八分音符頑固音型、大鼓一三拍、開拍外的輕腳踏鈸、低音、淡淡的鋪底。"""
    chords = [("D2", ["D3", "A3", "D4", "F4"]), ("Bb1", ["Bb2", "F3", "Bb3", "D4"]),
              ("F2", ["F3", "C4", "F4", "A4"]), ("C2", ["C3", "G3", "C4", "E4"])]
    shape = [0, 2, 1, 2, 3, 2, 1, 2]
    for bar in range(8):
        root, tones = chords[bar % 4]
        b0 = bar * 4
        for i, idx in enumerate(shape):
            v.add(v.pizz(tones[idx]), b0 + i * 0.5, (0.34 if i % 4 == 0 else 0.24) * v.rng.uniform(0.92, 1.0),
                  0.3 if i % 2 else -0.3)
            v.add(v.hat(), b0 + i * 0.5 + 0.5, 0.05 if i % 2 == 0 else 0.03, 0.4)
        for beat in (0, 2):
            v.add(v.kick(), b0 + beat, 0.42)
        for beat in (0, 1.5, 2, 3.5):
            v.add(v.sub(root, 0.4 * v.beat), b0 + beat, 0.13)
        for k, tone in enumerate(tones[1:4]):
            v.add(v.pad(tone, 4 * v.beat), b0, 0.02, (-0.6, 0.0, 0.6)[k])
    return 0.12


def _minimal(v: _Voice) -> float:
    """E 小調 Em–C–G–D：低音八分音符脈衝、十六分音符的電子撥奏（留空拍）、四拍大鼓、細碎腳踏鈸。"""
    chords = [("E2", ["E4", "G4", "B4", "E5"]), ("C2", ["C4", "E4", "G4", "C5"]),
              ("G2", ["G3", "B3", "D4", "G4"]), ("D2", ["D4", "F#4", "A4", "D5"])]
    steps = [0, None, 2, 1, None, 3, 2, None, 1, None, 3, 2, None, 1, 0, None]
    for bar in range(8):
        root, tones = chords[bar % 4]
        b0 = bar * 4
        for i in range(8):
            v.add(v.sub(root, 0.42 * v.beat), b0 + i * 0.5, 0.15 if i % 2 == 0 else 0.10)
        for i, idx in enumerate(steps):
            if idx is not None:
                v.add(v.synth_pluck(tones[idx]), b0 + i * 0.25, 0.16 * v.rng.uniform(0.85, 1.0), 0.45 if i % 2 else -0.45)
        for i in range(16):
            v.add(v.hat(0.02), b0 + i * 0.25, 0.035 if i % 4 == 2 else 0.015, -0.3)
        for beat in range(4):
            v.add(v.kick(0.12), b0 + beat, 0.30)
    return 0.10


_ARRANGEMENTS: dict[str, Callable[[_Voice], float]] = {"calm": _calm, "pulse": _pulse, "minimal": _minimal}


# ---------------------------------------------------------------- 輸出與快取


def write_wav(y: np.ndarray, out: Path) -> None:
    """(樣本, 聲道) 的 float → 48 kHz 16-bit WAV（先寫暫存檔再換名）。"""
    pcm = np.clip(np.round(y * 32767), -32768, 32767).astype("<i2")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(y.shape[1])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    os.replace(tmp, out)


def read_track(path: Path) -> np.ndarray:
    """讀 16-bit WAV → (樣本, 聲道) 的 float（−1..1）。"""
    with wave.open(str(path), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
        return x.reshape(-1, w.getnchannels())


def render_track(name: str, out: str | os.PathLike) -> Path:
    """產生一首配樂（一輪 8 小節、可循環）寫到 out。"""
    track = TRACKS[name]
    v = _Voice(track)
    wet = _ARRANGEMENTS[name](v)
    out = Path(out)
    write_wav(v.finish(wet), out)
    return out


def _code_hash() -> str:
    return hashlib.sha256(_HERE.read_bytes()).hexdigest()[:10]


def ensure_track(name: str, data_dir: str | os.PathLike | None = None) -> Path:
    """配樂檔（沒有就產生，放 <資料位置>/music/）。同一首的舊版本檔順手清掉。"""
    from article_to_video.user_settings import resolve_data_dir

    music_dir = resolve_data_dir(data_dir) / "music"
    path = music_dir / f"{name}-v{MUSIC_VERSION}-{_code_hash()}.wav"
    if not path.is_file():
        render_track(name, path)
        for old in music_dir.glob(f"{name}-v*.wav"):
            if old != path:
                old.unlink()
    return path


def write_preview(name: str, out: str | os.PathLike, seconds: float = 20.0,
                  data_dir: str | os.PathLike | None = None) -> Path:
    """試聽檔：配樂循環到 seconds 秒，開頭 0.05 秒淡入、結尾 1.5 秒淡出。"""
    loop = read_track(ensure_track(name, data_dir))
    n = round(seconds * SR)
    y = np.tile(loop, (n // len(loop) + 1, 1))[:n].copy()
    y *= _fade(n, 0.05, min(1.5, seconds / 3))[:, None]
    out = Path(out)
    write_wav(y, out)
    return out


def _fade(n: int, fade_in: float, fade_out: float) -> np.ndarray:
    g = np.ones(n)
    a, b = min(n, int(fade_in * SR)), min(n, int(fade_out * SR))
    if a:
        g[:a] = np.linspace(0, 1, a)
    if b:
        g[n - b:] *= np.cos(np.linspace(0, np.pi / 2, b)) ** 2
    return g

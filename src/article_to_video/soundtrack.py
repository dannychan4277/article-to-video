"""音軌：旁白＋配樂混成成片的聲音（48 kHz、立體聲、16-bit WAV），配樂壓在旁白下面。

規則（電平都相對「旁白電平」＝旁白段說話時段的 RMS；旁白是靜音（假配音層）時當作 −20 dBFS）：
- 說話時配樂比旁白低 DUCK_DB（22 dB）。段與段之間短於 BRIDGE（2 秒）的停頓當成同一段，配樂不會一抽一抽。
- 較長的空檔比旁白低 GAP_DB；片尾卡（旁白唸完之後）比旁白低 OUTRO_DB，配樂成為主角。
- 壓低在說話前 ATTACK 秒就到位、說完 RELEASE 秒後才放開；轉換是 0.3 秒的直線（dB）漸變。
- 開頭淡入 0.3 秒；結尾淡出 min(2 秒, 片尾卡長度 × 0.6)。
- 配樂一輪一輪循環到成片長度（樂句本身可無縫循環，見 music.py）。旁白原樣保留（兩個聲道都放），
  總和超過 −0.2 dBFS 的瞬間用軟限幅壓住。
"""

from __future__ import annotations

import math
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from article_to_video.music import SR, read_track, soft_limit, write_wav

__all__ = ["DUCK_DB", "GAP_DB", "OUTRO_DB", "MixReport", "mix_soundtrack"]

DUCK_DB = -22.0
GAP_DB = -14.0
OUTRO_DB = -6.0
BRIDGE = 2.0
ATTACK = 0.3
RELEASE = 0.6
RAMP = 0.3
FADE_IN = 0.3
FADE_OUT = 2.0
SILENT_VOICE_DB = -20.0
_CTRL = 100  # 控制曲線每秒幾點


@dataclass(frozen=True)
class MixReport:
    voice_db: float
    """量到的旁白電平（dBFS RMS）；旁白是靜音（假配音層）時是 SILENT_VOICE_DB。"""
    music_under_voice_db: float
    """說話時配樂的電平（dBFS RMS）。"""
    music_end_card_db: float
    """片尾卡時配樂的電平（淡出前，dBFS RMS）。"""


def _read_mono(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SR or w.getsampwidth() != 2:
            raise ValueError(f"{path.name} 要是 {SR} Hz、16-bit 的 WAV")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
        return x.reshape(-1, w.getnchannels()).mean(axis=1)


def _db(x: np.ndarray) -> float:
    return 20 * math.log10(math.sqrt(float(np.mean(np.square(x)))) + 1e-12) if len(x) else -120.0


def _merge(speech: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(speech):
        if out and a - out[-1][1] < BRIDGE:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def mix_soundtrack(
    narration: Path,
    music: Path,
    *,
    speech: Sequence[tuple[float, float]],
    outro_start: float,
    out: Path,
) -> MixReport:
    """narration：旁白音軌（48 kHz 16-bit WAV，已經是成片長度）；music：一輪可循環的配樂；
    speech：說話的時段（秒）；outro_start：片尾卡開始的秒數。寫出立體聲音軌到 out。"""
    nar = _read_mono(narration)
    total = len(nar)
    loop = read_track(music)
    bed = np.tile(loop, (total // len(loop) + 1, 1))[:total]

    def idx(t: float) -> int:
        return max(0, round(t * SR))

    talk = np.concatenate([nar[idx(a): idx(b)] for a, b in speech]) if speech else np.zeros(0)
    measured = _db(talk)
    voice_db = measured if measured > -60 else SILENT_VOICE_DB

    # 控制曲線（dB，每 10ms 一點）→ 平滑成直線漸變 → 換成每個樣本的增益
    k = math.ceil(total / SR * _CTRL) + 1

    def c(t: float) -> int:
        return min(k, max(0, round(t * _CTRL)))

    level = np.full(k, voice_db + GAP_DB)
    for a, b in _merge(speech):
        level[c(a - ATTACK): c(b + RELEASE)] = voice_db + DUCK_DB
    level[c(outro_start + RAMP / 2):] = voice_db + OUTRO_DB
    w = max(1, round(RAMP * _CTRL))
    level = np.convolve(np.pad(level, (w // 2, w - w // 2 - 1), mode="edge"), np.ones(w) / w, mode="valid")
    gain_db = level - _db(loop)
    gain = 10 ** (np.interp(np.arange(total) / SR * _CTRL, np.arange(k), gain_db) / 20)

    fade = np.ones(total)
    fi = min(total, round(FADE_IN * SR))
    fo = min(total, round(min(FADE_OUT, max(0.0, total / SR - outro_start) * 0.6) * SR))
    if fi:
        fade[:fi] = np.linspace(0, 1, fi)
    if fo:
        fade[total - fo:] *= np.cos(np.linspace(0, np.pi / 2, fo)) ** 2
    music_part = bed * (gain * fade)[:, None]

    mix = soft_limit(nar[:, None] + music_part, 10 ** (-0.2 / 20))
    write_wav(mix, out)

    under = np.concatenate([music_part[idx(a): idx(b)] for a, b in speech]) if speech else np.zeros((0, 2))
    end_card = music_part[idx(outro_start + RAMP): total - fo]
    return MixReport(voice_db=round(voice_db, 1), music_under_voice_db=round(_db(under), 1),
                     music_end_card_db=round(_db(end_card), 1))

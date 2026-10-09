"""配音層：旁白段（段落鍵 → 文字）進，每段的音檔、長度、每個字的時間點出。

介面只有一個：`narrate(segments, engine=…, voice=…, rate=…, cache_dir=…)`。要換別家配音，
寫一個符合 `TTSEngine` 的類別、登記到 `TTS_ENGINES`，在 settings.json 寫 `"tts": "<名字>"`
（或 CLI `--tts <名字>`）就好，建置流程不用改。

- `edge`：edge-tts（微軟線上語音，zh-TW 曉臻／曉雨／雲哲），要連網。
- `fake`：假配音層，給測試用：靜音＋寫死規則的時間點（見 FakeTTS），不連網、結果固定。

快取鍵＝「配音層｜聲音｜語速｜文字」，同樣的段落不會重打。所有音檔一律轉成 48 kHz、單聲道、16-bit WAV，
長度直接從 WAV 讀（不用 ffprobe），混音時逐樣本對齊。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import unicodedata
import wave
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "SAMPLE_RATE",
    "TTS_ENGINES",
    "EdgeTTS",
    "FakeTTS",
    "NarrationError",
    "SegmentAudio",
    "Synthesis",
    "TTSEngine",
    "WordTiming",
    "get_tts_engine",
    "map_word_boundaries",
    "narrate",
]

SAMPLE_RATE = 48000
_CACHE_VERSION = "1"


class NarrationError(RuntimeError):
    """配音失敗（繁中訊息）。"""


@dataclass(frozen=True)
class WordTiming:
    """一個 TTS 詞：在旁白段文字裡的字元範圍 [start_char, end_char)，與唸這個詞的起訖秒數（相對這段音檔開頭）。"""

    start_char: int
    end_char: int
    start: float
    end: float


@dataclass(frozen=True)
class SegmentAudio:
    key: str
    text: str
    audio: Path
    """48 kHz 單聲道 16-bit WAV。"""
    duration: float
    """音檔長度（秒），含頭尾靜音。"""
    words: tuple[WordTiming, ...]

    def char_start(self, i: int) -> float:
        """第 i 個字「開始唸」的秒數：包含它的那個詞的開頭；它不在任何詞裡（標點）就取下一個詞的開頭。"""
        for w in self.words:
            if w.start_char <= i < w.end_char:
                return w.start
        nxt = [w.start for w in self.words if w.start_char > i]
        if nxt:
            return nxt[0]
        return self.speech_end

    def char_end(self, i: int) -> float:
        """第 i 個字「唸完」的秒數：包含它的那個詞的結尾；不在任何詞裡就取前一個詞的結尾。"""
        for w in self.words:
            if w.start_char <= i < w.end_char:
                return w.end
        prev = [w.end for w in self.words if w.end_char <= i]
        if prev:
            return prev[-1]
        return self.words[0].start if self.words else 0.0

    def char_times(self) -> list[float]:
        """每個字元位置對應的時間點（開始唸的秒數）。"""
        return [self.char_start(i) for i in range(len(self.text))]

    @property
    def speech_end(self) -> float:
        """最後一個詞唸完的秒數（不含結尾靜音）。"""
        return self.words[-1].end if self.words else self.duration


@dataclass(frozen=True)
class Synthesis:
    audio: Path
    """配音層寫出的音檔（任何 ffmpeg 讀得懂的格式；不是 48k 單聲道 WAV 的話會再轉一次）。"""
    words: list[WordTiming]


class TTSEngine(Protocol):
    name: str

    def synthesize(self, text: str, *, voice: str, rate: str, out_stem: Path) -> Synthesis:
        """把 text 唸成音檔，寫在 out_stem 加副檔名的位置；回傳音檔路徑和詞級時間點。"""
        ...


# ---------------------------------------------------------------- 假配音層


class FakeTTS:
    """假配音層：產生靜音，時間點照固定規則（測試靠這份規則獨立算出預期時間）。

    - 開頭靜音 HEAD = 0.1 秒
    - 每個非標點、非空白的字唸 CHAR = 0.2 秒，一個字就是一個詞
    - 每個標點（Unicode 類別 P*）停 PAUSE = 0.25 秒，不算詞
    - 空白不佔時間
    - 結尾靜音 TAIL = 0.3 秒
    聲音、語速一律忽略（但仍是快取鍵的一部分）。
    """

    name = "fake"
    HEAD = 0.1
    CHAR = 0.2
    PAUSE = 0.25
    TAIL = 0.3

    def synthesize(self, text: str, *, voice: str, rate: str, out_stem: Path) -> Synthesis:
        t, words = self.HEAD, []
        for i, ch in enumerate(text):
            if ch.isspace():
                continue
            if unicodedata.category(ch).startswith("P"):
                t += self.PAUSE
                continue
            words.append(WordTiming(i, i + 1, round(t, 6), round(t + self.CHAR, 6)))
            t += self.CHAR
        t += self.TAIL
        path = out_stem.with_suffix(".wav")
        _write_silence(path, round(t * SAMPLE_RATE))
        return Synthesis(path, words)


def _write_silence(path: Path, samples: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"\x00\x00" * samples)


# ---------------------------------------------------------------- edge-tts


def map_word_boundaries(text: str, boundaries: Sequence[tuple[float, float, str]]) -> list[WordTiming]:
    """edge-tts 的 WordBoundary（offset、duration 單位 100 奈秒，text 是那個詞）→ 對回原文字元位置。

    依序往後找；TTS 把文字正規化過（例如阿拉伯數字）而對不回原文的詞就跳過。
    """
    out: list[WordTiming] = []
    pos = 0
    for offset, duration, word in boundaries:
        if not word:
            continue
        i = text.find(word, pos)
        if i < 0:
            continue
        start = offset / 1e7
        out.append(WordTiming(i, i + len(word), round(start, 6), round(start + duration / 1e7, 6)))
        pos = i + len(word)
    return out


class EdgeTTS:
    """edge-tts（edge-tts>=7,<8）。一定要 boundary="WordBoundary"，7.x 預設只給句子級時間點。

    communicate：測試時可換成假的 Communicate 類別（同樣的建構參數、有 async stream()）。
    """

    name = "edge"

    def __init__(self, communicate: Callable[..., Any] | None = None) -> None:
        self._communicate = communicate

    def synthesize(self, text: str, *, voice: str, rate: str, out_stem: Path) -> Synthesis:
        path = out_stem.with_suffix(".mp3")
        try:
            words = asyncio.run(self._stream(text, voice, rate, path))
        except NarrationError:
            raise
        except Exception as e:  # 網路、服務端錯誤
            path.unlink(missing_ok=True)
            raise NarrationError(
                f"edge-tts 配音失敗（{type(e).__name__}: {e}）。edge-tts 要連網，"
                "請確認網路正常再試一次；一直失敗的話可能是微軟的服務改了，回報給 Danny。"
            ) from e
        if not words:
            path.unlink(missing_ok=True)
            raise NarrationError("edge-tts 有回音檔，但沒有詞級時間點，錨點沒辦法對。")
        return Synthesis(path, words)

    async def _stream(self, text: str, voice: str, rate: str, path: Path) -> list[WordTiming]:
        factory = self._communicate
        if factory is None:
            import edge_tts

            factory = edge_tts.Communicate
        com = factory(text, voice, rate=rate, boundary="WordBoundary")
        boundaries: list[tuple[float, float, str]] = []
        got_audio = False
        with open(path, "wb") as f:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                    got_audio = True
                elif ch["type"] == "WordBoundary":
                    boundaries.append((ch["offset"], ch.get("duration", 0), ch["text"]))
        if not got_audio:
            raise NarrationError("edge-tts 沒有回傳任何聲音。")
        return map_word_boundaries(text, boundaries)


TTS_ENGINES: dict[str, Callable[[], TTSEngine]] = {"edge": EdgeTTS, "fake": FakeTTS}


def get_tts_engine(name: str) -> TTSEngine:
    try:
        return TTS_ENGINES[name]()
    except KeyError:
        raise NarrationError(f"沒有叫「{name}」的配音層（可用：{'、'.join(TTS_ENGINES)}）") from None


# ---------------------------------------------------------------- 快取＋統一格式


def _cache_key(engine: str, voice: str, rate: str, text: str) -> str:
    return hashlib.sha256(f"{_CACHE_VERSION}|{engine}|{voice}|{rate}|{text}".encode()).hexdigest()[:24]


def _wav_params_ok(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == SAMPLE_RATE
    except (wave.Error, EOFError, OSError):
        return False


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def _to_wav(src: Path, dst: Path, ffmpeg: Path | None) -> None:
    if src.suffix == ".wav" and _wav_params_ok(src):
        if src != dst:
            os.replace(src, dst)
        return
    from article_to_video.ffmpeg import find_ffmpeg

    exe = ffmpeg or find_ffmpeg()
    proc = subprocess.run(
        [str(exe), "-hide_banner", "-v", "error", "-y", "-i", str(src),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(dst)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise NarrationError(f"配音音檔轉檔失敗：{proc.stderr.strip()[-500:]}")
    if src != dst:
        src.unlink(missing_ok=True)


def narrate(
    segments: Mapping[str, str],
    *,
    engine: TTSEngine,
    voice: str,
    rate: str,
    cache_dir: str | os.PathLike,
    ffmpeg: str | os.PathLike | None = None,
    jobs: int = 4,
    on_segment: Callable[[str, bool], None] | None = None,
) -> dict[str, SegmentAudio]:
    """把每段旁白配成音（有快取就直接用），回傳 {段落鍵: SegmentAudio}，順序同 segments。

    快取在 cache_dir：<鍵>.wav＋<鍵>.json。同一個配音層、現在用不到的快取會被清掉。
    on_segment(段落鍵, 是否用快取)：每段完成時呼叫（印進度用）。
    """
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    ff = Path(ffmpeg) if ffmpeg is not None else None
    keys = {k: _cache_key(engine.name, voice, rate, t) for k, t in segments.items()}

    def one(seg: str) -> tuple[SegmentAudio, bool]:
        text, ck = segments[seg], keys[seg]
        wav, meta = cache / f"{ck}.wav", cache / f"{ck}.json"
        if wav.is_file() and meta.is_file():
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
                if info.get("text") == text:
                    words = tuple(WordTiming(*w) for w in info["words"])
                    return SegmentAudio(seg, text, wav, info["duration"], words), True
            except (ValueError, KeyError, TypeError):
                pass
        try:
            syn = engine.synthesize(text, voice=voice, rate=rate, out_stem=cache / f"{ck}-part")
            _to_wav(syn.audio, wav, ff)
        except NarrationError as e:
            raise NarrationError(f"旁白段 {seg} 配音失敗：{e}") from e
        duration = _wav_duration(wav)
        info = {
            "engine": engine.name, "voice": voice, "rate": rate, "text": text, "duration": duration,
            "words": [[w.start_char, w.end_char, w.start, w.end] for w in syn.words],
        }
        meta.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        return SegmentAudio(seg, text, wav, duration, tuple(syn.words)), False

    # 一字不差的兩段旁白共用同一個快取鍵：只配一次（並行配同一個鍵會搶同一個暫存檔），其他段沿用那份音檔
    first: dict[str, str] = {}
    for seg, ck in keys.items():
        first.setdefault(ck, seg)
    results: dict[str, SegmentAudio] = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        done = dict(zip(first.values(), pool.map(one, list(first.values()))))
    for seg in segments:
        lead = first[keys[seg]]
        audio, cached = done[lead]
        results[seg] = audio if seg == lead else replace(audio, key=seg)
        if on_segment:
            on_segment(seg, cached or seg != lead)

    _prune(cache, engine.name, set(keys.values()))
    return results


def _prune(cache: Path, engine: str, keep: set[str]) -> None:
    for meta in cache.glob("*.json"):
        if meta.stem in keep:
            continue
        try:
            same_engine = json.loads(meta.read_text(encoding="utf-8")).get("engine") == engine
        except ValueError:
            same_engine = True
        if same_engine:
            meta.unlink(missing_ok=True)
            meta.with_suffix(".wav").unlink(missing_ok=True)
    for part in cache.glob("*-part.*"):
        part.unlink(missing_ok=True)


def write_timing(audio: Mapping[str, SegmentAudio], path: Path) -> None:
    """把配音結果寫成 timing.json（審片頁、除錯用）。"""
    data = {
        k: {"text": a.text, "audio": str(a.audio), "duration": a.duration,
            "words": [[w.start_char, w.end_char, w.start, w.end] for w in a.words]}
        for k, a in audio.items()
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

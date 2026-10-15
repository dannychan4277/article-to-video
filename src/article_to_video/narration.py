"""配音層：旁白段（段落鍵 → 文字）進，每段的音檔、長度、每個字的時間點出。

介面只有一個：`narrate(segments, engine=…, voice=…, rate=…, cache_dir=…)`。要換別家配音，
寫一個符合 `TTSEngine` 的類別、登記到 `TTS_ENGINES`，在 settings.json 寫 `"tts": "<名字>"`
（或 CLI `--tts <名字>`）就好，建置流程不用改。

- `edge`：edge-tts（微軟線上語音，zh-TW 曉臻／曉雨／雲哲），要連網，免費（預設）。
- `elevenlabs`：ElevenLabs（付費，要 API key），見 tts_providers.py。
- `google`：Google Cloud Text-to-Speech（付費、有免費額度，要 API key），見 tts_providers.py。
- `fake`：假配音層，給測試用：靜音＋寫死規則的時間點（見 FakeTTS），不連網、結果固定。

快取鍵＝「配音層｜模型｜聲音｜語速｜文字」，同樣的段落不會重打（付費的就不會重複花錢）；換配音層全部重配一次，
換回來直接用舊的快取。所有音檔一律轉成 48 kHz、單聲道、16-bit WAV，長度直接從 WAV 讀（不用 ffprobe），
混音時逐樣本對齊。

頭尾靜音（節奏）：配音服務每段頭尾都帶空白（edge-tts 開頭約 0.2 秒、結尾約 0.8 秒），段與段之間加上 gap 會停
1.2 秒以上。narrate() 把每段頭尾修成固定的一小段（LEAD_PAD、TAIL_PAD），詞的時間跟著往前挪，錨點照樣落在詞上；
段落中間的停頓不動。修剪的位置看「聽得到聲音」（10 毫秒一格、RMS 高過 SILENCE_DB）和詞的時間點，取比較寬的，
不會切到字。原始音檔照舊快取（<鍵>.wav，快取鍵不變：舊專案不用重配、付費的不重花錢）；修剪的位置記在 <鍵>.json
的 "trim"、修好的音檔是 <鍵>.trim.wav。整段聽不到聲音（假配音層、回報問題重現時補的靜音）就不修——重現時照
json 記下的位置修，時間跟作者那台一樣。舊專案重建時會套用（片子變短、分鏡時間跟著重算）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
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
    "cached_timing_file",
    "map_word_boundaries",
    "narrate",
]

SAMPLE_RATE = 48000
_CACHE_VERSION = "1"

TRIM_VERSION = "1"
"""修剪規則的版本：改了 LEAD_PAD／TAIL_PAD／SILENCE_DB 就加一，舊的修剪位置會照新規則重算（原始音檔不用重配）。"""
LEAD_PAD = 0.06
"""每段第一個聲音（或第一個詞，取早的）前面留多少秒。"""
TAIL_PAD = 0.15
"""每段最後一個聲音（或最後一個詞唸完，取晚的）後面留多少秒。"""
SILENCE_DB = -50.0
"""比這個小聲（dBFS，10 毫秒一格的 RMS）算沒聲音。edge-tts 的空白是數位靜音（全 0），說話的地方遠高於這個。"""
_FADE = 0.005


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
    """音檔長度（秒），含頭尾留白（已修成 LEAD_PAD／TAIL_PAD，見檔頭「頭尾靜音」）。"""
    words: tuple[WordTiming, ...]
    sent_chars: int = 0
    """這次執行為了這段送給配音服務的字數（付費配音照這個計費）；用快取、或跟別段同一句共用時是 0。"""

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
    chars: int | None = None
    """送給配音服務的計費字數；None＝就是文字的字數（Google 的 SSML 外殼也算錢，它自己報）。"""


class TTSEngine(Protocol):
    name: str
    """配音層名字（TTS_ENGINES 的鍵）。可以另外有 `model: str`（例 ElevenLabs 的模型），會算進快取鍵；
    也可以有 `prepare()`：有段落真的要配音時先叫一次（例：找 API key，找不到就丟 NarrationError），全部用快取就不叫。"""

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
    """edge-tts（edge-tts>=7.3.1,<8）。一定要 boundary="WordBoundary"，7.x 預設只給句子級時間點。

    微軟偶爾整段不回聲音（edge-tts 丟 NoAudioReceived），等兩三秒再要一次就好：照 retry_delays 的秒數
    等了重試（預設最多試 3 次）。其他錯誤（連不上、服務改了）重試也沒用，直接報。
    communicate：測試時可換成假的 Communicate 類別（同樣的建構參數、有 async stream()）。
    """

    name = "edge"

    def __init__(self, communicate: Callable[..., Any] | None = None,
                 retry_delays: Sequence[float] = (2.0, 3.0)) -> None:
        self._communicate = communicate
        self._retry_delays = tuple(retry_delays)

    def synthesize(self, text: str, *, voice: str, rate: str, out_stem: Path) -> Synthesis:
        from edge_tts.exceptions import NoAudioReceived

        path = out_stem.with_suffix(".mp3")
        attempts = len(self._retry_delays) + 1
        for attempt in range(attempts):
            try:
                words = asyncio.run(self._stream(text, voice, rate, path))
                break
            except NarrationError:
                raise
            except NoAudioReceived as e:
                if attempt + 1 < attempts:
                    time.sleep(self._retry_delays[attempt])
                    continue
                err, tried = e, f"，試了 {attempts} 次"
            except Exception as e:  # 網路、服務端錯誤
                err, tried = e, ""
            path.unlink(missing_ok=True)
            raise NarrationError(
                f"edge-tts 配音失敗（{type(err).__name__}: {err}{tried}）。edge-tts 要連網，"
                "請確認網路正常再試一次；一直失敗的話可能是微軟的服務改了，回報給 Danny。"
            ) from err
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


def _elevenlabs(*, model: str = "", data_dir: str | os.PathLike | None = None) -> TTSEngine:
    from article_to_video.tts_providers import ElevenLabsTTS

    return ElevenLabsTTS(model=model or None, data_dir=data_dir)


def _google(*, model: str = "", data_dir: str | os.PathLike | None = None) -> TTSEngine:
    from article_to_video.tts_providers import GoogleTTS

    return GoogleTTS(data_dir=data_dir)


TTS_ENGINES: dict[str, Callable[..., TTSEngine]] = {
    "edge": lambda **_: EdgeTTS(),
    "elevenlabs": _elevenlabs,
    "google": _google,
    "fake": lambda **_: FakeTTS(),
}
"""配音層名字 → 工廠（關鍵字參數 model、data_dir；用不到的忽略）。"""


def get_tts_engine(name: str, *, model: str = "", data_dir: str | os.PathLike | None = None) -> TTSEngine:
    """model：ElevenLabs 的模型（空字串＝預設）；data_dir：使用者資料位置（付費配音在 <資料位置>/keys/ 找 API key）。
    建立時不找 key：全部段落都有快取就不需要 key（例：維護者重現回報的問題）。"""
    try:
        factory = TTS_ENGINES[name]
    except KeyError:
        raise NarrationError(f"沒有叫「{name}」的配音層（可用：{'、'.join(TTS_ENGINES)}）") from None
    return factory(model=model, data_dir=data_dir)


# ---------------------------------------------------------------- 快取＋統一格式


def _cache_key(engine: str, voice: str, rate: str, text: str, model: str = "") -> str:
    # 沒有模型的配音層（edge、fake、google）鍵的寫法跟以前一樣：舊專案的快取照樣命中
    who = f"{engine}|{model}" if model else engine
    return hashlib.sha256(f"{_CACHE_VERSION}|{who}|{voice}|{rate}|{text}".encode()).hexdigest()[:24]


def cached_timing_file(cache_dir: str | os.PathLike, engine: str, voice: str, rate: str, text: str,
                       model: str = "") -> Path:
    """這段旁白在配音快取裡的時間點檔（<鍵>.json；同名的 .wav 是音檔）。檔案在不在要自己看。"""
    return Path(cache_dir) / f"{_cache_key(engine, voice, rate, text, model)}.json"


def _wav_params_ok(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == SAMPLE_RATE
    except (wave.Error, EOFError, OSError):
        return False


def _wav_frames(path: Path) -> int:
    with wave.open(str(path), "rb") as w:
        return w.getnframes()


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


def _sound_bounds(wav: Path) -> tuple[float, float] | None:
    """聽得到聲音的範圍（秒）：第一格到最後一格 RMS 高過 SILENCE_DB 的 10 毫秒；整段都聽不到就 None。"""
    import numpy as np

    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)
    n = max(1, round(sr * 0.01))
    full = len(x) // n * n
    if full == 0:
        return None
    rms = np.sqrt(np.mean(x[:full].reshape(-1, n) ** 2, axis=1))
    loud = np.nonzero(rms > 32768 * 10 ** (SILENCE_DB / 20))[0]
    if len(loud) == 0:
        return None
    return loud[0] * n / sr, min(len(x), (loud[-1] + 1) * n) / sr


def _trim_window(wav: Path, duration: float, words: Sequence[WordTiming]) -> tuple[float, float]:
    """要留下的範圍 [開始, 結束)（秒）：聽得到的聲音和詞的時間點取比較寬的，前後各留 LEAD_PAD／TAIL_PAD。
    整段聽不到聲音就整段留著。"""
    bounds = _sound_bounds(wav)
    if bounds is None:
        return 0.0, duration
    first, last = bounds
    if words:
        first, last = min(first, words[0].start), max(last, words[-1].end)
    return round(max(0.0, first - LEAD_PAD), 6), round(min(duration, last + TAIL_PAD), 6)


def _write_trimmed(src: Path, dst: Path, start: float, end: float) -> None:
    """src 的 [start, end) 寫成 dst，頭尾各淡入淡出 _FADE 秒（切在留白裡，避免爆音）。"""
    import numpy as np

    with wave.open(str(src), "rb") as r:
        sr = r.getframerate()
        r.setpos(min(r.getnframes(), round(start * sr)))
        x = np.frombuffer(r.readframes(max(0, round(end * sr) - round(start * sr))), dtype="<i2").astype(np.float64)
    k = min(len(x) // 2, round(_FADE * sr))
    if k:
        ramp = np.linspace(0.0, 1.0, k, endpoint=False)
        x[:k] *= ramp
        x[len(x) - k:] *= ramp[::-1]
    tmp = dst.with_name(dst.stem + "-part.wav")  # 中斷留下的暫存檔：_prune 清 *-part.*
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.round(x).astype("<i2").tobytes())
    os.replace(tmp, dst)


def _trimmed(audio: SegmentAudio, meta: Path) -> SegmentAudio:
    """頭尾靜音修成固定的留白（見檔頭）。修剪位置記在 meta 的 "trim"（同一版規則就照記下的，不重新聽）。"""
    info = json.loads(meta.read_text(encoding="utf-8"))
    trim = info.get("trim")
    if not (isinstance(trim, dict) and trim.get("version") == TRIM_VERSION):
        start, end = _trim_window(audio.audio, audio.duration, audio.words)
        info["trim"] = {"version": TRIM_VERSION, "start": start, "end": end}
        meta.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    start, end = float(info["trim"]["start"]), min(float(info["trim"]["end"]), audio.duration)
    if start <= 0 and end >= audio.duration:
        return audio
    out = meta.with_name(meta.stem + ".trim.wav")
    expected = round(end * SAMPLE_RATE) - round(start * SAMPLE_RATE)
    if not (_wav_params_ok(out) and _wav_frames(out) == expected):
        _write_trimmed(audio.audio, out, start, end)
    words = tuple(WordTiming(w.start_char, w.end_char, round(w.start - start, 6), round(w.end - start, 6))
                  for w in audio.words)
    return replace(audio, audio=out, duration=_wav_duration(out), words=words)


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

    快取在 cache_dir：<鍵>.wav＋<鍵>.json。同一個配音層、模型、聲音、語速底下現在用不到的快取（改過的舊旁白）
    會被清掉；別的配音層／聲音／語速的留著，換回去不用重配（付費的不用再花錢）。
    on_segment(段落鍵, 是否用快取)：每段完成時呼叫（印進度用）。每段送出去的字數看 SegmentAudio.sent_chars。
    """
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    ff = Path(ffmpeg) if ffmpeg is not None else None
    model = getattr(engine, "model", "") or ""
    keys = {k: _cache_key(engine.name, voice, rate, t, model) for k, t in segments.items()}

    def from_cache(seg: str) -> SegmentAudio | None:
        text, ck = segments[seg], keys[seg]
        wav, meta = cache / f"{ck}.wav", cache / f"{ck}.json"
        if wav.is_file() and meta.is_file():
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
                if info.get("text") == text:
                    words = tuple(WordTiming(*w) for w in info["words"])
                    return SegmentAudio(seg, text, wav, info["duration"], words)
            except (ValueError, KeyError, TypeError):
                pass
        return None

    def one(seg: str) -> tuple[SegmentAudio, bool]:
        hit = from_cache(seg)
        if hit is not None:
            return _trimmed(hit, cache / f"{keys[seg]}.json"), True
        text, ck = segments[seg], keys[seg]
        wav, meta = cache / f"{ck}.wav", cache / f"{ck}.json"
        try:
            syn = engine.synthesize(text, voice=voice, rate=rate, out_stem=cache / f"{ck}-part")
            _to_wav(syn.audio, wav, ff)
        except NarrationError as e:
            raise NarrationError(f"旁白段 {seg} 配音失敗：{e}") from e
        duration = _wav_duration(wav)
        info = {
            "engine": engine.name, "model": model, "voice": voice, "rate": rate, "text": text, "duration": duration,
            "words": [[w.start_char, w.end_char, w.start, w.end] for w in syn.words],
        }
        meta.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        sent = len(text) if syn.chars is None else syn.chars
        audio = SegmentAudio(seg, text, wav, duration, tuple(syn.words), sent_chars=sent)
        return _trimmed(audio, meta), False

    # 一字不差的兩段旁白共用同一個快取鍵：只配一次（並行配同一個鍵會搶同一個暫存檔），其他段沿用那份音檔
    first: dict[str, str] = {}
    for seg, ck in keys.items():
        first.setdefault(ck, seg)
    prepare = getattr(engine, "prepare", None)
    if prepare is not None and any(from_cache(seg) is None for seg in first.values()):
        prepare()  # 例：付費配音先找 API key，找不到就一次講清楚，不要每段各報一次
    results: dict[str, SegmentAudio] = {}
    workers = max(1, min(jobs, getattr(engine, "parallel", jobs)))  # 付費服務有同時請求數的上限
    with ThreadPoolExecutor(max_workers=workers) as pool:
        done = dict(zip(first.values(), pool.map(one, list(first.values()))))
    for seg in segments:
        lead = first[keys[seg]]
        audio, cached = done[lead]
        results[seg] = audio if seg == lead else replace(audio, key=seg, sent_chars=0)
        if on_segment:
            on_segment(seg, cached or seg != lead)

    _prune(cache, (engine.name, model, voice, rate), set(keys.values()))
    return results


def _prune(cache: Path, current: tuple[str, str, str, str], keep: set[str]) -> None:
    """清掉同一個（配音層, 模型, 聲音, 語速）底下、現在用不到的快取（旁白改過之後的舊版本）。"""
    for meta in cache.glob("*.json"):
        if meta.stem in keep or meta.name == "timing.json":
            continue
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            same = (info.get("engine"), info.get("model", ""), info.get("voice"), info.get("rate")) == current
        except (ValueError, AttributeError):
            same = True
        if same:
            meta.unlink(missing_ok=True)
            meta.with_suffix(".wav").unlink(missing_ok=True)
            meta.with_name(meta.stem + ".trim.wav").unlink(missing_ok=True)
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

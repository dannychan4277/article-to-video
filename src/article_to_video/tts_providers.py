"""付費配音層：ElevenLabs、Google Cloud Text-to-Speech（REST，用標準庫 urllib，不加依賴）。

兩家都照配音層介面（narration.TTSEngine）：旁白段文字進，音檔＋每個字的時間點出（錨點換算用）。

- ElevenLabs：`POST /v1/text-to-speech/{voice_id}/with-timestamps`（header `xi-api-key`），
  回傳 base64 音檔＋逐字 alignment（characters／character_start_times_seconds／character_end_times_seconds）。
  alignment 的文字可能跟送出去的不一樣（正規化：數字唸法、全形半形標點、多出的空白），用 difflib 對回旁白原文。
  模型預設 eleven_multilingual_v2（settings 的 elevenlabs_model 可改）；語速 → voice_settings.speed（0.7～1.2）。
  音檔要 wav_24000（沒有 mp3 的編碼延遲，時間點對得準），再用 ffmpeg 轉成管線要的 48k 單聲道 WAV。
- Google：`POST /v1beta1/text:synthesize`（header `X-Goog-Api-Key`，key 不放網址），輸入 SSML，
  每個「詞」（中文一字一個、連續英數字一個）的前後插 `<mark>`，`enableTimePointing: ["SSML_MARK"]` 拿回每個 mark 的秒數。
  mark 不計費，`<speak>` 外殼算錢（照 Google 計價頁）。LINEAR16 回來就是 WAV。語速 → speakingRate（0.25～2.0）。
  cmn-TW 只有 Standard-A/B/C、Wavenet-A/B/C 六個聲音，預設 cmn-TW-Wavenet-A（女聲）。

API key 絕不進 repo、專案資料夾、settings.json、紀錄、審片頁、回報問題的 zip。找 key 的順序（第一個有的）：
  1. 環境變數 ELEVENLABS_API_KEY／GOOGLE_TTS_API_KEY
  2. <資料位置>/keys/elevenlabs.key／google-tts.key（資料位置見 user_settings.py，主 skill 傳 ${CLAUDE_PLUGIN_DATA}）
  3. ~/.config/article-to-video/elevenlabs.key／google-tts.key（有設 XDG_CONFIG_HOME 就用那裡）
檔案裡只放 key 一行。錯誤訊息裡出現 key 一律換成「<API key>」（scrub_secrets；回報問題的 zip 也過一次）。

測試不連網：網址可用環境變數 ARTICLE_TO_VIDEO_ELEVENLABS_URL、ARTICLE_TO_VIDEO_GOOGLE_TTS_URL 改（指到 127.0.0.1 的假伺服器）。
"""

from __future__ import annotations

import base64
import difflib
import json
import os
import re
import ssl
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from article_to_video.narration import NarrationError, Synthesis, WordTiming

__all__ = [
    "DEFAULT_ELEVENLABS_MODEL",
    "DEFAULT_ELEVENLABS_VOICE",
    "DEFAULT_GOOGLE_VOICE",
    "ELEVENLABS_URL_ENV",
    "GOOGLE_TTS_URL_ENV",
    "GOOGLE_TW_VOICES",
    "KEY_SPECS",
    "PAID_ENGINES",
    "ApiKey",
    "ElevenLabsTTS",
    "GoogleTTS",
    "align_to_text",
    "build_ssml",
    "find_api_key",
    "key_locations",
    "missing_key_message",
    "scrub_secrets",
    "speech_tokens",
]

ELEVENLABS_URL_ENV = "ARTICLE_TO_VIDEO_ELEVENLABS_URL"
GOOGLE_TTS_URL_ENV = "ARTICLE_TO_VIDEO_GOOGLE_TTS_URL"
ELEVENLABS_URL = "https://api.elevenlabs.io"
GOOGLE_TTS_URL = "https://texttospeech.googleapis.com"

DEFAULT_ELEVENLABS_MODEL = "eleven_multilingual_v2"
DEFAULT_ELEVENLABS_VOICE = "EXAVITQu4vr4xnSDxMaL"
"""Sarah（ElevenLabs 內建聲音，每個帳號都有、不用加進帳號）。台灣口音的要從聲音庫挑，見 docs/maintaining.md。"""
ELEVENLABS_OUTPUT = "wav_24000"
ELEVENLABS_PARALLEL = 2
"""同時送幾段給 ElevenLabs：免費方案同時最多 2 個請求，再多會被擋（429）。"""

DEFAULT_GOOGLE_VOICE = "cmn-TW-Wavenet-A"
GOOGLE_TW_VOICES = {
    "cmn-TW-Wavenet-A": "女聲（WaveNet）",
    "cmn-TW-Wavenet-B": "男聲（WaveNet）",
    "cmn-TW-Wavenet-C": "男聲（WaveNet）",
    "cmn-TW-Standard-A": "女聲（Standard，較便宜）",
    "cmn-TW-Standard-B": "男聲（Standard，較便宜）",
    "cmn-TW-Standard-C": "男聲（Standard，較便宜）",
}
GOOGLE_SSML_MAX_BYTES = 5000
"""Google 一次請求的輸入上限（位元組）。"""

TIMEOUT = 90
SECRET = "<API key>"


# ---------------------------------------------------------------- API key


@dataclass(frozen=True)
class KeySpec:
    provider: str
    label: str
    env: str
    file: str
    signup: str
    """去哪裡拿 key（給 Danny 看）。"""


KEY_SPECS = {
    "elevenlabs": KeySpec("elevenlabs", "ElevenLabs", "ELEVENLABS_API_KEY", "elevenlabs.key",
                          "ElevenLabs 網站 → Developers → API Keys（權限至少要 Text to Speech）"),
    "google": KeySpec("google", "Google Cloud Text-to-Speech", "GOOGLE_TTS_API_KEY", "google-tts.key",
                      "Google Cloud Console：開 Cloud Text-to-Speech API → 憑證 → 建立 API 金鑰（可限制只能用這個 API）"),
}
PAID_ENGINES = tuple(KEY_SPECS)


@dataclass(frozen=True)
class ApiKey:
    value: str = field(repr=False)
    source: str
    """給人看的來源：環境變數名稱，或 key 檔的路徑（不是 key 本身）。"""


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "article-to-video"


def key_locations(provider: str, data_dir: str | os.PathLike | None = None) -> list[tuple[str, Path | None]]:
    """找 key 的地方，照順序：[(環境變數名, None), ("", <資料位置>/keys/<檔>), ("", ~/.config/article-to-video/<檔>)]。"""
    from article_to_video.user_settings import resolve_data_dir  # 避免循環匯入

    spec = KEY_SPECS[provider]
    return [(spec.env, None), ("", resolve_data_dir(data_dir) / "keys" / spec.file), ("", _config_dir() / spec.file)]


def find_api_key(provider: str, data_dir: str | os.PathLike | None = None) -> ApiKey | None:
    for env, path in key_locations(provider, data_dir):
        if path is None:
            value = os.environ.get(env, "").strip()
            if value:
                return ApiKey(value, f"環境變數 {env}")
            continue
        try:
            value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if value:
            return ApiKey(value.splitlines()[0].strip(), str(path))
    return None


def missing_key_message(provider: str, data_dir: str | os.PathLike | None = None) -> str:
    spec = KEY_SPECS[provider]
    env, data_file, config_file = key_locations(provider, data_dir)
    return (f"要用 {spec.label} 配音，但找不到它的 API key。放在下面任一個地方（檔案裡只放 key 一行）：\n"
            f"- 環境變數 {env[0]}\n- {data_file[1]}\n- {config_file[1]}\n"
            f"去哪裡拿 key：{spec.signup}（這件事請 Danny 處理）。"
            "不想用付費配音：settings set --tts edge 換回免費的 edge 語音。")


def _all_known_keys(data_dir: str | os.PathLike | None = None) -> set[str]:
    keys = set()
    for provider in KEY_SPECS:
        for env, path in key_locations(provider, data_dir):
            try:
                value = os.environ.get(env, "") if path is None else path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            keys.update(v.strip() for v in value.splitlines() if len(v.strip()) >= 8)
    return keys


_KEY_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])sk_[0-9A-Za-z]{24,}"),  # ElevenLabs
    re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_\-]{30,}"),  # Google API key
)


def scrub_secrets(text: str, *, data_dir: str | os.PathLike | None = None, extra: Sequence[str] = ()) -> str:
    """把文字裡的 API key 換成「<API key>」：這台電腦設定的每一把 key（環境變數、key 檔）、extra，
    再加上長得像 ElevenLabs（sk_…）、Google（AIza…）key 的字串。"""
    for k in sorted({*_all_known_keys(data_dir), *(e for e in extra if len(e) >= 8)}, key=len, reverse=True):
        text = text.replace(k, SECRET)
    for p in _KEY_PATTERNS:
        text = p.sub(SECRET, text)
    return text


class _KeyHolder:
    """付費配音共用：第一次要用時才找 key（全部段落都有快取就不用 key），之後沿用。"""

    provider: str

    def __init__(self, data_dir: str | os.PathLike | None, api_key: str | None) -> None:
        self._data_dir = data_dir
        self._key = api_key
        self._lock = threading.Lock()

    def prepare(self) -> None:
        self._api_key()

    def _api_key(self) -> str:
        with self._lock:
            if self._key is None:
                found = find_api_key(self.provider, self._data_dir)
                if found is None:
                    raise NarrationError(missing_key_message(self.provider, self._data_dir))
                self._key = found.value
            return self._key


# ---------------------------------------------------------------- 文字 → 詞 → 時間


def _is_ascii_alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def speech_tokens(text: str) -> list[tuple[int, int]]:
    """唸出來的「詞」（字元範圍 [起, 訖)）：連續英數字算一個（中間的小數點、千分位逗號也算，例 4.58、2,000），
    其他每個不是空白、不是標點的字自己一個（中文一字一個）。錨點對詞的開頭、唸完就是對這些範圍。"""
    out: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if _is_ascii_alnum(ch):
            j = i + 1
            while j < n and (_is_ascii_alnum(text[j]) or (
                    text[j] in ".," and text[j - 1].isdigit() and j + 1 < n and text[j + 1].isascii()
                    and text[j + 1].isdigit())):
                j += 1
            out.append((i, j))
            i = j
            continue
        if not ch.isspace() and not unicodedata.category(ch).startswith("P"):
            out.append((i, i + 1))
        i += 1
    return out


def _fold(ch: str) -> str:
    """比對用：全形半形、大小寫當成一樣（只在換完還是一個字的時候換，位置才對得回去）。"""
    k = unicodedata.normalize("NFKC", ch).lower()
    return k if len(k) == 1 else ch


def _words(text: str, starts: list[float | None], ends: list[float | None]) -> list[WordTiming]:
    """每個字的起訖秒數（有缺的用前後補）→ 每個詞的 WordTiming。"""
    n = len(text)
    known = [i for i in range(n) if starts[i] is not None]
    if not known:
        raise NarrationError("配音服務回來的時間點對不回旁白文字，錨點沒辦法對。")
    prev_end: float | None = None
    for i in range(n):  # 對不回的字（例：服務端吃掉的標點）：零長度，放在前一個字唸完（或下一個字開始）的地方
        if starts[i] is None:
            nxt = next((starts[j] for j in range(i + 1, n) if starts[j] is not None), None)
            t = prev_end if prev_end is not None else nxt
            starts[i] = ends[i] = t
        prev_end = ends[i]
    return [WordTiming(a, b, round(min(starts[a:b]), 6), round(max(ends[a:b]), 6)) for a, b in speech_tokens(text)]


def align_to_text(text: str, characters: Sequence[str], start_times: Sequence[float],
                  end_times: Sequence[float]) -> list[WordTiming]:
    """ElevenLabs 的逐字 alignment → 旁白原文每個詞的時間。

    alignment 的字可能跟原文不同（正規化）：用 difflib 對齊；一樣的字直接用它的時間，被換掉的一段（例 4.58 ↔ 四點五八）
    把那段時間平均分給原文那幾個字，多出來的字（空白）不管，少掉的字零長度。
    """
    a_chars: list[str] = []
    a_start: list[float] = []
    a_end: list[float] = []
    for c, s, e in zip(characters, start_times, end_times):
        for ch in c or "":
            a_chars.append(_fold(ch))
            a_start.append(float(s))
            a_end.append(float(e))
    starts: list[float | None] = [None] * len(text)
    ends: list[float | None] = [None] * len(text)
    sm = difflib.SequenceMatcher(None, [_fold(c) for c in text], a_chars, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                starts[i1 + k], ends[i1 + k] = a_start[j1 + k], a_end[j1 + k]
        elif tag == "replace":
            t0, t1, m = a_start[j1], a_end[j2 - 1], i2 - i1
            for k in range(m):
                starts[i1 + k], ends[i1 + k] = t0 + (t1 - t0) * k / m, t0 + (t1 - t0) * (k + 1) / m
    return _words(text, starts, ends)


def build_ssml(text: str) -> tuple[str, dict[str, int]]:
    """旁白 → SSML：每個詞的前後各插一個 <mark>（名字 c<字元位置>），連續英數字中間不插（不然 4.58 會唸壞）。
    回傳 (ssml, {mark 名字: 字元位置})。"""
    spots = sorted({p for a, b in speech_tokens(text) for p in (a, b)})
    names = {f"c{p}": p for p in spots}
    at = set(spots)
    parts = ["<speak>"]
    for i, ch in enumerate(text):
        if i in at:
            parts.append(f'<mark name="c{i}"/>')
        parts.append(escape(ch))
    if len(text) in at:
        parts.append(f'<mark name="c{len(text)}"/>')
    parts.append("</speak>")
    return "".join(parts), names


def words_from_marks(text: str, times: dict[int, float]) -> list[WordTiming]:
    """{字元位置: 秒數}（mark 的時間）→ 每個詞的 WordTiming：詞從它開頭那個 mark 唸到結尾那個 mark。
    服務端漏掉的 mark 用前後的 mark 照字元位置內插。"""
    if not times:
        raise NarrationError("Google 有回音檔，但沒有時間點（timepoints），錨點沒辦法對。")
    known = sorted(times.items())

    def at(p: int) -> float:
        if p in times:
            return times[p]
        before = [(q, t) for q, t in known if q < p]
        after = [(q, t) for q, t in known if q > p]
        if before and after:
            (q0, t0), (q1, t1) = before[-1], after[0]
            return t0 + (t1 - t0) * (p - q0) / (q1 - q0)
        return (before[-1] if before else after[0])[1]

    return [WordTiming(a, b, round(at(a), 6), round(at(b), 6)) for a, b in speech_tokens(text)]


# ---------------------------------------------------------------- HTTP


def _rate_percent(rate: str) -> int:
    m = re.fullmatch(r"([+-]\d{1,3})%", rate.strip())
    if not m:
        raise NarrationError(f"語速「{rate}」格式不對，要寫成像 +5%、-10% 這樣")
    return int(m.group(1))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _error_detail(raw: bytes) -> tuple[str, str]:
    """服務端錯誤的 (狀態代碼, 訊息)：ElevenLabs 是 {"detail": {...}}，Google 是 {"error": {...}}。"""
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return "", raw.decode("utf-8", "replace").strip()[:300]
    if isinstance(data, dict):
        d = data.get("detail") if "detail" in data else data.get("error")
        if isinstance(d, dict):
            return str(d.get("status") or ""), str(d.get("message") or "")[:300]
        if isinstance(d, list) and d:  # 422 驗證錯誤
            return "invalid_request", "; ".join(str(x.get("msg", x)) for x in d if isinstance(x, dict))[:300]
        if isinstance(d, str):
            return "", d[:300]
    return "", str(data)[:300]


def _post_json(url: str, body: dict, headers: dict[str, str], *, who: str, key: str,
               explain: Callable[[int, str, str], str]) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "article-to-video", **headers},
    )
    import certifi

    tls = ssl.create_default_context(cafile=certifi.where())  # uv 帶的 Python 不一定找得到 macOS 的根憑證
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=tls) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        status, message = _error_detail(e.read())
        raise NarrationError(scrub_secrets(explain(e.code, status, message), extra=[key])) from None
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, TimeoutError):
            raise NarrationError(f"{who} 等了 {TIMEOUT} 秒沒有回應（網路很慢或斷線），晚點再試") from None
        raise NarrationError(scrub_secrets(f"連不上 {who}（可能沒有網路；技術細節：{reason}）", extra=[key])) from None
    try:
        data = json.loads(raw)
    except ValueError:
        raise NarrationError(f"{who} 回來的不是 JSON（可能服務改版了），回報給 Danny") from None
    if not isinstance(data, dict):
        raise NarrationError(f"{who} 回來的格式看不懂（可能服務改版了），回報給 Danny")
    return data


def _base_url(env: str, default: str) -> str:
    return (os.environ.get(env) or default).rstrip("/")


# ---------------------------------------------------------------- ElevenLabs


def _explain_elevenlabs(voice: str, model: str) -> Callable[[int, str, str], str]:
    def explain(code: int, status: str, message: str) -> str:
        why = f"（{message}）" if message else ""
        if code == 401 or status in ("invalid_api_key", "missing_permissions"):
            return (f"ElevenLabs 不收這把 API key{why}：key 打錯、被刪掉，或沒有 Text to Speech 權限。"
                    "請 Danny 到 ElevenLabs 網站重新產生一把，換掉原本放的那份。")
        if code == 402 or status in ("quota_exceeded", "payment_required"):
            return f"ElevenLabs 的點數用完了{why}：到 ElevenLabs 網站看方案與剩餘點數。"
        if code == 404 or status == "voice_not_found":
            return (f"ElevenLabs 找不到聲音「{voice}」{why}：聲音庫裡的聲音要先在 ElevenLabs 網站加進帳號"
                    "（My Voices），或用 settings set --elevenlabs-voice <聲音代號> 換一個。")
        if status == "model_not_found" or "model" in status:
            return f"ElevenLabs 沒有模型「{model}」{why}：settings set --elevenlabs-model eleven_multilingual_v2 換回預設。"
        if code == 429:
            return f"ElevenLabs 說請求太多了{why}：等一分鐘再試（已經配好的段落不會重配）。"
        return f"ElevenLabs 回應錯誤（HTTP {code}{'，' + status if status else ''}）{why}"

    return explain


class ElevenLabsTTS(_KeyHolder):
    """ElevenLabs（付費）。voice＝聲音代號（voice_id）；model＝模型（預設 eleven_multilingual_v2）。"""

    name = "elevenlabs"
    provider = "elevenlabs"
    parallel = ELEVENLABS_PARALLEL

    def __init__(self, model: str | None = None, data_dir: str | os.PathLike | None = None,
                 api_key: str | None = None) -> None:
        super().__init__(data_dir, api_key)
        self.model = model or DEFAULT_ELEVENLABS_MODEL

    def synthesize(self, text: str, *, voice: str, rate: str, out_stem: Path) -> Synthesis:
        key = self._api_key()
        speed = round(_clamp(1 + _rate_percent(rate) / 100, 0.7, 1.2), 3)
        url = (f"{_base_url(ELEVENLABS_URL_ENV, ELEVENLABS_URL)}/v1/text-to-speech/"
               f"{urllib.parse.quote(voice, safe='')}/with-timestamps?output_format={ELEVENLABS_OUTPUT}")
        body = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": True,
                               "speed": speed},
        }
        data = _post_json(url, body, {"xi-api-key": key}, who="ElevenLabs", key=key,
                          explain=_explain_elevenlabs(voice, self.model))
        try:
            audio = base64.b64decode(data["audio_base64"])
            al = data.get("alignment") or data.get("normalized_alignment")
            chars, starts, ends = (al["characters"], al["character_start_times_seconds"],
                                   al["character_end_times_seconds"])
        except (KeyError, TypeError, ValueError):
            raise NarrationError("ElevenLabs 回來的資料少了音檔或時間點（可能服務改版了），回報給 Danny") from None
        if not audio:
            raise NarrationError("ElevenLabs 沒有回傳任何聲音。")
        path = out_stem.with_suffix(".wav")
        path.write_bytes(audio)
        return Synthesis(path, align_to_text(text, chars, starts, ends))


# ---------------------------------------------------------------- Google


def _explain_google(voice: str) -> Callable[[int, str, str], str]:
    def explain(code: int, status: str, message: str) -> str:
        why = f"（{message}）" if message else ""
        low = message.lower()
        if "api key" in low and ("not valid" in low or "invalid" in low or "expired" in low):
            return f"Google 不收這把 API key{why}：key 打錯或被刪掉了，請 Danny 到 Google Cloud Console 重新建一把。"
        if code == 403 or status == "PERMISSION_DENIED":
            return (f"Google 拒絕這把 API key{why}：通常是 key 所在的專案還沒啟用 Cloud Text-to-Speech API、"
                    "沒綁帳單，或 key 限制了不能用這個 API。請 Danny 到 Google Cloud Console 檢查。")
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            return f"Google 說用量超過上限{why}：等一分鐘再試（已經配好的段落不會重配）。"
        if code == 400 and "voice" in low:
            return f"Google 沒有聲音「{voice}」{why}：settings set --google-voice cmn-TW-Wavenet-A 換回預設。"
        return f"Google 回應錯誤（HTTP {code}{'，' + status if status else ''}）{why}"

    return explain


class GoogleTTS(_KeyHolder):
    """Google Cloud Text-to-Speech（付費、每月有免費額度）。voice＝聲音名稱（例 cmn-TW-Wavenet-A）。"""

    name = "google"
    provider = "google"
    model = ""

    def __init__(self, data_dir: str | os.PathLike | None = None, api_key: str | None = None) -> None:
        super().__init__(data_dir, api_key)

    def synthesize(self, text: str, *, voice: str, rate: str, out_stem: Path) -> Synthesis:
        key = self._api_key()
        ssml, marks = build_ssml(text)
        if len(ssml.encode("utf-8")) > GOOGLE_SSML_MAX_BYTES:
            raise NarrationError(f"這段旁白太長了（{len(text)} 字），Google 一次唸不完：拆成兩段旁白再配。")
        speaking_rate = round(_clamp(1 + _rate_percent(rate) / 100, 0.25, 2.0), 3)
        body = {
            "input": {"ssml": ssml},
            "voice": {"languageCode": "-".join(voice.split("-")[:2]), "name": voice},
            "audioConfig": {"audioEncoding": "LINEAR16", "speakingRate": speaking_rate},
            "enableTimePointing": ["SSML_MARK"],
        }
        data = _post_json(f"{_base_url(GOOGLE_TTS_URL_ENV, GOOGLE_TTS_URL)}/v1beta1/text:synthesize", body,
                          {"X-Goog-Api-Key": key}, who="Google", key=key, explain=_explain_google(voice))
        try:
            audio = base64.b64decode(data["audioContent"])
            points = data.get("timepoints") or []
            times = {marks[p["markName"]]: float(p["timeSeconds"]) for p in points if p.get("markName") in marks}
        except (KeyError, TypeError, ValueError):
            raise NarrationError("Google 回來的資料少了音檔或時間點（可能服務改版了），回報給 Danny") from None
        if not audio:
            raise NarrationError("Google 沒有回傳任何聲音。")
        path = out_stem.with_suffix(".wav")
        path.write_bytes(audio)
        billed = len(re.sub(r'<mark name="[^"]*"/>', "", ssml))  # mark 不計費，<speak> 外殼算
        return Synthesis(path, words_from_marks(text, times), chars=billed)

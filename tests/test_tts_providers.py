"""付費配音層：ElevenLabs、Google Cloud TTS。

切點：narrate()＋get_tts_engine()（配音層介面）、指令列（narrate／build／settings／check-env／report-issue）。
不連網：兩家的 API 都換成 127.0.0.1 的假伺服器（網址用環境變數指過去，子行程也吃得到）。

假伺服器的時間規則（測試照這份規則自己算預期值）：每個字（含標點）0.1 秒、從 0.05 秒開始，
第 i 個字 [0.05＋0.1i, 0.15＋0.1i)；音檔長度＝0.05＋0.1×字數＋0.3 秒（靜音）。
Google 沒有逐字時間，用 SSML <mark>：位置在第 i 個字前面的 mark，時間＝0.05＋0.1i。
"""

from __future__ import annotations

import base64
import io
import json
import re
import threading
import wave
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import unescape
from pathlib import Path

import pytest

from article_to_video.narration import WordTiming, get_tts_engine, narrate

FAKE_KEY = "fake-elevenlabs-key-7f3a9c2e"
FAKE_GOOGLE_KEY = "fake-google-key-41b8d0aa"
T0, STEP, TAIL = 0.05, 0.1, 0.3


def _wav(seconds: float, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * round(seconds * rate))
    return buf.getvalue()


@dataclass
class FakeTTSServer:
    url: str = ""
    requests: list[dict] = field(default_factory=list)
    status: int | None = None
    """設了就每個配音請求都回這個 HTTP 狀態（body 照抄收到的 key，模擬服務端把 key 印出來）。"""
    normalize: dict[str, str] = field(default_factory=dict)
    """ElevenLabs 回的 alignment 文字：照這張表替換過（模擬正規化：數字唸法、標點、多出的空白）。"""

    def synth(self, provider: str) -> list[dict]:
        return [r for r in self.requests if r["provider"] == provider]


@pytest.fixture
def tts_server(monkeypatch):
    state = FakeTTSServer()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            provider = "google" if self.path.startswith("/v1beta1/") else "elevenlabs"
            state.requests.append({"provider": provider, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                   "body": body})
            if state.status is not None:
                echoed = self.headers.get("xi-api-key") or self.headers.get("X-Goog-Api-Key")
                return self._send(state.status, {"detail": {"status": "invalid_api_key",
                                                            "message": f"Invalid API key: {echoed}"}})
            if provider == "elevenlabs":
                text = body["text"]
                for a, b in state.normalize.items():
                    text = text.replace(a, b)
                n = len(text)
                return self._send(200, {
                    "audio_base64": base64.b64encode(_wav(T0 + STEP * n + TAIL)).decode(),
                    "alignment": {"characters": list(text),
                                  "character_start_times_seconds": [round(T0 + STEP * i, 3) for i in range(n)],
                                  "character_end_times_seconds": [round(T0 + STEP * (i + 1), 3) for i in range(n)]},
                    "normalized_alignment": None,
                })
            ssml = body["input"]["ssml"]
            inner = re.fullmatch(r"<speak>(.*)</speak>", ssml, flags=re.S).group(1)
            points, pos = [], 0
            for part in re.split(r"(<mark name=\"[^\"]+\"/>)", inner):
                m = re.fullmatch(r"<mark name=\"([^\"]+)\"/>", part)
                if m:
                    points.append({"markName": m.group(1), "timeSeconds": round(T0 + STEP * pos, 3)})
                else:
                    pos += len(unescape(part))
            return self._send(200, {"audioContent": base64.b64encode(_wav(T0 + STEP * pos + TAIL)).decode(),
                                    "timepoints": points, "audioConfig": {}})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("ARTICLE_TO_VIDEO_ELEVENLABS_URL", state.url)
    monkeypatch.setenv("ARTICLE_TO_VIDEO_GOOGLE_TTS_URL", state.url)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def _rule_word(i: int) -> WordTiming:
    """假伺服器規則下，第 i 個字自己一個詞的時間。"""
    return WordTiming(i, i + 1, round(T0 + STEP * i, 6), round(T0 + STEP * (i + 1), 6))


# ---------------------------------------------------------------- ElevenLabs


def test_elevenlabs_voices_each_segment_with_per_character_times(tmp_path, tts_server, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    text = "殖利率，走高。"
    engine = get_tts_engine("elevenlabs", data_dir=tmp_path / "data")
    out = narrate({"s01": text}, engine=engine, voice="VoiceId1234567890abc", rate="+5%", cache_dir=tmp_path / "cache")

    [req] = tts_server.synth("elevenlabs")
    assert req["path"].startswith("/v1/text-to-speech/VoiceId1234567890abc/with-timestamps")
    assert req["headers"]["xi-api-key"] == FAKE_KEY
    assert FAKE_KEY not in req["path"]
    assert req["body"]["text"] == text
    assert req["body"]["model_id"] == "eleven_multilingual_v2"
    assert req["body"]["voice_settings"]["speed"] == pytest.approx(1.05)

    a = out["s01"]
    # 殖0 利1 率2 ，3 走4 高5 。6：每個字自己一個詞，標點不算詞
    assert list(a.words) == [_rule_word(0), _rule_word(1), _rule_word(2), _rule_word(4), _rule_word(5)]
    assert a.duration == pytest.approx(T0 + STEP * 7 + TAIL, abs=0.002)
    with wave.open(str(a.audio)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (48000, 1, 2)
    assert a.sent_chars == len(text)


def test_elevenlabs_alignment_that_differs_from_the_narration_still_lands_on_each_character(
        tmp_path, tts_server, monkeypatch):
    """ElevenLabs 回的 alignment 文字可能被正規化過（數字唸法、半形標點、多一個空白），每個字還是要對到時間。"""
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    text = "利率4.58%，走高"  # 利0 率1 4.58 是 2–5 %6 ，7 走8 高9
    tts_server.normalize = {"4.58%": "四點五八趴", "，": ", "}  # 伺服器那邊：利0 率1 四2 點3 五4 八5 趴6 ,7 ␠8 走9 高10
    out = narrate({"s01": text}, engine=get_tts_engine("elevenlabs"), voice="VoiceId1234567890abc", rate="+0%",
                  cache_dir=tmp_path)
    assert list(out["s01"].words) == [
        _rule_word(0),  # 利
        _rule_word(1),  # 率
        WordTiming(2, 6, 0.25, 0.65),  # 「4.58」一個詞＝「四點五八趴」五個字平均分，前四個字（% 是標點）
        WordTiming(8, 9, 0.95, 1.05),  # 走＝伺服器第 9 個字
        WordTiming(9, 10, 1.05, 1.15),  # 高
    ]


# ---------------------------------------------------------------- Google


def test_google_voices_with_ssml_marks_and_gets_per_character_times(tmp_path, tts_server, monkeypatch):
    monkeypatch.setenv("GOOGLE_TTS_API_KEY", FAKE_GOOGLE_KEY)
    engine = get_tts_engine("google")
    out = narrate({"s01": "殖利率，走高。", "s02": "利率4.58，走高"}, engine=engine, voice="cmn-TW-Wavenet-A",
                  rate="+5%", cache_dir=tmp_path)

    reqs = {r["body"]["input"]["ssml"]: r for r in tts_server.synth("google")}
    assert len(reqs) == 2
    for ssml, req in reqs.items():
        assert req["path"] == "/v1beta1/text:synthesize"  # key 不放網址
        assert req["headers"]["x-goog-api-key"] == FAKE_GOOGLE_KEY
        assert req["body"]["enableTimePointing"] == ["SSML_MARK"]
        assert req["body"]["voice"] == {"languageCode": "cmn-TW", "name": "cmn-TW-Wavenet-A"}
        assert req["body"]["audioConfig"]["audioEncoding"] == "LINEAR16"
        assert req["body"]["audioConfig"]["speakingRate"] == pytest.approx(1.05)
    assert any("4.58" in s for s in reqs), "數字中間不能插 mark（Google 會把 4.58 唸壞）"

    a = out["s01"]
    assert list(a.words) == [_rule_word(0), _rule_word(1), _rule_word(2), _rule_word(4), _rule_word(5)]
    assert a.duration == pytest.approx(T0 + STEP * 7 + TAIL, abs=0.002)
    with wave.open(str(a.audio)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (48000, 1, 2)
    assert a.sent_chars == len("<speak>殖利率，走高。</speak>")  # mark 不計費，<speak> 外殼算錢

    b = out["s02"]  # 利0 率1 4.58 是 2–5 ，6 走7 高8
    assert list(b.words) == [_rule_word(0), _rule_word(1), WordTiming(2, 6, 0.25, 0.65), _rule_word(7), _rule_word(8)]


# ---------------------------------------------------------------- API key


@pytest.mark.parametrize("provider, env, file", [("elevenlabs", "ELEVENLABS_API_KEY", "elevenlabs.key"),
                                                 ("google", "GOOGLE_TTS_API_KEY", "google-tts.key")])
def test_api_key_comes_from_env_then_data_dir_then_user_config(tmp_path, tts_server, monkeypatch, provider, env, file):
    from article_to_video.narration import NarrationError

    data, config = tmp_path / "plugin-data", tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    (data / "keys").mkdir(parents=True)
    (config / "article-to-video").mkdir(parents=True)
    voice = "cmn-TW-Wavenet-A" if provider == "google" else "VoiceId1234567890abc"
    header = "x-goog-api-key" if provider == "google" else "xi-api-key"

    def used_key(text: str) -> str:
        narrate({"s01": text}, engine=get_tts_engine(provider, data_dir=data), voice=voice, rate="+5%",
                cache_dir=tmp_path / "cache")
        return tts_server.synth(provider)[-1]["headers"][header]

    (config / "article-to-video" / file).write_text("key-from-user-config\n", encoding="utf-8")
    assert used_key("一。") == "key-from-user-config"
    (data / "keys" / file).write_text("  key-from-data-dir \n", encoding="utf-8")
    assert used_key("二。") == "key-from-data-dir"
    monkeypatch.setenv(env, "key-from-env")
    assert used_key("三。") == "key-from-env"

    monkeypatch.delenv(env)
    (data / "keys" / file).unlink()
    (config / "article-to-video" / file).unlink()
    asked = len(tts_server.requests)
    with pytest.raises(NarrationError) as e:
        used_key("四。")
    msg = str(e.value)
    assert "找不到" in msg and "API key" in msg
    for where in (env, str(data / "keys" / file), str(config / "article-to-video" / file)):
        assert where in msg  # 三個放 key 的地方都講
    assert "--tts edge" in msg  # 不想付費的退路
    assert len(tts_server.requests) == asked  # 沒有 key 就不送出去

    # 全部段落都有快取：不需要 key（例：維護者重現回報的問題）
    narrate({"s01": "三。"}, engine=get_tts_engine(provider, data_dir=data), voice=voice, rate="+5%",
            cache_dir=tmp_path / "cache")

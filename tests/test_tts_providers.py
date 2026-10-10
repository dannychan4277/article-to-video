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

from conftest import MINI_PROJECT

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


# ---------------------------------------------------------------- 設定、指令列、花了多少字


def _project(tmp_path: Path, settings: dict | None = None) -> Path:
    root = tmp_path / "專案"
    root.mkdir()
    (root / "narration.json").write_text((MINI_PROJECT / "narration.json").read_text(encoding="utf-8"),
                                         encoding="utf-8")
    if settings is not None:
        (root / "settings.json").write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    return root


def _cli(capsys, *argv: object) -> str:
    from article_to_video import cli

    capsys.readouterr()
    code = cli.main([str(a) for a in argv])
    out = capsys.readouterr()
    assert code == 0, out.out + out.err
    return out.out


def _sent(out: str) -> int:
    m = re.search(r"送出 (\d+) 字", out)
    assert m, out
    return int(m.group(1))


def test_provider_is_picked_in_settings_and_switching_back_costs_nothing(tmp_path, tts_server, monkeypatch, capsys):
    """settings set 選配音層與它自己的聲音 → 新專案快照帶過去；這支片換到 Google 全部重配一次，換回 ElevenLabs 用舊快取、
    送出 0 字。每次配音都印這次送出幾個字（付費配音照字數算錢）。"""
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GOOGLE_TTS_API_KEY", FAKE_GOOGLE_KEY)
    data = tmp_path / "plugin-data"
    _cli(capsys, "settings", "set", "--data-dir", data, "--channel", "小明", "--tts", "elevenlabs",
         "--elevenlabs-voice", "VoiceId1234567890abc")
    root = _project(tmp_path)
    texts = json.loads((MINI_PROJECT / "narration.json").read_text(encoding="utf-8"))["segments"].values()
    n_chars = sum(len(t) for t in texts)  # 兩段旁白的字數

    out = _cli(capsys, "narrate", root, "--data-dir", data)
    snap = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    assert (snap["tts"], snap["elevenlabs_voice"]) == ("elevenlabs", "VoiceId1234567890abc")
    assert snap["google_voice"] == "cmn-TW-Wavenet-A"  # 每家有自己的聲音
    assert [r["path"].split("/")[3] for r in tts_server.synth("elevenlabs")] == ["VoiceId1234567890abc"] * 2
    assert _sent(out) == n_chars
    assert _sent(_cli(capsys, "narrate", root, "--data-dir", data)) == 0  # 沒改：全部用快取

    _cli(capsys, "settings", "set", "--project", root, "--tts", "google")
    out = _cli(capsys, "narrate", root, "--data-dir", data)
    assert len(tts_server.synth("google")) == 2
    assert _sent(out) == n_chars + 2 * len("<speak></speak>")

    _cli(capsys, "settings", "set", "--project", root, "--tts", "elevenlabs")
    assert _sent(_cli(capsys, "narrate", root, "--data-dir", data)) == 0
    assert len(tts_server.synth("elevenlabs")) == 2  # 換回來沒有重配

    _cli(capsys, "settings", "set", "--project", root, "--elevenlabs-model", "eleven_v3")
    assert _sent(_cli(capsys, "narrate", root, "--data-dir", data)) == n_chars  # 換模型就重配
    assert tts_server.synth("elevenlabs")[-1]["body"]["model_id"] == "eleven_v3"

    assert _sent(_cli(capsys, "narrate", root, "--data-dir", data, "--tts", "fake")) == n_chars  # --tts 蓋過設定
    assert json.loads(_cli(capsys, "settings", "show", "--json", "--data-dir", data))["tts"] == "elevenlabs"


def test_a_project_made_before_paid_voices_keeps_its_edge_voice(tmp_path, capsys):
    """設定快照是付費配音出現之前做的（有頻道名、沒有 tts）：使用者後來改用 ElevenLabs，舊片重建照樣是 edge。"""
    data = tmp_path / "plugin-data"
    _cli(capsys, "settings", "set", "--data-dir", data, "--channel", "小明", "--tts", "elevenlabs")
    old = _project(tmp_path, {"channel": "小明", "logo": None, "brand_color": None, "voice": "zh-TW-HsiaoYuNeural",
                              "rate": "+5%", "music": "calm", "aspect": "16x9"})
    _cli(capsys, "narrate", old, "--data-dir", data, "--tts", "fake")
    snap = json.loads((old / "settings.json").read_text(encoding="utf-8"))
    assert snap["tts"] == "edge"
    assert snap["voice"] == "zh-TW-HsiaoYuNeural"


# ---------------------------------------------------------------- key 不外流


def _files_containing(root: Path, secret: str) -> list[str]:
    return [str(p) for p in root.rglob("*") if p.is_file() and secret.encode() in p.read_bytes()]


def test_the_api_key_never_reaches_logs_project_files_or_the_issue_report(tmp_path, tts_server, capsys, monkeypatch):
    """key 放在資料位置的 keys/ 裡；服務端回錯誤時連 key 一起印出來（最壞情況）：錯誤訊息、last-run 紀錄、專案裡任何檔案、
    回報問題的 zip、環境檢查都不能出現 key。之後配音成功，回報的 zip 標明用 ElevenLabs 的時間點（維護者不用 key 就能重現）。"""
    import shutil
    import zipfile

    from article_to_video import cli

    data = tmp_path / "plugin-data"
    (data / "keys").mkdir(parents=True)
    (data / "keys" / "elevenlabs.key").write_text(FAKE_KEY + "\n", encoding="utf-8")
    _cli(capsys, "settings", "set", "--data-dir", data, "--channel", "小明", "--tts", "elevenlabs")
    root = tmp_path / "專案"
    shutil.copytree(MINI_PROJECT, root)
    (root / "settings.json").unlink()

    tts_server.status = 401
    capsys.readouterr()
    assert cli.main(["build", str(root), "--data-dir", str(data), "--no-open"]) == 1
    out = capsys.readouterr()
    assert "ElevenLabs 不收這把 API key" in out.err
    assert FAKE_KEY in json.dumps(tts_server.requests[-1]["headers"])  # 真的有送 key（服務端也真的印回來了）
    assert FAKE_KEY not in out.out + out.err

    _cli(capsys, "report-issue", root, "--out-dir", tmp_path / "失敗之後")
    tts_server.status = None
    _cli(capsys, "narrate", root, "--data-dir", data)
    # 作者把 key 貼進問題描述（環境變數裡的 key＝這台設定的 key；另一把長得像 ElevenLabs key 的也要遮）
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    other = "sk_" + "a1" * 24
    _cli(capsys, "report-issue", root, "--out-dir", tmp_path / "成功之後", "--note", f"key 是 {FAKE_KEY}，舊的是 {other}")

    assert _files_containing(root, FAKE_KEY) == []
    zips = [*(tmp_path / "失敗之後").glob("*.zip"), *(tmp_path / "成功之後").glob("*.zip")]
    assert len(zips) == 2
    for zpath in zips:
        with zipfile.ZipFile(zpath) as z:
            leaked = [n for n in z.namelist() if FAKE_KEY.encode() in z.read(n) or other.encode() in z.read(n)]
            assert leaked == []
    with zipfile.ZipFile(zips[-1]) as z:
        assert json.loads(z.read("report.json"))["tts"] == "elevenlabs"


# ---------------------------------------------------------------- 試聽


def test_voice_samples_for_paid_providers_use_their_own_voices(tmp_path, tts_server, monkeypatch, capsys):
    """試聽：Google 給六個台灣口音的聲音各一個；ElevenLabs 給設定的聲音，或 --voice 指定的幾個（挑聲音用）。"""
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GOOGLE_TTS_API_KEY", FAKE_GOOGLE_KEY)
    data, out = tmp_path / "plugin-data", tmp_path / "試聽"
    text = "試聽一下。"

    printed = _cli(capsys, "samples", "voice", "--tts", "google", "--out", out, "--text", text, "--data-dir", data)
    assert sorted(p.name for p in out.glob("*.wav")) == sorted(
        f"voice-cmn-TW-{kind}-{x}.wav" for kind in ("Standard", "Wavenet") for x in "ABC")
    assert _sent(printed) == 6 * len(f"<speak>{text}</speak>")

    _cli(capsys, "settings", "set", "--data-dir", data, "--elevenlabs-voice", "SetVoice1234567890ab")
    _cli(capsys, "samples", "voice", "--tts", "elevenlabs", "--out", out, "--text", text, "--data-dir", data)
    assert (out / "voice-SetVoice1234567890ab.wav").is_file()
    _cli(capsys, "samples", "voice", "--tts", "elevenlabs", "--out", out, "--text", text, "--data-dir", data,
         "--voice", "TryVoiceA1234567890a", "--voice", "TryVoiceB1234567890b")
    assert (out / "voice-TryVoiceA1234567890a.wav").is_file() and (out / "voice-TryVoiceB1234567890b.wav").is_file()
    assert [r["path"].split("/")[3] for r in tts_server.synth("elevenlabs")] == [
        "SetVoice1234567890ab", "TryVoiceA1234567890a", "TryVoiceB1234567890b"]


def test_voice_samples_take_the_same_voice_names_as_settings(tmp_path, tts_server, monkeypatch, capsys):
    """示範時 `samples voice --voice 曉臻` 直接把「曉臻」交給 edge-tts → Invalid voice。試聽的 --voice 收的寫法要跟
    settings set 一樣：edge 收中文名、Google 收 Wavenet-B 這種簡寫；寫錯的用繁中說有哪些可以挑。"""
    import edge_tts

    from test_narration import _mp3_bytes

    audio = _mp3_bytes(tmp_path, 1.0)
    asked: list[str] = []

    class EdgeLike:  # 跟 edge-tts 一樣：聲音不是代號就在建構時丟 ValueError
        def __init__(self, text, voice, **kw):
            if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}-\w+Neural", voice):
                raise ValueError(f"Invalid voice '{voice}'.")
            asked.append(voice)
            self.text = text

        async def stream(self):
            yield {"type": "audio", "data": audio}
            yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 2_000_000, "text": self.text[:1]}

    monkeypatch.setattr(edge_tts, "Communicate", EdgeLike)
    monkeypatch.setenv("GOOGLE_TTS_API_KEY", FAKE_GOOGLE_KEY)
    data, out = tmp_path / "plugin-data", tmp_path / "試聽"

    printed = _cli(capsys, "samples", "voice", "--out", out, "--text", "試聽。", "--data-dir", data,
                   "--voice", "曉臻", "--voice", "雲哲")
    assert asked == ["zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural"]
    assert (out / "voice-zh-TW-HsiaoChenNeural.wav").is_file() and "曉臻（zh-TW-HsiaoChenNeural）" in printed

    _cli(capsys, "samples", "voice", "--tts", "google", "--out", out, "--text", "試聽。", "--data-dir", data,
         "--voice", "wavenet-b")
    assert [r["body"]["voice"]["name"] for r in tts_server.synth("google")] == ["cmn-TW-Wavenet-B"]

    from article_to_video import cli

    capsys.readouterr()
    assert cli.main(["samples", "voice", "--out", str(out), "--data-dir", str(data), "--voice", "小美"]) == 1
    err = capsys.readouterr().err
    assert "小美" in err and "曉臻" in err and "Traceback" not in err


# ---------------------------------------------------------------- 檢查環境


def test_check_env_says_whether_the_paid_voice_has_a_key_and_only_tries_it_when_asked(tmp_path, tts_server, capsys):
    from article_to_video import cli
    from article_to_video.environment import tts_check

    data = tmp_path / "plugin-data"
    _cli(capsys, "settings", "set", "--data-dir", data, "--channel", "小明", "--tts", "edge")
    assert tts_check(data)().ok is None  # edge 免費、不用 key：只是資訊

    said = _cli(capsys, "settings", "set", "--data-dir", data, "--tts", "google")
    assert "找不到" in said and str(data / "keys" / "google-tts.key") in said  # 換到付費配音當下就提醒 key 還沒放
    c = tts_check(data)()
    assert c.ok is False and "API key" in c.value
    assert str(data / "keys" / "google-tts.key") in c.fix  # 修法講 key 放哪裡

    (data / "keys").mkdir()
    (data / "keys" / "google-tts.key").write_text(FAKE_GOOGLE_KEY, encoding="utf-8")
    c = tts_check(data)()
    assert c.ok is True and str(data / "keys" / "google-tts.key") in c.value  # 講 key 從哪裡來，不講 key 本身
    assert tts_server.requests == []  # 沒說要試唸就不連線、不花錢

    capsys.readouterr()
    cli.main(["check-env", "--json", "--data-dir", str(data), "--try-voice"])
    out = capsys.readouterr().out
    [tts] = [c for c in json.loads(out)["checks"] if c["key"] == "tts"]
    assert tts["ok"] is True and "試唸" in tts["value"], tts
    [req] = tts_server.synth("google")
    assert req["body"]["voice"]["name"] == "cmn-TW-Wavenet-A"
    assert FAKE_GOOGLE_KEY not in out

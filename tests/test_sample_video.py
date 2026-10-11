"""安裝最後一步：渲一支約 10 秒的測試片並打開（#12）。

素材是外掛自帶的範例專案（自己寫的兩句旁白＋FRED DGS2 公有領域數據的圖），套用作者剛設定好的頻道名、聲音、配樂。
測試用假配音層；打開影片那一步把 macOS 的 open 換掉（系統邊界）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from article_to_video.ffmpeg import find_ffmpeg

SAMPLE = Path(__file__).resolve().parent.parent / "src" / "article_to_video" / "sample_project"


def run_cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    from conftest import run_cli as run

    return run(*args, env=env, timeout=300)


def duration(video: Path) -> float:
    ffprobe = find_ffmpeg().with_name("ffprobe")
    out = subprocess.run([str(ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(video)], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return float(out)


@pytest.fixture(scope="module")
def test_video(tmp_path_factory):
    out = tmp_path_factory.mktemp("影片") / "解說影片" / "安裝測試片"
    proc = run_cli("test-video", "--out", str(out), "--tts", "fake")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return out, proc


def test_test_video_is_about_ten_seconds_and_says_where_it_is(test_video):
    out, proc = test_video
    video = out / "成片.mp4"
    assert video.is_file() and (out / "字幕.srt").is_file()
    assert 9.5 <= duration(video) <= 11.5  # 假配音 10.2 秒；真的 edge-tts（曉臻 +15%、頭尾空白修掉）實測 8.7 秒
    assert str(video) in proc.stdout


def test_test_video_uses_the_authors_channel_and_only_the_bundled_sample(test_video, user_data_dir):
    out, _ = test_video
    end_card = json.loads((out / "build" / "16x9" / "specs" / "end.card.json").read_text(encoding="utf-8"))
    assert end_card["data"]["channel"] == "測試頻道"  # conftest 的使用者設定
    for name in ("narration.json", "shots.json"):
        assert (out / name).read_bytes() == (SAMPLE / name).read_bytes()


def test_test_video_opens_and_follows_changed_settings(test_video, tmp_path, monkeypatch, capsys):
    """再做一次（例如換了聲音、把頻道名清掉之後）：設定快照跟著最新的使用者設定，做完用 open 打開成片。
    頻道名是選填（只想自己看的人不填）：沒填照樣做得出測試片，片尾卡不放名字。
    假配音層不管聲音，只有片尾卡要重渲，其他片段走快取，這個測試很快。"""
    import shutil

    from article_to_video import cli, sample_video

    out = tmp_path / "安裝測試片"
    shutil.copytree(test_video[0], out)  # 連 build/ 一起搬：片段快取照樣命中
    data = tmp_path / "資料"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"voice": "zh-TW-YunJheNeural"}), encoding="utf-8")  # 沒填頻道名
    snapshot = out / "settings.json"
    assert json.loads(snapshot.read_text(encoding="utf-8"))["voice"] == "zh-TW-HsiaoChenNeural"  # 第一次：預設曉臻
    opened: list[Path] = []
    monkeypatch.setattr(sample_video, "open_file", lambda p: opened.append(Path(p)) or True)
    monkeypatch.delenv("ARTICLE_TO_VIDEO_NO_OPEN", raising=False)
    monkeypatch.delenv("CI", raising=False)

    code = cli.main(["test-video", "--out", str(out), "--tts", "fake", "--data-dir", str(data)])
    assert code == 0, capsys.readouterr()
    assert opened == [out / "成片.mp4"]
    assert json.loads(snapshot.read_text(encoding="utf-8"))["voice"] == "zh-TW-YunJheNeural"  # 換成雲哲
    end_card = json.loads((out / "build" / "16x9" / "specs" / "end.card.json").read_text(encoding="utf-8"))
    assert not end_card["data"].get("channel") and end_card["data"]["sources"]  # 片尾卡不放名字，來源照放


def test_test_video_never_overwrites_a_real_project(tmp_path):
    real = tmp_path / "2026-10-01-我的片"
    real.mkdir()
    (real / "narration.json").write_text('{"segments": {"s01": "我自己的旁白。"}}', encoding="utf-8")
    proc = run_cli("test-video", "--out", str(real), "--tts", "fake")
    assert proc.returncode == 1
    assert "不是測試片" in proc.stderr
    assert "我自己的旁白" in (real / "narration.json").read_text(encoding="utf-8")

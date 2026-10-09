"""find_ffmpeg：找一個有 libass（subtitles 濾鏡）的 ffmpeg。"""

import subprocess
import sys
from pathlib import Path

import pytest

from article_to_video.ffmpeg import FFmpegNotFoundError, find_ffmpeg

FILTERS_WITH_SUBTITLES = """Filters:
 ... scale             V->V       Scale the input video size and/or convert the image format.
 ... subtitles         V->V       Render text subtitles onto input video using the libass library.
"""
FILTERS_WITHOUT_SUBTITLES = """Filters:
 ... scale             V->V       Scale the input video size and/or convert the image format.
 ... setpts            V->V       Set PTS for the output video frame.
"""


def fake_ffmpeg(path: Path, filters_text: str) -> Path:
    """做一支假的 ffmpeg：被問 -filters 就印出指定的濾鏡表。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.write({filters_text!r})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_skips_ffmpeg_without_subtitles_filter(tmp_path):
    plain = fake_ffmpeg(tmp_path / "plain" / "ffmpeg", FILTERS_WITHOUT_SUBTITLES)
    full = fake_ffmpeg(tmp_path / "full" / "ffmpeg", FILTERS_WITH_SUBTITLES)

    assert find_ffmpeg(candidates=[tmp_path / "missing" / "ffmpeg", plain, full]) == full


def test_no_usable_ffmpeg_raises_with_fix(tmp_path):
    plain = fake_ffmpeg(tmp_path / "plain" / "ffmpeg", FILTERS_WITHOUT_SUBTITLES)

    with pytest.raises(FFmpegNotFoundError) as err:
        find_ffmpeg(candidates=[tmp_path / "missing" / "ffmpeg", plain])

    msg = str(err.value)
    assert "brew install ffmpeg-full" in msg
    assert str(plain) in msg  # 說清楚查過哪些、為什麼不能用
    assert "字幕" in msg


def test_found_ffmpeg_can_burn_subtitles(tmp_path):
    ffmpeg = find_ffmpeg()
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n測試字幕\n", encoding="utf-8")
    out = tmp_path / "frame.png"

    proc = subprocess.run(
        [str(ffmpeg), "-v", "error", "-f", "lavfi", "-i", "color=c=white:s=320x180:d=1",
         "-vf", f"subtitles=filename={srt}", "-frames:v", "1", "-y", str(out)],
        capture_output=True, text=True, encoding="utf-8",
    )

    assert proc.returncode == 0, proc.stderr
    assert out.exists()

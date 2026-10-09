"""內嵌引擎渲片段：外掛自己帶的引擎、字型，渲得出片段；頁面報錯、缺字都要大聲失敗。"""

import json
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from article_to_video.clips import (
    EXAMPLE_T3_SPEC,
    ClipRenderError,
    MissingGlyphError,
    render_clip,
    render_stills,
)
from article_to_video.ffmpeg import find_ffmpeg


def probe(video: Path) -> dict:
    ffprobe = find_ffmpeg().with_name("ffprobe")
    out = subprocess.run(
        [str(ffprobe), "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=codec_name,width,height,nb_read_frames:format=duration",
         "-of", "json", str(video)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    info = json.loads(out)
    s = info["streams"][0]
    return {
        "codec": s["codec_name"],
        "size": (s["width"], s["height"]),
        "frames": int(s["nb_read_frames"]),
        "duration": float(info["format"]["duration"]),
    }


@pytest.fixture(scope="session")
def example_clip(tmp_path_factory):
    out = tmp_path_factory.mktemp("example") / "t3.mp4"
    return render_clip(EXAMPLE_T3_SPEC, out)


def test_example_finance_chart_renders_without_page_warnings(example_clip):
    assert example_clip.path.exists()
    assert example_clip.warnings == []


def test_example_clip_is_3s_1080p_h264(example_clip):
    info = probe(example_clip.path)
    assert info["codec"] == "h264"
    assert info["size"] == (1920, 1080)
    assert info["frames"] == 90  # 3 s × 30 fps
    assert info["duration"] == pytest.approx(3.0, abs=0.05)


def short_chart_spec(title: str, **overrides) -> dict:
    spec = {
        "grammar": "t3_finance_chart",
        "duration": 0.5,
        "data": {
            "title": title,
            "unit": "殖利率（%）",
            "source": "資料來源：FRED（DGS10）",
            "series": [{"label": "2023", "value": 3.96}, {"label": "2024", "value": 4.21}],
        },
        "cues": [{"at": 0, "kind": "title"}, {"at": 0, "kind": "bar"}],
    }
    spec.update(overrides)
    return spec


def test_missing_glyph_fails_loudly_naming_the_characters(tmp_path):
    out = tmp_path / "bad.mp4"
    # 𠀋（U+2000B）、𪚥（U+2A6A5）是 CJK 擴展 B 的罕用字，外掛附帶的子集沒有
    spec = short_chart_spec("十年期殖利率𠀋走高𪚥")

    with pytest.raises(MissingGlyphError) as err:
        render_clip(spec, out)

    assert err.value.missing_chars == "𠀋𪚥"
    assert "𠀋𪚥" in str(err.value)
    assert "缺的字" in str(err.value)
    assert not out.exists()


def write_test_png(path: Path, w: int = 640, h: int = 400) -> Path:
    """自製測試圖：左右兩塊色塊加一條深色橫帶（只用標準庫寫 PNG）。"""
    rows = []
    for y in range(h):
        row = bytearray([0])  # filter: none
        for x in range(w):
            if h * 0.45 < y < h * 0.55:
                row += bytes((30, 30, 30))
            elif x < w // 2:
                row += bytes((210, 80, 60))
            else:
                row += bytes((60, 120, 200))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"".join(rows)))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)
    return path


def png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


def test_vox_clip_renders_stills_from_a_relative_image(tmp_path):
    write_test_png(tmp_path / "剪報.png")
    spec = {
        "grammar": "y2_vox",
        "duration": 4,
        "safe": {"bottom": 140},
        "data": {"drift": 0.008},
        "cues": [
            {"at": 0, "kind": "image", "image": "剪報.png", "text": "自製測試圖"},
            {"at": 1.2, "kind": "highlight", "data": {"rect": [0.05, 0.4, 0.5, 0.2]}},
        ],
    }

    result = render_stills(spec, tmp_path / "stills", [0.6, 2.5], base_dir=tmp_path)

    assert [p.name for p in result.paths] == ["t000.60.png", "t002.50.png"]
    assert all(png_size(p) == (1920, 1080) for p in result.paths)
    assert result.warnings == []


def test_page_error_fails_loudly(tmp_path):
    out = tmp_path / "bad.mp4"
    spec = short_chart_spec("十年期殖利率", grammar="no_such_grammar")

    with pytest.raises(ClipRenderError) as err:
        render_clip(spec, out)

    assert "片段渲染失敗" in str(err.value)
    assert err.value.page_errors  # 頁面的 console.error 有被收進來
    assert not out.exists()

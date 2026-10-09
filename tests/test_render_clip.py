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


@pytest.mark.parametrize("aspect", ["16x9", "9x16"])
def test_annotation_on_a_negative_bar_leaves_its_value_label_readable(tmp_path, aspect):
    """負數柱（實質殖利率常常是負的）的標註不能壓到那根柱的數值標籤（例「-0.9%」）。
    標註出現前後各一張靜態圖：數值標籤那一塊在標註出現後一個像素都不能變，標註本身要畫在別處。數字是自製的示意數字。"""
    import numpy as np
    from PIL import Image

    from article_to_video.timeline import FORMATS

    spec = {
        "grammar": "t3_finance_chart", "duration": 2.5, **FORMATS[aspect].spec_base(),
        "data": {"title": "實質殖利率：有幾年是負的", "unit": "示意數據，%", "source": "來源：測試用自製數字",
                 "chart": "bar", "decimals": 1, "suffix": "%",
                 "series": [{"label": "甲", "value": 1.2}, {"label": "乙", "value": -0.9},
                            {"label": "丙", "value": 0.6}, {"label": "丁", "value": -1.4}],
                 "highlight": {"index": 1, "text": "跌最深", "sub": "負的實質殖利率"}},
        "cues": [{"at": 0, "kind": "title"}, {"at": 0.1, "kind": "bar"}, {"at": 1.5, "kind": "highlight"}],
    }
    before, after = (np.asarray(Image.open(p).convert("RGB")).astype(int)
                     for p in render_stills(spec, tmp_path, [1.4, 2.45]).paths)
    # 標註的那根柱：標註出現後只有它還是主色藍（#006BA2），其他柱淡掉
    blue = (np.abs(after - np.array([0x00, 0x6B, 0xA2])).max(axis=-1) <= 12)
    ys, xs = np.nonzero(blue)
    x0, x1, bottom = xs.min(), xs.max(), ys.max()
    # 數值標籤：標註出現前，柱底下方、這根柱那一欄裡的黑字
    col = before[bottom + 2: bottom + 90, x0 - 40: x1 + 41]
    ly, lx = np.nonzero(col.max(axis=-1) < 100)
    assert len(ly) > 50, "找不到負數柱的數值標籤"
    top, left = bottom + 2 + ly.min() - 4, x0 - 40 + lx.min() - 4
    label = (slice(top, bottom + 2 + ly.max() + 5), slice(left, x0 - 40 + lx.max() + 5))
    changed = int((np.abs(after[label] - before[label]).max(axis=-1) > 40).sum())
    assert changed == 0, f"標註壓到數值標籤：標籤那一塊有 {changed} 個像素變了"
    drawn = int(((np.abs(after - before).max(axis=-1) > 40) & (before.max(axis=-1) > 200)).sum())
    assert drawn > 1000  # 標註真的有畫出來（畫在別處）


@pytest.mark.parametrize("aspect", ["16x9", "9x16"])
def test_annotation_above_a_short_bar_has_a_visible_leader_line(tmp_path, aspect):
    """短柱旁邊有高柱時，標註放在短柱正上方；文字塊和柱上數值標籤之間的引線要看得到（不能被數值標籤蓋掉、只剩一小截）。
    標註出現前後各一張靜態圖：只在標註那根柱中線上（左右 3 像素內）多出來的像素＝引線，至少要有約 24 像素長。數字是自製的。"""
    import numpy as np
    from PIL import Image

    from article_to_video.timeline import FORMATS

    spec = {
        "grammar": "t3_finance_chart", "duration": 2.5, **FORMATS[aspect].spec_base(),
        "data": {"title": "兩個月的利差", "unit": "示意數據，百分點", "source": "來源：測試用自製數字",
                 "chart": "bar", "decimals": 2, "series": [{"label": "甲月", "value": 2.4}, {"label": "乙月", "value": 1.1}],
                 "highlight": {"index": 1, "text": "縮小一半以上"}},
        "cues": [{"at": 0, "kind": "title"}, {"at": 0.1, "kind": "bar"}, {"at": 1.5, "kind": "highlight"}],
    }
    before, after = (np.asarray(Image.open(p).convert("RGB")).astype(int)
                     for p in render_stills(spec, tmp_path, [1.4, 2.45]).paths)
    blue = (np.abs(after - np.array([0x00, 0x6B, 0xA2])).max(axis=-1) <= 12)  # 標註那根柱（其他柱淡掉）
    ys, xs = np.nonzero(blue)
    cx, top = (xs.min() + xs.max()) // 2, ys.min()
    new = (np.abs(after - before).max(axis=-1) > 40)[:top]
    near = new[:, cx - 3: cx + 4].any(axis=1)
    wide = new[:, cx - 200: cx - 3].any(axis=1) | new[:, cx + 4: cx + 201].any(axis=1)
    leader_rows = int((near & ~wide).sum())
    assert leader_rows >= 24 * (1080 / 1080), f"引線只看得到 {leader_rows} 像素"


def test_page_error_fails_loudly(tmp_path):
    out = tmp_path / "bad.mp4"
    spec = short_chart_spec("十年期殖利率", grammar="no_such_grammar")

    with pytest.raises(ClipRenderError) as err:
        render_clip(spec, out)

    assert "片段渲染失敗" in str(err.value)
    assert err.value.page_errors  # 頁面的 console.error 有被收進來
    assert not out.exists()

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


MAGENTA = (230, 0, 230)


def _framed_box_image(path: Path, frame: list[float], rect: list[float]) -> None:
    """白底 1600×1000：frame 四個角各一塊洋紅色標記（往內縮一點），rect 那裡一個深色數字塊。"""
    from PIL import Image, ImageDraw

    w, h = 1600, 1000
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = frame[0] * w, frame[1] * h, (frame[0] + frame[2]) * w, (frame[1] + frame[3]) * h
    for cx, cy in ((x0 + 30, y0 + 30), (x1 - 30, y0 + 30), (x0 + 30, y1 - 30), (x1 - 30, y1 - 30)):
        d.rectangle([cx - 14, cy - 14, cx + 14, cy + 14], fill=MAGENTA)
    rx, ry, rw, rh = rect[0] * w, rect[1] * h, rect[2] * w, rect[3] * h
    d.rectangle([rx + 8, ry + 8, rx + rw - 8, ry + rh - 8], fill=(40, 40, 40))
    im.save(path)


@pytest.mark.parametrize("aspect", ["16x9", "9x16"])
def test_vox_box_push_shows_exactly_the_frame_it_is_given(tmp_path, aspect):
    """框的 data.frame（原圖上要留在畫面裡的範圍，例：正在唸的那一整行、表格的欄名）：推近之後這一塊剛好填滿畫面——
    四個角的標記都看得到（沒推太深切掉），而且分散到畫面四邊（真的推近了，不是停在整張圖）。"""
    import numpy as np
    from PIL import Image

    from article_to_video.timeline import FORMATS

    fmt = FORMATS[aspect]
    base = fmt.spec_base()
    ws = base["width"] - base["safe"].get("left", 0) - base["safe"].get("right", 0)
    hs = base["height"] - base["safe"].get("top", 0) - base["safe"].get("bottom", 0)
    fw = min(0.5, 0.7 * (ws / hs) / 1.6)
    fh = fw * 1600 / 1000 / (ws / hs)              # 跟可用畫面同樣寬高比
    frame = [0.3, 0.15, fw, fh]
    rect = [0.3 + fw / 2 - 0.04, 0.15 + fh / 2 - 0.03, 0.08, 0.06]  # 小框：原版會推到兩倍半以上
    _framed_box_image(tmp_path / "表.png", frame, rect)
    spec = {"grammar": "y2_vox", "duration": 4, **base, "data": {"drift": 0.008}, "cues": [
        {"at": 0, "kind": "image", "image": "表.png", "text": "圖1"},
        {"at": 1.0, "kind": "highlight", "data": {"rect": rect, "frame": frame, "travel": False}}]}
    [png] = render_stills(spec, tmp_path / "s", [2.4], base_dir=tmp_path).paths
    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    mag = (a[..., 0] > 120) & (a[..., 2] > 120) & (a[..., 1] < 60)  # 洋紅（壓了舊紙色、暗角，會暗一點）
    ys, xs = np.nonzero(mag)
    cx = base["safe"].get("left", 0) + ws / 2
    cy = base["safe"].get("top", 0) + hs / 2
    quads = {(x < cx, y < cy) for x, y in zip(xs, ys)}
    assert len(quads) == 4, f"四個角只看得到 {len(quads)} 個（推太深，切掉了）"
    assert xs.max() - xs.min() > 0.7 * ws, "沒推近：標記擠在畫面中間"


def _camera_still_runs(frames, times, thresh=0.6):
    """相鄰兩幀平均差 < thresh 的連續時段（鏡頭停著，只有慢漂）：[(起, 訖)]。"""
    import numpy as np

    runs, start = [], None
    for k in range(1, len(frames)):
        still = float(np.abs(frames[k] - frames[k - 1]).mean()) < thresh
        if still and start is None:
            start = times[k - 1]
        if not still and start is not None:
            runs.append((start, times[k - 1]))
            start = None
    if start is not None:
        runs.append((start, times[-1]))
    return runs


def test_vox_camera_holds_on_a_box_before_moving_to_the_next_item(tmp_path):
    """框推近停住後，鏡頭至少停 1.2 秒才移向下一件素材（框到下一件隔 2.9 秒＝規則的最低限度）。
    小畫面渲 1/15 秒一張：推近完（框出現後 0.9 秒）到離開之間，相鄰兩幀幾乎一樣的時段至少 1.1 秒。"""
    import numpy as np
    from PIL import Image

    frame, rect = [0.3, 0.3, 0.3, 0.3 * 1.6 / (640 / 313)], [0.4, 0.38, 0.08, 0.06]
    _framed_box_image(tmp_path / "表.png", frame, rect)
    spec = {"grammar": "y2_vox", "duration": 5, "width": 640, "height": 360, "safe": {"bottom": 47},
            "data": {"drift": 0.008, "frameFill": 0.94, "camera": {"pushAt": 0.1, "push": 0.8, "travel": 0.8, "itemTravel": 0.75}},
            "cues": [{"at": 0, "kind": "image", "image": "表.png", "text": "圖1"},
                     {"at": 1.0, "kind": "highlight", "data": {"rect": rect, "frame": frame, "travel": False}},
                     {"at": 3.9, "kind": "point", "text": "下一張卡"}]}
    times = [round(1.0 + k / 15, 4) for k in range(46)]  # 1.0～4.0
    frames = [np.asarray(Image.open(p).convert("RGB")).astype(int)
              for p in render_stills(spec, tmp_path / "s", times, base_dir=tmp_path).paths]
    held = max((b - a for a, b in _camera_still_runs(frames, times) if a >= 1.8), default=0)
    assert held >= 1.1, f"框推近後鏡頭只停了 {held:.2f} 秒：{_camera_still_runs(frames, times)}"


def test_vox_camera_never_shows_an_empty_desk_while_moving_off_a_close_up(tmp_path):
    """從推得很近的框（圖的最左邊）移到下一張圖（還沒貼上來，3.95 秒到位、4.0 秒貼上）：移動途中（3.15 秒起），
    到位前 0.15 秒為止，每一幀都還看得到一成以上剛離開的那張圖——中途拉遠、不拖長尾，不會有一串只剩桌面的幀
    （原版長尾移動加上高倍推近，3.5 秒起就只剩不到一成，之後一路是空桌面）。"""
    import numpy as np
    from PIL import Image

    blue = Image.new("RGB", (1600, 1000), (120, 170, 235))
    blue.save(tmp_path / "藍.png")
    frame, rect = [0.02, 0.4, 0.12, 0.12 * 1.6 / (640 / 313)], [0.04, 0.45, 0.06, 0.05]
    spec = {"grammar": "y2_vox", "duration": 5, "width": 640, "height": 360, "safe": {"bottom": 47},
            "data": {"drift": 0.008, "frameFill": 0.94, "camera": {"pushAt": 0.1, "push": 0.8, "travel": 0.8, "itemTravel": 0.75}},
            "cues": [{"at": 0, "kind": "image", "image": "藍.png", "text": "圖1"},
                     {"at": 1.0, "kind": "highlight", "data": {"rect": rect, "frame": frame, "travel": False}},
                     {"at": 4.0, "kind": "image", "image": "藍.png", "text": "圖2"}]}
    times = [round(3.2 + k / 15, 4) for k in range(10)]  # 3.2～3.8
    for t, p in zip(times, render_stills(spec, tmp_path / "s", times, base_dir=tmp_path).paths):
        a = np.asarray(Image.open(p).convert("RGB")).astype(int)[: 360 - 47]  # 字幕帶以上
        share = float(((a[..., 2] > a[..., 0] + 40)).mean())
        assert share >= 0.10, f"{t:.2f} 秒畫面幾乎只剩桌面（剛離開的圖只占 {share:.1%}）"


def _components(mask):
    """mask（二維 bool）的連通塊（8 鄰）：每塊一個 (ys, xs)。測試裡的小區域用，不追求快。"""
    import numpy as np

    seen = np.zeros_like(mask, dtype=bool)
    out = []
    h, w = mask.shape
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        stack, ys, xs = [(y0, x0)], [], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            ys.append(y)
            xs.append(x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]:
                        seen[yy, xx] = True
                        stack.append((yy, xx))
        out.append((np.array(ys), np.array(xs)))
    return out


# 自製的月資料（不是任何文章的數字）：先倒掛、谷底在第 9 個月、回正，最後兩個月往下掉
DIP = [0.80, 0.55, 0.30, 0.10, -0.15, -0.40, -0.60, -0.75, -0.85, -0.80, -0.70, -0.55,
       -0.40, -0.20, 0.05, 0.25, 0.40, 0.45, 0.50, 0.55, 0.60, 0.62, 0.48, 0.35]


def _dip_spec(aspect: str) -> dict:
    from article_to_video.timeline import FORMATS

    return {
        "grammar": "t3_finance_chart", "duration": 3.2, **FORMATS[aspect].spec_base(),
        "data": {"title": "利差倒掛之後轉正", "unit": "示意數據，百分點", "source": "來源：測試用自製數字",
                 "chart": "line", "decimals": 2, "xTicks": [0, 12, 23],
                 "series": [{"label": f"{2024 + i // 12}-{i % 12 + 1:02d}", "value": v} for i, v in enumerate(DIP)],
                 "highlight": {"index": 8, "text": "倒掛最深"}},
        "cues": [{"at": 0, "kind": "title"}, {"at": 0.1, "kind": "line", "dur": 1.0}, {"at": 2.0, "kind": "highlight"}],
    }


@pytest.mark.parametrize("aspect", ["16x9", "9x16"])
def test_line_annotation_keeps_off_the_line_and_the_zero_axis(tmp_path, aspect):
    """折線的標註文字不壓線、不壓零軸（谷底往上 140 的預設位置正好壓在零軸上）。標註出現前後各一張靜態圖：
    多出來的深色粗筆畫＝標註文字（細的引線去掉），那塊在標註出現前不能有折線（紅）或零軸（整列深色）。"""
    import numpy as np
    from PIL import Image

    before, after = (np.asarray(Image.open(p).convert("RGB")).astype(int)
                     for p in render_stills(_dip_spec(aspect), tmp_path, [1.9, 3.15]).paths)
    text = (np.abs(after - before).max(axis=-1) > 60) & (after.max(axis=-1) < 90)
    core = text.copy()  # 侵蝕 5×5：2 像素寬的引線消失，粗體字的筆畫留著
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            core[2:-2, 2:-2] &= text[2 + dy: text.shape[0] - 2 + dy, 2 + dx: text.shape[1] - 2 + dx]
    ys, xs = np.nonzero(core)
    assert len(ys) > 50, "找不到標註文字"
    y0, y1, x0, x1 = ys.min() - 6, ys.max() + 7, xs.min() - 6, xs.max() + 7
    under = before[y0:y1, x0:x1]
    red = (under[..., 0] > 170) & (under[..., 1] < 100) & (under[..., 2] < 100)
    assert red.sum() == 0, f"標註文字壓在折線上（{red.sum()} 個像素）"
    dark_rows = ((under.max(axis=-1) < 90).mean(axis=1) > 0.8).sum()
    assert dark_rows == 0, "標註文字壓在零軸上"


@pytest.mark.parametrize("aspect", ["16x9", "9x16"])
def test_line_end_value_is_not_crossed_by_the_line_or_a_gridline(tmp_path, aspect):
    """線尾數值（例「0.35」）：線最後往下掉時，預設寫在線頭左上會被線穿過，格線也剛好穿過字。
    最後一幀線頭附近的紅色連通塊：線（最大那塊）以外每個字自成一塊（「0.35」四塊），線的像素不進到字的範圍；
    字的範圍裡也沒有格線的淺灰。"""
    import numpy as np
    from PIL import Image

    spec = _dip_spec(aspect)
    [png] = render_stills(spec, tmp_path, [1.9]).paths
    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    red = (a[..., 0] > 170) & (a[..., 1] < 120) & (a[..., 2] < 120)
    red[: spec["safe"].get("top", 0) + 120] = False    # 頂端色條、小旗也是紅的
    ys, xs = np.nonzero(red)
    end_x = xs.max()                                   # 線頭（圓點的右緣）
    end_y = int(np.median(ys[xs >= end_x - 3]))
    roi = (slice(max(0, end_y - 140), end_y + 140), slice(end_x - 220, end_x + 20))
    comps = sorted(_components(red[roi]), key=lambda c: -len(c[0]))
    dot_x = end_x - (end_x - 220)                      # 線頭圓點（白邊把它和線分開）是最右邊那塊，不是字
    line, glyphs = comps[0], [c for c in comps[1:] if len(c[0]) > 15 and c[1].max() < dot_x - 2]
    assert len(glyphs) >= 4, f"線尾數值的字黏在線上（分開的字只有 {len(glyphs)} 塊）"
    gy = np.concatenate([g[0] for g in glyphs]); gx = np.concatenate([g[1] for g in glyphs])
    box = (slice(gy.min() - 2, gy.max() + 3), slice(gx.min() - 2, gx.max() + 3))
    inside = ((line[0] >= box[0].start) & (line[0] < box[0].stop) & (line[1] >= box[1].start) & (line[1] < box[1].stop))
    assert inside.sum() == 0, f"折線穿過線尾數值（{inside.sum()} 個像素）"
    region = a[roi][box]
    gray = (region.min(axis=-1) > 170) & (region.max(axis=-1) < 238) & (np.ptp(region, axis=-1) < 12)
    assert gray.sum() < 5, f"格線穿過線尾數值（{gray.sum()} 個像素）"


def test_page_error_fails_loudly(tmp_path):
    out = tmp_path / "bad.mp4"
    spec = short_chart_spec("十年期殖利率", grammar="no_such_grammar")

    with pytest.raises(ClipRenderError) as err:
        render_clip(spec, out)

    assert "片段渲染失敗" in str(err.value)
    assert err.value.page_errors  # 頁面的 console.error 有被收進來
    assert not out.exists()

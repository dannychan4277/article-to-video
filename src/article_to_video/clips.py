"""用外掛內嵌的 huashu-art-motion 引擎渲片段（財經圖表 t3_finance_chart、Vox 剪報 y2_vox）。

引擎在 `engine/`（render.py＋clip.html＋語法程式＋字型），用這個專案自己的 Python 跑 render.py，
不依賴使用者電腦上的其他 skill。頁面報錯、缺字一律丟例外，不會默默出一支有問題的片段。
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from article_to_video.ffmpeg import find_ffmpeg

__all__ = [
    "ENGINE_DIR",
    "EXAMPLE_T3_SPEC",
    "FONTS_DIR",
    "QA_SCRIPT",
    "RENDER_SCRIPT",
    "ClipRenderError",
    "MissingGlyphError",
    "RenderResult",
    "render_clip",
    "render_stills",
]

ENGINE_DIR = Path(__file__).resolve().parent / "engine"
RENDER_SCRIPT = ENGINE_DIR / "render.py"
QA_SCRIPT = ENGINE_DIR / "qa.py"
FONTS_DIR = ENGINE_DIR / "lib" / "fonts"
EXAMPLE_T3_SPEC = ENGINE_DIR / "examples" / "t3_finance_chart.json"

SpecInput = Mapping | str | os.PathLike


@dataclass(frozen=True)
class RenderResult:
    paths: list[Path]
    """產出的檔案：片段一支，或靜幀多張（照 times 的順序）。"""
    warnings: list[str] = field(default_factory=list)
    """頁面告警（不致命，例如 cue 超出片段時長）。"""

    @property
    def path(self) -> Path:
        return self.paths[0]


class ClipRenderError(RuntimeError):
    """片段渲染失敗：引擎開機失敗、頁面報錯、ffmpeg 編碼失敗……"""

    def __init__(self, message: str, *, page_errors: Sequence[str] = (), log: str = "") -> None:
        super().__init__(message)
        self.page_errors = list(page_errors)
        self.log = log


class MissingGlyphError(ClipRenderError):
    """分鏡設定裡有外掛附帶字型沒有的字。"""

    def __init__(self, missing_by_font: Mapping[str, str], *, log: str = "") -> None:
        chars = "".join(dict.fromkeys("".join(missing_by_font.values())))
        super().__init__(
            f"分鏡設定裡有字型缺的字：{chars}\n"
            "外掛附帶的字型（Noto Sans CJK TC 子集，含 Big5 與 GB2312 的字）沒有這些字。"
            "硬渲的話會偷偷換成這台電腦上的其他字型，換台電腦畫面就變樣，所以先停下來。\n"
            "修法：把這幾個字換成常用字，或改寫那句話，再重渲一次。",
            log=log,
        )
        self.missing_chars = chars
        self.missing_by_font = dict(missing_by_font)


def render_clip(
    spec: SpecInput,
    out: str | os.PathLike,
    *,
    ffmpeg: str | os.PathLike | None = None,
    alpha: bool = False,
    base_dir: str | os.PathLike | None = None,
) -> RenderResult:
    """把一份分鏡設定（dict 或 JSON 檔）渲成片段。

    預設出無聲 H.264（yuv420p、每秒一個關鍵幀）；alpha=True 出 ProRes 4444 帶透明（用 .mov）。
    時長、fps、寬高都照 spec。ffmpeg 不給就用 find_ffmpeg() 找到的那支。
    spec 是 dict 時，裡面的相對圖片路徑相對 base_dir（預設目前資料夾）；是檔案時相對檔案所在資料夾。
    失敗丟 ClipRenderError（缺字是它的子類 MissingGlyphError），失敗時不留下輸出檔。
    """
    out_path = Path(out).absolute()
    ffmpeg_path = Path(ffmpeg) if ffmpeg is not None else find_ffmpeg()
    args = ["--out", str(out_path), "--ffmpeg", str(ffmpeg_path)]
    if alpha:
        args.append("--alpha")
    with _spec_file(spec, base_dir) as spec_path:
        warnings = _run_render(["--spec", str(spec_path), *args], outputs=[out_path])
    return RenderResult([out_path], warnings)


def render_stills(
    spec: SpecInput,
    out_dir: str | os.PathLike,
    times: Sequence[float],
    *,
    base_dir: str | os.PathLike | None = None,
) -> RenderResult:
    """只渲指定時刻的靜幀 PNG（檔名 t{秒:06.2f}.png，例 t001.50.png），用來快速看畫面。"""
    if not times:
        raise ValueError("render_stills 至少要給一個時刻")
    dir_path = Path(out_dir).absolute()
    secs = [float(t) for t in times]
    paths = [dir_path / f"t{t:06.2f}.png" for t in secs]
    args = ["--out", str(dir_path), "--stills", ",".join(repr(t) for t in secs)]
    with _spec_file(spec, base_dir) as spec_path:
        warnings = _run_render(["--spec", str(spec_path), *args], outputs=paths)
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise ClipRenderError("靜幀沒有產出：" + "、".join(str(p) for p in missing))
    return RenderResult(paths, warnings)


@contextmanager
def _spec_file(spec: SpecInput, base_dir: str | os.PathLike | None) -> Iterator[Path]:
    if isinstance(spec, Mapping):
        data = _absolutize_images(copy.deepcopy(dict(spec)), Path(base_dir or Path.cwd()).absolute())
        with tempfile.TemporaryDirectory(prefix="atv-spec-") as tmp:
            path = Path(tmp) / "clip.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            yield path
        return
    path = Path(spec).absolute()
    if not path.is_file():
        raise ClipRenderError(f"找不到分鏡設定檔：{path}")
    yield path


def _absolutize_images(spec: dict, base: Path) -> dict:
    def fix(v: object) -> object:
        if not isinstance(v, str) or not v or v.startswith(("data:", "http:", "https:")):
            return v
        p = Path(v)
        return str(p if p.is_absolute() else base / p)

    for cue in spec.get("cues") or []:
        if isinstance(cue, dict) and cue.get("image"):
            cue["image"] = fix(cue["image"])
    data = spec.get("data")
    if isinstance(data, dict) and data.get("image"):
        data["image"] = fix(data["image"])
    return spec


def _run_render(args: list[str], *, outputs: list[Path]) -> list[str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run(
        [sys.executable, str(RENDER_SCRIPT), "--strict-glyphs", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    log = proc.stdout + ("\n" if proc.stdout and proc.stderr else "") + proc.stderr
    lines = log.splitlines()
    warnings = [ln[len("[page] "):] for ln in lines if ln.startswith("[page] ")]
    page_errors = [ln for ln in lines if ln.startswith(("[page-error] ", "[pageerror] "))]
    missing: dict[str, str] = {}
    for ln in lines:
        if ln.startswith("[missing-glyphs] "):
            missing = json.loads(ln[len("[missing-glyphs] "):])

    if proc.returncode == 0 and not page_errors and not missing:
        return warnings

    for p in outputs:
        p.unlink(missing_ok=True)
    if missing:
        raise MissingGlyphError(missing, log=log)
    detail = ""
    if page_errors:
        detail += f"\n頁面報錯 {len(page_errors)} 條：\n" + "\n".join("  " + e for e in page_errors[:10])
    engine_msg = proc.stderr.strip().splitlines()[-12:]
    if engine_msg:
        detail += "\n引擎訊息：\n" + "\n".join("  " + m for m in engine_msg)
    raise ClipRenderError(
        f"片段渲染失敗（render.py 結束碼 {proc.returncode}）。{detail}",
        page_errors=page_errors,
        log=log,
    )

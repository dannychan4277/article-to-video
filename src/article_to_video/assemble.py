"""組裝：旁白音軌（依段落起點擺好）＋接片＋燒字幕＋混音 → 成片。

- 旁白音軌：段落之間不會重疊（留白 ≥ 0），所以直接用標準庫把每段 WAV 擺到「段落起點」那個樣本，
  中間補靜音，總長補到跟畫面一樣長。逐樣本對齊，不經 ffmpeg 混音。
  （配樂那張票在 assemble_video 加一路墊樂、壓在旁白下面。）
- 接片＋燒字幕＋混音一次跑完：concat 片段（同一套渲染參數）→ subtitles 濾鏡（fontsdir 指外掛附帶字型）→
  H.264＋AAC。ffmpeg 在建置資料夾裡執行、用相對路徑，專案路徑有中文、空白、冒號都不用跳脫。
- 燒完讀 libass 紀錄，確認字幕用的是附帶字型（subtitles.check_libass_log）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import wave
from collections.abc import Iterable, Mapping
from pathlib import Path

from article_to_video.narration import SAMPLE_RATE
from article_to_video.subtitles import SUBTITLE_FONT_FILE, check_libass_log
from article_to_video.timeline import SegmentTiming

__all__ = ["ASSEMBLY_VERSION", "AssemblyError", "assemble_video", "file_digest", "final_key", "write_narration_track"]

ASSEMBLY_VERSION = "1"
"""組裝參數（編碼設定、字幕樣式邏輯）改了就加一，讓舊成片重組。"""


class AssemblyError(RuntimeError):
    """組裝失敗（繁中訊息）。"""


def write_narration_track(segments: Iterable[SegmentTiming], total_seconds: float, out: Path) -> None:
    total = round(total_seconds * SAMPLE_RATE)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        pos = 0
        for seg in segments:
            start = round(seg.start * SAMPLE_RATE)
            with wave.open(str(seg.audio.audio), "rb") as r:
                frames = r.readframes(r.getnframes())
            if start < pos:
                raise AssemblyError(f"旁白段 {seg.key} 跟上一段重疊了（段間留白不能是負的）")
            w.writeframes(b"\x00\x00" * (start - pos))
            take = min(len(frames) // 2, max(0, total - start))
            w.writeframes(frames[: take * 2])
            pos = start + take
        if pos < total:
            w.writeframes(b"\x00\x00" * (total - pos))


def _prepare_fonts(fonts_dir: Path) -> None:
    """fontsdir 只放字幕字型一個檔（libass 會試著載入資料夾裡每個檔，放授權文字檔會報錯）。"""
    fonts_dir.mkdir(parents=True, exist_ok=True)
    dst = fonts_dir / SUBTITLE_FONT_FILE.name
    for f in fonts_dir.iterdir():
        if f != dst:
            f.unlink() if f.is_file() else shutil.rmtree(f)
    if not dst.is_file() or dst.stat().st_size != SUBTITLE_FONT_FILE.stat().st_size:
        shutil.copyfile(SUBTITLE_FONT_FILE, dst)


def assemble_video(
    *,
    workdir: Path,
    clips: Iterable[Path],
    narration: Path,
    ass: Path,
    fonts_dir: Path,
    out: Path,
    ffmpeg: Path,
    fps: int,
    log_path: Path,
) -> None:
    """clips、narration、ass、fonts_dir 都要在 workdir 底下（會用相對路徑給 ffmpeg）。"""
    _prepare_fonts(fonts_dir)

    def rel(p: Path) -> str:
        r = Path(os.path.relpath(p, workdir)).as_posix()
        if any(ch in r for ch in "'\\:,;[]=") or not r.isascii():
            raise AssemblyError(f"建置資料夾裡的檔名不能有特殊字元：{r}")
        return r

    concat = workdir / "clips.txt"
    concat.write_text("".join(f"file '{rel(c)}'\n" for c in clips), encoding="utf-8")
    tmp = out.with_name(out.stem + ".part" + out.suffix)
    cmd = [
        str(ffmpeg), "-hide_banner", "-v", "verbose", "-y",
        "-f", "concat", "-safe", "0", "-i", rel(concat),
        "-i", rel(narration),
        "-vf", f"subtitles={rel(ass)}:fontsdir={rel(fonts_dir)}",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(tmp),
    ]
    proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, encoding="utf-8", errors="replace")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise AssemblyError(f"ffmpeg 組裝成片失敗（結束碼 {proc.returncode}），完整紀錄在 {log_path}：\n{tail}")
    problems = check_libass_log(proc.stderr)
    if problems:
        tmp.unlink(missing_ok=True)
        raise AssemblyError(
            "字幕沒有完全用外掛附帶的字型（換台電腦字幕會長得不一樣），所以不出片：\n"
            + "\n".join(f"- {p}" for p in problems) + f"\n完整紀錄在 {log_path}"
        )
    os.replace(tmp, out)


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def final_key(parts: Mapping[str, object]) -> str:
    """成片的快取鍵：片段雜湊、旁白音軌、字幕、組裝參數都沒變，就不用重新組裝。"""
    return hashlib.sha256(json.dumps(dict(parts), sort_keys=True, ensure_ascii=False).encode()).hexdigest()

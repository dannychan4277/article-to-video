"""回報問題：`report-issue` 打包 zip，`reproduce-issue` 在維護者電腦上從 zip 重渲出錯的分鏡。

切點：兩個 CLI 子指令（子行程或 cli.main），只看 zip 裡有什麼、重現出來的檔案與錯誤訊息。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from conftest import MINI_PROJECT


def cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "article_to_video", *args],
        capture_output=True, text=True, encoding="utf-8", env=full_env,
    )


def read_text(z: zipfile.ZipFile, name: str) -> str:
    return z.read(name).decode("utf-8")


@pytest.fixture(scope="module")
def good_report(built_project, tmp_path_factory) -> Path:
    """作者說「第 2 張圖的標註跑掉了」：建置成功的專案，指定分鏡用編號（2）。"""
    desk = tmp_path_factory.mktemp("桌面")
    proc = cli("report-issue", str(built_project.dir), "--shot", "2",
               "--note", "第二張圖的標註跑掉了", "--out-dir", str(desk))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    [zpath] = desk.glob("*.zip")
    assert str(zpath) in proc.stdout
    return zpath


def test_report_zip_carries_the_narration_shots_and_settings(built_project, good_report):
    with zipfile.ZipFile(good_report) as z:
        for name in ("narration.json", "shots.json", "settings.json"):
            assert json.loads(read_text(z, f"project/{name}")) == json.loads(
                (built_project.dir / name).read_text(encoding="utf-8"))
        report = json.loads(read_text(z, "report.json"))
        assert report["shots"] == ["SH02"]
        assert "第二張圖的標註跑掉了" in read_text(z, "問題描述.txt")


def test_report_carries_an_environment_check(good_report):
    from article_to_video.ffmpeg import find_ffmpeg

    with zipfile.ZipFile(good_report) as z:
        env = {c["key"]: c for c in json.loads(read_text(z, "environment.json"))["checks"]}
        text = read_text(z, "environment.txt")
    sw_vers = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True, check=True).stdout.strip()
    assert env["macos"]["value"] == sw_vers
    assert env["cpu"]["detail"]["machine"] in {"arm64", "x86_64"}
    assert env["python"]["detail"]["version"] == ".".join(map(str, sys.version_info[:3]))
    ff = env["ffmpeg"]
    assert ff["ok"] is True and ff["detail"]["chosen"] == str(find_ffmpeg())
    by_path = {c["path"]: c for c in ff["detail"]["candidates"]}
    assert by_path[str(find_ffmpeg())]["subtitles"] is True
    assert by_path[str(find_ffmpeg())]["version"]
    assert env["playwright"]["value"] == "1.63.0"  # pyproject 釘的版本
    assert env["chromium"]["ok"] is True and env["chromium"]["value"]
    assert env["edge-tts"]["ok"] is True and env["edge-tts"]["value"].startswith("7.")
    assert "ffmpeg" in text and "Chromium" in text


def forbid_narration(monkeypatch) -> None:
    """維護者那台：配音層一律不准再叫（時間點要用作者那台的紀錄）；這個行程本來就不准連網（conftest）。"""
    from article_to_video import narration

    def no_tts(*a, **k):
        raise AssertionError("重現時不應該重新配音")

    monkeypatch.setattr(narration.FakeTTS, "synthesize", no_tts)
    monkeypatch.setattr(narration.EdgeTTS, "synthesize", no_tts)


def png_rgb(path: Path) -> bytes:
    """把 PNG 解成 RGB24 位元組（用 ffmpeg，不靠影像套件）。"""
    from article_to_video.ffmpeg import find_ffmpeg

    return subprocess.run(
        [str(find_ffmpeg()), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout


def test_maintainer_rerenders_the_reported_shot_from_the_zip_offline(good_report, tmp_path, monkeypatch):
    with zipfile.ZipFile(good_report) as z:
        reported = {n.split("/")[-1]: z.read(n) for n in z.namelist()
                    if n.startswith("stills/SH02/") and n.endswith(".png")}
    assert len(reported) >= 2

    forbid_narration(monkeypatch)
    from article_to_video.cli import main

    out = tmp_path / "維護者" / "重現這包"
    assert main(["reproduce-issue", str(good_report), "--out", str(out)]) == 0

    stills = out / "重現" / "stills" / "SH02"
    assert sorted(p.name for p in stills.glob("*.png")) == sorted(reported)
    for name, data in reported.items():
        mine = tmp_path / "作者那張.png"
        mine.write_bytes(data)
        assert png_rgb(stills / name) == png_rgb(mine), f"{name} 重渲出來跟作者那台不一樣"
    clip = out / "重現" / "clips" / "SH02.mp4"
    assert clip.is_file() and clip.stat().st_size > 0


@pytest.fixture(scope="module")
def failed_build(tmp_path_factory) -> Path:
    """建置失敗的專案：SH02 的標註用了字型沒有的「〇」，渲 SH02 時被引擎擋下（SH01 照樣渲得出來）。"""
    root = tmp_path_factory.mktemp("影片") / "2026-10-09-壞掉的一支"
    shutil.copytree(MINI_PROJECT, root)
    shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
    shots["shots"][1]["chart"]["data"]["highlight"]["text"] = "二〇二三較低"
    (root / "shots.json").write_text(json.dumps(shots, ensure_ascii=False, indent=1), encoding="utf-8")
    proc = cli("build", str(root), "--tts", "fake")
    assert proc.returncode == 1 and "〇" in proc.stderr, proc.stdout + proc.stderr
    return root


@pytest.fixture(scope="module")
def failed_report(failed_build, tmp_path_factory) -> Path:
    desk = tmp_path_factory.mktemp("桌面")
    proc = cli("report-issue", str(failed_build), "--out-dir", str(desk))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    [zpath] = desk.glob("*.zip")
    return zpath


def test_report_after_a_failed_build_names_the_broken_shot_and_keeps_the_error_logs(failed_report):
    with zipfile.ZipFile(failed_report) as z:
        names = z.namelist()
        report = json.loads(read_text(z, "report.json"))
        assert report["shots"] == ["SH02"]  # 沒指定分鏡：從最後一次建置的錯誤找出來
        assert "〇" in read_text(z, "errors/last-run.log")
        [render_log] = [n for n in names if n.startswith("errors/") and n.endswith("render-SH02.log")]
        assert "〇" in read_text(z, render_log)
        # 靜態圖渲不出來（就是要回報的問題）：記下原因，打包照樣完成
        assert "〇" in report["stills"]["SH02"]["error"]
        assert not [n for n in names if n.startswith("stills/SH02/")]


def test_maintainer_gets_the_same_failure_from_the_zip(failed_report, tmp_path):
    proc = cli("reproduce-issue", str(failed_report), "--out", str(tmp_path / "重現"))
    assert proc.returncode == 1
    assert "分鏡 SH02" in proc.stderr and "〇" in proc.stderr


def test_maintainer_gets_the_same_timeline_error_using_the_authors_narration_timing(tmp_path, monkeypatch, capsys):
    """配音做完、算時間軸時才出錯：zip 帶著作者那台的旁白時間點，維護者那台算出一模一樣的錯。"""
    root = tmp_path / "2026-10-09-錨點放錯"
    shutil.copytree(MINI_PROJECT, root)
    shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
    shots["shots"][0]["chart"]["cues"][3]["at"] = {"segment": "s02", "word": "倒掛"}  # 落在 SH02 的時段
    (root / "shots.json").write_text(json.dumps(shots, ensure_ascii=False, indent=1), encoding="utf-8")
    build = cli("build", str(root), "--tts", "fake")
    assert build.returncode == 1
    error = build.stderr.strip().removeprefix("建置失敗：")
    assert "SH01" in error

    rep = cli("report-issue", str(root), "--out-dir", str(tmp_path / "桌面"))
    assert rep.returncode == 0, rep.stdout + rep.stderr
    [zpath] = (tmp_path / "桌面").glob("*.zip")
    with zipfile.ZipFile(zpath) as z:
        assert json.loads(read_text(z, "report.json"))["shots"] == ["SH01"]

    forbid_narration(monkeypatch)
    from article_to_video.cli import main

    assert main(["reproduce-issue", str(zpath), "--out", str(tmp_path / "重現")]) == 1
    assert error in capsys.readouterr().err


def test_report_without_a_project_still_carries_the_environment(tmp_path):
    """還沒有專案就出事（例如安裝階段）：照樣打包環境檢查，並告訴作者傳給 Danny。"""
    proc = cli("report-issue", "--note", "裝好之後一做片就當掉", "--out-dir", str(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "LINE" in proc.stdout and "Danny" in proc.stdout
    [zpath] = tmp_path.glob("*.zip")
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        assert "environment.json" in names and "問題描述.txt" in names
        assert not [n for n in names if n.startswith("project/")]


SKILL = Path(__file__).resolve().parent.parent / "skills" / "report-issue" / "SKILL.md"


def test_report_skill_triggers_on_the_phrase_and_runs_only_the_report_command():
    text = SKILL.read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    meta = dict(line.split(": ", 1) for line in front.strip().splitlines())
    assert meta["name"] == "report-issue"
    assert "回報問題" in meta["description"]
    assert "disable-model-invocation" not in meta  # 作者說「回報問題」Claude 要能自己叫
    assert meta["allowed-tools"] == "Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video report-issue *)"
    assert "uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video report-issue" in body
    assert "LINE" in body and "Danny" in body
    # skill 裡叫到的選項，指令真的有
    help_text = cli("report-issue", "--help").stdout
    for flag in {f for f in body.split() if f.startswith("--") and len(f) > 2} - {"--project"}:
        assert flag in help_text, f"skill 用了指令沒有的選項 {flag}"


TEXT_SUFFIXES = (".json", ".txt", ".log")


def test_report_keeps_only_what_reproducing_needs(tmp_path):
    """個資：文章全文、沒用到的原圖、成片、配音音檔都不放；家目錄路徑與使用者名稱遮掉；預設放桌面。"""
    import getpass

    fake_home = tmp_path / "家"
    root = fake_home / "Movies" / "解說影片" / "2026-10-09-有剪報的一支"
    shutil.copytree(MINI_PROJECT, root)
    images = root / "article" / "images"
    images.mkdir(parents=True)
    (root / "article" / "全文.txt").write_text("這是文章全文，不能外流。", encoding="utf-8")
    (images / "p1-剪報.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"used" * 10)
    (images / "p2-沒用到.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"unused" * 10)
    outside = tmp_path / "下載" / "別處的圖.png"
    outside.parent.mkdir()
    outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"outside" * 10)
    (root / "成片.mp4").write_bytes(b"\0" * 1000)
    shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
    shots["shots"].append({"id": "SH03", "start": {"segment": "s02", "word": "倒掛"},
                           "vox": {"cues": [{"at": 0, "image": "article/images/p1-剪報.png"},
                                            {"at": 1, "image": str(outside)}]}})
    (root / "shots.json").write_text(json.dumps(shots, ensure_ascii=False, indent=1), encoding="utf-8")

    env = {"HOME": str(fake_home),
           "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
           or str(Path.home() / "Library" / "Caches" / "ms-playwright")}
    proc = cli("report-issue", str(root), "--shot", "SH03", env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    [zpath] = (fake_home / "Desktop").glob("*.zip")

    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        assert not [n for n in names if n.endswith((".mp4", ".wav", ".mov"))]
        assert [n for n in names if "/article/" in n] == ["project/article/images/p1-剪報.png"]
        assert z.read("project/article/images/p1-剪報.png") == (images / "p1-剪報.png").read_bytes()
        # 專案外的圖：放進 _外部圖片，打包的分鏡設定改指那裡（解開就能用）
        packed = json.loads(read_text(z, "project/shots.json"))
        moved = packed["shots"][2]["vox"]["cues"][1]["image"]
        assert z.read(f"project/{moved}") == outside.read_bytes()
        for n in names:
            if n.endswith(TEXT_SUFFIXES):
                text = read_text(z, n)
                for secret in (str(fake_home), str(Path.home()), getpass.getuser(), "這是文章全文"):
                    assert secret not in text, f"{n} 裡有「{secret}」"

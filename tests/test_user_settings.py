"""使用者設定：頻道名、logo、品牌色、聲音、語速、配樂、預設比例。

切點是指令列 `python -m article_to_video settings …`（主 skill／安裝流程就是這樣叫的）：
設定存在外掛資料夾以外（--data-dir，主 skill 傳 ${CLAUDE_PLUGIN_DATA}；沒給就用
~/Library/Application Support/article-to-video），更新外掛（換一份外掛程式碼）不受影響。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args: str, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    from conftest import run_cli

    return run_cli(*args, env=env, cwd=cwd)


def show(data_dir: Path) -> dict:
    proc = run("settings", "show", "--json", "--data-dir", str(data_dir))
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_settings_are_saved_in_the_data_dir_and_read_back(tmp_path):
    data = tmp_path / "plugin-data"
    proc = run("settings", "set", "--data-dir", str(data),
               "--channel", "小明的財經筆記", "--voice", "曉雨", "--rate", "+10%",
               "--music", "pulse", "--brand-color", "#1a5fb4", "--aspect", "9:16")
    assert proc.returncode == 0, proc.stderr

    got = show(data)
    assert got["channel"] == "小明的財經筆記"
    assert got["voice"] == "zh-TW-HsiaoYuNeural"  # 中文名換成聲音代號
    assert got["rate"] == "+10%"
    assert got["music"] == "pulse"
    assert got["brand_color"] == "#1A5FB4"
    assert got["aspect"] == "9x16"
    assert (data / "settings.json").is_file()


def test_settings_survive_a_plugin_update(tmp_path):
    """更新外掛＝換一份外掛程式碼、資料夾路徑也換了；設定在資料夾以外，所以還在。"""
    data = tmp_path / "plugin-data"
    before = {p for p in ROOT.rglob("*") if ".venv" not in p.parts and "__pycache__" not in p.parts}
    assert run("settings", "set", "--data-dir", str(data), "--channel", "小明").returncode == 0
    after = {p for p in ROOT.rglob("*") if ".venv" not in p.parts and "__pycache__" not in p.parts}
    assert after == before  # 外掛資料夾裡什麼都沒寫

    elsewhere = tmp_path / "另一個工作資料夾"
    elsewhere.mkdir()
    proc = run("settings", "show", "--json", "--data-dir", str(data), cwd=elsewhere)
    assert json.loads(proc.stdout)["channel"] == "小明"


def test_without_data_dir_settings_go_to_application_support(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "ARTICLE_TO_VIDEO_DATA": "", "CLAUDE_PLUGIN_DATA": ""}
    assert run("settings", "set", "--channel", "小明", env=env).returncode == 0
    saved = home / "Library" / "Application Support" / "article-to-video" / "settings.json"
    assert json.loads(saved.read_text(encoding="utf-8"))["channel"] == "小明"


def test_bad_values_are_rejected_in_chinese_and_nothing_is_saved(tmp_path):
    data = tmp_path / "plugin-data"
    assert run("settings", "set", "--data-dir", str(data), "--channel", "小明").returncode == 0
    proc = run("settings", "set", "--data-dir", str(data),
               "--voice", "曉華", "--brand-color", "藍色", "--music", "搖滾", "--channel", "改名")
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    for needle in ("聲音「曉華」", "曉臻", "品牌色「藍色」", "#1A5FB4", "配樂「搖滾」", "沉穩鋼琴"):
        assert needle in proc.stderr, proc.stderr
    assert show(data)["channel"] == "小明"  # 有任何一個不對就整批不改


def test_the_channel_name_is_optional_and_can_be_cleared(tmp_path):
    """只想自己看懂文章、沒有頻道的人：頻道名可以不填，填過也能清掉（空字串，或跟 logo 一樣說 none）。
    看設定時說片尾卡不放名字就好，不叫他去設定。"""
    data = tmp_path / "plugin-data"
    assert run("settings", "set", "--data-dir", str(data), "--channel", "小明").returncode == 0
    proc = run("settings", "set", "--data-dir", str(data), "--channel", "")
    assert proc.returncode == 0, proc.stderr
    assert show(data)["channel"] == ""
    line = next(ln for ln in run("settings", "show", "--data-dir", str(data)).stdout.splitlines() if "頻道名" in ln)
    assert "片尾卡不放" in line and "請設定" not in line, line

    assert run("settings", "set", "--data-dir", str(data), "--channel", "小明").returncode == 0
    assert run("settings", "set", "--data-dir", str(data), "--channel", "none").returncode == 0
    assert show(data)["channel"] == ""


def test_a_light_brand_color_is_kept_and_the_charts_take_care_of_readability(tmp_path):
    """作者真正的品牌色可能很淺（例如淡黃）：照樣存起來，跟他說圖表上的字和線會用加深的版本。"""
    data = tmp_path / "plugin-data"
    proc = run("settings", "set", "--data-dir", str(data), "--brand-color", "#ffe680")
    assert proc.returncode == 0, proc.stderr
    assert show(data)["brand_color"] == "#FFE680"
    assert "#FFE680" in proc.stdout and "加深" in proc.stdout and "原色" in proc.stdout


def test_the_independent_review_setting_says_when_it_really_happens(tmp_path):
    """獨立審片是在作者看審片頁之前（出片之前）審一輪；設定的說明不能寫成「出片後」。"""
    data = tmp_path / "plugin-data"
    proc = run("settings", "set", "--data-dir", str(data), "--independent-review", "開")
    assert proc.returncode == 0, proc.stderr
    line = next(ln for ln in proc.stdout.splitlines() if "獨立審片" in ln)
    assert "出片後" not in line and "出片之後" not in line
    assert "作者看審片頁之前" in line
    helptext = run("settings", "set", "--help").stdout
    assert "出片後" not in helptext and "作者看審片頁之前" in helptext


def test_logo_is_copied_into_the_data_dir(tmp_path):
    """logo 複製一份進資料位置：原檔之後搬走、刪掉都沒關係。"""
    src = tmp_path / "我的 logo.png"
    src.write_bytes(_png(8, 8))
    data = tmp_path / "plugin-data"
    assert run("settings", "set", "--data-dir", str(data), "--logo", str(src)).returncode == 0
    src.unlink()
    got = show(data)
    assert (data / got["logo"]).read_bytes() == _png(8, 8)

    not_image = tmp_path / "notes.txt"
    not_image.write_text("不是圖", encoding="utf-8")
    proc = run("settings", "set", "--data-dir", str(data), "--logo", str(not_image))
    assert proc.returncode == 1 and "不是 PNG、JPG 或 WebP" in proc.stderr

    assert run("settings", "set", "--data-dir", str(data), "--logo", "none").returncode == 0
    assert show(data)["logo"] is None
    assert not list(data.glob("logo.*"))


# ---------------------------------------------------------------- 每支片的設定快照


def _new_project(tmp_path: Path) -> Path:
    """一支還沒建置過的片：settings.json 只有 Claude 寫的建置參數，沒有使用者設定的快照。"""
    import shutil

    from conftest import MINI_PROJECT

    root = tmp_path / "影片" / "2026-10-09-新的一支"
    shutil.copytree(MINI_PROJECT, root)
    (root / "settings.json").write_text(json.dumps({"gap": 0.4, "tail": 1.2}), encoding="utf-8")
    return root


def test_first_build_snapshots_the_user_settings_into_the_project(tmp_path):
    from article_to_video.build import plan_build

    data = tmp_path / "plugin-data"
    logo = tmp_path / "logo.png"
    logo.write_bytes(_png(8, 8))
    assert run("settings", "set", "--data-dir", str(data), "--channel", "小明的財經筆記", "--voice", "雲哲",
               "--music", "minimal", "--brand-color", "#1A5FB4", "--logo", str(logo)).returncode == 0
    root = _new_project(tmp_path)

    plan = plan_build(root, tts="fake", data_dir=data)
    snap = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    assert snap["gap"] == 0.4 and snap["tail"] == 1.2  # Claude 寫的不動
    assert snap["channel"] == "小明的財經筆記"
    assert snap["voice"] == "zh-TW-YunJheNeural"
    assert snap["music"] == "minimal"
    assert snap["brand_color"] == "#1A5FB4"
    assert (root / snap["logo"]).read_bytes() == _png(8, 8)  # logo 也複製一份進專案
    assert plan.project.settings.voice == "zh-TW-YunJheNeural"

    # 之後改了使用者設定：舊專案照舊（重建出來一樣），新專案才用新的
    assert run("settings", "set", "--data-dir", str(data), "--voice", "曉臻", "--channel", "改名了",
               "--logo", "none").returncode == 0
    plan_build(root, tts="fake", data_dir=data)
    again = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    assert again == snap
    assert (root / snap["logo"]).is_file()


def test_changing_one_project_leaves_the_user_settings_alone(tmp_path):
    from article_to_video.build import plan_build

    data = tmp_path / "plugin-data"
    assert run("settings", "set", "--data-dir", str(data), "--channel", "小明").returncode == 0
    root = _new_project(tmp_path)
    plan_build(root, tts="fake", data_dir=data)

    proc = run("settings", "set", "--project", str(root), "--voice", "曉雨", "--music", "none")
    assert proc.returncode == 0, proc.stderr
    plan = plan_build(root, tts="fake", data_dir=data)
    assert plan.project.settings.voice == "zh-TW-HsiaoYuNeural"
    assert plan.project.settings.music == "none"
    assert show(data)["voice"] == "zh-TW-HsiaoChenNeural"


def _png(w: int, h: int, rgb: tuple[int, int, int] = (20, 90, 180)) -> bytes:
    """標準庫寫一張純色 PNG。"""
    import struct
    import zlib

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    rows = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))

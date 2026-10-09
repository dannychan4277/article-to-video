"""使用者設定：頻道名、logo、品牌色、聲音、語速、配樂、預設比例。

存在外掛資料夾以外，更新外掛不受影響（${CLAUDE_PLUGIN_DATA} 在最後一個 scope 解除安裝時會被刪掉，除非加 --keep-data）：

    資料位置（第一個有值的）：
      1. 指令列 --data-dir（主 skill 傳 ${CLAUDE_PLUGIN_DATA}）
      2. 環境變數 ARTICLE_TO_VIDEO_DATA（測試用）、CLAUDE_PLUGIN_DATA（hook 裡有）
      3. ~/Library/Application Support/article-to-video/
    <資料位置>/
      settings.json    使用者設定（只記有設定過的欄位，其他用預設）
      logo.png         logo（設定時從使用者給的路徑複製進來；也可能是 .jpg／.webp）
      music/           配樂快取（程式作曲，刪掉會自動重產，一模一樣）
      previews/        安裝時試聽用的聲音、配樂樣本

每支片的專案資料夾有一份設定快照（<專案>/settings.json 裡的這幾個欄位）：第一次建置時從使用者設定
複製過去（logo 也複製一份進專案），之後使用者設定再怎麼改，舊專案重建出來都一樣。
要連這支片一起改，用 `settings set --project <專案> …`。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from article_to_video.music import TRACKS
from article_to_video.project import SETTINGS_FILE, ZH_TW_VOICES, Settings

__all__ = [
    "ASPECTS",
    "DATA_ENV",
    "SNAPSHOT_KEYS",
    "USER_ONLY_KEYS",
    "SettingsError",
    "UserSettings",
    "load_user_settings",
    "normalize_value",
    "resolve_data_dir",
    "snapshot_into_project",
    "update_project_settings",
    "update_user_settings",
]

DATA_ENV = "ARTICLE_TO_VIDEO_DATA"
FALLBACK_DATA_DIR = Path("Library") / "Application Support" / "article-to-video"
SNAPSHOT_KEYS = ("channel", "logo", "brand_color", "voice", "rate", "music", "aspect")
USER_ONLY_KEYS = ("independent_review",)
"""只在使用者設定、不進每支片設定快照的鍵（做片流程的開關，不影響成片長什麼樣子）。"""
_ON_WORDS = {"開", "开", "打開", "要", "on", "true", "yes", "1"}
_OFF_WORDS = {"關", "关", "關掉", "不要", "off", "false", "no", "0"}
ASPECTS = {"16x9": "橫式 16:9", "9x16": "直式 9:16"}
_ASPECT_ALIASES = {"16x9": "16x9", "16:9": "16x9", "橫式": "16x9", "横式": "16x9",
                   "9x16": "9x16", "9:16": "9x16", "直式": "9x16"}
_NONE_WORDS = {"", "none", "無", "不要", "沒有", "null"}
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RATE_RE = re.compile(r"^[+-]\d{1,3}%$")
MAX_CHANNEL_CHARS = 40
_IMAGE_KINDS = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"))


class SettingsError(ValueError):
    """設定值不對（繁中訊息，一次列出全部）。"""

    def __init__(self, problems: list[str] | str) -> None:
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        msg = self.problems[0] if len(self.problems) == 1 else "".join(f"\n- {p}" for p in self.problems)
        super().__init__(msg)


@dataclass(frozen=True)
class UserSettings:
    channel: str = ""
    """頻道名（片尾卡上的名字）。"""
    logo: str | None = None
    """logo 檔名（相對資料位置）。"""
    brand_color: str | None = None
    """品牌色 #RRGGBB；None＝圖表用預設紅黑白。"""
    voice: str = Settings.voice
    rate: str = Settings.rate
    music: str = Settings.music
    """配樂（music.TRACKS 的名字）或 "none"。"""
    aspect: str = Settings.aspect
    """預設比例："16x9" 或 "9x16"。"""
    independent_review: bool = True
    """作者看審片頁之前（出片之前）要不要派一個只看成片的 agent 獨立審片一輪（independent_review.py）。全部影片共用，不進設定快照。"""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- 位置


def resolve_data_dir(explicit: str | os.PathLike | None = None) -> Path:
    """資料位置。explicit 是沒被替換的 "${CLAUDE_PLUGIN_DATA}" 字樣時當作沒給。"""
    candidates = [str(explicit) if explicit is not None else "", os.environ.get(DATA_ENV, ""),
                  os.environ.get("CLAUDE_PLUGIN_DATA", "")]
    for c in candidates:
        if c.strip() and "${" not in c:
            return Path(c).expanduser().absolute()
    return Path.home() / FALLBACK_DATA_DIR


def _settings_file(data_dir: Path) -> Path:
    return data_dir / "settings.json"


# ---------------------------------------------------------------- 驗證


def _hex(value: str) -> str | None:
    m = _HEX_RE.match(value.strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return "#" + h.upper()


def _is_none(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in _NONE_WORDS)


def normalize_value(key: str, value: Any) -> Any:
    """把一個設定值換成存檔的樣子（中文名 → 代號、色碼大寫…）；不合法丟 SettingsError（繁中）。logo 另外處理。"""
    if key == "channel":
        if not isinstance(value, str):
            raise SettingsError("頻道名要是文字")
        v = " ".join(value.split())
        if len(v) > MAX_CHANNEL_CHARS:
            raise SettingsError(f"頻道名太長了（{len(v)} 個字），片尾卡放不下，請縮到 {MAX_CHANNEL_CHARS} 個字以內")
        return v
    if key == "brand_color":
        if _is_none(value):
            return None
        hx = _hex(value) if isinstance(value, str) else None
        if hx is None:
            raise SettingsError(f"品牌色「{value}」看不懂，要寫成色碼，例如 #1A5FB4")
        return hx  # 多淺都收：圖表上的可讀性由 brand.readable_on_white 處理（字、線、柱用加深的版本）
    if key == "voice":
        by_name = {name: vid for vid, name in ZH_TW_VOICES.items()}
        v = by_name.get(value, value) if isinstance(value, str) else value
        if v not in ZH_TW_VOICES:
            names = "、".join(f"{n}（{vid}）" for vid, n in ZH_TW_VOICES.items())
            raise SettingsError(f"聲音「{value}」不在支援清單裡，請從這三個挑：{names}")
        return v
    if key == "rate":
        v = value.strip() if isinstance(value, str) else value
        if isinstance(v, str) and re.fullmatch(r"\d{1,3}%", v):
            v = "+" + v
        if not isinstance(v, str) or not _RATE_RE.match(v):
            raise SettingsError(f"語速「{value}」格式不對，要寫成像 +5%、-10% 這樣")
        if not -50 <= int(v[:-1]) <= 50:
            raise SettingsError(f"語速 {v} 太極端了，請在 -50% 到 +50% 之間")
        return v
    if key == "music":
        if _is_none(value):
            return "none"
        by_label = {t.label: name for name, t in TRACKS.items()}
        v = by_label.get(value, value) if isinstance(value, str) else value
        if v not in TRACKS:
            names = "、".join(f"{t.label}（{n}）" for n, t in TRACKS.items())
            raise SettingsError(f"配樂「{value}」不存在，請從這幾首挑：{names}，或寫 none 不要配樂")
        return v
    if key == "aspect":
        v = _ASPECT_ALIASES.get(value.strip() if isinstance(value, str) else value)
        if v is None:
            raise SettingsError(f"預設比例「{value}」看不懂，只能是 16:9（橫式）或 9:16（直式）")
        return v
    if key == "independent_review":
        if isinstance(value, bool):
            return value
        v = value.strip().lower() if isinstance(value, str) else value
        if v in _ON_WORDS:
            return True
        if v in _OFF_WORDS:
            return False
        raise SettingsError(f"獨立審片「{value}」看不懂，寫「開」或「關」")
    raise SettingsError(f"不認得的設定「{key}」（認得的：{'、'.join(SNAPSHOT_KEYS + USER_ONLY_KEYS)}）")


def _image_suffix(path: Path) -> str:
    head = path.read_bytes()[:16]
    for magic, suffix in _IMAGE_KINDS:
        if head.startswith(magic):
            return suffix
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    raise SettingsError(f"logo「{path.name}」不是 PNG、JPG 或 WebP 圖檔")


def _copy_logo(src: str | os.PathLike, dest_dir: Path) -> str:
    """把 logo 複製到 dest_dir/logo.<副檔名>，回傳檔名。舊的 logo 檔一併清掉。"""
    p = Path(src).expanduser()
    if not p.is_file():
        raise SettingsError(f"找不到 logo 檔：{p}")
    suffix = _image_suffix(p)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = "logo" + suffix
    if p.resolve() != (dest_dir / name).resolve():
        for old in dest_dir.glob("logo.*"):
            if old.name != name:
                old.unlink()
        shutil.copyfile(p, dest_dir / name)
    return name


# ---------------------------------------------------------------- 讀寫


def _read_raw(path: Path, what: str) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SettingsError(f"{what}（{path}）第 {e.lineno} 行附近不是合法的 JSON，請修好或刪掉重設") from None
    if not isinstance(raw, dict):
        raise SettingsError(f"{what}（{path}）要是一個物件")
    return raw


def _write_raw(path: Path, raw: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dict(raw), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_user_settings(data_dir: str | os.PathLike | None = None) -> UserSettings:
    d = resolve_data_dir(data_dir)
    raw = _read_raw(_settings_file(d), "使用者設定")
    known = {f.name for f in fields(UserSettings)}
    values = {k: v for k, v in raw.items() if k in known}
    return UserSettings(**values)


def _apply_changes(raw: dict[str, Any], changes: Mapping[str, Any], logo_dir: Path) -> None:
    """先驗證全部，全部都對才動到 raw 與 logo 檔（有一個不對就整批不改）。"""
    problems: list[str] = []
    new: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "logo":
            continue
        try:
            new[key] = normalize_value(key, value)
        except SettingsError as e:
            problems.extend(e.problems)
    logo = changes.get("logo")
    if "logo" in changes and not _is_none(logo):
        p = Path(logo).expanduser()
        try:
            if not p.is_file():
                raise SettingsError(f"找不到 logo 檔：{p}")
            _image_suffix(p)
        except SettingsError as e:
            problems.extend(e.problems)
    if problems:
        raise SettingsError(problems)
    if "logo" in changes:
        if _is_none(logo):
            for old in logo_dir.glob("logo.*"):
                old.unlink()
            new["logo"] = None
        else:
            new["logo"] = _copy_logo(logo, logo_dir)
    raw.update(new)


def update_user_settings(changes: Mapping[str, Any], data_dir: str | os.PathLike | None = None) -> UserSettings:
    """改使用者設定（之後新做的片都用這份）。logo 給圖檔路徑（會複製進資料位置），給 "none" 拿掉。"""
    d = resolve_data_dir(data_dir)
    raw = _read_raw(_settings_file(d), "使用者設定")
    _apply_changes(raw, changes, d)
    _write_raw(_settings_file(d), raw)
    return load_user_settings(d)


def update_project_settings(project_dir: str | os.PathLike, changes: Mapping[str, Any]) -> dict[str, Any]:
    """只改這支片的設定快照（例：這支片換聲音）。logo 會複製進專案資料夾。"""
    root = Path(project_dir)
    if not root.is_dir():
        raise SettingsError(f"找不到專案資料夾 {root}")
    path = root / SETTINGS_FILE
    raw = _read_raw(path, "專案設定快照")
    _apply_changes(raw, changes, root)
    _write_raw(path, raw)
    return raw


def snapshot_into_project(project_dir: str | os.PathLike, data_dir: str | os.PathLike | None = None) -> list[str]:
    """專案的設定快照缺哪個欄位，就從使用者設定補上（logo 複製一份進專案）；已經有的不動。回傳補了哪些欄位。

    settings.json 壞掉（不是合法 JSON）時什麼都不做，讓載入專案時報出清楚的錯。
    """
    root = Path(project_dir)
    path = root / SETTINGS_FILE
    try:
        raw = _read_raw(path, "專案設定快照")
    except SettingsError:
        return []
    missing = [k for k in SNAPSHOT_KEYS if k not in raw]
    if not missing or not root.is_dir():
        return []
    d = resolve_data_dir(data_dir)
    user = load_user_settings(d)
    for key in missing:
        if key == "logo":
            raw["logo"] = _copy_logo(d / user.logo, root) if user.logo and (d / user.logo).is_file() else None
        else:
            raw[key] = getattr(user, key)
    _write_raw(path, raw)
    return missing

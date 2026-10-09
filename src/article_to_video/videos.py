"""影片資料夾：每支片在「影片／解說影片」裡有一個「日期-標題」資料夾，做片用的專案放在它的「專案」子資料夾。

    ~/Movies/解說影片/                       videos_root()：Finder 側邊欄的「影片」→「解說影片」
      2026-10-09-殖利率倒掛/                 一支片一個資料夾（日期＝開專案那天，標題＝文章標題或 --title）
        殖利率倒掛.mp4                        作者點頭之後 publish 才放進來（成片，16:9）
        字幕-殖利率倒掛.srt                   字幕檔：故意不跟成片同名，播放器才不會自動再疊一層字幕
        殖利率倒掛-直式.mp4、字幕-殖利率倒掛-直式.srt   有出直式才有
        專案/                                 專案資料夾（project.py）：文章、旁白稿、分鏡設定、審片頁、審片用的成片…
      安裝測試片/                             安裝時的測試片（sample_video.py；本身就是專案資料夾，不算一支片）

為什麼專案放子資料夾、成片另外複製出來（而不是直接用專案資料夾）：作者點頭之前也要建置成片（自動檢查、獨立審片都看成片），
那份留在「專案」裡給審片頁用；作者在 Finder 看到的「日期-標題」資料夾裡，只有他點頭過的成片和字幕檔，不會混著 JSON、
build/ 這些做片的東西，也不會把還沒審過的版本當成正式的片。

根目錄：環境變數 ARTICLE_TO_VIDEO_VIDEOS（測試指到 tmp）或指令的 --root，沒有就是 ~/Movies/解說影片。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from article_to_video.project import ProjectPaths

__all__ = [
    "PROJECT_DIR",
    "VIDEOS_ENV",
    "ProjectInfo",
    "derive_title",
    "format_projects",
    "list_projects",
    "new_project",
    "output_paths",
    "safe_title",
    "video_folder",
    "video_title",
    "videos_root",
]

VIDEOS_ENV = "ARTICLE_TO_VIDEO_VIDEOS"
PROJECT_DIR = "專案"
OUTPUT_RECORD = "output.json"
"""<專案>/output.json：每個比例最近一次出片放到哪裡、是審片頁第幾版（publish.py 寫）。"""
TITLE_MAX = 30
_FOLDER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def videos_root(explicit: str | os.PathLike | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().absolute()
    env = os.environ.get(VIDEOS_ENV, "").strip()
    if env:
        return Path(env).expanduser().absolute()
    return Path.home() / "Movies" / "解說影片"


def safe_title(text: str) -> str:
    """當資料夾名、檔名用：拿掉 Finder／檔案系統不能用的字（/ : 換成 -），空白併成一個，最多 30 字。"""
    t = _UNSAFE.sub("-", text)
    t = re.sub(r"\s+", " ", t).strip().lstrip(".")
    return t[:TITLE_MAX].strip() or "未命名"


def derive_title(paragraphs: Sequence[str]) -> str:
    """沒給標題時用文章第一段（通常就是標題）：在第一個冒號、破折號、句號之類的地方切，太長就截短。"""
    first = next((p.strip() for p in paragraphs if p.strip()), "")
    m = re.search(r"[：:｜|—–，。？！?!]", first)
    if m and m.start() >= 4:
        first = first[:m.start()]
    return safe_title(first[:24])


# ---------------------------------------------------------------- 一支片的資料夾


def video_folder(project_dir: str | os.PathLike) -> Path:
    """作者在 Finder 看到的那個「日期-標題」資料夾：專案資料夾叫「專案」就是它的上一層，不然就是專案資料夾本身。"""
    p = Path(project_dir).absolute()
    return p.parent if p.name == PROJECT_DIR else p


def video_title(project_dir: str | os.PathLike) -> str:
    name = video_folder(project_dir).name
    m = _FOLDER_RE.match(name)
    return m.group(2) if m else name


def output_paths(project_dir: str | os.PathLike, aspect: str) -> tuple[Path, Path]:
    """出片時成片與字幕檔要放的位置（16x9／9x16）。"""
    folder, title = video_folder(project_dir), video_title(project_dir)
    tail = "" if aspect == "16x9" else "-直式"
    return folder / f"{title}{tail}.mp4", folder / f"字幕-{title}{tail}.srt"


@dataclass(frozen=True)
class NewProject:
    folder: Path
    project: Path


def _free_folder(root: Path, day: str, title: str) -> Path:
    base = root / f"{day}-{title}"
    n = 2
    folder = base
    while folder.exists():
        folder = root / f"{day}-{title}-{n}"
        n += 1
    return folder


def new_project(source: str, *, title: str | None = None, root: str | os.PathLike | None = None,
                today: date | None = None) -> int:
    """指令列 `new`：建「日期-標題/專案」、擷取文章（含截字檢查）。擷取不下去就把剛建的資料夾整個拿掉，回 1。
    source 是 - 時從標準輸入讀作者貼的文字。沒給標題就用文章第一段（derive_title）。"""
    from article_to_video.article import ArticleError, extract_article, print_extract_summary
    from article_to_video.textio import STDIN, TextInputError, read_stdin

    base = videos_root(root)
    day = (today or date.today()).isoformat()
    try:
        text = read_stdin() if source == STDIN else None
        name = safe_title(title) if title else (derive_title(text.splitlines()) if text is not None else None)
        folder = _free_folder(base, day, name or "未命名")
        project = folder / PROJECT_DIR
        project.mkdir(parents=True)
        try:
            art = (extract_article(project, text=text) if text is not None
                   else extract_article(project, Path(source).expanduser()))
            if name is None:  # 檔案：擷取完才知道標題，資料夾整個改名
                target = base / f"{day}-{derive_title([p.text for p in art.paragraphs])}"
                if target != folder:
                    target = _free_folder(base, day, target.name[len(day) + 1:])
                    folder.rename(target)
                    folder, project = target, target / PROJECT_DIR
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    except ArticleError as e:
        print(f"擷取失敗：{e}", file=sys.stderr)
        return 1
    except TextInputError as e:
        print(f"開不了新專案：{e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"開不了新專案：檔案讀寫出錯（{e.strerror or e}：{e.filename or ''}）。請確認「{base}」可以寫入、磁碟還有空間。",
              file=sys.stderr)
        return 1
    print(f"專案資料夾：{project}")
    print(f"（作者在 Finder 看到的是：影片 → {base.name} → {folder.name}；出片之後成片會放在這裡）")
    print_extract_summary(art, where=project / "article")
    return 0


# ---------------------------------------------------------------- 舊專案


@dataclass(frozen=True)
class ProjectInfo:
    title: str
    date: str | None
    folder: Path
    project: Path
    modified: float
    shots: int | None
    """分鏡數；還沒有分鏡設定是 None。"""
    has_narration: bool
    review_versions: dict[str, int]
    """比例 → 審片頁第幾版。"""
    outputs: dict[str, dict[str, Any]]
    """比例 → 最近一次出片（output.json）。"""

    @property
    def stage(self) -> str:
        if self.outputs:
            return "已出片"
        if self.review_versions:
            return "審片中"
        if self.shots is not None:
            return "寫好分鏡，還沒預覽"
        if self.has_narration:
            return "寫好旁白，還沒寫分鏡"
        return "還沒寫旁白"

    def to_json(self) -> dict[str, Any]:
        return {"title": self.title, "date": self.date, "folder": str(self.folder), "project": str(self.project),
                "modified": datetime.fromtimestamp(self.modified).isoformat(timespec="seconds"),
                "stage": self.stage, "shots": self.shots, "reviewVersions": self.review_versions,
                "outputs": self.outputs}


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_outputs(project_dir: str | os.PathLike) -> dict[str, dict[str, Any]]:
    raw = _load(Path(project_dir) / OUTPUT_RECORD)
    outs = raw.get("outputs") if isinstance(raw, dict) else None
    return {k: v for k, v in outs.items() if isinstance(v, dict)} if isinstance(outs, dict) else {}


def _info(folder: Path) -> ProjectInfo | None:
    project = folder / PROJECT_DIR
    if not project.is_dir():
        return None
    paths = ProjectPaths(project)
    m = _FOLDER_RE.match(folder.name)
    shots_raw = _load(paths.shots)
    shots = len(shots_raw["shots"]) if isinstance(shots_raw, dict) and isinstance(shots_raw.get("shots"), list) else None
    versions: dict[str, int] = {}
    for review in sorted(paths.build.glob("*/review.json")):
        v = _load(review)
        if isinstance(v, dict) and isinstance(v.get("version"), int):
            versions[review.parent.name] = v["version"]
    mtimes = [p.stat().st_mtime for p in project.rglob("*") if p.is_file()] or [project.stat().st_mtime]
    return ProjectInfo(
        title=m.group(2) if m else folder.name, date=m.group(1) if m else None, folder=folder, project=project,
        modified=max(mtimes), shots=shots, has_narration=paths.narration.is_file(), review_versions=versions,
        outputs=load_outputs(project),
    )


def list_projects(root: str | os.PathLike | None = None, query: str = "") -> list[ProjectInfo]:
    """影片資料夾裡的每支片，最近動過的排前面；query 只留標題含這幾個字的。"""
    base = videos_root(root)
    if not base.is_dir():
        return []
    infos = [i for f in base.iterdir() if f.is_dir() and (i := _info(f)) is not None]
    if query:
        infos = [i for i in infos if query in i.title or query in i.folder.name]
    return sorted(infos, key=lambda i: i.modified, reverse=True)


def format_projects(infos: Sequence[ProjectInfo], root: Path) -> str:
    if not infos:
        return f"「{root}」裡還沒有做過的影片。"
    lines = [f"影片資料夾：{root}（最近動過的在前面）"]
    for n, i in enumerate(infos, 1):
        when = datetime.fromtimestamp(i.modified).strftime("%Y-%m-%d %H:%M")
        bits = [i.stage]
        if i.shots is not None:
            bits.append(f"{i.shots} 個分鏡")
        for aspect, v in i.review_versions.items():
            bits.append(f"審片頁第 {v} 版" + ("" if aspect == "16x9" else "（直式）"))
        for aspect, o in i.outputs.items():
            bits.append(f"{'直式' if aspect == '9x16' else ''}出片：第 {o.get('reviewVersion', '?')} 版")
        lines.append(f"{n}. {i.title}（{i.date or '沒有日期'}；最後修改 {when}）：{'，'.join(bits)}")
        lines.append(f"   專案資料夾：{i.project}")
    return "\n".join(lines)

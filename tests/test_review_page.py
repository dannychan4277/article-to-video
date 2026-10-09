"""審片頁：預覽模式（只出分鏡靜態圖＋審片頁）與出片後的審片頁。

看的是作者會看到的東西：專案資料夾裡的「審片頁.html」（用 HTML 解析器讀卡片），以及卡片引用的靜態圖檔。
預期值從測試專案的旁白與假配音層規則在這裡自己寫，不呼叫被測程式。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import pytest

REVIEW_PAGE = "審片頁.html"

# 測試專案：SH01 從開頭到 s02「曲線」之前；SH02（接續分鏡）從「曲線」到片尾
EXPECTED_NARRATION = {
    "SH01": ["兩年期殖利率，平均四點五八。", "十年期只有三點九六，"],
    "SH02": ["曲線倒掛。"],
}


@dataclass
class Card:
    id: str
    number: int
    changed: bool = False
    narration: str = ""
    timecode: str = ""
    comment_boxes: int = 0
    stills: list[tuple[str, float]] = field(default_factory=list)
    """(圖檔 src, 片中秒數)；第一張是完成狀態。"""
    slots: dict[str, str] = field(default_factory=dict)
    """data-slot → 裡面的文字（數字對帳、自動檢查）。"""


@dataclass
class ReviewPage:
    title: str = ""
    cards: list[Card] = field(default_factory=list)
    videos: list[str] = field(default_factory=list)
    overall_boxes: int = 0
    menu_text: str = ""
    changes_text: str = ""
    """「這版改了哪些分鏡」那一區的文字。"""
    page_slots: dict[str, str] = field(default_factory=dict)
    attrs: dict[str, str] = field(default_factory=dict)


class _Parser(HTMLParser):
    """認 class：card（data-shot-id、data-shot-number、data-changed）、narration、timecode、still（data-t）、
    comment、overall、slot（data-slot）、menu；id=changes；<video src>。"""

    def __init__(self) -> None:
        super().__init__()
        self.page = ReviewPage()
        self.stack: list[tuple[str, set[str], dict[str, str]]] = []
        self.capture: list[tuple[str, int]] = []  # (目標, 開始時的 stack 深度)
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        cls = set(a.get("class", "").split())
        if tag == "html":
            self.page.attrs = a
        if tag == "title":
            self.in_title = True
        if "card" in cls and tag == "article":
            self.page.cards.append(Card(id=a["data-shot-id"], number=int(a["data-shot-number"]),
                                        changed=a.get("data-changed") == "true"))
        card = self.page.cards[-1] if self._in_card() else None
        if tag == "img" and "still" in cls and card is not None:
            card.stills.append((a["src"], float(a["data-t"])))
        if tag == "video" and "src" in a:
            self.page.videos.append(a["src"])
        if tag == "source" and "src" in a and self.stack and self.stack[-1][0] == "video":
            self.page.videos.append(a["src"])
        if tag == "textarea" and "comment" in cls and card is not None:
            card.comment_boxes += 1
        if tag == "textarea" and "overall" in cls:
            self.page.overall_boxes += 1
        if tag not in ("img", "source", "input", "br", "meta", "link"):
            self.stack.append((tag, cls, a))
            for target in ("narration", "timecode", "menu"):
                if target in cls:
                    self.capture.append((target, len(self.stack)))
            if "slot" in cls:
                self.capture.append(("slot:" + a.get("data-slot", ""), len(self.stack)))
            if a.get("id") == "changes":
                self.capture.append(("changes", len(self.stack)))

    def _in_card(self) -> bool:
        return any(tag == "article" and "card" in c for tag, c, _ in self.stack)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in ("img", "source", "input", "br", "meta", "link"):
            return
        if self.stack:
            self.stack.pop()
        self.capture = [(t, d) for t, d in self.capture if d <= len(self.stack)]

    def handle_data(self, data):
        if self.in_title:
            self.page.title += data
        for target, _ in self.capture:
            card = self.page.cards[-1] if self.page.cards else None
            if target == "menu":
                self.page.menu_text += data
            elif target == "changes":
                self.page.changes_text += data
            elif target.startswith("slot:"):
                name = target[5:]
                if self._in_card() and card is not None:
                    card.slots[name] = card.slots.get(name, "") + data
                else:
                    self.page.page_slots[name] = self.page.page_slots.get(name, "") + data
            elif card is not None:
                setattr(card, target, getattr(card, target) + data)


def read_review(project_dir: Path) -> ReviewPage:
    p = _Parser()
    p.feed((project_dir / REVIEW_PAGE).read_text(encoding="utf-8"))
    return p.page


def local_file(project_dir: Path, src: str) -> Path:
    assert not src.startswith(("http:", "https:", "//")), f"審片頁不能引用網路資源：{src}"
    return project_dir / unquote(src)


# ---------------------------------------------------------------- 預覽模式


def test_preview_writes_one_card_per_shot_each_with_its_narration(previewed_project):
    page = read_review(previewed_project.dir)
    assert [(c.number, c.id) for c in page.cards] == [(1, "SH01"), (2, "SH02")]
    for card in page.cards:
        for piece in EXPECTED_NARRATION[card.id]:
            assert piece in card.narration, f"分鏡 {card.number} 的卡片少了旁白「{piece}」：{card.narration!r}"
        assert card.comment_boxes == 1
    assert page.overall_boxes == 1


def test_preview_renders_stills_only_not_the_video(previewed_project):
    root = previewed_project.dir
    assert not (root / "成片.mp4").exists()
    assert not (root / "字幕.srt").exists()
    clips = root / "build" / "16x9" / "clips"
    assert not clips.exists() or not list(clips.glob("*.mp4"))
    for card in read_review(root).cards:
        assert card.stills, f"分鏡 {card.number} 沒有靜態圖"
        for src, _ in card.stills:
            png = local_file(root, src)
            assert png.is_file(), src
            assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


FPS = 30
SH01_END = 5.7  # s02「曲線」開頭 3.7＋2.15−0.15（假配音規則）
BAR2_AT = 3.75  # s02「十年期」開頭＋0.1 秒：第二根柱開始長
HIGHLIGHT_AT = 2.75  # s01「四點五八」唸完：標註出現
VIDEO_END = 8.4


def png_rgb(path: Path, crop: tuple[int, int, int, int]) -> bytes:
    from article_to_video.ffmpeg import find_ffmpeg

    x, y, w, h = crop
    proc = subprocess.run(
        [str(find_ffmpeg()), "-v", "error", "-i", str(path), "-vf", f"crop={w}:{h}:{x}:{y}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    )
    assert len(proc.stdout) == w * h * 3
    return proc.stdout


def bluish(px: bytes) -> int:
    """柱子（主色藍或淡化後的灰藍）像素數。"""
    return sum(1 for i in range(0, len(px), 3) if px[i + 2] - px[i] >= 12 and px[i] <= 215)


SECOND_BAR = (1100, 300, 450, 440)  # 第二根柱在畫面右半邊


def test_first_still_of_each_card_is_the_shots_finished_state(previewed_project):
    page = read_review(previewed_project.dir)
    ends = {"SH01": SH01_END, "SH02": VIDEO_END}
    for card in page.cards:
        _, t = card.stills[0]
        assert abs(t - (ends[card.id] - 1 / FPS)) < 0.5 / FPS, f"分鏡 {card.number} 的第一張不是最後一幀：{t}"
    final_png = local_file(previewed_project.dir, page.cards[0].stills[0][0])
    assert bluish(png_rgb(final_png, SECOND_BAR)) > 500  # 完成狀態：第二根柱已經長好


def test_cards_also_show_the_moment_just_before_each_later_beat(previewed_project):
    page = read_review(previewed_project.dir)
    sh01 = page.cards[0]
    moments = {round(t * FPS): src for src, t in sh01.stills[1:]}
    for beat in (HIGHLIGHT_AT, BAR2_AT):
        before = round(beat * FPS + 0.5) - 1  # 那個畫面事件出現的前一幀
        assert before in moments, f"沒有 {beat} 秒那個事件之前的畫面：{sorted(moments)}"
    before_bar2 = local_file(previewed_project.dir, moments[round(BAR2_AT * FPS + 0.5) - 1])
    assert bluish(png_rgb(before_bar2, SECOND_BAR)) == 0  # 第二根柱還沒出來
    assert all(t < SH01_END for _, t in sh01.stills)


def test_each_card_shows_where_the_shot_sits_in_the_video(previewed_project):
    page = read_review(previewed_project.dir)
    assert [c.timecode.strip() for c in page.cards] == ["0:00.0–0:05.7", "0:05.7–0:08.4"]


# ---------------------------------------------------------------- 出片之後


def test_after_the_final_build_the_page_embeds_the_video(built_project):
    page = read_review(built_project.dir)
    assert page.attrs.get("data-stage") == "final"
    assert len(page.videos) == 1
    assert local_file(built_project.dir, page.videos[0]).resolve() == built_project.video.resolve()
    assert [(c.number, c.id) for c in page.cards] == [(1, "SH01"), (2, "SH02")]
    for card in page.cards:
        assert all(piece in card.narration for piece in EXPECTED_NARRATION[card.id])
        assert card.stills and local_file(built_project.dir, card.stills[0][0]).is_file()



def run_cli(command: str, root: Path) -> None:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-m", "article_to_video", command, str(root), "--tts", "fake"],
                          capture_output=True, text=True, encoding="utf-8", env=env)
    assert proc.returncode == 0, f"{command} 失敗：\n{proc.stdout}\n{proc.stderr}"


def edit_json(path: Path, fn) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


@pytest.fixture
def copy_of_built(built_project, tmp_path) -> Path:
    dst = tmp_path / "2026-10-09-殖利率曲線"
    shutil.copytree(built_project.dir, dst)
    return dst


def changed_ids(page: ReviewPage) -> list[str]:
    return [c.id for c in page.cards if c.changed]


def test_rebuilt_page_marks_the_shots_that_changed_since_the_last_version(copy_of_built):
    root = copy_of_built
    first = read_review(root)
    assert first.attrs["data-version"] == "1"
    assert changed_ids(first) == []

    edit_json(root / "shots.json", lambda d: d["shots"][1]["chart"]["data"]["highlight"].update(text="長天期偏低"))
    run_cli("preview", root)
    page = read_review(root)
    assert page.attrs["data-version"] == "2"
    assert changed_ids(page) == ["SH02"]
    assert "分鏡 2" in page.changes_text

    run_cli("build", root)  # 沒再改：出片後還是第 2 版，標記留著
    page = read_review(root)
    assert page.attrs["data-stage"] == "final"
    assert page.attrs["data-version"] == "2"
    assert changed_ids(page) == ["SH02"]


def test_narration_edits_count_as_changes_to_the_shots_that_say_them(copy_of_built):
    root = copy_of_built
    edit_json(root / "narration.json", lambda d: d["segments"].update(s02="十年期僅有三點九六，曲線倒掛。"))
    run_cli("preview", root)
    page = read_review(root)
    assert changed_ids(page) == ["SH01"]  # 「僅有」在 SH01 那段唸；SH02 的旁白和畫面都沒變
    assert "十年期僅有三點九六" in page.cards[0].narration


def test_page_says_which_shots_were_removed(copy_of_built):
    root = copy_of_built
    edit_json(root / "shots.json", lambda d: d["shots"].pop(1))
    run_cli("preview", root)
    page = read_review(root)
    assert [c.id for c in page.cards] == ["SH01"]
    assert "拿掉了" in page.changes_text and "分鏡 2" in page.changes_text

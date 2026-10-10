"""審片頁在瀏覽器裡的行為：用無頭 Chromium 以本機檔案（file://）打開，像作者一樣打字、按按鈕。

「複製給 Claude」產生的文字用 article_to_video.feedback.parse_feedback 解析回來比對，
所以這裡同時守住「頁面產生的格式」和「主 skill 用的解析器」是同一套。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright

from article_to_video.feedback import ShotComment, parse_feedback

PROJECT_NAME = "殖利率曲線"
"""「複製給 Claude」標頭寫的是片名：專案資料夾「2026-10-09-殖利率曲線」去掉日期。"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page_url(previewed_project) -> str:
    return (previewed_project.dir / "審片頁.html").as_uri()


def open_page(browser, url: str, *, clipboard: bool = True, init_script: str | None = None) -> Page:
    ctx = browser.new_context()
    if clipboard:
        ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    if init_script:
        ctx.add_init_script(init_script)
    page = ctx.new_page()
    page.goto(url)
    return page


def comment_box(page: Page, shot_id: str):
    return page.locator(f'textarea.comment[data-shot-id="{shot_id}"]')


def copy_button(page: Page):
    return page.get_by_role("button", name="複製給 Claude")


def test_copy_for_claude_gives_one_line_per_comment_then_the_overall_comment(browser, page_url):
    page = open_page(browser, page_url)
    comment_box(page, "SH02").fill("標註晚一點出來\n等唸到「倒掛」")
    page.locator("textarea#overall").fill("節奏可以")
    copy_button(page).click()
    page.wait_for_function("document.getElementById('copy-status').dataset.tone === 'ok'")

    copied = page.evaluate("navigator.clipboard.readText()")
    shown = page.locator("#copy-out").input_value()
    assert copied == shown
    fb = parse_feedback(copied)
    assert fb.project == PROJECT_NAME
    assert fb.version == 1
    assert fb.shots == [ShotComment(2, "SH02", "標註晚一點出來\n等唸到「倒掛」")]
    assert fb.overall == "節奏可以"
    assert copied.splitlines()[1] == "分鏡 2（SH02）：標註晚一點出來"
    page.context.close()


def test_copy_still_works_when_the_page_cannot_use_the_clipboard_api(browser, page_url):
    """以本機檔案開啟時瀏覽器可能不給 Clipboard API：改用選取＋execCommand；連那個都不行就選好文字請作者按 ⌘C。"""
    no_api = "Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true});"
    for extra, tone in (("", "ok"), ("document.execCommand = () => false;", "warn")):
        page = open_page(browser, page_url, clipboard=False, init_script=no_api + extra)
        comment_box(page, "SH01").fill("標題改短")
        copy_button(page).click()
        status = page.locator("#copy-status")
        assert status.get_attribute("data-tone") == tone
        out = page.locator("#copy-out")
        assert out.is_visible()
        text = out.input_value()
        assert parse_feedback(text).shots == [ShotComment(1, "SH01", "標題改短")]
        selected = page.evaluate("(() => { const t = document.getElementById('copy-out');"
                                 " return document.activeElement === t && t.selectionStart === 0 && t.selectionEnd === t.value.length; })()")
        assert selected, "文字要先選好，作者按 ⌘C 就能複製"
        if tone == "warn":
            assert "⌘C" in status.inner_text()
        page.context.close()


def test_nothing_to_copy_tells_the_author_what_to_do(browser, page_url):
    page = open_page(browser, page_url)
    copy_button(page).click()
    assert "還沒寫任何留言" in page.locator("#copy-status").inner_text()
    assert not page.locator("#copy-out").is_visible()
    page.context.close()


def test_typed_comments_survive_a_reload(browser, page_url):
    page = open_page(browser, page_url)
    comment_box(page, "SH01").fill("數字要跟原文一樣")
    page.locator("textarea#overall").fill("整體不錯")
    page.reload()
    assert comment_box(page, "SH01").input_value() == "數字要跟原文一樣"
    assert page.locator("textarea#overall").input_value() == "整體不錯"
    page.context.close()


def test_page_still_works_when_the_browser_blocks_storage(browser, page_url):
    blocked = ("Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked'); }});")
    page = open_page(browser, page_url, init_script=blocked)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    comment_box(page, "SH01").fill("好")
    copy_button(page).click()
    page.wait_for_function("document.getElementById('copy-status').dataset.tone === 'ok'")
    assert errors == []
    page.context.close()


def test_menu_examples_go_into_the_overall_comment(browser, page_url):
    page = open_page(browser, page_url)
    menu = page.locator("#menu")
    assert "能加什麼" in menu.inner_text()
    examples = menu.locator("button.example")
    assert examples.count() >= 10
    first, second = examples.nth(0).inner_text().strip(), examples.nth(1).inner_text().strip()
    examples.nth(0).click()
    examples.nth(1).click()
    assert page.locator("textarea#overall").input_value() == f"{first}\n{second}"
    page.context.close()


def test_page_loads_nothing_from_the_network(browser, page_url):
    page = browser.new_page()
    urls: list[str] = []
    page.on("request", lambda r: urls.append(r.url))
    page.goto(page_url)
    page.wait_for_load_state("load")
    page.evaluate("Promise.all(Array.from(document.images).map(i => { i.loading = 'eager'; return i.decode().catch(() => {}); }))")
    assert urls and all(u.startswith("file://") for u in urls), [u for u in urls if not u.startswith("file://")]
    page.close()



def _preview(root: Path) -> None:
    from conftest import run_cli

    proc = run_cli("preview", root, "--tts", "fake")
    assert proc.returncode == 0, proc.stderr


def _change_a_shot(root: Path) -> None:
    shots = json.loads((root / "shots.json").read_text(encoding="utf-8"))
    shots["shots"][1]["chart"]["data"]["highlight"]["text"] = "長天期偏低"
    (root / "shots.json").write_text(json.dumps(shots, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("copied_before_rebuild", [False, True])
def test_comments_not_yet_sent_are_offered_back_after_a_rebuild(browser, previewed_project, tmp_path, copied_before_rebuild):
    root = tmp_path / "2026-10-09-殖利率曲線"
    shutil.copytree(previewed_project.dir, root)
    _preview(root)  # 在這個資料夾重寫一次（瀏覽器存留言的鍵跟著專案資料夾的路徑）
    url = (root / "審片頁.html").as_uri()
    page = open_page(browser, url)
    comment_box(page, "SH01").fill("這句旁白太長")
    if copied_before_rebuild:
        copy_button(page).click()
        page.wait_for_function("document.getElementById('copy-status').dataset.tone === 'ok'")

    _change_a_shot(root)
    _preview(root)  # 第 2 版
    page.reload()
    assert page.locator("html").get_attribute("data-version") == "2"
    carry = page.locator("#carry")
    if copied_before_rebuild:  # 已經交給 Claude 了：新的一版從空白開始
        assert not carry.is_visible()
        assert comment_box(page, "SH01").input_value() == ""
    else:
        assert carry.is_visible()
        assert "第 1 版" in carry.inner_text()
        page.get_by_role("button", name="放回來").click()
        assert comment_box(page, "SH01").input_value() == "這句旁白太長"
    page.context.close()

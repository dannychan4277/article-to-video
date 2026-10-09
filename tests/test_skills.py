"""所有 skill（#13）：每一行要 Claude 跑的指令都被那支 skill 自己的 allowed-tools 預先核准（範圍要窄）、指令和選項真的存在、
會讀寫使用者設定的指令都帶 --data-dir ${CLAUDE_PLUGIN_DATA}；主流程兩支用講的就能叫；skill 指到的說明檔都在。

skill 裡要 Claude 跑的指令＝沒有語言標記的程式碼區塊（```）的每一行，加上行內 `uv …`；
寫成 ```json、```text 的區塊是範例，不算指令。
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))
PREFIXES = ("uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video ",
            "~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video ")
VIDEOS = "~/Movies/解說影片"


def parse_skill(name: str) -> tuple[dict[str, str], list[str], str]:
    text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    meta: dict[str, str] = {}
    rules: list[str] = []
    for line in front.splitlines():
        if line.startswith("  - "):
            rules.append(line[4:].strip())
        elif line.strip():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
            if key.strip() == "allowed-tools" and value.strip():
                rules.append(value.strip())
    return meta, rules, body


def commands_in(body: str) -> list[str]:
    blocks = re.findall(r"```\n(.*?)```", body, flags=re.S)
    cmds = [ln.strip() for b in blocks for ln in b.splitlines() if ln.strip()]
    inline = [c for c in re.findall(r"`([^`\n]+)`", body) if c.startswith(("uv ", "~/.local/bin/uv "))]
    return cmds + inline


def bash_rules(rules: list[str]) -> list[str]:
    return [m.group(1) for r in rules if (m := re.fullmatch(r"Bash\((.*)\)", r))]


def allowed(cmd: str, rules: list[str]) -> bool:
    """Claude Code 的 Bash 規則：整串比對，* 代表任何文字；結尾「 *」也比對到沒有參數的指令。"""
    for pat in bash_rules(rules):
        pats = [pat, pat[:-2]] if pat.endswith(" *") else [pat]
        if any(fnmatch.fnmatchcase(cmd, p) for p in pats):
            return True
    return False


_HELP: dict[tuple[str, ...], str] = {}


def help_text(*argv: str) -> str:
    """子指令的 --help（in-process；同一個子指令只跑一次）。"""
    import contextlib
    import io

    from article_to_video import cli

    if argv not in _HELP:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), pytest.raises(SystemExit) as e:
            cli.main([*argv, "--help"])
        assert e.value.code == 0, f"沒有這個指令：{argv}"
        _HELP[argv] = buf.getvalue()
    return _HELP[argv]


def subcommand(args: list[str]) -> tuple[str, ...]:
    return tuple(args[:2]) if args[0] in ("samples", "settings") else (args[0],)


def test_every_skill_is_checked():
    assert {"make-video", "edit-video", "setup", "check-env", "report-issue"} <= set(SKILLS)


@pytest.mark.parametrize("name", SKILLS)
def test_every_command_is_preapproved_real_and_keeps_settings_outside_the_plugin(name):
    _, rules, body = parse_skill(name)
    cmds = commands_in(body)
    assert cmds, "skill 裡沒有指令？"
    for cmd in cmds:
        assert allowed(cmd, rules), f"skill 叫了沒預先核准的指令：{cmd}"
        prefix = next((p for p in PREFIXES if cmd.startswith(p)), None)
        if prefix is None:
            continue
        args = cmd[len(prefix):].split()
        helps = help_text(*subcommand(args))
        for flag in re.findall(r"(?<!\S)--[a-z][a-z-]*", cmd[len(prefix):]):
            assert flag in helps, f"{' '.join(subcommand(args))} 沒有選項 {flag}：{cmd}"
        if "--data-dir" in helps:  # 讀寫使用者設定的指令：設定要在外掛資料夾外面，更新外掛不會不見
            assert "--data-dir ${CLAUDE_PLUGIN_DATA}" in cmd, cmd


@pytest.mark.parametrize("name", SKILLS)
def test_preapproved_rules_stay_narrow(name):
    _, rules, _ = parse_skill(name)
    for pat in bash_rules(rules):
        assert pat.count("*") <= 1 and not pat.startswith("*"), pat
        if "article_to_video" in pat:
            assert pat.startswith(PREFIXES), pat
            sub = pat.split("article_to_video ", 1)[1]
            assert sub != "*" and not sub.startswith("*"), f"放行了外掛的所有子指令：{pat}"
        assert pat not in ("uv run *", "sh *", "curl *", "afplay *", "open *", "rm *"), pat
    for r in rules:
        assert re.fullmatch(r"(Bash|Read|Edit)\(.+\)", r), f"看不懂的規則：{r}"
        if r.startswith(("Read(", "Edit(")):
            assert r[5:-1] == f"{VIDEOS}/**", f"讀寫檔案只放行影片資料夾：{r}"


@pytest.mark.parametrize("name, triggers", [
    ("make-video", ["做成影片"]),
    ("edit-video", ["改影片", "審片留言", "可以出片", "上次那支"]),
])
def test_main_flow_skills_trigger_on_plain_words(name, triggers):
    meta, rules, _ = parse_skill(name)
    assert meta["name"] == name
    for t in triggers:
        assert t in meta["description"], t
    assert "disable-model-invocation" not in meta  # 作者用講的，Claude 要能自己叫
    assert len(meta["description"]) < 1536
    # 專案在 ~/Movies/解說影片 裡（不在 Claude 開工的資料夾）：寫旁白稿、分鏡設定要預先核准
    assert f"Edit({VIDEOS}/**)" in rules and f"Read({VIDEOS}/**)" in rules


@pytest.mark.parametrize("name", ["make-video", "edit-video"])
def test_main_flow_skills_point_at_files_that_exist(name):
    _, _, body = parse_skill(name)
    refs = re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\s`）)]+)", body)
    assert refs
    for ref in refs:
        assert (ROOT / ref).exists(), ref
    for ref in ("narration-rules.md", "storyboard-rules.md", "formats.md", "vox-rules.md", "checks-and-review.md"):
        assert ref in body or name == "edit-video", ref


def test_json_formats_in_the_skill_docs_are_valid_projects(tmp_path):
    """formats.md 的 narration.json、shots.json 範例照抄就是能讀的專案（Claude 照著寫不會一開始就錯）。"""
    from article_to_video.project import load_project

    text = (ROOT / "skills" / "make-video" / "references" / "formats.md").read_text(encoding="utf-8")
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", text, flags=re.S) if b.lstrip().startswith("{\"")]
    narration = next(b for b in blocks if "segments" in b)
    shots = next(b for b in blocks if "shots" in b)
    (tmp_path / "narration.json").write_text(json.dumps(narration, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "shots.json").write_text(json.dumps(shots, ensure_ascii=False), encoding="utf-8")
    project = load_project(tmp_path)
    assert [s.id for s in project.shots] == ["SH01", "SH02"]

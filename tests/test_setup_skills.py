"""安裝與設定、檢查環境兩個 skill（#12）：自然語言觸發、預先核准的指令剛好涵蓋 skill 裡叫的指令、指令和選項真的存在。"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
PREFIXES = ("uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video ",
            "~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video ")


def parse_skill(name: str) -> tuple[dict[str, str], list[str], str]:
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    meta: dict[str, str] = {}
    rules: list[str] = []
    for line in front.splitlines():
        if line.startswith("  - "):
            rules.append(line[4:].strip())
        elif line.strip():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, rules, body


def commands_in(body: str) -> list[str]:
    """skill 裡要 Claude 跑的指令：程式碼區塊的每一行，加上行內 `uv …` 這種。"""
    blocks = re.findall(r"```\n(.*?)```", body, flags=re.S)
    cmds = [ln.strip() for b in blocks for ln in b.splitlines() if ln.strip()]
    inline = [c for c in re.findall(r"`([^`\n]+)`", body) if c.startswith(("uv ", "~/.local/bin/uv "))]
    return cmds + inline


def allowed(cmd: str, rules: list[str]) -> bool:
    """Claude Code 的 Bash 規則：整串比對，* 代表任何文字；結尾「 *」也比對到沒有參數的指令。"""
    for rule in rules:
        m = re.fullmatch(r"Bash\((.*)\)", rule)
        assert m, f"不是 Bash 規則：{rule}"
        pat = m.group(1)
        pats = [pat, pat[:-2]] if pat.endswith(" *") else [pat]
        if any(fnmatch.fnmatchcase(cmd, p) for p in pats):
            return True
    return False


def help_text(capsys, *argv: str) -> str:
    from article_to_video import cli

    with pytest.raises(SystemExit) as e:
        cli.main([*argv, "--help"])
    assert e.value.code == 0, f"沒有這個指令：{argv}"
    return capsys.readouterr().out


@pytest.mark.parametrize("name, triggers", [("setup", ["安裝", "設定", "換聲音", "換配樂"]), ("check-env", ["檢查環境"])])
def test_skill_triggers_on_plain_words(name, triggers):
    meta, _, _ = parse_skill(name)
    assert meta["name"] == name
    for t in triggers:
        assert f"「{t}」" in meta["description"]
    assert "disable-model-invocation" not in meta  # 作者用講的，Claude 要能自己叫
    assert len(meta["description"]) < 1536  # 清單裡會被截斷的長度


@pytest.mark.parametrize("name", ["setup", "check-env"])
def test_every_command_in_the_skill_is_preapproved_and_real(name, capsys):
    _, rules, body = parse_skill(name)
    cmds = commands_in(body)
    assert cmds
    helps: list[str] = []
    for cmd in cmds:
        assert allowed(cmd, rules), f"skill 叫了沒預先核准的指令：{cmd}"
        prefix = next((p for p in PREFIXES if cmd.startswith(p)), None)
        if prefix is None:
            continue
        args = cmd[len(prefix):].split()
        sub = args[:2] if args[0] in ("samples", "settings") else args[:1]
        helps.append(help_text(capsys, *sub))
        # 設定、試聽檔都要放在外掛的資料位置（更新外掛不會不見）
        assert "--data-dir ${CLAUDE_PLUGIN_DATA}" in cmd, cmd
    not_ours = {"--project", "--version", "--create-dirs"}  # uv、curl 的選項
    flags = {f.rstrip("。，、）") for f in re.findall(r"--[a-z][a-z-]+", body)} - not_ours
    for flag in flags:
        assert any(flag in h for h in helps), f"skill 用了指令沒有的選項 {flag}"


@pytest.mark.parametrize("name", ["setup", "check-env"])
def test_preapproved_rules_stay_narrow(name):
    _, rules, _ = parse_skill(name)
    for rule in rules:
        pat = rule[len("Bash("):-1]
        assert pat.count("*") <= 1 and not pat.startswith("*"), rule
        if "article_to_video" in pat:
            assert pat.startswith(PREFIXES), rule
            sub = pat.split("article_to_video ", 1)[1]
            assert sub != "*" and not sub.startswith("*"), f"放行了外掛的所有子指令：{rule}"
        assert pat not in ("uv run *", "sh *", "curl *", "afplay *"), rule


def test_check_env_skill_runs_only_the_check():
    _, rules, _ = parse_skill("check-env")
    assert all(" check-env " in r for r in rules)


def test_fixing_a_broken_plugin_never_costs_the_author_his_settings():
    """外掛檔案壞掉時的修法：/plugin 的 Update now 重新下載外掛檔案。解除安裝（重新安裝的第一步）會把外掛資料夾
    ${CLAUDE_PLUGIN_DATA}（頻道名、logo、聲音…）一起刪掉，所以不能叫作者「重新安裝、設定不會不見」。"""
    from article_to_video.environment import SAY_UPDATE_PLUGIN

    _, _, body = parse_skill("check-env")
    for text in (body, SAY_UPDATE_PLUGIN):
        assert "Update now" in text and "解除安裝會把" in text and "設定一起刪掉" in text, text
        assert "設定不會不見" not in text and "重新安裝" not in text, text

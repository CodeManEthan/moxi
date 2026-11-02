"""The shipped moxi-handoff skill: where the announcement says, named as its folder, and
saying what the handoff code does."""

from __future__ import annotations

import re
from importlib import resources

from moxi_launcher import handoffs, module


def skill_text() -> str:
    folder = resources.files("moxi_launcher").joinpath(module.SKILLS)
    return folder.joinpath("moxi-handoff", "SKILL.md").read_text(encoding="utf-8")


def test_the_skill_ships_in_the_skills_folder_with_its_name_and_description():
    text = skill_text()
    front = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert front, "the skill opens with its front matter"
    fields = dict(line.split(": ", 1) for line in front.group(1).splitlines())
    assert fields["name"] == "moxi-handoff"
    assert fields["description"]


def test_the_skill_states_the_folder_and_the_file_name_rule():
    text = skill_text()
    assert "handoff dir:" in text
    assert "moxi launcher handoff dir" in text
    assert "<YYYY-MM-DD>-<key>.md" in text
    assert handoffs.file_name("lead", "2026-10-30", 2) == "2026-10-30-lead-2.md"
    assert "`-2`, then `-3`" in text


def test_the_skill_states_the_recycle_steps():
    text = skill_text()
    section = text.split("## Recycling yourself", 1)[1]
    assert "recycles at its limit" in section
    assert "let your subagents finish" in section
    assert "moxi launcher relaunch --self --handoff <file>" in section
    assert "End the turn" in section
    assert "`relaunched`" in section and "read the handoff" in section
    assert "background shell" not in section

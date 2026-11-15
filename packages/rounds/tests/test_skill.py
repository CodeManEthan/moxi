"""The shipped moxi-rounds skill: the one skill, where the announcement says, named as its
folder, and judgment rather than a command manual."""

from __future__ import annotations

import re
from importlib import resources

from moxi import modules, names

from moxi_rounds import module


def skills_folder():
    return resources.files("moxi_rounds").joinpath(module.SKILLS)


def skill_text() -> str:
    return skills_folder().joinpath("moxi-rounds", "SKILL.md").read_text(encoding="utf-8")


def front_and_body(text: str) -> tuple[dict[str, str], str]:
    found = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    assert found, "the skill opens with its front matter"
    fields = dict(line.split(": ", 1) for line in found.group(1).splitlines())
    return fields, found.group(2)


def test_the_announcement_names_the_skills_folder_as_the_core_reads_it():
    assert modules.load("rounds").skills == "skills"


def test_the_package_ships_one_skill_named_as_its_folder():
    folders = sorted(entry.name for entry in skills_folder().iterdir()
                     if entry.is_dir() and names.is_skill_dir(entry.name))
    assert folders == ["moxi-rounds"]
    assert names.is_skill_name("moxi-rounds")
    fields, _ = front_and_body(skill_text())
    assert fields["name"] == "moxi-rounds"


def test_the_description_is_one_plain_line_a_harness_reads_without_quoting():
    fields, _ = front_and_body(skill_text())
    description = fields["description"]
    assert description and "\n" not in description
    # A harness reads the front matter as YAML, where a colon and a space would start a
    # mapping inside the plain value.
    assert ": " not in description and not description.startswith(("'", '"'))


def test_the_skill_is_about_a_thousand_words():
    _, body = front_and_body(skill_text())
    assert 800 <= len(body.split()) <= 1200


def test_the_skill_holds_judgment_and_leaves_the_commands_to_next():
    _, body = front_and_body(skill_text())
    assert "moxi rounds next" in body and "--help" in body
    assert "```" not in body
    flags = set(re.findall(r"--[a-z][a-z-]*", body))
    assert flags == {"--help"}


def test_the_skill_covers_each_call_a_tool_cannot_check():
    _, body = front_and_body(skill_text())
    headings = re.findall(r"^## (.+)$", body, re.M)
    assert headings == ["The brake on design passes", "Which tier does what",
                        "Is a decision major?", "Cutting slices", "Writing a ruling",
                        "Writing a question", "What a re-check reads"]


def test_the_skill_names_no_tier_or_model_of_its_own():
    _, body = front_and_body(skill_text())
    for word in ("first", "second", "third"):
        assert f"`{word}`" not in body
    assert not re.search(r"\b(opus|sonnet|haiku|gpt)\b", body, re.I)

"""A project's checklist, watched paths and verifier's brief lines, and the watched-paths
guard's switch: what each holds, what the config check refuses, and how each is read."""

from __future__ import annotations

import pytest
from moxi import config
from moxi.units import switch_problem

from moxi_rounds import module, settings


def refusal(text: str) -> config.ConfigError:
    with pytest.raises(config.ConfigError) as caught:
        settings.check_text(text)
    assert caught.value.problem and caught.value.fix
    return caught.value


ITEM = '{ id = "public-surface", text = "Adds a public command.", skills = ["layout-review"] }'
ENTRY = '{ pattern = "packages/*/", note = "A new package folder.", skills = ["layout-review"] }'


def test_a_project_with_no_table_has_none_of_the_three():
    read = settings.load()
    assert {key: settings.project_setting(read, "proj-a", key)
            for key in settings.PROJECT_KEYS} == {
        "checklist": [], "watched_paths": [], "verifier_brief": []}


def test_the_three_are_read_from_the_projects_own_table_only(write_config):
    write_config(f"""\
        [projects.proj-a]
        checklist = [{ITEM}]
        watched_paths = [{ENTRY}]
        verifier_brief = ["Review check: no machine's own path."]
        """)
    read = settings.load()
    assert settings.project_setting(read, "PROJ-A", "checklist") == [
        {"id": "public-surface", "text": "Adds a public command.", "skills": ["layout-review"]}]
    assert settings.project_setting(read, "proj-a", "watched_paths")[0]["pattern"] == "packages/*/"
    assert settings.project_setting(read, "proj-a", "verifier_brief") == [
        "Review check: no machine's own path."]
    assert settings.project_setting(read, "proj-b", "checklist") == []
    settings.project_setting(read, "proj-a", "checklist").clear()
    assert settings.project_setting(read, "proj-a", "checklist")


def test_the_three_have_no_defaults_form():
    for key in settings.PROJECT_KEYS:
        assert f"defaults.{key}" not in settings.schema().keys
        with pytest.raises(KeyError):
            settings.round_setting(settings.load(), "proj-a", key)
        with pytest.raises(KeyError):
            settings.build_setting(settings.load(), "proj-a", key)
    with pytest.raises(config.ConfigError):
        settings.check_text(f"[defaults]\nchecklist = [{ITEM}]\n")


@pytest.mark.parametrize("body, key, words", [
    ('checklist = "x"', "checklist", "not a list"),
    ('checklist = ["x"]', "checklist[0]", "not a table"),
    ('checklist = [{ id = "a", text = "t", skills = ["s"], more = 1 }]', "checklist[0].more",
     "not a key"),
    ('checklist = [{ text = "t", skills = ["s"] }]', "checklist[0].id", "missing"),
    ('checklist = [{ id = "Public", text = "t", skills = ["s"] }]', "checklist[0].id",
     "not an item id"),
    ('checklist = [{ id = "a--b", text = "t", skills = ["s"] }]', "checklist[0].id",
     "not an item id"),
    (f'checklist = [{{ id = "{"a" * 33}", text = "t", skills = ["s"] }}]',
     "checklist[0].id", "not an item id"),
    ('checklist = [{ id = "a", text = "t", skills = ["s"] }, '
     '{ id = "a", text = "u", skills = ["s"] }]', "checklist[1].id", "used before"),
    ('checklist = [{ id = "a", text = "", skills = ["s"] }]', "checklist[0].text",
     "one line"),
    ('checklist = [{ id = "a", text = "t\\nu", skills = ["s"] }]', "checklist[0].text",
     "one line"),
    ('checklist = [{ id = "a", text = "t", skills = [] }]', "checklist[0].skills",
     "not a list of skill names"),
    ('checklist = [{ id = "a", text = "t" }]', "checklist[0].skills", "missing"),
    ('checklist = [{ id = "a", text = "t", skills = ["s", "s"] }]', "checklist[0].skills",
     "twice"),
    ('verifier_brief = "one line"', "verifier_brief", "not a list of strings"),
    ('verifier_brief = [1]', "verifier_brief", "not a list of strings"),
])
def test_a_malformed_checklist_or_brief_is_refused(body, key, words):
    error = refusal(f"[projects.proj-a]\n{body}\n")
    assert error.key == f"projects.proj-a.{key}"
    assert words in error.problem


@pytest.mark.parametrize("name", ["skills/layout", "~/x", "a b", "a\\b", ":x", "x:",
                                  "a:b:c", "-x", ".x", ""])
def test_a_skill_named_as_no_harness_lists_one_is_refused(name):
    error = refusal(f'[projects.proj-a]\nchecklist = [{{ id = "a", text = "t", '
                    f'skills = ["{name.replace(chr(92), chr(92) * 2)}"] }}]\n')
    assert "not a skill's name" in error.problem
    assert error.fix == "name the skill as the harness lists it, as layout-review"


@pytest.mark.parametrize("name", ["layout-review", "Layout_Review.2", "kit:layout-review"])
def test_a_skill_named_as_a_harness_lists_one_passes(name):
    settings.check_text(f'[projects.proj-a]\nchecklist = [{{ id = "a", text = "t", '
                        f'skills = ["{name}"] }}]\n')


@pytest.mark.parametrize("pattern, words", [
    ("", "empty"), ("/", "not relative"), ("/abs", "not relative"),
    ("C:/x", "drive"), ("a//b", "empty part"), ("a//", "empty part"),
    ("./a", ". or .."), ("a/../b", ". or .."), ("a**/b", "**"), ("a/x**", "**"),
    ("a/[b", "no closing"),
])
def test_a_pattern_that_is_no_path_inside_the_project_is_refused(pattern, words):
    error = refusal(f'[projects.proj-a]\nwatched_paths = [{{ pattern = "{pattern}", '
                    f'note = "n", skills = ["s"] }}]\n')
    assert error.key == "projects.proj-a.watched_paths[0].pattern"
    assert words in error.problem


def test_a_pattern_with_a_backslash_is_refused_with_the_slash_fix():
    error = refusal('[projects.proj-a]\nwatched_paths = [{ pattern = "packages\\\\x", '
                    'note = "n", skills = ["s"] }]\n')
    assert error.fix == "write / between parts; it matches a path written with \\ too"


@pytest.mark.parametrize("pattern", ["packages/*/", "packages/*/pyproject.toml",
                                     "src/**", "**/x.py", "a/[ab]?.py", "x"])
def test_a_pattern_inside_the_project_passes_and_one_trailing_slash_marks_a_dir(
        pattern):
    assert settings.pattern_problem(pattern) == ""
    settings.check_text(f'[projects.proj-a]\nwatched_paths = [{{ pattern = "{pattern}", '
                        f'note = "n", skills = ["s"], only_new = true }}]\n')


@pytest.mark.parametrize("body, key, words", [
    ('watched_paths = {}', "watched_paths", "not a list"),
    ('watched_paths = [{ pattern = "a", note = "n", skills = ["s"], text = "x" }]',
     "watched_paths[0].text", "not a key"),
    ('watched_paths = [{ pattern = "a", skills = ["s"] }]', "watched_paths[0].note",
     "missing"),
    ('watched_paths = [{ pattern = "a", note = "n" }]', "watched_paths[0].skills",
     "missing"),
    ('watched_paths = [{ pattern = "a", note = "n", skills = ["s"], only_new = 1 }]',
     "watched_paths[0].only_new", "not a boolean"),
])
def test_a_malformed_watched_paths_entry_is_refused(body, key, words):
    error = refusal(f"[projects.proj-a]\n{body}\n")
    assert error.key == f"projects.proj-a.{key}"
    assert words in error.problem


def test_an_unknown_project_key_names_the_three_in_its_fix():
    error = refusal("[projects.proj-a]\nchecklists = []\n")
    for key in settings.PROJECT_KEYS:
        assert key in error.fix


def test_the_guards_switch_is_off_by_default_and_passes_the_cores_switch_check():
    assert settings.GUARD_SWITCH == "watched_paths_guard.enabled"
    assert settings.load()[settings.GUARD_SWITCH] is False
    assert switch_problem(settings.schema(), settings.GUARD_SWITCH) is None
    settings.check_text("[watched_paths_guard]\nenabled = true\n")


def test_the_install_asks_about_the_switch():
    assert set(module.QUESTIONS) == {settings.GUARD_SWITCH}
    text = module.QUESTIONS[settings.GUARD_SWITCH]
    assert text.startswith("the watched-paths guard") and "never refuses" in text
    assert "watched-paths guard" in module.OPTIONAL["turns"]

"""A project's own settings: what a project table may hold, each table the config check
refuses, and a setting read for a round or for a build."""

from __future__ import annotations

import pytest
from moxi import config

from moxi_rounds import settings


def refusal(text: str) -> config.ConfigError:
    """The error the module's check raises for config text `text`."""
    with pytest.raises(config.ConfigError) as caught:
        settings.check_text(text)
    assert caught.value.exit_code == 2
    assert caught.value.problem and caught.value.fix
    return caught.value


def project(body: str) -> str:
    """A config text with one project table, `proj-a`, holding `body`."""
    return f"[projects.proj-a]\n{body}\n"


# -- what a stranger gets ----------------------------------------------------------------

def test_an_empty_config_gives_a_round_the_defaults():
    read = settings.load()
    assert {key: settings.round_setting(read, "proj-a", key)
            for key in settings.ROUND_KEYS} == {
        "flow": "full", "dir": "rounds", "ratify": "majors", "trace": False,
        "idle_days": 14, "dormant_days": 30, "slice_minutes": 45, "sitting_at": 10}


def test_an_empty_config_gives_a_build_a_local_merge_and_no_check():
    read = settings.load()
    assert {key: settings.build_setting(read, "proj-a", key)
            for key in settings.BUILD_KEYS} == {
        "into": None, "check": None, "finish": [{"do": "merge", "id": "merge"}],
        "check_on": "merged", "shared_paths": [], "keep_branch": False,
        "lock_ref": None, "brief": []}


def test_an_empty_config_passes_the_check_and_ranks_the_three_built_in_flows():
    assert settings.check_text("") is None
    assert settings.load()["tiers"] == ["fix", "small", "full"]


# -- a setting for a round or for a build ------------------------------------------------

BOTH = """\
    [defaults]
    flow = "small"
    trace = true
    check_on = "tip"
    sitting_at = 4

    [projects.proj-a]
    flow = "fix"
    dir = "/srv/rounds"
    ratify = "off"
    idle_days = 3
    check_on = "merged"
    into = "main"
    check = { command = "make check", timeout_s = 600 }
    finish = [ { do = "merge" }, { do = "push", remote = "origin" } ]
    shared_paths = ["*.lock"]
    keep_branch = true
    lock_ref = "refs/locks/finish"
    brief = ["Run only your own tests."]
"""


def test_a_projects_own_value_is_read_over_the_default(write_config):
    write_config(BOTH)
    read = settings.load()
    assert [settings.round_setting(read, "proj-a", key)
            for key in ("flow", "dir", "ratify", "idle_days")] == [
        "fix", "/srv/rounds", "off", 3]
    assert settings.build_setting(read, "proj-a", "check_on") == "merged"
    assert settings.build_setting(read, "proj-a", "into") == "main"
    assert settings.build_setting(read, "proj-a", "check") == {"command": "make check",
                                                              "timeout_s": 600}
    assert settings.build_setting(read, "proj-a", "shared_paths") == ["*.lock"]
    assert settings.build_setting(read, "proj-a", "keep_branch") is True
    assert settings.build_setting(read, "proj-a", "lock_ref") == "refs/locks/finish"
    assert settings.build_setting(read, "proj-a", "brief") == ["Run only your own tests."]


def test_a_key_the_project_leaves_out_reads_the_defaults_table(write_config):
    write_config(BOTH)
    read = settings.load()
    assert settings.round_setting(read, "proj-a", "trace") is True
    assert settings.round_setting(read, "proj-a", "sitting_at") == 4
    assert settings.round_setting(read, "proj-a", "dormant_days") == 30


def test_a_project_with_no_table_reads_the_defaults_table(write_config):
    write_config(BOTH)
    read = settings.load()
    assert settings.round_setting(read, "proj-b", "flow") == "small"
    assert settings.build_setting(read, "proj-b", "check_on") == "tip"
    assert settings.build_setting(read, "proj-b", "finish") == [{"do": "merge",
                                                                 "id": "merge"}]


def test_a_round_reads_its_home_project_and_a_build_its_repo(write_config):
    write_config("""\
        [projects.proj-a]
        trace = true
        check_on = "tip"

        [projects.proj-b]
        into = "release"
    """)
    read = settings.load()
    assert settings.round_setting(read, "proj-a", "trace") is True
    assert settings.build_setting(read, "proj-b", "check_on") == "merged"
    assert settings.build_setting(read, "proj-b", "into") == "release"
    assert settings.build_setting(read, "proj-a", "into") is None


def test_a_project_table_is_found_ignoring_case_as_the_projects_list_finds_a_key(
        write_config):
    write_config("""\
        [projects.PROJ-A]
        flow = "fix"
        into = "release"
    """)
    read = settings.load()
    assert settings.round_setting(read, "proj-a", "flow") == "fix"
    assert settings.build_setting(read, "Proj-A", "into") == "release"
    assert settings.project_table(read, "proj-b") == {}


def test_two_tables_that_name_one_project_in_two_cases_are_refused():
    error = refusal('[projects.proj-a]\nflow = "fix"\n\n[projects.PROJ-A]\n'
                    'flow = "small"\n')
    assert error.key == "projects.PROJ-A"
    assert "names the project that [projects.proj-a] names" in error.problem
    assert "[projects.proj-a]" in error.fix


def test_the_keys_for_a_round_and_for_a_build_share_none():
    assert not set(settings.ROUND_KEYS) & set(settings.BUILD_KEYS)
    assert set(settings.ROUND_KEYS) | {"check_on"} >= {
        key.name.removeprefix("defaults.") for key in settings.schema().live()
        if key.name.startswith("defaults.")}


@pytest.mark.parametrize("key", settings.BUILD_KEYS)
def test_a_builds_key_is_not_read_for_a_round(key):
    with pytest.raises(KeyError):
        settings.round_setting(settings.load(), "proj-a", key)


@pytest.mark.parametrize("key", settings.ROUND_KEYS)
def test_a_rounds_key_is_not_read_for_a_build(key):
    with pytest.raises(KeyError):
        settings.build_setting(settings.load(), "proj-a", key)


def test_a_value_as_read_is_the_callers_own_copy(write_config):
    write_config(BOTH)
    settings.build_setting(settings.load(), "proj-a", "finish").clear()
    settings.build_setting(settings.load(), "proj-b", "finish")[0]["id"] = "changed"
    settings.build_setting(settings.load(), "proj-b", "brief").append("changed")
    read = settings.load()
    assert len(settings.build_setting(read, "proj-a", "finish")) == 2
    assert settings.build_setting(read, "proj-b", "finish") == [{"do": "merge",
                                                                 "id": "merge"}]
    assert settings.build_setting(read, "proj-b", "brief") == []


# -- finish actions and their ids --------------------------------------------------------

def test_a_merge_and_a_push_take_their_kind_as_their_id(write_config):
    write_config(project(
        'finish = [ { do = "merge" }, { do = "push", remote = "origin" } ]'))
    assert settings.build_setting(settings.load(), "proj-a", "finish") == [
        {"do": "merge", "id": "merge"},
        {"do": "push", "remote": "origin", "id": "push"}]


def test_an_action_keeps_the_id_it_names(write_config):
    write_config(project(
        'finish = [ { do = "push", id = "upstream", remote = "origin" }, '
        '{ do = "command", id = "pr", argv = ["gh", "pr", "create", "--head", '
        '"{branch}", "--fill"] }, '
        '{ do = "command", id = "deploy", argv = ["./deploy.sh", "{tip}"], '
        'timeout_s = 900, repeat = true } ]'))
    actions = settings.build_setting(settings.load(), "proj-a", "finish")
    assert [action["id"] for action in actions] == ["upstream", "pr", "deploy"]
    assert actions[2] == {"do": "command", "id": "deploy",
                          "argv": ["./deploy.sh", "{tip}"], "timeout_s": 900,
                          "repeat": True}


def test_two_pushes_are_told_apart_by_their_ids():
    assert settings.check_text(project(
        'finish = [ { do = "push", remote = "origin" }, '
        '{ do = "push", id = "mirror", remote = "mirror" } ]')) is None


def test_a_command_action_with_no_id_is_refused():
    error = refusal(project('finish = [ { do = "merge" }, '
                            '{ do = "command", argv = ["./deploy.sh"] } ]'))
    assert error.key == "projects.proj-a.finish[1].id"
    assert "is missing" in error.problem and 'id = "deploy"' in error.fix


@pytest.mark.parametrize("actions, key", [
    ('{ do = "merge" }, { do = "merge" }', "projects.proj-a.finish[1]"),
    ('{ do = "push", remote = "origin" }, { do = "push", remote = "mirror" }',
     "projects.proj-a.finish[1]"),
    ('{ do = "command", id = "pr", argv = ["a"] }, '
     '{ do = "command", id = "pr", argv = ["b"] }', "projects.proj-a.finish[1].id"),
    ('{ do = "merge" }, { do = "command", id = "merge", argv = ["a"] }',
     "projects.proj-a.finish[1].id"),
    ('{ do = "push", id = "merge", remote = "origin" }, { do = "merge" }',
     "projects.proj-a.finish[1]"),
])
def test_two_finish_actions_with_one_id_are_refused(actions, key):
    error = refusal(project(f"finish = [ {actions} ]"))
    assert error.key == key
    assert "the id of the action at projects.proj-a.finish[0] too" in error.problem
    assert error.fix == "give each action an `id` of its own"


@pytest.mark.parametrize("value, key, words", [
    ('"merge"', "finish", "is a string, not a list"),
    ('[ "merge" ]', "finish[0]", "is a string, not a table"),
    ('[ { id = "merge" } ]', "finish[0].do", "is missing"),
    ('[ { do = "rebase" } ]', "finish[0].do", "which it cannot be"),
    ('[ { do = "merge", remote = "origin" } ]', "finish[0].remote",
     "is not a key of a merge action"),
    ('[ { do = "push", remote = "origin", repeat = true } ]', "finish[0].repeat",
     "is not a key of a push action"),
    ('[ { do = "push" } ]', "finish[0].remote", "not a name"),
    ('[ { do = "merge", id = "" } ]', "finish[0].id", "not a name"),
    ('[ { do = "command", id = "pr" } ]', "finish[0].argv", "is missing"),
    ('[ { do = "command", id = "pr", argv = [] } ]', "finish[0].argv",
     "is not a command"),
    ('[ { do = "command", id = "pr", argv = ["a"], timeout_s = -1 } ]',
     "finish[0].timeout_s", "is not a number of seconds"),
    ('[ { do = "command", id = "pr", argv = ["a"], repeat = "yes" } ]',
     "finish[0].repeat", "is a string, not a boolean"),
])
def test_a_malformed_finish_action_is_refused(value, key, words):
    error = refusal(project(f"finish = {value}"))
    assert error.key == f"projects.proj-a.{key}" and words in error.problem


# -- each project table the check refuses ------------------------------------------------

def test_an_unknown_key_in_a_project_table_is_refused():
    error = refusal(project('branch = "main"'))
    assert error.key == "projects.proj-a.branch"
    assert error.problem == "is not a key of a project table"
    assert "into" in error.fix and "sitting_at" in error.fix


def test_a_key_of_the_file_that_is_not_a_projects_is_refused_in_its_table():
    assert refusal(project("tiers = []")).key == "projects.proj-a.tiers"
    assert refusal(project("worktree_dir = \"/srv/trees\"")).key == (
        "projects.proj-a.worktree_dir")


@pytest.mark.parametrize("command, words", [
    ("make 'check", "does not split into a command and its arguments"),
    ('uv run \\"pytest', "does not split into a command and its arguments"),
    ("", "splits into no command"),
    ("   ", "splits into no command"),
])
def test_a_check_command_that_does_not_split_is_refused(command, words):
    error = refusal(project(f'check = {{ command = "{command}", timeout_s = 60 }}'))
    assert error.key == "projects.proj-a.check.command"
    assert words in error.problem


def test_a_check_command_that_splits_is_accepted():
    assert settings.check_text(project(
        'check = { command = "uv run --locked pytest -k \'not slow\'" }')) is None


@pytest.mark.parametrize("value, key, words", [
    ('"make check"', "check", "is a string, not a table"),
    ("{ timeout_s = 60 }", "check.command", "is missing"),
    ('{ command = ["make"] }', "check.command", "is not a command line"),
    ('{ command = "make", timeout_s = 0 }', "check.timeout_s",
     "is not a number of seconds"),
    ('{ command = "make", timeout = 60 }', "check.timeout", "is not a key of a check"),
])
def test_a_malformed_check_is_refused(value, key, words):
    error = refusal(project(f"check = {value}"))
    assert error.key == f"projects.proj-a.{key}" and words in error.problem


def test_a_sitting_count_below_zero_is_refused_in_the_defaults():
    error = refusal("[defaults]\nsitting_at = -1\n")
    assert error.key == "defaults.sitting_at"
    assert error.problem == "is -1, below 0" and "or 0" in error.fix


def test_a_sitting_count_below_zero_is_refused_in_a_project_table():
    error = refusal(project("sitting_at = -3"))
    assert error.key == "projects.proj-a.sitting_at"
    assert error.problem == "is -3, below 0"


def test_a_sitting_count_of_zero_is_accepted():
    assert settings.check_text("[defaults]\nsitting_at = 0\n"
                               + project("sitting_at = 0")) is None


@pytest.mark.parametrize("body, key, words", [
    ('ratify = "all"', "ratify", "which it cannot be"),
    ('check_on = "branch"', "check_on", "which it cannot be"),
    ('trace = "yes"', "trace", "is a string, not a boolean"),
    ("idle_days = true", "idle_days", "is a boolean, not an int"),
    ('slice_minutes = "45"', "slice_minutes", "is a string, not an int"),
    ("flow = 3", "flow", "is an int, not a string"),
    ("dir = 3", "dir", "is not a folder"),
    ("into = 3", "into", "is an int, not a name"),
    ('keep_branch = "yes"', "keep_branch", "is a string, not a boolean"),
    ('shared_paths = "*.lock"', "shared_paths", "is not a list of strings"),
    ("brief = [1]", "brief", "is not a list of strings"),
    ("lock_ref = false", "lock_ref", "is a boolean, not a name"),
])
def test_a_project_value_of_the_wrong_type_is_refused(body, key, words):
    error = refusal(project(body))
    assert error.key == f"projects.proj-a.{key}" and words in error.problem


def test_a_project_that_is_not_a_table_is_refused():
    error = refusal("[projects]\nproj-a = \"main\"\n")
    assert error.key == "projects.proj-a" and "is a string, not a table" in error.problem


# -- a relative rounds folder ------------------------------------------------------------

def test_a_relative_dir_is_refused_for_a_project_with_no_notes_folder(
        add_project, without_notes):
    add_project("proj-a")
    error = refusal(project('dir = "designs"'))
    assert error.key == "projects.proj-a.dir"
    assert "is the relative path 'designs'" in error.problem
    assert "has no notes folder" in error.problem
    assert "absolute" in error.fix
    assert "moxi projects set proj-a --notes-root" in error.fix


def test_a_relative_dir_is_accepted_when_the_projects_record_names_a_notes_folder(
        add_project, without_notes, tmp_path):
    add_project("proj-a", notes_root=tmp_path / "notes" / "proj-a")
    assert settings.has_notes_folder("proj-a") is True
    assert settings.check_text(project('dir = "designs"')) is None


def test_a_relative_dir_is_accepted_when_notes_gives_the_project_a_folder(add_project):
    add_project("proj-a")
    assert settings.has_notes_folder("proj-a") is True
    assert settings.check_text(project('dir = "designs"')) is None


def test_an_absolute_dir_is_accepted_for_a_project_with_no_notes_folder(
        add_project, without_notes, tmp_path):
    add_project("proj-a")
    assert settings.has_notes_folder("proj-a") is False
    assert settings.check_text(project(f'dir = "{tmp_path / "rounds"}"')) is None
    assert settings.check_text(project('dir = "~/rounds"')) is None


def test_a_relative_dir_is_not_judged_for_a_project_the_list_does_not_hold(
        without_notes):
    assert settings.has_notes_folder("proj-a") is None
    assert settings.check_text(project('dir = "designs"')) is None


def test_the_default_relative_dir_alone_refuses_no_config(add_project, without_notes):
    add_project("proj-a")
    assert settings.check_text(project('into = "main"')) is None
    assert settings.check_text("") is None


def test_loading_a_refused_file_raises_and_names_it(write_config, add_project,
                                                    without_notes):
    add_project("proj-a")
    path = write_config(project('dir = "designs"'))
    with pytest.raises(config.ConfigError) as caught:
        settings.load()
    assert caught.value.file == str(path)
    assert caught.value.key == "projects.proj-a.dir"


def test_moxi_config_check_names_the_key_a_project_table_is_refused_for(
        write_config, capsys):
    from moxi import cli

    path = write_config(project('finish = [ { do = "command", argv = ["./deploy.sh"] } ]'))
    assert cli.main(["config", "rounds", "check"]) == 1
    err = capsys.readouterr().err
    assert str(path) in err and "key 'projects.proj-a.finish[0].id'" in err
    assert "fix: name the action" in err

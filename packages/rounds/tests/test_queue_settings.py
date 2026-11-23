"""A queue project's settings: `land`, the check's `scoped` command, and the project's
`director`, read from its own table else `[defaults]`, with a director's name held to a
name's shape by `moxi config rounds check`."""

from __future__ import annotations

import pytest
from moxi import config

from moxi_rounds import settings

SCOPED = ('check = { command = "make check", scoped = "make check BASE={base}", '
          'timeout_s = 60 }')


def refusal(text: str) -> config.ConfigError:
    with pytest.raises(config.ConfigError) as caught:
        settings.check_text(text)
    return caught.value


def project(body: str, name: str = "proj-a") -> str:
    return f"[projects.{name}]\n{body}\n"


def test_a_project_lands_by_finish_unless_its_table_says_queue(write_config):
    write_config(project(f'land = "queue"\n{SCOPED}') + project("", "proj-b"))
    read = settings.load()
    assert settings.build_setting(read, "proj-a", "land") == settings.LAND_QUEUE
    assert settings.build_setting(read, "proj-b", "land") == settings.LAND_FINISH


def test_land_takes_finish_or_queue_and_nothing_else():
    error = refusal(project(f'land = "batch"\n{SCOPED}'))
    assert error.key == "projects.proj-a.land" and "which it cannot be" in error.problem
    assert '"finish"' in error.fix and '"queue"' in error.fix


def test_land_is_not_a_defaults_key():
    error = refusal('[defaults]\nland = "queue"\n')
    assert "defaults.land" in str(error)


def test_a_queue_project_whose_check_names_no_scoped_command_is_refused():
    error = refusal(project('land = "queue"\ncheck = { command = "make check" }'))
    assert error.key == "projects.proj-a.land"
    assert "no scoped command" in error.problem and "{base}" in error.fix


def test_the_scoped_command_is_read_with_the_check(write_config):
    write_config(project(f'land = "queue"\n{SCOPED}'))
    check = settings.build_setting(settings.load(), "proj-a", "check")
    assert check["scoped"] == "make check BASE={base}"


@pytest.mark.parametrize("scoped, words", [
    ("\"make 'x\"", "does not split"),
    ('""', "splits into no command"),
    ("3", "is not a command line"),
])
def test_a_scoped_command_that_is_no_command_line_is_refused(scoped, words):
    error = refusal(project(f'check = {{ command = "make", scoped = {scoped} }}'))
    assert error.key == "projects.proj-a.check.scoped" and words in error.problem


def test_ci_local_takes_no_scoped_command():
    error = refusal(project(
        'check = { command = "make" }\nci = { workflows = ["ci.yml"], '
        'local = { command = "make local", scoped = "make" } }'))
    assert error.key == "projects.proj-a.ci.local.scoped"


def test_the_module_names_no_director(write_config):
    assert settings.build_setting(settings.load(), "proj-a", "director") == ""


def test_the_director_is_the_projects_own_else_the_defaults(write_config):
    write_config('[defaults]\ndirector = "fleet-lead"\n'
                 + project('director = "proj-lead"') + project("", "proj-b"))
    read = settings.load()
    assert settings.build_setting(read, "proj-a", "director") == "proj-lead"
    assert settings.build_setting(read, "proj-b", "director") == "fleet-lead"


@pytest.mark.parametrize("text, key", [
    ('[defaults]\ndirector = "fleet lead"\n', "defaults.director"),
    ('[defaults]\ndirector = "a@b@c"\n', "defaults.director"),
    (project('director = "who?"'), "projects.proj-a.director"),
])
def test_a_director_without_a_names_shape_is_refused(text, key):
    error = refusal(text)
    assert error.key == key and "is not a comms name" in error.problem


@pytest.mark.parametrize("name", ["fleet-lead", "core.lead_2", "lead@moxi", ""])
def test_a_director_with_a_names_shape_or_none_passes(name):
    assert settings.check_text(f'[defaults]\ndirector = "{name}"\n') is None


def test_moxi_config_rounds_check_refuses_a_director_by_its_key(write_config, capsys):
    from moxi import cli

    path = write_config('[defaults]\ndirector = "not a name"\n')
    assert cli.main(["config", "rounds", "check"]) == 1
    err = capsys.readouterr().err
    assert str(path) in err and "key 'defaults.director'" in err

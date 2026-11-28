"""A lane's settings: `[lanes.<name>]`, a project's `[projects.<key>.lanes.<name>]`, their
refusals and defaults, and the note `config check` prints where a lane gives its own
words."""

from __future__ import annotations

import pytest
from moxi import config

from moxi_rounds import settings

PROJECT = ('[projects.proj-a]\ncheck = { command = "make check", '
           'scoped = "make part {base}", timeout_s = 60 }\n')


def refusal(text: str) -> config.ConfigError:
    with pytest.raises(config.ConfigError) as caught:
        settings.check_text(text)
    return caught.value


def test_a_lane_reads_its_defaults(write_config):
    write_config('[lanes.far]\nhost = "far-host"\n')
    found = settings.lane(settings.load(), "far")
    assert found == {"host": "far-host", "moxi": "moxi", "ssh": ["ssh"],
                     "connect_timeout_s": 10, "shared_lock": None}


def test_a_lane_no_table_names_reads_none(write_config):
    write_config("")
    assert settings.lane(settings.load(), "far") is None


def test_ssh_words_are_used_as_given_a_backslash_kept_whole(write_config):
    write_config("[lanes.far]\nhost = \"far-host\"\n"
                 "ssh = ['C:\\Windows\\System32\\OpenSSH\\ssh.exe', '-F', 'my config']\n")
    found = settings.lane(settings.load(), "far")
    assert found["ssh"] == ["C:\\Windows\\System32\\OpenSSH\\ssh.exe", "-F", "my config"]


def test_a_projects_lanes_read_in_the_order_it_names_them(write_config):
    write_config('[lanes.one]\nhost = "a"\n[lanes.two]\nhost = "b"\n' + PROJECT
                 + '[projects.proj-a.lanes.two]\ntimeout_s = 90\nmemory_mib = 4096\n'
                 '[projects.proj-a.lanes.one]\npreflight = ["git --version"]\n')
    found = settings.project_lanes(settings.load(), "proj-a")
    assert list(found) == ["two", "one"]
    assert found["two"] == {"timeout_s": 90, "memory_mib": 4096}
    assert found["one"] == {"preflight": ["git --version"]}
    assert settings.project_lanes(settings.load(), "proj-b") == {}


def test_a_lane_with_no_host_is_refused():
    error = refusal('[lanes.far]\nmoxi = "moxi"\n')
    assert error.key == "lanes.far.host"


@pytest.mark.parametrize("value", ["0", "-1", "true", '"10"'])
def test_a_connect_timeout_not_above_zero_is_refused(value):
    error = refusal(f'[lanes.far]\nhost = "h"\nconnect_timeout_s = {value}\n')
    assert error.key == "lanes.far.connect_timeout_s"


@pytest.mark.parametrize("value", ["[]", '["ssh", ""]', '"ssh"', "[1]"])
def test_an_ssh_that_is_not_a_list_of_words_is_refused(value):
    error = refusal(f'[lanes.far]\nhost = "h"\nssh = {value}\n')
    assert error.key == "lanes.far.ssh"


def test_a_key_a_lane_does_not_take_is_refused():
    error = refusal('[lanes.far]\nhost = "h"\nport = 22\n')
    assert error.key == "lanes.far.port"


def test_a_shared_lock_that_is_no_path_is_refused():
    error = refusal('[lanes.far]\nhost = "h"\nshared_lock = ""\n')
    assert error.key == "lanes.far.shared_lock"


def test_a_project_lane_naming_a_lane_lanes_lacks_is_refused():
    error = refusal('[lanes.far]\nhost = "h"\n' + PROJECT
                    + "[projects.proj-a.lanes.near]\ntimeout_s = 60\n")
    assert error.key == "projects.proj-a.lanes.near"
    assert "far" in error.problem


@pytest.mark.parametrize("value", ["0", "-5"])
def test_a_project_lane_timeout_not_above_zero_is_refused(value):
    error = refusal('[lanes.far]\nhost = "h"\n' + PROJECT
                    + f"[projects.proj-a.lanes.far]\ntimeout_s = {value}\n")
    assert error.key == "projects.proj-a.lanes.far.timeout_s"


@pytest.mark.parametrize("value", ["63", "100.5", "true"])
def test_a_project_lane_memory_mib_below_64_or_not_whole_is_refused(value):
    error = refusal('[lanes.far]\nhost = "h"\n' + PROJECT
                    + f"[projects.proj-a.lanes.far]\nmemory_mib = {value}\n")
    assert error.key == "projects.proj-a.lanes.far.memory_mib"


@pytest.mark.parametrize("value", ['"node --version"', '["node --version", ""]', "[3]"])
def test_a_preflight_that_is_not_a_list_of_lines_is_refused(value):
    error = refusal('[lanes.far]\nhost = "h"\n' + PROJECT
                    + f"[projects.proj-a.lanes.far]\npreflight = {value}\n")
    assert error.key == "projects.proj-a.lanes.far.preflight"


def test_a_project_lane_command_that_splits_into_nothing_is_refused():
    error = refusal('[lanes.far]\nhost = "h"\n' + PROJECT
                    + '[projects.proj-a.lanes.far]\ncommand = "  "\n')
    assert error.key == "projects.proj-a.lanes.far.command"


def test_a_key_a_project_lane_does_not_take_is_refused():
    error = refusal('[lanes.far]\nhost = "h"\n' + PROJECT
                    + '[projects.proj-a.lanes.far]\nhost = "x"\n')
    assert error.key == "projects.proj-a.lanes.far.host"


def test_config_check_shows_a_lanes_own_words_under_the_projects(capsys):
    settings.check_text('[lanes.far]\nhost = "h"\n' + PROJECT
                        + '[projects.proj-a.lanes.far]\ncommand = "env W=4 make check"\n')
    err = capsys.readouterr().err.splitlines()
    assert "on lane far these replace the project's" in err[0]
    assert err[1].split() == ["project", "command", "make", "check"]
    assert err[2].split() == ["lane", "command", "env", "W=4", "make", "check"]
    assert len(err) == 3


def test_config_check_says_nothing_of_a_lane_that_gives_no_words(capsys):
    settings.check_text('[lanes.far]\nhost = "h"\n' + PROJECT
                        + "[projects.proj-a.lanes.far]\ntimeout_s = 60\n")
    assert capsys.readouterr().err == ""

"""sessions' one local-path cut (`api.cut_local`), ported from the door's cut: loopback
origins to `local`, then every path under a root to its last segment."""

from __future__ import annotations

import pytest

from moxi_sessions import api
from moxi_sessions.pathcut import last_segment

ENV = {"HOME": "/data/someone"}


def cut(text, env=ENV):
    return api.cut_local(text, env)


def test_a_call_line_s_path_is_its_last_segment():
    assert cut("Read(/home/someone/notes/work/moxi/x.md)") == "Read(x.md)"


def test_the_home_path_env_names_is_a_root():
    assert cut("open /data/someone/work/plan.md now") == "open plan.md now"
    assert cut("open /data/someone/work/plan.md now", {}) == \
        "open /data/someone/work/plan.md now"
    assert cut("/DATA/SOMEONE/x/y.txt") == "y.txt"


@pytest.mark.parametrize("text, expected", [
    ("~/notes/a.md", "a.md"),
    ("$HOME/notes/a.md", "a.md"),
    ("see ~/", "see ~"),
])
def test_tilde_and_dollar_home_are_roots(text, expected):
    assert cut(text) == expected


@pytest.mark.parametrize("root", ["/home/", "/tmp/", "/root/", "/etc/", "/var/", "/usr/",
                                  "/opt/", "/mnt/", "/media/", "/run/", "/srv/"])
def test_each_system_root_is_cut(root):
    assert cut(f"x={root}a/b/c.log") == "x=c.log"


def test_a_root_is_cut_wherever_it_occurs_and_case_doesnt_matter():
    assert cut("https://example.com/home/u/page") == "https://example.compage"
    assert cut("/Etc/Passwd") == "Passwd"


def test_a_path_outside_the_roots_is_left():
    assert cut("/data/other/x and a/b/c") == "/data/other/x and a/b/c"


@pytest.mark.parametrize("end", [" ", "\t", "\n", '"', "'", "`", "<", ">", "|", ",", ";",
                                 "(", ")", "{", "}", "[", "]"])
def test_a_path_runs_to_each_terminator(end):
    assert cut(f"/srv/app/x.py{end}tail") == f"x.py{end}tail"


def test_a_path_runs_to_the_text_s_end():
    assert cut("/srv/app/dir/") == "dir"


def test_a_long_segment_is_cut_to_255_bytes_on_a_character_boundary():
    name = "a" + "\u00e9" * 200
    shown = cut(f"/tmp/{name}")
    assert len(shown.encode()) <= 255 and name.startswith(shown)


@pytest.mark.parametrize("text, shown", [("/home/", "home"), ("~/", "~"),
                                         ("$HOME/", "$HOME")])
def test_a_bare_root_shows_its_own_name(text, shown):
    assert cut(text) == shown


def test_a_cwd_with_no_part_is_slash_and_an_empty_one_is_none():
    assert last_segment("/") == "/"
    assert last_segment("") is None and last_segment(None) is None
    assert last_segment("/data/x/") == "x"


def test_a_placeholder_in_a_middle_segment_is_part_of_the_path():
    assert cut("/home/someone/[REDACTED:x]/dir/name") == "name"
    assert cut("see /srv/[REDACTED:github-pat]/app/x.py now") == "see x.py now"


def test_a_placeholder_as_the_last_segment_is_what_shows():
    assert cut("/home/someone/[REDACTED:x]") == "[REDACTED:x]"
    assert cut("(/home/someone/[REDACTED:x])") == "([REDACTED:x])"


def test_only_a_placeholder_joins_the_run_and_other_brackets_still_end_it():
    assert cut("/srv/app/[note]/x") == "app[note]/x"
    assert cut("/srv/app/[REDACTED:x y]/z") == "app[REDACTED:x y]/z"
    assert cut("/srv/app/[REDACTED:x") == "app[REDACTED:x"


@pytest.mark.parametrize("text", [
    "localhost", "LOCALHOST:8080", "http://localhost:8080/api?x=1", "127.0.0.1",
    "https://127.12.0.3:9/x", "127.1", "0.0.0.0:80", "[::1]:3000", "::1",
    "0:0:0:0:0:0:0:1", "ws://[0:0:0:0:0:0:0:1]/socket#top",
])
def test_loopback_origins_become_local(text):
    assert cut(f"at {text} now") == "at local now"


@pytest.mark.parametrize("text", ["mylocalhost", "localhost-dev", "x.localhost",
                                  "127.0.0.1a"])
def test_loopback_is_matched_as_a_whole_token_only(text):
    assert cut(text) == text


def test_loopback_goes_before_paths():
    assert cut("http://localhost/home/u/x") == "local"

"""The render command, run as Claude runs it: a child process, input on stdin, under the
core's audit hook."""

import json
import os
import re
import time

import pytest
from statusline_kit import (
    BY_SESSION,
    FROM_INPUT,
    INPUT,
    config_file,
    out,
    render,
    text_segment,
    write_config,
)

from moxi import modules, paths

SEP = " │ "


def test_the_default_line_with_no_config_file_and_no_folder_made():
    assert not config_file().parent.exists()
    done = render("--no-color")
    # usage shows nothing without rate_limits, cache nothing without a clock record.
    assert out(done) == [SEP.join(["…/work/tree", "Madeup 9", "high", "ctx:42%"])]
    assert not config_file().parent.exists()


def test_the_default_line_shows_usage_and_cache_at_the_time_now():
    """The render hands each segment the time now, in epoch seconds: usage's limit that
    reset a minute ago shows `reset`, and cache's record from 150 s ago shows its age."""
    for name in ("usage", "cache"):
        found = modules.announced(name)
        if found.state != modules.PRESENT or not found.segment:
            pytest.skip(f"{name} is not installed here")
    now = time.time()
    # cache's clock record, where its watcher writes it.
    folder = paths.Layout("cache").runtime_dir() / "clock"
    folder.mkdir(parents=True)
    session = INPUT["session_id"]
    (folder / f"{session}.json").write_text(json.dumps({
        "v": 1, "session": session, "harness": "claude", "pid": 1, "kind": "warm",
        "last_request": now - 150, "ttl": 3600, "read_at": now, "seen_at": now,
        "code": None, "warn_before": 900, "stale_after": 30}))
    limits = {"five_hour": {"used_percentage": 23, "resets_at": now + 3600},
              "seven_day": {"used_percentage": 61, "resets_at": now - 60}}
    [shown] = out(render("--no-color", stdin={**INPUT, "rate_limits": limits}))
    head, cache = shown.rsplit(SEP, 1)
    assert head == SEP.join(["…/work/tree", "Madeup 9", "high", "ctx:42%",
                             "5h:23% 7d:reset"])
    assert re.fullmatch(r"cache:2m\d+s", cache), cache


def test_every_base_field_on_two_rows_with_colour():
    write_config("""
        show = ["dir", "branch", "model", "effort", "context", "row", "session",
                "transcript", "lines", "cost"]
        separator = " / "
    """)
    done = render(stdin={**INPUT, "context_window": {"used_percentage": 85}})
    assert out(done) == ["…/work/tree / Madeup 9 / high / \033[31mctx:85%\033[0m",
                         "#abcdef / abcdef123456.jsonl / +12/-3 / $1.24"]


def test_color_false_in_the_config_turns_colour_off():
    write_config('show = ["context"]\ncolor = false\n')
    done = render(stdin={**INPUT, "context_window": {"used_percentage": 85}})
    assert out(done) == ["ctx:85%"]


def test_the_config_sets_parts_and_bands():
    write_config("""
        show = ["dir", "context"]
        [dir]
        parts = 1
        [context]
        warn_at = 10
        alert_at = 90
    """)
    assert out(render()) == [f"…/tree{SEP}\033[33mctx:42%\033[0m"]


@pytest.mark.parametrize("stdin", [b"", b"not json", b"[1, 2]", b"\xff\xfe"],
                         ids=["empty", "not json", "a list", "not utf-8"])
def test_a_broken_input_prints_a_question_mark_and_exits_0(stdin):
    done = render(stdin=stdin)
    assert out(done) == ["?"]
    assert done.stderr.decode().count("\n") == 1


def test_show_replaces_the_configs_items():
    write_config('show = ["model"]\n')
    assert out(render("--show", "effort,row,cost", "--no-color")) == ["high", "$1.24"]
    assert out(render("--show=cost")) == ["$1.24"]


def test_only_prints_one_item_and_nothing_else():
    write_config('show = ["model", "effort"]\nseparator = "!!"\n')
    assert out(render("--only", "cost")) == ["$1.24"]
    assert out(render("--only", "branch")) == [""]


def test_no_color_wins_over_the_config():
    write_config('show = ["context"]\ncolor = true\n')
    done = render("--no-color", stdin={**INPUT, "context_window": {"used_percentage": 99}})
    assert out(done) == ["ctx:99%"]


def test_config_reads_that_file_and_not_its_own(tmp_path):
    write_config('show = ["model"]\n')
    draft = tmp_path / "draft.toml"
    draft.write_text('show = ["effort", "cost"]\nseparator = "+"\n')
    assert out(render("--config", str(draft))) == ["high+$1.24"]
    assert out(render()) == ["Madeup 9"]


def test_a_missing_config_file_prints_config_question_mark(tmp_path):
    done = render("--config", str(tmp_path / "gone.toml"))
    assert out(done) == ["config?"]
    err = done.stderr.decode()
    assert err.count("\n") == 1
    assert "gone.toml" in err


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_config_file_prints_config_question_mark(tmp_path):
    draft = tmp_path / "locked.toml"
    draft.write_text('show = ["model"]\n')
    draft.chmod(0)
    try:
        done = render("--config", str(draft))
    finally:
        draft.chmod(0o600)
    assert out(done) == ["config?"]
    assert "locked.toml" in done.stderr.decode()


@pytest.mark.parametrize("text", [
    "show = [",
    'show = ["model", "row", "effort", "row", "cost"]\n',
    'nope = 1\n',
    '[context]\nwarn_at = 90\nalert_at = 80\n',
], ids=["not toml", "two rows", "unknown key", "warn above alert"])
def test_a_config_its_check_refuses_prints_config_question_mark(text, tmp_path):
    draft = tmp_path / "bad.toml"
    draft.write_text(text)
    done = render("--config", str(draft))
    assert out(done) == ["config?"]
    assert done.stderr.decode().count("\n") == 1
    write_config(text)
    assert out(render()) == ["config?"]


def test_an_unknown_argument_prints_an_empty_line_and_exits_0():
    done = render("--bogus")
    assert out(done) == [""]
    assert "unknown argument '--bogus'" in done.stderr.decode()


def test_segments_through_discovery(site):
    site.module("alpha", FROM_INPUT)
    site.module("beta", BY_SESSION)
    write_config('show = ["model", "alpha", "beta", "gamma"]\n')
    assert out(render(site=site)) == [SEP.join(["Madeup 9", "\033[31min:9\033[0m",
                                                "\033[32msid:abc\033[0m"])]
    assert out(render("--no-color", site=site)) == [SEP.join(["Madeup 9", "in:9",
                                                              "sid:abc"])]


def test_a_module_with_no_segment_shows_nothing(site):
    site.module("alpha")
    assert out(render("--show", "model,alpha", site=site)) == ["Madeup 9"]


def test_a_raising_segment_names_itself_on_stderr_and_leaves_the_rest(site):
    site.module("alpha", "def from_input(payload, now):\n    raise ValueError('no')\n")
    site.module("beta", text_segment("b"))
    done = render("--show", "model,alpha,beta", "--no-color", site=site)
    assert out(done) == [f"Madeup 9{SEP}b"]
    assert "alpha: ValueError: no" in done.stderr.decode()


# -- under the hook -------------------------------------------------------------------

def test_a_segment_that_writes_a_file_is_refused(site, tmp_path):
    target = tmp_path / "written"
    site.module("alpha", f"""
        def from_input(payload, now):
            open({str(target)!r}, "w").write("x")
    """)
    site.module("beta", text_segment("b"))
    done = render("--show", "model,alpha,beta", "--no-color", site=site)
    assert out(done) == [f"Madeup 9{SEP}b"]
    assert "a status line render may not open" in done.stderr.decode()
    assert not target.exists()


def test_a_segment_that_writes_at_import_is_refused(site, tmp_path):
    target = tmp_path / "written"
    site.module("alpha", f"open({str(target)!r}, 'w').write('x')\n")
    done = render("--show", "model,alpha", site=site)
    assert out(done) == ["Madeup 9"]
    assert not target.exists()


def test_a_segment_that_starts_a_process_is_refused(site, tmp_path):
    target = tmp_path / "touched"
    site.module("alpha", f"""
        import subprocess
        def from_input(payload, now):
            subprocess.run(["touch", {str(target)!r}])
    """)
    done = render("--show", "model,alpha", site=site)
    assert out(done) == ["Madeup 9"]
    assert "subprocess.Popen" in done.stderr.decode()
    assert not target.exists()


def test_a_segment_that_opens_a_socket_is_refused(site):
    site.module("alpha", """
        import socket
        def from_input(payload, now):
            with socket.socket() as s:
                s.connect(("127.0.0.1", 9))
    """)
    done = render("--show", "model,alpha", site=site)
    assert out(done) == ["Madeup 9"]
    assert "socket.connect" in done.stderr.decode()


def test_the_config_is_read_under_the_hook(site, tmp_path):
    """The hook is in before the config is read: a config file that exists reads, and a
    segment then shown can't write."""
    target = tmp_path / "written"
    site.module("alpha", f"""
        def from_input(payload, now):
            open({str(target)!r}, "w")
    """)
    write_config('show = ["alpha", "effort"]\n')
    done = render(site=site)
    assert out(done) == ["high"]
    assert not target.exists()

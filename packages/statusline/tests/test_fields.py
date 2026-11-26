"""The base fields, each from Claude's input, formatted and never computed."""

import copy

import pytest
from statusline_kit import INPUT

from moxi_statusline.fields import RED, RESET, YELLOW, Options, field


def opts(**kw) -> Options:
    return Options(**{"home": "/home/someone", **kw})


def with_(path: str, value) -> dict:
    payload = copy.deepcopy(INPUT)
    *parents, last = path.split(".")
    node = payload
    for key in parents:
        node = node.setdefault(key, {})
    node[last] = value
    return payload


def test_each_field_from_the_input():
    o = opts(color=False)
    assert field("dir", INPUT, o) == "…/work/tree"
    assert field("model", INPUT, o) == "Madeup 9"
    assert field("effort", INPUT, o) == "high"
    assert field("context", INPUT, o) == "ctx:42%"
    assert field("session", INPUT, o) == "#abcdef"
    assert field("transcript", INPUT, o) == "abcdef123456.jsonl"
    assert field("lines", INPUT, o) == "+12/-3"
    assert field("cost", INPUT, o) == "$1.24"


@pytest.mark.parametrize(("path", "parts", "shown"), [
    ("/home/someone", 2, "~"),
    ("/home/someone/", 2, "~"),
    ("/home/someone/a", 2, "~/a"),
    ("/home/someone/a/b", 2, "~/a/b"),
    ("/home/someone/a/b/c", 2, "…/b/c"),
    ("/home/someone/a/b/c", 3, "~/a/b/c"),
    ("/home/someone/a/b/c", 1, "…/c"),
    ("/home/someoneelse/x", 2, "…/someoneelse/x"),
    ("/srv/x", 2, "/srv/x"),
    ("/srv/x/y", 2, "…/x/y"),
    ("/", 2, "/"),
])
def test_dir_cuts_to_its_parts_and_shows_home_as_tilde(path, parts, shown):
    assert field("dir", with_("workspace.current_dir", path), opts(parts=parts)) == shown


def test_dir_falls_back_to_cwd_and_is_left_out_with_neither():
    payload = copy.deepcopy(INPUT)
    del payload["workspace"]
    assert field("dir", payload, opts()) == "…/up/cwd"
    del payload["cwd"]
    assert field("dir", payload, opts()) is None


def test_dir_with_no_home_known_shows_the_path():
    assert field("dir", with_("workspace.current_dir", "/home/x"), opts(home=None)) == \
        "/home/x"


def test_an_unreadable_input_shows_a_question_mark_for_dir_and_nothing_else():
    o = opts()
    assert field("dir", None, o) == "?"
    for name in ("branch", "model", "effort", "context", "session", "transcript",
                 "lines", "cost"):
        assert field(name, None, o) is None


def test_model_cuts_a_leading_claude_only():
    assert field("model", with_("model.display_name", "Opus Claude"), opts()) == \
        "Opus Claude"
    assert field("model", with_("model.display_name", "Claude "), opts()) is None
    assert field("model", with_("model", None), opts()) is None


def test_effort_as_given_and_nothing_when_absent():
    assert field("effort", with_("effort.level", "max"), opts()) == "max"
    payload = copy.deepcopy(INPUT)
    del payload["effort"]
    assert field("effort", payload, opts()) is None


@pytest.mark.parametrize(("used", "colour"), [
    (0, None), (49, None), (50, YELLOW), (79, YELLOW), (80, RED), (100, RED),
])
def test_context_bands(used, colour):
    shown = field("context", with_("context_window.used_percentage", used), opts())
    assert shown == (f"{colour}ctx:{used}%{RESET}" if colour else f"ctx:{used}%")


def test_context_bands_follow_the_config():
    payload = with_("context_window.used_percentage", 30)
    assert field("context", payload, opts(warn_at=20, alert_at=30)) == \
        f"{RED}ctx:30%{RESET}"
    assert field("context", payload, opts(warn_at=20, alert_at=40)) == \
        f"{YELLOW}ctx:30%{RESET}"


def test_context_colour_off():
    payload = with_("context_window.used_percentage", 90)
    assert field("context", payload, opts(color=False)) == "ctx:90%"


def test_context_shows_whole_halves_up_and_the_band_follows_what_shows():
    assert field("context", with_("context_window.used_percentage", 49.5), opts()) == \
        f"{YELLOW}ctx:50%{RESET}"
    assert field("context", with_("context_window.used_percentage", 49.4),
                 opts()) == "ctx:49%"


def test_context_null_is_a_dash_and_junk_or_absent_is_left_out():
    assert field("context", with_("context_window.used_percentage", None), opts()) == \
        "ctx:—"
    assert field("context", with_("context_window", {}), opts()) == "ctx:—"
    assert field("context", with_("context_window.used_percentage", "42"), opts()) is None
    assert field("context", with_("context_window.used_percentage", True),  # noqa: FBT003 -- a bool is the bad input
                 opts()) is None
    payload = copy.deepcopy(INPUT)
    del payload["context_window"]
    assert field("context", payload, opts()) is None


def test_lines_left_out_when_both_are_zero_or_absent():
    payload = with_("cost", {"total_lines_added": 0, "total_lines_removed": 0})
    assert field("lines", payload, opts()) is None
    assert field("lines", with_("cost", {}), opts()) is None
    assert field("lines", with_("cost", {"total_lines_removed": 4}), opts()) == "+0/-4"


def test_cost_shows_claudes_figure_in_dollars():
    assert field("cost", with_("cost.total_cost_usd", 0), opts()) == "$0.00"
    assert field("cost", with_("cost.total_cost_usd", None), opts()) is None


def test_session_and_transcript_left_out_when_absent():
    payload = copy.deepcopy(INPUT)
    del payload["session_id"], payload["transcript_path"]
    assert field("session", payload, opts()) is None
    assert field("transcript", payload, opts()) is None


def test_effort_falls_back_to_claude_effort_and_the_payload_wins(monkeypatch):
    monkeypatch.setenv("CLAUDE_EFFORT", "  low \n")
    assert field("effort", with_("effort.level", "high"), opts()) == "high"
    for blank in (None, "", "   "):
        assert field("effort", with_("effort.level", blank), opts()) == "low"
    payload = copy.deepcopy(INPUT)
    payload.pop("effort", None)
    assert field("effort", payload, opts()) == "low"
    monkeypatch.setenv("CLAUDE_EFFORT", "  ")
    assert field("effort", payload, opts()) is None
    monkeypatch.delenv("CLAUDE_EFFORT")
    assert field("effort", payload, opts()) is None

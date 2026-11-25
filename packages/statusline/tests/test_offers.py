"""The two offers over a user's own status line, at a fake terminal and with nobody at it,
and `moxi statusline pieces`.

The old line is a made-up script in the test's tmp; detection's throwaway folder is made
there too and checked gone.
"""

from __future__ import annotations

import io
import json
import re
import shlex
import sys
import tempfile
import textwrap
import types

import pytest
from statusline_kit import config_file
from test_detect import ALL
from test_setup import Terminal, line, settings_file, write_settings

from moxi_statusline import offers, pieces, setup, switch
from moxi_statusline.cli import main

SEGMENTS = ["cache", "usage"]
MEASURED = re.compile(r"^Each piece starts Python once per render\. Measured just now: "
                      r"cache \d+ ms \(a session with no clock record\), usage \d+ ms\.$")


@pytest.fixture
def throwaway(tmp_path, monkeypatch):
    """Detection's throwaway folder goes in the test's tmp, and is gone after."""
    where = tmp_path / "throwaway"
    where.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(where))
    yield where
    assert list(where.iterdir()) == []


@pytest.fixture
def segments(monkeypatch):
    """The installed modules with a segment, as setup sees them."""
    found = list(SEGMENTS)
    monkeypatch.setattr(setup, "_segments", lambda: list(found))
    return found


@pytest.fixture
def own(tmp_path):
    """Claude Code's line is a made-up script of the user's own; returns its value."""
    script = tmp_path / "lines" / "line.py"
    script.parent.mkdir()
    ran = tmp_path / "ran"
    script.write_text(f"open({str(ran)!r}, 'a').write('ran\\n')\n" + textwrap.dedent(ALL),
                      encoding="utf-8")
    value = {"type": "command",
             "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}",
             "padding": 1}
    raw = write_settings({"statusLine": value})
    return value, raw, ran


def items() -> list[str]:
    text = config_file().read_text()
    return json.loads(re.search(r"^show = (\[.*\])$", text, re.M).group(1))


# -- at a terminal -----------------------------------------------------------------------

def test_switch_shows_what_the_old_line_did_plus_what_it_lacked(own, throwaway, segments):
    value, _, ran = own
    term = Terminal("y", "1")
    assert setup.setup(ask=term.ask, say=term.say) == 0
    assert term.asked == [setup.DETECT_QUESTION, offers.CHOICE]
    assert ran.read_text() == "ran\n"
    said = "\n".join(term.said)
    assert ("Your status line shows: dir, branch, model, effort, context, 5h/7d limits | "
            "session, transcript, lines, cost") in said
    assert 'It also shows other text (it may be only labels or separators): "' in said
    assert "Moxi's line would add: cache timer" in said
    assert "\n".join(offers.CHOICES) in said
    assert term.said[-1] == switch.SWITCHED
    assert switch.is_moxis(line())
    assert line()["padding"] == 1
    assert switch.saved().value == value
    assert items() == ["dir", "branch", "model", "effort", "context", "usage", "cache",
                       "row", "session", "transcript", "lines", "cost"]
    assert setup.recorded()["answer"] == "switch"


def test_limits_without_usage_say_how_to_install_it(own, throwaway, segments):
    segments.remove("usage")
    term = Terminal("", "1")
    assert setup.setup(ask=term.ask, say=term.say) == 0
    assert "usage" not in items()
    assert items()[:6] == ["dir", "branch", "model", "effort", "context", "cache"]
    assert term.said[-1] == offers.INSTALL_USAGE


def test_keep_writes_nothing_and_prints_measured_pieces(own, throwaway, segments):
    _, raw, ran = own
    term = Terminal("n", "2")
    assert setup.setup(ask=term.ask, say=term.say) == 0
    assert not ran.exists()
    assert settings_file().read_bytes() == raw
    assert not config_file().exists()
    assert not switch.previous_path().exists()
    assert setup.recorded()["answer"] == "keep"
    render = switch.render_command()
    at = term.said.index(pieces.LEAD)
    assert term.said[at + 1] == (f"  cache timer:   printf '%s' \"$input\" | {render} "
                                 "--only cache")
    assert term.said[at + 2] == (f"  usage figures: printf '%s' \"$input\" | {render} "
                                 "--only usage")
    assert MEASURED.match(term.said[at + 3]), term.said[at + 3]
    assert term.said[at + 4] == pieces.NEEDS
    # A second install neither asks nor runs the line.
    again = Terminal()
    assert setup.setup(ask=again.ask, say=again.say) == 0
    assert again.asked == [] and again.said == [setup.KEPT]


def test_declining_the_run_offers_the_default_items(own, throwaway, segments):
    _, _, ran = own
    term = Terminal("n", "1")
    assert setup.setup(ask=term.ask, say=term.say) == 0
    assert not ran.exists()
    assert ("Moxi's line would show its default items: dir, branch, model, effort, "
            "context, usage, cache") in term.said
    assert "\n".join(offers.CHOICES_DEFAULT) in "\n".join(term.said)
    assert switch.is_moxis(line())
    assert items() == setup.default_show()


def test_a_run_that_finds_nothing_says_why_and_still_offers_both(tmp_path, throwaway,
                                                                  segments):
    raw = write_settings({"statusLine": {"type": "command", "command": "exit 2"}})
    term = Terminal("y", "2")
    assert setup.setup(ask=term.ask, say=term.say) == 0
    assert "Your status line exited with code 2, so nothing was detected." in term.said
    assert settings_file().read_bytes() == raw
    assert setup.recorded()["answer"] == "keep"


def test_an_answer_neither_1_nor_2_is_asked_again(own, throwaway, segments):
    term = Terminal("n", "3", "1")
    assert setup.setup(ask=term.ask, say=term.say) == 0
    assert term.asked[2] == f"'3' is neither 1 nor 2; {offers.CHOICE}"
    assert switch.is_moxis(line())


@pytest.mark.parametrize("answers", [(), ("y",)])
def test_input_that_ends_records_and_writes_nothing(answers, own, throwaway, segments):
    _, raw, _ = own
    term = Terminal(*answers)
    assert setup.setup(ask=term.ask, say=term.say) == 0
    assert term.said[-1] == f"{switch.LEFT}."
    assert settings_file().read_bytes() == raw
    assert setup.recorded() is None


def test_use_offers_again_whatever_was_recorded(own, throwaway, segments):
    setup.record("keep")
    term = Terminal("n", "1")
    assert setup.use(ask=term.ask, say=term.say) == 0
    assert switch.is_moxis(line())
    assert setup.recorded()["answer"] == "switch"


# -- nobody at the keyboard --------------------------------------------------------------

@pytest.fixture
def nobody(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))


@pytest.mark.parametrize("verb", ["setup", "use"])
def test_nobody_at_the_keyboard_writes_nothing_and_prints_both(verb, own, throwaway,
                                                               nobody):
    value, raw, ran = own
    said = []
    assert getattr(setup, verb)(say=said.append) == 0
    assert not ran.exists()
    assert settings_file().read_bytes() == raw
    assert setup.recorded() is None
    assert not config_file().exists()
    assert said[0] == (f"Claude Code's status line is your own ({value['command']}); it "
                       "is left as it is.")
    assert "moxi statusline use" in said[1]
    render = switch.render_command()
    commands = [s for s in said if s.endswith("--only cache") or s.endswith("--only usage")]
    assert commands[0].endswith(f"| {render} --only cache")
    assert not any("Measured" in s for s in said)
    assert said[-1] == "moxi statusline pieces prints these with what each costs."


# -- pieces ------------------------------------------------------------------------------

def test_pieces_put_cache_first_and_the_rest_by_name():
    found = pieces.pieces(["zeta", "usage", "alpha", "cache"], render="/r")
    assert [p.module for p in found] == ["cache", "usage", "alpha", "zeta"]
    assert [p.label for p in found] == ["cache timer", "usage figures", "alpha", "zeta"]
    assert pieces.lines([]) == [pieces.NONE]


def test_the_pieces_verb_prints_measured_times(capsys):
    assert main(["pieces"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == pieces.LEAD
    found = [ln for ln in out if "--only" in ln]
    assert found and found[0].lstrip().startswith("cache timer:")
    assert MEASURED.match(out[len(found) + 1]), out
    assert out[-1] == pieces.NEEDS


def test_a_cost_is_the_median_of_five_timed_runs_after_one_to_warm_up(tmp_path,
                                                                       monkeypatch):
    log = tmp_path / "runs"
    piece = pieces.Piece("cache", "cache timer", f"echo run >> {shlex.quote(str(log))}")
    # Each run reads the clock at its start and its end. The warm-up takes 5 s; the five
    # timed runs take 10, 80, 20, 40 and 30 ms.
    took = [5.0, 0.010, 0.080, 0.020, 0.040, 0.030]
    reads = iter([t for run in took for t in (100.0, 100.0 + run)])
    clock = types.SimpleNamespace(perf_counter=lambda: next(reads))
    monkeypatch.setattr(pieces, "time", clock)
    assert pieces.measure(piece) == pytest.approx(30.0)
    assert log.read_text() == "run\n" * 6


def test_a_piece_command_runs_as_printed(tmp_path):
    piece = pieces.pieces(["cache"])[0]
    ms = pieces.measure(piece, runs=1)
    assert ms is not None and ms > 0
    broken = pieces.Piece("cache", "cache timer", "exit 1")
    assert pieces.measure(broken, runs=1) is None
    assert "cache not measured" in pieces.lines([broken], {"cache": None})[2]

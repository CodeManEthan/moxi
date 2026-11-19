"""Detection: a user's own status line run once with marked input, and what it shows.

Every old line here is a made-up script in the test's tmp, and the throwaway folder is
made in the test's tmp too and checked gone after each run.
"""

from __future__ import annotations

import json
import os
import random
import shlex
import sys
import textwrap
import time
from pathlib import Path

import pytest

from moxi_statusline import detect, offers

SEGMENTS = ["cache", "usage"]
DEFAULT = ["dir", "branch", "model", "effort", "context", "usage", "cache"]

#: Prints every piece on two rows, with colours, labels and separators.
ALL = r"""
import json, os, subprocess, sys
d = json.load(sys.stdin)
b = subprocess.run(["git", "branch", "--show-current"], capture_output=True,
                   text=True).stdout.strip()
r = d["rate_limits"]
print(f"\033[34m{d['workspace']['current_dir']}\033[0m │ {b} │ "
      f"{d['model']['display_name']} {d['effort']['level']} │ "
      f"ctx:{d['context_window']['used_percentage']}% │ "
      f"5h:{r['five_hour']['used_percentage']}% 7d:{r['seven_day']['used_percentage']}%")
print(f"#{d['session_id'][:8]} │ {os.path.basename(d['transcript_path'])} │ "
      f"+{d['cost']['total_lines_added']}/-{d['cost']['total_lines_removed']} "
      f"${d['cost']['total_cost_usd']:.2f}")
"""


class Scripted(detect.Draw):
    """Draws that come from a queue first: `letters` in turn, `numbers` by range."""

    def __init__(self, letters=(), numbers=None):
        super().__init__(random.Random(1234))
        self.queue = list(letters)
        self.numbers = {k: list(v) for k, v in (numbers or {}).items()}
        self.letter_calls = 0

    def letters(self, n):
        self.letter_calls += 1
        if self.queue:
            text = self.queue.pop(0)
            assert len(text) == n
            return text
        return super().letters(n)

    def number(self, low, high):
        queued = self.numbers.get((low, high))
        if queued:
            return queued.pop(0)
        return super().number(low, high)


@pytest.fixture
def throwaway(tmp_path) -> Path:
    """Where detection makes its throwaway folder; checked empty after the test."""
    where = tmp_path / "throwaway"
    where.mkdir()
    yield where
    assert list(where.iterdir()) == [], "the throwaway folder was left behind"


@pytest.fixture
def line(tmp_path):
    """Write a made-up old line as a Python script; returns the command that runs it."""
    lines_dir = tmp_path / "lines"
    lines_dir.mkdir()

    def make(body: str, name: str = "line.py") -> str:
        path = lines_dir / name
        path.write_text(textwrap.dedent(body))
        return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"

    return make


def run(command, throwaway, **kw) -> detect.Detection:
    return detect.detect(command, tmp=str(throwaway), **kw)


# -- what the line shows -----------------------------------------------------------------

def test_all_markers_on_two_rows(line, throwaway):
    found = run(line(ALL), throwaway)
    assert found.problem is None
    assert found.rows() == [["dir", "branch", "model", "effort", "context", "limits"],
                            ["session", "transcript", "lines", "cost"]]
    assert found.also == "│ │ │ ctx:% │ 5h:% 7d:% | # │ │ +/- $"
    show, added, needs = offers.preset(found, SEGMENTS, DEFAULT)
    assert show == ["dir", "branch", "model", "effort", "context", "usage", "cache", "row",
                    "session", "transcript", "lines", "cost"]
    assert added == ["cache"] and not needs


def test_colours_and_an_osc8_link_are_stripped(line, throwaway):
    found = run(line(r"""
        import json, os, sys
        d = json.load(sys.stdin)
        cwd = d["cwd"]
        print(f"\033]8;;file://{cwd}\033\\{os.path.basename(cwd)}\033]8;;\033\\ "
              f"\033[1;38;5;208m{d['model']['id']}\033[0m "
              f"\033[33mctx:{d['context_window']['used_percentage']}%\033[m")
    """), throwaway)
    assert found.pieces() == ["dir", "model", "context"]
    assert found.also == "ctx:%"
    assert offers.preset(found, SEGMENTS, DEFAULT)[0] == ["dir", "model", "context",
                                                          "usage", "cache"]


def test_extra_text_is_shown_as_it_is(line, throwaway):
    found = run(line("""
        import json, sys
        d = json.load(sys.stdin)
        print(f"up 3d │ {d['model']['display_name']}")
    """), throwaway)
    assert found.pieces() == ["model"]
    assert found.also == "up 3d │"


def test_a_line_that_shows_only_the_dir_finds_dir_alone(line, throwaway):
    found = run(line("""
        import os
        print(os.getcwd())
    """), throwaway)
    assert found.pieces() == ["dir"]
    assert found.also is None
    assert offers.preset(found, SEGMENTS, DEFAULT)[0] == ["dir", "usage", "cache"]


def test_digits_inside_a_longer_token_find_no_number(line, throwaway):
    found = run(line("""
        import json, sys
        d = json.load(sys.stdin)
        r = d["rate_limits"]
        c = d["cost"]
        numbers = [d["context_window"]["used_percentage"],
                   r["five_hour"]["used_percentage"], r["seven_day"]["used_percentage"]]
        parts = []
        for n in numbers:
            parts += [f"a{n}f", f"41{n}", f"_{n}_", f"8.{n}", f"{n}.5"]
        for n in (c["total_lines_added"], c["total_lines_removed"]):
            parts += [f"9{n}", f"{n}0", f"x{n}", f"{n}.0"]
        parts += [f"1{c['total_cost_usd']}", f"{c['total_cost_usd']}5"]
        print("/x/" + "/".join(parts))
    """), throwaway)
    assert found.problem is None
    assert found.pieces() == []
    assert offers.preset(found, SEGMENTS, DEFAULT)[0] is None


def _held(year, month, day, hour, minute, second) -> float:
    return time.mktime((year, month, day, hour, minute, second, 0, 0, -1))


def test_the_resets_clock_time_finds_no_limit(line, throwaway):
    # The limits reset 60 s after the input is built, at 13:37:42 local time; the draws
    # offer those numbers first, and each is skipped.
    held = _held(2030, 3, 4, 13, 36, 42)
    resets = time.localtime(held + 60)
    assert (resets.tm_hour, resets.tm_min, resets.tm_sec) == (13, 37, 42)
    draw = Scripted(numbers={(11, 49): [37, 42, 13, 37, 42, 13]})
    found = run(line("""
        import json, sys, time
        d = json.load(sys.stdin)
        at = d["rate_limits"]["five_hour"]["resets_at"]
        for t in (time.localtime(at), time.gmtime(at)):
            print(f"resets {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d} "
                  f"{t.tm_hour % 12 or 12}:{t.tm_min:02d}")
    """), throwaway, now=lambda: held, draw=draw)
    assert found.problem is None
    assert "limits" not in found.pieces() and "context" not in found.pieces()
    assert found.pieces() == []


def test_the_date_and_hhmm_with_a_held_clock_find_no_lines(line, throwaway):
    held = _held(2030, 3, 4, 14, 52, 30)
    draw = Scripted(numbers={(1000, 9999): [2030, 1452, 1452, 2030]})
    found = run(line("""
        import json, sys, time
        d = json.load(sys.stdin)
        t = time.localtime(d["rate_limits"]["five_hour"]["resets_at"] - 60)
        print(f"{t.tm_year}-{t.tm_mon:02d}-{t.tm_mday:02d} {t.tm_hour:02d}{t.tm_min:02d} "
              f"{t.tm_min:02d}{t.tm_sec:02d} {t.tm_mon:02d}{t.tm_mday:02d}")
    """), throwaway, now=lambda: held, draw=draw)
    assert found.problem is None
    assert found.pieces() == []


def test_a_two_digit_year_with_a_held_clock_finds_no_context(line, throwaway):
    # 8 November 2026: the year's 26 is no other clock number then, and the draw offers
    # it first.
    held = _held(2026, 11, 8, 14, 52, 30)
    draw = Scripted(numbers={(11, 49): [26]})
    found = run(line("""
        import json, sys, time
        d = json.load(sys.stdin)
        t = time.localtime(d["rate_limits"]["five_hour"]["resets_at"] - 60)
        print(time.strftime("%d/%m/%y", t))
    """), throwaway, now=lambda: held, draw=draw)
    assert found.problem is None
    assert found.pieces() == []


def test_the_cost_skips_an_hmm_time_with_a_held_clock(line, throwaway):
    # 14:52 is 2.52 on a twelve-hour clock; the draw offers $2.52 first.
    held = _held(2030, 3, 4, 14, 52, 30)
    draw = Scripted(numbers={(2, 9): [2, 3], (1, 99): [52, 47]})
    found = run(line("""
        import json, sys, time
        d = json.load(sys.stdin)
        t = time.localtime(d["rate_limits"]["five_hour"]["resets_at"] - 60)
        print(f"{t.tm_hour % 12 or 12}.{t.tm_min:02d}")
    """), throwaway, now=lambda: held, draw=draw)
    assert found.problem is None
    assert found.pieces() == []


def test_clock_numbers_hold_the_year_and_both_day_orders():
    # 25 November 2037, 14:52:18: 37 is the year alone, 1125 the month and day alone,
    # and 2511 the day and month alone.
    held = _held(2037, 11, 25, 14, 52, 18)
    two, four, _ = detect.clock_numbers(held, held + 60)
    assert 37 in two
    assert 1125 in four
    assert 2511 in four


def test_clock_numbers_cover_the_span_and_the_resets_in_both_zones():
    held = _held(2030, 3, 4, 14, 52, 30)
    two, four, hm = detect.clock_numbers(held, held + 60)
    for when in (time.localtime(held), time.gmtime(held), time.localtime(held + 10),
                 time.localtime(held + 60)):
        assert {when.tm_min, when.tm_sec, when.tm_hour, when.tm_mday, when.tm_mon,
                when.tm_year % 100} <= two
        assert when.tm_hour * 100 + when.tm_min in four
        assert when.tm_min * 100 + when.tm_sec in four
    assert {1900, 2026, 2100} <= four and 1899 not in four
    assert "2.52" in hm and "14.52" in hm


# -- runs that detect nothing ------------------------------------------------------------

def test_a_line_that_sleeps_past_the_limit_is_killed(line, throwaway, tmp_path):
    pid_file = tmp_path / "pid"
    start = time.monotonic()
    found = run(f"sleep 30 & echo $! > {shlex.quote(str(pid_file))}; wait", throwaway,
                limit=0.5)
    assert time.monotonic() - start < 5
    assert found.pieces() == [] and "didn't finish within 0.5 s" in found.problem
    pid = int(pid_file.read_text())
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("the line's process outlived the run")


def test_what_a_line_leaves_running_is_killed_when_it_exits(throwaway, tmp_path):
    # The line exits at once and leaves a child that would write a second later. The
    # child holds no pipe, so the run ends with the line.
    late, pid_file = tmp_path / "late", tmp_path / "pid"
    found = run(f"(sleep 1; echo x > {shlex.quote(str(late))}) >/dev/null 2>&1 & "
                f"echo $! > {shlex.quote(str(pid_file))}; echo hello", throwaway)
    assert found.problem is None
    pid = int(pid_file.read_text())
    for _ in range(25):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("what the line left running outlived the run")
    assert not late.exists()


def test_a_non_zero_exit_detects_nothing(throwaway):
    found = run("echo /x; exit 3", throwaway)
    assert found.pieces() == [] and found.problem == (
        "Your status line exited with code 3, so nothing was detected.")


@pytest.mark.parametrize("command", ["true", r"printf '\033[0m\n'"])
def test_a_line_that_prints_nothing_detects_nothing(command, throwaway):
    found = run(command, throwaway)
    assert found.pieces() == [] and found.problem == (
        "Your status line printed nothing, so nothing was detected.")


def test_writes_under_home_land_in_the_throwaway_dir(line, throwaway, tmp_path):
    seen = tmp_path / "seen.json"
    found = run(line(f"""
        import json, os, sys
        d = json.load(sys.stdin)
        home = os.environ["HOME"]
        os.makedirs(os.path.join(home, ".cache", "line"), exist_ok=True)
        with open(os.path.join(home, ".cache", "line", "last.json"), "w") as f:
            json.dump(d, f)
        with open({str(seen)!r}, "w") as f:
            json.dump({{"home": home, "env": dict(os.environ)}}, f)
        print(d["model"]["display_name"])
    """), throwaway)
    assert found.pieces() == ["model"]
    record = json.loads(seen.read_text())
    assert record["home"].startswith(str(throwaway) + os.sep)
    assert not os.path.exists(record["home"])
    assert not (Path(os.environ["HOME"]) / ".cache" / "line").exists()
    names = record["env"]
    assert "CLAUDE_CONFIG_DIR" not in names
    assert [n for n in names if n.startswith("MOXI_")] == []
    assert [n for n in names if n.startswith("XDG_") and n.endswith("_HOME")] == []


def test_a_command_naming_home_is_found(throwaway):
    script = Path(os.environ["HOME"]) / "my line.py"
    script.write_text(ALL)
    found = run(f"{shlex.quote(sys.executable)} ~/'my line.py'", throwaway)
    assert found.problem is None and "model" in found.pieces()
    found = run(f'{shlex.quote(sys.executable)} "$HOME/my line.py"', throwaway)
    assert "model" in found.pieces()


def test_a_command_that_cannot_run_detects_nothing(throwaway):
    found = run("/no/such/line.sh", throwaway)
    assert found.pieces() == [] and "exited with code 127" in found.problem


# -- the check before the run ------------------------------------------------------------

def _clashing() -> list[str]:
    """Letters for one draw whose session id starts with the model marker's first 6."""
    return ["folderpp", "branchqq", "ghijklmn", "modelidr", "transcrs",
            "ghijklzz", "wxyz", "wxyz", "wxyz", "wxyzwxyzwxyz"]


#: A throwaway folder's path with no digit in it, and a clock held still: the numbers a
#: draw takes then depend on the scripted draws alone, never on a pytest temp folder's
#: number or on the second the test runs.
ROOT = "/t/rootwxyz"


def _still() -> float:
    return _held(2030, 3, 4, 9, 15, 0)


def test_a_forced_collision_draws_again():
    draw = Scripted(letters=_clashing())
    drawn = detect.plan(ROOT, draw, now=_still)
    assert drawn is not None
    assert draw.letter_calls == 20
    assert drawn.session[:6] != "ghijkl"
    assert drawn.clash() is None


def test_a_collision_every_time_detects_nothing(line, throwaway, monkeypatch):
    # Made from inside the throwaway folder, the root is `./rootwxyz`: no digit in it.
    monkeypatch.chdir(throwaway)
    draw = Scripted(letters=["rootwxyz"] + _clashing() * (detect.REDRAWS + 1))
    found = detect.detect(line(ALL), tmp=".", now=_still, draw=draw)
    assert found.pieces() == []
    assert found.problem.startswith("Moxi couldn't draw test input")
    assert not found.ran


def test_a_temp_path_holding_a_marker_number_draws_again(tmp_path):
    root = tmp_path / "pytest-37" / "rootwxyz"
    draw = Scripted(numbers={(11, 49): [37, 21, 22, 23, 24, 25]})
    drawn = detect.plan(str(root), draw)
    assert drawn is not None and 37 not in (drawn.context, drawn.five_hour,
                                            drawn.seven_day)


def test_every_marker_is_found_by_its_own_rule_only():
    drawn = detect.plan(ROOT, Scripted())
    data = json.dumps(drawn.payload())
    for marker in drawn.markers:
        if marker.piece == "branch":
            continue
        assert marker.find(data), marker
    assert detect.Marker("x", "37", "number").find("4137 a37f _37_ 8.37 37.5") == []
    assert detect.Marker("x", "37", "number").find("ctx:37% (37)") == [4, 9]
    assert detect.Marker("x", "ghijkl", "prefix").find("#ghijklmn xghijkl") == [1]
    assert detect.Marker("x", "xhigh", "word").find("xhighs xhigh_ (xhigh)") == [15]


def test_the_sandbox_unsets_homes_and_moxi_variables():
    env = detect.sandbox({"PATH": "/bin", "HOME": "/h", "MOXI_ROOT": "x",
                          "CLAUDE_CONFIG_DIR": "/c", "XDG_CONFIG_HOME": "/x",
                          "XDG_RUNTIME_DIR": "/r", "TERM": "dumb"}, "/fake")
    assert env == {"PATH": "/bin", "HOME": "/fake", "XDG_RUNTIME_DIR": "/r",
                   "TERM": "dumb"}


def test_home_in_the_command_is_the_real_one():
    assert detect.homed("~/a.sh --x=~/b $HOME/c ${HOME}/d a~b $HOMEY", "/h") == \
        "/h/a.sh --x=/h/b /h/c /h/d a~b $HOMEY"

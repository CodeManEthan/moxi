"""The key rule of `answer_screen`, on a fake pane with its own clock, and the mismatches
store.

The fake draws a dialog and, like the real ones, redraws a set delay after each key
(300 ms unless a test says otherwise), so a key decided on the frame before the redraw
fails these tests. Its clock moves only when the code under test sleeps.
"""

from __future__ import annotations

import json
import os

import pytest

from moxi_slots import answer, places
from moxi_slots.answer import Frame, drive, prune, save_mismatch
from moxi_slots.model import NOT_SETTLED
from moxi_slots.screens import CLAUDE_DEV_CHANNELS, CLAUDE_EXIT_DIALOG, CLAUDE_TRUST_FOLDER

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/answer.py",
                                 "packages/slots/src/moxi_slots/screens*")

WIDTH = 60


class FakeDialog:
    """A dialog on a fake pane.

    `head` rows come first, then the choice rows (the marker on `marked`), then `foot`.
    `on_enter`: `die` (the pane dies), `leave` (the dialog is replaced by a prompt),
    `ignore`. `moves`: whether Up/Down move the marker (`wrong` moves it the other way).
    `torn`: seconds after each key during which a read shows a half-drawn frame.
    `dead_at`: the clock time the pane dies by itself."""

    def __init__(self, head, choices, foot, *, marked=0, delay=0.3, on_enter="leave",
                 moves="yes", torn=0.0, dead_at=None, in_mode=False):
        self.head, self.choices, self.foot = list(head), list(choices), list(foot)
        self.marked = marked
        self.delay, self.on_enter, self.moves, self.torn = delay, on_enter, moves, torn
        self.dead_at, self.in_mode = dead_at, in_mode
        self.t = 0.0
        self.pending: list[tuple[float, str]] = []
        self.keys: list[tuple[float, str, int]] = []   # (time, key, marked row at the key)
        self.reads = 0
        self.dead = False
        self.left = False
        self.last_key_at = None

    # The clock.
    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(seconds, 0.0)
        self._apply()

    def _apply(self) -> None:
        for due, key in sorted(self.pending):
            if due > self.t:
                continue
            self.pending.remove((due, key))
            if key == "Enter":
                if self.on_enter == "die":
                    self.dead = True
                elif self.on_enter == "leave":
                    self.left = True
            elif self.moves != "no":
                step = 1 if key == "Down" else -1
                if self.moves == "wrong":
                    step = -step
                self.marked = min(max(self.marked + step, 0), len(self.choices) - 1)
        if self.dead_at is not None and self.t >= self.dead_at:
            self.dead = True

    def text(self) -> str:
        if self.left:
            return "\n".join(["─" * WIDTH, "❯ ", "─" * WIDTH, "  ready", ""])
        if self.last_key_at is not None and self.t - self.last_key_at < self.torn:
            return "\n".join(["history", "", ""])
        rows = ["history", "─" * WIDTH, *self.head]
        rows += [(" ❯ " if i == self.marked else "   ") + c for i, c in enumerate(self.choices)]
        return "\n".join([*rows, *self.foot, ""])

    # The pane.
    def read(self) -> Frame:
        self.reads += 1
        self._apply()
        return Frame(self.text(), WIDTH, self.dead, self.in_mode)

    def send(self, key: str) -> None:
        assert key in answer.KEYS
        self.keys.append((self.t, key, self.marked))
        self.last_key_at = self.t
        self.pending.append((self.t + self.delay, key))

    def ended(self, timeout: float) -> bool:
        end = self.t + timeout
        while self.t < end and not self.dead:
            self.sleep(0.1)
        return self.dead

    def sent(self) -> list[str]:
        return [k for _, k, _ in self.keys]


def trust(**kw) -> FakeDialog:
    return FakeDialog([" Accessing workspace:", "", " /work", ""],
                      ["No, exit", "Yes, I trust this folder"],
                      ["", " Enter to confirm · Esc to cancel"], **kw)


def dev(**kw) -> FakeDialog:
    return FakeDialog(["  WARNING: Loading development channels", ""],
                      ["1. I am using this for local development", "2. Exit"],
                      ["", "  Enter to confirm · Esc to cancel"], **kw)


def exit_dialog(items=("shell · sleep 600",), **kw) -> FakeDialog:
    kw.setdefault("on_enter", "die")
    return FakeDialog(["  Background work is running", "",
                       "  The following will stop when you exit:", "",
                       *("  " + i for i in items), ""],
                      ["1. Exit and stop tasks", "2. Move to background and exit", "3. Stay"],
                      ["", "  Enter to confirm · Esc to cancel"], **kw)


def test_the_trust_screen_gets_one_down_then_enter_on_the_settled_read():
    pane = trust()
    done = drive(pane, CLAUDE_TRUST_FOLDER, 5)
    assert (done.outcome, done.why) == ("gone", "")
    assert pane.sent() == ["Down", "Enter"]
    (down_at, _, _), (enter_at, _, marked_at_enter) = pane.keys
    # Enter went after the redraw, on the option, with the new row held 0.3 s.
    assert marked_at_enter == 1
    assert enter_at >= down_at + pane.delay + answer.SETTLE_GAP


def test_a_slow_redraw_is_waited_for_not_answered_early():
    pane = trust(delay=1.2)
    done = drive(pane, CLAUDE_TRUST_FOLDER, 5)
    assert done.outcome == "gone" and pane.sent() == ["Down", "Enter"]


def test_the_first_key_waits_for_two_full_reads_half_a_second_apart():
    pane = dev()
    done = drive(pane, CLAUDE_DEV_CHANNELS, 5)
    assert done.outcome == "gone" and pane.sent() == ["Enter"]
    assert pane.keys[0][0] >= answer.STABLE


def test_dev_channels_marker_on_the_option_is_enter_alone():
    pane = dev()
    assert drive(pane, CLAUDE_DEV_CHANNELS, 5).outcome == "gone"
    assert pane.sent() == ["Enter"]


def test_exit_dialog_with_wait_zero_is_one_read_then_enter():
    pane = exit_dialog()
    done = drive(pane, CLAUDE_EXIT_DIALOG, 0)
    assert done.outcome == "ended"
    assert pane.sent() == ["Enter"]
    assert pane.keys[0][0] == 0.0 and pane.reads == 1


def test_exit_dialog_marker_off_the_option_sends_nothing():
    pane = exit_dialog(marked=2)
    done = drive(pane, CLAUDE_EXIT_DIALOG, 0)
    assert done.outcome == "mismatch" and "Enter alone" in done.why
    assert pane.sent() == []


def test_exit_dialog_with_a_subagent_row_sends_nothing():
    pane = exit_dialog(items=("subagent · Run sleep 90",))
    done = drive(pane, CLAUDE_EXIT_DIALOG, 0)
    assert done.outcome == "mismatch" and "subagent" in done.why
    assert pane.sent() == []


def test_exit_dialog_that_ignores_enter_is_not_ended():
    pane = exit_dialog(on_enter="ignore")
    done = drive(pane, CLAUDE_EXIT_DIALOG, 0)
    assert done.outcome == "not-ended" and pane.sent() == ["Enter"]


def test_a_key_that_never_takes_effect_is_not_settled_and_no_enter_follows():
    pane = trust(moves="no")
    done = drive(pane, CLAUDE_TRUST_FOLDER, 5)
    assert done.outcome == "mismatch"
    assert done.why.startswith(NOT_SETTLED) and "stayed on row" in done.why
    assert pane.sent() == ["Down"]


def test_a_torn_frame_inside_the_settle_window_keeps_polling():
    pane = trust(torn=0.25)
    done = drive(pane, CLAUDE_TRUST_FOLDER, 5)
    assert done.outcome == "gone" and pane.sent() == ["Down", "Enter"]


def test_a_frame_torn_to_the_bound_is_not_settled():
    pane = trust(torn=10.0)
    done = drive(pane, CLAUDE_TRUST_FOLDER, 5)
    assert done.outcome == "mismatch" and done.why.startswith(NOT_SETTLED)
    assert "no longer fully matches" in done.why
    assert pane.sent() == ["Down"]


def test_a_marker_that_moves_away_ends_the_call():
    pane = trust(moves="wrong", marked=0)
    pane.choices.insert(0, "Maybe later")
    pane.marked = 1
    done = drive(pane, CLAUDE_TRUST_FOLDER, 5)
    assert done.outcome == "mismatch" and "moved away" in done.why
    assert pane.sent() == ["Down"]


def test_a_dead_pane_is_never_a_settled_read():
    pane = trust(dead_at=0.0)
    done = drive(pane, CLAUDE_TRUST_FOLDER, 5)
    assert done.outcome == "ended-without-screen" and pane.sent() == []
    pane = exit_dialog(dead_at=0.0)
    assert drive(pane, CLAUDE_EXIT_DIALOG, 0).outcome == "ended-without-screen"
    assert pane.sent() == []


def test_a_pane_that_dies_after_a_move_gets_no_further_key():
    pane = trust(dead_at=0.7)
    done = drive(pane, CLAUDE_TRUST_FOLDER, 5)
    assert done.outcome == "ended-without-screen" and pane.sent() == ["Down"]


def test_wait_bounds_the_whole_call():
    pane = trust(delay=1.5)
    done = drive(pane, CLAUDE_TRUST_FOLDER, 1.0)
    assert done.outcome == "mismatch" and done.why.startswith(NOT_SETTLED)
    assert "Enter" not in pane.sent()
    assert pane.now() <= 1.0 + 1e-9


def test_no_key_is_sent_without_time_to_settle_it():
    pane = trust()
    done = drive(pane, CLAUDE_TRUST_FOLDER, 0)
    assert done.outcome == "mismatch" and done.why.startswith(NOT_SETTLED)
    assert pane.sent() == []


def test_a_screen_that_never_settles_in_wait_is_a_mismatch():
    pane = trust(in_mode=True)
    done = drive(pane, CLAUDE_TRUST_FOLDER, 2)
    assert done.outcome == "mismatch" and "copy mode" in done.why
    assert pane.sent() == [] and pane.now() <= 2.0


def test_a_screen_that_stays_after_enter_is_a_mismatch():
    pane = dev(on_enter="ignore")
    done = drive(pane, CLAUDE_DEV_CHANNELS, 5)
    assert done.outcome == "mismatch" and "still there" in done.why
    assert pane.sent() == ["Enter"]


def test_a_gone_screen_whose_pane_dies_after_enter_is_a_mismatch():
    pane = dev(on_enter="die")
    done = drive(pane, CLAUDE_DEV_CHANNELS, 5)
    assert done.outcome == "mismatch" and "ended after Enter" in done.why


def test_one_call_presses_enter_at_most_once():
    for make, screen, wait in ((trust, CLAUDE_TRUST_FOLDER, 5), (dev, CLAUDE_DEV_CHANNELS, 5),
                               (exit_dialog, CLAUDE_EXIT_DIALOG, 0)):
        pane = make(on_enter="ignore")
        drive(pane, screen, wait)
        assert pane.sent().count("Enter") == 1


def test_a_gone_pane_is_no_pane():
    class Gone:
        def read(self):
            return None

        now = staticmethod(lambda: 0.0)
        sleep = staticmethod(lambda s: None)

    assert drive(Gone(), CLAUDE_TRUST_FOLDER, 5).outcome == "no-pane"


# -- the mismatches store ---------------------------------------------------------------

def test_a_mismatch_saves_the_region_plain_and_with_colour():
    text = "history\n" + "─" * 20 + "\n ❯ No, exit\n   Yes\n\n\n"
    ansi = "\x1b[1mhistory\x1b[0m\n\x1b[33m" + "─" * 20 + "\x1b[0m\n \x1b[1m❯\x1b[0m No, exit\n" \
           "   Yes\n\n"
    path = save_mismatch(CLAUDE_TRUST_FOLDER, "no row is the option", Frame(text, 20), ansi)
    assert path.parent == places.mismatches_dir()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    record = json.loads(path.read_text())
    assert record["screen"] == "CLAUDE_TRUST_FOLDER" and record["width"] == 20
    assert record["plain"] == [" ❯ No, exit", "   Yes"]
    assert record["ansi"] == [" \x1b[1m❯\x1b[0m No, exit", "   Yes"]
    assert "history" not in json.dumps(record)


def test_the_store_keeps_the_last_twenty():
    frame = Frame("─" * 10 + "\nx\n", 10)
    paths = [save_mismatch(CLAUDE_DEV_CHANNELS, f"n{i}", frame, "") for i in range(25)]
    kept = sorted(os.listdir(places.mismatches_dir()))
    assert len(kept) == answer.KEEP == 20
    assert kept == sorted(p.name for p in paths[-20:])


def test_prune_of_no_store_is_nothing():
    assert prune() == []

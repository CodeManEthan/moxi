"""`read_screen`, `answer_screen` and `pane_use` against a real tmux: a small program
(`screen_program.py`) draws a captured screen in a slot's run pane and answers keys a set
delay after each, the way the dialogs do. Each test has its own private server.

Skipped, with the reason, where tmux 3.2 or newer is not on PATH.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from moxi_slots import answer, cli, doctor, launch, places, slots, tmux
from moxi_slots.answer import answer_screen, pane_use, read_screen
from moxi_slots.errors import Refused, UsageError
from moxi_slots.screens import (CLAUDE_DEV_CHANNELS, CLAUDE_EXIT_DIALOG, CLAUDE_TRUST_FOLDER,
                                SCREENS, plain)

from slots_kit import pane_field, wait_for

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/answer.py",
                                 "packages/slots/src/moxi_slots/screens*",
                                 "packages/slots/src/moxi_slots/cli.py")

HERE = Path(__file__).parent
PROGRAM = HERE / "screen_program.py"
FIXTURES = HERE / "fixtures" / "screens"
EXIT = FIXTURES / "claude-2.1.296-exit-shell-160.ansi.txt"
SUBAGENT = FIXTURES / "claude-2.1.296-exit-subagent-160.plain.txt"
TRUST = FIXTURES / "claude-2.1.314-trust-120x40.plain.txt"
DEV = FIXTURES / "claude-2.1.314-dev-channels-120x40.plain.txt"
EXIT_CHOICES = "Exit and stop tasks|Move to background and exit|Stay"
TRUST_CHOICES = "No, exit|Yes, I trust this folder"
DEV_CHOICES = "I am using this for local development|Exit"


@pytest.fixture
def wide(server):
    """The private server, with new windows 160 columns wide, and slot 1."""
    tmux.call("set-option", "-g", "default-size", "160x45")
    slots.create([1])
    return server


def draw(tmp_path, fixture: Path, *, choices: str, on_enter="exit", delay=0.3,
         slot=1) -> tuple[str, Path]:
    """Run the program in the slot; return its pane id and key log, once it has drawn."""
    log = tmp_path / f"keys-{fixture.stem}.log"
    placed = launch.start(slot, [sys.executable, str(PROGRAM), str(fixture), str(log),
                                 "--choices", choices, "--delay", str(delay),
                                 "--on-enter", on_enter],
                          env={"PATH": os.environ["PATH"], "LANG": "C.UTF-8"},
                          cwd=str(tmp_path), label="dialog")
    assert placed.exec == "started", placed
    wait_for(lambda: "Enter to confirm" in tmux.call("capture-pane", "-p", "-t",
                                                      placed.pane_id).out,
             what="the program to draw")
    return placed.pane_id, log


def keys(log: Path) -> list[str]:
    return log.read_text().split() if log.exists() else []


def test_the_exit_dialog_answered_ends_the_pane(wide, tmp_path):
    pane, log = draw(tmp_path, EXIT, choices=EXIT_CHOICES)
    done = answer_screen(1, CLAUDE_EXIT_DIALOG, wait_s=5)
    assert (done.outcome, done.why, done.keys) == ("ended", "", ("Enter",))
    assert done.capture is None
    assert keys(log) == ["Enter"]
    assert pane_field(pane, "pane_dead") == "1"


def test_the_exit_step_with_wait_zero_after_a_read(wide, tmp_path):
    pane, log = draw(tmp_path, EXIT, choices=EXIT_CHOICES)
    seen = read_screen(1, [CLAUDE_EXIT_DIALOG])
    assert (seen.screen, seen.full, seen.dead, seen.pane_id) == \
        (CLAUDE_EXIT_DIALOG, True, False, pane)
    done = answer_screen(1, CLAUDE_EXIT_DIALOG, wait_s=0)
    assert done.outcome == "ended" and keys(log) == ["Enter"]


def test_a_changed_screen_is_a_mismatch_and_no_key_is_received(wide, tmp_path):
    _, log = draw(tmp_path, SUBAGENT, choices=EXIT_CHOICES)
    done = answer_screen(1, CLAUDE_EXIT_DIALOG, wait_s=1)
    assert done.outcome == "mismatch" and "subagent" in done.why
    assert done.keys == () and keys(log) == []
    record = json.loads(Path(done.capture).read_text())
    assert Path(done.capture).parent == places.mismatches_dir()
    assert record["screen"] == "CLAUDE_EXIT_DIALOG"
    assert any("subagent" in row for row in record["plain"])
    assert [plain(r).rstrip() for r in record["ansi"]] == record["plain"]
    row = doctor.mismatches_row()
    assert row.state == "ok" and row.detail.startswith("1 saved")


def test_an_exit_dialog_that_ignores_enter_is_not_ended(wide, tmp_path, monkeypatch):
    monkeypatch.setattr(answer, "ENDED_WAIT", 1.0)
    pane, log = draw(tmp_path, EXIT, choices=EXIT_CHOICES, on_enter="ignore")
    done = answer_screen(1, CLAUDE_EXIT_DIALOG, wait_s=5)
    assert done.outcome == "not-ended" and keys(log) == ["Enter"]
    assert pane_field(pane, "pane_dead") == "0"


def test_the_trust_screen_gets_down_then_enter(wide, tmp_path):
    _, log = draw(tmp_path, TRUST, choices=TRUST_CHOICES, on_enter="leave")
    seen = read_screen(1, SCREENS)
    assert seen.screen is CLAUDE_TRUST_FOLDER and seen.full
    done = answer_screen(1, CLAUDE_TRUST_FOLDER, wait_s=10)
    assert (done.outcome, done.why) == ("gone", "")
    assert keys(log) == ["Down", "Enter"] and done.keys == ("Down", "Enter")
    assert read_screen(1, SCREENS).screen is None


def test_dev_channels_gets_enter(wide, tmp_path):
    _, log = draw(tmp_path, DEV, choices=DEV_CHOICES, on_enter="leave")
    done = answer_screen(1, CLAUDE_DEV_CHANNELS, wait_s=5)
    assert done.outcome == "gone" and keys(log) == ["Enter"]


def test_a_dead_pane_is_ended_without_screen(wide, tmp_path):
    pane, log = draw(tmp_path, EXIT, choices=EXIT_CHOICES, on_enter="exit")
    tmux.call("send-keys", "-t", pane, "Enter")
    wait_for(lambda: pane_field(pane, "pane_dead") == "1", what="the pane to die")
    seen = read_screen(1, [CLAUDE_EXIT_DIALOG])
    assert seen.dead and not seen.full
    done = answer_screen(1, CLAUDE_EXIT_DIALOG, wait_s=0)
    assert done.outcome == "ended-without-screen" and done.keys == ()


def test_a_slot_with_no_run_pane(wide):
    assert read_screen(1, SCREENS).screen is None
    assert read_screen(1, SCREENS).why == "slot 1 has no run pane"
    done = answer_screen(1, CLAUDE_TRUST_FOLDER)
    assert done.outcome == "no-pane" and done.keys == ()
    use = pane_use(1)
    assert use.pane_id is None and use.slot == 1


def test_an_unknown_slot_is_refused(wide):
    with pytest.raises(Refused) as caught:
        read_screen(7, SCREENS)
    assert caught.value.reason == "no-slot"
    with pytest.raises(UsageError):
        read_screen(1, ["CLAUDE_TRUST_FOLDER"])
    with pytest.raises(UsageError):
        answer_screen(1, CLAUDE_TRUST_FOLDER, wait_s=-1)


def test_pane_use_reads_copy_mode_and_activity(wide, tmp_path):
    pane, _ = draw(tmp_path, TRUST, choices=TRUST_CHOICES)
    use = pane_use(1)
    assert (use.pane_id, use.pane_in_mode, use.attached, use.client_activity) == \
        (pane, False, 0, None)
    assert use.pane_activity is not None and use.pane_activity > 0
    tmux.call("copy-mode", "-t", pane)
    assert pane_use(1).pane_in_mode is True
    seen = read_screen(1, SCREENS)
    assert seen.screen is CLAUDE_TRUST_FOLDER and not seen.full
    assert "copy mode" in seen.why


def test_the_screen_verb(wide, tmp_path, capsys):
    draw(tmp_path, TRUST, choices=TRUST_CHOICES)
    assert cli.main(["screen", "1"]) == 0
    assert capsys.readouterr().out.strip() == "CLAUDE_TRUST_FOLDER: full match"
    assert cli.main(["screen", "1", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["screen"] == "CLAUDE_TRUST_FOLDER" and out["full"] is True
    assert "Yes, I trust this folder" in "\n".join(out["rows"])
    assert cli.main(["screen", "9"]) == 3

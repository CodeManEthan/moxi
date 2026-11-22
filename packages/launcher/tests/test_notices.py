"""The wording of a relaunch notice and of a session's note: a run's detail is a sentence
of its own, and a run's word that repeats its outcome is not said twice."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from moxi_launcher import notices


def run(outcome, word=None, detail=None, *, mode="director", slot=16):
    return SimpleNamespace(run_id="r1", session_id="s1", slot=slot, outcome=outcome,
                           word=word, detail=detail, mode=mode, repair_of=None)


def test_a_detail_with_no_full_stop_gets_one():
    said = notices.text(run("relaunch-failed", "no-cwd", "the directory /work does not "
                            "exist"), None, "relaunch-failed")
    assert said == ("Relaunch notice from launcher: run r1 for session s1 in slot 16 ended "
                    "relaunch-failed: no-cwd. The directory /work does not exist. The "
                    "session is down and was not launched again.")


@pytest.mark.parametrize("detail", ["It ended.", "It ended?", "It ended!"])
def test_a_detail_that_ends_a_sentence_is_left_as_it_is(detail):
    said = notices.text(run("gone", "gone", detail), None, "gone")
    assert said == ("Relaunch notice from launcher: run r1 for session s1 in slot 16 ended "
                    f"gone. {detail} The session is down.")


def test_a_detail_starts_its_sentence_with_a_capital():
    said = notices.text(run("gone", "gone", "it ended."), None, "gone")
    assert "ended gone. It ended. The session is down." in said


def test_a_gone_notice_says_gone_once():
    said = notices.text(run("gone", "gone", "session s1 is not live"), None, "gone")
    assert said == ("Relaunch notice from launcher: run r1 for session s1 in slot 16 ended "
                    "gone. Session s1 is not live. The session is down.")


def test_a_note_not_taken_says_the_runs_outcome_once():
    said = notices.text(run("stopped", "stopped", "the job was sent stop", mode="self"),
                        None, notices.NOTE_NOT_TAKEN)
    assert said == ("Relaunch notice from launcher: run r1 for session s1 in slot 16 ended "
                    "note-not-taken (the run ended stopped). The session was not touched, "
                    "and did not take the note saying so.")


def test_a_note_not_taken_keeps_a_word_of_its_own():
    said = notices.text(run("refused", "no-cwd", "x", mode="self"), None,
                        notices.NOTE_NOT_TAKEN)
    assert "ended note-not-taken (the run ended refused: no-cwd)." in said


def test_an_error_note_with_no_word_of_its_own_says_error_once():
    assert notices.note_text(run("error", "error", "it broke", mode="self")) == (
        "Relaunch note from launcher: run r1 ended error (it broke). The relaunch stopped "
        "with an error before the exit: carry on with your work.")
    assert notices.note_text(run("error", "turns-failed", "it broke", mode="self")) == (
        "Relaunch note from launcher: run r1 ended error (turns-failed: it broke). The "
        "relaunch stopped with an error before the exit: carry on with your work.")


def test_a_not_exited_note_with_no_word_of_its_own_says_not_exited_once():
    assert notices.note_text(run("not-exited", None, "no dialog", mode="self")) == (
        "Relaunch note from launcher: run r1 ended not-exited (no dialog). The relaunch did "
        "not exit this session: carry on with your work, and arm it again at your next "
        "pause point if you still need it.")
    assert notices.note_text(run("not-exited", "no-dialog", "no dialog", mode="self")) == (
        "Relaunch note from launcher: run r1 ended not-exited (no dialog). The relaunch did "
        "not exit this session (no-dialog): carry on with your work, and arm it again at "
        "your next pause point if you still need it.")


def test_a_stopped_note_names_its_outcome_once():
    assert notices.note_text(run("stopped", "stopped", "the job was sent stop",
                                 mode="self")) == (
        "Relaunch note from launcher: run r1 did not run (stopped: the job was sent stop). "
        "Nothing was exited. Carry on, and arm it again at your next pause point if you "
        "still need it.")

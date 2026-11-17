"""The exit rule and the exit watch of a headless session (`adapters/headless.py`): the
parser over every value and refusal, and the watch on a fake clock, one case per clause
of the ending rule."""

from __future__ import annotations

import os
import signal
import subprocess
import sys

import pytest

from conftest import cache_absent
from moxi_comms.adapters import headless
from moxi_comms.adapters.headless import ExitRule, ExitWatch, parse_exit
from moxi_comms.adapters.terminal import pidfd_open


# --------------------------------------------------------------------------- the parser

@pytest.mark.parametrize("values, rule", [
    ([], ExitRule(values=("done",), done=True)),
    (None, ExitRule(values=("done",), done=True)),
    (["done"], ExitRule(values=("done",), done=True)),
    (["cold"], ExitRule(values=("cold",), cold=True)),
    (["idle=90m"], ExitRule(values=("idle=90m",), idle_s=5400)),
    (["after=2h"], ExitRule(values=("after=2h",), after_s=7200)),
    (["after=30s", "done"], ExitRule(values=("after=30s", "done"), done=True, after_s=30)),
    (["cold", "idle=1h", "after=12h", "done"],
     ExitRule(values=("cold", "idle=1h", "after=12h", "done"), done=True, cold=True,
              idle_s=3600, after_s=43200)),
])
def test_parse_exit_reads_every_value(values, rule):
    assert parse_exit(values, harness="codex") == rule


@pytest.mark.parametrize("values, words", [
    (["soon"], "give done, cold, idle=<D> or after=<D>"),
    (["idle"], "give done, cold"),
    (["idle=90"], "whole number above 0 with its unit"),
    (["idle=0m"], "whole number above 0"),
    (["after=-5m"], "whole number above 0"),
    (["after=1.5h"], "whole number above 0"),
    (["after=2d"], "unit, s, m or h"),
    (["idle= 5m"], "whole number"),
    (["done", "done"], "--exit done is given twice"),
    (["idle=5m", "idle=10m"], "--exit idle= is given twice"),
    (["after=5m", "after=5m"], "--exit after= is given twice"),
])
def test_parse_exit_refuses_with_one_line_naming_the_fix(values, words):
    with pytest.raises(ValueError, match=None) as caught:
        parse_exit(values, harness="codex")
    assert words in str(caught.value) and "\n" not in str(caught.value)


@pytest.mark.parametrize("harness", ["claude", "opencode"])
def test_parse_exit_refuses_a_harness_with_no_headless_launch(harness):
    """Only a harness whose wrapper takes the headless flags, the ones comms' API names
    in `HEADLESS`, has a rule to parse."""
    with pytest.raises(ValueError, match=f"{harness} has no headless launch; one of "
                                         "codex has"):
        parse_exit(["done"], harness=harness)
    assert headless.HEADLESS == frozenset({"codex"})


def test_parse_exit_takes_a_list_not_one_string():
    with pytest.raises(TypeError):
        parse_exit("done", harness="codex")


def test_cold_needs_the_cache_module(monkeypatch):
    cache_absent(monkeypatch)
    with pytest.raises(ValueError, match="cold needs the cache module"):
        parse_exit(["cold"], harness="codex")
    assert parse_exit(["idle=5m"], harness="codex").idle_s == 300


@pytest.mark.parametrize("values, gap", [
    (["idle=5m"], True), (["after=5m"], True), (["idle=5m", "cold"], False),
    (["done"], False), ([], False), (["cold"], False), (["done", "after=1h"], True),
])
def test_the_cold_gap_is_idle_or_after_without_cold(values, gap):
    assert parse_exit(values, harness="codex").cold_gap is gap


def test_the_done_read_waits_past_the_daemons_settle_window():
    from moxi_comms import daemon

    assert headless.DONE_READ_AFTER_S >= max(daemon.RELEASE_SETTLE_STEPS) + headless.TICK_S


# --------------------------------------------------------------------------- the watch

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def watch(*values, found=True, offers_shells=False):
    clock = Clock()
    w = ExitWatch(parse_exit(list(values), harness="codex"), clock)
    w.clock_ = clock
    w.offers_shells = offers_shells
    if found:
        w.on_found()
    return w


def answered(w, outcome="answer"):
    """The brief's turn runs and ends."""
    w.on_turn_started()
    w.clock_.advance(1)
    w.on_turn_ended(outcome, is_brief=True)


def past_window(w):
    w.clock_.advance(headless.DONE_READ_AFTER_S)


# clause 2: nothing but `after=` and the ready bound before the brief's turn ends

def test_nothing_but_after_fires_before_the_briefs_turn_ends():
    w = watch("done", "cold", "idle=1m", "after=1h")
    w.clock_.advance(120)
    w.note_mail(handed=0, waiting=0)
    w.note_cache_kind("cold")
    assert (w.due(), w.wants_mail_read(), w.wants_cache_read()) == (None, False, False)
    w.on_turn_started()
    w.clock_.advance(3600)
    assert w.due() == "after"


def test_the_ready_bound_fires_brief_failed_from_the_bind():
    w = watch("done", found=False)
    w.clock_.advance(headless.HEADLESS_READY_WAIT_S - 1)
    assert w.due() is None
    w.clock_.advance(1)
    assert w.due() == "brief-failed"
    assert "not ready" in w.why("brief-failed")


def test_an_after_shorter_than_the_ready_bound_fires_first():
    w = watch("after=1m", found=False)
    w.clock_.advance(60)
    assert w.due() == "after"


def test_a_session_whose_brief_turn_started_is_past_the_ready_bound():
    w = watch("done")
    w.on_turn_started()
    w.clock_.advance(headless.HEADLESS_READY_WAIT_S * 2)
    assert w.due() is None


def test_the_briefs_turn_has_the_ready_bound_from_the_find():
    """A brief turns took and never started: the session ends within the bound of its
    being found, at once, with no wait for busy work."""
    w = watch("done", found=False, offers_shells=True)
    w.clock_.advance(headless.HEADLESS_READY_WAIT_S - 10)
    w.on_found()
    w.clock_.advance(headless.HEADLESS_READY_WAIT_S - 1)
    assert w.due() is None
    w.clock_.advance(1)
    assert w.due() == "brief-failed"
    assert w.why("brief-failed") == (
        f"the brief's turn did not start within {headless.HEADLESS_READY_WAIT_S}s")


@pytest.mark.parametrize("why", ["busy-turn", "the endpoint went away"])
def test_a_brief_turns_refuses_or_loses_ends_at_once(why):
    w = watch("done", offers_shells=True)
    w.on_brief_failed(why)
    assert w.due() == "brief-failed"
    assert why in w.why("brief-failed")


@pytest.mark.parametrize("outcome", ["error", "aborted", "refusal"])
def test_a_brief_turn_that_ends_otherwise_than_answer_fails_the_brief(outcome):
    w = watch("idle=1m", "cold")
    answered(w, outcome)
    assert w.due() == "brief-failed"
    assert w.why("brief-failed") == f"the brief's turn ended {outcome}"


@pytest.mark.parametrize("outcome", ["error", "aborted"])
def test_a_failed_brief_turn_does_not_arm_done_cold_or_idle(outcome):
    w = watch("done", "cold", "idle=1m", offers_shells=True)
    answered(w, outcome)
    past_window(w)
    assert not w.wants_mail_read() and not w.wants_cache_read()
    w.note_shells(busy=2)
    assert w.due() is None


def test_only_the_briefs_turn_arms():
    w = watch("idle=1m")
    w.on_turn_started()
    w.on_turn_ended("answer")
    w.clock_.advance(120)
    assert w.due() is None


# `done`

def test_done_reads_only_after_the_settle_window_and_fires_when_nothing_is_handed():
    w = watch("done")
    answered(w)
    assert not w.wants_mail_read()
    w.note_mail(handed=0, waiting=0)       # a read inside the window does not count
    assert w.due() is None
    w.clock_.advance(headless.DONE_READ_AFTER_S - 0.5)
    assert not w.wants_mail_read() and w.due() is None
    w.clock_.advance(0.5)
    assert w.wants_mail_read()
    w.note_mail(handed=0, waiting=0)
    assert not w.wants_mail_read()
    assert w.due() == "done"


def test_done_waits_while_rows_are_handed_and_for_the_turn_they_start():
    w = watch("done")
    answered(w)
    past_window(w)
    w.note_mail(handed=2, waiting=0)
    assert w.due() is None and not w.wants_mail_read()
    w.on_turn_started()                    # the look released the rows: a turn
    assert w.due() is None and not w.wants_mail_read()
    w.clock_.advance(30)
    w.on_turn_ended("answer")
    assert w.due() is None and not w.wants_mail_read()
    past_window(w)
    assert w.wants_mail_read()
    w.note_mail(handed=0, waiting=0)
    assert w.due() == "done"


def test_done_does_not_wait_on_rows_still_waiting_after_the_look():
    w = watch("done")
    answered(w)
    past_window(w)
    w.note_mail(handed=0, waiting=3)
    assert w.due() == "done"
    assert w.waiting == 3


def test_done_never_fires_during_a_turn():
    w = watch("done")
    answered(w)
    past_window(w)
    w.on_turn_started()
    w.note_mail(handed=0, waiting=0)
    w.clock_.advance(3600)
    assert w.due() is None and not w.wants_mail_read()


def test_done_is_not_read_for_a_rule_without_it():
    w = watch("idle=1h")
    answered(w)
    past_window(w)
    assert not w.wants_mail_read()


# `idle=`

def test_idle_counts_from_the_last_turns_end():
    w = watch("idle=10m")
    answered(w)
    w.clock_.advance(500)
    w.on_turn_started()
    w.clock_.advance(1000)
    assert w.due() is None                 # a running turn is not idle
    w.on_turn_ended("answer")
    w.clock_.advance(599)
    assert w.due() is None
    w.clock_.advance(1)
    assert w.due() == "idle"


# `after=`

def test_after_fires_mid_turn_and_waits_for_no_shells():
    w = watch("after=10m", offers_shells=True)
    answered(w)
    w.on_turn_started()
    w.clock_.advance(600)
    assert w.due() == "after"
    assert not w.wants_shells()


# `cold` and `cache-unreadable`

def test_cold_fires_on_a_cold_read_with_no_turn():
    w = watch("cold")
    answered(w)
    assert w.wants_cache_read()
    w.note_cache_kind("warm")
    assert w.due() is None and not w.wants_cache_read()
    w.clock_.advance(headless.CACHE_READ_EVERY_S)
    assert w.wants_cache_read()
    w.note_cache_kind("cold")
    assert w.due() == "cold"


def test_a_cold_read_during_a_turn_does_not_count():
    w = watch("cold")
    answered(w)
    w.on_turn_started()
    assert not w.wants_cache_read()
    w.note_cache_kind("cold")
    w.on_turn_ended("answer")
    assert w.due() is None


@pytest.mark.parametrize("kind", ["no-cache", "warm", "fresh"])
def test_cold_never_fires_on_a_passing_read(kind):
    w = watch("cold")
    answered(w)
    for _ in range(100):
        w.note_cache_kind(kind)
        w.clock_.advance(headless.CACHE_READ_EVERY_S)
    assert w.due() is None


def test_the_first_no_cache_read_is_said_once():
    w = watch("cold")
    answered(w)
    assert w.note_cache_kind("no-cache") == "cold will not fire: the cache reads no-cache"
    assert w.note_cache_kind("no-cache") is None
    assert watch("done").note_cache_kind("no-cache") is None


def test_cache_unreadable_after_the_bound_of_unknown_reads():
    w = watch("cold")
    answered(w)
    w.note_cache_kind("unknown")
    for _ in range(4):
        w.clock_.advance(headless.CACHE_READ_EVERY_S)
        w.note_cache_kind("unknown")
        assert w.due() is None
    w.clock_.advance(headless.HEADLESS_READY_WAIT_S - 4 * headless.CACHE_READ_EVERY_S)
    assert w.due() == "cache-unreadable"


def test_a_passing_read_inside_the_unknown_span_starts_it_again():
    w = watch("cold")
    answered(w)
    w.note_cache_kind("unknown")
    w.clock_.advance(200)
    w.note_cache_kind("warm")
    w.clock_.advance(60)
    w.note_cache_kind("unknown")
    w.clock_.advance(headless.HEADLESS_READY_WAIT_S - 1)
    assert w.due() is None
    w.clock_.advance(1)
    assert w.due() == "cache-unreadable"


def test_a_turn_inside_the_unknown_span_starts_it_again():
    w = watch("cold")
    answered(w)
    w.note_cache_kind("unknown")
    w.clock_.advance(250)
    w.on_turn_started()
    w.on_turn_ended("answer")
    w.clock_.advance(100)
    assert w.due() is None
    w.note_cache_kind("unknown")
    w.clock_.advance(headless.HEADLESS_READY_WAIT_S - 1)
    assert w.due() is None
    w.clock_.advance(1)
    assert w.due() == "cache-unreadable"


def test_unknown_is_not_cache_unreadable_without_cold():
    w = watch("idle=1h")
    answered(w)
    w.note_cache_kind("unknown")
    w.clock_.advance(headless.HEADLESS_READY_WAIT_S)
    assert w.due() is None


# clause 5: the wait for the session's busy work

def test_an_automatic_cause_waits_for_busy_work_and_fires_when_none_is_listed():
    w = watch("idle=1m", offers_shells=True)
    answered(w)
    w.clock_.advance(60)
    assert w.due() is None and w.wants_shells()
    w.note_shells(busy=2)
    assert w.due() is None and not w.wants_shells()
    w.clock_.advance(headless.TICK_S)
    assert w.wants_shells()
    w.note_shells(busy=0)
    assert w.due() == "idle"


def test_a_list_turns_cannot_vouch_for_counts_as_busy_until_the_hour():
    w = watch("idle=1m", offers_shells=True)
    answered(w)
    w.clock_.advance(60)
    assert w.due() is None
    w.note_shells(busy=None)
    assert w.due() is None
    w.clock_.advance(headless.SHELLS_WAIT_S - 61)
    w.note_shells(busy=None)
    assert w.due() is None
    w.clock_.advance(1)
    assert w.due() == "idle"


def test_a_shells_read_from_before_the_cause_does_not_count():
    w = watch("done", offers_shells=True)
    answered(w)
    w.note_shells(busy=0)
    past_window(w)
    w.note_mail(handed=0, waiting=0)
    assert w.due() is None and w.wants_shells()
    w.note_shells(busy=0)
    assert w.due() == "done"


def test_with_no_shells_list_nothing_waits():
    w = watch("idle=1m", offers_shells=False)
    answered(w)
    w.clock_.advance(60)
    assert not w.wants_shells()
    assert w.due() == "idle"


def test_a_turn_drops_the_pending_cause_and_it_is_judged_again_after():
    w = watch("idle=1m", offers_shells=True)
    answered(w)
    w.clock_.advance(60)
    assert w.due() is None and w.pending is not None
    w.note_shells(busy=1)
    w.on_turn_started()
    assert w.pending is None and not w.wants_shells()
    w.note_shells(busy=0)                  # read during the turn: it does not count
    w.on_turn_ended("answer")
    assert w.due() is None
    w.clock_.advance(60)
    assert w.due() is None and w.wants_shells()
    w.note_shells(busy=0)
    assert w.due() == "idle"


def test_a_failed_brief_turn_waits_for_busy_work_too():
    w = watch("done", offers_shells=True)
    answered(w, "error")
    assert w.due() is None and w.wants_shells()
    w.note_shells(busy=0)
    assert w.due() == "brief-failed"


def test_a_brief_refused_waits_for_no_shells():
    w = watch("done", offers_shells=True)
    w.on_brief_failed("busy-turn")
    assert w.due() == "brief-failed" and not w.wants_shells()


# clause 4: first to fire

def test_the_first_cause_to_fire_wins():
    w = watch("idle=2m", "after=3m", "done")
    answered(w)
    w.clock_.advance(60)
    assert w.due() is None
    w.clock_.advance(60)
    assert w.due() == "idle"
    w.clock_.advance(60)
    assert w.due() == "after"              # the adapter ended on idle before this


# --------------------------------------------------------------------------- the end

def test_end_logs_the_cause_and_signals_the_host_through_its_pidfd():
    host = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    said: list = []
    try:
        handle = pidfd_open(host.pid)
        try:
            assert headless.end(handle, "idle", waiting=2, say=said.append)
            assert host.wait(timeout=30) == -signal.SIGTERM
            assert not headless.end(handle, "after", say=said.append)
        finally:
            os.close(handle)
    finally:
        if host.poll() is None:
            host.kill()
            host.wait()
    assert said == ["headless session ended: idle; 2 row(s) left waiting for the name",
                    "headless session ended: after"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="pidfds are Linux's")
def test_end_signals_the_host_when_python_lacks_the_pidfd_wrappers(monkeypatch):
    """A Python built without `os.pidfd_open` and `signal.pidfd_send_signal` still ends
    the host: both helpers fall back to the system calls."""
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    monkeypatch.delattr(signal, "pidfd_send_signal", raising=False)
    host = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        handle = pidfd_open(host.pid)
        try:
            assert headless.end(handle, "idle", say=lambda line: None)
            assert host.wait(timeout=30) == -signal.SIGTERM
            assert not headless.end(handle, "after", say=lambda line: None)
        finally:
            os.close(handle)
    finally:
        if host.poll() is None:
            host.kill()
            host.wait()


def test_the_end_line_names_why_the_brief_failed():
    assert headless.end_line("brief-failed", 0, "the brief's turn ended error") == (
        "headless session ended: brief-failed (the brief's turn ended error); 0 row(s) "
        "left waiting for the name")

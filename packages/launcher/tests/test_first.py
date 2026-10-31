"""The first turn: its text, the sender against a fake turns on the launcher's test seam,
every outcome and the state each leaves, the rule that the text is sent at most once, the
`deliver` verb's function and the detached deliverer."""

from __future__ import annotations

import io
import json
import os
import sys
import threading
from pathlib import Path

import pytest
from moxi import exit as exits
from moxi import modules
from moxi_dev.testing import wait_for

import launcher_fake_turns as fake
from moxi_launcher import first, store

TESTS = Path(__file__).resolve().parent
CLAUDE_ID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
THREAD_ID = "thr-0001"
BRIEF = "Read the design and start the build."


@pytest.fixture
def turns(tmp_path, monkeypatch):
    """The fake turns behind the seam, and a way to script it and read its calls."""
    directory = tmp_path / "fake-turns"
    directory.mkdir()
    monkeypatch.setenv(first.TURNS_SEAM, "launcher_fake_turns")
    monkeypatch.setenv(fake.DIR, str(directory))

    class Rig:
        dir = directory

        def script(self, **plan):
            (directory / "script.json").write_text(json.dumps(plan))

        def calls(self, name=None):
            return [c for c in fake.calls(directory) if name is None or c["call"] == name]

    return Rig()


@pytest.fixture
def conn():
    connection = store.connect()
    yield connection
    connection.close()


def launch(conn, request_id="r-1", *, harness="claude", state="started", brief=BRIEF,
           handoff=None, argv=None, pid=None, mode=None, actor="ada"):
    """A launch row as the launcher leaves it before its first turn."""
    mode = mode or ("interactive" if harness == "claude" else "host")
    store.insert(conn, request_id=request_id, actor=actor, harness=harness,
                 target="terminal", cwd="/work", values={"mode": mode},
                 argv=argv or [harness, "--permission-mode", "default"], env={},
                 posture="go", session_id=CLAUDE_ID if harness == "claude" else None,
                 brief=brief, handoff_path=handoff)
    if state != "spawning":
        for step in ("started", "ready", "sending", "first-sent"):
            store.set_state(conn, request_id, step)
            if step == state:
                break
    if pid is not None:
        store.set_facts(conn, request_id, pid=pid)
    return request_id


def send(conn, request_id="r-1", **kw):
    kw.setdefault("ready_wait_s", 60)
    kw.setdefault("first_wait_s", 120)
    return first.send(conn, request_id, **kw)


def row(conn, request_id="r-1"):
    return store.get(conn, request_id)


def outcomes(conn, request_id="r-1"):
    return [e.detail for e in store.events(conn, request_id) if e.kind == first.EVENT]


# -- the text ---------------------------------------------------------------------------


def test_a_brief_alone_starts_with_its_header():
    assert first.text(actor="ada", brief=BRIEF) == (
        "Brief from launcher, launched by ada\n\n" + BRIEF)


def test_a_handoff_names_its_path_and_comes_before_the_brief(tmp_path):
    path = tmp_path / "2026-10-30-ada.md"
    path.write_text("# Handoff\n\nDone: the store.\n")
    assert first.text(actor="ada@demo", handoff=path) == (
        f"Handoff from launcher, launched by ada@demo: {path}\n\n# Handoff\n\nDone: the store.")
    both = first.text(actor="ada@demo", brief=BRIEF, handoff=path)
    assert both.splitlines()[0] == f"Handoff from launcher, launched by ada@demo: {path}"
    assert both.index("Done: the store.") < both.index(BRIEF)
    assert both.endswith("\n\n" + BRIEF)


def test_a_handoff_read_already_is_not_read_again(tmp_path):
    missing = tmp_path / "gone.md"
    assert first.text(actor="a", handoff=missing, handoff_text="kept") == (
        f"Handoff from launcher, launched by a: {missing}\n\nkept")


def test_no_brief_and_no_handoff_is_no_first_turn():
    assert first.text(actor="a") is None
    assert first.text(actor="a", brief="  \n") is None


def test_an_unreadable_handoff_is_refused(tmp_path):
    with pytest.raises(first.HandoffUnreadable) as caught:
        first.text(actor="a", handoff=tmp_path / "nowhere.md")
    assert caught.value.exit_code == exits.REFUSED
    assert "nowhere.md" in str(caught.value)


def test_the_actor_is_the_comms_name_else_the_user(monkeypatch):
    assert first.actor({"MOXI_COMMS_NAME": "ada@demo"}) == "ada@demo"
    monkeypatch.setattr(first.getpass, "getuser", lambda: "ada")
    assert first.actor({}) == "ada"


def test_a_prompt_on_the_argv_is_seen():
    assert first.on_argv(["claude", "Brief from launcher, launched by a\n\nx", "--x"])
    assert first.on_argv(["codex", "exec", "Handoff from launcher, launched by a: /p"])
    assert not first.on_argv(["claude", "--session-id", CLAUDE_ID])


def test_the_ref_is_the_launch_s_own():
    assert first.ref("r-1") == "launcher:r-1"
    assert first.ref("r-1") != first.ref("r-2")


# -- reaching turns ---------------------------------------------------------------------


def test_the_seam_names_the_module_that_stands_in_for_turns(turns):
    assert first.turns_api() is fake


def test_without_the_seam_turns_is_loaded_with_the_interface_the_sender_speaks(monkeypatch):
    monkeypatch.delenv(first.TURNS_SEAM, raising=False)
    asked = []

    class Found:
        ok = True
        api = "turns' API"

    monkeypatch.setattr(modules, "available", lambda name: True)
    monkeypatch.setattr(modules, "load",
                        lambda name, interface=None: asked.append((name, interface)) or Found)
    assert first.turns_api() == "turns' API"
    assert asked == [("turns", first.TURNS_INTERFACE)]


def test_turns_absent_or_too_old_is_none(monkeypatch):
    monkeypatch.delenv(first.TURNS_SEAM, raising=False)
    monkeypatch.setattr(modules, "available", lambda name: False)
    assert first.turns_api() is None

    class Broken:
        ok = False
        reason = "moxi-turns 0.1 offers interfaces {1}; the caller speaks 2"

    monkeypatch.setattr(modules, "available", lambda name: True)
    monkeypatch.setattr(modules, "load", lambda name, interface=None: Broken)
    assert first.turns_api() is None


# -- the outcomes -----------------------------------------------------------------------


def test_delivered(turns, conn):
    launch(conn)
    outcome = send(conn)
    assert (outcome.outcome, outcome.state, outcome.reason) == ("sent", "first-sent", None)
    assert outcome.exit_code == exits.OK and not outcome.unconfirmed
    assert (row(conn).state, row(conn).reason) == ("first-sent", None)
    [ready] = turns.calls("wait_ready")
    assert (ready["session_id"], ready["pid"], ready["timeout"]) == (CLAUDE_ID, None, 60)
    [given] = turns.calls("deliver")
    assert given["text"] == "Brief from launcher, launched by ada\n\n" + BRIEF
    assert given["ref"] == "launcher:r-1"
    # A plain delivery: the bare turn, with none of the only-if-idle checks.
    assert not any(given[k] for k in ("only_if_idle", "if_turn_number", "refuse_draft",
                                      "refuse_agents"))
    assert [c["timeout"] for c in turns.calls("wait_started")] == [120]
    assert outcomes(conn) == [{"outcome": "started", "ref": "launcher:r-1",
                               "delivery": "d-1", "turn_id": "t-1"}]


def test_a_session_already_ready_is_sent_to(turns, conn):
    launch(conn, state="ready")
    assert send(conn).outcome == "sent"
    assert row(conn).state == "first-sent"


def test_refused(turns, conn):
    turns.script(started={"kind": "refused", "reason": "dropped"})
    launch(conn)
    outcome = send(conn)
    assert (outcome.outcome, outcome.state, outcome.reason) == ("refused", "failed", "dropped")
    assert outcome.exit_code == exits.REFUSED
    assert (row(conn).state, row(conn).reason) == ("failed", "dropped")
    assert len(turns.calls("deliver")) == 1
    assert "refused: dropped" in outcome.line()


def test_refused_at_once_because_nothing_could_be_reached(turns, conn):
    turns.script(started={"kind": "refused", "reason": "gone"})
    launch(conn)
    assert send(conn).outcome == "refused"
    assert (row(conn).state, row(conn).reason) == ("failed", "gone")


@pytest.mark.parametrize("reason", ["expired: no turn after 300 s", "untied: no turn held",
                                    "unanswered", "session ended"])
def test_lost_is_unconfirmed_never_failed(turns, conn, reason):
    turns.script(started={"kind": "lost", "reason": reason})
    launch(conn)
    outcome = send(conn)
    assert (outcome.outcome, outcome.state, outcome.reason) == (
        "unconfirmed", "first-sent", "unconfirmed")
    assert outcome.unconfirmed and outcome.exit_code == exits.OK
    assert (row(conn).state, row(conn).reason) == ("first-sent", "unconfirmed")
    assert len(turns.calls("deliver")) == 1
    assert "unconfirmed" in outcome.line() and "not sent again" in outcome.line()
    assert outcomes(conn)[0]["why"] == f"lost: {reason}"


def test_no_answer_in_time_is_unconfirmed_never_refused(turns, conn):
    turns.script(started="timeout")
    launch(conn)
    outcome = send(conn, first_wait_s=2)
    assert (outcome.outcome, row(conn).state, row(conn).reason) == (
        "unconfirmed", "first-sent", "unconfirmed")
    assert outcome.detail == "not started within 2 s"
    assert len(turns.calls("deliver")) == 1


def test_an_unanswered_hand_in_resolves_by_its_ref(turns, conn):
    turns.script(deliver="unanswered")
    launch(conn)
    outcome = send(conn)
    assert (outcome.outcome, row(conn).state) == ("sent", "first-sent")
    assert [c["ref"] for c in turns.calls("resolve")] == ["launcher:r-1"]
    assert turns.calls("reconnect") == []
    assert len(turns.calls("deliver")) == 1


def test_a_closed_connection_reconnects_once_and_resolves(turns, conn):
    turns.script(deliver="unanswered", connection="closed")
    launch(conn)
    outcome = send(conn)
    assert (outcome.outcome, row(conn).state, row(conn).reason) == (
        "sent", "first-sent", None)
    assert len(turns.calls("reconnect")) == 1
    assert len(turns.calls("deliver")) == 1


def test_a_closed_connection_whose_reconnect_is_refused_is_unconfirmed(turns, conn):
    turns.script(deliver="unanswered", connection="closed", reconnect="refused")
    launch(conn)
    outcome = send(conn)
    assert (outcome.outcome, row(conn).state, row(conn).reason) == (
        "unconfirmed", "first-sent", "unconfirmed")
    assert outcome.detail == "lost: unanswered"
    assert len(turns.calls("reconnect")) == 1
    assert len(turns.calls("deliver")) == 1


def test_a_ref_the_endpoint_never_recorded_is_unconfirmed(turns, conn):
    turns.script(deliver="unanswered", connection="closed", started="unknown")
    launch(conn)
    assert send(conn).outcome == "unconfirmed"
    assert (row(conn).state, row(conn).reason) == ("first-sent", "unconfirmed")
    assert len(turns.calls("deliver")) == 1


@pytest.mark.parametrize("plan", [{"deliver": "raise"}, {"started": "raise"}])
def test_a_failing_call_is_unconfirmed(turns, conn, plan):
    turns.script(**plan)
    launch(conn)
    outcome = send(conn)
    assert (outcome.outcome, row(conn).state, row(conn).reason) == (
        "unconfirmed", "first-sent", "unconfirmed")
    assert outcome.detail.startswith("TurnsError: ")
    assert len(turns.calls("deliver")) == 1


@pytest.mark.parametrize("ready", ["timeout", "error"])
def test_not_ready_in_time_fails_and_sends_nothing(turns, conn, ready):
    turns.script(ready=ready)
    launch(conn)
    outcome = send(conn, ready_wait_s=5)
    assert (outcome.outcome, outcome.state, outcome.reason) == ("no-ready", "failed",
                                                                 "no-ready")
    assert outcome.exit_code == exits.ERROR
    assert (row(conn).state, row(conn).reason) == ("failed", "no-ready")
    assert turns.calls("deliver") == []


@pytest.mark.parametrize("state", ["spawning", "started", "ready"])
def test_no_ready_ends_any_state_before_the_send(turns, conn, state):
    turns.script(ready="timeout")
    launch(conn, state=state)
    assert send(conn).outcome == "no-ready"
    assert row(conn).state == "failed"


def test_codex_is_found_by_the_host_s_pid_and_its_thread_id_learned(turns, conn):
    turns.script(session_id=THREAD_ID)
    launch(conn, harness="codex", pid=777)
    outcome = send(conn)
    assert outcome.outcome == "sent" and outcome.session_id == THREAD_ID
    [ready] = turns.calls("wait_ready")
    assert (ready["pid"], ready["session_id"]) == (777, None)
    assert row(conn).session_id == THREAD_ID
    assert store.session_ids(conn, "r-1") == [(THREAD_ID, "sender")]
    assert store.resolve(conn, THREAD_ID).request_id == "r-1"


def test_codex_takes_the_pid_it_is_given(turns, conn):
    turns.script(session_id=THREAD_ID)
    launch(conn, harness="codex", pid=777)
    send(conn, pid=888)
    assert turns.calls("wait_ready")[0]["pid"] == 888


def test_codex_with_no_pid_is_not_ready(turns, conn):
    launch(conn, harness="codex")
    outcome = send(conn)
    assert (outcome.outcome, row(conn).state) == ("no-ready", "failed")
    assert turns.calls() == []


def test_the_thread_id_is_learned_even_when_the_send_is_not_this_sender_s(turns, conn):
    turns.script(session_id=THREAD_ID)
    launch(conn, harness="codex", pid=777)

    class Racing:
        def __getattr__(self, name):
            return getattr(fake, name)

        def wait_ready(self, **where):
            session = fake.wait_ready(**where)
            store.set_state(conn, "r-1", "failed", reason="exited")
            return session

    assert send(conn, turns=Racing()).outcome == "not-claimed"
    assert row(conn).session_id == THREAD_ID
    assert turns.calls("deliver") == []


# -- at most once -----------------------------------------------------------------------


PLANS = {
    "delivered": {},
    "refused": {"started": {"kind": "refused", "reason": "dropped"}},
    "lost": {"started": {"kind": "lost", "reason": "expired: 300 s"}},
    "timeout": {"started": "timeout"},
    "closed-reconnect": {"deliver": "unanswered", "connection": "closed"},
    "closed-refused": {"deliver": "unanswered", "connection": "closed",
                       "reconnect": "refused"},
    "deliver-raises": {"deliver": "raise"},
}


@pytest.mark.parametrize("plan", list(PLANS))
def test_a_second_sender_never_sends_again(turns, conn, plan):
    turns.script(**PLANS[plan])
    launch(conn)
    done = send(conn)
    after = (row(conn).state, row(conn).reason)
    again = send(conn)
    assert again.outcome == "not-claimed" and again.exit_code == exits.OK
    assert (row(conn).state, row(conn).reason) == after
    assert len(turns.calls("deliver")) == 1
    assert done.outcome in ("sent", "refused", "unconfirmed")


def test_a_dead_sender_s_claim_is_never_retried(turns, conn):
    launch(conn, state="sending")
    assert send(conn).outcome == "not-claimed"
    assert row(conn).state == "sending"
    assert turns.calls() == []


def test_a_row_that_failed_while_the_sender_waited_sends_nothing(turns, conn):
    launch(conn)

    class Racing:
        def __getattr__(self, name):
            return getattr(fake, name)

        def wait_ready(self, **where):
            session = fake.wait_ready(**where)
            store.set_state(conn, "r-1", "failed", reason="exited")
            return session

    outcome = send(conn, turns=Racing())
    assert outcome.outcome == "not-claimed"
    assert (row(conn).state, row(conn).reason) == ("failed", "exited")
    assert turns.calls("deliver") == []


def test_two_senders_at_once_send_once(turns, conn):
    launch(conn)
    results = []

    def one():
        mine = store.connect()
        try:
            results.append(send(mine).outcome)
        finally:
            mine.close()

    threads = [threading.Thread(target=one) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["not-claimed"] * 3 + ["sent"]
    assert len(turns.calls("deliver")) == 1
    assert row(conn).state == "first-sent"


# -- nothing to send --------------------------------------------------------------------


def test_a_launch_with_no_first_turn_owes_nothing(turns, conn):
    launch(conn, brief=None)
    outcome = send(conn)
    assert outcome.outcome == "not-owed" and outcome.state is None
    assert row(conn).state == "started"
    assert turns.calls() == []


def test_a_first_turn_on_the_argv_is_not_sent_again(turns, conn):
    prompt = "Brief from launcher, launched by ada\n\n" + BRIEF
    launch(conn, argv=["claude", prompt, "--session-id", CLAUDE_ID])
    assert send(conn).outcome == "not-owed"
    assert row(conn).state == "started"
    assert turns.calls() == []


def test_a_handoff_gone_by_send_time_fails_and_sends_nothing(turns, conn, tmp_path):
    launch(conn, brief=None, handoff=str(tmp_path / "gone.md"))
    outcome = send(conn)
    assert (outcome.outcome, row(conn).state, row(conn).reason) == (
        "failed", "failed", "handoff-unreadable")
    assert turns.calls() == []


def test_a_handoff_is_sent_with_its_header(turns, conn, tmp_path):
    path = tmp_path / "2026-10-30-ada.md"
    path.write_text("Done: the store.\n")
    launch(conn, handoff=str(path))
    assert send(conn).outcome == "sent"
    [given] = turns.calls("deliver")
    assert given["text"] == (f"Handoff from launcher, launched by ada: {path}\n\n"
                             f"Done: the store.\n\n{BRIEF}")


def test_without_turns_the_row_fails_and_nothing_is_sent(conn, monkeypatch):
    monkeypatch.setattr(first, "turns_api", lambda: None)
    launch(conn)
    outcome = send(conn)
    assert (outcome.outcome, row(conn).state, row(conn).reason) == (
        "failed", "failed", "no-turns")


def test_an_unknown_launch(turns, conn):
    outcome = send(conn, "r-none")
    assert outcome.outcome == "unknown-launch" and outcome.exit_code == exits.REFUSED


def test_the_waits_come_from_settings(turns, conn):
    launch(conn)
    first.send(conn, "r-1")
    assert turns.calls("wait_ready")[0]["timeout"] == 60
    assert turns.calls("wait_started")[0]["timeout"] == 120


# -- the deliver verb and the detached deliverer -----------------------------------------


@pytest.mark.parametrize("plan, code, word", [
    ({}, exits.OK, "delivered"),
    ({"started": {"kind": "lost", "reason": "expired"}}, exits.OK, "unconfirmed"),
    ({"started": {"kind": "refused", "reason": "dropped"}}, exits.REFUSED, "refused"),
    ({"ready": "timeout"}, exits.ERROR, "not ready"),
])
def test_deliver_prints_one_line_and_exits_by_the_outcome(turns, conn, plan, code, word):
    turns.script(**plan)
    launch(conn)
    out = io.StringIO()
    assert first.deliver("r-1", pid=os.getpid(), out=out) == code
    [line] = out.getvalue().splitlines()
    assert word in line


def test_deliver_with_no_launch_log_is_an_unknown_launch(turns):
    out = io.StringIO()
    assert first.deliver("r-1", out=out) == exits.REFUSED
    assert out.getvalue() == "no launch r-1 in the launch log\n"
    assert not store.path().exists()


def test_deliver_with_the_log_busy_is_tempfail(turns, conn, monkeypatch):
    launch(conn)

    def busy(*a, **k):
        raise store.Busy("locked")
    monkeypatch.setattr(first, "send", busy)
    assert first.deliver("r-1", out=io.StringIO()) == exits.TEMPFAIL


def test_the_deliverer_s_command(monkeypatch):
    assert first.deliverer_argv("r-1", 1234)[1:] == ["launcher", "deliver", "r-1",
                                                     "--pid", "1234"]
    assert Path(first.deliverer_argv("r-1", 1)[0]).name.startswith("moxi")


def test_the_run_log_is_under_the_runs_store():
    path = first.run_log("r-1")
    assert path.name == "r-1.log" and path.parent.name == "runs"
    assert path.parent.parent.name == "launcher"


def test_the_detached_deliverer_sends_writes_the_row_and_logs(turns, conn, monkeypatch):
    """A real detached process, reaching the fake turns through the seam named in its
    environment, as `moxi launcher deliver` does once the verb is wired."""
    launch(conn)
    code = ("import sys; from moxi_launcher import first; "
            "sys.exit(first.deliver(sys.argv[1], pid=int(sys.argv[3])))")
    monkeypatch.setattr(first, "deliverer_argv", lambda rid, pid: [
        sys.executable, "-c", code, rid, "--pid", str(pid)])
    env = dict(os.environ, PYTHONPATH=str(TESTS))
    child = first.start_deliverer("r-1", env=env)
    assert isinstance(child, int) and child != os.getpid()
    wait_for(lambda: row(conn).state == "first-sent", timeout=30)
    [given] = turns.calls("deliver")
    assert given["pid"] == child
    assert given["ref"] == "launcher:r-1"
    log_path = first.run_log("r-1")
    wait_for(lambda: log_path.exists() and "delivered" in log_path.read_text(), timeout=30)
    assert oct(log_path.stat().st_mode & 0o777) == oct(0o600)


def test_the_detached_deliverer_takes_the_launcher_s_own_pid(monkeypatch):
    seen = {}
    monkeypatch.setattr(first.proc, "spawn",
                        lambda argv, **kw: seen.update(argv=argv, **kw) or 99)
    assert first.start_deliverer("r-1") == 99
    assert seen["argv"][-2:] == ["--pid", str(os.getpid())]
    assert seen["detached"] is True and seen["output"] == first.run_log("r-1")

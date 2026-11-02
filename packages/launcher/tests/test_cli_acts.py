"""The CLI's acts on launched sessions: relaunch, exit-session and exits, sessions and
resume, keep-warm holds, state, and `log --table`. Each verb parses, calls the library
function it stands for with what was typed, prints its lines or its JSON, and exits with
the code its outcome or refusal carries."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from moxi import exit as codes
from moxi import modules

import launcher_fake_turns as fake_turns
from moxi_launcher import (cli, exits, keepwarm, relaunch, resume, slots, state, store, target,
                           targets)

SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
OTHER = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


@pytest.fixture(autouse=True)
def no_turns(monkeypatch):
    """The installed turns hidden: a verb that needs turns to find its session meets
    `no-turns`, and a test that wants a session fakes the library call instead."""
    fake_turns.hide(monkeypatch)


@pytest.fixture(autouse=True)
def no_slots(monkeypatch):
    """slots not installed, whatever this environment holds, so no verb asks a real tmux
    server."""
    monkeypatch.delenv(slots.SEAM, raising=False)
    real = modules.available
    monkeypatch.setattr(modules, "available",
                        lambda name: name not in ("slots", "cache") and real(name))


def run(argv, capsys) -> tuple[int, str, str]:
    code = cli.main(argv)
    seen = capsys.readouterr()
    return code, seen.out, seen.err


def result(code: int = codes.OK, *lines: str, **data) -> relaunch.Result:
    return relaunch.Result(code, lines or ("done",), data=data)


def calls(monkeypatch, module, name: str, answer) -> list:
    """Stand `answer` in for `module.name`, and record each call's arguments."""
    seen = []

    def stand_in(*args, **kwargs):
        seen.append((args, kwargs))
        if isinstance(answer, BaseException):
            raise answer
        return answer
    monkeypatch.setattr(module, name, stand_in)
    return seen


def launched(conn, *, request_id="r1", session_id=SID, target_="slot", values=None,
             at=None) -> None:
    store.insert(conn, request_id=request_id, actor="ann", harness="claude", target=target_,
                 cwd="/work", values={"model": "opus", **(values or {})}, argv=["claude"],
                 env={}, posture="operator", session_id=session_id,
                 slot="3" if target_ == "slot" else None, at=at)


def ended_run(conn, outcome, *, kind="relaunch", mode="self", request_id="r1", word=None,
              detail=None, at=None) -> store.Run:
    run_ = store.insert_run(conn, kind=kind, mode=mode, request_id=request_id,
                            session_id=SID, actor="ann", state="acting", job="proc:1@1.0",
                            slot="3", at=at)
    store.end_run(conn, run_.run_id, outcome, word=word, detail=detail, at=at)
    return store.get_run(conn, run_.run_id)


@pytest.fixture
def conn():
    connection = store.connect()
    yield connection
    connection.close()


def too_new() -> None:
    store.path().parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(store.path())
    raw.execute("PRAGMA user_version = 99")
    raw.close()


# -- parsing --------------------------------------------------------------------------------

PARSES = [
    (["relaunch", "--self", "--handoff", "h.md"],
     {"verb": "relaunch", "self": True, "handoff": "h.md", "target": None, "status": None,
      "from_record": None, "dry_run": False, "json": False}),
    (["relaunch", "--self", "--handoff", "h.md", "--dry-run", "--json"],
     {"self": True, "dry_run": True, "json": True}),
    (["relaunch", SID], {"target": SID, "self": False, "handoff": None}),
    (["relaunch", "boss@work", "--dry-run"], {"target": "boss@work", "dry_run": True}),
    (["relaunch", "--status", "ab12", "--json"], {"status": "ab12", "json": True}),
    (["relaunch", "--from-record", "ab12"], {"from_record": "ab12", "target": None}),
    (["exit-session", SID], {"verb": "exit-session", "session_id": SID, "dry_run": False,
                             "json": False}),
    (["exit-session", SID, "--dry-run", "--json"], {"dry_run": True, "json": True}),
    (["exits"], {"verb": "exits", "limit": None, "before": None, "json": False}),
    (["exits", "--limit", "5", "--before", "2026-10-30T00:00:00Z", "--json"],
     {"limit": 5, "before": "2026-10-30T00:00:00Z", "json": True}),
    (["sessions"], {"verb": "sessions", "limit": None, "before": None, "json": False}),
    (["sessions", "--limit", "2", "--json"], {"limit": 2, "json": True}),
    (["resume", SID], {"verb": "resume", "session_id": SID, "slot": None}),
    (["resume", SID, "--slot", "4"], {"slot": "4"}),
    (["keep-warm", "hold", "build-7"], {"verb": "keep-warm", "action": "hold",
                                        "name": "build-7", "json": False}),
    (["keep-warm", "release", "build-7"], {"action": "release", "name": "build-7"}),
    (["keep-warm", "holds"], {"action": "holds", "json": False}),
    (["keep-warm", "holds", "--json"], {"action": "holds", "json": True}),
    (["state"], {"verb": "state", "session_id": None, "json": False}),
    (["state", SID, "--json"], {"session_id": SID, "json": True}),
    (["log", "--table", "runs"], {"verb": "log", "table": "runs"}),
    (["log"], {"table": "launches"}),
]


@pytest.mark.parametrize(("argv", "expected"), PARSES, ids=[" ".join(a) for a, _ in PARSES])
def test_each_act_parses(argv, expected):
    args = vars(cli.parse(argv))
    assert {key: args[key] for key in expected} == expected


REFUSED = [
    ["relaunch"],
    ["relaunch", "--self"],
    ["relaunch", "--handoff", "h.md"],
    ["relaunch", SID, "--handoff", "h.md"],
    ["relaunch", SID, "--self", "--handoff", "h.md"],
    ["relaunch", SID, "--status", "ab12"],
    ["relaunch", "--status", "ab12", "--from-record", "ab12"],
    ["relaunch", "--self", "--handoff", "h.md", "--status", "ab12"],
    ["relaunch", "--status", "ab12", "--dry-run"],
    ["relaunch", "--from-record", "ab12", "--dry-run"],
    ["relaunch", "--status"],
    ["relaunch", SID, OTHER],
    # Nothing is abbreviated.
    ["relaunch", "--stat", "ab12"],
    ["relaunch", "--self", "--hand", "h.md"],
    ["exit-session"],
    ["exit-session", SID, OTHER],
    ["exit-session", SID, "--dry"],
    ["exits", "--limit", "many"],
    ["exits", SID],
    ["sessions", "--bogus"],
    ["resume"],
    ["resume", SID, "--slot"],
    ["keep-warm"],
    ["keep-warm", "hold"],
    ["keep-warm", "open", "x"],
    ["keep-warm", "hold", "x", "--json"],
    ["keep-warm", "holds", "x"],
    ["state", SID, OTHER],
    ["log", "--table", "nope"],
]


@pytest.mark.parametrize("argv", REFUSED, ids=[" ".join(a) for a in REFUSED])
def test_bad_arguments_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as raised:
        cli.parse(argv)
    assert raised.value.code == codes.USAGE


def test_log_tables_are_the_stores():
    assert cli.TABLES == store.TABLES


# -- how an act's refusal prints and exits ----------------------------------------------------

REFUSALS = [
    (relaunch.Refused("not-slot", "not in a slot", "launch it in a slot"), codes.REFUSED,
     "not-slot"),
    (relaunch.Refused("comms-offline", "comms is down", "try again", codes.TEMPFAIL),
     codes.TEMPFAIL, "comms-offline"),
    (relaunch.Refused("comms-failed", "comms failed", "check doctor", codes.ERROR),
     codes.ERROR, "comms-failed"),
    (target.NotLaunched(), codes.REFUSED, "not-launched"),
    (target.Unclaimed("r1"), codes.REFUSED, "unclaimed"),
    (target.Changed("r1", "1@1.0", "2@2.0", False), codes.REFUSED, "changed"),
    (target.NoTurns(), codes.REFUSED, "no-turns"),
    (target.TurnsFailed(OSError("no socket")), codes.ERROR, "turns-failed"),
    (targets.SlotsBusy("slot", "slots is locked", "try again"), codes.TEMPFAIL, "try-again"),
    (targets.SlotsFailed("slot", "slots failed", "moxi slots list"), codes.ERROR,
     "slots-failed"),
    (store.Busy("the launch log is locked"), codes.TEMPFAIL, "busy"),
]


@pytest.mark.parametrize("error, code, short", REFUSALS,
                         ids=[short for _, _, short in REFUSALS])
def test_a_relaunch_refusal_prints_one_line_its_code_and_a_hint(error, code, short,
                                                                monkeypatch, capsys):
    calls(monkeypatch, relaunch, "call_director", error)
    status, out, err = run(["relaunch", SID], capsys)
    assert status == code and out == ""
    line, hint = err.splitlines()
    assert line.startswith("moxi launcher relaunch: ") and line.endswith(f"[{short}]")
    assert hint.startswith("hint: ") and len(hint) > len("hint: ")


def open_run() -> store.Run:
    return store.Run("ab12cd34ef56ab78", "keep-warm", "ask", "r1", SID, "launcher", None,
                     None, None, None, None, None, None, "proc:1@1.0", "acting", None, None,
                     None, 0, "2026-10-31T10:00:00.000000Z", "2026-10-31T10:00:00.000000Z")


@pytest.mark.parametrize("argv, module, name", [
    (["relaunch", "--self", "--handoff", "h.md"], relaunch, "arm_self"),
    (["relaunch", SID], relaunch, "call_director"),
    (["relaunch", "--from-record", "ab12"], relaunch, "from_record"),
    (["exit-session", SID], exits, "exit_session"),
])
def test_an_open_run_is_refused_run_open_naming_it(argv, module, name, monkeypatch,
                                                   capsys):
    calls(monkeypatch, module, name, store.RunOpen(open_run()))
    status, out, err = run(argv, capsys)
    assert status == codes.REFUSED and out == ""
    line, hint = err.splitlines()
    assert line.endswith("[run-open]") and "ab12cd34ef56ab78" in line
    assert hint == ("hint: that run must end first; moxi launcher relaunch --status "
                    "ab12cd34ef56ab78 shows it")


def test_an_error_that_is_not_a_refusal_is_not_hidden(monkeypatch):
    calls(monkeypatch, relaunch, "status", RuntimeError("a bug"))
    with pytest.raises(RuntimeError):
        cli.main(["relaunch", "--status", "ab12"])


# -- relaunch -------------------------------------------------------------------------------


def test_relaunch_self_arms_with_the_handoff_and_prints_the_armed_text(monkeypatch, capsys):
    seen = calls(monkeypatch, relaunch, "arm_self",
                 result(codes.OK, "armed ab12: this session exits", "End your turn now.",
                        outcome="armed", run_id="ab12"))
    status, out, err = run(["relaunch", "--self", "--handoff", "h.md"], capsys)
    assert (status, err) == (codes.OK, "")
    assert out == "armed ab12: this session exits\nEnd your turn now.\n"
    assert seen == [(("h.md",), {"dry_run": False})]


def test_relaunch_self_dry_run_and_json(monkeypatch, capsys):
    seen = calls(monkeypatch, relaunch, "arm_self",
                 result(codes.OK, "would arm", outcome="dry-run", session_id=SID))
    status, out, _ = run(["relaunch", "--self", "--handoff", "h.md", "--dry-run", "--json"],
                         capsys)
    assert status == codes.OK
    assert json.loads(out) == {"outcome": "dry-run", "session_id": SID}
    assert seen == [(("h.md",), {"dry_run": True})]


def test_relaunch_self_with_no_launch_log_is_refused_and_makes_nothing(tmp_path, capsys):
    handoff = tmp_path / "h.md"
    handoff.write_text("where the work stands")
    status, out, err = run(["relaunch", "--self", "--handoff", str(handoff)], capsys)
    assert status == codes.REFUSED and out == ""
    assert err.splitlines()[0].endswith("[not-launched]")
    assert not store.path().exists()


def test_relaunch_self_without_turns_is_refused_and_writes_nothing(conn, tmp_path, capsys):
    """Through the real arm: no session can be found above this process without turns, so
    it is refused before the handoff is read, and no run is written."""
    launched(conn)
    handoff = tmp_path / "h.md"
    handoff.write_text("where the work stands")
    status, out, err = run(["relaunch", "--self", "--handoff", str(handoff)], capsys)
    assert status == codes.REFUSED and out == ""
    assert err.splitlines()[0].endswith("[no-turns]")
    assert store.runs(conn) == []


@pytest.mark.parametrize("code, outcome", [
    (codes.OK, "armed"), (codes.ERROR, "not-started")])
def test_relaunch_self_exits_with_the_arms_code(code, outcome, monkeypatch, capsys):
    calls(monkeypatch, relaunch, "arm_self", result(code, f"run ab12: {outcome}",
                                                     outcome=outcome))
    status, out, err = run(["relaunch", "--self", "--handoff", "h.md"], capsys)
    assert status == code
    assert (out if code == codes.OK else err) == f"run ab12: {outcome}\n"


def test_relaunch_director_calls_with_the_target(monkeypatch, capsys):
    seen = calls(monkeypatch, relaunch, "call_director",
                 result(codes.OK, "run ab12: relaunched", outcome="relaunched"))
    status, out, _ = run(["relaunch", "boss@work"], capsys)
    assert (status, out) == (codes.OK, "run ab12: relaunched\n")
    assert seen == [(("boss@work",), {"dry_run": False})]
    run(["relaunch", SID, "--dry-run"], capsys)
    assert seen[-1] == ((SID,), {"dry_run": True})


@pytest.mark.parametrize("code, outcome", [
    (codes.OK, "relaunched"),
    (codes.TEMPFAIL, "held"),
    (codes.TEMPFAIL, "waiting"),          # still running past the follow bound
    (codes.ERROR, "not-exited"),
    (codes.ERROR, "relaunch-failed"),
    (codes.ERROR, "not-started"),
])
def test_relaunch_director_exits_with_its_outcomes_code(code, outcome, monkeypatch, capsys):
    calls(monkeypatch, relaunch, "call_director",
          result(code, f"run ab12: {outcome}", outcome=outcome))
    status, out, err = run(["relaunch", SID], capsys)
    assert status == code
    assert (out, err) == (("run ab12: " + outcome + "\n", "") if code == codes.OK
                          else ("", "run ab12: " + outcome + "\n"))
    status, out, _ = run(["relaunch", SID, "--json"], capsys)
    assert status == code and json.loads(out)["outcome"] == outcome


def test_relaunch_director_without_comms_is_refused(monkeypatch, capsys):
    """Through the real call: comms not installed."""
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "comms" and real(name))
    status, out, err = run(["relaunch", SID], capsys)
    assert status == codes.REFUSED and out == ""
    assert err.splitlines()[0].endswith("[no-comms]")


def test_relaunch_status_shows_the_run_and_its_latest_wait(conn, capsys):
    launched(conn)
    run_ = store.insert_run(conn, kind="relaunch", mode="self", request_id="r1",
                            session_id=SID, actor="ann", job="slots:relaunch-x", slot="3")
    store.add_run_event(conn, run_.run_id, "wait", {"word": "turn", "detail": "a turn is open"},
                        at="2026-10-31T10:00:00.000000Z")
    store.add_run_event(conn, run_.run_id, "wait",
                        {"word": "person", "detail": "a client was active 40 s ago"},
                        at="2026-10-31T10:01:00.000000Z")
    status, out, err = run(["relaunch", "--status", run_.run_id], capsys)
    assert (status, err) == (codes.OK, "")
    lines = out.splitlines()
    assert lines[0] == (f"run {run_.run_id}: relaunch (self) of session {SID} in slot 3: "
                        "armed")
    assert lines[1].startswith("waiting on: person: a client was active 40 s ago")
    assert len(lines) == 4
    status, out, _ = run(["relaunch", "--status", run_.run_id, "--json"], capsys)
    data = json.loads(out)
    assert status == codes.OK and data["outcome"] == "armed"
    assert data["wait"]["word"] == "person" and len(data["events"]) == 2


def test_relaunch_status_of_an_ended_run_carries_its_outcome(conn, capsys):
    launched(conn)
    run_ = ended_run(conn, "not-exited", word="mismatch", detail="the dialog changed")
    status, out, _ = run(["relaunch", "--status", run_.run_id, "--json"], capsys)
    assert status == codes.OK and json.loads(out)["outcome"] == "not-exited"


def test_relaunch_status_of_no_run_is_refused(conn, capsys):
    status, out, err = run(["relaunch", "--status", "nope"], capsys)
    assert status == codes.REFUSED and out == ""
    assert err.splitlines()[0].endswith("[no-run]")


def test_relaunch_status_with_no_launch_log_is_refused_and_makes_nothing(capsys):
    status, _, err = run(["relaunch", "--status", "nope"], capsys)
    assert status == codes.REFUSED and "[not-launched]" in err
    assert not store.path().exists()


def test_relaunch_status_of_a_newer_launch_log_is_refused(capsys):
    too_new()
    status, _, err = run(["relaunch", "--status", "nope"], capsys)
    assert status == codes.REFUSED and "[store-too-new]" in err


@pytest.mark.parametrize("code, outcome", [
    (codes.OK, "relaunched"), (codes.ERROR, "relaunch-failed"), (codes.TEMPFAIL, "acting"),
    (codes.ERROR, "not-started")])
def test_relaunch_from_record_follows_its_repair(code, outcome, monkeypatch, capsys):
    seen = calls(monkeypatch, relaunch, "from_record",
                 result(code, f"run cd34: {outcome}", outcome=outcome))
    status, out, err = run(["relaunch", "--from-record", "ab12"], capsys)
    assert status == code and seen == [(("ab12",), {})]
    assert (out if code == codes.OK else err) == f"run cd34: {outcome}\n"


def test_relaunch_from_record_of_a_run_that_cant_be_repaired_is_refused(conn, capsys):
    """Through the real repair: a run that relaunched its session is not repaired."""
    launched(conn)
    run_ = ended_run(conn, "relaunched")
    status, out, err = run(["relaunch", "--from-record", run_.run_id], capsys)
    assert status == codes.REFUSED and out == ""
    assert err.splitlines()[0].endswith("[not-repairable]")


def test_relaunch_from_record_of_an_exit_run_is_refused(conn, capsys):
    launched(conn)
    run_ = ended_run(conn, "exited", kind="exit", mode="verb")
    status, _, err = run(["relaunch", "--from-record", run_.run_id], capsys)
    assert status == codes.REFUSED and err.splitlines()[0].endswith("[not-relaunch]")


def test_relaunch_from_record_without_slots_is_refused(conn, capsys):
    launched(conn)
    run_ = ended_run(conn, "relaunch-failed", word="no-pane")
    status, _, err = run(["relaunch", "--from-record", run_.run_id], capsys)
    assert status == codes.REFUSED and err.splitlines()[0].endswith("[no-slots]")


# -- exit-session and exits -----------------------------------------------------------------


@pytest.mark.parametrize("code, verdict", [
    (codes.OK, "exited"), (codes.ERROR, "at-dialog"), (codes.ERROR, "not-exited"),
    (codes.TEMPFAIL, "held")])
def test_exit_session_exits_with_its_verdicts_code(code, verdict, monkeypatch, capsys):
    seen = calls(monkeypatch, exits, "exit_session",
                 result(code, f"session {SID}: {verdict}", outcome=verdict, run_id="ab12"))
    status, out, err = run(["exit-session", SID], capsys)
    assert status == code and seen == [((SID,), {"dry_run": False})]
    assert (out if code == codes.OK else err) == f"session {SID}: {verdict}\n"
    status, out, _ = run(["exit-session", SID, "--dry-run", "--json"], capsys)
    assert status == code and json.loads(out)["outcome"] == verdict
    assert seen[-1] == ((SID,), {"dry_run": True})


def test_exit_session_refused_exits_3(monkeypatch, capsys):
    calls(monkeypatch, exits, "exit_session",
          exits.Refused("no-exit", "this session offers no exit", "exit it by hand"))
    status, out, err = run(["exit-session", SID], capsys)
    assert status == codes.REFUSED and out == ""
    assert err.splitlines()[0].endswith("[no-exit]")


def test_exit_session_of_a_session_no_launch_holds_is_refused(conn, capsys):
    """Through the real verb: refused `not-launched`, logged nowhere."""
    launched(conn)
    status, _, err = run(["exit-session", OTHER], capsys)
    assert status == codes.REFUSED and "[not-launched]" in err
    assert store.exits_log(conn) == []


def test_exit_session_of_an_unclaimed_launch_is_refused_and_logged(conn, capsys):
    """Through the real verb: the launch is found, so its refusal is logged `refused`."""
    launched(conn)
    status, _, err = run(["exit-session", SID], capsys)
    assert status == codes.REFUSED and "[unclaimed]" in err
    [row] = store.exits_log(conn)
    assert (row.verdict, row.session_id, row.run_id) == ("refused", SID, None)


def exit_rows(conn) -> None:
    launched(conn)
    store.add_exit(conn, request_id="r1", session_id=SID, verdict="held", word="person",
                   detail="a client was active", slot="3", at="2026-10-30T10:00:00.000000Z")
    store.add_exit(conn, request_id="r1", session_id=SID, verdict="exited", slot="3",
                   at="2026-10-31T10:00:00.000000Z")


def test_exits_lists_newest_first(conn, capsys):
    exit_rows(conn)
    status, out, err = run(["exits"], capsys)
    assert (status, err) == (codes.OK, "")
    newest, oldest = out.splitlines()
    assert newest.startswith("2026-10-31T10:00:00.000000Z exited session ")
    assert oldest.startswith("2026-10-30T10:00:00.000000Z held (person)")
    assert oldest.endswith(": a client was active")


def test_exits_json_is_one_row_per_line_and_pages(conn, capsys):
    exit_rows(conn)
    status, out, _ = run(["exits", "--json"], capsys)
    rows = [json.loads(line) for line in out.splitlines()]
    assert status == codes.OK and [r["verdict"] for r in rows] == ["exited", "held"]
    status, out, _ = run(["exits", "--json", "--limit", "1"], capsys)
    assert [json.loads(line)["verdict"] for line in out.splitlines()] == ["exited"]
    status, out, _ = run(["exits", "--before", "2026-10-31T00:00:00Z"], capsys)
    assert len(out.splitlines()) == 1 and " held " in out


def test_exits_with_none_says_so_and_makes_nothing(capsys):
    assert run(["exits"], capsys) == (codes.OK, "", "no exits\n")
    assert run(["exits", "--json"], capsys) == (codes.OK, "", "")
    assert not store.path().exists()


@pytest.mark.parametrize("verb", ["exits", "sessions"])
@pytest.mark.parametrize("bound", [["--before", "yesterday"], ["--limit", "0"]])
def test_a_list_with_a_bad_bound_exits_2(verb, bound, capsys):
    status, out, err = run([verb, *bound], capsys)
    assert status == codes.USAGE and out == "" and err.startswith(f"moxi launcher {verb}: ")


@pytest.mark.parametrize("verb", ["exits", "sessions"])
def test_a_list_of_a_newer_launch_log_is_refused(verb, capsys):
    too_new()
    status, _, err = run([verb], capsys)
    assert status == codes.REFUSED and "[store-too-new]" in err


# -- sessions and resume --------------------------------------------------------------------


def test_sessions_lists_each_launch_and_whether_it_resumes(conn, capsys):
    launched(conn, at="2026-10-30T10:00:00.000000Z")
    launched(conn, request_id="r2", session_id=OTHER, target_="headless",
             at="2026-10-31T10:00:00.000000Z")
    status, out, err = run(["sessions"], capsys)
    assert (status, err) == (codes.OK, "")
    newest, oldest = out.splitlines()
    assert "launch r2" in newest and "not resumable" in newest and "headless" in newest
    assert "claude in slot 3, launch r1" in oldest and oldest.endswith("ended: resumable")


def test_sessions_json_is_one_entry_per_line_and_pages(conn, capsys):
    launched(conn, at="2026-10-30T10:00:00.000000Z")
    launched(conn, request_id="r2", session_id=OTHER, at="2026-10-31T10:00:00.000000Z")
    status, out, _ = run(["sessions", "--json"], capsys)
    entries = [json.loads(line) for line in out.splitlines()]
    assert status == codes.OK and [e["request_id"] for e in entries] == ["r2", "r1"]
    assert entries[1]["resumable"] is True and entries[1]["live"] is False
    status, out, _ = run(["sessions", "--json", "--limit", "1"], capsys)
    assert [json.loads(line)["request_id"] for line in out.splitlines()] == ["r2"]
    status, out, _ = run(["sessions", "--before", "2026-10-31T00:00:00Z"], capsys)
    assert "launch r1" in out and "launch r2" not in out


def test_sessions_with_none_says_so_and_makes_nothing(capsys):
    assert run(["sessions"], capsys) == (codes.OK, "", "no sessions\n")
    assert run(["sessions", "--json"], capsys) == (codes.OK, "", "")
    assert not store.path().exists()


def test_resume_hands_the_slot_and_the_warn_printer_over(monkeypatch, capsys):
    seen = calls(monkeypatch, resume, "resume",
                 result(codes.OK, "launch r9 started in slot 4", outcome="resumed"))
    status, out, _ = run(["resume", SID, "--slot", "4"], capsys)
    assert (status, out) == (codes.OK, "launch r9 started in slot 4\n")
    assert seen == [((SID,), {"slot": "4", "warn": cli._warn})]
    run(["resume", SID], capsys)
    assert seen[-1][1]["slot"] is None


def test_resume_whose_launch_failed_exits_1_on_stderr(monkeypatch, capsys):
    calls(monkeypatch, resume, "resume",
          result(codes.ERROR, "launch r9 failed: no-command", outcome="failed"))
    assert run(["resume", SID], capsys) == (codes.ERROR, "", "launch r9 failed: no-command\n")


@pytest.mark.parametrize("error, code, short", [
    (resume.Refused("live", f"session {SID} is live", "moxi slots attach 3"), codes.REFUSED,
     "live"),
    (resume.Refused("liveness-unread", "can't be read", "check doctor", codes.ERROR),
     codes.ERROR, "liveness-unread"),
    (resume.Refused("slots-busy", "slots did not answer", "try again", codes.TEMPFAIL),
     codes.TEMPFAIL, "slots-busy"),
    (target.NotLaunched(), codes.REFUSED, "not-launched"),
])
def test_resume_refused_exits_with_its_code(error, code, short, monkeypatch, capsys):
    calls(monkeypatch, resume, "resume", error)
    status, out, err = run(["resume", SID], capsys)
    assert status == code and out == "" and err.splitlines()[0].endswith(f"[{short}]")


def test_resume_of_a_headless_launch_is_refused_and_writes_nothing(conn, capsys):
    """Through the real verb: nothing to resume a headless launch into."""
    launched(conn, target_="headless")
    status, _, err = run(["resume", SID], capsys)
    assert status == codes.REFUSED and err.splitlines()[0].endswith("[headless]")
    assert [r.request_id for r in store.log(conn)] == ["r1"]


def test_resume_of_a_session_no_launch_holds_is_refused(conn, capsys):
    launched(conn)
    status, _, err = run(["resume", OTHER], capsys)
    assert status == codes.REFUSED and "[not-launched]" in err


# -- keep-warm ------------------------------------------------------------------------------


@pytest.mark.parametrize("argv, name, args", [
    (["keep-warm", "hold", "build-7"], "hold", ("build-7",)),
    (["keep-warm", "release", "build-7"], "release", ("build-7",)),
    (["keep-warm", "holds"], "holds", ()),
])
def test_keep_warm_calls_its_action(argv, name, args, monkeypatch, capsys):
    seen = calls(monkeypatch, keepwarm, name,
                 result(codes.OK, "hold build-7 opened", outcome="opened"))
    status, out, err = run(argv, capsys)
    assert (status, out, err) == (codes.OK, "hold build-7 opened\n", "")
    assert seen == [(args, {})]


def test_keep_warm_holds_json(monkeypatch, capsys):
    holds = [{"request_id": "r1", "name": "build-7", "opened_at": "t", "released_at": None}]
    calls(monkeypatch, keepwarm, "holds", result(codes.OK, "build-7  opened t",
                                                 outcome="holds", holds=holds))
    status, out, _ = run(["keep-warm", "holds", "--json"], capsys)
    assert status == codes.OK
    assert json.loads(out) == {"outcome": "holds", "holds": holds}


@pytest.mark.parametrize("argv, short, code", [
    (["keep-warm", "hold", " padded"], "bad-name", codes.USAGE),
    (["keep-warm", "release", "x" * (keepwarm.NAME_CAP + 1)], "bad-name", codes.USAGE),
    (["keep-warm", "hold", "build-7"], "not-launched", codes.REFUSED),
    (["keep-warm", "holds"], "not-launched", codes.REFUSED),
])
def test_keep_warm_refusals_with_no_launch_log(argv, short, code, capsys):
    status, out, err = run(argv, capsys)
    assert status == code and out == "" and err.splitlines()[0].endswith(f"[{short}]")
    assert not store.path().exists()


@pytest.mark.parametrize("argv", [["keep-warm", "hold", "build-7"],
                                  ["keep-warm", "release", "build-7"], ["keep-warm", "holds"]])
def test_keep_warm_without_turns_is_refused_and_writes_nothing(argv, conn, capsys):
    """Through the real verbs: this process's session can't be found without turns."""
    launched(conn, values={"keep_warm": "on"})
    status, out, err = run(argv, capsys)
    assert status == codes.REFUSED and out == ""
    assert err.splitlines()[0].endswith("[no-turns]")
    assert store.holds_log(conn) == []


def test_keep_warm_on_a_launch_that_is_off_is_refused(monkeypatch, capsys):
    calls(monkeypatch, keepwarm, "hold",
          relaunch.Refused("off", "launch r1 does not keep its session warm", "launch with "
                                  "keep_warm on to use holds"))
    status, _, err = run(["keep-warm", "hold", "build-7"], capsys)
    assert status == codes.REFUSED and err.splitlines()[0].endswith("[off]")


# -- state ----------------------------------------------------------------------------------


def test_state_of_a_quiet_launch_is_ok(conn, capsys):
    launched(conn)
    status, out, err = run(["state", SID], capsys)
    assert (status, err) == (codes.OK, "")
    lines = out.splitlines()
    assert lines[0] == f"session {SID} (launch r1): ok"
    assert lines[1] == "relaunch: none" and lines[3] == "keep warm: off"


def test_state_of_a_failed_run_is_red_and_still_exits_0(conn, capsys):
    launched(conn)
    ended_run(conn, "relaunch-failed", mode="director", word="no-pane", detail="gone")
    status, out, _ = run(["state", SID], capsys)
    assert status == codes.OK
    assert out.splitlines()[0].startswith(f"session {SID} (launch r1): red, failed since ")
    status, out, _ = run(["state", SID, "--json"], capsys)
    data = json.loads(out)
    assert status == codes.OK and data["outcome"] == "failed" and data["red"] is True
    assert data["why"] == "no-pane: gone"


def test_state_json_of_a_quiet_launch_says_ok(conn, capsys):
    launched(conn)
    status, out, _ = run(["state", SID, "--json"], capsys)
    data = json.loads(out)
    assert status == codes.OK and data["outcome"] == "ok" and data["red"] is False


def test_state_with_no_session_named_asks_for_this_ones(monkeypatch, capsys):
    seen = calls(monkeypatch, state, "show", result(codes.OK, "session x: ok", outcome="ok"))
    assert run(["state"], capsys)[:2] == (codes.OK, "session x: ok\n")
    assert seen == [((None,), {})]


@pytest.mark.parametrize("argv, short", [
    (["state", OTHER], "not-launched"),
    (["state"], "no-turns"),
])
def test_state_refusals(argv, short, conn, capsys):
    launched(conn)
    status, out, err = run(argv, capsys)
    assert status == codes.REFUSED and out == ""
    assert err.splitlines()[0].endswith(f"[{short}]")


# -- log --table ----------------------------------------------------------------------------


def tables(conn) -> store.Run:
    exit_rows(conn)
    launched(conn, request_id="r2", session_id=OTHER, values={"keep_warm": "on"})
    store.open_hold(conn, "r2", "build-7", at="2026-10-31T09:00:00.000000Z")
    store.open_hold(conn, "r2", "build-6", at="2026-10-30T09:00:00.000000Z")
    store.release_hold(conn, "r2", "build-6", at="2026-10-30T12:00:00.000000Z")
    run_ = ended_run(conn, "exited", kind="exit", mode="verb",
                     at="2026-10-31T10:00:00.000000Z")
    store.add_run_event(conn, run_.run_id, "exit", {"result": "ran"},
                        at="2026-10-31T10:00:01.000000Z")
    return run_


def test_log_table_runs_lists_each_run(conn, capsys):
    run_ = tables(conn)
    status, out, err = run(["log", "--table", "runs"], capsys)
    assert (status, err) == (codes.OK, "")
    assert out == (f"2026-10-31T10:00:00Z  run {run_.run_id}: exit (verb) of session {SID} "
                   "in slot 3: exited\n")


def test_log_table_runs_json_carries_each_runs_events(conn, capsys):
    run_ = tables(conn)
    status, out, _ = run(["log", "--table", "runs", "--json"], capsys)
    [line] = out.splitlines()
    data = json.loads(line)
    assert status == codes.OK and data["run_id"] == run_.run_id
    assert data["outcome"] == "exited"
    assert data["events"] == [{"at": "2026-10-31T10:00:01.000000Z", "kind": "exit",
                               "detail": {"result": "ran"}}]


def test_log_table_exits_and_holds(conn, capsys):
    tables(conn)
    status, out, _ = run(["log", "--table", "exits", "--limit", "1"], capsys)
    assert status == codes.OK and out.startswith("2026-10-31T10:00:00.000000Z exited")
    status, out, _ = run(["log", "--table", "holds"], capsys)
    assert out.splitlines() == [
        "2026-10-31T09:00:00Z  build-7  launch r2  open",
        "2026-10-30T09:00:00Z  build-6  launch r2  released 2026-10-30T12:00:00Z"]
    status, out, _ = run(["log", "--table", "holds", "--json",
                          "--before", "2026-10-31T00:00:00Z"], capsys)
    assert [json.loads(line) for line in out.splitlines()] == [
        {"request_id": "r2", "name": "build-6", "opened_at": "2026-10-30T09:00:00.000000Z",
         "released_at": "2026-10-30T12:00:00.000000Z"}]


@pytest.mark.parametrize("table", store.TABLES)
def test_log_table_with_no_launch_log_lists_nothing_and_makes_nothing(table, capsys):
    assert run(["log", "--table", table], capsys) == (codes.OK, "", f"no {table}\n")
    assert run(["log", "--table", table, "--json"], capsys) == (codes.OK, "", "")
    assert not store.path().exists()


@pytest.mark.parametrize("argv", [["log", "--table", "runs"],
                                  ["log", "--table", "holds", "--json"]])
def test_log_table_of_a_newer_launch_log_is_refused(argv, capsys):
    too_new()
    status, _, err = run(argv, capsys)
    assert status == codes.REFUSED and "[store-too-new]" in err


def test_log_table_with_a_bad_bound_exits_2(capsys):
    assert run(["log", "--table", "runs", "--limit", "0"], capsys)[0] == codes.USAGE


def test_the_store_reads_only_its_tables(conn):
    with pytest.raises(ValueError):
        store.table(conn, "launches")


# -- through the core's command ---------------------------------------------------------------


def test_every_act_has_a_handler_and_help():
    parser = cli.build_parser()
    acts = {"relaunch", "exit-session", "exits", "sessions", "resume", "keep-warm", "state"}
    assert acts <= set(cli.VERBS)
    choices = next(a for a in parser._actions if a.dest == "verb").choices
    for act in acts:
        assert choices[act].description



@pytest.mark.parametrize("argv, code, err", [
    (["exits"], codes.OK, "no exits\n"),
    (["state", SID], codes.REFUSED, "[not-launched]"),
])
def test_the_acts_reach_this_cli_through_the_cores_command(argv, code, err, tmp_path):
    moxi = shutil.which("moxi", path=str(Path(sys.executable).parent))
    if moxi is None:
        pytest.skip("this environment has no moxi script")
    done = subprocess.run([moxi, "launcher", *argv], capture_output=True, text=True,
                          timeout=60, env=dict(os.environ), cwd=str(tmp_path))
    assert done.returncode == code, done.stderr
    assert err in done.stderr

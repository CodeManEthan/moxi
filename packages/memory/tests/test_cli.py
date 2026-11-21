"""`moxi memory`: the search lines and the read line under them, exit codes and the
incomplete-answer lines, `ingest` and `reindex` returning at once, and `status`."""

import json
import os
import time

import pytest
from moxi import files
from moxi_dev.testing import wait_for
from moxi_sessions import api as sessions

from moxi_memory import clock, cli, index, ingest, links, start, state

T = sessions.testing
START = 1_700_000_000.0


def config(**over):
    base = {"harnesses": ["claude", "codex", "opencode"], "index.include_subagents": False,
            "index.tail_chars": 1000, "redact.sets": ["credentials", "broad"]}
    base.update(over)
    return base


def run(mode="walk", **kw):
    kw.setdefault("config", config())
    kw.setdefault("launcher", links.Link(links.NONE))
    kw.setdefault("then", lambda args: None)
    return ingest.run(mode, **kw)


def claude(n=1, **kw):
    t = T.ClaudeTranscript(sessions.root("claude"),
                           session_id=f"0190b000-0000-7000-8000-{n:012d}", start=START, **kw)
    t._count = 1000 * n
    return t


def memory(capsys, *argv):
    code = cli.main(list(argv), prog="moxi memory")
    out, err = capsys.readouterr()
    return code, out, err


# -- search -----------------------------------------------------------------------------

def test_a_hit_line_and_the_read_line_under_the_hits(capsys, tmp_path):
    (tmp_path / "proj" / ".git").mkdir(parents=True)
    t = claude(cwd=str(tmp_path / "proj"))
    t.user("the lemur question")
    t.write()
    run()
    code, out, _ = memory(capsys, "search", "lemur")
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == (f"1. claude  {t.session_id}  {clock.show(START)}  proj  user")
    assert "«lemur»" in lines[1]
    assert lines[-1] == (f"moxi sessions read {t.session_id} --around "
                         f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(START))}")


def test_the_read_line_names_the_harness_when_the_hit_is_not_claude_codes(capsys):
    r = T.CodexRollout(sessions.root("codex"))
    r.turn()
    r.user("a capybara in codex")
    r.write()
    run()
    code, out, _ = memory(capsys, "search", "capybara")
    assert code == 0
    read = out.splitlines()[-1]
    assert read.startswith(f"moxi sessions read {r.thread_id} --harness codex --around ")


def test_no_match_exits_0_and_says_how_many_rows(capsys):
    t = claude()
    t.user("one")
    t.assistant("two")
    t.write()
    run()
    assert memory(capsys, "search", "three") == (0, "no match in 2 rows\n", "")


def test_a_missing_index_exits_1_naming_status_and_reindex(capsys):
    code, out, err = memory(capsys, "search", "anything")
    assert code == 1 and out == ""
    assert "moxi memory status" in err and "moxi memory reindex" in err
    assert "starts a detached rebuild; this search can't wait for it" in err
    code, out, _ = memory(capsys, "search", "anything", "--json")
    assert code == 1 and json.loads(out)["error"] == "missing"


def test_a_missing_index_during_a_rebuild_says_the_rebuild_runs(capsys):
    with files.lock(index.rebuild_lock_path()):
        code, _, err = memory(capsys, "search", "anything")
    assert code == 1 and "a rebuild is running" in err and "moxi memory status" in err
    assert "reindex" not in err


def test_an_unreadable_index_exits_1(capsys):
    run()
    path = index.db_path(index.current())
    for suffix in ("-wal", "-shm"):
        if os.path.exists(f"{path}{suffix}"):
            os.remove(f"{path}{suffix}")
    path.write_bytes(b"not a database " * 200)
    code, _, err = memory(capsys, "search", "anything")
    assert code == 1 and "can't be read" in err and "moxi memory reindex" in err


def spoil_state():
    path = state.path()
    for suffix in ("-wal", "-shm"):
        if os.path.exists(f"{path}{suffix}"):
            os.remove(f"{path}{suffix}")
    path.write_bytes(b"not a database " * 200)


def test_a_state_store_that_cant_be_read_is_named_and_the_index_stays_ok(capsys):
    run()
    spoil_state()
    code, _, err = memory(capsys, "search", "anything")
    assert code == 1 and "state.db" in err and "state store can't be read" in err
    assert "index-" not in err and "moxi memory reindex" not in err
    code, out, _ = memory(capsys, "status")
    assert code == 0 and "index: generation" in out
    assert "sessions: the state store can't be read" in out and "state.db" in out
    code, out, _ = memory(capsys, "status", "--json")
    got = json.loads(out)
    assert got["index"] == "ok" and "state.db" in got["sessions_unreadable"]


def test_a_filling_index_and_a_running_rebuild_each_add_one_line(capsys):
    with index.writer() as w:
        conn = w.connect()
        index.meta_set(conn, "listed.claude", 7)
        conn.execute("INSERT INTO sources (harness, source) VALUES ('claude', 'a'), "
                     "('claude', 'b')")
        conn.close()
    with files.lock(index.rebuild_lock_path()):
        sc = state.connect()
        state.write_entry(sc, START)
        sc.close()
        code, out, _ = memory(capsys, "search", "anything")
        _, json_out, _ = memory(capsys, "search", "anything", "--json")
    doc = json.loads(json_out)
    assert doc["filling"] == {"sources_read": 2, "listed": 7}
    assert doc["rebuild_started_at"] == START
    assert code == 0
    assert out.splitlines() == [
        "no match in 0 rows",
        "index still filling: 2 of 7 sources read",
        f"rebuild running since {clock.show(START)}; sessions since then are not in "
        "these results",
    ]


def test_json_carries_the_hits_the_states_and_the_read_line(capsys):
    t = claude()
    t.user("an okapi")
    t.write()
    run()
    code, out, _ = memory(capsys, "search", "okapi", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["rows"] == 1 and doc["tokens"] == ["okapi"]
    hit, = doc["hits"]
    assert hit["session_id"] == t.session_id and hit["kind"] == "user"
    assert hit["at"] == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(START))
    assert doc["top_read_command"] == hit["read_command"] and doc["filling"] is None
    assert doc["rebuild_started_at"] is None and doc["notes"] == []


def test_bad_arguments_exit_2(capsys):
    run()
    assert memory(capsys, "search", "x", "--since", "yesterday-ish")[0] == 2
    assert memory(capsys, "search", "x", "--limit", "0")[0] == 2
    assert memory(capsys, "search", "x", "--harness", "vim")[0] == 2


def test_since_takes_an_age_iso_or_epoch_seconds():
    assert cli.parse_since("7d", now=1000_000.0) == 1000_000.0 - 7 * 86400
    assert cli.parse_since("90m", now=10_000.0) == 10_000.0 - 5400
    assert cli.parse_since("1700000000") == 1_700_000_000.0
    assert cli.parse_since("2023-11-14T22:13:20Z") == 1_700_000_000.0
    with pytest.raises(ValueError):
        cli.parse_since("soon")


def test_since_refuses_a_bare_year():
    for text in ("2026", " 2025 ", "99"):
        with pytest.raises(ValueError, match="ISO 8601"):
            cli.parse_since(text)
    assert cli.parse_since("2026-01-01") > 1_700_000_000.0


def test_clock_show_is_local_time_to_the_minute():
    assert clock.show(START) == time.strftime("%Y-%m-%d %H:%M", time.localtime(START))
    assert clock.show(None) == "-"


# -- ingest and reindex -----------------------------------------------------------------

@pytest.fixture
def slow_child(tmp_path, monkeypatch):
    done = tmp_path / "done"
    script = tmp_path / "slow-child"
    script.write_text(f"#!/bin/sh\nsleep 2\necho \"$@\" > {done}\n")
    script.chmod(0o755)
    import moxi.paths
    monkeypatch.setattr(moxi.paths, "script", lambda name: script)
    return done


@pytest.mark.parametrize("argv, args", [(["ingest"], "--walk --asked"),
                                        (["ingest", "--all"], "--all"),
                                        (["reindex"], "--rebuild"),
                                        (["reindex", "--relabel"], "--relabel")])
def test_ingest_and_reindex_return_before_their_child_finishes(capsys, slow_child, argv,
                                                                args):
    began = time.monotonic()
    code, out, _ = memory(capsys, *argv)
    assert code == 0 and time.monotonic() - began < 1 and not slow_child.exists()
    assert out.splitlines()[-1] == "watch it with: moxi memory status"
    wait_for(slow_child.exists, timeout=10)
    assert slow_child.read_text().strip() == args


def test_reindex_says_what_a_rebuild_drops_before_it_starts_the_child(capsys, monkeypatch):
    printed = []

    def spawn(args):
        printed.append(capsys.readouterr().out)
        return 4242

    monkeypatch.setattr(start, "spawn", spawn)
    code, out, _ = memory(capsys, "reindex")
    assert code == 0
    assert ("drops each session whose transcript is gone from every folder of its "
            "harness" in printed[0])
    assert "process 4242" in out


def test_reindex_over_rows_an_older_redact_stored_says_what_it_keeps(capsys, monkeypatch):
    t = claude()
    t.user("a prompt")
    t.write()
    run()
    with index.writer() as w:
        conn = w.connect()
        try:
            conn.execute("DELETE FROM meta WHERE key = ?", (index.REDACT_VERSION_KEY,))
        finally:
            conn.close()
    monkeypatch.setattr(start, "spawn", lambda args: 4242)
    code, out, _ = memory(capsys, "reindex")
    assert code == 0
    assert "stored under secrets' redact version 1" in out
    assert "drops no session, and no row of a source it can't read to its end" in out
    assert "that the source no longer gives leaves" in out
    assert "drops each session whose transcript is gone from every folder" not in out


def test_reindex_while_a_rebuild_runs_is_queued_behind_it(capsys, monkeypatch):
    spawned = []
    monkeypatch.setattr(start, "spawn", lambda args: spawned.append(args) or 4242)
    with files.lock(index.rebuild_lock_path()):
        code, out, _ = memory(capsys, "reindex", "--relabel")
    assert code == 0 and spawned == [["--relabel"]]
    assert "queued behind the rebuild that started" in out
    assert "runs when that rebuild ends" in out and "moxi memory status" in out
    assert "started" not in out.replace("rebuild that started", "")


@pytest.mark.parametrize("argv, args", [
    (["reindex"], ["--rebuild"]), (["reindex", "--relabel"], ["--relabel"]),
    (["ingest", "--all"], ["--all"]), (["ingest"], ["--walk", "--asked"])])
def test_a_request_never_says_started_and_touches_no_writer_lock(capsys, monkeypatch, argv,
                                                                 args):
    spawned, taken = [], []
    monkeypatch.setattr(start, "spawn", lambda a: spawned.append(a) or 4242)
    real = files.lock
    monkeypatch.setattr(files, "lock",
                        lambda path, **kw: taken.append(path) or real(path, **kw))
    code, out, _ = memory(capsys, *argv)
    assert code == 0 and spawned == [args] and taken == []
    assert "process 4242" in out and "started" not in out
    assert ("it runs now, or, if an ingest holds memory's writer lock, as soon as that "
            "ingest ends; `moxi memory status` shows which") in out


# -- status -----------------------------------------------------------------------------

def test_status_shows_a_waiting_request(capsys):
    run()
    conn = state.connect()
    try:
        state.ask(conn, "rebuild", START)
    finally:
        conn.close()
    code, out, _ = memory(capsys, "status")
    assert code == 0 and f"request waiting: rebuild, asked {clock.show(START)}" in out
    code, out, _ = memory(capsys, "status", "--json")
    assert json.loads(out)["waiting"] == {"kind": "rebuild", "asked_at": START}
    run("waiting")
    code, out, _ = memory(capsys, "status")
    assert "request waiting: none" in out


def test_status_shows_every_field(capsys):
    t = claude()
    t.user("q")
    call = t.request().tool_use("Bash", {"command": "ls"})
    t.tool_result(call, "out")
    t.assistant("a")
    t.write()
    r = T.CodexRollout(sessions.root("codex"))
    r.turn()
    r.user("codex q")
    r.write()
    run(config=config(harnesses=["claude", "codex"]))
    run("rebuild", config=config(harnesses=["claude", "codex"]))
    later = claude(2)
    later.user("not walked yet")
    later.write()
    code, out, _ = memory(capsys, "status", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["index"] == "ok"
    assert doc["rows"]["1"] == {"claude": 3, "codex": 1}
    assert doc["rows"]["2"] == {"claude": 1} and doc["rows"]["subagents"] == {}
    assert doc["sources_read"] == 2 and doc["sources_not_read"] == 0
    assert doc["first_read"] is True and doc["filling"] is None
    assert doc["rebuild_started_at"] is None and doc["tokenizer"] == index.TOKENIZER
    assert doc["dropped"] == 0 and doc["rebuilt"] is not None
    assert doc["waiting"] is None
    assert doc["last_ingest"] is not None
    assert doc["backlog"]["claude"] == 1
    assert doc["sessions"]["total"] == 2 and doc["sessions"]["by_kind"] == {"none": 2}
    assert doc["sessions"]["without_identity"] == 2
    code, out, _ = memory(capsys, "status")
    assert code == 0
    for start_ in ("index: generation", "rows: tier 1 claude 3, codex 1", "sources: 2 read",
                   "last ingest: ", "backlog: ", "first full read: finished",
                   "rebuild: none running", "last rebuild: started ",
                   "sessions: 2 (by launch kind: none 2); 0 with an identity"):
        assert any(line.startswith(start_) for line in out.splitlines()), start_


def test_status_counts_a_harness_with_no_root_as_nothing_behind(capsys):
    t = claude()
    t.user("q")
    t.write()
    run()                              # the Codex and OpenCode roots are not there
    code, out, _ = memory(capsys, "status", "--json")
    assert json.loads(out)["backlog"] == {"claude": 0, "codex": 0, "opencode": 0}
    code, out, _ = memory(capsys, "status")
    assert "root not listable" not in out


def test_status_with_no_index_exits_1_and_says_what_to_run(capsys):
    code, out, _ = memory(capsys, "status")
    assert code == 1 and out.startswith("index: none")
    assert "moxi memory reindex" in out
    code, out, _ = memory(capsys, "status", "--json")
    assert code == 1 and json.loads(out)["index"] == "missing"

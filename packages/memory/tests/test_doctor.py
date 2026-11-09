"""The doctor's rows, the announcement, and the additions contribution."""

import os

import pytest
from moxi.doctor import Row
from moxi_sessions import api

from moxi_memory import additions, doctor, ingest, links, module, state, table

T = api.testing
NAMES = ["index", "ingest", "sets", "start", "sessions", "links", "retention"]


def write_config(name, text):
    from moxi.paths import Layout

    path = Layout(name).config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def by_name():
    rows = list(doctor.rows())
    assert all(isinstance(r, Row) and r.module == "memory" for r in rows)
    return {r.name: r for r in rows}


@pytest.fixture(autouse=True)
def no_links(monkeypatch):
    monkeypatch.setattr(links, "comms", lambda: links.Link(links.NONE,
                                                           reason="comms is not installed"))
    monkeypatch.setattr(links, "launcher", lambda: links.Link(
        links.NONE, reason="launcher is not installed"))


def ingest_one():
    t = T.ClaudeTranscript(api.root("claude"), session_id="0190b000-0000-7000-8000-00000000c1a0")
    t.user("how do the widgets render")
    t.request().text("they render twice")
    t.write()
    report = ingest.run("walk", None, None, launcher=links.Link(links.NONE),
                        then=lambda args: None)
    assert not report.busy and report.sources == 1


# -- the doctor -------------------------------------------------------------------------------

def test_a_fresh_home_gives_every_row_and_writes_nothing():
    rows = by_name()
    assert list(rows) == NAMES
    assert rows["index"].state == "warn" and "no index yet" in rows["index"].detail
    assert rows["ingest"].state == "warn"
    assert rows["sets"].state == "ok" and "credentials, broad" in rows["sets"].detail
    assert rows["sessions"].state == "ok" and "no session table yet" in rows["sessions"].detail
    assert rows["retention"].state == "ok"
    assert not state.path().exists()


def test_a_harness_with_no_root_does_not_keep_the_index_filling():
    ingest_one()                      # the Codex and OpenCode roots are not there
    rows = by_name()
    assert "still filling" not in rows["index"].detail


def test_an_index_whose_first_read_has_not_finished_is_still_filling():
    ingest_one()
    from moxi_memory import index

    with index.writer() as w:
        index.meta_set(w.connect(), "first_read", 0)
    assert "still filling" in by_name()["index"].detail


def test_after_an_ingest_the_index_and_ingest_rows_say_what_is_there():
    write_config("memory", 'harnesses = ["claude"]\n')
    ingest_one()
    rows = by_name()
    index_row = rows["index"]
    assert index_row.state == "ok"
    assert "generation 1" in index_row.detail and "schema 1" in index_row.detail
    assert "2 messages and calls" in index_row.detail
    assert "still filling" not in index_row.detail
    assert "claude: last walk " in rows["ingest"].detail
    assert "0 sources changed since" in rows["ingest"].detail
    sessions = rows["sessions"]
    assert sessions.detail.startswith("1 session, 0 with an identity, 1 settled")


def test_the_ingest_row_counts_what_status_counts_as_backlog():
    write_config("memory", 'harnesses = ["claude"]\n')
    t = T.ClaudeTranscript(api.root("claude"), session_id="0190b000-0000-7000-8000-00000000c1a0")
    t.user("how do the widgets render")
    t.write()
    ingest.run("walk", None, None, launcher=links.Link(links.NONE),
               then=lambda args: None)
    sub = t.subagent("a0190b0000000001")        # subagents are off: no backlog
    sub.user("subagent work item")
    sub.write()
    from moxi_memory import status

    assert status.status().backlog == {"claude": 0}
    assert "claude: last walk " in by_name()["ingest"].detail
    assert "0 sources changed since" in by_name()["ingest"].detail


def test_a_killed_rebuild_is_a_warn_on_the_index_row_until_a_rebuild_finishes():
    ingest_one()
    conn = state.connect()
    try:
        state.write_unfinished(conn, 1_700_000_000.0)
    finally:
        conn.close()
    row = by_name()["index"]
    assert row.state == "warn" and "did not finish" in row.detail
    assert row.fix == "moxi memory reindex"
    ingest.run("rebuild", launcher=links.Link(links.NONE), then=lambda args: None)
    row = by_name()["index"]
    assert row.state == "ok" and "did not finish" not in row.detail


def ask(kind, asked):
    conn = state.connect()
    try:
        state.ask(conn, kind, asked)
    finally:
        conn.close()


def test_a_waiting_request_is_said_and_a_warn_only_when_no_child_started_it():
    import time

    from moxi import files

    from moxi_memory import index

    write_config("memory", 'harnesses = ["claude"]\n')
    ingest_one()
    ask("rebuild", time.time())
    row = by_name()["ingest"]
    assert row.state == "ok" and "a rebuild request waiting since" in row.detail
    with files.lock(index.rebuild_lock_path()):
        row = by_name()["ingest"]
    assert row.state == "ok" and "queued behind the rebuild running since" in row.detail
    ask("relabel", time.time() - doctor.WAITING_WARN - 60)
    with files.lock(index.rebuild_lock_path()):
        assert by_name()["ingest"].state == "ok"   # behind a rebuild: nothing to do
    row = by_name()["ingest"]
    assert row.state == "warn" and "a relabel request waiting since" in row.detail
    assert row.fix == "moxi memory ingest"


def test_a_harness_with_no_root_is_not_a_walk_missing():
    ingest_one()                      # the Codex and OpenCode roots are not there
    row = by_name()["ingest"]
    assert row.state == "ok", row.detail
    assert "codex: nothing to read, no root at " in row.detail
    assert "opencode: nothing to read, no root at " in row.detail
    os.makedirs(api.root("codex"), exist_ok=True)
    row = by_name()["ingest"]
    assert row.state == "warn" and "codex: no walk yet" in row.detail


def test_the_sessions_row_counts_identities_and_kinds():
    conn = state.connect()
    table.write_start(conn, "claude", "a", "shop")
    table.write_identity(conn, "claude", "a", "planner")
    table.write_read(conn, "claude", "a", newest=5.0, kind="scratch", settled=True)
    table.write_start(conn, "codex", "b", "shop")
    conn.close()
    detail = by_name()["sessions"].detail
    assert detail == "2 sessions, 1 with an identity, 1 settled; kinds: none 1, scratch 1"


def test_a_set_secrets_does_not_have_fails_the_sets_row():
    write_config("memory", '[redact]\nsets = ["credentials", "no-such-set"]\n')
    row = by_name()["sets"]
    assert row.state == "fail" and "no-such-set" in row.detail


def test_the_start_row_reads_turns_start_list():
    row = by_name()["start"]
    assert row.state == "ok", row.detail
    write_config("turns", '[start]\norder = ["comms"]\n')
    row = by_name()["start"]
    assert row.state == "warn" and "claude" in row.detail
    assert row.fix == "moxi config set turns start.order --add memory"


def test_the_links_row_says_what_memory_does_without_each(monkeypatch):
    detail = by_name()["links"].detail
    assert "comms not linked" in detail and "no agent list" in detail
    assert "launcher not linked" in detail and "may be listed" in detail
    monkeypatch.setattr(links, "comms", lambda: links.Link(links.READY, object()))
    monkeypatch.setattr(links, "launcher",
                        lambda: links.Link(links.FAILING, reason="it broke"))
    row = by_name()["links"]
    assert row.state == "warn"
    assert "comms loads" in row.detail and "launcher fails to load (it broke)" in row.detail


def test_the_retention_row_is_sessions_brief_and_warns_where_sessions_does():
    path = os.path.join(os.environ["CODEX_HOME"], "config.toml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("[features]\nlocal_thread_store_compression = true\n")
    row = by_name()["retention"]
    assert row.state == "warn"
    assert row.detail == "; ".join(item.brief for item in api.retention())


# -- the announcement -----------------------------------------------------------------------

def test_the_announcement_names_every_part():
    assert module.API == "moxi_memory.api"
    assert module.CLI == "moxi_memory.cli:main"
    assert module.SETUP == "moxi_memory.setup:setup"
    assert module.TEARDOWN == "moxi_memory.setup:teardown"
    assert module.DOCTOR == "moxi_memory.doctor:rows"
    assert set(module.OPTIONAL) == {"comms", "launcher"}
    assert module.TIMERS["ingest"] == {
        "command": "moxi-memory-ingest", "args": ["--walk"], "every": "1h",
        "switch": "ingest.timer",
        "doc": "read the transcripts that changed since the last walk"}
    from moxi_memory import settings

    assert "ingest.timer" in settings.schema().keys


# -- the additions -----------------------------------------------------------------------------

def test_the_additions_let_the_two_commands_run_in_claude_and_opencode():
    got = additions.additions(object())
    assert got == {"tool_permissions": {
        "claude": ("Bash(moxi memory search:*)", "Bash(moxi sessions read:*)"),
        "opencode": ("bash moxi memory search*", "bash moxi sessions read*")}}


def test_turns_puts_the_additions_into_a_launch(monkeypatch):
    import moxi_turns as turns

    adds = turns.launch_additions("claude", cwd="/", env={})
    assert "--allowedTools=Bash(moxi memory search:*)" in adds.argv
    assert "--allowedTools=Bash(moxi sessions read:*)" in adds.argv
    codex = turns.launch_additions("codex", cwd="/", env={})
    assert not any("moxi memory" in a for a in codex.argv)

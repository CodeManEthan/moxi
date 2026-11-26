"""The doctor's rows, the announcement, and the additions contribution."""

import os

import pytest
from moxi.doctor import Row
from moxi_secrets import REDACT_VERSION
from moxi_sessions import api

from moxi_memory import additions, doctor, ingest, links, module, state, table

T = api.testing
#: The doctor's own live check, kept before the fixture below stands one in for it.
LIVE_SUBCONSCIOUS = doctor.live_subconscious
NAMES = ["index", "ingest", "folders", "sets", "start", "sessions", "links", "retention",
         "subconscious"]


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
    monkeypatch.setattr(links, "notes", lambda: links.Link(
        links.NONE, reason="notes is not installed"))
    monkeypatch.setattr(doctor, "live_subconscious", lambda: [])


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
    # the same command search and status name for a missing index
    assert rows["index"].fix == rows["ingest"].fix == "moxi memory reindex"
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
    assert "generation 1" in index_row.detail and "schema 2" in index_row.detail
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


def test_the_index_row_shows_the_redact_version_and_warns_while_it_is_below_secrets():
    from moxi_memory import index

    ingest_one()
    row = by_name()["index"]
    assert row.state == "ok" and f"redact version {REDACT_VERSION}" in row.detail
    with index.writer() as w:
        conn = w.connect()
        try:
            conn.execute("DELETE FROM meta WHERE key = ?", (index.REDACT_VERSION_KEY,))
        finally:
            conn.close()
    row = by_name()["index"]
    assert row.state == "warn" and "redact version 1" in row.detail
    assert f"secrets' redact version {REDACT_VERSION} catches" in row.detail
    assert row.fix == "moxi memory reindex"
    ingest.run("rebuild", launcher=links.Link(links.NONE), then=lambda args: None)
    row = by_name()["index"]
    assert row.state == "ok" and f"redact version {REDACT_VERSION}" in row.detail
    assert "redacted again the rows stored under redact version 1" in row.detail
    assert "dropped no session" in row.detail


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
    assert row.fix == "moxi config set memory redact.sets --remove no-such-set"
    assert "credentials" in row.detail.split("Secrets' sets:")[1]
    write_config("memory", '[redact]\nsets = ["no-such-set"]\n')
    assert by_name()["sets"].fix == "moxi config unset memory redact.sets"


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
    assert "notes not linked" in detail and "no notes" in detail
    assert "slots" not in detail
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
    assert set(module.OPTIONAL) == {"comms", "launcher", "connector", "secrets", "notes"}
    assert module.TIMERS["ingest"] == {
        "command": "moxi-memory-ingest", "args": ["--walk"], "every": "1h",
        "switch": "ingest.timer.enabled",
        "doc": "read the transcripts that changed since the last walk"}
    from moxi_memory import settings

    assert "ingest.timer.enabled" in settings.schema().keys


def test_the_index_is_a_declared_rebuildable_store_and_the_session_table_is_kept():
    from moxi.modules import load
    from moxi_memory import index, settings

    stores = load("memory").stores
    entry = stores["index"]
    assert entry["kind"] == "dir" and entry["rebuildable"] is True
    assert settings.cache_dir() / entry["path"] == index.index_dir()
    assert stores["sessions"]["rebuildable"] is False
    ingest_one()
    assert index.db_path(index.current()).parent == index.index_dir()
    assert {p.name for p in settings.cache_dir().iterdir()} == {entry["path"]}


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


def test_a_state_store_that_cant_be_read_is_a_warn_on_the_sessions_row():
    ingest_one()
    path = state.path()
    for suffix in ("-wal", "-shm"):
        if os.path.exists(f"{path}{suffix}"):
            os.remove(f"{path}{suffix}")
    path.write_bytes(b"not a database " * 200)
    rows = by_name()
    assert rows["sessions"].state == "warn"
    assert "the check failed" not in rows["sessions"].detail
    assert rows["index"].state == "ok"


# -- the subconscious -------------------------------------------------------------------

SID = "0190b000-0000-7000-8000-0000000000d1"
#: An identity no live process has: this pid with a start time it never had.
DEAD = {"pid": os.getpid(), "started": 1.0}


def recall_line(recall, *, updated, **fields):
    from moxi_memory import recalls

    with recalls.session_lock(SID):
        recalls.put_line(SID, {"recall": recall, "harness": "claude", **fields},
                         now=updated)


def test_the_subconscious_row_names_a_live_session_with_no_route_while_it_holds(
        monkeypatch):
    assert by_name()["subconscious"].state == "ok"
    monkeypatch.setattr(doctor, "live_subconscious", lambda: [
        ("s-off", "automatic", "no turns fork in this session"),
        ("s-on", "on-demand", None)])
    row = by_name()["subconscious"]
    assert row.state == "warn" and row.fix == "moxi memory subconscious list"
    assert "on in 1 live session; a recall fork's" in row.detail
    assert "permission asks are refused" in row.detail
    assert "off for want of a route in s-off (no turns fork in this session)" in row.detail
    assert "s-on" not in row.detail
    monkeypatch.setattr(doctor, "live_subconscious", lambda: [("s-on", "automatic", None)])
    row = by_name()["subconscious"]
    assert row.state == "ok" and "off for want" not in row.detail
    monkeypatch.setattr(doctor, "live_subconscious", lambda: [
        ("x-codex", "automatic", "Codex: no recall route runs on Codex")])
    row = by_name()["subconscious"]
    assert row.state == "warn" and "on in" not in row.detail
    assert "off for want of a route in x-codex" in row.detail


def test_a_recall_stopped_by_the_config_shows_only_while_the_config_is_in_error():
    import time

    from moxi_memory import recalls

    recall_line("r-config", updated=time.time(), state=recalls.LOST, reason="config",
                error="limit_s is 0: give 1 to 3600")
    row = by_name()["subconscious"]
    assert row.state == "ok" and "r-config" not in row.detail
    write_config("memory", "this is = = not toml\n")
    row = by_name()["subconscious"]
    assert row.state == "warn" and row.fix == "moxi config memory"
    assert "1 recall stopped while memory's config is in error (r-config)" in row.detail
    assert "limit_s is 0: give 1 to 3600" in row.detail


def test_only_the_recalls_stopped_since_the_config_last_read_cleanly_are_listed():
    import time

    from moxi_memory import recalls

    now = time.time()
    recall_line("r-fixed", updated=now - 300, at=now - 300, state=recalls.LOST,
                reason="config", error="an error fixed since")
    recall_line("r-good", updated=now - 200, at=now - 200, state=recalls.BACK,
                prefetch_hits=3)
    recall_line("r-now", updated=now - 100, at=now - 100, state=recalls.LOST,
                reason="config", error="limit_s is 0: give 1 to 3600")
    write_config("memory", "this is = = not toml\n")
    row = by_name()["subconscious"]
    assert row.state == "warn"
    assert ("1 recall stopped while memory's config is in error (r-now): "
            "limit_s is 0: give 1 to 3600") in row.detail
    assert "r-fixed" not in row.detail and "an error fixed since" not in row.detail


def test_a_line_still_sending_is_listed_with_its_wait_and_last_refusal():
    import time

    from moxi_memory import recall_take, recalls

    now = time.time()
    recall_line("r-send", updated=now, state=recalls.SENDING, child=recall_take.me(),
                sending_at=now - 120, send_tries=4, send_reason="unavailable: daemon down")
    row = by_name()["subconscious"]
    assert row.state == "warn"
    assert ("1 recall still sending, waiting for comms to take its answer: r-send "
            "(sending for 120s, 4 tries refused, the last: unavailable: daemon down)"
            ) in row.detail


def test_a_line_whose_child_is_gone_is_listed_with_its_age_until_taken():
    import time

    from moxi_memory import recall_take, recalls

    now = time.time()
    recall_line("r-stuck", updated=now - 600, state=recalls.OPEN, child=DEAD,
                ref="r-stuck:1", kept=True)
    recall_line("r-young", updated=now, state=recalls.OPEN, child=DEAD)
    recall_line("r-live", updated=now - 600, state=recalls.OPEN, child=recall_take.me())
    recall_line("r-done", updated=now - 600, state=recalls.BACK, child=DEAD, kept=False)
    row = by_name()["subconscious"]
    assert row.state == "warn"
    assert "1 recall line not yet taken, child gone: r-stuck (open, 10m old)" in row.detail
    for other in ("r-young", "r-live", "r-done"):
        assert other not in row.detail


def test_recalls_whose_child_never_ran_are_listed():
    import time

    from moxi_memory import recall_start, recalls, records

    now = time.time()
    recall_line("r-spawn", updated=now, at=now - 60, state=recalls.LOST, reason="spawn",
                error="FileNotFoundError: moxi-memory-subconscious")
    recall_line("r-old-spawn", updated=now, at=now - 2 * 86400, state=recalls.LOST,
                reason="spawn", error="an older error")
    with recalls.session_lock(SID):
        for recall_id, state, age in (("r-silent", "starting", 600),
                                      ("r-stuck", "hurried", 10800),
                                      ("r-new", "starting", 1), ("r-hurried", "hurried", 1),
                                      ("r-nameless", "starting", 120),
                                      ("r-forked", "starting", 120)):
            records.write({"recall": recall_id, "session": SID, "state": state,
                           "settings": recall_start.default_settings()}, now=now - age)
        records.update(SID, "r-forked", fork="agent-1")
    recall_line("r-nameless", updated=now - 120, at=now - 120, state=recalls.OPEN,
                child=None)
    recall_line("r-forked", updated=now - 120, at=now - 120, state=recalls.OPEN,
                child=None)
    row = by_name()["subconscious"]
    assert row.state == "warn" and row.fix == "moxi memory subconscious list"
    assert ("1 recall in the last day whose child could not start (r-spawn): "
            "FileNotFoundError: moxi-memory-subconscious") in row.detail
    assert ("3 recalls with no log line naming its child (gone before it wrote one, or "
            "with no identity to write): r-nameless (starting, 2m old), r-silent (starting, "
            "10m old), r-stuck (hurried, 3h old)") in row.detail
    assert "r-forked (starting" not in row.detail
    for other in ("r-old-spawn", "r-new", "r-hurried"):
        assert other not in row.detail


def test_a_comms_route_record_whose_child_died_before_its_line_is_listed():
    import time

    from moxi_memory import recall_guard, recall_start, recall_take, recalls, records

    now = time.time()
    live = recall_take.me()
    with recalls.session_lock(SID):
        for recall_id, route, holder, age in (
                ("r-slot-gone", recall_start.SLOT, DEAD, 30),
                ("r-host-nameless", recall_start.HOST_FORK, None, 600),
                ("r-slot-live", recall_start.SLOT, live, 600)):
            record = {"recall": recall_id, "session": SID, "state": "starting",
                      "route": route, "settings": recall_start.default_settings()}
            if holder is not None:
                record["holder"] = holder
            records.write(record, now=now - age)
    for recall_id in ("r-slot-gone", "r-host-nameless", "r-slot-live"):
        record = records.read(SID, recall_id)
        assert recall_guard.awaits_fork(SID, record) is False
    row = by_name()["subconscious"]
    assert row.state == "warn"
    assert ("2 recalls with no log line naming its child (gone before it wrote one, or "
            "with no identity to write): r-slot-gone (starting, 30s old), r-host-nameless "
            "(starting, 10m old)") in row.detail
    assert "r-slot-live" not in row.detail


class FakeSession:
    def __init__(self, sid, harness, stamps, capabilities=("fork",)):
        self.session_id, self.harness = sid, harness
        self.stamps, self.capabilities = stamps, frozenset(capabilities)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def env(self, names):
        return {n: self.stamps.get(n) for n in names}


def test_live_sessions_are_checked_as_a_recalls_own_try_checks_them(monkeypatch):
    import moxi_turns as client

    from moxi_memory import recall_start

    on = {"MOXI_MEMORY_SUBCONSCIOUS": "automatic"}
    slot = {**on, "MOXI_MEMORY_SUBCONSCIOUS_ROUTE": "slot"}
    monkeypatch.setattr(recall_start, "offers_fork", lambda sid: sid != "c-none")
    monkeypatch.setattr(recall_start, "slot_why", lambda sid, env=None: (
        None if sid == "x-slot" else recall_start.NO_SLOTS))
    monkeypatch.setattr(client, "sessions", lambda: [
        FakeSession("c-fork", "claude", on),
        FakeSession("c-none", "claude", on, capabilities=()),
        FakeSession("c-slot", "claude", slot),
        FakeSession("x-codex", "codex", {"MOXI_MEMORY_SUBCONSCIOUS": "on-demand"}),
        FakeSession("x-slot", "codex", slot),
        FakeSession("c-forked", "claude", {**on, "MOXI_MEMORY_SUBCONSCIOUS_FORK": "r-1"}),
        FakeSession("c-off", "claude", {}),
        FakeSession("c-bad", "claude", {"MOXI_MEMORY_SUBCONSCIOUS": "sometimes"})])
    assert LIVE_SUBCONSCIOUS() == [
        ("c-fork", "automatic", None),
        ("c-none", "automatic", "no turns fork in this session; the slot route: no slots "
                                "here"),
        ("c-slot", "automatic", "no slots here"),
        ("x-codex", "on-demand", "Codex: off unless the launch picks the slot route"),
        ("x-slot", "automatic", None)]


def test_a_route_that_answers_by_comms_is_off_while_its_recall_sender_cant_send(
        monkeypatch):
    import moxi_turns as client

    from moxi_memory import recall_start

    on = {"MOXI_MEMORY_SUBCONSCIOUS": "automatic"}
    monkeypatch.setattr(recall_start, "BY_COMMS", (recall_start.SUBAGENT,))
    whys = {"c-free": None, "c-held": "recall sender in conflict: held-subconscious",
            "c-nameless": "no comms name for this session"}
    monkeypatch.setattr(recall_start, "comms_why", lambda sid, env=None: whys[sid])
    monkeypatch.setattr(recall_start, "offers_fork", lambda sid: True)
    monkeypatch.setattr(client, "sessions", lambda: [
        FakeSession(sid, "claude", on) for sid in whys])
    live = LIVE_SUBCONSCIOUS()
    assert live == [("c-free", "automatic", None),
                    ("c-held", "automatic", "recall sender in conflict: held-subconscious"),
                    ("c-nameless", "automatic", "no comms name for this session")]
    monkeypatch.setattr(doctor, "live_subconscious", lambda: live)
    row = by_name()["subconscious"]
    assert row.state == "warn"
    assert ("off for want of a route in c-held (recall sender in conflict: "
            "held-subconscious), c-nameless (no comms name for this session)") in row.detail

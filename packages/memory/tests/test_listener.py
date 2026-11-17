"""The listeners: the start line, the reminder, the ingest trigger, and the start lists
with their deadline, their caps and the late identity."""

import os
import threading
import time

import moxi_turns as turns
import pytest
from moxi_dev.testing import wait_for

from moxi_memory import (clock, index, links, listener, listeners, settings, start, state,
                         table)

HARNESSES = ("claude", "codex", "opencode")
LINE_WITH_LABEL = (
    "memory: `moxi memory search \"<words>\"` searches past sessions in every project "
    "(`--project shop` for this one). `moxi sessions read <session>` shows part of one of "
    "the sessions listed below. Search when the task needs something from an earlier "
    "session.")
LINE_WITHOUT_LABEL = (
    "memory: `moxi memory search \"<words>\"` searches past sessions in every project. "
    "`moxi sessions read <session>` shows part of one of the sessions listed below. Search "
    "when the task needs something from an earlier session.")
REMINDER = "memory: search past sessions if this turn needs them."


@pytest.fixture
def shop(tmp_path):
    """A project folder whose label is `shop`."""
    shop_dir = tmp_path / "work" / "shop"
    (shop_dir / ".git").mkdir(parents=True)
    return str(shop_dir)


def event(kind="start", harness="claude", sid="me", cwd=None, env=None):
    return turns.SessionEvent(kind, harness, sid, env=dict(env or {}), cwd=cwd)


class FakeComms:
    def __init__(self, answer=None, delay=0.0, block=None, check=None):
        self.answer = answer
        self.delay = delay
        self.block = block
        self.check = check
        self.asked = []

    def caller_identity(self, env=None):
        self.asked.append(env)
        if self.check is not None:
            self.check()
        if self.block is not None:
            self.block.wait(10)
            return None
        if self.delay:
            time.sleep(self.delay)
        return self.answer


@pytest.fixture
def comms(monkeypatch):
    """Install a fake comms link; returns a setter."""
    def use(fake):
        monkeypatch.setattr(links, "comms", lambda: links.Link(links.READY, fake))
        return fake
    return use


@pytest.fixture
def no_comms(monkeypatch):
    monkeypatch.setattr(links, "comms",
                        lambda: links.Link(links.NONE, reason="comms is not installed"))


@pytest.fixture
def conn():
    c = state.connect()
    yield c
    c.close()


def listed(conn, harness, sid, *, label="shop", identity=None, kind=None, newest=1000.0,
           settled=True):
    """A row as a child leaves it after reading a model answer."""
    table.write_start(conn, harness, sid, label, now=1.0)
    if identity is not None:
        table.write_identity(conn, harness, sid, identity, now=1.0)
    table.write_read(conn, harness, sid, newest=newest, kind=kind, settled=settled, now=2.0)


def write_config(text):
    from moxi.paths import Layout

    path = Layout("memory").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def when(newest):
    return clock.show(newest)


# -- the registration ------------------------------------------------------------------

def test_the_four_rows_in_order():
    assert listeners.LISTENERS == (
        (("start",), "moxi_memory.listener:start"),
        (("start",), "moxi_memory.listener:lists"),
        (("prompt",), "moxi_memory.listener:prompt"),
        (("turn", "gone"), "moxi_memory.listener:fire"),
    )
    for _events, target in listeners.LISTENERS:
        name = target.split(":")[1]
        assert callable(getattr(listener, name))


def test_the_registration_imports_only_the_standard_library():
    import ast
    import sys

    source = open(listeners.__file__).read()
    names = {alias.name.split(".")[0] for node in ast.walk(ast.parse(source))
             if isinstance(node, (ast.Import, ast.ImportFrom))
             for alias in getattr(node, "names", ())
             if not isinstance(node, ast.ImportFrom) or not node.module}
    names |= {node.module.split(".")[0] for node in ast.walk(ast.parse(source))
              if isinstance(node, ast.ImportFrom) and node.module}
    assert names <= set(sys.stdlib_module_names) | {"__future__"}


# -- the start line and the reminder ------------------------------------------------------

@pytest.mark.parametrize("harness", HARNESSES)
def test_the_start_line_is_the_same_for_every_harness_with_and_without_a_label(harness,
                                                                              shop):
    assert listener.start(event(harness=harness, cwd=shop)) == LINE_WITH_LABEL
    assert listener.start(event(harness=harness, cwd=os.sep)) == LINE_WITHOUT_LABEL


def test_the_start_line_takes_the_work_dir_from_pwd_when_the_event_has_none(shop):
    assert listener.start(event(env={"PWD": shop})) == LINE_WITH_LABEL


def test_a_label_a_shell_would_split_is_quoted():
    assert "(`--project 'my shop'` for this one)" in listener.start_line("my shop")


@pytest.mark.parametrize("harness", HARNESSES)
def test_the_reminder_is_the_same_for_every_harness(harness):
    assert listener.prompt(event("prompt", harness=harness)) == REMINDER


def test_the_reminder_off_sends_nothing():
    write_config("reminder = false\n")
    assert settings.read()["reminder"] is False
    assert listener.prompt(event("prompt")) is None


def test_no_answer_opens_the_index(monkeypatch, shop, conn, no_comms):
    def refuse(*a, **k):
        raise AssertionError("a listener opened the index")

    for name in ("open_read", "writer", "rebuild_running", "current", "generations"):
        monkeypatch.setattr(index, name, refuse)
    listed(conn, "codex", "other")
    ev = event(cwd=shop)
    assert listener.start(ev) == LINE_WITH_LABEL
    assert listener.lists(ev) is not None
    assert listener.prompt(event("prompt")) == REMINDER
    cache = settings.cache_dir()
    assert not cache.exists() or list(cache.iterdir()) == []


# -- fire ---------------------------------------------------------------------------------

def test_the_turn_row_only_starts_a_child(monkeypatch):
    spawned = []
    monkeypatch.setattr(start, "spawn", lambda args: spawned.append(args) or 4242)
    assert listener.fire(event("turn", sid="s1")) is None
    assert listener.fire(event("gone", sid="s1")) is None
    assert listener.fire(event("prompt", sid="s1")) is None
    assert spawned == [["--turn", "claude", "s1"], ["--gone", "claude", "s1"]]
    assert not state.path().exists()
    cache = settings.cache_dir()
    assert not cache.exists() or list(cache.iterdir()) == []


# -- the lists ------------------------------------------------------------------------------

def test_with_comms_both_lists_show_five_each_and_the_second_leaves_out_the_first(
        comms, conn, shop):
    comms(FakeComms("planner@shop"))
    # Seven of the agent's own sessions, from another project and this one.
    for i in range(7):
        listed(conn, "claude", f"own{i}", identity="planner@shop",
               label="shop" if i in (6, 4) else "elsewhere", newest=2000.0 + i)
    # Seven project sessions run by others or by no one known.
    for i in range(7):
        listed(conn, "codex", f"proj{i}", identity=None if i % 2 else "builder@shop",
               newest=1000.0 + i)
    listed(conn, "claude", "scratchy", identity="planner@shop", kind="scratch",
           newest=9000.0)
    listed(conn, "claude", "me", identity="planner@shop", newest=9999.0)

    text = listener.lists(event(cwd=shop, sid="me"))

    own = [f"- own{i}  {when(2000.0 + i)}" for i in (6, 5, 4, 3, 2)]
    proj = [f"- proj{i}  {when(1000.0 + i)}  {'codex' if i % 2 else 'builder@shop'}"
            for i in (6, 5, 4, 3, 2)]
    assert text == "\n".join(["your recent sessions (planner@shop):", *own,
                              "recent sessions in shop:", *proj])
    assert "scratchy" not in text and "- me " not in text
    assert len(text.splitlines()) <= 12 and len(text) <= 1000


def test_the_second_list_skips_what_the_first_shows(comms, conn, shop):
    comms(FakeComms("planner"))
    for i in range(3):
        listed(conn, "claude", f"own{i}", identity="planner", newest=3000.0 + i)
    listed(conn, "claude", "other", identity="builder", newest=100.0)
    text = listener.lists(event(cwd=shop))
    project = text.split("recent sessions in shop:\n")[1]
    assert project == f"- other  {when(100.0)}  builder"


def test_the_same_qualified_identity_matches_from_another_project_and_no_other_does(
        comms, conn, shop):
    comms(FakeComms("planner@moxi"))
    listed(conn, "claude", "same", identity="planner@moxi", label="far", newest=500.0)
    listed(conn, "claude", "bare", identity="planner", label="far", newest=501.0)
    listed(conn, "claude", "scoped", identity="planner@core", label="far", newest=502.0)
    text = listener.lists(event(cwd=shop))
    assert text == f"your recent sessions (planner@moxi):\n- same  {when(500.0)}"


def test_without_comms_only_the_project_list_shows_naming_harnesses(no_comms, conn, shop):
    listed(conn, "codex", "a", identity="planner", newest=10.0)
    listed(conn, "opencode", "b", newest=20.0)
    text = listener.lists(event(cwd=shop))
    assert text == "\n".join(["recent sessions in shop:", f"- b  {when(20.0)}  opencode",
                              f"- a  {when(10.0)}  planner"])
    # The session's row is written, with no identity.
    assert table.row(conn, "claude", "me").identity is None


def test_without_the_launcher_no_session_is_left_out_as_scratch(no_comms, conn, shop):
    # A child that found no launcher interface settles the kind as none.
    listed(conn, "claude", "unknown-kind", kind=None, newest=10.0)
    listed(conn, "claude", "project-kind", kind="project", newest=11.0)
    listed(conn, "claude", "seat-kind", kind="seat", newest=12.0)
    text = listener.lists(event(cwd=shop))
    for sid in ("unknown-kind", "project-kind", "seat-kind"):
        assert f"- {sid} " in text


def test_an_empty_list_is_left_out_with_its_heading(comms, conn, shop):
    comms(FakeComms("planner"))
    assert listener.lists(event(cwd=shop)) is None
    listed(conn, "claude", "p", newest=10.0)
    assert listener.lists(event(cwd=shop)) == f"recent sessions in shop:\n- p  {when(10.0)}  claude"
    table.write_late_identity(conn, "claude", "p", "builder")
    listed(conn, "claude", "own", identity="planner", label="far", newest=11.0)
    assert listener.lists(event(cwd=shop)) == (
        f"your recent sessions (planner):\n- own  {when(11.0)}\n"
        f"recent sessions in shop:\n- p  {when(10.0)}  builder")


def test_a_session_with_no_label_gets_no_project_list(no_comms, conn):
    listed(conn, "claude", "p", label=None)
    assert listener.lists(event(cwd=os.sep)) is None


# -- settled kinds ----------------------------------------------------------------------------

def test_a_session_the_listener_wrote_but_no_child_read_is_in_no_list(comms, conn, shop):
    comms(FakeComms("planner"))
    # Another session started beside this one: its own listener wrote its row first.
    assert listener.lists(event(cwd=shop, sid="scratch-launch")) is None
    assert table.row(conn, "claude", "scratch-launch").identity == "planner"
    assert listener.lists(event(cwd=shop, sid="me")) is None
    # ... and after this session's queries, the same.
    assert listener.lists(event(cwd=shop, sid="later")) is None
    assert listener.lists(event(cwd=shop, sid="me")) is None


def test_after_a_child_read_none_is_listed_and_scratch_is_not(no_comms, conn, shop):
    listed(conn, "claude", "none-kind", kind=None, newest=10.0)
    listed(conn, "claude", "scratch-kind", kind="scratch", newest=11.0)
    listed(conn, "claude", "unsettled", kind=None, settled=False, newest=12.0)
    text = listener.lists(event(cwd=shop))
    assert "none-kind" in text
    assert "scratch-kind" not in text and "unsettled" not in text


# -- the deadline -------------------------------------------------------------------------------

def test_the_row_is_written_before_the_identity_is_asked(comms, conn, shop):
    seen = []

    def look():
        c = state.connect()
        seen.append(table.row(c, "claude", "me"))
        c.close()

    comms(FakeComms("planner", check=look))
    listener.lists(event(cwd=shop))
    assert seen and seen[0] is not None and seen[0].label == "shop"
    assert seen[0].identity is None
    assert table.row(conn, "claude", "me").identity == "planner"


@pytest.mark.timed
def test_a_late_identity_keeps_the_project_list_and_is_written_when_it_comes(
        comms, conn, shop):
    fake = comms(FakeComms("planner", delay=0.8))
    listed(conn, "claude", "own", identity="planner", label="far", newest=11.0)
    listed(conn, "codex", "p", newest=10.0)
    began = time.monotonic()
    text = listener.lists(event(cwd=shop, env={"MOXI_COMMS_CLAIM": "x"}))
    took = time.monotonic() - began
    assert took < listener.DEADLINE
    assert text == f"recent sessions in shop:\n- p  {when(10.0)}  codex"
    assert fake.asked == [{"MOXI_COMMS_CLAIM": "x"}]
    wait_for(lambda: table.row(conn, "claude", "me").identity == "planner", timeout=5)


@pytest.mark.timed
def test_a_claim_socket_that_never_answers_costs_only_the_agent_list(comms, conn, shop):
    hung = threading.Event()
    comms(FakeComms("planner", block=hung))
    listed(conn, "codex", "p", newest=10.0)
    began = time.monotonic()
    text = listener.lists(event(cwd=shop))
    assert time.monotonic() - began < listener.DEADLINE
    assert text == f"recent sessions in shop:\n- p  {when(10.0)}  codex"
    hung.set()
    time.sleep(0.1)
    assert table.row(conn, "claude", "me").identity is None


@pytest.mark.timed
def test_a_held_state_store_makes_lists_skip_its_write_within_its_cap(no_comms, conn,
                                                                      shop):
    listed(conn, "codex", "p", newest=10.0)
    holder = state.connect()
    holder.execute("BEGIN IMMEDIATE")
    try:
        began = time.monotonic()
        text = listener.lists(event(cwd=shop))
        took = time.monotonic() - began
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert table.row(conn, "claude", "me") is None
    # The write waited its 250 ms; the reads still ran beside the held write lock.
    assert took < listener.ROW_WAIT + listener.QUERY_WAIT + 0.2
    assert text == f"recent sessions in shop:\n- p  {when(10.0)}  codex"


@pytest.mark.timed
def test_a_query_past_its_bound_is_interrupted_and_leaves_its_list_out(monkeypatch, comms,
                                                                      conn, shop):
    comms(FakeComms("planner"))
    listed(conn, "claude", "own", identity="planner", label="far", newest=11.0)
    listed(conn, "codex", "p", newest=10.0)

    def endless(c, *a, **k):
        return c.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) "
                         "SELECT count(*) FROM n").fetchall()

    monkeypatch.setattr(table, "agent_sessions", endless)
    began = time.monotonic()
    text = listener.lists(event(cwd=shop))
    took = time.monotonic() - began
    assert listener.QUERY_WAIT <= took < listener.DEADLINE
    assert text == f"recent sessions in shop:\n- p  {when(10.0)}  codex"
    assert table.row(conn, "claude", "me").identity == "planner"


@pytest.mark.timed
def test_lists_answers_inside_its_deadline_when_every_step_reaches_its_cap(
        monkeypatch, comms, conn, shop):
    hung = threading.Event()
    comms(FakeComms("planner", block=hung))
    holder = state.connect()
    holder.execute("BEGIN IMMEDIATE")

    def endless(c, *a, **k):
        return c.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) "
                         "SELECT count(*) FROM n").fetchall()

    monkeypatch.setattr(table, "project_sessions", endless)
    try:
        began = time.monotonic()
        text = listener.lists(event(cwd=shop))
        took = time.monotonic() - began
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        hung.set()
    assert text is None
    assert took < listener.DEADLINE + 0.1


def test_the_start_line_needs_nothing_lists_reads(monkeypatch, shop):
    def broken(*a, **k):
        raise RuntimeError("the store is broken")

    monkeypatch.setattr(state, "connect", broken)
    assert listener.start(event(cwd=shop)) == LINE_WITH_LABEL
    assert listener.lists(event(cwd=shop)) is None


# -- the late identity --------------------------------------------------------------------------

def test_a_late_identity_writes_only_its_own_existing_empty_row_and_never_empty(conn):
    me = ("claude", "me")
    # No row: nothing is made.
    assert listener.write_late_identity(me, "planner") is False
    assert table.row(conn, *me) is None
    # A row with no identity takes it; another session's row is untouched.
    table.write_start(conn, "claude", "me", "shop")
    table.write_start(conn, "claude", "other", "shop")
    assert listener.write_late_identity(me, "planner") is True
    assert table.row(conn, *me).identity == "planner"
    assert table.row(conn, "claude", "other").identity is None
    # A row that holds an identity keeps it; an empty answer writes nothing.
    assert listener.write_late_identity(me, "builder") is False
    assert listener.write_late_identity(("claude", "other"), "  ") is False
    assert listener.write_late_identity(("claude", "other"), None) is False
    assert table.row(conn, *me).identity == "planner"
    assert table.row(conn, "claude", "other").identity is None

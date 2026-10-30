"""The OpenCode clock: a session's rows in `opencode.db`, the next pair first, free models,
and `fresh` on every witness of a new session.

Every database is written by sessions' builder (`OpenCodeDb`, through
`moxi_sessions.api.testing`) in OpenCode 1.18.34's columns, not copied from a live one;
every read is against tmp paths. One builder per database: its ids grow in the order rows
are added, as OpenCode's do, so a test adds each session's rows in time order.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from moxi_cache import api, opencode, table
from moxi_sessions.api import testing

NOW = 1_791_300_000.0                    # a fixed "now" for the pure reads
SID_B = "ses_0000000000000TestSessionB"  # a session no database here holds

OpenCodeDb = testing.OpenCodeDb


# ================================================================ opencode fixtures

BUILD_NULL = {"agent": "build", "model": None}
FREE_AGENT = {"agent": "build", "model": "opencode/big-pickle"}


def oc_db(tmp_path, *steps, wal=True, totals=None, **session):
    """A builder for `<tmp_path>/opencode/opencode.db` holding one session, with `steps`
    applied to it (see `add`). Returns the session's handle; its builder is `.db`.
    `totals` sets every token and cost total of the session row to that number."""
    db = OpenCodeDb(tmp_path / "opencode", wal=wal)
    if totals is not None:
        session["totals"] = dict.fromkeys(opencode.TOTALS, totals)
    return add(db.session(**session), *steps)


def add(s, *steps):
    """Apply each step (`user`, `request`) to the session `s`, in order."""
    for step in steps:
        step(s)
    return s


def user(t, *, model=("opencode", "big-pickle"), raw_model=...):
    """A user message at `t` with one text part, as a step. `model` is the pair it asks
    for (None for none); `raw_model` writes its `model` key as given."""
    return lambda s: s.user("hello", model=model, raw_model=raw_model, at=t)


def request(t, provider, model, *, steps=((0.0, 3.0),), agent="build", summary=None,
            created=None, user=True):
    """A user message and its assistant reply whose first step starts at `t`, each step
    `(start offset, end offset)` from `t` (end None for a step still open), as a step.
    The reply is created at `created` (default `t`)."""
    def step(s):
        if user:
            s.user("hello", model=(provider, model), at=(created or t) - 0.5)
        reply = s.assistant(model=(provider, model), agent=agent, summary=summary,
                            at=created or t)
        for start, end in steps:
            reply.step_start(at=t + start)
            if end is not None:
                reply.step_finish(at=t + end)
        return reply
    return step


def change(s, sql):
    """Write the session's database, then run `sql` on it: a column or table taken away."""
    path = s.db.write()
    with sqlite3.connect(path) as c:
        c.execute(sql)
    c.close()
    return path


def oc_read(s, sid=..., *, turned=True, new_session=False, agent_model=BUILD_NULL,
            now=NOW, source=...):
    """`s`'s clock: its database written first, unless `source` names the path to read."""
    path = str(s.db.write()) if source is ... else source
    return api.read("opencode", s.id if sid is ... else sid, cache_source=path,
                    turned=turned, new_session=new_session, agent_model=agent_model,
                    now=now)


# ================================================================ the opencode reader

def test_anthropic_counts_5_minutes(tmp_path):
    s = oc_db(tmp_path, request(NOW - 299, "anthropic", "claude-sonnet-4"),
              model=("anthropic", "claude-sonnet-4"))
    clock = oc_read(s)
    assert (clock.kind, clock.ttl_seconds, clock.key) == (
        "warm", 300, "opencode anthropic/claude-sonnet-4")
    s2 = oc_db(tmp_path / "b", request(NOW - 300, "anthropic", "claude-sonnet-4"),
               model=("anthropic", "claude-sonnet-4"))
    assert oc_read(s2).kind == "cold"


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-6-astra", "gpt-5.6-terra",
                                   "gpt-5.6-luna"])
def test_openai_counts_30_minutes_and_no_drift_lowers_it(tmp_path, model):
    for age, kind in ((1799, "warm"), (1800, "cold")):
        s = oc_db(tmp_path / str(age), request(NOW - age, "openai", model),
                  model=("openai", model))
        clock = oc_read(s)
        assert (clock.kind, clock.ttl_seconds) == (kind, 1800)


@pytest.mark.parametrize("model", ["big-pickle", "space-bunny-free"])
@pytest.mark.parametrize("age", [0, 30 * 86400])
def test_a_free_next_pair_is_no_cache_at_any_age(tmp_path, model, age):
    s = oc_db(tmp_path, request(NOW - age, "opencode", model), model=("opencode", model))
    clock = oc_read(s)
    assert clock.kind == "no-cache" and clock.ttl_seconds is None
    assert clock.reason == (f"opencode/{model} is a free model, so a cold cache costs "
                            "nothing and is not counted")
    assert clock.key == f"opencode opencode/{model} (free)"


def test_an_unknown_provider_reads_no_cache(tmp_path):
    """No lifetime row for the pair: no clock, `no-cache`."""
    s = oc_db(tmp_path, request(NOW - 5, "deepseek", "deepseek-chat"),
              model=("deepseek", "deepseek-chat"))
    clock = oc_read(s)
    assert clock.kind == "no-cache" and clock.reason == opencode.no_row(
        "deepseek", "deepseek-chat")


def test_the_stamp_errs_old(tmp_path):
    claude_x = ("anthropic", "claude-x")
    # First step: the message's creation, not the step-start 3 s later.
    s = oc_db(tmp_path / "a", request(NOW - 297, *claude_x, created=NOW - 300),
              model=claude_x)
    assert oc_read(s).kind == "cold"
    # A later step reads from the step-finish before it.
    s = oc_db(tmp_path / "b", request(NOW - 1000, *claude_x,
                                      steps=((0, 10), (700, 710))),   # starts at NOW-300
              model=claude_x)
    clock = oc_read(s)
    assert clock.age_seconds == pytest.approx(990, abs=0.01)
    # A step that started 6 minutes ago and finished 2 minutes ago is cold on 300 s.
    s = oc_db(tmp_path / "c", request(NOW - 360, *claude_x, steps=((0, 240),)),
              model=claude_x)
    assert oc_read(s).kind == "cold"
    # A request in flight reads by its start.
    s = oc_db(tmp_path / "d", request(NOW - 100, *claude_x, steps=((0, None),)),
              model=claude_x)
    clock = oc_read(s)
    assert clock.kind == "warm" and clock.age_seconds == pytest.approx(100, abs=0.01)


def test_a_compaction_and_a_child_session_do_not_move_the_clock(tmp_path):
    claude_x = ("anthropic", "claude-x")
    s = oc_db(tmp_path, request(NOW - 400, *claude_x),
              request(NOW - 10, *claude_x, agent="compaction", user=False),
              request(NOW - 9, *claude_x, summary=True, user=False), model=claude_x)
    add(s.child(model=claude_x), request(NOW - 5, *claude_x))
    clock = oc_read(s)
    assert clock.kind == "cold" and clock.age_seconds == pytest.approx(400, abs=0.01)


def fresh_session(tmp_path, **kw):
    return oc_db(tmp_path, user(NOW - 5, model=("openai", "gpt-5.6-sol")),
                 model=("openai", "gpt-5.6-sol"), **kw)


def test_the_launchers_own_new_session_is_fresh(tmp_path):
    s = fresh_session(tmp_path)
    assert oc_read(s, turned=False).kind == "fresh"
    assert oc_read(s, turned=True).kind == "unknown"


@pytest.mark.parametrize("case", ["assistant-no-step", "bad-role", "bad-data",
                                  "file-part", "step-on-user", "totals", "version",
                                  "missing-column", "no-session", "missing-db",
                                  "unreadable-db", "locked-db", "no-part-table",
                                  "no-source"])
def test_each_witness_that_fails_is_unknown(tmp_path, case):
    if case == "missing-column":
        s = oc_db(tmp_path)
        change(s, "ALTER TABLE session DROP COLUMN tokens_cache_write")
    elif case == "no-part-table":
        s = oc_db(tmp_path, model=("openai", "gpt-5.6-sol"))
        change(s, "DROP TABLE part")
    else:
        # A writer's exclusive lock blocks a reader only outside WAL mode.
        s = fresh_session(tmp_path, wal=case != "locked-db",
                          totals=5 if case == "totals" else None,
                          version="1.18.35" if case == "version" else "1.18.34")
    if case == "assistant-no-step":
        s.message({"role": "assistant", "agent": "build", "providerID": "openai",
                   "modelID": "gpt-5.6-sol"}, at=NOW - 4)
    elif case == "bad-role":
        s.message({"role": "system"}, at=NOW - 4)
    elif case == "bad-data":
        s.message("{not json", at=NOW - 4)
    elif case == "file-part":
        user(NOW - 3)(s).part({"type": "file", "url": "x"}, at=NOW - 3)
    elif case == "step-on-user":
        user(NOW - 3)(s).part({"type": "step-start"}, at=NOW - 3)
    path = s.db.write()
    source = str(path)
    if case == "no-session":
        clock = oc_read(s, sid=SID_B, turned=False, source=source)
        assert clock.kind == "unknown" and "no session" in clock.reason
        return
    elif case == "missing-db":
        source = str(tmp_path / "nowhere.db")
    elif case == "unreadable-db":
        path.chmod(0)
    elif case == "no-source":
        source = None
    try:
        if case == "locked-db":
            holder = sqlite3.connect(path)
            holder.execute("BEGIN EXCLUSIVE")
            try:
                start = time.monotonic()
                clock = oc_read(s, turned=False, source=source)
                assert time.monotonic() - start < 2
            finally:
                holder.rollback()
                holder.close()
        else:
            clock = oc_read(s, turned=False, source=source)
    finally:
        if case == "unreadable-db":
            path.chmod(0o600)
    assert clock.kind == "unknown", (case, clock)
    if case == "assistant-no-step":
        assert clock.reason == opencode.UNSHAPED_ROWS


def test_the_session_rows_step_needs_only_the_session_tables_shape(tmp_path):
    """A free next pair from the session row's model needs the shape of `session`, not
    of `message` or `part`; the full read needs all three."""
    s = oc_db(tmp_path / "free", model=("opencode", "big-pickle"))
    change(s, "DROP TABLE part")
    assert oc_read(s).kind == "no-cache"
    s = oc_db(tmp_path / "paid", model=("anthropic", "claude-A"))
    change(s, "DROP TABLE part")
    clock = oc_read(s)
    assert clock.kind == "unknown" and "no `part` table" in clock.reason


def test_the_pairs_under_the_wildcard_row(tmp_path):
    def read(**kw):
        return oc_read(s, **kw)
    # A last request on claude-A with the session's model now claude-B: cold.
    s = oc_db(tmp_path / "a", request(NOW - 10, "anthropic", "claude-A"),
              model=("anthropic", "claude-B"))
    assert read().reason == opencode.CHANGED
    # A provider change: cold.
    s = oc_db(tmp_path / "b", request(NOW - 10, "anthropic", "claude-A"),
              model=("bedrock", "claude-A"))
    assert read().reason == opencode.CHANGED
    # The default agent's model differs from the session's: cold.
    s = oc_db(tmp_path / "c", request(NOW - 10, "anthropic", "claude-A"),
              model=("anthropic", "claude-A"))
    assert read().kind == "warm"
    assert read(agent_model={"agent": "build", "model": "anthropic/claude-B"}).reason == \
        opencode.CHANGED
    # An agent with a null model falls through to the session's model.
    assert read(agent_model={"agent": "plan", "model": None}).kind == "warm"
    # A default agent's model that does not parse: unknown, never the next step.
    clock = read(agent_model={"agent": "build", "model": "no-slash"})
    assert clock.kind == "unknown" and "does not parse" in clock.reason
    # No agent_model reported: unknown.
    clock = read(agent_model=api.MISSING)
    assert clock.kind == "unknown" and clock.reason == opencode.NO_AGENT
    # No session model and no user model: unknown (no server default is read).
    s = oc_db(tmp_path / "d", request(NOW - 10, "anthropic", "claude-A", user=False),
              model=None)
    assert read().kind == "unknown"
    # A session model that will not parse, and a latest user model that will not.
    s = oc_db(tmp_path / "e", request(NOW - 10, "anthropic", "claude-A"),
              raw_model="{not json")
    assert read().kind == "unknown"
    s = oc_db(tmp_path / "f", request(NOW - 10, "anthropic", "claude-A"),
              user(NOW - 5, raw_model="anthropic/claude-A"), model=None)
    assert read().kind == "unknown"
    # The latest user model, when the session has none, gives the next pair.
    s = oc_db(tmp_path / "g", request(NOW - 10, "anthropic", "claude-A"), model=None)
    assert read().kind == "warm"
    # A last assistant message with no providerID: unknown.
    s = oc_db(tmp_path / "h", model=("anthropic", "claude-A"))
    s.message({"role": "assistant", "agent": "build", "modelID": "claude-A"},
              at=NOW - 10).part({"type": "step-start"}, at=NOW - 9)
    clock = read()
    assert clock.kind == "unknown" and clock.reason == opencode.NO_PAIR


def test_a_revert_pending_is_unknown_with_and_without_a_request(tmp_path):
    """The `revert` value is the one a real revert on OpenCode 1.18.34 wrote on
    2026-10-06 (two prompts on opencode/big-pickle in an isolated data dir, then
    `POST /session/:id/revert` at the second user message): `{"messageID": ...}`, the
    later messages left in the db until the next prompt.

    The same run checked whether a revert's cleanup lowers the session's totals: it
    does. After the revert the totals were unchanged (input 4,730, cache read 20,224);
    after the next prompt, which cleans up before it runs, input was 4,666, lower than
    before though that prompt's own request had been added. So the session row's totals
    are not a running maximum, and a fully reverted session could show low totals; the
    fresh check still needs `turned` false, which no session that has had a turn has."""
    revert = json.dumps({"messageID": "msg_000000000000000TestRevert"},
                        separators=(",", ":"))
    s = oc_db(tmp_path / "a", request(NOW - 10, "anthropic", "claude-A"),
              model=("anthropic", "claude-A"), revert=revert)
    clock = oc_read(s)
    assert clock.kind == "unknown" and clock.reason == opencode.REVERT
    s = fresh_session(tmp_path / "b", revert=revert)
    assert oc_read(s, turned=False).reason == opencode.REVERT

# ====================================================================== free models

def free_db(tmp_path, *, last=("opencode", "big-pickle"), age=600, model=..., **session):
    s = oc_db(tmp_path, model=("opencode", "big-pickle") if model is ... else model,
              **session)
    if last is not None:
        add(s, request(NOW - age, *last))
    return s


@pytest.mark.parametrize("case", [
    "last-big-pickle", "last-space-bunny", "change-onto-free", "no-request-turned",
    "no-request-fresh", "revert-by-session-row", "unshaped-row-by-session-row",
    "agent-no-source", "agent-missing-db", "agent-bad-db", "no-id-new-session",
    "no-id-no-new-session"])
def test_a_known_free_next_pair_is_no_cache_at_any_age(tmp_path, case):
    agent = BUILD_NULL
    source = ...
    sid = ...
    kw = {}
    if case == "last-big-pickle":
        s = free_db(tmp_path, age=40 * 86400)
    elif case == "last-space-bunny":
        s = free_db(tmp_path, last=("opencode", "space-bunny-free"))
    elif case == "change-onto-free":
        s = free_db(tmp_path, last=("anthropic", "claude-A"))
    elif case == "no-request-turned":
        s = free_db(tmp_path, last=None)
        kw["turned"] = True
    elif case == "no-request-fresh":
        s = free_db(tmp_path, last=None)
        kw["turned"] = False
    elif case == "revert-by-session-row":
        s = free_db(tmp_path, revert=json.dumps({"messageID": "msg_x"}))
    elif case == "unshaped-row-by-session-row":
        s = free_db(tmp_path)
        s.message({"role": "tool"}, at=NOW - 1)
    else:
        s = free_db(tmp_path)
        agent = FREE_AGENT
        if case == "agent-no-source":
            source = None
        elif case == "agent-missing-db":
            source = str(tmp_path / "gone.db")
        elif case == "agent-bad-db":
            bad = tmp_path / "bad.db"
            bad.write_text("this is not sqlite")
            source = str(bad)
        elif case == "no-id-new-session":
            sid, kw["new_session"] = None, True
        elif case == "no-id-no-new-session":
            sid = None
    clock = oc_read(s, sid, agent_model=agent, source=source, **kw)
    assert clock.kind == "no-cache", (case, clock)
    assert clock.key in ("opencode opencode/big-pickle (free)",)


@pytest.mark.parametrize("case", ["no-agent-model", "null-model-no-db", "bad-session-model",
                                  "user-model-revert", "user-model-unshaped", "no-table"])
def test_a_free_model_that_cannot_be_read_as_next_is_unknown(tmp_path, case):
    agent, source = BUILD_NULL, ...
    if case == "no-agent-model":
        s, agent = free_db(tmp_path), api.MISSING
    elif case == "null-model-no-db":
        s, source = free_db(tmp_path), None
    elif case == "bad-session-model":
        s = free_db(tmp_path, model=None, raw_model="{nope")
    elif case in ("user-model-revert", "user-model-unshaped"):
        s = free_db(tmp_path, model=None,
                    revert=json.dumps({"messageID": "m"}) if case == "user-model-revert"
                    else None)
        if case == "user-model-unshaped":
            s.message({"role": "tool"}, at=NOW - 1)
    else:
        s = free_db(tmp_path)
        agent = FREE_AGENT
        table.TABLE_PATH.write_text("not = [toml")
    clock = oc_read(s, agent_model=agent, source=source)
    assert clock.kind == "unknown", (case, clock)


def test_a_paid_next_pair_after_a_free_one_reads_by_the_pair_rule(tmp_path):
    s = free_db(tmp_path, model=("anthropic", "claude-A"))
    clock = oc_read(s)
    assert clock.kind == "cold" and clock.reason == opencode.CHANGED
    s = free_db(tmp_path / "b", last=("opencode", "gpt-5.5"), model=("opencode", "gpt-5.5"))
    clock = oc_read(s)
    assert clock.kind == "no-cache" and clock.reason == opencode.no_row("opencode",
                                                                         "gpt-5.5")


def test_the_rule_reads_only_the_next_pair(tmp_path, monkeypatch):
    """Mutation guards: a free next pair never needs the last request, and an unknown
    next pair never falls through to the last one."""
    s = free_db(tmp_path, last=("opencode", "big-pickle"), model=None)
    add(s, user(NOW - 1, model=("anthropic", "claude-A")))
    clock = oc_read(s)
    assert clock.kind == "cold", "the next pair is the latest user model, not the last"
    s = free_db(tmp_path / "b", last=("opencode", "big-pickle"))
    assert oc_read(s, agent_model=api.MISSING).kind == "unknown"


# ============================================================================ drift


def test_a_stamp_in_the_future_is_unknown(tmp_path):
    s = oc_db(tmp_path, request(NOW + 6, "openai", "gpt-5.6-sol"),
              model=("opencode", "gpt-5.6-sol"))
    clock = oc_read(s, agent_model=BUILD_NULL)
    assert (clock.kind, clock.code) == ("unknown", "future-stamp")
    assert "stamped in the future" in clock.reason


def test_every_unknown_and_no_cache_carries_a_code(tmp_path):
    """A caller branches on the code, never on the reason's words."""
    s = oc_db(tmp_path, request(NOW - 5, "deepseek", "deepseek-chat"),
              model=("deepseek", "deepseek-chat"))
    assert oc_read(s).code == "no-row"
    assert oc_read(s, agent_model=api.MISSING).code == "no-pair"
    assert oc_read(s, source=None).code in ("no-source", "no-pair")
    assert oc_read(s, sid=SID_B, turned=False).code == "no-record"
    free = oc_db(tmp_path / "free")
    assert oc_read(free, agent_model=FREE_AGENT).code == "free"

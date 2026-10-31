"""The listener group and its asking."""

from __future__ import annotations

import dataclasses
import os
import time
import types

import pytest

import moxi_turns as turns
from fake_harness import FakeHarness
import listener_targets
from moxi_turns import listeners


class Entry:
    def __init__(self, name, rows):
        self.name = name
        self._rows = rows

    def load(self):
        if isinstance(self._rows, Exception):
            raise self._rows
        return types.SimpleNamespace(LISTENERS=self._rows)


def registry(*entries, order=("comms", "alpha", "zeta"), limit=0):
    return listeners.load(entries, order=order, limit=limit)


def event(name="ready", **kw):
    return turns.SessionEvent(event=name, harness="fake", session_id="s-1",
                              env={"HOME": "/h"}, **kw)


@pytest.fixture(autouse=True)
def clear_calls():
    listener_targets.CALLS.clear()


def test_texts_join_in_list_order_and_each_listener_gets_its_own_env():
    reg = registry(Entry("zeta", [("start", "listener_targets:comms")]),
                   Entry("alpha", [(("start", "prompt"), "listener_targets:cache")]),
                   order=("zeta", "alpha"))
    assert [ls.module for ls in reg.listeners] == ["alpha", "zeta"]
    text = listeners.ask(reg, event("ready", source="startup"), 2.0)
    assert text == "comms at start\n\ncache context"
    assert ("cache", "start", None) in listener_targets.CALLS
    assert listeners.ask(reg, event("prompt"), 2.0) == "cache context"


def test_a_late_broken_or_odd_answer_counts_as_none():
    reg = registry(Entry("a", [("prompt", "listener_targets:slow")]),
                   Entry("b", [("prompt", "listener_targets:broken")]),
                   Entry("c", [("prompt", "listener_targets:not_text")]),
                   Entry("d", [("prompt", "listener_targets:silent")]),
                   Entry("e", [("prompt", "listener_targets:cache")]),
                   Entry("f", [("prompt", "listener_targets:missing")]))
    t0 = time.monotonic()
    assert listeners.ask(reg, event("prompt"), 0.2) == "cache context"
    assert time.monotonic() - t0 < 0.6          # one bound for all, not one each
    assert listeners.ask(registry(Entry("d", [("prompt", "listener_targets:silent")])),
                         event("prompt"), 0.2) is None


def test_turn_and_gone_answers_are_ignored_and_do_not_wait():
    reg = registry(Entry("a", [(("turn", "gone"), "listener_targets:slow")]),
                   Entry("b", [("turn", "listener_targets:comms")]))
    t0 = time.monotonic()
    assert listeners.ask(reg, event("turn", turn_id="t1", outcome="answer"), 2.0) is None
    assert time.monotonic() - t0 < 0.5
    t0 = time.monotonic()
    listeners.ask(reg, event("gone", reason="bye"), 0.3, wait=True)
    assert 0.25 < time.monotonic() - t0 < 0.9


@pytest.mark.parametrize("rows, why", [
    (None, "LISTENERS"),
    ([("ready",)], "not (events, target)"),
    ([("tool", "x.y:z")], "kept for a later turns"),
    ([(("start", "tool"), "x.y:z")], "kept for a later turns"),
    ([("prompt", "x.y:z", ["floor"])], "not a table of options"),
    ([("prompt", "x.y:z", {"floor": 1})], "has floor 1"),
    ([("prompt", "x.y:z", {"floor": "no-colon"})], "has floor"),
    ([("finished", "x.y:z")], "names events"),
    ([("ready", "no-colon")], "has target"),
    (ImportError("gone"), "does not load"),
])
def test_a_bad_registration_is_broken_and_costs_only_its_module(rows, why):
    reg = registry(Entry("bad", rows), Entry("good", [("ready", "listener_targets:cache")]))
    assert [b.module for b in reg.broken] == ["bad"] and why in reg.broken[0].reason
    assert [ls.module for ls in reg.listeners] == ["good"]


def test_two_registrations_under_one_name_are_both_off():
    reg = registry(Entry("comms", [("ready", "listener_targets:comms")]),
                   Entry("comms", [("ready", "listener_targets:cache")]))
    assert reg.listeners == () and "uninstall all but one" in reg.broken[0].reason


def test_the_endpoint_asks_at_ready_and_prompt_and_tells_turn_and_gone(make_endpoint):
    reg = registry(Entry("comms", [(("start", "prompt", "turn", "gone"),
                                    "listener_targets:comms")]))
    env = dict(os.environ, SESSION_STAMP="x")
    ep, fake = make_endpoint(registry=reg, env=env)
    assert ep.ready("startup", "s-1") == "comms at start"
    assert ep.prompt() == "comms at prompt"
    fake.start_turn()
    fake.end_turn("aborted")
    ep.gone("the session ended")
    time.sleep(0.1)
    assert [c[:4] for c in listener_targets.CALLS] == [
        ("comms", "start", "s-1", "startup"), ("comms", "prompt", "s-1", None),
        ("comms", "turn", "s-1", None), ("comms", "gone", "s-1", None)]


def test_the_environment_metadata_group_loads(monkeypatch):
    # With nothing registered in this environment, the group is empty and nothing breaks.
    reg = listeners.load()
    assert all(b.module for b in reg.broken)
    assert listeners.GROUP == "moxi.turns.listeners"


# -- the start list ------------------------------------------------------------------------

def _answer(text):
    return types.SimpleNamespace(answer=lambda event: text)


def _module(name, rows):
    return Entry(name, rows)


def test_the_default_order_is_the_schemas_and_an_uninstalled_module_is_skipped():
    assert listeners.default_order() == ("launcher", "comms", "notes", "memory")
    reg = listeners.load([Entry("comms", [("start", "listener_targets:comms")])], order=None,
                         limit=0)
    plan = listeners.plan(reg, present=lambda name: name == "comms")
    assert [e.entry for e in plan.entries] == ["comms"]
    assert dict(plan.skipped) == {"launcher": "not installed", "notes": "not installed",
                                  "memory": "not installed"}
    assert listeners.ask(reg, event("ready", source="startup"), 2.0) == "comms at start"


def test_an_off_list_start_row_adds_nothing_and_ready_rows_are_still_told():
    reg = registry(Entry("comms", [("start", "listener_targets:comms")]),
                   Entry("other", [("start", "listener_targets:cache")]),
                   Entry("watcher", [("ready", "listener_targets:comms")]), order=("comms",))
    assert listeners.ask(reg, event("ready", source="startup"), 2.0) == "comms at start"
    time.sleep(0.1)
    assert ("comms", "ready", "s-1", "startup") in listener_targets.CALLS
    assert not any(c[0] == "cache" for c in listener_targets.CALLS)
    assert listeners.plan(reg).off_list == ("other",)


def test_a_module_with_several_start_rows_gets_one_share_joined_in_declared_order():
    reg = registry(Entry("launcher", [("start", "listener_targets:silent"),
                                      ("start", "listener_targets:cache"),
                                      ("ready", "listener_targets:comms"),
                                      ("start", "listener_targets:comms")]),
                   order=("launcher",))
    assert listeners.ask(reg, event("start"), 2.0) == "cache context\n\ncomms at start"
    assert len(listeners.plan(reg).entries) == 1


def test_file_entries_are_read_and_a_missing_one_adds_nothing(tmp_path, caplog):
    brief = tmp_path / "brief.md"
    brief.write_text("the brief")
    missing = str(tmp_path / "gone.md")
    reg = registry(Entry("comms", [("start", "listener_targets:comms")]),
                   order=(str(brief), missing, "comms", "relative/file.md"))
    for _ in range(2):
        assert listeners.ask(reg, event("start"), 2.0) == "the brief\n\ncomms at start"
    logged = [r.message for r in caplog.records if "adds nothing" in r.message]
    assert len(logged) == 2                     # each bad entry once per endpoint
    assert dict(listeners.plan(reg).skipped) == {
        missing: "does not exist",
        "relative/file.md": "not an absolute path; write it from / or ~"}


def test_shares_pass_on_and_a_long_answer_is_cut_on_a_line_with_a_last_line():
    long = "\n".join(f"line {i:03d} " + "x" * 30 for i in range(100))
    out = listeners.share_out([("a", "short"), ("b", long), ("c", None)], 300)
    assert out.startswith("short\n\nline 000")
    assert len(out) <= 300
    last = out.splitlines()[-1]
    assert last.startswith("[turns: b cut at ") and last.endswith(" characters]")
    kept = out.split("\n\n", 1)[1].rsplit("\n", 1)[0]
    assert kept.endswith("x") and f"cut at {len(kept)} characters" in last
    # Equal shares when every answer is long.
    out = listeners.share_out([("a", long), ("b", long)], 400)
    assert out.count("[turns: ") == 2 and len(out) <= 400


def test_the_limit_is_clamped_to_the_harness_ceiling():
    long = "y" * 20_000
    reg = registry(Entry("comms", [("start", "listener_targets:big")]), order=("comms",),
                   limit=50_000)
    listener_targets.BIG = long
    for harness, ceiling in (("claude", 9500), ("codex", 9000), ("fake", 9000)):
        text = listeners.ask(reg, dataclasses.replace(event("start"), harness=harness), 2.0)
        assert len(text) <= ceiling and text.endswith("characters]")
    reg = registry(Entry("comms", [("start", "listener_targets:big")]), order=("comms",),
                   limit=100)
    assert len(listeners.ask(reg, event("start"), 2.0)) <= 100


def test_the_after_tool_usage_and_codex_limits_events_load():
    reg = registry(Entry("usage", [(("after-tool", "usage", "codex_limits"),
                                    "listener_targets:silent")]))
    assert reg.broken == () and reg.notices == ()
    assert [ls.module for ls in reg.naming("after-tool")] == ["usage"]
    assert [ls.module for ls in reg.naming("codex_limits")] == ["usage"]
    assert listeners.RESERVED == ("tool",)


def test_a_two_item_row_has_no_floor_and_a_third_item_carries_one():
    reg = registry(Entry("comms", [("start", "listener_targets:comms")]),
                   Entry("usage", [("after-tool", "listener_targets:silent",
                                    {"floor": "listener_targets:big"}),
                                   ("usage", "listener_targets:silent", {}),
                                   ("turn", "listener_targets:silent", None)]))
    by = {(ls.module, ls.target, tuple(sorted(ls.events))): ls.floor
          for ls in reg.listeners}
    assert by == {("comms", "listener_targets:comms", ("start",)): None,
                  ("usage", "listener_targets:silent", ("after-tool",)):
                      "listener_targets:big",
                  ("usage", "listener_targets:silent", ("usage",)): None,
                  ("usage", "listener_targets:silent", ("turn",)): None}
    assert reg.notices == () and reg.broken == ()


def test_an_unknown_option_is_ignored_and_named():
    reg = registry(Entry("usage", [("after-tool", "listener_targets:silent",
                                    {"floor": "listener_targets:big", "switch": "x"})]))
    assert [ls.floor for ls in reg.listeners] == ["listener_targets:big"]
    assert [n.module for n in reg.notices] == ["usage"]
    assert "doesn't know option 'switch'" in reg.notices[0].reason


def test_a_row_longer_than_three_items_loads_its_first_three_with_a_notice():
    reg = registry(Entry("comms", [("start", "listener_targets:comms",
                                    {"floor": "listener_targets:big"}, "later")]),
                   order=("comms",))
    assert [(ls.target, ls.floor) for ls in reg.listeners] == [
        ("listener_targets:comms", "listener_targets:big")]
    assert reg.notices and "older than the row" in reg.notices[0].reason
    assert "first three items" in reg.notices[0].reason


def test_a_listener_written_for_the_first_events_reads_the_longer_event():
    # Every field slot 8 adds trails with a default, so the old construction still works
    # and the new fields read None.
    ev = event("prompt")
    assert (ev.cwd, ev.text, ev.origin, ev.agent_id, ev.record_path, ev.usage,
            ev.received_at, ev.payload, ev.kind, ev.is_error, ev.point, ev.tool,
            ev.tool_use_id) == (None,) * 13
    names = [f.name for f in dataclasses.fields(turns.SessionEvent)]
    assert names[:8] == ["event", "harness", "session_id", "env", "source", "turn_id",
                         "outcome", "reason"]
    assert names[8:] == ["cwd", "agent_id", "tool", "tool_use_id", "is_error", "point",
                         "record_path", "text", "origin", "usage", "received_at",
                         "payload", "kind"]
    full = turns.SessionEvent("after-tool", "claude", "s-1", {}, None, None, None, None,
                              "/w", "a-1", "Bash", "tu-1", False, "after", "/r.jsonl")
    assert (full.cwd, full.agent_id, full.point, full.record_path) == (
        "/w", "a-1", "after", "/r.jsonl")

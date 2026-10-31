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


# -- after-tool rows and floors ------------------------------------------------------------

def _after_reg(*entries):
    return listeners.load(entries, order=(), limit=0)


def test_after_tool_rows_join_in_module_order_and_faults_add_nothing(caplog):
    reg = _after_reg(Entry("zeta", [("after-tool", "listener_targets:after")]),
                     Entry("alpha", [("after-tool", "listener_targets:after"),
                                     ("after-tool", "listener_targets:broken"),
                                     ("after-tool", "listener_targets:not_text"),
                                     ("after-tool", "listener_targets:slow")]))
    rows = listeners.after_rows(reg)
    assert list(rows) == ["listener_targets:after", "listener_targets:broken",
                          "listener_targets:not_text", "listener_targets:slow",
                          "listener_targets:after#2"]
    at = listeners.AfterTool(rows, 0.3)
    ev = event("after-tool", tool="Bash", point="after")
    t0 = time.monotonic()
    assert at.ask(ev) == "after Bash\n\nafter Bash"
    assert time.monotonic() - t0 < 0.6
    assert at.ask(ev, ["listener_targets:after#2", "nobody"]) == "after Bash"
    assert at.ask(ev, []) is None
    # Each kind of fault is logged once per endpoint.
    at.ask(ev, ["listener_targets:broken"])
    assert sum("raised" in r.getMessage() for r in caplog.records) == 1
    at.close()


def test_four_hung_calls_then_a_new_call_skips_the_row_at_once():
    listener_targets.HOLD.clear()
    rows = listeners.after_rows(_after_reg(Entry("u", [("after-tool",
                                                         "listener_targets:hang")])))
    at = listeners.AfterTool(rows, 0.2)
    ev = event("after-tool", tool="Bash")
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(8) as pool:
        # Eight at once: four are taken, four wait for a thread inside the bound and skip.
        results = list(pool.map(lambda _: at.ask(ev), range(8)))
    assert results == [None] * 8
    t0 = time.monotonic()
    assert at.ask(ev) is None                       # every thread hung: no wait
    assert time.monotonic() - t0 < 0.1
    listener_targets.HOLD.set()
    time.sleep(0.1)
    at.close()


def test_parallel_calls_each_reach_a_row():
    rows = listeners.after_rows(_after_reg(Entry("u", [("after-tool",
                                                         "listener_targets:after")])))
    at = listeners.AfterTool(rows, 0.5)
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(4) as pool:
        out = list(pool.map(lambda i: at.ask(event("after-tool", tool=f"T{i}",
                                                   agent_id=f"a{i}")), range(4)))
    assert out == [f"after T{i}" for i in range(4)]
    at.close()


def _floors(*rows, wait=0.3):
    reg = _after_reg(Entry("u", [("after-tool", "listener_targets:after", {"floor": f})
                                 if f else ("after-tool", "listener_targets:after")
                                 for f in rows]))
    sent = []
    return listeners.Floors(listeners.after_rows(reg), wait, send=sent.append), sent


def test_floors_read_at_refresh_and_after_a_call_and_are_sent_on_change_only():
    listener_targets.FLOOR["value"] = 40_000
    fl, sent = _floors("listener_targets:floor", None)
    assert fl.values() == {"listener_targets:after": 0, "listener_targets:after#2": 0}
    fl.refresh(event("ready"))
    assert sent == [{"listener_targets:after": 40_000, "listener_targets:after#2": 0}]
    fl.refresh(event("ready"))
    assert len(sent) == 1                           # nothing changed: nothing sent
    listener_targets.FLOOR["value"] = 0
    fl.after_call(fl.rows["listener_targets:after"], event("after-tool", agent_id="a1"))
    assert sent[-1]["listener_targets:after"] == 0
    fl.after_call(fl.rows["listener_targets:after#2"], event("turn"))  # no floor: no read
    assert len(sent) == 2


@pytest.mark.parametrize("target", ["listener_targets:floor_raises",
                                    "listener_targets:floor_text",
                                    "listener_targets:floor_slow",
                                    "listener_targets:missing"])
def test_a_floor_that_raises_overruns_or_answers_no_int_reads_0(target):
    listener_targets.FLOOR["value"] = 9
    fl, sent = _floors("listener_targets:floor", wait=0.2)
    fl.refresh(event("ready"))
    assert fl.values() == {"listener_targets:after": 9}
    bad = dataclasses.replace(fl.rows["listener_targets:after"], floor=target, _fn=None,
                              _floor_fn=None)
    fl2 = listeners.Floors({"r": bad}, 0.2, send=sent.append)
    fl2._values["r"] = 9
    t0 = time.monotonic()
    fl2.refresh(event("ready"))
    assert fl2.values() == {"r": 0}
    assert time.monotonic() - t0 < 0.5


def test_the_endpoint_asks_after_tool_rows_reads_floors_and_carries_cwd(make_endpoint):
    listener_targets.FLOOR["value"] = 5
    reg = _after_reg(Entry("usage", [("after-tool", "listener_targets:after",
                                      {"floor": "listener_targets:floor"}),
                                     (("prompt", "usage", "turn"), "listener_targets:seen")]))
    told = []

    class Fake(FakeHarness):
        record_path = "/p/S-1.jsonl"

        def floors_changed(self, values):
            told.append(values)

    ep, fake = make_endpoint(Fake(), registry=reg)
    assert ep.floors.values() == {"listener_targets:after": 0}
    ep.ready("startup", "s-1")
    assert told == [{"listener_targets:after": 5}]
    assert ep.after_tool("Bash", tool_use_id="u1", agent_id="a1", is_error=True) \
        == "after Bash"
    assert ("after", "Bash", "a1", True, "after", "/p/S-1.jsonl", fake.cwd) \
        in listener_targets.CALLS
    assert ep.after_tool("Bash", rows=[]) is None
    ep.prompt("hello", "composer")
    before = time.time()
    ep.usage({"rateLimits": [], "context": {"tokens": 3}})
    time.sleep(0.2)
    prompts = [c for c in listener_targets.CALLS if c[0] == "prompt"]
    assert prompts == [("prompt", fake.cwd, "hello", "composer", None, None)]
    (u,) = [c for c in listener_targets.CALLS if c[0] == "usage"]
    assert u[4] == {"rateLimits": [], "context": {"tokens": 3}} and u[5] >= before
    assert fake.cwd


def test_each_adapter_states_its_tool_points():
    from moxi_turns.claude_endpoint import ClaudeHarness
    from moxi_turns.codex import CodexHarness
    from moxi_turns.harness import Harness
    from moxi_turns.opencode import OpenCodeHarness

    assert Harness.tool_points() == {"pre": False, "after": False}
    assert Harness().after_tool_point() is None
    for cls in (ClaudeHarness, CodexHarness):
        assert cls.tool_points() == {"pre": True, "after": True}
    assert OpenCodeHarness.tool_points() == {"pre": False, "after": False}


def test_a_harness_with_a_pre_tool_point_and_no_after_tool_point_fires_at_pre(
        make_endpoint):
    """The pre-tool fallback: the same event fires before the call, `point` `pre`, and
    its text goes where the pre-tool point puts context."""
    listener_targets.CALLS.clear()
    reg = _after_reg(Entry("usage", [("after-tool", "listener_targets:after")]))

    class PreOnly(FakeHarness):
        @staticmethod
        def tool_points():
            return {"pre": True, "after": False}

        def pre_tool(self, tool):
            """The adapter's pre-tool hook: its context for the call."""
            point = self.after_tool_point()
            if point == "pre":
                return self.endpoint.after_tool(tool, tool_use_id="u1", point=point)
            return None

        def post_tool(self, tool):
            if self.after_tool_point() == "after":
                return self.endpoint.after_tool(tool, tool_use_id="u1")
            return None

    ep, fake = make_endpoint(PreOnly(), registry=reg)
    assert fake.after_tool_point() == "pre"
    assert fake.post_tool("Bash") is None
    assert fake.pre_tool("Bash") == "after Bash"
    assert [c[:5] for c in listener_targets.CALLS] == [("after", "Bash", None, None, "pre")]

    class Both(PreOnly):
        @staticmethod
        def tool_points():
            return {"pre": True, "after": True}

    listener_targets.CALLS.clear()
    ep, fake = make_endpoint(Both(), registry=reg)
    assert fake.after_tool_point() == "after"
    assert fake.pre_tool("Bash") is None
    assert fake.post_tool("Bash") == "after Bash"
    assert [c[:5] for c in listener_targets.CALLS] == [("after", "Bash", None, None, "after")]


# -- the start plan, read in public --------------------------------------------------------


@pytest.fixture
def installed(monkeypatch, turns_env):
    """The listener group `start_plan` reads, set by the test, and turns' config home."""
    from moxi import modules

    held = {"entries": []}
    monkeypatch.setattr(modules, "extensions", lambda group: list(held["entries"]))
    monkeypatch.setattr(modules, "present",
                        lambda name: name in {e.name for e in held["entries"]})
    return held


def test_the_start_plan_reads_the_list_shares_and_file_sizes_and_runs_nothing(
        installed, turns_env, tmp_path):
    from conftest import write_config

    brief = tmp_path / "brief.md"
    brief.write_text("b" * 1234)
    installed["entries"] = [Entry("comms", [("start", "listener_targets:comms")]),
                            Entry("launcher", [("start", "listener_targets:cache")]),
                            Entry("cache", [("start", "listener_targets:cache")])]
    write_config(turns_env, f'[start]\norder = ["launcher", "{brief}", "comms", "notes", '
                            f'"comms", "{tmp_path / "gone.md"}"]\nlimit = 0\n')
    got = turns.start_plan("claude")
    assert isinstance(got, turns.StartPlan)
    assert (got.harness, got.total, got.limit, got.ceiling) == ("claude", 9500, 0, 9500)
    # 9500 over three: the first holds 3166 and the remainder 2; a later one 3166 less
    # the separator before it.
    assert got.entries == (turns.StartEntry("launcher", "listener", 3168),
                           turns.StartEntry(str(brief), "file", 3164, 1234),
                           turns.StartEntry("comms", "listener", 3164))
    assert dict(got.skipped) == {
        "notes": "not installed", "comms": "listed twice; the first counts",
        str(tmp_path / "gone.md"): "does not exist",
        "cache": "registers a start row but is not on start.order"}
    assert listener_targets.CALLS == []                  # no listener ran
    # Each share is what the start itself holds for the entry: filled to its share,
    # every entry goes in whole, and one character more is cut.
    full = [(e.name, "z" * e.share) for e in got.entries]
    assert len(listeners.share_out(full, got.total)) == got.total
    assert "[turns: " not in listeners.share_out(full, got.total)
    full[1] = (full[1][0], full[1][1] + "z")
    assert f"[turns: {brief} cut at" in listeners.share_out(full, got.total)


def test_the_start_plan_limits_to_the_harness_and_reads_a_file_up_to_the_total(
        installed, turns_env, tmp_path):
    from conftest import write_config

    brief = tmp_path / "brief.md"
    brief.write_text("x" * 20_000)
    write_config(turns_env, f'[start]\norder = ["{brief}"]\nlimit = 50000\n')
    got = turns.start_plan("codex")
    assert (got.total, got.limit, got.ceiling) == (9000, 50000, 9000)
    assert got.entries == (turns.StartEntry(str(brief), "file", 9000, 9000),)
    write_config(turns_env, f'[start]\norder = ["{brief}"]\nlimit = 100\n')
    assert turns.start_plan("opencode").entries[0] == turns.StartEntry(str(brief), "file",
                                                                       100, 100)


def test_the_start_plan_reads_the_config_from_the_env_it_is_given(installed, turns_env,
                                                                    tmp_path):
    from conftest import write_config

    other = tmp_path / "other-cfg"
    (other / "turns").mkdir(parents=True)
    (other / "turns" / "config.toml").write_text('[start]\norder = []\nlimit = 10\n')
    write_config(turns_env, '[start]\norder = ["comms"]\n')
    installed["entries"] = [Entry("comms", [("start", "listener_targets:comms")])]
    assert [e.name for e in turns.start_plan("claude").entries] == ["comms"]
    got = turns.start_plan("claude", env=dict(os.environ, MOXI_TURNS_CONFIG_HOME=str(other)))
    assert got.entries == () and got.total == 10
    assert dict(got.skipped) == {"comms": "registers a start row but is not on start.order"}


def test_the_start_plan_refuses_a_harness_turns_does_not_know():
    with pytest.raises(ValueError, match="harness"):
        turns.start_plan("fake")

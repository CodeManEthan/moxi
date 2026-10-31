"""turns' checks for the front door's doctor, and the hook times behind its rows."""

from __future__ import annotations

import io
import json
import sys
import time
import types

import pytest

from conftest import write_config
from moxi import doctor as moxi_doctor
from moxi import modules
from moxi_turns import claude_leg, client, codex_hooks, doctor, hooktimes, listeners, module


class Entry:
    def __init__(self, name, rows):
        self.name = name
        self._rows = rows

    def load(self):
        if isinstance(self._rows, Exception):
            raise self._rows
        return types.SimpleNamespace(LISTENERS=self._rows)


def rows_by_name():
    out = {}
    for row in doctor.check():
        assert isinstance(row, moxi_doctor.Row) and row.module == "turns"
        out[row.name] = row
    return out


@pytest.fixture
def registry(monkeypatch):
    """The listener group the doctor reads, set by the test."""
    holder = {"entries": []}
    real = listeners.load

    def load(entries=None, **kw):
        return real(holder["entries"] if entries is None else entries, **kw)

    monkeypatch.setattr(listeners, "load", load)
    return holder


def test_the_announcement_declares_the_doctor_budget_and_store():
    modules.reset()
    found = modules.load("turns", interface=2)
    assert found.state == modules.PRESENT, found.reason
    assert module.DOCTOR == "moxi_turns.doctor:check"
    assert module.BUDGETS == {"hook_ms": 2.0}
    assert module.STORES["hook-times"]["path"] == hooktimes.FILE
    # The commands grant is stamped per launch and read by turns: declared in both.
    from moxi_turns.config import COMMANDS_ENV

    assert COMMANDS_ENV in found.session_env and COMMANDS_ENV in module.INTERNAL_ENV
    import moxi_turns

    assert not hasattr(moxi_turns, "check") and "check" not in moxi_turns.__all__
    assert "Row" not in moxi_turns.__all__


def test_the_config_row(make_endpoint, turns_env, registry):
    make_endpoint()
    rows = rows_by_name()
    assert rows["config"].state == "ok" and "stop_handler.wait_ms 2000" in rows["config"].detail
    assert rows["endpoints"].detail.startswith("1 live")
    assert any(name.startswith("endpoint fake pid") for name in rows)
    write_config(turns_env, "[stop_handler]\nwait_ms = 'soon'\n")
    assert rows_by_name()["config"].state == "fail"
    write_config(turns_env, "[hold]\nwait_ms = 3000\n")
    row = rows_by_name()["config"]
    assert row.state == "fail" and "stop_handler.wait_ms" in row.detail
    write_config(turns_env, "[commands]\nallow = ['clear', 'clera']\n")
    row = rows_by_name()["config"]
    assert row.state == "warn" and "clera" in row.detail and row.fix


def test_the_start_list_rows(turns_env, registry, tmp_path):
    registry["entries"] = [Entry("comms", [("start", "listener_targets:comms")]),
                           Entry("other", [("start", "listener_targets:cache")])]
    missing = tmp_path / "gone.md"
    write_config(turns_env, f"[start]\norder = ['comms', 'notes', '{missing}']\n")
    rows = rows_by_name()
    assert rows["start list"].state == "ok"
    assert rows["start list"].detail.startswith("asks comms; skipped ")
    assert "notes: not installed" in rows["start list"].detail
    off = rows["start list: other"]
    assert off.state == "warn" and off.fix == "add it to start.order"
    assert rows[f"start list: {missing}"].state == "warn"
    assert "does not exist" in rows[f"start list: {missing}"].detail


def test_the_listener_rows(turns_env, registry):
    registry["entries"] = [Entry("early", [("tool", "x.y:z")]),
                           Entry("later", [("start", "x.y:z", {"floor": "x.y:f",
                                                                 "later": 1})]),
                           Entry("fine", [(("ready", "prompt"), "x.y:z")])]
    rows = rows_by_name()
    assert rows["listener early"].state == "fail"
    assert "kept for a later turns" in rows["listener early"].detail
    assert rows["listener fine"].state == "ok"
    assert rows["listener fine"].detail == "registered for ready, prompt"
    later = [r for r in doctor.check() if r.name == "listener later"]
    assert {r.state for r in later} == {"ok", "warn"}


def test_a_v1_directory_is_named_and_left(turns_env, registry):
    old = client.runtime_dir() / "claude-old"
    old.mkdir(parents=True)
    (old / "hello.json").write_text(json.dumps({"v": 1}))
    rows = rows_by_name()
    row = rows[f"endpoints: v1 {old}"]
    assert row.state == "warn" and "reload that session's plugins" in row.fix
    assert old.exists()


def test_with_no_samples_each_harness_reads_none_recorded(turns_env, registry):
    rows = rows_by_name()
    for harness in ("claude", "codex"):
        assert rows[f"hook time: {harness}"].state == "ok"
        assert "no hook times recorded" in rows[f"hook time: {harness}"].detail
    assert not any("opencode" in name and "hook time" in name for name in rows)


# -- hook times ------------------------------------------------------------------------------

def samples(harness, event, values, at=None):
    at = time.time() if at is None else at
    return [hooktimes.Sample(harness, event, float(v), at) for v in values]


def test_rows_against_a_fake_floor():
    floors = []

    def floor():
        floors.append(1)
        return 10.0

    data = (samples("claude", "classic.Stop", range(1, 31))          # p90 27 > 20
            + samples("claude", "turn.start", [5] * 25)               # p90 5 <= 20
            + samples("claude", "classic.SessionStart", [100] * 3)    # too few
            + samples("codex", "Stop", [5] * 20))                     # +10 floor each: 15
    rows = {r.name: r for r in hooktimes.rows(data, budget=2.0, floor=floor)}
    stop = rows["hook time: claude classic.Stop"]
    assert stop.state == "warn" and "p50 15.0 ms, p90 27.0 ms over 30 samples" in stop.detail
    assert "floor 10.0 ms, budget 2 x floor = 20.0 ms" in stop.detail
    assert rows["hook time: claude turn.start"].state == "ok"
    few = rows["hook time: claude classic.SessionStart"]
    assert few.state == "ok" and few.detail == "too few samples (3) to judge"
    codex = rows["hook time: codex Stop"]
    assert codex.state == "ok" and "p90 15.0 ms" in codex.detail and "adds the floor" in codex.detail
    assert floors == [1]                                    # measured once, in the run


def test_the_floor_is_not_measured_without_enough_samples():
    def floor():
        raise AssertionError("measured")

    rows = hooktimes.rows(samples("claude", "turn.start", [1] * 5), budget=2.0, floor=floor)
    assert [r.state for r in rows] == ["ok", "ok"]


def test_the_floor_is_the_cores(monkeypatch):
    monkeypatch.setattr(moxi_doctor, "floor_ms", lambda: 12.5)
    assert hooktimes.floor_ms() == 12.5
    rows = hooktimes.rows(samples("claude", "turn.start", [30] * 20), budget=2.0)
    assert rows[0].state == "warn" and "floor 12.5 ms" in rows[0].detail


def test_a_recorder_writes_once_a_flush_and_the_reader_keeps_the_window(tmp_path):
    path = tmp_path / "hook-times.jsonl"
    rec = hooktimes.Recorder("claude", path=path)
    rec.add("turn.start", 3.5)
    rec.add("turn.start", -1)                  # dropped
    rec.add("turn.start", float("nan"))        # dropped
    rec.add("classic.Stop", 9, at=time.time() - 2 * 86400)
    assert not path.exists()
    assert rec.flush() == 2
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert lines[0]["harness"] == "claude" and lines[0]["event"] == "turn.start"
    got = hooktimes.read(path)
    assert [(s.event, s.ms) for s in got] == [("turn.start", 3.5)]
    rec.add("turn.start", 4)
    rec.close()
    assert len(hooktimes.read(path)) == 2


def test_the_store_lives_in_turns_state(state_home):
    path = hooktimes.store_path()
    assert path.name == hooktimes.FILE and path.is_relative_to(state_home)


def test_the_codex_host_times_a_ping_from_its_stamp(monkeypatch, tmp_path):
    from moxi_turns.results import StopFacts

    class Harness:
        session_id = "thread-1"

        def __init__(self):
            self.endpoint = types.SimpleNamespace(ready=lambda *a, **k: None,
                                                  prompt=lambda: None,
                                                  on_stop=lambda facts: None)

        def foreign_hook(self, event, session_id):
            pass

        def stop_facts(self, frame):
            return StopFacts(turn_id=None, session_id="thread-1")

        def stop_answered(self, turn_id):
            pass

    rec = hooktimes.Recorder("codex", path=tmp_path / "h.jsonl")
    server = codex_hooks.HookServer(Harness(), tmp_path / "hooks.sock", times=rec).start()
    try:
        monkeypatch.setattr(codex_hooks, "_STARTED", time.time() - 0.25)
        for event, data in (("UserPromptSubmit", {"session_id": "thread-1"}),
                            ("UserPromptSubmit", {"session_id": "another"})):
            monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
            monkeypatch.setattr(sys, "stdout", io.StringIO())
            assert codex_hooks.main(["--socket", str(server.socket_path), event]) == 0
    finally:
        server.stop()
    got = hooktimes.read(tmp_path / "h.jsonl")
    assert [(s.harness, s.event) for s in got] == [("codex", "UserPromptSubmit")]
    assert 250 <= got[0].ms < 5000


def test_the_claude_leg_takes_hook_times_beside_its_handler(tmp_path):
    from claude_runner import post

    rec = hooktimes.Recorder("claude", path=tmp_path / "h.jsonl")
    seen = []
    server = claude_leg.LegServer(str(tmp_path / "mod.sock"),
                                  lambda path, body: seen.append(path) or {},
                                  times=rec).start()
    try:
        status, answer = post(server.path, claude_leg.PATH_HOOK_TIMES, {"samples": [
            {"event": "classic.Stop", "ms": 12.5}, {"event": "gate prompt.submit", "ms": 1},
            {"event": "bad", "ms": "x"}]})
        assert status == 200 and answer == {"ok": True}
        assert post(server.path, claude_leg.PATH_HOOK_TIMES, {})[0] == 400
    finally:
        server.close()
    assert seen == []
    got = hooktimes.read(tmp_path / "h.jsonl")
    assert [(s.harness, s.event, s.ms) for s in got] == [
        ("claude", "classic.Stop", 12.5), ("claude", "gate prompt.submit", 1.0)]


# -- guards, floors and the removal guard's Claude Code version ---------------------------

class GuardEntry:
    """A `moxi.turns.guards` entry point: its module's `GUARDS`, or an exception."""

    def __init__(self, name, guards_, delay=0.0):
        self.name = name
        self._guards = guards_
        self._delay = delay

    def load(self):
        if self._delay:
            time.sleep(self._delay)
        if isinstance(self._guards, Exception):
            raise self._guards
        return types.SimpleNamespace(GUARDS=self._guards)


@pytest.fixture
def guard_group(monkeypatch):
    """The guard group the doctor reads, set by the test; switches read off unless the
    test names them in `switches`."""
    from moxi_turns import guards

    holder = {"entries": [], "switches": {}}
    real = guards.load

    def load(on=None, *, entries=None, switches=None, env=None):
        def read(module, key):
            return holder["switches"].get((module, key), (False, None))
        return real(on, entries=holder["entries"] if entries is None else entries,
                    switches=switches or read, env=env)

    monkeypatch.setattr(guards, "load", load)
    return holder


def a_guard(name, **kw):
    from moxi_turns.guards import Guard

    kw.setdefault("calls", {"claude": ("Bash",), "codex": ("shell",)})
    kw.setdefault("deny", "no")
    return Guard(name, **kw)


def judge_ok(call):
    return None


def guard_rows():
    return {name: row for name, row in rows_by_name().items()
            if name == "guards" or name.startswith("guard ")}


def test_with_no_guard_on_the_shipped_guards_read_off_with_their_switch(
        turns_env, registry):
    rows = guard_rows()
    assert rows["guards"].state == "ok" and rows["guards"].detail == "no guard is on"
    for gid, key in [("turns.removal", "guards.removal"),
                     ("turns.no-nested-agents", "guards.no_nested_agents"),
                     ("turns.question-mode", "guards.question_mode")]:
        row = rows[f"guard {gid}"]
        assert row.state == "off" and row.detail
        assert row.fix == f"moxi config set turns {key} true"


def test_a_guard_on_names_its_source_and_where_it_runs(turns_env, registry, guard_group):
    guard_group["entries"] = [GuardEntry("mod", [
        a_guard("both"), a_guard("claude-only", calls={"claude": ("Bash",)}),
        a_guard("by-switch", switch="mod.switch"), a_guard("idle", doc="Idle guard.")])]
    guard_group["switches"][("mod", "mod.switch")] = (True, None)
    write_config(turns_env, "[guards]\non = ['mod.both', 'mod.claude-only']\n")
    rows = guard_rows()
    detail = rows["guards"].detail
    assert "mod.both (on by guards.on): runs claude in the runner, codex behind the hook" \
        in detail
    assert "mod.claude-only (on by guards.on): runs claude in the runner" in detail
    assert "mod.by-switch (on by its switch)" in detail
    assert rows["guard mod.claude-only: codex"].state == "ok"
    assert "covers no call on codex" in rows["guard mod.claude-only: codex"].detail
    assert "guard mod.both: codex" not in rows and "guard mod.both: claude" not in rows
    for gid in ("mod.both", "mod.claude-only", "mod.by-switch"):
        row = rows[f"guard {gid}: opencode"]
        assert row.state == "ok" and "no pre-tool point" in row.detail
    idle = rows["guard mod.idle"]
    assert (idle.state, idle.detail) == ("off", "Idle guard.")
    assert idle.fix == "moxi config set turns guards.on --add mod.idle"
    assert not any(r.state in ("fail", "warn") for r in rows.values())


def test_guards_on_the_list_that_do_not_load_fail(turns_env, registry, guard_group):
    guard_group["entries"] = [
        GuardEntry("mod", [a_guard("alias", calls={"codex": ("Agent",)}),
                           a_guard("nojudge", judge="nowhere_at_all.mod:judge"),
                           a_guard("judged", judge="test_doctor:judge_ok"),
                           a_guard("unlisted-alias", calls={"codex": ("Write",)})]),
        GuardEntry("broken", ImportError("no such module"))]
    write_config(turns_env, "[guards]\non = ['mod.alias', 'mod.nojudge', 'mod.judged', "
                            "'broken.g', 'mod.typo']\n")
    rows = guard_rows()
    alias = rows["guard mod.alias"]
    assert alias.state == "fail" and "spawn_agent" in alias.detail
    assert alias.fix == "moxi config set turns guards.on --remove mod.alias"
    nojudge = rows["guard mod.nojudge"]
    assert nojudge.state == "fail" and "does not import" in nojudge.detail
    assert nojudge.fix == "moxi config set turns guards.on --remove mod.nojudge"
    assert "guard mod.judged" not in rows
    assert rows["guard broken.g"].state == "fail"
    assert "no such module" in rows["guard broken.g"].detail
    assert rows["guard mod.typo"].state == "fail"
    # A shape problem of a guard nobody turned on is no row; neither is the module's own
    # load problem, which only its listed ids carry.
    assert "guard mod.unlisted-alias" not in rows and "guard broken" not in rows


def test_a_switch_that_fails_the_core_check_fails_listed_or_not(turns_env, registry,
                                                               guard_group):
    guard_group["entries"] = [GuardEntry("mod", [a_guard("sw", switch="mod.sw")])]
    guard_group["switches"][("mod", "mod.sw")] = (False, "its default is true")
    row = guard_rows()["guard mod.sw"]
    assert row.state == "fail" and "its default is true" in row.detail
    assert [r.state for r in doctor.check() if r.name == "guard mod.sw"] == ["fail"]


def test_a_listed_module_not_installed_and_a_slow_import_warn(turns_env, registry,
                                                             guard_group):
    guard_group["entries"] = [GuardEntry("slow", [a_guard("g")], delay=0.03)]
    write_config(turns_env, "[guards]\non = ['usage.ceiling', 'slow.g']\n")
    rows = guard_rows()
    missing = rows["guard usage.ceiling"]
    assert missing.state == "warn" and "not installed" in missing.detail
    assert missing.fix == "moxi config set turns guards.on --remove usage.ceiling"
    slow = rows["guard slow.g"]
    assert slow.state == "warn" and "over 10 ms" in slow.detail


@pytest.mark.parametrize("installed, warned", [("2.1.316 (Claude Code)", True),
                                               ("2.1.289 (Claude Code)", False),
                                               ("2.0.99 (Claude Code)", False)])
def test_the_removal_guard_warns_on_a_newer_claude_code(turns_env, registry, monkeypatch,
                                                        installed, warned):
    import subprocess

    from moxi_turns import claude_setup
    from moxi_turns.guards import removal

    assert removal.CHECKED_CLAUDE == "2.1.289"
    monkeypatch.setattr(claude_setup, "_claude", lambda: "/x/claude")
    monkeypatch.setattr(claude_setup, "_run", lambda argv: subprocess.CompletedProcess(
        argv, 0, installed + "\n", ""))
    write_config(turns_env, "[guards]\nremoval = true\n")
    rows = guard_rows()
    assert "turns.removal (on by its switch): runs claude in the runner" \
        in rows["guards"].detail
    row = rows.get("guard turns.removal")
    if warned:
        assert row.state == "warn" and "2.1.316" in row.detail and "2.1.289" in row.detail
        assert row.fix == "moxi config set turns guards.removal false"
    else:
        assert row is None


def test_an_after_tool_floor_that_does_not_import_fails_its_listener_row(turns_env,
                                                                        registry):
    registry["entries"] = [
        Entry("usage", [("after-tool", "listener_targets:silent",
                         {"floor": "nowhere_at_all.mod:floor"})]),
        Entry("fine", [("after-tool", "listener_targets:silent",
                        {"floor": "listener_targets:silent"})])]
    usage = [r for r in doctor.check() if r.name == "listener usage"]
    assert {r.state for r in usage} == {"ok", "fail"}
    fail = [r for r in usage if r.state == "fail"][0]
    assert "nowhere_at_all.mod:floor" in fail.detail and "does not import" in fail.detail
    assert [r.state for r in doctor.check() if r.name == "listener fine"] == ["ok"]


def test_hook_time_rows_name_the_guard_and_after_tool_events():
    rows = hooktimes.rows(samples("claude", "guard", [1.0] * 25)
                          + samples("claude", "after-tool", [1.0] * 25)
                          + samples("codex", "after-tool", [1.0] * 25),
                          budget=2.0, floor=10.0)
    names = {r.name: r.state for r in rows}
    assert names == {"hook time: claude after-tool": "ok", "hook time: claude guard": "ok",
                     "hook time: codex after-tool": "ok"}

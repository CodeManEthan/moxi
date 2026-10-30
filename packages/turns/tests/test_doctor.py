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
                           Entry("later", [("start", "x.y:z", {"floor": 1})]),
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

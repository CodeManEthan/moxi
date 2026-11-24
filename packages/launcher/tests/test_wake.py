"""The wake guard: which processes each kill form ends, whose they are, which sessions'
caches refuse, its declaration and switch, and a break letting the call through."""

from __future__ import annotations

import importlib

import pytest
from moxi import modules
from moxi.paths import Layout

from moxi_launcher import wake
from moxi_launcher.wake import Live, Looked, Proc, Procs

#: turns is optional: its names are read through the core's module loader.
ToolCall = modules.load("turns").api.ToolCall

MINE, OTHER, THIRD = "mine-0001", "other-0002", "third-0003"

#: Two sessions, each with a background shell and its child; and a process under none.
TABLE = [
    Proc(1, 0, "init"),
    Proc(100, 1, "claude", ("claude",)),                       # my harness
    Proc(110, 100, "bash", ("bash", "-c", "npm run dev")),
    Proc(111, 110, "node", ("node", "dev-server.js"), "me", pgid=110, sid=110),
    Proc(200, 1, "claude", ("claude",)),                       # the other harness
    Proc(210, 200, "bash", ("bash", "-c", "pytest -x")),
    Proc(211, 210, "python3", ("python3", "-m", "pytest", "-x"), "me", pgid=210, sid=210),
    Proc(300, 1, "postgres", ("postgres", "-D", "/data"), "pg", pgid=300),
]
LIVE = [Live(100, MINE, "claude"), Live(200, OTHER, "claude", ("other-0000",))]


def table() -> Procs:
    return Procs(TABLE)


def bash(command: str, *, session: str | None = MINE, tool: str = "Bash",
         harness: str = "claude") -> ToolCall:
    return ToolCall(harness, session, None, tool, input={"command": command}, env={})


def judged(command: str, kind: str = "cold", **kw) -> tuple[str | None, list]:
    asked: list = []

    def look(session, env):
        asked.append(session.session_id)
        return Looked(kind, 3720.0, "tester@proj")

    return wake.decide(bash(command, **kw), procs=table(), sessions=lambda: LIVE,
                       look=look), asked


# -- which processes a command ends ------------------------------------------------------

@pytest.mark.parametrize("command, pids", [
    ("kill 211", [211]),
    ("kill -9 211 210", [211, 210]),
    ("kill -KILL 211", [211]),
    ("kill -s TERM 211", [211]),
    ("kill --signal=TERM 211", [211]),
    ("kill -TERM -210", [211]),
    ("kill -- -210", [211]),
    ("kill -0 211", []),
    ("kill -s 0 211", []),
    ("kill -l", []),
    ("kill %1 $! $$", []),
    ("kill 0", []),
    ("kill $PID", []),
    ("kill $(cat /tmp/pidfile)", []),
    ("kill $(pgrep -f pytest)", [210, 211]),
    ('kill "$(pgrep python3)"', [211]),
    ("kill `pgrep python3`", [211]),
    ("kill x$(pgrep python3)", []),
    ("kill $(pgrep python3 | head -1)", []),
    ("kill $(pidof python3)", [211]),
    ("pgrep python3 | xargs kill", [211]),
    ("pgrep -f pytest | xargs -r kill -9", [210, 211]),
    ("ls | xargs kill", []),
    ("pkill python3", [211]),
    ("pkill -9 -f 'pytest -x'", [210, 211]),
    ("pkill -x pyth", []),
    ("pkill -i PYTHON3", [211]),
    ("pkill -P 210", [211]),
    ("pkill -u pg", [300]),
    ("pkill -u me -f dev", [111]),
    ("pkill -n python3", []),
    ("pkill -v python3", []),
    ("killall python3", [211]),
    ("killall -9 node", [111]),
    ("killall -r 'pyth.*'", [211]),
    ("killall -u me node", []),
    ("taskkill /PID 211 /F", [211]),
    ("taskkill /F /IM pyth*", [211]),
    ("echo ok; sudo kill -9 211", [211]),
    ("FOO=1 env kill 211 && ls", [211]),
    ("sleep 1\nkill 211", [211]),
    ("echo 'kill 211'", []),
    ("# kill 211", []),
])
def test_each_kill_form_resolves_to_the_processes_it_ends(command, pids):
    assert wake.targets(command, table()) == pids


@pytest.mark.parametrize("command, pids", [
    ("Stop-Process -Id 211", [211]),
    ("Stop-Process 211, 111 -Force", [211, 111]),
    ("spps -Name python3", [211]),
    ("Stop-Process -Name pyth*", [211]),
    ("kill -Name node", [111]),
    ("taskkill /IM python3", [211]),
    ("Stop-Process -Id 211 -WhatIf", []),
    ("Get-Process python3 | Stop-Process", []),
    ("Stop-Process -Id (Get-Content pid.txt)", []),
])
def test_powershell_forms_resolve_too(command, pids):
    assert wake.targets(command, table(), powershell=True) == pids


def test_windows_names_match_without_their_extension():
    procs = Procs([Proc(10, 1, "node.exe"), Proc(1, 0, "System")])
    assert wake.targets("Stop-Process -Name node", procs, powershell=True) == [10]
    assert wake.targets("taskkill /IM NODE.EXE /F", procs) == [10]


# -- whose processes ---------------------------------------------------------------------

def test_each_target_is_owned_by_the_harness_above_it():
    owners = wake.owner_sessions([211, 111, 300], LIVE, table())
    assert [s.session_id for s in owners] == [OTHER, MINE]


def test_a_harness_process_itself_is_not_the_guards():
    assert wake.owner_sessions([200], LIVE, table()) == []


# -- the verdict -------------------------------------------------------------------------

def test_a_cold_session_is_refused_with_what_to_do_instead():
    reason, asked = judged("kill -9 211")
    assert asked == [OTHER]
    assert reason.startswith("Refused before it ran (launcher wake guard): ")
    assert f"session {OTHER} (tester@proj), whose cache is cold (1 h 2 min" in reason
    assert "would give it a turn on a cold cache" in reason
    assert "moxi launcher relaunch tester@proj, as its director" in reason
    assert "or leave it" in reason


def test_the_refusal_promises_nothing_of_how_a_relaunch_ends_the_work():
    reason, _ = judged("kill 211")
    lowered = reason.lower()
    for promise in ("stops its", "ends its shells", "will end", "background shells",
                    "subagent"):
        assert promise not in lowered


@pytest.mark.parametrize("kind", ["warm", "fresh", "unknown", "no-cache"])
def test_a_session_not_read_cold_lets_the_call_run(kind):
    reason, asked = judged("kill 211", kind)
    assert reason is None and asked == [OTHER]


def test_the_callers_own_session_is_skipped_unread():
    reason, asked = judged("kill 111")
    assert reason is None and asked == []


def test_the_callers_session_by_an_earlier_id_is_its_own():
    reason, asked = judged("kill 211", session="other-0000")
    assert reason is None and asked == []


def test_a_target_under_no_session_lets_the_call_run():
    reason, asked = judged("pkill postgres")
    assert reason is None and asked == []


def test_an_unread_cache_lets_the_call_run():
    def look(session, env):
        raise OSError("no store")

    assert wake.decide(bash("kill 211"), procs=table(), sessions=lambda: LIVE,
                       look=look) is None


@pytest.mark.parametrize("tool, harness", [("Read", "claude"), ("Bash", "opencode"),
                                           ("apply_patch", "codex")])
def test_other_tools_are_not_judged(tool, harness):
    reason, asked = judged("kill 211", tool=tool, harness=harness)
    assert reason is None and asked == []


def test_codex_bash_is_judged():
    reason, _ = judged("kill 211", harness="codex")
    assert reason is not None


def test_two_cold_sessions_are_each_named():
    live = LIVE + [Live(400, THIRD, "codex")]
    procs = Procs(TABLE + [Proc(400, 1, "codex"), Proc(410, 400, "sleep", pgid=410)])
    reason = wake.decide(bash("kill 211 410"), procs=procs, sessions=lambda: live,
                         look=lambda s, env: Looked("cold"))
    assert f"session {OTHER}, whose cache is cold;" in reason
    assert f"session {THIRD}, whose cache is cold." in reason
    assert "relaunch each" in reason


def test_a_command_with_no_target_lists_no_sessions():
    def never():
        raise AssertionError("sessions asked for a call that ends nothing")

    assert wake.decide(bash("kill $PID"), procs=table(), sessions=never) is None


# -- the declaration and the switch ------------------------------------------------------

def test_the_guard_is_declared_as_turns_reads_it():
    from moxi_launcher.guards import GUARDS

    (g,) = GUARDS
    assert (g.name, g.who, g.judge, g.sessions, g.on_error, g.switch) == (
        "wake", "any", "moxi_launcher.wake:judge", "moxi_launcher.wake:on_for", "allow",
        None)
    assert g.calls == {"claude": ("Bash", "PowerShell"), "codex": ("Bash",)}
    assert (g.match_flags, g.deadline_ms) == ("i", 2000)
    assert g.problems() == []


@pytest.mark.parametrize("command", ["kill 1", "PKILL x", "killall x", "taskkill /PID 1",
                                     "Stop-Process -Id 1", "spps 1"])
def test_the_pre_filter_passes_every_kill_form(command):
    import re

    assert re.search(wake.MATCH["command"], command, re.IGNORECASE)


def test_turns_loads_it_from_the_launchers_entry():
    guards = importlib.import_module("moxi_turns.guards")
    got = guards.load(on=["launcher.wake"], sessions=lambda target: (False, None))
    on = got.get("launcher.wake")
    assert on is not None and on.by == ("list",)
    assert not [p for p in got.problems if p.id.startswith("launcher")]


def test_on_for_is_on_with_cache_and_the_shipped_default():
    assert modules.available("cache")
    assert wake.on_for({}) is True


def test_on_for_is_off_without_cache(monkeypatch):
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "cache" and real(name))
    assert wake.on_for({}) is False


def test_on_for_is_off_when_the_setting_is_false():
    path = Layout("launcher").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[wake_guard]\nenabled = false\n")
    assert wake.on_for({}) is False


def test_a_broken_judge_lets_the_call_run(monkeypatch):
    guards = importlib.import_module("moxi_turns.guards")
    loaded = guards.load(on=["launcher.wake"], sessions=lambda target: (False, None))

    def broken(call, **kw):
        raise RuntimeError("judge broke")

    monkeypatch.setattr(wake, "decide", broken)
    run = importlib.import_module("moxi_turns.guards.run")
    runner = run.GuardRunner("claude", settings=lambda: ([], 3000), load=lambda on: loaded,
                             record=lambda *a: None, clear=lambda key: None)
    try:
        runner.reload()
        answer = runner.ask(bash("kill 211"), ["launcher.wake"])
        assert answer.deny is None
        assert [b.id for b in answer.breaks] == ["launcher.wake"]
    finally:
        runner.close()

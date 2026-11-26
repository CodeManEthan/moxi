"""The wake guard: which processes each kill form ends, whose they are, which sessions'
caches refuse, its declaration and switch, and a break letting the call through."""

from __future__ import annotations

import importlib
import shlex

import pytest
from moxi import modules
from moxi.paths import Layout

from moxi_launcher import wake
from moxi_launcher.wake import Live, Looked, Proc, Procs

#: turns is optional: its names are read through the core's module loader.
ToolCall = modules.load("turns").api.ToolCall

MINE, OTHER, THIRD = "mine-0001", "other-0002", "third-0003"
#: The platforms as `sys.platform` names them: a test names the one it reads as.
LINUX, WINDOWS, MACOS = "linux", "win32", "darwin"

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
                       look=look, system=LINUX), asked


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
    assert wake.targets(command, table(), system=LINUX) == pids


#: A process at pid 2 under a session, so a redirection's fd read as a pid would show.
FD_TABLE = TABLE + [Proc(2, 210, "sh", ("sh",))]


@pytest.mark.parametrize("command, pids", [
    ("kill $(pgrep python3) 2>/dev/null", [211]),
    ("kill 211 2>&1", [211]),
    ("kill 211 >&2", [211]),
    ("kill 211 1>&2", [211]),
    ("kill 211 &>/dev/null", [211]),
    ("kill 211 &>> log", [211]),
    ("kill 211 2> err.log", [211]),
    ("kill 211 2>>err.log", [211]),
    ("kill 211 {fd}>&-", [211]),
    ("kill 211 <<< 2", [211]),
    ("kill 211 2>&1 | tee out", [211]),
    ("kill $(pgrep python3 2>/dev/null)", [211]),
    ("pgrep python3 2>/dev/null | xargs kill", [211]),
    ("pkill python3 2>/dev/null", [211]),
    ("kill 211&>x; kill 111", [211, 111]),
    ("kill 211 >| out 111", [211, 111]),
    # Not a redirection's fd: a word apart from the operator, or quoted, is an argument.
    ("kill 211 2 >/dev/null", [211, 2]),
    ("kill 211 '2'>/dev/null", [211, 2]),
])
def test_a_redirection_is_no_target(command, pids):
    assert wake.targets(command, Procs(FD_TABLE), system=LINUX) == pids


@pytest.mark.parametrize("command, pids", [
    ("Stop-Process -Id 211 2>$null", [211]),
    ("Stop-Process -Id 211 2>&1", [211]),
    ("Stop-Process 211 *> out.txt", [211]),
    ("Stop-Process 211 >> 2", [211]),
    ("Stop-Process -Name python3 2>&1 | Out-Null", [211]),
])
def test_a_powershell_redirection_is_no_target(command, pids):
    assert wake.targets(command, Procs(FD_TABLE), powershell=True) == pids


# -- Windows and macOS -------------------------------------------------------------------

@pytest.mark.parametrize("command, pids", [
    ("taskkill //PID 211 //F", [211]),
    ("taskkill //F //IM pyth*", [211]),
    ("taskkill //PID 211,111", [211, 111]),
    ("taskkill /PID 211", [211]),
])
def test_git_bash_taskkill_switches_are_read(command, pids):
    assert wake.targets(command, table(), system=WINDOWS) == pids


@pytest.mark.parametrize("command", [
    "kill 211", "kill -9 211", "kill $(pgrep python3)", "pkill python3",
    "killall python3", "pgrep python3 | xargs kill",
])
def test_a_windows_shells_msys_kill_is_not_judged(command):
    """Git Bash's kill takes MSYS pids, not the Windows pids the table holds."""
    assert wake.targets(command, table(), system=WINDOWS) == []
    assert wake.targets(command, table(), system=LINUX) != []


def test_codex_on_windows_is_read_as_powershell():
    def judged_on(system, command):
        return wake.decide(bash(command, harness="codex"), procs=table(),
                           sessions=lambda: LIVE, look=lambda s, env: Looked("cold"),
                           system=system)

    assert judged_on(WINDOWS, "Stop-Process -Id 211") is not None
    assert judged_on(WINDOWS, "spps -Name python3 -Force") is not None
    assert judged_on(LINUX, "Stop-Process -Id 211") is None
    # Claude's Bash on Windows is Git Bash, not PowerShell.
    claude = wake.decide(bash("Stop-Process -Id 211"), procs=table(),
                         sessions=lambda: LIVE, look=lambda s, env: Looked("cold"),
                         system=WINDOWS)
    assert claude is None


@pytest.mark.parametrize("command, pids", [
    ("killall -m 'pyth.*'", [211]),
    ("killall -m pyth", [211]),
    ("killall -9 -m node", [111]),
    ("killall -s python3", []),
    ("killall -d python3", []),
    ("killall -l", []),
    ("killall -c python3 node", []),
    ("killall -t ttys001 node", []),
])
def test_macos_killall_forms(command, pids):
    assert wake.targets(command, table(), system=MACOS) == pids


def test_killall_m_is_a_pattern_off_macos_too():
    assert wake.targets("killall -m 'pyth.*'", table(), system=LINUX) == [211]


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


# -- every command the shell reader gives --------------------------------------------------

def nested(text: str, times: int) -> str:
    """`text` handed to `bash -c`, `times` shells deep."""
    for _ in range(times):
        text = "bash -c " + shlex.quote(text)
    return text


SH, PS = "Bash", "PowerShell"


@pytest.mark.parametrize("tool, command", [
    # Operators.
    (SH, "true && kill 211"),
    (SH, "a | kill 211"),
    # Quoting.
    (SH, "echo $'a\\'b' ; kill 211"),
    # Escapes.
    (SH, "\\kill 211"),
    # Substitutions.
    (SH, "echo $(kill 211)"),
    (SH, "x=$(kill 211)"),
    (SH, 'echo "$(kill 211)"'),
    (SH, "echo `kill 211`"),
    (SH, "cat <(kill 211)"),
    (SH, "echo ${x:-$(kill 211)}"),
    (PS, '"$(Stop-Process -Id 211)"'),
    (PS, "@(Stop-Process -Id 211)"),
    # Arithmetic.
    (SH, "echo $((1<<2)); kill 211"),
    (SH, "while (( n << 1 )); do\nkill 211\ndone"),
    (SH, "for ((i=0; i<(1<<3); i++)); do kill 211; done"),
    # Reserved words.
    (SH, "{ kill 211; }"),
    (SH, "! kill 211"),
    (SH, "if true; then kill 211; fi"),
    (SH, "while :; do kill 211; done"),
    (SH, "if a; then :; elif b; then :; else kill 211; fi"),
    (SH, "time -p kill 211"),
    (SH, "case x in x) kill 211;; esac"),
    (SH, 'echo "$(case x in x) kill 211;; esac)"'),
    (SH, "function f { kill 211; }"),
    # Wrappers.
    (SH, "sudo -u root kill 211"),
    (SH, "env -i X=1 nohup kill 211"),
    (SH, "timeout 5 kill 211"),
    (SH, "sudo --preserve-env kill 211"),
    # Heredocs with an unquoted delimiter: the body's substitutions run.
    (SH, "cat <<EOF\n$(kill 211)\nEOF"),
    (SH, "cat <<EOF\n`kill 211`\nEOF"),
    (SH, "cat <<EOF\nsay \"hi\" it's $(kill 211)\nEOF"),
    # Here-strings.
    (PS, '$s = @"\nhello $(Stop-Process -Id 211)\n"@'),
    # PowerShell statements.
    (PS, "if ($true) { Stop-Process -Id 211 }"),
    (PS, "try {Stop-Process -Id 211} catch {}"),
    (PS, "& Stop-Process -Id 211"),
    # cmd.
    (SH, "cmd /c taskkill /PID 211"),
    (SH, 'cmd.exe //c "taskkill /PID 211"'),
    (SH, "cmd /q /c taskkill /PID 211"),
    (SH, "cmd /c if exist x (taskkill /PID 211)"),
    (SH, 'cmd /c "if exist x (taskkill /PID 211)"'),
    # A shell's argument.
    (SH, "bash -c 'kill 211'"),
    (SH, 'sh -lc "kill 211"'),
    (SH, "bash -o pipefail -c 'kill 211'"),
    (SH, "/bin/zsh -c 'bash -c \"kill 211\"'"),
    (SH, "sudo -u x bash -c 'kill 211'"),
    (SH, "pwsh -Command 'Stop-Process -Id 211'"),
    (SH, "pwsh -NoProfile -com 'Stop-Process -Id 211'"),
    (SH, 'powershell "Stop-Process -Id 211"'),
    (SH, nested("kill 211", 3)),
    # A shell's input.
    (SH, "bash <<'EOF'\nkill 211\nEOF"),
    (SH, "bash -s <<< 'kill 211'"),
    (SH, "pwsh -Command - <<'EOF'\nStop-Process -Id 211\nEOF"),
    # The pipe.
    (SH, "echo 'kill 211' | bash"),
    (SH, "echo -n kill 211 | sh"),
    (SH, "cat <<'EOF' | sh\nkill 211\nEOF"),
    (SH, "cat <<< 'kill 211' | sh"),
    (SH, "echo 'Stop-Process -Id 211' | pwsh -Command -"),
    # A kill read wherever it may run, a dead branch included.
    (SH, "if false; then kill 211; fi"),
    # A `${...}` default bash splits gives the words kill reads.
    (SH, "kill ${x:- 211 }"),
])
def test_every_command_the_reader_gives_is_refused_on_a_cold_target(tool, command):
    assert wake.targets(command, table(), powershell=tool == PS, system=LINUX) == [211]
    reason, asked = judged(command, tool=tool)
    assert reason is not None and asked == [OTHER]


@pytest.mark.parametrize("tool, command", [
    # What the grammar keeps as data.
    (SH, "echo 'kill 211'"),
    (SH, "cat <<'EOF'\nkill 211\nEOF"),
    (SH, "cat <<'EOF'\n$(kill 211)\nEOF"),
    (SH, "git commit -m \"$(cat <<'EOF'\ndon't kill 211\nEOF\n)\""),
    (PS, "$s = @'\nit's Stop-Process -Id 211\n'@"),
    (SH, nested("kill 211", 4)),
    (SH, "bash script.sh <<'EOF'\nkill 211\nEOF"),
    # Values.
    (SH, "k=kill; $k 211"),
    # Escapes a program processes.
    (SH, "printf 'kill 211\\n' | sh"),
    (SH, "printf '%s' x | sh"),
    (SH, "echo -e 'kill 211' | sh"),
    # Strings run as code.
    (SH, "eval 'kill 211'"),
    (PS, "iex 'Stop-Process -Id 211'"),
    # cmd's control words.
    (SH, "cmd /c start /b taskkill /PID 211"),
    (SH, "cmd /c call taskkill /PID 211"),
    # Options the tables don't name.
    (SH, "env -S 'kill 211'"),
    (SH, "bash --frob -c 'kill 211'"),
    (SH, "sudo --login kill 211"),
    # Programs the tables don't name.
    (SH, "ssh host kill 211"),
    (SH, "xargs sh -c 'kill 211'"),
    # Text from outside the command.
    (SH, "cat pids.txt | sh"),
    # The reader's named misread: `$((` is always arithmetic.
    (SH, "echo $((true); (kill 211))"),
])
def test_what_the_reader_does_not_read_ends_nothing(tool, command):
    assert wake.targets(command, table(), powershell=tool == PS, system=LINUX) == []
    reason, asked = judged(command, tool=tool)
    assert reason is None and asked == []


def test_a_quoted_kill_word_never_reaches_the_judge():
    """`"ki"ll 211` runs `kill`, but the raw text holds no kill-family word, so turns'
    pre-filter passes it by unjudged."""
    import re

    assert not re.search(wake.MATCH["command"], '"ki"ll 211', re.IGNORECASE)


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
                       look=look, system=LINUX) is None


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
    names = {OTHER: "tester@proj", THIRD: None}
    reason = wake.decide(bash("kill 211 410"), procs=procs, sessions=lambda: live,
                         look=lambda s, env: Looked("cold", None, names[s.session_id]),
                         system=LINUX)
    assert f"session {OTHER} (tester@proj), whose cache is cold;" in reason
    assert f"session {THIRD}, whose cache is cold." in reason
    assert ("Ending their processes would give each a turn on a cold cache; relaunch "
            "each, as its director (moxi launcher relaunch tester@proj; moxi launcher "
            f"relaunch {THIRD}), or leave each.") in reason


def test_a_command_with_no_target_lists_no_sessions():
    def never():
        raise AssertionError("sessions asked for a call that ends nothing")

    assert wake.decide(bash("kill $PID"), procs=table(), sessions=never,
                       system=LINUX) is None


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

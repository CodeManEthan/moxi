"""`moxi comms claude` forks the session's adapter before it execs `claude`,
and the daemon's no-endpoint state as a sender and the doctor read it.

The launch runs for real in a child process, with a stand-in `claude` that records what
it found when it started and then exits: the adapter is up, bound and listening on the
claim socket before `claude` starts (the wrapper waits for its word), its claim's
root is the `claude` process (the wrapper's pid, kept across the exec), it reports no
endpoint while the session has none, and it deregisters and cleans up when `claude`
exits. A name the daemon refuses ends the launch before `claude` starts.
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import textwrap

import pytest
from conftest import wait_until

from moxi import exit as moxi_exit
from moxi_comms import claimfile, paths, reserved, secrets
from moxi_comms.adapters import session, word

NAME = "launched"
#: The id the launch names to `claude`, and its session dir's name.
SID = "5e55a1d0-0000-4000-8000-0000000000f1"


def binding(rig, qualified=NAME):
    with rig.daemon._lock:
        return rig.daemon.bindings.get(qualified)


@pytest.fixture
def fake_claude(tmp_path):
    out = tmp_path / "claude.out"
    script = tmp_path / "claude"
    script.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        if [ -S "$MOXI_COMMS_CLAIM_SOCKET" ]; then echo socket-ready >> {out}; fi
        echo "pid $$" >> {out}
        echo "args $*" >> {out}
        while [ ! -e {tmp_path}/release ]; do sleep 0.05; done
        """))
    script.chmod(0o755)
    return script, out, tmp_path / "release"


def test_the_launch_forks_a_bound_adapter_before_claude_and_it_ends_with_claude(
        rig, fake_claude, turns_plugins):
    from conftest import TURNS_PLUGINS_PATCH
    script, out, release = fake_claude
    code = (TURNS_PLUGINS_PATCH.format(root=str(turns_plugins))
            + "from moxi_comms.adapters import session; raise SystemExit(session.main("
            f"['--name', {NAME!r}, '--session-id', {SID!r}, '--claude', {str(script)!r}, "
            "'--', '--model', 'haiku']))")
    proc = subprocess.Popen([sys.executable, "-c", code], env=dict(os.environ),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert wait_until(lambda: out.exists() and "args" in out.read_text(), timeout=15)
        lines = out.read_text().splitlines()
        assert lines[0] == "socket-ready", "the claim socket listened before claude started"
        pid = int(lines[1].split()[1])
        assert pid == proc.pid, "claude keeps the wrapper's pid"
        plugins = (f"--plugin-dir {turns_plugins / 'moxi-turns'} "
                   f"--plugin-dir {turns_plugins / 'moxi-turns-gate'}")
        assert lines[2] == f"args --session-id {SID} {plugins} --model haiku -n {NAME}", \
            "the launch names its id to claude, then turns' plugins, then the passthrough"
        assert (turns_plugins / "moxi-turns" / "endpoint.json").exists()
        b = binding(rig)
        assert b is not None, "bound before claude started"
        assert b.endpoint is False, "the session has no turns endpoint"
        assert b.session_id == SID, "the adapter states the named id from its bind"
        record = claimfile.read(SID)
        assert [r["pid"] for r in record["roots"]] == [pid], "claude is the claim's root"
        sock = paths.claim_socket(SID)
        assert sock.is_socket() and record["socket"] == str(sock)
    finally:
        release.touch()
    proc.wait(timeout=15)
    assert proc.returncode == 0, proc.stderr.read()
    assert wait_until(lambda: binding(rig) is None, timeout=10), "the name deregisters"
    assert wait_until(lambda: not (session.sessions_dir() / SID).exists(), timeout=5)
    assert claimfile.read(SID) is None
    log = session.log_dir() / f"claude-{SID}.log"
    assert "claude pid" in log.read_text()


def test_the_wrapper_waits_a_bounded_time_for_the_adapters_word():
    """A word that never comes holds `claude` up by `ready_wait` and no more; a refusal
    comes with its reason, and an adapter that ended without a word reads as failed."""
    r, w = os.pipe()
    try:
        assert word.await_word(r, 0.05) is None
        word.teller(w)(word.REFUSED, "console is reserved for launcher")
        assert word.await_word(r, 0.05) == (word.REFUSED,
                                            "console is reserved for launcher")
    finally:
        os.close(r)
    r, w = os.pipe()
    say = word.teller(w)
    say(word.BOUND)
    say(word.FAILED, "the adapter ended")
    assert word.await_word(r, 0.05) == (word.BOUND, "")
    os.close(r)
    r, w = os.pipe()
    os.close(w)
    assert word.await_word(r, 0.05) == (word.FAILED, "")
    os.close(r)


def run_wrapper(turns_plugins, script, name, sid, *extra):
    """The wrapper's `main` in a child process, to its end."""
    from conftest import TURNS_PLUGINS_PATCH
    code = (TURNS_PLUGINS_PATCH.format(root=str(turns_plugins))
            + "from moxi_comms.adapters import session; raise SystemExit(session.main("
            f"['--name', {name!r}, *{list(extra)!r}, '--session-id', {sid!r}, "
            f"'--claude', {str(script)!r}]))")
    return subprocess.run([sys.executable, "-c", code], env=dict(os.environ),
                          capture_output=True, text=True, timeout=60)


def test_a_reserved_name_is_refused_before_claude_starts(rig, fake_claude, turns_plugins):
    script, out, release = fake_claude
    release.touch()                     # a claude that did start would end at once
    reserved.reconcile(declared={"launcher": ["console"]}, chain=secrets.from_config())
    sid = "5e55a1d0-0000-4000-8000-0000000000a1"
    proc = run_wrapper(turns_plugins, script, "console", sid, "--global")
    assert proc.returncode == moxi_exit.REFUSED, proc.stderr
    assert "console was not launched: console is reserved for launcher" in proc.stderr
    assert not out.exists(), "claude never started"
    assert binding(rig, "console") is None
    assert wait_until(lambda: not (session.sessions_dir() / sid).exists(), timeout=5)


def test_a_held_name_is_refused_before_claude_starts(rig, fake_claude, turns_plugins):
    script, out, release = fake_claude
    release.touch()
    rig.shim(name="heldname", global_=True)
    holder = binding(rig, "heldname")
    assert holder is not None
    proc = run_wrapper(turns_plugins, script, "heldname",
                       "5e55a1d0-0000-4000-8000-0000000000a2", "--global")
    assert proc.returncode == moxi_exit.REFUSED, proc.stderr
    assert "heldname was not launched: heldname is already bound" in proc.stderr
    assert not out.exists(), "claude never started"
    assert binding(rig, "heldname") is holder, "the holder keeps its name"


# ------------------------------------------------------------- the wrapper's pidfd

@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="pidfds are Linux's")
def test_the_pidfd_comes_from_the_system_call_when_python_lacks_the_wrapper(monkeypatch):
    """A Python built without `os.pidfd_open` still gets a working pidfd: it polls
    unready while the process runs and ready once it has ended. A pid that names no
    process raises OSError with the call's own errno, ESRCH, as the wrapper does."""
    import select

    from moxi_comms.adapters import terminal
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                            stdin=subprocess.PIPE)
    fd = terminal.pidfd_open(proc.pid)
    try:
        assert os.get_inheritable(fd) is False
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        assert poller.poll(0) == [], "the process still runs"
        proc.stdin.close()
        proc.wait(timeout=30)
        assert poller.poll(5000) != [], "the pidfd is ready once the process has ended"
    finally:
        os.close(fd)
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    with pytest.raises(OSError) as raised:
        terminal.pidfd_open(2 ** 22 + 1)     # past the kernel's largest pid
    assert raised.value.errno == errno.ESRCH


def test_where_no_pidfd_exists_the_helper_fails_as_the_wrapper_would(monkeypatch):
    from moxi_comms.adapters import terminal
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(AttributeError):
        terminal.pidfd_open(os.getpid())


# ------------------------------------------------------------- the id the launch names

OTHER = "5e55a1d0-0000-4000-8000-0000000000f2"


def dry_run(capsys, *passthrough, own=None) -> tuple[int, str, list[str], str]:
    """(exit, the session line's id, the argv after `claude` minus `-n`, stderr) of a
    dry-run launch with this passthrough."""
    argv = ["--name", NAME, "--dry-run"]
    if own is not None:
        argv += ["--session-id", own]
    code = session.main([*argv, "--", *passthrough])
    cap = capsys.readouterr()
    if code != 0:
        return code, "", [], cap.err
    lines = cap.out.splitlines()
    sid = lines[0].split()[1]
    launch = next(ln for ln in lines if ln.startswith("  launch")).split()[1:]
    assert launch[0] == "claude" and launch[-2:] == ["-n", NAME]
    return code, sid, without_plugins(launch[1:-2]), cap.err


def without_plugins(argv: list[str]) -> list[str]:
    """`argv` less turns' `--plugin-dir DIR` pairs, which every launch carries."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--plugin-dir":
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def is_uuid(value: str) -> bool:
    import uuid
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def test_a_launch_that_names_no_id_gets_one(capsys):
    code, sid, argv, _ = dry_run(capsys, "--model", "haiku")
    assert code == 0 and is_uuid(sid)
    assert argv == ["--session-id", sid, "--model", "haiku"], "a fresh UUID, named"
    code, sid, argv, _ = dry_run(capsys, own=SID)
    assert (sid, argv) == (SID, ["--session-id", SID]), "the wrapper's own id, named"


@pytest.mark.parametrize("passthrough", [
    ["--session-id", SID], [f"--session-id={SID}"],
    ["--resume", SID], ["-r", SID], [f"--resume={SID}"],
], ids=["session-id", "session-id=", "resume", "-r", "resume="])
def test_an_id_the_passthrough_names_is_the_id(capsys, passthrough):
    code, sid, argv, _ = dry_run(capsys, *passthrough)
    assert (code, sid, argv) == (0, SID, passthrough), "passed through, nothing added"
    code, sid, argv, _ = dry_run(capsys, *passthrough, own=SID)
    assert (code, sid, argv) == (0, SID, passthrough), "the wrapper's own id agrees"


@pytest.mark.parametrize("passthrough", [
    ["--resume", SID, "--fork-session"], ["--continue", "--fork-session"],
], ids=["resume-fork", "continue-fork"])
def test_a_fork_is_named_a_new_id(capsys, passthrough):
    code, sid, argv, _ = dry_run(capsys, *passthrough)
    assert code == 0 and is_uuid(sid) and sid != SID
    assert argv == ["--session-id", sid, *passthrough]
    code, sid, argv, _ = dry_run(capsys, *passthrough, own=OTHER)
    assert (sid, argv) == (OTHER, ["--session-id", OTHER, *passthrough])


@pytest.mark.parametrize("passthrough", [
    ["--continue"], ["-c"], ["--resume"], ["-r"], ["--resume", "cache-bug"],
    ["--resume", "--model", "haiku"], ["--from-pr", "12"], ["--from-pr=12"],
    ["--teleport"], ["--teleport=abc"],
], ids=["continue", "-c", "resume", "-r", "resume-search", "resume-then-flag",
        "from-pr", "from-pr=", "teleport", "teleport="])
def test_an_unnamed_resume_passes_through_and_names_no_id(capsys, passthrough):
    code, sid, argv, _ = dry_run(capsys, *passthrough)
    assert code == 0 and argv == passthrough, "unchanged, no --session-id added"
    stated, added = session.launch_session_id(passthrough)
    assert (stated, added) == (None, []), "the binding states no id until the look"
    code, sid, argv, _ = dry_run(capsys, *passthrough, own=SID)
    assert (code, sid, argv) == (0, SID, passthrough), \
        "the wrapper's own id names the session dir and nothing else"
    assert session.launch_session_id(passthrough, SID) == (None, [])


@pytest.mark.parametrize("passthrough", [
    ["--resume", SID.upper()], ["-r", SID.upper()], ["--session-id", SID.upper()],
], ids=["resume", "-r", "session-id"])
def test_an_uppercase_id_is_stated_in_lowercase(capsys, passthrough):
    """`claude` and turns report the id in lowercase and the transcript is named by it,
    so the binding states that spelling. The passthrough is not rewritten."""
    code, sid, argv, _ = dry_run(capsys, *passthrough)
    assert (code, sid, argv) == (0, SID, passthrough)
    code, sid, argv, _ = dry_run(capsys, *passthrough, own=SID.upper())
    assert (code, sid, argv) == (0, SID, passthrough)


def test_an_uppercase_own_id_is_named_and_stated_in_lowercase(capsys):
    code, sid, argv, _ = dry_run(capsys, own=SID.upper())
    assert (code, sid, argv) == (0, SID, ["--session-id", SID])
    code, sid, argv, _ = dry_run(capsys, "--continue", own=SID.upper())
    assert (code, sid, argv) == (0, SID, ["--continue"]), "the session dir, in lowercase"


def test_a_second_launch_of_a_running_session_id_is_refused(capsys):
    """Two launches of one id would share one session dir and one claim socket. A
    claim socket that answers refuses the second; a stale socket file does not."""
    import socket
    path = session.Session(SID, name=NAME).claim_socket
    path.parent.mkdir(parents=True, exist_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(str(path))
        srv.listen(1)
        code, _, _, err = dry_run(capsys, "--resume", SID)
        assert code == 2 and "already running under comms" in err, err
    finally:
        srv.close()
    assert path.exists(), "the socket file stays behind, stale"
    code, sid, _, _ = dry_run(capsys, "--resume", SID)
    assert (code, sid) == (0, SID)


def test_flags_after_claudes_own_double_dash_are_not_read():
    stated, added = session.launch_session_id(["-p", "--", "--continue"])
    assert stated is not None and added == ["--session-id", stated]


@pytest.mark.parametrize("own,passthrough,says", [
    ("fk1", [], "is not a UUID"),
    ("fk1", ["--continue"], "is not a UUID"),
    (OTHER, ["--session-id", SID], "differs from the id the passthrough names"),
    (OTHER, ["--resume", SID], "differs from the id the passthrough names"),
    (None, ["--session-id", "fk1"], "after -- is not a UUID"),
], ids=["own-not-uuid", "own-not-uuid-continue", "differs-session-id", "differs-resume",
        "passthrough-not-uuid"])
def test_the_wrapper_refuses_an_id_it_cannot_name(capsys, own, passthrough, says):
    code, _, _, err = dry_run(capsys, *passthrough, own=own)
    assert code == 2 and says in err, err


def test_the_adapter_states_the_named_id_from_its_bind():
    """The adapter's shim carries the launch's id before any look; an unnamed
    resume's carries none."""
    for stated in (SID, None):
        s = session.Session(stated or "fk3", name=NAME, project="proj-a")
        s.stated_id = stated
        shim = session.adapter_shim(s, os.getpid())
        try:
            assert shim.session_id == stated and shim.turned is False
        finally:
            shim.stop(remove_claim_file=True)


# ------------------------------------------------------------ turns' launch additions

def fake_additions(monkeypatch, *, argv=(), env=None, notes=(), raises=None) -> list:
    """`moxi_turns.launch_additions` answering these additions (or raising), and the
    calls it was given."""
    import moxi_turns
    calls = []

    def additions(harness, *, cwd, env: dict, session_id=None, commands=()):
        calls.append({"harness": harness, "cwd": cwd, "env": dict(env),
                      "session_id": session_id, "commands": tuple(commands)})
        if raises is not None:
            raise raises
        return moxi_turns.LaunchAdditions(harness, tuple(argv), dict(env_out),
                                          {}, tuple(notes))

    env_out = env or {}
    monkeypatch.setattr(moxi_turns, "launch_additions", additions)
    return calls


@pytest.fixture
def launched(monkeypatch, tmp_path):
    """Run the wrapper to its exec and keep what it would exec: (main, the captures)."""
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    seen = {}

    def launch(s, found, cmd, env, *, prog="moxi-comms-claude", **_):
        seen.update(session=s, binary=found, cmd=cmd, env=env)
        return 0

    monkeypatch.setattr(session, "launch", launch)

    def run(*passthrough, own=SID):
        code = session.main(["--name", NAME, "--session-id", own, "--claude", str(binary),
                             "--", *passthrough])
        return code, seen
    return run


def test_the_additions_follow_the_wrappers_flags_and_come_before_the_passthrough(
        capsys, monkeypatch):
    calls = fake_additions(monkeypatch, argv=("--plugin-dir", "/p/runner",
                                              "--plugin-dir", "/p/gate",
                                              "--allowedTools=Bash(git:*)"))
    assert session.main(["--name", NAME, "--session-id", SID, "--dry-run", "--",
                         "--model", "haiku"]) == 0
    launch = next(ln for ln in capsys.readouterr().out.splitlines()
                  if ln.startswith("  launch"))
    # One word per argv item here: no word of this launch holds a space.
    assert launch.split()[1:] == ["claude", "--session-id", SID,
                                  "--plugin-dir", "/p/runner", "--plugin-dir", "/p/gate",
                                  "--allowedTools=Bash(git:*)",
                                  "--model", "haiku", "-n", NAME]
    (call,) = calls
    assert call["harness"] == "claude" and call["session_id"] == SID
    assert call["cwd"] == os.getcwd() and call["commands"] == ()
    assert call["env"]["MOXI_COMMS_NAME"] == NAME, "asked with the stamped environment"


def test_the_dry_run_prints_the_whole_line_additions_included(capsys, monkeypatch):
    fake_additions(monkeypatch, argv=("--plugin-dir", "/p/runner", "--plugin-dir", "/p/gate"))
    assert session.main(["--name", NAME, "--session-id", SID, "--dry-run", "--",
                         "--model", "haiku"]) == 0
    out = capsys.readouterr().out
    assert (f"  launch     claude --session-id {SID} --plugin-dir /p/runner --plugin-dir "
            f"/p/gate --model haiku -n {NAME}") in out


@pytest.mark.parametrize("passthrough,kept", [
    (["--plugin-dir", "/p/runner"], ["--plugin-dir", "/p/gate", "--allowedTools=Read"]),
    (["--plugin-dir=/p/gate"], ["--plugin-dir", "/p/runner", "--allowedTools=Read"]),
    (["--allowedTools", "Read"], ["--plugin-dir", "/p/runner", "--plugin-dir", "/p/gate"]),
    (["--allowedTools=Read", "--plugin-dir", "/p/runner", "--plugin-dir", "/p/gate"], []),
    (["--plugin-dir", "/elsewhere"],
     ["--plugin-dir", "/p/runner", "--plugin-dir", "/p/gate", "--allowedTools=Read"]),
    (["-p", "--", "--plugin-dir", "/p/runner"],
     ["--plugin-dir", "/p/runner", "--plugin-dir", "/p/gate", "--allowedTools=Read"]),
], ids=["pair", "joined", "rule-as-pair", "all-given", "other-dir", "after-claudes-dash"])
def test_an_addition_the_passthrough_already_gives_is_not_added_again(passthrough, kept):
    additions = ("--plugin-dir", "/p/runner", "--plugin-dir", "/p/gate",
                 "--allowedTools=Read")
    assert session.not_given(additions, passthrough) == kept


def test_the_additions_env_is_merged_and_a_launchers_grant_survives_the_scrub(
        monkeypatch, launched):
    """`stamped_env` scrubs comms' own variables only: a `MOXI_TURNS_COMMANDS` grant the
    launcher put in the wrapper's environment reaches the session, and an inherited comms
    stamp does not."""
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "exit,compact")
    monkeypatch.setenv("MOXI_COMMS_DIRECTORS", "someone-else")
    calls = fake_additions(monkeypatch, argv=("--plugin-dir", "/p/runner"),
                           env={"MOXI_TURNS_EXTRA": "1"})
    code, seen = launched("--model", "haiku")
    assert code == 0
    env = seen["env"]
    assert env["MOXI_TURNS_COMMANDS"] == "exit,compact", "the launcher's grant survives"
    assert env["MOXI_TURNS_EXTRA"] == "1", "the additions' env is merged"
    assert env["MOXI_COMMS_NAME"] == NAME and "MOXI_COMMS_DIRECTORS" not in env
    assert calls[0]["env"]["MOXI_TURNS_COMMANDS"] == "exit,compact"
    assert seen["cmd"][1:] == ["--session-id", SID, "--plugin-dir", "/p/runner",
                               "--model", "haiku", "-n", NAME]


def test_the_additions_env_goes_over_the_launchers(monkeypatch, launched):
    monkeypatch.setenv("MOXI_TURNS_COMMANDS", "exit")
    fake_additions(monkeypatch, env={"MOXI_TURNS_COMMANDS": "clear"})
    code, seen = launched()
    assert code == 0 and seen["env"]["MOXI_TURNS_COMMANDS"] == "clear"


@pytest.mark.parametrize("error", ["turns", "value"])
def test_additions_turns_cannot_give_refuse_the_launch_before_anything_starts(
        capsys, monkeypatch, launched, error):
    import moxi_turns
    from moxi import exit as moxi_exit
    raised = (moxi_turns.TurnsError("the Claude plugin cannot be set up: no folder")
              if error == "turns" else ValueError("harness 'claude' is not one of x"))
    fake_additions(monkeypatch, raises=raised)
    code, seen = launched()
    assert code == moxi_exit.ERROR
    err = capsys.readouterr().err
    assert err.startswith("moxi-comms-claude: turns cannot give this launch its additions, "
                          "so claude was not started: ") and str(raised) in err, err
    assert "Traceback" not in err
    assert seen == {}, "nothing was launched"
    assert not (session.sessions_dir() / SID).exists(), "no session directory was made"


def test_the_additions_notes_are_said_on_stderr(capsys, monkeypatch):
    fake_additions(monkeypatch, notes=("additions of tools skipped: it raised boom",))
    code, _, _, err = dry_run(capsys, own=SID)
    assert code == 0
    assert "moxi-comms-claude: additions of tools skipped: it raised boom" in err


# --------------------------------------------------------------------------- the daemon

def test_the_session_query_and_the_roster_carry_the_endpoint_state(rig):
    from moxi_comms.adapters.claude_adapter import ClaudeAdapter
    shim = rig.shim(name="reviewer", scope="proj-a", start=False)
    a = ClaudeAdapter(shim, 1, look_interval=60).start(tolerate_offline=False)
    try:
        assert wait_until(lambda: binding(rig, "reviewer@proj-a") is not None)
        reply = rig.daemon._session_query({"identity": "reviewer@proj-a"})
        assert reply["endpoint"] is False and reply["endpoint_for"] >= 0
        agents = rig.daemon._agents_query({})["bindings"]
        (row,) = [b for b in agents if b["qualified"] == "reviewer@proj-a"]
        assert row["endpoint"] is False and row["endpoint_since"] is not None
        shim.notify_endpoint(True)
        assert wait_until(lambda: binding(rig, "reviewer@proj-a").endpoint is True)
    finally:
        a.stop()
    plain = rig.shim(name="other", scope="proj-a")
    assert wait_until(lambda: binding(rig, "other@proj-a") is not None)
    assert rig.daemon._session_query({"identity": "other@proj-a"})["endpoint"] is None, \
        "an adapter that reports no endpoint state reads as before"
    assert plain.endpoint is None
    unbound = rig.daemon._session_query({"identity": "nobody@proj-a"})
    assert unbound["endpoint"] is None and unbound["endpoint_for"] is None


def test_the_doctor_names_a_bound_session_with_no_endpoint_and_leaves_turns_rows(rig):
    """comms' doctor keeps its own row for a bound session with no endpoint; turns'
    rows are turns' declared checks, which the front door's doctor runs."""
    from moxi_comms.adapters.claude_adapter import ClaudeAdapter
    shim = rig.shim(name="reviewer", scope="proj-a", start=False)
    a = ClaudeAdapter(shim, 1, look_interval=60).start(tolerate_offline=False)
    try:
        assert wait_until(lambda: binding(rig, "reviewer@proj-a") is not None)
        code, out = rig.cli(["doctor"])
    finally:
        a.stop()
    assert code == 0, out
    waiting = [ln for ln in out.splitlines() if ln.startswith("waiting")]
    assert len(waiting) == 1 and "reviewer@proj-a: bound, no turns endpoint for" in \
        waiting[0], out
    assert not [ln for ln in out.splitlines() if ln.startswith("turns")], out


def test_the_doctor_says_none_are_waiting(rig):
    code, out = rig.cli(["doctor"])
    assert code == 0, out
    assert any(ln.startswith("waiting") and "none" in ln for ln in out.splitlines()), out


def test_a_register_carries_the_endpoint_state(rig, monkeypatch):
    from moxi_comms.client import DaemonClient
    seen = []
    real = DaemonClient.call

    def call(self, method, params=None, **kw):
        if method == "bind":
            seen.append(dict(params or {}))
        return real(self, method, params, **kw)

    monkeypatch.setattr(DaemonClient, "call", call)
    shim = rig.shim(name="reviewer", scope="proj-a", start=False)
    shim.endpoint = False
    shim.start()
    assert seen[-1]["endpoint"] is False
    plain = rig.shim(name="other", scope="proj-a")
    assert "endpoint" not in seen[-1]
    del plain
    json.dumps(seen[0])


def test_a_look_releases_held_mail_on_a_passing_read_and_is_no_turn(rig, monkeypatch):
    """`activity kind=look`: the adapter's first look at an idle session. It releases
    what the daemon holds for the name, as a turn end does, and leaves `turned` alone."""
    from conftest import clock_reads_warm, own_activity, queued
    clock_reads_warm(monkeypatch, "claude-shim")
    rig.shim(name="planner", scope="proj-a")
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "--held", "reviewer@proj-a", "later"],
                         name="planner", scope="proj-a")
    assert code == 0, out
    assert wait_until(lambda: queued(rig.daemon, "reviewer@proj-a"))
    assert reviewer.queue == [], "a [held] message waits for a release point"
    with rig.daemon._lock:
        rig.daemon.bindings["reviewer@proj-a"].turned = False
        rig.daemon.turned_ids.discard(reviewer.session_id)
    reviewer.turned = False
    own_activity(reviewer, kind="look")
    assert wait_until(lambda: len(reviewer.queue) == 1), "the look released it"
    assert binding(rig, "reviewer@proj-a").turned is False, "a look is no turn"

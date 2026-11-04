"""The real turns Codex host behind comms, with the fake app-server binary."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys

import pytest
from conftest import clock_reads_warm, cursor_of, wait_until

from fake_codex import calls, control_call, events, install


IDENTITY = "hosted@proj-a"
#: How long a wrapper gets to stop on SIGTERM before it is killed.
STOP_GRACE = 20
PR_SET_PDEATHSIG = 1                  # linux/prctl.h


def die_with_parent():
    """A `preexec_fn`: the kernel sends the child SIGTERM when this thread exits, and
    a child whose parent is already gone exits at once. libc is looked up here, never
    in the child. Python warns that a `preexec_fn` is unsafe when the parent has
    threads, since the forked child can block on a lock another thread held; `Popen`
    offers no other way to set this signal, and the child here takes no lock: it makes
    two system calls and returns. turns' own launch helpers do the same."""
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    parent = os.getpid()

    def _pre() -> None:
        prctl(PR_SET_PDEATHSIG, int(signal.SIGTERM), 0, 0, 0)
        if os.getppid() != parent:
            os._exit(0)

    return _pre


@pytest.fixture
def spawn():
    """`subprocess.Popen` for the wrappers, which nothing else reaps. Each one dies
    with this worker (the parent-death signal: a worker killed past its timeout runs no
    teardown), and the teardown stops every one still running, SIGTERM then SIGKILL,
    and waits for it, however the test ended. turns' host gives the app-server the same
    signal, so it goes with its wrapper."""
    procs = []

    def _spawn(argv, **kwargs):
        proc = subprocess.Popen(argv, preexec_fn=die_with_parent(), **kwargs)
        procs.append(proc)
        return proc

    yield _spawn
    for proc in procs:
        stop(proc)


def stop(proc) -> str:
    """Stop a wrapper, SIGTERM then SIGKILL after `STOP_GRACE`, and wait for it.
    Returns its output (a second call returns it again)."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
    try:
        output = proc.communicate(timeout=STOP_GRACE)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        output = proc.communicate()[0]
    return output or ""


def wrapper(spawn, records, tmp_path):
    """`moxi-comms-codex` for IDENTITY over turns' real host and the fake `codex`."""
    binary = install(tmp_path / "bin")
    work = tmp_path / "work"
    work.mkdir()
    config = tmp_path / "candidate.toml"
    config.write_text(f'[codex.agents."{IDENTITY}"]\ncwd = "{work}"\n')
    env = dict(os.environ)
    env.update({
        "PATH": f"{binary.parent}{os.pathsep}{env.get('PATH', '')}",
        "FAKE_CODEX_DIR": str(records),
        "MOXI_TURNS_RUNTIME_DIR": env["MOXI_COMMS_RUNTIME_DIR"],
    })
    return spawn(
        [sys.executable, "-m", "moxi_comms.adapters.codex", IDENTITY,
         "--config", str(config)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def digest_with(records, message_id) -> list:
    """The `turn/start` and `turn/steer` calls whose input names the message."""
    return [c["method"] for c in calls(records)
            if c["method"] in ("turn/start", "turn/steer")
            and any(f"msg {message_id}" in (item.get("text") or "")
                    for item in c["params"].get("input") or [])]


def test_mail_sent_through_the_daemon_reaches_the_hosted_thread_and_settles(
        rig, tmp_path, monkeypatch, spawn):
    """comms' own path over turns' real host: `moxi comms send`, the daemon, the
    wrapper, turns' host and the app-server. The cursor moves when the turn ends."""
    clock_reads_warm(monkeypatch, "codex")
    records = tmp_path / "records"
    records.mkdir()
    proc = wrapper(spawn, records, tmp_path)
    try:
        assert wait_until(lambda: IDENTITY in rig.daemon.bindings, 20), \
            proc.stdout.read() if proc.poll() is not None else "no binding"
        assert wait_until(lambda: rig.daemon.bindings[IDENTITY].session_id, 20)
        # The launch primes its thread with a turn of its own, which the stub ends
        # itself. Mail sent while that turn runs is steered into it, and the stub's end
        # then races the `complete` below; under load the steer can also outlast the
        # turns client's wait, so comms requeues the digest into a second turn nothing
        # ends. That requeue is a race in turns' client, which reads a slow steer as
        # refused; this test steers around it and does not cover it. The daemon marks the binding `turned` once the adapter reports the
        # priming turn's end, so the mail starts a turn of its own.
        assert wait_until(lambda: rig.daemon.bindings[IDENTITY].turned, 20)
        code, out = rig.cli(["send", "--posture", "none", IDENTITY, "through the daemon"],
                             name="main", scope="proj-a")
        assert code == 0, out
        mid = out.split()[1]
        assert wait_until(lambda: digest_with(records, mid), 20), calls(records)
        assert cursor_of(IDENTITY) != mid, "not before the turn ends"
        control_call(records, {"op": "complete"})
        assert wait_until(lambda: cursor_of(IDENTITY) == mid, 15)
    finally:
        output = stop(proc)
    assert proc.returncode == 0, output


def test_the_wrapper_runs_turns_real_host_and_passes_the_roster_options(
        rig, tmp_path, spawn):
    records = tmp_path / "records"
    records.mkdir()
    binary = install(tmp_path / "bin")
    work = tmp_path / "work"
    work.mkdir()
    writable = tmp_path / "writable"
    writable.mkdir()
    config = tmp_path / "candidate.toml"
    config.write_text(
        f'[codex.agents."{IDENTITY}"]\n'
        f'cwd = "{work}"\n'
        'sandbox = "workspace-write"\n'
        f'writable_roots = ["{writable}"]\n'
        'network_access = true\n'
        'model = "gpt-sample"\n'
    )
    env = dict(os.environ)
    env.update({
        "PATH": f"{binary.parent}{os.pathsep}{env.get('PATH', '')}",
        "FAKE_CODEX_DIR": str(records),
        "MOXI_TURNS_RUNTIME_DIR": env["MOXI_COMMS_RUNTIME_DIR"],
    })
    proc = spawn(
        [sys.executable, "-m", "moxi_comms.adapters.codex", IDENTITY,
         "--config", str(config)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert wait_until(lambda: calls(records, "thread/start"), 20), \
            proc.stdout.read() if proc.poll() is not None else "thread/start did not arrive"
        assert wait_until(lambda: IDENTITY in rig.daemon.bindings, 10)
        params = calls(records, "thread/start")[0]["params"]
        assert params["cwd"] == str(work)
        assert params["approvalPolicy"] == "never"
        assert params["developerInstructions"]
        assert params["model"] == "gpt-sample"
        assert params["sandbox"] == "workspace-write"
        assert params["config"]["agents"] == {"enabled": False}
        settings = params["config"]["sandbox_workspace_write"]
        assert settings["network_access"] is True
        assert str(writable) in settings["writable_roots"]
        # A launch with no window is primed too, with no mail sent, so its thread has a
        # rollout that a later `thread/resume` can load; and the daemon has its id.
        assert wait_until(lambda: calls(records, "turn/start"), 10)
        thread_id = calls(records, "turn/start")[0]["params"]["threadId"]
        assert wait_until(
            lambda: rig.daemon.bindings[IDENTITY].session_id == thread_id, 10)
        assert proc.poll() is None
    finally:
        output = stop(proc)
    assert proc.returncode == 0, output
    assert wait_until(lambda: IDENTITY not in rig.daemon.bindings, 5)
    assert wait_until(lambda: any(row["event"] == "app-server-exit"
                                  for row in events(records)), 5)


def test_a_launch_told_to_resume_resumes_the_thread_under_the_same_name(
        rig, tmp_path, spawn):
    """A first launch starts and primes a thread; a second, `--name ... --resume T`,
    has turns' host resume T, and the name is bound to T as a resume."""
    from moxi_comms import codex_threads

    records = tmp_path / "records"
    records.mkdir()
    first = wrapper(spawn, records, tmp_path)
    try:
        assert wait_until(lambda: calls(records, "turn/start"), 20), \
            first.stdout.read() if first.poll() is not None else "no priming turn"
        thread_id = calls(records, "turn/start")[0]["params"]["threadId"]
        assert wait_until(
            lambda: rig.daemon.bindings[IDENTITY].session_id == thread_id, 10)
        # Once the priming turn has ended the thread has a rollout, which
        # `thread/resume` needs.
        assert wait_until(lambda: rig.daemon.bindings[IDENTITY].turned, 20)
    finally:
        output = stop(first)
    assert first.returncode == 0, output
    assert wait_until(lambda: IDENTITY not in rig.daemon.bindings, 10)
    first_launch = codex_threads.read_meta(IDENTITY)["launch"]

    env = dict(os.environ)
    env.update({
        "PATH": f"{(tmp_path / 'bin')}{os.pathsep}{env.get('PATH', '')}",
        "FAKE_CODEX_DIR": str(records),
        "MOXI_TURNS_RUNTIME_DIR": env["MOXI_COMMS_RUNTIME_DIR"],
    })
    name, scope = IDENTITY.split("@")
    second = spawn(
        [sys.executable, "-m", "moxi_comms.adapters.codex", "--name", name, "--scope",
         scope, "--no-window", "--resume", thread_id],
        cwd=str(tmp_path / "work"), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    try:
        assert wait_until(lambda: calls(records, "thread/resume"), 20), \
            second.stdout.read() if second.poll() is not None else "no thread/resume"
        assert calls(records, "thread/resume")[0]["params"]["threadId"] == thread_id
        assert len(calls(records, "thread/start")) == 1, "no new thread"
        assert wait_until(lambda: IDENTITY in rig.daemon.bindings, 10)
        binding = rig.daemon.bindings[IDENTITY]
        assert (binding.session_id, binding.new_session) == (thread_id, False)
        assert binding.turned is True, "a resumed thread had turns before this launch"
        record = wait_until(lambda: (lambda r: r if r.get("launch") != first_launch
                                     else None)(codex_threads.read_meta(IDENTITY) or {}),
                            10)
        assert record["thread_id"] == thread_id and record["layer_one"], record
        assert second.poll() is None
    finally:
        output = stop(second)
    assert second.returncode == 0, output

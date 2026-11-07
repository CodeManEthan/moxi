#!/usr/bin/env python3
"""Smoke the Codex delivery path through turns' Codex host.

Every root (comms', turns' and the rest of Moxi's) goes under one temporary directory,
and the comms daemon runs on the mock backend there. Keep TMPDIR short (``/tmp``) for
``--dry-run``: comms' and turns' sockets take Moxi's short socket path under a long one,
but the fake ``codex``'s control socket sits under that root at its full path, and a
Unix socket path has a length limit.

``--dry-run`` uses the repository's fake ``codex`` executable. It starts a real comms
daemon and the roster form of ``moxi-comms-codex``, which binds the name and execs
``moxi-turns-codex-host``; the host spawns the fake app-server. It checks the thread's
settings, waits for the launch to open its thread with no turn, then sends mail with
``moxi comms send`` from a sender that holds a binding of its own. The mail goes through
the daemon and the wrapper's adapter into the thread; the script checks that the cursor
moves only when the fake ends the turn, and that stopping the host ends the app-server.

``--live`` puts the requested real Codex binary on a private PATH, then runs the same
daemon and wrapper and hands one delivery to the hosted session through turns' public
API. It spends a model turn and waits for that turn to end ``answer``.

    uv run python scripts/codex-smoke.py --dry-run
    CODEX_HOME=<dir> uv run python scripts/codex-smoke.py --live --codex <path>
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = Path(sys.executable).parent
SCOPE = "codex-smoke"
SENDER = "smoke-sender"
TARGET = "smoke-codex"
IDENTITY = f"{TARGET}@{SCOPE}"

results = []


def check(ok: bool, line: str) -> bool:
    results.append((bool(ok), line))
    print(f"{'PASS' if ok else 'FAIL'}  {line}", flush=True)
    return bool(ok)


def note(line: str) -> None:
    print(f"      {line}", flush=True)


def wait_until(predicate, timeout=20.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def report() -> int:
    failed = [line for ok, line in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    for line in failed:
        print(f"  FAIL  {line}")
    return 1 if failed else 0


def clean_environ() -> dict:
    return {key: value for key, value in os.environ.items()
            if not key.startswith("MOXI_COMMS_")}


def build_env(root: Path, home: Path, runtime: Path, store: Path, binary_dir: Path) -> dict:
    env = clean_environ()
    env.update({
        "MOXI_HOME": str(root / "moxi"),
        "MOXI_COMMS_BACKEND": "mock",
        "MOXI_COMMS_HOME": str(home),
        "MOXI_COMMS_DEVICE": "codexsmoke",
        "MOXI_COMMS_RUNTIME_DIR": str(runtime),
        "MOXI_TURNS_RUNTIME_DIR": str(runtime),
        "MOXI_COMMS_PROJECT": SCOPE,
        "MOXI_COMMS_MOCK_BACKEND": str(store),
        "PYTHONPATH": str(REPO / "src"),
        "PATH": f"{binary_dir}{os.pathsep}{BIN}{os.pathsep}{env.get('PATH', '')}",
    })
    return env


def load_fake_codex():
    tests = str(REPO / "tests")
    if tests not in sys.path:
        sys.path.insert(0, tests)
    return importlib.import_module("fake_codex")


def provision_home(env, store: Path) -> None:
    for key in [key for key in os.environ if key.startswith("MOXI_")]:
        del os.environ[key]
    os.environ.update({key: value for key, value in env.items()
                       if key.startswith("MOXI_")})
    from moxi_comms.mock_record import MockRecord
    from moxi_comms.paths import comms_db
    from moxi_comms.provision import provision

    provision(MockRecord(store), comms_db(), operator=("900001", "operator"))


def write_roster(path: Path, work: Path, model: str | None = "gpt-sample") -> Path:
    """The smoke's roster. The fake codex checks its model; a live run leaves the model
    out, since the real one refuses a name it doesn't serve."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'[codex.agents."{IDENTITY}"]\n'
        f'cwd = "{work}"\n'
        'sandbox = "workspace-write"\n'
        f'writable_roots = ["{work}"]\n'
        'network_access = true\n'
        + (f'model = "{model}"\n' if model else '')
        + f'directors = ["{SENDER}"]\n'
    )
    return path


def start_daemon(env, runtime: Path, store: Path, log: Path):
    handle = log.open("ab")
    proc = subprocess.Popen(
        [str(BIN / "moxi-comms-daemon"), "--backend-store", str(store)],
        env=env, stdout=handle, stderr=handle,
    )
    proc.output_handle = handle
    from moxi_comms.paths import daemon_socket

    socket = daemon_socket()               # the address this process's roots give
    if not wait_until(socket.exists, 15):
        stop(proc)
        tail = log.read_text(errors="replace").strip().splitlines()[-5:]
        raise RuntimeError("the daemon never opened its socket:\n  " + "\n  ".join(tail))
    return proc


def comms(env, argv):
    return subprocess.run([str(BIN / "moxi"), "comms", *argv], env=env,
                          capture_output=True, text=True, timeout=60)


def launch(env, roster: Path, log: Path):
    handle = log.open("ab")
    proc = subprocess.Popen(
        [str(BIN / "moxi-comms-codex"), IDENTITY, "--config", str(roster)],
        env=env, stdout=handle, stderr=handle,
    )
    proc.output_handle = handle
    return proc


def stop(proc, timeout=20.0):
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    handle = getattr(proc, "output_handle", None)
    if handle is not None:
        handle.close()


def thread_id_of(identity: str):
    from moxi_comms import codex_threads

    return codex_threads.read(identity)


def hosted_session(timeout: float):
    """The turns session the Codex host published, or None."""
    import moxi_turns as turns

    return wait_until(lambda: next((item for item in turns.sessions()
                                    if item.harness == "codex"), None), timeout)


def opened(session) -> bool:
    """The launch has opened its thread: the load's Ready came, and no turn is open."""
    try:
        facts = session.facts()
    except Exception:                                          # noqa: BLE001
        return False
    return facts.ready is not None and not facts.turn_open


def sender_session():
    """Hold SENDER's binding in this process, with the server's own daemon arm: a send
    needs a live binding, and each `moxi comms send` runs as a root of it."""
    from moxi_comms.claimhold import ClaimServer

    server = ClaimServer(SENDER, SCOPE, kind="claude-shim", arm=True)
    return server.start()


def bound(server) -> bool:
    from moxi_comms.claimhold import BOUND

    return server.holder.state == BOUND


def send(env, sender, text) -> str:
    """One send to the Codex identity from `sender`'s session; returns its id."""
    proc = sender.spawn([str(BIN / "moxi"), "comms", "send", IDENTITY, text,
                         "--posture", "none"],
                        env={**env, "MOXI_COMMS_NAME": SENDER, "MOXI_COMMS_SCOPE": SCOPE},
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = proc.communicate(timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"moxi comms send failed: {(err or out).strip()}")
    return out.split()[1]


def cursor_of(identity: str):
    """The name's cursor in comms.db, which the daemon keeps."""
    from moxi_comms.store import books

    row = books.read_cursor(identity)
    return None if row is None else row["cursor"]


def write_rollout(sessions: Path, thread_id: str) -> None:
    """A rollout for the thread whose one turn ended seconds ago, as a real Codex writes
    one. The fake writes none, and without one the cache reads `unknown` once a turn of
    the thread has ended."""
    load_fake_codex()                       # puts the tests on sys.path
    clock = importlib.import_module("test_cache_clock")
    clock.Rollout(thread_id).turn("t1", time.time() - 8, model="gpt-5.6-terra",
                                  length=2.0).write(sessions)


def digest_with(records: Path, message_id: str) -> bool:
    """Whether a `turn/start` or `turn/steer` the fake app-server got carries the id."""
    fake_codex = load_fake_codex()
    for call in fake_codex.calls(records):
        if call["method"] in ("turn/start", "turn/steer"):
            for item in call["params"].get("input") or []:
                if item.get("type") == "text" and f"msg {message_id}" in item.get("text", ""):
                    return True
    return False


def prepare(binary: str | None = None, model: str | None = "gpt-sample"):
    root = Path(tempfile.mkdtemp(prefix="moxi-codex-smoke-"))
    home = root / "home"
    runtime = root / "run"
    (runtime / "comms").mkdir(parents=True)
    store = root / "mock" / "record.json"
    store.parent.mkdir()
    work = root / "work"
    work.mkdir()
    binary_dir = root / "bin"
    binary_dir.mkdir()
    if binary is not None:
        (binary_dir / "codex").symlink_to(Path(binary).resolve())
    env = build_env(root, home, runtime, store, binary_dir)
    provision_home(env, store)
    from moxi_comms import config

    roster = write_roster(config.config_path(env), work, model)
    note(f"root {root}")
    return root, runtime, store, work, binary_dir, env, roster


def dry_run() -> int:
    fake_codex = load_fake_codex()
    root, runtime, store, work, binary_dir, env, roster = prepare()
    records = root / "fake-records"
    records.mkdir()
    fake_codex.install(binary_dir)
    env["FAKE_CODEX_DIR"] = str(records)
    env["CODEX_HOME"] = str(root / "codex-home")
    env["HOME"] = str(root / "home-dir")
    daemon = adapter = sender = None
    try:
        unknown = subprocess.run(
            [str(BIN / "moxi-comms-codex"), "stranger@nowhere", "--dry-run",
             "--config", str(roster)], env=env, capture_output=True, text=True,
        )
        check(unknown.returncode == 2 and "stranger@nowhere" in unknown.stderr,
              "the roster form refuses an identity its roster does not name (exit 2)")
        plan = subprocess.run(
            [str(BIN / "moxi-comms-codex"), IDENTITY, "--dry-run", "--config",
             str(roster)], env=env, capture_output=True, text=True,
        )
        check(plan.returncode == 0 and "moxi-turns-codex-host" in plan.stdout
              and "agents.enabled=false" in plan.stdout,
              "the dry run prints the host line, multi-agent off")

        daemon = start_daemon(env, runtime, store, root / "daemon.log")
        adapter = launch(env, roster, root / "wrapper.log")
        check(bool(wait_until(lambda: IDENTITY in comms(env, ["agents"]).stdout, 20)),
              "the wrapper bound the name on the daemon socket")
        starts = wait_until(lambda: fake_codex.calls(records, "thread/start"), 20) or []
        check(bool(starts), "the host spawned the fake app-server and started a thread")
        params = starts[0]["params"] if starts else {}
        settings = params.get("config", {}).get("sandbox_workspace_write", {})
        check(params.get("approvalPolicy") == "never"
              and params.get("sandbox") == "workspace-write"
              and params.get("model") == "gpt-sample",
              "the start carries the host's approval policy and the roster's model and "
              "sandbox")
        check(settings.get("network_access") is True
              and str(work) in settings.get("writable_roots", [])
              and params.get("config", {}).get("agents") == {"enabled": False},
              "the start carries network, writable roots and the disabled agents switch")

        session = hosted_session(20)
        check(session is not None, "turns published the hosted session through its API")
        check(session is not None and bool(wait_until(lambda: opened(session), 20)),
              "the launch opened its thread")
        check(bool(fake_codex.calls(records, "thread/inject_items"))
              and not fake_codex.calls(records, "turn/start"),
              "the thread was opened with an injected item, not a turn")
        check(bool(wait_until(lambda: thread_id_of(IDENTITY), 10)),
              "the adapter recorded the hosted thread id")

        # comms' own path: moxi comms send -> daemon -> adapter -> turns -> app-server.
        write_rollout(root / "codex-home" / "sessions", thread_id_of(IDENTITY))
        sender = sender_session()
        check(bool(wait_until(lambda: bound(sender), 10)), "the sender holds its binding")
        mail = send(env, sender, "codex smoke through the daemon")
        check(bool(wait_until(lambda: digest_with(records, mail), 20)),
              f"the digest for msg {mail} reached the thread through the daemon")
        check(cursor_of(IDENTITY) != mail, "the cursor waits for the digest's turn to end")
        fake_codex.control_call(records, {"op": "complete"})
        check(bool(wait_until(lambda: cursor_of(IDENTITY) == mail, 15)),
              f"the turn's end advanced the cursor to {mail}")

        app_pid = fake_codex.events(records)[0]["pid"]
        stop(adapter)
        adapter = None
        check(wait_until(lambda: any(row["event"] == "app-server-exit"
                                     for row in fake_codex.events(records)), 10),
              f"stopping the host ended the app-server process {app_pid}")
        check(bool(wait_until(lambda: IDENTITY not in comms(env, ["agents"]).stdout, 10)),
              "the name is unbound once the host has gone")
        return report()
    finally:
        if sender is not None:
            sender.stop()
        stop(adapter)
        stop(daemon)
        shutil.rmtree(root, ignore_errors=True)


def codex_home() -> Path:
    """Return the explicit Codex home, or Codex's default home."""
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def own_home_refusal(binary: str) -> str | None:
    """Refuse a non-PATH Codex binary unless it has a separate home."""
    on_path = shutil.which("codex")
    same = on_path is not None and os.path.realpath(on_path) == os.path.realpath(binary)
    if same:
        return None
    home = os.environ.get("CODEX_HOME")
    default = os.path.realpath(Path.home() / ".codex")
    if not home or os.path.realpath(home) == default:
        return (f"{binary} is not the codex on PATH ({on_path}); set CODEX_HOME to a "
                "directory of its own for this run")
    return None


def live_run(binary: str) -> int:
    if not Path(binary).is_file():
        raise SystemExit(f"--codex does not name a file: {binary}")
    refusal = own_home_refusal(binary)
    if refusal:
        raise SystemExit(refusal)
    root, runtime, store, _work, _binary_dir, env, roster = prepare(binary, model=None)
    env["CODEX_HOME"] = str(codex_home())
    daemon = adapter = None
    try:
        daemon = start_daemon(env, runtime, store, root / "daemon.log")
        adapter = launch(env, roster, root / "wrapper.log")
        check(bool(wait_until(lambda: IDENTITY in comms(env, ["agents"]).stdout, 60)),
              "the live wrapper registered after turns loaded the real app-server")
        import moxi_turns as turns

        session = hosted_session(60)
        check(session is not None, "turns published the hosted session through its API")
        if session is None:
            return report()
        wait_until(lambda: opened(session), 60)
        delivery = session.deliver("Reply with a short acknowledgement, then stop.",
                                   ref="smoke-live")
        started = delivery.wait_started(60)
        ended = delivery.wait_ended(300)
        check(isinstance(started, turns.Started) and bool(started.turn_id)
              and isinstance(ended, turns.Ended) and ended.outcome == "answer",
              f"the real Codex turn {getattr(started, 'turn_id', started)} completed "
              "and settled")
        return report()
    finally:
        stop(adapter)
        stop(daemon)
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="real comms and turns processes with the fake codex binary")
    mode.add_argument("--live", action="store_true",
                      help="run a real Codex turn; spends tokens")
    parser.add_argument("--codex", default="codex", help="real Codex binary for --live")
    args = parser.parse_args()
    binary = shutil.which(args.codex) or args.codex
    return dry_run() if args.dry_run else live_run(binary)


if __name__ == "__main__":
    raise SystemExit(main())

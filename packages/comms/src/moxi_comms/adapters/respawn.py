"""A new Claude adapter for a session whose adapter is gone while `claude` runs.

The adapter `moxi comms claude` forked (`session.launch`) lives as long as `claude`; one
that dies first (a crash, a kill, the OOM killer) leaves its claim file under a live
`claude`, with a socket that answers no one (`claimfile.adapter_gone`). An MCP shim that
Claude Code spawned again bound its session again from its claim file. This does the same
for the adapter: everything a new one needs outlives the old one.

    claude          the claim file's `tree` root: its pid and start time. A pidfd taken
                    on that pid is the adapter's watch once the start time still matches,
                    so a reused pid cannot stand in for it
    the identity    the claim file, which the new shim reads (`ClaudeShim._make_holder`),
                    and the session's stamps
    the tool token  the session's environment, which turns hands comms' listener
    the endpoint    `turns.find(pid=<claude>)`, as any adapter finds it

comms' listener calls `respawn_adapter` at the session's start and prompt
(`listener.respawn_gone_adapter`). It starts `python -I -m moxi_comms.adapters.respawn`
in a session of its own, in the session's runtime directory: isolated, so neither the
endpoint's working directory nor a PYTHONPATH or PYTHONHOME in the session's
environment can load code into it. That process forks the adapter and leaves at once,
so the adapter is nobody's child, and the listener waits at most `wait_s` for its word
(`adapters.word`). The new adapter holds a lock beside the claim file,
`respawn-<key>.lock`, for its whole life (`moxi.files.lock`): a second one, started by a
second start or prompt while the first binds, finds it held, says `word.STARTING` and
ends before it touches the claim file or its socket.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("comms.claude.respawn")

#: The claim file kind of the adapter `moxi comms claude` forks.
CLAUDE_KIND = "claude-shim"
#: The lock a respawned adapter holds while it lives.
LOCK_PREFIX = "respawn-"
#: How long the starting process may take to fork the adapter and leave, in seconds.
FORK_WAIT_S = 5.0


class CannotRespawn(Exception):
    """No new adapter can take this session: its text says why."""


def lock_path(key: str, env=None) -> Path:
    """The lock a respawned adapter for the session keyed `key` holds, beside its claim
    file."""
    from ..claimfile import claim_dir

    return claim_dir(env) / f"{LOCK_PREFIX}{key}.lock"


def claude_root(record: dict):
    """The record's live `tree` root (the `claude` process), or None."""
    from .. import claimfile
    from ..membership import DEFAULT_PROC, TREE

    for root in claimfile.roots_of(record):
        if root.reach == TREE and DEFAULT_PROC.alive(root.pid, root.start):
            return root
    return None


def respawn_adapter(env: dict, key: str, record: dict, *, wait_s: float):
    """Start a new adapter for the session keyed `key`, whose claim file was read as
    the record given, with the session's environment `env`, and wait at most `wait_s`
    seconds for its word.

    Answers the word, `(code, reason)`, `word.STARTING` among them when a new adapter
    already serves the session; None when it didn't come in time (the adapter goes on
    binding). Raises `CannotRespawn` when none can be started."""
    if record.get("kind") != CLAUDE_KIND:
        raise CannotRespawn("its adapter was not one `moxi comms claude` started")
    root = claude_root(record)
    if root is None:
        raise CannotRespawn("its claude process could not be found")
    if not sys.platform.startswith("linux"):
        raise CannotRespawn("this system has no process handle to watch claude with")
    if key in ("", ".", "..") or "/" in key:
        raise CannotRespawn(f"its claim file's key {key!r} names no session directory")
    r, w = os.pipe()
    try:
        argv = [sys.executable, "-I", "-m", __name__, key, str(root.pid), root.start,
                str(w)]
        try:
            starter = subprocess.Popen(
                argv, env=env, cwd=session_dir(key, env), pass_fds=(w,),
                start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise CannotRespawn(f"a new adapter could not be started: {exc}") from exc
        os.close(w)
        w = None
        try:
            starter.wait(FORK_WAIT_S)
        except subprocess.TimeoutExpired:
            return None
        from .word import await_word

        return await_word(r, wait_s)
    finally:
        if w is not None:
            os.close(w)
        os.close(r)


def session_dir(key: str, env: dict) -> Path:
    """The runtime directory of the session keyed `key` (0700), made when it is gone:
    the new adapter's working directory, which `Session.write` keeps."""
    from .session import sessions_dir

    path = sessions_dir(env) / key
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    os.chmod(path, 0o700)
    return path


def main(argv=None) -> int:                               # pragma: no cover - forked
    """`python -m moxi_comms.adapters.respawn <key> <claude pid> <start> <ready fd>`:
    fork the adapter and leave. The adapter takes the session's respawn lock, a pidfd on
    `claude` whose start time it checks, and serves the session as the launch's adapter
    did."""
    key, pid, start, ready = (argv if argv is not None else sys.argv[1:])
    ready_fd, harness_pid = int(ready), int(pid)
    if os.fork() != 0:
        return 0
    from moxi import files

    from ..membership import DEFAULT_PROC
    from .session import _adapter_process
    from .terminal import pidfd_open
    from .word import FAILED, STARTING, teller

    with contextlib.ExitStack() as held:
        try:
            held.enter_context(files.lock(lock_path(key), timeout_s=0))
        except files.LockTimeout:
            teller(ready_fd)(STARTING)
            return 0
        try:
            handle = pidfd_open(harness_pid)
        except OSError as exc:
            teller(ready_fd)(FAILED, f"claude could not be watched: {exc}")
            return 1
        if DEFAULT_PROC.start_time(harness_pid) != start:
            teller(ready_fd)(FAILED, "its claude process has ended")
            return 1
        env = dict(os.environ)
        session = session_of(key, env)
        session.write()
        return _adapter_process(session, env, harness_pid, handle, ready_fd,
                                respawned=True)


def session_of(key: str, env: dict):
    """The launch's `Session` for the session keyed `key`, from the stamps in its
    environment. The claim itself comes from the claim file, which the new shim reads."""
    from .. import directors as directors_mod
    from ..constants import COMMAND_ENV, GLOBAL_ENV, NAME_ENV, PROJECT_ENV, SCOPE_ENV
    from .session import Session, _is_uuid

    session = Session(key, name=env.get(NAME_ENV), scope=env.get(SCOPE_ENV),
                      global_=bool(env.get(GLOBAL_ENV)), project=env.get(PROJECT_ENV),
                      directors=directors_mod.from_env(env), comms=env.get(COMMAND_ENV))
    session.stated_id = key if _is_uuid(key) else None
    return session


__all__ = ["CLAUDE_KIND", "CannotRespawn", "FORK_WAIT_S", "LOCK_PREFIX",
           "claude_root", "lock_path", "main", "respawn_adapter", "session_dir",
           "session_of"]


if __name__ == "__main__":                                 # pragma: no cover
    raise SystemExit(main())

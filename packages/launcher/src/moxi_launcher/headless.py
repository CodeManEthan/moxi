"""A headless launch continued and ended by its caller (interface 4).

    continue_headless(session_id, text, by=None) -> LaunchResult   # a headless resume
    end_headless(request_id) -> Ended                      # end its whole process tree

**A headless resume** launches the headless row that holds `session_id` again, resumed,
with `text` as its prompt: a replay of the stored row (`api.ReplayRequest`, no preset
and no `[defaults]` read), so it keeps the row's values and the stamps its request gave
(`STAMPS`, kept in its values), under a request id of its own, launched by `by` (the
caller's identity when None, as a launch with no actor). A resume keeps the session
id, so every round of a session continued this way names one transcript. It is refused,
with nothing written, when no launch holds the id (`not-launched`), the launch was not
headless (`not-headless`), or the session is still running or starting (`live`: the
store's check as the row is written).

**Ending a headless launch** ends its process and every descendant, on every system. The
row's pid and start time name the process; when `moxi.proc.identity` no longer matches
them (the launch has ended, and the pid may be another process's) nothing is signalled.
TERM first, then KILL to whatever is left after `KILL_GRACE_S`. On Linux and macOS the
headless target starts the harness in a session of its own, so its process group is
signalled, then every descendant found from the pid, for one that left the group. On
Windows, where it starts in a detached process group with no job, each descendant found
from the pid is ended, the root first so it starts no more.
"""

from __future__ import annotations

import os
import signal
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from moxi import proc

from . import catalog, first, store

#: The key a launch's values keep the stamps its request gave under, so a headless resume
#: stamps them again.
STAMPS = "stamps"
#: How long a headless launch's tree has between TERM and KILL, in seconds.
KILL_GRACE_S = 2.0

#: The outcomes of `end_headless`.
ENDED = "ended"
GONE = "gone"
FAILED = "failed"


class NotHeadless(catalog.Refused):
    """A headless resume of a launch that did not run headless."""

    code = "not-headless"


@dataclass(frozen=True)
class Ended:
    """What `end_headless` did: `outcome` `ended` (the tree was ended), `gone` (no
    process of the launch runs: none recorded, or its pid names another process now) or
    `failed`, with `error` saying why."""

    outcome: str
    error: str = ""


@contextmanager
def _store(env: Mapping[str, str] | None):
    if not store.path(env).exists():
        yield None
        return
    conn = store.connect(env=env, create=False)
    try:
        yield conn
    finally:
        conn.close()


def continue_headless(session_id: str, text: str, *, request_id: str | None = None,
                      by: str | None = None, env: Mapping[str, str] | None = None,
                      launch: Callable[..., object] | None = None):
    """Resume the headless session `session_id` with `text` as its prompt, launched by
    `by` (the caller's identity when None); returns the launch's `api.LaunchResult`.
    Raises a refusal with nothing written. `launch` stands in for `api.launch`."""
    from . import api, target

    caller = dict(os.environ if env is None else env)
    with _store(env) as conn:
        row = store.resolve(conn, session_id) if conn is not None else None
    if row is None:
        raise target.NotLaunched()
    if row.target != "headless":
        raise NotHeadless("target", f"launch {row.request_id} ran in its {row.target}, and "
                                    "only a headless launch is continued headless",
                          "resume it with moxi launcher resume")
    stamps = {str(k): str(v) for k, v in dict(row.values.get(STAMPS) or {}).items()}
    request = api.ReplayRequest(row, resume_of=row.request_id,
                                request_id=request_id or str(uuid.uuid4()),
                                session_id=row.newest_session_id or row.session_id,
                                actor=by or first.actor(caller), brief=text,
                                stamps=stamps)
    try:
        return (launch or api.launch)(request, env=env)
    except store.SessionLive as exc:
        from .resume import met_refusal

        raise met_refusal(exc) from None


def end_headless(request_id: str, *, env: Mapping[str, str] | None = None) -> Ended:
    """End the running headless launch `request_id`'s whole process tree. Never raises
    for a launch that has ended or was never recorded: that is `gone`."""
    try:
        with _store(env) as conn:
            row = store.get(conn, request_id) if conn is not None else None
    except Exception as exc:  # noqa: BLE001 - a launch log that can't be read
        return Ended(FAILED, f"the launch log can't be read: {exc}")
    if row is None or row.pid is None or row.pid_started is None:
        return Ended(GONE)
    root = proc.ProcId(int(row.pid), float(row.pid_started))
    if not proc.alive(root):
        return Ended(GONE)
    try:
        end_tree(root)
    except Exception as exc:  # noqa: BLE001 - an end that failed is said, not raised
        return Ended(FAILED, str(exc))
    return Ended(ENDED) if not proc.alive(root) else Ended(FAILED, "it still runs")


def end_tree(root: proc.ProcId) -> None:
    """End the process `root` names and every descendant, TERM first and KILL to what is
    left after `KILL_GRACE_S`; nothing when its pid no longer names that process."""
    import psutil

    if not proc.alive(root):
        return
    try:
        top = psutil.Process(root.pid)
        below = top.children(recursive=True)
    except psutil.Error:
        top, below = None, []
    posix = sys.platform != "win32"
    if posix:
        _signal_group(root.pid, signal.SIGTERM)
    for item in ([top] if top is not None else []) + below:
        _quiet(item.terminate)
    deadline = time.monotonic() + KILL_GRACE_S
    while time.monotonic() < deadline and (
            proc.alive(root) or any(_quiet(item.is_running) for item in below)):
        time.sleep(0.05)
    if posix and (proc.alive(root) or any(_quiet(item.is_running) for item in below)):
        _signal_group(root.pid, signal.SIGKILL)
    for item in ([top] if top is not None and proc.alive(root) else []) + below:
        _quiet(item.kill)
    _reap(root.pid)


def _signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except (OSError, AttributeError):
        pass


def _quiet(call):
    import psutil

    try:
        return call()
    except psutil.Error:
        return None


def _reap(pid: int) -> None:
    """Reap `pid` when it is this process's child, so it leaves no zombie that reads
    alive."""
    if sys.platform == "win32":
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass


__all__ = ["ENDED", "FAILED", "GONE", "KILL_GRACE_S", "STAMPS", "Ended", "NotHeadless",
           "continue_headless", "end_headless", "end_tree"]

"""`moxi launcher fork`: a new session that starts from a copy of another session's
conversation.

    fork(session_id)               # fork SESSION_ID [--target slot|terminal] [--slot N]

**A fork is a replay of the launch row of the session it forks** (`api.ReplayRequest`
with `fork_of`), which may be live: that is the point, and the session forked goes on as
it was. Claude Code gets `--resume <id> --fork-session` beside the session id the launcher
mints for the fork; turns' Codex host gets `--fork <thread id>`, which forks the thread
where `--resume` resumes it. What the fork copies is each catalog row's `fork` class
(`catalog.forked`): the rows that shape the request the harness sends are copied as the
parent ran them, since a fork that sends another prefix misses the parent's prompt cache;
the rows about who the session is, what its first turn says and how it lives and ends are
dropped, since a fork is a short, nameless reader; the rows the launcher composes for each
launch are composed again. So a fork has no comms name, no start posture, no brief and no
preset, and its row records `fork_of`, the session id it copies, which makes its launch
kind `fork`.

**Its environment** is built as a replay's, from the parent row's recorded base and the
launcher's settings, never from the process that asked for the fork, so the comms name
and claim socket of the session it copies never reach it. A caller of `api.fork` may add
`stamps` (see there).

**Where it runs** is the verb's own choice, `target`: this terminal (the default) or a
slot, `slot` naming one (the lowest free one when unset). On Linux and macOS a fork into
this terminal does not return: the process becomes the harness.

It is refused, with nothing written, when:

- no launch holds the id (`not-launched`);
- the launch has no session id (`no-session-id`): a Codex launch made without turns'
  host never learned its thread id;
- its harness is no longer one the launcher launches (`not-catalogued`);
- it is a Codex session and turns' Codex host can't start here (`not-yet`).

The new session's id is printed once the launch knows it: at once for Claude Code, whose
id is minted, and for a Codex fork into a slot once its host is ready.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from moxi import exit as codes

from . import catalog, first, relaunch, store
from .target import NotLaunched

Refused = relaunch.Refused
Result = relaunch.Result
#: The targets a fork runs in.
TARGETS = ("terminal", "slot")


def fork(session_id: str, *, target: str = "terminal", slot: int | str | None = None,
         stamps: Mapping[str, str] | None = None, request_id: str | None = None,
         caller_env: Mapping[str, str] | None = None,
         launch: Callable[..., Any] | None = None,
         warn: Callable[[str], None] | None = None,
         conn: sqlite3.Connection | None = None) -> Result:
    """Fork the session `session_id`, and say what the launch did: its lines and its
    data (`outcome` `forked` or `failed`, the new `session_id`, `fork_of` and the
    launch's own fields). Raises a refusal with nothing written."""
    result, parent = start(session_id, target=target, slot=slot, stamps=stamps,
                           request_id=request_id, caller_env=caller_env, launch=launch,
                           warn=warn, conn=conn)
    lines = (result.line,)
    if result.outcome is not None and result.outcome.line() != result.line:
        lines += (result.outcome.line(),)
    if result.session_id:
        lines += (f"session {result.session_id}, a fork of session {parent}",)
    return Result(result.exit_code, lines, data={
        "outcome": "forked" if result.exit_code == codes.OK else "failed",
        "state": result.state, "reason": result.reason, "request_id": result.request_id,
        "session_id": result.session_id, "fork_of": parent, "target": result.target,
        "slot": result.slot, "duplicate": result.duplicate})


def start(session_id: str, *, target: str = "terminal",
          slot: int | str | None = None, stamps: Mapping[str, str] | None = None,
          request_id: str | None = None, caller_env: Mapping[str, str] | None = None,
          launch: Callable[..., Any] | None = None,
          warn: Callable[[str], None] | None = None,
          conn: sqlite3.Connection | None = None) -> tuple[Any, str]:
    """Launch the fork of `session_id`; return the launch's `api.LaunchResult`, with the
    new session's id once the launch log knows it, and the id of the session forked (the
    one its launch was last known by). Raises a refusal with nothing written. `launch`
    stands in for `api.launch`."""
    from . import api

    if target not in TARGETS:
        raise catalog.BadValue("target", f"a fork runs in this terminal or a slot, not "
                                         f"{target}",
                               "fork it with --target terminal or --target slot")
    if slot is not None and target != "slot":
        raise catalog.BadValue("slot", f"slot {slot} is named, but the fork's target is "
                                       f"{target} and only a slot launch takes a slot",
                               "fork it with --target slot, or name no slot")
    env = dict(os.environ if caller_env is None else caller_env)
    with relaunch._store(conn) as c:
        row = store.resolve(c, session_id)
        if row is None:
            raise NotLaunched()
        parent = row.newest_session_id or row.session_id
        refused = refusal(row, parent)
        if refused is not None:
            raise refused
    request = api.ReplayRequest(row, fork_of=parent, target=target, slot=slot,
                                stamps=dict(stamps or {}),
                                request_id=request_id or str(uuid.uuid4()),
                                actor=first.actor(env))
    result = (launch or api.launch)(request, env=caller_env, warn=warn)
    if result.session_id is None and not result.duplicate:
        result = _learned(result, conn)
    return result, str(parent)


def refusal(row: store.LaunchRow, session_id: str | None) -> Refused | None:
    """The refusal a fork of `row`'s session meets, or None."""
    if row.harness not in catalog.HARNESSES:
        return Refused("not-catalogued", f"the launcher no longer launches {row.harness}",
                       f"fork it with {row.harness} itself")
    if not session_id:
        return Refused("no-session-id", f"launch {row.request_id} has no session id to "
                                        "fork",
                       "a Codex launch made without turns' host never learned its thread "
                       "id")
    return None


def _learned(result: Any, conn: sqlite3.Connection | None) -> Any:
    """`result` with the session id its launch has learned since it started (a Codex
    thread's, once its host is ready), when the launch log holds one."""
    import dataclasses

    try:
        with relaunch._store(conn) as c:
            row = store.get(c, result.request_id)
    except (NotLaunched, store.Busy):
        return result
    if row is None or not row.session_id:
        return result
    return dataclasses.replace(result, session_id=row.session_id)


__all__ = ["Refused", "Result", "TARGETS", "fork", "refusal", "start"]

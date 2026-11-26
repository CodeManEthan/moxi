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

**Its model** is the one the session forked runs, so the fork shares its prompt cache: the
row's model when the row names one, else the model pin a relaunch takes (`pin.choose`),
the model the session's transcript last used, since a session whose row names no model
may run another (after `/model`, or after a pinned relaunch). As a relaunch's, the pin is
the fork's model and a `model-pin` event on its row, never a value in the row, and when
nothing can be read the fork runs the harness's default model.

**Its environment** is built as a replay's, from the parent row's recorded base and the
launcher's settings, never from the process that asked for the fork, so the comms name
and claim socket of the session it copies never reach it. A caller of `api.fork` may add
`stamps`, variables set on the fork after the scrub. Each must be one a module owns and
lists in its `SESSION_ENV`; its own prefix (`MOXI_<MODULE>_`) says which module owns it.
The launcher's and turns' are refused, since the launch stamps those itself
(`stamp_refusal`).

**Where it runs** is the verb's own choice, `target`: this terminal (the default) or a
slot, `slot` naming one (the lowest free one when unset). On Linux and macOS a fork into
this terminal does not return: the process becomes the harness.

It is refused, with nothing written, when:

- no launch holds the id (`not-launched`);
- the launch has no session id (`no-session-id`): a Codex launch made without turns'
  host never learned its thread id;
- its harness is no longer one the launcher launches (`not-catalogued`);
- it is a Codex session and turns' Codex host can't start here (`not-yet`), or the host
  found has no `--fork` (`not-yet`, naming the turns version it needs);
- a stamp is not one a module owns and lists, or is the launcher's or turns'
  (`bad-value`).

The new session's id is printed once the launch knows it: at once for Claude Code, whose
id is minted, and for a Codex fork into a slot once its host is ready.

**A recall's slot fork** (`recall_fork`) is a launch whose recorded environment carries
memory's fork stamp (`SUBCONSCIOUS_FORK_ENV`, read from memory's API): memory forked it
to answer one recall and ends it when the recall ends. Park leaves it running, crash
discovery settles its slot and restore drops its entry, so its slot is never reserved
for it and it is never brought back. Where memory can't be loaded, no launch counts as
one.
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from moxi import exit as codes
from moxi import modules as moxi_modules
from moxi import names as moxi_names

from . import catalog, first, pin, relaunch, store
from .target import NotLaunched

Refused = relaunch.Refused
Result = relaunch.Result
#: The targets a fork runs in.
TARGETS = ("terminal", "slot")
#: The first turns whose Codex host takes `--fork`.
CODEX_FORK_TURNS = "0.2.6"
#: The modules whose variables a fork's caller may not stamp: the launch stamps them
#: itself.
UNSTAMPED = ("launcher", "turns")


def recall_fork(row: store.LaunchRow | None) -> bool:
    """Whether `row` launched a recall's slot fork: its recorded environment carries
    memory's fork stamp."""
    if row is None:
        return False
    found = moxi_modules.load("memory", interface=1)
    name = getattr(found.api, "SUBCONSCIOUS_FORK_ENV", None) if found.ok else None
    if not name:
        return False
    try:
        env = row.env
    except ValueError:
        return False
    return isinstance(env, Mapping) and bool(env.get(name))


def fork(session_id: str, *, target: str = "terminal", slot: int | str | None = None,
         stamps: Mapping[str, str] | None = None, request_id: str | None = None,
         caller_env: Mapping[str, str] | None = None,
         launch: Callable[..., Any] | None = None,
         warn: Callable[[str], None] | None = None,
         conn: sqlite3.Connection | None = None, sessions: Any = pin.LOOK) -> Result:
    """Fork the session `session_id`, and say what the launch did: its lines and its
    data (`outcome` `forked` or `failed`, the new `session_id`, `fork_of` and the
    launch's own fields). Raises a refusal with nothing written."""
    result, parent = start(session_id, target=target, slot=slot, stamps=stamps,
                           request_id=request_id, caller_env=caller_env, launch=launch,
                           warn=warn, conn=conn, sessions=sessions)
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
          conn: sqlite3.Connection | None = None,
          sessions: Any = pin.LOOK) -> tuple[Any, str]:
    """Launch the fork of `session_id`; return the launch's `api.LaunchResult`, with the
    new session's id once the launch log knows it, and the id of the session forked (the
    one its launch was last known by). Raises a refusal with nothing written. `launch`
    stands in for `api.launch`, `sessions` for sessions' API, which the model pin reads
    through (None: not installed)."""
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
    chosen = pin.choose(row, parent, sessions=sessions, env=env)
    request = api.ReplayRequest(row, fork_of=parent, target=target, slot=slot,
                                model_pin=chosen.model if chosen.pinned else None,
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


def stamp_refusal(stamps: Mapping[str, str]) -> catalog.BadValue | None:
    """The refusal the fork's `stamps` meet, or None. Each name must be listed in the
    `SESSION_ENV` of the module its prefix names (a key ending in `_` lists every name
    under it), that module must be present, and it must not be the launcher or turns."""
    for name in sorted(stamps):
        stamper = next((module for module in UNSTAMPED
                        if name.startswith(moxi_names.env_prefix(module))), None)
        if stamper is not None:
            return catalog.BadValue("stamps", f"{name} is {stamper}'s, which the launch "
                                              "stamps itself",
                                    "leave it out of the stamps")
        owner = _owner(name)
        if owner is None:
            return catalog.BadValue("stamps", f"{name} is no module's variable",
                                    "stamp only a variable your module lists in its "
                                    "SESSION_ENV")
        listed = moxi_modules.load(owner)
        if not listed.ok or not _lists(listed.session_env, name):
            return catalog.BadValue("stamps", f"{name} is not in {owner}'s SESSION_ENV",
                                    f"list it in {owner}'s SESSION_ENV, or leave it out "
                                    "of the stamps")
    return None


def host_refusal(harness: str) -> catalog.Refused | None:
    """The refusal a fork of a `harness` session meets for the turns installed here, or
    None: a Codex fork needs turns' Codex host to take `--fork`, which turns
    `CODEX_FORK_TURNS` added. Without turns the composer refuses the fork itself."""
    from .compose import NotYet

    if harness != "codex":
        return None
    found = moxi_modules.load("turns")
    if not found.ok or not found.version:
        return None
    if _release(found.version) >= _release(CODEX_FORK_TURNS):
        return None
    return NotYet("fork", f"turns' Codex host here is moxi-turns {found.version}, which "
                          f"has no --fork; a Codex fork needs moxi-turns "
                          f"{CODEX_FORK_TURNS} or newer",
                  f"upgrade moxi-turns to {CODEX_FORK_TURNS} or newer")


def _release(version: str) -> tuple[int, ...]:
    """The release numbers `version` starts with: (0, 2, 5) for `0.2.5.dev1`."""
    found = re.match(r"\d+(?:\.\d+)*", version)
    return tuple(int(part) for part in found.group().split(".")) if found else ()


def _owner(name: str) -> str | None:
    """The installed module whose prefix `name` carries, the longest one when two do."""
    owners = [entry.name for entry in moxi_modules.installed()
              if moxi_names.is_module_name(entry.name)
              and name.startswith(moxi_names.env_prefix(entry.name))]
    return max(owners, key=len) if owners else None


def _lists(session_env: Mapping[str, str], name: str) -> bool:
    return any(key == name or (key.endswith("_") and name.startswith(key))
               for key in session_env)


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


__all__ = ["CODEX_FORK_TURNS", "Refused", "Result", "TARGETS", "UNSTAMPED", "fork",
           "host_refusal", "recall_fork", "refusal", "stamp_refusal", "start"]

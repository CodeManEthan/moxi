"""The launcher's Python API: what the CLI calls, and what any other caller may.

    launch(request) -> LaunchResult     # resolve, check, compose, log, start
    check(request) -> list[Refusal]     # the live-state checks; writes nothing
    preview(request) -> Composition     # resolve and compose; starts nothing

`launch` runs one launch in this order: resolve its values (`presets.resolve`), check the
live state (`check`), compose it (`compose.build`, the call `preview` makes), find the
command its target runs, write its row to the launch log (state `spawning`), then start it
in its target (`targets`), which records the process and moves the row on. The session's
readiness and its first turn come after, from the ready recorder and the first-turn
sender.

**A refusal before the row is written writes nothing**: `launch` raises it, a
`catalog.Refused` (or one of the launch log's own, `store.TooLarge`, `store.SessionTaken`,
`store.Busy`), each with the `exit_code` the command exits with (`exit_code` reads it).
**After the row is written the row says what happened**: `launch` returns a
`LaunchResult` whose `state`, `reason` and `exit_code` are the row's.

**A request id is a launch's idempotency key.** A request whose id is already in the
launch log starts nothing: `launch` returns the stored row's outcome, `duplicate` set. It
looks the id up before it resolves or checks anything, so a retry is never refused by
what its own first run holds, such as its comms name or its named slot; the insert's own
uniqueness still catches two first runs that race.

`preview` composes through the same `compose.build`, with the request's ids, so for one
request it shows the argv the launch hands its target. Two limits: composing a Claude
launch without comms asks turns for its launch additions, which writes turns' own endpoint
file, so `preview` is not free of side effects outside the launcher; and for a Codex launch
under turns' host it shows the host's command, not the `codex` command the host builds.

A launch in this terminal does not return on Linux and macOS: the process becomes the
harness. On Windows it returns when the harness exits, with the harness's exit code; if
the first turn's deliverer could not start, the row ended `failed` while the harness ran,
and the result says that failure, with its exit code.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from moxi import exit as exits
from moxi import files
from moxi import modules as moxi_modules

from . import catalog, compose, first, handoffs, identity, presets, store, targets
from .compose import Composition, common

log = logging.getLogger("moxi.launcher.api")

#: A refusal: what `check` lists and `launch` raises, with `code`, `problem`, `hint` and
#: `exit_code`.
Refusal = catalog.Refused
#: The comms identity states a launch is refused in (`identity`).
TAKEN = identity.TAKEN
IdentityTaken = identity.IdentityTaken
IdentityRefused = identity.IdentityRefused
CommsUnanswered = identity.CommsUnanswered
CommsOffline = identity.CommsOffline


class NoDirectory(Refusal):
    """The directory a launch starts in does not exist."""

    code = "no-cwd"


class ScratchRoot(Refusal):
    """The folder this user's scratch launches go under is one they can't trust: a link,
    not a folder, another user's, or open to other users."""

    code = "scratch-root"


@dataclass(frozen=True)
class LaunchRequest:
    """One launch asked for: at most one preset (`seat` or `project_preset`), the flags
    typed (`typed`, as `catalog.parse(...).values` gives them) and passed on (`extra`),
    `--off-posture`, the caller's directory, and the request id, Claude session id and
    actor, each minted or read from the environment when left None."""

    seat: str | None = None
    project_preset: str | None = None
    typed: Mapping[str, object] = field(default_factory=dict)
    extra: Sequence[str] = ()
    off_posture: bool = False
    cwd: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    actor: str | None = None


@dataclass(frozen=True)
class LaunchResult:
    """What one launch did, as its row in the launch log says.

    `state` and `reason` are the row's; `exit_code` is what the command exits with. `pid`
    is the process started, and `slot` the tmux slot it runs in for a slot launch.
    `duplicate` is set when the request id was already in the
    launch log, so nothing was started and this is the stored row's outcome. `warnings`
    are one line each, `line` one line for the caller. `outcome` is the first turn's, when
    the launch sent it itself."""

    request_id: str
    harness: str
    target: str
    state: str
    exit_code: int
    session_id: str | None = None
    mode: str | None = None
    reason: str | None = None
    pid: int | None = None
    duplicate: bool = False
    argv: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    line: str = ""
    outcome: first.Outcome | None = None
    slot: str | None = None


def launch(request, *, environ: Mapping[str, str] | None = None,
           warn: Callable[[str], None] | None = None) -> LaunchResult:
    """Launch what `request` asks for. Raises a refusal, with nothing written, before the
    row; returns the row's outcome after it. See the module's text for the order.

    `warn` is called with each warning line just before the session starts, the last
    moment a launch in this terminal can show one; the result carries them too."""
    caller = dict(os.environ if environ is None else environ)
    bad_id = _request_id_refusal(getattr(request, "request_id", None))
    if bad_id is not None:
        raise bad_id
    known = _known(getattr(request, "request_id", None), environ)
    if known is not None:
        return _stored(known)
    resolved = _resolve(request, environ)
    refusals = _check(resolved, caller)
    if refusals:
        raise refusals[0]

    ids = _with_ids(request, resolved.harness)
    composition = compose.build(ids, resolved, environ=environ, create=True)
    target = targets.get(composition.target)
    env = common.environment(caller, composition, moxi_modules.session_env())
    command = target.prepare(composition, env)
    values = composition.values
    session_id = ids.session_id if composition.harness == "claude" else None

    handoff_key = handoffs.key_of_row(SimpleNamespace(
        identity=resolved.identity, preset_kind=resolved.preset_kind,
        preset_name=resolved.preset_name, project_key=resolved.project_key))
    conn = store.connect(env=environ)
    try:
        inserted = store.insert(
            conn, request_id=ids.request_id, actor=_actor(ids, caller),
            harness=composition.harness, target=composition.target,
            cwd=str(values["cwd"]), values=values, argv=composition.argv,
            env=composition.env, posture=str(resolved.posture or ""),
            session_id=session_id, slot=_slot_named(values),
            preset_kind=resolved.preset_kind, preset_name=resolved.preset_name,
            project_key=resolved.project_key,
            identity=resolved.identity, scope=resolved.scope, brief=composition.brief,
            handoff_path=str(values.get("handoff") or "") or None,
            handoff_key=handoff_key)
        if not inserted.new:
            return _stored(inserted.row)

        rid = ids.request_id
        warnings = _warnings(resolved, composition)
        try:
            _make_files(composition, environ, handoff_key)
        except OSError as error:
            failed = targets.fail(conn, rid, targets.SPAWN_FAILED,
                                   f"the launch's files could not be made: {error}")
            return _result(composition, rid, session_id, failed, warnings)
        try:
            targets.sweep(conn, env=environ)
        except Exception as error:  # noqa: BLE001  (the sweep never stops a launch)
            log.warning("the run-file sweep stopped: %s", error)
        for line in warnings if warn is not None else ():
            warn(line)
        started = target.start(targets.Launch(
            conn=conn, request_id=rid, composition=composition, argv=composition.argv,
            command=command, env=env, cwd=str(values["cwd"]), deliverer_env=caller))
        return _result(composition, rid, session_id, started, warnings)
    finally:
        conn.close()


def check(request, *, environ: Mapping[str, str] | None = None) -> list[Refusal]:
    """The refusals `launch` would meet in the live state, without composing or writing
    anything: the resolver's own (an unknown preset or project, projects unreadable or
    busy, a posture off its default), a directory that does not exist, a scratch folder's
    parent this user can't trust (a link, another user's, or open to others), a slot
    named for a launch whose target is not `slot` (exit 2, like a flag misused), a comms
    name that is held, reserved or retired (or that comms refuses, or can't answer on),
    and the target's (for a slot launch: slots not installed or not supported here, or a
    named slot that is held, occupied or missing)."""
    caller = dict(os.environ if environ is None else environ)
    bad_id = _request_id_refusal(getattr(request, "request_id", None))
    if bad_id is not None:
        return [bad_id]
    try:
        resolved = _resolve(request, environ)
    except Refusal as refusal:
        return [refusal]
    return _check(resolved, caller)


def preview(request, *, environ: Mapping[str, str] | None = None) -> Composition:
    """The composition `request` would launch: argv, the stamped environment, target and
    mode. Starts nothing and writes nothing of the launcher's. Its `warnings` are the
    ones `launch` gives: the resolver's, then the composer's."""
    bad_id = _request_id_refusal(getattr(request, "request_id", None))
    if bad_id is not None:
        raise bad_id
    resolved = _resolve(request, environ)
    composition = compose.build(request, resolved, environ=environ)
    return dataclasses.replace(composition,
                               warnings=_warnings(resolved, composition))


def exit_code(error: BaseException) -> int:
    """The code a command exits with for an error `launch`, `check` or `preview` raised:
    its own `exit_code`, else 1."""
    return int(getattr(error, "exit_code", exits.ERROR))


# -- the parts ----------------------------------------------------------------------------


def _resolve(request, environ: Mapping[str, str] | None = None):
    return presets.resolve(request, env=environ)


def _request_id_refusal(request_id: str | None) -> Refusal | None:
    """A refusal for a request id a caller gives that is not one plain file name, or None.
    The id names the launch's scratch folder and run log, so one holding a separator or
    `..` would put them outside the launcher's own folders."""
    if not request_id or common.plain_name(request_id):
        return None
    return catalog.BadValue("request_id", f"request id {request_id!r} is not a plain name",
                            "give letters, digits, '.', '_' or '-' only, or leave it out "
                            "and the launcher makes one")


def _known(request_id: str | None,
           environ: Mapping[str, str] | None) -> store.LaunchRow | None:
    """The row of a request id already in the launch log, read before anything else, so a
    retried request gets its stored outcome and is not refused by what its own first run
    holds (its comms name, its named slot). Reads only: a launch log not made yet is
    left unmade."""
    if not request_id or not store.path(environ).exists():
        return None
    conn = store.connect(env=environ, create=False)
    try:
        return store.get(conn, request_id)
    finally:
        conn.close()


def _check(resolved, caller: Mapping[str, str]) -> list[Refusal]:
    found: list[Refusal] = []
    if not resolved.scratch and resolved.cwd and not os.path.isdir(resolved.cwd):
        found.append(NoDirectory("cwd", f"the directory {resolved.cwd} does not exist",
                                 "make it, or launch from another directory"))
    if resolved.scratch:
        problem = common.scratch_root_problem()
        if problem is not None:
            found.append(ScratchRoot(
                "scratch", f"a scratch launch can't use its folder: {problem}",
                "move that folder away, or launch in a directory of your own"))
    named = targets.named_slot(getattr(resolved, "values", {}) or {})
    if named is not None and resolved.target != "slot":
        found.append(catalog.BadValue(
            "slot", f"slot {named} is named, but the launch's target is {resolved.target} "
                    "and only a slot launch takes a slot",
            "launch with --target slot, or name no slot"))
    if resolved.identity:
        taken = _identity_taken(resolved.identity, resolved.scope)
        if taken is not None:
            found.append(taken)
    found += targets.get(resolved.target).check(resolved)
    return found


def _identity_taken(name: str, scope: str | None) -> Refusal | None:
    """comms' word on whether `name` is free for this launch (`identity.check`)."""
    return identity.check(name, scope, _comms_api())


def _comms_api():
    return presets.comms_api()


def _with_ids(request, harness: str) -> SimpleNamespace:
    """`request` with its request id, and a Claude launch's session id, minted when it
    gives none."""
    names = ("seat", "project_preset", "typed", "extra", "off_posture", "cwd", "actor")
    given = {name: getattr(request, name, None) for name in names}
    request_id = getattr(request, "request_id", None) or str(uuid.uuid4())
    session_id = getattr(request, "session_id", None)
    if harness == "claude" and not session_id:
        session_id = str(uuid.uuid4())
    return SimpleNamespace(**given, request_id=request_id, session_id=session_id)


def _warnings(resolved, composition: Composition) -> tuple[str, ...]:
    """A launch's warning lines: the resolver's, then the composer's not already given."""
    given = tuple(getattr(resolved, "warnings", ()) or ())
    return given + tuple(w for w in composition.warnings if w not in given)


def _slot_named(values: Mapping[str, object]) -> str | None:
    """The slot a launch names, as the row records it before the start, or None."""
    named = targets.named_slot(values)
    return None if named is None else str(named)


def _actor(request, caller: Mapping[str, str]) -> str:
    return getattr(request, "actor", None) or first.actor(caller)


def _make_files(composition: Composition, environ: Mapping[str, str] | None = None,
                handoff_key: str | None = None) -> None:
    """What a launch makes before its start: Claude's system prompt file, a scratch
    launch's directory (0700, never removed by the launcher), and the handoff folder its
    identity block names when it has a handoff key. The scratch folder's parent is made
    private first, which refuses one that is a link or another user's, in case one turned
    up since the check. The launcher's own handoff folder is made only here, after the row,
    so a refusal leaves it unmade."""
    values = composition.values
    if values.get("scratch"):
        where = Path(str(values["cwd"]))
        files.private_dir(common.scratch_root())
        files.private_dir(where)
    if handoff_key and values.get("cwd"):
        handoffs.folder(str(values["cwd"]), notes_dir=values.get("notes_dir") or None,
                        env=environ)
    if composition.prompt_file is not None:
        common.write_prompt(composition.prompt_file)


def _result(composition: Composition, request_id: str, session_id: str | None,
            started: targets.Started, warnings: tuple[str, ...]) -> LaunchResult:
    return LaunchResult(
        request_id=request_id, harness=composition.harness, target=composition.target,
        state=started.state, exit_code=started.exit_code, session_id=session_id,
        mode=composition.mode, reason=started.reason, pid=started.pid,
        argv=composition.argv, warnings=warnings,
        line=started.line or f"launch {request_id} {started.state}",
        outcome=started.outcome, slot=started.slot)


def _stored(row: store.LaunchRow) -> LaunchResult:
    reason = f" ({row.reason})" if row.reason else ""
    return LaunchResult(
        request_id=row.request_id, harness=row.harness, target=row.target,
        state=row.state, exit_code=targets.exit_of(row), session_id=row.session_id,
        mode=row.values.get("mode"), reason=row.reason, pid=row.pid, duplicate=True,
        argv=tuple(row.argv), slot=row.slot,
        line=f"launch {row.request_id} was already made; it is {row.state}{reason}, and "
             "nothing was started again")


__all__ = ["TAKEN", "CommsOffline", "CommsUnanswered", "IdentityRefused", "IdentityTaken",
           "LaunchRequest", "LaunchResult", "NoDirectory", "Refusal", "ScratchRoot", "check",
           "exit_code", "launch", "preview"]

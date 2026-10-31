"""The launcher's Python API: what the CLI calls, and what any other caller may.

    launch(request) -> LaunchResult     # resolve, check, compose, log, start
    check(request) -> list[Refusal]     # the live-state checks; writes nothing
    preview(request) -> Composition     # resolve and compose; starts nothing
    open_hold(request_id, name)         # a keep-warm hold on a launch (`keepwarm`)
    release_hold(request_id, name)

A request is a `LaunchRequest` (presets and flags, resolved here) or a `ReplayRequest`
(a stored launch made again: a relaunch or a resume, its values the stored row's).

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

**A replay** takes its values from the stored row's `values_json` as they are, with no
preset read, and its own request id (and, but for a resume, its own Claude session id). It
keeps the row's directory, a scratch one too. An option row (keep-warm, recycling) the
install no longer offers, or whose value's needs it no longer meets, goes back to its
default with a warning, as a comms name does without comms. It never sends the row's
brief: a self relaunch's first turn is its handoff alone, under the header `Handoff from
launcher, relaunched by <actor>: <path>`, and a director's relaunch or a resume sends
none. **A replay's environment is built from the row and the settings only**: the row's recorded
base (`base_env_json`: the short list slots' server keeps, the core's locators, the
keep-lists' and provider names classed recorded, on Windows the names a process needs
to start; `common.base_env`), then each name the user lists, read from the replaying
process: `relaunch.pass_env`, or `relaunch.director_pass_env` in a director's relaunch,
since there that process is the director's. A credential (a token, key, secret,
password, or proxy, endpoint or base URL) is never recorded, so it reaches a replay only
that way; when the base shows a provider switch on or a credential set at launch and
nothing was passed, `LaunchResult.base` says `no-credentials`. A row made before the base
was recorded takes the short list alone from the replaying process, and
`LaunchResult.base` says `job`. A resume into the caller's own terminal adds the
caller's terminal names (`TERM`, `COLORTERM`, `TERM_PROGRAM`, `TERM_PROGRAM_VERSION`).
Then the scrub and the stamps. No other variable of the replaying process reaches the
child.

A launch in this terminal does not return on Linux and macOS: the process becomes the
harness. On Windows it returns when the harness exits, with the harness's exit code; if
the first turn's deliverer could not start, the row ended `failed` while the harness ran,
and the result says that failure, with its exit code.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import TYPE_CHECKING

from moxi import exit as exits
from moxi import files
from moxi import modules as moxi_modules

from . import catalog, compose, first, handoffs, identity, presets, slots, store, targets
from .compose import Composition, common

if TYPE_CHECKING:
    from . import keepwarm

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
class ReplayRequest:
    """A stored launch made again: `row` is its launch log row.

    `relaunch_of` or `resume_of` names the launch it makes again (the row's request id),
    and goes on the new row. A resume's session id is `session_id`, else the row's newest
    (`row.newest_session_id`, which `store.resolve` sets), else the row's; it is passed
    with `--resume`, never minted. `request_id` and `actor` are as `LaunchRequest`'s.

    `posture` is the start posture the identity block shows in place of the row's
    (`common.RELAUNCHED` for a self relaunch, `with:<director>` for a director's); it goes
    in the new row's `posture` and never in its `values_json`. `handoff` is the handoff a
    self relaunch carries as its first turn, `run` the relaunch's run id, stamped as
    `MOXI_LAUNCHER_RELAUNCH` when there is a handoff. `director` marks a director's
    relaunch, whose credentials come from `relaunch.director_pass_env`.

    For a slot launch: `slot` is the slot it goes to, else the row's own when `respawn`,
    else the one the row's values name; `hold` is the slot hold the caller has, which
    lets the launch into the slot it holds and ends with the start; `respawn` starts it
    in the slot's ended run pane, keeping the pane (a relaunch); `chain` is the first
    request id of the chain of relaunches, which the slot's `meta` keeps (default: the
    first launch reached through `relaunch_of`, for a relaunch; else the new launch's
    own id)."""

    row: store.LaunchRow
    relaunch_of: str | None = None
    resume_of: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    actor: str | None = None
    posture: str | None = None
    handoff: str | None = None
    run: str | None = None
    slot: int | str | None = None
    hold: str | None = None
    respawn: bool = False
    chain: str | None = None
    director: bool = False

    #: Marks a request whose values are a stored row's, not resolved from presets.
    replay = True


@dataclass(frozen=True)
class Replayed(presets.Resolved):
    """A replay's values, as `presets.resolve` gives a fresh launch's, with the slot hold
    the replay comes with."""

    hold: str | None = None


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
    base: str | None = None


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
    child, base = _child_base(request, caller)
    composition = compose.build(ids, resolved, environ=environ, child_environ=child,
                                create=True)
    target = targets.get(composition.target)
    env = common.environment(child, composition, moxi_modules.session_env())
    command = target.prepare(composition, env)
    values = composition.values
    resume = bool(getattr(request, "resume_of", None))
    session_id = ids.session_id if composition.harness == "claude" or resume else None

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
            handoff_key=handoff_key, base_env=common.base_env(env),
            relaunch_of=getattr(request, "relaunch_of", None),
            resume_of=getattr(request, "resume_of", None))
        if not inserted.new:
            return _stored(inserted.row)

        rid = ids.request_id
        warnings = _warnings(resolved, composition)
        if base == "no-credentials":
            warnings += (NO_CREDENTIALS,)
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
            command=command, env=env, cwd=str(values["cwd"]), deliverer_env=caller,
            hold=getattr(request, "hold", None),
            respawn=bool(getattr(request, "respawn", False)),
            chain=_chain(conn, request, rid)))
        return _result(composition, rid, session_id, started, warnings, base)
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
    if getattr(request, "replay", False):
        caller = dict(os.environ if environ is None else environ)
        child, _ = _child_base(request, caller)
        composition = compose.build(_with_ids(request, resolved.harness), resolved,
                                    environ=environ, child_environ=child)
    else:
        composition = compose.build(request, resolved, environ=environ)
    return dataclasses.replace(composition,
                               warnings=_warnings(resolved, composition))


def exit_code(error: BaseException) -> int:
    """The code a command exits with for an error `launch`, `check` or `preview` raised:
    its own `exit_code`, else 1."""
    return int(getattr(error, "exit_code", exits.ERROR))


# -- the parts ----------------------------------------------------------------------------


def _resolve(request, environ: Mapping[str, str] | None = None):
    if getattr(request, "replay", False):
        return _replayed(request)
    return presets.resolve(request, env=environ)


#: What a replay does not take from the stored values as values: the composer's own
#: record of the mode and the rows it left out, and `--off-posture`, which goes back on
#: its own field.
_NOT_REPLAYED = ("mode", "not_applied", "off_posture")


#: The option rows a replay sets back to their default, with a warning, when the
#: install no longer offers them or no longer meets their value's needs: stamped, they
#: would promise a timer or a crossing that never comes.
_OPTION_ROWS = ("keep_warm", "recycle_on_expiry", "recycle_at_limit")


def _unoffered_option(harness: str, target: str, values: dict, name: str) -> str | None:
    """Set the option row `name` in `values` back to its default when it is set and this
    install doesn't offer it, or doesn't meet what its value needs; return the warning,
    or None."""
    row_ = catalog.row(harness, name)
    value = values.get(name)
    if row_ is None or value in (None, "", row_.default):
        return None
    why = row_.unoffered(slots.links, harness)
    need = row_.when.get(value) if isinstance(value, str) else None
    if why is None and need is not None:
        try:
            catalog.refuse_values(harness, {"target": target, name: value})
        except catalog.Refused as refused:
            why = (refused.problem, "")
    if why is None:
        return None
    values[name] = row_.default
    return f"{name} {value} is not applied: {why[0]}"


def _replayed(request: ReplayRequest) -> Replayed:
    """A replay's resolved values: the stored row's `values_json` as they are, its brief
    left out, its handoff the replay's, its slot the replay's, with the mode worked out
    again for the modules installed now. A comms identity is dropped, with a warning, when
    comms is no longer installed, as a fresh launch's is."""
    row = request.row
    if request.relaunch_of and request.resume_of:
        raise ValueError("a replay is a relaunch or a resume, not both")
    if request.respawn and row.target != "slot":
        raise catalog.BadValue("target", f"launch {row.request_id} ran in its {row.target}"
                                         ", and only a slot launch is respawned",
                               "replay it without respawn")
    stored = row.values
    values = {k: v for k, v in stored.items() if k not in _NOT_REPLAYED}
    values.update(brief="", brief_file="", handoff=request.handoff or "")
    slot = request.slot if request.slot is not None else (
        row.slot if request.respawn else None)
    if slot is not None:
        values["slot"] = str(slot)
    harness, target = row.harness, row.target
    warnings: list[str] = []
    identity, scope = row.identity or None, row.scope or None
    if identity and not slots.links("comms"):
        warnings.append(f"identity {identity} is ignored: comms is not installed")
        identity = scope = None
        values["identity"], values["scope"] = "", ""
    for name in _OPTION_ROWS:
        dropped = _unoffered_option(harness, target, values, name)
        if dropped is not None:
            warnings.append(dropped)
    mode, turns, more = presets.mode_of(harness, target, identity=identity)
    warnings += more
    posture = row.posture if request.posture is None else request.posture
    links = {name for name, on in (
        ("turns", turns), ("comms", identity is not None),
        ("projects", bool(row.project_key)), ("slots", target == "slot"),
        ("notes", catalog.is_set(values.get("notes_dir"))),
        ("usage", catalog.is_set(values.get("warn_at")))) if on}
    return Replayed(
        harness=harness, mode=mode, values=MappingProxyType(values),
        sources=MappingProxyType({}), chosen=frozenset(),
        preset_kind=row.preset_kind, preset_name=row.preset_name, cwd=row.cwd,
        scratch=bool(values.get("scratch")), project_key=row.project_key, agents=(),
        identity=identity, scope=scope, posture=str(posture or ""), target=target,
        off_posture=bool(stored.get("off_posture", False)), links=frozenset(links),
        warnings=tuple(warnings), hold=request.hold)


def _child_base(request, caller: Mapping[str, str]) -> tuple[dict[str, str], str | None]:
    """The environment the child's is built from, and where its base came from: the
    caller's whole environment for a fresh launch (None); for a replay
    (`common.replay_env`), the row's recorded base (`row`, or `no-credentials` when it
    shows a sign-in that nothing passed), or the caller's short list for a row that has
    none (`job`)."""
    if not getattr(request, "replay", False):
        return caller, None
    names = _pass_env(director=bool(getattr(request, "director", False)))
    terminal = bool(request.resume_of) and request.row.target == "terminal"
    stored = request.row.base_env
    if stored is None:
        return common.replay_env(common.short_env(caller), caller, names,
                                 terminal=terminal), "job"
    passed = [name for name in names if name in caller]
    where = "no-credentials" if common.no_credentials(stored, passed) else "row"
    return common.replay_env(stored, caller, names, terminal=terminal), where


#: The warning of a replay that may start signed out.
NO_CREDENTIALS = ("the launch signed in through its environment or another provider, and "
                  "no sign-in variable was passed to this one: list the names in "
                  "relaunch.pass_env (relaunch.director_pass_env for a director's "
                  "relaunch) if it starts signed out")


def _pass_env(*, director: bool = False) -> list[str]:
    """The names `relaunch.pass_env` lists, or `relaunch.director_pass_env` for a
    director's relaunch."""
    from . import settings

    key = "relaunch.director_pass_env" if director else "relaunch.pass_env"
    return [str(name) for name in settings.current()[key]]


def _chain(conn, request, request_id: str) -> str:
    """The first request id of the launch's chain of relaunches: the request's `chain`,
    else, for a relaunch, the first launch reached through `relaunch_of`, else the
    launch's own id."""
    given = getattr(request, "chain", None)
    if given:
        return str(given)
    first_id = getattr(request, "relaunch_of", None)
    seen = {request_id}
    while first_id and first_id not in seen:
        seen.add(first_id)
        row = store.get(conn, first_id)
        if row is None or not row.relaunch_of:
            break
        first_id = row.relaunch_of
    return first_id or request_id


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


def _with_ids(request, harness: str):
    """`request` with its request id, and a Claude launch's session id, minted when it
    gives none. A resume's session id is the one it resumes, never minted; a replay that
    has none to resume is refused."""
    if getattr(request, "replay", False):
        session_id = request.session_id
        if request.resume_of:
            session_id = session_id or request.row.newest_session_id or request.row.session_id
            if not session_id:
                raise Refusal("resume", f"launch {request.row.request_id} has no session id "
                                        "to resume", "a Codex launch made without turns' "
                                                     "host has none: use codex resume")
        elif harness == "claude" and not session_id:
            session_id = str(uuid.uuid4())
        return dataclasses.replace(request,
                                   request_id=request.request_id or str(uuid.uuid4()),
                                   session_id=session_id)
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


def open_hold(request_id: str, name: str, *,
              conn: sqlite3.Connection | None = None) -> keepwarm.Held:
    """Open the keep-warm hold `name` on the launch `request_id` (a no-op when the name is
    open anywhere in the launch's chain), for a caller that knows the launch. Raises a
    refusal when the launch is unknown, does not keep warm, or the name is bad."""
    from . import keepwarm

    return keepwarm.open_hold(request_id, name, conn=conn)


def release_hold(request_id: str, name: str, *,
                 conn: sqlite3.Connection | None = None) -> list[store.Hold]:
    """Release the keep-warm hold `name` wherever it is open in the chain of the launch
    `request_id`, and return the holds released (none when it was not open)."""
    from . import keepwarm

    return keepwarm.release_hold(request_id, name, conn=conn)


def _result(composition: Composition, request_id: str, session_id: str | None,
            started: targets.Started, warnings: tuple[str, ...],
            base: str | None = None) -> LaunchResult:
    return LaunchResult(
        request_id=request_id, harness=composition.harness, target=composition.target,
        state=started.state, exit_code=started.exit_code, session_id=session_id,
        mode=composition.mode, reason=started.reason, pid=started.pid,
        argv=composition.argv, warnings=warnings,
        line=started.line or f"launch {request_id} {started.state}",
        outcome=started.outcome, slot=started.slot, base=base)


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
           "LaunchRequest", "LaunchResult", "NoDirectory", "Refusal", "ReplayRequest",
           "Replayed", "ScratchRoot", "check", "exit_code", "launch", "open_hold", "preview",
           "release_hold"]

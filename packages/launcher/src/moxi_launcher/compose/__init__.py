"""The composers: one launch's resolved values in, its argv and environment out.

`compose(values, ctx)` is pure. It picks the harness's composer (`compose.claude`,
`compose.codex`), which works out the launch's mode, applies the modes rule, spells every
row the mode takes in its fixed order, places the opening prompt before every flag,
stamps the environment and refuses what is past a size limit. `values` are the launch's
resolved values by row name, launcher rows and harness rows together; `ctx` (a `Context`)
holds everything else a composer reads.

`context(...)` gathers that `ctx` for one launch: the installed modules, the texts the
launch names (its brief, handoff and instructions files are read here), the handoff
folder, turns' Codex host, and, for a Claude launch without comms, turns' launch
additions, which write nothing.

`build(request, resolved)` is the two together, and is what both `preview` and the launch
call, so for one request, its ids given, `preview` shows the argv that runs. A replay of a
stored launch (a request whose `replay` is true, `api.ReplayRequest`) comes the same way,
with what it adds: its posture, a resume, the relaunch's header and stamps.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import SimpleNamespace

from moxi import modules as moxi_modules
from moxi import paths
from moxi.paths import Layout

from .. import first, handoffs, presets, slots
from . import claude, codex, common
from .common import (Additions, Composition, Context, NoBrief, NotYet, PromptFile,
                     TooLong, TurnsAdditions, Unreadable, environment, first_turn,
                     identity_block, scrub, stamps, write_prompt)

MODULE = "launcher"
#: The optional links a launch may use, as `Context.modules` names them.
LINKS = ("turns", "comms", "slots", "projects", "notes", "usage", "loadout")
#: The turns interface whose names the launcher calls (launch additions, the ready wait,
#: delivery, the Codex host). An older turns counts as absent.
TURNS_INTERFACE = 2
#: turns' Codex host, its own process.
HOST = presets.CODEX_HOST
COMPOSERS = {"claude": claude, "codex": codex}


def compose(values: Mapping[str, object], ctx: Context) -> Composition:
    """Compose one launch with its harness's composer."""
    harness = values.get("harness") or "claude"
    if harness not in COMPOSERS:
        raise ValueError(f"no composer for harness {harness!r}")
    return COMPOSERS[harness].compose(values, ctx)


def installed() -> frozenset[str]:
    """The optional links installed here. turns counts only when it offers the interface
    the launcher calls, the first-turn sender's answer (`first.turns_api`), so a launch
    composed with turns is one the sender reaches."""
    found = {name for name in LINKS if name != "turns" and slots.links(name)}
    if first.turns_api(quiet=True) is not None:
        found.add("turns")
    return frozenset(found)


def moxi_command() -> str:
    """The `moxi` command comms' wrappers run under: this environment's own."""
    try:
        return str(paths.script("moxi"))
    except FileNotFoundError:
        return "moxi"


def host_script() -> str | None:
    """turns' Codex host, or None when it can't start here (`presets.codex_host`)."""
    return presets.codex_host()


def context(values: Mapping[str, object], *, request_id: str,
            session_id: str | None = None, mode: str | None = None,
            actor: str | None = None,
            chosen: Iterable[str] = (), project_key: str | None = None,
            preset_kind: str | None = None, preset_name: str | None = None,
            environ: Mapping[str, str] | None = None,
            child_environ: Mapping[str, str] | None = None,
            modules: frozenset[str] | None = None, create: bool = False,
            replay: Mapping[str, object] | None = None) -> Context:
    """Gather the context of one launch. `mode` is the resolver's, None to let the
    composer work it out. `create` lets notes make its handoff folder when it is missing
    (a launch); `preview` leaves it alone. The launcher's own handoff folder is never made
    here: a launch makes it with its other files, after its row is written.

    `environ` is the caller's environment; `child_environ` the one the child's is built
    from, which turns' launch additions are asked with (default `environ`; a replay's
    differs). `replay` holds the `Context` fields a replay sets (`posture`, `resume`,
    `relaunched`, `extra_stamps`)."""
    environ = dict(os.environ if environ is None else environ)
    child = environ if child_environ is None else dict(child_environ)
    links = installed() if modules is None else frozenset(modules)
    harness = values.get("harness") or "claude"
    who = actor if actor is not None else first.actor(environ)

    brief = values.get("brief") or _read("brief_file", values.get("brief_file"))
    handoff_text = _read("handoff", values.get("handoff"))
    instructions = values.get("instructions") or _read(
        "instructions_file", values.get("instructions_file"))

    comms = bool(values.get("identity")) and "comms" in links
    key = handoffs.key_of_row(SimpleNamespace(
        identity=values.get("identity") if comms else None, preset_kind=preset_kind,
        preset_name=preset_name, project_key=project_key))
    handoff_dir = None
    if key and values.get("cwd"):
        handoff_dir = str(handoffs.folder(str(values["cwd"]),
                                          notes_dir=values.get("notes_dir") or None,
                                          env=environ, create=create,
                                          create_own=False))

    role_found = None
    if values.get("agent"):
        role_found = str(values["agent"]) in presets.roles(
            harness, cwd=values.get("cwd") or None, env=environ)
    host = host_script() if harness == "codex" else None
    additions = None
    if harness == "claude" and "turns" in links and not comms:
        granted = common.commands(values)
        base = {**scrub(child, moxi_modules.session_env()),
                **stamps(request_id, turns=True, granted=granted)}
        additions = launch_additions(cwd=str(values.get("cwd") or ""), env=base,
                                     session_id=session_id, granted=granted)

    return Context(request_id=request_id, session_id=session_id, mode=mode, actor=who,
                   modules=links, chosen=frozenset(chosen), project_key=project_key,
                   preset_kind=preset_kind, preset_name=preset_name,
                   brief=str(brief) if brief else None,
                   handoff_text=handoff_text,
                   instructions=str(instructions) if instructions else None,
                   handoff_dir=handoff_dir, role_found=role_found, additions=additions,
                   runs_dir=str(Layout(MODULE).state_dir(environ) / "runs"),
                   moxi=moxi_command(), host=host,
                   comms_codex=harness == "codex" and comms and presets.comms_codex(),
                   **dict(replay or {}))


def launch_additions(*, cwd: str, env: Mapping[str, str], session_id: str | None,
                     granted: Iterable[str] = common.COMMANDS) -> Additions:
    """turns' launch additions for a Claude launch without comms, asked once, with the
    commands the launch grants (`common.commands`), which turns renders into its own
    `MOXI_TURNS_COMMANDS`. turns'
    own error, or its `ValueError` for a harness or command it does not know, is a
    `TurnsAdditions` refusal, so `preview` and the launch say it in one line."""
    api = first.turns_api(quiet=True)
    call = getattr(api, "launch_additions", None) if api is not None else None
    if call is None:
        raise RuntimeError("turns' launch additions can't be read: "
                           + ("turns is not reachable" if api is None
                              else "its API has no launch_additions"))
    turns_error = getattr(api, "TurnsError", None)
    caught = (ValueError,) + ((turns_error,) if isinstance(turns_error, type)
                              and issubclass(turns_error, BaseException) else ())
    try:
        got = call("claude", cwd=cwd, env=dict(env), session_id=session_id,
                   commands=tuple(granted))
    except caught as error:
        raise TurnsAdditions(None, f"turns could not give this launch its additions: "
                                   f"{error}", "moxi doctor shows what is wrong with turns"
                             ) from error
    return Additions(argv=tuple(str(word) for word in got.argv),
                     env={str(k): str(v) for k, v in dict(got.env).items()},
                     notes=tuple(str(note) for note in (got.notes or ())))


def build(request, resolved, *, environ: Mapping[str, str] | None = None,
          child_environ: Mapping[str, str] | None = None,
          modules: frozenset[str] | None = None, create: bool = False) -> Composition:
    """Compose the launch `request` asks for, from its `resolved` values
    (`presets.resolve`'s `Resolved`).

    `request` gives `request_id`, `session_id` and `actor`, each read as None when it is
    missing (an id is then minted, the actor read from the environment). A scratch launch
    works in `common.scratch_dir(request_id)`, which the launch makes; this only names it.
    `preview` and the launch both come here, so the same request, its ids given, composes
    the same argv.

    A replay (`request.replay` true) keeps the stored launch's directory, a scratch one
    too, where a resumed session's transcript is found; its session id is the one it
    resumes when `request.resume_of` is set. It gives the identity block its `posture`,
    and a relaunch with a handoff (`relaunch_of` and `handoff` set) the relaunch's header
    and, with `run`, the `RELAUNCH` stamp.
    """
    values = dict(resolved.values)
    request_id = getattr(request, "request_id", None) or str(uuid.uuid4())
    session_id = getattr(request, "session_id", None)
    replay = _replay(request)
    if (values.get("harness") or "claude") == "claude" and not session_id:
        session_id = str(uuid.uuid4())
    if replay is None and (getattr(resolved, "scratch", False)
                           or (values.get("scratch") and not values.get("cwd"))):
        values["cwd"] = str(common.scratch_dir(request_id))
    if hasattr(resolved, "off_posture"):
        values["off_posture"] = bool(resolved.off_posture)
    ctx = context(values, request_id=request_id, session_id=session_id,
                  mode=getattr(resolved, "mode", None),
                  actor=getattr(request, "actor", None),
                  chosen=getattr(resolved, "chosen", ()),
                  project_key=getattr(resolved, "project_key", None),
                  preset_kind=getattr(resolved, "preset_kind", None),
                  preset_name=getattr(resolved, "preset_name", None),
                  environ=environ, child_environ=child_environ, modules=modules,
                  create=create, replay=replay)
    return compose(values, ctx)


def _replay(request) -> dict[str, object] | None:
    """The `Context` fields a replay request sets, or None for a request that is not one."""
    if not getattr(request, "replay", False):
        return None
    handoff = getattr(request, "handoff", None)
    relaunched = bool(getattr(request, "relaunch_of", None) and handoff)
    run = getattr(request, "run", None)
    return {"posture": getattr(request, "posture", None),
            "resume": bool(getattr(request, "resume_of", None)),
            "relaunched": relaunched,
            "extra_stamps": {common.RELAUNCH: str(run)} if relaunched and run else {}}


def _read(row: str, name: object) -> str | None:
    if not name:
        return None
    path = Path(str(name)).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Unreadable(row, f"{row} {path} can't be read: {exc}",
                         f"fix the path, or leave {row} out") from exc


__all__ = ["COMPOSERS", "HOST", "LINKS", "TURNS_INTERFACE", "Additions", "Composition",
           "Context", "NoBrief", "NotYet", "PromptFile", "TooLong", "TurnsAdditions",
           "Unreadable",
           "build", "compose", "context", "environment", "first_turn", "host_script",
           "identity_block", "installed", "launch_additions", "moxi_command", "scrub", "stamps",
           "write_prompt"]

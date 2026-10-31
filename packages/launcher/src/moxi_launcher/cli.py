"""`moxi launcher`: launch a session, and read what was launched.

    moxi launcher run [--seat NAME | --project-preset NAME] [catalog flags] [-- extra]
    moxi launcher preview [--seat NAME | --project-preset NAME] [catalog flags] [--json]
    moxi launcher log [--limit N] [--before TIME] [--json]
    moxi launcher presets [list | show NAME | check] [--project KEY] [--json]
    moxi launcher catalog [HARNESS] [--json]
    moxi launcher handoff dir [--json]
    moxi launcher handoffs [--key KEY] [--json]
    moxi launcher deliver REQUEST_ID --pid PID

`run` launches; `preview` prints the argv and environment a launch would use and starts
nothing. `log` lists launches, newest first. `presets` lists, shows or checks the seats
and project presets, and with `--project KEY` lists a project's agents beside the seat of
each. `catalog` lists the flag rows this install offers. `handoff dir` prints the handoff
folder for this session; `handoffs` lists a key's handoffs and which still wait for their
seat. `deliver` is internal: the detached process that sends a terminal launch's first
turn.

`run` and `preview` keep what this parser does not know as `rest`, and read it again
against the catalog of the harness the launch starts: the one `--harness` names, else the
preset's, else the one `[defaults]` names, else the shipped default. A flag the catalog
does not know is passed on to the harness, last on its argv, after one warning line.

The CLI calls the library in its own process. Exit codes are `moxi.exit`'s: 0 done; 1 a
start that failed; 2 bad arguments or a bad value; 3 refused (before the launch's row is
written nothing is written, after it the row says why); 75 a lock held past its wait. A
refusal prints one line ending in its short code, and a hint.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from moxi import exit as codes
from moxi import store as core_store
from moxi.config import ConfigError

#: The verbs that take the catalog's flags. Their parser is the launcher's rows plus the
#: chosen harness's catalog, read with `parse_known_args`, so an unknown flag passes on.
LAUNCH_VERBS = ("run", "preview")
PRESET_ACTIONS = ("list", "show", "check")

#: The short code and the hint of each error that is not a catalog refusal, which
#: carries its own.
_OTHER_ERRORS = {
    "StoreTooNew": ("store-too-new", "install a newer moxi-launcher"),
    "Busy": ("busy", "try again in a moment"),
    "TooLarge": ("too-large", "shorten it"),
    "SessionTaken": ("session-taken", "launch without a session id, and one is minted"),
    "NoKey": ("no-key", "launch the session as a seat, or give it a comms name"),
    "HandoffUnreadable": ("unreadable", "check the handoff's path"),
}


class NoPreset(Exception):
    """`presets show NAME` names no seat or project preset."""

    code = "no-preset"
    exit_code = codes.REFUSED

    def __init__(self, name: str):
        self.problem = f"no seat or project preset is named {name!r}"
        self.hint = "moxi launcher presets list shows them"
        super().__init__(f"{self.problem}; {self.hint}")


def _errors() -> tuple[type[BaseException], ...]:
    """The errors a verb stops on and reports as a refusal, not as a traceback."""
    from . import catalog, first, handoffs

    return (catalog.Refused, ConfigError, core_store.StoreError, handoffs.NoKey,
            first.HandoffUnreadable, NoPreset)


def refusal_lines(error: BaseException) -> tuple[str, str | None]:
    """What a refusal prints: the problem with its short code, and the hint, if any."""
    if isinstance(error, ConfigError):
        where = error.file or "config"
        if error.key:
            where += f": key {error.key!r}"
        return f"{where}: {error.problem} [bad-config]", error.fix
    problem = getattr(error, "problem", None)
    hint = getattr(error, "hint", None)
    code = getattr(error, "code", None)
    if problem is None or code is None:
        code, hint = _OTHER_ERRORS.get(type(error).__name__, ("error", hint))
        problem = str(error)
    return f"{problem} [{code}]", hint


def _refuse(prog: str, verb: str, error: BaseException) -> int:
    line, hint = refusal_lines(error)
    print(f"{prog} {verb}: {line}", file=sys.stderr)
    if hint:
        print(f"hint: {hint}", file=sys.stderr)
    return int(getattr(error, "exit_code", codes.ERROR))


def _warn(line: str) -> None:
    print(f"warning: {line}", file=sys.stderr, flush=True)


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


# -- run and preview ----------------------------------------------------------------------


def _request(args: argparse.Namespace):
    """The launch request the typed words ask for, its flags read against the catalog of
    the harness the launch starts. Prints the warning for flags passed on unknown."""
    from . import api, catalog, presets

    asked = presets.Request(seat=args.seat, project_preset=args.project_preset)
    harness = catalog.typed_harness(args.rest) or presets.harness_of(asked)
    parsed = catalog.parse(args.rest, harness)
    if parsed.warning:
        _warn(parsed.warning)
    return api.LaunchRequest(seat=args.seat, project_preset=args.project_preset,
                             typed=dict(parsed.values), extra=tuple(parsed.extra),
                             off_posture=args.off_posture, cwd=os.getcwd())


def _run(args: argparse.Namespace, prog: str) -> int:
    from . import api

    result = api.launch(_request(args), warn=_warn)
    out = sys.stdout if result.exit_code == codes.OK else sys.stderr
    print(result.line, file=out)
    if result.outcome is not None and result.outcome.line() != result.line:
        print(result.outcome.line(), file=out)
    return result.exit_code


def command_line(argv: Sequence[str]) -> str:
    """`argv` as one line this system's shell reads back as the same words."""
    if sys.platform == "win32":
        return subprocess.list2cmdline(list(argv))
    return shlex.join(argv)


def preview_json(composition) -> dict:
    """What `preview --json` prints for one composition."""
    return {"harness": composition.harness, "mode": composition.mode,
            "target": composition.target, "argv": list(composition.argv),
            "env": dict(composition.env), "first_turn": composition.first,
            "not_applied": list(composition.not_applied),
            "warnings": list(composition.warnings), "values": dict(composition.values)}


def _preview(args: argparse.Namespace, prog: str) -> int:
    from . import api

    composition = api.preview(_request(args))
    if args.json:
        print(_dump(preview_json(composition)))
        return codes.OK
    for line in composition.warnings:
        _warn(line)
    print(f"harness {composition.harness}, mode {composition.mode}, "
          f"target {composition.target}")
    print("argv:")
    print(f"  {command_line(composition.argv)}")
    print("env:")
    for name, value in composition.env.items():
        print(f"  {name}={shlex.quote(str(value))}")
    if composition.not_applied:
        print("not applied in this mode: " + ", ".join(composition.not_applied))
    if composition.first:
        print("first turn, sent once the session is ready:")
        for line in composition.first.splitlines():
            print(f"  {line}" if line else "")
    return codes.OK


# -- log ----------------------------------------------------------------------------------


def _log(args: argparse.Namespace, prog: str) -> int:
    from . import store

    if args.limit is not None and args.limit < 1:
        print(f"{prog} log: --limit must be 1 or more", file=sys.stderr)
        return codes.USAGE
    if args.before is not None:
        try:
            store.parse_time(args.before)
        except ValueError:
            print(f"{prog} log: --before {args.before!r} is not a time; give UTC ISO "
                  "8601, such as 2026-10-30T12:00:00Z", file=sys.stderr)
            return codes.USAGE
    if args.json:
        for line in store.log_lines(limit=args.limit, before=args.before):
            print(line)
        return codes.OK
    rows = _log_rows(args.limit, args.before)
    if not rows:
        print("no launches", file=sys.stderr)
        return codes.OK
    after = store.stale_after_s()
    for row in rows:
        print(log_line(row, stale=row.stale(after)))
    return codes.OK


def _log_rows(limit: int | None, before: str | None) -> list:
    from . import store

    try:
        conn = store.connect(create=False)
    except core_store.StoreTooNew:
        raise
    except core_store.StoreError:
        return []
    try:
        return store.log(conn, limit=limit, before=before)
    finally:
        conn.close()


def log_line(row, *, stale: bool = False) -> str:
    """One launch as `log` prints it: when, request id, harness and target, the state
    (its reason, and whether it reads as stale), the preset and the directory."""
    state = row.state + (f" ({row.reason})" if row.reason else "")
    if stale:
        state += ", stale"
    preset = f"{row.preset_kind}.{row.preset_name}" if row.preset_name else "-"
    return (f"{row.created_at[:19]}Z  {row.request_id}  {row.harness}/{row.target}  "
            f"{state}  {preset}  {row.cwd}")


# -- presets ------------------------------------------------------------------------------


def _presets(args: argparse.Namespace, prog: str) -> int:
    from . import presets

    if args.project is not None:
        if args.action != "list":
            print(f"{prog} presets: --project goes with list only", file=sys.stderr)
            return codes.USAGE
        return _project_agents(args, presets)
    if args.action == "check":
        return _presets_check(args, presets)
    found = presets.read()
    if args.action == "show":
        return _presets_show(args, found)
    if args.json:
        print(_dump({"file": str(found.path), "defaults": dict(found.defaults),
                     "presets": [_preset_json(p) for p in found.all()]}))
        return codes.OK
    if not found.all():
        print(f"no presets in {found.path}", file=sys.stderr)
        return codes.OK
    for preset in found.all():
        print(f"{preset.where:<28} {str(preset.harness or '-'):<8} {_place(preset)}")
    return codes.OK


def _preset_json(preset) -> dict:
    return {"kind": preset.kind, "name": preset.name, "harness": preset.harness,
            "values": dict(preset.values)}


def _place(preset) -> str:
    """Where a preset starts its sessions, as the file names it."""
    for key in ("cwd", "project", "home"):
        if preset.values.get(key):
            return f"{key} {preset.values[key]}"
    if preset.values.get("scratch"):
        return "scratch"
    return "-"


def _presets_show(args: argparse.Namespace, found) -> int:
    from . import catalog, presets

    kind, _, name = args.name.partition(".")
    if kind in presets.KINDS and name:
        shown = [found.get(kind, name)]
    else:
        shown = [p for p in found.all() if p.name == args.name]
    if not shown:
        raise NoPreset(args.name)
    if args.json:
        print(_dump([_preset_json(p) for p in shown]))
        return codes.OK
    for i, preset in enumerate(shown):
        if i:
            print()
        print(f"[{preset.where}]")
        for key, value in preset.values.items():
            print(f"{key} = {catalog.toml_literal(value)}")
    return codes.OK


def _presets_check(args: argparse.Namespace, presets) -> int:
    problems = presets.check()
    errors = [p for p in problems if p.level == presets.ERROR]
    if args.json:
        print(_dump({"file": str(presets.path()), "ok": not errors,
                     "problems": [p.as_json() for p in problems]}))
    else:
        for problem in problems:
            print(f"{problem} [{problem.code}]")
        if not problems:
            print(f"{presets.path()}: every preset is good")
    return errors[0].exit_code if errors else codes.OK


def _project_agents(args: argparse.Namespace, presets) -> int:
    key, agents = presets.project_agents(args.project)
    if args.json:
        print(_dump({"project": key, "agents": [a.as_json() for a in agents]}))
        return codes.OK
    print(f"project {key}")
    if not agents:
        print("  no agents")
    for agent in agents:
        seats = ", ".join(f"seat {s}" for s in agent.seats) or "no seat"
        print(f"  {agent.name:<24} {seats}")
    return codes.OK


# -- catalog ------------------------------------------------------------------------------


def _catalog(args: argparse.Namespace, prog: str) -> int:
    from . import catalog

    if args.harness is not None and args.harness not in catalog.HARNESSES:
        print(f"{prog} catalog: no harness {args.harness!r}; the harnesses are "
              f"{', '.join(catalog.HARNESSES)}", file=sys.stderr)
        return codes.USAGE
    harnesses = catalog.HARNESSES if args.harness is None else (args.harness,)
    groups = {catalog.LAUNCHER: catalog.launcher()}
    groups.update((harness, catalog.load(harness)) for harness in harnesses)
    described = {name: [catalog.describe(row) for row in rows]
                 for name, rows in groups.items()}
    if args.json:
        print(json.dumps(described, indent=2))
        return codes.OK
    for name, rows in described.items():
        print(name)
        for row in rows:
            if row["owned"]:
                where = "set by the launcher"
            elif row["name"] == catalog.EXTRA:
                where = "the words after --"
            elif not row["offered"]:
                where = row["unoffered"] or f"needs {row['requires']}"
            else:
                where = row["option"] or ""
            modes = f" [{', '.join(row['modes'])}]" if row["modes"] else ""
            print(f"  {row['name']:<26} {where:<24} {row['doc']}{modes}")
    return codes.OK


# -- handoffs -----------------------------------------------------------------------------


def _handoff(args: argparse.Namespace, prog: str) -> int:
    from . import handoffs

    here = handoffs.current()
    if args.json:
        print(_dump({"key": here.key, "folder": str(here.folder),
                     "request_id": here.request_id}))
    else:
        print(here.folder)
    return codes.OK


def _handoffs(args: argparse.Namespace, prog: str) -> int:
    from . import handoffs

    if args.key is not None:
        key = handoffs.check_key(args.key)
        if key is None:
            print(f"{prog} handoffs: {args.key!r} can't be a handoff key; a key is "
                  "letters, digits, '.', '_' and '-'", file=sys.stderr)
            return codes.USAGE
        folder = handoffs.folder(Path.cwd(), create=False)
    else:
        here = handoffs.current(create=False)
        key, folder = here.key, here.folder
    found = handoffs.handoffs(folder, key)
    waiting = {h.path for h in handoffs.unpicked(folder, key, _picked(key))}
    if args.json:
        print(_dump({"key": key, "folder": str(folder),
                     "handoffs": [{"path": str(h.path), "date": h.date,
                                   "waiting": h.path in waiting} for h in found]}))
        return codes.OK
    if not found:
        print(f"no handoffs of {key} in {folder}", file=sys.stderr)
        return codes.OK
    for handoff in found:
        mark = "waiting" if handoff.path in waiting else "picked"
        print(f"{handoff.date}  {mark:<7}  {handoff.path}")
    return codes.OK


def _picked(key: str) -> list[str]:
    """The paths of `key`'s picked handoffs; none when the launch log was never made."""
    from . import store

    try:
        conn = store.connect(create=False)
    except core_store.StoreTooNew:
        raise
    except core_store.StoreError:
        return []
    try:
        return store.picked(conn, key)
    finally:
        conn.close()


# -- deliver ------------------------------------------------------------------------------


def _deliver(args: argparse.Namespace, prog: str) -> int:
    from . import first

    return first.deliver(args.request_id, pid=args.pid)


VERBS: dict[str, Callable[[argparse.Namespace, str], int]] = {
    "run": _run,
    "preview": _preview,
    "log": _log,
    "presets": _presets,
    "catalog": _catalog,
    "handoff": _handoff,
    "handoffs": _handoffs,
    "deliver": _deliver,
}


# -- the parser ---------------------------------------------------------------------------


def _launch_flags(parser: argparse.ArgumentParser) -> None:
    """The flags `run` and `preview` share: at most one preset, and the posture override."""
    preset = parser.add_mutually_exclusive_group()
    preset.add_argument("--seat", metavar="NAME", help="launch the seat NAME")
    preset.add_argument("--project-preset", metavar="NAME",
                        help="launch the project preset NAME")
    parser.add_argument("--off-posture", action="store_true",
                        help="allow a permission row that differs from your default")


LAUNCH_EPILOG = ("Any flag of the launcher's or the harness's catalog may follow; "
                 "moxi launcher catalog lists them. A flag the catalog does not know is "
                 "passed on to the harness, after one warning.")


def build_parser(prog: str = "moxi launcher") -> argparse.ArgumentParser:
    """The parser of every verb. `run` and `preview` carry only the launcher's fixed flags
    here; the catalog's rows join them when the catalog is read."""
    parser = argparse.ArgumentParser(
        prog=prog, description="Launch a coding-agent session, and read what was launched.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    # A launch's flags are read again from `rest` against the catalog, so these two
    # parsers abbreviate nothing: `--project` must stay the catalog's, never become
    # `--project-preset`.
    run = verbs.add_parser("run", help="launch a session", allow_abbrev=False,
                           epilog=LAUNCH_EPILOG,
                           description="Launch a session from at most one preset and the "
                                       "flags typed. Flags after -- go to the harness last.")
    _launch_flags(run)

    preview = verbs.add_parser("preview", help="print what a launch would run",
                               allow_abbrev=False, epilog=LAUNCH_EPILOG,
                               description="Print the argv and environment a launch would "
                                           "use; starts nothing.")
    _launch_flags(preview)
    preview.add_argument("--json", action="store_true", help="print one JSON object")

    log = verbs.add_parser("log", help="list launches, newest first",
                           description="List launches from the launch log, newest first.")
    log.add_argument("--limit", type=int, metavar="N", help="list at most N launches")
    log.add_argument("--before", metavar="TIME",
                     help="list launches made before TIME (UTC ISO 8601)")
    log.add_argument("--json", action="store_true", help="print one JSON object per line")

    presets = verbs.add_parser("presets", help="list, show or check the presets",
                               description="List, show or check the seats and project "
                                           "presets in presets.toml.")
    presets.add_argument("action", nargs="?", choices=PRESET_ACTIONS, default="list",
                         help="list (the default), show NAME, or check")
    presets.add_argument("name", nargs="?", metavar="NAME",
                         help="the preset to show: a name, or seat.NAME or project.NAME")
    presets.add_argument("--project", metavar="KEY",
                         help="list the project's agents and the seat matching each")
    presets.add_argument("--json", action="store_true", help="print JSON")

    catalog = verbs.add_parser("catalog", help="list the flag rows this install offers",
                               description="List the flag rows offered on this install.")
    catalog.add_argument("harness", nargs="?", metavar="HARNESS",
                         help="list only this harness's rows")
    catalog.add_argument("--json", action="store_true", help="print JSON")

    handoff = verbs.add_parser("handoff", help="print this session's handoff folder",
                               description="Print the handoff folder for this session's "
                                           "key.")
    handoff.add_argument("what", choices=("dir",), help="dir")
    handoff.add_argument("--json", action="store_true", help="print JSON")

    handoffs = verbs.add_parser("handoffs", help="list a key's handoffs",
                                description="List a key's handoffs, newest first, and "
                                            "which still wait for their seat. The key is "
                                            "this session's unless --key names one.")
    handoffs.add_argument("--key", metavar="KEY", help="list this key's handoffs")
    handoffs.add_argument("--json", action="store_true", help="print JSON")

    deliver = verbs.add_parser("deliver", help="internal: send a launch's first turn",
                               description="Internal: wait for a launched session and send "
                                           "its first turn.")
    deliver.add_argument("request_id", metavar="REQUEST_ID", help="the launch's request id")
    deliver.add_argument("--pid", type=int, required=True, metavar="PID",
                         help="the pid the session runs as")
    return parser


def parse(argv: Sequence[str] | None = None, *, prog: str = "moxi launcher"
          ) -> argparse.Namespace:
    """Parse `argv`. For `run` and `preview` what the parser does not know is kept, in
    order, as `rest`; any other verb refuses it (exit 2)."""
    parser = build_parser(prog)
    args, rest = parser.parse_known_args(argv)
    if args.verb not in LAUNCH_VERBS and rest:
        parser.error(f"unrecognized arguments: {' '.join(rest)}")
    if args.verb == "presets" and args.name is not None and args.action != "show":
        parser.error(f"presets {args.action} takes no NAME")
    if args.verb == "presets" and args.action == "show" and args.name is None:
        parser.error("presets show needs a NAME")
    args.rest = rest
    return args


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi launcher") -> int:
    args = parse(argv, prog=prog)
    try:
        return VERBS[args.verb](args, prog)
    except _errors() as error:
        return _refuse(prog, args.verb, error)


__all__ = ["LAUNCH_VERBS", "PRESET_ACTIONS", "VERBS", "NoPreset", "build_parser",
           "command_line", "log_line", "main", "parse", "preview_json", "refusal_lines"]

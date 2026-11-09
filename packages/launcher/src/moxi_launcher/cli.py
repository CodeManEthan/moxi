"""`moxi launcher`: launch a session, act on the sessions it launched, and read what was
launched.

    moxi launcher run [--seat NAME | --project-preset NAME] [--leave-out ROW ...]
                      [catalog flags] [-- extra]
    moxi launcher preview [--seat NAME | --project-preset NAME] [--leave-out ROW ...]
                          [catalog flags] [--json]
    moxi launcher log [--table runs|exits|holds] [--limit N] [--before TIME] [--json]
    moxi launcher presets [list | show NAME | check] [--project KEY] [--json]
    moxi launcher catalog [HARNESS] [--json]
    moxi launcher handoff dir [--json]
    moxi launcher handoffs [--key KEY] [--json]
    moxi launcher deliver REQUEST_ID --pid PID
    moxi launcher relaunch --self --handoff FILE [--dry-run] [--json]
    moxi launcher relaunch SESSION_ID|IDENTITY [--dry-run] [--json]
    moxi launcher relaunch --status RUN [--json]
    moxi launcher relaunch --from-record RUN [--json]
    moxi launcher exit-session SESSION_ID [--dry-run] [--json]
    moxi launcher exits [--limit N] [--before TIME] [--json]
    moxi launcher sessions [--limit N] [--before TIME] [--json]
    moxi launcher resume SESSION_ID [--slot N]
    moxi launcher keep-warm hold NAME | release NAME | holds [--json]
    moxi launcher state [SESSION_ID] [--json]

`run` launches; `preview` prints the argv and environment a launch would use and starts
nothing. `log` lists launches, newest first, or with `--table` the runs, the exits or the
holds. `presets` lists, shows or checks the seats and project presets, and with `--project
KEY` lists a project's agents beside the seat of each. `catalog` lists the flag rows this
install offers. `handoff dir` prints the handoff folder for this session; `handoffs` lists
a key's handoffs and which still wait for their seat, a `--key` in the folder of that key's
newest launch. `deliver` is internal: the detached process that sends a terminal launch's
first turn.

The acts on a launched session: `relaunch` arms a relaunch of this session (`--self`), or
relaunches another as one of its directors, shows a run (`--status`), or launches again
the session of a run that exited it and did not bring it back (`--from-record`).
`exit-session` exits a launched session, and `exits` lists every exit tried. `sessions`
lists past launches and whether each can be resumed; `resume` launches one again,
resumed. `keep-warm` opens, releases and lists this session's named holds. `state` shows
a launched session's relaunch, recycle and keep-warm state, and whether it needs a look.

`run` and `preview` keep what this parser does not know as `rest`, and read it again
against the catalog of the harness the launch starts: the one `--harness` names, else the
preset's, else the one `[defaults]` names, else the shipped default. A flag the catalog
does not know is passed on to the harness, last on its argv, after one warning line.

The CLI calls the library in its own process. Exit codes are `moxi.exit`'s: 0 done (a
relaunch armed or relaunched, a session exited); 1 a start that failed, or a run that
ended in a failed outcome; 2 bad arguments or a bad value; 3 refused (before the launch's
row is written nothing is written, after it the row says why); 75 a lock held past its
wait, an act held by the session's state now, or a run still going past the follow
bound: try again. A refusal prints one line ending in its short code, and a hint.
`--json` prints the verb's data, with its outcome word.
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

#: The tables `log --table` lists besides the launches.
TABLES = ("runs", "exits", "holds")
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
    "RunOpen": ("run-open", "that run must end first; moxi launcher relaunch --status {run} "
                            "shows it"),
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
        run = getattr(error, "run", None)
        if code == "run-open" and run is not None:
            hint = hint.format(run=run.run_id)
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

    left_out = tuple(args.leave_out or ())
    asked = presets.Request(seat=args.seat, project_preset=args.project_preset,
                            left_out=left_out)
    harness = catalog.typed_harness(args.rest) or presets.harness_of(asked)
    parsed = catalog.parse(args.rest, harness)
    if parsed.warning:
        _warn(parsed.warning)
    return api.LaunchRequest(seat=args.seat, project_preset=args.project_preset,
                             typed=dict(parsed.values), extra=tuple(parsed.extra),
                             off_posture=args.off_posture, left_out=left_out,
                             cwd=os.getcwd())


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


def _bad_bounds(args: argparse.Namespace, prog: str, verb: str) -> int | None:
    """USAGE, after saying why, when `--limit` or `--before` can't page a list."""
    from . import store

    if args.limit is not None and args.limit < 1:
        print(f"{prog} {verb}: --limit must be 1 or more", file=sys.stderr)
        return codes.USAGE
    if args.before is not None:
        try:
            store.parse_time(args.before)
        except ValueError:
            print(f"{prog} {verb}: --before {args.before!r} is not a time; give UTC ISO "
                  "8601, such as 2026-10-30T12:00:00Z", file=sys.stderr)
            return codes.USAGE
    return None


def _log(args: argparse.Namespace, prog: str) -> int:
    from . import store

    bad = _bad_bounds(args, prog, "log")
    if bad is not None:
        return bad
    if args.table != "launches":
        return _log_table(args)
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


def _log_table(args: argparse.Namespace) -> int:
    """`log --table runs|exits|holds`: that table's rows, newest first."""
    from . import store

    if args.json:
        for line in store.table_lines(args.table, limit=args.limit, before=args.before):
            print(line)
        return codes.OK
    try:
        conn = store.connect(create=False)
    except core_store.StoreTooNew:
        raise
    except core_store.StoreError:
        rows = []
    else:
        try:
            rows = store.table(conn, args.table, limit=args.limit, before=args.before)
        finally:
            conn.close()
    if not rows:
        print(f"no {args.table}", file=sys.stderr)
        return codes.OK
    for row in rows:
        print(table_line(args.table, row))
    return codes.OK


def table_line(table: str, row) -> str:
    """One row of `log --table` as it prints: a run with when it was made, an exit as
    `exits` prints it, a hold with when it was opened and whether it is still open."""
    from . import exits, relaunch

    if table == "runs":
        return f"{row.created_at[:19]}Z  {relaunch.run_line(row)}"
    if table == "exits":
        return exits.exit_line(row)
    shut = "open" if row.released_at is None else f"released {row.released_at[:19]}Z"
    return f"{row.opened_at[:19]}Z  {row.name}  launch {row.request_id}  {shut}"


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
        read = handoffs.folder_of_key(key)
        folder, launch = read.folder, read.launch
    else:
        here = handoffs.current(create=False)
        key, folder, launch = here.key, here.folder, None
    found = handoffs.handoffs(folder, key)
    waiting = {h.path for h in handoffs.unpicked(folder, key, _picked(key))}
    if args.json:
        shown = {"key": key, "folder": str(folder)}
        if args.key is not None:
            shown["launch"] = launch.request_id if launch is not None else None
        shown["handoffs"] = [{"path": str(h.path), "date": h.date,
                              "waiting": h.path in waiting} for h in found]
        print(_dump(shown))
        return codes.OK
    if args.key is not None:
        if launch is not None:
            print(f"reading {folder}, the handoff folder of {key}'s newest launch "
                  f"({launch.cwd})", file=sys.stderr)
        else:
            print(f"reading {folder}: {key} has no launch, so this directory's folder",
                  file=sys.stderr)
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


# -- acts on launched sessions --------------------------------------------------------------


def _said(result, args: argparse.Namespace) -> int:
    """Print a verb's result and return its exit code: with `--json` its data (the outcome
    word among it) on stdout; else its lines, on stdout when it exits 0 and on stderr
    otherwise."""
    if getattr(args, "json", False):
        print(_dump(dict(result.data)))
    else:
        out = sys.stdout if result.exit_code == codes.OK else sys.stderr
        for line in result.lines:
            print(line, file=out)
    return result.exit_code


def _relaunch(args: argparse.Namespace, prog: str) -> int:
    from . import relaunch

    if args.status is not None:
        return _said(relaunch.status(args.status), args)
    if args.from_record is not None:
        return _said(relaunch.from_record(args.from_record), args)
    if args.self:
        return _said(relaunch.arm_self(args.handoff, dry_run=args.dry_run), args)
    return _said(relaunch.call_director(args.target, dry_run=args.dry_run), args)


def _exit_session(args: argparse.Namespace, prog: str) -> int:
    from . import exits

    return _said(exits.exit_session(args.session_id, dry_run=args.dry_run), args)


def _listed(args: argparse.Namespace, result, key: str) -> int:
    """Print a list verb's result: with `--json` one JSON object per entry per line, as
    `log --json` prints; else one line per entry, or "no <key>" on stderr."""
    entries = result.data[key]
    if args.json:
        for entry in entries:
            print(json.dumps(entry, ensure_ascii=False, separators=(",", ":"), default=str))
        return result.exit_code
    if not entries:
        print(f"no {key}", file=sys.stderr)
        return result.exit_code
    for line in result.lines:
        print(line)
    return result.exit_code


def _exits(args: argparse.Namespace, prog: str) -> int:
    from . import exits

    bad = _bad_bounds(args, prog, "exits")
    if bad is not None:
        return bad
    return _listed(args, exits.exits(limit=args.limit, before=args.before), "exits")


def _sessions(args: argparse.Namespace, prog: str) -> int:
    from . import resume

    bad = _bad_bounds(args, prog, "sessions")
    if bad is not None:
        return bad
    return _listed(args, resume.sessions(limit=args.limit, before=args.before), "sessions")


def _resume(args: argparse.Namespace, prog: str) -> int:
    from . import resume

    return _said(resume.resume(args.session_id, slot=args.slot, warn=_warn), args)


def _keep_warm(args: argparse.Namespace, prog: str) -> int:
    from . import keepwarm

    if args.action == "hold":
        return _said(keepwarm.hold(args.name), args)
    if args.action == "release":
        return _said(keepwarm.release(args.name), args)
    return _said(keepwarm.holds(), args)


def _state(args: argparse.Namespace, prog: str) -> int:
    from . import state

    return _said(state.show(args.session_id), args)


VERBS: dict[str, Callable[[argparse.Namespace, str], int]] = {
    "run": _run,
    "preview": _preview,
    "log": _log,
    "presets": _presets,
    "catalog": _catalog,
    "handoff": _handoff,
    "handoffs": _handoffs,
    "deliver": _deliver,
    "relaunch": _relaunch,
    "exit-session": _exit_session,
    "exits": _exits,
    "sessions": _sessions,
    "resume": _resume,
    "keep-warm": _keep_warm,
    "state": _state,
}


# -- the parser ---------------------------------------------------------------------------


def _launch_flags(parser: argparse.ArgumentParser) -> None:
    """The flags `run` and `preview` share: at most one preset, the posture override, and
    the rows left out."""
    preset = parser.add_mutually_exclusive_group()
    preset.add_argument("--seat", metavar="NAME", help="launch the seat NAME")
    preset.add_argument("--project-preset", metavar="NAME",
                        help="launch the project preset NAME")
    parser.add_argument("--off-posture", action="store_true",
                        help="allow a permission row that differs from your default")
    parser.add_argument("--leave-out", action="append", metavar="ROW",
                        help="leave the catalog row ROW out of this launch, whatever "
                             "[defaults] or the preset sets; give it once per row")


LAUNCH_EPILOG = ("Any flag of the launcher's or the harness's catalog may follow; "
                 "moxi launcher catalog lists them. A flag the catalog does not know is "
                 "passed on to the harness, after one warning.")


def build_parser(prog: str = "moxi launcher") -> argparse.ArgumentParser:
    """The parser of every verb. `run` and `preview` carry only the launcher's fixed flags
    here; the catalog's rows join them when the catalog is read."""
    parser = argparse.ArgumentParser(
        prog=prog, description="Launch a coding-agent session, act on the sessions launched, "
                               "and read what was launched.")
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
                           description="List launches from the launch log, newest first, or "
                                       "with --table the runs, exits or holds.")
    log.add_argument("--table", choices=("launches", *TABLES), default="launches",
                     help="the table to list: launches (the default), runs, exits or holds")
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
    handoffs.add_argument("--key", metavar="KEY",
                          help="list this key's handoffs, in the folder of its newest launch")
    handoffs.add_argument("--json", action="store_true", help="print JSON")

    deliver = verbs.add_parser("deliver", help="internal: send a launch's first turn",
                               description="Internal: wait for a launched session and send "
                                           "its first turn.")
    deliver.add_argument("request_id", metavar="REQUEST_ID", help="the launch's request id")
    deliver.add_argument("--pid", type=int, required=True, metavar="PID",
                         help="the pid the session runs as")
    _act_parsers(verbs)
    return parser


def _act_parsers(verbs) -> None:
    """The parsers of the acts on launched sessions."""
    relaunch = verbs.add_parser(
        "relaunch", help="relaunch a session in its slot", allow_abbrev=False,
        description="Relaunch a session in its slot: this session with --self and a handoff "
                    "for the new one, or another as one of its directors. --status shows a "
                    "run; --from-record launches again the session of a run that exited it "
                    "and did not bring it back.")
    relaunch.add_argument("target", nargs="?", metavar="SESSION_ID|IDENTITY",
                          help="the session to relaunch as its director: its session id or "
                               "its comms name")
    form = relaunch.add_mutually_exclusive_group()
    form.add_argument("--self", action="store_true",
                      help="arm a relaunch of the session this runs in")
    form.add_argument("--status", metavar="RUN", help="show the run RUN and its events")
    form.add_argument("--from-record", metavar="RUN",
                      help="launch again the session the run RUN exited")
    relaunch.add_argument("--handoff", metavar="FILE",
                          help="with --self: the handoff the new session gets first")
    relaunch.add_argument("--dry-run", action="store_true",
                          help="take the checks and write nothing")
    relaunch.add_argument("--json", action="store_true", help="print one JSON object")

    exit_session = verbs.add_parser(
        "exit-session", help="exit a launched session", allow_abbrev=False,
        description="Exit a session the launcher launched, once it is quiet; in a slot, "
                    "Claude Code's exit dialog is answered too.")
    exit_session.add_argument("session_id", metavar="SESSION_ID", help="the session to exit")
    exit_session.add_argument("--dry-run", action="store_true",
                              help="take the checks and the quiet read; exit nothing")
    exit_session.add_argument("--json", action="store_true", help="print one JSON object")

    for name, what in (("exits", "every exit tried, newest first"),
                       ("sessions", "past launches, newest first, and which can resume")):
        listing = verbs.add_parser(name, help=f"list {what}", allow_abbrev=False,
                                   description=f"List {what}.")
        listing.add_argument("--limit", type=int, metavar="N", help="list at most N")
        listing.add_argument("--before", metavar="TIME",
                             help="list those before TIME (UTC ISO 8601)")
        listing.add_argument("--json", action="store_true",
                             help="print one JSON object per line")

    resume = verbs.add_parser(
        "resume", help="launch a past session again, resumed", allow_abbrev=False,
        description="Launch a past session again, resumed, where it ran: its slot when "
                    "that can take it, else the lowest that can, or this terminal.")
    resume.add_argument("session_id", metavar="SESSION_ID", help="the session to resume")
    resume.add_argument("--slot", metavar="N", help="resume it in slot N instead")

    keep_warm = verbs.add_parser(
        "keep-warm", help="open, release or list this session's holds", allow_abbrev=False,
        description="Open, release or list the named holds that keep this session's cache "
                    "warm while work it handed out is running.")
    actions = keep_warm.add_subparsers(dest="action", metavar="ACTION")
    actions.required = True
    for action, what in (("hold", "open the hold NAME"), ("release", "release the hold NAME")):
        one = actions.add_parser(action, help=what, allow_abbrev=False,
                                 description=what[0].upper() + what[1:] + ".")
        one.add_argument("name", metavar="NAME", help="the hold's name: the work it stands for")
        one.set_defaults(json=False)
    listed = actions.add_parser("holds", help="list the open holds", allow_abbrev=False,
                                description="List the open holds of this session's launch "
                                            "and the launches it replaced.")
    listed.add_argument("--json", action="store_true", help="print one JSON object")

    state = verbs.add_parser(
        "state", help="show a launched session's state", allow_abbrev=False,
        description="Show a launched session's relaunch, recycle and keep-warm state, and "
                    "whether it needs a look. With no SESSION_ID, this session's.")
    state.add_argument("session_id", nargs="?", metavar="SESSION_ID",
                       help="the session; this one when left out")
    state.add_argument("--json", action="store_true", help="print one JSON object")


def _check_relaunch(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Refuse (exit 2) a relaunch that asks for none, or more than one, of its forms."""
    forms = [args.self, args.target is not None, args.status is not None,
             args.from_record is not None]
    if sum(forms) != 1:
        parser.error("relaunch takes one of --self --handoff FILE, a SESSION_ID or "
                     "IDENTITY, --status RUN or --from-record RUN")
    if args.self and not args.handoff:
        parser.error("relaunch --self needs --handoff FILE")
    if args.handoff and not args.self:
        parser.error("--handoff goes with --self")
    if args.dry_run and (args.status is not None or args.from_record is not None):
        parser.error("--dry-run goes with --self or a SESSION_ID or IDENTITY")


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
    if args.verb == "relaunch":
        _check_relaunch(parser, args)
    args.rest = rest
    return args


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi launcher") -> int:
    args = parse(argv, prog=prog)
    try:
        return VERBS[args.verb](args, prog)
    except _errors() as error:
        return _refuse(prog, args.verb, error)


__all__ = ["LAUNCH_VERBS", "PRESET_ACTIONS", "TABLES", "VERBS", "NoPreset", "build_parser",
           "command_line", "log_line", "main", "parse", "preview_json", "refusal_lines",
           "table_line"]

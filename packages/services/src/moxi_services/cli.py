"""`moxi services`: every daemon and timer on the machine, from the command line.

    moxi services list [--project KEY] [--kind daemon|timer] [--health] [--json]
    moxi services show ID [--json]
    moxi services add NAME [--timer (--every N | --at HH:MM[,HH:MM...] [--days mon,...])]
                           [--cwd DIR] [--health "CMD ARGS"] [--project KEY]
                           [--launches-session] [--doc TEXT] [--enable] -- ARGV...
    moxi services add NAME --unit REF (--enable | --disable) [--timer]
                           [--health "CMD ARGS"] [--project KEY] [--launches-session]
                           [--doc TEXT]
    moxi services remove ID
    moxi services start|stop|restart ID...
    moxi services enable|disable ID...
    moxi services watch [ID...] [--every SECONDS] [--json]

`ID` is `<module>/<name>` for a module's daemon or timer, and `services/<name>` or a
bare `<name>` for one the user added or adopted. The read verbs (`list`, `show`,
`watch`) take `--json`.

A verb given several ids acts on each, prints its lines, and exits with the worst
code: 0 done; 1 an act or a write failed, or the state read after it does not match;
2 asked wrongly; 3 refused; 75 the store or the core lock stayed busy (try again). A
failure prints its lines and the command that fixes it to stderr.

`--health "CMD ARGS"` is split as this system splits a command line (POSIX shell words,
or the Windows rules `CommandLineToArgvW` follows), stored as a list and printed back.
`--at` and `--days` take comma-separated lists.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from collections.abc import Sequence

import moxi.exit as codes

from . import acts, rows, state, store
from .errors import ServicesError, Usage
from .rows import ModuleRow, UserRow
from .verdict import View, last_run, views

#: How often `watch` reads the supervisor, by default, and how often it runs health
#: checks, in seconds.
WATCH_EVERY = 5.0
WATCH_HEALTH = 30.0
#: Exit codes from least to most severe, for a verb given several ids.
_SEVERITY = (codes.OK, codes.TEMPFAIL, codes.REFUSED, codes.USAGE, codes.ERROR)

_PARSERS: dict[str, argparse.ArgumentParser] = {}

# What `watch` waits and counts with; tests put their own in place.
_sleep = time.sleep
_clock = time.monotonic


def worst(codes_: Sequence[int]) -> int:
    """The most severe of several exit codes: a failure over a usage error over a
    refusal over a busy wait over done."""
    def rank(code: int) -> int:
        return _SEVERITY.index(code) if code in _SEVERITY else len(_SEVERITY)
    return max(codes_, key=rank, default=codes.OK)


# --- splitting a command line -----------------------------------------------------------

def split_command(text: str, *, windows: bool | None = None) -> list[str]:
    """Split `text` into an argument list as this system's command lines are split:
    `shlex.split` on POSIX, the rules of Windows' `CommandLineToArgvW` there. Raises
    `Usage` (exit 2) on an empty command or unbalanced quotes."""
    windows = sys.platform == "win32" if windows is None else windows
    if windows:
        argv = _split_windows(text)
    else:
        try:
            argv = shlex.split(text)
        except ValueError as exc:
            raise Usage(f"--health {text!r}: {exc}", None) from None
    if not argv or not argv[0]:
        raise Usage("--health is empty: give the command to run", None)
    return argv


def _split_windows(text: str) -> list[str]:
    """`CommandLineToArgvW`'s rules. The program name, first, ends at the next blank
    outside quotes and takes no escapes. After it: 2n backslashes and a quote are n
    backslashes and a quote that opens or closes a quoted part; 2n+1 backslashes and a
    quote are n backslashes and a literal quote; backslashes before anything else are
    themselves; inside a quoted part two quotes are one literal quote."""
    i, n = 0, len(text)
    argv: list[str] = []
    while i < n and text[i] in " \t":
        i += 1
    if i >= n:
        return argv
    # The program name.
    word = []
    if text[i] == '"':
        i += 1
        while i < n and text[i] != '"':
            word.append(text[i])
            i += 1
        i += 1
    while i < n and text[i] not in " \t":
        word.append(text[i])
        i += 1
    argv.append("".join(word))
    # The arguments.
    while True:
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            return argv
        word, quoted = [], False
        while i < n and (quoted or text[i] not in " \t"):
            c = text[i]
            if c == "\\":
                j = i
                while j < n and text[j] == "\\":
                    j += 1
                count = j - i
                if j < n and text[j] == '"':
                    word.append("\\" * (count // 2))
                    if count % 2:
                        word.append('"')
                        i = j + 1
                    else:
                        i = j
                    continue
                word.append("\\" * count)
                i = j
                continue
            if c == '"':
                if quoted and i + 1 < n and text[i + 1] == '"':
                    word.append('"')
                    i += 2
                    continue
                quoted = not quoted
                i += 1
                continue
            word.append(c)
            i += 1
        argv.append("".join(word))


def _commas(text: str | None) -> list[str] | None:
    if text is None:
        return None
    return [part.strip() for part in text.split(",")]


# --- the parser -------------------------------------------------------------------------

def _parser(prog: str) -> argparse.ArgumentParser:
    """The parser, built once per `prog`."""
    if prog in _PARSERS:
        return _PARSERS[prog]
    parser = argparse.ArgumentParser(
        prog=prog, description="Every daemon and timer on the machine: list, show, add, "
                               "remove, start, stop, restart, enable, disable and watch.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    def verb(name: str, help: str, *, reads: bool = False) -> argparse.ArgumentParser:
        p = verbs.add_parser(name, help=help, description=help[0].upper() + help[1:] + ".")
        if reads:
            p.add_argument("--json", action="store_true", help="print JSON")
        return p

    p = verb("list", "list every row with its verdict", reads=True)
    p.add_argument("--project", metavar="KEY", help="only rows of this project")
    p.add_argument("--kind", choices=("daemon", "timer"), help="only daemons or timers")
    p.add_argument("--health", action="store_true",
                   help="also run each running daemon's health check")

    p = verb("show", "show one row: its verdict, state, health, process and runs",
             reads=True)
    p.add_argument("id", metavar="ID")

    p = verb("add", "add a command as a service (after --), or adopt a unit with --unit")
    p.add_argument("name", metavar="NAME")
    p.add_argument("--unit", metavar="REF",
                   help="adopt this existing unit: a systemd user unit, a launchd label "
                        "or a Task Scheduler path")
    on = p.add_mutually_exclusive_group()
    on.add_argument("--enable", action="store_true",
                    help="turn it on (a new command starts off without it)")
    on.add_argument("--disable", action="store_true", help="turn it off")
    p.add_argument("--timer", action="store_true", help="a timer, not a daemon")
    schedule = p.add_mutually_exclusive_group()
    schedule.add_argument("--every", metavar="N", help="run every N minutes (5m) or hours (2h)")
    schedule.add_argument("--at", metavar="HH:MM[,HH:MM...]", help="run at these times")
    p.add_argument("--days", metavar="mon,...", help="only on these days (with --at)")
    p.add_argument("--cwd", metavar="DIR", help="the directory the command runs in")
    p.add_argument("--health", metavar='"CMD ARGS"',
                   help="a command whose exit 0 means the daemon is healthy")
    p.add_argument("--project", metavar="KEY", help="the project it belongs to")
    p.add_argument("--launches-session", action="store_true",
                   help="mark a timer that launches an agent session (it spends usage)")
    p.add_argument("--doc", default="", metavar="TEXT", help="one line saying what it is")

    p = verb("remove", "remove a row you added or adopted")
    p.add_argument("id", metavar="ID")

    for name, help in (("start", "start a daemon, or run a timer now"),
                       ("stop", "stop a daemon until the next login or start, or end a "
                                "timer's run"),
                       ("restart", "stop, then start"),
                       ("enable", "turn rows on: a daemon running and starting at login, "
                                  "a timer's schedule armed"),
                       ("disable", "turn rows off")):
        p = verb(name, help)
        p.add_argument("ids", nargs="+", metavar="ID")

    p = verb("watch", "print a line whenever a row's verdict changes, until interrupted",
             reads=True)
    p.add_argument("ids", nargs="*", metavar="ID")
    p.add_argument("--every", type=float, default=WATCH_EVERY, metavar="SECONDS",
                   help=f"seconds between reads (default {WATCH_EVERY:g})")

    _PARSERS[prog] = parser
    return parser


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi services") -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command: list[str] | None = None
    if argv and argv[0] == "add" and "--" in argv:
        cut = argv.index("--")
        argv, command = argv[:cut], argv[cut + 1:]
    try:
        args = _parser(prog).parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else codes.USAGE
    args.command = command
    try:
        return _VERBS[args.verb](args, sys.stdout)
    except ServicesError as exc:
        _refusal(f"{prog} {args.verb}", exc.reason, exc.fix)
        return exc.exit_code


def _refusal(where: str, reason: str, fix: str | None) -> None:
    print(f"{where}: {reason}", file=sys.stderr)
    if fix:
        print(f"  fix: {fix}", file=sys.stderr)


# --- the acts ---------------------------------------------------------------------------

def _say(outcome: acts.Outcome, out) -> None:
    """An act's lines: on stdout when it was done, else on stderr with its fix."""
    stream = out if outcome.ok else sys.stderr
    for line in outcome.lines:
        print(line, file=stream)
    if not outcome.ok and outcome.fix:
        print(f"  fix: {outcome.fix}", file=sys.stderr)


def _act(args, out) -> int:
    act = getattr(acts, args.verb)
    found = []
    for ident in args.ids:
        outcome = act(ident)
        _say(outcome, out)
        found.append(outcome.code)
    return worst(found)


def _remove(args, out) -> int:
    outcome = acts.remove(args.id)
    _say(outcome, out)
    return outcome.code


def _add(args, out) -> int:
    health = split_command(args.health) if args.health is not None else None
    if args.unit is not None:
        given = [flag for flag, value in (("--every", args.every), ("--at", args.at),
                                          ("--days", args.days), ("--cwd", args.cwd))
                 if value is not None]
        if args.command is not None:
            given.append("-- ARGV")
        if given:
            raise Usage(f"{', '.join(given)} {'is' if len(given) == 1 else 'are'} for a "
                        "command services writes, not an adopted unit, which keeps its own",
                        None)
        enable = True if args.enable else False if args.disable else None
        outcome = acts.add_adopted(args.name, args.unit, enable=enable, timer=args.timer,
                                   health=health, project=args.project,
                                   launches_session=args.launches_session, doc=args.doc)
    else:
        if not args.command:
            raise Usage("give the command to run after --, or --unit REF to adopt a unit",
                        f"moxi services add {args.name} -- /path/to/program ARGS")
        if not args.timer and (args.every or args.at or args.days):
            raise Usage("--every, --at and --days are for a timer; add --timer", None)
        cwd = None
        if args.cwd is not None:
            if not args.cwd:
                raise Usage("--cwd is empty; name a directory", None)
            cwd = os.path.abspath(os.path.expanduser(args.cwd))
        outcome = acts.add_written(args.name, args.command, timer=args.timer,
                                   every=args.every, at=_commas(args.at),
                                   days=_commas(args.days), cwd=cwd, health=health,
                                   project=args.project,
                                   launches_session=args.launches_session, doc=args.doc,
                                   enable=args.enable)
    _say(outcome, out)
    if outcome.ok and health is not None:
        print(f"health check: {json.dumps(health)}", file=out)
    return outcome.code


# --- the reads --------------------------------------------------------------------------

def _all_rows(project: str | None = None, kind: str | None = None, *,
              noted: dict[str, str] | None = None) -> list:
    """Every row: the user's rows, then every installed module's daemons and timers.
    A module whose run-time declarations failed is said on stderr. With `noted` (the
    problems said before, by module, kept up to date here), a problem is said only when
    it is new or has changed."""
    user = store.read_rows()
    found: list = list(user)
    if project is None:
        modules, problems = rows.module_rows()
        for module, reason in problems:
            if noted is None or noted.get(module) != reason:
                print(f"note: {reason}; {module}'s static daemons and timers are listed",
                      file=sys.stderr)
        if noted is not None:
            noted.clear()
            noted.update(problems)
        found += modules
    else:
        wanted = project.casefold()
        found = [r for r in found if r.project is not None
                 and r.project.casefold() == wanted]
    if kind is not None:
        found = [r for r in found if r.kind == kind]
    return found


def _iso(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _switch(row) -> str | None:
    """The settings key that is the row's on/off, or None for the store's flag."""
    if isinstance(row, ModuleRow):
        return row.switch
    if state.is_wrapped(row):
        return state.key(row.name)
    return None


def _flag_text(view: View) -> str:
    row, flag = view.row, view.flag
    word = "unknown" if flag is None else "on" if flag else "off"
    if isinstance(row, ModuleRow) and row.switch is None:
        return "on (no switch)"
    key = _switch(row)
    return f"{key} {word}" if key else word


def _when_text(view: View) -> str:
    st = view.st
    if st is None:
        return ""
    if view.row.kind == "daemon":
        return f"since {time.strftime('%Y-%m-%d %H:%M', time.localtime(st.since))}" \
            if st.state == "active" and st.since else ""
    last = last_run(st)
    if last is None:
        return "no run yet"
    return f"last run {last.text}" + (f" at {last.at}" if last.at else "")


def _view_json(view: View) -> dict:
    row, st = view.row, view.st
    last = last_run(st)
    return {
        "id": row.id, "module": row.module, "name": row.name, "form": row.form,
        "kind": row.kind,
        "project": row.project if isinstance(row, UserRow) else None,
        "launches_session": bool(isinstance(row, UserRow) and row.launches_session),
        "flag": view.flag, "switch": _switch(row),
        "verdict": view.verdict.as_dict(),
        "state": None if st is None else st.state,
        "enabled": None if st is None else st.enabled,
        "pid": None if st is None else st.pid,
        "since": None if st is None else _iso(st.since),
        "last_run": None if last is None else {"failed": last.failed, "text": last.text,
                                               "at": last.at},
        "health": (None if view.health is None
                   else {"state": view.health[0], "detail": view.health[1]}),
    }


def _list(args, out) -> int:
    found = _all_rows(args.project, args.kind)
    seen = views(found, health=args.health)
    if args.json:
        print(json.dumps([_view_json(v) for v in seen], indent=2), file=out)
        return codes.OK
    if not seen:
        print("no services" + (f" of project {args.project}" if args.project else "")
              + "; `moxi services add NAME -- ARGV...` adds one", file=out)
        return codes.OK
    groups: dict[str, list[View]] = {}
    none: list[View] = []
    for view in seen:
        project = view.row.project if isinstance(view.row, UserRow) else None
        if project is None:
            none.append(view)
        else:
            groups.setdefault(project, []).append(view)
    ordered = [(key, groups[key]) for key in sorted(groups, key=lambda k: (k.casefold(), k))]
    if none:
        ordered.append((None, none))
    table = []
    for _key, group in ordered:
        for view in group:
            health = ""
            if args.health:
                health = "-" if view.health is None else f"health {view.health[0]}"
            table.append((view, [view.row.id, view.row.kind, view.verdict.state,
                                 view.verdict.reason, health, _flag_text(view),
                                 _when_text(view)]))
    widths = [max(len(cells[i]) for _v, cells in table) for i in range(7)]
    marked = False
    i = 0
    for key, group in ordered:
        if groups:
            print(f"{key}" if key is not None else "no project", file=out)
        for view in group:
            cells = table[i][1]
            i += 1
            text = "  ".join(c.ljust(widths[n]) for n, c in enumerate(cells) if widths[n])
            mark = isinstance(view.row, UserRow) and view.row.launches_session
            marked = marked or mark
            print(("  " if groups else "") + text.rstrip() + ("  *" if mark else ""),
                  file=out)
    if marked:
        print("* launches an agent session, so its runs spend usage", file=out)
    return codes.OK


def _process(pid: int | None) -> dict | None:
    """A running daemon's process: uptime, resident memory and CPU seconds, or None."""
    if not pid:
        return None
    try:
        import psutil

        proc = psutil.Process(pid)
        with proc.oneshot():
            started = proc.create_time()
            rss = proc.memory_info().rss
            cpu = proc.cpu_times()
        return {"pid": pid, "uptime_seconds": round(max(0.0, time.time() - started), 1),
                "rss_bytes": int(rss), "cpu_seconds": round(cpu.user + cpu.system, 2)}
    except Exception:  # noqa: BLE001 - a process gone or unreadable shows nothing
        return None


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _show(args, out) -> int:
    row = acts.resolve(args.id)
    view = views([row], health=True)[0]
    st = view.st
    process = _process(st.pid) if (row.kind == "daemon" and st is not None
                                   and st.state == "active") else None
    runs = state.last_runs(row, 5) if row.kind == "timer" else []
    if args.json:
        found = _view_json(view)
        found["row"] = row.as_dict()
        found["detail"] = None if st is None else st.detail
        found["log"] = None if st is None else st.log
        found["wrapped"] = (None if st is None or st.wrapped is None else
                            {"module": st.wrapped.module, "key": st.wrapped.key,
                             "name": st.wrapped.name})
        found["process"] = process
        found["runs"] = list(runs)
        print(json.dumps(found, indent=2), file=out)
        return codes.OK
    lines = [row.id]

    def put(label: str, value) -> None:
        if value not in (None, "", ()):
            lines.append(f"  {label:<16} {value}")

    verdict = view.verdict
    put("verdict", f"{verdict.state}: {verdict.reason}")
    put("fix", verdict.fix)
    put("kind", f"{row.form} {row.kind}")
    if st is not None:
        enabled = {True: "yes", False: "no", None: "unknown"}[st.enabled]
        what = "schedule armed" if row.kind == "timer" else "starts at login"
        put("state", f"{st.state}" + (f" ({st.detail})" if st.detail else ""))
        put(what, enabled)
    put("on/off", _flag_text(view))
    if isinstance(row, UserRow):
        put("command", shlex.join(row.argv) if row.argv else None)
        put("unit", row.unit)
        put("cwd", row.cwd)
        put("schedule", _schedule(row))
        put("health", shlex.join(row.health) if row.health else None)
        put("project", row.project)
        put("doc", row.doc)
        if row.launches_session:
            put("note", "launches an agent session, so its runs spend usage")
        if row.stopped:
            put("stopped", f"by hand at {row.stopped}")
        put("added", f"{row.created} on {row.platform}")
    else:
        put("switch", row.switch)
        put("doc", row.doc)
    if view.health is not None:
        put("checked", f"{view.health[0]}: {view.health[1]}")
    if process is not None:
        put("process", f"pid {process['pid']}, up {_duration(process['uptime_seconds'])}, "
                       f"{process['rss_bytes'] / 1048576:.1f} MiB resident, "
                       f"{process['cpu_seconds']:g} s of CPU")
    elif st is not None and st.pid:
        put("pid", st.pid)
    if row.kind == "timer":
        if runs:
            lines.append("  runs")
            for run in runs:
                lines.append(f"    {run.get('at', '?')}  {run.get('result', '?')}  "
                             f"exit {run.get('exit')}  {run.get('seconds', '?')} s")
        else:
            last = last_run(st)
            put("last run", "none recorded" if last is None else
                f"{last.text}" + (f" at {last.at}" if last.at else ""))
    if st is not None:
        put("log", st.log)
    print("\n".join(lines), file=out)
    return codes.OK


def _schedule(row: UserRow) -> str | None:
    if row.every:
        return f"every {row.every}"
    if row.at:
        days = f" on {','.join(row.days)}" if row.days else ""
        return f"at {','.join(row.at)}{days}"
    return None


# --- watch ------------------------------------------------------------------------------

def _watch_rows(idents: Sequence[str], noted: dict[str, str]) -> tuple[list, list[str]]:
    """The rows to watch this round, and the ids asked for that are gone. A module's
    problem with its declarations is said once, and again only when it changes."""
    if not idents:
        return _all_rows(noted=noted), []
    found, gone = [], []
    for ident in idents:
        try:
            found.append(acts.resolve(ident))
        except ServicesError as exc:
            if exc.exit_code != codes.REFUSED:
                raise
            gone.append(acts._ident(ident))
    return found, gone


def _watch(args, out) -> int:
    if args.every <= 0:
        raise Usage("--every takes a number of seconds above 0", None)
    for ident in args.ids:
        acts.resolve(ident)  # an unknown id is refused before the first read
    seen: dict[str, tuple[str, str]] = {}
    checked: dict[str, tuple[str, str]] = {}
    last_health: float | None = None
    noted: dict[str, str] = {}
    try:
        while True:
            try:
                found, gone = _watch_rows(args.ids, noted)
                now = _clock()
                fresh = last_health is None or now - last_health >= WATCH_HEALTH
                current = views(found, health=fresh, known_health=None if fresh
                                else checked)
                if fresh:
                    checked = {v.row.id: v.health for v in current if v.health is not None}
                    last_health = now
                for view in current:
                    key = (view.verdict.state, view.verdict.reason)
                    if seen.get(view.row.id) != key:
                        seen[view.row.id] = key
                        _watch_line(out, args.json, view.row.id, view)
                here = {v.row.id for v in current}
                for ident in [i for i in seen if i not in here] + gone:
                    if ident in seen:
                        del seen[ident]
                        _watch_line(out, args.json, ident, None)
            except ServicesError as exc:
                print(f"{time.strftime('%H:%M:%S')} {exc.reason}", file=sys.stderr)
            out.flush()
            _sleep(args.every)
    except KeyboardInterrupt:
        return codes.OK


def _watch_line(out, as_json: bool, ident: str, view: View | None) -> None:
    if as_json:
        found = {"at": _iso(time.time()), "id": ident}
        if view is None:
            found["gone"] = True
        else:
            found.update(_view_json(view))
        print(json.dumps(found), file=out)
        return
    stamp = time.strftime("%H:%M:%S")
    if view is None:
        print(f"{stamp}  {ident}  gone", file=out)
        return
    verdict = view.verdict
    fix = f"  (fix: {verdict.fix})" if verdict.fix and verdict.state in ("warn", "fail") \
        else ""
    print(f"{stamp}  {ident}  {verdict.state}  {verdict.reason}{fix}", file=out)


_VERBS = {"list": _list, "show": _show, "add": _add, "remove": _remove, "start": _act,
          "stop": _act, "restart": _act, "enable": _act, "disable": _act,
          "watch": _watch}


__all__ = ["WATCH_EVERY", "WATCH_HEALTH", "main", "worst", "split_command"]

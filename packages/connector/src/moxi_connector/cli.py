"""`moxi connector`: the public MCP door, from the command line.

    moxi connector on
    moxi connector off
    moxi connector status [--json]
    moxi connector check [--json]
    moxi connector log [--days N] [--json]

`on` sets the switch (the core writes and starts the daemon) and prints the next steps.
`off` revokes every grant, then sets the switch false. `status` shows the address, the
ports, the process, each provider with its tools and rows, the grants, the last call and
every row of the doctor that isn't ok. `check` is the one networked check: a probe of the
public address through the edge, run only when typed. `log` prints the call log.

Exit codes: 0 ok; 1 the check failed, or a step went wrong; 2 usage; 3 refused (the
connector is off, has no address, or its process doesn't answer). With `--json` a refusal
prints `{"error": ..., "fix": ...}`.

Verbs are kept by name in `VERBS`, each `fn(args, out) -> int`, with their arguments added
in `_parser`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Any, TextIO

from moxi import exit as codes
from moxi.config import ConfigError

from . import calllog, checks, doctor, probe
from . import origin as origins

MODULE = "connector"
TARGET = doctor.TARGET
#: Where Claude takes the address.
ADD_IN_CLAUDE = "Claude's Settings, Connectors, Add custom connector"


class Refused(Exception):
    """A verb that can't go ahead: `reason`, and the command that fixes it."""

    def __init__(self, reason: str, fix: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.fix = fix


# -- the switch ------------------------------------------------------------------------------

def _set_switch(value: bool, by: str) -> list[str]:
    """Set `enabled` through `moxi.config.set`, which writes and applies the daemon's
    units. Returns the lines the core printed while it applied them."""
    from moxi import config

    lines = io.StringIO()
    with contextlib.redirect_stdout(lines):
        config.set(MODULE, "enabled", value, by=by)
    return [line for line in lines.getvalue().splitlines() if line.strip()]


def revoke_every_grant() -> str | None:
    """Revoke every grant in the grants file, before `off` sets the switch false, so no
    grant stays valid behind a port nothing holds. Returns the line `off` prints about
    it, or None when there is nothing to say. The sign-in fills this in; until then there
    are no grants to revoke."""
    return None


def _on(args, out: TextIO) -> int:
    for line in _set_switch(True, "moxi connector on"):
        print(line, file=out)
    snap = doctor.take(ask=_no_ask)
    print("The connector is on.", file=out)
    if snap.origin is None:
        print("Next:", file=out)
        print(f"  1. moxi tunnel add {TARGET}", file=out)
        print("  2. moxi connector status, which shows the URL to paste into "
              f"{ADD_IN_CLAUDE}", file=out)
        return codes.OK
    print(f"Paste this URL into {ADD_IN_CLAUDE}:", file=out)
    print(f"  {snap.origin.resource}", file=out)
    warning = snap.origin.port_warning()
    if warning:
        print(warning, file=out)
    return codes.OK


def _off(args, out: TextIO) -> int:
    code = codes.OK
    try:
        said = revoke_every_grant()
    except Exception as e:  # noqa: BLE001 - said, and the switch still goes off
        print(f"The grants couldn't be revoked: {e}", file=sys.stderr)
        print("  fix: moxi connector revoke --all", file=sys.stderr)
        code = codes.ERROR
    else:
        if said:
            print(said, file=out)
    for line in _set_switch(False, "moxi connector off"):
        print(line, file=out)
    print("The connector is off.", file=out)
    address = doctor.take(ask=_no_ask).address
    if address is not None and getattr(address, "state", None) is not None:
        print(f"The tunnel entry {TARGET} stays until moxi tunnel remove {TARGET}.",
              file=out)
    return code


def _no_ask(method: str, params: object = None) -> object:
    from moxi import ipc

    raise ipc.NotServing("not asked")


# -- status ----------------------------------------------------------------------------------

def _status(args, out: TextIO) -> int:
    snap = doctor.take()
    rows = doctor.build(snap)
    if args.json:
        print(json.dumps(_status_json(snap, rows), indent=2), file=out)
        return codes.OK
    if not snap.switch:
        print("The connector is off; moxi connector on turns it on.", file=out)
    if snap.origin is not None:
        print(f"URL: {snap.origin.resource}   (paste into {ADD_IN_CLAUDE})", file=out)
    else:
        print(f"URL: none yet; moxi tunnel add {TARGET} gives one", file=out)
    print(f"loopback port: {snap.port}", file=out)
    answer = snap.answer
    if answer is None:
        if snap.switch:
            print(f"process: doesn't answer ({snap.error})", file=out)
    else:
        print(f"process: answers, pid {answer.get('pid')}", file=out)
        served = {t["name"]: t["provider"] for t in answer.get("tools") or ()
                  if isinstance(t, dict)}
        if not answer.get("providers"):
            print("providers: none", file=out)
        for entry in answer.get("providers") or ():
            tools = [t for t in entry.get("tools") or ()
                     if served.get(t) == entry.get("module")]
            line = f"provider {entry.get('module')}: {entry.get('state')}"
            if entry.get("why"):
                line += f" ({entry['why']})"
            print(line, file=out)
            if tools:
                print(f"  tools: {', '.join(tools)}", file=out)
        for name, level, text in answer.get("rows") or ():
            print(f"  {name}: {level}: {text}", file=out)
    if snap.grants is not None:
        print(f"grants: {snap.grants.count}"
              + (f", {snap.grants.stale} stale" if snap.grants.stale else ""), file=out)
    call = snap.last_call
    if call is not None:
        print(f"last call: {call.get('at')} {call.get('route')} {call.get('status')}"
              + (f" {call['tool']}" if call.get("tool") else ""), file=out)
    for row in rows:
        if row.state in ("warn", "fail"):
            print(f"{row.state}: {row.name}: {row.detail}", file=out)
            if row.fix:
                print(f"  fix: {row.fix}", file=out)
    return codes.OK


def _status_json(snap: doctor.Snapshot, rows) -> dict[str, Any]:
    return {
        "enabled": snap.switch,
        "url": snap.origin.resource if snap.origin else None,
        "origin": snap.origin.url if snap.origin else None,
        "source": snap.source,
        "port": snap.port,
        "process": snap.answer,
        "process_error": snap.error if snap.answer is None else None,
        "grants": asdict(snap.grants) if snap.grants is not None else None,
        "last_call": snap.last_call,
        "last_check": snap.last_check,
        "rows": [{"name": r.name, "state": r.state, "detail": r.detail, "fix": r.fix}
                 for r in rows],
    }


# -- check -----------------------------------------------------------------------------------

def _check(args, out: TextIO, *, fetch: probe.Fetch | None = None) -> int:
    from moxi import ipc

    snap = doctor.take()
    if not snap.switch:
        raise Refused("the connector is off", "moxi connector on")
    if snap.answer is None:
        raise Refused(f"the connector's process doesn't answer: {snap.error}",
                      checks.RESTART)
    url = snap.answer.get("url")
    if not url:
        raise Refused("the connector has no public address", f"moxi tunnel add {TARGET}")
    found = origins.parse(url)

    def ask(method: str, params: object) -> object:
        return checks.ask(method, params)

    try:
        steps = probe.run(found, ask=ask, fetch=fetch or probe.fetch, provider=snap.edge)
    except ipc.IpcError as e:
        raise Refused(f"the connector stopped answering during the check: {e}",
                      checks.RESTART) from None
    passed = probe.passed([s for s in steps if s.name != "note"])
    failed = next((s.name for s in steps if not s.passed), None)
    try:
        calllog.CallLog().write({"at": calllog.now(), "kind": "check", "url": found.url,
                                 "passed": passed, "failed": failed,
                                 "steps": {s.name: s.passed for s in steps
                                           if s.name != "note"}},
                                durable=True, collapse=None)
    except Exception as e:  # noqa: BLE001 - the check's answer stands without its row
        print(f"The check's result couldn't be written to the call log: {e}",
              file=sys.stderr)
    if args.json:
        print(json.dumps({"url": found.url, "passed": passed,
                          "steps": [asdict(s) for s in steps]}, indent=2), file=out)
    else:
        for s in steps:
            mark = "note" if s.name == "note" else ("pass" if s.passed else "FAIL")
            print(f"{mark}: {s.text}", file=out)
        print("The check passed." if passed else "The check failed.", file=out)
    return codes.OK if passed else codes.ERROR


# -- log -------------------------------------------------------------------------------------

def _log(args, out: TextIO) -> int:
    if args.days < 1:
        raise Refused("--days takes a whole number of days, 1 or more")
    for row in calllog.read_rows(days=args.days):
        if args.json:
            print(json.dumps(row, sort_keys=True), file=out)
        else:
            print(f"{row.get('at', '?')} {row.get('kind', '?')} {calllog.detail(row)}"
                  .rstrip(), file=out)
    return codes.OK


# -- the parser and the dispatch -------------------------------------------------------------

VERBS: dict[str, Callable[[argparse.Namespace, TextIO], int]] = {
    "on": _on, "off": _off, "status": _status, "check": _check, "log": _log,
}


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog, description="The public MCP door for the Claude apps: turn it on and "
                               "off, see its state, check its address, read its log.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    verbs.add_parser("on", help="turn the connector on",
                     description="Set the switch; the core writes and starts the daemon. "
                                 "Prints the next steps.")
    verbs.add_parser("off", help="revoke every grant and turn the connector off",
                     description="Revoke every grant, so the next sign-in needs a code, "
                                 "then set the switch false. The tunnel entry stays.")
    p = verbs.add_parser("status", help="the address, the process, the providers",
                         description="The URL, the ports, each provider with its tools and "
                                     "rows, the grants, the last call and every warning.")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = verbs.add_parser("check", help="probe the public address through the edge",
                         description="Ask the public address for the metadata as Claude "
                                     "would, and check the request reached this connector; "
                                     "then check an unsigned call is answered 401.")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = verbs.add_parser("log", help="the call log",
                         description="The call log's rows, oldest first.")
    p.add_argument("--days", type=int, default=1, metavar="N",
                   help="today and the N-1 days before (default 1)")
    p.add_argument("--json", action="store_true", help="one JSON object per line")
    return parser


def _fail(args, name: str, reason: str, fix: str | None, code: int) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"error": reason, "fix": fix}), file=sys.stdout)
    else:
        print(f"{name}: {reason}", file=sys.stderr)
        if fix:
            print(f"  fix: {fix}", file=sys.stderr)
    return code


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi connector") -> int:
    try:
        args = _parser(prog).parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else codes.USAGE
    name = f"{prog} {args.verb}"
    try:
        return VERBS[args.verb](args, sys.stdout)
    except Refused as exc:
        return _fail(args, name, exc.reason, exc.fix, codes.REFUSED)
    except ConfigError as exc:
        return _fail(args, name, str(exc), exc.fix, exc.exit_code)


__all__ = ["ADD_IN_CLAUDE", "Refused", "VERBS", "main", "revoke_every_grant"]

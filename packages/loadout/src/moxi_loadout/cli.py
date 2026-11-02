"""`moxi loadout`: your skills, agents and settings, placed for each harness.

    moxi loadout install [--user] [--project P]... [--replace ID]... [--dry-run] [--json]
    moxi loadout status [--user] [--project P]... [--json]
    moxi loadout add|remove KIND NAME [--user | --project P] [--json]
    moxi loadout enable|disable ID [--user | --project P] [--json]
    moxi loadout list [--json]

`install` takes over the module skills you said yes to, plans the whole manifest and
applies the steps in scope (all of them without a flag). `--replace` takes a path or an id,
`<kind>:<name>@<harness>`; what it replaces is kept under the core's `displaced/` folder.
`status` prints one line per item that is not `ok`, then notes, then a count. `add` and
`remove` write a section's list, `enable` and `disable` its `disabled` ids, then each
installs with that item's steps as the scope. `list` prints every item with its kind,
scope, harnesses and source.

KIND is skill, agent, hook, permissions or mcp. Exit codes: 0 done (every item `ok` or
`off`); 1 an error, or for `status` any item in another state; 2 usage, an unknown
manifest key included; 3 refused; 75 a lock held past its wait.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from moxi import exit as codes

#: Each verb's function, by name, so a verb can be added beside these.
VERBS: dict = {}
#: Functions that add more verbs to the parser: `fn(verbs, verb)`.
EXTRA_VERBS: list = []


def _parser(prog: str) -> argparse.ArgumentParser:
    from moxi_loadout.manifest import KINDS

    parser = argparse.ArgumentParser(
        prog=prog, description="Your skills, agents and settings, placed for each harness.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    def verb(name: str, help: str) -> argparse.ArgumentParser:
        p = verbs.add_parser(name, help=help, description=help[0].upper() + help[1:] + ".")
        p.add_argument("--json", action="store_true", help="print JSON")
        return p

    def scopes(p, *, many: bool) -> None:
        if many:
            p.add_argument("--user", action="store_true", help="the user section only")
            p.add_argument("--project", action="append", default=[], metavar="P",
                           help="project section P (a path or a key); may repeat")
        else:
            group = p.add_mutually_exclusive_group()
            group.add_argument("--user", action="store_true", help="the user section")
            group.add_argument("--project", metavar="P",
                               help="project section P (a path or a key)")

    p = verb("install", "place what the manifest lists")
    scopes(p, many=True)
    p.add_argument("--replace", action="append", default=[], metavar="ID",
                   help="move what is in the way aside and place over it: a path, or "
                        "<kind>:<name>@<harness>; may repeat")
    p.add_argument("--dry-run", action="store_true", help="plan and print, change nothing")
    p = verb("status", "say what has drifted")
    scopes(p, many=True)
    for name, help in (("add", "list an item in a section, then install it"),
                       ("remove", "take an item out of a section, then install")):
        p = verb(name, help)
        p.add_argument("kind", metavar="KIND", choices=KINDS)
        p.add_argument("name", metavar="NAME")
        scopes(p, many=False)
    for name, help in (("enable", "switch an item back on, then install it"),
                       ("disable", "switch an item off for a harness, then install")):
        p = verb(name, help)
        p.add_argument("ident", metavar="ID", help="<kind>:<name>@<harness>, harness * "
                                                    "for every one")
        scopes(p, many=False)
    verb("list", "every item, with its kind, scope, harnesses and source")
    for extra in EXTRA_VERBS:
        extra(verbs, verb)
    return parser


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi loadout") -> int:
    from moxi import log

    try:
        args = _parser(prog).parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else codes.USAGE
    log.setup("loadout")
    from moxi.config import ConfigError

    from moxi_loadout.ledger import LedgerError
    from moxi_loadout.manifest import ManifestError

    try:
        return VERBS[args.verb](args)
    except ManifestError as exc:
        print(f"{prog} {args.verb}: {exc}", file=sys.stderr)
        return exc.exit_code
    except (ConfigError, LedgerError) as exc:
        print(f"{prog} {args.verb}: {exc}", file=sys.stderr)
        return codes.ERROR
    except _lock_timeout() as exc:
        print(f"{prog} {args.verb}: {exc}; try again", file=sys.stderr)
        return codes.TEMPFAIL


def _lock_timeout():
    from moxi.files import LockTimeout

    return LockTimeout


# -- the verbs ------------------------------------------------------------------------------

def _scope(args, item=None):
    from moxi_loadout.manifest import load
    from moxi_loadout.planner import Scope

    projects = args.project if isinstance(args.project, list) else (
        [] if args.project is None else [args.project])
    named = []
    if projects:
        found = load()
        for p in projects:
            section = found.section(p)
            if section is None:
                from moxi_loadout.manifest import ManifestError

                raise ManifestError(found.path, [f"no section [project.{p}]"])
            named.append(section.name)
    return Scope(user=bool(args.user), projects=tuple(named), item=item)


def _emit(outcome, args) -> int:
    if args.json:
        print(json.dumps(outcome.as_json(), indent=1))
    else:
        for line in outcome.text():
            print(line)
    return outcome.code


def _install(args) -> int:
    from moxi_loadout import install

    outcome = install.install(_scope(args), replace=args.replace, dry_run=args.dry_run)
    return _emit(outcome, args)


def _status(args) -> int:
    from moxi_loadout import install

    return _emit(install.status(_scope(args)), args)


def _write_then_install(args, written: list[str], item) -> int:
    from moxi_loadout import install
    from moxi_loadout.planner import Scope

    outcome = install.install(Scope(item=item))
    outcome.lines[:0] = written
    return _emit(outcome, args)


def _add(args) -> int:
    from moxi_loadout import writes

    where = "user" if args.project is None else f"project {args.project}"
    changed = writes.add(args.kind, args.name, project=args.project)
    line = (f"listed {args.kind} {args.name} in {where}" if changed
            else f"{where} lists {args.kind} {args.name} already")
    return _write_then_install(args, [line], (args.kind, args.name))


def _remove(args) -> int:
    from moxi_loadout import writes

    where = "user" if args.project is None else f"project {args.project}"
    changed = writes.remove(args.kind, args.name, project=args.project)
    line = (f"took {args.kind} {args.name} out of {where}" if changed
            else f"{where} doesn't list {args.kind} {args.name}")
    return _write_then_install(args, [line], (args.kind, args.name))


def _toggle(args, write, done: str) -> int:
    from moxi_loadout.manifest import ManifestError, parse_id

    try:
        ident = parse_id(args.ident)
    except ValueError as exc:
        raise ManifestError(None, [str(exc)]) from None
    changed = write(ident, user=args.user, project=args.project)
    lines = [f"{done} {ident} in {s}" for s in changed] or [f"{ident}: nothing to change"]
    return _write_then_install(args, lines, (ident.kind, ident.name))


def _enable(args) -> int:
    from moxi_loadout import writes

    return _toggle(args, writes.enable, "enabled")


def _disable(args) -> int:
    from moxi_loadout import writes

    return _toggle(args, writes.disable, "disabled")


def _list(args) -> int:
    from moxi_loadout import listing

    entries = listing.entries()
    if args.json:
        print(json.dumps(entries, indent=1))
    else:
        for e in entries:
            harnesses = ",".join(e["harnesses"]) if e["harnesses"] else "every harness"
            print(f"{e['kind']} {e['name']}  {e['scope']}  {harnesses}  {e['source']}")
    return codes.OK


VERBS.update({"install": _install, "status": _status, "add": _add, "remove": _remove,
              "enable": _enable, "disable": _disable, "list": _list})


__all__ = ["VERBS", "EXTRA_VERBS", "main"]

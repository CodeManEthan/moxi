"""`moxi projects`: the list of named places, from the command line.

    moxi projects add KEY [--root DIR] [--create] [--kind KIND] [--lead NAME]
                          [--notes-root DIR]
    moxi projects set KEY [--root DIR] [--kind KIND] [--lead NAME | --no-lead]
                          [--notes-root DIR | --no-notes-root]
    moxi projects remove KEY
    moxi projects promote SLUG KEY [--kind KIND] [--root DIR] [--create]
    moxi projects list [--kind KIND]
    moxi projects show KEY
    moxi projects find [PATH]
    moxi projects kinds
    moxi projects agents add KEY NAME
    moxi projects agents remove KEY NAME

A write prints the project as it now stands (remove prints it as it was). `promote` makes
notes' inbox one-off SLUG project KEY, a topic by default, and moves its folder to the
project's notes folder; it needs notes installed. `find` prints the key of the project
PATH (default: the working directory) is in, or nothing. Every verb takes `--json`, which
prints the read API's field names instead; `find --json` prints the project or `null`.

Paths on the command line are made absolute: `~` expanded, relative ones against the
working directory; an empty `--root` or `--notes-root` is a usage error. Notes that
refuse nothing (a root that doesn't exist yet, what `--create` made, what `promote`
moved) go to stderr. A refusal prints its reason and the command that fixes it to
stderr, and exits 1 (an error), 2 (usage), 3 (refused) or 75 (the store stayed locked;
try again).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from moxi import exit as codes

from . import acts, api
from .api import DEFAULT_KIND, Kinds, Project, ProjectsError
from .rules import absolutize

_PARSERS: dict[str, argparse.ArgumentParser] = {}


def _directory(text: str) -> str:
    """A directory as typed; empty is a usage error, not the working directory."""
    if not text:
        raise argparse.ArgumentTypeError("is empty; name a directory")
    return text


def _parser(prog: str) -> argparse.ArgumentParser:
    """The parser, built once per `prog`."""
    if prog in _PARSERS:
        return _PARSERS[prog]
    parser = argparse.ArgumentParser(
        prog=prog, description="Moxi's list of named places: projects, their roots, "
                               "kinds and agents.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    def verb(name: str, help: str) -> argparse.ArgumentParser:
        p = verbs.add_parser(name, help=help, description=help[0].upper() + help[1:] + ".")
        p.add_argument("--json", action="store_true", help="print JSON")
        return p

    p = verb("add", "add a project")
    p.add_argument("key", metavar="KEY")
    p.add_argument("--root", metavar="DIR", type=_directory,
                   help="its directory (needed unless an installed module supplies it)")
    p.add_argument("--create", action="store_true",
                   help="make the root first (for most kinds, a directory and git init)")
    p.add_argument("--kind", default=DEFAULT_KIND, metavar="KIND",
                   help=f"its kind (default: {DEFAULT_KIND}; `kinds` lists them)")
    p.add_argument("--lead", metavar="NAME", help="its lead agent")
    p.add_argument("--notes-root", metavar="DIR", type=_directory,
                   help="its notes directory (only while notes is installed)")

    p = verb("set", "change a project's fields")
    p.add_argument("key", metavar="KEY")
    p.add_argument("--root", metavar="DIR", type=_directory, help="a new directory")
    p.add_argument("--kind", metavar="KIND", help="a new kind")
    lead = p.add_mutually_exclusive_group()
    lead.add_argument("--lead", metavar="NAME", help="a new lead agent")
    lead.add_argument("--no-lead", action="store_true", help="clear the lead")
    notes = p.add_mutually_exclusive_group()
    notes.add_argument("--notes-root", metavar="DIR", type=_directory,
                       help="a new notes directory (only while notes is installed)")
    notes.add_argument("--no-notes-root", action="store_true", help="clear the notes root")

    p = verb("remove", "remove a project")
    p.add_argument("key", metavar="KEY")

    p = verb("promote", "make an inbox one-off of notes' a project, moving its folder to "
                        "the project's notes folder")
    p.add_argument("slug", metavar="SLUG", help="the one-off (`moxi notes inbox list`)")
    p.add_argument("key", metavar="KEY")
    p.add_argument("--kind", default=acts.TOPIC, metavar="KIND",
                   help=f"its kind (default: {acts.TOPIC}, whose folder is the one-off)")
    p.add_argument("--root", metavar="DIR", type=_directory,
                   help="its directory (needed for any kind but a topic)")
    p.add_argument("--create", action="store_true",
                   help="make the root first (not for a topic)")

    p = verb("list", "list every project, by key")
    p.add_argument("--kind", metavar="KIND", help="only projects of this kind")

    p = verb("show", "show one project with its agents")
    p.add_argument("key", metavar="KEY")

    p = verb("find", "print the project a directory is in")
    p.add_argument("path", nargs="?", metavar="PATH",
                   help="the directory (default: the working directory)")

    verb("kinds", "list the kinds, built in and contributed, and any contribution that "
                  "won't load")

    agents = verbs.add_parser("agents", help="add or remove a project's associated agents",
                              description="Add or remove a project's associated agents. "
                                          "The lead is set with `set --lead`.")
    pair = agents.add_subparsers(dest="action", metavar="ACTION")
    pair.required = True
    for action, help in (("add", "add an associated agent"),
                         ("remove", "remove an associated agent")):
        p = pair.add_parser(action, help=help, description=help[0].upper() + help[1:] + ".")
        p.add_argument("key", metavar="KEY")
        p.add_argument("name", metavar="NAME")
        p.add_argument("--json", action="store_true", help="print JSON")

    _PARSERS[prog] = parser
    return parser


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi projects") -> int:
    try:
        args = _parser(prog).parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else codes.USAGE
    out = sys.stdout
    try:
        return _VERBS[args.verb](args, out)
    except ProjectsError as exc:
        name = f"{prog} {args.verb}" + (f" {args.action}" if args.verb == "agents" else "")
        print(f"{name}: {exc.reason}", file=sys.stderr)
        if exc.fix:
            print(f"  fix: {exc.fix}", file=sys.stderr)
        return exc.exit_code


# --- the verbs ------------------------------------------------------------------------------

def _path(text: str | None) -> str | None:
    return None if text is None else absolutize(text)


def _add(args, out) -> int:
    added = acts.add(args.key, root=_path(args.root), kind=args.kind, lead=args.lead,
                     notes_root=_path(args.notes_root), create=args.create)
    for note in added.notes:
        print(f"note: {note}", file=sys.stderr)
    _print(added.project, args.json, out)
    return codes.OK


def _set(args, out) -> int:
    changes: dict[str, str | None] = {}
    if args.root is not None:
        changes["root"] = _path(args.root)
    if args.kind is not None:
        changes["kind"] = args.kind
    if args.lead is not None:
        changes["lead"] = args.lead
    elif args.no_lead:
        changes["lead"] = None
    if args.notes_root is not None:
        changes["notes_root"] = _path(args.notes_root)
    elif args.no_notes_root:
        changes["notes_root"] = None
    project = acts.set_fields(args.key, **changes)
    if "root" in changes:
        note = acts.root_note(project.root)
        if note:
            print(f"note: {note}", file=sys.stderr)
    _print(project, args.json, out)
    return codes.OK


def _remove(args, out) -> int:
    project = acts.remove(args.key)
    if args.json:
        print(json.dumps(project.as_dict(), indent=2), file=out)
    else:
        print(f"removed project {project.key} (root {project.root})", file=out)
    return codes.OK


def _promote(args, out) -> int:
    added = acts.promote(args.slug, args.key, kind=args.kind, root=_path(args.root),
                         create=args.create)
    for note in added.notes:
        print(f"note: {note}", file=sys.stderr)
    _print(added.project, args.json, out)
    return codes.OK


def _list(args, out) -> int:
    projects = api.list_projects(args.kind)
    if args.json:
        print(json.dumps([p.as_dict() for p in projects], indent=2), file=out)
        return codes.OK
    if not projects:
        if args.kind is None:
            print("no projects yet; `moxi projects add KEY --root DIR` adds one", file=out)
        else:
            print(f"no projects of kind {args.kind}", file=out)
        return codes.OK
    width = max(len(p.key) for p in projects)
    kinds = max(len(p.kind) for p in projects)
    for p in projects:
        print(f"{p.key:<{width}}  {p.kind:<{kinds}}  {p.root}", file=out)
    return codes.OK


def _show(args, out) -> int:
    _print(api.get(args.key), args.json, out)
    return codes.OK


def _find(args, out) -> int:
    path = _path(args.path) if args.path is not None else os.getcwd()
    project = api.for_path(path)
    if args.json:
        print(json.dumps(None if project is None else project.as_dict(), indent=2),
              file=out)
    elif project is not None:
        print(project.key, file=out)
    return codes.OK


def _kinds(args, out) -> int:
    found = api.kinds()
    if args.json:
        print(json.dumps(_kinds_dict(found), indent=2), file=out)
        return codes.OK
    width = max((len(k.name) for k in found.found), default=0)
    module = max((len(k.module) for k in found.found), default=0)
    for k in found.found:
        print(f"{k.name:<{width}}  {k.module:<{module}}  {k.mint:<6}  {k.doc}", file=out)
    if found.broken:
        print("\ncan't be used:", file=out)
        for name, reason in found.broken:
            print(f"  {name}: {reason}", file=out)
    return codes.OK


def _agents(args, out) -> int:
    act = acts.add_agent if args.action == "add" else acts.remove_agent
    _print(act(args.key, args.name), args.json, out)
    return codes.OK


_VERBS = {"add": _add, "set": _set, "remove": _remove, "promote": _promote, "list": _list,
          "show": _show, "find": _find, "kinds": _kinds, "agents": _agents}


# --- printing -------------------------------------------------------------------------------

def _kinds_dict(found: Kinds) -> dict:
    return {"found": [{"name": k.name, "doc": k.doc, "module": k.module, "mint": k.mint}
                      for k in found.found],
            "broken": [{"module": module, "reason": reason}
                       for module, reason in found.broken]}


def _print(project: Project, as_json: bool, out) -> None:
    if as_json:
        print(json.dumps(project.as_dict(), indent=2), file=out)
        return
    lines = [("root", project.root), ("kind", project.kind),
             ("lead", project.lead or "none"),
             ("associated", " ".join(project.associated) or "none")]
    if project.notes_root is not None:
        lines.append(("notes root", project.notes_root))
    lines.append(("created", project.created))
    print(project.key, file=out)
    for label, value in lines:
        print(f"  {label:<11} {value}", file=out)


__all__ = ["main"]

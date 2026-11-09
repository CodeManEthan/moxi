"""`moxi repo`: the git hooks of the repos you name, and the committer's specs.

    moxi repo git-hooks [list] [PATH] [--projects] [--json]
    moxi repo git-hooks available [--json]
    moxi repo git-hooks add PATH [PIECE...] [--mode auto|config|shim] [--set KEY=VALUE]...
    moxi repo git-hooks remove PATH [PIECE...]
    moxi repo git-hooks enable|disable PATH PIECE
    moxi repo git-hooks refresh [PATH]
    moxi repo commit PATH [--spec NAME] [--wait-s SECONDS] [--json]
    moxi repo specs [PATH] [--json]
    moxi repo specs remove PATH NAME

PATH is a path inside the repo. With the projects module installed it may also be a
project key, and listings show each repo's key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from moxi_repo import api, projects
from moxi_repo.run import quote_arg

GIT_HOOK_VERBS = ("list", "available", "add", "remove", "enable", "disable", "refresh")


def _path_note() -> str:
    """What PATH may be, said by whether projects is installed and offers what repo
    reads."""
    if projects.api() is not None:
        return ("PATH is a path inside the repo, or a project key (matched ignoring case); "
                "listings show each repo's project key.")
    return (f"PATH is a path inside the repo. With projects installed ({projects.INSTALL}) "
            "it may also be a project key, and listings show each repo's key.")


def _parser(prog: str) -> argparse.ArgumentParser:
    note = _path_note()
    parser = argparse.ArgumentParser(
        prog=prog, description="Run other modules' git hook pieces in the repos you name.",
        epilog=note)
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True
    hooks = verbs.add_parser(
        "git-hooks", help="the git hook pieces registered in your repos",
        description="The git hook pieces registered in your repos. With no verb, list them.",
        epilog=note)
    sub = hooks.add_subparsers(dest="action", metavar="ACTION")
    sub.required = True

    ls = sub.add_parser("list", help="each indexed repo's git hooks, or one repo's",
                        description="Each indexed repo, or the one at PATH: git version and "
                                    "mode; per event, its pieces in order, the existing git "
                                    "hook, and problems.", epilog=note)
    ls.add_argument("path", nargs="?", metavar="PATH")
    ls.add_argument("--projects", action="store_true",
                    help="add every project's repo, including those repo hasn't touched "
                         f"(needs projects: {projects.INSTALL})")
    ls.add_argument("--json", action="store_true", help="print one JSON array")

    av = sub.add_parser("available", help="the pieces installed modules declare",
                        description="The pieces installed modules declare: id, event, fail "
                                    "policy, the settings each takes, one line.")
    av.add_argument("--json", action="store_true", help="print one JSON object")

    add = sub.add_parser(
        "add", help="register pieces in a repo",
        description="Register pieces in the repo at PATH. With no piece, index the repo and "
                    "write nothing else. Every --set for one key in one call together give "
                    "that key's whole list of values; --set KEY= alone removes the key; keys "
                    "not named are kept.")
    add.add_argument("path", metavar="PATH")
    add.add_argument("pieces", nargs="*", metavar="PIECE", help="a piece id, module/name")
    add.add_argument("--mode", choices=("auto", "config", "shim"),
                     help="pin how git reaches the pieces in this repo (auto unpins)")
    add.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                     dest="sets", help="set a named piece's setting in this repo")

    rm = sub.add_parser("remove", help="remove pieces, or everything repo wrote, from a repo",
                        description="Remove the named pieces from the repo at PATH; with "
                                    "none, everything repo wrote there. On a path that no "
                                    "longer exists, drop it from the index.")
    rm.add_argument("path", metavar="PATH")
    rm.add_argument("pieces", nargs="*", metavar="PIECE")

    for name, what in (("enable", "turn a piece on in a repo"),
                       ("disable", "turn a piece off in a repo, leaving it registered")):
        flip = sub.add_parser(name, help=what, description=what[0].upper() + what[1:] + ".")
        flip.add_argument("path", metavar="PATH")
        flip.add_argument("piece", metavar="PIECE")

    rf = sub.add_parser("refresh", help="rewrite every git hook entry, shim and launcher",
                        description="Re-read declarations, git's version and the "
                                    "interpreter, and rewrite every entry, shim and launcher "
                                    "of one repo or of every indexed repo, keeping each "
                                    "piece's enabled key, settings and record flag.")
    rf.add_argument("path", nargs="?", metavar="PATH")

    cm = verbs.add_parser(
        "commit", help="run a repo's saved committer specs",
        description="Run one pass of the spec saved as NAME in the repo at PATH, or of every "
                    "spec saved there, and print each pass's line.", epilog=note)
    cm.add_argument("path", metavar="PATH")
    cm.add_argument("--spec", metavar="NAME", help="the saved spec to run")
    cm.add_argument("--wait-s", type=float, default=0, metavar="SECONDS",
                    help="wait this long for another pass's lock (default: try once)")
    cm.add_argument("--json", action="store_true", help="print one JSON array")

    sp = verbs.add_parser(
        "specs", help="the committer's specs, and forgetting one",
        description="List each repo's saved specs, and the specs that ran without being "
                    "saved, with their last result. 'specs remove PATH NAME' forgets one: its "
                    "saved section, its state and its doctor records.", epilog=note)
    sp.add_argument("args", nargs="*", metavar="PATH | remove PATH NAME")
    sp.add_argument("--json", action="store_true", help="print one JSON array")
    for name, verb in sub.choices.items():
        if name != "available":
            verb.epilog = note
    return parser


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi repo") -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["git-hooks"] and (len(argv) == 1 or (
            argv[1] not in GIT_HOOK_VERBS and not argv[1].startswith("-"))):
        argv.insert(1, "list")
    if argv[:2] == ["git-hooks", "--json"]:
        argv.insert(1, "list")
    parser = _parser(prog)
    args = parser.parse_args(argv)
    if args.verb == "specs":
        if args.args[:1] == ["remove"] and len(args.args) != 3:
            parser.error("specs remove takes PATH NAME")
        if args.args[:1] != ["remove"] and len(args.args) > 1:
            parser.error("specs takes one PATH, or: remove PATH NAME")
    action = args.action if args.verb == "git-hooks" else args.verb
    try:
        _resolve_paths(args, action)
        return _ACTIONS[action](args)
    except projects.Unavailable as exc:
        print(f"repo: {exc.text}", file=sys.stderr)
        return exc.code
    except api.Failure as exc:
        print(f"repo: {exc.text}", file=sys.stderr)
        for line in exc.lines:
            print(f"  {line}", file=sys.stderr)
        return exc.code


def _resolve_paths(args, action: str) -> None:
    """Turn a project key given for PATH into its root. For `remove`, a path that no
    longer exists but names an indexed repo stays a path, so `remove` can drop it: the
    repo's root, its git dir, or a work tree git still lists for that git dir."""
    def one(arg):
        if arg is None or (action == "remove" and not os.path.lexists(arg)
                           and api.indexed_at(arg)):
            return arg
        return projects.resolve(arg)

    if action == "specs":
        if args.args[:1] == ["remove"]:
            args.args[1] = one(args.args[1])
        elif args.args:
            args.args[0] = one(args.args[0])
    elif hasattr(args, "path"):
        args.path = one(args.path)


def _with_keys(repos: list[dict], *, add_projects: bool = False) -> list[dict]:
    """Each listed repo with its project key (`project`, None when it is in no project or
    projects is off); with `add_projects`, every project's repo not listed yet, marked not
    indexed. A projects error leaves the key empty and prints one line."""
    from moxi_repo import gitconf

    found = projects.api()
    for repo in repos:
        repo["project"] = None
    if found is None:
        if add_projects:
            print(f"repo: projects isn't installed, so --projects adds nothing; run: "
                  f"{projects.INSTALL}", file=sys.stderr)
        return repos
    said = set()

    def say(line):
        if line and line not in said:
            said.add(line)
            print(f"repo: {line}", file=sys.stderr)

    for repo in repos:
        repo["project"], why = projects.key_for(found, repo["root"])
        say(why)
    if add_projects:
        listed, why = projects.project_roots(found)
        say(why)
        seen = {gitconf.key_of(r["common_dir"]) for r in repos if r.get("common_dir")}
        for key, root in listed:
            try:
                where = gitconf.resolve(root)
            except gitconf.NotARepo:
                continue
            if where.key in seen:
                continue
            seen.add(where.key)
            for repo in api.status(where.root):
                repo["project"] = key
                repos.append(repo)
    return repos


def print_report(report: api.Report) -> int:
    """Print a verb's report: its lines, the chain lines needed, then its problems on
    stderr. Returns 1 when there were problems, else 0."""
    for line in report.lines:
        print(line)
    for root, event, hook, line, present in report.chain:
        if present:
            print(f"{root}: {event}: the chain line is in {hook}")
        else:
            print(f"{root}: {event}: chain line needed. Add this line to {hook}, before any "
                  f"line that reads stdin:\n  {line}")
    for problem in report.problems:
        print(f"repo: {problem}", file=sys.stderr)
    return 0 if report.ok else 1


def _list(args) -> int:
    repos = _with_keys(api.status(args.path), add_projects=args.projects)
    if args.json:
        print(json.dumps(repos, indent=2, sort_keys=True))
        return 0
    if not repos:
        print("no repo has git hook pieces yet; add one with: moxi repo git-hooks add PATH "
              "PIECE")
    for repo in repos:
        mode = repo.get("mode") or "none yet"
        pin = repo.get("mode_set")
        print(f"{repo['root']}"
              + (f"  project {repo['project']}" if repo.get("project") else "")
              + ("" if repo.get("indexed", True) else "  (not indexed)")
              + f"  git {repo.get('git_version', 'unknown')}, {mode} mode"
              + (f" (pinned {pin})" if pin in ("config", "shim") else ""))
        by_event: dict[str, list] = {}
        for piece in repo["pieces"]:
            by_event.setdefault(piece["event"], []).append(piece)
        for event, listed in sorted(by_event.items()):
            info = repo["events"].get(event, {})
            via = info.get("via") or "nothing"
            print(f"  {event}  via {via}")
            for p in listed:
                settings = "; ".join(f"{k}={','.join(v)}" for k, v in sorted(
                    p["settings"].items()))
                print(f"    {p['order']:>2} {p['id']}  {p['fail']}  "
                      f"{'enabled' if p['enabled'] else 'disabled'}"
                      + (f"  settings: {settings}" if settings else "")
                      + ("  (doctor record open)" if p["recorded"] else ""))
            if info.get("existing"):
                print(f"    existing git hook: {info['existing']}")
            if info.get("chain_line") == "present":
                print("    chain line present")
            elif info.get("chain_line") == "needed":
                hook = info.get("hook") or f"{repo.get('hooks_dir')}/{event}"
                print(f"    chain line needed in {hook}:\n"
                      f"      {info.get('line')}")
        for problem in repo["problems"]:
            print(f"  problem: {problem}")
    return 0


def _available(args) -> int:
    found = api.available()
    if args.json:
        print(json.dumps(found, indent=2, sort_keys=True))
        return 0
    if not found["pieces"] and not found["refused"]:
        print("no installed module declares a git hook piece")
    for p in found["pieces"]:
        settings = ", ".join(f"{k} ({v})" for k, v in sorted(p["settings"].items()))
        print(f"{p['id']}  {p['event']}, {p['fail']}, order {p['order']}"
              + (f", settings: {settings}" if settings else "")
              + (f"\n  {p['doc']}" if p["doc"] else ""))
    for pid, why in found["refused"].items():
        print(f"{pid}  refused: {why}")
    return 0


def _add(args) -> int:
    return print_report(api.add(args.path, args.pieces, mode=args.mode, sets=args.sets))


def _remove(args) -> int:
    return print_report(api.remove(args.path, args.pieces))


def _enable(args) -> int:
    return print_report(api.enable(args.path, args.piece))


def _disable(args) -> int:
    return print_report(api.disable(args.path, args.piece))


def _refresh(args) -> int:
    return print_report(api.refresh(args.path))


def _commit(args) -> int:
    from moxi_repo import committer

    try:
        where = committer._where(args.path, exact=False)
    except committer.SpecError as exc:
        raise api.Usage(str(exc)) from None
    saved = committer.saved_names(where.cgd)
    if args.spec and args.spec not in saved:
        raise api.Usage(f"no spec named {args.spec} is saved in {where.top}; see: "
                        f"moxi repo specs {quote_arg(where.top)}")
    names = [args.spec] if args.spec else saved
    if not names:
        if args.json:
            print("[]")
        else:
            print(f"no spec is saved in {where.top}")
        return 0
    results = [committer.commit_saved(where.top, name, wait_s=args.wait_s) for name in names]
    codes = []
    for result in results:
        code = result.exit_code
        if result.status == "busy" and args.wait_s:
            code = 75
        codes.append(code)
        if not args.json:
            out = sys.stdout if code == 0 else sys.stderr
            print(result.line(), file=out)
            for line in result.hook_lines:
                print(f"  {line}", file=out)
    if args.json:
        print(json.dumps([r.as_dict() for r in results], indent=2, sort_keys=True))
    for worst in (1, 3, 75):
        if worst in codes:
            return worst
    return 0


def _specs(args) -> int:
    if args.args[:1] == ["remove"]:
        return print_report(api.forget_spec(args.args[1], args.args[2]))
    repos = _with_keys(api.specs(args.args[0] if args.args else None))
    if args.json:
        print(json.dumps(repos, indent=2, sort_keys=True))
        return 0
    if not any(repo["specs"] for repo in repos):
        print("no committer spec has been saved or run yet")
    for repo in repos:
        print(repo["root"] + (f"  project {repo['project']}" if repo.get("project") else ""))
        for item in repo["specs"]:
            last = item.get("last") or {}
            print(f"  {item['name']}  {'saved' if item['saved'] else 'not saved'}"
                  + (f"  last: {last.get('status')}" if last else "  never ran"))
            if item.get("problem"):
                print(f"    problem: {item['problem']}")
            spec = item.get("spec")
            if spec:
                print(f"    paths: {', '.join(spec['paths'])}; every "
                      f"{spec['min_interval_s']} s; push: {spec['push'] or 'none'}")
            if last.get("line"):
                print(f"    {last['line']}")
            if item.get("refused_since"):
                print(f"    refused since {item['refused_since']}, "
                      f"{item.get('refused_passes')} passes")
            for branch, info in sorted(item.get("push", {}).items()):
                print(f"    push of {branch}: {info.get('error')}")
            for path, info in sorted(item.get("left", {}).items()):
                print(f"    left out: {path} ({info.get('why')})")
            for key in item.get("records", []):
                print(f"    doctor record open: {key}")
    return 0


_ACTIONS = {"list": _list, "available": _available, "add": _add, "remove": _remove,
            "enable": _enable, "disable": _disable, "refresh": _refresh,
            "commit": _commit, "specs": _specs}

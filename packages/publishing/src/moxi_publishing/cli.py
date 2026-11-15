"""`moxi publishing`: guard a public repo, from the command line.

    moxi publishing enable PATH [--github OWNER/REPO] [--set KEY=VALUE]...
    moxi publishing disable PATH
    moxi publishing list [--json]

PATH is a path inside the repo; with the projects module installed it may also be a
project key. Exit codes: 0 done, 1 failed, 2 usage, 3 refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from moxi import exit as codes

_SETS_HELP = (
    "set a piece's setting in this repo: drop-trailer and pass-trailer (the message "
    "piece), main, protected, word-required and free-ref (the push piece). Every --set for "
    "one key together give that key's whole list, replacing its seed or what the repo "
    "had; KEY= alone removes the key; keys you don't name are kept. A key on pass-trailer "
    "is never dropped, even when drop-trailer holds it too.")


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog, description="Keep private text out of a public git repo.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True
    p = verbs.add_parser(
        "enable", help="mark a repo public and register the three guards",
        description="Mark the repo at PATH public (moxi-publishing.public in its git "
                    "config) and register the message, content and push pieces. Where a "
                    "piece is first registered its settings start from publishing's "
                    "defaults.* config keys; a repo enabled again keeps the settings it "
                    "has.")
    p.add_argument("path", metavar="PATH")
    p.add_argument("--github", metavar="OWNER/REPO",
                   help="the GitHub repo (default: read from origin when it is GitHub)")
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE",
                   help=_SETS_HELP)
    p = verbs.add_parser("disable", help="remove the three guards, then the public flag",
                         description="Remove the three pieces from the repo at PATH, then "
                                     "its public flag.")
    p.add_argument("path", metavar="PATH")
    p = verbs.add_parser("list", help="every repo marked public, with its pieces",
                         description="Every repo repo has indexed that is marked public, "
                                     "with each piece's state and settings.")
    p.add_argument("--json", action="store_true", help="print JSON")
    return parser


def _print_listing(repos: list[dict]) -> None:
    if not repos:
        print("no repo is marked public")
        return
    for repo in repos:
        print(repo["root"] + (f" (github {repo['github']})" if repo["github"] else ""))
        for piece, state in repo["pieces"].items():
            if state is None:
                print(f"  {piece}: missing")
                continue
            settings = "; ".join(f"{k}={','.join(v)}"
                                 for k, v in sorted(state["settings"].items()))
            print(f"  {piece}: {'enabled' if state['enabled'] else 'disabled'}"
                  + (f" ({settings})" if settings else ""))


def main(argv: Sequence[str] | None = None, prog: str = "moxi publishing") -> int:
    from moxi_publishing import flag

    args = _parser(prog).parse_args(argv)
    try:
        if args.verb == "enable":
            lines = flag.enable(args.path, github=args.github, sets=args.sets)
        elif args.verb == "disable":
            lines = flag.disable(args.path)
        else:
            repos = flag.listing()
            if args.json:
                print(json.dumps(repos, indent=2))
            else:
                _print_listing(repos)
            return codes.OK
    except flag.Failure as exc:
        print(f"{prog}: {exc.text}", file=sys.stderr)
        return exc.code
    for line in lines:
        print(line)
    return codes.OK


if __name__ == "__main__":
    sys.exit(main())

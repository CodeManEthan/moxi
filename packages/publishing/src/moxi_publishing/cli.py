"""`moxi publishing`: guard a public repo, from the command line.

    moxi publishing enable PATH [--github OWNER/REPO] [--set KEY=VALUE]...
    moxi publishing disable PATH
    moxi publishing list [--json]
    moxi publishing scan [PATH] [--rev REV] [--since REV] [--ci]
    moxi publishing bump PACKAGE... [--part patch|minor|major] [--path PATH]
    moxi publishing tag PACKAGE [--at REV] [--remote NAME] [--path PATH]
    moxi publishing ci gate TAG [--github OWNER/REPO] [--artifact DIR]
    moxi publishing ci notes TAG
    moxi publishing ci siblings TAG --artifact DIR [--resolution highest|lowest-direct]
    moxi publishing workflows write [PATH]

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
    p = verbs.add_parser(
        "scan", help="scan a commit's whole tree for text a public repo can't hold",
        description="Read every file of the tree at REV as git stores it, whatever "
                    "attributes say, and every path name, through secrets' credentials "
                    "and private names sets and the provenance rules, with the "
                    "allowlists of that tree. Prints one line per hit; exits 3 when "
                    "there is one.")
    p.add_argument("path", metavar="PATH", nargs="?", default=".",
                   help="a path inside the repo, or a project key (default: here)")
    p.add_argument("--rev", default="HEAD", help="the commit or tree (default: HEAD)")
    p.add_argument("--since", metavar="REV",
                   help="also read each commit of REV..--rev whole: its message and every "
                        "header (author, committer, a merged signed tag). When --rev names "
                        "an annotated tag, its message and tagger are read either way")
    p.add_argument("--ci", action="store_true",
                   help="leave out the private names list and the private allowlist, "
                        "which only your machine has")
    p = verbs.add_parser(
        "bump", help="set the next version of packages, for one commit",
        description="For each PACKAGE: set its project.version to the next one, date its "
                    "CHANGELOG's ## Unreleased section and open a new one above it, move "
                    "its README's links into the repository to the new release tag; then "
                    "run uv lock. The edits are left unstaged. Refuses, changing nothing, "
                    "a CHANGELOG with no ## Unreleased heading, or a requirement another "
                    "package places on one that the new version would not meet.")
    p.add_argument("packages", metavar="PACKAGE", nargs="+")
    p.add_argument("--part", choices=("patch", "minor", "major"), default="patch",
                   help="the part of the version to raise (default: patch)")
    p.add_argument("--path", default=".", metavar="PATH",
                   help="a path inside the work tree, or a project key (default: here)")
    p = verbs.add_parser(
        "tag", help="run the tag gate and make a package's release tag",
        description="Run the tag gate for PACKAGE at REV (default: the tip of main as "
                    "the remote's tracking ref has it) and, when every check passes, make "
                    "its annotated release tag, <distribution>-v<version>, whose message "
                    "is the CHANGELOG section. Pushes nothing. Exits 3 when the gate "
                    "refuses.")
    p.add_argument("package", metavar="PACKAGE")
    p.add_argument("--at", metavar="REV", help="the commit to tag (default: main's tip)")
    p.add_argument("--remote", default="origin", metavar="NAME",
                   help="the remote whose tracking ref holds main (default: origin)")
    p.add_argument("--path", default=".", metavar="PATH",
                   help="a path inside the repo, or a project key (default: here)")
    p = verbs.add_parser("ci", help="the release workflow's steps",
                         description="The steps the release workflow runs on a tag.")
    steps = p.add_subparsers(dest="step", metavar="STEP")
    steps.required = True
    s = steps.add_parser(
        "gate", help="run the tag gate on a pushed tag",
        description="Run the tag gate on TAG, with main read from origin/<the "
                    "repository's default branch>. Given --artifact, the index may hold "
                    "files of the version only when each is one of the build's.")
    s.add_argument("tag", metavar="TAG")
    s.add_argument("--github", metavar="OWNER/REPO",
                   help="the GitHub repo (default: moxi-publishing.github)")
    s.add_argument("--artifact", metavar="DIR", help="the directory of the build's files")
    s = steps.add_parser("notes", help="print the CHANGELOG section of a tag's version",
                         description="Print the CHANGELOG section of TAG's version, read "
                                     "at the tagged commit.")
    s.add_argument("tag", metavar="TAG")
    s = steps.add_parser(
        "siblings", help="test the built package against its published siblings",
        description="Run the package's whole test suite from a fresh venv holding only "
                    "its wheel from DIR, the extras whose sibling requirements a "
                    "published version admits (the others are reported), the published "
                    "siblings, and its dev group's test tools at uv.lock's versions; "
                    "first at the newest published siblings it admits, then at the "
                    "lowest set that resolves, reporting a floor the resolver raised. "
                    "The run fails when the package imports from anywhere but the venv's "
                    "site-packages. Exits 3 when a required sibling has no published "
                    "version it admits.")
    s.add_argument("tag", metavar="TAG")
    s.add_argument("--artifact", metavar="DIR", required=True,
                   help="the directory of the build's files")
    s.add_argument("--resolution", choices=("highest", "lowest-direct"),
                   help="run once, at this resolution only (default: both)")
    p = verbs.add_parser("workflows", help="the workflow files of a public repo",
                         description="The release and public scan workflow files.")
    steps = p.add_subparsers(dest="step", metavar="STEP")
    steps.required = True
    s = steps.add_parser(
        "write", help="write the release and public scan workflow files",
        description="Write .github/workflows/publishing.yml and release.yml into the "
                    "repo at PATH from publishing's templates, replacing what is there.")
    s.add_argument("path", metavar="PATH", nargs="?", default=".",
                   help="a path inside the repo, or a project key (default: here)")
    return parser


def _release(args, prog: str) -> int:
    from moxi_publishing import flag, release

    try:
        if args.verb == "bump":
            result = release.bump(args.path, args.packages, part=args.part)
        elif args.verb == "tag":
            result = release.tag(args.path, args.package, at=args.at, remote=args.remote)
        elif args.verb == "workflows":
            result = release.workflows_write(args.path)
        elif args.step == "gate":
            result = release.ci_gate(".", args.tag, github=args.github,
                                     artifact=args.artifact)
        elif args.step == "siblings":
            result = release.ci_siblings(".", args.tag, artifact=args.artifact,
                                         resolution=args.resolution)
        else:
            result = release.ci_notes(".", args.tag)
    except flag.Failure as exc:
        print(f"{prog}: {exc.text}", file=sys.stderr)
        return exc.code
    for line in result.lines:
        print(line)
    return result.code


def _scan(args, prog: str) -> int:
    from moxi_secrets import AllowlistError, SetUnavailable

    from moxi_publishing import content, flag, tags

    try:
        root = flag.work_tree(args.path)
        refused = content.scan_tree(root, args.rev, ci=args.ci)
        from moxi_publishing import push

        refused += push.judge_history(tags.repo_git(root), args.rev, args.since,
                                      sets=content.CI_SETS if args.ci else content.SETS)
    except flag.Failure as exc:
        print(f"{prog}: {exc.text}", file=sys.stderr)
        return exc.code
    except (content.ScanError, SetUnavailable, AllowlistError, OSError) as exc:
        print(f"{prog}: the tree can't be scanned: {exc}", file=sys.stderr)
        return codes.ERROR
    for line in refused:
        print(line)
    if refused:
        print(f"{prog}: {len(refused)} hit(s) a public repo can't hold", file=sys.stderr)
        return codes.REFUSED
    return codes.OK


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
    if args.verb == "scan":
        return _scan(args, prog)
    if args.verb in ("bump", "tag", "ci", "workflows"):
        return _release(args, prog)
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

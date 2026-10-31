"""`moxi-dev stamp`: each web page's build stamp matches the sources it was built from.

A module with a page keeps its sources in `packages/<name>/web/` and commits what the
Node build (`npm run build`, `tools/web/build.mjs`) makes from them in
`packages/<name>/src/moxi_<name>/web/`, beside `build-stamp.txt`: a hash of the build's
inputs. This check recomputes that hash in Python, with no Node, and fails a package
whose committed stamp differs, so sources never land without the files built from them.

The inputs of a package are its files under `web/`, every file under `tools/web/`, and
the root `package-lock.json`: each file git tracks there, or would track (untracked and
not ignored). The stamp is SHA-256, in hex, over `esbuild@<version>` and a NUL, with the
version the lock pins, then for each input in order of its path's UTF-8 bytes: the path
relative to the repository (POSIX), a NUL, the file's bytes and a NUL. `build.mjs`
computes the same value; a change to one is a change to the other.

Every input must be `text eol=lf` or `binary` by `.gitattributes`, so its working-tree
bytes are its committed bytes on every system and both sides hash the same bytes. An
input with neither, or a link, fails the check.

Two ways to run it, from the repository:

- `moxi-dev stamp` reads the working tree and checks every package with a page.
- `moxi-dev stamp --staged` reads the index, as a pre-commit hook does, and checks only
  the packages the staged commit touches: one whose inputs or built files differ from
  HEAD's. A commit that touches no page's files passes whatever state the pages are in.

It exits 0 when every checked stamp matches, 1 when one does not, and 2 when the check
could not run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

LOCK = "package-lock.json"
STAMP_NAME = "build-stamp.txt"
FIX = "run `npm run build` and commit what it writes"

_WEB_INPUT = re.compile(r"packages/[^/]+/web/")
_BUILD_JSON = re.compile(r"packages/([^/]+)/web/build\.json")


class StampError(Exception):
    """The check can't run; the message says why."""


def is_input(rel: str) -> bool:
    """True for a path the stamp reads: a package's `web/` files, `tools/web/`, the lock."""
    return rel == LOCK or rel.startswith("tools/web/") or bool(_WEB_INPUT.match(rel))


def outdir(name: str) -> str:
    """The package's output directory, relative to the repository."""
    return f"packages/{name}/src/moxi_{name.replace('-', '_')}/web"


def stamp_path(name: str) -> str:
    return f"{outdir(name)}/{STAMP_NAME}"


def web_packages(paths: Iterable[str]) -> list[str]:
    """The packages with a page: those whose `web/build.json` is among the paths."""
    names = (m.group(1) for m in map(_BUILD_JSON.fullmatch, paths) if m)
    return sorted(names, key=lambda name: name.encode())


def package_inputs(paths: Iterable[str], name: str) -> list[str]:
    """A package's inputs among the paths, in the stamp's order."""
    own = f"packages/{name}/web/"
    return sorted((rel for rel in paths
                   if rel.startswith(own) or rel.startswith("tools/web/") or rel == LOCK),
                  key=lambda rel: rel.encode())


def stamp(version: str, files: Iterable[tuple[str, bytes]]) -> str:
    """The stamp over the esbuild version and the inputs, given in order."""
    digest = hashlib.sha256(f"esbuild@{version}\0".encode())
    for rel, data in files:
        digest.update(rel.encode() + b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def locked_esbuild(lock: bytes) -> str:
    """The esbuild version a `package-lock.json` pins."""
    try:
        version = json.loads(lock)["packages"]["node_modules/esbuild"]["version"]
    except (ValueError, KeyError, TypeError) as exc:
        raise StampError(f"{LOCK} pins no esbuild ({type(exc).__name__}: {exc})") from exc
    if not isinstance(version, str):
        raise StampError(f"{LOCK} pins no esbuild")
    return version


def _git(root: Path, *args: str, input: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(["git", *args], cwd=root, input=input, capture_output=True)
    except OSError as exc:
        raise StampError(f"could not run git ({exc})") from exc
    if proc.returncode != 0:
        raise StampError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def _split(output: bytes) -> list[str]:
    return [name for name in output.decode().split("\0") if name]


def _attributes(root: Path, paths: list[str], *, cached: bool) -> list[str]:
    """The paths whose bytes a checkout may rewrite: neither `text eol=lf` nor `binary`."""
    if not paths:
        return []
    args = ["check-attr", "-z", "--stdin", *(["--cached"] if cached else []), "text", "eol"]
    out = _split(_git(root, *args, input="\0".join(paths).encode() + b"\0"))
    attrs: dict[str, dict[str, str]] = {}
    for rel, attr, value in zip(out[0::3], out[1::3], out[2::3]):
        attrs.setdefault(rel, {})[attr] = value
    return [rel for rel in paths
            if not ((attrs.get(rel, {}).get("text") == "set"
                     and attrs.get(rel, {}).get("eol") == "lf")
                    or attrs.get(rel, {}).get("text") == "unset")]


class _WorkTree:
    """The files as the working tree holds them."""

    def __init__(self, root: Path):
        self.root = root
        listed = _split(_git(root, "ls-files", "-z", "--cached", "--others",
                             "--exclude-standard"))
        self.paths = []
        for rel in sorted(set(listed)):
            if not is_input(rel):
                continue
            full = root / rel
            if full.is_symlink():
                raise StampError(f"{rel} is a link; a build input must be a file")
            if full.is_file():
                self.paths.append(rel)

    def read(self, rel: str) -> bytes | None:
        try:
            return (self.root / rel).read_bytes()
        except FileNotFoundError:
            return None

    def unsafe(self) -> list[str]:
        return _attributes(self.root, self.paths, cached=False)


class _Index:
    """The files as the index holds them: the commit as it will land."""

    def __init__(self, root: Path):
        self.root = root
        self.blobs: dict[str, str] = {}
        for entry in _split(_git(root, "ls-files", "-z", "--stage")):
            meta, rel = entry.split("\t", 1)
            mode, blob, stage = meta.split()
            if not (is_input(rel) or rel.endswith("/" + STAMP_NAME)):
                continue
            if stage != "0":
                raise StampError(f"{rel} is unmerged; resolve it first")
            if is_input(rel) and mode != "100644" and mode != "100755":
                raise StampError(f"{rel} is a link or a submodule; a build input must be a file")
            self.blobs[rel] = blob
        self.paths = sorted(rel for rel in self.blobs if is_input(rel))
        self._data = self._cat(list(self.blobs.values()))

    def _cat(self, blobs: list[str]) -> dict[str, bytes]:
        if not blobs:
            return {}
        out = _git(self.root, "cat-file", "--batch", input="\n".join(blobs).encode() + b"\n")
        data, at = {}, 0
        while at < len(out):
            end = out.index(b"\n", at)
            blob, _, size = out[at:end].decode().split()
            start = end + 1
            data[blob] = out[start:start + int(size)]
            at = start + int(size) + 1
        return data

    def read(self, rel: str) -> bytes | None:
        blob = self.blobs.get(rel)
        return None if blob is None else self._data[blob]

    def unsafe(self) -> list[str]:
        return _attributes(self.root, self.paths, cached=True)

    def touched(self) -> set[str]:
        """The paths the staged commit changes against HEAD; every path with no HEAD."""
        head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=self.root,
                              capture_output=True)
        if head.returncode != 0:
            return set(self.blobs)
        return set(_split(_git(self.root, "diff", "--cached", "--name-only", "-z",
                               "--no-renames", "HEAD")))


def check_stamps(root: Path, *, staged: bool = False,
                 only: Iterable[str] | None = None) -> tuple[list[str], list[str]]:
    """Check the stamps of the packages with a page at `root`.

    Returns the packages checked and one line per problem. `staged` reads the index and
    checks only the packages the staged commit touches; `only` limits the check to those
    package directories. Raises `StampError` when the check can't run.
    """
    tree = _Index(root) if staged else _WorkTree(root)
    names = web_packages(tree.paths)
    if only is not None:
        wanted = set(only)
        names = [name for name in names if name in wanted]
    if staged:
        touched = tree.touched()
        shared = any(rel == LOCK or rel.startswith("tools/web/") for rel in touched)
        names = [name for name in names
                 if shared or stamp_path(name) in touched
                 or any(rel.startswith(f"packages/{name}/web/") for rel in touched)]
    if not names:
        return [], []
    unsafe = tree.unsafe()
    if unsafe:
        raise StampError("these build inputs are neither `text eol=lf` nor `binary` in "
                         ".gitattributes, so a checkout may change their bytes; add a rule "
                         f"for their type or remove them: {', '.join(unsafe)}")
    lock = tree.read(LOCK)
    if lock is None:
        raise StampError(f"there is no {LOCK}; run `npm install` at the repository root")
    version = locked_esbuild(lock)
    problems = []
    for name in names:
        files = [(rel, tree.read(rel)) for rel in package_inputs(tree.paths, name)]
        want = stamp(version, files)
        have = tree.read(stamp_path(name))
        if have is None:
            problems.append(f"{name}: {stamp_path(name)} is missing; {FIX}")
        elif have.decode(errors="replace").strip() != want:
            problems.append(f"{name}: its web sources changed since its last build; {FIX}")
    return names, problems


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moxi-dev stamp",
        description="Check that each web page's committed build stamp matches its sources, "
                    "without Node. Run it from the repository's root.")
    parser.add_argument("--staged", action="store_true",
                        help="read the index and check only the packages the staged "
                             "commit touches, as a pre-commit hook does")
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        root = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        names, problems = check_stamps(root, staged=args.staged)
    except StampError as exc:
        print(f"moxi-dev stamp: {exc}", file=sys.stderr)
        return 2
    for problem in problems:
        print(f"moxi-dev stamp: {problem}", file=sys.stderr)
    if problems and args.staged:
        print("moxi-dev stamp: the commit is refused; nothing was committed", file=sys.stderr)
    checked = ", ".join(names) or "none"
    print(f"moxi-dev stamp: {'fail' if problems else 'pass'} | packages: {checked}",
          flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

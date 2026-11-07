"""The module contract check, which every package's `tests/test_contract.py` runs.

`check(root, *, staged=False)` reads a package's `pyproject.toml` and its Python files with
`ast`, imports none of them, and returns a sorted list of `Finding`. With `staged` it
reads every file from git's index instead of the working tree, so a partly staged commit
is judged as it will land. An empty list means the package keeps the contract.

From a shell, `moxi-dev contract [--staged] [PATH]` (or `python -m moxi_dev.contract`)
runs the same check over the module directory at PATH, the current directory by
default, inside the workspace or out of it: it prints each finding, then exits 0 when
there is none, 1 when there are some and 2 when the check cannot run there.

The contract's two import rules are checked by import-linter and deptry, with a
configuration `moxi_dev.lint` writes from each package's declarations, and `moxi-dev
check` runs them. What those tools cannot see stays here. The checks, numbered as each
finding names them:

1. Another module's internals. import-linter sees which modules a package imports;
   it cannot see the names a `from` import takes. So here: in `src/` and `tests/`, a
   name or a dotted part starting with `_` imported from the core or from a Moxi
   module the package declares, and a name imported from the core's package itself
   (`from moxi import x`) that is not one of the core's public modules.
2. An undeclared dependency: deptry's alone, so nothing here.
3. An extra reached at import time from the announcement, the `API` module, the `CLI`
   target, the `SETUP` target or a console script not in `GATED`.
4. Extras and reasons out of step: an extra without an `OPTIONAL` sentence or the
   reverse; an extra that installs an optional Moxi module under another name than the
   module's; a `GATED` script or extra that does not exist.
5. A setting in the environment (in `src/`): an environment read whose key is neither a
   literal nor a module-level string constant, unless it falls under a prefix entry of
   `INTERNAL_ENV`; a key starting `MOXI_` that `INTERNAL_ENV` does not declare.
6. A module's own console script looked up on PATH with `shutil.which` (in `src/`).
7. The names: the `moxi.modules` entry point, the distribution and import names (no
   module may take a core verb or a name the core keeps for itself), the console
   scripts (a module never declares `moxi`, which the core alone declares; its others
   are named `moxi-<name>-<part>`), the announcement, the `CLI` target's `argv` and
   `prog` parameters, the `SETTINGS` schema and the skills in the `SKILLS` folder. Each
   name of `moxi.modules.KNOWN` the announcement defines is read as a literal and held to
   the checks `moxi.modules.load` runs; a name it leaves out passes, as it takes its
   default. The `SKILLS` folder, beside the announcement in its package and written
   with `/` (a backslash fails, as POSIX reads it as part of a name), must be there;
   each skill in it (a subfolder `moxi.names.is_skill_folder` counts) is named
   `moxi-<name>` and holds a `SKILL.md` whose frontmatter `name:` is the folder name.
   A symlink along the way (the folder, a skill, its `SKILL.md`) is followed. In a staged
   run the link's target is read against the link's folder and looked up in the index,
   so the check counts and reads what a checkout of the commit would; a link whose
   target isn't committed, that points outside the package or that loops is a finding
   naming the link.
8. A write site not listed. In `src/`, every call that writes a file itself (`open`,
   `io.open`, `os.fdopen` or a path's `.open` in a write mode, `os.open` with a write
   flag, `.write_text`, `.write_bytes`, `os.replace`, `sqlite3.connect`) must have its
   line in `tests/write-sites.txt`, and every line there must still match a site. The
   list holds the sites a module has before it moves onto `moxi.files` and `moxi.store`,
   so it only shrinks.

`segment` findings hold a status line segment to showing only: the import-time closure of
the announcement's `SEGMENT` module, within the module's package, may not import
`subprocess`, `socket`, `multiprocessing`, `ctypes`, `http`, `urllib` or turns' package,
nor call `os.system`, `os.exec*`, `os.spawn*`, `os.fork` or `os.kill`.

The core's own package, known by its distribution name `moxi`, is held to checks 1 to 6
with two exemptions: check 5 does not apply inside `moxi.paths` and `moxi.config`, which
read every module's root locators and declared overrides by computed keys, nor inside
`moxi.log` and `moxi.proc`, which read the core's own logging and lifeline variables, nor
inside `moxi.units._fence`, which reads the variable that lets a proof row reach a real
supervisor under pytest; and check 6 does not apply to `shutil.which("moxi")` inside
`moxi.cli`, where the shortcut verb looks up the umbrella's place. Check 7 is replaced:
the core declares no `moxi.modules` entry point, its import package is `moxi`, and its
one console script is `moxi = "moxi.cli:main"`. With no announcement, any extra the core
declares fails check 4. Check 8 does not apply to the core, which holds the helpers
modules write through. The core's vendored copies under `src/moxi/_vendor/` are released
libraries kept as released, so no check reads them.

moxi-dev, the distribution this module ships in, is the contributors' package and not a
module: checks 1, 7 and 8 do not apply to it (it installs nothing a user runs, so it
has no move onto the core's write helpers to track), an import of `moxi_dev` is never
another module's internals, and with no announcement to name its `INTERNAL_ENV`, check
5 lets it read the variables named `MOXI_TEST_<what>`, which are its own.
"""

from __future__ import annotations

import ast
import posixpath
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from moxi import names
from moxi.modules import KNOWN, _check, _relative, _TooNew

#: The distribution and import name of the core.
CORE = names.HARNESS
#: The contributors' package: not a module, so checks 1, 7 and 8 pass it by.
DEV = f"{names.HARNESS}-dev"
#: Its import name, which another package's tests may import like any declared one.
DEV_IMPORT = f"{names.HARNESS}_dev"
#: The variables the contributors' package reads, having no announcement to declare
#: them in: the test tiers' and the check's own (`MOXI_TEST_LIVE`, `MOXI_TEST_WORKERS`).
DEV_ENV_PREFIX = "MOXI_TEST_"
#: The entry-point group a module announces itself in (as `moxi.modules.GROUP`).
GROUP = "moxi.modules"
#: The core modules another module may import (the core's table of what it holds). The
#: core's other modules (install, uninstall, run, catalogue, strays, home and the
#: underscored ones) are its own.
CORE_MODULES = frozenset({"names", "paths", "config", "modules", "cli", "files", "store",
                          "proc", "log", "exit", "aio", "ipc", "units", "doctor", "place"})
#: Where the core keeps its vendored copies of released libraries, which no check reads.
VENDORED = "src/moxi/_vendor/"
#: The core's one console script.
CORE_SCRIPT = ("moxi", "moxi.cli:main")
#: Inside these core modules check 5 does not apply.
CORE_ENV_READERS = frozenset({"moxi.paths", "moxi.config", "moxi.log", "moxi.proc",
                              "moxi.units._fence"})
#: The core's own plumbing variables, which the core reads where check 5 applies.
CORE_INTERNAL_ENV = frozenset({"MOXI_CORE_LOCK_FD"})
#: What a segment's closure may not import (top-level package names), besides turns.
SEGMENT_IMPORTS = frozenset({"subprocess", "socket", "multiprocessing", "ctypes", "http",
                             "urllib"})

_MODULE_IMPORT = re.compile(r"moxi_([a-z0-9]+)")
_MODULE_DIST = re.compile(r"moxi-([a-z0-9]+)")
_SCRIPT_PART = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_DOTTED = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*")
_TARGET = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*:[A-Za-z_]\w*")
_REQUIREMENT = re.compile(r"\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*"
                          r"(?:\[([^\]]*)\])?")


class CheckError(Exception):
    """The check cannot run over this path at all (no `pyproject.toml`, not a git repo)."""


@dataclass(frozen=True, order=True)
class Finding:
    """One way the repo breaks the contract."""

    path: str
    line: int
    check: str
    message: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: check {self.check}: {self.message}"


def check(root: Path | str, *, staged: bool = False) -> list[Finding]:
    """Check the module repo at `root` against the contract; return its findings, sorted.

    With `staged`, every file is read from git's index, as the commit will land.
    """
    root = Path(root)
    tree = _IndexTree(root) if staged else _WorkTree(root)
    return sorted(set(_Repo(root, tree).run()))


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi-dev contract") -> int:
    """`moxi-dev contract [--staged] [PATH]`: print the findings; exit 1 on any, 2 when the
    check cannot run."""
    import argparse

    parser = argparse.ArgumentParser(
        prog=prog,
        description="Check a Moxi module's directory against the module contract: the "
                    "names its imports take, its extras, its environment reads and its "
                    "names. The import rules import-linter and deptry check run in "
                    "moxi-dev check.")
    parser.add_argument("--staged", action="store_true",
                        help="read the files from git's index, as the next commit lands")
    parser.add_argument("path", nargs="?", default=".", metavar="PATH",
                        help="the module's directory (default: the current directory)")
    args = parser.parse_args(argv)
    try:
        findings = check(args.path, staged=args.staged)
    except CheckError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding)
    if findings:
        count = len(findings)
        print(f"{prog}: {count} finding{'s' if count != 1 else ''} in {args.path}",
              file=sys.stderr)
        return 1
    return 0


# --- reading the tree ----------------------------------------------------------------------

def _git(root: Path, *args: str, staged: bool, data: bytes | None = None) -> bytes:
    import os
    import subprocess

    env = dict(os.environ)
    if not staged:
        # Outside a hook's staged run, find the repo by the path alone.
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            env.pop(name, None)
    try:
        done = subprocess.run(["git", "-C", str(root), *args], input=data,
                              capture_output=True, env=env, check=False)
    except OSError as exc:
        raise CheckError(f"cannot run git: {exc}") from None
    if done.returncode != 0:
        raise CheckError(f"git {args[0]} failed in {root}: "
                         f"{done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def _is_git(root: Path, *, staged: bool) -> bool:
    try:
        out = _git(root, "rev-parse", "--is-inside-work-tree", staged=staged)
    except CheckError:
        return False
    return out.strip() == b"true"


class _WorkTree:
    """The files of a working tree: tracked and untracked-unignored in a git repo,
    else every file outside hidden, build and environment directories."""

    _SKIP = frozenset({"__pycache__", "build", "dist", "node_modules"})

    def __init__(self, root: Path):
        if not root.is_dir():
            raise CheckError(f"{root} is not a directory")
        self.root = root
        if _is_git(root, staged=False):
            out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                       staged=False)
            listed = sorted({p for p in out.decode("utf-8", "surrogateescape").split("\0")
                             if p})
            self.paths = [p for p in listed if (root / p).is_file()]
        else:
            self.paths = sorted(self._walk(root, PurePosixPath()))

    def _walk(self, here: Path, rel: PurePosixPath) -> Iterable[str]:
        for entry in sorted(here.iterdir()):
            sub = rel / entry.name
            if entry.is_dir():
                if (entry.name.startswith(".") or entry.name in self._SKIP
                        or entry.name.endswith(".egg-info")
                        or (entry / "pyvenv.cfg").exists()):
                    continue
                yield from self._walk(entry, sub)
            elif entry.is_file():
                yield str(sub)

    def files(self) -> list[str]:
        return self.paths

    def read(self, path: str) -> bytes | None:
        try:
            return (self.root / path).read_bytes()
        except OSError:
            return None

    def folder(self, path: str) -> list[str] | str:
        """The names of the subfolders of folder `path`, as the file system has them;
        else `missing` or `file`."""
        here = self.root / path
        if not here.is_dir():
            return "file" if here.exists() else "missing"
        try:
            return sorted(entry.name for entry in here.iterdir() if entry.is_dir())
        except OSError:
            return "missing"

    def link_problem(self, path: str) -> tuple[str, str] | None:
        """The file system follows every symlink itself, so none is reported here."""
        return None


class _IndexTree:
    """The files of git's index: what the next commit holds.

    A symlink is followed as the file system would follow it in a checkout of that
    commit: its blob is the target, read against the link's folder and looked up in the
    index, so a symlinked folder or file reads the same here as in the working tree.

    `files()` lists regular files only. A symlink to a file is followed when the skills
    check reads it, but the other checks don't scan it here, while the working tree's
    listing includes it."""

    _HOPS = 40          # the most symlinks one path may pass through, as on Linux

    def __init__(self, root: Path):
        if not root.is_dir():
            raise CheckError(f"{root} is not a directory")
        if not _is_git(root, staged=True):
            raise CheckError(f"{root} is not inside a git work tree, so it has no index "
                             "to check; drop --staged")
        self.root = root
        out = _git(root, "ls-files", "-z", "--stage", staged=True)
        self.blobs: dict[str, str] = {}
        self.links: dict[str, str] = {}
        self.unmerged: list[str] = []
        for entry in out.decode("utf-8", "surrogateescape").split("\0"):
            if not entry:
                continue
            info, path = entry.split("\t", 1)
            mode, sha, stage = info.split()
            if stage != "0":
                self.unmerged.append(path)
            elif mode in ("100644", "100755"):
                self.blobs[path] = sha
            elif mode == "120000":
                self.links[path] = sha
        self.paths = sorted(self.blobs)
        self.dirs = {str(parent) for p in (*self.blobs, *self.links)
                     for parent in PurePosixPath(p).parents if str(parent) != "."}
        self._cache: dict[str, bytes] = {}
        self._targets: dict[str, str] = {}

    def files(self) -> list[str]:
        return self.paths

    def _target(self, link: str) -> str:
        if link not in self._targets:
            raw = _git(self.root, "cat-file", "blob", self.links[link], staged=True)
            self._targets[link] = raw.decode("utf-8", "surrogateescape")
        return self._targets[link]

    def _resolve(self, path: str) -> tuple[str | None, tuple[str, str] | None]:
        """`path` with every symlink along it followed, as `(path, None)`; or `(None,
        (link, why))` naming the link that can't be followed. A path that isn't in the
        index comes back as it is, for the caller to find missing."""
        parts = path.split("/")
        hops = 0
        at = 0
        while at < len(parts):
            here = "/".join(parts[:at + 1])
            if here not in self.links:
                at += 1
                continue
            target = self._target(here)
            hops += 1
            if hops > self._HOPS:
                return None, (here, "is a symlink in a loop")
            joined = posixpath.normpath(posixpath.join(posixpath.dirname(here), target))
            if target.startswith("/") or joined == ".." or joined.startswith("../"):
                return None, (here, f"is a symlink to {target}, outside the package, so "
                                    "the commit doesn't carry what it points at")
            if joined != "." and joined not in self.blobs and joined not in self.links \
                    and joined not in self.dirs:
                return None, (here, f"is a symlink whose target, {target}, isn't "
                                    "committed")
            parts = ([] if joined == "." else joined.split("/")) + parts[at + 1:]
            at = 0
        return "/".join(parts), None

    def link_problem(self, path: str) -> tuple[str, str] | None:
        """The symlink on the way to `path` that can't be followed in the index, as
        `(link, why)`; else None."""
        return self._resolve(path)[1]

    def _entries(self, path: str) -> Iterable[str]:
        prefix = f"{path}/" if path else ""
        for p in (*self.blobs, *self.links):
            if p.startswith(prefix):
                yield p[len(prefix):]

    def folder(self, path: str) -> list[str] | str:
        """The names of the subfolders of folder `path`, each the first part of a path
        under it that has a file below it, or a symlink in it to such a folder (or one
        that can't be followed, for the caller to report); else `missing` or `file`."""
        found, _ = self._resolve(path)
        if found is None:
            return "missing"
        if found in self.blobs:
            return "file"
        under = list(self._entries(found))
        if not under:
            return "missing"
        names = {p.split("/", 1)[0] for p in under if "/" in p}
        for name in under:
            link = f"{found}/{name}" if found else name
            if "/" not in name and link in self.links:
                there, problem = self._resolve(link)
                if problem is not None or there in self.dirs:
                    names.add(name)
        return sorted(names)

    def read(self, path: str) -> bytes | None:
        found, _ = self._resolve(path)
        if found is None or found not in self.blobs:
            return None
        if not self._cache:
            self._load()
        if found not in self._cache:
            self._cache[found] = _git(self.root, "cat-file", "blob", self.blobs[found],
                                      staged=True)
        return self._cache[found]

    def _load(self) -> None:
        # One `git cat-file --batch` for every blob the check may read.
        wanted = [p for p in self.paths if p.endswith((".py", ".toml"))]
        if not wanted:
            self._cache[""] = b""
            return
        out = _git(self.root, "cat-file", "--batch", staged=True,
                   data="".join(f"{self.blobs[p]}\n" for p in wanted).encode())
        at = 0
        for path in wanted:
            end = out.index(b"\n", at)
            header = out[at:end].split()
            size = int(header[2])
            self._cache[path] = out[end + 1:end + 1 + size]
            at = end + 1 + size + 1


# --- requirements and names ----------------------------------------------------------------

def _skill_md_name(raw: bytes) -> tuple[int, str | None, str | None]:
    """The `name:` a skill's `SKILL.md` gives, as `(line, name, None)`, or `(line, None,
    problem)` when it gives none the check can read.

    The file opens with a `---` line (after a UTF-8 byte order mark, if any) and its
    frontmatter runs to the next `---` line; lines may end in CRLF. The frontmatter holds
    one `name:` line at column 0, whose value is stripped of spaces and of one pair of
    matching quotes. The frontmatter is not read as YAML, so a name written another way
    (with a comment after it, say, or as a block scalar) is not the folder name."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return 0, None, "is not UTF-8"
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[0].rstrip(" \t") != "---":
        return 1, None, "does not open with a --- line"
    for end, line in enumerate(lines[1:], start=1):
        if line.rstrip(" \t") == "---":
            break
    else:
        return 1, None, "opens its frontmatter with --- and never closes it"
    found = [(n + 1, line) for n, line in enumerate(lines[1:end], start=1)
             if line.startswith("name:")]
    if not found:
        return 1, None, "has no name: line in its frontmatter"
    if len(found) > 1:
        return found[1][0], None, (f"has {len(found)} name: lines in its frontmatter "
                                   f"(lines {', '.join(str(n) for n, _ in found)})")
    line_no, line = found[0]
    value = line[len("name:"):].strip(" \t")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return line_no, value, None

def _normalize(name: str) -> str:
    """A distribution or extra name as packaging compares them."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement(text: object) -> tuple[str, frozenset[str]] | None:
    """The normalized distribution name and extras of one requirement string."""
    if not isinstance(text, str):
        return None
    match = _REQUIREMENT.match(text)
    if not match:
        return None
    extras = frozenset(_normalize(e.strip()) for e in (match.group(2) or "").split(",")
                       if e.strip())
    return _normalize(match.group(1)), extras


def _literal(node: ast.AST):
    """The value of an announcement's assignment, as `ast.literal_eval` reads it, with
    `frozenset(...)`, `set(...)`, `dict(...)` and `tuple(...)` of literals too."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("frozenset", "set", "dict", "tuple")
            and len(node.args) <= 1):
        make = {"frozenset": frozenset, "set": set, "dict": dict, "tuple": tuple}
        inner = _literal(node.args[0]) if node.args else ()
        if node.keywords:
            if node.func.id != "dict" or any(k.arg is None for k in node.keywords):
                raise ValueError("not a literal")
            return dict(inner, **{k.arg: _literal(k.value) for k in node.keywords})
        return make[node.func.id](inner)
    return ast.literal_eval(node)


def _assignments(tree: ast.Module) -> dict[str, ast.AST]:
    """Each module-level name bound by a plain or annotated assignment; the last wins."""
    found: dict[str, ast.AST] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = stmt.value
        elif (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
              and stmt.value is not None):
            found[stmt.target.id] = stmt.value
    return found


def _string_constants(tree: ast.Module) -> dict[str, str]:
    return {name: value.value for name, value in _assignments(tree).items()
            if isinstance(value, ast.Constant) and isinstance(value.value, str)}


def _is_type_checking(test: ast.AST) -> bool:
    return ((isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
            or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"))


@dataclass(frozen=True)
class _Import:
    line: int
    module: str           # as written; "" for `from . import x`
    names: tuple[str, ...]
    level: int
    at_import: bool       # runs when the file is imported (not in a function or a
                          # TYPE_CHECKING block)


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: list[_Import] = []
        self.deferred = 0

    def _function(self, node: ast.AST) -> None:
        self.deferred += 1
        self.generic_visit(node)
        self.deferred -= 1

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = _function

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            self.visit(node.test)
            self.deferred += 1
            for stmt in node.body:
                self.visit(stmt)
            self.deferred -= 1
            for stmt in node.orelse:
                self.visit(stmt)
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.found.append(_Import(node.lineno, alias.name, (), 0, not self.deferred))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.found.append(_Import(node.lineno, node.module or "",
                                  tuple(a.name for a in node.names), node.level,
                                  not self.deferred))


class _Source:
    """One parsed Python file of the repo."""

    def __init__(self, path: str, tree: ast.Module, module: str | None, is_package: bool):
        self.path = path
        self.tree = tree
        #: Its dotted name when it is in the repo's import package(s) under `src/`.
        self.module = module
        self.is_package = is_package
        visitor = _ImportVisitor()
        visitor.visit(tree)
        self.imports = visitor.found
        parts = PurePosixPath(path).parts
        self.in_src = parts[0] == "src"
        self.in_tests = parts[0] == "tests"

    def absolute(self, imp: _Import) -> str | None:
        """The absolute module an import names; None for a relative import outside a
        package the check knows."""
        if imp.level == 0:
            return imp.module
        if self.module is None:
            return None
        package = self.module.split(".") if self.is_package else self.module.split(".")[:-1]
        if imp.level - 1 > len(package) - 1:
            return None
        base = package[:len(package) - (imp.level - 1)]
        return ".".join(base + ([imp.module] if imp.module else []))


# --- the repo --------------------------------------------------------------------------------

class _Repo:
    def __init__(self, root: Path, tree):
        self.root = root
        self.tree = tree
        self.findings: list[Finding] = []
        self._apis: dict[str, tuple[bool, str | None]] = {}

    def add(self, path: str, line: int, check: str, message: str) -> None:
        self.findings.append(Finding(path, line, check, message))

    # -- setup ---------------------------------------------------------------------------

    def run(self) -> list[Finding]:
        for path in getattr(self.tree, "unmerged", ()):
            self.add(path, 0, "read", "has unmerged entries in the index; resolve the "
                                      "merge first")
        if not self._read_pyproject():
            return self.findings
        self._read_sources()
        self._read_announcement()
        if not self.dev:
            self.check_imports()       # check 1, the names import-linter cannot see
        self.check_extras_at_import()  # check 3
        self.check_extras()            # check 4
        self.check_environment()       # check 5
        self.check_which()             # check 6
        if self.core:
            self.check_core_names()    # check 7, the core's form
        elif not self.dev:
            self.check_names()         # check 7
        self.check_segment()
        if not self.core and not self.dev:
            self.check_write_sites()   # check 8
        return self.findings

    def _read_pyproject(self) -> bool:
        import tomllib

        raw = self.tree.read("pyproject.toml")
        if raw is None:
            raise CheckError(f"{self.root} has no pyproject.toml, so it is not a module "
                             "repo")
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            self.add("pyproject.toml", 0, "read", f"cannot be parsed ({exc})")
            return False
        project = data.get("project")
        if not isinstance(project, dict) or not isinstance(project.get("name"), str):
            self.add("pyproject.toml", 0, "7", "has no [project] name")
            return False
        self.dist = _normalize(project["name"])
        self.core = self.dist == CORE
        self.dev = self.dist == DEV
        match = _MODULE_DIST.fullmatch(self.dist)
        #: The module's name, when the distribution is spelled `moxi-<name>`.
        self.name = match.group(1) if match else None

        self.deps: dict[str, frozenset[str]] = {}
        self.extras: dict[str, list[tuple[str, frozenset[str]]]] = {}
        self.extra_names: dict[str, str] = {}   # normalized -> as written
        for req in project.get("dependencies") or []:
            parsed = _requirement(req)
            if parsed:
                self.deps[parsed[0]] = self.deps.get(parsed[0], frozenset()) | parsed[1]
        for extra, reqs in (project.get("optional-dependencies") or {}).items():
            norm = _normalize(extra)
            self.extra_names[norm] = extra
            self.extras[norm] = [p for p in map(_requirement, reqs or []) if p]
        self.groups: set[str] = set()
        for reqs in (data.get("dependency-groups") or {}).values():
            for req in reqs or []:
                parsed = _requirement(req)
                if parsed:
                    self.groups.add(parsed[0])
        in_extras = {dist for reqs in self.extras.values() for dist, _ in reqs}
        #: Distributions installed only through an extra.
        self.extra_only = in_extras - set(self.deps) - {self.dist}
        self.declared = set(self.deps) | in_extras | {self.dist}

        table = ((data.get("tool") or {}).get("moxi") or {}).get("imports") or {}
        self.import_table = {k: _normalize(v) for k, v in table.items()
                             if isinstance(k, str) and isinstance(v, str)}
        self.scripts: dict[str, str] = {}
        for group in ("scripts", "gui-scripts"):
            for script, target in (project.get(group) or {}).items():
                self.scripts[script] = target if isinstance(target, str) else ""
        eps = (project.get("entry-points") or {}).get(GROUP)
        self.entry_points: dict[str, str] = dict(eps) if isinstance(eps, dict) else {}
        self.own_import = CORE if self.core else (f"moxi_{self.name}" if self.name else None)
        return True

    def _read_sources(self) -> None:
        self.sources: dict[str, _Source] = {}
        self.modules: dict[str, _Source] = {}
        src_tops = set()
        tests_local = {"tests", "conftest"}
        for path in self.tree.files():
            parts = PurePosixPath(path).parts
            if parts[0] == "src" and len(parts) >= 2:
                src_tops.add(parts[1].removesuffix(".py"))
            if parts[0] == "tests" and len(parts) >= 2:
                tests_local.update(p.removesuffix(".py") for p in parts[1:])
        #: The top-level import packages under `src/`.
        self.src_tops = {t for t in src_tops if t.isidentifier()}
        self.own = set(self.src_tops) | ({self.own_import} if self.own_import else set())
        self.tests_local = {t for t in tests_local if t.isidentifier()}
        for path in self.tree.files():
            if not path.endswith(".py"):
                continue
            if self.core and path.startswith(VENDORED):
                continue
            raw = self.tree.read(path)
            try:
                parsed = ast.parse(raw if raw is not None else b"", filename=path)
            except (SyntaxError, ValueError) as exc:
                why = exc.msg if isinstance(exc, SyntaxError) else str(exc)
                self.add(path, getattr(exc, "lineno", 0) or 0, "read",
                         f"does not parse, so the check cannot read it ({why})")
                continue
            parts = PurePosixPath(path).with_suffix("").parts
            module, is_package = None, False
            if (parts[0] == "src" and len(parts) >= 2
                    and all(p.isidentifier() for p in parts[1:])):
                dotted = list(parts[1:])
                if dotted[-1] == "__init__":
                    dotted.pop()
                    is_package = True
                module = ".".join(dotted) if dotted else None
            source = _Source(path, parsed, module, is_package)
            self.sources[path] = source
            if module:
                self.modules[module] = source
        self._edges = self._import_edges()

    def _read_announcement(self) -> None:
        """Read the announcement the entry point names, without importing it."""
        self.announcement: dict[str, object] | None = None
        self.announcement_source: _Source | None = None
        self.announcement_problems: list[str] = []
        if self.core or len(self.entry_points) != 1:
            return
        target = next(iter(self.entry_points.values()))
        if not isinstance(target, str) or target not in self.modules:
            return
        source = self.modules[target]
        self.announcement_source = source
        bound = _assignments(source.tree)
        values: dict[str, object] = {}
        for name in KNOWN:
            if name not in bound:
                continue
            try:
                values[name] = _literal(bound[name])
            except (ValueError, TypeError, SyntaxError, RecursionError):
                self.announcement_problems.append(
                    f"defines {name} by an expression the check cannot read; write it as "
                    "a literal")
        self.announcement = values

    def ann(self, name: str, default=None):
        value = (self.announcement or {}).get(name, default)
        return default if value is None else value

    def ann_map(self, name: str) -> dict[str, str]:
        value = self.ann(name, {})
        if isinstance(value, Mapping):
            return {k: v for k, v in value.items() if isinstance(k, str)}
        return {}

    # -- import resolution -----------------------------------------------------------------

    def provider(self, top: str) -> list[str]:
        """The distributions that may provide import name `top`, by the repo's own
        declarations only."""
        if top == CORE:
            return [CORE]
        match = _MODULE_IMPORT.fullmatch(top)
        if match:
            return [f"moxi-{match.group(1)}"]
        found = [_normalize(top)]
        if top in self.import_table:
            found.append(self.import_table[top])
        return found

    def _import_edges(self) -> dict[str, set[str]]:
        """Own modules each own module imports when it is imported."""
        edges: dict[str, set[str]] = {}
        for module, source in self.modules.items():
            out = edges.setdefault(module, set())
            for imp in source.imports:
                if not imp.at_import:
                    continue
                target = source.absolute(imp)
                if not target or target.split(".")[0] not in self.own:
                    continue
                out.add(target)
                for name in imp.names:
                    if f"{target}.{name}" in self.modules:
                        out.add(f"{target}.{name}")
        return edges

    def closure(self, starts: Iterable[str]) -> set[str]:
        """The own modules loaded when each of `starts` is imported."""
        seen: set[str] = set()
        stack = list(starts)
        while stack:
            module = stack.pop()
            if module in seen:
                continue
            seen.add(module)
            parts = module.split(".")
            stack.extend(".".join(parts[:i]) for i in range(1, len(parts)))
            stack.extend(self._edges.get(module, ()))
        return {m for m in seen if m in self.modules}

    # -- check 1 -----------------------------------------------------------------------------

    def check_imports(self) -> None:
        """The names an import takes from the core or a declared Moxi module.

        Which modules a package may import at all is import-linter's to check; this sees
        what it cannot: a name or dotted part starting with `_`, and a name taken from
        the core's package itself that is not one of its public modules."""
        for source in self.sources.values():
            if not (source.in_src or source.in_tests):
                continue
            for imp in source.imports:
                if imp.level:
                    continue
                top = imp.module.split(".")[0]
                if top in self.own or not self._reaches_moxi(top):
                    continue
                where = (source.path, imp.line, "1")
                owner = "the core's contract" if top == CORE else "part of an API"
                parts = imp.module.split(".")
                private = [p for p in parts[1:] if p.startswith("_")]
                if private:
                    self.add(*where, f"imports {imp.module}: a name starting with _ is not "
                                     f"{owner}")
                    continue
                for name in imp.names:
                    if name.startswith("_"):
                        self.add(*where, f"imports {name} from {imp.module}: a name "
                                         f"starting with _ is not {owner}")
                    elif (top == CORE and len(parts) == 1 and name != "*"
                          and name not in CORE_MODULES):
                        self.add(*where, f"imports moxi.{name}, which is not one of the "
                                         f"core's modules "
                                         f"({', '.join(sorted(CORE_MODULES))})")

    def _reaches_moxi(self, top: str) -> bool:
        """Whether import name `top` is the core's or a declared Moxi module's package."""
        if top == CORE:
            return True
        if top == DEV_IMPORT or not _MODULE_IMPORT.fullmatch(top):
            return False
        dist = self.provider(top)[0]
        return dist in self.deps or dist in self.extra_only

    def _api_of(self, module: str, dist: str) -> tuple[bool, str | None]:
        """Whether the announcement of required module `module` could be read, and the
        `API` it declares. Read from this environment's metadata without importing, else
        from a checkout of it beside this repo."""
        if module not in self._apis:
            found = _api_from_environment(dist)
            if found is None:
                found = _api_from_checkout(self.root.resolve().parent / module, dist)
            self._apis[module] = found if found is not None else (False, None)
        return self._apis[module]

    # -- check 3 -----------------------------------------------------------------------------

    def check_extras_at_import(self) -> None:
        if not self.extra_only:
            return
        starts: dict[str, str] = {}
        own = self.own

        def start(dotted: object, label: str) -> None:
            if isinstance(dotted, str) and dotted.split(".")[0] in own:
                starts.setdefault(label, dotted)

        if self.announcement_source is not None:
            start(self.announcement_source.module, "the announcement")
            start(self.ann("API"), "the API module")
            for name in ("CLI", "SETUP"):
                target = self.ann(name)
                if isinstance(target, str):
                    start(target.split(":")[0], f"the {name} target")
        gated = self.ann_map("GATED")
        for script, target in self.scripts.items():
            if script not in gated and target:
                start(target.split(":")[0].strip(), f"the console script {script}")
        hits: dict[tuple[str, int], tuple[str, set[str]]] = {}
        for label, dotted in starts.items():
            for module in self.closure([dotted]):
                source = self.modules[module]
                for imp in source.imports:
                    if not imp.at_import or imp.level:
                        continue
                    top = imp.module.split(".")[0]
                    if top in own:
                        continue
                    extra = [d for d in self.provider(top) if d in self.extra_only]
                    if extra and not any(d in self.deps for d in self.provider(top)):
                        key = (source.path, imp.line)
                        hits.setdefault(key, (f"{top} ({extra[0]})", set()))[1].add(label)
        for (path, line), (what, labels) in hits.items():
            self.add(path, line, "3",
                     f"imports {what} at import time, which comes only with an extra, and "
                     f"is reached from {', '.join(sorted(labels))}; import it inside the "
                     "function that needs it, or gate the console script in GATED")

    # -- check 4 -----------------------------------------------------------------------------

    def check_extras(self) -> None:
        if self.core:
            for norm in self.extras:
                self.add("pyproject.toml", 0, "4",
                         f"declares the extra {self.extra_names[norm]!r}, but the core has "
                         "no announcement to give it an OPTIONAL sentence; the core "
                         "declares no extras")
            return
        if self.announcement is None:
            return
        optional = {_normalize(k): k for k in self.ann_map("OPTIONAL")}
        path = self.announcement_source.path
        for norm in sorted(set(self.extras) - set(optional)):
            self.add("pyproject.toml", 0, "4",
                     f"declares the extra {self.extra_names[norm]!r} with no OPTIONAL "
                     f"sentence in {path}; add one saying what installing it enables")
        for norm in sorted(set(optional) - set(self.extras)):
            self.add(path, 0, "4",
                     f"OPTIONAL names {optional[norm]!r}, which pyproject.toml does not "
                     "declare as an extra")
        for norm, reqs in sorted(self.extras.items()):
            for dist, _ in reqs:
                match = _MODULE_DIST.fullmatch(dist)
                if match and dist in self.extra_only and norm != match.group(1):
                    self.add("pyproject.toml", 0, "4",
                             f"the extra {self.extra_names[norm]!r} installs the optional "
                             f"module {dist}; an extra that installs a module is named by "
                             f"it: {match.group(1)!r}")
        for script, extra in self.ann_map("GATED").items():
            if script not in self.scripts:
                self.add(path, 0, "4", f"GATED names the console script {script!r}, which "
                                       "pyproject.toml does not declare")
            if not isinstance(extra, str) or _normalize(extra) not in self.extras:
                self.add(path, 0, "4", f"GATED gates {script!r} on the extra {extra!r}, "
                                       "which pyproject.toml does not declare")

    # -- checks 5 and 6 ----------------------------------------------------------------------

    def check_environment(self) -> None:
        internal = self.ann_map("INTERNAL_ENV")
        exact = {k for k in internal if not k.endswith("_")}
        if self.core:
            exact |= CORE_INTERNAL_ENV
        prefixes = tuple(k for k in internal if k.endswith("_"))
        if self.dev:
            prefixes += (DEV_ENV_PREFIX,)
        for source in self.sources.values():
            if not source.in_src:
                continue
            if self.core and source.module in CORE_ENV_READERS:
                continue
            names_ = _OsNames(source.tree)
            consts = _string_constants(source.tree)
            for line, key in names_.environment_reads():
                literal, prefix = _key_text(key, consts)
                if literal is None:
                    if prefixes and prefix.startswith(prefixes):
                        continue
                    self.add(source.path, line, "5",
                             "reads the environment by a key that is neither a literal nor "
                             "a module-level string constant; a setting belongs in the "
                             "config file, read through moxi.config")
                elif literal.startswith("MOXI_") and literal not in exact and not (
                        prefixes and literal.startswith(prefixes)):
                    self.add(source.path, line, "5",
                             f"reads {literal}, which INTERNAL_ENV does not declare; a "
                             "setting belongs in the config file (an env override is "
                             "declared in the schema), a root locator is read by "
                             "moxi.paths, and other plumbing is named in INTERNAL_ENV")

    def check_which(self) -> None:
        if not self.scripts:
            return
        for source in self.sources.values():
            if not source.in_src:
                continue
            names_ = _OsNames(source.tree)
            consts = _string_constants(source.tree)
            for line, arg in names_.which_calls():
                literal, _ = _key_text(arg, consts)
                if literal is None or literal not in self.scripts:
                    continue
                if (self.core and literal == CORE_SCRIPT[0] and source.module
                        and (source.module == "moxi.cli"
                             or source.module.startswith("moxi.cli."))):
                    continue
                self.add(source.path, line, "6",
                         f"looks up its own console script {literal!r} on PATH, where "
                         "another program's script of that name may stand; use "
                         "moxi.paths.script(name)")

    # -- check 8 -----------------------------------------------------------------------------

    def check_write_sites(self) -> None:
        found: dict[str, list[tuple[str, int]]] = {}
        for source in sorted(self.sources.values(), key=lambda s: s.path):
            if not source.in_src:
                continue
            for site, line in write_sites(source.tree):
                found.setdefault(f"{source.path} {site}", []).append((source.path, line))
        raw = self.tree.read(WRITE_SITES)
        listed: dict[str, int] = {}
        for text in (raw or b"").decode("utf-8", "replace").splitlines():
            text = text.strip()
            if text and not text.startswith("#"):
                key = " ".join(text.split())
                listed[key] = listed.get(key, 0) + 1
        for key, places in found.items():
            for path, line in places[listed.get(key, 0):]:
                self.add(path, line, "8",
                         f"writes a file itself ({key.split(' ', 1)[1]}); write through "
                         f"moxi.files or moxi.store, or, for a site this module had "
                         f"before, list it in {WRITE_SITES}")
        for key, count in sorted(listed.items()):
            extra = count - len(found.get(key, ()))
            if extra > 0:
                self.add(WRITE_SITES, 0, "8",
                         f"lists {key!r} {extra} more time(s) than src/ has it; remove "
                         f"the line, so the list only shrinks")

    # -- check 7 -----------------------------------------------------------------------------

    def check_core_names(self) -> None:
        here = "pyproject.toml"
        for name in self.entry_points:
            self.add(here, 0, "7", f"declares the {GROUP} entry point {name!r}: the core "
                                   "is not a module and declares none")
        if CORE not in self.src_tops:
            self.add(here, 0, "7", "has no src/moxi: the core's import package is moxi")
        for top in sorted(self.src_tops - {CORE}):
            self.add(f"src/{top}", 0, "7", "is a second import package; the core's one "
                                           "package is moxi")
        script, target = CORE_SCRIPT
        if _target(self.scripts.get(script)) != target:
            have = self.scripts.get(script)
            self.add(here, 0, "7", f"declares the console script {script} = {have!r}; the "
                                   f"core declares {script} = {target!r}" if have else
                                   f"does not declare the console script {script} = "
                                   f"{target!r}")
        for other in sorted(set(self.scripts) - {script}):
            self.add(here, 0, "7", f"declares the console script {other!r}; the core "
                                   f"declares {script} alone")

    def check_names(self) -> None:
        here = "pyproject.toml"
        name = self.name
        if name is None:
            self.add(here, 0, "7", f"names the distribution {self.dist!r}; a module's "
                                   "distribution is moxi-<name>, the name lower-case ASCII "
                                   "letters and digits")
        elif name in names.VERBS:
            self.add(here, 0, "7", f"names the module {name!r}, one of the umbrella's own "
                                   "verbs; `moxi " + name + "` could never reach it")
        elif name in names.RESERVED:
            self.add(here, 0, "7", f"names the module {name!r}: {name!r} is a name moxi "
                                   f"keeps for itself, not a module: "
                                   f"{names.reserved_why(name)}; the module needs another "
                                   "name")
        if name and self.own_import not in self.src_tops:
            self.add(here, 0, "7", f"has no src/{self.own_import}: module {name!r} is "
                                   f"imported as {self.own_import}")
        for top in sorted(self.src_tops - {self.own_import}):
            self.add(f"src/{top}", 0, "7",
                     f"is an import package other than {self.own_import}" if name else
                     "is an import package not spelled moxi_<name>")
        # The entry point.
        if not self.entry_points:
            self.add(here, 0, "7", f"declares no [project.entry-points.\"{GROUP}\"] entry "
                                   "point naming the module's announcement")
        elif len(self.entry_points) > 1:
            self.add(here, 0, "7", f"declares {len(self.entry_points)} {GROUP} entry "
                                   "points; a module declares one, named by the module")
        else:
            (ep_name, target), = self.entry_points.items()
            if name and ep_name != name:
                self.add(here, 0, "7", f"names its {GROUP} entry point {ep_name!r}; it "
                                       f"is named by the module: {name!r}")
            if not isinstance(target, str) or not _DOTTED.fullmatch(target):
                self.add(here, 0, "7", f"points its {GROUP} entry point at {target!r}; it "
                                       "names the announcement module alone, with no "
                                       "attribute")
            elif target not in self.modules:
                self.add(here, 0, "7", f"points its {GROUP} entry point at {target}, which "
                                       "is no module under src/")
            elif self.own_import and target.split(".")[0] != self.own_import:
                self.add(here, 0, "7", f"points its {GROUP} entry point at {target}, "
                                       f"outside the module's package {self.own_import}")
        # Console scripts.
        for script in sorted(self.scripts):
            if script == CORE_SCRIPT[0]:
                # Only the core declares it. A module that did would put a second moxi
                # on PATH wherever it was installed on its own.
                self.add(here, 0, "7", f"declares the console script {script}; only the "
                                       f"core declares it, and a module is installed with "
                                       f"`moxi install {name or '<module>'}`")
                continue
            ok = (name is not None and script.startswith(f"moxi-{name}-")
                  and _SCRIPT_PART.fullmatch(script[len(f"moxi-{name}-"):]))
            if not ok:
                self.add(here, 0, "7", f"declares the console script {script!r}; a "
                                       f"module's scripts other than moxi are named "
                                       f"moxi-{name or '<module>'}-<part>")
        # The announcement.
        source = self.announcement_source
        if source is None:
            return
        for problem in self.announcement_problems:
            self.add(source.path, 0, "7", f"the announcement {problem}")
        self._check_announcement_values(source)
        self._check_cli_target(source)
        self._check_settings(source)
        self._check_skills(source)

    def _check_announcement_values(self, source: _Source) -> None:
        """The checks `moxi.modules.load` runs, over the literals read from the source."""
        try:
            _, problems = _check(self.announcement or {}, self.name)
        except _TooNew as exc:
            problems = [str(exc)]
        for problem in problems:
            self.add(source.path, 0, "7", f"the announcement {problem}")

    def _check_cli_target(self, source: _Source) -> None:
        target = self.ann("CLI")
        if not isinstance(target, str) or not _TARGET.fullmatch(target):
            return
        module, _, function = target.partition(":")
        where = source.path
        if module.split(".")[0] not in self.own:
            self.add(where, 0, "7", f"CLI names {target}, outside the module's package")
            return
        found = self.modules.get(module)
        defn = None
        if found is not None:
            for stmt in found.tree.body:
                if (isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and stmt.name == function):
                    defn = stmt
        if defn is None:
            self.add(where, 0, "7", f"CLI names {target}, and no top-level function "
                                    f"{function} is defined in {module}")
            return
        args = defn.args
        named = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
        by_keyword = {a.arg for a in args.args + args.kwonlyargs}
        missing = [n for n, ok in (("argv", "argv" in named),
                                   ("prog", "prog" in by_keyword)) if not ok]
        if missing:
            self.add(found.path, defn.lineno, "7",
                     f"the CLI target {function} lacks {' and '.join(missing)}; `moxi "
                     f"<module>` calls it as {function}(argv, prog=\"moxi <module>\")")

    def _check_settings(self, source: _Source) -> None:
        resource = self.ann("SETTINGS")
        if not isinstance(resource, str) or not resource:
            return
        package_dir = PurePosixPath(source.path).parent
        path = str(package_dir / resource)
        raw = self.tree.read(path)
        if raw is None:
            self.add(source.path, 0, "7", f"SETTINGS names {resource!r}, and {path} does "
                                          "not exist")
            return
        from moxi.config import ConfigError, Schema

        try:
            schema = Schema.parse(raw.decode("utf-8"), source=path)
            if self.name:
                schema.check_env(self.name)
        except UnicodeDecodeError as exc:
            self.add(path, 0, "7", f"the SETTINGS schema cannot be read ({exc})")
        except ConfigError as exc:
            self.add(path, 0, "7", f"the SETTINGS schema does not hold: {exc}")

    def _check_skills(self, source: _Source) -> None:
        """The skills in the `SKILLS` folder, read from the tree as the settle step lists
        them once installed: the folder beside the announcement, in its package, as
        `importlib.resources` finds it. A `SKILLS` the announcement check refuses is left
        to that check."""
        resource = self.ann("SKILLS")
        if not isinstance(resource, str) or not _relative(resource):
            return
        if "\\" in resource:
            # The settle step hands the value to importlib.resources as it is, and on POSIX
            # a backslash is part of a name there, not a separator.
            self.add(source.path, 0, "7", f"SKILLS names {resource!r}, which has a "
                                          "backslash; write the path with /, which "
                                          "resolves the same on every platform")
            return
        parts = [p for p in resource.split("/") if p not in ("", ".")]
        folder = str(PurePosixPath(source.path).parent.joinpath(*parts))
        if self._link_problem(folder):
            return
        listed = self.tree.folder(folder)
        if listed == "missing":
            self.add(source.path, 0, "7", f"SKILLS names {resource!r}, and {folder} does "
                                          "not exist")
            return
        if listed == "file":
            self.add(source.path, 0, "7", f"SKILLS names {resource!r}, and {folder} is a "
                                          "file, not a folder")
            return
        for name in listed:
            if not names.is_skill_folder(name):
                continue
            where = f"{folder}/{name}"
            if self._link_problem(where):
                continue
            try:
                names.check_skill_name(name)
            except ValueError as exc:
                self.add(where, 0, "7", f"is a skill folder, and {exc}")
            path = f"{where}/SKILL.md"
            if self._link_problem(path):
                continue
            raw = self.tree.read(path)
            if raw is None:
                self.add(where, 0, "7", "is a skill folder with no SKILL.md")
                continue
            line, said, problem = _skill_md_name(raw)
            if problem is not None:
                self.add(path, line, "7", f"{problem}, so it can't name the skill {name}")
            elif said != name:
                self.add(path, line, "7", f"names the skill {said!r}; its name: is the "
                                          f"folder's name, {name}")

    def _link_problem(self, path: str) -> bool:
        """Report the symlink on the way to `path` that the tree can't follow, if any."""
        problem = self.tree.link_problem(path)
        if problem is None:
            return False
        link, why = problem
        self.add(link, 0, "7", why)
        return True

    # -- the status line segment -------------------------------------------------------------

    def check_segment(self) -> None:
        segment = self.ann("SEGMENT")
        if not isinstance(segment, str) or segment.split(".")[0] not in self.own:
            return
        if segment not in self.modules:
            self.add(self.announcement_source.path, 0, "segment",
                     f"SEGMENT names {segment}, which is no module under src/")
            return
        forbidden = SEGMENT_IMPORTS | {"moxi_turns"}
        for module in sorted(self.closure([segment])):
            source = self.modules[module]
            for imp in source.imports:
                if imp.level:
                    continue
                top = imp.module.split(".")[0]
                if top in forbidden:
                    self.add(source.path, imp.line, "segment",
                             f"imports {imp.module} inside the closure of the segment "
                             f"{segment}; a status line segment shows status and triggers "
                             "nothing")
            for line, call in _OsNames(source.tree).process_calls():
                self.add(source.path, line, "segment",
                         f"calls {call} inside the closure of the segment {segment}; a "
                         "status line segment shows status and triggers nothing")


#: A module's list of the write sites it has before moving onto the helpers (check 8).
WRITE_SITES = "tests/write-sites.txt"

_WRITE_MODE = frozenset("wax+")
_WRITE_FLAGS = frozenset({"O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"})


def _mode_writes(node: ast.AST | None) -> bool | None:
    """True when a mode argument writes, False when it only reads, None when it can't be
    read (not a string literal)."""
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return bool(_WRITE_MODE & set(node.value))
    return None


#: The letters of an `open` mode; a path's `.open` reads its first argument as a mode only
#: when it is made of these alone.
_MODE_LETTERS = frozenset("rwxab+t")


def _path_mode_writes(node: ast.AST | None) -> bool:
    """True when a path's `.open` argument is a mode that writes: a string literal of mode
    letters alone with a writing letter among them. A URL or a member name is not a mode."""
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    letters = set(node.value)
    return bool(letters) and letters <= _MODE_LETTERS and bool(letters & _WRITE_MODE)


def _flags_write(node: ast.AST | None) -> bool:
    if node is None:
        return False
    named = {n.attr if isinstance(n, ast.Attribute) else n.id
             for n in ast.walk(node) if isinstance(n, (ast.Attribute, ast.Name))}
    if named & _WRITE_FLAGS:
        return True
    # A flags value the check can't read (a variable, a call) counts as a write.
    return not (named and all(n in ("os", "O_RDONLY") or n.startswith("O_") for n in named))


def _arg(call: ast.Call, index: int, keyword: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return call.args[index] if len(call.args) > index else None


def write_sites(tree: ast.Module) -> list[tuple[str, int]]:
    """Each `("<scope> <call>", line)` of a call in `tree` that writes a file itself.

    `<scope>` is the dotted name of the enclosing classes and functions, or `<module>`.
    """
    out: list[tuple[str, int]] = []

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, [*scope, child.name])
                continue
            if isinstance(child, ast.Call):
                call = _write_call(child)
                if call is not None:
                    out.append((f"{'.'.join(scope) or '<module>'} {call}", child.lineno))
            visit(child, scope)

    visit(tree, [])
    return out


def _write_call(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        if func.id == "open" and _mode_writes(_arg(call, 1, "mode")) is not False:
            return "open"
        return None
    if not isinstance(func, ast.Attribute):
        return None
    owner = func.value.id if isinstance(func.value, ast.Name) else None
    attr = func.attr
    if attr in ("write_text", "write_bytes"):
        return f".{attr}"
    if owner == "os" and attr == "replace":
        return "os.replace"
    if owner == "sqlite3" and attr == "connect":
        return "sqlite3.connect"
    if owner == "os" and attr == "open":
        return "os.open" if _flags_write(_arg(call, 1, "flags")) else None
    if owner in ("io", "os") and attr in ("open", "fdopen"):
        return f"{owner}.{attr}" if _mode_writes(_arg(call, 1, "mode")) is not False else None
    if attr == "open":
        # A path's open(mode): only a mode the check can read as writing counts, since
        # many other objects have an `open` (a browser, an archive) taking other things,
        # such as `webbrowser.open("https://...")` or `zf.open("data.txt")`.
        return ".open" if _path_mode_writes(_arg(call, 0, "mode")) else None
    return None


def _target(text: str | None) -> str | None:
    return None if text is None else re.sub(r"\s+", "", text)


def _key_text(node: ast.AST, consts: Mapping[str, str]) -> tuple[str | None, str]:
    """A key expression's text when it is a literal or a module-level string constant,
    and the literal text it starts with in any case."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, node.value
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id], consts[node.id]
    if isinstance(node, ast.JoinedStr):
        prefix = ""
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                prefix += part.value
            else:
                return None, prefix
        return prefix, prefix
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, left_prefix = _key_text(node.left, consts)
        if left is None:
            return None, left_prefix
        right, right_prefix = _key_text(node.right, consts)
        if right is None:
            return None, left + right_prefix
        return left + right, left + right
    return None, ""


class _OsNames:
    """How one file spells `os`, `os.environ`, `os.getenv` and `shutil.which`."""

    _PROCESS = ("system", "fork", "kill")

    def __init__(self, tree: ast.Module):
        self.tree = tree
        self.os: set[str] = set()
        self.environ: set[str] = set()
        self.getenv: set[str] = set()
        self.shutil: set[str] = set()
        self.which: set[str] = set()
        self.process: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    bound = alias.asname if alias.asname and "." not in alias.name else top
                    if alias.name == "os" or (top == "os" and not alias.asname):
                        self.os.add(bound)
                    elif alias.name == "shutil":
                        self.shutil.add(alias.asname or "shutil")
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if node.module == "os":
                        if alias.name in ("environ", "environb"):
                            self.environ.add(bound)
                        elif alias.name in ("getenv", "getenvb"):
                            self.getenv.add(bound)
                        elif self._is_process(alias.name):
                            self.process[bound] = f"os.{alias.name}"
                    elif node.module == "shutil" and alias.name == "which":
                        self.which.add(bound)

    @classmethod
    def _is_process(cls, attr: str) -> bool:
        return attr in cls._PROCESS or attr.startswith(("exec", "spawn"))

    def _is_os(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id in self.os

    def _is_environ(self, node: ast.AST) -> bool:
        return ((isinstance(node, ast.Attribute) and node.attr in ("environ", "environb")
                 and self._is_os(node.value))
                or (isinstance(node, ast.Name) and node.id in self.environ))

    def environment_reads(self) -> Iterable[tuple[int, ast.AST]]:
        """(line, key expression) for each keyed read of the environment."""
        if not (self.os or self.environ or self.getenv):
            return
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and node.args:
                func = node.func
                if ((isinstance(func, ast.Attribute)
                     and func.attr in ("get", "pop", "setdefault")
                     and self._is_environ(func.value))
                        or (isinstance(func, ast.Attribute)
                            and func.attr in ("getenv", "getenvb") and self._is_os(func.value))
                        or (isinstance(func, ast.Name) and func.id in self.getenv)):
                    yield node.lineno, node.args[0]
            elif (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
                  and self._is_environ(node.value)):
                yield node.lineno, node.slice
            elif isinstance(node, ast.Compare):
                for op, right in zip(node.ops, node.comparators, strict=True):
                    if isinstance(op, (ast.In, ast.NotIn)) and self._is_environ(right):
                        yield node.lineno, node.left

    def which_calls(self) -> Iterable[tuple[int, ast.AST]]:
        if not (self.shutil or self.which):
            return
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and node.args:
                func = node.func
                if ((isinstance(func, ast.Attribute) and func.attr == "which"
                     and isinstance(func.value, ast.Name) and func.value.id in self.shutil)
                        or (isinstance(func, ast.Name) and func.id in self.which)):
                    yield node.lineno, node.args[0]

    def process_calls(self) -> Iterable[tuple[int, str]]:
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Attribute) and self._is_os(func.value)
                    and self._is_process(func.attr)):
                yield node.lineno, f"os.{func.attr}"
            elif isinstance(func, ast.Name) and func.id in self.process:
                yield node.lineno, self.process[func.id]


# --- another module's announcement ---------------------------------------------------------

def _api_in(path: Path) -> tuple[bool, str | None] | None:
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None
    bound = _assignments(tree)
    if "API" not in bound:
        return None
    try:
        api = _literal(bound["API"])
    except (ValueError, TypeError, SyntaxError):
        return None
    if api is None or (isinstance(api, str) and _DOTTED.fullmatch(api)):
        return True, api
    return None


def _module_file(bases: Iterable[str | Path], dotted: str) -> Path | None:
    rest = dotted.split(".")[1:]
    for base in bases:
        if rest:
            candidates = [Path(base, *rest[:-1], rest[-1] + ".py"),
                          Path(base, *rest, "__init__.py")]
        else:
            candidates = [Path(base, "__init__.py")]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    return None


def _api_from_environment(dist: str) -> tuple[bool, str | None] | None:
    """Read a required module's announcement through this environment's metadata, finding
    its file without importing anything."""
    from importlib import metadata, util

    try:
        found = metadata.distribution(dist)
    except metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a damaged install reads as not found
        return None
    eps = [ep for ep in found.entry_points if ep.group == GROUP]
    if len(eps) != 1 or ":" in eps[0].value:
        return None
    target = eps[0].value
    try:
        spec = util.find_spec(target.split(".")[0])
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    path = _module_file(spec.submodule_search_locations, target)
    return _api_in(path) if path else None


def _api_from_checkout(repo: Path, dist: str) -> tuple[bool, str | None] | None:
    """Read a required module's announcement from its repo checked out at `repo`."""
    import tomllib

    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    project = data.get("project") or {}
    if _normalize(str(project.get("name", ""))) != dist:
        return None
    eps = (project.get("entry-points") or {}).get(GROUP) or {}
    if len(eps) != 1:
        return None
    target = next(iter(eps.values()))
    if not isinstance(target, str) or not _DOTTED.fullmatch(target):
        return None
    path = _module_file([repo / "src" / target.split(".")[0]], target)
    return _api_in(path) if path else None


#: What a package's tests import from here; these names keep their place.
__all__ = ["CheckError", "Finding", "check"]


if __name__ == "__main__":
    sys.exit(main(prog="python -m moxi_dev.contract"))

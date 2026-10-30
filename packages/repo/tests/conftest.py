"""Shared fixtures: real git in temp repos, and a demo module's pieces the git hooks run.

A git hook starts `python -I -m moxi_repo.run`, a process of its own that ignores
`PYTHONPATH`. So the tests build, once per session, a small virtual environment whose
site-packages pulls in this workspace's environment and a demo site holding the test
module `demo` (distribution `moxi-demo`, package `demo_pieces`). Repo's entries, shims
and launcher are pointed at that environment's Python, and the demo site is put on this
process's `sys.path` too, so `moxi.modules.extensions` finds the demo declarations here.

Each demo piece does what the environment variable `DEMO_<NAME>` says (`ok` by default;
`refuse`, `raise`, `notice`, `record`, `print`, `stage`, `none`, `input`, `slow`, `glob`),
and
appends one JSON line per run to the file `DEMO_LOG` names.

Git runs with its own global config in tmp (`GIT_CONFIG_GLOBAL`) and no system config, so
nothing here reads or writes the real ones.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from moxi_repo import gitconf

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32",
                                reason="POSIX sh git hooks; the Windows run is owed")

DEMO_INIT = '''
"""A module made for repo's tests: it declares git hook pieces."""

def _piece(event, fail, order, doc, **more):
    out = {"event": event, "target": "demo_pieces.hooks:" + event.replace("-", "_"),
           "fail": fail, "order": order, "doc": doc}
    out.update(more)
    return out

FIRST = dict(_piece("pre-commit", "closed", 10, "Runs first."),
             target="demo_pieces.hooks:first")
GATE = dict(_piece("pre-commit", "closed", 20, "A closed gate.",
                   settings={"refused-name": "multi", "label": "one"}),
            target="demo_pieces.hooks:gate")
LINT = dict(_piece("pre-commit", "open", 60, "An open check."),
            target="demo_pieces.hooks:lint")
ZZ_EARLY = dict(_piece("pre-commit", "open", 5, "Sorts last by id, runs first by order."),
                target="demo_pieces.hooks:zz_early")
MSG = dict(_piece("commit-msg", "closed", 50, "Reads the message."),
           target="demo_pieces.hooks:msg")
WATCH = dict(_piece("post-commit", "open", 50, "Watches commits."),
             target="demo_pieces.hooks:watch")
AFTER_MERGE = dict(_piece("post-merge", "open", 50, "Watches merges."),
                   target="demo_pieces.hooks:after_merge")
PUSH_CHECK = dict(_piece("pre-push", "closed", 50, "Checks a push."),
                  target="demo_pieces.hooks:push_check")
GHOST = _piece("pre-commit", "closed", 30, "Its target can't be imported.",
               target="demo_missing.nowhere:fn")
CAMEL = _piece("pre-commit", "open", 50, "A bad setting name.",
               settings={"refusedTrailer": "multi"})
RESERVED = _piece("pre-commit", "open", 50, "A setting named like a registration key.",
                  settings={"enabled": "one"})
'''

DEMO_HOOKS = '''
import json
import os

import moxi_repo


def _run(name, ctx):
    action = os.environ.get("DEMO_" + name.upper().replace("-", "_"), "ok")
    log = os.environ.get("DEMO_LOG")
    glob = None
    if action == "glob":
        glob = ctx.git("diff", "--cached", "--name-only", "--", "*.md").stdout.split("\\n")
        glob = sorted(x for x in glob if x)
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": "demo/" + name, "event": ctx.event, "args": list(ctx.args),
                "stdin": ctx.stdin.decode("utf-8", "replace"),
                "committer": ctx.committer, "settings": ctx.settings,
                "root": ctx.root, "updates": [u.local_ref for u in ctx.updates],
                "message": (open(ctx.message_path, encoding="utf-8").read()
                            if ctx.message_path else None),
                "glob": glob,
            }) + "\\n")
    if action == "refuse":
        return moxi_repo.Outcome.refuse(name + " says no", "a second line")
    if action == "raise":
        raise ValueError(name + " fell over")
    if action == "notice":
        return moxi_repo.Outcome.notice(name + " noticed something")
    if action == "record":
        return moxi_repo.Outcome.record(name + " recorded something")
    if action == "print":
        print(name + " printed this")
    if action == "stage":
        path = os.path.join(ctx.root, "staged-by-piece.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("added by a piece\\n")
        ctx.git("add", "staged-by-piece.txt")
    if action == "input":
        done = ctx.git("hash-object", "--stdin", input="hello\\n")
        print("hash " + done.stdout.strip())
    if action == "none":
        return None
    return moxi_repo.Outcome.ok()


def first(ctx): return _run("first", ctx)
def gate(ctx): return _run("gate", ctx)
def lint(ctx): return _run("lint", ctx)
def zz_early(ctx): return _run("zz-early", ctx)
def msg(ctx): return _run("msg", ctx)
def watch(ctx): return _run("watch", ctx)
def after_merge(ctx): return _run("after-merge", ctx)
def push_check(ctx): return _run("push-check", ctx)
'''

DEMO_COMMITTING = '''
"""A prepare step and trailers for the committer's tests, given as "pkg.mod:fn" targets.

`DEMO_DROP` names the paths to drop (comma-separated); `DEMO_PREPARE` set to `refuse`
refuses, `raise` raises, `edit` appends to each path it keeps. Each call appends one JSON
line to the file `DEMO_PREPARE_LOG` names.
"""

import json
import os

import moxi_repo

PATHSPEC_VARS = ("GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS",
                 "GIT_ICASE_PATHSPECS")


def prepare(ctx):
    action = os.environ.get("DEMO_PREPARE", "")
    drop = [p for p in os.environ.get("DEMO_DROP", "").split(",") if p]
    log = os.environ.get("DEMO_PREPARE_LOG")
    if log:
        seen = ctx.git("diff", "--cached", "--name-only", "--", "*.md").stdout.split("\\n")
        env = ctx.git("var", "GIT_EDITOR")  # any call: the wrapper logs its environment
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "changes": [[c.status, c.path, c.old_path, c.blob] for c in ctx.changes],
                "glob": sorted(x for x in seen if x), "spec": ctx.spec,
                "root": str(ctx.root)}) + "\\n")
    if action == "refuse":
        return moxi_repo.Prepared(refuse="demo prepare says no")
    if action == "raise":
        raise RuntimeError("demo prepare fell over")
    if action == "edit":
        for c in ctx.changes:
            if c.path not in drop and c.blob:
                with open(os.path.join(ctx.root, c.path), "a", encoding="utf-8") as fh:
                    fh.write("edited after prepare\\n")
    return moxi_repo.Prepared(drop=[(p, "demo says so") for p in drop])


def trailers(changes, since):
    return [("Demo-Paths", str(len(changes))), ("Demo-Since", "yes" if since else "no")]
'''

DEMO_ENTRIES = {
    "demo.first": "demo_pieces:FIRST",
    "demo.gate": "demo_pieces:GATE",
    "demo.lint": "demo_pieces:LINT",
    "demo.zz-early": "demo_pieces:ZZ_EARLY",
    "demo.msg": "demo_pieces:MSG",
    "demo.watch": "demo_pieces:WATCH",
    "demo.after-merge": "demo_pieces:AFTER_MERGE",
    "demo.push-check": "demo_pieces:PUSH_CHECK",
    "demo.ghost": "demo_pieces:GHOST",
    "demo.camel": "demo_pieces:CAMEL",
    "demo.reserved": "demo_pieces:RESERVED",
}


def _write_demo(site: Path) -> None:
    package = site / "demo_pieces"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(DEMO_INIT, encoding="utf-8")
    (package / "hooks.py").write_text(DEMO_HOOKS, encoding="utf-8")
    (package / "committing.py").write_text(DEMO_COMMITTING, encoding="utf-8")
    info = site / "moxi_demo-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: moxi-demo\nVersion: 0.1.0\n",
                                   encoding="utf-8")
    (info / "entry_points.txt").write_text(
        "[moxi.repo.git_hooks]\n" + "".join(f"{k} = {v}\n" for k, v in DEMO_ENTRIES.items()),
        encoding="utf-8")


def make_env(root: Path, *, site: Path | None, with_workspace: bool = True) -> Path:
    """A virtual environment at `root` whose Python sees this workspace's environment
    (unless `with_workspace` is False) and `site`; returns its Python."""
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    packages = root / "lib" / version / "site-packages"
    packages.mkdir(parents=True)
    (root / "bin").mkdir()
    base = Path(sys._base_executable).resolve()
    python = root / "bin" / "python"
    python.symlink_to(base)
    (root / "pyvenv.cfg").write_text(
        f"home = {base.parent}\ninclude-system-site-packages = false\n"
        f"version = {sys.version_info.major}.{sys.version_info.minor}."
        f"{sys.version_info.micro}\n", encoding="utf-8")
    lines = []
    if with_workspace:
        lines.append(f"import site; site.addsitedir({sysconfig.get_path('purelib')!r})")
    if site is not None:
        lines.append(str(site))
    (packages / "moxi-tests.pth").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return python


@pytest.fixture(scope="session")
def hook_env(tmp_path_factory) -> dict[str, Path]:
    """The demo site and the environments the git hooks run in."""
    if sys.platform == "win32":
        pytest.skip("the test environment is built the POSIX way; the Windows run is owed")
    root = tmp_path_factory.mktemp("hook-env")
    site = root / "demo-site"
    _write_demo(site)
    return {"site": site,
            "python": make_env(root / "env", site=site),
            "bare": make_env(root / "bare-env", site=site, with_workspace=False)}


@pytest.fixture
def demo(hook_env, tmp_path, monkeypatch) -> "Demo":
    """Git configured in tmp, the demo pieces declared, and entries that run them."""
    monkeypatch.syspath_prepend(str(hook_env["site"]))
    python = str(hook_env["python"])
    monkeypatch.setattr(gitconf, "interpreter", lambda: python)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tname = sample-bot\n\temail = bot@example.invalid\n"
                         "[init]\n\tdefaultBranch = main\n[advice]\n\tdetachedHead = false\n"
                         "[protocol \"file\"]\n\tallow = always\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    log = tmp_path / "demo.log"
    monkeypatch.setenv("DEMO_LOG", str(log))
    for name in list(os.environ):
        if name.startswith("DEMO_") and name != "DEMO_LOG":
            monkeypatch.delenv(name)
    yield Demo(tmp_path, log, monkeypatch, hook_env)
    for mod in [m for m in sys.modules if m.split(".")[0] == "demo_pieces"]:
        del sys.modules[mod]


class Demo:
    def __init__(self, tmp: Path, log: Path, monkeypatch, hook_env):
        self.tmp, self.log, self.mp, self.hook_env = tmp, log, monkeypatch, hook_env

    def repo(self, name: str = "alpha-notes", *, commit: bool = True) -> Path:
        path = self.tmp / name
        git(self.tmp, "init", "-q", name)
        if commit:
            (path / "README.md").write_text("hello\n", encoding="utf-8")
            git(path, "add", "README.md")
            git(path, "commit", "-q", "-m", "start")
        return path

    def act(self, **actions: str) -> None:
        """Set what each named demo piece does: act(gate="refuse")."""
        for name, action in actions.items():
            self.mp.setenv("DEMO_" + name.upper(), action)

    def runs(self) -> list[dict]:
        """Each demo piece run since the last call, in order."""
        if not self.log.exists():
            return []
        lines = [json.loads(line) for line in self.log.read_text(encoding="utf-8")
                 .splitlines() if line.strip()]
        self.log.unlink()
        return lines

    def ids(self) -> list[str]:
        return [run["id"] for run in self.runs()]


def git(cwd: Path, *args: str, input: str | None = None, check: bool = True,
        env: dict | None = None) -> subprocess.CompletedProcess:
    """Run git in `cwd` with this process's (fenced) environment plus `env`."""
    full = dict(os.environ)
    full.update(env or {})
    done = subprocess.run(["git", *args], cwd=cwd, input=input, capture_output=True,
                          text=True, env=full, timeout=60)
    if check and done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed ({done.returncode}):\n"
                             f"{done.stdout}{done.stderr}")
    return done


def change(repo: Path, name: str = "note.md", text: str = "a change\n") -> None:
    """Write a file and stage it."""
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)
    git(repo, "add", "--", name)


def commit(repo: Path, *flags: str, pre: tuple[str, ...] = (), message: str = "a commit",
           env: dict | None = None) -> subprocess.CompletedProcess:
    """`git <pre> commit -q <flags> -m <message>`; returns the finished process, whatever
    its status."""
    return git(repo, *pre, "commit", "-q", *flags, "-m", message, check=False, env=env)


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


# -- a site of fake distributions, for the optional modules repo reads ----------------------

class Site:
    """A directory on `sys.path` where a test installs fake distributions.

    The same shape as the core's own test site: while it is active, `importlib.metadata`
    and `moxi.modules.load` find only its distributions, plus `moxi`, `moxi-repo` and the
    demo pieces' distribution, so a module installed in the workspace can't stand in for
    the fake one, nor hide that it is absent.
    """

    def __init__(self, root: Path):
        self.root = root
        self.packages: set[str] = set()

    def write(self, relpath: str, text: str) -> Path:
        import textwrap

        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        top = relpath.split("/")[0].removesuffix(".py")
        if not top.endswith(".dist-info"):
            self.packages.add(top)
        return path

    def module(self, name: str, *, version: str = "0.1.0", package: str | None = None,
               **names: str) -> str:
        """A whole fake module: its distribution, package and announcement. Each keyword
        is an announcement name's Python expression. `package` names the fake's package
        (default `moxi_<name>`); give another name when the real module is installed in
        the workspace, so the fake never imports, or unloads, the real package."""
        package = package or f"moxi_{name}"
        fields = {"INTERFACES": "frozenset({1})", "API": "None",
                  "SUMMARY": "'A module made for a test.'", **names}
        self.write(f"{package}/__init__.py", "")
        self.write(f"{package}/module.py", "".join(f"{k} = {v}\n" for k, v in fields.items()))
        info = f"moxi_{name}-{version}.dist-info"
        self.write(f"{info}/METADATA",
                   f"Metadata-Version: 2.1\nName: moxi-{name}\nVersion: {version}\n")
        self.write(f"{info}/entry_points.txt", f"[moxi.modules]\n{name} = {package}.module\n")
        return package


def see_only(root: Path, keep: tuple[Path, ...], monkeypatch) -> None:
    """Let `importlib.metadata` and `moxi.modules.load` find only the distributions under
    `root`, `moxi`, `moxi-repo` and those under `keep`."""
    from importlib.metadata import Distribution

    import moxi.modules

    roots = {root, *keep}
    discover = Distribution.discover

    def seen(dist) -> bool:
        return (dist.name in ("moxi", "moxi-repo")
                or Path(dist.locate_file("")) in roots)

    def filtered(cls, **kwargs):
        return (d for d in discover(**kwargs) if seen(d))

    monkeypatch.setattr(Distribution, "discover", classmethod(filtered))
    find = moxi.modules._DistInfo.find.__func__

    def find_seen(cls, module):
        dist = find(cls, module)
        return dist if dist is not None and Path(dist.path).parent == root else None

    monkeypatch.setattr(moxi.modules._DistInfo, "find", classmethod(find_seen))


@pytest.fixture
def site(demo, tmp_path, monkeypatch):
    """A fresh site on `sys.path` for the test's fake distributions, beside the demo."""
    import moxi.modules

    root = tmp_path / "site"
    root.mkdir()
    see_only(root, (demo.hook_env["site"],), monkeypatch)
    monkeypatch.syspath_prepend(str(root))
    moxi.modules.reset()
    s = Site(root)
    yield s
    moxi.modules.reset()
    for mod in list(sys.modules):
        if mod.split(".")[0] in s.packages:
            del sys.modules[mod]


# -- the doctor ----------------------------------------------------------------------------------

def doctor(name: str | None = None) -> list:
    """The rows `moxi doctor repo --json` prints, as `moxi.doctor.Row`s; with `name`, only
    the rows whose name starts with it."""
    import contextlib
    import io

    import moxi.cli
    from moxi.doctor import Row

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = moxi.cli.main(["doctor", "repo", "--json"])
    rows = [Row(**row) for row in json.loads(out.getvalue())]
    assert code == (1 if any(r.state == "fail" for r in rows) else 0)
    return [r for r in rows if name is None or r.name.startswith(name)]


#: The rows repo's own checks give, and the core's records and setup version rows for
#: repo. The core's other rows (`stray`, `store ...`) are about the test's environment.
REPO_ROWS = ("git", "hooks ", "spec ", "uncovered ", "ledger ", "record ", "setup version")


def problems(name: str | None = None) -> list:
    """`doctor(name)`'s `warn` and `fail` rows of `REPO_ROWS`."""
    return [r for r in doctor(name) if r.state in ("warn", "fail")
            and r.name.startswith(REPO_ROWS)]

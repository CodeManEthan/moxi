"""loadout's two git hook pieces, as repo reads them from the `moxi.repo.git_hooks` group.

A commit (post-commit) or a merge (post-merge) that changes a path under the loadout
folder runs `moxi loadout install`, so what the user commits to the folder is placed for
each harness at once. Both pieces are open: one that breaks never stops git.

`run(ctx)`, the target of both, returns at once, with nothing done, unless:

- `reinstall_on_commit` is on (it is read at each run, so turning it off stops the
  pieces before an install removes them);
- `ctx.root` is the work tree that holds the folder; a linked worktree has a root of its
  own and is skipped, and so is a commit in any other repository;
- the commit (`HEAD^..HEAD`, or the empty tree to `HEAD` for a root commit) or the merge
  (`ORIG_HEAD..HEAD`) changed a path under the folder. There is no branch condition:
  install reads the folder as this work tree has it, on whatever branch.

The install runs as its own process, `python -m moxi_loadout install` in this
environment, with three minutes before it is stopped with everything it started. Rule:
the child install runs with git's local variables and its four pathspec variables
removed, so a `GIT_DIR` or `GIT_INDEX_FILE` the git hook was given never reaches the
placer's git calls or the would-commit check.

A failed install, or one that ran out of time, prints one line naming the command to run
(repo prints it on stderr), and leaves a doctor record, `loadout`'s `reinstall`, which the
next clean install clears.

At module level this imports `os` alone, and `moxi.proc` only once an install runs: the
git hook runner imports it to call a piece, and repo imports it to read the
declarations. A test holds that import under ten milliseconds.
"""

from __future__ import annotations

import os

REINSTALL_COMMIT = {
    "event": "post-commit",
    "target": "moxi_loadout.gitpiece:run",
    "fail": "open",
    "order": 50,
    "doc": "Run moxi loadout install when a commit changes the loadout folder.",
}

REINSTALL_MERGE = {
    "event": "post-merge",
    "target": "moxi_loadout.gitpiece:run",
    "fail": "open",
    "order": 50,
    "doc": "Run moxi loadout install when a merge or a pull changes the loadout folder.",
}

#: How long the child install may run, in seconds, before it is stopped.
INSTALL_SECONDS = 180.0
#: How long each of the piece's own git calls may take, in seconds.
GIT_SECONDS = 10.0
#: The command a person runs when the git hook's install failed.
COMMAND = "moxi loadout install"
#: The doctor record's module and key.
RECORD_MODULE = "loadout"
RECORD_KEY = "reinstall"


def run(ctx):
    """Both pieces' target. See the module's docs."""
    from moxi_loadout import repo_link

    link, why = repo_link.api()
    if link is None:
        # Only repo's runner calls this, so repo is there; one it can't load is a break.
        raise RuntimeError(why or "repo is not installed")
    if not ctx.root:
        return link.Outcome.ok()
    from moxi_loadout import settings

    machine = settings.read()
    if not machine.reinstall_on_commit:
        return link.Outcome.ok()
    folder = machine.source
    if not _holds(link, ctx.root, folder):
        return link.Outcome.ok()
    span = _span(link, ctx.root, ctx.event)
    if span is None:
        return link.Outcome.ok()
    # The path git reads keeps the folder's case as it is on disk: normcase lowercases on
    # Windows, and git's pathspec matches case exactly even where the file system doesn't.
    rel = os.path.relpath(os.path.realpath(folder), os.path.realpath(ctx.root))
    paths = () if rel == os.curdir else (rel,)
    if not link.changes(ctx.root, range=span, paths=paths, timeout=GIT_SECONDS):
        return link.Outcome.ok()
    failure = _install()
    if failure is None:
        return link.Outcome.ok()
    head = _head(link, ctx.root)
    what = "a merge" if ctx.event == "post-merge" else "a commit"
    text = (f"{ctx.event} git hook: {COMMAND} after {what}"
            + (f" ({head})" if head else "") + f" {failure}")
    line = f"the git hook's {COMMAND} {failure}; run: {COMMAND}"
    try:
        from moxi import doctor

        doctor.record(RECORD_MODULE, text, key=RECORD_KEY, fix=COMMAND)
    except Exception as exc:  # noqa: BLE001 - the line still says what to run
        line += f" (the doctor record couldn't be written: {exc})"
    return link.Outcome.notice(line)


def _real(path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _holds(link, root: str, folder) -> bool:
    """Whether `root` is the top of the work tree that holds `folder`."""
    real_root, real_folder = _real(root), _real(folder)
    if real_folder != real_root and not real_folder.startswith(
            real_root.rstrip(os.sep) + os.sep):
        return False
    if not os.path.isdir(folder):
        return False
    done = link.git(folder, "rev-parse", "--show-toplevel", timeout=GIT_SECONDS)
    top = done.stdout.strip()
    return done.returncode == 0 and bool(top) and _real(top) == real_root


def _span(link, root: str, event: str) -> tuple[str, str] | None:
    """The two commits whose difference the event brought, or None when there is none to
    read (a merge with no `ORIG_HEAD`, a commit with no `HEAD`)."""
    if event == "post-merge":
        before = _verify(link, root, "ORIG_HEAD")
        return None if before is None else (before, "HEAD")
    if _verify(link, root, "HEAD") is None:
        return None
    parent = _verify(link, root, "HEAD^")
    if parent is not None:
        return (parent, "HEAD")
    done = link.git(root, "hash-object", "-t", "tree", "--stdin", input=b"",
                    timeout=GIT_SECONDS)
    tree = done.stdout.strip()
    if done.returncode != 0 or not tree:
        raise RuntimeError(f"git can't give the empty tree: {done.stderr.strip()}")
    return (tree, "HEAD")


def _verify(link, root: str, rev: str) -> str | None:
    done = link.git(root, "rev-parse", "-q", "--verify", f"{rev}^{{commit}}",
                    timeout=GIT_SECONDS)
    found = done.stdout.strip()
    return found if done.returncode == 0 and found else None


def _head(link, root: str) -> str | None:
    import subprocess

    try:
        done = link.git(root, "rev-parse", "--short", "HEAD", timeout=GIT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def clean_env() -> dict[str, str]:
    """This process's environment less git's local variables and its four pathspec
    variables, which a git hook is given and the child install must not see."""
    from moxi_loadout import ignores

    return ignores.Git(timeout=GIT_SECONDS).env()


def _install() -> str | None:
    """Run the child install; None when it exited 0, else what went wrong, in words."""
    import subprocess

    from moxi import paths, proc

    argv = [os.fspath(paths.python()), "-m", "moxi_loadout", "install"]
    try:
        done = proc.run(argv, timeout=INSTALL_SECONDS, env=clean_env())
    except subprocess.TimeoutExpired:
        return f"took longer than {INSTALL_SECONDS:.0f} s and was stopped"
    except OSError as exc:
        return f"couldn't start ({exc})"
    if done.returncode == 0:
        return None
    last = _last_line(done.stderr) or _last_line(done.stdout)
    return f"exited {done.returncode}" + (f" ({last})" if last else "")


def _last_line(data) -> str:
    if not data:
        return ""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


__all__ = ["REINSTALL_COMMIT", "REINSTALL_MERGE", "INSTALL_SECONDS", "COMMAND",
           "RECORD_KEY", "run", "clean_env"]

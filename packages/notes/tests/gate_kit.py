"""Helpers for the judges' tests: made-up secrets, a notes repo with the gates, git runs.

Token-shaped strings are built at run time from short pieces and seeded random
characters, so no test file holds one whole.
"""

from __future__ import annotations

import os
import random
import string
import subprocess
from pathlib import Path

import moxi.config
import moxi.paths

from notes_kit import configure, home, repo

ALNUM = string.ascii_letters + string.digits
NAME = "quillon"


def token(seed: int = 1) -> str:
    r = random.Random(seed)
    return "".join(["gh", "p_", "".join(r.choice(ALNUM) for _ in range(36))])


def google_key(seed: int = 2) -> str:
    r = random.Random(seed)
    return "".join(["AI", "za", "".join(r.choice(ALNUM + "-_") for _ in range(35))])


def private_names(*words: str) -> None:
    moxi.config.set("secrets", "private_names.words", list(words), by="test")


def break_private_names() -> None:
    """A private-names list that won't load, so the set can't run."""
    path = Path(moxi.paths.Layout("secrets").config_dir()) / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[private_names]\nwords = ["quillfeather", 2024]\n', encoding="utf-8")


def sh(root: Path, *args: str, env: dict | None = None, check: bool = True,
       input: str | None = None) -> subprocess.CompletedProcess:
    full = dict(os.environ)
    full.update(env or {})
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          env=full, timeout=60, input=input)
    if check and done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} exited {done.returncode}:\n"
                             f"{done.stdout}{done.stderr}")
    return done


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def tree(root: Path, rev: str = "HEAD") -> set[str]:
    return set(sh(root, "ls-tree", "-r", "--name-only", rev).stdout.splitlines())


def staged(root: Path) -> set[str]:
    return set(sh(root, "diff", "--cached", "--name-only").stdout.splitlines())


def show(root: Path, spec: str) -> str:
    return sh(root, "show", spec).stdout


def index_tree(root: Path) -> str:
    return sh(root, "write-tree").stdout.strip()


def notes_repo(*pieces: str) -> Path:
    """`~/notes` as a repo with one commit, the snapshot's interval at zero and the
    named git hook pieces registered."""
    from moxi_repo import api

    root = repo(home() / "notes", commit=True)
    configure(snapshot__interval=0)
    if pieces:
        api.add(root, *pieces)
    return root


def snapshot(root: Path):
    """One snapshot pass with notes' spec, saved first."""
    from moxi_repo.api import commit_saved

    from moxi_notes import spec

    spec.ensure_saved(spec.build())
    return commit_saved(root, spec.NAME)


def allow_line(path: str, rule: str, match: str = "*") -> str:
    return "\t".join([path, rule, match, "2026-11-01", "made up for a test"]) + "\n"

"""Shared fixtures: real git repos copied from one template, and config in the test's home.

Git runs with its own global config in tmp (`GIT_CONFIG_GLOBAL`) and no system config, so
nothing here reads or writes the real ones. The git hooks repo writes run this
environment's Python, where publishing is installed, so the pieces run for real.

Credential-shaped values come from `token(seed)`, built from parts with a seeded
generator, so no test file holds one whole.
"""

from __future__ import annotations

import os
import random
import shutil
import string
import subprocess
from pathlib import Path

import pytest

GITCONFIG = ("[user]\n\tname = operator\n\temail = operator@example.test\n"
             "[init]\n\tdefaultBranch = main\n[advice]\n\tdetachedHead = false\n"
             "[protocol \"file\"]\n\tallow = always\n")


def git(cwd: Path, *args: str, check: bool = True, env: dict | None = None,
        input: str | None = None) -> subprocess.CompletedProcess:
    """Run git in `cwd` with this process's (fenced) environment plus `env`."""
    full = dict(os.environ)
    full.update(env or {})
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env=full, timeout=60, input=input)
    if check and done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed ({done.returncode}):\n"
                             f"{done.stdout}{done.stderr}")
    return done


def token(seed: int = 7) -> str:
    """A made-up GitHub token: the shape secrets' credentials set finds."""
    r = random.Random(seed)
    return "gh" + "p_" + "".join(r.choice(string.ascii_letters + string.digits)
                                 for _ in range(36))


@pytest.fixture(scope="session")
def template(tmp_path_factory) -> Path:
    """A repo with one commit, made once and copied into each test that wants one."""
    where = tmp_path_factory.mktemp("template")
    config = where / "gitconfig"
    config.write_text(GITCONFIG, encoding="utf-8")
    env = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_GLOBAL": str(config),
           "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(where)}
    repo = where / "proj-a"
    for args in (("init", "-q", str(repo)),):
        subprocess.run(["git", *args], check=True, env=env, capture_output=True)
    (repo / "README.md").write_text("proj-a\n", encoding="utf-8")
    for args in (("add", "README.md"), ("commit", "-q", "-m", "start")):
        subprocess.run(["git", *args], cwd=repo, check=True, env=env, capture_output=True)
    return repo


@pytest.fixture
def gitenv(tmp_path, monkeypatch) -> Path:
    """Git's global config in the test's tmp."""
    config = tmp_path / "gitconfig"
    config.write_text(GITCONFIG, encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return config


@pytest.fixture
def repo(template, gitenv, tmp_path) -> Path:
    """A copy of the template repo, `proj-a`, in the test's tmp."""
    dest = tmp_path / "proj-a"
    shutil.copytree(template, dest, symlinks=True)
    return dest


def write_config(module: str, text: str) -> Path:
    """Write module `module`'s config file in the test's home."""
    from moxi import paths

    path = paths.Layout(module).config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def names() -> Path:
    """secrets' private names list, with made-up names."""
    return write_config("secrets", '[private_names]\nwords = ["bobsecret"]\n'
                                   'emails = ["bob@private.example.test"]\n'
                                   '[author]\nname = "operator"\n'
                                   'email = "operator@example.test"\n')


def commit(repo: Path, message: str, *, name: str = "note.md", text: str = "a line\n",
           flags: tuple[str, ...] = (), env: dict | None = None
           ) -> subprocess.CompletedProcess:
    """Append `text` to `name`, stage it and commit with `message` (from a file, so every
    line reaches the git hook as written); returns the finished process."""
    path = repo / name
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)
    git(repo, "add", "--", name)
    msg = repo.parent / "message.txt"
    msg.write_text(message, encoding="utf-8")
    return git(repo, "commit", "-q", *flags, "-F", str(msg), check=False, env=env)


def last_message(repo: Path) -> str:
    """HEAD's message as committed (`%B` less the newline `log` adds)."""
    return git(repo, "log", "-1", "--format=%B").stdout.removesuffix("\n")


def public_repo(repo: Path, *pieces: str, sets: tuple[str, ...] = ()) -> Path:
    """Mark `repo` public and register only `pieces`, as a test of one piece wants."""
    from moxi_repo import api

    git(repo, "config", "moxi-publishing.public", "true")
    api.add(repo, *pieces, sets=list(sets))
    return repo

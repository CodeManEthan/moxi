"""Helpers the notes tests share: notes' settings, a git identity, repos and projects.

Every test runs inside the test kit's fence, which points `HOME` into the test's tmp. A
git call here gets an identity and a global config file of its own in tmp, so no test
reads the real global git config.
"""

from __future__ import annotations

import io
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import moxi.cli
import moxi.config


def home() -> Path:
    return Path.home()


def configure(**values) -> None:
    """Set notes' settings, by key (dots spelled `__`)."""
    for key, value in values.items():
        moxi.config.set("notes", key.replace("__", "."), value, by="test")


def git_env(tmp_path: Path, monkeypatch) -> None:
    """A git identity and a global config file of the test's own."""
    config = tmp_path / "gitconfig"
    config.write_text("[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "sample-bot")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "bot@example.invalid")


def git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=30, check=True)
    return done.stdout


def repo(path: Path, *, commit: bool = False) -> Path:
    """A git repo at `path`, with one commit when `commit`."""
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", str(path))
    if commit:
        (path / "README.md").write_text("alpha\n", encoding="utf-8")
        git("add", "README.md", cwd=path)
        git("commit", "-q", "-m", "first", cwd=path)
    return path


def moxi_cmd(*argv: str) -> tuple[int, str, str]:
    """Run `moxi <argv>` in process: its exit code, stdout and stderr."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = moxi.cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def add_project(key: str, root: Path, *extra: str) -> None:
    code, out, err = moxi_cmd("projects", "add", key, "--root", str(root), *extra)
    assert code == 0, out + err


def projects_store() -> Path:
    import moxi.paths

    return Path(moxi.paths.Layout("projects").data_dir()) / "projects.db"


def no_projects(monkeypatch) -> None:
    """Make projects look not installed."""
    import moxi.modules

    real = moxi.modules.available
    monkeypatch.setattr(moxi.modules, "available",
                        lambda name: False if name == "projects" else real(name))

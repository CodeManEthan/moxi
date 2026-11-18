"""Codex folder trust settled on the launch's own argv, in temporary git trees with a
temporary `CODEX_HOME`: the trust root as Codex finds it (no repository, the root, a
subfolder, a linked worktree, a submodule, a symlinked folder), the user's entry on the
folder or on its root, `trusted` or `untrusted`, and `trust_cwd` on and off, each in
interactive, host and exec mode. Case 1 and case 3 pass `trusted` for the launch folder
with the `trust_cwd` row's spelling; case 2 passes nothing. Codex's config is read and
never written."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from moxi_launcher import catalog, codex_trust, compose
from moxi_launcher.compose import codex

HOST = "/env/bin/moxi-turns-codex-host"
MODES = ("interactive", "host", "exec")

pytestmark = pytest.mark.touches("packages/launcher/src/moxi_launcher/codex_trust.py",
                                 "packages/launcher/src/moxi_launcher/compose/codex.py")


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                        "HOME": str(cwd), "PATH": "/usr/bin:/bin",
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})


@pytest.fixture
def tree(tmp_path) -> Path:
    """`repo` (a git repository with a commit and a subfolder), `repo-wt` (its linked
    worktree), `plain` (no repository), `link` (a symlink to `repo/sub`) and `codex-home`."""
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "sub" / "f.txt").write_text("x\n")
    git("init", "-q", cwd=repo)
    git("add", ".", cwd=repo)
    git("commit", "-q", "-m", "one", cwd=repo)
    git("worktree", "add", "-q", str(tmp_path / "repo-wt"), cwd=repo)
    (tmp_path / "plain").mkdir()
    (tmp_path / "link").symlink_to(repo / "sub")
    (tmp_path / "codex-home").mkdir()
    return tmp_path


def write_config(tree: Path, projects: dict[str, str]) -> Path:
    path = tree / "codex-home" / "config.toml"
    lines = ['model = "gpt-5"', ""]
    for key, level in projects.items():
        lines += [f"[projects.{json.dumps(key)}]", f'trust_level = "{level}"', ""]
    path.write_text("\n".join(lines))
    return path


def env_of(tree: Path) -> dict[str, str]:
    return {"CODEX_HOME": str(tree / "codex-home"), "HOME": str(tree)}


def composed(tree: Path, cwd: Path, mode: str, *, trust_cwd: bool = False):
    target = "headless" if mode == "exec" else "terminal"
    values = {"harness": "codex", "cwd": str(cwd), "target": target, "trust_cwd": trust_cwd}
    links = frozenset({"turns"}) if mode == "host" else frozenset()
    ctx = compose.context(values, request_id="r1", modules=links, env=env_of(tree),
                          mode=mode)
    ctx = dataclasses.replace(ctx, host=HOST, brief="fix the build")
    return codex.compose(values, ctx)


def trust_item(mode: str, cwd: Path) -> list[str]:
    if mode == "host":
        return ["--option", f'projects.{json.dumps(str(cwd))}.trust_level="trusted"']
    return ["-c", f'projects={{{json.dumps(str(cwd))}={{trust_level="trusted"}}}}']


def trust_items(argv) -> list[str]:
    """Each item on `argv` that passes a trust level, with the flag before it."""
    argv = list(argv)
    found: list[str] = []
    for i, token in enumerate(argv):
        if "trust_level" in token:
            found += [argv[i - 1], token]
    return found


# (where the launch runs, the user's entries by key, trust_cwd, the case)
CASES = [
    ("plain", {}, False, codex_trust.NO_ENTRY),
    ("plain", {}, True, codex_trust.ON),
    ("plain", {"plain": "untrusted"}, False, codex_trust.USERS),
    ("plain", {"plain": "untrusted"}, True, codex_trust.ON),
    ("repo", {}, False, codex_trust.NO_ENTRY),
    ("repo", {"repo": "trusted"}, False, codex_trust.USERS),
    ("repo/sub", {}, False, codex_trust.NO_ENTRY),
    ("repo/sub", {"repo": "untrusted"}, False, codex_trust.USERS),
    ("repo/sub", {"repo/sub": "trusted"}, False, codex_trust.USERS),
    ("repo/sub", {"plain": "trusted"}, False, codex_trust.NO_ENTRY),
    ("repo-wt", {}, False, codex_trust.NO_ENTRY),
    ("repo-wt", {"repo": "untrusted"}, False, codex_trust.USERS),
    ("repo-wt", {"repo-wt": "trusted"}, False, codex_trust.USERS),
    ("link", {"repo/sub": "trusted"}, False, codex_trust.USERS),
    ("link", {"repo": "untrusted"}, False, codex_trust.USERS),
    ("link", {}, False, codex_trust.NO_ENTRY),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("where, entries, trust_cwd, case", CASES)
def test_each_case_in_each_mode(tree, mode, where, entries, trust_cwd, case):
    config = write_config(tree, {str((tree / key).resolve()): level
                                 for key, level in entries.items()})
    before = hashlib.sha256(config.read_bytes()).hexdigest()
    cwd = tree / where
    got = composed(tree, cwd, mode, trust_cwd=trust_cwd)
    assert got.mode == mode
    trust = got.values[codex.TRUST]
    assert trust["case"] == case
    if case == codex_trust.USERS:
        assert trust["level"] is None and trust_items(got.argv) == []
    else:
        assert trust["level"] == "trusted"
        assert trust_items(got.argv) == trust_item(mode, cwd)
    assert hashlib.sha256(config.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("mode", MODES)
def test_with_no_codex_config_the_launch_runs_trusted(tree, mode):
    got = composed(tree, tree / "repo", mode)
    assert got.values[codex.TRUST]["case"] == codex_trust.NO_ENTRY
    assert "does not exist" in got.values[codex.TRUST]["line"]
    assert trust_items(got.argv) == trust_item(mode, tree / "repo")
    assert not (tree / "codex-home" / "config.toml").exists()


def test_an_entry_with_no_level_decides_before_the_root(tree):
    path = tree / "codex-home" / "config.toml"
    sub = (tree / "repo" / "sub").resolve()
    path.write_text(f'[projects.{json.dumps(str(sub))}]\nother = 1\n\n'
                    f'[projects.{json.dumps(str((tree / "repo").resolve()))}]\n'
                    'trust_level = "untrusted"\n')
    got = composed(tree, tree / "repo" / "sub", "interactive")
    assert got.values[codex.TRUST]["case"] == codex_trust.NO_ENTRY


def test_a_config_that_does_not_parse_counts_as_no_entry_and_says_so(tree):
    (tree / "codex-home" / "config.toml").write_text("[projects\n")
    got = composed(tree, tree / "repo", "interactive")
    trust = got.values[codex.TRUST]
    assert trust["case"] == codex_trust.NO_ENTRY and "can't be read" in trust["line"]


def test_codex_home_defaults_to_codex_under_home(tree):
    assert codex_trust.config_file({"HOME": str(tree)}) == tree / ".codex" / "config.toml"


def test_the_trust_root_follows_codex(tree):
    repo = (tree / "repo").resolve()
    assert codex_trust.trust_root(tree / "repo" / "sub") == repo
    assert codex_trust.trust_root(tree / "repo-wt") == repo
    assert codex_trust.trust_root(tree / "link") == repo
    assert codex_trust.trust_root(tree / "plain") is None
    sub = tree / "plain" / "module"
    sub.mkdir()
    (sub / ".git").write_text("gitdir: ../../repo/.git/modules/module\n")
    assert codex_trust.trust_root(sub) is None


def test_the_preview_and_the_record_carry_the_line(tree):
    got = composed(tree, tree / "repo", "interactive")
    line = got.values[codex.TRUST]["line"]
    assert "runs trusted for this launch only, written nowhere" in line
    assert codex.TRUST not in {row.name for row in catalog.load("codex")}

"""`moxi repo git-hooks`: what it prints and the exit codes it gives."""

from __future__ import annotations

import json
import os
import subprocess
import sysconfig
from pathlib import Path

import pytest

from moxi_repo import cli, gitconf, shim
from tests.conftest import POSIX_ONLY, git

pytestmark = POSIX_ONLY


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_git_hooks_with_no_verb_lists(demo, capsys):
    code, out, _ = run(capsys, "git-hooks")
    assert code == 0 and "no repo has git hook pieces yet" in out
    repo = demo.repo()
    run(capsys, "git-hooks", "add", str(repo), "demo/gate", "demo/watch",
        "--set", "label=x")
    root = gitconf.resolve(repo).root
    code, out, _ = run(capsys, "git-hooks")
    assert code == 0
    assert out.splitlines()[0].startswith(f"{root}  git ")
    assert "    20 demo/gate  closed  enabled  settings: label=x" in out
    assert "    50 demo/watch  open  enabled" in out
    assert run(capsys, "git-hooks", str(repo))[1] == out
    assert run(capsys, "git-hooks", "list", str(repo))[1] == out


def test_the_json_forms(demo, capsys):
    repo = demo.repo()
    run(capsys, "git-hooks", "add", str(repo), "demo/gate")
    for argv in (["git-hooks", "--json"], ["git-hooks", "list", "--json"],
                 ["git-hooks", "list", str(repo), "--json"]):
        code, out, _ = run(capsys, *argv)
        [listed] = json.loads(out)
        assert code == 0 and [p["id"] for p in listed["pieces"]] == ["demo/gate"]
        assert listed["events"]["pre-commit"]["via"] in ("config entry", "shim")
    code, out, _ = run(capsys, "git-hooks", "available", "--json")
    found = json.loads(out)
    assert code == 0
    assert {p["id"] for p in found["pieces"]} >= {"demo/gate", "demo/watch", "demo/msg"}
    assert set(found["refused"]) == {"demo/camel", "demo/reserved"}
    code, out, _ = run(capsys, "git-hooks", "available")
    assert code == 0 and "demo/gate  pre-commit, closed, order 20" in out
    assert "demo/camel  refused: " in out


def test_exit_codes(demo, capsys):
    repo = demo.repo()
    code, _, err = run(capsys, "git-hooks", "add", str(repo), "demo/nope")
    assert code == 2 and err.startswith("repo: no installed module declares the piece demo/nope")
    code, _, err = run(capsys, "git-hooks", "add", str(demo.tmp / "nowhere"), "demo/gate")
    assert code == 2 and "no git work tree" in err
    code, _, err = run(capsys, "git-hooks", "add", str(repo), "demo/camel")
    assert code == 3 and "demo/camel's declaration is refused" in err
    code, _, err = run(capsys, "git-hooks", "remove", str(repo), "demo/gate")
    assert code == 2 and "demo/gate is not registered" in err
    code, _, err = run(capsys, "git-hooks", "disable", str(repo), "demo/gate")
    assert code == 2
    hooks = repo / ".git" / "hooks"
    (hooks / "pre-commit.local").write_text("#!/bin/sh\n", encoding="utf-8")
    code, _, err = run(capsys, "git-hooks", "add", str(repo), "demo/gate", "--mode", "shim")
    assert code == 3 and "nothing was written" in err and "pre-commit.local" in err
    assert gitconf.read(gitconf.resolve(repo)).sections == set()
    (hooks / "pre-commit.local").unlink()
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 34, 1))
    code, _, err = run(capsys, "git-hooks", "add", str(repo), "demo/gate")
    assert code == 3
    assert "git 2.34.1 is older than 2.39.0, the oldest repo's git hooks support" in err
    assert gitconf.read(gitconf.resolve(repo)).sections == set()
    code, _, err = run(capsys, "git-hooks", "refresh")
    assert code == 3
    with pytest.raises(SystemExit) as stopped:
        run(capsys, "git-hooks", "add", str(repo), "demo/gate", "--mode", "sideways")
    assert stopped.value.code == 2


def test_add_prints_the_chain_line_where_one_is_needed(demo, capsys):
    repo = demo.repo()
    git(repo, "config", "core.hooksPath", ".githooks")
    code, out, _ = run(capsys, "git-hooks", "add", str(repo), "demo/gate", "--mode", "shim")
    assert code == 0
    hook = gitconf.resolve(repo).hooks_dir / "pre-commit"
    assert (f"{gitconf.resolve(repo).root}: pre-commit: chain line needed. Add this line to "
            f"{hook}, before any line that reads stdin:\n  "
            f"{shim.chain_line('pre-commit', False)}\n") in out
    code, out, _ = run(capsys, "git-hooks", str(repo))
    assert "chain line needed in" in out
    hook.parent.mkdir()
    hook.write_text(f"#!/bin/sh\n{shim.chain_line('pre-commit', False)}\n", encoding="utf-8")
    code, out, _ = run(capsys, "git-hooks", "refresh", str(repo))
    assert code == 0 and f"pre-commit: the chain line is in {hook}" in out
    assert "chain line present" in run(capsys, "git-hooks", str(repo))[1]


def test_the_moxi_command_reaches_git_hooks(demo):
    moxi = Path(sysconfig.get_path("scripts")) / "moxi"
    env = dict(os.environ, PYTHONPATH=str(demo.hook_env["site"]))
    repo = demo.repo()

    def moxi_repo(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(moxi), "repo", *argv], capture_output=True, text=True,
                              env=env, timeout=60)

    done = moxi_repo("git-hooks", "available", "--json")
    assert done.returncode == 0, done.stderr
    assert "demo/gate" in {p["id"] for p in json.loads(done.stdout)["pieces"]}
    done = moxi_repo("git-hooks", "add", str(repo), "demo/nope")
    assert done.returncode == 2 and "demo/nope" in done.stderr
    done = moxi_repo("git-hooks", "add", str(repo), "demo/camel")
    assert done.returncode == 3


def test_commit_takes_a_path_inside_the_repo(demo, capsys):
    import moxi_repo

    repo = demo.repo()
    moxi_repo.save_spec(moxi_repo.Spec(name="demo/snap", root=repo, paths=("notes",),
                                       author=("snap-bot", "snap@example.invalid")))
    (repo / "notes").mkdir()
    (repo / "notes" / "a.md").write_text("a line\n", encoding="utf-8")
    code, out, err = run(capsys, "commit", str(repo / "notes"))
    assert code == 0, err
    assert "repo: demo/snap committed" in out

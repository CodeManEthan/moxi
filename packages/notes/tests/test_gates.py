"""The pre-commit and push gates, run by real git through repo's git hook runner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from moxi_notes import gates, judge

from gate_kit import (allow_line, break_private_names, index_tree, notes_repo,
                      private_names, sh, staged, token, tree, write)
from notes_kit import configure, git_env

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="git hooks through POSIX sh; the Windows run is owed")

GATE, PUSH = "notes/secret-gate", "notes/push-gate"
SECRET = "key = {}\n"


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)


@pytest.fixture
def root() -> Path:
    return notes_repo(GATE)


def secret(seed: int = 1) -> str:
    return SECRET.format(token(seed))


def commit(root: Path, *flags: str, env: dict | None = None, message: str = "hand"):
    return sh(root, "commit", "-q", "-m", message, *flags, env=env, check=False)


# -- hand commits under leave-out ---------------------------------------------------------

def test_a_plain_commit_leaves_the_hit_out_and_commits_the_rest(root):
    write(root, "plan.md", "intro\n" + secret())
    write(root, "clean.md", "fine\n")
    sh(root, "add", "plan.md", "clean.md")
    done = commit(root)
    assert done.returncode == 0, done.stderr
    assert "clean.md" in tree(root) and "plan.md" not in tree(root)
    assert ("notes: left out plan.md (credentials/github-pat line 2); remove it or add an "
            "allowance") in done.stderr
    assert token() not in done.stderr
    assert "plan.md" not in staged(root)


def test_a_pathspec_commit_leaves_the_hit_out_and_keeps_it_staged(root):
    write(root, "plan.md", secret())
    write(root, "clean.md", "fine\n")
    sh(root, "add", "plan.md", "clean.md")
    done = commit(root, "--", "plan.md", "clean.md")
    assert done.returncode == 0, done.stderr
    assert "clean.md" in tree(root) and "plan.md" not in tree(root)
    assert "plan.md" in staged(root)


def test_an_all_commit_leaves_the_hit_out(root):
    write(root, "plan.md", "fine\n")
    write(root, "clean.md", "fine\n")
    sh(root, "add", "plan.md", "clean.md")
    assert commit(root).returncode == 0
    write(root, "plan.md", "fine\n" + secret())
    write(root, "clean.md", "fine, still\n")
    done = commit(root, "-a")
    assert done.returncode == 0, done.stderr
    assert sh(root, "show", "HEAD:plan.md").stdout == "fine\n"
    assert sh(root, "show", "HEAD:clean.md").stdout == "fine, still\n"


def test_the_deletions_are_held_when_an_added_file_is_left_out(root):
    write(root, "old.md", "".join(f"line {i}\n" for i in range(20)))
    sh(root, "add", "old.md")
    assert commit(root).returncode == 0
    sh(root, "mv", "old.md", "new.md")
    with open(root / "new.md", "a", encoding="utf-8") as fh:
        fh.write(secret())
    write(root, "clean.md", "fine\n")
    sh(root, "add", "new.md", "clean.md")
    done = commit(root)
    assert done.returncode == 0, done.stderr
    assert {"old.md", "clean.md"} <= tree(root) and "new.md" not in tree(root)
    assert "held back the deletion of old.md" in done.stderr


def test_a_commit_with_nothing_left_is_refused_and_the_index_kept(root):
    write(root, "plan.md", secret())
    sh(root, "add", "plan.md")
    before = index_tree(root)
    done = commit(root)
    assert done.returncode != 0
    assert "every staged file holds a secret; nothing left to commit" in done.stderr
    assert "  plan.md:1 credentials/github-pat" in done.stderr
    assert index_tree(root) == before


def test_an_amend_leaves_the_file_as_the_amended_commit_had_it(root):
    write(root, "plan.md", "first\n")
    write(root, "clean.md", "first\n")
    sh(root, "add", "plan.md", "clean.md")
    assert commit(root).returncode == 0
    write(root, "plan.md", "first\n" + secret())
    write(root, "clean.md", "second\n")
    sh(root, "add", "plan.md", "clean.md")
    done = commit(root, "--amend")
    assert done.returncode == 0, done.stderr
    assert sh(root, "show", "HEAD:plan.md").stdout == "first\n"
    assert sh(root, "show", "HEAD:clean.md").stdout == "second\n"
    assert sh(root, "rev-list", "--count", "HEAD").stdout.strip() == "2"


@pytest.mark.parametrize("var", ["GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS",
                                 "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"])
def test_the_users_pathspec_variables_change_nothing(root, var):
    write(root, "a[1].md", secret())
    write(root, "a1.md", "fine\n")
    write(root, "A[1].md", "fine\n")
    sh(root, "add", "a[1].md", "a1.md", "A[1].md")
    done = commit(root, env={var: "1"})
    assert done.returncode == 0, done.stderr
    assert tree(root) >= {"a1.md", "A[1].md"} and "a[1].md" not in tree(root)


def test_a_blob_over_max_mb_is_left_out(root):
    configure(max_mb=1)
    write(root, "big.md", "x" * (1024 * 1024 + 10))
    write(root, "clean.md", "fine\n")
    sh(root, "add", "big.md", "clean.md")
    done = commit(root)
    assert done.returncode == 0, done.stderr
    assert "left out big.md (notes/too-large" in done.stderr
    assert "big.md" not in tree(root)


def test_a_malformed_allowlist_is_judged_as_empty_and_said(root):
    write(root, ".secrets-allow", allow_line("plan.md", "credentials/github-pat") + "bad\n")
    write(root, "plan.md", secret())
    sh(root, "add", ".secrets-allow", "plan.md")
    done = commit(root)
    assert done.returncode == 0, done.stderr
    assert "notes: judged with no allowances:" in done.stderr
    assert ".secrets-allow: line 2:" in done.stderr
    assert "plan.md" not in tree(root) and ".secrets-allow" in tree(root)


def test_an_allowance_keeps_the_hit(root):
    write(root, ".secrets-allow", allow_line("plan.md", "credentials/github-pat"))
    write(root, "plan.md", secret())
    sh(root, "add", ".secrets-allow", "plan.md")
    done = commit(root)
    assert done.returncode == 0 and done.stderr == ""
    assert "plan.md" in tree(root)


def test_a_set_that_cant_run_refuses(root):
    break_private_names()
    write(root, "clean.md", "fine\n")
    sh(root, "add", "clean.md")
    done = commit(root)
    assert done.returncode != 0 and "a set notes judges with can't run" in done.stderr


def test_a_clean_commit_prints_nothing(root):
    write(root, "clean.md", "fine\n")
    sh(root, "add", "clean.md")
    done = commit(root)
    assert done.returncode == 0 and done.stderr == ""


def test_an_unstage_that_does_not_take_refuses(root, monkeypatch):
    write(root, "plan.md", secret())
    write(root, "clean.md", "fine\n")
    sh(root, "add", "plan.md", "clean.md")
    monkeypatch.chdir(root)
    monkeypatch.setattr(gates, "_unstage_args",
                        lambda paths: (("reset", "-q", "--pathspec-from-file=-",
                                        "--pathspec-file-nul"), b":(literal)nothing.md"))
    from moxi_repo.api import HookContext

    out = gates.pre_commit(HookContext("pre-commit"))
    assert out.kind == "refuse" and "could not be left out" in out.lines[0]
    assert "  plan.md is still staged" in out.lines


# -- refusals -----------------------------------------------------------------------------

def test_a_hand_commit_under_redact_is_refused_with_the_fixes(root):
    configure(action="redact")
    write(root, "plan.md", secret())
    write(root, "clean.md", "fine\n")
    sh(root, "add", "plan.md", "clean.md")
    before = index_tree(root)
    done = commit(root)
    assert done.returncode != 0
    assert "1 staged file holds a secret the clean filter did not redact" in done.stderr
    assert "  plan.md:1 credentials/github-pat" in done.stderr
    assert "git add the file again so the filter redacts it" in done.stderr
    assert index_tree(root) == before


def test_a_committer_commit_is_refused_naming_the_files(root):
    write(root, "plan.md", secret())
    sh(root, "add", "plan.md")
    before = index_tree(root)
    done = commit(root, env={"MOXI_REPO_COMMITTER": "notes/snapshot"})
    assert done.returncode != 0
    assert "1 file changed after the snapshot judged it" in done.stderr
    assert "  plan.md:1 credentials/github-pat" in done.stderr
    assert index_tree(root) == before


def _conflict(root: Path, kind: str) -> None:
    """Stop `kind` on a conflict in c.md, resolve it with a secret and stage it."""
    write(root, "c.md", "base\n")
    sh(root, "add", "c.md")
    sh(root, "commit", "-q", "-m", "base")
    sh(root, "switch", "-q", "-c", "side")
    write(root, "c.md", "side\n")
    sh(root, "commit", "-q", "-am", "side")
    sh(root, "switch", "-q", "main")
    write(root, "c.md", "main\n")
    sh(root, "commit", "-q", "-am", "main")
    if kind == "merge":
        sh(root, "merge", "-q", "side", check=False)
    elif kind == "cherry-pick":
        sh(root, "cherry-pick", "side", check=False)
    elif kind == "revert":
        write(root, "c.md", "later\n")
        sh(root, "commit", "-q", "-am", "later")
        sh(root, "revert", "--no-edit", "HEAD~1", check=False)
    else:
        sh(root, "switch", "-q", "side")
        sh(root, "rebase", "-q", "main", check=False)
    write(root, "c.md", secret())
    sh(root, "add", "c.md")


@pytest.mark.parametrize("action", ["leave-out", "redact"])
@pytest.mark.parametrize("kind", ["merge", "cherry-pick", "revert", "rebase"])
def test_a_commit_that_concludes_an_operation_is_refused(root, kind, action):
    configure(action=action)
    _conflict(root, kind)
    before = index_tree(root)
    head = sh(root, "rev-parse", "HEAD").stdout
    done = commit(root, "--no-edit")
    assert done.returncode != 0
    word = "rebase step" if kind == "rebase" else kind
    assert f"this commit concludes a {word}" in done.stderr
    assert "  c.md:1 credentials/github-pat" in done.stderr
    assert "--abort" in done.stderr
    assert index_tree(root) == before
    assert sh(root, "rev-parse", "HEAD").stdout == head


def test_rebase_continue_commits_without_the_gate_and_the_push_gate_refuses(tmp_path):
    root = notes_repo(GATE, PUSH)
    remote = tmp_path / "remote.git"
    sh(tmp_path, "init", "-q", "--bare", str(remote))
    sh(root, "remote", "add", "origin", str(remote))
    _conflict(root, "rebase")
    # git's sequencer makes this commit itself and runs no pre-commit git hook.
    done = sh(root, "rebase", "--continue", env={"GIT_EDITOR": "true"}, check=False)
    assert done.returncode == 0
    assert sh(root, "show", "side:c.md").stdout == secret()
    done = push(root, "origin", "side")
    assert done.returncode != 0 and "c.md:1 credentials/github-pat" in done.stderr


def test_rebase_continue_at_an_edit_stop_commits_without_the_gate_and_the_push_refuses(
        tmp_path):
    root = notes_repo(GATE, PUSH)
    remote = tmp_path / "remote.git"
    sh(tmp_path, "init", "-q", "--bare", str(remote))
    sh(root, "remote", "add", "origin", str(remote))
    write(root, "c.md", "base\n")
    sh(root, "add", "c.md")
    sh(root, "commit", "-q", "-m", "base")
    write(root, "c.md", "next\n")
    sh(root, "commit", "-q", "-am", "next")
    editor = tmp_path / "edit_first.py"
    editor.write_text("import sys\nwith open(sys.argv[1]) as f:\n    t = f.read()\n"
                      "with open(sys.argv[1], 'w') as f:\n"
                      "    f.write(t.replace('pick', 'edit', 1))\n", encoding="utf-8")
    done = sh(root, "rebase", "-q", "-i", "HEAD~1", check=False,
              env={"GIT_SEQUENCE_EDITOR": f"'{sys.executable}' '{editor}'"})
    assert done.returncode == 0, done.stderr
    assert (root / ".git" / "rebase-merge").is_dir()
    write(root, "c.md", secret())
    sh(root, "add", "c.md")
    # the step's commit is the sequencer's own, with no pre-commit git hook
    done = sh(root, "rebase", "--continue", env={"GIT_EDITOR": "true"}, check=False)
    assert done.returncode == 0, done.stderr
    assert sh(root, "show", "main:c.md").stdout == secret()
    done = push(root, "origin", "main")
    assert done.returncode != 0 and "c.md:1 credentials/github-pat" in done.stderr


# -- the push gate ------------------------------------------------------------------------

@pytest.fixture
def pushing(tmp_path) -> tuple[Path, Path]:
    root = notes_repo(PUSH)
    remote = tmp_path / "remote.git"
    sh(tmp_path, "init", "-q", "--bare", str(remote))
    sh(root, "remote", "add", "origin", str(remote))
    sh(root, "push", "-q", "origin", "main")
    return root, remote


def add_commit(root: Path, name: str, text: str, *, env: dict | None = None) -> None:
    write(root, name, text)
    sh(root, "add", name)
    sh(root, "commit", "-q", "-m", name, env=env)


def push(root: Path, *args: str):
    return sh(root, "push", "-q", *args, check=False)


def refs(remote: Path) -> str:
    return sh(remote, "for-each-ref", "--format=%(refname) %(objectname)").stdout


def test_an_updated_branch_with_a_secret_is_refused(pushing):
    root, remote = pushing
    add_commit(root, "clean.md", "fine\n")
    assert push(root, "origin", "main").returncode == 0
    add_commit(root, "plan.md", secret())
    before = refs(remote)
    done = push(root, "origin", "main")
    assert done.returncode != 0
    assert "the push would carry a secret (1 finding); nothing was pushed" in done.stderr
    assert "  plan.md:1 credentials/github-pat" in done.stderr
    assert refs(remote) == before


def test_a_new_branch_is_judged_on_what_the_remote_lacks(pushing):
    root, remote = pushing
    sh(root, "switch", "-q", "-c", "side")
    add_commit(root, "plan.md", secret())
    done = push(root, "origin", "side")
    assert done.returncode != 0 and "plan.md:1" in done.stderr
    assert "refs/heads/side" not in refs(remote)


def test_a_remote_sha_unknown_here_is_judged_against_the_remotes_refs(pushing, tmp_path):
    root, remote = pushing
    other = tmp_path / "other"
    sh(tmp_path, "clone", "-q", str(remote), str(other))
    add_commit(other, "theirs.md", "theirs\n")
    sh(other, "push", "-q", "origin", "main")
    add_commit(root, "plan.md", secret())
    done = push(root, "--force", "origin", "main")
    assert done.returncode != 0 and "plan.md:1" in done.stderr


def test_a_merge_that_brings_its_own_secret_is_refused(pushing):
    root, _ = pushing
    sh(root, "switch", "-q", "-c", "side")
    add_commit(root, "side.md", "side\n")
    sh(root, "switch", "-q", "main")
    add_commit(root, "main.md", "main\n")
    sh(root, "merge", "-q", "--no-commit", "side")
    write(root, "evil.md", secret())
    sh(root, "add", "evil.md")
    sh(root, "commit", "-q", "--no-edit")
    done = push(root, "origin", "main")
    assert done.returncode != 0 and "evil.md:1" in done.stderr


def test_a_delete_is_not_judged(pushing):
    root, remote = pushing
    sh(root, "switch", "-q", "-c", "side")
    add_commit(root, "clean.md", "fine\n")
    assert push(root, "origin", "side").returncode == 0
    add_commit(root, "plan.md", secret())
    assert push(root, "origin", "--delete", "side").returncode == 0
    assert "refs/heads/side" not in refs(remote)


@pytest.mark.parametrize("who", ["AUTHOR", "COMMITTER"])
def test_a_private_name_as_author_or_committer_is_refused(pushing, who):
    root, _ = pushing
    private_names("quillon")
    add_commit(root, "clean.md", "fine\n", env={f"GIT_{who}_NAME": "Quillon"})
    done = push(root, "origin", "main")
    assert done.returncode != 0
    assert f"  {who.lower()} of " in done.stderr and "private-names/private-name" in done.stderr
    assert "Quillon" not in done.stderr


def test_an_author_allowance_lets_the_push_go(pushing):
    root, _ = pushing
    private_names("quillon")
    common = Path(sh(root, "rev-parse", "--path-format=absolute",
                     "--git-common-dir").stdout.strip())
    (common / judge.PRIVATE).write_text(
        allow_line("@author", "private-names/private-name", "text:quillon"),
        encoding="utf-8")
    add_commit(root, "clean.md", "fine\n", env={"GIT_AUTHOR_NAME": "Quillon"})
    done = push(root, "origin", "main")
    assert done.returncode == 0 and done.stderr == ""

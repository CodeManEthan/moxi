"""Setup and teardown: the questions with and without a terminal, the preview, the writes."""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

import moxi.config
import moxi.paths
from moxi_notes import judge, setup, spec

from gate_kit import (notes_repo, private_names, sh, snapshot, token, tree,
                      write)
from notes_kit import configure, git_env, home, repo

posix = pytest.mark.skipif(sys.platform == "win32",
                           reason="git hooks through POSIX sh; the Windows run is owed")
TODAY = "2026-11-01"


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)


@pytest.fixture
def no_terminal(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))


class Keyboard:
    """Answers typed at the questions, in order; records each question asked."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.asked: list[str] = []

    def __call__(self, question: str) -> str:
        self.asked.append(question)
        return self.answers.pop(0) if self.answers else ""


def run(ask=None) -> tuple[int, str]:
    out = io.StringIO()
    code = setup.setup(ask=ask, out=out, today=TODAY)
    return code, out.getvalue()


def setting(key: str):
    from moxi_notes import settings

    return moxi.config.load("notes", settings.schema())[key]


def pieces(root: Path) -> dict[str, bool]:
    from moxi_repo import api

    listed = api.status(root)
    return {p["id"]: p["enabled"] for p in listed[0]["pieces"]}


def driver(root: Path) -> str | None:
    done = sh(root, "config", "filter.moxi-notes.process", check=False)
    return done.stdout.strip() if done.returncode == 0 else None


def attributes(root: Path) -> str:
    path = root / ".git" / "info" / "attributes"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# -- the questions ------------------------------------------------------------------------

@posix
def test_without_a_terminal_it_takes_the_defaults_and_prints_how_to_change_each(
        no_terminal):
    code, out = run()
    root = home() / "notes"
    assert code == 0, out
    assert (root / ".git").is_dir()
    assert "making it one (the default)" in out
    assert "moxi config set notes root PATH" in out
    assert "moxi config set notes action redact" in out
    assert setting("action") == "leave-out"
    assert pieces(root) == {"notes/secret-gate": True, "notes/push-gate": True}
    assert driver(root) is None
    from moxi_repo.api import saved_spec

    assert spec.same(saved_spec(root, spec.NAME), spec.build())


@posix
def test_at_a_terminal_each_question_is_asked_and_the_answers_written(tmp_path):
    elsewhere = home() / "writing"
    keys = Keyboard(str(elsewhere), "y", "2")
    code, out = run(keys)
    assert code == 0, out
    assert keys.asked[0] == "Where are your notes? [~/notes] "
    assert keys.asked[1].startswith("~/writing is in no git repo. Make it a git repo")
    assert keys.asked[2] == setup.ACTION_QUESTION.format(default=1)
    assert keys.asked[2].startswith("When a note holds a secret (a key or token, or a name "
                                    "from your private names list):\n")
    assert keys.asked[2].endswith("Either way, a push that would carry a secret is "
                                  "refused.\nChoose [1]: ")
    assert str(setting("root")) == str(elsewhere)
    assert setting("action") == "redact"
    assert (elsewhere / ".git").is_dir()
    assert driver(elsewhere) is not None
    assert attributes(elsewhere) == "* filter=moxi-notes\n"
    assert not (home() / "notes").exists()


@posix
def test_a_no_to_git_init_leaves_the_folder_alone_and_writes_nothing_in_it():
    root = home() / "notes"
    root.mkdir()
    code, out = run(Keyboard("", "n", ""))
    assert code == 0, out
    assert not (root / ".git").exists()
    assert "snapshots and the gates stay off" in out


@posix
def test_git_init_runs_only_on_a_root_in_no_work_tree(no_terminal):
    outer = repo(home() / "code", commit=True)
    inside = outer / "notes"
    inside.mkdir()
    configure(root=str(inside))
    code, out = run()
    assert code == 0, out
    assert not (inside / ".git").exists()
    assert "is inside the git repo" in out and "nothing is written there" in out
    assert sh(outer, "config", "--get-regexp", "moxi-repo", check=False).stdout == ""
    # A root that is already a repo is never initialised again.
    root = notes_repo()
    configure(root=str(root))
    head = sh(root, "rev-parse", "HEAD").stdout
    code, out = run()
    assert code == 0 and "made" not in out
    assert sh(root, "rev-parse", "HEAD").stdout == head


@posix
def test_the_push_question_is_asked_only_with_remotes_and_no_push_set(tmp_path):
    root = notes_repo()
    keys = Keyboard("", "")
    run(keys)
    assert not any("Push snapshots" in q for q in keys.asked)
    bare = tmp_path / "remote.git"
    sh(tmp_path, "init", "-q", "--bare", str(bare))
    sh(root, "remote", "add", "origin", str(bare))
    keys = Keyboard("", "", "origin")
    code, out = run(keys)
    assert code == 0, out
    assert keys.asked[-1] == "Push snapshots to which remote? [origin/none] "
    assert setting("snapshot.push") == "origin"
    keys = Keyboard("", "")
    run(keys)
    assert not any("Push snapshots" in q for q in keys.asked)


@posix
def test_without_a_terminal_the_push_stays_off_with_the_line_that_sets_it(tmp_path,
                                                                         no_terminal):
    root = notes_repo()
    sh(root, "remote", "add", "origin", str(tmp_path / "remote.git"))
    code, out = run()
    assert code == 0
    assert "moxi config set notes snapshot.push origin" in out
    assert setting("snapshot.push") == ""


@posix
def test_switching_actions_both_ways_writes_and_removes_the_filter():
    root = notes_repo()
    assert run(Keyboard("", "2"))[0] == 0
    assert driver(root) is not None
    assert sh(root, "config", "filter.moxi-notes.required").stdout.strip() == "false"
    assert attributes(root) == "* filter=moxi-notes\n"
    assert run(Keyboard("", ""))[0] == 0  # Enter keeps redact
    assert setting("action") == "redact" and driver(root) is not None
    keys = Keyboard("", "1")
    assert run(keys)[0] == 0
    assert keys.asked[-1].endswith("Choose [2]: ")
    assert setting("action") == "leave-out"
    assert driver(root) is None and attributes(root) == ""


@posix
def test_setup_runs_again_without_change_and_enables_a_disabled_gate(no_terminal):
    from moxi_repo import api

    root = notes_repo()
    assert run()[0] == 0
    config = sorted(sh(root, "config", "--list", "--local").stdout.splitlines())
    api.disable(root, "notes/push-gate")
    code, out = run()
    assert code == 0, out
    assert pieces(root) == {"notes/secret-gate": True, "notes/push-gate": True}
    assert sorted(sh(root, "config", "--list", "--local").stdout.splitlines()) == config


def test_a_config_that_cannot_be_read_fails_setup_with_the_reason(no_terminal):
    path = Path(moxi.paths.Layout("notes").config_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("action = 'shred'\n", encoding="utf-8")
    code, out = run()
    assert code == 1 and "can't read notes' config" in out



# -- the preview --------------------------------------------------------------------------

@posix
def test_the_preview_counts_files_and_authors_and_prints_the_allowance_lines(
        no_terminal, monkeypatch):
    root = notes_repo()
    private_names("quillon")
    write(root, "plan.md", "intro\nkey = " + token() + "\n")
    write(root, "paths.md", "see /home/quillon/code/alpha\n")
    write(root, "clean.md", "nothing here\n")
    write(root, "draft/untracked.md", "Quillon wrote this\n")
    write(root, ".gitignore", "ignored.md\n")
    write(root, "ignored.md", "quillon\n")
    sh(root, "add", "plan.md", "paths.md", "clean.md", ".gitignore")
    sh(root, "commit", "-q", "-m", "notes")
    sh(root, "commit", "-q", "--allow-empty", "-m", "by hand",
       env={"GIT_AUTHOR_NAME": "quillon", "GIT_AUTHOR_EMAIL": "q@example.invalid"})
    code, out = run()
    assert code == 0, out
    assert "3 hold a secret" in out
    assert "  plan.md  credentials/github-pat line 2" in out
    assert "  paths.md  private-names/private-name line 1" in out
    assert "  draft/untracked.md  private-names/private-name line 1" in out
    assert "ignored.md" not in out.replace("untracked.md", "")
    assert "with leave-out, each one leaves the snapshots at its next change" in out
    assert "1 commit author holds a private name" in out
    private = root / ".git" / "secrets-allow-private"
    assert f"the private allowlist is {private}" in out
    lines = [line for line in out.splitlines() if "\ttext:" in line]
    assert lines == [
        f"*\tprivate-names/private-name\ttext:quillon\t{TODAY}\tpaths in my own notes",
        f"@author\tprivate-names/private-name\ttext:quillon\t{TODAY}\tmy own commits",
    ]


@posix
def test_an_allowance_written_where_the_preview_says_keeps_its_hit_at_the_next_snapshot(
        no_terminal):
    root = notes_repo()
    private_names("quillon")
    write(root, "paths.md", "see /home/quillon/code/alpha\n")
    code, out = run()
    assert code == 0
    lines = [line for line in out.splitlines() if line.startswith("*\t")]
    assert "paths.md" not in tree(root, "HEAD")
    first = snapshot(root)
    assert "paths.md" not in tree(root)
    private = Path(out.split("the private allowlist is ", 1)[1].split(",", 1)[0])
    assert private == root / ".git" / "secrets-allow-private"
    private.write_text(lines[0] + "\n", encoding="utf-8")
    write(root, "paths.md", "see /home/quillon/code/alpha and beta\n")
    second = snapshot(root)
    assert second.status == "committed", (first, second)
    assert "paths.md" in tree(root)


@posix
def test_the_preview_under_redact_says_the_pushed_copy_is_blanked(no_terminal):
    root = notes_repo()
    configure(action="redact")
    write(root, "plan.md", "intro\nkey = " + token() + "\n")
    code, out = run()
    assert "with redact, each one's committed and pushed copy has its hits blanked" in out
    assert "a cloud session reading the pushed copy sees them blanked" in out


@posix
def test_the_preview_names_ten_paths_and_counts_the_rest(no_terminal):
    root = notes_repo()
    for n in range(12):
        write(root, f"n{n:02}.md", "key = " + token(n) + "\n")
    code, out = run()
    assert "12 hold a secret" in out and "and 2 more" in out
    assert out.count("credentials/github-pat") == 10


@posix
def test_a_separate_git_dir_has_no_private_allowlist_and_the_preview_says_so(
        tmp_path, no_terminal):
    root = home() / "notes"
    (tmp_path / "store").mkdir()
    sh(tmp_path, "init", "-q", f"--separate-git-dir={tmp_path / 'store' / 'notes.git'}",
       str(root))
    private_names("quillon")
    write(root, "paths.md", "see /home/quillon/code/alpha\n")
    code, out = run()
    assert code == 0, out
    assert "is not a folder named .git" in out
    assert "\ttext:" not in out
    assert judge.private_place_ok(tmp_path / "store" / "notes.git") is False


# -- teardown -----------------------------------------------------------------------------

@posix
def test_teardown_removes_the_filter_and_leaves_the_notes(no_terminal):
    root = notes_repo()
    write(root, "plan.md", "a note\n")
    assert run(Keyboard("", "2"))[0] == 0
    assert driver(root) is not None
    out = io.StringIO()
    assert setup.teardown(out=out) == 0
    assert driver(root) is None and attributes(root) == ""
    assert (root / "plan.md").read_text() == "a note\n"
    assert setup.teardown(out=io.StringIO()) == 0


def test_teardown_with_no_notes_repo_does_nothing(no_terminal):
    out = io.StringIO()
    assert setup.teardown(out=out) == 0
    assert "nothing of notes' is there" in out.getvalue()
    assert not (home() / "notes").exists()


def test_the_announcement_names_setup_and_teardown():
    from moxi_notes import module

    assert module.SETUP == "moxi_notes.setup:setup"
    assert module.TEARDOWN == "moxi_notes.setup:teardown"


@posix
def test_moxi_notes_setup_runs_setup(no_terminal):
    from notes_kit import moxi_cmd

    code, out, err = moxi_cmd("notes", "setup")
    assert code == 0, out + err
    assert (home() / "notes" / ".git").is_dir()
    assert os.path.isdir(home() / "notes")


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@posix
def test_a_terminal_on_stdin_is_asked_through_input(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _Terminal("\n\n2\n"))
    code = setup.setup(out=io.StringIO(), today=TODAY)
    assert code == 0
    shown = capsys.readouterr().out
    assert "Where are your notes? [~/notes] " in shown and "Choose [1]: " in shown
    assert setting("action") == "redact"
    assert (home() / "notes" / ".git").is_dir()

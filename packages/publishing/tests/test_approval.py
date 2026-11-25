"""Push approval: the approval piece and `moxi publishing approval enable|disable`."""

from __future__ import annotations

import json

import pytest
from conftest import git, write_config

from moxi_publishing import approval, cli, flag, push

ZERO = "0" * 40
WORD = "comms:1234"


def log_lines():
    path = push.log_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def run(repo, monkeypatch):
    """The approval piece as git's pre-push hook runs it in `repo`, with the agent test
    faked: `run(*remote_refs, harness=..., word=..., patterns=...)`. Each remote ref is
    pushed from HEAD, as a new ref; a ref starting with `-` is a deletion of it."""
    import moxi.terminal
    from moxi_repo.api import HookContext

    monkeypatch.chdir(repo)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()

    def go(*refs, harness="claude", word=None, patterns=("main",)):
        monkeypatch.setattr(moxi.terminal, "agent", lambda *a, **k: harness)
        if word is None:
            monkeypatch.delenv(push.WORD_ENV, raising=False)
        else:
            monkeypatch.setenv(push.WORD_ENV, word)
        lines = [f"(delete) {ZERO} {ref[1:]} {head}" if ref.startswith("-")
                 else f"HEAD {head} {ref} {ZERO}" for ref in refs]
        return approval.pre_push(HookContext(
            "pre-push", args=("origin", "/elsewhere/remote.git"),
            stdin=("\n".join(lines) + "\n").encode(),
            settings={"branch": list(patterns)}))

    return go


def refused(outcome) -> str:
    assert outcome.kind == "refuse", outcome
    return "\n".join(outcome.lines)


def test_an_agents_push_to_a_named_branch_needs_the_word(run):
    text = refused(run("refs/heads/main"))
    assert "runs in a claude session and moves refs/heads/main" in text
    assert push.WORD_ENV in text
    assert run("refs/heads/main", word=WORD).kind == "ok"


def test_an_agents_push_to_another_branch_needs_none(run):
    assert run("refs/heads/topic").kind == "ok"


def test_a_push_from_the_users_own_terminal_needs_none(run):
    assert run("refs/heads/main", harness=None).kind == "ok"
    assert run("refs/tags/v1", harness=None).kind == "ok"


def test_an_agents_tag_push_needs_the_word_whatever_the_patterns_name(run):
    assert "refs/tags/moxi-v1.0.0" in refused(run("refs/tags/moxi-v1.0.0"))
    assert run("refs/tags/moxi-v1.0.0", word=WORD).kind == "ok"
    assert "refs/tags/old" in refused(run("-refs/tags/old"))


def test_a_deletion_of_a_named_branch_needs_the_word(run):
    assert "refs/heads/main" in refused(run("-refs/heads/main"))


def test_patterns_take_wildcards_and_star_is_every_branch(run):
    assert run("refs/heads/release/1", patterns=("release/*",)).kind == "refuse"
    assert run("refs/heads/rel-2", patterns=("rel-[0-9]",)).kind == "refuse"
    assert run("refs/heads/topic", patterns=("release/*",)).kind == "ok"
    assert run("refs/heads/a/b", patterns=("*",)).kind == "refuse"


def test_a_word_not_matching_the_pattern_refuses(run):
    write_config("publishing", "[push]\nword_pattern = '^comms:[0-9]+$'\n")
    assert "does not match push.word_pattern" in refused(run("refs/heads/main",
                                                             word="yes"))
    assert run("refs/heads/main", word=WORD).kind == "ok"
    # A blank word is none.
    assert push.WORD_ENV in refused(run("refs/heads/main", word="   "))


def test_a_passing_push_is_logged_with_the_word_and_the_agent(run):
    run("refs/heads/main", word=WORD)
    run("refs/heads/topic", harness=None)
    first, second = log_lines()[-2:]
    assert (first["word"], first["agent"], first["piece"]) == (WORD, "claude",
                                                               approval.PIECE_ID)
    assert first["refs"][0]["remote_ref"] == "refs/heads/main"
    assert (second["word"], second["agent"]) == (None, None)
    assert first["url"] == "/elsewhere/remote.git"


def test_where_the_push_piece_runs_it_logs_and_approval_does_not(repo, names, run):
    from moxi_repo import api

    api.add(repo, push.PIECE_ID)
    run("refs/heads/main", word=WORD)
    assert log_lines() == []


def test_a_refused_push_is_not_logged(run):
    run("refs/heads/main")
    assert log_lines() == []


def test_a_failing_agent_test_refuses(run, monkeypatch):
    import moxi.terminal

    def broken(*a, **k):
        raise OSError("no process table")

    monkeypatch.setattr(moxi.terminal, "agent", broken)
    from moxi_repo.api import HookContext

    done = approval.pre_push(HookContext("pre-push", args=("origin", "x"), stdin=b"",
                                         settings={"branch": ["main"]}))
    assert "can't be checked, so it is refused: no process table" in refused(done)


# -- through git ----------------------------------------------------------------------------

def test_a_real_push_from_an_agent_needs_the_word(repo, names, remote):
    approval.enable(repo, ["main"])
    git(repo, "commit", "-q", "--allow-empty", "-m", "Another")
    agent = {"CLAUDECODE": "1"}
    done = git(repo, "push", "-q", "origin", "main", check=False,
               env={**agent, push.WORD_ENV: ""})
    assert done.returncode != 0 and push.WORD_ENV in done.stderr
    done = git(repo, "push", "-q", "origin", "main", check=False,
               env={**agent, push.WORD_ENV: WORD})
    assert done.returncode == 0, done.stderr
    assert log_lines()[-1]["word"] == WORD


# -- enable, disable ------------------------------------------------------------------------

def test_enable_registers_the_patterns_and_enable_again_replaces_them(repo, capsys):
    assert cli.main(["approval", "enable", str(repo), "--branch", "main",
                     "--branch", "release/*"]) == 0
    assert "an agent's push of main release/*, or of any tag" in capsys.readouterr().out
    pieces = flag.registered(str(repo))
    assert pieces[approval.PIECE_ID]["settings"]["branch"] == ["main", "release/*"]
    approval.enable(repo, ["*"])
    assert flag.registered(str(repo))[approval.PIECE_ID]["settings"]["branch"] == ["*"]
    # It reads no flag: the repo is neither clean nor public, and the doctor names nothing.
    assert not flag.is_clean(str(repo))
    from moxi_publishing import doctor

    assert not [row for row in doctor.rows() if row.name == f"repo {repo}"]


def test_enable_wants_a_branch_and_refuses_a_ref_name(repo):
    with pytest.raises(SystemExit):
        cli.main(["approval", "enable", str(repo)])
    with pytest.raises(flag.Failure) as caught:
        approval.enable(repo, ["refs/heads/main"])
    assert caught.value.code == 2 and "names the branch, as 'main'" in caught.value.text
    with pytest.raises(flag.Failure):
        approval.enable(repo, [" "])
    assert flag.registered(str(repo)) == {}


def test_disable_removes_the_piece(repo, capsys):
    approval.enable(repo, ["main"])
    assert cli.main(["approval", "disable", str(repo)]) == 0
    assert flag.registered(str(repo)) == {}
    assert "nothing to remove" in approval.disable(repo)[0]

"""The message piece: drops, passes and refusals, through real commits and `judge`."""

from __future__ import annotations

import os
import stat

import pytest
from conftest import commit, git, last_message, public_repo, token, write_config

from moxi_publishing import message

PIECE = "publishing/message"
#: A session link and a pointer, joined at run time so no file spells one whole.
LINK = "https://claude" + ".ai/code/" + "session_0123abcd"
POINTER = "0123abcd9" + "@" + "2026-01-02"


@pytest.fixture
def pub(repo, names):
    return public_repo(repo, PIECE, sets=("drop-trailer=Claude-Session",
                                          "drop-trailer=Commit-Check"))


def judged(text, **kw):
    kw.setdefault("rules", None)
    return message.judge(text, **kw)


# -- drops ----------------------------------------------------------------------------------

def test_each_default_key_is_dropped_and_said(pub):
    done = commit(pub, "Add a note\n\nWhy it helps.\n\nClaude-Session: " + LINK +
                  "\nCommit-Check: 0123456789ab\n")
    assert done.returncode == 0, done.stderr
    assert last_message(pub) == "Add a note\n\nWhy it helps.\n\n"[:-1]
    assert "dropped the Claude-Session line" in done.stderr
    assert "dropped the Commit-Check line" in done.stderr


def test_a_configured_key_is_dropped_with_its_continuation_lines(repo, names):
    public_repo(repo, PIECE, sets=("drop-trailer=Co-Authored-By",))
    done = commit(repo, "Add a note\n\nBody.\n\nCo-authored-by: alice <alice@example.test>\n"
                        "  more of the value\nSigned-off-by: bob <bob@example.test>\n")
    assert done.returncode == 0, done.stderr
    assert last_message(repo) == ("Add a note\n\nBody.\n\n"
                                  "Signed-off-by: bob <bob@example.test>\n")


def test_drop_matches_the_key_ignoring_case_and_keeps_other_lines_byte_for_byte():
    text = "Subject\r\n\r\nbody  \r\n\r\nclaude-session : x\r\nKeep: yes\r\n"
    out = judged(text, drop=["Claude-Session"], rules=(), sets=())
    assert out.text == "Subject\r\n\r\nbody  \r\n\r\nKeep: yes\r\n"
    assert out.dropped == ["claude-session"]


# -- pass keys ------------------------------------------------------------------------------

def test_a_pass_key_is_left_byte_for_byte_and_skipped_by_the_provenance_rules(repo, names):
    public_repo(repo, PIECE, sets=("drop-trailer=Claude-Session",
                                   "pass-trailer=Commit-Check"))
    # Twelve digits: the message-id rule would refuse it on any other line.
    line = "Commit-Check:  123456789012"
    done = commit(repo, f"Add a note\n\n{line}\n")
    assert done.returncode == 0, done.stderr
    assert last_message(repo) == f"Add a note\n\n{line}\n"


def test_a_pass_key_is_still_scanned_with_secrets(repo, names):
    public_repo(repo, PIECE, sets=("pass-trailer=Commit-Check",))
    done = commit(repo, "Add a note\n\nCommit-Check: " + token() + "\n")
    assert done.returncode != 0
    assert "line 3, credentials/" in done.stderr


def test_a_key_on_both_settings_is_never_dropped(repo, names):
    public_repo(repo, PIECE, sets=("drop-trailer=Commit-Check", "drop-trailer=Claude-Session",
                                   "pass-trailer=Commit-Check"))
    done = commit(repo, "Add a note\n\nCommit-Check: 0123456789ab\n")
    assert done.returncode == 0, done.stderr
    assert "Commit-Check: 0123456789ab" in last_message(repo)
    assert "dropped" not in done.stderr


@pytest.mark.skipif(os.name == "nt", reason="a POSIX sh git hook")
def test_a_later_commit_msg_hook_reads_the_pass_key(repo, names):
    public_repo(repo, PIECE, sets=("pass-trailer=Commit-Check",))
    seen = repo.parent / "seen.txt"
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text("#!/bin/sh\ncp \"$1\" '" + str(seen) + "'\n"
                    "grep -v '^Commit-Check:' \"$1\" > \"$1.new\" && mv \"$1.new\" \"$1\"\n",
                    encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    done = commit(repo, "Add a note\n\nCommit-Check: 0123456789ab\n")
    assert done.returncode == 0, done.stderr
    assert "Commit-Check: 0123456789ab" in seen.read_text(encoding="utf-8")
    assert "Commit-Check" not in last_message(repo)


# -- refusals -------------------------------------------------------------------------------

@pytest.mark.parametrize(("line", "rule"), [
    ("See " + LINK + " for it", "session-link"),
    ("from " + POINTER, "session-pointer"),
    ("as comms:" + "4611686018", "message-id"),
    ("re msg " + "42", "message-id"),
    ("ref " + "1234567890" + "12", "message-id"),
    ("as [C" + "7] said \"keep it\"", "quoted-ruling"),
])
def test_each_message_rule_refuses(line, rule):
    out = judged(f"Subject\n\n{line}\n", rules=message_rules(), sets=())
    assert [text for _, text in out.refusals] == [f"line 3, {rule}: {line}"]


def message_rules():
    from moxi_publishing import rules

    return rules.NAMES


def test_a_ruling_tag_without_a_quote_and_dates_pass():
    out = judged("Subject\n\nas [C" + "7] said, on 2026-01-02\n", rules=message_rules(),
                 sets=())
    assert out.refusals == []


def test_a_switched_off_rule_does_not_run(repo, names):
    write_config("publishing", 'provenance.rules = ["session-pointer"]\n')
    public_repo(repo, PIECE)
    assert commit(repo, "Subject\n\nSee " + LINK + "\n").returncode == 0
    done = commit(repo, "Subject\n\nfrom " + POINTER + "\n")
    assert done.returncode != 0 and "session-pointer" in done.stderr


def test_a_refusal_names_each_line_cut_at_100_characters_and_the_next_step(pub):
    long = "x" * 120 + " " + LINK
    done = commit(pub, f"Subject\n\n{long}\nfrom {POINTER}\n")
    assert done.returncode != 0
    assert f"line 3, session-link: {long[:97]}..." in done.stderr
    assert f"line 4, session-pointer: from {POINTER}" in done.stderr
    assert "reword those lines and commit again" in done.stderr
    assert git(pub, "rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_both_secrets_sets_refuse_and_a_credential_never_prints(pub):
    secret = token(11)
    done = commit(pub, f"Subject\n\nkey {secret}\nthanks bobsecret\n")
    assert done.returncode != 0
    assert "line 3, credentials/" in done.stderr
    assert "line 4, private-names/private-name" in done.stderr
    assert secret not in done.stderr and "bobsecret" not in done.stderr


def test_a_provenance_line_masks_the_secrets_beside_it(pub):
    secret = token(12)
    done = commit(pub, f"Subject\n\nfrom {POINTER} key {secret} bobsecret\n")
    assert done.returncode != 0
    assert (f"line 3, session-pointer: from {POINTER} key [github-pat] [private-name]"
            in done.stderr)
    assert secret not in done.stderr and "bobsecret" not in done.stderr


def test_a_private_email_in_a_trailer_refuses(pub):
    done = commit(pub, "Subject\n\nBody.\n\nReviewed-by: bob <bob@private.example.test>\n")
    assert done.returncode != 0 and "private-names/private-email" in done.stderr


def test_comment_lines_are_read():
    out = judged("Subject\n# from " + POINTER + "\n", rules=message_rules(), sets=())
    assert [n for n, _ in out.refusals] == [2]


def test_nothing_below_the_scissors_line_is_read(pub):
    text = ("Subject\n\n# ------------------------ >8 ------------------------\n"
            "See " + LINK + "\nClaude-Session: x\n")
    out = judged(text, drop=["Claude-Session"], rules=message_rules(), sets=())
    assert out.refusals == [] and out.dropped == [] and out.text == text
    # git cuts at the scissors line only when the message is edited.
    done = commit(pub, text, flags=("-e", "--cleanup=scissors"), env={"GIT_EDITOR": "true"})
    assert done.returncode == 0, done.stderr
    assert last_message(pub) == "Subject\n"


def test_a_list_that_will_not_load_refuses(pub):
    write_config("secrets", "private_names = 3\n")
    done = commit(pub, "Subject\n")
    assert done.returncode != 0
    assert "can't be checked, so it is refused" in done.stderr


def test_a_config_that_will_not_load_refuses(pub):
    write_config("publishing", 'provenance.rules = ["no-such-rule"]\n')
    done = commit(pub, "Subject\n")
    assert done.returncode != 0
    assert "can't be checked" in done.stderr


# -- merges, replays and the committer ------------------------------------------------------

def _side_branch(repo):
    git(repo, "checkout", "-q", "-b", "side")
    assert commit(repo, "Side change\n", name="side.md").returncode == 0
    git(repo, "checkout", "-q", "main")
    assert commit(repo, "Main change\n", name="main.md").returncode == 0


def test_a_merge_message_is_judged_and_names_the_merge_step(pub):
    _side_branch(pub)
    done = git(pub, "merge", "-q", "--no-ff", "side", "-m", "Merge side\n\nfrom " + POINTER,
               check=False)
    assert done.returncode != 0
    assert "git merge --abort" in done.stderr
    git(pub, "merge", "--abort")
    done = git(pub, "merge", "-q", "--no-ff", "side", "-m",
               "Merge side\n\nClaude-Session: " + LINK, check=False)
    assert done.returncode == 0, done.stderr
    assert last_message(pub).strip() == "Merge side"


def test_a_replay_committed_by_hand_is_judged(pub):
    # git's sequencer runs no commit-msg on the commits it makes itself; a replay stopped
    # on a conflict is committed with git commit, which runs it.
    git(pub, "checkout", "-q", "-b", "side")
    assert commit(pub, "Side change\n", name="same.md", text="side\n").returncode == 0
    git(pub, "checkout", "-q", "main")
    assert commit(pub, "Main change\n", name="same.md", text="main\n").returncode == 0
    assert git(pub, "cherry-pick", "side", check=False).returncode != 0
    (pub / "same.md").write_text("both\n", encoding="utf-8")
    git(pub, "add", "same.md")
    done = commit(pub, "Picked\n\nfrom " + POINTER + "\n", name="other.md")
    assert done.returncode != 0 and "session-pointer" in done.stderr
    assert "continue the rebase, cherry-pick or revert" in done.stderr
    done = commit(pub, "Picked\n\nClaude-Session: " + LINK + "\n", name="other.md")
    assert done.returncode == 0, done.stderr
    assert last_message(pub) == "Picked\n"


def test_a_committer_commit_is_judged_alike(pub):
    from moxi_repo import api

    (pub / "notes").mkdir()
    (pub / "notes" / "today.md").write_text("today\n", encoding="utf-8")
    spec = api.Spec(name="proj/snapshot", root=str(pub), paths=("notes",),
                    author=("operator", "operator@example.test"), push=None,
                    trailers=lambda changes, since: [("Claude-Session", LINK)])
    result = api.commit(spec)
    assert result.status == "committed", result.line()
    assert "Claude-Session" not in last_message(pub)


# -- the flag -------------------------------------------------------------------------------

def test_the_piece_refuses_without_the_flag(repo, names):
    public_repo(repo, PIECE)
    git(repo, "config", "--unset", "moxi-publishing.public")
    done = commit(repo, "Subject\n")
    assert done.returncode != 0
    assert "marked neither clean nor public" in done.stderr
    assert "moxi publishing clean-history enable" in done.stderr
    assert "moxi publishing enable" in done.stderr

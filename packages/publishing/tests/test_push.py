"""The push piece, against real repos and a bare remote: the word, each rule for a ref,
what is new to the remote and how it is judged, and the log."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import commit, fingerprint, git, public_repo, token, write_config

PIECE = "publishing/push"
POINTER = "0123abcd9" + "@" + "2026-01-02"
#: A ruling-shaped tag, joined at run time so no file spells one whole.
TAG = "[" + "Q7" + "]"


@pytest.fixture
def pub(repo, names, remote):
    return public_repo(repo, PIECE, sets=("main=main",))


def push(repo, *args, env=None):
    return git(repo, "push", "-q", *args, check=False, env=env)


def refused(done, *texts):
    assert done.returncode != 0, done.stderr
    for text in texts:
        assert text in done.stderr, (text, done.stderr)


def passed(done):
    assert done.returncode == 0, done.stderr


def log_lines():
    from moxi_publishing import push as piece

    path = piece.log_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def head(repo, rev="HEAD"):
    return git(repo, "rev-parse", rev).stdout.strip()


# -- the word -------------------------------------------------------------------------------

def test_the_word_when_required(repo, names, remote):
    public_repo(repo, PIECE, sets=("main=main", "word-required=true"))
    commit(repo, "Add")
    refused(push(repo, "origin", "main"), "MOXI_PUBLISHING_PUSH_WORD")
    refused(push(repo, "origin", "main", env={"MOXI_PUBLISHING_PUSH_WORD": "two words"}),
            "push.word_pattern")
    passed(push(repo, "origin", "main", env={"MOXI_PUBLISHING_PUSH_WORD": "comms:1"}))
    assert log_lines()[-1]["word"] == "comms:1"


def test_the_word_pattern_is_the_users(repo, names, remote):
    write_config("publishing", "[push]\nword_pattern = '^ok-[0-9]+$'\n")
    public_repo(repo, PIECE, sets=("main=main", "word-required=true"))
    commit(repo, "Add")
    refused(push(repo, "origin", "main", env={"MOXI_PUBLISHING_PUSH_WORD": "comms:1"}))
    passed(push(repo, "origin", "main", env={"MOXI_PUBLISHING_PUSH_WORD": "ok-12"}))


# -- branches -------------------------------------------------------------------------------

def test_a_branch_moved_forward_or_created_passes_and_is_logged(pub, remote):
    commit(pub, "Add")
    passed(push(pub, "origin", "main"))
    git(pub, "switch", "-q", "-c", "topic")
    commit(pub, "More")
    passed(push(pub, "origin", "topic"))
    last = log_lines()[-1]
    assert last["remote"] == "origin" and last["url"] == str(remote)
    assert last["refs"][0]["remote_ref"] == "refs/heads/topic"
    assert last["commits"] == [head(pub)]
    assert Path(last["repo"]) == pub  # git names the work tree with `/` on Windows


def test_a_branch_is_never_rewritten(pub):
    commit(pub, "Add")
    passed(push(pub, "origin", "main"))
    git(pub, "commit", "-q", "--amend", "-m", "Changed")
    refused(push(pub, "-f", "origin", "main"), "never rewritten")


def test_main_and_a_protected_branch_are_never_deleted(repo, names, remote):
    public_repo(repo, PIECE, sets=("main=main", "protected=keep"))
    git(repo, "branch", "keep")
    passed(push(repo, "origin", "keep"))
    refused(push(repo, "origin", ":keep"), "keep is never deleted")
    refused(push(repo, "origin", ":main"), "main is never deleted")


def test_a_branch_is_deleted_only_once_merged_into_main(pub):
    git(pub, "switch", "-q", "-c", "topic")
    commit(pub, "Topic")
    passed(push(pub, "origin", "topic"))
    refused(push(pub, "origin", ":topic"), "not merged into main as last fetched",
            "if it was merged on the remote since, fetch first (git fetch origin)")
    git(pub, "switch", "-q", "main")
    git(pub, "merge", "-q", "--no-ff", "-m", "Merge topic", "topic")
    passed(push(pub, "origin", "main"))
    passed(push(pub, "origin", ":topic"))


def test_a_deletion_whose_tip_is_not_here_says_fetch_first(pub, remote, tmp_path):
    git(pub, "branch", "topic")
    passed(push(pub, "origin", "topic"))
    other = tmp_path / "proj-b"
    git(tmp_path, "clone", "-q", str(remote), str(other))
    git(other, "switch", "-q", "topic")
    commit(other, "Elsewhere")
    git(other, "push", "-q", "origin", "topic")
    refused(push(pub, "origin", ":topic"), "fetch first")


def test_a_deletion_without_a_tracking_ref_of_main_says_fetch_first(pub):
    git(pub, "branch", "topic")
    passed(push(pub, "origin", "topic"))
    git(pub, "update-ref", "-d", "refs/remotes/origin/main")
    refused(push(pub, "origin", ":topic"), "fetch first")


# -- tags and other refs --------------------------------------------------------------------

def test_a_tag_needs_free_ref_and_a_release_tag_is_never_freed(repo, names, remote):
    public_repo(repo, PIECE, sets=("main=main", "free-ref=refs/tags/docs/*",
                                   "free-ref=refs/notes/*"))
    git(repo, "tag", "plain")
    refused(push(repo, "origin", "plain"), "free-ref")
    git(repo, "tag", "docs/one")
    passed(push(repo, "origin", "docs/one"))
    commit(repo, "Add")
    git(repo, "tag", "-f", "docs/one")
    passed(push(repo, "-f", "origin", "docs/one"))
    passed(push(repo, "origin", ":refs/tags/docs/one"))
    git(repo, "tag", "proj-a-v0.1.0")
    refused(push(repo, "origin", "proj-a-v0.1.0"), "shaped like a release tag")
    # A pattern written by hand that names release tags still frees none of them.
    git(repo, "config", "--add", "moxi-repo.piece.publishing/push.free-ref", "refs/tags/*")
    refused(push(repo, "origin", "proj-a-v0.1.0"), "shaped like a release tag")
    refused(push(repo, "origin", ":refs/tags/proj-a-v0.0.9"), "never moved or deleted")


def test_another_ref_needs_free_ref(repo, names, remote):
    git(repo, "notes", "add", "-m", "a note")
    public_repo(repo, PIECE, sets=("main=main",))
    refused(push(repo, "origin", "refs/notes/commits"), "free-ref")
    git(repo, "config", "--add", "moxi-repo.piece.publishing/push.free-ref", "refs/notes/*")
    passed(push(repo, "origin", "refs/notes/commits"))


def test_a_ref_outside_the_commit_and_tag_chain_is_refused(repo, names, remote):
    public_repo(repo, PIECE, sets=("main=main", "free-ref=refs/notes/*"))
    tree = head(repo, "HEAD^{tree}")
    refused(push(repo, "origin", f"{tree}:refs/notes/tree"), "not a commit")
    git(repo, "tag", "-a", "-m", "a tree", "docs/tree", tree)
    git(repo, "config", "--add", "moxi-repo.piece.publishing/push.free-ref",
        "refs/tags/docs/*")
    refused(push(repo, "origin", "docs/tree"), "not a commit")


@pytest.mark.parametrize("count", [2, 3, 4])
def test_a_push_creating_more_than_one_tag_is_refused_naming_the_count(
        repo, names, remote, count):
    public_repo(repo, PIECE, sets=("main=main", "free-ref=refs/tags/docs/*"))
    for n in range(count):
        git(repo, "tag", f"docs/t{n}")
    done = push(repo, "origin", *[f"docs/t{n}" for n in range(count)])
    refused(done, f"the push creates {count} tags; a push creates at most one tag, so "
                  "each gets its own push event and release run: push them one at a time")
    assert git(remote, "tag", "--list").stdout == ""
    assert log_lines() == []
    # One at a time, each passes.
    for n in range(count):
        passed(push(repo, "origin", f"docs/t{n}"))


def test_one_tag_per_push_counts_tags_of_every_kind(repo, names, remote):
    public_repo(repo, PIECE, sets=("main=main", "free-ref=refs/tags/docs/*",
                                   "free-ref=refs/tags/note-*"))
    git(repo, "tag", "docs/light")
    git(repo, "tag", "-a", "-m", "annotated", "note-annotated")
    refused(push(repo, "origin", "docs/light", "note-annotated"), "creates 2 tags")


def test_one_tag_per_push_counts_only_tags_it_creates(repo, names, remote):
    public_repo(repo, PIECE, sets=("main=main", "free-ref=refs/tags/docs/*"))
    for name in ("docs/old", "docs/moved"):
        git(repo, "tag", name)
    passed(push(repo, "origin", "docs/old"))
    passed(push(repo, "origin", "docs/moved"))
    commit(repo, "Add")
    git(repo, "tag", "-f", "docs/moved")
    git(repo, "tag", "docs/new")
    # One created, one moved, one deleted, and a branch: one tag created, so it passes.
    passed(push(repo, "-f", "origin", "main", "docs/new", "docs/moved", ":refs/tags/docs/old"))
    assert sorted(git(remote, "tag", "--list").stdout.split()) == ["docs/moved", "docs/new"]


def test_a_ref_name_holding_a_private_name_is_refused(pub):
    git(pub, "branch", "fix-bobsecret")
    done = push(pub, "origin", "fix-bobsecret")
    refused(done, "refs/heads/fix-[private-name]: private-names/private-name")
    assert "bobsecret" not in done.stderr


# -- what is new to the remote --------------------------------------------------------------

def test_a_remote_with_no_tracking_refs(repo, names, remote, tmp_path):
    public_repo(repo, PIECE, sets=("main=main",))
    commit(repo, "Add")
    done = push(repo, str(remote), "main")
    # A bare URL never gets tracking refs, so the fix named is a named remote, not a fetch.
    refused(done, f"push through a named remote instead (git remote add NAME {remote},")
    assert "fetch first" not in done.stderr
    empty = tmp_path / "empty.git"
    git(tmp_path, "init", "-q", "--bare", str(empty))
    commit(repo, "A name", text="bobsecret\n")
    done = push(repo, str(empty), "main")
    refused(done, "note.md:2: private-names/private-name")
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    passed(push(repo, str(empty), "main"))
    assert set(log_lines()[-1]["commits"]) == set(
        git(repo, "rev-list", "HEAD").stdout.split())


def with_userinfo(scheme: str, secret: str, rest: str) -> str:
    """A URL whose userinfo carries `secret`, joined from parts so this file never holds
    one whole."""
    return f"{scheme}:/" + f"/alice:{secret}@{rest}"


def on_localhost(path) -> str:
    """`localhost` and `path` as a file URL's path (`/C:/...` on Windows)."""
    return "localhost" + Path(path).as_uri()[len("file://"):]


def test_a_urls_userinfo_is_never_printed_or_logged(repo, names, remote, tmp_path,
                                                    monkeypatch):
    from moxi_repo.api import HookContext

    from moxi_publishing import push as piece

    secret = token(13)
    assert piece._shown_url(with_userinfo("https", secret, "example.test/proj-a.git")) == \
        "https://example.test/proj-a.git"
    assert piece._shown_url("git@example.test:alice/proj-a.git") == \
        "git@example.test:alice/proj-a.git"
    public_repo(repo, PIECE, sets=("main=main",))
    commit(repo, "Add")
    # The piece as git's pre-push hook runs it for a push to the bare URL. It asks the
    # remote for its refs: Git for Windows reads no file URL with a host and names the URL
    # whole in its error, which the piece relays cleaned; elsewhere the remote answers.
    url = with_userinfo("file", secret, on_localhost(remote))
    monkeypatch.chdir(repo)
    outcome = piece.pre_push(HookContext(
        "pre-push", args=(url, url), settings={"main": ["main"]},
        stdin=f"refs/heads/main {head(repo)} refs/heads/main {'0' * 40}\n".encode()))
    said = "\n".join(outcome.lines)
    assert outcome.kind == "refuse" and secret not in said, said
    if os.name == "nt":
        assert f"can't ask file://{on_localhost(remote)} for its refs: " in said, said
        assert f"'//{on_localhost(remote)}'" in said, said
    else:
        assert f"no tracking refs for file://{on_localhost(remote)} here" in said, said
    assert not log_lines()
    if os.name == "nt":
        pytest.skip("Git for Windows reads no file URL with a host, so no push through one "
                    "reaches the piece: git refuses it first, in its own words")
    done = push(repo, url, "main")
    refused(done, f"no tracking refs for file://{on_localhost(remote)} here")
    assert secret not in done.stderr
    empty = tmp_path / "empty.git"
    git(tmp_path, "init", "-q", "--bare", str(empty))
    passed(push(repo, with_userinfo("file", secret, on_localhost(empty)), "main"))
    logged = log_lines()[-1]
    assert logged["url"] == logged["remote"] == f"file://{on_localhost(empty)}"


def test_git_text_is_shown_without_the_push_urls_userinfo():
    from moxi_publishing import push as piece

    secret = token(15)
    url = with_userinfo("file", secret, "localhost/C:/w/remote.git")
    # Git for Windows' own words for that URL: a local path, the userinfo inside.
    text = ("fatal: '/" + f"/alice:{secret}@localhost/C:/w/remote.git' does not appear "
            "to be a git repository")
    assert piece._redacted(text, (url, url)) == \
        "fatal: '//localhost/C:/w/remote.git' does not appear to be a git repository"
    # Percent-encoded in the URL, written decoded; and any scheme's userinfo in a line.
    coded = with_userinfo("https", f"{secret[:6]}%40{secret[6:]}", "example.test/a.git")
    assert piece._redacted(f"x alice:{secret[:6]}@{secret[6:]}@example.test y",
                           (coded,)) == "x example.test y"
    other = with_userinfo("https", token(16), "example.test/b.git")
    assert piece._redacted(f"see '{other}' and {other}.", ()) == \
        "see 'https://example.test/b.git' and https://example.test/b.git."
    assert piece._redacted("fatal: https://example.test/a@b.git", ()) == \
        "fatal: https://example.test/a@b.git"


def test_a_login_name_is_no_secret_and_stays_in_git_text():
    from moxi_publishing import push as piece

    line = "github.com/git/r: git push refused by git@github.com"
    assert piece._redacted(line, ("ssh://git@github.com/o/r.git",)) == line
    assert piece._redacted("fatal: the gitlab remote", ("file://git@host/r.git",)) == \
        "fatal: the gitlab remote"


def test_a_password_is_taken_out_alone_too_and_a_token_standing_as_the_user():
    from moxi_publishing import push as piece

    secret = token(12)
    url = with_userinfo("https", secret, "example.test/a.git")
    text = f"x{secret}y and alice:{secret}@example.test and {secret}."
    assert piece._redacted(text, (url,)) == "xy and example.test and ."
    bare = f"https://{secret}" + "@example.test/a.git"   # a token as the user
    assert piece._redacted(f"denied for {secret}", (bare,)) == "denied for "


def test_a_shown_url_drops_userinfo_up_to_the_last_at_before_the_path():
    from moxi_publishing import push as piece

    secret = token(14)
    host = "example.test/proj-a.git"
    shown = "https://example.test/proj-a.git"
    # An unencoded `@` in the password: git reads the host after the last one.
    split = f"{secret[:7]}@{secret[7:]}"
    assert piece._shown_url(with_userinfo("https", split, host)) == shown
    assert secret[7:] not in piece._shown_url(with_userinfo("https", split, host))
    assert piece._shown_url(with_userinfo("https", f"{secret[:7]}%40{secret[7:]}", host)) \
        == shown
    assert piece._shown_url("https:/" + f"/alice@{host}") == shown
    assert piece._shown_url("alice@example.test:proj-a.git") == \
        "alice@example.test:proj-a.git"
    assert piece._shown_url("https://example.test/alice@proj-a.git") == \
        "https://example.test/alice@proj-a.git"
    assert piece._shown_url(with_userinfo("https", secret, "example.test/a@b.git")) == \
        "https://example.test/a@b.git"


def test_an_intermediate_commits_lines_are_read(pub):
    a = commit(pub, "Add a name", text="bobsecret\n")
    assert a.returncode == 0
    sha = head(pub)
    commit(pub, "Take it out", text="")
    (pub / "note.md").write_text("clean\n", encoding="utf-8")
    git(pub, "commit", "-q", "-am", "Clean")
    refused(push(pub, "origin", "main"), f"{sha[:12]} note.md:1: private-names/private-name")


def test_a_diff_attribute_hides_nothing(pub):
    (pub / ".gitattributes").write_text("*.md -diff\n", encoding="utf-8")
    git(pub, "add", ".gitattributes")
    git(pub, "commit", "-q", "-m", "Attributes")
    commit(pub, "Add", name="doc.md", text=f"key = {token(31)}\n")
    refused(push(pub, "origin", "main"), "doc.md:1: credentials/")


def test_the_allowlists_are_read_from_the_pushed_commit(pub):
    value = token(32)
    allow = "\t".join(("a.ini", "credentials/*", fingerprint(value), "2026-01-02", "why"))
    (pub / ".secrets-allow").write_text(allow + "\n", encoding="utf-8")
    commit(pub, "Add", name="a.ini", text=f"key = {value}\n")
    refused(push(pub, "origin", "main"), "a.ini:1: credentials/")
    # An allowance committed later doesn't reach back: it must be in the commit judged.
    git(pub, "add", ".secrets-allow")
    git(pub, "commit", "-q", "-m", "Allow")
    refused(push(pub, "origin", "main"), "a.ini:1: credentials/")
    git(pub, "reset", "-q", "--soft", "HEAD~2")
    git(pub, "commit", "-q", "-m", "Add with its allowance")
    passed(push(pub, "origin", "main"))


# -- merges ---------------------------------------------------------------------------------

def test_a_merge_resolution_line_committed_without_hooks_is_refused(pub):
    commit(pub, "Base", name="doc.md", text="base\n")
    git(pub, "switch", "-q", "-c", "side")
    (pub / "doc.md").write_text("side\n", encoding="utf-8")
    git(pub, "commit", "-q", "-am", "Side")
    git(pub, "switch", "-q", "main")
    (pub / "doc.md").write_text("main\n", encoding="utf-8")
    git(pub, "commit", "-q", "-am", "Main")
    git(pub, "merge", "-q", "side", check=False)
    (pub / "doc.md").write_text("side\nresolved by bobsecret\n", encoding="utf-8")
    git(pub, "add", "doc.md")
    git(pub, "commit", "-q", "--no-verify", "--no-edit")
    done = push(pub, "origin", "main")
    refused(done, f"{head(pub)[:12]} doc.md:2: private-names/private-name")
    assert f"{head(pub)[:12]} doc.md:1:" not in done.stderr


def test_a_merge_of_a_public_branch_does_not_re_read_it(pub):
    git(pub, "switch", "-q", "-c", "side")
    commit(pub, "Side", name="side.md", text="side says laterword\n")
    passed(push(pub, "origin", "side"))
    git(pub, "switch", "-q", "main")
    commit(pub, "Main", name="main.md", text="main\n")
    git(pub, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    write_config("secrets", '[private_names]\nwords = ["bobsecret", "laterword"]\n'
                            '[author]\nname = "operator"\n'
                            'email = "operator@example.test"\n')
    passed(push(pub, "origin", "main"))


# -- messages and headers -------------------------------------------------------------------

def test_a_message_is_judged_as_at_commit(pub):
    commit(pub, f"Add\n\nsee {POINTER}\n")
    refused(push(pub, "origin", "main"), "message line 3, session-pointer")


def test_a_provenance_line_at_push_masks_a_credential(pub):
    secret = token(12)
    commit(pub, f"Add\n\nsee {POINTER} key {secret}\n", text=f"see {POINTER} {secret}\n")
    done = push(pub, "origin", "main")
    refused(done, f"message line 3, session-pointer: see {POINTER} key [github-pat]",
            f"note.md:1: session-pointer: see {POINTER} [github-pat]")
    assert secret not in done.stderr


def test_commit_rules_are_applied_at_push(repo, names, remote):
    public_repo(repo, PIECE, "repo/commit-rules",
                sets=("main=main", "refused-trailer=Round",
                      "tag-pattern=" + r"\[[A-Z][0-9]+\]"))
    commit(repo, "Add\n\nBody.\n\nRound: proj-a/one\n", flags=("--no-verify",))
    refused(push(repo, "origin", "main"), "Round")
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    commit(repo, f"Fix {TAG}\n", flags=("--no-verify",))
    refused(push(repo, "origin", "main"), TAG)
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    # -m keeps a comment-prefixed line, so it is the commit's own and is judged.
    git(repo, "commit", "-q", "--allow-empty", "-m", "Fix it", "-m", f"# per {TAG}")
    refused(push(repo, "origin", "main"), TAG)


def test_commit_rules_reach_the_trailers_at_push_as_the_repo_sets(repo, names, remote):
    public_repo(repo, PIECE, "repo/commit-rules",
                sets=("main=main", "tag-pattern=" + r"\[[A-Z][0-9]+\]"))
    commit(repo, f"Add\n\nBody.\n\nNote: see {TAG}\n", flags=("--no-verify",))
    passed(push(repo, "origin", "main"))
    git(repo, "config", "moxi-repo.piece.repo/commit-rules.tag-check-trailers", "true")
    commit(repo, f"Add more\n\nBody.\n\nNote: also {TAG}\n", flags=("--no-verify",))
    refused(push(repo, "origin", "main"), TAG)


@pytest.mark.parametrize("setting, key", [("drop-trailer", "Claude-Session"),
                                          ("pass-trailer", "Commit-Check")])
def test_a_leftover_key_in_the_repos_own_commit_is_refused(repo, names, remote, setting,
                                                           key):
    public_repo(repo, PIECE, sets=("main=main",))
    git(repo, "config", f"moxi-repo.piece.publishing/message.{setting}", key)
    commit(repo, f"Add\n\nBody.\n\n{key}: abc\n", flags=("--no-verify",))
    refused(push(repo, "origin", "main"), f"{key} is still there")


def test_a_foreign_commits_leftover_key_passes_but_its_text_is_still_read(repo, names,
                                                                         remote):
    public_repo(repo, PIECE, sets=("main=main",))
    git(repo, "config", "moxi-repo.piece.publishing/message.drop-trailer", "Co-authored-by")
    alice = {"GIT_COMMITTER_NAME": "alice", "GIT_COMMITTER_EMAIL": "alice@example.test",
             "GIT_AUTHOR_NAME": "alice", "GIT_AUTHOR_EMAIL": "alice@example.test"}
    commit(repo, "Add\n\nBody.\n\nCo-authored-by: carol <carol@example.test>\n", env=alice)
    passed(push(repo, "origin", "main"))
    commit(repo, "Add\n\nBody.\n\nCo-authored-by: bobsecret <carol@example.test>\n",
           env=alice)
    refused(push(repo, "origin", "main"), "private-names/private-name")


def test_ownership_is_read_from_the_committer_not_the_author(repo, names, remote):
    public_repo(repo, PIECE, sets=("main=main",))
    git(repo, "config", "moxi-repo.piece.publishing/message.drop-trailer", "Co-authored-by")
    alice = {"GIT_COMMITTER_NAME": "alice", "GIT_COMMITTER_EMAIL": "alice@example.test",
             "GIT_AUTHOR_NAME": "alice", "GIT_AUTHOR_EMAIL": "alice@example.test"}
    git(repo, "switch", "-q", "-c", "topic")
    commit(repo, "Add\n\nBody.\n\nCo-authored-by: carol <carol@example.test>\n", env=alice)
    git(repo, "switch", "-q", "main")
    # A cherry-pick keeps alice as the author and makes the repo's identity the committer.
    git(repo, "cherry-pick", "topic")
    assert git(repo, "log", "-1", "--format=%ae %ce").stdout.split() == [
        "alice@example.test", "operator@example.test"]
    refused(push(repo, "origin", "main"), "Co-authored-by is still there")
    # The other way round: the repo's identity authored it, alice committed it.
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    commit(repo, "Add\n\nBody.\n\nCo-authored-by: carol <carol@example.test>\n",
           env={"GIT_COMMITTER_NAME": "alice", "GIT_COMMITTER_EMAIL": "alice@example.test"})
    passed(push(repo, "origin", "main"))


@pytest.mark.parametrize("env, header", [
    ({"GIT_AUTHOR_NAME": "bobsecret"}, "author header"),
    ({"GIT_COMMITTER_EMAIL": "bob@private.example.test"}, "committer header"),
])
def test_author_and_committer_headers_are_read(pub, env, header):
    commit(pub, "Add", env=env)
    done = push(pub, "origin", "main")
    refused(done, f"{header}: private-names/")
    assert "bobsecret" not in done.stderr and "bob@private" not in done.stderr


def test_the_repos_own_author_email_is_not_a_hit(pub):
    commit(pub, "Add", env={"GIT_AUTHOR_EMAIL": "operator@example.test"})
    passed(push(pub, "origin", "main"))


SCISSORS = "# " + "-" * 24 + " >8 " + "-" * 24


def test_text_below_a_scissors_line_is_read_at_push(repo, names, remote):
    public_repo(repo, PIECE, sets=("main=main", "free-ref=refs/tags/docs/*"))
    # Only an edited message is cut there; -F, -m and a verbatim tag keep every line.
    commit(repo, f"Add\n\n{SCISSORS}\nnamed for bobsecret\nkey {token(3)}\n")
    refused(push(repo, "origin", "main"), "message line 4, private-names/",
            "message line 5, credentials/")
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    git(repo, "commit", "-q", "--allow-empty", "-m", "Add", "-m", SCISSORS,
        "-m", f"named for bobsecret, key {token(3)}")
    refused(push(repo, "origin", "main"), "message line 5, private-names/",
            "message line 5, credentials/")
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    git(repo, "tag", "-a", "--cleanup=verbatim", "-m", f"Docs\n{SCISSORS}\nkey {token(3)}",
        "docs/a")
    refused(push(repo, "origin", "docs/a"), "tag refs/tags/docs/a message line 3, credentials/")


def test_tag_objects_are_read_down_the_chain(repo, names, remote):
    public_repo(repo, PIECE, sets=("main=main", "free-ref=refs/tags/docs/*"))
    git(repo, "tag", "-a", "-m", "fine", "docs/a")
    passed(push(repo, "origin", "docs/a"))
    git(repo, "tag", "-a", "-m", "named for bobsecret", "docs/inner")
    git(repo, "tag", "-a", "-m", "outer", "docs/outer", "docs/inner")
    git(repo, "tag", "-d", "docs/inner")
    refused(push(repo, "origin", "docs/outer"), "tag refs/tags/docs/outer message line 1")
    git(repo, "tag", "-a", "-m", "fine", "docs/b", env={"GIT_COMMITTER_NAME": "bobsecret"})
    refused(push(repo, "origin", "docs/b"), "tagger header: private-names/")


@pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                    reason="a signed tag here is signed with an ssh key")
def test_a_merged_signed_tags_header_is_read(pub, tmp_path):
    key = tmp_path / "signing-key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "operator",
                    "-f", str(key)], check=True, capture_output=True, timeout=60)
    git(pub, "config", "gpg.format", "ssh")
    git(pub, "config", "user.signingKey", str(key))
    git(pub, "switch", "-q", "-c", "side")
    commit(pub, "Side", name="side.md")
    git(pub, "tag", "-s", "-m", "release by bobsecret", "docs/side")
    git(pub, "switch", "-q", "main")
    commit(pub, "Main", name="main.md")
    done = git(pub, "merge", "-q", "--no-ff", "-m", "Merge", "docs/side", check=False)
    assert done.returncode == 0, done.stderr
    assert "\nmergetag " in git(pub, "cat-file", "commit", "HEAD").stdout
    refused(push(pub, "origin", "main"), "mergetag message line 1")


# -- the log --------------------------------------------------------------------------------

def test_a_log_that_cannot_be_written_refuses(pub):
    from moxi_publishing import push as piece

    path = piece.log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    commit(pub, "Add")
    refused(push(pub, "origin", "main"), "push log can't be written")


def test_the_piece_without_the_flag_refuses(pub):
    git(pub, "config", "--unset", "moxi-publishing.public")
    refused(push(pub, "origin", "main"), "isn't marked public")

"""`moxi publishing scan`: a commit's whole tree, read as git stores it, with the allowlists
of that tree; `--ci` without the private names or the private allowlist."""

from __future__ import annotations

import pytest
from conftest import git, token

from moxi_publishing import cli

POINTER = "0123abcd9" + "@" + "2026-01-02"


def put(repo, name, text, message="Add"):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-q", "-m", message)


def scan(capsys, *args):
    code = cli.main(["scan", *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_a_clean_tree_exits_0(repo, names, capsys):
    code, out, _ = scan(capsys, str(repo))
    assert (code, out) == (0, "")


def test_every_file_and_path_name_is_read(repo, names, capsys):
    value = token(21)
    put(repo, ".gitattributes", "*.md -diff\n*.txt diff=hide\n")
    git(repo, "config", "diff.hide.textconv", "true")
    put(repo, "docs/a.md", "line one\nask bobsecret\n")
    put(repo, "conf/b.txt", f"key = {value}\n")
    put(repo, "notes/c.rst", f"see {POINTER}\n")
    put(repo, "bobsecret.md", "plain\n")
    code, out, err = scan(capsys, str(repo))
    assert code == 3
    assert out.splitlines() == [
        "[private-name].md (path name): private-names/private-name",
        "conf/b.txt:1: credentials/github-pat",
        "docs/a.md:2: private-names/private-name",
        f"notes/c.rst:1: session-pointer: see {POINTER}",
    ]
    assert value not in out + err
    assert "bobsecret" not in out + err


def test_rev_picks_the_tree(repo, names, capsys):
    put(repo, "a.md", "bobsecret\n")
    assert scan(capsys, str(repo))[0] == 3
    assert scan(capsys, str(repo), "--rev", "HEAD~1")[0] == 0
    git(repo, "rm", "-q", "a.md")
    git(repo, "commit", "-q", "-m", "Remove")
    assert scan(capsys, str(repo))[0] == 0


def test_ci_leaves_out_private_names_but_not_credentials(repo, names, capsys):
    put(repo, "a.md", "bobsecret\n")
    assert scan(capsys, str(repo), "--ci")[:2] == (0, "")
    put(repo, "b.md", f"key = {token(22)}\n")
    code, out, _ = scan(capsys, str(repo), "--ci")
    assert (code, out) == (3, "b.md:1: credentials/github-pat\n")


def test_the_allowlists_are_read_from_the_tree_scanned(repo, names, capsys):
    value = token(23)
    from moxi_secrets.scan import fingerprint

    put(repo, "b.md", f"key = {value}\nsee {POINTER}\n")
    allow = "\t".join(("b.md", "credentials/*", fingerprint(value), "2026-01-02", "why"))
    (repo / ".secrets-allow").write_text(allow + "\n", encoding="utf-8")
    (repo / ".residue-allow").write_text(f"b.md\t{POINTER}\twhy\n", encoding="utf-8")
    assert scan(capsys, str(repo))[0] == 3, "work tree allowances must not count"
    git(repo, "add", ".secrets-allow", ".residue-allow")
    git(repo, "commit", "-q", "-m", "Allow")
    assert scan(capsys, str(repo))[:2] == (0, "")
    assert scan(capsys, str(repo), "--ci")[:2] == (0, "")


def test_the_private_allowlist_counts_except_under_ci(repo, names, capsys):
    put(repo, "a.md", "bobsecret\n")
    (repo / ".git" / "secrets-allow-private").write_text(
        "\t".join(("a.md", "private-names/*", "text:bobsecret", "2026-01-02", "why")) + "\n",
        encoding="utf-8")
    assert scan(capsys, str(repo))[:2] == (0, "")
    put(repo, "c.md", f"key = {token(24)}\n")
    (repo / ".git" / "secrets-allow-private").write_text("not a line\n", encoding="utf-8")
    assert scan(capsys, str(repo))[0] == 1
    assert scan(capsys, str(repo), "--ci")[0] == 3


def test_a_linked_worktree_scans_its_own_head(repo, names, capsys, tmp_path):
    git(repo, "worktree", "add", "-q", "-b", "side", str(tmp_path / "side"))
    put(tmp_path / "side", "a.md", "bobsecret\n")
    assert scan(capsys, str(repo))[0] == 0
    assert scan(capsys, str(tmp_path / "side"))[0] == 3


@pytest.mark.parametrize("args", [("--rev", "no-such-rev"), ()])
def test_a_tree_that_cannot_be_read_fails(tmp_path, gitenv, names, capsys, args):
    repo = tmp_path / "proj-b"
    git(tmp_path, "init", "-q", str(repo))
    code, out, err = scan(capsys, str(repo), *args)
    assert (code, out) == (1, "")
    assert "names no commit or tree" in err


def test_not_a_repo_is_a_usage_error(tmp_path, gitenv, capsys):
    code, _, err = scan(capsys, str(tmp_path))
    assert code == 2 and "no git work tree" in err


# -- the commits a push brings: --since -----------------------------------------------------

def test_since_reads_each_commits_message_and_headers(repo, names, capsys):
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    put(repo, "a.md", "clean\n", message=f"Add a\n\nsee {POINTER}\n")
    git(repo, "commit", "-q", "--allow-empty", "-m", "Quiet",
        env={"GIT_AUTHOR_NAME": "bobsecret"})
    code, out, _ = scan(capsys, str(repo))
    assert code == 0
    code, out, err = scan(capsys, str(repo), "--since", base)
    assert code == 3
    lines = out.splitlines()
    assert any(line.endswith("author header: private-names/private-name") for line in lines)
    assert any(f"message line 3, session-pointer: see {POINTER}" in line for line in lines)
    assert "bobsecret" not in out + err
    # The commits the base already holds aren't read again.
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert scan(capsys, str(repo), "--since", head)[0] == 0


def test_since_with_ci_leaves_out_private_names(repo, names, capsys):
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "commit", "-q", "--allow-empty", "-m", "Quiet",
        env={"GIT_AUTHOR_NAME": "bobsecret"})
    assert scan(capsys, str(repo), "--since", base, "--ci")[0] == 0
    git(repo, "commit", "-q", "--allow-empty", "-m", f"key {token(5)}")
    code, out, _ = scan(capsys, str(repo), "--since", base, "--ci")
    assert code == 3 and "credentials/github-pat" in out


def test_a_tag_named_as_rev_has_its_message_and_tagger_read(repo, names, capsys):
    git(repo, "tag", "-a", "-m", f"Release\n\nsee {POINTER}", "docs-1",
        env={"GIT_COMMITTER_EMAIL": "bob@private.example.test"})
    code, out, _ = scan(capsys, str(repo), "--rev", "docs-1", "--since", "HEAD")
    assert code == 3
    assert "tag docs-1 tagger header: private-names/" in out
    assert "tag docs-1 message line 3, session-pointer" in out
    assert scan(capsys, str(repo), "--rev", "docs-1")[0] == 3

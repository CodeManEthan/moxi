"""A local replace ref (`git replace`) changes what most git reads see, while a push sends
the objects themselves. Every read here ignores replace refs, so one changes no verdict."""

from __future__ import annotations

from conftest import commit, git, public_repo

from moxi_publishing import cli


def twin(repo, rev: str, name: str, text: str) -> str:
    """A commit with `rev`'s parents and message, whose tree has `name` holding `text`."""
    git(repo, "read-tree", rev)
    blob = git(repo, "hash-object", "-w", "--stdin", stdin_data=text).stdout.strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
    tree = git(repo, "write-tree").stdout.strip()
    git(repo, "read-tree", "HEAD")
    parents = git(repo, "rev-list", "--parents", "-n", "1", rev).stdout.split()[1:]
    args = [arg for parent in parents for arg in ("-p", parent)]
    return git(repo, "commit-tree", tree, *args, "-m", "Add").stdout.strip()


def hide(repo, name: str, text: str) -> None:
    """Stand a clean twin in for HEAD."""
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "replace", head, twin(repo, head, name, text))


def scan(capsys, *args):
    code = cli.main(["scan", *args])
    return code, capsys.readouterr().out


def test_a_replaced_commit_is_read_as_it_is_at_push_and_in_scan(repo, names, remote,
                                                                 capsys):
    public_repo(repo, "publishing/push", sets=("main=main",))
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert commit(repo, "Add", text="named for bobsecret\n").returncode == 0
    hide(repo, "note.md", "a clean line\n")
    assert "bobsecret" not in git(repo, "show", "HEAD:note.md").stdout
    done = git(repo, "push", "-q", "origin", "main", check=False)
    assert done.returncode != 0 and "note.md:1: private-names/" in done.stderr
    code, out = scan(capsys, str(repo))
    assert code == 3 and "note.md:1: private-names/" in out
    git(repo, "commit", "-q", "--allow-empty", "-m", "Quiet",
        env={"GIT_AUTHOR_NAME": "bobsecret"})
    clean = twin(repo, "HEAD", "other.md", "x\n")
    git(repo, "replace", "-f", git(repo, "rev-parse", "HEAD").stdout.strip(), clean)
    code, out = scan(capsys, str(repo), "--from", base)
    assert code == 3 and "author header: private-names/" in out


def test_a_replaced_head_hides_nothing_at_commit(repo, names):
    public_repo(repo, "publishing/content")
    # The twin already holds the line, so a diff against it would show nothing added.
    hide(repo, "note.md", "named for bobsecret\n")
    done = commit(repo, "Add", text="named for bobsecret\n")
    assert done.returncode != 0 and "note.md:1: private-names/" in done.stderr

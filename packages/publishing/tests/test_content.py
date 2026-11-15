"""The content piece: what a commit adds, read whole and as git stores it, judged with the
allowlists of the index; and the patch reader under it."""

from __future__ import annotations

import pytest
from conftest import commit, fingerprint, git, public_repo, token, write_config

from moxi_publishing import content

PIECE = "publishing/content"
#: A session pointer, joined at run time so no file spells one whole.
POINTER = "0123abcd9" + "@" + "2026-01-02"
NAMES = ('[private_names]\nwords = ["bobsecret", "laterword"]\n'
         'emails = ["bob@private.example.test"]\n'
         '[author]\nname = "operator"\nemail = "operator@example.test"\n')


@pytest.fixture
def pub(repo, names):
    return public_repo(repo, PIECE)


def allow_line(path, which, match):
    return "\t".join((path, which, match, "2026-01-02", "made-up reason")) + "\n"


def stage(repo, name, text):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "--", name)


# -- what is read ---------------------------------------------------------------------------

def test_only_added_lines_are_read(repo, names):
    commit(repo, "Old text", text="bobsecret was here before\n")
    public_repo(repo, PIECE)
    assert commit(repo, "Add a line", text="a plain line\n").returncode == 0
    done = commit(repo, "Add a name", text="ask bobsecret\n")
    assert done.returncode != 0
    assert "note.md:3: private-names/private-name" in done.stderr
    assert "ask bobsecret" not in done.stderr


def test_a_credential_is_refused_by_its_rule_and_never_shown(pub):
    value = token(11)
    done = commit(pub, "Add a key", name="conf/settings.ini", text=f"key = {value}\n")
    assert done.returncode != 0
    assert "conf/settings.ini:1: credentials/" in done.stderr
    assert value not in done.stderr


def test_a_provenance_hit_is_shown_with_its_line(pub):
    done = commit(pub, "Add a pointer", text=f"see {POINTER} for why\n")
    assert done.returncode != 0
    assert f"note.md:1: session-pointer: see {POINTER} for why" in done.stderr
    assert "Nothing was committed" in done.stderr


def test_a_credential_beside_a_provenance_hit_is_masked(pub):
    value = token(12)
    done = commit(pub, "Add", text=f"see {POINTER} key {value} bobsecret\n")
    assert done.returncode != 0
    assert (f"note.md:1: session-pointer: see {POINTER} key [github-pat] [private-name]"
            in done.stderr)
    assert value not in done.stderr and "bobsecret" not in done.stderr
    # An allowance passes the credential but never prints it.
    out = content.judge([("a.md", 1, f"see {POINTER} key {value}")], sets=("credentials",),
                        allow=_allow_all(value))
    assert out == [f"a.md:1: session-pointer: see {POINTER} key [github-pat]"]
    out = content.judge([], [f"docs/{POINTER} {value}.md"], sets=("credentials",))
    assert f"docs/{POINTER} [github-pat].md (path name): session-pointer" in out
    assert not any(value in line for line in out)


def _allow_all(value):
    from moxi_secrets import Allowlist

    return Allowlist.from_text(allow_line("a.md", "credentials/*", fingerprint(value))
                               .encode(), None, public_name=".secrets-allow")


def test_a_moved_file_is_read_whole(repo, names):
    commit(repo, "Old text", name="old.md", text="bobsecret\n")
    public_repo(repo, PIECE)
    git(repo, "mv", "old.md", "new.md")
    done = git(repo, "commit", "-q", "-m", "Move it", check=False)
    assert done.returncode != 0
    assert "new.md:1: private-names/private-name" in done.stderr


@pytest.mark.parametrize("attributes, config", [
    ("*.md -diff\n", ()),
    ("*.md binary\n", ()),
    ("*.md diff=hide\n", ("diff.hide.textconv", "true")),
    ("*.md diff=hide\n", ("diff.hide.command", "true")),
], ids=["no-diff", "binary", "textconv", "external-diff"])
def test_no_attribute_or_driver_hides_a_file(pub, attributes, config):
    stage(pub, ".gitattributes", attributes)
    assert git(pub, "commit", "-q", "-m", "Attributes", check=False).returncode == 0
    if config:
        git(pub, "config", *config)
    git(pub, "config", "diff.external", "true")
    done = commit(pub, "Add a name", name="doc.md", text="bobsecret\n")
    assert done.returncode != 0
    assert "doc.md:1: private-names/private-name" in done.stderr


def test_a_new_path_name_is_read_and_its_hit_masked(pub):
    done = commit(pub, "Add a file", name="bobsecret/plan.md", text="plain\n")
    assert done.returncode != 0
    assert "[private-name]/plan.md (path name): private-names/private-name" in done.stderr
    assert "bobsecret" not in done.stderr
    git(pub, "reset", "-q")
    done = commit(pub, "Add a file", name=f"log-{POINTER}.md", text="plain\n")
    assert done.returncode != 0
    assert f"log-{POINTER}.md (path name): session-pointer" in done.stderr


def test_the_first_commit_of_an_empty_repo_is_read(tmp_path, gitenv, names):
    repo = tmp_path / "proj-b"
    git(tmp_path, "init", "-q", str(repo))
    public_repo(repo, PIECE)
    done = commit(repo, "Start", text="bobsecret\n")
    assert done.returncode != 0
    assert "note.md:1: private-names/private-name" in done.stderr


# -- merges ---------------------------------------------------------------------------------

@pytest.fixture
def conflicted(pub):
    """A merge stopped on a conflict: the side branch's line holds a word that became a
    private name after the side commit was made."""
    commit(pub, "Base", name="doc.md", text="base\n")
    git(pub, "switch", "-q", "-c", "side")
    stage(pub, "doc.md", "side says laterword\n")
    git(pub, "commit", "-q", "-m", "Side")
    git(pub, "switch", "-q", "main")
    stage(pub, "doc.md", "main says hello\n")
    git(pub, "commit", "-q", "-m", "Main")
    write_config("secrets", NAMES)
    assert git(pub, "merge", "side", check=False).returncode != 0
    return pub


def test_a_merge_does_not_refuse_what_its_other_parent_already_holds(conflicted):
    stage(conflicted, "doc.md", "side says laterword\n")
    done = git(conflicted, "commit", "--no-edit", check=False)
    assert done.returncode == 0, done.stderr


def test_a_merge_refuses_its_own_lines(conflicted):
    stage(conflicted, "doc.md", "side says laterword\nmerged by bobsecret\n")
    done = git(conflicted, "commit", "--no-edit", check=False)
    assert done.returncode != 0
    assert "doc.md:2: private-names/private-name" in done.stderr
    assert "doc.md:1:" not in done.stderr


def test_a_merge_reads_a_new_path_only_when_no_parent_has_it(conflicted):
    stage(conflicted, "doc.md", "side says laterword\n")
    stage(conflicted, "laterword.md", "plain\n")
    done = git(conflicted, "commit", "--no-edit", check=False)
    assert done.returncode != 0
    assert "[private-name].md (path name)" in done.stderr


# -- the allowlists -------------------------------------------------------------------------

def test_a_credential_allowed_in_the_index_passes_and_an_unstaged_one_does_not(pub):
    value = token(12)
    line = allow_line("conf/a.ini", "credentials/*", fingerprint(value))
    (pub / ".secrets-allow").write_text(line, encoding="utf-8")
    done = commit(pub, "Add a key", name="conf/a.ini", text=f"key = {value}\n")
    assert done.returncode != 0, "an allowance in the work tree only must not count"
    git(pub, "add", ".secrets-allow")
    done = git(pub, "commit", "-q", "-m", "Add a key", check=False)
    assert done.returncode == 0, done.stderr


def test_a_staged_allowance_is_read_even_when_the_work_tree_drops_it(pub):
    value = token(13)
    stage(pub, ".secrets-allow", allow_line("*", "credentials/*", fingerprint(value)))
    (pub / ".secrets-allow").write_text("", encoding="utf-8")
    done = commit(pub, "Add a key", name="conf/a.ini", text=f"key = {value}\n")
    assert done.returncode == 0, done.stderr


def test_a_private_name_allowed_in_the_private_file_passes(pub):
    private = pub / ".git" / "secrets-allow-private"
    private.write_text(allow_line("note.md", "private-names/*", "text:bobsecret"),
                       encoding="utf-8")
    assert commit(pub, "Add a name", text="bobsecret\n").returncode == 0
    done = commit(pub, "Add it elsewhere", name="other.md", text="bobsecret\n")
    assert done.returncode != 0


def test_a_provenance_text_allowed_in_the_index_passes(pub):
    (pub / ".residue-allow").write_text(f"note.md\t{POINTER}\tmade-up reason\n",
                                        encoding="utf-8")
    done = commit(pub, "Add a pointer", text=f"see {POINTER}\n")
    assert done.returncode != 0, "an allowance in the work tree only must not count"
    git(pub, "add", ".residue-allow")
    assert git(pub, "commit", "-q", "-m", "Add", check=False).returncode == 0
    done = commit(pub, "Elsewhere", name="other.md", text=f"see {POINTER}\n")
    assert done.returncode != 0


def test_the_residue_allowlist_is_skipped_by_the_provenance_rules_only(pub):
    stage(pub, ".residue-allow", f"note.md\t{POINTER}\tmade-up reason\n")
    assert git(pub, "commit", "-q", "-m", "Allow", check=False).returncode == 0
    stage(pub, ".residue-allow", f"note.md\t{token(14)}\tmade-up reason\n")
    done = git(pub, "commit", "-q", "-m", "Allow", check=False)
    assert done.returncode != 0
    assert ".residue-allow:1: credentials/" in done.stderr


@pytest.mark.parametrize("name, text, said", [
    (".secrets-allow", "only one field\n", ":.secrets-allow: line 1"),
    (".residue-allow", "only-a-path\n", ":.residue-allow: line 1"),
])
def test_a_malformed_allowlist_refuses(pub, name, text, said):
    stage(pub, name, text)
    done = commit(pub, "Add", text="plain\n")
    assert done.returncode != 0
    assert "can't be scanned" in done.stderr and said in done.stderr


def test_a_set_that_cannot_run_refuses(pub):
    write_config("secrets", "[private_names]\nwords = [1]\n")
    done = commit(pub, "Add", text="plain\n")
    assert done.returncode != 0
    assert "can't be scanned" in done.stderr


def test_the_piece_without_the_flag_refuses(repo, names):
    public_repo(repo, PIECE)
    git(repo, "config", "--unset", "moxi-publishing.public")
    done = commit(repo, "Add", text="plain\n")
    assert done.returncode != 0
    assert "isn't marked public" in done.stderr


# -- the patch reader -----------------------------------------------------------------------

def test_the_patch_reader_takes_quoted_paths_and_header_like_lines_as_text(pub):
    names = ['with space.md', 'tab\there.md', 'quote"d.md', 'café.md', 'plain.md']
    for name in names:
        stage(pub, name, "+++ b/x\ndiff --git a/x b/x\n@@ -1 +1 @@\n")
    done = git(pub, "diff", "--cached", *content.DIFF_FLAGS)
    files = content.parse_patch(done.stdout.encode("utf-8", "surrogateescape"))
    assert sorted(f.path for f in files) == sorted(names)
    for f in files:
        assert f.new
        assert f.lines == [(1, "+++ b/x"), (2, "diff --git a/x b/x"), (3, "@@ -1 +1 @@")]


def test_the_patch_reader_numbers_lines_on_the_new_side():
    patch = (b"diff --git a/f b/f\nindex 1..2 100644\n--- a/f\n+++ b/f\n"
             b"@@ -2 +2,2 @@\n-old\n+new one\n+new two\n\\ No newline at end of file\n"
             b"@@ -9,0 +11 @@\n+late\n")
    [f] = content.parse_patch(patch)
    assert (f.path, f.new) == ("f", False)
    assert f.lines == [(2, "new one"), (3, "new two"), (11, "late")]


def test_the_residue_allowlist_format():
    pairs = content.residue_allowed("# comment\n\na.md\tX1\twhy\r\nb.md\tY2\n")
    assert pairs == {("a.md", "X1"), ("b.md", "Y2")}
    assert content.residue_allowed(None) == frozenset()

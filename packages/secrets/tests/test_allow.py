"""The allowlist: every valid form in each file and what it keeps, every malformed form
refused by file and line number without its text, and where the private file may lie."""

from __future__ import annotations

import importlib

import corpus
import pytest

from moxi_secrets import Allowlist, AllowlistError, scan
from moxi_secrets.allow import normalise_text

scan_mod = importlib.import_module("moxi_secrets.scan")

DAY = "2026-11-02"
#: Words that appear in the lines below and nowhere in any message the allowlist writes.
WHY = "zebrafinch-reason"


def line(path, which, match, day=DAY, why=WHY):
    return "\t".join((path, which, match, day, why))


@pytest.fixture
def config_dir():
    from moxi.paths import Layout

    path = Layout("secrets").config_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def private(tmp_path):
    """private(*lines) -> the path of a private file inside a repo's .git directory."""
    gitdir = tmp_path / "repo" / ".git"
    gitdir.mkdir(parents=True, exist_ok=True)

    def write(*lines):
        path = gitdir / "secrets-allow-private"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
    return write


@pytest.fixture
def public(tmp_path):
    """public(*lines) -> the path of a public `.secrets-allow` in a work tree."""
    def write(*lines):
        path = tmp_path / "repo" / ".secrets-allow"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
    return write


def token_text(seed=1):
    r = corpus.rng(seed)
    token = corpus.github(r)
    return token, f"key = {token}\n"


# -- the valid forms and what each keeps ----------------------------------------------------

def test_every_valid_form_of_the_public_file_parses_and_keeps_its_hit(public):
    token, text = token_text()
    fp = scan_mod.fingerprint(token)
    lines = [
        line("*", "credentials/github-pat", fp),
        line("docs/setup.md", "credentials/github-pat", fp.upper().replace("SHA256", "sha256")),
        line("tests/", "credentials/*", "*"),
        line("@author", "credentials/github-pat", "*"),
        line("*", "credentials/*", fp),
        line("docs/", "credentials/no-such-rule-any-more", "*"),
    ]
    allow = Allowlist.read(public(*lines, "", "# a comment", "   # indented comment"))
    assert len(allow) == len(lines)
    assert scan(text, ("credentials",)) != []
    for kept_path in (None, "docs/setup.md", "tests/a/b.py", "@author", "src/x.py"):
        assert scan(text, ("credentials",), path=kept_path, allow=allow) == [], kept_path


@pytest.mark.parametrize("entry, kept, not_kept", [
    # A path compared exactly.
    (("docs/setup.md", "credentials/github-pat", "*"), ["docs/setup.md"],
     [None, "docs/setup.mdx", "x/docs/setup.md", "docs"]),
    # A directory, matching every path under it.
    (("tests/", "credentials/github-pat", "*"), ["tests/a.py", "tests/deep/b.py"],
     [None, "tests", "src/tests/a.py", "testsuite/a.py"]),
    # The commit's author and committer headers.
    (("@author", "credentials/*", "*"), ["@author"], [None, "author", "src/@author"]),
    # Any path, but only this set and rule.
    (("*", "credentials/github-pat", "*"), [None, "a.txt"], []),
    (("*", "credentials/stripe-access-token", "*"), [], [None, "a.txt"]),
    # Another set's line keeps nothing of this one.
    (("docs/", "broad/*", "*"), [], [None, "docs/a.md"]),
    # Any set and rule, at one path.
    (("docs/", "*", "*"), ["docs/a.md"], [None, "src/a.md"]),
])
def test_each_path_and_rule_form_keeps_what_it_says(private, entry, kept, not_kept):
    _token, text = token_text(2)
    allow = Allowlist.read(None, private(line(*entry)))
    for path in kept:
        assert [h for h in scan(text, ("credentials",), path=path, allow=allow)
                if h.rule == "github-pat"] == [], path
    for path in not_kept:
        assert [h for h in scan(text, ("credentials",), path=path, allow=allow)
                if h.rule == "github-pat"] != [], path


def test_a_fingerprint_line_keeps_that_value_only(private):
    r = corpus.rng(3)
    a, b = corpus.github(r), corpus.github(r)
    text = f"a = {a}\nb = {b}\n"
    upper = "sha256:" + scan_mod.fingerprint(a)[len("sha256:"):].upper()
    allow = Allowlist.read(None, private(line("*", "credentials/github-pat", upper)))
    hits = [h for h in scan(text, ("credentials",), allow=allow) if h.rule == "github-pat"]
    assert [text[h.start:h.end] for h in hits] == [b]


def test_every_valid_form_of_the_private_file_parses(private):
    lines = [
        line("docs/", "broad/*", "*"),
        line("notes/", "personal-data/iban", "sha256:0123456789abcdef"),
        line("@author", "private-names/private-email", "text:someone@example.test"),
        line("*", "private-names/*", "text:Alexa Quill"),
        line("*", "private-names/private-name", "*"),
        line("src/a.py", "*", "sha256:FEDCBA9876543210"),
        line("*", "credentials/github-pat", "*"),
        line("*", "*", "sha256:0000000000000000"),
    ]
    assert len(Allowlist.read(None, private(*lines))) == len(lines)


def test_text_matches_are_normalised_on_both_sides(private, names_config):
    names_config(words=["alexa-quill", "alexa quill"])
    text = "alexa_quill, ALEXA-QUILL, alexa quill, Alexa 　 Quill"
    assert len(scan(text, ("private-names",))) == 4
    allow = Allowlist.read(None, private(line("*", "private-names/private-name",
                                              "text:  ALEXA_quill -")))
    left = [text[h.start:h.end] for h in scan(text, ("private-names",), allow=allow)]
    assert left == ["alexa quill", "Alexa 　 Quill"]
    spaced = Allowlist.read(None, private(line("*", "private-names/*",
                                               "text:Alexa _- Quill")))
    left = [text[h.start:h.end] for h in scan(text, ("private-names",), allow=spaced)]
    assert left == ["alexa_quill", "ALEXA-QUILL"]


def test_normalise_text():
    assert normalise_text("Alexa-Quill") == normalise_text("ALEXA_QUILL") == "alexa-quill"
    assert normalise_text("alexa quill") == "alexa quill"
    assert normalise_text(" a _- b ") == "a b"
    assert normalise_text("_-x-_") == "x"
    assert normalise_text("Straße") == "strasse"


def test_a_text_line_keeps_private_names_hits_only(private, names_config):
    names_config(words=["quillfeather"])
    allow = Allowlist.read(None, private(line("*", "private-names/*", "text:quillfeather")))
    assert scan("quillfeather", ("private-names",), allow=allow) == []
    known = scan("quillfeather", ("private-names",), allow=allow, known=["quillfeather"])
    assert [h.set for h in known] == ["known"]


def test_the_private_files_own_entries_are_kept(private, names_config):
    names_config(words=["alexa quill", "zedlander"], emails=["aq@example.test"])
    path = private(
        line("@author", "private-names/private-email", "text:aq@example.test"),
        line("*", "private-names/private-name", "text:Alexa Quill", why="for zedlander"),
    )
    own = path.read_text(encoding="utf-8")
    allow = Allowlist.read(None, path)
    assert {own[h.start:h.end] for h in scan(own, ("private-names",))} == {
        "aq@example.test", "Alexa Quill", "zedlander"}
    # Its own text: values are no hit in the file itself; another name still is.
    assert [own[h.start:h.end] for h in scan(own, ("private-names",), allow=allow)] == [
        "zedlander"]
    # Elsewhere the email line still needs its path.
    assert scan("mail aq@example.test", ("private-names",), allow=allow) != []
    assert scan("mail aq@example.test", ("private-names",), path="@author",
                allow=allow) == []


def test_a_crlf_private_files_own_entries_are_kept(private, names_config):
    # Scanned from its bytes, a CRLF file keeps its line ends; read with newline
    # translation, it doesn't. Its own text: values are no hit either way.
    names_config(words=["alexa quill", "zedlander"])
    # Scoped to @author, the line keeps nothing in the file itself; only the own-text
    # keep can.
    path = private(line("@author", "private-names/private-name", "text:Alexa Quill",
                        why="for zedlander"))
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    allow = Allowlist.read(None, path)
    for own in (path.read_bytes().decode("utf-8", "surrogateescape"),
                path.read_text(encoding="utf-8")):
        assert {own[h.start:h.end] for h in scan(own, ("private-names",))} == {
            "Alexa Quill", "zedlander"}
        assert [own[h.start:h.end] for h in scan(own, ("private-names",), allow=allow)] == [
            "zedlander"]


def test_missing_files_add_no_lines(tmp_path, config_dir):
    assert len(Allowlist.read(tmp_path / "none", config_dir / "none")) == 0
    assert len(Allowlist.read()) == 0


def test_windows_line_ends_parse(tmp_path):
    path = tmp_path / ".secrets-allow"
    path.write_bytes(("# c\r\n" + line("*", "credentials/jwt", "*") + "\r\n").encode())
    assert len(Allowlist.read(path)) == 1


# -- malformed lines ------------------------------------------------------------------------

BAD_ANYWHERE = [
    ("four fields", "\t".join(("docs/", "credentials/jwt", "*", DAY))),
    ("six fields", line("docs/", "credentials/jwt", "*") + "\textra"),
    ("one field", "docs/a.md credentials/jwt * " + DAY),
    ("an empty path", line("", "credentials/jwt", "*")),
    ("an absolute path", line("/docs/a.md", "credentials/jwt", "*")),
    ("a backslash path", line("docs\\a.md", "credentials/jwt", "*")),
    ("an unknown set", line("docs/", "secretz/jwt", "*")),
    ("a set with no rule", line("docs/", "credentials", "*")),
    ("a set and a slash", line("docs/", "credentials/", "*")),
    ("an empty rule field", line("docs/", "", "*")),
    ("15 hex", line("docs/", "credentials/jwt", "sha256:" + "a" * 15)),
    ("17 hex", line("docs/", "credentials/jwt", "sha256:" + "a" * 17)),
    ("not hex", line("docs/", "credentials/jwt", "sha256:" + "g" * 16)),
    ("an upper prefix", line("docs/", "credentials/jwt", "SHA256:" + "a" * 16)),
    ("a bare value", line("docs/", "credentials/jwt", "abc123")),
    ("a bad date", line("docs/", "credentials/jwt", "*", day="2026-1-2")),
    ("a date in words", line("docs/", "credentials/jwt", "*", day="today")),
    ("a date that is no day", line("docs/", "credentials/jwt", "*", day="2026-02-30")),
    ("all three *", line("*", "*", "*")),
    ("credentials everywhere", line("*", "credentials/*", "*")),
]

BAD_PUBLIC = [
    ("text: in the public file", line("docs/", "private-names/private-name", "text:x")),
    ("text: for a credential", line("docs/", "credentials/jwt", "text:x")),
    ("a broad line", line("docs/", "broad/*", "*")),
    ("a personal-data line", line("docs/", "personal-data/iban", "sha256:" + "a" * 16)),
    ("a private-names line", line("docs/", "private-names/*", "*")),
    ("a line for every set", line("docs/", "*", "*")),
]

BAD_PRIVATE = [
    ("text: for a credential", line("docs/", "credentials/jwt", "text:x")),
    ("text: for broad", line("docs/", "broad/*", "text:x")),
    ("text: for any set", line("docs/", "*", "text:x")),
    ("an empty text:", line("docs/", "private-names/*", "text: -_ ")),
    ("broad everywhere", line("*", "broad/*", "*")),
    ("private names everywhere", line("*", "private-names/*", "*")),
    ("personal data everywhere", line("*", "personal-data/*", "*")),
]


def assert_refused(read, n, path):
    with pytest.raises(AllowlistError) as caught:
        read()
    message = str(caught.value)
    assert str(path) in message and f"line {n}:" in message
    for word in (WHY, "docs/", "credentials/jwt", "aaaaaaaaaaaaaaa", "secretz", "abc123",
                 "2026-1-2", "2026-02-30", "extra"):
        assert word not in message, (word, message)


@pytest.mark.parametrize("what, bad", BAD_ANYWHERE + BAD_PUBLIC,
                         ids=[w for w, _ in BAD_ANYWHERE + BAD_PUBLIC])
def test_the_public_file_refuses_a_malformed_line_by_file_and_number(public, what, bad):
    good = line("docs/", "credentials/jwt", "*", why="fine")
    path = public("# header", good, "", bad, good)
    assert_refused(lambda: Allowlist.read(path), 4, path)


@pytest.mark.parametrize("what, bad", BAD_ANYWHERE + BAD_PRIVATE,
                         ids=[w for w, _ in BAD_ANYWHERE + BAD_PRIVATE])
def test_the_private_file_refuses_a_malformed_line_by_file_and_number(private, what, bad):
    good = line("docs/", "broad/*", "*", why="fine")
    path = private(good, bad)
    assert_refused(lambda: Allowlist.read(None, path), 2, path)


def test_a_file_that_is_not_utf8_is_refused(tmp_path):
    path = tmp_path / ".secrets-allow"
    path.write_bytes(b"\xff\xfe" + line("*", "credentials/jwt", "*").encode())
    with pytest.raises(AllowlistError):
        Allowlist.read(path)


# -- where the private file may lie ---------------------------------------------------------

def private_line():
    return line("*", "private-names/*", "text:x") + "\n"


def test_the_private_file_may_lie_in_a_git_directory_or_the_config_directory(
        tmp_path, config_dir):
    in_git = tmp_path / "repo" / ".git" / "secrets-allow-private"
    in_git.parent.mkdir(parents=True)
    in_git.write_text(private_line(), encoding="utf-8")
    worktree_git = tmp_path / "repo" / ".git" / "worktrees" / "b" / "allow"
    worktree_git.parent.mkdir(parents=True)
    worktree_git.write_text(private_line(), encoding="utf-8")
    in_config = config_dir / "allow-private"
    in_config.write_text(private_line(), encoding="utf-8")
    nested = config_dir / "more" / "allow"
    nested.parent.mkdir()
    nested.write_text(private_line(), encoding="utf-8")
    for path in (in_git, worktree_git, in_config, nested):
        assert len(Allowlist.read(None, path)) == 1, path


def test_the_private_file_elsewhere_is_refused(tmp_path, config_dir):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    in_tree = repo / "allow.txt"
    in_tree.write_text(private_line(), encoding="utf-8")
    named_git = repo / "x.git"
    named_git.mkdir()
    (named_git / "allow").write_text(private_line(), encoding="utf-8")
    link = config_dir / "allow-private"
    link.symlink_to(in_tree)
    linked_dir = config_dir / "linked"
    linked_dir.symlink_to(repo, target_is_directory=True)
    for path in (in_tree, repo / ".git" / ".." / "allow.txt", named_git / "allow",
                 tmp_path / "loose.txt", link, linked_dir / "allow.txt",
                 config_dir.parent / "other-module" / "allow"):
        with pytest.raises(AllowlistError) as caught:
            Allowlist.read(None, path)
        assert "private" in str(caught.value), path
    # The same file is fine as a public file: only the private one has a place.
    assert len(Allowlist.read(tmp_path / "missing", None)) == 0


def test_a_git_directory_reached_through_a_link_is_still_a_git_directory(tmp_path):
    real = tmp_path / "real" / ".git"
    real.mkdir(parents=True)
    (real / "allow").write_text(private_line(), encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "real", target_is_directory=True)
    assert len(Allowlist.read(None, alias / ".git" / "allow")) == 1

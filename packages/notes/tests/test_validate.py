"""Validation: the types file, the walk and its prune, each class and finding, the verb."""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

from moxi_notes import api, doctypes
from moxi_notes import validate as v

from notes_kit import configure, home, moxi_cmd

TYPES = """\
required = ["type", "project", "description", "tags", "updated"]

[[type]]
name = "brief"
homes = ["*/plans/*.md"]
nature = "living-state"
status = ["open", "closed"]

[[type]]
name = "journal"
homes = ["journal/**/*.md"]
nature = "record"

[[type]]
name = "minutes"
homes = ["alpha/plans/minutes-*.md"]
nature = "ephemeral"
priority = 60

[[type]]
name = "ledger"
homes = ["old/**"]
nature = "record"
frozen = true

[[type]]
name = "agent-file"
homes = ["**/AGENT.md"]
nature = "living-state"
exempt = true
"""

FULL = ("---\ntype: brief\nproject: alpha\ndescription: d\ntags: [a]\n"
        "updated: 2026-08-12\n---\n# body\n")


def root() -> Path:
    return home() / "notes"


def write(relative: str, text: str = "# a note\n") -> Path:
    path = root() / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def types(text: str = TYPES) -> Path:
    return write("doc-types.toml", text)


def note(report, relative):
    return next(n for n in report.notes if n.relative == relative)


def codes(report, relative):
    return [code for code, _ in note(report, relative).findings]


@pytest.fixture
def tree():
    types()
    write("alpha/plans/one.md", FULL)
    write("alpha/plans/minutes-may.md", "# minutes\n")
    write("journal/2026/may.md", "# may\n")
    write("old/a/b.md", "# a dead ledger\n")
    write("alpha/AGENT.md", "not frontmatter at all\n")
    write("loose.md", "# mine\n")
    write("misc/thing.md", "# mine\n")
    return root()


# --- the types file -----------------------------------------------------------------------

def test_the_types_file_is_read_from_the_root_by_the_setting(tree):
    found = doctypes.load()
    assert found.present and found.path == str(root() / "doc-types.toml")
    assert found.names == ("brief", "journal", "minutes", "ledger", "agent-file")
    brief = found.get("brief")
    assert (brief.nature, brief.priority, brief.status, brief.frozen, brief.exempt) == \
        ("living-state", 50, ("open", "closed"), False, False)
    assert found.get("minutes").priority == 60
    assert found.get("ledger").frozen and found.get("agent-file").exempt
    configure(types="meta/types.toml")
    write("meta/types.toml", '[[type]]\nname = "journal"\nhomes = []\nnature = "record"\n')
    assert doctypes.load().names == ("journal",)
    assert doctypes.load().required == ("type", "project", "description", "tags", "updated")


def test_required_comes_from_the_file():
    types('required = ["type", "owner"]\n')
    found = doctypes.load()
    assert found.required == ("type", "owner") and found.types == ()


def test_an_absent_file_declares_nothing_and_every_note_is_unfiled_but_checked():
    write("alpha/plans/one.md", FULL)
    write("loose.md", "# mine\n")
    report = api.validate()
    assert report.ok and report.types.present is False
    assert [n.klass for n in report.notes] == ["unfiled", "unfiled"]
    assert codes(report, "loose.md") == [v.MISSING_FRONTMATTER]
    assert codes(report, "alpha/plans/one.md") == []
    write("x.md", "---\ntype: anything\nticket: 1\n---\n")
    assert codes(api.validate(), "x.md") == [v.MISSING_REQUIRED, v.UNKNOWN_KEYS]


@pytest.mark.parametrize("text,line,why", [
    ("[[type]\n", 1, "is not TOML"),
    ('colour = "blue"\n', 1, "'colour' is not a key notes knows"),
    ('required = []\n', 1, "required must be a list"),
    ('required = ["type", "type"]\n', 1, "names a key twice"),
    ('required = ["not a key"]\n', 1, "required must be a list"),
    ('type = "brief"\n', 1, "list of tables"),
    ('[[type]]\nname = "brief"\nhomes = []\nnature = "record"\ncolour = 1\n', 5,
     "'colour' is not a key of a type"),
    ('[[type]]\nname = "brief"\nhomes = []\n', 1, "type 1 has no nature"),
    ('[[type]]\nname = "two words"\nhomes = []\nnature = "record"\n', 2, "no spaces"),
    ('[[type]]\nname = "brief"\nhomes = "x"\nnature = "record"\n', 3, "list of globs"),
    ('[[type]]\nname = "brief"\nhomes = ["/abs/*.md"]\nnature = "record"\n', 3,
     "relative to the notes root"),
    ('[[type]]\nname = "brief"\nhomes = ["~/x.md"]\nnature = "record"\n', 3,
     "relative to the notes root"),
    ('[[type]]\nname = "brief"\nhomes = ["a/../b.md"]\nnature = "record"\n', 3, "climbs out"),
    ('[[type]]\nname = "brief"\nhomes = ["a\\\\b.md"]\nnature = "record"\n', 3, "with /"),
    ('[[type]]\nname = "brief"\nhomes = ["[z-a]*.md"]\nnature = "record"\n', 3,
     "is not a glob notes can read"),
    ('[[type]]\nname = "brief"\nhomes = ["a[]b].md"]\nnature = "record"\n', 3,
     "is not a glob notes can read"),
    ('[[type]]\nname = "brief"\nhomes = []\nnature = "sometimes"\n', 4, "nature of 'brief'"),
    ('[[type]]\nname = "brief"\nhomes = []\nnature = "record"\npriority = "high"\n', 5,
     "whole number"),
    ('[[type]]\nname = "brief"\nhomes = []\nnature = "record"\npriority = true\n', 5,
     "whole number"),
    ('[[type]]\nname = "brief"\nhomes = []\nnature = "record"\nstatus = "open"\n', 5,
     "list of values"),
    ('[[type]]\nname = "brief"\nhomes = []\nnature = "record"\nfrozen = "yes"\n', 5,
     "true or false"),
    ('[[type]]\nname = "brief"\nhomes = []\nnature = "record"\nexempt = 1\n', 5,
     "true or false"),
    ('[[type]]\nname = "brief"\nhomes = []\nnature = "record"\n\n'
     '[[type]]\nname = "brief"\nhomes = []\nnature = "record"\n', 7, "declared twice"),
])
def test_a_bad_types_file_refuses_the_walk_with_its_file_line_and_reason(tree, text, line,
                                                                         why):
    types(text)
    with pytest.raises(doctypes.TypesError) as caught:
        doctypes.load()
    assert caught.value.line == line and why in caught.value.why
    assert str(caught.value).startswith(f"{root() / 'doc-types.toml'}:{line}: ")
    report = api.validate()
    assert not report.ok and report.notes == []
    assert f"doc-types.toml:{line}: " in report.reason
    code, out, err = moxi_cmd("notes", "validate")
    assert code == 1 and "can't validate" in out + err
    assert "unknown-type" not in out


def test_a_types_file_that_is_not_utf8_or_can_not_be_read_refuses(tree):
    (root() / "doc-types.toml").write_bytes(b"\xff\xfe")
    assert not api.validate().ok
    (root() / "doc-types.toml").unlink()
    (root() / "doc-types.toml").mkdir()
    report = api.validate()
    assert not report.ok and "can't be read" in report.reason


@pytest.mark.parametrize("glob,path,hit", [
    ("*/plans/*.md", "alpha/plans/one.md", True),
    ("*/plans/*.md", "alpha/x/plans/one.md", False),
    ("*.md", "a/b.md", False),
    ("**/*.md", "b.md", True),
    ("**/*.md", "a/b/c.md", True),
    ("a/**", "a/b/c.md", True),
    ("a/**/c.md", "a/c.md", True),
    ("?.md", "a.md", True),
    ("?.md", "ab.md", False),
    ("[ab].md", "b.md", True),
    ("[!ab].md", "b.md", False),
    ("x.md", "X.md", False),
    ("a.b.md", "aXb.md", False),
])
def test_the_glob_dialect(glob, path, hit):
    found = doctypes.Types(path="t", present=True,
                           types=(doctypes.DocType("t", (glob,), "record"),))
    assert bool(found.match(path)) is hit


# --- the walk ----------------------------------------------------------------------------

def test_every_class_is_given_and_the_classes_are_disjoint(tree):
    report = api.validate()
    assert report.ok
    klass = {n.relative: (n.klass, n.type) for n in report.notes}
    assert klass == {
        "alpha/plans/one.md": ("filed", "brief"),
        "alpha/plans/minutes-may.md": ("filed", "minutes"),
        "journal/2026/may.md": ("filed", "journal"),
        "old/a/b.md": ("frozen", "ledger"),
        "alpha/AGENT.md": ("exempt", "agent-file"),
        "loose.md": ("unfiled", None),
        "misc/thing.md": ("unfiled", None),
    }
    counts = report.counts()
    assert counts["files"] == sum(counts[k] for k in v.CLASSES) == 7
    assert (counts["filed_with_frontmatter"], counts["filed_without_frontmatter"]) == (1, 2)


def test_frozen_exempt_and_unfiled_notes_are_never_opened(tree):
    report = api.validate()
    for relative in ("old/a/b.md", "alpha/AGENT.md", "loose.md", "misc/thing.md"):
        assert note(report, relative).header is None
        assert note(report, relative).findings == []


def test_a_frozen_type_wins_over_any_priority(tree):
    types(TYPES + '\n[[type]]\nname = "notes-old"\nhomes = ["old/a/*.md"]\n'
                  'nature = "record"\npriority = 99\n')
    assert note(api.validate(), "old/a/b.md").klass == "frozen"


def test_the_highest_priority_wins_and_a_tie_is_ambiguous(tree):
    report = api.validate()
    minutes = note(report, "alpha/plans/minutes-may.md")
    assert minutes.matched == ("minutes",) and v.AMBIGUOUS_HOME not in codes(report,
                                                                               minutes.relative)
    types(TYPES.replace("priority = 60", "priority = 50"))
    report = api.validate()
    minutes = note(report, "alpha/plans/minutes-may.md")
    assert minutes.matched == ("brief", "minutes")
    assert codes(report, minutes.relative)[0] == v.AMBIGUOUS_HOME


def test_every_finding_code(tree):
    write("alpha/plans/two.md", "---\ntype: brief\n\n# never closed\n")
    write("alpha/plans/three.md", "---\ntype: invented\nticket: 7\nstatus: open\n---\n")
    write("alpha/plans/four.md", FULL.replace("type: brief", "type: journal")
          .replace("tags: [a]", "tags: [a]\nstatus: open"))
    write("alpha/plans/five.md", FULL.replace("updated", "status: maybe\nupdated"))
    write("journal/six.md", FULL.replace("type: brief", "type: journal")
          .replace("tags: [a]", "tags: [a]\nstatus: open"))
    report = api.validate()
    assert codes(report, "alpha/plans/minutes-may.md") == [v.MISSING_FRONTMATTER]
    assert codes(report, "alpha/plans/two.md") == [v.UNREADABLE_FRONTMATTER]
    assert codes(report, "alpha/plans/three.md") == [v.MISSING_REQUIRED, v.UNKNOWN_TYPE,
                                                     v.UNKNOWN_KEYS]
    three = dict(note(report, "alpha/plans/three.md").findings)
    assert three[v.MISSING_REQUIRED] == "project, description, tags, updated"
    assert three[v.UNKNOWN_KEYS] == "ticket"
    assert "doc-types.toml" in three[v.UNKNOWN_TYPE]
    assert codes(report, "alpha/plans/four.md") == [v.TYPE_DISAGREES]
    assert codes(report, "alpha/plans/five.md") == [v.UNKNOWN_STATUS]
    assert "(closed, open)" in dict(note(report, "alpha/plans/five.md").findings)[
        v.UNKNOWN_STATUS]
    # journal permits no status, so the key itself is the one finding
    assert codes(report, "journal/six.md") == [v.UNKNOWN_KEYS]
    assert codes(report, "alpha/plans/one.md") == []
    seen = {code for n in report.notes for code, _ in n.findings}
    write("alpha/plans/minutes-tie.md", "# x\n")
    types(TYPES.replace("priority = 60", "priority = 50"))
    seen |= {code for n in api.validate().notes for code, _ in n.findings}
    assert seen == set(v.CODES)


def test_a_status_the_type_allows_is_neither_unknown_nor_required(tree):
    write("alpha/plans/one.md", FULL.replace("updated", "status: closed\nupdated"))
    assert codes(api.validate(), "alpha/plans/one.md") == []


def test_a_file_that_can_not_be_read_is_a_finding(tree):
    if sys.platform == "win32" or os.geteuid() == 0:
        pytest.skip("needs a file mode that denies the reader")
    path = write("alpha/plans/locked.md", FULL)
    path.chmod(0)
    try:
        assert codes(api.validate(), "alpha/plans/locked.md") == [v.UNREADABLE_FRONTMATTER]
    finally:
        path.chmod(0o644)


def test_vendored_folders_and_git_are_pruned_before_the_walk_descends(tree, monkeypatch):
    for marker in v.VENDORED + (".git",):
        write(f"alpha/plans/{marker}/inner.md", FULL)
    write("vendor-notes/plan.md", "# a folder that only starts with a marker\n")
    walked = []
    real = os.walk

    def spy(top, *a, **kw):
        for folder, dirs, files in real(top, *a, **kw):
            walked.append(Path(folder).name)
            yield folder, dirs, files

    monkeypatch.setattr(v.os, "walk", spy)
    report = api.validate()
    assert not set(walked) & set(v.VENDORED + (".git",))
    assert not any("/inner.md" in n.relative for n in report.notes)
    assert note(report, "vendor-notes/plan.md").klass == "unfiled"
    assert all(report.excluded[m] == 1 for m in v.VENDORED + (".git",))


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlinked_folder_or_file_is_never_followed(tree, tmp_path):
    outside = tmp_path / "outside"
    (outside / "plans").mkdir(parents=True)
    (outside / "plans" / "secret.md").write_text(FULL)
    (root() / "beta").symlink_to(outside, target_is_directory=True)
    (root() / "alpha" / "plans" / "linked.md").symlink_to(outside / "plans" / "secret.md")
    report = api.validate()
    assert not any("secret" in n.path or "linked" in n.relative for n in report.notes)
    assert report.excluded[v.SYMLINK] == 2


def test_every_marker_is_counted_even_at_zero(tree):
    report = api.validate()
    assert set(report.excluded) == set(v.MARKERS)
    code, out, _ = moxi_cmd("notes", "validate")
    assert code == 0
    for marker in v.MARKERS:
        assert f"{marker} 0" in out


def test_a_root_that_is_not_a_folder_refuses(tmp_path):
    configure(root=str(tmp_path / "nowhere"))
    report = api.validate()
    assert not report.ok and "is not a folder" in report.reason
    assert moxi_cmd("notes", "validate")[0] == 1


def test_validate_writes_nothing(tree):
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root().rglob("*")
              if p.is_file()}
    moxi_cmd("notes", "validate", "--json", "--unfiled", "--limit", "0")
    moxi_cmd("notes", "validate", "--unfiled")
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root().rglob("*")
             if p.is_file()}
    assert before == after


# --- the verb ------------------------------------------------------------------------------

def test_the_verb_prints_counts_and_findings_and_exits_0(tree):
    code, out, err = moxi_cmd("notes", "validate")
    assert code == 0 and err == ""
    assert "7 markdown file(s): filed 3, frozen 1, exempt 1, unfiled 2" in out
    assert "alpha/plans/minutes-may.md (minutes)" in out
    assert "missing-frontmatter" in out
    assert "- ledger: 1 file(s)" in out and "- agent-file: 1 file(s)" in out
    assert "- misc/: 1 file(s)" in out and "- (root): 1 file(s)" in out
    assert "    misc/thing.md" not in out
    assert "    misc/thing.md" in moxi_cmd("notes", "validate", "--unfiled")[1]


def test_the_limit_caps_text_and_never_json(tree):
    for i in range(5):
        write(f"journal/n{i}.md")
    code, out, _ = moxi_cmd("notes", "validate", "--limit", "2")
    assert code == 0 and "...and 5 more file(s) with findings" in out
    code, out, _ = moxi_cmd("notes", "validate", "--json", "--limit", "2")
    data = json.loads(out)
    assert sum(1 for f in data["files"] if f["findings"]) == 7
    assert moxi_cmd("notes", "validate", "--limit", "-1")[0] == 2


def test_the_json_shape(tree):
    data = json.loads(moxi_cmd("notes", "validate", "--json")[1])
    assert data["ok"] is True and data["root"] == str(root())
    assert data["types"]["present"] is True and len(data["types"]["types"]) == 5
    assert data["counts"]["files"] == 7
    one = next(f for f in data["files"] if f["path"].endswith("one.md"))
    assert one["class"] == "filed" and one["type"] == "brief"
    assert one["frontmatter"]["fields"]["project"] == "alpha"
    assert set(data["excluded"]) == set(v.MARKERS)


def test_a_bad_types_setting_is_refused():
    code, _, err = moxi_cmd("config", "set", "notes", "types", "/abs/types.toml")
    assert code != 0 and "types" in err
    code, _, err = moxi_cmd("config", "set", "notes", "types", "../types.toml")
    assert code != 0


# --- no type name in code ------------------------------------------------------------------

SRC = Path(__file__).resolve().parents[1] / "src" / "moxi_notes"


def test_no_type_name_is_spelled_in_the_package():
    """The vocabulary is the user's file. No string in notes' code names a type, neither
    the ones these tests declare nor the ones notes are commonly typed with."""
    import tomllib

    names = {row["name"] for row in tomllib.loads(TYPES)["type"]}
    names |= {"brief", "journal", "minutes", "ledger", "agent-file", "design", "handoff",
              "proposal", "meeting-note", "status-report", "decision-record",
              "research-note", "archive", "state", "memo", "spec"}
    found = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in names:
                found.append(f"{path.name}:{node.lineno} {node.value!r}")
    assert found == []

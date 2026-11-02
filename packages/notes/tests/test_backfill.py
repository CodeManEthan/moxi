"""Backfill: a dry run by default; `--apply` adds only missing keys it can infer, never
replaces one, and never writes through a symlink."""

from __future__ import annotations

import datetime
import json
import os
import sys

import pytest

from moxi_notes import backfill
from moxi_notes import validate as v

from notes_kit import add_project, configure, home, moxi_cmd, no_projects
from test_validate import TYPES, root, types, write


def stamp(path) -> str:
    return datetime.date.fromtimestamp(os.lstat(path).st_mtime).isoformat()


def proposal(found, relative):
    return next(p for p in found.proposals if p.relative == relative)


def snapshot():
    return {p: p.read_bytes() for p in root().rglob("*") if p.is_file() and not p.is_symlink()}


@pytest.fixture
def tree():
    types()
    write("alpha/plans/one.md", "# one\n\nbody text\n")
    write("alpha/plans/two.md", "---\ntype: journal\ndescription: kept\n---\nbody\n")
    write("journal/may.md", "---\nticket: 7\n---\n# may\n")
    write("old/a/b.md", "# a dead ledger\n")
    write("alpha/AGENT.md", "not frontmatter at all\n")
    write("loose.md", "# mine\n")
    return root()


def test_the_plan_takes_only_filed_notes(tree):
    found = backfill.plan()
    assert found.ok
    assert sorted(p.relative for p in found.proposals) == [
        "alpha/plans/one.md", "alpha/plans/two.md", "journal/may.md"]


def test_type_from_the_home_updated_from_the_modified_date(tree):
    one = proposal(backfill.plan(), "alpha/plans/one.md")
    assert one.additions["type"] == "brief"
    assert one.additions["updated"] == stamp(root() / "alpha/plans/one.md")


def test_description_and_tags_are_never_inferred_and_say_so(tree):
    one = proposal(backfill.plan(), "alpha/plans/one.md")
    assert "description" not in one.additions and "tags" not in one.additions
    assert ("description", backfill.NEVER) in one.skipped
    assert ("tags", backfill.NEVER) in one.skipped


def test_a_required_key_it_can_not_infer_is_skipped(tree):
    types(TYPES.replace('required = ["type", "project", "description", "tags", "updated"]',
                        'required = ["type", "owner"]'))
    one = proposal(backfill.plan(), "alpha/plans/one.md")
    assert one.additions == {"type": "brief"}
    assert one.skipped == [("owner", backfill.NEVER)]


def test_a_key_that_is_there_is_never_proposed(tree):
    two = proposal(backfill.plan(), "alpha/plans/two.md")
    assert "type" not in two.additions and "description" not in dict(two.skipped)


def test_without_projects_project_is_skipped_naming_the_install(tree, monkeypatch):
    no_projects(monkeypatch)
    one = proposal(backfill.plan(), "alpha/plans/one.md")
    assert "project" not in one.additions
    assert "moxi install projects" in dict(one.skipped)["project"]


def test_project_is_the_project_whose_notes_folder_holds_the_file(tree):
    add_project("alpha", home() / "code" / "alpha", "--notes-root", str(root() / "alpha"))
    add_project("beta", home() / "code" / "beta",
                "--notes-root", str(root() / "alpha" / "plans"))
    found = backfill.plan()
    assert proposal(found, "alpha/plans/one.md").additions["project"] == "beta"
    journal = proposal(found, "journal/may.md")
    assert "project" not in journal.additions
    assert dict(journal.skipped)["project"] == "in no project's notes folder"


def test_two_projects_on_one_folder_skip_it():
    folders = [("/n/alpha", "alpha"), ("/n/alpha", "beta"), ("/n", "gamma")]
    assert backfill.infer_project("/n/alpha/x.md", folders)[0] is None
    assert "2 projects" in backfill.infer_project("/n/alpha/x.md", folders)[1]
    assert backfill.infer_project("/n/y.md", folders) == ("gamma", "")


def test_a_header_it_can_not_read_and_a_tie_are_refused(tree):
    write("alpha/plans/bad.md", "---\ntype: brief\n\nnever closed\n")
    write("alpha/plans/minutes-x.md", "# tie\n")
    types(TYPES.replace("priority = 60", "priority = 50"))
    found = backfill.plan()
    assert "can't be read" in proposal(found, "alpha/plans/bad.md").refused
    assert "same priority" in proposal(found, "alpha/plans/minutes-x.md").refused
    refused = [root() / "alpha/plans/bad.md", root() / "alpha/plans/minutes-x.md"]
    before = [p.read_bytes() for p in refused]
    backfill.apply(found)
    assert [p.read_bytes() for p in refused] == before


def test_a_bad_types_file_plans_nothing(tree):
    types("colour = 1\n")
    found = backfill.plan()
    assert not found.ok and found.proposals == []
    code, out, err = moxi_cmd("notes", "backfill", "--apply")
    assert code == 1 and "can't backfill" in out + err


def test_the_dry_run_is_the_default_and_writes_nothing(tree):
    before = snapshot()
    code, out, _ = moxi_cmd("notes", "backfill")
    assert code == 0 and "a dry run; nothing was written" in out
    assert "+ type: brief" in out
    assert "moxi notes backfill --apply" in out
    moxi_cmd("notes", "backfill", "--json")
    assert snapshot() == before


def test_apply_adds_only_missing_inferable_keys_and_is_idempotent(tree, monkeypatch):
    no_projects(monkeypatch)
    one = root() / "alpha/plans/one.md"
    day = stamp(one)
    code, out, _ = moxi_cmd("notes", "backfill", "--apply")
    assert code == 0 and "written: 3 file(s), errors: 0" in out
    assert one.read_text() == f"---\ntype: brief\nupdated: {day}\n---\n\n# one\n\nbody text\n"
    two = (root() / "alpha/plans/two.md").read_text()
    assert two.startswith("---\ntype: journal\ndescription: kept\nupdated: ")
    may = (root() / "journal/may.md").read_text()
    assert may.startswith("---\ntype: journal\nupdated: ") and "ticket: 7\n---\n# may\n" in may
    after = snapshot()
    code, out, _ = moxi_cmd("notes", "backfill", "--apply")
    assert code == 0 and "written: 0 file(s)" in out
    assert snapshot() == after


def test_apply_never_replaces_a_key_added_since_the_plan(tree):
    found = backfill.plan()
    path = root() / "alpha/plans/one.md"
    path.write_text("---\ntype: journal\n---\n# one\n")
    backfill.apply(found)
    one = proposal(found, "alpha/plans/one.md")
    text = path.read_text()
    assert text.startswith("---\ntype: journal\n") and "type: brief" not in text
    assert ("type", "there when the file was read again; not replaced") in one.skipped


def test_apply_leaves_frozen_exempt_and_unfiled_notes_alone(tree):
    paths = [root() / r for r in ("old/a/b.md", "alpha/AGENT.md", "loose.md")]
    before = [p.read_bytes() for p in paths]
    backfill.apply(backfill.plan())
    assert [p.read_bytes() for p in paths] == before


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_apply_never_writes_through_a_symlink(tree, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# outside\n")
    found = backfill.plan()
    one = root() / "alpha/plans/one.md"
    one.unlink()
    one.symlink_to(outside)
    backfill.apply(found)
    assert outside.read_text() == "# outside\n"
    assert "never written through" in proposal(found, "alpha/plans/one.md").error
    moxi_cmd("notes", "backfill", "--apply")
    assert outside.read_text() == "# outside\n"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_apply_never_writes_outside_the_root_through_a_linked_folder(tree, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "one.md").write_text("# outside\n")
    found = backfill.plan()
    plans = root() / "alpha" / "plans"
    for child in plans.iterdir():
        child.unlink()
    plans.rmdir()
    plans.symlink_to(outside, target_is_directory=True)
    backfill.apply(found)
    assert (outside / "one.md").read_text() == "# outside\n"
    assert "not under the notes root" in proposal(found, "alpha/plans/one.md").error


def test_a_file_that_is_not_utf8_is_left_alone(tree):
    path = root() / "alpha/plans/one.md"
    found = backfill.plan()
    path.write_bytes(b"\xff\xfe# one\n")
    backfill.apply(found)
    assert path.read_bytes() == b"\xff\xfe# one\n"
    assert "not UTF-8" in proposal(found, "alpha/plans/one.md").error
    assert backfill.render(found).count("error") >= 1


def test_the_json_and_the_limit(tree):
    for i in range(3):
        write(f"journal/n{i}.md")
    data = json.loads(moxi_cmd("notes", "backfill", "--json", "--limit", "1")[1])
    assert data["ok"] is True and data["apply"] is False
    assert data["counts"]["considered"] == 6 and len(data["proposals"]) == 6
    first = next(p for p in data["proposals"] if p["path"].endswith("one.md"))
    assert first["additions"]["type"] == "brief"
    assert {"key": "tags", "reason": backfill.NEVER} in first["skipped"]
    out = moxi_cmd("notes", "backfill", "--limit", "1")[1]
    assert "...and 5 more" in out


def test_a_types_setting_elsewhere_is_followed(tree):
    configure(types="meta/types.toml")
    write("meta/types.toml", '[[type]]\nname = "journal"\nhomes = ["loose.md"]\n'
                             'nature = "record"\n')
    found = backfill.plan()
    assert [p.relative for p in found.proposals] == ["loose.md"]
    assert proposal(found, "loose.md").additions["type"] == "journal"
    assert v.validate().types.path.endswith(os.path.join("meta", "types.toml"))


@pytest.mark.skipif(not backfill.O_NOFOLLOW, reason="the OS has no O_NOFOLLOW")
def test_a_symlink_swapped_in_after_the_lstat_is_not_opened(tree, monkeypatch):
    found = backfill.plan()
    one = root() / "alpha/plans/one.md"
    other = root() / "loose.md"
    regular = os.lstat(one)
    one.unlink()
    one.symlink_to(other)
    real_lstat = os.lstat
    monkeypatch.setattr(backfill.os, "lstat",
                        lambda p, *a, **kw: regular if os.fspath(p) == str(one)
                        else real_lstat(p, *a, **kw))
    backfill.apply(found)
    assert other.read_text() == "# mine\n"
    assert "can't be opened" in proposal(found, "alpha/plans/one.md").error


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_apply_keeps_a_files_mode_and_leaves_a_read_only_one_alone(tree):
    one, two = root() / "alpha/plans/one.md", root() / "alpha/plans/two.md"
    one.chmod(0o640)
    two.chmod(0o444)
    found = backfill.apply(backfill.plan())
    assert proposal(found, "alpha/plans/one.md").applied
    assert one.stat().st_mode & 0o777 == 0o640
    assert "read-only" in proposal(found, "alpha/plans/two.md").error
    assert two.read_text() == "---\ntype: journal\ndescription: kept\n---\nbody\n"
    two.chmod(0o644)
    assert moxi_cmd("notes", "backfill", "--apply")[0] == 0

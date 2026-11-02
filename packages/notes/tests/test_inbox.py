"""Inbox one-offs: the slug rule, making one, listing them and renaming one."""

from __future__ import annotations

import re

import pytest

from moxi_notes import api
from moxi_notes.errors import BadSlug, Refused

from notes_kit import add_project, configure, home, moxi_cmd, no_projects


def inbox():
    return home() / "notes" / "inbox"


@pytest.mark.parametrize("slug", ["a", "0", "one-off", "a1-b2", "x" * 63, "9-"])
def test_a_good_slug_names_a_folder_under_the_inbox(slug):
    assert api.inbox_dir(slug) == inbox() / slug


@pytest.mark.parametrize("slug", ["", "-a", "A", "one_off", "one off", "a/b", "..", ".a",
                                  "x" * 64, "é", None, 7])
def test_a_bad_slug_is_a_value_error(slug):
    with pytest.raises(ValueError, match="not an inbox slug"):
        api.inbox_dir(slug)
    with pytest.raises(BadSlug):
        api.make_inbox(slug)


def test_the_inbox_follows_the_prefix():
    configure(prefix="team")
    assert api.inbox_dir("one-off") == home() / "notes" / "team" / "inbox" / "one-off"


def test_make_inbox_makes_a_plain_folder_and_keeps_an_existing_one():
    made = api.make_inbox("one-off")
    assert made == inbox() / "one-off" and made.is_dir()
    assert not (made / ".git").exists()
    (made / "plan.md").write_text("p\n")
    assert api.make_inbox("one-off") == made
    assert (made / "plan.md").read_text() == "p\n"


def test_make_inbox_refuses_a_file_in_the_way():
    inbox().mkdir(parents=True)
    (inbox() / "one-off").write_text("x\n")
    with pytest.raises(Refused, match="not a folder"):
        api.make_inbox("one-off")


def test_inbox_list_names_each_one_off_by_slug(tmp_path):
    assert api.inbox_list() == []
    for slug in ("beta", "alpha"):
        api.make_inbox(slug)
    (inbox() / "notes.md").write_text("n\n")
    (inbox() / "Bad_Name").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (inbox() / "linked").symlink_to(tmp_path / "elsewhere")
    found = api.inbox_list()
    assert [one.slug for one in found] == ["alpha", "beta"]
    assert found[0].path == inbox() / "alpha"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", found[0].modified)
    assert found[0].as_dict()["path"] == str(inbox() / "alpha")


def test_rename_moves_the_folder():
    made = api.make_inbox("one-off")
    (made / "plan.md").write_text("p\n")
    moved = api.rename_inbox("one-off", "two-off")
    assert moved == inbox() / "two-off" and (moved / "plan.md").exists()
    assert not made.exists()


def test_rename_refuses_bad_slugs_a_missing_source_and_an_existing_target():
    api.make_inbox("alpha")
    api.make_inbox("beta")
    with pytest.raises(BadSlug):
        api.rename_inbox("alpha", "Not_A_Slug")
    with pytest.raises(BadSlug):
        api.rename_inbox("../alpha", "gamma")
    with pytest.raises(Refused, match="no inbox one-off"):
        api.rename_inbox("gamma", "delta")
    with pytest.raises(Refused, match="exists already"):
        api.rename_inbox("alpha", "beta")
    assert [one.slug for one in api.inbox_list()] == ["alpha", "beta"]


def test_rename_refuses_a_project_key_in_any_case():
    api.make_inbox("one-off")
    root = home() / "code" / "Alpha"
    root.mkdir(parents=True)
    add_project("Alpha", root)
    with pytest.raises(Refused, match="key of project 'Alpha'"):
        api.rename_inbox("one-off", "alpha")
    assert (inbox() / "one-off").is_dir()


def test_rename_without_projects_checks_no_key(monkeypatch):
    api.make_inbox("one-off")
    no_projects(monkeypatch)
    assert api.rename_inbox("one-off", "alpha") == inbox() / "alpha"


def test_the_inbox_verbs(capsys):
    code, out, err = moxi_cmd("notes", "inbox", "add", "one-off")
    assert (code, out.strip()) == (0, str(inbox() / "one-off")), err
    code, out, _ = moxi_cmd("notes", "inbox", "list")
    assert code == 0 and out.startswith("one-off  ")
    code, out, _ = moxi_cmd("notes", "inbox", "list", "--json")
    assert code == 0 and '"slug": "one-off"' in out
    code, out, _ = moxi_cmd("notes", "inbox", "rename", "one-off", "two-off")
    assert (code, out.strip()) == (0, str(inbox() / "two-off"))
    code, _out, err = moxi_cmd("notes", "inbox", "rename", "one-off", "three")
    assert code == 3 and "no inbox one-off" in err and "fix:" in err
    code, _out, err = moxi_cmd("notes", "inbox", "add", "Bad")
    assert code == 3 and "not an inbox slug" in err

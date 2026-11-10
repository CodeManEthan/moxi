"""A project's rounds folder, however its `dir` setting places it, and the rounds found
in one."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import exit as exits
from moxi import modules

from moxi_rounds import folders, log
from moxi_rounds.errors import BadName, NoFolder, UnknownProject


@pytest.fixture
def notes_gives(monkeypatch):
    """`notes_gives(answer)`: the notes module reads as installed, and its
    `notes_root_for` returns `answer` (or raises it, when it is an exception). Returns
    the list of calls made to it."""

    def stand_in(answer):
        calls = []

        def notes_root_for(root, kind="project", key=None):
            calls.append((root, kind, key))
            if isinstance(answer, Exception):
                raise answer
            return answer

        installed, load = modules.available, modules.load
        monkeypatch.setattr(modules, "available",
                            lambda name: name == "notes" or installed(name))
        monkeypatch.setattr(
            modules, "load", lambda name, **wanted: SimpleNamespace(
                ok=True, reason="", api=SimpleNamespace(notes_root_for=notes_root_for))
            if name == "notes" else load(name, **wanted))
        return calls

    return stand_in


def open_round(folder: Path, slug: str) -> None:
    (folder / slug).mkdir(parents=True)
    log.append(log.round_log(folder / slug), "round.opened",
               {"description": "a placeholder round", "flow": "fix", "steps": []},
               actor="planner")


# -- the rounds folder -------------------------------------------------------------------

def test_a_relative_dir_is_placed_in_the_notes_folder_the_projects_record_names(
        add_project, without_notes, tmp_path):
    add_project("proj-a", notes_root=tmp_path / "notes" / "proj-a")
    assert folders.home("proj-a") == folders.Home(
        "proj-a", tmp_path / "notes" / "proj-a" / "rounds")


def test_a_projects_own_dir_is_used_in_place_of_the_default(
        add_project, without_notes, write_config, tmp_path):
    add_project("proj-a", notes_root=tmp_path / "notes" / "proj-a")
    write_config("""
        [defaults]
        dir = "designs"

        [projects.proj-a]
        dir = "plans/rounds"
    """)
    add_project("proj-b", notes_root=tmp_path / "notes" / "proj-b")
    assert folders.home("proj-a").folder == tmp_path / "notes" / "proj-a" / "plans/rounds"
    assert folders.home("proj-b").folder == tmp_path / "notes" / "proj-b" / "designs"


def test_a_project_with_no_notes_folder_of_its_own_gets_the_one_notes_gives_it(
        add_project, notes_gives, tmp_path):
    root = add_project("proj-a")
    calls = notes_gives(tmp_path / "from-notes")
    assert folders.home("proj-a").folder == tmp_path / "from-notes" / "rounds"
    assert calls == [(str(root), "project", "proj-a")]


def test_notes_is_not_asked_when_the_projects_record_names_a_notes_folder(
        add_project, notes_gives, tmp_path):
    add_project("proj-a", notes_root=tmp_path / "notes" / "proj-a")
    calls = notes_gives(tmp_path / "from-notes")
    assert folders.home("proj-a").folder == tmp_path / "notes" / "proj-a" / "rounds"
    assert calls == []


def test_an_absolute_dir_is_the_folder_and_needs_no_notes_folder(
        add_project, without_notes, write_config, tmp_path):
    add_project("proj-a")
    write_config(f"""
        [projects.proj-a]
        dir = "{tmp_path / 'elsewhere'}"
    """)
    assert folders.home("proj-a").folder == tmp_path / "elsewhere"


def test_a_dir_under_the_users_home_is_absolute(add_project, without_notes, write_config):
    add_project("proj-a")
    write_config("""
        [projects.proj-a]
        dir = "~/kept/rounds"
    """)
    assert folders.home("proj-a").folder == Path.home() / "kept" / "rounds"


def test_resolving_a_folder_makes_nothing(add_project, without_notes, tmp_path):
    add_project("proj-a", notes_root=tmp_path / "notes" / "proj-a")
    assert not folders.home("proj-a").folder.exists()


def test_a_project_is_found_in_any_case_and_named_as_it_is_stored(
        add_project, without_notes, tmp_path):
    add_project("proj-a", notes_root=tmp_path / "notes" / "proj-a")
    assert folders.home("PROJ-A").project == "proj-a"


def test_settings_already_read_are_used_as_given(add_project, without_notes, write_config,
                                                 tmp_path):
    from moxi_rounds import settings

    add_project("proj-a", notes_root=tmp_path / "notes" / "proj-a")
    read = settings.load()
    write_config("""
        [defaults]
        dir = "designs"
    """)
    assert folders.home("proj-a", read).folder.name == "rounds"
    assert folders.home("proj-a").folder.name == "designs"


def test_a_relative_dir_with_no_notes_folder_anywhere_names_the_setting_that_fixes_it(
        add_project, without_notes):
    add_project("proj-a")
    with pytest.raises(NoFolder) as caught:
        folders.home("proj-a")
    error = caught.value
    assert error.exit_code == exits.REFUSED
    assert error.project == "proj-a"
    assert "notes module is not installed" in error.reason
    assert "[projects.proj-a]" in error.fix and "dir =" in error.fix
    assert "moxi projects set proj-a --notes-root" in error.fix


@pytest.mark.parametrize("answer, said", [
    (None, "notes gives it none"),
    (RuntimeError("no such place"), "no such place"),
])
def test_a_relative_dir_is_not_resolved_when_notes_has_no_folder_for_the_project(
        add_project, notes_gives, answer, said):
    add_project("proj-a")
    notes_gives(answer)
    with pytest.raises(NoFolder) as caught:
        folders.home("proj-a")
    assert said in caught.value.reason


def test_a_project_the_projects_list_does_not_hold_is_its_own_error(without_notes):
    with pytest.raises(UnknownProject) as caught:
        folders.home("proj-b")
    assert caught.value.exit_code == exits.REFUSED
    assert "moxi projects add proj-b" in caught.value.fix


def test_every_project_is_listed_with_its_folder_and_the_unresolved_beside_them(
        add_project, without_notes, tmp_path):
    add_project("proj-a", notes_root=tmp_path / "notes" / "proj-a")
    add_project("proj-b")
    found, failed = folders.homes()
    assert found == [folders.Home("proj-a", tmp_path / "notes" / "proj-a" / "rounds")]
    assert [(type(error), error.project) for error in failed] == [(NoFolder, "proj-b")]


# -- the rounds in it --------------------------------------------------------------------

def test_the_rounds_of_a_folder_are_its_folders_that_hold_a_log(tmp_path):
    folder = tmp_path / "rounds"
    open_round(folder, "second-round")
    open_round(folder, "a-round")
    open_round(folder, "round-3")
    (folder / "no-log-here").mkdir()
    (folder / "only-a-note").mkdir()
    (folder / "only-a-note" / "round-note.md").write_text("generated\n")
    (folder / "project.jsonl").write_text("")
    (folder / "rulings.md").write_text("generated\n")
    assert folders.slugs(folder) == ["a-round", "round-3", "second-round"]


def test_a_folder_whose_log_is_a_folder_is_no_round(tmp_path):
    folder = tmp_path / "rounds"
    (folder / "a-round" / "round.jsonl").mkdir(parents=True)
    assert folders.slugs(folder) == []


def test_a_folder_whose_name_is_no_slug_is_no_round(tmp_path):
    folder = tmp_path / "rounds"
    for name in ("Upper", "under_score", "dot.ted", "a-round"):
        open_round(folder, name)
    assert folders.slugs(folder) == ["a-round"]


def test_a_rounds_folder_that_is_not_there_holds_no_round(tmp_path):
    assert folders.slugs(tmp_path / "rounds") == []
    (tmp_path / "a-file").write_text("")
    assert folders.slugs(tmp_path / "a-file") == []


def test_rounds_are_named_by_project_and_slug(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    open_round(first, "a-round")
    open_round(second, "b-round")
    open_round(second, "a-round")
    assert folders.names([folders.Home("proj-b", second), folders.Home("proj-a", first)]) \
        == ["proj-b/a-round", "proj-b/b-round", "proj-a/a-round"]


def test_a_rounds_name_splits_into_its_project_and_slug():
    assert folders.split("proj-a/a-round-2") == ("proj-a", "a-round-2")
    assert folders.Home("proj-a", Path("rounds")).round_folder("a-round") \
        == Path("rounds") / "a-round"


@pytest.mark.parametrize("name", ["proj-a", "proj-a/", "/a-round", "proj-a/A-round",
                                  "proj-a/a_round", "proj-a/a/b", "proj-a/a round", ""])
def test_a_name_that_is_no_rounds_is_refused_as_a_bad_argument(name):
    with pytest.raises(BadName) as caught:
        folders.split(name)
    assert caught.value.exit_code == exits.USAGE
    assert "<project>/<slug>" in caught.value.fix

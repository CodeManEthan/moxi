"""The read API's contract: its calls, types and errors."""

from __future__ import annotations

import os

import moxi.modules
import pytest

from moxi_projects import api


def test_the_module_offers_interface_1_through_the_core():
    moxi.modules.reset()
    found = moxi.modules.load("projects", interface=1)
    assert found.ok, found.reason
    assert found.api is api
    assert moxi.modules.available("projects")


def test_list_projects_answers_every_project_by_key(golden_copy):
    projects = api.list_projects()
    assert [p.key for p in projects] == ["alpha", "Beta", "delta.web", "gamma"]
    alpha = projects[0]
    assert alpha.root == os.path.join(golden_copy, "alpha")
    assert alpha.lead == "lead-a"
    assert alpha.associated == ("helper", "scout")
    assert alpha.agents == ("lead-a", "helper", "scout")


def test_list_projects_filters_by_kind(golden_copy):
    assert [p.key for p in api.list_projects(kind="agent")] == ["Beta"]
    assert api.list_projects(kind="topic") == []


def test_get_matches_the_key_ignoring_case_and_returns_the_stored_spelling(golden_copy):
    assert api.get("beta").key == "Beta"
    assert api.get("ALPHA").key == "alpha"


def test_get_of_an_unknown_key_raises_not_found_carrying_the_key(golden_copy):
    with pytest.raises(api.NotFound) as caught:
        api.get("nope")
    assert caught.value.key == "nope"
    assert caught.value.exit_code == 3
    assert "moxi projects add nope" in caught.value.fix
    assert isinstance(caught.value, api.ProjectsError)


def test_a_project_without_a_lead_has_only_its_set_as_agents(golden_copy):
    delta = api.get("delta.web")
    assert delta.lead is None
    assert delta.agents == ("helper",)


def test_a_project_is_read_only_and_compared_by_value(golden_copy):
    project = api.get("alpha")
    with pytest.raises(AttributeError):
        project.key = "other"
    assert project == api.get("ALPHA")
    assert hash(project) == hash(api.get("alpha"))
    assert "alpha" in repr(project)


def test_as_dict_gives_the_field_names_and_agents(golden_copy):
    assert api.get("alpha").as_dict() == {
        "key": "alpha", "root": os.path.join(golden_copy, "alpha"), "kind": "project",
        "lead": "lead-a", "associated": ["helper", "scout"], "notes_root": None,
        "created": api.get("alpha").created, "agents": ["lead-a", "helper", "scout"]}


def test_kinds_without_contributions_are_the_built_in_two(site):
    kinds = api.kinds()
    assert kinds.found == (
        api.Kind(name="agent", doc="an agent's home directory", module="projects",
                 mint="git"),
        api.Kind(name="project", doc="a code project: a directory, usually a git "
                                     "repository", module="projects", mint="git"))
    assert kinds.broken == ()


def test_kinds_never_raises(monkeypatch):
    from moxi_projects import contributions

    def boom():
        raise RuntimeError("no")

    monkeypatch.setattr(contributions, "load", boom)
    kinds = api.kinds()
    assert kinds.found == ()
    assert kinds.broken and kinds.broken[0][0] == "projects"


def test_the_errors_carry_their_exit_codes():
    assert api.ProjectsError("x").exit_code == 1
    assert api.Unreadable("x").exit_code == 1
    assert api.Busy().exit_code == 75
    too_new = api.TooNew("/s.db", 9, 1)
    assert too_new.exit_code == 3
    assert too_new.version == 9 and too_new.known == 1
    assert "moxi install projects" in str(too_new)

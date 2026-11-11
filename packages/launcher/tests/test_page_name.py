"""`GET /api/name`: the name a launch of the form would take, the same name a preview of
that launch gives for a folder, a template being edited rendered unsaved, the save check's
refusals, and what saving a template writes."""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from moxi import modules, proc
from moxi.paths import Layout, python
from test_page import get, post

from moxi_launcher import names, page, pagehandlers, settings, slots, store

OWN = "<project>-<model>-<date:mmm-dd hh:mm>"
EVERY = "<identity>-<date:mmm-dd-yyyy hh:mm>"
HINT = ("use <project>, <agent>, <model>, <identity>, <scope>, <instance>, "
        "<date[:format]> and <time[:format]>, and no other '<' or '>'")


@pytest.fixture(autouse=True)
def fresh_handlers(monkeypatch):
    """Each test logs each failure class anew, and reads no module as installed unless it
    says so."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(slots, "links", lambda name: False)


def installed(monkeypatch, *modules):
    monkeypatch.setattr(slots, "links", lambda name: name in modules)


def settings_file(text: str) -> None:
    path = Layout("launcher").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def name(**query) -> dict:
    status, said, _ = get("/api/name", urlencode(query))
    assert status == 200, said
    return said


def up(**answer) -> dict:
    return {"state": "up", "detail": None, "refused": None, "save": None, **answer}


def test_the_answer_declares_its_keys():
    assert set(page.NAME) == {"state", "detail", "name", "template", "from", "refused",
                              "save"}
    assert set(page.NAME["save"].items) == {"key", "value"}


def test_the_name_is_the_shipped_templates_for_the_form_as_it_stands():
    assert name(cwd="/work/proj-b", model="opus") == up(
        name="proj-b-opus-Nov-08 12:45", template=names.DEFAULT, **{"from": "global"})
    # A project stands before the folder, and the default model leaves its piece empty.
    assert name(project="proj-a", cwd="/work/elsewhere")["name"] == "proj-a-Nov-08 12:45"
    assert name()["name"] == "Nov-08 12:45"


def test_a_project_with_a_template_of_its_own_is_named_by_it():
    settings_file(f'[name]\ntemplate = "{EVERY}"\n[name.templates]\nproj-a = "<project> <model>"\n')
    assert name(project="proj-a", model="opus") == up(
        name="proj-a opus", template="<project> <model>", **{"from": "project"})
    assert name(project="proj-b", model="opus") == up(
        name="Nov-08-2026 12:45", template=EVERY, **{"from": "global"})


@pytest.fixture
def proj_a(tmp_path, monkeypatch):
    """The project `proj-a`, its root a folder of this test's, as projects finds it for a
    folder in it; projects reads as installed."""
    root = tmp_path / "proj-a"
    (root / "sub").mkdir(parents=True)
    project = SimpleNamespace(key="proj-a", root=str(root), lead=None, associated=(),
                              agents=(), notes_root=None)

    def for_path(path):
        return project if path == str(root) or path.startswith(str(root) + os.sep) else None

    found = modules.Found(name="projects", state=modules.PRESENT, interfaces=frozenset({1}),
                          api=SimpleNamespace(get=lambda key: project, for_path=for_path))
    real_load = modules.load
    monkeypatch.setattr(modules, "load", lambda module, interface=None: found
                        if module == "projects" else real_load(module, interface=interface))
    installed(monkeypatch, "projects")
    return root


def previewed_name(cwd: str, **values: str) -> str:
    """The session name a preview of a terminal launch from Home in `cwd` gives."""
    status, said = post("/api/preview", {"values": {"harness": "claude", "target": "terminal",
                                                    "cwd": cwd, **values}})
    assert status == 200 and said["refused"] == [], said
    argv = said["argv"]
    return argv[argv.index("-n") + 1]


def test_the_home_folder_is_named_as_a_launch_there_is_named(tmp_path, monkeypatch):
    home = tmp_path / "shell"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    said = name(cwd="~")
    assert said["name"] == previewed_name("~") == "shell-Nov-08 12:45", said


def test_a_folder_inside_a_project_is_named_by_the_projects_key_and_template(proj_a):
    settings_file(f'[name]\ntemplate = "{EVERY}"\n[name.templates]\nproj-a = "<project>-<model>"\n')
    folder = str(proj_a / "sub")
    said = name(cwd=folder, model="opus")
    assert said == up(name="proj-a-opus", template="<project>-<model>", **{"from": "project"})
    assert previewed_name(folder, model="opus") == "proj-a-opus"
    # Outside every project the folder's own name stands in, from the template for every launch.
    assert name(cwd=str(proj_a.parent))["name"] == "Nov-08-2026 12:45"


def test_a_seat_stands_before_the_agent_role():
    settings_file('[name]\ntemplate = "<agent>"\n')
    assert name(seat="planner", agent="helper")["name"] == "planner"
    assert name(agent="helper")["name"] == "helper"


def test_an_identity_fills_nothing_without_comms(monkeypatch):
    settings_file(f'[name]\ntemplate = "{EVERY}-<scope>"\n')
    asked = {"identity": "planner", "scope": "box-1"}
    assert name(**asked)["name"] == "Nov-08-2026 12:45"
    installed(monkeypatch, "comms")
    assert name(**asked)["name"] == "planner-Nov-08-2026 12:45-box-1"


def test_instance_counts_past_the_live_launches():
    settings_file('[name]\ntemplate = "<project>-<instance>"\n')
    assert name(project="proj-a")["name"] == "proj-a-1"
    me = proc.identity(os.getpid())
    conn = store.connect()
    try:
        store.insert(conn, request_id="r1", actor="alice", harness="claude",
                     target="terminal", cwd="/work", values={"name": "proj-a-1"},
                     argv=["claude"], env={}, posture="operator", session_id="s1", slot=None)
        store.set_state(conn, "r1", "started", pid=me.pid, pid_started=me.started)
    finally:
        conn.close()
    assert name(project="proj-a")["name"] == "proj-a-2"


def test_a_template_being_edited_is_rendered_unsaved_with_what_saving_it_writes():
    assert name(project="proj-a", model="opus", template="<model> in <project>") == up(
        name="opus in proj-a", template="<model> in <project>", **{"from": "global"},
        save={"key": "name.template", "value": "<model> in <project>"})
    assert settings.current()[names.TEMPLATE] == names.DEFAULT


def test_keeping_a_template_for_a_project_writes_the_whole_table_with_its_entry_set():
    settings_file('[name.templates]\nproj-b = "<agent>"\n"my proj" = "<model>"\n')
    said = name(project="proj-a", template=OWN, keep="project")
    assert (said["name"], said["from"]) == ("proj-a-Nov-08 12:45", "project")
    assert said["save"] == {
        "key": "name.templates",
        "value": '{proj-b = "<agent>", "my proj" = "<model>", proj-a = "' + OWN + '"}'}
    # Kept for every launch, the same text is the one setting's value.
    assert name(project="proj-a", template=OWN, keep="global")["save"] == {
        "key": "name.template", "value": OWN}


def test_what_saving_writes_is_a_value_the_settings_command_takes():
    settings_file('[name.templates]\n"my proj" = "<model>"\n')
    for query in ({"template": 'a "quoted" <agent>'},
                  {"template": OWN, "project": "proj-a", "keep": "project"}):
        save = name(**query)["save"]
        done = subprocess.run([str(python()), "-P", "-m", "moxi", "config", "set", "--by",
                               "ui", "--", "launcher", save["key"], save["value"]],
                              capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr
    current = settings.current()
    assert current[names.TEMPLATE] == 'a "quoted" <agent>'
    assert current[names.TEMPLATES] == {"my proj": "<model>", "proj-a": OWN}


@pytest.mark.parametrize("template, problem", [
    ("<project>-<n>", "the name template holds <n>, which is not a piece a name is made of"),
    ("<branch>", "the name template holds <branch>, which is not a piece a name is made of"),
    ("<agent", "the name template holds a '<' or '>' that is not part of a piece"),
    ("agent>", "the name template holds a '<' or '>' that is not part of a piece"),
])
@pytest.mark.parametrize("keep", [None, "project"])
def test_a_template_the_save_check_refuses_has_no_name_and_nothing_to_save(
        template, problem, keep):
    query = {"project": "proj-a", "template": template, **({"keep": keep} if keep else {})}
    assert name(**query) == up(
        name=None, template=template, **{"from": keep or "global"},
        refused={"code": "bad-template", "row": "name", "problem": problem, "hint": HINT})


def test_a_refused_template_in_the_settings_is_answered_by_the_shipped_one():
    settings_file('[name]\ntemplate = "<n>"\n')
    assert name(project="proj-a") == up(name="proj-a-Nov-08 12:45", template=names.DEFAULT,
                                        **{"from": "global"})


def test_settings_that_cant_be_read_are_a_refusal():
    settings_file("[name\n")
    said = name(project="proj-a")
    assert (said["state"], said["name"], said["save"]) == ("up", None, None)
    assert said["refused"]["code"] == "bad-config"


@pytest.mark.parametrize("query, words", [
    ({"keep": "project"}, "keep is asked with template"),
    ({"keep": "global"}, "keep is asked with template"),
    ({"keep": "project", "template": "<agent>"}, "keep=project is asked with project"),
])
def test_a_keep_the_route_cant_take_is_refused(query, words):
    status, said, _ = get("/api/name", urlencode(query))
    assert status == 422 and words in str(said)


def test_a_query_outside_the_declared_ones_is_refused():
    assert get("/api/name", "keep=seat&template=x")[0] == 400
    assert get("/api/name", "harness=opencode")[0] == 400
    assert get("/api/name", "branch=main")[0] == 400
    assert get("/api/name", "harness=codex&model=gpt&cwd=/work/proj-b")[1]["name"] == \
        "proj-b-gpt-Nov-08 12:45"


def test_a_failed_read_says_only_the_class(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("/a/private/path")

    monkeypatch.setattr(names, "chosen", boom)
    status, said, raw = get("/api/name")
    assert status == 200 and said["state"] == "error" and b"private" not in raw
    assert "RuntimeError" in said["detail"]
    assert (said["name"], said["template"], said["from"], said["refused"], said["save"]) == (
        None, names.DEFAULT, "global", None, None)

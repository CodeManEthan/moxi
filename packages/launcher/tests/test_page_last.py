"""What a launch records of its own request, and what the page hands back from it: the
`sources` and `filled` a fresh launch's values hold, `filled` through the ticket and the
request file, `request_of` resolving again to the launch it was read from, and
`/api/last` keyed by a seat, a project or a folder. Every launch read here is made by
the launch itself, through the page's request and the resolver, in a terminal whose exec
is stood in for."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from moxi import modules
from test_page import get, post, write_presets

import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import api, catalog, first, job, pagehandlers, presets, slots, store, targets
from moxi_launcher.api import ReplayRequest

BRIEF = "Read the design and start the build."
#: The rows of a stored launch's values that are not handed back, or are no row.
NOT_HANDED = ("target", "slot", "name", "mode", "not_applied", "off_posture", "sources",
              "filled")


class NotFound(Exception):
    def __init__(self, key):
        self.key, self.fix = key, f"moxi projects add {key}"
        super().__init__(f"no project is named {key!r}")


@pytest.fixture
def installed(monkeypatch) -> set[str]:
    """The modules that read as installed: projects, until a test changes the set."""
    names = {"projects"}
    monkeypatch.setattr(slots, "links", lambda name: name in names)
    return names


@pytest.fixture(autouse=True)
def fresh_handlers(monkeypatch, installed):
    """Each test logs each failure class anew; no slots, turns hidden, nothing spawned."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(slots, "api", lambda: None)
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: None)
    fake_turns.hide(monkeypatch)

    def no_spawn(argv, **kw):
        raise AssertionError(f"spawned {argv}")

    monkeypatch.setattr(pagehandlers, "_spawn", no_spawn)


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def no_exec(monkeypatch):
    """exec stood in for, so a terminal launch returns and its row stays."""
    monkeypatch.setattr(targets, "_execve", lambda command, argv, env: None)
    here = os.getcwd()
    yield
    os.chdir(here)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Projects `proj-a` and `proj-b` each with a root, a folder outside both, an added
    folder, and the presets: seats `planner` and `coder` both at home in `proj-a`, and
    `proj-a`'s project preset."""
    roots = {"proj-a": tmp_path / "proj-a", "proj-b": tmp_path / "proj-b"}
    for root in roots.values():
        root.mkdir()
    (roots["proj-a"] / "src").mkdir()
    table = {key: SimpleNamespace(key=key, root=str(root), lead=None, associated=(),
                                  agents=(), notes_root=None) for key, root in roots.items()}

    def get_project(key):
        if key in table:
            return table[key]
        raise NotFound(key)

    def for_path(path):
        for project in table.values():
            if path == project.root or path.startswith(project.root + os.sep):
                return project
        return None

    projects_api = SimpleNamespace(get=get_project, for_path=for_path, NotFound=NotFound)
    found = modules.Found(name="projects", state=modules.PRESENT, interfaces=frozenset({1}),
                          api=projects_api)
    real_available, real_load = modules.available, modules.load
    monkeypatch.setattr(modules, "available",
                        lambda name: name == "projects" or real_available(name))
    monkeypatch.setattr(modules, "load", lambda name, interface=None: found
                        if name == "projects" else real_load(name, interface=interface))
    work, added = tmp_path / "work", tmp_path / "added"
    work.mkdir()
    added.mkdir()
    write_presets(f"""\
[seat.planner]
harness = "claude"
home = "proj-a"
effort = "high"

[seat.coder]
harness = "codex"
home = "proj-a"

[project.proj-a]
project = "proj-a"
model = "opus"
effort = "medium"
add_dir = ["{added}"]
""")
    brief_file = tmp_path / "brief.md"
    brief_file.write_text(BRIEF)
    return SimpleNamespace(root=roots["proj-a"], other=roots["proj-b"], work=work,
                           added=added, brief_file=brief_file, elsewhere=tmp_path)


def stored(request_id: str) -> store.LaunchRow:
    conn = store.connect(create=False)
    try:
        return store.get(conn, request_id)
    finally:
        conn.close()


def launch(body: dict, request_id: str, **more) -> store.LaunchRow:
    """A launch of the page's request `body`, made as the page's job makes it, and its
    row; `more` replaces fields of the request (`actor`)."""
    request, _ = pagehandlers.launch_request(body, request_id)
    api.launch(dataclasses.replace(request, **more))
    return stored(request_id)


def again(row: store.LaunchRow, elsewhere: Path):
    """What `request_of` hands back for `row`, and that sent as a body resolved again
    from a caller in another folder."""
    handed = pagehandlers.request_of(row, pagehandlers._available())
    os.chdir(elsewhere)
    request, _ = pagehandlers.launch_request(pagehandlers.body_of(handed), "again")
    return handed, presets.resolve(request)


def plain(value):
    return json.loads(json.dumps(value, default=str))


def assert_same_launch(row: store.LaunchRow, resolved) -> None:
    """`resolved` is the launch `row` recorded, row by row, but where it ran and its
    rendered name; a brief from a file is compared as the text the launch read."""
    skip = set(NOT_HANDED)
    if row.values["sources"].get("brief_file") in pagehandlers.GIVEN:
        skip |= {"brief", "brief_file"}
        assert resolved.values["brief"] == row.brief
    got = {k: v for k, v in resolved.values.items() if k not in skip}
    want = {k: row.values.get(k) for k in got}
    assert plain(got) == plain(want)
    assert (resolved.cwd, resolved.project_key) == (row.cwd, row.project_key)


def last(**query) -> dict:
    status, said, _ = get("/api/last", urlencode(query))
    assert status == 200 and said["state"] == "up", said
    return said


def handed_values(said: dict) -> dict:
    return {v["name"]: v["items"] if v["items"] is not None else v["value"]
            for v in said["values"]}


# --- what a launch records --------------------------------------------------------------------

def test_a_launch_records_where_each_value_came_from_and_what_was_filled(world):
    row = launch({"project_preset": "proj-a",
                  "values": {"effort": "high", "add_dir": [str(world.work)]},
                  "filled": ["effort", "model", "permission_mode"]}, "r1")
    sources = row.values["sources"]
    # Only a typed row is filled; the preset's model stays the preset's.
    assert row.values["filled"] == ["effort"] and sources["effort"] == api.LAST
    assert sources["add_dir"] == presets.TYPED and sources["model"] == presets.PRESET
    assert sources["cwd"] == presets.PRESET and sources["permission_mode"] == presets.SHIPPED


def test_a_replay_records_neither_and_the_log_takes_no_new_step(world):
    launch({"values": {"cwd": str(world.work), "model": "opus"}, "filled": ["model"]}, "r1")
    api.launch(ReplayRequest(stored("r1"), relaunch_of="r1", request_id="r2"))
    replayed = stored("r2")
    assert "sources" not in replayed.values and "filled" not in replayed.values
    assert replayed.values["model"] == "opus"
    assert len(store.STEPS) == 2


def test_filled_goes_through_the_request_file():
    data = {"typed": {"model": "opus"}, "filled": ["model"]}
    assert job.launch_request("r1", data).filled == ("model",)
    assert job.launch_request("r1", {}).filled == ()
    with pytest.raises(job.NotHere):
        job.launch_request("r1", {**data, "filled": "model"})


def test_the_page_writes_filled_into_the_request_file(world, monkeypatch):
    calls = []
    monkeypatch.setattr(pagehandlers, "_spawn", lambda argv, **kw: calls.append(argv) or 1)
    body = {"values": {"harness": "claude", "target": "headless", "cwd": str(world.work),
                       "brief": BRIEF, "model": "opus"}, "filled": ["model", "effort"]}
    status, said = post("/api/preview", body)
    assert status == 200 and said["ticket"], said
    status, _ = post("/api/launch", {**body, "ticket": said["ticket"]})
    assert status == 202 and len(calls) == 1
    data = json.loads(targets.request_file(said["request_id"]).read_text())
    # A name that is no typed row of the request is not carried.
    assert data["filled"] == ["model"]


def test_a_launch_whose_filled_differs_from_its_previews_is_a_bad_ticket(world):
    body = {"values": {"cwd": str(world.work), "model": "opus"},
            "filled": ["model", "effort"]}
    issued, _ = pagehandlers.ticket(body, "r1", now=1000.0)
    same = {**body, "filled": ["effort", "model"], "ticket": issued}
    assert pagehandlers.verify(same, now=1000.0) == ("r1", None)
    for filled in ([], ["model"], ["model", "effort", "add_dir"]):
        rid, refused = pagehandlers.verify({**body, "filled": filled, "ticket": issued},
                                           now=1000.0)
        assert rid is None and refused["code"] == "bad-ticket"


# --- what a launch hands back -----------------------------------------------------------------

CASES = {
    "a project picked": lambda w: {"values": {"project": "proj-a", "model": "sonnet"}},
    "a folder typed inside the project": lambda w: {
        "values": {"cwd": str(w.root / "src"), "effort": "low"}},
    "no place named": lambda w: {"values": {"model": "opus"}},
    "a seat with its home": lambda w: {"seat": "planner", "values": {"model": "sonnet"}},
    "a seat with a place typed over its home": lambda w: {
        "seat": "planner", "values": {"cwd": str(w.work)}},
    "a brief typed": lambda w: {"values": {"cwd": str(w.work), "brief": BRIEF}},
    "a brief from a file": lambda w: {
        "values": {"cwd": str(w.work), "brief_file": str(w.brief_file)}},
    "a list emptied over the preset's": lambda w: {
        "project_preset": "proj-a", "values": {"add_dir": []}},
    "a row left out": lambda w: {"project_preset": "proj-a", "leave_out": ["effort"]},
    "a name typed": lambda w: {"values": {"cwd": str(w.work), "name": "Night shift"}},
}


@pytest.mark.parametrize("case", list(CASES))
def test_what_a_launch_hands_back_resolves_to_that_launch(world, case):
    if case == "no place named":
        os.chdir(world.work)
    row = launch(CASES[case](world), "r1")
    handed, resolved = again(row, world.elsewhere)
    assert not {"target", "slot"} & set(handed["values"])
    assert handed["not_offered"] == [] and resolved.target == "terminal"
    assert_same_launch(row, resolved)


def test_what_is_handed_back_holds_only_what_the_request_gave(world):
    os.chdir(world.work)
    handed = pagehandlers.request_of(launch({"values": {"model": "opus"}}, "r1"),
                                     pagehandlers._available())
    # No place was named: the folder it started in is the request's.
    assert handed["values"] == {"model": "opus", "cwd": str(world.work)}
    handed = pagehandlers.request_of(
        launch({"seat": "planner", "values": {"model": "sonnet"}}, "r2"),
        pagehandlers._available())
    # The seat's home and effort are given again by the seat.
    assert handed["seat"] == "planner" and handed["values"] == {"model": "sonnet"}
    handed = pagehandlers.request_of(
        launch({"project_preset": "proj-a", "values": {"add_dir": []},
                "leave_out": ["effort"]}, "r3"), pagehandlers._available())
    assert handed["project_preset"] == "proj-a" and handed["values"] == {}
    assert sorted(handed["leave_out"]) == ["add_dir", "effort"]
    handed = pagehandlers.request_of(
        launch({"values": {"cwd": str(world.work), "brief_file": str(world.brief_file)}},
               "r4"), pagehandlers._available())
    # The text the launch read, never the file's path.
    assert handed["values"]["brief"] == BRIEF and "brief_file" not in handed["values"]
    handed = pagehandlers.request_of(
        launch({"values": {"project": "proj-a"}}, "r5"), pagehandlers._available())
    assert handed["values"] == {"cwd": str(world.root)}


def test_a_typed_row_this_install_no_longer_offers_is_named_and_left_out(world, installed):
    installed.add("usage")
    row = launch({"values": {"cwd": str(world.work), "warn_at": "1.5x"}}, "r1")
    handed, resolved = again(row, world.elsewhere)
    assert handed["values"]["warn_at"] == "1.5x"
    assert_same_launch(row, resolved)
    installed.discard("usage")
    handed, resolved = again(row, world.elsewhere)
    assert "warn_at" not in handed["values"] and handed["not_offered"] == ["warn_at"]
    assert resolved.cwd == str(world.work) and "warn_at" not in resolved.values


def test_a_launch_off_its_posture_is_handed_back_with_its_allowance(world):
    body = {"values": {"harness": "codex", "cwd": str(world.work),
                       "sandbox": "danger-full-access"}, "off_posture": True}
    row = launch(body, "r1")
    handed, resolved = again(row, world.elsewhere)
    assert handed["off_posture"] is True
    assert handed["values"]["sandbox"] == "danger-full-access"
    assert_same_launch(row, resolved)
    # Run again is accepted and makes that launch again.
    made = launch(pagehandlers.body_of(handed), "r2")
    assert made.values["sandbox"] == "danger-full-access" and made.values["off_posture"]
    # The same body without the allowance is refused.
    request, _ = pagehandlers.launch_request(
        {**pagehandlers.body_of(handed), "off_posture": False}, "r3")
    with pytest.raises(presets.OffPosture):
        presets.resolve(request)


# --- /api/last ------------------------------------------------------------------------------

def test_the_last_launch_fills_no_row_a_launch_never_carries(world):
    launch({"values": {"cwd": str(world.work), "brief": BRIEF, "model": "opus",
                       "effort": "high", "name": "Night shift",
                       "add_dir": [str(world.added)]}}, "r1")
    said = last(cwd=str(world.work))
    assert said["harness"] == "claude" and said["at"] == stored("r1").created_at
    values = handed_values(said)
    assert values == {"model": "opus", "effort": "high", "add_dir": [str(world.added)]}
    never = {r.name for r in catalog.launcher() if not r.carry}
    assert {"brief", "brief_file", "handoff", "cwd", "project", "scratch", "target",
            "slot", "name"} <= never
    for harness in catalog.HARNESSES:
        never |= {r.name for r in catalog.load(harness) if not r.carry}
    assert not never & set(values)


def test_a_form_filled_from_the_last_launch_resolves_and_records_the_fill(world):
    launch({"values": {"cwd": str(world.work), "model": "opus", "effort": "high"}}, "r1")
    values = handed_values(last(cwd=str(world.work)))
    body = {"values": {"cwd": str(world.work), **values}, "filled": sorted(values)}
    row = launch(body, "r2")
    assert sorted(row.values["filled"]) == ["effort", "model"]
    assert row.values["sources"]["model"] == api.LAST
    assert (row.values["model"], row.values["effort"]) == ("opus", "high")


def test_a_danger_value_and_a_posture_row_fill_like_any_other(world):
    launch({"values": {"harness": "codex", "cwd": str(world.work),
                       "sandbox": "danger-full-access", "approval": "never"},
            "off_posture": True}, "r1")
    said = last(cwd=str(world.work))
    assert said["harness"] == "codex"
    values = handed_values(said)
    # The harness was typed, so it is handed back with the rest.
    assert values == {"harness": "codex", "sandbox": "danger-full-access",
                      "approval": "never"}
    body = {"values": {"cwd": str(world.work), **values},
            "filled": sorted(values), "off_posture": True}
    request, _ = pagehandlers.launch_request(body, "r2")
    assert presets.resolve(request).values["sandbox"] == "danger-full-access"


def test_a_key_with_no_launch_or_no_key_fills_nothing(world):
    none = {"state": "up", "detail": None, "at": None, "harness": None, "values": []}
    assert last() == none
    assert last(cwd=str(world.work)) == none
    launch({"values": {"cwd": str(world.work), "model": "opus"}}, "r1")
    assert last(cwd=str(world.other)) == none and last(seat="planner") == none


def test_a_launch_logged_before_launches_recorded_their_sources_fills_nothing(world,
                                                                              monkeypatch):
    with monkeypatch.context() as older:
        older.setattr(api, "_recorded", lambda request, resolved, values: values)
        row = launch({"values": {"cwd": str(world.work), "model": "opus"}}, "r1")
    assert "sources" not in row.values
    assert last(cwd=str(world.work))["values"] == []
    assert last(project="proj-a")["values"] == []


def test_only_a_launch_this_user_made_afresh_and_that_ran_is_the_last(world, monkeypatch):
    launch({"values": {"cwd": str(world.work), "model": "opus"}}, "fresh")
    expected = {"model": "opus"}
    # A relaunch and a resume of it.
    api.launch(ReplayRequest(stored("fresh"), relaunch_of="fresh", request_id="relaunch"))
    with monkeypatch.context() as ended:
        # The launch's process is this test's, which runs: read it as ended.
        ended.setattr(store, "_running", lambda row, at, starting_s: None)
        api.launch(ReplayRequest(stored("fresh"), resume_of="fresh", request_id="resume"))
    # Another user's launch.
    launch({"values": {"cwd": str(world.work), "model": "sonnet"}}, "theirs", actor="bob")
    # A launch whose files could not be made, which ended failed.
    with monkeypatch.context() as broken:
        def no_files(*args, **kw):
            raise OSError(28, "No space left on device")

        broken.setattr(api, "_make_files", no_files)
        failed = launch({"values": {"cwd": str(world.work), "model": "fable"}}, "failed")
    assert failed.state == "failed"
    # A scratch launch: it ran in no folder of the key.
    launch({"values": {"scratch": "true", "model": "fable"}}, "scratch")
    rows = {r.request_id for r in _log()}
    assert rows == {"fresh", "relaunch", "resume", "theirs", "failed", "scratch"}
    said = last(cwd=str(world.work))
    assert said["at"] == stored("fresh").created_at and handed_values(said) == expected


def _log() -> list[store.LaunchRow]:
    conn = store.connect(create=False)
    try:
        return store.log(conn)
    finally:
        conn.close()


# --- whose launch -----------------------------------------------------------------------------

def test_the_last_launch_is_the_seats_else_the_projects_else_the_folders(world):
    root = str(world.root)
    launch({"seat": "planner", "values": {"model": "sonnet"}}, "planner")
    launch({"seat": "coder", "values": {"sandbox": "danger-full-access"},
            "off_posture": True}, "coder")
    launch({"project_preset": "proj-a", "values": {"effort": "low"}}, "project")
    launch({"values": {"cwd": root, "model": "fable"}}, "bare")
    for row in _log():
        assert row.cwd == root and row.project_key == "proj-a", row.request_id

    def newest(**query) -> str:
        at = last(**query)["at"]
        [rid] = [r.request_id for r in _log() if r.created_at == at]
        return rid

    assert newest(seat="planner") == "planner"
    assert handed_values(last(seat="coder")) == {"sandbox": "danger-full-access"}
    # A project's and a folder's launches never include a seat's.
    assert newest(project="proj-a") == "bare" and newest(cwd=root) == "bare"
    assert newest(cwd=root + "/") == "bare"
    # The first given of the three decides.
    assert newest(seat="planner", project="proj-a", cwd=root) == "planner"
    assert last(seat="nobody", project="proj-a")["values"] == []
    assert newest(project="proj-a", cwd=str(world.work)) == "bare"

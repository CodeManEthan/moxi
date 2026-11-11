"""What a launch records of its own request, and what the page hands back from it: the
`sources` and `filled` a fresh launch's values hold, `filled` through the ticket and the
request file, `request_of` resolving again to the launch it was read from, `/api/last`
and `/api/recents` keyed by a seat, a project or a folder, and `/api/recent` resolving
one recent as running it again would. Every launch read here is made by
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
from moxi_launcher import (api, catalog, first, job, page, pagehandlers, presets, slots, store,
                           targets)
from moxi_launcher.api import ReplayRequest

BRIEF = "Read the design and start the build."
#: The first turn the seat `scout` gives.
SCOUT_BRIEF = "Sweep the far corridor first."
#: The rows Home leaves out of a request whose box is empty or holds its own text.
FIRST_TURN = ["brief", "brief_file", "handoff"]
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
    folder, and the presets: seats `planner` and `coder` both at home in `proj-a`, seats
    `scout` with a brief and `pilot` with a brief file, and `proj-a`'s project preset."""
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
    brief_file = tmp_path / "brief.md"
    brief_file.write_text(BRIEF)
    write_presets(f"""\
[seat.planner]
harness = "claude"
home = "proj-a"
effort = "high"

[seat.coder]
harness = "codex"
home = "proj-a"

[seat.scout]
harness = "claude"
brief = "{SCOUT_BRIEF}"

[seat.pilot]
harness = "claude"
brief_file = "{brief_file}"

[project.proj-a]
project = "proj-a"
model = "opus"
effort = "medium"
add_dir = ["{added}"]
""")
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
    "a preset's brief left out": lambda w: {
        "seat": "scout", "values": {"cwd": str(w.work)}, "leave_out": FIRST_TURN},
    "a preset's brief file left out": lambda w: {
        "seat": "pilot", "values": {"cwd": str(w.work)}, "leave_out": FIRST_TURN},
    "a brief typed over a preset's brief file": lambda w: {
        "seat": "pilot", "values": {"cwd": str(w.work), "brief": SCOUT_BRIEF},
        "leave_out": ["brief_file", "handoff"]},
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
    # A brief the request left out stays out, so the preset's is not given again.
    for seat, rid in (("scout", "r6"), ("pilot", "r7")):
        handed = pagehandlers.request_of(
            launch({"seat": seat, "values": {"cwd": str(world.work)}, "leave_out": FIRST_TURN},
                   rid), pagehandlers._available())
        assert handed["values"] == {"cwd": str(world.work)}, seat
        assert {"brief", "brief_file"} <= set(handed["leave_out"]), seat
    # A brief typed is handed back as itself, the file left out beside it.
    handed = pagehandlers.request_of(
        launch({"seat": "pilot", "values": {"cwd": str(world.work), "brief": SCOUT_BRIEF},
                "leave_out": ["brief_file", "handoff"]}, "r8"), pagehandlers._available())
    assert handed["values"] == {"cwd": str(world.work), "brief": SCOUT_BRIEF}
    assert "brief_file" in handed["leave_out"] and "brief" not in handed["leave_out"]


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


def test_a_log_the_launcher_wrote_before_these_routes_is_read_by_all_three(world, monkeypatch):
    work = str(world.work)
    launch({"values": {"cwd": work, "model": "opus"}}, "fresh")
    with monkeypatch.context() as older:
        # A row as the launcher wrote it before launches were named and recorded what
        # their request gave: no `name`, no `sources`, no `filled`.
        older.setattr(api, "_recorded", lambda request, resolved, values: {
            k: v for k, v in values.items() if k != "name"})
        row = launch({"values": {"cwd": work, "model": "sonnet", "effort": "low"}}, "older")
    assert not {"name", "sources", "filled"} & set(row.values)
    assert row.created_at > stored("fresh").created_at
    for query in ({"cwd": work}, {"cwd": work + "/"}):
        said = last(**query)
        assert said["at"] == stored("fresh").created_at, query
        assert handed_values(said) == {"model": "opus"}, query
        assert listed(**query) == ["fresh"], query
    said = recent(request="older", target="terminal")
    assert said["refused"]["code"] == "no-recent" and said["request"] is None
    said = recent(request="fresh", target="terminal")
    assert said["refused"] is None and said["shows"]["model"] == "opus"


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


# --- /api/recents ---------------------------------------------------------------------------

def recents(**query) -> list[dict]:
    status, said, _ = get("/api/recents", urlencode(query))
    assert status == 200 and said["state"] == "up", said
    return said["recents"]


def listed(**query) -> list[str]:
    return [one["request_id"] for one in recents(**query)]


def test_recents_are_the_newest_three_requests_each_once(world):
    work = str(world.work)
    launch({"values": {"cwd": work, "model": "opus"}}, "r1")
    launch({"values": {"cwd": work, "model": "sonnet"}}, "r2")
    # The same request again: it heads the list in the older one's place.
    launch({"values": {"cwd": work, "model": "opus"}}, "r3")
    launch({"values": {"cwd": work, "brief": BRIEF, "effort": "high"}}, "r4")
    assert listed(cwd=work) == ["r4", "r3", "r2"]
    launch({"values": {"cwd": work, "model": "fable", "effort": "low"}}, "r5")
    said = recents(cwd=work)
    assert [one["request_id"] for one in said] == ["r5", "r4", "r3"]
    assert said[0] == {"request_id": "r5", "at": stored("r5").created_at,
                       "head": "fable · low", "target": "terminal", "slot": None}
    assert [one["head"] for one in said[1:]] == ["Read the design and start", "opus"]


def test_a_recents_head_is_whole_words_of_its_brief_else_its_model_else_its_harness(world):
    work = str(world.work)
    launch({"values": {"cwd": work, "brief": "x" * 40}}, "long")
    launch({"values": {"cwd": work, "brief": "  Fix\nthe   build  "}}, "spaced")
    launch({"values": {"cwd": work, "effort": "max"}}, "effort")
    launch({"values": {"cwd": work, "add_dir": [str(world.added)]}}, "bare")
    heads = {one["request_id"]: one["head"] for one in recents(cwd=work)}
    assert heads == {"bare": "claude", "effort": "max", "spaced": "Fix the build"}
    assert pagehandlers.head(stored("long")) == "x" * pagehandlers.HEAD_MAX


def test_recents_list_only_launches_this_user_made_afresh_that_ran(world, monkeypatch):
    work = str(world.work)
    launch({"values": {"cwd": work, "model": "opus"}}, "fresh")
    api.launch(ReplayRequest(stored("fresh"), relaunch_of="fresh", request_id="relaunch"))
    with monkeypatch.context() as ended:
        ended.setattr(store, "_running", lambda row, at, starting_s: None)
        api.launch(ReplayRequest(stored("fresh"), resume_of="fresh", request_id="resume"))
    launch({"values": {"cwd": work, "model": "sonnet"}}, "theirs", actor="bob")
    with monkeypatch.context() as broken:
        def no_files(*args, **kw):
            raise OSError(28, "No space left on device")

        broken.setattr(api, "_make_files", no_files)
        assert launch({"values": {"cwd": work, "model": "fable"}}, "failed").state == "failed"
    launch({"values": {"scratch": "true", "model": "fable"}}, "scratch")
    with monkeypatch.context() as older:
        older.setattr(api, "_recorded", lambda request, resolved, values: values)
        launch({"values": {"cwd": work, "effort": "low"}}, "older")
    assert {r.request_id for r in _log()} == {"fresh", "relaunch", "resume", "theirs",
                                               "failed", "scratch", "older"}
    assert listed(cwd=work) == ["fresh"]
    # None of the others is run again either.
    for rid in ("relaunch", "resume", "theirs", "failed", "scratch", "older", "nothing"):
        said = recent(request=rid, target="terminal")
        assert said["refused"]["code"] == "no-recent", rid
        assert said["request"] is None and said["shows"] is None


def test_recents_are_the_seats_else_the_projects_else_the_folders(world):
    root = str(world.root)
    launch({"seat": "planner", "values": {"model": "sonnet"}}, "planner")
    launch({"seat": "coder", "values": {"sandbox": "danger-full-access"},
            "off_posture": True}, "coder")
    launch({"project_preset": "proj-a", "values": {"effort": "low"}}, "project")
    launch({"values": {"cwd": root, "model": "fable"}}, "bare")
    assert listed(seat="planner") == ["planner"] and listed(seat="coder") == ["coder"]
    assert listed(project="proj-a") == ["bare", "project"]
    assert listed(cwd=root) == ["bare", "project"] == listed(cwd=root + "/")
    assert listed(seat="planner", project="proj-a", cwd=root) == ["planner"]
    assert listed(project="proj-a", cwd=str(world.work)) == ["bare", "project"]
    assert listed(seat="nobody", project="proj-a") == [] and listed() == []


# --- /api/recent ----------------------------------------------------------------------------

def recent(**query) -> dict:
    status, said, _ = get("/api/recent", urlencode(query))
    assert status == 200 and said["state"] == "up", said
    return said


def body_from(asked: dict) -> dict:
    """`/api/recent`'s `request` as the page posts it: its values as a table."""
    body = {"values": {v["name"]: v["items"] if v["items"] is not None else v["value"]
                       for v in asked["values"]},
            "leave_out": asked["leave_out"], "off_posture": asked["off_posture"]}
    for key in ("seat", "project_preset"):
        if asked[key]:
            body[key] = asked[key]
    return body


def edit_presets(old: str, new: str) -> None:
    text = presets.path().read_text()
    assert old in text
    write_presets(text.replace(old, new))


def test_a_recent_shows_what_its_request_resolves_to_now(world):
    launch({"project_preset": "proj-a", "values": {"effort": "high", "brief": BRIEF}}, "r1")
    os.chdir(world.elsewhere)
    said = recent(request="r1", target="terminal")
    assert said["refused"] is None
    asked = said["request"]
    assert asked["project_preset"] == "proj-a" and asked["seat"] is None
    assert body_from(asked) == {
        "project_preset": "proj-a", "leave_out": ["slot"], "off_posture": False,
        "values": {"effort": "high", "brief": BRIEF, "target": "terminal"}}
    # The page's preview of that request resolves to what the recent shows.
    status, previewed = post("/api/preview", body_from(asked))
    assert status == 200 and previewed["refused"] == [], previewed
    assert previewed["target"] == "terminal" and previewed["command"]
    request, _ = pagehandlers.launch_request(body_from(asked), "again")
    resolved, composition = presets.resolve(request), api.preview(request)
    assert said["shows"] == {
        "harness": previewed["harness"], "model": resolved.values["model"],
        "effort": resolved.values["effort"], "prompt": composition.brief,
        "cwd": resolved.cwd, "project_key": resolved.project_key,
        "off_posture": [], "danger": [], "not_offered": []}
    assert said["shows"]["model"] == "opus" and said["shows"]["effort"] == "high"
    assert said["shows"]["prompt"] == BRIEF and said["shows"]["cwd"] == str(world.root)
    # A preset's value is given again by the preset as it is now.
    edit_presets('model = "opus"', 'model = "sonnet"')
    assert recent(request="r1", target="terminal")["shows"]["model"] == "sonnet"


@pytest.mark.parametrize("seat", ["scout", "pilot"])
def test_a_recent_whose_launch_left_out_its_presets_brief_shows_no_prompt(world, seat):
    row = launch({"seat": seat, "values": {"cwd": str(world.work)}, "leave_out": FIRST_TURN},
                 "r1")
    assert row.brief is None
    said = recent(request="r1", target="terminal")
    assert said["refused"] is None, said["refused"]
    assert said["shows"]["prompt"] is None
    asked = said["request"]
    assert {"brief", "brief_file"} <= set(asked["leave_out"])
    assert not set(FIRST_TURN) & {v["name"] for v in asked["values"]}
    # Run again launches with no first turn, as the launch did.
    request, _ = pagehandlers.launch_request(body_from(asked), "again")
    assert api.preview(request).brief is None
    assert_same_launch(row, presets.resolve(request))


def test_a_recent_whose_preset_is_gone_is_refused(world):
    launch({"project_preset": "proj-a", "values": {"effort": "high"}}, "r1")
    edit_presets("[project.proj-a]", "[project.proj-b]")
    said = recent(request="r1", target="terminal")
    assert said["request"] is None and said["shows"] is None
    assert said["refused"]["code"] == "no-preset" and said["refused"]["problem"]


def test_a_recents_off_posture_names_the_rows_the_resolver_refuses(world):
    launch({"values": {"harness": "codex", "cwd": str(world.work),
                       "sandbox": "danger-full-access", "approval": "never"},
            "off_posture": True}, "r1")
    said = recent(request="r1", target="terminal")
    assert said["shows"]["off_posture"] == ["sandbox", "approval"]
    assert said["shows"]["danger"] == ["sandbox"] and said["request"]["off_posture"] is True
    body = body_from(said["request"])
    request, _ = pagehandlers.launch_request({**body, "off_posture": False}, "again")
    with pytest.raises(presets.OffPosture) as refused:
        presets.resolve(request)
    assert refused.value.row == "sandbox"
    request, _ = pagehandlers.launch_request(body, "again")
    assert presets.resolve(request).values["sandbox"] == "danger-full-access"
    # Defaults that hold the same posture leave nothing off it; the danger stays named.
    edit_presets("[seat.planner]", '[defaults]\nsandbox = "danger-full-access"\n'
                                   'approval = "never"\n\n[seat.planner]')
    said = recent(request="r1", target="terminal")
    assert said["shows"]["off_posture"] == [] and said["request"]["off_posture"] is False
    assert said["shows"]["danger"] == ["sandbox"]


def test_a_recent_names_a_posture_its_preset_now_sets_off_the_default(world):
    launch({"project_preset": "proj-a", "values": {"effort": "high"}}, "r1")
    edit_presets('model = "opus"', 'model = "opus"\npermission_mode = "plan"')
    said = recent(request="r1", target="terminal")
    assert said["shows"]["off_posture"] == ["permission_mode"]
    assert said["request"]["off_posture"] is True and said["shows"]["danger"] == []
    request, _ = pagehandlers.launch_request(
        {**body_from(said["request"]), "off_posture": False}, "again")
    with pytest.raises(presets.OffPosture) as refused:
        presets.resolve(request)
    assert refused.value.row == "permission_mode"


def test_the_posture_rows_off_their_default_are_the_resolvers_own():
    harness = "codex"
    values = {"sandbox": "danger-full-access", "approval": "never"}
    sources = {"sandbox": presets.TYPED, "approval": presets.PRESET}
    assert presets.off_posture_rows(harness, values, sources, {}) == ["sandbox", "approval"]
    assert presets.off_posture_rows(harness, values, sources,
                                    {"sandbox": "danger-full-access"}) == ["approval"]
    # A value [defaults] itself gives is never off it.
    shipped = {"sandbox": presets.DEFAULTS, "approval": presets.SHIPPED}
    assert presets.off_posture_rows(harness, values, shipped, {}) == []


def test_a_recent_runs_where_the_page_would_run_it_now(world):
    launch({"values": {"cwd": str(world.work), "model": "opus", "brief": BRIEF}}, "r1")
    said = recent(request="r1", target="headless")
    assert said["refused"] is None, said["refused"]
    values = {v["name"]: v["value"] for v in said["request"]["values"]}
    assert values == {"model": "opus", "cwd": str(world.work), "brief": BRIEF,
                      "target": "headless"}
    assert said["request"]["leave_out"] == ["slot"]
    # Without slots a slot launch is refused, as its preview would be.
    said = recent(request="r1", target="slot", slot="2")
    assert said["request"] is None and said["refused"]["code"]
    status, previewed = post("/api/preview", {"values": {
        "cwd": str(world.work), "model": "opus", "brief": BRIEF, "target": "slot",
        "slot": "2"}})
    assert status == 200 and previewed["refused"][0] == said["refused"]


def test_a_recent_names_a_row_this_install_no_longer_offers(world, installed):
    installed.add("usage")
    launch({"values": {"cwd": str(world.work), "warn_at": "1.5x"}}, "r1")
    assert recent(request="r1", target="terminal")["shows"]["not_offered"] == []
    installed.discard("usage")
    said = recent(request="r1", target="terminal")
    assert said["shows"]["not_offered"] == ["warn_at"]
    assert "warn_at" not in {v["name"] for v in said["request"]["values"]}


def test_the_recent_routes_mark_the_brief_and_every_handed_back_value():
    routes = {route.path: route.returns for route in page.PAGE.routes}
    assert routes["/api/recents"]["recents"].items.items["head"].mark == "redact"
    asked = routes["/api/recent"]["request"].items
    assert asked["values"].items.items["value"].mark == "redact"
    shows = routes["/api/recent"]["shows"].items
    assert shows["prompt"].mark == "redact" and shows["cwd"].mark == "path"

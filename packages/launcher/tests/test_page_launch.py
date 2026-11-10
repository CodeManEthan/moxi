"""A launch from the launcher's page: the form's values read as typed, the whole form
compared with what the layers give each row, `/api/preview` with its refusals, the PATH
check and the ticket, `/api/launch` with its request file and its detached job (a fake
spawn), and the job's `launch` act against a fake harness on PATH."""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import stat
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import pytest
from test_page import get, post, write_presets

import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import (api, catalog, cli, first, job, page, pagehandlers, presets, slots,
                           store, targets)

BRIEF = "Read the design and start the build."
POSIX = pytest.mark.skipif(sys.platform == "win32",
                           reason="the fake harness is a script run by its #! line")


@pytest.fixture(autouse=True)
def fresh_handlers(monkeypatch):
    """Each test logs each failure class anew; no other module reads as installed, turns
    is hidden, and nothing is spawned unless a test says how."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(slots, "links", lambda name: False)
    monkeypatch.setattr(slots, "api", lambda: None)
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: None)
    fake_turns.hide(monkeypatch)

    def no_spawn(argv, **kw):
        raise AssertionError(f"spawned {argv}")

    monkeypatch.setattr(pagehandlers, "_spawn", no_spawn)


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


@pytest.fixture
def work(tmp_path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def headless(work: Path, **more) -> dict:
    """A request body for a headless Claude launch in `work`."""
    values = {"harness": "claude", "target": "headless", "cwd": str(work), "brief": BRIEF,
              **more}
    return {"values": values}


def previewed(body: dict) -> dict:
    status, said = post("/api/preview", body)
    assert status == 200, said
    return said


def codes(said: dict) -> list[str]:
    return [r["code"] for r in said["refused"]]


def row(request_id: str) -> store.LaunchRow | None:
    if not store.path().exists():
        return None
    conn = store.connect(create=False)
    try:
        return store.get(conn, request_id)
    finally:
        conn.close()


def requested(request_id: str) -> dict:
    status, said, _ = get("/api/launches", f"request={request_id}")
    assert status == 200 and said["state"] == "up", said
    return said


class Spawned:
    """A stand-in for `proc.spawn` that records each call, and runs the launch job in
    this process when `run` is set."""

    def __init__(self, run: bool = False):
        self.calls: list[dict] = []
        self.run = run
        self.threads: list[threading.Thread] = []

    def __call__(self, argv, **kw):
        self.calls.append({"argv": list(argv), **kw})
        if self.run:
            worker = threading.Thread(target=job.run_request, args=(argv[-1],))
            worker.start()
            self.threads.append(worker)
        return 4242

    def join(self) -> None:
        for worker in self.threads:
            worker.join(60)


# --- the form's values as typed -------------------------------------------------------------

def test_each_value_becomes_its_rows_option_as_the_command_line_spells_it():
    words, direct = pagehandlers.tokens(
        {"harness": "claude", "target": "headless", "scratch": "true",
         "model": "opus", "remote_control": "true", "add_dir": ["/a", "~/b"],
         "extra_flags": ["--verbose", "-x"]}, "claude", lambda name: False)
    assert words == ["--harness=claude", "--target=headless", "--scratch", "--model=opus",
                     "--remote-control", "--add-dir=/a", "--add-dir=~/b",
                     "--", "--verbose", "-x"]
    assert direct == {}
    words, _ = pagehandlers.tokens({"scratch": "false", "remote_control": "mine"}, "claude",
                                   lambda name: False)
    assert words == ["--no-scratch", "--remote-control=mine"]


def test_what_the_command_line_cant_type_is_typed_directly():
    words, direct = pagehandlers.tokens({"remote_control": "false", "add_dir": []},
                                        "claude", lambda name: False)
    assert words == [] and direct == {"remote_control": False, "add_dir": []}
    # A row this install does not offer is refused there too, as the parser refuses it.
    with pytest.raises(catalog.NeedsModule):
        pagehandlers.tokens({"director": []}, "claude", lambda name: False)


@pytest.mark.parametrize("values,row,words", [
    ({"search": "true"}, "search", "not a claude row"),
    ({"scratch": "yes"}, "scratch", "not true or false"),
    ({"cwd": "work"}, "cwd", "relative path"),
    ({"add_dir": ["/a", "b"]}, "add_dir", "relative path"),
])
def test_a_value_the_launch_cant_take_is_refused(values, row, words):
    with pytest.raises(catalog.BadValue) as refused:
        pagehandlers.tokens(values, "claude", lambda name: False)
    assert refused.value.row == row and words in refused.value.problem


def test_the_values_go_through_the_command_lines_parser(work):
    request, command = pagehandlers.launch_request(
        {"seat": "", "values": {"harness": "codex", "cwd": str(work), "search": "true",
                                "add_dir": ["~/x"]}, "off_posture": True}, "r1")
    assert request.typed["search"] is True and request.typed["harness"] == "codex"
    assert request.typed["add_dir"] == [str(Path("~/x").expanduser())]
    assert request.seat is None and request.off_posture and request.request_id == "r1"
    assert request.cwd is None
    assert command[:4] == ["moxi", "launcher", "run", "--off-posture"]
    with pytest.raises(catalog.BadValue):
        pagehandlers.launch_request({"values": {"model": "opus", "harness": "claude",
                                                "target": "nowhere"}}, "r1")


# --- the whole form, compared with the layers ----------------------------------------------

#: One fixed id for every request id and session id minted, so a page's preview and a
#: command line's can be compared word for word.
FIXED = uuid.UUID("00000000-0000-4000-8000-000000000001")

RULE_FILE = """\
[defaults]
cwd = "{work}"
model = "sonnet"
effort = "low"
permission_mode = "acceptEdits"

[seat.planner]
harness = "claude"
effort = "high"
add_dir = ["{work}"]
brief = "the planner's brief"

[seat.scratchy]
scratch = true
model = "opus"

[seat.coder]
harness = "codex"
search = true
profile = "fast"

[project.there]
cwd = "{there}"
instructions = "the project's instructions"
extra_flags = ["--verbose"]
"""


@pytest.fixture
def rule_file(work) -> None:
    there = work / "there"
    there.mkdir()
    write_presets(RULE_FILE.format(work=work, there=there))


@pytest.fixture
def fixed_ids(monkeypatch) -> None:
    monkeypatch.setattr(uuid, "uuid4", lambda: FIXED)


def form_of(preset: dict) -> dict:
    status, said, _ = get("/api/form", urlencode(preset))
    assert status == 200 and said["refused"] is None, said
    return said


def filled(preset: dict) -> dict:
    """The request a form filled from `/api/form` for `preset` sends untouched, as the
    panel builds it: each field with a value in `values`, each empty one in `leave_out`
    (an unticked bool among them), a withheld text kept and in neither."""
    said = form_of(preset)
    harness = said["harness"]
    values: dict = {"harness": harness}
    leave_out = []
    for found in said["rows"]:
        name = found["name"]
        if name == "harness" or found["withheld"]:
            continue
        kind = catalog.row(harness, name).type
        if found["items"] is not None:
            values[name] = found["items"]
        elif found["value"] is not None and not (kind == "bool" and found["value"] == "false"):
            values[name] = found["value"]
        else:
            leave_out.append(name)
    return {**preset, "values": values, "leave_out": leave_out}


def edited(body: dict, change: dict, empty: list) -> dict:
    values = {**body["values"], **change}
    leave_out = [name for name in body["leave_out"] if name not in change]
    for name in empty:
        values.pop(name, None)
        leave_out.append(name)
    return {**body, "values": values, "leave_out": leave_out}


def command_line_preview(words: list[str]):
    """What `moxi launcher preview` composes for `words`."""
    request = cli._request(cli.parse(["preview", *words]))
    return request, api.preview(request)


def shown(composition) -> dict:
    return {"harness": composition.harness, "mode": composition.mode,
            "target": composition.target, "argv": list(composition.argv),
            "stamps": {str(k): str(v) for k, v in composition.env.items()},
            "first_turn": composition.first, "not_applied": list(composition.not_applied),
            "warnings": list(composition.warnings)}


def page_shown(said: dict) -> dict:
    return {key: said[key] for key in ("harness", "mode", "target", "argv", "first_turn",
                                       "not_applied", "warnings")} | {
        "stamps": {s["name"]: s["value"] for s in said["stamps"]}}


NONE: dict = {}
PLANNER = {"seat": "planner"}
SCRATCHY = {"seat": "scratchy"}
CODER = {"seat": "coder"}
THERE = {"project_preset": "there"}

#: For each preset: the form unchanged; one row changed; one emptied that the preset
#: sets; one emptied that only `[defaults]` sets; one emptied that nothing sets. Each
#: with the command line's words that ask for the same launch.
RULE_CASES = [
    (NONE, {}, [], []),
    (NONE, {"model": "fable"}, [], ["--model=fable"]),
    (NONE, {}, ["model"], ["--leave-out", "model"]),
    (NONE, {}, ["agent"], []),
    (PLANNER, {}, [], []),
    (PLANNER, {"effort": "max"}, [], ["--effort=max"]),
    (PLANNER, {}, ["add_dir"], ["--leave-out", "add_dir"]),
    (PLANNER, {}, ["model"], ["--leave-out", "model"]),
    (PLANNER, {}, ["native_name"], []),
    (PLANNER, {}, ["brief"], ["--leave-out", "brief"]),
    (SCRATCHY, {}, [], []),
    (SCRATCHY, {"model": "fable"}, [], ["--model=fable"]),
    (SCRATCHY, {}, ["scratch"], ["--leave-out", "scratch"]),
    (SCRATCHY, {}, ["effort"], ["--leave-out", "effort"]),
    (SCRATCHY, {}, ["agent"], []),
    (CODER, {}, [], []),
    (CODER, {"profile": "slow"}, [], ["--profile=slow"]),
    (CODER, {}, ["search"], ["--leave-out", "search"]),
    (CODER, {}, ["model"], ["--leave-out", "model"]),
    (CODER, {}, ["trust_cwd"], []),
    (THERE, {}, [], []),
    (THERE, {"model": "fable"}, [], ["--model=fable"]),
    (THERE, {}, ["extra_flags"], ["--leave-out", "extra_flags"]),
    (THERE, {}, ["effort"], ["--leave-out", "effort"]),
    (THERE, {}, ["brief_file"], []),
]


def _preset_words(preset: dict) -> list[str]:
    return [word for key, value in preset.items()
            for word in (f"--{key.replace('_', '-')}", value)]


@pytest.mark.parametrize("preset,change,empty,words", RULE_CASES,
                         ids=[f"{'-'.join(c[0].values()) or 'none'}:{c[1] or c[2] or 'as-filled'}"
                              for c in RULE_CASES])
def test_the_pages_preview_is_the_command_lines_for_the_same_launch(
        rule_file, fixed_ids, monkeypatch, tmp_path, preset, change, empty, words):
    monkeypatch.setenv("PATH", str(tmp_path))  # a terminal launch's PATH is the user's
    body = edited(filled(preset), change, empty)
    said = previewed(body)
    assert said["refused"] == [], said["refused"]
    line = [*_preset_words(preset), *words]
    request, composition = command_line_preview(line)
    assert page_shown(said) == shown(composition)
    assert said["left_out"] == list(request.left_out)
    # The command to copy is the command line's, so run as written it previews the same.
    assert said["command"] == cli.command_line(["moxi", "launcher", "run", *line])
    if not change and not empty:
        assert said["left_out"] == [] and "--leave-out" not in said["command"]


def test_the_command_to_copy_carries_the_posture_allowance_and_each_leave_out(
        rule_file, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    body = edited(filled(PLANNER), {}, ["permission_mode", "add_dir"])
    said = previewed({**body, "off_posture": True})
    assert said["refused"] == [] and said["left_out"] == ["permission_mode", "add_dir"]
    assert said["command"] == ("moxi launcher run --seat planner --off-posture "
                               "--leave-out permission_mode --leave-out add_dir")
    words = cli.parse(said["command"].split()[2:])
    assert words.leave_out == ["permission_mode", "add_dir"] and words.off_posture


def test_a_value_sent_equal_to_its_layer_keeps_the_layers_source(rule_file):
    request, _ = pagehandlers.launch_request(filled(PLANNER), "r1")
    assert dict(request.typed) == {} and tuple(request.extra) == ()
    assert tuple(request.left_out) == ()
    resolved = job.placed(request)
    assert resolved.sources["effort"] == "preset" and resolved.sources["model"] == "defaults"
    # A posture row equal to [defaults] is not typed, so it needs no allowance.
    assert resolved.sources["permission_mode"] == "defaults" and not request.off_posture


@POSIX
def test_a_defaults_row_the_mode_does_not_take_stays_not_applied(work, harness):
    write_presets(f'[defaults]\napproval = "never"\n\n[seat.coder]\nharness = "codex"\n'
                  f'cwd = "{work}"\ntarget = "headless"\nbrief = "{BRIEF}"\n')
    body = filled(CODER)
    assert body["values"]["approval"] == "never"
    said = previewed(body)
    assert said["refused"] == [] and said["mode"] == "exec"
    assert "approval" in said["not_applied"]


def test_limit_at_at_its_shipped_word_is_not_refused_for_its_companion(work, monkeypatch):
    monkeypatch.setattr(slots, "links", lambda name: name in ("turns", "usage"))
    write_presets(f'[seat.lead]\ncwd = "{work}"\n')
    body = filled({"seat": "lead"})
    assert (body["values"]["limit_at"], body["values"]["recycle_at_limit"]) == ("1x", "off")
    request, _ = pagehandlers.launch_request(body, "r1")
    assert "limit_at" not in request.typed
    assert job.placed(request).values["limit_at"] == "1x"
    # Typed, the same word would be refused: limit_at needs recycle_at_limit.
    with pytest.raises(catalog.Refused, match="limit_at"):
        job.placed(dataclasses.replace(request, typed={"limit_at": "1x"}))


def test_a_word_enum_left_out_with_its_companion_off_launches_with_no_recycling(
        work, monkeypatch):
    monkeypatch.setattr(slots, "links", lambda name: name in ("turns", "usage"))
    write_presets(f'[seat.lead]\ncwd = "{work}"\nrecycle_at_limit = "compact"\n'
                  'limit_at = "2x"\n')
    body = edited(filled({"seat": "lead"}), {"recycle_at_limit": "off"}, ["limit_at"])
    request, _ = pagehandlers.launch_request(body, "r1")
    assert dict(request.typed) == {"recycle_at_limit": "off"}
    assert tuple(request.left_out) == ("limit_at",)
    resolved = job.placed(request)
    assert resolved.values["recycle_at_limit"] == "off"
    assert (resolved.values["limit_at"], resolved.sources["limit_at"]) == ("1x", "left-out")


def test_limit_at_the_preset_sets_to_its_shipped_word_can_still_be_left_out(
        work, monkeypatch, tmp_path, fixed_ids):
    # The seat chose limit_at even though "1x" is the shipped word, so leaving it out
    # removes that choice, as --leave-out does on the command line.
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(slots, "links", lambda name: name in ("turns", "usage"))
    write_presets(f'[seat.lead]\ncwd = "{work}"\nrecycle_at_limit = "compact"\n'
                  'limit_at = "1x"\n')
    body = edited(filled({"seat": "lead"}), {"recycle_at_limit": "off"}, ["limit_at"])
    said = previewed(body)
    assert said["refused"] == [] and said["left_out"] == ["limit_at"]
    line = ["--seat", "lead", "--recycle-at-limit=off", "--leave-out", "limit_at"]
    request, composition = command_line_preview(line)
    assert page_shown(said) == shown(composition)
    assert said["left_out"] == list(request.left_out)
    assert said["command"] == cli.command_line(["moxi", "launcher", "run", *line])
    # Kept, the seat's limit_at needs the recycling the form turned off.
    kept = previewed(edited(filled({"seat": "lead"}), {"recycle_at_limit": "off"}, []))
    assert codes(kept) == ["needs-module"] and kept["refused"][0]["row"] == "limit_at"


@POSIX
def test_a_codex_seats_approval_at_its_shipped_word_left_out_is_not_refused(
        work, harness, fixed_ids):
    write_presets(f'[seat.coder]\nharness = "codex"\ncwd = "{work}"\ntarget = "headless"\n'
                  f'brief = "{BRIEF}"\napproval = "on-request"\n')
    assert codes(previewed(filled(CODER))) == ["not-taken"]
    said = previewed(edited(filled(CODER), {}, ["approval"]))
    assert said["refused"] == [] and said["left_out"] == ["approval"]
    line = ["--seat", "coder", "--leave-out", "approval"]
    request, composition = command_line_preview(line)
    assert page_shown(said) == shown(composition)
    _, command = pagehandlers.launch_request(edited(filled(CODER), {}, ["approval"]), "r1")
    assert command == ["moxi", "launcher", "run", *line]


@POSIX
def test_a_codex_seats_approval_left_out_on_a_mode_without_it_is_not_refused(work, harness):
    write_presets(f'[seat.coder]\nharness = "codex"\ncwd = "{work}"\ntarget = "headless"\n'
                  f'brief = "{BRIEF}"\napproval = "never"\n')
    # approval "never" is off the default posture, so the seat needs the allowance.
    kept = previewed({**filled(CODER), "off_posture": True})
    assert codes(kept) == ["not-taken"]
    said = previewed(edited(filled(CODER), {}, ["approval"]))
    assert said["refused"] == [] and said["left_out"] == ["approval"]


@POSIX
def test_a_codex_seats_approval_left_out_launches_under_claude(work, harness):
    write_presets(f'[seat.coder]\nharness = "codex"\ncwd = "{work}"\ntarget = "headless"\n'
                  f'brief = "{BRIEF}"\napproval = "never"\n')
    said = previewed({"seat": "coder", "values": {"harness": "claude"},
                      "leave_out": ["approval"]})
    assert said["refused"] == [] and said["harness"] == "claude"
    assert said["left_out"] == ["approval"]
    # Without it, the seat can't be laid for claude.
    refused = previewed({"seat": "coder", "values": {"harness": "claude"}})
    assert codes(refused) == ["bad-preset"]


def test_a_left_out_posture_row_that_defaults_sets_needs_the_allowance(rule_file):
    body = edited(filled(NONE), {}, ["permission_mode"])
    said = previewed(body)
    assert codes(said) == ["off-posture"] and said["ticket"] is None
    assert said["refused"][0]["hint"] == job.OFF_POSTURE_HINT
    allowed = previewed({**body, "off_posture": True})
    assert allowed["refused"] == [] and allowed["left_out"] == ["permission_mode"]
    assert "acceptEdits" not in allowed["argv"]


def test_leaving_out_the_only_place_is_refused_no_cwd(rule_file):
    said = previewed(edited(filled(NONE), {}, ["cwd"]))
    assert codes(said) == ["no-cwd"] and said["ticket"] is None


@pytest.mark.parametrize("body,row", [
    ({"values": {"model": "opus"}, "leave_out": ["model"]}, "model"),
    ({"values": {"add_dir": []}, "leave_out": ["add_dir"]}, "add_dir"),
    ({"values": {}, "leave_out": ["nonsense"]}, "nonsense"),
    ({"values": {}, "leave_out": ["harness"]}, "harness"),
    ({"seat": "coder", "values": {"harness": "codex"}, "leave_out": ["harness"]}, "harness"),
])
def test_a_row_in_both_lists_harness_or_no_row_at_all_is_refused_bad_value(rule_file, body,
                                                                            row):
    said = previewed(body)
    assert codes(said) == ["bad-value"] and said["refused"][0]["row"] == row


def test_an_empty_list_or_a_switch_set_off_reads_as_a_leave_out(work):
    write_presets(f'[seat.lead]\ncwd = "{work}"\nadd_dir = ["{work}"]\n'
                  'remote_control = true\nextra_flags = ["--verbose"]\n')
    request, command = pagehandlers.launch_request(
        {"seat": "lead", "values": {"add_dir": [], "remote_control": "false",
                                    "extra_flags": []}}, "r1")
    assert dict(request.typed) == {} and tuple(request.extra) == ()
    assert set(request.left_out) == {"add_dir", "remote_control", "extra_flags"}
    resolved = job.placed(request)
    assert resolved.values["add_dir"] == [] and resolved.values["remote_control"] is False
    assert resolved.values["extra_flags"] == []
    assert command.count("--leave-out") == 3


def test_a_seat_with_scratch_unticked_launches_in_the_typed_cwd(work, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    write_presets(f'[defaults]\ncwd = "{work}"\n\n[seat.scratchy]\nscratch = true\n')
    body = edited(filled(SCRATCHY), {"cwd": str(elsewhere)}, ["scratch"])
    request, _ = pagehandlers.launch_request(body, "r1")
    assert dict(request.typed) == {"cwd": str(elsewhere)}
    assert tuple(request.left_out) == ("scratch",)
    resolved = job.placed(request)
    assert resolved.cwd == str(elsewhere) and resolved.scratch is False


def test_an_unchanged_form_sends_no_left_out_row(work):
    write_presets(f'[defaults]\ncwd = "{work}"\n\n[seat.home]\nproject = "moxi"\n')
    body = filled({"seat": "home"})
    assert "cwd" in body["leave_out"] and body["values"]["project"] == "moxi"
    request, command = pagehandlers.launch_request(body, "r1")
    assert tuple(request.left_out) == () and dict(request.typed) == {}
    assert "--leave-out" not in command


def test_the_harness_is_typed_only_when_it_is_not_the_layers(rule_file):
    request, command = pagehandlers.launch_request(filled(CODER), "r1")
    assert "harness" not in request.typed and "--harness=codex" not in command
    body = filled(THERE)
    request, command = pagehandlers.launch_request(
        {**body, "values": {"harness": "codex", "cwd": body["values"]["cwd"]},
         "leave_out": []}, "r1")
    assert request.typed["harness"] == "codex" and "--harness=codex" in command


def test_an_unchanged_form_types_nothing_and_leaves_nothing_out_over_every_preset(work,
                                                                                  monkeypatch):
    monkeypatch.setattr(slots, "links", lambda name: name in ("turns", "usage"))
    there = work / "there"
    there.mkdir()
    write_presets(RULE_FILE.format(work=work, there=there) + f"""
[seat.lead]
cwd = "{work}"
recycle_at_limit = "compact"
limit_at = "1x"

[seat.headless]
harness = "codex"
target = "headless"
approval = "on-request"
brief = "{BRIEF}"

[seat.blank]
model = ""
add_dir = []
remote_control = false
""")
    # A row a layer sets to an empty value shows empty, and leaving it out removes nothing.
    for preset in (NONE, PLANNER, SCRATCHY, CODER, THERE, {"seat": "lead"},
                   {"seat": "headless"}, {"seat": "blank"}):
        request, command = pagehandlers.launch_request(filled(preset), "r1")
        assert dict(request.typed) == {} and tuple(request.extra) == (), preset
        assert tuple(request.left_out) == () and "--leave-out" not in command, preset


# --- every row, set by one layer: as filled, and emptied ---

def _sample(row: catalog.Row, work: Path) -> tuple[object, dict]:
    """A value of `row` other than its shipped default, and the rows it needs beside it."""
    if row.name == "limit_at":
        return "2x", {"recycle_at_limit": "compact"}
    if row.name == "slot":
        return "3", {"target": "slot"}
    if row.optional_value or row.type == "bool":
        return True, {}
    if row.values:
        return next(v for v in row.values if v != row.default), {}
    if row.name == catalog.EXTRA:
        return ["--verbose"], {}
    if row.type == "list" or row.repeatable:
        return [str(work), str(work / "b")], {}
    if row.type == "path":
        return str(work), {}
    return f"x-{row.name}", {}


def _every_module_but_projects(name: str) -> bool:
    return name != "projects"


EVERY_ROW = [(harness, name, layer)
             for harness in catalog.HARNESSES
             for name in catalog.offered(harness, _every_module_but_projects)
             if name not in ("harness", "project")
             for layer in ("defaults", "seat")]


def _resolved(request) -> tuple:
    try:
        found = presets.resolve(request, available=slots.links)
    except catalog.Refused as refused:
        return ("refused", type(refused).__name__, str(refused))
    return dict(found.values), dict(found.sources), sorted(found.chosen)


@pytest.mark.parametrize("harness,name,layer", EVERY_ROW,
                         ids=["-".join(case) for case in EVERY_ROW])
def test_every_row_a_layer_sets_is_kept_as_filled_and_left_out_when_emptied(
        work, monkeypatch, harness, name, layer):
    monkeypatch.setattr(slots, "links", _every_module_but_projects)
    (work / "b").mkdir()
    value, beside = _sample(catalog.row(harness, name), work)
    rows = {name: value, **beside}
    defaults = {} if {"cwd", "scratch"} & set(rows) else {"cwd": str(work)}
    seat = {"harness": harness}
    (defaults if layer == "defaults" else seat).update(rows)
    write_presets("[defaults]\n" + "".join(f"{k} = {catalog.toml_literal(v)}\n" for k, v in defaults.items())
                  + "[seat.s]\n" + "".join(f"{k} = {catalog.toml_literal(v)}\n" for k, v in seat.items()))
    body = {**filled({"seat": "s"}), "off_posture": True}
    request, command = pagehandlers.launch_request(body, "r1")
    assert dict(request.typed) == {} and tuple(request.left_out) == ()
    assert "--leave-out" not in command
    # Emptied as the panel empties it (a withheld text: its keep box unticked).
    request, command = pagehandlers.launch_request(edited(body, {}, [name]), "r2")
    assert dict(request.typed) == {} and tuple(request.left_out) == (name,)
    assert command[-2:] == ["--leave-out", name]
    line = api.LaunchRequest(seat="s", off_posture=True, left_out=(name,), request_id="r3")
    assert _resolved(request) == _resolved(line)


# --- groups of rows that exclude each other ---

def test_a_typed_cwd_with_the_presets_project_carried_is_refused_naming_both(work, tmp_path):
    write_presets('[seat.home]\nproject = "moxi"\n')
    body = edited(filled({"seat": "home"}), {"cwd": str(tmp_path)}, [])
    request, _ = pagehandlers.launch_request(body, "r1")
    assert request.typed["project"] == "moxi" and request.typed["cwd"] == str(tmp_path)
    with pytest.raises(catalog.BadValue) as refused:
        job.placed(request)
    assert refused.value.problem == "cwd and project each name where the session starts"
    said = previewed(body)
    assert codes(said) == ["bad-value"] and said["ticket"] is None


def test_a_typed_cwd_with_the_presets_project_left_out_launches_in_cwd(tmp_path):
    write_presets('[seat.home]\nproject = "moxi"\n')
    body = edited(filled({"seat": "home"}), {"cwd": str(tmp_path)}, ["project"])
    request, _ = pagehandlers.launch_request(body, "r1")
    assert dict(request.typed) == {"cwd": str(tmp_path)}
    assert tuple(request.left_out) == ("project",)
    assert job.placed(request).cwd == str(tmp_path)


def test_a_typed_brief_with_the_presets_brief_file_carried_is_refused(work):
    write_presets(f'[seat.filed]\ncwd = "{work}"\nbrief_file = "/b.md"\n')
    body = edited(filled({"seat": "filed"}), {"brief": BRIEF}, [])
    with pytest.raises(catalog.BadValue) as refused:
        job.placed(pagehandlers.launch_request(body, "r1")[0])
    assert refused.value.problem == "both brief and brief_file are given"
    left = edited(filled({"seat": "filed"}), {"brief": BRIEF}, ["brief_file"])
    resolved = job.placed(pagehandlers.launch_request(left, "r1")[0])
    assert resolved.values["brief"] == BRIEF and resolved.values["brief_file"] == ""


def test_a_kept_withheld_brief_with_a_typed_brief_file_takes_the_file(work):
    """A kept withheld brief is in neither list, so the typed file replaces the preset's
    brief as `--brief-file` does on the command line; unticked, it is left out."""
    write_presets(f'[seat.planner]\ncwd = "{work}"\nbrief = "the planner\'s brief"\n')
    body = filled({"seat": "planner"})
    assert "brief" not in body["values"] and "brief" not in body["leave_out"]
    typed = edited(body, {"brief_file": "/b.md"}, [])
    request, _ = pagehandlers.launch_request(typed, "r1")
    assert dict(request.typed) == {"brief_file": "/b.md"} and tuple(request.left_out) == ()
    resolved = job.placed(request)
    assert resolved.values["brief_file"] == "/b.md" and resolved.values["brief"] == ""
    unticked = edited(body, {"brief_file": "/b.md"}, ["brief"])
    request, _ = pagehandlers.launch_request(unticked, "r1")
    assert tuple(request.left_out) == ("brief",)
    assert job.placed(request).values["brief_file"] == "/b.md"


# --- the comparison at launch, and nothing written to the presets file ---

def test_the_comparison_runs_again_at_launch(work):
    write_presets(f'[seat.lead]\ncwd = "{work}"\nmodel = "opus"\n')
    body = filled({"seat": "lead"})
    request, _ = pagehandlers.launch_request(body, "r1")
    assert "model" not in request.typed
    write_presets(f'[seat.lead]\ncwd = "{work}"\nmodel = "sonnet"\n')
    request, _ = pagehandlers.launch_request(body, "r1")
    assert request.typed["model"] == "opus"


def _presets_names(module) -> set[str]:
    """Every name `module`'s source reaches through `presets.`."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id == "presets"}


def test_no_route_or_handler_names_a_writer_of_the_presets_file():
    readers = {"read", "check", "layered", "harness_of", "Request", "Presets", "Preset",
               "Layered", "ERROR", "DEFAULTS", "PRESET", "TWO_SPELLINGS", "mode_of",
               "SHIPPED", "TYPED", "LEFT_OUT"}
    for module in (page, pagehandlers):
        text = Path(module.__file__).read_text(encoding="utf-8")
        assert "presets.toml" not in text and "presets.path" not in text, module.__name__
        assert _presets_names(module) <= readers, module.__name__


def test_the_page_server_writes_nothing_to_the_presets_file(rule_file, work, harness,
                                                           monkeypatch):
    monkeypatch.setattr(pagehandlers, "_spawn", Spawned())
    file = presets.path()
    before, stamp = file.read_bytes(), file.stat().st_mtime_ns
    for preset in (NONE, PLANNER, SCRATCHY, CODER, THERE):
        get("/api/form", urlencode(preset))
        body = edited(filled(preset), {"target": "headless", "brief": BRIEF},
                      ["model"])
        said = previewed(body)
        if said["ticket"]:
            post("/api/launch", {**body, "ticket": said["ticket"]})
    assert file.read_bytes() == before and file.stat().st_mtime_ns == stamp


# --- /api/preview ----------------------------------------------------------------------------

@POSIX
def test_a_preview_shows_the_launch_and_issues_a_ticket(work, harness):
    said = previewed(headless(work, model="opus"))
    assert said["refused"] == []
    assert (said["harness"], said["mode"], said["target"]) == ("claude", "print", "headless")
    assert said["argv"][:2] == ["claude", "-p"] and "--model" in said["argv"]
    stamps = {s["name"]: s["value"] for s in said["stamps"]}
    assert stamps["MOXI_LAUNCHER_REQUEST"] == said["request_id"]
    assert said["command"] is None
    rid, expires, mac = said["ticket"].split(".")
    assert rid == said["request_id"] and len(mac) == 64
    assert int(expires) - time.time() == pytest.approx(600, abs=5)
    assert said["expires_at"].endswith("Z")
    # The preview starts and writes nothing of the launch.
    assert harness.runs() == [] and row(rid) is None


def test_a_value_the_page_sends_overrides_the_preset_even_the_shipped_default(work, harness):
    write_presets(f'[project.here]\ncwd = "{work}"\nmodel = "opus"\ntarget = "headless"\n'
                  f'brief = "{BRIEF}"\n')
    shown = previewed({"project_preset": "here"})
    assert shown["refused"] == [] and "opus" in shown["argv"]
    shown = previewed({"project_preset": "here", "values": {"model": ""}})
    assert shown["refused"] == [] and "--model" not in shown["argv"]


def test_a_launch_that_names_no_place_is_refused_no_cwd(harness):
    said = previewed({"values": {"target": "headless", "brief": BRIEF}})
    assert codes(said) == ["no-cwd"] and said["refused"][0]["row"] == "cwd"
    assert said["ticket"] is None and said["request_id"] is None


def test_a_scratch_or_a_project_preset_is_a_place(work, harness):
    assert previewed({"values": {"target": "headless", "brief": BRIEF,
                                 "scratch": "true"}})["refused"] == []
    write_presets(f'[project.here]\ncwd = "{work}"\n')
    said = previewed({"project_preset": "here",
                      "values": {"target": "headless", "brief": BRIEF}})
    assert said["refused"] == []



def test_a_posture_off_its_default_is_refused_naming_the_pages_control(work, harness):
    said = previewed(headless(work, permission_mode="bypassPermissions"))
    [refused] = said["refused"]
    assert refused["code"] == "off-posture" and said["ticket"] is None
    assert refused["hint"] == job.OFF_POSTURE_HINT
    assert "--off-posture" not in refused["hint"]
    allowed = previewed({**headless(work, permission_mode="bypassPermissions"),
                         "off_posture": True})
    assert allowed["refused"] == [] and allowed["ticket"] is not None

def test_a_harness_not_on_the_servers_path_is_refused_naming_that_path(work, tmp_path,
                                                                       monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    said = previewed(headless(work))
    [refused] = said["refused"]
    assert refused["code"] == "not-found"
    assert refused["problem"] == pagehandlers.NOT_ON_PATH.format(command="claude",
                                                                 path=str(empty))
    assert said["argv"][:2] == ["claude", "-p"] and said["ticket"] is None


def test_the_preview_shows_every_refusal_check_finds(tmp_path, harness):
    said = previewed({"values": {"harness": "claude", "target": "slot",
                                 "cwd": str(tmp_path / "gone"), "brief": BRIEF}})
    assert set(codes(said)) >= {"no-cwd"} and len(said["refused"]) >= 2
    assert any("slots" in r["problem"] for r in said["refused"])
    assert said["ticket"] is None


def test_a_refusal_before_the_composition_is_answered_alone(work):
    said = previewed({"values": {"harness": "claude", "cwd": str(work), "model": "opus",
                                 "search": "true"}})
    assert codes(said) == ["bad-value"] and said["refused"][0]["row"] == "search"
    assert said["argv"] == [] and said["harness"] is None


def test_a_preview_that_fails_unexpectedly_names_only_the_class(work, monkeypatch):
    def broken(request, **kw):
        raise RuntimeError("/home/someone/private")

    monkeypatch.setattr(pagehandlers.api, "preview", broken)
    status, said = post("/api/preview", headless(work))
    assert status == 200 and codes(said) == ["error"]
    assert said["refused"][0]["problem"] == pagehandlers.UNEXPECTED.format("RuntimeError")
    assert said["ticket"] is None


def test_a_terminal_launch_is_answered_with_the_command_to_copy(work, tmp_path,
                                                               monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # the terminal's PATH is the user's, not ours
    said = previewed({"seat": "", "values": {"cwd": str(work), "model": "opus"},
                      "off_posture": False})
    assert said["refused"] == [] and said["target"] == "terminal"
    assert said["ticket"] is None and said["request_id"] is None
    assert said["command"] == f"moxi launcher run --cwd={work} --model=opus"


def test_the_ticket_key_is_made_once_private_and_replaced_when_bad(work, harness):
    previewed(headless(work))
    key = Path(store.path()).parent / pagehandlers.KEY_FILE
    first_key = key.read_bytes()
    assert len(first_key) == 32 and stat.S_IMODE(key.stat().st_mode) == 0o600
    previewed(headless(work))
    assert key.read_bytes() == first_key
    key.write_bytes(b"short")
    assert len(pagehandlers.page_key()) == 32 and key.read_bytes() != first_key


def test_a_ticket_holds_only_for_its_request_until_it_expires(work):
    body = headless(work)
    issued, _ = pagehandlers.ticket(body, "r1", now=1000.0)
    assert pagehandlers.verify({**body, "ticket": issued}, now=1000.0) == ("r1", None)
    assert pagehandlers.verify({**body, "ticket": issued}, now=1600.0) == ("r1", None)
    _, late = pagehandlers.verify({**body, "ticket": issued}, now=1601.0)
    assert late["code"] == "expired"
    changed = headless(work, model="opus")
    # Each part of the request the ticket was issued for binds it: the values, the rows
    # left out, the seat, the project preset, the posture allowance and the request id.
    for bad in ({**changed, "ticket": issued}, {**body, "leave_out": ["model"], "ticket": issued},
                {**body, "seat": "other", "ticket": issued},
                {**body, "project_preset": "other", "ticket": issued},
                {**body, "off_posture": True, "ticket": issued},
                {**body, "ticket": "r2" + issued[2:]},
                {**body, "ticket": "nonsense"}, {**body, "ticket": "../x.1.ab"}):
        rid, refused = pagehandlers.verify(bad, now=1000.0)
        assert rid is None and refused["code"] == "bad-ticket"



def test_a_ticket_binds_the_rows_left_out_in_any_order(work):
    body = {**headless(work), "leave_out": ["model", "add_dir"]}
    issued, _ = pagehandlers.ticket(body, "r1", now=1000.0)
    same = {**body, "leave_out": ["add_dir", "model"], "ticket": issued}
    assert pagehandlers.verify(same, now=1000.0) == ("r1", None)
    fewer = {**body, "leave_out": ["model"], "ticket": issued}
    assert pagehandlers.verify(fewer, now=1000.0)[1]["code"] == "bad-ticket"


def test_a_ticket_whose_signature_is_not_hex_is_a_bad_ticket(work):
    body = headless(work)
    issued, _ = pagehandlers.ticket(body, "r1", now=1000.0)
    head = issued.rsplit(".", 1)[0]
    for sig in ("é" * 64, "\u00ff", ""):
        rid, refused = pagehandlers.verify({**body, "ticket": f"{head}.{sig}"}, now=1000.0)
        assert rid is None and refused["code"] == "bad-ticket"

# --- /api/launch -----------------------------------------------------------------------------

def ticketed(body: dict, request_id: str = "r1") -> dict:
    return {**body, "ticket": pagehandlers.ticket(body, request_id)[0]}


def test_a_launch_writes_its_request_file_and_spawns_its_job_detached(work, harness,
                                                                      monkeypatch):
    spawned = Spawned()
    monkeypatch.setattr(pagehandlers, "_spawn", spawned)
    said = previewed(headless(work, model="opus"))
    status, answer = post("/api/launch", {**headless(work, model="opus"),
                                          "ticket": said["ticket"]})
    rid = said["request_id"]
    assert (status, answer) == (202, {"status": f"/api/launches?request={rid}"})
    [call] = spawned.calls
    assert Path(call["argv"][0]).name.startswith("moxi-launcher-job")
    assert call["argv"][1:] == ["launch", rid] and call["detached"] is True
    assert call["output"] == targets.run_log(rid)
    assert "MOXI_LAUNCHER_REQUEST" not in call["env"]
    path = targets.request_file(rid)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = json.loads(path.read_text())
    assert data["act"] == "launch" and data["request_id"] == rid
    assert data["typed"]["model"] == "opus" and data["typed"]["cwd"] == str(work)
    assert data["left_out"] == []
    # A second use of the ticket is a 409, and spawns nothing.
    status, answer = post("/api/launch", {**headless(work, model="opus"),
                                          "ticket": said["ticket"]})
    assert status == 409 and rid in answer["conflict"]
    assert len(spawned.calls) == 1


def test_a_ticket_used_after_its_job_ended_is_a_409_too(work, harness, monkeypatch):
    spawned = Spawned()
    monkeypatch.setattr(pagehandlers, "_spawn", spawned)
    body = ticketed(headless(work))
    conn = store.connect()
    store.add_event(conn, store.LAUNCH_REFUSED, {"code": "no-cwd"}, request_id="r1")
    conn.close()
    status, answer = post("/api/launch", body)
    assert status == 409 and spawned.calls == []
    assert not targets.request_file("r1").exists()


def test_a_bad_or_expired_ticket_is_refused_and_starts_nothing(work):
    body = headless(work)
    status, said = post("/api/launch", {**body, "ticket": "r1.1.ab"})
    assert status == 200 and said["refused"]["code"] == "bad-ticket"
    issued = pagehandlers.ticket(body, "r1", now=0.0)[0]
    status, said = post("/api/launch", {**body, "ticket": issued})
    assert status == 200 and said["refused"]["code"] == "expired"
    assert not targets.runs_dir().exists() or not list(targets.runs_dir().iterdir())


def test_a_terminal_launch_is_never_run_by_the_page(work):
    body = ticketed({"values": {"cwd": str(work), "model": "opus"}})
    status, said = post("/api/launch", body)
    assert status == 200 and said["refused"]["code"] == "terminal"
    assert said["command"] == f"moxi launcher run --cwd={work} --model=opus"
    assert not targets.request_file("r1").exists()


def test_a_launch_refused_again_at_launch_writes_nothing(harness):
    body = ticketed({"values": {"target": "headless", "brief": BRIEF}})
    status, said = post("/api/launch", body)
    assert status == 200 and said["refused"]["code"] == "no-cwd"
    assert not targets.request_file("r1").exists()


def test_a_job_that_does_not_start_leaves_no_request_file(work, harness, monkeypatch):
    def broken(argv, **kw):
        raise OSError(2, "No such file: '/home/someone/bin/moxi-launcher-job'")

    monkeypatch.setattr(pagehandlers, "_spawn", broken)
    status, said = post("/api/launch", ticketed(headless(work)))
    assert status == 200 and said["refused"]["code"] == targets.SPAWN_FAILED
    assert "someone" not in said["refused"]["problem"]
    assert not targets.request_file("r1").exists()


@POSIX
def test_preview_then_launch_then_the_row_answers_the_status_path(work, harness,
                                                                  monkeypatch):
    spawned = Spawned(run=True)
    monkeypatch.setattr(pagehandlers, "_spawn", spawned)
    body = headless(work)
    said = previewed(body)
    status, answer = post("/api/launch", {**body, "ticket": said["ticket"]})
    assert status == 202
    spawned.join()
    rid = said["request_id"]
    [launched] = requested(rid)["launches"]
    assert launched["request_id"] == rid and launched["state"] == "started"
    assert launched["cwd"] == str(work) and launched["target"] == "headless"
    run = harness.one()
    # What ran is what the preview showed, but the Claude session id each one mints.
    assert run["cwd"] == str(work) and len(run["argv"]) == len(said["argv"])
    assert launched["session_id"] in run["argv"]
    assert [w for w in run["argv"][1:] if not _uuid(w)] == [
        w for w in said["argv"][1:] if not _uuid(w)]
    assert not targets.request_file(rid).exists()


def _uuid(word: str) -> bool:
    try:
        uuid.UUID(word)
    except ValueError:
        return False
    return True


# --- the launch job --------------------------------------------------------------------------

def request_file(request_id: str = "r1", **data) -> Path:
    return job.write_request(request_id, {"act": "launch", "request_id": request_id,
                                          "typed": {}, "extra": [], **data})


@POSIX
def test_the_job_launches_the_request_and_deletes_its_file(work, harness):
    path = request_file(typed={"target": "headless", "cwd": str(work), "brief": BRIEF})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert job.main(["launch", "r1"]) == 0
    got = row("r1")
    assert got.state == "started" and got.cwd == str(work)
    assert harness.one()["cwd"] == str(work)
    assert not path.exists()


def test_a_second_request_file_for_one_id_is_refused():
    request_file()
    with pytest.raises(FileExistsError):
        request_file()


@pytest.mark.parametrize("typed,code", [
    ({"target": "headless", "brief": BRIEF}, "no-cwd"),
    ({"target": "headless", "brief": BRIEF, "cwd": "/nowhere/at/all"}, "no-cwd"),
    ({"cwd": "WORK", "brief": BRIEF}, "not-run"),
    ({"target": "headless", "cwd": "WORK"}, "no-brief"),
    ({"target": "headless", "cwd": "WORK", "brief": BRIEF, "slot": "3"}, "needs-module"),
])
def test_a_refusal_before_the_row_is_recorded_under_the_request_id(typed, code, harness,
                                                                   work):
    typed = {k: str(work) if v == "WORK" else v for k, v in typed.items()}
    path = request_file(typed=typed)
    assert job.run_request("r1") == "refused"
    assert row("r1") is None and not path.exists()
    said = requested("r1")
    assert said["launches"] == [] and said["refused"]["code"] == code
    assert said["refused"]["problem"] and said["refused"]["hint"]
    assert harness.runs() == []


def test_the_job_reads_the_rows_a_request_file_leaves_out():
    request = job.launch_request("r1", {"typed": {}, "extra": [],
                                        "left_out": ["model", "add_dir"]})
    assert tuple(request.left_out) == ("model", "add_dir")
    assert tuple(job.launch_request("r1", {"typed": {}, "extra": []}).left_out) == ()
    with pytest.raises(job.NotHere):
        job.launch_request("r1", {"typed": {}, "extra": [], "left_out": "model"})


@POSIX
def test_the_job_launches_without_the_rows_its_request_leaves_out(work, harness):
    write_presets(f'[defaults]\ncwd = "{work}"\nmodel = "opus"\n')
    request_file(typed={"target": "headless", "brief": BRIEF}, left_out=["model"])
    assert job.main(["launch", "r1"]) == 0
    assert row("r1").state == "started" and row("r1").values["model"] == ""
    assert "opus" not in harness.one()["argv"]


def test_a_request_leaving_out_the_only_place_is_refused_no_cwd(work, harness):
    write_presets(f'[defaults]\ncwd = "{work}"\n')
    path = request_file(typed={"target": "headless", "brief": BRIEF}, left_out=["cwd"])
    assert job.run_request("r1") == "refused"
    assert requested("r1")["refused"]["code"] == "no-cwd"
    assert row("r1") is None and not path.exists() and harness.runs() == []


def test_a_launch_past_its_wait_is_recorded_timed_out(work):
    release = threading.Event()

    def slow(request):
        release.wait(30)

    path = request_file(typed={"target": "headless", "cwd": str(work), "brief": BRIEF})
    try:
        assert job.run_request("r1", wait_s=0.2, launch=slow) == "timed-out"
    finally:
        release.set()
    assert requested("r1")["refused"]["code"] == "timed-out"
    assert not path.exists()


def test_a_request_file_that_cant_be_read_is_recorded(monkeypatch):
    path = targets.request_file("r1")
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    assert job.run_request("r1") == "refused"
    assert requested("r1")["refused"]["code"] == "not-run"
    assert not path.exists()
    assert job.run_request("r2") == "refused"  # no file at all


def test_an_unexpected_failure_names_only_its_class(work):
    def broken(request):
        raise RuntimeError("/home/someone/private")

    request_file(typed={"target": "headless", "cwd": str(work), "brief": BRIEF})
    assert job.run_request("r1", launch=broken) == "refused"
    refused = requested("r1")["refused"]
    assert refused["code"] == "error"
    assert refused["problem"] == job.UNEXPECTED_JOB.format("RuntimeError")


def test_the_sweep_takes_an_old_request_file():
    path = request_file()
    old = time.time() - 100 * 86400
    os.utime(path, (old, old))
    swept = targets.sweep(keep_days=90)
    assert path.name in swept.removed and not path.exists()


@pytest.mark.parametrize(("pinned", "more"), [
    ('target = "slot"', ""), ('target = "slot"\nslot = "7"', " --leave-out slot"),
    ('target = "headless"', "")])
def test_a_terminal_launchs_command_names_the_terminal_where_the_defaults_pin_another_target(
        work, tmp_path, monkeypatch, pinned, more):
    monkeypatch.setenv("PATH", str(tmp_path))
    write_presets(f"[defaults]\n{pinned}\n")
    # What the box on the shell's home sends, empty, for a launch in the user's terminal.
    said = previewed({"values": {"harness": "claude", "cwd": str(work), "target": "terminal"},
                      "leave_out": ["brief", "slot"]})
    assert said["refused"] == [] and said["target"] == "terminal"
    assert said["command"] == f"moxi launcher run --cwd={work} --target=terminal{more}"
    # Pasted, the command starts in the terminal: the pinned target does not take it.
    _, composition = command_line_preview(said["command"].split()[3:])
    assert composition.target == "terminal"

"""The launcher's page server in process, through loopback's strict app, and the launcher
without the UI.

Loopback is reached as the page reaches it, through `moxi.modules.load`: the launcher's
distribution doesn't require it, so its tests don't import it either. The app runs behind
`guard` with an upstream profile, as the runner serves it, with strict projection, so a
handler that sends a key its route doesn't declare, or a null its route doesn't allow,
fails here.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest
from moxi import modules
from moxi import proc

from moxi_launcher import catalog, first, page, pagehandlers, presets, slots, store

ROOT = Path(__file__).resolve().parents[1]
#: Where the web build writes the page's assets.
WEB = ROOT / "src" / "moxi_launcher" / "web"
TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321
SID = "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f"
ROUTES = ["/api/catalog", "/api/presets", "/api/form", "/api/launches", "/api/sessions",
          "/api/state", "/api/runs", "/api/places", "/api/targets", "/api/last", "/api/recents",
          "/api/recent", "/api/name", "/api/preview", "/api/launch", "/api/resume",
          "/api/events"]
#: Keys no answer of the page may declare, at any depth: an environment, a launch's
#: recorded values, argv or brief, and a process's facts.
NEVER_SENT = {"env", "env_json", "base_env", "base_env_json", "environ", "brief",
              "values_json", "argv_json", "pid", "pid_started", "endpoint", "pane_id", "job"}


def loopback():
    found = modules.load("ui", interface=3)
    assert found.ok, found.reason
    return found.api.loopback


@pytest.fixture(autouse=True)
def fresh_handlers(monkeypatch):
    """Each test logs each failure class anew, and reads no module as installed unless it
    says so."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(slots, "links", lambda name: False)


def installed(monkeypatch, *names):
    monkeypatch.setattr(slots, "links", lambda name: name in names)


# --- driving the app ----------------------------------------------------------------------

def served():
    lb = loopback()
    inner = lb.app(page.PAGE, module="launcher", server_id=SERVER_ID, strict=True)
    profile = lb.upstream(lb.build_table(page.PAGE)[0], token=TOKEN, port=PORT)
    return lb.guard(inner, lambda: profile)


async def _send(app, method: str, path: str, query: bytes, body: bytes | None):
    headers = [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode())]
    if body is not None:
        headers.append((b"content-type", b"application/json"))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "server": ("127.0.0.1", PORT),
        "client": ("127.0.0.1", 50000), "scheme": "http", "method": method,
        "root_path": "", "path": path, "raw_path": path.encode("ascii"),
        "query_string": query, "headers": headers, "state": {},
    }
    sent = {"status": 0, "body": b""}
    asked = [False]

    async def receive():
        if not asked[0]:
            asked[0] = True
            return {"type": "http.request", "body": body or b"", "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    await app(scope, receive, send)
    return sent["status"], sent["body"]


def get(path: str, query: str = "", app=None) -> tuple[int, dict | None, bytes]:
    status, raw = asyncio.run(_send(app or served(), "GET", path, query.encode(), None))
    return status, json.loads(raw) if raw else None, raw


def post(path: str, body: dict, app=None) -> tuple[int, dict | None]:
    data = json.dumps(body).encode()
    status, raw = asyncio.run(_send(app or served(), "POST", path, b"", data))
    return status, json.loads(raw) if raw else None


def keys(shape, out=None) -> dict[str, set]:
    """Every key `shape` declares at any depth, with the marks it carries."""
    out = {} if out is None else out
    items = shape.items
    if isinstance(items, loopback().Out):
        keys(items, out)
    elif items is not None:
        for key, sub in items.items():
            out.setdefault(key, set()).add(sub.mark)
            keys(sub, out)
    return out


def declared() -> dict[str, set]:
    lb = loopback()
    found: dict[str, set] = {}
    for route in page.PAGE.routes:
        shapes = route.returns or {}
        shapes = {**shapes, **(route.topics or {})}
        keys(lb.Out("table", items=shapes), out=found)
    return found


# --- the table ------------------------------------------------------------------------------

def test_the_gate_matrix_passes():
    assert loopback().gate_matrix(page.PAGE)


def test_the_page_declares_every_route_none_for_the_gateway():
    exported = loopback().export(page.PAGE)
    routes = {route["path"]: route for route in exported["routes"]}
    assert list(routes) == ROUTES
    assert {p for p, r in routes.items() if r["kind"] == "act"} == {
        "/api/preview", "/api/launch", "/api/resume"}
    assert not any(r["gateway"] for r in routes.values())
    assert [p for p, r in routes.items() if r["stream"]] == ["/api/events"]
    assert list(routes["/api/events"]["topics"]) == ["sessions", "launches"]
    assert routes["/api/launches"]["query"]["request"]["type"] == "string"
    assert page.PAGE.idle_s == 300 and page.PAGE.health is None


def test_the_export_lists_the_built_assets():
    exported = loopback().export(page.PAGE)
    assert exported["assets"] == ["build-stamp.txt", "page.js", "prompt.js", "sessions.js",
                                  "state.js"]


def test_no_answer_declares_an_environment_a_brief_or_a_process():
    found = declared()
    assert not set(found) & NEVER_SENT


def test_paths_are_marked_path_and_what_may_hold_the_brief_redact():
    found = declared()
    assert found["cwd"] == {"path"} and found["handoff_path"] == {"path"}
    preview = page.PAGE.routes[ROUTES.index("/api/preview")].returns
    assert preview["argv"].items.mark == "redact"
    assert preview["first_turn"].mark == "redact" and preview["command"].mark == "redact"


def test_a_launch_request_takes_every_settable_catalog_key_and_no_other():
    settable = {row.name: row for rows in (catalog.launcher(), catalog.load("claude"),
                                           catalog.load("codex"))
                for row in rows if row.settable}
    assert set(page.VALUES) == set(settable)
    assert {"resume", "session_id", "skip_git_check", "update_check"}.isdisjoint(page.VALUES)
    for name, item in page.VALUES.items():
        many = settable[name].type == "list" or settable[name].repeatable
        assert item.type == ("list" if many else "string") and not item.required, name
    status, said = post("/api/preview", {"values": {"model": "opus", "nope": "x"}})
    assert (status, said) == (400, {"error": "unknown key: values.nope"})


# --- /api/catalog -------------------------------------------------------------------------

def catalog_answer() -> dict[str, list[dict]]:
    status, said, _ = get("/api/catalog")
    assert status == 200 and said["state"] == "up" and said["detail"] is None
    return {h["name"]: {f["name"]: f for f in h["fields"]} for h in said["harnesses"]}


def test_the_catalog_gives_each_harness_the_launcher_rows_then_its_own():
    answer = catalog_answer()
    assert list(answer) == ["claude", "codex"]
    want = [row.name for row in (*catalog.launcher(), *catalog.load("claude")) if row.settable]
    assert list(answer["claude"]) == want
    assert "resume" not in answer["claude"] and "skip_git_check" not in answer["codex"]
    assert "search" in answer["codex"] and "search" not in answer["claude"]


def test_a_catalog_row_is_spelled_as_the_command_line_spells_it():
    claude = catalog_answer()["claude"]
    assert claude["scratch"]["default"] == "false" and claude["scratch"]["type"] == "bool"
    assert claude["harness"]["default"] == "claude"
    assert claude["harness"]["values"] == ["claude", "codex"]
    assert claude["cwd"]["default"] is None and claude["cwd"]["type"] == "path"
    assert claude["add_dir"]["default"] is None and claude["add_dir"]["value_items"] is None
    assert claude["add_dir"]["repeatable"] is True
    assert claude["permission_mode"]["flag"] == "--permission-mode"
    assert claude["permission_mode"]["posture"] is True
    assert claude["remote_control"]["optional_value"] is True
    assert claude["remote_control"]["default"] is None


def test_a_row_this_install_does_not_offer_is_drawn_off_with_the_launchers_reason():
    claude = catalog_answer()["claude"]
    assert claude["director"]["requires"] == "comms"
    assert claude["director"]["needs_absent"] is True
    assert claude["director"]["note"] == "director needs comms"
    # A row needing several modules names none to the form; its note says which is absent.
    assert claude["keep_warm"]["requires"] is None
    assert claude["keep_warm"]["note"] == "keep_warm needs turns"
    assert claude["model"]["needs_absent"] is False and claude["model"]["note"] is None


def test_an_enum_value_this_install_does_not_offer_is_left_out_with_a_note(monkeypatch):
    installed(monkeypatch, "comms", "turns", "usage")
    claude = catalog_answer()["claude"]
    assert claude["director"]["needs_absent"] is False and claude["director"]["note"] is None
    limit = claude["recycle_at_limit"]
    assert limit["needs_absent"] is False
    assert limit["values"] == ["off", "compact"]
    assert limit["note"] == ("relaunch is not offered here: relaunch at limit needs slots "
                             "and a slot launch")


def test_a_failed_catalog_read_says_only_the_class(monkeypatch):
    def broken(harness):
        raise RuntimeError("/home/someone/private/catalog.toml is unreadable")

    monkeypatch.setattr(catalog, "load", broken)
    status, said, raw = get("/api/catalog")
    assert status == 200 and said == {
        "state": "error", "detail": pagehandlers.UNEXPECTED.format("RuntimeError"),
        "harnesses": []}
    assert b"someone" not in raw


# --- /api/presets -------------------------------------------------------------------------

PRESETS_FILE = """\
[seat.planner]
harness = "claude"
model = "opus"
brief = "the planner's brief, kept off the page"
add_dir = ["/a", "/b"]

[seat.broken]
harness = "claude"
effort = "loudest"

[project.moxi]
harness = "codex"
cwd = "/work"
search = true
"""


def write_presets(text: str) -> None:
    where = presets.path()
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(text)


def test_presets_send_each_preset_spelled_with_its_brief_withheld():
    write_presets(PRESETS_FILE)
    status, said, raw = get("/api/presets")
    assert status == 200 and said["state"] == "up"
    named = {p["name"]: p for p in said["presets"]}
    assert [p["name"] for p in said["presets"]] == ["broken", "planner", "moxi"]
    planner = {v["name"]: v for v in named["planner"]["values"]}
    assert named["planner"]["kind"] == "seat" and named["planner"]["harness"] == "claude"
    assert planner["model"] == {"name": "model", "value": "opus", "items": None}
    assert planner["add_dir"] == {"name": "add_dir", "value": None, "items": ["/a", "/b"]}
    assert planner["brief"] == {"name": "brief", "value": None, "items": None}
    assert b"kept off the page" not in raw
    moxi = {v["name"]: v["value"] for v in named["moxi"]["values"]}
    assert moxi == {"harness": "codex", "cwd": "/work", "search": "true"}
    assert [(p["where"], p["key"], p["level"]) for p in said["problems"]] == [
        ("seat.broken", "effort", "error")]


def test_a_presets_file_that_is_not_toml_is_one_problem():
    write_presets("[seat.planner\nharness = 'claude'\n")
    status, said, raw = get("/api/presets")
    assert status == 200 and said["state"] == "up" and said["presets"] == []
    [problem] = said["problems"]
    assert (problem["where"], problem["code"], problem["level"]) == ("file", "bad-file", "error")
    assert str(presets.path()).encode() not in raw


def test_no_presets_file_reads_empty():
    status, said, _ = get("/api/presets")
    assert (status, said) == (200, {"state": "up", "detail": None, "presets": [],
                                    "problems": []})


# --- /api/form ----------------------------------------------------------------------------

FORM_FILE = """\
[defaults]
model = "sonnet"
cwd = "/srv/default"
instructions = "the default instructions, kept off the page"

[seat.planner]
harness = "claude"
effort = "high"
scratch = true
add_dir = ["/a", "/b"]
brief = "the planner's brief, kept off the page"

[seat.filed]
instructions_file = "/seat.md"

[seat.coder]
harness = "codex"
approval = "never"
search = true

[project.there]
cwd = "/srv/there"
"""


def form(query: str = "") -> dict:
    status, said, raw = get("/api/form", query)
    assert status == 200 and said["state"] == "up" and said["detail"] is None, said
    assert b"kept off the page" not in raw
    return said


def form_rows(said: dict) -> dict[str, dict]:
    return {r["name"]: r for r in said["rows"]}


def test_the_form_gives_each_offered_row_in_catalog_order():
    write_presets(FORM_FILE)
    said = form("seat=planner")
    assert said["harness"] == "claude" and said["refused"] is None
    want = [row.name for row in (*catalog.launcher(), *catalog.load("claude"))
            if row.settable and row.offered(slots.links, "claude")]
    assert [r["name"] for r in said["rows"]] == want
    # A row this install doesn't offer has no entry: the form shows it off.
    assert "director" not in form_rows(said)


def test_the_form_spells_each_value_as_a_presets_value_with_its_source():
    write_presets(FORM_FILE)
    rows = form_rows(form("seat=planner"))
    assert rows["effort"] == {"name": "effort", "value": "high", "items": None,
                              "source": "preset", "withheld": False}
    assert rows["add_dir"] == {"name": "add_dir", "value": None, "items": ["/a", "/b"],
                               "source": "preset", "withheld": False}
    assert rows["scratch"]["value"] == "true" and rows["scratch"]["source"] == "preset"
    assert rows["model"] == {"name": "model", "value": "sonnet", "items": None,
                             "source": "defaults", "withheld": False}
    # A row no layer sets holds its shipped default: a word for a word enum, else nothing.
    assert (rows["target"]["value"], rows["target"]["source"]) == ("terminal", "shipped")
    assert (rows["agent"]["value"], rows["agent"]["items"]) == (None, None)
    assert rows["harness"] == {"name": "harness", "value": "claude", "items": None,
                               "source": "preset", "withheld": False}


def test_the_form_shows_one_place_whole_from_the_latest_layer():
    write_presets(FORM_FILE)
    rows = form_rows(form("seat=planner"))
    assert [(rows[n]["value"], rows[n]["source"]) for n in ("cwd", "project", "scratch")] == [
        (None, "shipped"), (None, "shipped"), ("true", "preset")]
    rows = form_rows(form("project_preset=there"))
    assert (rows["cwd"]["value"], rows["cwd"]["source"]) == ("/srv/there", "preset")
    rows = form_rows(form())
    assert (rows["cwd"]["value"], rows["cwd"]["source"]) == ("/srv/default", "defaults")
    assert (rows["scratch"]["value"], rows["scratch"]["source"]) == ("false", "shipped")


def test_a_text_written_inline_comes_withheld_from_either_layer():
    write_presets(FORM_FILE)
    rows = form_rows(form("seat=planner"))
    assert rows["brief"] == {"name": "brief", "value": None, "items": None,
                             "source": "preset", "withheld": True}
    assert rows["instructions"] == {"name": "instructions", "value": None, "items": None,
                                    "source": "defaults", "withheld": True}
    # A spelling another layer's replaced is at its default, and nothing is withheld.
    rows = form_rows(form("seat=filed"))
    assert rows["instructions"] == {"name": "instructions", "value": None, "items": None,
                                    "source": "shipped", "withheld": False}
    assert (rows["instructions_file"]["value"], rows["instructions_file"]["withheld"]) == (
        "/seat.md", False)


def test_the_form_is_drawn_for_the_presets_harness_or_the_one_asked():
    write_presets(FORM_FILE)
    said = form("seat=coder")
    assert said["harness"] == "codex"
    rows = form_rows(said)
    assert rows["approval"]["value"] == "never" and rows["search"]["value"] == "true"
    assert "permission_mode" not in rows
    said = form("project_preset=there&harness=codex")
    assert said["harness"] == "codex" and "sandbox" in form_rows(said)
    # The harness row holds the layers' harness; the form is drawn for the one asked.
    assert form_rows(said)["harness"]["value"] == "claude"


def test_both_presets_named_are_refused_in_the_resolvers_words():
    write_presets(FORM_FILE)
    said = form("seat=planner&project_preset=there")
    assert said["rows"] == [] and said["harness"] == "claude"
    assert said["refused"] == {"code": "bad-value", "row": None,
                               "problem": "a launch names at most one preset",
                               "hint": "give --seat or --project-preset, not both"}


def test_a_harness_the_preset_cant_be_laid_for_answers_refused():
    write_presets(FORM_FILE)
    said = form("seat=coder&harness=claude")
    assert said["harness"] == "claude" and said["rows"] == []
    assert said["refused"]["code"] == "bad-preset" and said["refused"]["row"] == "approval"


def test_an_unknown_preset_or_an_unreadable_file_answers_refused():
    write_presets(FORM_FILE)
    said = form("seat=nobody")
    assert said["refused"]["code"] == "no-preset" and said["rows"] == []
    write_presets("[seat.planner\n")
    said = form("seat=planner")
    assert said["refused"]["code"] == "bad-config" and said["harness"] == "claude"
    status, _, _ = get("/api/form", "harness=gemini")
    assert status in (400, 422)


def test_no_presets_file_gives_the_shipped_defaults():
    said = form()
    assert said["harness"] == "claude" and said["refused"] is None
    assert {r["source"] for r in said["rows"]} == {"shipped"}


def form_modes(query: str = "") -> dict[str, str | None]:
    said = form(query)
    assert [m["target"] for m in said["modes"]] == ["terminal", "headless", "slot"]
    return {m["target"]: m["mode"] for m in said["modes"]}


def test_the_form_names_the_command_a_launch_composes_in_each_target():
    assert form()["modes"] == [{"target": "terminal", "mode": "interactive"},
                               {"target": "headless", "mode": "print"},
                               {"target": "slot", "mode": "interactive"}]
    assert form_modes("harness=codex") == {"terminal": "interactive", "headless": "exec",
                                           "slot": "interactive"}


def test_the_forms_modes_follow_turns_host_for_codex(monkeypatch):
    installed(monkeypatch, "turns")
    monkeypatch.setattr(presets, "_host_installed", lambda: True)
    assert form_modes("harness=codex") == {"terminal": "host", "headless": "exec",
                                           "slot": "host"}
    assert form_modes("harness=claude")["terminal"] == "interactive"


def test_the_forms_modes_are_null_where_a_launch_under_the_identity_is_refused(monkeypatch):
    installed(monkeypatch, "comms", "turns")
    monkeypatch.setattr(presets, "_host_installed", lambda: True)
    monkeypatch.setattr(presets, "comms_codex", lambda: True)
    assert form_modes("identity=planner") == {"terminal": "interactive", "headless": None,
                                              "slot": "interactive"}
    assert form_modes("harness=codex&identity=planner") == {
        "terminal": "host", "headless": None, "slot": "host"}
    # Without its wrapper's host a Codex launch under a name is refused everywhere.
    monkeypatch.setattr(presets, "_host_installed", lambda: False)
    assert form_modes("harness=codex&identity=planner") == {
        "terminal": None, "headless": None, "slot": None}
    assert form_modes("harness=codex")["terminal"] == "interactive"


def test_an_identity_changes_no_mode_without_comms():
    assert form_modes("identity=planner") == form_modes()
    assert form_modes("harness=codex&identity=planner") == form_modes("harness=codex")


def test_the_forms_modes_are_for_the_harness_it_is_drawn_for():
    write_presets(FORM_FILE)
    assert form_modes("seat=coder")["headless"] == "exec"
    # A form that is refused still says its harness's modes.
    said = form("seat=coder&harness=claude")
    assert said["rows"] == [] and said["modes"][1] == {"target": "headless", "mode": "print"}
    # Every mode named is one some row of that harness composes into.
    for harness in catalog.HARNESSES:
        known = {mode for row in catalog.load(harness) for mode in row.modes}
        assert set(form_modes(f"harness={harness}").values()) <= known


def test_a_failed_form_read_says_only_the_class(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("/home/someone/private/presets.toml")

    monkeypatch.setattr(presets, "layered", broken)
    status, said, raw = get("/api/form", "seat=planner")
    assert status == 200 and said["state"] == "error" and said["rows"] == []
    assert said["detail"] == pagehandlers.UNEXPECTED.format("RuntimeError")
    assert b"someone" not in raw


# --- /api/launches ------------------------------------------------------------------------

def seed(conn, request_id: str, at: str, **more) -> None:
    store.insert(conn, request_id=request_id, actor="ann", harness="claude", target="slot",
                 cwd="/work", values={"model": "opus"}, argv=["claude", "the brief text"],
                 env={"MOXI_LAUNCHER_REQUEST": "secret-stamp"}, posture="operator",
                 session_id=SID if request_id == "r1" else str(uuid.uuid4()), slot="3",
                 brief="the brief text", base_env={"HOME": "/home/ann-secret"}, at=at,
                 **more)


@pytest.fixture
def conn():
    connection = store.connect()
    seed(connection, "r1", "2026-01-01T00:00:00.000000Z")
    seed(connection, "r2", "2026-01-02T00:00:00.000000Z", preset_kind="seat",
         preset_name="planner")
    seed(connection, "r3", "2026-01-03T00:00:00.000000Z")
    yield connection
    connection.close()


def test_launches_come_newest_first_cut_to_what_the_page_shows(conn):
    status, said, raw = get("/api/launches")
    assert status == 200 and said["state"] == "up" and said["refused"] is None
    assert [row["request_id"] for row in said["launches"]] == ["r3", "r2", "r1"]
    first = said["launches"][1]
    assert set(first) == set(page.LAUNCH.items)
    assert (first["preset_kind"], first["preset_name"], first["cwd"]) == ("seat", "planner",
                                                                          "/work")
    assert first["state"] == "spawning" and first["stale"] is True
    for secret in (b"the brief text", b"secret-stamp", b"ann-secret"):
        assert secret not in raw


def test_launches_page_on_by_limit_and_before(conn):
    _, said, _ = get("/api/launches", "limit=1")
    assert [row["request_id"] for row in said["launches"]] == ["r3"]
    before = said["launches"][0]["created_at"]
    _, said, _ = get("/api/launches", f"limit=5&before={before}")
    assert [row["request_id"] for row in said["launches"]] == ["r2", "r1"]


def test_a_request_id_answers_its_row_its_refusal_or_nothing_yet(conn):
    _, said, _ = get("/api/launches", "request=r2")
    assert [row["request_id"] for row in said["launches"]] == ["r2"]
    assert said["refused"] is None
    _, said, _ = get("/api/launches", "request=r9")
    assert (said["launches"], said["refused"]) == ([], None)
    store.add_event(conn, store.LAUNCH_REFUSED,
                    {"code": "occupied", "row": "slot", "problem": "slot 3 is taken",
                     "hint": "pick another slot"}, request_id="r9")
    _, said, _ = get("/api/launches", "request=r9")
    assert said["launches"] == []
    assert said["refused"] == {"code": "occupied", "row": "slot",
                               "problem": "slot 3 is taken", "hint": "pick another slot"}


def test_a_failed_launch_asked_by_its_id_carries_the_sentence_recorded_for_it(conn):
    _, said, _ = get("/api/launches", "request=r2")
    assert said["why"] is None
    store.set_state(conn, "r2", "failed", reason="occupied")
    _, said, _ = get("/api/launches", "request=r2")
    assert (said["launches"][0]["reason"], said["why"]) == ("occupied", None)
    store.add_event(conn, "start-failed", {"reason": "occupied", "why": "an older try"},
                    request_id="r2")
    store.add_event(conn, "start-failed",
                    {"reason": "occupied", "why": "slots refused the slot: slot 3 is taken"},
                    request_id="r2")
    _, said, _ = get("/api/launches", "request=r2")
    assert said["why"] == "slots refused the slot: slot 3 is taken"
    # The log's page names no one launch, and carries no sentence.
    assert get("/api/launches")[1]["why"] is None


@pytest.mark.parametrize("query,words", [
    ("request=r1&limit=2", "request is asked alone, without limit"),
    ("before=yesterday", "before is a time"),
    ("limit=0", "limit is 1 or more"),
])
def test_a_query_the_launch_log_cant_take_is_refused(conn, query, words):
    status, said, _ = get("/api/launches", query)
    assert status == 422 and said["refused"].startswith(words)


def test_no_launch_log_reads_empty_and_makes_none():
    status, said, _ = get("/api/launches")
    assert (status, said) == (200, {"state": "up", "detail": None, "launches": [],
                                    "refused": None, "why": None})
    assert not store.path().exists()


def test_a_failed_launch_log_read_says_only_the_class(conn, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("/home/someone/private/launcher.db is locked")

    monkeypatch.setattr(store, "log", broken)
    status, said, raw = get("/api/launches")
    assert status == 200 and said["state"] == "error"
    assert said["detail"] == pagehandlers.UNEXPECTED.format("RuntimeError")
    assert b"someone" not in raw


# --- /api/runs ----------------------------------------------------------------------------

def me() -> str:
    ident = proc.identity(os.getpid())
    return f"proc:{ident.pid}@{ident.started:.6f}"


def ran(conn, request_id: str = "r1", outcome: str = "nudged"):
    run = store.insert_run(conn, kind="keep-warm", mode="ask", request_id=request_id,
                           session_id=SID, actor="launcher", state="acting", job=me(),
                           handoff_path="/notes/handoff.md")
    store.add_run_event(conn, run.run_id, "note",
                        {"verdict": "taken", "why": "", "text": "the whole notice"})
    store.add_run_event(conn, run.run_id, "wait", {"word": "person", "detail": "typing"})
    store.add_run_event(conn, run.run_id, "pane-ended", {"pane_id": "%7", "status": 0})
    store.add_run_event(conn, run.run_id, "answer", {"outcome": "at-dialog", "why": "w",
                                                     "capture": "/state/screens/r1.txt"})
    store.end_run(conn, run.run_id, outcome)
    return run.run_id


def test_runs_come_with_their_events_detail_as_text(conn):
    run_id = ran(conn)
    status, said, raw = get("/api/runs")
    assert status == 200 and said["state"] == "up"
    [run] = said["runs"]
    assert set(run) == set(page.RUN.items)
    assert (run["run_id"], run["outcome"], run["handoff_path"]) == (run_id, "nudged",
                                                                   "/notes/handoff.md")
    assert [(e["kind"], json.loads(e["detail"])) for e in run["events"]] == [
        ("note", {"verdict": "taken", "why": ""}),
        ("wait", {"word": "person", "detail": "typing"}),
        ("pane-ended", {"status": 0}),
        ("answer", {"outcome": "at-dialog", "why": "w"})]
    assert b"the whole notice" not in raw and b"proc:" not in raw
    assert b"%7" not in raw and b"screens" not in raw


def test_runs_by_run_by_launch_and_paged(conn):
    first = ran(conn, "r1")
    second = ran(conn, "r2")
    _, said, _ = get("/api/runs", f"run={first}")
    assert [r["run_id"] for r in said["runs"]] == [first]
    _, said, _ = get("/api/runs", "run=nope")
    assert said["runs"] == []
    _, said, _ = get("/api/runs", "request=r2")
    assert [r["run_id"] for r in said["runs"]] == [second]
    _, said, _ = get("/api/runs", "limit=1")
    assert [r["run_id"] for r in said["runs"]] == [second]
    status, said, _ = get("/api/runs", f"run={first}&request=r1")
    assert status == 422 and said["refused"] == "run is asked alone, without request"


def test_no_launch_log_has_no_runs():
    assert get("/api/runs")[1] == {"state": "up", "detail": None, "runs": []}


# --- /api/places --------------------------------------------------------------------------

def launched(connection, request_id: str, at: str, **more) -> None:
    """A launch the page server's own actor made, started in a terminal."""
    args = {"request_id": request_id, "actor": first.actor(), "harness": "codex",
            "target": "terminal", "cwd": "/work", "values": {}, "argv": ["codex"],
            "env": {}, "posture": "operator", "at": at}
    store.insert(connection, **{**args, **more})


@pytest.fixture
def log():
    connection = store.connect()
    yield connection
    connection.close()


def test_places_are_the_actors_own_newest_first_with_the_actor_named(log):
    launched(log, "a", "2026-01-01T09:00:00.000000Z", cwd="/work/one")
    launched(log, "b", "2026-01-01T10:00:00.000000Z", cwd="/src/proj-a/docs",
             project_key="proj-a")
    launched(log, "c", "2026-01-01T11:00:00.000000Z", cwd="/work/one")
    launched(log, "d", "2026-01-01T12:00:00.000000Z", cwd="/work/theirs", actor="alice")
    status, said, _ = get("/api/places")
    assert (status, said) == (200, {
        "state": "up", "detail": None, "projects": False, "actor": first.actor(),
        "home": os.path.expanduser("~"),
        "places": [
            {"project_key": None, "cwd": "/work/one", "at": "2026-01-01T11:00:00.000000Z"},
            {"project_key": "proj-a", "cwd": "/src/proj-a/docs",
             "at": "2026-01-01T10:00:00.000000Z"}]})
    assert said["actor"] != "alice"


def test_places_leave_out_failed_relaunched_resumed_and_scratch_launches(log):
    launched(log, "kept", "2026-01-01T09:00:00.000000Z", cwd="/work/kept")
    launched(log, "failed", "2026-01-01T10:00:00.000000Z", cwd="/work/failed")
    store.set_state(log, "failed", "failed", reason="exited")
    launched(log, "again", "2026-01-01T11:00:00.000000Z", cwd="/work/again",
             relaunch_of="kept")
    launched(log, "resumed", "2026-01-01T12:00:00.000000Z", cwd="/work/resumed",
             resume_of="kept", session_id="thread-1")
    launched(log, "scratch", "2026-01-01T13:00:00.000000Z", cwd="/tmp/scratch-1",
             values={"scratch": True})
    assert [p["cwd"] for p in get("/api/places")[1]["places"]] == ["/work/kept"]


def test_places_stop_at_five(log):
    for number in range(7):
        launched(log, f"r{number}", f"2026-01-01T1{number}:00:00.000000Z",
                 cwd=f"/work/{number}")
    assert [p["cwd"] for p in get("/api/places")[1]["places"]] == [
        f"/work/{number}" for number in (6, 5, 4, 3, 2)]


def test_places_say_whether_a_launch_may_name_a_project(monkeypatch):
    assert get("/api/places")[1]["projects"] is False
    monkeypatch.setattr(pagehandlers, "_available", lambda: lambda name: name == "projects")
    assert get("/api/places")[1]["projects"] is True


def test_a_launch_made_under_a_comms_name_is_that_names_place(log, monkeypatch):
    launched(log, "mine", "2026-01-01T09:00:00.000000Z", cwd="/work/mine")
    launched(log, "theirs", "2026-01-01T10:00:00.000000Z", cwd="/work/theirs",
             actor="planner")
    monkeypatch.setenv(first.COMMS_NAME, "planner")
    said = get("/api/places")[1]
    assert said["actor"] == "planner"
    assert [p["cwd"] for p in said["places"]] == ["/work/theirs"]


def test_no_launch_log_reads_no_place_and_makes_none():
    status, said, _ = get("/api/places")
    assert (status, said) == (200, {"state": "up", "detail": None, "projects": False,
                                    "actor": first.actor(),
                                    "home": os.path.expanduser("~"), "places": []})
    assert not store.path().exists()


def test_a_failed_places_read_says_only_the_class(log, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("/home/someone/private/launcher.db")

    monkeypatch.setattr(store, "places", broken)
    status, said, raw = get("/api/places")
    assert status == 200 and said == {
        "state": "error", "detail": pagehandlers.UNEXPECTED.format("RuntimeError"),
        "projects": False, "actor": first.actor(), "home": os.path.expanduser("~"),
        "places": []}
    assert b"someone" not in raw


def test_places_name_the_home_folder_the_page_server_sees(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    assert get("/api/places")[1]["home"] == str(tmp_path / "elsewhere")


# --- /api/targets -------------------------------------------------------------------------

TERMINAL = {"state": "up", "detail": None, "slots": [], "new_slot": False, "unread": False,
            "default": {"target": "terminal", "slot": None}}


@pytest.fixture
def fake_slots(tmp_path, monkeypatch):
    """The fake slots behind the launcher's seam, read as installed; `fake_slots(...)`
    scripts it, and `fake_slots.calls(name)` lists the calls it got."""
    import launcher_fake_slots as fake

    directory = tmp_path / "fake-slots"
    directory.mkdir()
    monkeypatch.setenv(slots.SEAM, "launcher_fake_slots")
    monkeypatch.setenv(fake.DIR, str(directory))
    installed(monkeypatch, "slots")
    fake.reset()

    def plan(**keys):
        (directory / "script.json").write_text(json.dumps(keys))

    plan.calls = lambda name=None: fake.calls(directory, name)
    plan.fake = fake
    return plan


def targets_answer() -> dict:
    status, said, _ = get("/api/targets")
    assert status == 200 and said["state"] == "up" and said["detail"] is None
    return said


def test_targets_list_each_slot_in_number_order_with_its_state_and_hold(fake_slots):
    fake_slots(slots={
        "4": {"state": "missing", "launchable": False},
        "3": {"state": "free", "launchable": False,
              "hold": {"holder": "planner", "until": 1900000000.5}},
        "2": {"state": "ended", "launchable": True},
        "1": {"state": "occupied", "launchable": False},
        "10": {"state": "free", "launchable": True}})
    said = targets_answer()

    def slot(number, state, launchable, holder=None, until=None):
        return {"number": number, "state": state, "launchable": launchable,
                "holder": holder, "until": until, "taken_by": None}

    assert said["slots"] == [
        slot(1, "occupied", False), slot(2, "ended", True),
        slot(3, "free", False, "planner", 1900000000.5), slot(4, "missing", False),
        slot(10, "free", True)]
    assert said["new_slot"] is True
    assert said["default"] == {"target": "slot", "slot": 2}


def test_the_default_is_a_new_slot_when_none_is_launchable_or_there_is_none(fake_slots):
    fake_slots(slots={"1": {"state": "occupied", "launchable": False},
                      "2": {"state": "free", "launchable": False,
                            "hold": {"holder": "planner", "until": 1900000000.0}}})
    said = targets_answer()
    assert said["new_slot"] is True and said["default"] == {"target": "slot", "slot": None}
    fake_slots()
    assert targets_answer() == {"state": "up", "detail": None, "slots": [], "new_slot": True,
                                "unread": False,
                                "default": {"target": "slot", "slot": None}}


def test_a_session_with_no_registry_row_is_not_listed(fake_slots):
    fake_slots(slots={"1": {"state": "free", "launchable": True},
                      "2": {"state": "occupied", "launchable": False, "created": None}})
    assert [s["number"] for s in targets_answer()["slots"]] == [1]


def test_a_taken_slot_names_the_launch_the_log_holds_for_it(fake_slots, log):
    launched(log, "named", "2026-01-01T09:00:00.000000Z", identity="planner",
             cwd="/src/proj-a")
    launched(log, "bare", "2026-01-01T10:30:00.000000Z", cwd="/src/proj-b")
    launched(log, "called", "2026-01-01T11:00:00.000000Z", identity="planner",
             cwd="/src/proj-a", values={"name": "proj-a-opus-Jan-01 11:00"})
    at = store.parse_time("2026-01-01T10:30:00.000000Z").astimezone()

    def taken(request_id, state="occupied"):
        return {"state": state, "launchable": state != "occupied",
                "last": {"at": 1.0, "label": f"launcher:{request_id}",
                         "meta": {"request_id": request_id}}}

    fake_slots(slots={"1": taken("named"), "2": taken("bare"), "3": taken("unknown"),
                      "4": {"state": "occupied", "launchable": False},
                      "5": taken("named", state="ended"), "7": taken("called"),
                      "6": {"state": "occupied", "launchable": False,
                            "last": {"at": 1.0, "label": "by hand", "meta": None}}})
    by = {s["number"]: s["taken_by"] for s in targets_answer()["slots"]}
    assert by == {1: {"request_id": "named", "label": "planner"},
                  2: {"request_id": "bare", "label": f"proj-b {at:%H:%M}"},
                  3: None, 4: None, 5: None, 6: None,
                  7: {"request_id": "called", "label": "proj-a-opus-Jan-01 11:00"}}


def test_a_slot_a_launch_started_in_reads_taken_by_that_launch(fake_slots, log, tmp_path):
    launched(log, "r1", "2026-01-01T09:00:00.000000Z", identity="coder")
    fake_slots(slots={"1": {"state": "free", "launchable": True}})
    fake_slots.fake.start(None, ["true"], env={}, cwd=str(tmp_path), label="launcher:r1",
                          meta={"request_id": "r1"}, create=True)
    said = targets_answer()
    assert said["slots"] == [{"number": 1, "state": "occupied", "launchable": False,
                              "holder": None, "until": None,
                              "taken_by": {"request_id": "r1", "label": "coder"}}]
    assert said["default"] == {"target": "slot", "slot": None}


def test_slots_that_cannot_be_read_offer_the_terminal_and_say_they_are_unread(fake_slots):
    fake_slots(list="error", slots={"1": {"state": "free", "launchable": True}})
    assert targets_answer() == {**TERMINAL, "unread": True}


def test_slots_that_cannot_run_here_offer_no_slot_and_the_terminal(fake_slots):
    fake_slots(supported={"ok": False, "why": "no tmux"},
               slots={"1": {"state": "free", "launchable": True}})
    assert targets_answer() == TERMINAL
    assert fake_slots.calls("list_slots") == []


def test_without_slots_the_default_is_the_terminal_and_slots_is_not_asked(fake_slots,
                                                                         monkeypatch):
    fake_slots(slots={"1": {"state": "free", "launchable": True}})
    monkeypatch.setattr(pagehandlers, "_available", lambda: lambda name: False)
    assert targets_answer() == TERMINAL
    assert fake_slots.calls() == []


def test_slots_installed_but_not_loadable_offers_the_terminal(monkeypatch):
    installed(monkeypatch, "slots")
    monkeypatch.setattr(slots, "api", lambda: None)
    assert targets_answer() == TERMINAL


def test_headless_is_never_the_default(fake_slots, monkeypatch):
    for plan in ({}, {"slots": {"1": {"state": "free", "launchable": True}}},
                 {"list": "error"}, {"supported": {"ok": False}}):
        fake_slots(**plan)
        assert targets_answer()["default"]["target"] in ("slot", "terminal")
    monkeypatch.setattr(pagehandlers, "_available", lambda: lambda name: False)
    assert targets_answer()["default"]["target"] == "terminal"


def test_a_failed_targets_read_says_only_the_class(fake_slots, monkeypatch):
    def broken():
        raise RuntimeError("/home/someone/private/registry.json")

    monkeypatch.setattr(fake_slots.fake, "list_slots", broken)
    status, said, raw = get("/api/targets")
    assert status == 200 and said == {
        **TERMINAL, "state": "error", "unread": True,
        "detail": pagehandlers.UNEXPECTED.format("RuntimeError")}
    assert b"someone" not in raw


def test_a_failed_targets_read_without_slots_does_not_say_slots_are_unread(monkeypatch):
    def broken(conn=None):
        raise RuntimeError("no")

    monkeypatch.setattr(pagehandlers, "_available", lambda: broken)
    status, said, _ = get("/api/targets")
    assert status == 200 and (said["state"], said["unread"]) == ("error", False)


# --- the announcement, and the launcher without the UI ---------------------------------------

def test_the_announcement_names_the_page_and_the_ui_link():
    from moxi_launcher import module

    assert module.PAGE == "moxi_launcher.page:PAGE"
    assert module.OPTIONAL["ui"].startswith("The launcher's page in the browser UI shell")
    found = modules.load("launcher")
    assert found.ok and found.page == module.PAGE


def test_the_announcement_declares_the_panel_the_home_block_and_the_state():
    from moxi_launcher import module

    assert set(module.COMPONENTS) == {"page", "prompt", "sessions", "state"}
    assert {name: decl["place"] for name, decl in module.COMPONENTS.items()} == {
        "page": "page", "prompt": "block", "sessions": "block", "state": "inline"}
    assert module.COMPONENTS["sessions"]["home"] is True
    assert "attributes" not in module.COMPONENTS["sessions"]
    assert module.COMPONENTS["state"]["attributes"] == {
        "session": {"type": "string", "required": True, "doc": "a session id"}}
    for decl in module.COMPONENTS.values():
        assert decl["contract"] == 1
        assert decl["title"].strip() and "\n" not in decl["doc"]
    found = modules.load("launcher")
    assert found.ok and set(found.components) == {"page", "prompt", "sessions", "state"}


def test_each_components_script_is_a_built_asset_that_defines_its_element():
    from moxi_launcher import module

    assets = loopback().export(page.PAGE)["assets"]
    for name, decl in module.COMPONENTS.items():
        # The shell places a component only when its script is one of the page's assets.
        assert decl["script"] in assets
        text = (WEB / decl["script"]).read_text(encoding="utf-8")
        # Each imports only the shared client, by its absolute URL, and defines its
        # element once.
        assert re.findall(r'from"([^"]+)"', text) == ["/m/ui/client.js"]
        assert re.search(r"\bimport\s*\(", text) is None
        assert f'"moxi-launcher-{name}"' in text and "customElements.get(" in text
        # Its own Lit, with Lit's licence kept at the end.
        assert "lit-html" in text and "SPDX-License-Identifier: BSD-3-Clause" in text


def test_the_components_reach_data_only_through_their_routes_and_topics():
    sessions = (WEB / "sessions.js").read_text(encoding="utf-8")
    state = (WEB / "state.js").read_text(encoding="utf-8")
    for text in (sessions, state):
        assert '"launcher","sessions"' in text
        assert "fetch(" not in text and "WebSocket" not in text and "EventSource" not in text
    assert '"api/state"' in sessions and '"api/launches"' in sessions
    assert '"launcher","launches"' in sessions
    assert "api/state?session=" in state
    # The panel reads and acts only through its own routes, follows both topics, draws the
    # launch form with the shell's one form renderer and places a state per live row.
    panel = (WEB / "page.js").read_text(encoding="utf-8")
    assert "fetch(" not in panel and "WebSocket" not in panel and "EventSource" not in panel
    for route in ("api/catalog", "api/presets", "api/launches", "api/state", "api/sessions",
                  "api/preview", "api/launch", "api/resume"):
        assert f'"{route}"' in panel, route
    assert '"launcher","launches"' in panel and '"launcher","sessions"' in panel
    assert "moxi-ui-form" in panel and 'mode="submit"' in panel
    assert '"launcher","state"' in panel
    # Red is drawn with the theme's error colour; a live session that's fine with its live
    # colour.
    for text in (sessions, state):
        assert "var(--moxi-error)" in text and "var(--moxi-live)" in text


WITHOUT_UI = textwrap.dedent("""\
    import contextlib
    import io
    import json
    import sys

    # As if the UI shell weren't installed.
    sys.modules["moxi_ui"] = None

    import moxi_launcher.api
    import moxi_launcher.cachetimers
    import moxi_launcher.cli
    import moxi_launcher.doctor
    import moxi_launcher.job
    import moxi_launcher.listeners
    import moxi_launcher.module
    from moxi_dev.contract import check

    with contextlib.redirect_stdout(io.StringIO()) as printed:
        code = moxi_launcher.cli.main(["catalog", "--json"], prog="moxi launcher")
    rows = len(json.loads(printed.getvalue()))
    loaded = sorted(name for name in sys.modules
                    if name in ("moxi_launcher.page", "moxi_launcher.pagehandlers")
                    or name.startswith("moxi_ui."))
    try:
        import moxi_launcher.page
    except ImportError as err:
        refused = str(err)
    else:
        refused = None
    findings = [str(finding) for finding in check(sys.argv[1])]
    print(json.dumps({"code": code, "rows": rows, "loaded": loaded, "refused": refused,
                      "findings": findings}))
""")


def test_the_launcher_works_without_the_ui(tmp_path):
    done = subprocess.run([sys.executable, "-P", "-c", WITHOUT_UI, str(ROOT)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["code"] == 0 and out["rows"] > 0
    assert out["loaded"] == []
    assert out["refused"].startswith("the launcher's page needs the ui module")
    assert out["findings"] == []


def test_the_page_states_its_group_and_order_among_the_pages():
    from moxi_launcher import module

    page_decl = module.COMPONENTS["page"]
    assert (page_decl["group"], page_decl["order"]) == ("daily", 10)

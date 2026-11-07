"""sessions' page server in process, through loopback's strict app.

Loopback is reached as the page reaches it, through `moxi.modules.load`: sessions'
distribution doesn't require it, so these tests don't import it either. The app runs
behind `guard` with an upstream profile, as the runner serves it, with strict projection,
so a handler that sends a key its route doesn't declare, or a value of another type, fails
here as a 500.

The routes are served from a page that holds them without the asset directory, so these
tests read the routes alone; the built assets are checked on their own, against `PAGE`.

Each harness's records are written by `moxi_sessions.testing`'s builders under roots the
test's environment names, as the page server finds them in the shell's environment.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from moxi import modules
from moxi_sessions import page, pagehandlers
from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb
from moxi_sessions.testing.claude import usage

from conftest import write_config

TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321
SESSION = "0190b000-0000-7000-8000-00000000c1a0"
THREAD = "0190c000-0000-7000-8000-0000000c0de1"
CHILD_THREAD = "0190c000-0000-7000-8000-0000000c0de9"

posix_only = pytest.mark.skipif(sys.platform == "win32",
                                reason="a directory's mode bits are POSIX's")


def loopback():
    found = modules.load("loopback", interface=1)
    assert found.ok, found.reason
    return found.api


LB = loopback()

#: The page's routes, without its asset directory.
ROUTES = LB.Page(page.PAGE.routes, budget=page.BUDGET, idle_s=page.PAGE.idle_s,
                 package="moxi_sessions")


def served():
    inner = LB.app(ROUTES, module="sessions", server_id=SERVER_ID, strict=True)
    profile = LB.upstream(LB.build_table(ROUTES)[0], token=TOKEN, port=PORT)
    return LB.guard(inner, lambda: profile)


async def _send(app, path: str, query: str) -> tuple[int, dict | None]:
    headers = [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode())]
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "server": ("127.0.0.1", PORT),
        "client": ("127.0.0.1", 50000), "scheme": "http", "method": "GET",
        "root_path": "", "path": path, "raw_path": path.encode("ascii"),
        "query_string": query.encode(), "headers": headers, "state": {},
    }
    sent = {"status": 0, "body": b""}
    asked = [False]

    async def receive():
        if not asked[0]:
            asked[0] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    await app(scope, receive, send)
    return sent["status"], json.loads(sent["body"]) if sent["body"] else None


def get(path: str, query: str = "") -> tuple[int, dict | None]:
    return asyncio.run(_send(served(), path, query))


def read(path: str, query: str = "") -> dict:
    status, body = get(path, query)
    assert status == 200, body
    return body


def refused(path: str, query: str) -> str:
    status, body = get(path, query)
    assert status == 422, body
    return body["refused"]


def window(harness: str, session: str, extra: str = "") -> dict:
    return read("/api/window", f"harness={harness}&session={session}{extra}")


def listing(harness: str, session: str) -> dict:
    return read("/api/subagents", f"harness={harness}&session={session}")


# -- the roots, as the page server's environment names them ----------------------------------

@pytest.fixture
def roots(tmp_path, monkeypatch) -> dict[str, Path]:
    """Each harness's root under the test's tmp, named by the environment the page server
    reads: Claude Code's projects root, Codex's sessions root, OpenCode's data root."""
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    data = tmp_path / "data"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    return {"claude": claude_home / "projects", "codex": codex_home / "sessions",
            "opencode": data / "opencode"}


def claude_turns(root: Path, turns: int = 3) -> ClaudeTranscript:
    t = ClaudeTranscript(root)
    for n in range(turns):
        t.user(f"turn {n}")
        req = t.request(usage_block=usage(input=3, cache_read=900, output=40))
        req.thinking("which file?")
        call = req.tool_use("Read", {"file_path": "/work/project/notes.md"})
        t.tool_result(call, "the notes, which the page never sends")
        req.text(f"done {n}")
        t.turn_duration()
    return t


def codex_turns(root: Path, turns: int = 3) -> CodexRollout:
    r = CodexRollout(root)
    for n in range(turns):
        r.turn()
        r.user(f"turn {n}")
        call = r.tool_call("exec", "cat notes.md")
        r.tool_output(call, "the notes, which the page never sends")
        r.request()
        r.assistant(f"done {n}")
        r.turn_end()
    return r


def opencode_turns(root: Path, turns: int = 3) -> tuple[OpenCodeDb, object]:
    db = OpenCodeDb(root)
    s = db.session(title="fix the tests")
    for n in range(turns):
        s.user(f"turn {n}")
        a = s.assistant()
        a.step_start()
        a.tool("bash", {"command": "pytest -q"}, output="the output, never sent")
        a.text(f"done {n}")
        a.step_finish(input=900, output=40, cache_read=1200)
        a.complete()
    return db, s


def _kinds(body: dict) -> list[str]:
    return [row["kind"] for row in body["records"]]


def _texts(body: dict) -> list[str]:
    return [row["text"] for row in body["records"]
            if row["kind"] == "message" and not row["reasoning"]]


# -- the table ------------------------------------------------------------------------------

def test_the_gate_matrix_passes():
    assert LB.gate_matrix(ROUTES)


def test_the_page_declares_two_reads_and_nothing_the_gateways():
    routes = {r["path"]: r for r in LB.export(ROUTES)["routes"]}
    assert set(routes) == {"/api/window", "/api/subagents"}
    assert all(r["kind"] == "read" for r in routes.values())
    assert not any(r["gateway"] or r["stream"] or r["confirmed"] for r in routes.values())
    assert set(routes["/api/window"]["query"]) == {"harness", "session", "agent", "turns",
                                                  "before"}
    assert set(routes["/api/subagents"]["query"]) == {"harness", "session"}
    assert routes["/api/window"]["query"]["harness"]["values"] == ["claude", "codex",
                                                                   "opencode"]
    assert routes["/api/window"]["query"]["before"]["type"] == "string"


def _marks(shape: dict, at: str = "") -> dict[str, str]:
    """Each marked value of an exported shape, by its dotted place (`[]` a list's item)."""
    found = {at: shape["mark"]} if shape.get("mark") else {}
    items = shape.get("items")
    if shape["type"] == "list":
        found.update(_marks(items, at + "[]"))
    elif shape["type"] == "table":
        for key, sub in items.items():
            found.update(_marks(sub, f"{at}.{key}"))
    return found


def test_every_path_is_marked_path_and_all_content_redact():
    marks: dict[str, str] = {}
    for route in LB.export(ROUTES)["routes"]:
        for key, shape in route["returns"].items():
            marks.update({f"{route['path']} {k}": v for k, v in _marks(shape, key).items()})
    assert {k for k, v in marks.items() if v == "path"} == {
        "/api/window path", "/api/window header.cwd", "/api/subagents entries[].path",
        "/api/subagents unreadable_parts[].where"}
    assert {k for k, v in marks.items() if v == "redact"} == {
        "/api/window header.title", "/api/window records[].text",
        "/api/window records[].summary", "/api/window records[].title",
        "/api/subagents reason", "/api/subagents entries[].description",
        "/api/subagents unreadable_parts[].reason"}


def test_no_shape_has_room_for_a_tool_input_or_output_or_a_raw_record():
    record = page.RECORD.items
    for key in ("input", "output", "extra", "raw", "fields", "content_kinds"):
        assert key not in record
    assert "unknown" in record and "truncated" in record


def test_the_announcement_offers_the_page_and_imports_none_of_it():
    from moxi_sessions import module

    assert module.PAGE == "moxi_sessions.page:PAGE"
    assert "ui" in module.OPTIONAL
    assert module.INTERFACES == frozenset({2, 3})
    probe = ("import sys, moxi_sessions.module, moxi_sessions.cli, moxi_sessions.api\n"
             "print(sorted(m for m in sys.modules if m.startswith(('moxi_loopback', "
             "'moxi_ui', 'moxi_sessions.page'))))")
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          timeout=60, check=True)
    assert done.stdout.strip() == "[]"


def test_the_announcement_declares_the_page_and_the_window_block():
    from moxi_sessions import module

    assert set(module.COMPONENTS) == {"page", "window"}
    for name, decl in module.COMPONENTS.items():
        assert decl["script"] == f"{name}.js"
        assert decl["contract"] == 1
        assert (decl["place"] == "page") == (name == "page")
    block = module.COMPONENTS["window"]
    assert block["place"] == "block" and not block.get("home", False)
    attrs = block["attributes"]
    assert set(attrs) == {"harness", "session", "agent"}
    assert attrs["harness"] == {"type": "enum", "values": ["claude", "codex", "opencode"],
                                "required": True, "doc": attrs["harness"]["doc"]}
    assert attrs["session"]["type"] == "string" and attrs["session"]["required"] is True
    assert attrs["agent"]["type"] == "string" and not attrs["agent"].get("required", False)
    assert "attributes" not in module.COMPONENTS["page"]


def test_the_export_carries_the_measured_budget():
    assert LB.export(ROUTES)["budget"] == {"rss": "96M", "fds": 32} == page.BUDGET
    assert page.PAGE.budget == ROUTES.budget


def test_each_component_is_a_built_asset_of_the_page():
    from moxi_sessions import module

    exported = LB.export(page.PAGE)
    assert {"page.js", "window.js"} <= set(exported["assets"])
    for decl in module.COMPONENTS.values():
        assert decl["script"] in exported["assets"]
    assert {r["path"] for r in exported["routes"]} == {"/api/window", "/api/subagents"}
    assert LB.gate_matrix(page.PAGE)


# -- the window, for each harness -----------------------------------------------------------

def test_a_claude_window_sends_each_record_as_its_view_row(roots):
    t = claude_turns(roots["claude"], 1)
    t.title("fix the tests")
    t.write()
    body = window("claude", SESSION)
    assert body["shape"] == 1 and body["harness"] == "claude"
    assert body["session"] == SESSION and body["agent"] is None
    assert body["path"] == str(t.path)
    assert body["header"]["cwd"] == "/work/project"
    assert body["header"]["version"] == "2.1.314"
    assert body["header"]["title"] == "fix the tests"
    assert body["turns"] == 5 and body["has_earlier"] is False and body["before"] is None
    rows = body["records"]
    assert _texts(body) == ["turn 0", "done 0"]
    thought = next(r for r in rows if r["kind"] == "message" and r["reasoning"])
    assert thought["text"] == "which file?" and thought["role"] == "assistant"
    call = next(r for r in rows if r["kind"] == "tool_call")
    assert call["name"] == "Read" and "notes.md" in call["summary"]
    result = next(r for r in rows if r["kind"] == "tool_result")
    assert result["call_id"] == call["call_id"]
    assert result["size_bytes"] == len(b"the notes, which the page never sends")
    request = next(r for r in rows if r["kind"] == "request")
    assert request["model"] == "claude-model-x" and request["output_tokens"] == 40
    assert request["input_tokens"] == 903
    assert any(r["kind"] == "marker" and r["marker"] == "turn_end"
               and r["duration_ms"] == 1200 for r in rows)
    assert body["last_activity"] == max(r["time"] for r in rows if "time" in r)
    assert all({"kind", "raw_kind", "at", "unknown"} <= set(r) for r in rows)
    assert all(set(r["at"]) == {"line", "offset"} for r in rows)
    assert "never sends" not in json.dumps(body)


def test_a_field_the_harness_did_not_say_is_left_out_and_named(roots):
    claude_turns(roots["claude"], 1).write()
    request = next(r for r in window("claude", SESSION)["records"] if r["kind"] == "request")
    # Claude Code writes no cost of its own.
    assert "cost_usd" in request["unknown"] and "cost_usd" not in request


def test_a_value_of_a_type_the_row_does_not_carry_is_named_unknown(roots):
    t = ClaudeTranscript(roots["claude"])
    t.user("hi")
    t.assistant("there", model=5, requested_model=None)
    t.write()
    rows = window("claude", SESSION)["records"]
    request = next(r for r in rows if r["kind"] == "request")
    assert "model" not in request and "model" in request["unknown"]


def test_a_codex_window_sends_its_turns_requests_and_limits(roots):
    r = codex_turns(roots["codex"], 1)
    r.write()
    body = window("codex", THREAD)
    assert body["path"] == str(r.path) and body["header"]["cwd"] == "/work/project"
    assert body["header"]["version"] == "0.153.0"
    assert _texts(body)[-2:] == ["turn 0", "done 0"]
    kinds = _kinds(body)
    assert {"message", "tool_call", "tool_result", "request", "limits", "marker"} <= set(kinds)
    limits = next(row for row in body["records"] if row["kind"] == "limits")
    assert limits["limit_id"] == "codex"
    assert [w["name"] for w in limits["windows"]] == ["primary", "secondary"]
    assert limits["windows"][0]["used_percent"] == 5.0
    assert "never sends" not in json.dumps(body)


def test_an_opencode_window_reads_the_session_row(roots):
    db, s = opencode_turns(roots["opencode"], 1)
    db.write()
    body = window("opencode", s.id)
    assert body["header"]["title"] == "fix the tests"
    assert body["header"]["cwd"] == "/work/project"
    assert body["path"] == str(db.path) and body["ended"] == "start"
    assert _texts(body) == ["turn 0", "done 0"]
    assert all("table" in r["at"] and "id" in r["at"] for r in body["records"])
    request = next(row for row in body["records"] if row["kind"] == "request")
    assert request["output_tokens"] == 40
    assert "never sent" not in json.dumps(body)


# -- paging back ----------------------------------------------------------------------------

@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_before_pages_back_through_every_turn_once(harness, roots):
    if harness == "claude":
        claude_turns(roots["claude"], 3).write()
        session = SESSION
    elif harness == "codex":
        codex_turns(roots["codex"], 3).write()
        session = THREAD
    else:
        db, s = opencode_turns(roots["opencode"], 3)
        db.write()
        session = s.id
    first = window(harness, session, "&turns=2")
    assert first["turns"] == 2 and first["has_earlier"] is True
    assert isinstance(first["before"], str)
    second = window(harness, session, f"&turns=2&before={first['before']}")
    assert second["has_earlier"] is False and second["before"] is None
    whole = window(harness, session, "&turns=50")
    assert second["records"] + first["records"] == whole["records"]
    assert [t for t in _texts(second) + _texts(first)] == [
        "turn 0", "done 0", "turn 1", "done 1", "turn 2", "done 2"]


@pytest.mark.parametrize("harness, before", [
    ("claude", "abc"), ("claude", "-1"), ("claude", "1.5"), ("codex", "msg_x"),
    ("claude", ""),
])
def test_a_before_that_is_not_a_byte_offset_is_refused(harness, before, roots):
    claude_turns(roots["claude"], 1).write()
    codex_turns(roots["codex"], 1).write()
    session = SESSION if harness == "claude" else THREAD
    said = refused("/api/window", f"harness={harness}&session={session}&before={before}")
    assert "byte offset" in said


def test_a_before_naming_no_opencode_message_is_refused(roots):
    db, s = opencode_turns(roots["opencode"], 1)
    db.write()
    said = refused("/api/window", f"harness=opencode&session={s.id}&before=msg_nothing")
    assert "names no message" in said
    assert "empty" in refused("/api/window", f"harness=opencode&session={s.id}&before=")


def test_turns_below_one_is_refused_and_over_its_max_is_a_400(roots):
    claude_turns(roots["claude"], 1).write()
    assert "1 or more" in refused("/api/window", f"harness=claude&session={SESSION}&turns=0")
    status, _ = get("/api/window", f"harness=claude&session={SESSION}&turns=51")
    assert status == 400


@pytest.mark.parametrize("query", [
    "harness=gemini&session=x",
    f"session={SESSION}",
    "harness=claude",
    f"harness=claude&session={'a' * 257}",
    f"harness=claude&session={SESSION}&path=/etc",
])
def test_the_routes_own_checks_are_a_400(query, roots):
    assert get("/api/window", query)[0] == 400


# -- the text cap ---------------------------------------------------------------------------

def test_a_long_message_is_cut_at_the_cap_on_a_character_edge(roots):
    t = ClaudeTranscript(roots["claude"])
    long = "é" * 40000                                      # 80,000 bytes in UTF-8
    t.user(long)
    t.assistant("short")
    t.write()
    rows = window("claude", SESSION)["records"]
    user = next(r for r in rows if r["kind"] == "message" and r["role"] == "user")
    assert user["truncated"] is True and user["text_bytes"] == 80000
    assert user["text"] == "é" * (pagehandlers.TEXT_MAX // 2)
    short = next(r for r in rows if r["kind"] == "message" and r["role"] == "assistant")
    assert short["truncated"] is False and short["text_bytes"] == 5


# -- finding fails --------------------------------------------------------------------------

def test_a_session_no_root_holds_is_refused_with_each_probe(roots):
    roots["claude"].mkdir(parents=True)
    said = refused("/api/window", f"harness=claude&session={SESSION}")
    assert f"no claude session {SESSION} was found" in said and "miss" in said
    said = refused("/api/subagents", f"harness=codex&session={THREAD}")
    assert f"no codex session {THREAD}" in said


def test_an_id_the_search_does_not_look_up_is_refused(roots):
    roots["claude"].mkdir(parents=True)
    said = refused("/api/window", "harness=claude&session=..%2Fetc")
    assert "not a session id the search looks up" in said


def test_a_missing_opencode_database_is_refused_with_its_reason(roots):
    said = refused("/api/window", "harness=opencode&session=ses_nothing")
    assert "unreadable" in said


def test_a_compressed_rollout_alone_is_refused_with_why(roots):
    r = codex_turns(roots["codex"], 1)
    r.path = r.path.with_name(r.path.name + ".zst")
    r.write()
    said = refused("/api/window", f"harness=codex&session={THREAD}")
    assert "zstd" in said


def test_an_unreadable_config_is_refused(roots, own_config):
    write_config(own_config, "this is = = not toml\n")
    said = refused("/api/window", f"harness=claude&session={SESSION}")
    assert "config cannot be read" in said


# -- subagents ------------------------------------------------------------------------------

def test_a_claude_subagent_is_listed_and_opened_under_its_session(roots):
    t = claude_turns(roots["claude"], 1)
    sub = t.subagent("a0190b0000000001", description="Look into the failing test",
                     tool_use_id="toolu_9")
    sub.user("look into it")
    sub.assistant("found it")
    t.write()
    body = listing("claude", SESSION)
    assert body["shape"] == 1 and body["state"] == "listed" and body["reason"] is None
    [entry] = body["entries"]
    assert entry["agent_id"] == "a0190b0000000001"
    assert entry["session"] == SESSION and entry["agent"] == "a0190b0000000001"
    assert entry["route"] == f"claude/{SESSION}/a0190b0000000001"
    assert entry["description"] == "Look into the failing test"
    assert entry["agent_type"] == "general-purpose" and entry["tool_use_id"] == "toolu_9"
    assert entry["path"] == str(sub.path) and entry["size"] == sub.path.stat().st_size
    opened = window("claude", SESSION, "&agent=a0190b0000000001")
    assert opened["agent"] == "a0190b0000000001" and opened["path"] == str(sub.path)
    assert _texts(opened) == ["look into it", "found it"]
    assert all(r["agent_id"] == "a0190b0000000001" for r in opened["records"])


def test_a_subagent_the_session_does_not_hold_is_refused(roots):
    claude_turns(roots["claude"], 1).write()
    said = refused("/api/window", f"harness=claude&session={SESSION}&agent=a99")
    assert f"session {SESSION} has no subagent a99" in said


@pytest.mark.parametrize("harness", ["codex", "opencode"])
def test_an_agent_is_refused_where_a_subagent_is_a_session_of_its_own(harness, roots):
    said = refused("/api/window", f"harness={harness}&session=x&agent=y")
    assert "a session of its own" in said


def test_a_session_with_no_subagents_lists_none(roots):
    claude_turns(roots["claude"], 1).write()
    body = listing("claude", SESSION)
    assert body["state"] == "none" and body["entries"] == [] and body["reason"] is None


@posix_only
def test_a_subagent_directory_that_cannot_be_listed_is_unreadable_with_why(roots):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root reads a directory whatever its mode")
    t = claude_turns(roots["claude"], 1)
    t.subagent().assistant("x")
    t.write()
    directory = t.path.with_suffix("") / "subagents"
    directory.chmod(0)
    try:
        body = listing("claude", SESSION)
    finally:
        directory.chmod(0o755)
    assert body["state"] == "unreadable" and "cannot be listed" in body["reason"]


def test_an_opencode_child_opens_as_a_session_of_its_own(roots):
    db, s = opencode_turns(roots["opencode"], 1)
    child = s.child(title="look into it")
    child.user("look")
    a = child.assistant()
    a.step(text="looked", input=5, output=3)
    a.complete()
    db.write()
    [entry] = listing("opencode", s.id)["entries"]
    assert entry["session"] == child.id and entry["agent"] is None
    assert entry["route"] == f"opencode/{child.id}"
    opened = window("opencode", child.id)
    assert opened["header"]["parent"] == s.id
    assert _texts(opened) == ["look", "looked"]


def test_a_codex_subagent_thread_opens_as_a_session_of_its_own(roots):
    r = codex_turns(roots["codex"], 1)
    child = r.spawn(CHILD_THREAD, "explore")
    child.turn()
    child.user("explore")
    child.assistant("explored")
    r.write()
    child.write()
    body = listing("codex", THREAD)
    assert body["state"] == "listed"
    [entry] = body["entries"]
    assert entry["session"] == CHILD_THREAD and entry["agent"] is None
    assert entry["route"] == f"codex/{CHILD_THREAD}"
    assert entry["description"] == "/root/explore"
    opened = window("codex", CHILD_THREAD)
    assert opened["header"]["parent"] == THREAD
    assert _texts(opened)[-2:] == ["explore", "explored"]


def test_a_codex_child_named_but_not_found_is_a_part_that_could_not_be_read(roots):
    r = codex_turns(roots["codex"], 1)
    r.spawn(CHILD_THREAD, "explore")
    r.write()
    body = listing("codex", THREAD)
    assert body["state"] == "unreadable" and CHILD_THREAD in body["reason"]
    assert body["unreadable_parts"][0]["where"] == CHILD_THREAD


# -- reads write nothing --------------------------------------------------------------------

def _tree(root: Path) -> dict[str, tuple[int, bytes]]:
    """Every file under `root` with its mtime and bytes, but SQLite's own `-wal` and
    `-shm` beside a database, which its connections make and remove."""
    return {str(p): (p.stat().st_mtime_ns, p.read_bytes())
            for p in sorted(root.rglob("*"))
            if p.is_file() and not p.name.endswith(("-wal", "-shm"))}


def test_a_read_writes_nothing(roots, tmp_path):
    t = claude_turns(roots["claude"], 2)
    t.subagent().assistant("x")
    t.write()
    codex_turns(roots["codex"], 2).write()
    db, s = opencode_turns(roots["opencode"], 2)
    db.write()
    before = _tree(tmp_path)
    for harness, session in (("claude", SESSION), ("codex", THREAD), ("opencode", s.id)):
        window(harness, session)
        window(harness, session, "&turns=1")
        listing(harness, session)
    window("claude", SESSION, "&agent=a0190b0000000001")
    assert _tree(tmp_path) == before

"""statusline's page server in process, through loopback's strict app.

Loopback is reached as the page reaches it, through `moxi.modules.load`: statusline's
distribution doesn't require it, so its tests don't import it either. The app runs behind
`guard` with an upstream profile, as the runner serves it, with strict projection, so a
handler that sends a key its route doesn't declare fails here. The preview runs the real
render, in a child process, over the sample input.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import threading

import pytest
from statusline_kit import config_file, write_config

from moxi import config, modules, paths
from moxi_dev.testing import PROC_WAIT_S
from moxi_statusline import compile as segments
from moxi_statusline import page, pagehandlers

TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321
EMPTY = hashlib.sha256(b"").hexdigest()


def loopback():
    found = modules.load("ui", interface=3)
    assert found.ok, found.reason
    return found.api.loopback


LB = loopback()


@pytest.fixture(autouse=True)
def fresh_server(monkeypatch):
    """Each test is a page server's first: its sweep has not run."""
    monkeypatch.setattr(pagehandlers, "_swept", False)


def served():
    inner = LB.app(page.PAGE, module="statusline", server_id=SERVER_ID, strict=True)
    profile = LB.upstream(LB.build_table(page.PAGE)[0], token=TOKEN, port=PORT)
    return LB.guard(inner, lambda: profile)


async def _send(app, method: str, path: str, body: dict | None = None):
    headers = [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode())]
    raw = b""
    if method == "POST":
        raw = json.dumps(body or {}).encode()
        headers += [(b"content-type", b"application/json"),
                    (b"content-length", str(len(raw)).encode())]
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "server": ("127.0.0.1", PORT),
        "client": ("127.0.0.1", 50000), "scheme": "http", "method": method,
        "root_path": "", "path": path, "raw_path": path.encode("ascii"),
        "query_string": b"", "headers": headers, "state": {},
    }
    sent = {"status": 0, "body": b""}
    asked = [False]

    async def receive():
        if not asked[0]:
            asked[0] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    await app(scope, receive, send)
    return sent["status"], json.loads(sent["body"]) if sent["body"] else None


def get(path: str):
    return asyncio.run(_send(served(), "GET", path))


def post(path: str, body: dict | None = None):
    return asyncio.run(_send(served(), "POST", path, body))


def state() -> dict:
    status, body = get("/api/state")
    assert status == 200, body
    return body


def stamp() -> str:
    path = config_file()
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()


def snapshot() -> bytes | None:
    path = config_file()
    return path.read_bytes() if path.exists() else None


def previews() -> list[str]:
    return sorted(p.name for p in pagehandlers.runtime_dir().glob("preview-*.toml"))


def plain(lines: list[str]) -> list[str]:
    import re

    return [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines]


def settings_file():
    return paths.claude_home() / "settings.json"


def write_settings(data: dict) -> None:
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


# --- the table --------------------------------------------------------------------------

def test_the_gate_matrix_passes():
    assert LB.gate_matrix(page.PAGE)


def test_the_page_declares_its_routes_none_for_the_gateway():
    exported = LB.export(page.PAGE)
    routes = {route["path"]: route for route in exported["routes"]}
    assert list(routes) == ["/api/state", "/api/preview", "/api/save", "/api/use",
                            "/api/restore"]
    assert routes["/api/state"]["kind"] == "read"
    assert all(routes[p]["kind"] == "act" for p in list(routes)[1:])
    assert not any(route["gateway"] for route in routes.values())
    assert exported["assets"] == ["build-stamp.txt", "page.js"]


def test_the_announcement_names_the_page_and_its_component():
    found = modules.announced("statusline")
    assert found.page == "moxi_statusline.page:PAGE"
    assert found.components["page"]["title"] == "Status line"
    assert found.components["page"]["place"] == "page"


# --- /api/state -------------------------------------------------------------------------

def test_state_with_no_config_file_is_the_defaults_over_an_empty_stamp():
    body = state()
    assert body["base"] == EMPTY
    assert body["problem"] is None
    assert body["values"] == {
        "show": ["dir", "branch", "model", "effort", "context", "usage", "cache"],
        "separator": " │ ", "color": True, "dir": {"parts": 2},
        "context": {"warn_at": 50, "alert_at": 80}}
    assert body["fields"] == ["dir", "branch", "model", "effort", "context", "session",
                              "transcript", "lines", "cost"]
    assert [m["name"] for m in body["modules"]] == segments.present_segments()
    assert all(m["summary"] for m in body["modules"])
    assert not config_file().exists()


def test_state_gives_the_values_and_the_stamp_of_one_read():
    write_config("""
        # a comment kept
        show = ["model", "row", "dir"]
        separator = " / "
        [context]
        warn_at = 60
    """)
    body = state()
    assert body["base"] == stamp()
    assert body["values"]["show"] == ["model", "row", "dir"]
    assert body["values"]["separator"] == " / "
    assert body["values"]["context"] == {"warn_at": 60, "alert_at": 80}


def test_a_config_that_cant_be_read_is_said_with_the_defaults():
    write_config('show = ["row"]\n')
    body = state()
    assert body["problem"].startswith("The saved config can't be read: show has 'row' first")
    assert body["values"]["show"][0] == "dir"
    assert body["base"] == stamp()


def test_state_lists_no_module_without_a_segment(seen):
    seen.module("alpha")
    assert "alpha" not in [m["name"] for m in state()["modules"]]


def test_state_says_whose_line_claude_runs():
    claude = state()["claude"]
    assert claude == {"set": False, "command": None, "moxis": False, "here": False,
                      "saved": False, "problem": None}
    write_settings({"statusLine": {"type": "command", "command": "my-line.sh"}})
    claude = state()["claude"]
    assert claude["set"] and claude["command"] == "my-line.sh" and not claude["moxis"]


# --- /api/preview -----------------------------------------------------------------------

def draft(changes: dict, base: str | None = None) -> dict:
    return {"base": stamp() if base is None else base, "changes": changes}


def test_preview_renders_the_draft_and_writes_no_config():
    write_config('show = ["dir"]\n')
    before = snapshot()
    status, body = post("/api/preview", draft({"show": ["model", "effort"],
                                                "separator": " / ", "color": False}))
    assert status == 200, body
    assert body["lines"] == ["Sample / high"]
    assert body["ms"] > 0 and body["problem"] is None
    assert snapshot() == before
    assert previews() == []


def test_preview_of_no_change_renders_the_saved_config():
    write_config('show = ["effort", "row", "model"]\ncolor = false\n')
    status, body = post("/api/preview", draft({}))
    assert status == 200, body
    assert body["lines"] == ["high", "Sample"]


def test_preview_names_an_item_with_nothing_for_the_sample():
    shown = ["model", "branch"]
    if "cache" in segments.present_segments():
        shown.append("cache")
    status, body = post("/api/preview", draft({"show": shown, "color": False}))
    assert status == 200, body
    assert plain(body["lines"]) == ["Sample"]
    assert "branch: shows inside a git repository" in body["notes"]
    if "cache" in shown:
        assert "cache: shows in a live session" in body["notes"]


@pytest.mark.parametrize("changes,key", [
    ({"show": ["dir", "row", "model", "row", "cost"]}, "show"),
    ({"show": ["row", "dir"]}, "show"),
    ({"context": {"warn_at": 90}}, "context.warn_at"),
    ({"dir": {"parts": 0}}, "dir.parts"),
])
def test_a_refused_draft_is_a_422_naming_its_key_on_preview_and_save(changes, key):
    write_config('separator = " / "\n')
    before = snapshot()
    for route in ("/api/preview", "/api/save"):
        status, body = post(route, draft(changes))
        assert status == 422, (route, body)
        assert body["refused"].startswith(f"{key} "), body
    assert snapshot() == before
    assert previews() == []


def test_the_two_bands_raised_together_are_previewed_and_saved():
    changes = {"context": {"warn_at": 90, "alert_at": 95}}
    status, body = post("/api/preview", draft(changes))
    assert status == 200, body
    assert not config_file().exists()
    status, body = post("/api/save", draft(changes))
    assert status == 200, body
    assert body["values"]["context"] == {"warn_at": 90, "alert_at": 95}


def test_the_preview_renders_what_save_then_writes(tmp_path):
    write_config('# kept\nseparator = " / "\n')
    changes = {"show": ["effort", "model"], "dir": {"parts": 3}}
    text, _ = config.draft_text("statusline", pagehandlers.changes({"changes": changes}))
    status, _ = post("/api/save", draft(changes))
    assert status == 200
    assert config_file().read_text() == text


def test_save_writes_the_draft_once_and_answers_the_new_state():
    status, body = post("/api/save", draft({"show": ["model", "row", "dir"],
                                             "color": False}))
    assert status == 200, body
    assert body["values"]["show"] == ["model", "row", "dir"]
    assert body["values"]["color"] is False
    assert body["base"] == stamp() != EMPTY
    lines = config.history("statusline")
    assert [(r["key"], r["by"]) for r in lines] == [("show", "ui"), ("color", "ui")]


def test_a_stale_base_is_a_409_on_preview_and_save_and_writes_nothing():
    old = stamp()
    write_config('separator = " / "\n')
    before = snapshot()
    for route in ("/api/preview", "/api/save"):
        status, body = post(route, draft({"color": False}, base=old))
        assert status == 409, (route, body)
        assert "changed" in body["conflict"]
    assert snapshot() == before
    assert previews() == []


def test_a_held_lock_is_a_409_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(config, "LOCK_TIMEOUT", 0.2)
    write_config('separator = " / "\n')
    before = snapshot()
    path = config_file()
    # Another process holds the file's lock, as another writer would.
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD, str(path.with_name(path.name + ".lock"))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert holder.stdout.readline() == b"held\n"
        status, body = post("/api/save", draft({"color": False}))
    finally:
        holder.stdin.close()
        holder.wait(PROC_WAIT_S)
    assert status == 409, body
    assert "something else" in body["conflict"]
    assert snapshot() == before


HOLD = """
import sys
from moxi import files
with files.lock(sys.argv[1], timeout_s=5):
    print("held", flush=True)
    sys.stdin.read()
"""


def test_how_each_refusal_is_answered():
    changed = config.ConfigChanged(None, None, "has changed since it was read", "read it again")
    assert isinstance(pagehandlers.refusal(changed), LB.Conflict)
    held = config.ConfigError(None, None, "is locked", "try again")
    held.exit_code = 75
    assert isinstance(pagehandlers.refusal(held), LB.Conflict)
    named = pagehandlers.refusal(config.ConfigError(None, "dir.parts", "is 0", "set it to 1"))
    assert isinstance(named, LB.Refused)
    assert named.text == "dir.parts is 0. Fix: set it to 1."
    whole = pagehandlers.refusal(config.ConfigError(None, None, "breaks a rule", "fix it"))
    assert whole.text == "The draft as a whole was refused: breaks a rule. Fix: fix it."


def test_two_previews_in_flight_each_render_their_own_draft():
    base = stamp()

    async def both():
        app = served()
        return await asyncio.gather(
            _send(app, "POST", "/api/preview",
                  {"base": base, "changes": {"show": ["model"], "color": False}}),
            _send(app, "POST", "/api/preview",
                  {"base": base, "changes": {"show": ["effort"], "color": False}}))

    (s1, b1), (s2, b2) = asyncio.run(both())
    assert (s1, s2) == (200, 200)
    assert b1["lines"] == ["Sample"]
    assert b2["lines"] == ["high"]
    assert previews() == []


def test_a_render_stopped_at_the_limit_leaves_no_file(monkeypatch):
    monkeypatch.setattr(pagehandlers, "TIMEOUT", 0.001)
    status, body = post("/api/preview", draft({"show": ["model"]}))
    assert status == 200, body
    assert body["lines"] == [] and body["ms"] is None
    assert "did not finish" in body["problem"]
    assert previews() == []


def test_the_first_request_sweeps_left_preview_files_and_import_does_not():
    runtime_dir = pagehandlers.runtime_dir()
    left = runtime_dir / "preview-abc123.toml"
    other = runtime_dir / "page.json"
    left.write_text("show = []\n")
    other.write_text("{}")
    import importlib

    importlib.reload(pagehandlers)
    assert left.exists()
    pagehandlers._swept = False
    state()
    assert not left.exists()
    assert other.exists()
    # Only the first request sweeps.
    left.write_text("show = []\n")
    state()
    assert left.exists()


def test_the_sweep_runs_once_across_threads(monkeypatch):
    calls = []
    real = pagehandlers.sweep
    monkeypatch.setattr(pagehandlers, "sweep", lambda: calls.append(1) or real())
    (pagehandlers.runtime_dir() / "preview-x.toml").write_text("")
    threads = [threading.Thread(target=pagehandlers.state, args=(None,)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == [1]
    assert previews() == []


# --- /api/use and /api/restore ------------------------------------------------------------

def test_use_with_no_line_switches_to_moxis():
    status, body = post("/api/use")
    assert status == 200, body
    assert body["claude"]["moxis"] and body["claude"]["here"]
    assert body["said"]
    assert json.loads(settings_file().read_text())["statusLine"]["command"].endswith(
        "moxi-statusline-render")
    # use writes no config.
    assert not config_file().exists()


def test_use_over_another_line_keeps_it_to_put_back_and_restore_does():
    write_settings({"statusLine": {"type": "command", "command": "my-line.sh"}})
    status, body = post("/api/use")
    assert status == 200, body
    assert body["claude"]["moxis"] and body["claude"]["saved"]
    status, body = post("/api/restore")
    assert status == 200, body
    assert body["claude"]["command"] == "my-line.sh" and not body["claude"]["moxis"]


def test_restore_over_a_line_that_isnt_moxis_is_a_409():
    write_settings({"statusLine": {"type": "command", "command": "my-line.sh"}})
    post("/api/use")
    write_settings({"statusLine": {"type": "command", "command": "another.sh"}})
    status, body = post("/api/restore")
    assert status == 409, body
    assert "no longer Moxi's" in body["conflict"]
    assert json.loads(settings_file().read_text())["statusLine"]["command"] == "another.sh"

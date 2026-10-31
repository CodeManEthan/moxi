"""Confirmed acts through the shell, with no browser: the modules view names them, the
shell passes `Idempotency-Key` to the page server and no other browser header past its
allowlist, and the module's answers come back as it sent them.

The test shell serves `ui` and the `fixture` module (tests/conftest.py)."""

from __future__ import annotations

import http.client
import importlib.util
import json
import secrets

import pytest

from moxi_ui.page import MODULES_SHAPE
from moxi_ui.router import FORWARDED_HEADERS, forwarded
from moxi_ui.snapshot import confirmed_paths, modules_value

pytestmark = pytest.mark.timeout(60)

#: What may reach a page server: the allowlist, and the Host and token the shell sets.
REACHES = {name.decode() for name in FORWARDED_HEADERS} | {"host", "x-moxi-token"}


def _post(shell, session, path: str, body: bytes, extra: dict[str, str]) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", shell.port, timeout=10)
    try:
        conn.request("POST", path, body=body, headers={**session.headers(act=True), **extra})
        answer = conn.getresponse()
        raw = answer.read()
    finally:
        conn.close()
    return answer.status, (json.loads(raw) if raw else None)


def _lines(shell, name: str) -> list[str]:
    try:
        return (shell.state_dir("fixture") / name).read_text().splitlines()
    except FileNotFoundError:
        return []


def test_idempotency_key_is_forwarded_and_nothing_else_is_added():
    assert b"idempotency-key" in FORWARDED_HEADERS
    assert FORWARDED_HEADERS == {b"accept", b"accept-language", b"content-type",
                                 b"content-length", b"if-none-match", b"last-event-id",
                                 b"idempotency-key"}
    key = secrets.token_urlsafe(32)
    sent = [(b"Cookie", b"c=1"), (b"Idempotency-Key", key.encode()), (b"Origin", b"o"),
            (b"Accept", b"*/*")]
    assert forwarded(sent) == [(b"Idempotency-Key", key.encode()), (b"Accept", b"*/*")]


def test_the_modules_value_lists_each_modules_confirmed_acts():
    export = {"routes": [
        {"path": "/api/stop", "kind": "act", "confirmed": True, "status": "/api/status"},
        {"path": "/api/add", "kind": "act", "confirmed": False, "status": None},
        {"path": "/api/status", "kind": "read", "confirmed": False, "status": None},
        {"path": "/api/b", "kind": "act", "confirmed": True, "status": "/api/status"},
        # An older export has neither field; a read is never a confirmed act.
        {"path": "/api/old", "kind": "act"},
        {"path": "/api/odd", "kind": "read", "confirmed": True},
    ]}
    assert confirmed_paths(export) == ["/api/b", "/api/stop"]
    assert confirmed_paths(None) == [] and confirmed_paths({"routes": "x"}) == []
    value = modules_value([{"name": "usage", "state": "present", "page": "p.q:R"},
                           {"name": "plain", "state": "present"}],
                          {"usage": export}, {}, at="now")
    rows = {row["name"]: row for row in value["modules"]}
    assert rows["usage"]["confirmed"] == ["/api/b", "/api/stop"]
    assert rows["plain"]["confirmed"] == []
    assert set(rows["usage"]) == set(MODULES_SHAPE["modules"].items.items)


def test_the_shell_lists_the_fixtures_confirmed_act(ui_shell):
    status, view = ui_shell.session().get("/m/ui/api/modules")
    rows = {row["name"]: row for row in view["modules"]}
    assert status == 200 and rows["fixture"]["confirmed"] == ["/api/confirm"]
    assert rows["ui"]["confirmed"] == []


def test_the_page_server_gets_the_key_and_no_browser_header_past_the_allowlist(ui_shell):
    session = ui_shell.session()
    key = secrets.token_urlsafe(32)
    before = len(_lines(ui_shell, "confirm-headers"))
    status, data = _post(ui_shell, session, "/m/fixture/api/confirm", b'{"by":1}', {
        "Idempotency-Key": key, "User-Agent": "Test/1", "Referer": ui_shell.url + "/m/fixture/",
        "Accept": "application/json", "Accept-Encoding": "gzip"})
    assert status == 202, data
    assert data["status"].startswith("/api/job?job=")
    assert _lines(ui_shell, "keys")[-1] == key
    names, = [json.loads(line) for line in _lines(ui_shell, "confirm-headers")[before:]]
    assert "idempotency-key" in names
    assert set(names) <= REACHES, set(names) - REACHES
    # The status link the act gave is a read of the same page, through the shell.
    status, job = session.get("/m/fixture" + data["status"])
    assert status == 200 and job["state"] == "done"


def test_the_modules_answers_to_a_confirmed_act_pass_through_the_shell(ui_shell):
    session = ui_shell.session()

    def post(body: bytes, key: str | None = None) -> tuple[int, dict]:
        extra = {} if key is None else {"Idempotency-Key": key}
        return _post(ui_shell, session, "/m/fixture/api/confirm", body, extra)

    assert post(b"{}") == (400, {"error": "missing header: Idempotency-Key"})
    assert post(b"{}", "short") == (400, {"error": "malformed header: Idempotency-Key"})
    assert post(b'{"mode":"refuse"}', secrets.token_urlsafe(32)) == (
        422, {"refused": "the fixture refuses this act"})
    assert post(b'{"mode":"conflict"}', secrets.token_urlsafe(32)) == (
        409, {"conflict": "the fixture is busy with another act"})
    assert post(b'{"mode":"fail"}', secrets.token_urlsafe(32)) == (500, None)
    assert post(json.dumps({"pad": "x" * 70_000}).encode(), secrets.token_urlsafe(32)) == (
        413, None)
    # The same key again, after it finished: the first answer, and no second run.
    key = secrets.token_urlsafe(32)
    first = post(b'{"by":2}', key)
    runs = len(_lines(ui_shell, "keys"))
    assert first[0] == 202 and post(b'{"by":2}', key) == first
    assert len(_lines(ui_shell, "keys")) == runs
    assert post(b'{"by":3}', key) == (
        422, {"refused": "this Idempotency-Key was used for another request"})


def test_tunnel_is_not_installed_here():
    """Everything in this file and the browser's confirm tests runs with no tunnel module:
    the desk's confirmed acts need none."""
    assert importlib.util.find_spec("moxi_tunnel") is None

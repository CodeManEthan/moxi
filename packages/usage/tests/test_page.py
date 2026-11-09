"""usage's page server in process, through loopback's strict app, and usage without the UI.

Loopback is reached as the page reaches it, through `moxi.modules.load`: usage's
distribution doesn't require it, so its tests don't import it either. The app runs behind
`guard` with an upstream profile, as the runner serves it, with strict projection, so a
handler that sends a key its route doesn't declare, or a value of another type, fails
here. No test starts Codex: the pull through turns is a fake.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest

import moxi.store
from moxi import modules
from moxi_sessions import api as sessions
from moxi_usage import index, limits, models, page, pagehandlers, readings, reports, store
from moxi_usage.readings import Reading

from conftest import doc, write_config
from test_reports import (at, one_session_index, request, requests_index, source,
                          table)

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321
ClaudeTranscript = sessions.testing.ClaudeTranscript
usage = sessions.testing.usage


def loopback():
    found = modules.load("ui", interface=3)
    assert found.ok, found.reason
    return found.api.loopback


@pytest.fixture(autouse=True)
def fresh_handlers(monkeypatch):
    """Each test logs each failure class anew and starts its own feed."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(pagehandlers, "_current", None)


@pytest.fixture(autouse=True)
def turns(monkeypatch):
    """turns installed, with no pull to make: a stale Codex reading is never pulled."""
    monkeypatch.setattr(limits, "_turns_available", lambda: True)
    monkeypatch.setattr(limits, "_puller", lambda: (None, "the test makes no pull"))


@pytest.fixture
def seeded(monkeypatch):
    """The reports' one-session index (two Claude sessions, an OpenCode parent with its
    child), priced by the reports' test table, last passed at a known time."""
    monkeypatch.setattr(models, "table", lambda *a, **k: table())
    conn = store.open_index()
    try:
        one_session_index(conn)
        with moxi.store.transaction(conn):
            store.set_meta(conn, store.META_LAST_PASS, str(LAST_PASS))
    finally:
        conn.close()


LAST_PASS = at("2026-10-04T08:00")


# --- driving the app ----------------------------------------------------------------------

def served():
    lb = loopback()
    inner = lb.app(page.PAGE, module="usage", server_id=SERVER_ID, strict=True)
    profile = lb.upstream(lb.build_table(page.PAGE)[0], token=TOKEN, port=PORT)
    return lb.guard(inner, lambda: profile)


def scope(path: str, query: bytes = b"", method: str = "GET") -> dict:
    headers = [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode())]
    if method == "POST":
        headers.append((b"content-type", b"application/json"))
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "server": ("127.0.0.1", PORT),
        "client": ("127.0.0.1", 50000), "scheme": "http", "method": method,
        "root_path": "", "path": path, "raw_path": path.encode("ascii"),
        "query_string": query, "headers": headers, "state": {},
    }


async def ask(app, path: str, query: dict | None = None, *,
              method: str = "GET") -> tuple[int, dict | None]:
    sent = {"status": 0, "body": b""}
    asked = [False]
    body = b"{}" if method == "POST" else b""

    async def receive():
        if not asked[0]:
            asked[0] = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    raw = urlencode(query or {}).encode("ascii")
    await app(scope(path, raw, method), receive, send)
    return sent["status"], json.loads(sent["body"]) if sent["body"] else None


def get(path: str, **query) -> dict:
    status, body = asyncio.run(ask(served(), path, query))
    assert status == 200, body
    return body


def refused(path: str, **query) -> str:
    status, body = asyncio.run(ask(served(), path, query))
    assert status == 422, body
    return body["refused"]


class Stream:
    """One open stream on the app: its events as `(name, data)`, heartbeats left out."""

    def __init__(self, app, topics: str):
        self.status = 0
        self._gone = asyncio.Event()
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._buffer = b""
        first = [True]

        async def receive():
            if first[0]:
                first[0] = False
                return {"type": "http.request", "body": b"", "more_body": False}
            await self._gone.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                self.status = message["status"]
            elif message["type"] == "http.response.body":
                await self._chunks.put(message.get("body", b""))

        query = f"topics={topics}".encode("ascii")
        self.task = asyncio.get_running_loop().create_task(
            app(scope("/api/events", query), receive, send))

    async def next(self, timeout: float) -> tuple[str, dict]:
        async with asyncio.timeout(timeout):
            while True:
                while b"\n\n" not in self._buffer:
                    self._buffer += await self._chunks.get()
                block, _, self._buffer = self._buffer.partition(b"\n\n")
                if block.startswith(b":"):
                    continue
                fields = dict(line.split(": ", 1) for line in block.decode().split("\n"))
                return fields["event"], json.loads(fields["data"])

    async def none_within(self, seconds: float) -> bool:
        try:
            await self.next(seconds)
        except TimeoutError:
            return True
        return False

    async def close(self) -> None:
        self._gone.set()
        async with asyncio.timeout(3):
            await self.task


async def until(check, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.01)


# --- the table ------------------------------------------------------------------------------

def test_the_gate_matrix_passes():
    assert loopback().gate_matrix(page.PAGE)


FILTERS = {"since", "until", "harness", "project", "model", "utc"}


def test_each_route_declares_exactly_the_keys_it_reads_and_none_is_the_gateways():
    exported = loopback().export(page.PAGE)
    routes = {route["path"]: route for route in exported["routes"]}
    assert {path: (r["kind"], set(r["query"]), r["stream"]) for path, r in routes.items()} == {
        "/api/limits": ("read", set(), False),
        "/api/daily": ("read", {"by", *FILTERS}, False),
        "/api/sessions": ("read", {"top", *FILTERS}, False),
        "/api/session": ("read", {"session", *FILTERS}, False),
        "/api/projects": ("read", FILTERS, False),
        "/api/models": ("read", FILTERS, False),
        "/api/terms": ("read", {"by", "session", *FILTERS}, False),
        "/api/requests": ("read", {"session", "agent", *FILTERS}, False),
        "/api/prices": ("read", {"models", "providers", "q"}, False),
        "/api/status": ("read", set(), False),
        "/api/cost": ("read", set(), False),
        "/api/pass": ("act", set(), False),
        "/api/events": ("read", set(), True),
    }
    assert not any(route["gateway"] or route["confirmed"] for route in routes.values())
    assert routes["/api/session"]["query"]["session"]["required"]
    assert routes["/api/requests"]["query"]["session"]["required"]
    assert not routes["/api/terms"]["query"]["session"]["required"]
    assert list(routes["/api/events"]["topics"]) == ["limits", "index"]
    assert routes["/api/pass"]["body"] == {}
    assert page.HARNESSES == reports.HARNESSES


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


def test_every_path_carries_the_path_mark_and_every_title_the_redact_mark():
    exported = loopback().export(page.PAGE)
    marks: dict[str, dict[str, str]] = {}
    for route in exported["routes"]:
        shapes = route["returns"] or route["topics"]
        marks[route["path"]] = {}
        for key, shape in shapes.items():
            marks[route["path"]].update(_marks(shape, key))

    def marked(path, mark):
        return {at for at, m in marks[path].items() if m == mark}

    assert marked("/api/sessions", "path") == {"rows[].cwd"}
    assert marked("/api/projects", "path") == {"rows[].cwd"}
    assert marked("/api/status", "path") == {
        "lanes[].root", "lanes[].unreadable[].path", "lanes[].imports_path",
        "total.held_rows[].path", "total.duplicate_rows[].path",
        "total.duplicate_rows[].of_path"}
    for path in ("/api/limits", "/api/daily", "/api/session", "/api/models", "/api/cost",
                 "/api/terms", "/api/requests", "/api/prices", "/api/pass", "/api/events"):
        assert marked(path, "path") == set(), path
    assert "rows[].title" in marked("/api/sessions", "redact")
    assert "rows[].title" in marked("/api/session", "redact")


def test_the_page_serves_its_three_components_and_the_build_stamp():
    assert loopback().export(page.PAGE)["assets"] == ["build-stamp.txt", "cost.js",
                                                      "limits.js", "page.js"]


def test_the_announcement_offers_the_page_its_blocks_and_the_ui_extra():
    from moxi_usage import module

    assert module.PAGE == "moxi_usage.page:PAGE"
    assert module.OPTIONAL["ui"] == ("the usage page, with its limits and cost blocks, in "
                                     "the browser UI shell")
    assert {name: (c["place"], c.get("home", False), c["script"])
            for name, c in module.COMPONENTS.items()} == {
        "page": ("page", False, "page.js"), "limits": ("block", True, "limits.js"),
        "cost": ("block", True, "cost.js")}
    found = modules.load("usage", interface=1)
    assert found.ok and found.page == module.PAGE
    assert set(found.components) == {"page", "limits", "cost"}


#: A `moxi usage` command as the page's sources and the README name one: the verb (or
#: verbs, as `daily|monthly`), and `update` after `prices`.
NAMED = re.compile(r"moxi\s+usage\s+([a-z][a-z|-]*)(?:\s+(update)\b)?")


def test_every_command_the_page_or_readme_names_is_a_verb_of_the_cli(capsys):
    from moxi_usage import cli

    sources = [*sorted((ROOT / "web" / "src").glob("*.ts")), ROOT / "README.md",
               *(ROOT / "src" / "moxi_usage" / name
                 for name in ("page.py", "pagehandlers.py", "reports.py"))]
    named = {(verb, update) for path in sources
             for first, update in NAMED.findall(path.read_text(encoding="utf-8"))
             for verb in first.split("|")}
    assert ("index", "") in named and ("prices", "update") in named
    for verb, update in sorted(named):
        argv = [verb, *([update] if update else []), "--help"]
        with pytest.raises(SystemExit) as done:
            cli.main(argv)
        assert done.value.code == 0, (argv, capsys.readouterr().err)
    capsys.readouterr()


def test_the_export_command_reads_the_page(tmp_path):
    done = subprocess.run([sys.executable, "-P", "-m", "moxi_ui.loopback.export", "usage"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)["usage"]
    assert out["ok"], out
    assert [r["path"] for r in out["export"]["routes"]] == [
        r.path for r in page.PAGE.routes]


# --- the reports ----------------------------------------------------------------------------

SUM_KEYS = set(page.SUMS)
ROW_KEYS = set(page.ROW_SUMS)


def test_daily_sends_each_day_and_model_with_its_sums_and_flags(seeded):
    body = get("/api/daily", utc="true")
    assert body["by"] == "day" and body["tz"] == "UTC"
    assert (body["since"], body["until"], body["filters"]) == (None, None, [])
    assert body["index"] == {"passed": False, "last_pass": LAST_PASS,
                             "note": "no pass ran first; this report reads the index as "
                                     "it is"}
    days = sorted({row["period"] for row in body["rows"]})
    assert days == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert set(body["rows"][0]) == {"period", "model", *ROW_KEYS}
    assert set(body["total"]) == SUM_KEYS
    unpriced = [row for row in body["rows"] if row["model"] == "mystery-9"]
    assert [(r["cost"], r["unknown"], r["complete"]) for r in unpriced] == [
        (None, ["cost"], False)]
    assert unpriced[0]["unpriced_models"] == ["mystery-9"]
    unknown = [row for row in body["rows"] if row["unknown_rows"]]
    assert unknown and not any(row["complete"] for row in unknown)
    assert body["unpriced_models"] == ["mystery-9"]
    assert body["total"]["requests"] == sum(row["requests"] for row in body["rows"])
    direct = reports.report("daily", tz="UTC", fresh=False).total
    assert body["total"]["cost"] == pytest.approx(direct["cost"])


def test_monthly_sends_months_and_filters_come_as_a_list(seeded):
    body = get("/api/daily", by="month", utc="true", harness="claude", model="m-a")
    assert body["by"] == "month"
    assert {row["period"] for row in body["rows"]} == {"2026-10"}
    assert {row["model"] for row in body["rows"]} == {"m-a"}
    assert body["filters"] == [{"name": "harness", "value": "claude"},
                               {"name": "model", "value": "m-a"}]


def test_a_range_is_two_days_included_and_an_empty_day_leaves_its_end_open(seeded):
    body = get("/api/daily", since="2026-10-02", until="2026-10-02", utc="true")
    assert {row["period"] for row in body["rows"]} == {"2026-10-02"}
    assert body["since"] == at("2026-10-02T00:00")
    assert body["until"] == at("2026-10-03T00:00")
    assert get("/api/daily", since="", utc="true")["since"] is None


def test_days_are_the_servers_local_days_without_utc(seeded):
    body = get("/api/daily")
    assert body["tz"] == "local"
    expected = reports.report("daily", fresh=False).as_dict()
    assert [row["period"] for row in body["rows"]] == [row["day"] for row in expected["rows"]]


def test_a_bad_argument_is_refused_with_the_reports_words(seeded):
    assert "is not a date" in refused("/api/daily", since="yesterday")
    assert "before --since" in refused("/api/sessions", since="2026-10-03",
                                       until="2026-10-01")
    assert "1 or more" in refused("/api/sessions", top="0")
    status, body = asyncio.run(ask(served(), "/api/sessions", {"top": "1001"}))
    assert status == 400


def test_sessions_send_the_top_by_cost_and_how_many_more(seeded):
    body = get("/api/sessions", utc="true")
    assert (body["top"], body["more"]) == (200, 0)
    costs = [row["cost"] for row in body["rows"]]
    assert costs == sorted(costs, reverse=True) and len(costs) == 3
    first = body["rows"][0]
    assert set(first) == {"harness", "session_id", "title", "cwd", "project", "models",
                          "first_ts", "last_ts", *ROW_KEYS}
    s1 = next(row for row in body["rows"] if row["session_id"] == "s1")
    assert (s1["title"], s1["cwd"], s1["harness"]) == ("Tidy the parser", "/w/app", "claude")
    assert s1["models"] == ["m-a", "m-b", "mystery-9"]
    two = get("/api/sessions", top="2", utc="true")
    assert (len(two["rows"]), two["top"], two["more"]) == (2, 2, 1)
    assert two["rows"] == body["rows"][:2]


def test_a_session_sums_to_its_sessions_row_over_the_same_filters(seeded):
    for filters in ({}, {"since": "2026-10-02", "until": "2026-10-02"}, {"model": "m-a"}):
        rows = get("/api/sessions", utc="true", **filters)["rows"]
        for harness, sid in (("claude", "s1"), ("claude", "s2"), ("opencode", "ses_p")):
            one = get("/api/session", session=sid, harness=harness, utc="true", **filters)
            found = [row for row in rows if row["session_id"] == sid]
            if not found:
                assert one["rows"] == [] and one["total"]["requests"] == 0
                continue
            for name in SUM_KEYS - {"cost", "unknown", "harness_cost"}:
                assert one["total"][name] == found[0][name], (sid, filters, name)
            assert one["total"]["cost"] == pytest.approx(found[0]["cost"])


def test_a_session_is_split_by_agent_and_model(seeded):
    body = get("/api/session", session="s1", utc="true")
    assert [(r["agent"], r["kind"], r["model"]) for r in body["rows"]] == [
        ("main", "main", "m-a"), ("main", "main", "m-b"), ("a", "subagent", "m-a"),
        ("a", "subagent", "mystery-9"), ("b", "workflow", "m-b")]
    assert set(body["rows"][0]) == {"agent", "kind", "title", "model", "first_ts",
                                    "last_ts", *ROW_KEYS}
    assert body["rows"][0]["title"] == "Tidy the parser" and body["rows"][4]["title"] is None
    assert body["filters"] == [{"name": "session", "value": "s1"}]


def test_projects_send_a_key_or_a_cwd(seeded):
    body = get("/api/projects", utc="true")
    assert {(row["project"], row["cwd"]) for row in body["rows"]} == {
        (None, "/w/app"), (None, "/w/oc")}
    assert set(body["rows"][0]) == {"project", "cwd", "models", *ROW_KEYS}


def test_models_send_the_price_provenance_without_its_source(seeded):
    body = get("/api/models", utc="true")
    by_model = {row["model"]: row for row in body["rows"]}
    assert set(by_model) == {"m-a", "m-b", "mystery-9"}
    [found] = by_model["m-a"]["provenance"]
    assert set(found) == set(page.PROVENANCE.items)
    assert (found["rule"], found["period"]) == ("exact", None)
    direct = reports.report("models", fresh=False).as_dict()
    assert "source" in direct["rows"][0]["provenance"][0]


def test_cost_sends_today_and_the_six_days_before_it(seeded, monkeypatch):
    monkeypatch.setattr(pagehandlers, "_today", lambda: dt.date(2026, 10, 3))
    body = get("/api/cost")
    assert body["today"] == "2026-10-03" and body["tz"] == "local"
    local = dt.datetime(2026, 9, 27).astimezone().timestamp()
    assert body["since"] == local and body["until"] is None
    expected = reports.report("daily", since="2026-09-27", fresh=False)
    assert body["total"]["requests"] == expected.total["requests"]
    monkeypatch.setattr(pagehandlers, "_today", lambda: dt.date(2026, 10, 20))
    assert get("/api/cost")["rows"] == []


def test_status_cuts_the_lane_facts_and_held_reasons_to_declared_lists(seeded):
    lanes = {"claude": {"harness": "claude", "rank": 0, "root": "/h/claude", "state": "online",
                        "reason": None, "listed": 4, "held": 0, "compressed": 0,
                        "checked_at": LAST_PASS, "unreadable": [["/h/claude/x", "denied"]]},
             "codex": {"harness": "codex", "rank": 1, "root": "/h/codex", "state": "offline",
                       "reason": "root not found", "listed": 0, "held": 0,
                       "compressed": 0, "checked_at": LAST_PASS, "unreadable": [],
                       "imports_path": "/h/codex/imports.json",
                       "imports_reason": "imports file not found", "imported": 0,
                       "originator_only": 0, "a_new_fact": {"open": 1}}}
    conn = store.connect()
    try:
        with moxi.store.transaction(conn):
            store.set_meta(conn, store.META_LANES, json.dumps(lanes))
    finally:
        conn.close()
    conn = store.connect()
    try:
        source(conn, "/p/held.jsonl", [request("h1", at("2026-10-01T09:00"))],
               session_id="held", held="the source is gone")
    finally:
        conn.close()
    body = get("/api/status")
    by_lane = {lane["lane"]: lane for lane in body["lanes"]}
    assert set(by_lane) == {"claude", "codex", "opencode"}
    claude, codex = by_lane["claude"], by_lane["codex"]
    assert set(claude) == set(page.LANE.items)
    assert claude["unreadable"] == [{"path": "/h/claude/x", "reason": "denied"}]
    assert claude["held_reasons"] == [{"name": "the source is gone", "value": 1}]
    assert (claude["held"], claude["files"]) == (1, 5)
    assert (codex["state"], codex["reason"], codex["imports_path"]) == (
        "offline", "root not found", "/h/codex/imports.json")
    assert by_lane["opencode"]["state"] is None and by_lane["opencode"]["root"] is None
    total = body["total"]
    assert total["last_pass"] == LAST_PASS and total["index_version"] == store.INDEX_VERSION
    assert total["held_rows"] == [{"lane": "claude", "path": "/p/held.jsonl", "part": "",
                                   "reason": "the source is gone"}]
    assert body["index"]["last_pass"] == LAST_PASS


# --- the cost terms, the requests and the rates ----------------------------------------------

def test_terms_by_model_send_each_group_with_its_terms_and_agree_with_models(seeded):
    body = get("/api/terms", utc="true")
    assert body["by"] == "model"
    models_ = {row["model"]: row for row in get("/api/models", utc="true")["rows"]}
    assert {row["model"] for row in body["rows"]} == set(models_)
    for row in body["rows"]:
        assert set(row) == set(page.TERMS_ROW)
        assert (row["period"], row["agent"], row["kind"]) == (None, None, None)
        assert row["tokens"] == models_[row["model"]]["tokens"]
        if row["cost"] is None:
            assert models_[row["model"]]["cost"] is None and row["split"] == []
            continue
        assert row["cost"] == pytest.approx(models_[row["model"]]["cost"])
        assert sum(g["cost"] for g in row["groups"] if g["cost"] is not None) == pytest.approx(
            row["cost"])
        assert sum(item["cost"] for item in row["split"]) == pytest.approx(row["cost"])
        for group in row["groups"]:
            assert set(group) == set(page.GROUP.items)
            assert [t["class"] for t in group["terms"]] == [
                "input", "cache_read", "cache_write_5m", "cache_write_1h", "output"]
            assert sum(t["cost"] for t in group["terms"]) == pytest.approx(group["cost"])
    [mystery] = [row for row in body["rows"] if row["model"] == "mystery-9"]
    [group] = mystery["groups"]
    assert group["cost"] is None and group["why"]
    assert all(t["cost"] is None and "cost" in t["unknown"] for t in group["terms"])
    assert body["total"]["split"] and set(body["total"]) == {*page.SUMS, "split"}


def test_terms_by_day_and_by_a_sessions_agent(seeded):
    days = get("/api/terms", by="day", utc="true")
    assert days["by"] == "day"
    daily = get("/api/daily", utc="true")["rows"]
    assert [row["period"] for row in days["rows"]] == sorted({row["period"] for row in daily})
    for row in days["rows"]:
        mine = [d for d in daily if d["period"] == row["period"]]
        assert row["tokens"] == sum(d["tokens"] for d in mine)
        assert sum(item["cost"] for item in row["split"]) == pytest.approx(row["cost"])
    agents = get("/api/terms", by="agent", session="s1", harness="claude", utc="true")
    assert [(row["agent"], row["kind"]) for row in agents["rows"]] == [
        ("main", "main"), ("a", "subagent"), ("b", "workflow")]
    assert {"name": "session", "value": "s1"} in agents["filters"]
    assert "needs a session id" in refused("/api/terms", by="agent")
    narrowed = get("/api/terms", session="s1", harness="claude", utc="true")
    assert narrowed["total"]["requests"] == get(
        "/api/session", session="s1", harness="claude", utc="true")["total"]["requests"]


@pytest.fixture
def requests_seeded(monkeypatch):
    monkeypatch.setattr(models, "table", lambda *a, **k: table())
    conn = store.open_index()
    try:
        requests_index(conn)
    finally:
        conn.close()


def test_requests_send_one_row_per_request_with_the_marks_between_them(requests_seeded):
    body = get("/api/requests", session="s1", harness="claude", utc="true")
    rows = body["rows"]
    assert [row["n"] for row in rows] == [1, 2, 3, 4]
    assert set(rows[0]) == set(page.REQUEST_ROW)
    assert [row["turn_start"] for row in rows] == [False, False, True, False]
    assert rows[1]["tier"] == "fast"
    assert "output" in rows[2]["unknown"]
    assert rows[0]["window"] == 200000 and rows[3]["window"] is None
    assert "window" in rows[3]["unknown"]
    for row in rows:
        assert sum(item["cost"] for item in row["split"]) == pytest.approx(row["cost"])
    total = body["total"]
    assert total["marks"] == [{"after": 2, "ts": at("2026-10-01T12:00") + 90,
                               "trigger": "auto", "pre_tokens": 167_412}]
    assert (total["turns"], total["claimed_elsewhere"], total["copied_marks"],
            total["marks_known"]) == (2, 0, 0, True)
    sub = get("/api/requests", session="s1", agent="a", harness="claude", utc="true")
    assert [row["model"] for row in sub["rows"]] == ["m-b", "m-b"]
    assert sub["total"]["turns"] is None and sub["total"]["marks"] == []
    resumed = get("/api/requests", session="s2", harness="claude", utc="true")
    assert resumed["total"]["claimed_elsewhere"] == 3 and resumed["total"]["copied_marks"] == 1


def test_requests_need_a_session_and_answer_empty_for_an_unknown_one(requests_seeded):
    status, _body = asyncio.run(ask(served(), "/api/requests", {}))
    assert status == 400
    body = get("/api/requests", session="nope")
    assert body["rows"] == [] and body["total"]["marks"] == []


#: A dated `[[model]]` row over the reports' test table's `m-a`.
DATED = models.Override(id="m-a", prices={"input": 4.0, "output": 20.0},
                        start=at("2026-01-01T00:00"), note="the new rate", number=1)


@pytest.fixture
def rates(monkeypatch):
    monkeypatch.setattr(models, "table", lambda *a, **k: table(DATED))


def test_prices_send_the_models_asked_for_cut_to_their_shape(rates):
    body = get("/api/prices", models="m-a,gpt-x,mystery-9")
    assert set(body) == set(page.PRICES)
    assert body["matched"] == 0 and body["rows"] == 4
    by_model = {row["model"]: row for row in body["models"]}
    assert list(by_model) == ["m-a", "gpt-x", "mystery-9"]
    m_a = by_model["m-a"]
    assert set(m_a) == set(page.RATE.items)
    assert (m_a["input"], m_a["output"]) == (4.0, 20.0)
    assert m_a["provenance"]["period"] == {"from": "2026-01-01", "to": None}
    assert m_a["provenance"]["layer"] == "override"
    gpt = by_model["gpt-x"]
    [priority] = gpt["tiers"]
    assert priority["name"] == "priority"
    assert priority["conditions"] == {"above_tokens": None, "service_tier": "priority",
                                      "speed": None}
    assert priority["prices"]["cache_read"] == 0.2 and priority["prices"]["input"] == 2.0
    mystery = by_model["mystery-9"]
    assert mystery["input"] is None and mystery["tiers"] == []
    assert {"input", "output", "window", "tiers", "provider"} <= set(mystery["unknown"])
    [row] = body["overrides"]
    assert (row["id"], row["prices"]["input"], row["note"]) == ("m-a", 4.0, "the new rate")
    assert row["from"].startswith("2026-01-01") and row["to"] is None
    assert [layer["layer"] for layer in body["layers"]] == ["shipped", "override"]


def test_prices_look_each_model_up_under_its_provider(monkeypatch):
    seeded = models.Table(doc({"acme/zz-1": {"input": 1.0, "output": 2.0, "provider": "acme"}}))
    monkeypatch.setattr(models, "table", lambda *a, **k: seeded)
    body = get("/api/prices", models="zz-1,zz-1", providers=",acme")
    assert [row["asked_provider"] for row in body["models"]] == [None, "acme"]
    assert [row["input"] for row in body["models"]] == [None, 1.0]
    assert body["models"][1]["provenance"]["rule"] == "provider prefix"
    assert "one for each id" in refused("/api/prices", models="m-a", providers="a,b")
    assert "go with models" in refused("/api/prices", providers="anthropic")


def test_prices_search_the_table_and_say_how_many_matched(rates):
    body = get("/api/prices", q="M-")
    assert [row["model"] for row in body["models"]] == ["m-a", "m-b"]
    assert body["matched"] == 2
    none = get("/api/prices")
    assert none["models"] == [] and none["matched"] == 0 and none["overrides"]
    assert "2 characters" in refused("/api/prices", q="m")


def test_prices_refuse_both_keys_and_more_than_a_hundred_ids(rates):
    assert "not both" in refused("/api/prices", models="m-a", q="m-")
    many = ",".join(f"m-{n}" for n in range(pagehandlers.PRICES_MAX + 1))
    assert "at most 100" in refused("/api/prices", models=many)
    assert len(get("/api/prices", models=",".join(["m-a"] * 100))["models"]) == 100


def test_prices_search_the_shipped_table_past_fifty_matches():
    body = get("/api/prices", q="claude")
    assert len(body["models"]) == pagehandlers.MATCHES_MAX < body["matched"]
    ids = [row["model"] for row in body["models"]]
    assert ids == sorted(ids)


# --- the declared shapes on an index that isn't there or isn't this usage's -----------------

READS = [("/api/daily", {}), ("/api/daily", {"by": "month"}), ("/api/sessions", {}),
         ("/api/session", {"session": "s1"}), ("/api/projects", {}), ("/api/models", {}),
         ("/api/terms", {}), ("/api/terms", {"by": "agent", "session": "s1"}),
         ("/api/requests", {"session": "s1"}), ("/api/status", {}), ("/api/cost", {})]


def test_every_read_holds_its_shape_with_no_index():
    assert not store.index_path().exists()
    for path, query in READS:
        body = get(path, **query)
        assert "run `moxi usage index` to build it" in body["index"]["note"], path
        assert body.get("rows", body.get("lanes")) == [], path
    assert get("/api/status")["total"]["index_version"] == 0
    assert not store.index_path().exists()


def _set_version(version: int) -> None:
    store.open_index().close()
    conn = sqlite3.connect(store.index_path())
    try:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    finally:
        conn.close()


def test_every_read_holds_its_shape_on_an_older_index_and_migrates_nothing():
    _set_version(store.INDEX_VERSION - 1)
    for path, query in READS:
        body = get(path, **query)
        assert "run `moxi usage index` to bring it up" in body["index"]["note"], path
    assert get("/api/status")["total"]["index_version"] == store.INDEX_VERSION - 1
    conn = sqlite3.connect(store.index_path())
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.INDEX_VERSION - 1
    finally:
        conn.close()


def test_an_index_from_a_newer_usage_is_a_422_with_its_text():
    _set_version(store.INDEX_VERSION + 1)
    for path, query in READS:
        text = refused(path, **query)
        assert f"newer than the {store.INDEX_VERSION} this version knows" in text, path


# --- limits ---------------------------------------------------------------------------------

def put_readings(*found: Reading) -> None:
    conn = readings.open_store()
    try:
        readings.store(conn, found, now=time.time(), days=7)
    finally:
        conn.close()


def codex_reading(used: float, *, observed: float | None = None, extra: dict | None = None,
                  minutes: int = 300) -> Reading:
    now = time.time()
    observed = now - 60 if observed is None else observed
    return Reading("codex", "codex", minutes, "primary", used, now + 3600, observed,
                   "rollout", "thread-1", "pro", json.dumps(extra) if extra else None)


def test_limits_sends_each_window_with_its_dollars_as_a_list():
    put_readings(codex_reading(42.0, extra={"usedDollars": 3.5, "credits": {"limit_usd": 20,
                                                                            "plan": "x"}}))
    before = time.time()
    body = get("/api/limits")
    assert before <= body["now"] <= time.time()
    assert set(body) == {"now", "rows", "pull", "notes"}
    windows = {(r["harness"], r["label"]): r for r in body["rows"]}
    five = windows[("codex", "5h")]
    assert set(five) == set(page.WINDOW.items)
    assert (five["state"], five["used_percent"], five["plan_type"]) == ("fresh", 42.0, "pro")
    assert five["dollars"] == [{"name": "usedDollars", "value": 3.5},
                               {"name": "credits limit_usd", "value": 20.0}]
    assert five["observed_at"].endswith("Z") and five["unknown"] == []
    seven = windows[("codex", "7d")]
    assert seven["state"] == "unavailable" and seven["used_percent"] is None
    assert "used_percent" in seven["unknown"] and "age_s" not in seven["unknown"]
    assert windows[("claude", "5h")]["state"] == "unavailable"


def test_a_failed_pull_says_why_on_the_codex_rows():
    body = get("/api/limits")
    assert body["pull"] == {"tried": False, "ok": False, "reason": "the test makes no pull",
                            "via": None, "added": 0}
    codex = [row for row in body["rows"] if row["harness"] == "codex"]
    assert codex and all(row["note"] == "no newer reading: the test makes no pull"
                         for row in codex)


# --- the stream and its limits poller -------------------------------------------------------

def test_the_poller_publishes_limits_only_when_they_change(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    put_readings(codex_reading(10.0))

    async def go():
        stream = Stream(served(), "limits")
        try:
            name, first = await stream.next(5)
            assert name == "limits" and stream.status == 200
            assert set(first) == {"rows", "pull", "notes"}
            assert await stream.none_within(0.6)
            put_readings(codex_reading(55.0, observed=time.time() - 5))
            _, later = await stream.next(5)
            five = [r for r in later["rows"] if (r["harness"], r["label"]) == ("codex", "5h")]
            assert five[0]["used_percent"] == 55.0
            assert await stream.none_within(0.6)
        finally:
            await stream.close()

    asyncio.run(go())


def test_the_poller_starts_with_the_first_stream_and_runs_for_the_servers_life(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    gathered = []
    real = pagehandlers.gather_limits

    def counted():
        gathered.append(1)
        return real()

    monkeypatch.setattr(pagehandlers, "gather_limits", counted)

    async def go():
        app = served()
        await ask(app, "/api/pass", method="POST")        # a pass alone starts no poller
        assert pagehandlers._current.poller is None and gathered == []
        one, two = Stream(app, "limits"), Stream(app, "index")
        await one.next(5)
        feed = pagehandlers._current
        poller = feed.poller
        assert poller is not None and not poller.done()
        await asyncio.sleep(0.2)
        assert feed.poller is poller
        await one.close()
        await two.close()
        count = len(gathered)
        await asyncio.sleep(0.4)
        assert feed.poller is poller and not poller.done()
        assert len(gathered) > count
        again = Stream(app, "limits")
        assert (await again.next(5))[0] == "limits"
        assert feed.poller is poller
        await again.close()
        poller.cancel()

    asyncio.run(go())


def test_the_poller_outlives_a_gather_that_raises(monkeypatch, caplog):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.1)
    calls = []
    real = pagehandlers.gather_limits

    def flaky():
        calls.append(1)
        if len(calls) <= 2:
            raise RuntimeError("the readings broke")
        return real()

    monkeypatch.setattr(pagehandlers, "gather_limits", flaky)

    async def go():
        stream = Stream(served(), "limits")
        try:
            assert (await stream.next(5))[0] == "limits"
        finally:
            await stream.close()

    with caplog.at_level(logging.ERROR, logger="moxi.usage.page"):
        asyncio.run(go())
    assert len([r for r in caplog.records if r.exc_info]) == 1


# --- the pass -------------------------------------------------------------------------------

def test_the_pass_is_refused_with_no_index_and_builds_none():
    status, body = asyncio.run(ask(served(), "/api/pass", method="POST"))
    assert status == 422
    assert body["refused"] == "there is no index yet; run `moxi usage index` to build it"
    assert not store.index_path().exists()


def test_the_pass_is_refused_on_an_older_index_and_migrates_nothing():
    _set_version(store.INDEX_VERSION - 1)
    status, body = asyncio.run(ask(served(), "/api/pass", method="POST"))
    assert status == 422 and "run `moxi usage index` to bring it up" in body["refused"]
    conn = sqlite3.connect(store.index_path())
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.INDEX_VERSION - 1
    finally:
        conn.close()


def test_the_pass_is_refused_on_an_index_from_a_newer_usage():
    _set_version(store.INDEX_VERSION + 1)
    status, body = asyncio.run(ask(served(), "/api/pass", method="POST"))
    assert status == 422 and "newer than the" in body["refused"]


def test_the_pass_runs_once_at_a_time_holds_busy_and_publishes_index_at_start_and_end(
        seeded, monkeypatch):
    release = threading.Event()
    calls = []

    def slow_pass():
        calls.append(1)
        assert release.wait(10)
        return {"index": {"passed": True, "last_pass": LAST_PASS + 60, "note": None}}

    monkeypatch.setattr(pagehandlers, "_fresh_status", slow_pass)

    async def go():
        app = served()
        stream = Stream(app, "index")
        try:
            assert await stream.none_within(0.2)
            assert await ask(app, "/api/pass", method="POST") == (
                200, {"started": True, "running": True})
            assert await stream.next(5) == ("index", {"running": True, "last_pass": LAST_PASS,
                                                      "note": None})
            assert await ask(app, "/api/pass", method="POST") == (
                200, {"started": False, "running": True})
            _, health = await ask(app, "/api/health")
            assert health["open"]["busy"] == ["pass"]
            release.set()
            assert await stream.next(5) == ("index", {"running": False,
                                                      "last_pass": LAST_PASS + 60,
                                                      "note": None})
            await until(lambda: not pagehandlers._current.running())
            _, health = await ask(app, "/api/health")
            assert health["open"]["busy"] == []
            assert calls == [1]
            assert await ask(app, "/api/pass", method="POST") == (
                200, {"started": True, "running": True})
            await pagehandlers._current.pass_task
            assert calls == [1, 1]
        finally:
            release.set()
            await stream.close()

    asyncio.run(go())


def test_a_pass_that_fails_ends_with_its_reason_on_the_index_topic(seeded, monkeypatch,
                                                                   caplog):
    def broken():
        raise OSError("the disk went away")

    monkeypatch.setattr(pagehandlers, "_fresh_status", broken)

    async def go():
        app = served()
        stream = Stream(app, "index")
        try:
            assert (await ask(app, "/api/pass", method="POST"))[0] == 200
            assert (await stream.next(5))[1]["running"]
            _, end = await stream.next(5)
            assert end == {"running": False, "last_pass": LAST_PASS,
                           "note": "the pass failed (OSError); the page server's log has "
                                   "the details"}
        finally:
            await stream.close()

    asyncio.run(go())


def transcript(root: Path, n: int) -> None:
    t = ClaudeTranscript(root, session_id=f"0190d000-0000-7000-8000-{n:012d}",
                         project=f"-work-p{n}")
    t.user("go")
    t.assistant("ok", usage=usage(output=10 + n), request_id=f"req_{n}")
    t.write()


def test_a_catch_up_pass_over_many_new_sources_reads_them_on_the_pool(monkeypatch):
    claude = Path(sessions.root("claude"))
    transcript(claude, 0)
    first = index.pass_(projects=None, workers=1)
    write_config("[index]\nworkers = 2\n")
    many = index.POOL_ABOVE + 2
    for n in range(1, many + 1):
        transcript(claude, n)
    pooled = []
    real = index._read_pooled

    def spy(run, tasks, count):
        pooled.append((len(tasks), count))
        return real(run, tasks, count)

    monkeypatch.setattr(index, "_read_pooled", spy)

    async def go():
        app = served()
        stream = Stream(app, "index")
        try:
            assert await ask(app, "/api/pass", method="POST") == (
                200, {"started": True, "running": True})
            assert (await stream.next(5))[1]["last_pass"] == first.finished_at
            _, end = await stream.next(120)
            await pagehandlers._current.pass_task
            return end
        finally:
            await stream.close()

    end = asyncio.run(go())
    assert pooled == [(many, 2)]
    assert end["running"] is False and end["note"] is None
    assert end["last_pass"] > first.finished_at
    body = get("/api/sessions", top="1000")
    assert len(body["rows"]) == many + 1
    assert body["total"]["output"] == sum(10 + n for n in range(many + 1))


# --- without the UI ---------------------------------------------------------------------------

WITHOUT_UI = textwrap.dedent("""
    import json
    import sys

    # As if the UI shell weren't installed.
    sys.modules["moxi_ui"] = None

    import moxi_usage.api
    import moxi_usage.ceiling
    import moxi_usage.cli
    import moxi_usage.doctor
    import moxi_usage.guards
    import moxi_usage.listeners
    import moxi_usage.mark
    import moxi_usage.module
    from moxi_dev.contract import check

    code = moxi_usage.cli.main(["prices", "--json"], prog="moxi usage")
    loaded = sorted(name for name in sys.modules
                    if name in ("moxi_usage.page", "moxi_usage.pagehandlers")
                    or name.startswith("moxi_ui."))
    try:
        import moxi_usage.page
    except ImportError as err:
        refused = str(err)
    else:
        refused = None
    findings = [str(finding) for finding in check(sys.argv[1])]
    print(json.dumps({"code": code, "loaded": loaded, "refused": refused,
                      "findings": findings}))
""")


def test_usage_works_without_the_ui(tmp_path):
    done = subprocess.run([sys.executable, "-P", "-c", WITHOUT_UI, str(ROOT)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["code"] == 0
    assert out["loaded"] == []
    assert out["refused"].startswith("usage's page needs the ui module")
    assert out["findings"] == []

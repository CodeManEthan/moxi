"""The gateway's request path in process, from `watch` to the router: redaction of reads,
of the proxy's errors and of the push channel's events; the gateway's header policy; the
audit row of each request and each gate's refusal; the proxy headers stripped."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from moxi_loopback.api import Field, Out, Page, Route, export, gateway, guard

from gateway_kit import HOST, Authenticator, Phone
from moxi_ui import page as shell_page
from moxi_ui.gateway import Parts
from moxi_ui.gateway import audit as audit_mod
from moxi_ui.gateway.access import OPEN_PATHS, PAGES
from moxi_ui.gateway.acts import Acts
from moxi_ui.gateway.mount import Mount
from moxi_ui.gateway.router import ICONS, GatewayRouter, mark, strip, watch
from moxi_ui.gateway.shapes import handle
from moxi_ui.proxy import UpstreamError
from moxi_ui.push import Push
from moxi_ui.router import fallback, worker_layer
from test_push import FakeClient, FakeStarters, Reader, ask

KEY = b"q" * 32
LOG = "/var/lib/someone/shell.log"
ROWS = Route("/api/rows", "read", "pkg.mod:rows", gateway=True,
             returns={"rows": Out("list", items=Out("table", items={
                 "name": Out("string"), "log": Out("string", mark="path"),
                 "secret": Out("string", mark="redact", nullable=True),
                 "inner": Out("table", items={"where": Out("string", mark="path"),
                                              "n": Out("int")})})),
                      "note": Out("string")})
DESK = Route("/api/desk", "read", "pkg.mod:desk", returns={"x": Out("int")})
STATUS = Route("/api/job", "read", "pkg.mod:job", gateway=True,
               query={"job": Field("string")}, returns={"state": Out("string")})
ACT = Route("/api/go", "act", "pkg.mod:go", gateway=True, confirmed=True, status="/api/job",
            returns={}, body={"name": Field("string")})
STREAM = Route("/api/events", "read", "pkg.mod:events", gateway=True, stream=True,
               topics={"rows": Out("table", items={"log": Out("string", mark="path"),
                                                   "n": Out("int")})})
PLAIN_STREAM = Route("/api/events", "read", "pkg.mod:events", stream=True,
                     topics={"x": Out("table", items={"n": Out("int")})})
PLAIN_READ = Route("/api/read", "read", "pkg.mod:read", returns={"n": Out("int")})
MOUNTS = {"fixture": export(Page(routes=(ROWS, DESK, STATUS, ACT, STREAM))),
          "plain": export(Page(routes=(PLAIN_READ, PLAIN_STREAM))),
          "ui": export(shell_page.PAGE)}
FILES = {"manifest.json": b"{}", "sw.js": b"// worker", "offline.html": b"<!doctype html>",
         "icon-192.png": b"\x89PNG", "icon-512.png": b"\x89PNG", "client.js": b"// client",
         "theme.css": b":root{}"}
JSON = [(b"content-type", b"application/json")]
FIXTURE_POLICY = b"default-src 'none'; frame-ancestors 'none'"


class Response:
    def __init__(self, status: int, headers: list, body: bytes):
        self.status, self.headers, self.body = status, headers, body

    async def chunks(self):
        yield self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class Reads:
    """The proxy's client for module reads, played: `answers[target]` is `(status,
    headers, body)` or an exception to raise. Records each request."""

    def __init__(self):
        self.answers: dict[str, Any] = {}
        self.sent: list[tuple[str, str, list]] = []

    async def request(self, module, method, target, *, headers=(), **kw):
        self.sent.append((module, target, list(headers)))
        answer = self.answers[target]
        if isinstance(answer, BaseException):
            raise answer
        return Response(*answer)


class Gate:
    """The gateway's request path as the shell builds it, behind a paired phone:
    `watch(strip(guard(mark(Access.layer(GatewayRouter)))))`, on a gateway profile."""

    def __init__(self, tmp_path: Path, *, mounted: bool = True):
        self.phone = Phone(tmp_path)
        access = self.phone.access
        access.open_paths = OPEN_PATHS | ICONS
        self.state_dir = self.phone.state_dir
        self.writer = audit_mod.Audit(self.state_dir)
        self.mount = Mount(True, None) if mounted else Mount(False, "tcp")
        self.reads = Reads()
        self.push = Push(FakeStarters([{"module": "fixture", "state": "up", "log": LOG}]),
                         mounts=MOUNTS, client=FakeClient())
        self.page_answer: tuple[int, list, bytes] = (200, JSON, b"{}")
        self.acts = Acts(mounts=lambda: MOUNTS, mounted=lambda: self.mount.mounted,
                         client=self.reads, audit=self.writer, key=lambda: KEY,
                         device_of=access.device_of)
        self.router = GatewayRouter(access, mounts=lambda: MOUNTS,
                                    table=lambda: self.profile.table, mount=lambda: self.mount,
                                    page=self.own_page, push=self.push, starters=None,
                                    acts=self.acts, key=lambda: KEY, files=FILES.get)
        self.router.client = self.reads
        parts = access.parts(Parts(table=self.router.build, router=self.router,
                                   fallback=fallback))
        parts.router = mark(parts.router)
        self.profile = gateway(parts.table(self.mount), session=parts.session, host=HOST,
                               port=443, fallback=parts.fallback)
        self.phone.profile = self.profile
        self.phone.app = worker_layer(watch(guard(parts.router, lambda: self.profile),
                                            lambda: self.profile, self.writer,
                                            device_of=access.device_of))

    async def own_page(self, scope, receive, send):
        status, headers, body = self.page_answer
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    def paired(self) -> Gate:
        assert self.phone.pair(Authenticator()).status == 200
        return self

    def get(self, path: str, **kw):
        return self.phone.request("GET", path, **kw)

    def rows(self) -> list[dict]:
        self.writer.close()
        return list(audit_mod.read_rows(self.state_dir))


def gateway_policy(answer) -> bool:
    (policy,) = answer.header("content-security-policy")
    return "manifest-src 'self'" in policy and "worker-src 'self'" in policy


def _rows_body(value: dict) -> tuple[int, list, bytes]:
    return 200, [*JSON, (b"content-security-policy", FIXTURE_POLICY)], json.dumps(value).encode()


VALUE = {"rows": [{"name": "/plain/text", "log": "/var/lib/someone/a.log", "secret": "s3",
                   "inner": {"where": "/var/lib/someone/b/", "n": 4}}], "note": "kept"}


def test_a_gateway_read_has_its_marked_values_changed_and_nothing_else(tmp_path: Path) -> None:
    gate = Gate(tmp_path).paired()
    gate.reads.answers["/api/rows?x=1"] = _rows_body(VALUE)
    answer = gate.get("/m/fixture/api/rows?x=1")
    assert answer.status == 200
    got = answer.json()
    row = got["rows"][0]
    assert row["name"] == "/plain/text" and got["note"] == "kept" and row["inner"]["n"] == 4
    assert row["secret"] is None
    assert row["log"] == handle("/var/lib/someone/a.log", KEY)
    assert row["inner"]["where"] == handle("/var/lib/someone/b/", KEY)
    assert answer.header("content-length") == [str(len(answer.body))]
    assert answer.header("content-security-policy") == [FIXTURE_POLICY.decode()]
    again = gate.get("/m/fixture/api/rows?x=1").json()
    assert again == got, "a handle is the same on every read"
    assert b"someone" not in answer.body


def test_a_read_that_isnt_json_or_is_too_large_is_a_502_and_errors_pass_as_sent(
        tmp_path: Path) -> None:
    gate = Gate(tmp_path).paired()
    gate.reads.answers["/api/rows"] = (200, [(b"content-type", b"text/plain")], b"plain text")
    answer = gate.get("/m/fixture/api/rows")
    assert answer.status == 502 and answer.body == b"" and gateway_policy(answer)
    gate.reads.answers["/api/rows"] = (200, JSON, b'"' + b"x" * (1 << 20) + b'"')
    assert gate.get("/m/fixture/api/rows").status == 502
    for status in (409, 422, 503):
        body = json.dumps({"refused": "busy", "log": "/var/lib/someone/a.log"}).encode()
        gate.reads.answers["/api/rows"] = (status, JSON, body)
        answer = gate.get("/m/fixture/api/rows")
        assert (answer.status, answer.body) == (status, body)
    codes = [row["kind"] for row in gate.rows() if row["kind"] in audit_mod.CODES]
    assert codes == ["upstream", "upstream"]


def test_the_proxys_502_answers_with_its_log_as_a_handle(tmp_path: Path) -> None:
    gate = Gate(tmp_path).paired()
    gate.reads.answers["/api/rows"] = UpstreamError(
        502, {"error": "start", "module": "fixture", "exit": 1, "log": LOG})
    answer = gate.get("/m/fixture/api/rows")
    assert answer.status == 502 and gateway_policy(answer)
    assert answer.json() == {"error": "start", "module": "fixture", "exit": 1,
                             "log": handle(LOG, KEY)}


def test_a_module_files_proxy_502_is_redacted_and_carries_the_gateway_policy(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(MOUNTS, "fixture", {**MOUNTS["fixture"], "assets": ["app.js"]})
    gate = Gate(tmp_path).paired()
    failing = Reads()
    failing.answers["/app.js"] = UpstreamError(
        502, {"error": "start", "module": "fixture", "exit": 1, "log": LOG})
    gate.router.proxy.client = failing
    answer = gate.get("/m/fixture/app.js")
    assert answer.status == 502 and gateway_policy(answer)
    assert answer.json() == {"error": "start", "module": "fixture", "exit": 1,
                             "log": handle(LOG, KEY)}
    assert LOG.encode() not in answer.body
    # The module's own file still passes with the headers its server set.
    failing.answers["/app.js"] = (200, [(b"content-type", b"text/javascript"),
                                        (b"content-security-policy", FIXTURE_POLICY)], b"//")
    served = gate.get("/m/fixture/app.js")
    assert served.status == 200 and served.body == b"//"
    assert served.header("content-security-policy") == [FIXTURE_POLICY.decode()]


def test_the_shells_own_read_is_redacted_and_its_desk_routes_are_unreached(
        tmp_path: Path) -> None:
    gate = Gate(tmp_path).paired()
    gate.page_answer = (200, JSON, json.dumps(
        {"at": "now", "error": "/var/lib/someone/broken", "contracts": [1],
         "modules": []}).encode())
    answer = gate.get("/m/ui/api/modules")
    assert answer.status == 200
    assert answer.json() == {"at": "now", "error": None, "contracts": [1], "modules": []}
    for path in ("/m/ui/api/servers", "/m/ui/api/doctor", "/m/fixture/api/desk",
                 "/m/fixture/api/events", "/m/plain/api/read", "/m/fixture/api/go"):
        assert gate.get(path).status == 404, path
    assert gate.reads.sent == []


def test_every_answer_the_router_builds_carries_the_gateway_policy(tmp_path: Path) -> None:
    gate = Gate(tmp_path).paired()
    document = gate.get("/", navigate=True)
    assert document.status == 200 and gateway_policy(document)
    assert b'data-gateway="on"' in document.body and b'data-gateway-acts="on"' in document.body
    assert b'<link rel="manifest" href="/manifest.webmanifest">' in document.body
    for path in (*PAGES, "/manifest.webmanifest", "/sw.js", "/offline", "/icon-192.png",
                 "/m/ui/client.js", "/m/ui/theme.css"):
        answer = gate.get(path)
        assert answer.status == 200 and gateway_policy(answer), path
    worker = gate.get("/any/other.js", headers=(("service-worker", "script"),))
    assert worker.status == 200 and worker.body == FILES["sw.js"] and gateway_policy(worker)
    assert gate.phone.request("POST", "/gw/act/prepare",
                              {"module": "fixture", "path": "/api/go",
                               "body": {"name": "a"}}).status == 200
    state = gate.get("/gw/act/state?act=" + "A" * 43)
    assert state.status == 404 and gateway_policy(state)
    pushed = gate.get("/m/ui/api/push?topics=ui.modules")
    assert pushed.status == 200 and gateway_policy(pushed)
    assert pushed.header("content-type") == ["text/event-stream"]
    gate.reads.answers["/api/rows"] = UpstreamError(502, {"error": "unreachable",
                                                          "module": "fixture", "log": LOG})
    assert gateway_policy(gate.get("/m/fixture/api/rows"))

    refused = gate.phone.request("GET", "/m/fixture/api/rows",
                                 headers=(("host", "other.example.test"),))
    assert refused.status in (403, 404)
    (policy,) = refused.header("content-security-policy")
    assert "manifest-src" not in policy


def test_a_navigation_without_a_session_is_sent_to_sign_in(tmp_path: Path) -> None:
    gate = Gate(tmp_path)
    answer = gate.get("/", navigate=True)
    assert answer.status == 303 and answer.header("location")[0].startswith("/signin?n=")
    assert gate.get("/icon-512.png").status == 200, "the icons are open before sign-in"


def test_with_the_acts_unmounted_the_act_paths_are_404s_with_code_route(tmp_path: Path) -> None:
    gate = Gate(tmp_path, mounted=False).paired()
    assert gate.profile.table.find("POST", "/gw/act/prepare") is None
    document = gate.get("/", navigate=True)
    assert b'data-gateway-acts="off"' in document.body
    assert b'data-gateway-reason="tcp"' in document.body
    for method, path in (("POST", "/gw/act/prepare"), ("POST", "/gw/act/commit"),
                         ("GET", "/gw/act/state?act=" + "A" * 43)):
        body = {"act": "A" * 43} if method == "POST" else None
        assert gate.phone.request(method, path, body).status == 404, path
    assert gate.reads.sent == []
    refusals = [row for row in gate.rows() if row["kind"] == "route"]
    assert len(refusals) == 3 and {row["gate"] for row in refusals} == {5}


def test_each_request_writes_its_row_and_each_gate_its_refusal_code(tmp_path: Path) -> None:
    gate = Gate(tmp_path).paired()
    gate.reads.answers["/api/rows"] = _rows_body(VALUE)
    signed_in = gate.get("/m/fixture/api/rows")
    assert signed_in.status == 200
    statuses = {
        "host": gate.phone.request("GET", "/m/fixture/api/rows",
                                   headers=(("host", "other.example.test"),)).status,
        "options": gate.phone.request("OPTIONS", "/m/fixture/api/rows").status,
        "origin": gate.phone.request("POST", "/gw/act/prepare", {}, origin="https://evil.test"
                                     ).status,
        "credential": gate.phone.request("GET", "/m/fixture/api/rows", cookie=False).status,
        "route": gate.get("/m/fixture/api/desk").status,
        "content-type": gate.phone.request(
            "POST", "/gw/act/prepare", {}, headers=(("content-type", "text/plain"),)).status,
    }
    assert all(status in (403, 404) for status in statuses.values()), statuses
    rows = gate.rows()
    by_kind: dict[str, list[dict]] = {}
    for row in rows:
        by_kind.setdefault(row["kind"], []).append(row)
    gates = {"host": 1, "options": 2, "origin": 3, "credential": 4, "route": 5,
             "content-type": 5}
    for code, number in gates.items():
        assert [row.get("gate") for row in by_kind.get(code, [])] == [number], code
    (read,) = [row for row in by_kind["request"] if row["path"] == "/m/fixture/api/rows"]
    assert read["status"] == 200 and read["module"] == "fixture" and read["device"]
    assert read["session"] == gate.phone.access.store.find(
        [gate.phone.cookie], gate.phone.key, need_key=True)[:12]
    assert isinstance(read["ms"], int)
    assert all(not set(row) & audit_mod.FORBIDDEN for row in rows)
    assert gate.phone.cookie not in json.dumps(rows) and gate.phone.key not in json.dumps(rows)


def test_the_proxy_headers_are_stripped_before_anything_reads_them(tmp_path: Path) -> None:
    seen: list[list] = []

    async def inner(scope, receive, send):
        seen.append(list(scope["headers"]))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    writer = audit_mod.Audit(tmp_path)
    app = watch(mark(inner), lambda: None, writer, device_of=lambda _id: None)
    headers = [(b"host", HOST.encode()), (b"Tailscale-User-Login", b"someone@example.test"),
               (b"x-forwarded-for", b"10.0.0.1"), (b"Forwarded", b"for=10.0.0.1"),
               (b"accept", b"*/*")]

    async def send(message):
        return None

    async def receive():
        return {"type": "http.request", "body": b""}

    asyncio.run(app({"type": "http", "method": "GET", "raw_path": b"/x", "headers": headers},
                    receive, send))
    assert seen == [[(b"host", HOST.encode()), (b"accept", b"*/*")]]
    assert strip(headers) == seen[0]

    gate = Gate(tmp_path / "gate")
    answer = gate.get("/m/fixture/api/rows",
                      headers=(("tailscale-user-login", "someone@example.test"),))
    assert answer.status == 403, "a proxy's identity header admits nothing"


# --- the push channel's gateway form ----------------------------------------------------


async def _seen(reader: Reader, *wanted: str) -> dict[str, Any]:
    """Read `reader` until each of `wanted` has come, in any order: an event by its name,
    a state event as `moxi.state <module>`. The last of each."""
    got: dict[str, Any] = {}
    while not all(name in got for name in wanted):
        item = await reader.next()
        assert item is not None, f"the stream ended before {wanted}"
        name, data = item
        if name == "moxi.state":
            name = f"moxi.state {data['module']}"
        got[name] = data
    return got


def test_a_desk_and_a_phone_stream_share_one_upstream_and_only_the_phones_is_redacted() -> None:
    async def body():
        client = FakeClient()
        push = Push(FakeStarters([{"module": "fixture", "state": "up", "log": LOG}]),
                    mounts=MOUNTS, client=client)
        phone_app = push.gateway_app(lambda: KEY)
        client.publish("fixture", "rows", {"log": "/var/lib/someone/a.log", "n": 1})
        client.publish("plain", "x", {"n": 2})
        desk = Reader(push, ask("fixture.rows,plain.x")).start()
        phone = Reader(phone_app, ask("fixture.rows,plain.x,ui.servers")).start()
        on_desk = await _seen(desk, "fixture.rows", "plain.x")
        on_phone = await _seen(phone, "fixture.rows", "ui.servers", "moxi.state plain",
                               "moxi.state fixture")
        assert on_desk["fixture.rows"] == {"log": "/var/lib/someone/a.log", "n": 1}
        assert on_desk["plain.x"] == {"n": 2}
        assert on_phone["fixture.rows"] == {"log": handle("/var/lib/someone/a.log", KEY),
                                            "n": 1}
        assert on_phone["moxi.state plain"] == {"module": "plain", "state": "down",
                                                "detail": "not exported: x"}
        state = on_phone["moxi.state fixture"]
        assert set(state) == {"module", "state", "detail"} and state["module"] == "fixture"
        assert on_phone["ui.servers"]["servers"][0]["log"] == handle(LOG, KEY)
        assert [module for module, _ in client.requests].count("fixture") == 1
        assert [module for module, _ in client.requests].count("plain") == 1
        assert phone.header("content-security-policy").count("manifest-src 'self'") == 1
        assert "manifest-src" not in desk.header("content-security-policy")

        client.publish("fixture", "rows", {"log": "/var/lib/someone/c.log", "n": 3})
        assert (await phone.until("fixture.rows"))["log"] == handle("/var/lib/someone/c.log",
                                                                    KEY)
        assert (await desk.until("fixture.rows"))["log"] == "/var/lib/someone/c.log"
        assert all(item[0] != "plain.x" for item in [*phone.skipped, *phone.drained()])
        for reader in (desk, phone):
            reader.leave()
            await reader.task
        await push.aclose()

    asyncio.run(asyncio.wait_for(body(), 30))


def test_an_act_through_the_whole_path_writes_its_rows_and_a_routed_refusal_its_code(
        tmp_path: Path) -> None:
    gate = Gate(tmp_path).paired()
    gate.reads.answers["/api/go"] = (202, JSON, b'{"status":"/api/job?job=j9"}')
    prepared = gate.phone.request("POST", "/gw/act/prepare",
                                  {"module": "fixture", "path": "/api/go",
                                   "body": {"name": "a"}})
    assert prepared.status == 200
    act = prepared.json()["act"]
    committed = gate.phone.request("POST", "/gw/act/commit", {"act": act})
    assert committed.status == 202 and gateway_policy(committed)
    assert committed.json() == {"act": act, "outcome": "accepted", "status": "/api/job?job=j9"}
    (sent,) = gate.reads.sent
    assert (b"idempotency-key", act.encode()) in sent[2]
    unknown = gate.phone.request("POST", "/gw/act/commit", {"act": "C" * 43})
    assert unknown.status == 404
    rows = gate.rows()
    assert [row["kind"] for row in rows if row["kind"] in ("intent", "outcome")] == [
        "intent", "outcome"]
    commits = [row for row in rows if row["kind"] == "request" and row["path"] == "/gw/act/commit"]
    assert [row["status"] for row in commits] == [202, 404]
    (refused,) = [row for row in rows if row["kind"] == "route"]
    assert refused == {**refused, "path": "/gw/act/commit", "status": 404}
    assert "gate" not in refused, "the router refused it, not the check"


def test_the_phones_ui_modules_has_its_marked_fields_redacted() -> None:
    async def body():
        push = Push(FakeStarters([]), mounts=MOUNTS, client=FakeClient())
        value = {"at": "now", "error": "/var/lib/someone/broken", "contracts": [1],
                 "modules": []}
        push.publish("modules", value)
        desk = Reader(push, ask("ui.modules")).start()
        phone = Reader(push.gateway_app(lambda: KEY), ask("ui.modules")).start()
        assert (await _seen(desk, "ui.modules"))["ui.modules"] == value
        assert (await _seen(phone, "ui.modules"))["ui.modules"] == {**value, "error": None}
        for reader in (desk, phone):
            reader.leave()
            await reader.task
        await push.aclose()

    asyncio.run(asyncio.wait_for(body(), 30))

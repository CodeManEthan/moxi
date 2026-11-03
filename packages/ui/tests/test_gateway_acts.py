"""The phone's act store, in process: prepare, commit and state against a stand-in upstream
and a real loopback page app, with the audit log on disk."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from moxi_loopback.api import (
    Accepted, Field, Out, Page, Route, app, build_table, export, guard, upstream,
)

from moxi_ui.gateway import audit as audit_mod
from moxi_ui.gateway.acts import ACT_TTL, COMMITS_PER_MINUTE, KEPT, Acts
from moxi_ui.proxy import UpstreamError
from moxi_ui.session import admitted

VECTORS = Path(__file__).parents[1] / "web" / "test" / "outcome-vectors.json"
KEY = b"k" * 32
H = __name__
STATUS = Route("/api/job", "read", H + ":job", gateway=True, query={"job": Field("string")},
               returns={"state": Out("string")})
ACT = Route("/api/go", "act", H + ":go", gateway=True, confirmed=True, status="/api/job",
            returns={}, body={"name": Field("string", max=8), "n": Field("int", required=False)})
READ = Route("/api/rows", "read", H + ":rows", gateway=True, returns={"n": Out("int")})
PAGE = Page(routes=(STATUS, ACT, READ))
EXPORT = export(PAGE)
ACCEPTED = (202, {"status": "/api/job?job=j1"})
GOOD = {"module": "fixture", "path": "/api/go", "body": {"name": "ab"}}
#: Session id hash -> device: two sessions of one device and one of another.
DEVICES = {"s1": "dev-a", "s2": "dev-a", "s3": "dev-b"}
KEYS: list[str] = []


def go(call):
    """The loopback app's confirmed act: it records the key it ran under."""
    KEYS.append(call.key)
    return Accepted(f"/api/job?job={len(KEYS)}")


def job(call):
    return {"state": "done"}


def rows(call):
    return {"n": 1}


class Response:
    def __init__(self, status: int, body: bytes, headers: list | None = None):
        self.status, self.body = status, body
        self.headers = headers or [(b"content-type", b"application/json")]

    async def chunks(self):
        if self.body:
            yield self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class Client:
    """The proxy's client, played: records each send, waits on `gate` when one is set,
    then answers `answer`, a `(status, data)` or an exception to raise."""

    def __init__(self, answer: Any = ACCEPTED):
        self.answer = answer
        self.sent: list[dict[str, Any]] = []
        self.gate: asyncio.Event | None = None

    async def request(self, module, method, path, *, headers=(), body=b"", act=False, **kw):
        self.sent.append({"module": module, "method": method, "path": path,
                          "headers": dict(headers), "body": body, "act": act})
        if self.gate is not None:
            await self.gate.wait()
        if isinstance(self.answer, BaseException):
            raise self.answer
        status, data = self.answer
        return Response(status, b"" if data is None else json.dumps(data).encode())


class PageClient:
    """The proxy's client, sending each request into a real loopback page app in process,
    behind loopback's check on a module server's profile, with the Host and token the
    proxy adds."""

    PORT = 40000

    def __init__(self):
        self.token = "t" * 43
        profile = upstream(build_table(PAGE)[0], token=self.token, port=self.PORT)
        self.app = guard(app(PAGE, module="fixture", server_id="0" * 32), lambda: profile)
        self.sent: list[dict[str, Any]] = []

    async def request(self, module, method, path, *, headers=(), body=b"", act=False, **kw):
        self.sent.append({"path": path, "headers": dict(headers), "body": body})
        headers = [(b"host", f"127.0.0.1:{self.PORT}".encode()),
                   (b"x-moxi-token", self.token.encode()), *headers]
        scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(),
                 "query_string": b"", "headers": headers, "http_version": "1.1",
                 "scheme": "http", "server": ("127.0.0.1", self.PORT),
                 "client": ("127.0.0.1", 50000)}
        messages = [{"type": "http.request", "body": body, "more_body": False}]
        got: dict[str, Any] = {"body": b""}

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                got["status"], got["headers"] = message["status"], message["headers"]
            else:
                got["body"] += message.get("body", b"")

        await self.app(scope, receive, send)
        return Response(got["status"], got["body"], got["headers"])


class Store:
    """An `Acts` over `tmp_path`'s audit log, with clocks the test moves."""

    def __init__(self, tmp_path: Path, client: Any = None, *, mono: list | None = None):
        self.client = client if client is not None else Client()
        self.on = True
        self.mono = mono if mono is not None else [1000.0]
        self.wall = [1_800_000_000.0]
        self.audit = audit_mod.Audit(tmp_path, clock=lambda: self.wall[0])
        self.acts = Acts(mounts=lambda: {"fixture": EXPORT}, mounted=lambda: self.on,
                         client=self.client, audit=self.audit, key=lambda: KEY,
                         device_of=DEVICES.get, clock=lambda: self.wall[0],
                         monotonic=lambda: self.mono[0])
        self.state_dir = tmp_path

    async def call(self, method: str, path: str, body: Any = None, *, session: str = "s1",
                   query: bytes = b"") -> tuple[int, Any, str | None]:
        admitted.set(session)
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        messages = [{"type": "http.request", "body": b"" if body is None else data}]

        async def receive():
            return messages.pop(0)

        scope = {"type": "http", "method": method, "raw_path": path.encode(),
                 "query_string": query}
        (status, _headers, raw), code = await self.acts(scope, receive)
        return status, (json.loads(raw) if raw else None), code

    async def prepare(self, request: Any = None, **kw) -> str:
        status, data, _code = await self.call("POST", "/gw/act/prepare", request or GOOD, **kw)
        assert status == 200, data
        return data["act"]

    async def commit(self, act: str, **kw) -> tuple[int, Any, str | None]:
        return await self.call("POST", "/gw/act/commit", {"act": act}, **kw)

    async def state(self, act: str, **kw) -> tuple[int, Any, str | None]:
        return await self.call("GET", "/gw/act/state", query=b"act=" + act.encode(), **kw)

    def rows(self) -> list[dict]:
        return list(audit_mod.read_rows(self.state_dir, days=2, now=self.wall[0]))


def run(body):
    return asyncio.run(asyncio.wait_for(body(), 30))


async def until(predicate, what: str) -> None:
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def test_prepare_echoes_the_canonical_body_under_a_new_key_and_commit_sends_it_once(
        tmp_path: Path) -> None:
    async def body():
        store = Store(tmp_path)
        status, data, _ = await store.call(
            "POST", "/gw/act/prepare",
            {"module": "fixture", "path": "/api/go", "body": {"n": 2, "name": "ab"}})
        assert status == 200
        act = data["act"]
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", act)
        assert data["module"] == "fixture" and data["path"] == "/api/go"
        assert data["body"] == {"n": 2, "name": "ab"}
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", data["expires"])
        assert store.client.sent == []

        status, data, code = await store.commit(act)
        assert (status, code) == (202, None)
        assert data == {"act": act, "outcome": "accepted", "status": "/api/job?job=j1"}
        (sent,) = store.client.sent
        assert sent["body"] == b'{"n":2,"name":"ab"}' and sent["act"] is True
        assert sent["method"] == "POST" and sent["path"] == "/api/go"
        assert sent["headers"][b"idempotency-key"] == act.encode()

        kinds = [row["kind"] for row in store.rows()]
        assert kinds == ["intent", "outcome"]
        intent, outcome = store.rows()
        assert "body" not in intent and act not in json.dumps(intent)
        assert intent["act_hash"] == outcome["act_hash"] and len(intent["body_hmac"]) == 64
        assert outcome["outcome"] == "accepted" and outcome["status"] == 202

    run(body)


def test_prepare_refuses_what_isnt_a_reachable_confirmed_act_or_its_declared_body(
        tmp_path: Path) -> None:
    async def body():
        store = Store(tmp_path)
        for request in ({**GOOD, "path": "/api/rows"}, {**GOOD, "path": "/api/none"},
                        {**GOOD, "module": "other"}):
            assert await store.call("POST", "/gw/act/prepare", request) == (404, None, "route")
        for request in ({**GOOD, "body": {"name": "x" * 9}}, {**GOOD, "body": {"other": 1}},
                        {"module": "fixture", "path": "/api/go"}, b"not json"):
            status, data, code = await store.call("POST", "/gw/act/prepare", request)
            assert (status, code) == (400, None) and data["error"]
        status, _data, code = await store.call("POST", "/gw/act/prepare",
                                               {**GOOD, "body": {"name": "x" * (2 << 20)}})
        assert (status, code) == (413, "size")
        assert await store.call("GET", "/gw/act/nothing") == (404, None, "route")
        assert store.client.sent == [] and store.rows() == []

    run(body)


def test_a_double_commit_acts_once_and_the_second_gets_the_firsts_state(tmp_path: Path) -> None:
    async def body():
        store = Store(tmp_path)
        store.client.gate = asyncio.Event()
        act = await store.prepare()
        first = asyncio.ensure_future(store.commit(act))
        await until(lambda: store.client.sent, "the first send")
        status, data, code = await store.commit(act, session="s2")
        assert (status, code) == (202, None)
        assert data == {"act": act, "outcome": "unknown", "text": "the act is still being sent"}
        store.client.gate.set()
        status, data, _ = await first
        assert data["outcome"] == "accepted"
        again = await store.commit(act)
        assert again == (202, data, None)
        assert len(store.client.sent) == 1

        racing = await store.prepare()
        store.client.gate = None
        both = await asyncio.gather(store.commit(racing), store.commit(racing, session="s2"))
        assert len(store.client.sent) == 2 and all(status == 202 for status, _, _ in both)

    run(body)


def test_the_act_id_is_a_key_a_real_loopback_app_takes_and_the_act_runs_once(
        tmp_path: Path) -> None:
    async def body():
        KEYS.clear()
        store = Store(tmp_path, PageClient())
        act = await store.prepare()
        status, data, _ = await store.commit(act)
        assert status == 202
        assert data == {"act": act, "outcome": "accepted", "status": "/api/job?job=1"}
        assert KEYS == [act]
        assert await store.commit(act) == (202, data, None)
        assert KEYS == [act] and len(store.client.sent) == 1

    run(body)


def test_an_unspent_act_expires_after_its_time(tmp_path: Path) -> None:
    async def body():
        store = Store(tmp_path)
        act = await store.prepare()
        store.mono[0] += ACT_TTL + 1
        assert await store.commit(act) == (404, None, "route")
        assert await store.state(act) == (404, None, "route")
        assert store.client.sent == []

    run(body)


def test_one_act_in_flight_per_session_and_six_commits_a_minute(tmp_path: Path) -> None:
    async def body():
        store = Store(tmp_path)
        store.client.gate = asyncio.Event()
        first, second = await store.prepare(), await store.prepare()
        flying = asyncio.ensure_future(store.commit(first))
        await until(lambda: store.client.sent, "the first send")
        assert await store.commit(second) == (429, {"error": "busy"}, "rate")
        store.client.gate.set()
        await flying
        status, data, _ = await store.commit(second)
        assert status == 202 and data["outcome"] == "accepted", "a refused commit spent nothing"

        store.client.gate = None
        store.mono[0] += 61
        for _ in range(COMMITS_PER_MINUTE):
            assert (await store.commit(await store.prepare()))[0] == 202
        late = await store.prepare()
        assert await store.commit(late) == (429, {"error": "busy"}, "rate")
        store.mono[0] += 61
        assert (await store.commit(late))[0] == 202
        assert len(store.client.sent) == 2 + COMMITS_PER_MINUTE + 1

    run(body)


def test_state_is_the_devices_on_any_session_and_after_a_restart_comes_from_the_rows(
        tmp_path: Path) -> None:
    async def body():
        store = Store(tmp_path)
        act = await store.prepare()
        assert await store.state(act, session="s2") == (200, {"act": act, "state": "prepared"},
                                                        None)
        assert await store.state(act, session="s3") == (404, None, "route")
        assert await store.commit(act, session="s3") == (404, None, "route")
        await store.commit(act)
        assert await store.state(act, session="s2") == (
            200, {"act": act, "state": "accepted", "status": "/api/job?job=j1"}, None)

        refused = Store(tmp_path, Client((409, {"conflict": "busy with another act"})),
                        mono=store.mono)
        other = await refused.prepare()
        assert (await refused.commit(other))[1]["text"] == "busy with another act"

        restarted = Store(tmp_path)
        assert await restarted.state(act, session="s2") == (
            200, {"act": act, "state": "accepted", "module": "fixture"}, None)
        assert await restarted.state(other, session="s2") == (
            200, {"act": other, "state": "refused", "code": 409}, None)
        assert await restarted.state(act, session="s3") == (404, None, "route")
        assert await restarted.state("A" * 43) == (404, None, "route")
        status, _data, code = await restarted.call("GET", "/gw/act/state", query=b"act=x")
        assert (status, code) == (400, None)

        store.mono[0] += KEPT + 1
        assert await store.state(act, session="s2") == (
            200, {"act": act, "state": "accepted", "module": "fixture"}, None)

    run(body)


def test_an_intent_row_alone_reads_unknown(tmp_path: Path) -> None:
    async def body():
        import hashlib

        store = Store(tmp_path)
        act = "B" * 43
        store.audit.act("intent", {"module": "fixture", "path": "/api/go", "device": "dev-a",
                                   "act_hash": hashlib.sha256(act.encode()).hexdigest()})
        assert await store.state(act) == (200, {"act": act, "state": "unknown"}, None)

    run(body)


def _answer(vector: dict) -> Any:
    """What the stand-in upstream gives for one shared outcome vector: the proxy's own
    error for a proxy-shaped body, no answer for status 0, else the status and body."""
    data = vector["data"]
    if vector["status"] == 0:
        return ConnectionResetError("the connection broke after the send")
    if isinstance(data, dict) and data.get("error") in ("start", "unreachable", "upstream",
                                                        "timeout", "stopping"):
        return UpstreamError(vector["status"], data)
    return vector["status"], data


def test_every_upstream_outcome_maps_by_the_one_outcome_table(tmp_path: Path) -> None:
    async def body():
        mono = [1000.0]
        for vector in json.loads(VECTORS.read_text()):
            store = Store(tmp_path, Client(_answer(vector)), mono=mono)
            act = await store.prepare()
            status, data, code = await store.commit(act)
            assert (status, code) == (202, None), vector
            assert data == {"act": act, **vector["outcome"]}, vector
            assert store.rows()[-1]["outcome"] == vector["outcome"]["outcome"], vector
            mono[0] += 61

    run(body)


def test_an_act_unmounted_between_prepare_and_commit_is_a_404_and_stays_unspent(
        tmp_path: Path) -> None:
    async def body():
        store = Store(tmp_path)
        act = await store.prepare()
        store.on = False
        assert await store.commit(act) == (404, None, "route")
        assert await store.state(act) == (404, None, "route")
        assert await store.call("POST", "/gw/act/prepare", GOOD) == (404, None, "route")
        assert store.client.sent == [] and store.rows() == []
        store.on = True
        status, data, _ = await store.commit(act)
        assert status == 202 and data["outcome"] == "accepted"
        assert len(store.client.sent) == 1

    run(body)


@pytest.mark.parametrize("path", ["/gw/act/prepare", "/gw/act/commit"])
def test_acts_unmounted_while_the_body_arrives_send_nothing(tmp_path: Path, path: str) -> None:
    async def body():
        store = Store(tmp_path)
        act = await store.prepare()
        rows = store.rows()
        data = json.dumps(GOOD if path.endswith("prepare") else {"act": act}).encode()

        async def receive():
            store.on = False  # the mount ends while the body is on its way
            return {"type": "http.request", "body": data}

        admitted.set("s1")
        scope = {"type": "http", "method": "POST", "raw_path": path.encode(),
                 "query_string": b""}
        (status, _headers, _raw), code = await store.acts(scope, receive)
        assert (status, code) == (404, "route")
        assert store.client.sent == [] and store.rows() == rows

    run(body)


READER = """
import json, pathlib, sys
rows = []
for path in sorted(pathlib.Path(sys.argv[1]).glob("*.jsonl")):
    rows += [json.loads(line)["kind"] for line in path.read_text().splitlines()]
print(json.dumps(rows))
"""


def test_the_intent_row_is_on_disk_for_another_process_before_the_upstream_answers(
        tmp_path: Path) -> None:
    async def body():
        store = Store(tmp_path)
        store.client.gate = asyncio.Event()
        act = await store.prepare()
        flying = asyncio.ensure_future(store.commit(act))
        await until(lambda: store.client.sent, "the send")
        seen = subprocess.run([sys.executable, "-I", "-c", READER,
                               str(audit_mod.audit_dir(tmp_path))],
                              capture_output=True, text=True, timeout=60, check=True)
        assert json.loads(seen.stdout) == ["intent"]
        store.client.gate.set()
        await flying
        assert [row["kind"] for row in store.rows()] == ["intent", "outcome"]

    run(body)


def test_an_intent_row_that_cant_be_written_sends_nothing(tmp_path: Path,
                                                          monkeypatch) -> None:
    async def body():
        store = Store(tmp_path)

        def broken(kind, fields):
            raise OSError("the disk is full")

        monkeypatch.setattr(store.audit, "act", broken)
        act = await store.prepare()
        status, data, _ = await store.commit(act)
        assert status == 202
        assert data == {"act": act, "outcome": "refused", "code": 503,
                        "text": "the module didn't get the act"}
        assert store.client.sent == []
        assert (await store.commit(act))[1] == data

    run(body)


@pytest.mark.parametrize("raw", [b"{}", b'{"act": 5}', b'{"act": "x", "more": 1}'])
def test_a_malformed_commit_is_a_400_or_a_404(tmp_path: Path, raw: bytes) -> None:
    async def body():
        store = Store(tmp_path)
        status, _data, code = await store.call("POST", "/gw/act/commit", raw)
        assert (status, code) in ((400, None), (404, "route"))
        assert store.client.sent == []

    run(body)

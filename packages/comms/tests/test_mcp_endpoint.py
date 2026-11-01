"""The endpoint of comms' tools: MCP over HTTP on loopback, served by a test daemon on an
ephemeral port. The bearer check (401), the Host and Origin checks (421, 403), a restart,
uvicorn started and stopped without its main loop, the host seam it runs on, exit on a
failure, the `[mcp]` keys, `mcp_endpoint`, the uninstall's revocation and the import
cost."""

from __future__ import annotations

import asyncio
import http.client
import itertools
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
import warnings

import pytest
from conftest import wait_until, write_config

from moxi_comms import bindings, claimfile, config, wire
from moxi_comms.client import DaemonClient
from moxi_comms.daemon import Daemon
from moxi_comms.mcp import server as mcp_server
from moxi_comms.mcp import tokens
from moxi_comms.membership import ProcView, Root
from moxi_comms.paths import daemon_socket

PLANNER = "planner@proj-a"
_pids = itertools.count(61000, 10)

INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "test", "version": "0"}}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def call(name: str, arguments: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}


# --------------------------------------------------------------------------- the rig

class View(ProcView):
    """A process table the test writes; a pid with no entry is gone."""

    def __init__(self):
        super().__init__()
        self.table: dict = {}

    def stat(self, pid):
        return self.table.get(int(pid))

    def gone(self, pid):
        return int(pid) not in self.table

    def spawn(self) -> int:
        pid = next(_pids)
        self.table[pid] = (1, f"s{pid}")
        return pid


class Session:
    """A session's adapter: a claim file whose root is a process in `view`, a binding
    under that claim, and the hash of its token registered."""

    def __init__(self, view, name="planner", scope="proj-a"):
        self.root = view.spawn()
        self.key = f"{self.root}-s{self.root}"
        claimfile.write(self.key, {"key": self.key, "keying": "pid", "state": "bound",
                                   "roots": [Root(self.root, f"s{self.root}").as_dict()]})
        self.name, self.scope = name, scope
        self.token = tokens.mint()
        self.client: DaemonClient | None = None

    def bind(self) -> dict:
        self.client = DaemonClient(daemon_socket()).connect()
        self.client.bind(name=self.name, scope=self.scope, adapter="codex", claim=self.key)
        return self.client.mcp_token(tokens.digest(self.token))

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None


@pytest.fixture
def view():
    return View()


@pytest.fixture
def serving(rig, view):
    """Start the rig's daemon again, serving comms' tools on a free loopback port. Pass
    `tools=` to add tools to the endpoint, `port=` for a given port."""

    def start(*, port=0, tools=None, **kw) -> Daemon:
        rig.daemon.stop()
        daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False,
                        serve_tools=True, mcp_port=port, **kw)
        daemon.proc_view = view
        daemon.mcp_tools = tools
        rig.daemon = daemon
        return daemon.start()

    return start


@pytest.fixture
def sessions(view):
    made = []

    def make(**kw):
        s = Session(view, **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def whoami_tool(server) -> None:
    """A tool that answers the identity the bearer check placed the call as, read on
    the loop from the SDK's request context."""
    from mcp.server.auth.middleware.auth_context import get_access_token

    @server.tool(name="whoami")
    async def whoami() -> str:
        return get_access_token().client_id


def port_of(daemon) -> int:
    return daemon.mcp_endpoint.port


def post(daemon_or_port, body, *, token=None, host=None, origin=None, path="/mcp",
         method="POST", raw: bytes | None = None, timeout=10.0):
    """One HTTP request to the endpoint: (status, headers, body)."""
    port = daemon_or_port if isinstance(daemon_or_port, int) else port_of(daemon_or_port)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if host is not None:
        headers["Host"] = host
    if origin is not None:
        headers["Origin"] = origin
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None
                                        else None)
    try:
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


def result_of(response) -> dict:
    status, _, body = response
    assert status == 200, response
    return json.loads(body)["result"]


def ask_daemon(method: str, params=None):
    client = DaemonClient(daemon_socket()).connect()
    try:
        return client.call(method, params or {})
    finally:
        client.close()


def rows() -> list:
    conn = bindings.connect(read_only=True)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM mcp_tokens")]
    finally:
        conn.close()


def refused_connect(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(2)
        return s.connect_ex(("127.0.0.1", port)) != 0


# ==================================================================== mcp_endpoint

def test_a_daemon_that_serves_answers_its_url_and_the_registration_says_served(
        serving, sessions):
    daemon = serving()
    port = port_of(daemon)
    assert ask_daemon("mcp_endpoint") == {
        "served": True, "url": f"http://127.0.0.1:{port}/mcp", "why": None}
    assert daemon.mcp_served and daemon.mcp_url == mcp_server.url_for(port)
    assert sessions().bind() == {"served": True}


def test_a_daemon_built_without_asking_serves_nothing_and_binds_no_port(rig):
    assert ask_daemon("mcp_endpoint") == {"served": False, "url": None,
                                          "why": "disabled"}
    assert rig.daemon.mcp_endpoint is None


def test_enabled_false_serves_nothing(serving, sessions):
    write_config(None, "[mcp]\nenabled = false\n")
    daemon = serving()
    assert ask_daemon("mcp_endpoint") == {"served": False, "url": None,
                                          "why": "disabled"}
    assert daemon.mcp_endpoint is None
    assert sessions().bind() == {"served": False}


def test_the_port_comes_from_the_config_when_the_daemon_names_none(serving):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    write_config(None, f"[mcp]\nport = {free}\n")
    serving(port=None)
    assert ask_daemon("mcp_endpoint")["url"] == f"http://127.0.0.1:{free}/mcp"


def test_a_port_another_socket_listens_on_is_bind_failed_and_revokes_nothing(
        serving, sessions):
    serving()
    s = sessions()
    s.bind()
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        daemon = serving(port=holder.getsockname()[1])
        assert ask_daemon("mcp_endpoint") == {"served": False, "url": None,
                                              "why": "bind-failed"}
    assert daemon.mcp_endpoint is None
    assert [r["revoked_at"] for r in rows()] == [None]


def test_an_app_that_cant_start_is_failed_and_its_port_is_left_free(serving,
                                                                   monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("no app\nsecond line")

    monkeypatch.setattr(mcp_server, "build_app", broken)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    serving(port=free)
    assert ask_daemon("mcp_endpoint") == {"served": False, "url": None, "why": "failed"}
    assert refused_connect(free)


def test_mcp_endpoint_waits_for_the_endpoint_to_settle(serving, monkeypatch):
    """A call that arrives while the endpoint starts is answered after it settles."""
    started = threading.Event()
    real = mcp_server.Endpoint.start

    async def slow_start(self):
        started.set()
        await asyncio.sleep(0.3)
        await real(self)

    monkeypatch.setattr(mcp_server.Endpoint, "start", slow_start)
    answers = []
    worker = threading.Thread(target=lambda: answers.append(serving()))
    worker.start()
    assert started.wait(10)
    assert ask_daemon("mcp_endpoint")["served"] is True
    worker.join(10)


# ======================================================================= the checks

def test_a_registered_token_initializes_lists_and_calls_as_its_own_identity(
        serving, sessions):
    daemon = serving(tools=whoami_tool)
    s = sessions()
    s.bind()
    init = result_of(post(daemon, INITIALIZE, token=s.token))
    assert init["serverInfo"]["name"] == "comms"
    assert [t["name"] for t in result_of(post(daemon, LIST, token=s.token))["tools"]] \
        == ["whoami"]
    answer = result_of(post(daemon, call("whoami"), token=s.token))
    assert answer["content"][0]["text"] == PLANNER


def test_no_token_and_a_wrong_token_get_401_before_any_tool_runs(serving, sessions):
    ran = []

    def tools(server):
        @server.tool(name="whoami")
        async def whoami() -> str:
            ran.append(1)
            return "x"

    daemon = serving(tools=tools)
    sessions().bind()
    for token in (None, "", tokens.mint(), "not a token at all"):
        status, headers, body = post(daemon, call("whoami"), token=token)
        assert status == 401, (token, body)
        assert headers["www-authenticate"].startswith('Bearer error="invalid_token"')
    assert ran == []


def test_a_revoked_token_gets_401(serving, sessions):
    daemon = serving()
    s = sessions()
    s.bind()
    assert post(daemon, INITIALIZE, token=s.token)[0] == 200
    daemon.mcp_tokens.revoke_all(tokens.PORT_TAKEN)
    assert post(daemon, INITIALIZE, token=s.token)[0] == 401


def test_a_token_whose_session_ended_gets_401_and_its_row_reads_session_ended(
        serving, sessions):
    daemon = serving()
    s = sessions()
    s.bind()
    claimfile.remove(s.key)
    assert post(daemon, INITIALIZE, token=s.token)[0] == 401
    assert [r["revoked_why"] for r in rows()] == ["session-ended"]


def test_a_foreign_host_gets_421_and_a_foreign_origin_403(serving, sessions):
    daemon = serving()
    s = sessions()
    s.bind()
    port = port_of(daemon)
    assert post(daemon, INITIALIZE, token=s.token, host="evil.example:80")[0] == 421
    assert post(daemon, INITIALIZE, token=s.token,
                origin="http://evil.example")[0] == 403
    for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
        assert post(daemon, INITIALIZE, token=s.token, host=host)[0] == 200
    assert post(daemon, INITIALIZE, token=s.token,
                origin=f"http://localhost:{port}")[0] == 200


def test_a_get_is_405_and_holds_no_stream_open(serving, sessions):
    daemon = serving()
    s = sessions()
    s.bind()
    status, headers, body = post(daemon, None, token=s.token, method="GET", timeout=3)
    assert (status, headers.get("allow"), body) == (405, "POST", b"")


MODERN = "2026-07-28"


def modern(method: str, rid: int = 7, **params) -> tuple[dict, dict]:
    """A request at the 2026-07-28 protocol, which carries its version in each request
    (no initialize): its body and its routing headers."""
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": {**params, "_meta": {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"}}}}
    return body, {"MCP-Protocol-Version": MODERN, "Mcp-Method": method}


def post_modern(daemon, method: str, *, token, timeout=10.0, **params):
    body, extra = modern(method, **params)
    conn = http.client.HTTPConnection("127.0.0.1", port_of(daemon), timeout=timeout)
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Authorization": f"Bearer {token}", **extra}
    try:
        conn.request("POST", "/mcp", body=json.dumps(body).encode(), headers=headers)
        resp = conn.getresponse()
        return (resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read(),
                resp.will_close)
    finally:
        conn.close()


@pytest.mark.timed
def test_subscriptions_listen_is_refused_at_once_as_json_and_holds_nothing_open(
        serving, sessions):
    daemon = serving(tools=whoami_tool)
    s = sessions()
    s.bind()
    started = time.monotonic()
    # What Claude Code 2.1.316 asks for: the tool list's changes.
    status, headers, body, _ = post_modern(daemon, "subscriptions/listen", token=s.token,
                                           timeout=3,
                                           notifications={"toolsListChanged": True})
    assert time.monotonic() - started < 2
    assert headers["content-type"].startswith("application/json")
    error = json.loads(body)["error"]
    assert (status, error["code"]) == (404, -32601), body
    # The same protocol's other requests are served.
    status, _, body, _ = post_modern(daemon, "tools/list", token=s.token)
    assert status == 200
    assert [t["name"] for t in json.loads(body)["result"]["tools"]] == ["whoami"]


def test_the_capabilities_advertise_no_list_changed_and_no_subscribe(serving, sessions):
    daemon = serving(tools=whoami_tool)
    s = sessions()
    s.bind()
    status, _, body, _ = post_modern(daemon, "server/discover", token=s.token)
    assert status == 200, body
    capabilities = json.loads(body)["result"]["capabilities"]
    for capability in capabilities.values():
        if isinstance(capability, dict):
            assert not capability.get("listChanged"), capabilities
            assert not capability.get("subscribe"), capabilities


def test_a_request_body_over_256_kb_is_refused(serving, sessions):
    daemon = serving()
    s = sessions()
    s.bind()
    big = json.dumps(call("whoami", {"text": "x" * (256 * 1024)})).encode()
    assert post(daemon, None, token=s.token, raw=big)[0] == 413


def test_the_auth_settings_turn_the_sdk_resource_check_off_without_a_warning():
    pool = mcp_server.concurrent.futures.ThreadPoolExecutor(1)
    try:
        verifier = mcp_server.Verifier(lambda token: None, pool)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            app = mcp_server.build_app(verifier, mcp_server.url_for(8450))
    finally:
        pool.shutdown()
    assert isinstance(app, mcp_server._NoStreams)


def test_the_verifier_runs_the_registry_on_the_pool_never_on_the_loop():
    seen = []

    class Row:
        identity, claim_key = PLANNER, "123-s1"

    def verify(token):
        seen.append(threading.current_thread().name)
        return Row() if token == "good" else None

    pool = mcp_server.concurrent.futures.ThreadPoolExecutor(
        1, thread_name_prefix="the-pool")
    try:
        verifier = mcp_server.Verifier(verify, pool)
        good = asyncio.run(verifier.verify_token("good"))
        assert asyncio.run(verifier.verify_token("bad")) is None
        assert asyncio.run(verifier.verify_token("")) is None
    finally:
        pool.shutdown()
    assert (good.client_id, good.scopes, good.claims) == (PLANNER, ["comms"],
                                                           {"claim": "123-s1"})
    assert len(seen) == 2 and all(n.startswith("the-pool") for n in seen)


def test_a_verifier_whose_check_raises_lets_nothing_in(caplog):
    def verify(token):
        raise OSError(f"broken read of {token}")

    pool = mcp_server.concurrent.futures.ThreadPoolExecutor(1)
    try:
        verifier = mcp_server.Verifier(verify, pool)
        secret = tokens.mint()
        assert asyncio.run(verifier.verify_token(secret)) is None
    finally:
        pool.shutdown()
    assert secret not in caplog.text


def test_an_sdk_client_with_a_registered_token_lists_and_calls_the_tools(serving,
                                                                         sessions):
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    daemon = serving(tools=whoami_tool)
    s = sessions()
    s.bind()

    async def run():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {s.token}"},
                                      timeout=10) as http:
            async with streamable_http_client(daemon.mcp_url, http_client=http) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    answer = await session.call_tool("whoami", {})
                    return [t.name for t in listed.tools], answer.content[0].text

    assert asyncio.run(asyncio.wait_for(run(), 20)) == (["whoami"], PLANNER)


# ========================================================================== restart

def test_after_a_restart_the_row_survives_and_the_rebind_relinks_it(serving, sessions):
    daemon = serving(tools=whoami_tool)
    port = port_of(daemon)
    s = sessions()
    s.bind()
    assert result_of(post(daemon, call("whoami"), token=s.token))
    # Keep-alive connections to the first daemon leave TIME_WAIT sockets on the port.
    s.close()
    daemon = serving(port=port, tools=whoami_tool)
    assert ask_daemon("mcp_endpoint")["url"] == f"http://127.0.0.1:{port}/mcp"
    (row,) = daemon.mcp_tokens.active()
    assert row.identity == PLANNER and not row.bound
    # Before the rebind the token still places the call; the tools say it reconnects.
    assert result_of(post(port, call("whoami"), token=s.token))
    assert s.bind() == {"served": True}
    (row,) = daemon.mcp_tokens.active()
    assert row.bound
    answer = result_of(post(port, call("whoami"), token=s.token))
    assert answer["content"][0]["text"] == PLANNER
    assert [r["revoked_at"] for r in rows()] == [None]


def test_a_stopped_daemon_frees_the_port(serving, rig):
    daemon = serving()
    port = port_of(daemon)
    daemon.stop()
    assert refused_connect(port)
    assert not daemon.mcp_served
    with socket.socket() as again:
        again.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        again.bind(("127.0.0.1", port))


# ============================================================ uvicorn, without serve()

def test_uvicorn_is_started_and_stopped_by_hand_and_never_serves(serving, monkeypatch):
    import uvicorn

    calls = []
    real_startup, real_shutdown = uvicorn.Server.startup, uvicorn.Server.shutdown

    async def startup(self, sockets=None):
        calls.append(("startup", len(sockets), self.config.date_header,
                      self.config.limit_concurrency, self.config.timeout_keep_alive,
                      self.config.timeout_graceful_shutdown, self.config.lifespan,
                      self.config.http, type(self.lifespan).__name__))
        await real_startup(self, sockets=sockets)

    async def shutdown(self, sockets=None):
        calls.append(("shutdown",))
        await real_shutdown(self, sockets=sockets)

    def serve(*a, **kw):
        raise AssertionError("serve() runs uvicorn's main loop and its tick")

    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    monkeypatch.setattr(uvicorn.Server, "shutdown", shutdown)
    monkeypatch.setattr(uvicorn.Server, "serve", serve)
    monkeypatch.setattr(uvicorn.Server, "main_loop", serve)
    daemon = serving()
    daemon.stop()
    assert calls == [("startup", 1, False, 64, 5, mcp_server.GRACE, "on", "h11",
                      "LifespanOn"), ("shutdown",)]


def test_the_uvicorn_calls_the_endpoint_uses_keep_their_shape():
    """The endpoint takes `serve()`'s own steps by hand; a uvicorn that changes them
    fails here, not in a daemon."""
    import inspect

    import uvicorn

    assert list(inspect.signature(uvicorn.Server.startup).parameters) == ["self",
                                                                           "sockets"]
    assert list(inspect.signature(uvicorn.Server.shutdown).parameters) == ["self",
                                                                            "sockets"]
    source = inspect.getsource(uvicorn.Server._serve)
    assert "config.load()" in source
    assert "self.lifespan = config.lifespan_class(config)" in source
    assert "await self.startup(sockets=sockets)" in source
    assert "await self.main_loop()" in source
    assert "self.lifespan_class = " in inspect.getsource(uvicorn.Config.load)


def test_nothing_on_the_host_loop_uses_its_default_executor(serving, sessions):
    daemon = serving(tools=whoami_tool)
    s = sessions()
    s.bind()
    for body in (INITIALIZE, LIST, call("whoami")):
        result_of(post(daemon, body, token=s.token))
    post(daemon, INITIALIZE, token="wrong")
    # The loop's default executor (one thread, `moxi.aio.run`'s) never started a thread.
    executor = daemon.host.loop._default_executor
    assert executor is None or not executor._threads


# ======================================================================= the host seam

def test_the_endpoint_stops_before_the_daemon_socket_closes(serving):
    daemon = serving()
    seen = []
    real = daemon.mcp_endpoint.stop

    async def stop():
        seen.append(daemon.socket_path.exists() and daemon.host._server.is_serving())
        await real()

    daemon.host.stop_hook = stop
    port = port_of(daemon)
    daemon.stop()
    assert seen == [True]
    assert refused_connect(port)


def test_the_stop_hook_runs_once_and_not_at_all_without_a_loop():
    host = wire.Host("/nonexistent/never-started.sock", lambda *a: None, lambda c: None)
    ran = []

    async def hook():
        ran.append(1)

    host.stop_hook = hook
    assert host.close(timeout=0.1)
    assert ran == []


def test_the_stop_hook_is_skipped_on_the_loops_own_thread(rig):
    host = rig.daemon.host
    ran, done = [], threading.Event()

    async def hook():
        ran.append(1)

    host.stop_hook = hook

    def on_loop():
        host._run_stop_hook()
        done.set()

    host.loop.call_soon_threadsafe(on_loop)
    assert done.wait(5)
    assert ran == [] and host.stop_hook is None


def test_a_stop_hook_that_hangs_is_given_up_on(rig, monkeypatch):
    monkeypatch.setattr(wire, "STOP_HOOK_WAIT", 0.2)
    host = rig.daemon.host

    async def hook():
        await asyncio.sleep(60)

    host.stop_hook = hook
    started = time.monotonic()
    host._run_stop_hook()
    assert time.monotonic() - started < 5


def test_run_on_loop_refuses_from_the_loop_and_without_one(rig):
    host = rig.daemon.host
    assert host.run_on_loop(asyncio.sleep(0, "ran"), timeout=5) == "ran"
    unstarted = wire.Host("/nonexistent/x.sock", lambda *a: None, lambda c: None)
    with pytest.raises(RuntimeError):
        unstarted.run_on_loop(asyncio.sleep(0))


@pytest.mark.timed
def test_a_request_in_flight_at_the_stop_gets_a_graceful_bound(serving, sessions,
                                                                monkeypatch):
    monkeypatch.setattr(mcp_server, "GRACE", 1)
    monkeypatch.setattr(mcp_server, "STOP_WAIT", 2.0)
    entered = threading.Event()

    def tools(server):
        @server.tool(name="hang")
        async def hang() -> str:
            entered.set()
            await asyncio.Event().wait()
            return "never"

    daemon = serving(tools=tools)
    s = sessions()
    s.bind()
    caller = threading.Thread(
        target=lambda: _quietly(post, daemon, call("hang"), token=s.token, timeout=10))
    caller.start()
    assert entered.wait(10)
    started = time.monotonic()
    daemon.stop()
    assert time.monotonic() - started < 5
    caller.join(10)


def _quietly(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except OSError:
        return None


# ===================================================================== exit on failure

def test_an_endpoint_that_fails_after_it_started_stops_the_daemon_nonzero(
        serving, monkeypatch):
    """The app's lifespan ends on its own: the endpoint closes, `mcp_endpoint` answers
    `failed`, and the daemon is told to stop, which `main` turns into a nonzero exit."""
    import contextlib

    from mcp.server import streamable_http_manager

    crash = asyncio.Event()
    loops = []

    # The manager's own `run` holds the task group every request runs in; this one's
    # group loses a task on cue, which ends the app's lifespan as a real crash would.
    @contextlib.asynccontextmanager
    async def run(self):
        loops.append(asyncio.get_running_loop())

        async def die():
            await crash.wait()
            raise RuntimeError("the session manager's task group died")

        async with asyncio.TaskGroup() as tg:
            tg.create_task(die())
            yield

    monkeypatch.setattr(streamable_http_manager.StreamableHTTPSessionManager, "run", run)
    fatal = []
    daemon = serving(on_fatal=fatal.append)
    port = port_of(daemon)
    assert ask_daemon("mcp_endpoint")["served"]
    loops[0].call_soon_threadsafe(crash.set)
    assert wait_until(lambda: fatal, 10)
    assert fatal == ["comms' tools endpoint failed"] and daemon.fatal == fatal[0]
    assert ask_daemon("mcp_endpoint") == {"served": False, "url": None, "why": "failed"}
    assert wait_until(lambda: refused_connect(port), 5)


# =========================================================================== config

def test_the_mcp_keys_default_to_on_and_8450_and_are_read_at_start():
    cfg = config.load()
    assert (cfg.mcp.enabled, cfg.mcp.port) == (True, 8450)
    keys = config.schema().keys
    assert keys["mcp.enabled"].read == keys["mcp.port"].read == "start"


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_a_port_out_of_range_is_refused(port):
    write_config(None, f"[mcp]\nport = {port}\n")
    with pytest.raises(config.ConfigError) as raised:
        config.load()
    assert raised.value.key == "mcp.port"


def test_the_config_check_takes_the_mcp_keys():
    config.check_text("[mcp]\nenabled = false\nport = 9000\n")
    cfg = config.load()
    assert cfg.mcp.port == 8450


# ======================================================================== import cost

#: What the endpoint loads, which no path but the daemon's start may.
HEAVY = ("mcp", "uvicorn", "starlette", "sse_starlette", "pydantic", "httpx2")
#: Every comms module a hook, a verb, an adapter or a launch imports first.
LIGHT = ("moxi_comms.daemon", "moxi_comms.cli", "moxi_comms.client",
         "moxi_comms.listeners", "moxi_comms.listener", "moxi_comms.reserved",
         "moxi_comms.module", "moxi_comms.mcp", "moxi_comms.mcp.tokens",
         "moxi_comms.adapters.session", "moxi_comms.adapters.claude_shim",
         "moxi_comms.adapters.codex", "moxi_comms.adapters.opencode",
         "moxi_comms.api")


def test_no_hook_verb_adapter_or_launch_path_imports_the_endpoints_libraries():
    code = (f"import sys\nfor m in {LIGHT!r}:\n    __import__(m)\n"
            f"print(sorted({{n.split('.')[0] for n in sys.modules}} & set({HEAVY!r})))")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                          text=True, timeout=120, env=dict(os.environ))
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


def _import_ms(module: str) -> float:
    done = subprocess.run([sys.executable, "-I", "-X", "importtime", "-c",
                           f"import {module}"], capture_output=True, text=True,
                          timeout=120, env=dict(os.environ))
    assert done.returncode == 0, done.stderr
    for line in done.stderr.splitlines():
        parts = line.split("|")
        if len(parts) == 3 and parts[2].strip() == module:
            return int(parts[1]) / 1000
    raise AssertionError(f"{module} not in the import times")


@pytest.mark.timed
def test_the_endpoints_import_is_paid_by_the_daemon_start_alone():
    """The daemon's own module stays far under what the endpoint's libraries cost, which
    are paid once, at a daemon start with `[mcp] enabled`."""
    endpoint = statistics.median(_import_ms("moxi_comms.mcp.server") for _ in range(3))
    daemon = statistics.median(_import_ms("moxi_comms.daemon") for _ in range(3))
    assert endpoint < 1500, endpoint
    assert daemon < endpoint / 2, (daemon, endpoint)

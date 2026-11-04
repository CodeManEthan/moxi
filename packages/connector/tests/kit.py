"""A fake Claude client for the door, in process, and the fakes its tests share.

No listener is involved: `request` builds a scope as uvicorn's h11 protocol does, feeds the
body through `receive` and collects what the app sends. The requests are the ones the live
test's request log shows: Claude's MCP calls carry `User-Agent: Claude-User`, and its
sign-in calls arrived as `python-httpx/0.28.1`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from moxi_connector.provider import Provider, Result, StatusRow, Tool
from moxi_connector.signin import GRANT, Grant, Mount, unauthorized

HOST = "door.example.net"
CLAUDE = "Claude-User"
HTTPX = "python-httpx/0.28.1"
LEGACY = "2025-06-18"
MODERN = "2026-07-28"
TOKEN = "good-token"
GRANT_OF = Grant("0a1b2c3d", "phone", "client-1")


@dataclass
class Answer:
    status: int = 0
    headers: list = field(default_factory=list)
    body: bytes = b""
    messages: list = field(default_factory=list)

    def header(self, name: str):
        name_b = name.lower().encode()
        found = [v.decode() for k, v in self.headers if k.lower() == name_b]
        return found[0] if len(found) == 1 else (found or None)

    def json(self):
        return json.loads(self.body)


def scope(method="POST", path="/mcp", *, host=HOST, headers=(), user_agent=CLAUDE):
    pairs = []
    if host is not None:
        pairs.append((b"host", host.encode()))
    if user_agent is not None:
        pairs.append((b"user-agent", user_agent.encode()))
    pairs.extend((k.lower().encode(), v.encode()) for k, v in headers)
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": pairs, "server": ("127.0.0.1", 8452), "client": ("127.0.0.1", 50000)}


async def request(app, method="POST", path="/mcp", body=None, *, host=HOST, headers=(),
                  user_agent=CLAUDE, raw: bytes | None = None, send_fails=False) -> Answer:
    """One request through `app`. `send_fails` makes the server's send raise on the final
    body, as a connection the client dropped does."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None
                                        else b"")
    chunks = [data]
    out = Answer()

    async def receive():
        if chunks:
            return {"type": "http.request", "body": chunks.pop(), "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        out.messages.append(message)
        if message["type"] == "http.response.start":
            out.status = int(message["status"])
            out.headers = list(message.get("headers", []))
        elif message["type"] == "http.response.body":
            if send_fails and not message.get("more_body", False):
                raise OSError("the client went away")
            out.body += message.get("body", b"")

    extra = [("content-type", "application/json"),
             ("accept", "application/json, text/event-stream"), *headers]
    try:
        await app(scope(method, path, host=host, headers=extra, user_agent=user_agent),
                  receive, send)
    except OSError:
        if not send_fails:
            raise
    return out


def rpc(method: str, params: dict | None = None, rid: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}


def initialize(rid: int = 1) -> dict:
    return rpc("initialize", {"protocolVersion": LEGACY, "capabilities": {},
                              "clientInfo": {"name": "claude-ai", "version": "0.1.0"}}, rid)


def modern(method: str, rid: int = 7, **params) -> tuple[dict, list]:
    """A request at the 2026-07-28 protocol, which carries its version in each request:
    its body and its routing headers."""
    body = rpc(method, {**params, "_meta": {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "claude-ai", "version": "0"}}}, rid)
    headers = [("mcp-protocol-version", MODERN), ("mcp-method", method)]
    if method == "tools/call":
        headers.append(("mcp-name", params["name"]))
    return body, headers


def call(name: str, arguments: dict | None = None, rid: int = 3) -> dict:
    return rpc("tools/call", {"name": name, "arguments": arguments or {}}, rid)


def bearer(token: str = TOKEN) -> list:
    return [("authorization", f"Bearer {token}")]


class Admit:
    """A stand-in sign-in: a `POST /mcp` with `Bearer good-token` is admitted as
    `GRANT_OF`; anything else is 401. It counts its mounts and gives each one a route
    `/state` that answers the mount's own number, so a test sees which app answered."""

    def __init__(self):
        self.mounts: list = []
        self.stopped: list[bool] = []

    def mount(self, origin):
        number = len(self.mounts)
        self.mounts.append(origin)

        def guard(app):
            async def check(scope, receive, send):
                auth = dict(scope["headers"]).get(b"authorization", b"")
                if auth != f"Bearer {TOKEN}".encode():
                    await unauthorized(origin, send)
                    return
                scope[GRANT] = GRANT_OF
                await app(scope, receive, send)
            return check

        async def state(scope, receive, send):
            body = json.dumps({"mount": number, "origin": origin.url}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})

        return Mount(routes={"/state": state}, guard=guard)

    async def stopping(self, switch_on: bool) -> None:
        self.stopped.append(switch_on)


@dataclass
class Fake:
    """A fake provider's record of what happened to it."""

    calls: list = field(default_factory=list)
    started: list = field(default_factory=list)
    stopped: int = 0
    written: list = field(default_factory=list)


def provider(fake: Fake, *tools: Tool, fail_start: bool = False, gate=None,
             rows=(("bound", "ok", "the fake is up"),)) -> Provider:
    async def start(env):
        fake.started.append(env)
        if fail_start:
            raise RuntimeError("the fake can't start")

    async def stop():
        fake.stopped += 1

    return Provider(tools=tools, start=start, stop=stop,
                    status=lambda: [StatusRow(*r) for r in rows], gate=gate)


def echo_tool(fake: Fake, name: str = "echo") -> Tool:
    async def echo(args, ctx):
        fake.calls.append((name, dict(args), ctx))
        ctx.after_answer(fake.written.append)
        ctx.log(recipients=["planner"], returned=1)
        return Result(f"echo {args.get('say', '')}", {"said": args.get("say", "")})

    return Tool(name, "Say it back.", {"type": "object",
                                       "properties": {"say": {"type": "string"}}},
                echo, output_schema={"type": "object",
                                     "properties": {"said": {"type": "string"}}},
                annotations={"readOnlyHint": True, "idempotentHint": True})

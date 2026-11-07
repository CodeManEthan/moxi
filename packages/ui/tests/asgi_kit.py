"""Drive an ASGI app in process, and a page package of handlers for the server tests.

No listener is involved: a test builds a scope as uvicorn's h11 protocol does, feeds the
body through `receive` and collects what the app sends.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from moxi_ui.loopback.headers import SECURITY_HEADERS
from moxi_ui.loopback.server import CHECKED

TOKEN = "T" * 43
NONCE = "N" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321
TCP = ("127.0.0.1", PORT)
UNIX = ("/run/page.sock", None)


def scope(method="GET", path="/api/summary", *, query=b"", headers=(), server=TCP,
          checked=False, host=True, token=True, extra=None):
    """A scope as uvicorn's h11 protocol builds it. `raw_path` is the path as sent."""
    raw = path.encode("latin-1") if isinstance(path, str) else path
    pairs = []
    if host:
        pairs.append((b"host", b"127.0.0.1:%d" % PORT))
    if token:
        pairs.append((b"x-moxi-token", TOKEN.encode()))
    pairs.extend((k.lower(), v) for k, v in headers)
    out = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "server": server,
        "client": ("127.0.0.1", 50000) if server[1] is not None else None,
        "scheme": "http",
        "method": method,
        "root_path": "",
        "path": raw.decode("latin-1"),
        "raw_path": raw,
        "query_string": query,
        "headers": pairs,
        "state": {},
    }
    if checked:
        out[CHECKED] = True
    if extra:
        out.update(extra)
    return out


def json_headers():
    return [(b"content-type", b"application/json")]


@dataclass
class Answer:
    status: int = 0
    headers: list = field(default_factory=list)
    body: bytes = b""
    messages: list = field(default_factory=list)

    def header(self, name):
        name = name.lower().encode()
        found = [v.decode() for k, v in self.headers if k.lower() == name]
        return found[0] if len(found) == 1 else (found or None)

    def json(self):
        return json.loads(self.body)


async def drive(app, sc, body=b"", *, chunks=None):
    """Run one request through `app` and return what it sent."""
    parts = list(chunks) if chunks is not None else [body]
    queue = [{"type": "http.request", "body": part, "more_body": n < len(parts) - 1}
             for n, part in enumerate(parts)]
    answer = Answer()
    gone = asyncio.Event()

    async def receive():
        if queue:
            return queue.pop(0)
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        answer.messages.append(message)
        if message["type"] == "http.response.start":
            answer.status = message["status"]
            answer.headers = list(message["headers"])
        elif message["type"] == "http.response.body":
            answer.body += message.get("body", b"")

    try:
        await app(sc, receive, send)
    finally:
        gone.set()
    return answer


class StreamClient:
    """One open stream: the app runs as a task, and `read` takes what it has sent."""

    def __init__(self, app, sc):
        self.answer = Answer()
        self._gone = asyncio.Event()
        self._sent = asyncio.Queue()
        self._first = True
        self.buffer = b""

        async def receive():
            if self._first:
                self._first = False
                return {"type": "http.request", "body": b"", "more_body": False}
            await self._gone.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                self.answer.status = message["status"]
                self.answer.headers = list(message["headers"])
            elif message["type"] == "http.response.body":
                self.answer.body += message.get("body", b"")
            await self._sent.put(message)

        self.task = asyncio.get_running_loop().create_task(app(sc, receive, send))

    async def started(self, timeout=2.0):
        async with asyncio.timeout(timeout):
            while not self.answer.status:
                await self._sent.get()
        return self.answer.status

    async def events(self, count, timeout=2.0):
        """The next `count` server-sent events or comments, as (event, data) pairs; a
        comment is ("", text)."""
        out = []
        async with asyncio.timeout(timeout):
            while len(out) < count:
                while b"\n\n" not in self.buffer:
                    if self.task.done() and self._sent.empty():
                        raise EOFError("the stream ended")
                    message = await self._sent.get()
                    self.buffer += message.get("body", b"")
                    if message.get("type") == "http.response.body" and not message.get("more_body", False):
                        if b"\n\n" not in self.buffer:
                            raise EOFError("the stream ended")
                block, _, self.buffer = self.buffer.partition(b"\n\n")
                out.append(_parse_event(block.decode()))
        return out

    async def close(self, timeout=2.0):
        self._gone.set()
        async with asyncio.timeout(timeout):
            await self.task


def _parse_event(block):
    if block.startswith(":"):
        return ("", block[1:].strip())
    event, data = "", ""
    for line in block.split("\n"):
        key, _, value = line.partition(": ")
        if key == "event":
            event = value
        elif key == "data":
            data = json.loads(value)
    return (event, data)


def assert_header_set(answer, cache="no-store"):
    for name, value in SECURITY_HEADERS:
        assert answer.header(name) == value, name
    assert answer.header("cache-control") == cache
    assert answer.header("set-cookie") is None
    assert not any(k.startswith(b"access-control-") for k, _ in answer.headers)


# --- the page package -----------------------------------------------------------------

PACKAGE = "lbpages"

HANDLERS = '''
import asyncio
import threading
import time

from moxi_ui.loopback.server import Accepted, Conflict, Refused, Topics

TOPICS = Topics("today", "log")
STATE = {}
CALLS = []


def summary(call):
    CALLS.append(("summary", dict(call.query)))
    if "summary" in STATE:
        return STATE["summary"]
    return {"total": call.query.get("days", 0), "log": "/var/log/x"}


async def refresh(call):
    CALLS.append(("refresh", dict(call.body)))
    return {"ok": bool(call.body.get("force", False)), "module": call.module}


def big(call):
    CALLS.append(("big", dict(call.body)))
    return {}


def conflict(call):
    raise Conflict("already running")


def refused(call):
    raise Refused("not today: d\\u00e9j\\u00e0")


def boom(call):
    raise RuntimeError("the handler broke")


def accepted(call):
    return Accepted("/api/status")


def shape(call):
    return STATE["shape"]


async def wait(call):
    await STATE["wait"].wait()
    return {}


async def stall(call):
    time.sleep(STATE.get("stall", 0.7))
    return {}


def hold(call):
    with call.busy("launch"):
        STATE["held"].set()
        STATE["release"].wait(5)
    return {}


def thread_name(call):
    return {"name": threading.current_thread().name}


def events(call):
    return call.stream(STATE.get("topics", TOPICS))


def health():
    if "health_sleep" in STATE:
        time.sleep(STATE["health_sleep"])
    if STATE.get("health_raises"):
        raise RuntimeError("down")
    return STATE.get("health", (True, "fine"))
'''

FILES = {
    "web/index.html": b"<!doctype html><title>page</title>",
    "web/app.js": b"console.log('page');\n",
    "web/style.css": b"body{}\n",
}


def install_pages(root: Path) -> None:
    """Write the test page package under `root` and put `root` on `sys.path`."""
    pkg = root / PACKAGE
    (pkg / "web").mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "handlers.py").write_text(textwrap.dedent(HANDLERS))
    for name, data in FILES.items():
        (pkg / name).write_bytes(data)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def handlers():
    import importlib

    return importlib.import_module(f"{PACKAGE}.handlers")


def make_page(**overrides):
    from moxi_ui.loopback.routes import Field, Out, Page, Route

    h = f"{PACKAGE}.handlers"
    routes = (
        Route("/api/summary", "read", f"{h}:summary",
              query={"days": Field("int", required=False, max=366),
                     "name": Field("string", required=False)},
              returns={"total": Out("int"), "log": Out("string", mark="path")}),
        Route("/api/refresh", "act", f"{h}:refresh",
              body={"force": Field("bool", required=False)},
              returns={"ok": Out("bool"), "module": Out("string")}),
        Route("/api/big", "act", f"{h}:big", body={"text": Field("string")}, returns={},
              body_cap=16),
        Route("/api/conflict", "act", f"{h}:conflict", returns={}),
        Route("/api/refused", "act", f"{h}:refused", returns={}),
        Route("/api/boom", "read", f"{h}:boom", returns={}),
        Route("/api/accepted", "act", f"{h}:accepted", returns={}),
        Route("/api/shape", "read", f"{h}:shape",
              returns={"total": Out("int"), "note": Out("string", nullable=True)}),
        Route("/api/wait", "read", f"{h}:wait", returns={}),
        Route("/api/stall", "read", f"{h}:stall", returns={}),
        Route("/api/hold", "read", f"{h}:hold", returns={}),
        Route("/api/thread", "read", f"{h}:thread_name", returns={"name": Out("string")}),
        Route("/api/events", "read", f"{h}:events", stream=True,
              query={"since": Field("int", required=False)},
              topics={"today": Out("table", items={"total": Out("int")}),
                      "log": Out("list", items=Out("table", items={"line": Out("string")}))}),
    )
    args = {"routes": routes, "assets": "web", "health": f"{h}:health", "package": PACKAGE}
    args.update(overrides)
    return Page(**args)

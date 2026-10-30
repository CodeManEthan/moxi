"""Drive the shell's request path in process, and record `/proc/net/tcp` text.

No listener is involved: a test builds a scope as uvicorn's h11 protocol does and runs it
through `worker_layer(guard(prove(router), profile))` with the shell's real profile.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from moxi_loopback.api import Page, app, guard, prove

from moxi_ui.router import Router, shell_profile, shell_table, worker_layer
from moxi_ui.session import OwnerCheck, Sessions, Tickets, open_store

TOKEN = "T" * 43
NONCE = "N" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321
HOST = b"127.0.0.1:%d" % PORT
CLIENT = ("127.0.0.1", 50000)
SERVER = ("127.0.0.1", PORT)

#: A module's export as `moxi_loopback.export` prints it: a read, an act, a stream and
#: two assets.
FIXTURE_EXPORT = {
    "format": 1,
    "routes": [
        {"path": "/api/summary", "kind": "read"},
        {"path": "/api/refresh", "kind": "act"},
        {"path": "/api/events", "kind": "read", "stream": True},
    ],
    "assets": ["index.html", "page.js"],
}


def scope(method="GET", path="/", *, headers=(), host=HOST, client=CLIENT, server=SERVER,
          query=b""):
    raw = path.encode("latin-1") if isinstance(path, str) else path
    pairs = [(b"host", host)] if host is not None else []
    pairs.extend((k.lower(), v) for k, v in headers)
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "server": server,
        "client": client,
        "scheme": "http",
        "method": method,
        "root_path": "",
        "path": raw.decode("latin-1"),
        "raw_path": raw,
        "query_string": query,
        "headers": pairs,
        "state": {},
    }


@dataclass
class Answer:
    status: int = 0
    headers: list = field(default_factory=list)
    body: bytes = b""

    def header(self, name):
        name = name.lower().encode()
        found = [v.decode() for k, v in self.headers if k.lower() == name]
        return found[0] if len(found) == 1 else (found or None)

    def json(self):
        return json.loads(self.body)


async def _drive(asgi, sc, body):
    answer = Answer()
    queue = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        if queue:
            return queue.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            answer.status = message["status"]
            answer.headers = list(message["headers"])
        elif message["type"] == "http.response.body":
            answer.body += message.get("body", b"")

    await asgi(sc, receive, send)
    return answer


class Recorder:
    """A stand-in for the proxy, the push or the shell's page: answers 299 and keeps each
    scope it got."""

    def __init__(self, name):
        self.name = name
        self.scopes = []

    async def __call__(self, sc, receive, send):
        self.scopes.append(sc)
        await send({"type": "http.response.start", "status": 299, "headers": []})
        await send({"type": "http.response.body", "body": self.name.encode()})


class Harness:
    """One shell's request path over a store in `state_dir`, with recorders for the proxy
    and the push, and loopback's real in-process app for the shell's own page."""

    def __init__(self, state_dir: Path, *, owner: OwnerCheck | None = None, epoch=None,
                 page=None, names=("127.0.0.1", "localhost")):
        self.store = open_store(state_dir, epoch)
        self.tickets = Tickets(state_dir / "tickets")
        self.sessions = Sessions(self.store, self.tickets, owner=owner or OwnerCheck(False),
                                 token=lambda: TOKEN)
        self.proxy = Recorder("proxy")
        self.push = Recorder("push")
        self.page = page or app(Page(routes=()), module="ui", server_id=SERVER_ID,
                                prefix="/m/ui")
        self.router = Router(self.sessions, page=self.page, push=self.push, proxy=self.proxy)
        table = shell_table(["fixture"], {"fixture": FIXTURE_EXPORT})
        self.profile = shell_profile(table, self.sessions, names)
        self.asgi = worker_layer(guard(prove(self.router, token=TOKEN, server_id=SERVER_ID),
                                       lambda: self.profile))

    def send(self, method="GET", path="/", *, headers=(), body=b"", **kw):
        return asyncio.run(_drive(self.asgi, scope(method, path, headers=headers, **kw), body))

    def sign_in(self):
        """A live session: `(cookie header, key header)`."""
        session_id, key = self.store.mint()
        return (b"cookie", b"moxi-ui=" + session_id.encode()), (b"x-moxi-key", key.encode())


# Browser-like header sets.
FETCH = [(b"sec-fetch-site", b"same-origin"), (b"sec-fetch-mode", b"cors"),
         (b"sec-fetch-dest", b"empty")]
NAVIGATE = [(b"sec-fetch-site", b"none"), (b"sec-fetch-mode", b"navigate"),
            (b"sec-fetch-dest", b"document")]
ACT = [(b"origin", b"http://" + HOST), (b"sec-fetch-site", b"same-origin"),
       (b"content-type", b"application/json")]
WORKER = [(b"service-worker", b"script"), (b"sec-fetch-site", b"same-origin"),
          (b"sec-fetch-mode", b"same-origin"), (b"sec-fetch-dest", b"serviceworker")]


# --- recorded /proc/net/tcp -------------------------------------------------------------


def proc_address(host: str, port: int) -> str:
    raw = ipaddress.ip_address(host).packed
    words = "".join("%08X" % int.from_bytes(raw[i:i + 4], sys.byteorder)
                    for i in range(0, len(raw), 4))
    return f"{words}:{port:04X}"


def proc_row(n: int, local, remote, uid: int) -> str:
    return (f"{n:4d}: {proc_address(*local)} {proc_address(*remote)} 01 00000000:00000000 "
            f"00:00000000 00000000 {uid:5d}        0 {1000 + n} 1 0000000000000000 20 4 30 10 -1")


_HEAD = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
         "   uid  timeout inode")


def record_proc(root: Path, *, tcp=(), tcp6=()) -> Path:
    """A `/proc` with `net/tcp` and `net/tcp6` holding these `(local, remote, uid)` rows."""
    (root / "net").mkdir(parents=True, exist_ok=True)
    for name, rows in (("tcp", tcp), ("tcp6", tcp6)):
        lines = [_HEAD] + [proc_row(n, *row) for n, row in enumerate(rows)]
        (root / "net" / name).write_text("\n".join(lines) + "\n", encoding="ascii")
    return root


def connection_rows(client, server, uid):
    """Both ends of one loopback connection: the client's socket owned by `uid`, the
    server's accepted socket by this user."""
    return [(client, server, uid), (server, client, os.getuid())]

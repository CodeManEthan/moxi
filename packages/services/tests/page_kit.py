"""services' page server in process, for the page's tests: loopback reached as the page
reaches it, the strict app behind `guard` with an upstream profile, one request sent to
it, and one open stream on it."""

from __future__ import annotations

import asyncio
import json

from moxi import modules
from moxi_services import page

TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321


def loopback():
    found = modules.load("loopback", interface=1)
    assert found.ok, found.reason
    return found.api


LB = loopback()


def served():
    inner = LB.app(page.PAGE, module="services", server_id=SERVER_ID, strict=True)
    profile = LB.upstream(LB.build_table(page.PAGE)[0], token=TOKEN, port=PORT)
    return LB.guard(inner, lambda: profile)


def scope(method: str, path: str, query: str = "",
          headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "server": ("127.0.0.1", PORT),
        "client": ("127.0.0.1", 50000), "scheme": "http", "method": method,
        "root_path": "", "path": path, "raw_path": path.encode("ascii"),
        "query_string": query.encode(),
        "headers": [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode()),
                    *(headers or [])],
        "state": {},
    }


async def send(app, method: str, path: str, *, query: str = "", body: dict | None = None,
               key: str | None = None) -> tuple[int, dict | None]:
    """One request to `app`: its status and its JSON body."""
    headers = []
    raw = b""
    if method == "POST":
        raw = json.dumps(body or {}).encode()
        headers += [(b"content-type", b"application/json"),
                    (b"content-length", str(len(raw)).encode())]
    if key is not None:
        headers.append((b"idempotency-key", key.encode()))
    sent = {"status": 0, "body": b""}
    asked = [False]

    async def receive():
        if not asked[0]:
            asked[0] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)

    async def answer(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    await app(scope(method, path, query, headers), receive, answer)
    return sent["status"], json.loads(sent["body"]) if sent["body"] else None


class Stream:
    """One open stream on the app: its events as `(name, data)`, heartbeats left out."""

    def __init__(self, app, query: str = "topics=rows"):
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

        async def answer(message):
            if message["type"] == "http.response.start":
                self.status = message["status"]
            elif message["type"] == "http.response.body":
                await self._chunks.put(message.get("body", b""))

        self.task = asyncio.get_running_loop().create_task(
            app(scope("GET", "/api/events", query), receive, answer))

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

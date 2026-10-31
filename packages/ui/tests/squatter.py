"""A squatter: another program holding the shell's port before the shell does, as another
user on the machine could. It serves pages that leave code and data in the origin's
browser state, for the tests that check what the shell does about them.

It reads request heads of any size (the longest script URL an engine registers, with a
full cookie jar), answers each request and closes the connection, so nothing of it
outlasts `stop()` in the browser's connection pool.

- `/plant?kind=deep|percent|long&n=N` registers a worker with scope `/` from
  `/m/ui/x/y/sw.js`, `/m/ui/a%20b/sw.js` or `/m/ui/<N a's>/sw.js`; its title becomes
  `planted` once the worker is active, or `refused: <error>`.
- a script request (`Service-Worker: script`) gets the worker, with
  `Service-Worker-Allowed: /`: it takes every navigation it controls and answers it with a
  page titled `squatter`.
- `/cookies?k=K&s=S` sets K cookies of S bytes each with `document.cookie`; title `filled`.
- `/storage` puts `squat=1` in `localStorage`; title `stored`.
- `/cached` is a page titled `squatter cached` that may be cached for a year.
"""

from __future__ import annotations

import asyncio
import threading
from urllib.parse import urlsplit

#: The largest request head it reads.
HEAD_LIMIT = 64 * 1024 * 1024

WORKER = b"""
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  if (event.request.mode === "navigate") {
    event.respondWith(new Response("<!doctype html><title>squatter</title><p>squatted</p>",
                                   {headers: {"Content-Type": "text/html"}}));
  }
});
"""

PLANT = """<!doctype html><title>planting</title><script>
(async () => {
  const q = new URLSearchParams(location.search);
  const kind = q.get("kind");
  const path = kind === "deep" ? "/m/ui/x/y/sw.js"
    : kind === "percent" ? "/m/ui/a%20b/sw.js"
    : "/m/ui/" + "a".repeat(Number(q.get("n"))) + "/sw.js";
  try {
    const reg = await navigator.serviceWorker.register(path, {scope: "/"});
    const worker = reg.installing ?? reg.waiting ?? reg.active;
    if (worker.state !== "activated") {
      await new Promise((done) => worker.addEventListener("statechange",
        () => { if (worker.state === "activated") done(); }));
    }
    document.title = "planted";
  } catch (err) {
    document.title = "refused: " + err;
  }
})();
</script>"""

COOKIES = """<!doctype html><title>filling</title><script>
const q = new URLSearchParams(location.search);
const k = Number(q.get("k")), s = Number(q.get("s"));
for (let i = 0; i < k; i++) {
  const name = "q" + i;
  document.cookie = name + "=" + "x".repeat(s - name.length - 1) + "; path=/; max-age=3600";
}
document.title = "filled";
</script>"""

STORAGE = """<!doctype html><title>storing</title><script>
localStorage.setItem("squat", "1");
document.title = "stored";
</script>"""

CACHED = "<!doctype html><title>squatter cached</title><p>from the squatter</p>"


class Squatter:
    """The squatter's server on `127.0.0.1:<port>` (0 for one the system picks), in a
    thread of its own. Use it as a context manager, or `start()` and `stop()`."""

    def __init__(self, port: int = 0):
        self.port = port
        self.requests: list[dict] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.Server | None = None
        self._thread: threading.Thread | None = None
        #: Connections still open, closed by `stop()` (`Server.close_clients` is 3.13's).
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> Squatter:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def start(self) -> Squatter:
        ready = threading.Event()

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            self._server = self._loop.run_until_complete(asyncio.start_server(
                self._serve, "127.0.0.1", self.port, limit=HEAD_LIMIT))
            self.port = self._server.sockets[0].getsockname()[1]
            ready.set()
            self._loop.run_forever()
            self._loop.close()

        self._thread = threading.Thread(target=run, name="squatter", daemon=True)
        self._thread.start()
        if not ready.wait(10):
            raise RuntimeError("the squatter did not start")
        return self

    def stop(self) -> None:
        """Close the listener and every open connection, freeing the port."""
        loop, server = self._loop, self._server
        if loop is None or server is None:
            return

        async def close() -> None:
            server.close()
            for writer in list(self._writers):
                writer.close()
            await server.wait_closed()

        asyncio.run_coroutine_threadsafe(close(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(10)
        self._loop = self._server = None

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            await self._exchange(reader, writer)
        finally:
            self._writers.discard(writer)

    async def _exchange(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        line, *rest = head.decode("latin-1").split("\r\n")
        method, target, _ = (line.split(" ", 2) + ["", ""])[:3]
        headers = {}
        for item in rest:
            name, sep, value = item.partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()
        self.requests.append({"method": method, "target": target, "head": len(head),
                              "worker": headers.get("service-worker") == "script"})
        status, kind, body, extra = self._answer(target, headers)
        lines = [f"HTTP/1.1 {status}", f"Content-Type: {kind}", f"Content-Length: {len(body)}",
                 "Connection: close", *extra]
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
        try:
            await writer.drain()
        except ConnectionError:
            pass
        writer.close()

    def _answer(self, target: str, headers: dict) -> tuple[str, str, bytes, list[str]]:
        page = urlsplit(target).path
        html = "text/html; charset=utf-8"
        if headers.get("service-worker") == "script":
            return ("200 OK", "text/javascript", WORKER,
                    ["Service-Worker-Allowed: /", "Cache-Control: no-store"])
        if page == "/plant":
            return "200 OK", html, PLANT.encode(), ["Cache-Control: no-store"]
        if page == "/cookies":
            return "200 OK", html, COOKIES.encode(), ["Cache-Control: no-store"]
        if page == "/storage":
            return "200 OK", html, STORAGE.encode(), ["Cache-Control: no-store"]
        if page == "/cached":
            return "200 OK", html, CACHED.encode(), ["Cache-Control: max-age=31536000"]
        return "404 Not Found", "text/plain", b"", []


"""The runner's chain served by `serve` on a Unix socket, so each answer goes out through
uvicorn's h11 protocol: the header set on every status the base sends, both 413s, and a
stream the client drops.

The other server tests drive the ASGI app directly; these check that nothing between the
app and the socket loses a header or a refusal.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading

import pytest
from moxi_dev.testing import short_dir, wait_for

import asgi_kit
from asgi_kit import SERVER_ID, TOKEN, Answer, assert_header_set
from moxi_ui.loopback import server
from moxi_ui.loopback.check import upstream
from moxi_ui.loopback.serve import serve
from moxi_ui.loopback.server import app, guard, prove
from moxi_ui.loopback.transport import bind_unix

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets")


class Wire:
    """One page server for the module: `serve` on its own loop, in a thread."""

    def __init__(self, path):
        self.path = str(path)
        sock = bind_unix(self.path)
        self.inner = app(asgi_kit.make_page(), module="lbwire", server_id=SERVER_ID,
                         version="9.9")
        profile = upstream(self.inner.table, token=TOKEN)
        chain = guard(prove(self.inner, token=TOKEN, server_id=SERVER_ID), lambda: profile)
        self.ready = threading.Event()
        self.loop = None
        self.should_exit = None
        self.error = None

        async def main():
            self.loop = asyncio.get_running_loop()
            self.should_exit = asyncio.Event()
            self.ready.set()
            await serve(chain, [sock], should_exit=self.should_exit)

        def run():
            try:
                asyncio.run(main())
            except BaseException as err:  # noqa: BLE001 - raised again at the stop
                self.error = err
                self.ready.set()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        assert self.ready.wait(5)

    def stop(self):
        if self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.should_exit.set)
        self.thread.join(10)
        assert not self.thread.is_alive(), "serve did not return"
        if self.error is not None:
            raise self.error

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(self.path)
        return sock


@pytest.fixture(scope="module")
def wire(page_package):
    with short_dir() as directory:
        served = Wire(directory / "page.sock")
        try:
            yield served
        finally:
            served.stop()


def send(sock, method, path, *, token=True, headers=(), body=b""):
    lines = [f"{method} {path} HTTP/1.1".encode(), b"Host: localhost"]
    if token:
        lines.append(b"X-Moxi-Token: " + TOKEN.encode())
    lines.extend(name + b": " + value for name, value in headers)
    sock.sendall(b"\r\n".join(lines) + b"\r\n\r\n" + body)


def answer(sock, *, stream=False):
    """One response as an `Answer`: its head, and its body by Content-Length unless it
    is a stream."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        assert chunk, f"the connection closed after {data!r}"
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    headers = []
    for line in lines[1:]:
        name, _, value = line.partition(b":")
        headers.append((name.strip().lower(), value.strip()))
    got = Answer(status=int(lines[0].split()[1]), headers=headers)
    if not stream:
        length = int(got.header("content-length") or 0)
        while len(rest) < length:
            chunk = sock.recv(65536)
            if not chunk:
                break
            rest += chunk
        got.body = rest
    return got


def one(wire, method, path, **kwargs):
    with wire.connect() as sock:
        send(sock, method, path, **kwargs)
        return answer(sock)


JSON = (b"Content-Type", b"application/json")


def test_a_refusal_a_404_and_a_500_carry_the_header_set(wire, pages):
    refused = one(wire, "GET", "/api/summary", token=False)
    assert refused.status == 403 and refused.body == b""
    assert_header_set(refused)
    missing = one(wire, "GET", "/api/nothing")
    assert missing.status == 404 and missing.body == b""
    assert_header_set(missing)
    failed = one(wire, "GET", "/api/boom")
    assert failed.status == 500 and failed.body == b""
    assert_header_set(failed)
    served = one(wire, "GET", "/app.js")
    assert served.status == 200
    assert_header_set(served, cache="no-cache")


def test_a_content_length_over_the_cap_is_a_413_with_the_header_set(wire, pages):
    got = one(wire, "POST", "/api/big", headers=[JSON, (b"Content-Length", b"999999")])
    assert got.status == 413 and got.body == b""
    assert_header_set(got)
    assert pages.CALLS == []


def test_a_chunked_body_over_the_cap_is_a_413_and_the_handler_never_runs(wire, pages):
    body = b'{"text": "' + b"a" * 10 + b'"}'
    chunked = b"".join(b"%x\r\n%s\r\n" % (len(part), part)
                       for part in (body[:8], body[8:16], body[16:])) + b"0\r\n\r\n"
    got = one(wire, "POST", "/api/big",
              headers=[JSON, (b"Transfer-Encoding", b"chunked")], body=chunked)
    assert got.status == 413 and got.body == b""
    assert_header_set(got)
    assert pages.CALLS == []
    # The same body under the cap is read whole from its chunks.
    small = b'{"text":"abcde"}'
    got = one(wire, "POST", "/api/big", headers=[JSON, (b"Transfer-Encoding", b"chunked")],
              body=b"%x\r\n%s\r\n0\r\n\r\n" % (len(small), small))
    assert got.status == 200
    assert pages.CALLS == [("big", {"text": "abcde"})]


def test_a_dropped_stream_is_uncounted_and_the_stream_cap_answers_503(wire, pages):
    activity = wire.inner.activity
    streams = []
    try:
        for _ in range(server.STREAM_CAP):
            sock = wire.connect()
            streams.append(sock)
            send(sock, "GET", "/api/events")
            opened = answer(sock, stream=True)
            assert opened.status == 200
            assert opened.header("content-type").startswith("text/event-stream")
        wait_for(lambda: activity.streams == server.STREAM_CAP, 2, what="every stream counted")
        over = one(wire, "GET", "/api/events")
        assert over.status == 503
        assert over.header("retry-after") == "1"
        assert_header_set(over)
    finally:
        for sock in streams:
            sock.close()
    wait_for(lambda: activity.streams == 0, 2, what="the dropped streams uncounted")
    wait_for(lambda: activity.quiet() is not None, 2, what="the quiet run restarted")

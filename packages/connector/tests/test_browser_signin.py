"""The sign-in page in a real browser: Playwright's Chromium, headless, on loopback.

The door runs as a daemon on loopback. Its origin is `https://door.example.net:<port>`, so a
TLS relay with a certificate made here sits in front of it, and a stand-in for Claude's
callback answers on `https://callback.example.net:<port>/cb`. Chromium maps both names to
127.0.0.1 and accepts the certificate. The test opens the page, posts a wrong code and then
a right one through the form, and follows the 303 to the callback. A header the browser
enforces (the CSP's `form-action` on the redirect, `Origin` on the form's POST) fails here
when a fake client can't see it.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import ssl
import threading
from pathlib import Path
from urllib.parse import urlencode

import pytest
from kit import HTTPX, Running, echo_tool, http, provider
from signin_kit import pkce, query_of

from moxi_connector import grants
from moxi_connector.codes import mint_method
from moxi_connector.oauth import OAuth
from moxi_connector.origin import Origin
from moxi_connector.provider import Gate

pytestmark = pytest.mark.browser

DOOR = "door.example.net"
BACK = "callback.example.net"

#: Where Playwright keeps its browsers, read at import, before a test's fence points HOME
#: and the XDG homes into its tmp: Playwright's own default, unless the run names one.
BROWSERS = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(
    Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright")


def certificate(where):
    """A self-signed certificate for both names: (cert path, key path)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, DOOR)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(DOOR),
                                                        x509.DNSName(BACK)]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = where / "cert.pem", where / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


class Edge:
    """On a loop of its own: a TLS relay to the door's loopback port, and the stand-in
    callback, which keeps the path and query of each request it gets."""

    def __init__(self, cert, key):
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.load_cert_chain(cert, key)
        self.target = None
        self.seen: list[str] = []
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.relay, self.relay_port = self._serve(self._relay)
        self.back, self.back_port = self._serve(self._callback)

    def _serve(self, handler):
        async def start():
            server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=self.tls)
            return server, server.sockets[0].getsockname()[1]
        return asyncio.run_coroutine_threadsafe(start(), self.loop).result(10)

    async def _relay(self, reader, writer):
        try:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", self.target)
        except OSError:
            writer.close()
            return

        async def pipe(src, dst):
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, ssl.SSLError):
                pass
            finally:
                try:
                    dst.close()
                except Exception:  # noqa: BLE001
                    pass

        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))

    async def _callback(self, reader, writer):
        try:
            line = await reader.readline()
            while (await reader.readline()).strip():
                pass
            self.seen.append(line.decode().split(" ")[1])
            body = b"<!doctype html><title>Signed in</title><p>Signed in.</p>"
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n"
                         + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            await writer.drain()
        except (ConnectionError, ssl.SSLError, IndexError):
            pass
        finally:
            writer.close()

    def close(self):
        async def stop():
            for server in (self.relay, self.back):
                server.close()
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)


def test_a_real_browser_signs_in_through_the_page(make_host, fake, tmp_path, monkeypatch):
    from moxi_connector import checks
    from playwright.sync_api import expect, sync_playwright

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", BROWSERS)

    edge = Edge(*certificate(tmp_path))
    try:
        origin = Origin(DOOR, edge.relay_port)
        callback = f"https://{BACK}:{edge.back_port}/cb"
        allowed = Gate(peer=lambda peer: None, shell=lambda env: None)
        host = asyncio.run(make_host(providers={
            "demo": provider(fake, echo_tool(fake), gate=allowed)}))
        oauth = OAuth(grants.Store(), host.calllog, redirect_uris=(callback,))
        host.signin, host.redirect_uris = oauth, (callback,)
        with Running(host, origin,
                     methods={"mint_code": mint_method(oauth, host.registry)}) as d:
            edge.target = d.port
            at = f"{DOOR}:{edge.relay_port}"
            status, _, body = http(d.port, "POST", "/register", host=at, headers={
                "User-Agent": HTTPX, "Content-Type": "application/json"},
                body=json.dumps({"redirect_uris": [callback], "client_name": "Claude",
                                 "token_endpoint_auth_method": "none"}).encode())
            assert status == 201, body
            client = json.loads(body)
            verifier, challenge = pkce()
            url = origin.authorization_endpoint + "?" + urlencode({
                "client_id": client["client_id"], "redirect_uri": callback,
                "response_type": "code", "code_challenge": challenge,
                "code_challenge_method": "S256", "state": "browser-state"})
            typed = checks.ask("mint_code", {"label": "browser"})["code"]

            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=[
                    f"--host-resolver-rules=MAP {DOOR} 127.0.0.1, MAP {BACK} 127.0.0.1"])
                try:
                    context = browser.new_context(ignore_https_errors=True)
                    page = context.new_page()
                    page.set_default_timeout(5_000)
                    blocked = []
                    page.on("console", lambda m: blocked.append(m.text)
                            if "Content Security Policy" in m.text else None)
                    posts = []
                    page.on("request", lambda r: posts.append(r.all_headers())
                            if r.method == "POST" else None)

                    answer = page.goto(url)
                    assert answer.status == 200
                    heads = answer.all_headers()
                    assert heads["cache-control"] == "no-store"
                    assert heads["referrer-policy"] == "same-origin"
                    assert heads["x-content-type-options"] == "nosniff"
                    assert (f"form-action 'self' https://{BACK}:{edge.back_port}; "
                            "frame-ancestors 'none'") in heads["content-security-policy"]
                    expect(page.get_by_text("the name the client gave itself")).to_be_visible()

                    # A wrong code: the form comes back with the tries left.
                    page.get_by_label("Code").fill("WRONG-CODE")
                    page.get_by_role("button", name="Sign in").click()
                    expect(page.get_by_role("alert")).to_contain_text("4 tries left")

                    # The right one, typed sloppily: the 303 reaches the callback.
                    page.get_by_label("Code").fill(typed.lower().replace("-", " "))
                    page.get_by_role("button", name="Sign in").click()
                    page.wait_for_url(f"{callback}?**")
                    expect(page.get_by_text("Signed in.")).to_be_visible()
                    assert blocked == []
                finally:
                    browser.close()

            # The browser's own POSTs carried what the page checks.
            assert len(posts) == 2
            for sent in posts:
                assert sent["origin"] == origin.page_origin
                assert sent["sec-fetch-site"] == "same-origin"
            back = query_of(next(p for p in edge.seen if p.startswith("/cb")))
            assert back["state"] == "browser-state" and back["iss"] == origin.issuer
            status, _, body = http(d.port, "POST", "/token", host=at, headers={
                "User-Agent": HTTPX, "Content-Type": "application/x-www-form-urlencoded"},
                body=urlencode({"grant_type": "authorization_code", "code": back["code"],
                                "redirect_uri": callback, "client_id": client["client_id"],
                                "code_verifier": verifier}).encode())
            assert status == 200, body
            assert oauth.codes.count() == 0
        label = next(iter(oauth.store.view()["grants"].values()))["label"]
        assert label == "browser"
        assert [r["tries"] for r in host.calllog.rows if r.get("kind") == "code-miss"] == [4]
    finally:
        edge.close()

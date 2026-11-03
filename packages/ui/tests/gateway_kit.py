"""What the gateway's sign-in tests share: a fake passkey authenticator, a clock, an audit
recorder, and a request through loopback's `guard` on the gateway's profile, in process."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from moxi_loopback.api import Table, gateway, guard
from moxi_ui.gateway.access import COOKIE, Access
from moxi_ui.gateway.address import Kept

HOST = "gw.example.test"
ORIGIN = f"https://{HOST}"
KEPT = Kept("r1.1", HOST, 443, "cloudflare")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@dataclass
class Audit:
    rows: list = field(default_factory=list)

    def __call__(self, code: str, fields: dict[str, Any]) -> None:
        self.rows.append((code, dict(fields)))

    def codes(self) -> list[str]:
        return [code for code, _ in self.rows]


class Authenticator:
    """A platform authenticator: one P-256 passkey, made by `create` and used by `get`,
    with no attestation. `keep_count` False is a synced passkey, whose count stays 0."""

    def __init__(self, *, keep_count: bool = True):
        from cryptography.hazmat.primitives.asymmetric import ec

        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.keep_count = keep_count
        self.count = 0
        self.user_id = b""

    def _cose(self) -> bytes:
        from webauthn.helpers.encode_cbor import encode_cbor

        numbers = self.key.public_key().public_numbers()
        return encode_cbor({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"),
                            -3: numbers.y.to_bytes(32, "big")})

    @staticmethod
    def _flags(uv: bool, attested: bool = False) -> bytes:
        return bytes([0x01 | (0x04 if uv else 0) | (0x40 if attested else 0)])

    def create(self, options: dict, *, origin: str = ORIGIN, rp_id: str | None = None,
               uv: bool = True) -> dict:
        from webauthn.helpers.encode_cbor import encode_cbor

        rp_id = rp_id or options["rp"]["id"]
        self.user_id = unb64(options["user"]["id"])
        client = json.dumps({"type": "webauthn.create", "challenge": options["challenge"],
                             "origin": origin, "crossOrigin": False}).encode()
        auth = (hashlib.sha256(rp_id.encode()).digest() + self._flags(uv, attested=True)
                + self.count.to_bytes(4, "big") + bytes(16)
                + len(self.credential_id).to_bytes(2, "big") + self.credential_id
                + self._cose())
        attestation = encode_cbor({"fmt": "none", "attStmt": {}, "authData": auth})
        return {"id": b64(self.credential_id), "rawId": b64(self.credential_id),
                "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64(client),
                             "attestationObject": b64(attestation)}}

    def get(self, options: dict, *, origin: str = ORIGIN, rp_id: str | None = None,
            uv: bool = True, count: int | None = None, challenge: str | None = None) -> dict:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        rp_id = rp_id or options["rpId"]
        if count is None:
            if self.keep_count:
                self.count += 1
            count = self.count
        client = json.dumps({"type": "webauthn.get",
                             "challenge": challenge or options["challenge"],
                             "origin": origin, "crossOrigin": False}).encode()
        auth = (hashlib.sha256(rp_id.encode()).digest() + self._flags(uv)
                + count.to_bytes(4, "big"))
        signature = self.key.sign(auth + hashlib.sha256(client).digest(),
                                  ec.ECDSA(hashes.SHA256()))
        return {"id": b64(self.credential_id), "rawId": b64(self.credential_id),
                "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64(client), "authenticatorData": b64(auth),
                             "signature": b64(signature), "userHandle": b64(self.user_id)}}


async def _not_found(scope, receive, send):
    await send({"type": "http.response.start", "status": 404, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"route"})


@dataclass
class Answer:
    status: int
    headers: list
    body: bytes

    def header(self, name: str) -> list[str]:
        return [v.decode("latin-1") for k, v in self.headers if k.decode().lower() == name]

    def json(self) -> Any:
        return json.loads(self.body)


class Phone:
    """An `Access` behind loopback's `guard` on a gateway profile for `KEPT`, with a table
    that also holds a route, an asset and a confirmed act's path, as the full table would."""

    def __init__(self, tmp_path, *, kept: Kept | None = KEPT, clock: Clock | None = None,
                 pages: dict[str, bytes] | None = None):
        self.clock = clock or Clock()
        self.audit = Audit()
        self.kept = kept
        self.state_dir = tmp_path / "state"
        self.runtime_dir = tmp_path / "run"
        self.runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        files = pages if pages is not None else {
            "pair.html": b"<!doctype html><title>pair</title>",
            "signin.html": b"<!doctype html><title>signin</title>",
            "pair.js": b"// pair", "signin.js": b"// signin"}
        self.access = Access(state_dir=self.state_dir, runtime_dir=self.runtime_dir,
                             kept=lambda: self.kept, audit=self.audit,
                             pages=files.get, clock=self.clock)
        table = self.access.add_entries(Table())
        table.add("GET", "/m/fixture/api/read", "read")
        table.add("GET", "/m/fixture/app.js", "asset")
        self.profile = gateway(table, session=self.access, host=HOST, port=443)
        self.app = guard(self.access.layer(_ok), lambda: self.profile)
        self.cookie: str | None = None
        self.key: str | None = None

    def request(self, method: str, path: str, body: Any = None, *, cookie: bool = True,
                key: bool = True, headers: tuple = (), origin: str | None = ORIGIN,
                navigate: bool = False) -> Answer:
        data = b"" if body is None else json.dumps(body).encode()
        pairs = [("host", HOST)]
        if method != "GET" and origin is not None:
            pairs += [("origin", origin), ("content-type", "application/json")]
        pairs.append(("sec-fetch-site", "same-origin"))
        if navigate:
            pairs += [("sec-fetch-mode", "navigate"), ("sec-fetch-dest", "document")]
        if cookie and self.cookie:
            pairs.append(("cookie", f"{COOKIE}={self.cookie}"))
        if key and self.key:
            pairs.append(("x-moxi-key", self.key))
        pairs += list(headers)
        return asyncio.run(_call(self.app, method, path, data, pairs))

    def repeat(self, count: int, method: str, path: str, body: Any = None) -> list[int]:
        """Send one request `count` times, with no session, on one event loop; the
        statuses."""
        data = b"" if body is None else json.dumps(body).encode()
        pairs = [("host", HOST), ("origin", ORIGIN), ("content-type", "application/json"),
                 ("sec-fetch-site", "same-origin")]

        async def run() -> list[int]:
            return [(await _call(self.app, method, path, data, pairs)).status
                    for _ in range(count)]

        return asyncio.run(run())

    def keep(self, answer: Answer) -> None:
        """Keep the cookie and key a sign-in answer set, as the phone's page does."""
        (cookie,) = answer.header("set-cookie")
        self.cookie = cookie.split(";", 1)[0].split("=", 1)[1]
        self.key = answer.json()["key"]

    def mint(self) -> str:
        """A pairing code, as the desktop or the CLI mints one."""
        return self.access.tickets.mint()

    def pair(self, authenticator: Authenticator | None, *, name: str = "Pixel",
             keep: bool = True) -> Answer:
        begun = self.request("POST", "/api/pair/begin", {"code": self.mint(), "name": name})
        assert begun.status == 200, begun
        data = begun.json()
        credential = authenticator.create(data["options"]) if authenticator else None
        done = self.request("POST", "/api/pair/finish",
                            {"pairing": data["pairing"], "credential": credential})
        if keep and done.status == 200:
            self.keep(done)
        return done

    def sign_in(self, authenticator: Authenticator, *, keep: bool = True, **kwargs) -> Answer:
        begun = self.request("POST", "/api/signin/begin", {})
        assert begun.status == 200, begun
        assertion = authenticator.get(begun.json()["options"], **kwargs)
        done = self.request("POST", "/api/signin/finish", {"credential": assertion})
        if keep and done.status == 200:
            self.keep(done)
        return done


async def _call(app, method, path, body, headers) -> Answer:
    raw, _, query = path.partition("?")
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": raw, "raw_path": raw.encode(),
             "query_string": query.encode(), "scheme": "http", "http_version": "1.1",
             "headers": [(k.encode(), v.encode()) for k, v in headers],
             "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8461)}
    await app(scope, receive, send)
    start = sent[0]
    return Answer(start["status"], start["headers"],
                  b"".join(m.get("body", b"") for m in sent[1:]))

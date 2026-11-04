"""A fake Claude client for the sign-in: it registers, takes the page's entry in place of
the page, redeems codes at `/token`, refreshes, and calls `/mcp` as Claude-User."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from kit import CLAUDE, HTTPX, initialize, request

from moxi_connector.app import Front, build_app
from moxi_connector.oauth import OAuth
from moxi_connector.origin import Origin

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
CALLBACKS = (CALLBACK, "https://claude.com/api/mcp/auth_callback")


class Clock:
    """Seconds since the epoch, moved by hand."""

    def __init__(self):
        self.t = time.time()

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


class Door:
    """The door with the real sign-in, and a fake Claude client that talks to it."""

    def __init__(self, front: Front, oauth: OAuth, clock: Clock):
        self.front = front
        self.oauth = oauth
        self.clock = clock
        self.host = front.host

    @property
    def origin(self) -> Origin:
        return self.front.origin

    @property
    def rows(self):
        return self.host.calllog.rows

    async def swap(self, origin: Origin | None) -> None:
        await self.front.swap(build_app(origin, self.host))

    async def close(self) -> None:
        await self.front.close()

    def at(self, origin=None) -> dict:
        o = origin or self.origin
        return {"host": o.host if o.port == 443 else f"{o.host}:{o.port}"}

    async def register(self, *, redirect_uris=(CALLBACK,), method="client_secret_post",
                       name="Claude", origin=None):
        body = {"redirect_uris": list(redirect_uris), "client_name": name,
                "token_endpoint_auth_method": method,
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"]}
        return await request(self.front, "POST", "/register", body, user_agent=HTTPX,
                             **self.at(origin))

    async def client(self, **kw) -> dict:
        got = await self.register(**kw)
        assert got.status == 201, got.body
        return got.json()

    def page(self, client: dict, *, label="phone", resource=None, under=None):
        """The page's entry, in place of the page: check, keep, and issue. Returns the
        verifier and the redirect's query."""
        under = under or self.oauth.current
        verifier, challenge = pkce()
        params = {"client_id": client["client_id"], "redirect_uri": CALLBACK,
                  "response_type": "code", "code_challenge": challenge,
                  "code_challenge_method": "S256", "state": "st-" + secrets.token_hex(4)}
        if resource is not None:
            params["resource"] = resource
        pid = under.open(under.check(params))
        url = under.issue(pid, label=label)
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == CALLBACK
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert query["state"] == params["state"]
        return verifier, query

    async def token(self, client: dict, code: str, verifier: str, *, origin=None,
                    redirect_uri=CALLBACK):
        form = {"grant_type": "authorization_code", "code": code,
                "redirect_uri": redirect_uri, "client_id": client["client_id"],
                "code_verifier": verifier}
        if client.get("client_secret"):
            form["client_secret"] = client["client_secret"]
        return await request(self.front, "POST", "/token", form=form, user_agent=HTTPX,
                             **self.at(origin))

    async def refresh(self, client: dict, refresh: str, *, origin=None):
        form = {"grant_type": "refresh_token", "refresh_token": refresh,
                "client_id": client["client_id"]}
        if client.get("client_secret"):
            form["client_secret"] = client["client_secret"]
        return await request(self.front, "POST", "/token", form=form, user_agent=HTTPX,
                             **self.at(origin))

    async def sign_in(self, client: dict | None = None, **kw):
        client = client or await self.client()
        verifier, query = self.page(client, **kw)
        got = await self.token(client, query["code"], verifier)
        assert got.status == 200, got.body
        return client, got.json()

    async def mcp(self, access: str | None, body=None, *, origin=None, headers=()):
        auth = [("authorization", f"Bearer {access}")] if access else []
        return await request(self.front, "POST", "/mcp", body or initialize(),
                             headers=auth + list(headers), user_agent=CLAUDE,
                             **self.at(origin))

    def grant_id(self) -> str:
        live = [gid for gid, g in self.oauth.store.view()["grants"].items()
                if not g.get("revoked")]
        assert len(live) == 1, live
        return live[0]

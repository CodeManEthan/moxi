"""The sign-in: OAuth 2.1 the way Claude's connector service needs it.

`OAuth` is the door's `moxi_connector.signin.SignIn`. For each origin O it mounts
(`OAuth.mount(origin)`), it serves these paths, each by one handler, every value from O:

| path | methods | served by |
|---|---|---|
| `/.well-known/oauth-protected-resource/mcp` | GET | this module: `resource` O/mcp, `authorization_servers` [O], `bearer_methods_supported` ["header"] |
| `/.well-known/oauth-authorization-server` | GET | the SDK's `MetadataHandler`, with the metadata built here from O |
| `/register` | POST | the SDK's `RegistrationHandler`, with this module's provider |
| `/authorize` | GET, POST | the sign-in page, `OAuth.page(under)`; the SDK's `AuthorizationHandler` is never mounted |
| `/token` | POST | the SDK's `TokenHandler`, with a `ClientAuthenticator` that checks hashed secrets |
| `/revoke` | POST | the SDK's `RevocationHandler`, likewise |

and `POST /mcp` behind the SDK's `RequireAuthMiddleware` and `BearerAuthBackend`, the
backend's resource check set to O/mcp. The guard sets `scope[signin.GRANT]` to the call's
`Grant` before the MCP app runs. The SDK's `create_auth_routes` is never used: it fixes the
authorization endpoint and mounts its own `/authorize`.

**What lives where.** Pending sign-in requests and unredeemed OAuth codes were checked
against one origin, so they live on that origin's `Under` and are dropped when the next
origin is mounted. Clients and grants live in the grants file (`moxi_connector.grants`),
read again whenever it changes, so a revoke from the command line works with the process up
or down. `/token` touches only memory and that file, never the network or a provider.

**Registration.** A client may register only redirect URIs in `redirect_uris`; any other is
400 `invalid_redirect_uri`. At most `grants.UNFINISHED_MAX` clients that never held a grant
exist, each kept `grants.UNFINISHED_KEEP`; past that, 400 `invalid_client_metadata`. A client
that has held a grant is kept until `moxi connector revoke --all`. A client secret rests only
as its hash.

**Tokens.** An access token lives an hour. A refresh rotates the pair. A retired refresh
token is answered once more while the pair that replaced it has never been used and less
than `grants.GRACE` has passed; otherwise it is reuse, and the whole grant is revoked. A
grant lives `refresh_days` from its last refresh. A grant whose resource is not the current
O/mcp is stale and answered 401 until O comes back.

**Limits on callers without a token**, per minute: 10 registrations, 20 `GET /authorize`,
20 `POST /authorize`, 60 `POST /token`. Past a cap the answer is 429.

**The page's entry.** The sign-in page (`OAuth.page`, a factory of the ASGI app for
`/authorize` under one `Under`) checks a request with `Under.check(params)`, keeps it with
`Under.open(request)`, and, once the owner proved himself, finishes it with
`Under.issue(pending_id, label=...)`, which spends the pending request and answers the URL
to redirect the browser to, carrying the OAuth code, the state and the issuer.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.revoke import RevocationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.bearer_auth import (AuthenticatedUser, BearerAuthBackend,
                                                    RequireAuthMiddleware)
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import (AccessToken, AuthorizationCode, RefreshToken,
                                      RegistrationError, TokenError,
                                      construct_redirect_uri)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.server.transport_security import RequestBodyLimitMiddleware
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import request_response

from . import calllog, codes, grants
from .origin import ASM_PATH, PRM_PATH, Origin
from .signin import GRANT, ASGIApp, Grant, Mount

log = logging.getLogger("moxi.connector.oauth")

#: The one scope the connector grants.
SCOPE = "mcp"
#: The token endpoint's client authentication methods, as the SDK's `ClientAuthenticator`
#: accepts them.
AUTH_METHODS = ["none", "client_secret_post", "client_secret_basic"]
#: Seconds an OAuth code lives.
CODE_LIFE = 60
#: Seconds a pending sign-in request lives, and how many live at once.
PENDING_LIFE = 600
PENDING_MAX = 8
#: Misses a pending request takes before it is dropped.
PENDING_MISSES = 5
#: Requests a caller without a token may make per minute, by kind.
CAPS = {"register": 10, "authorize-get": 20, "authorize-post": 20, "token": 60}
#: The largest body the sign-in's routes take.
MAX_BODY = 64 * 1024
#: Seconds between two writes of one grant's last use.
USED_EVERY = 60

UNKNOWN_CLIENT = ("This sign-in uses a client the connector doesn't know. In Claude, remove "
                  "the connector and add it again.")
BAD_REDIRECT = ("This sign-in asks to return to an address the connector doesn't allow. In "
                "Claude, remove the connector and add it again.")
TOO_MANY_PENDING = "Too many sign-ins in progress. Try again in a few minutes."
TOO_MANY_CLIENTS = "too many clients; try again later"
SLOW_DOWN = b"Too many requests. Try again in a minute.\n"

_CHALLENGE = re.compile(r"[A-Za-z0-9._~-]{43,128}")


# -- what the page works with --------------------------------------------------------------

class PageError(Exception):
    """A request the page answers itself, with no redirect, since the redirect isn't
    trusted yet: `status` and the text to show."""

    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status = status
        self.text = text


class RedirectError(Exception):
    """A request whose fault goes back to the client: `url` is its redirect URI with the
    OAuth error, the state and the issuer."""

    def __init__(self, url: str, error: str):
        super().__init__(error)
        self.url = url
        self.error = error


class TooMany(PageError):
    """No room for one more pending request; none live is dropped to make room."""

    def __init__(self):
        super().__init__(429, TOO_MANY_PENDING)


@dataclass(frozen=True)
class AuthRequest:
    """A checked `/authorize` request. `resource` is always O/mcp."""

    client_id: str
    client_name: str | None
    redirect_uri: str
    code_challenge: str
    state: str
    resource: str


@dataclass
class Pending:
    """A sign-in request waiting for its owner's proof."""

    request: AuthRequest
    expires: float
    misses: int = 0


class Code(AuthorizationCode):
    """An OAuth code, with the label its grant will carry."""

    label: str | None = None


class Access(AccessToken):
    """An access token's grant, as the verifier found it."""

    grant_id: str
    label: str | None = None


class Refresh(RefreshToken):
    """A presented refresh token, with its grant and its verdict."""

    grant_id: str
    hash: str
    verdict: str


# -- the limits on callers without a token -------------------------------------------------

class Limits:
    """Per-minute counts, one window per kind of request."""

    def __init__(self, clock: Callable[[], float], caps: Mapping[str, int] = CAPS):
        self.clock = clock
        self.caps = dict(caps)
        self._windows: dict[str, tuple[int, int]] = {}

    def take(self, kind: str) -> bool:
        """Count one request of `kind`; False when it is past the minute's cap."""
        cap = self.caps.get(kind)
        if cap is None:
            return True
        minute = int(self.clock() // 60)
        start, count = self._windows.get(kind, (minute, 0))
        if start != minute:
            start, count = minute, 0
        if count >= cap:
            self._windows[kind] = (start, count)
            return False
        self._windows[kind] = (start, count + 1)
        return True


# -- the sign-in ---------------------------------------------------------------------------

class OAuth:
    """The sign-in the door mounts for each origin (`signin.SignIn`).

    `store` is the grants file, `calllog` the call log, `redirect_uris` the callbacks a
    client may register, `refresh_days` a grant's lifetime after its last refresh. `page`
    makes the `/authorize` app for one `Under`: the sign-in page (`moxi_connector.page`)
    unless a test gives another. `codes` are the one-time codes minted at the terminal;
    they outlive an origin, so they live here and not on an `Under`."""

    def __init__(self, store: grants.Store, calllog_: calllog.Writer, *,
                 redirect_uris: Iterable[str], clock: Callable[[], float] = time.time,
                 page: Callable[[Under], ASGIApp] | None = None):
        self.store = store
        self.calllog = calllog_
        self.redirect_uris = tuple(redirect_uris)
        self.clock = clock
        self.store.clock = clock
        self.limits = Limits(clock)
        if page is None:
            from .page import page as sign_in_page
            page = sign_in_page
        self.page = page
        self.codes = codes.Codes(clock)
        self.current: Under | None = None
        self._used: dict[str, float] = {}
        self._seen: dict[str, float] = {}
        self._stale: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

    @property
    def refresh_days(self) -> int:
        return self.store.days

    def mount(self, origin: Origin) -> Mount:
        """The routes and the guard for `origin`. The previous origin's pending requests and
        OAuth codes are dropped here, once the new ones are built: a mount that raises
        leaves the sign-in as it was."""
        under = Under(self, origin)
        mount = under.mount()
        if self.current is not None:
            self.current.drop_all()
        self.current = under
        self._later(self._tidy_and_note, origin)
        return mount

    async def stopping(self, switch_on: bool) -> None:
        """At the process's stop: write pending last-use times; with the switch off,
        revoke every grant, so none stays valid behind a port nothing holds."""
        for task in list(self._tasks):
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task
        for gid, seen in list(self._seen.items()):
            if seen > self._used.get(gid, 0):
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self.store.note_used, gid, seen)
        if not switch_on:
            count = await asyncio.to_thread(self.store.revoke_all, by="stop")
            await self.event("revoke", all=True, by="stop", count=count)

    # -- the call log ---------------------------------------------------------------------

    async def event(self, kind: str, *, collapse: str | None = None, **fields: Any) -> None:
        """One sign-in event, durable; never fails the request it belongs to."""
        row = {"at": calllog.now(), "kind": kind, **fields}
        try:
            await asyncio.to_thread(self.calllog.write, row, durable=True, collapse=collapse)
        except Exception:  # noqa: BLE001
            log.exception("the call log couldn't take a %s row", kind)

    def _later(self, fn: Callable[..., Awaitable[None]], *args: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(fn(*args))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _tidy_and_note(self, origin: Origin) -> None:
        """Drop what has outlived its keep, and note each grant this origin makes stale."""
        try:
            gone = await asyncio.to_thread(self.store.tidy)
        except Exception:  # noqa: BLE001
            log.exception("the grants file couldn't be tidied")
            gone = {"grants": []}
        for gid in gone.get("grants", ()):
            await self.event("grant-dropped", grant=gid, why="lifetime")
        data = self.store.view()
        now = self.clock()
        stale = {gid for gid, g in data["grants"].items()
                 if grants.is_live(g, now, self.refresh_days)
                 and grants.is_stale(g, origin.resource)}
        for gid in sorted(stale - self._stale):
            await self.event("grant-stale", grant=gid,
                             resource=data["grants"][gid].get("resource"))
        self._stale = stale

    # -- last use ------------------------------------------------------------------------

    async def used(self, gid: str, grant: Mapping[str, Any]) -> None:
        """Note a grant's use: written at once the first time its current pair is used
        (the grace rule reads it), else at most once a minute."""
        now = self.clock()
        self._seen[gid] = now
        last = self._used.get(gid)
        if grant.get("replacement_used") and last is not None and now - last < USED_EVERY:
            return
        self._used[gid] = now
        try:
            await asyncio.to_thread(self.store.note_used, gid, now)
        except Exception:  # noqa: BLE001 - a lost last-use time never refuses a call
            log.exception("the last use of grant %s couldn't be written", gid)


class Under:
    """The sign-in under one origin: its pending requests and OAuth codes, its provider,
    and its routes."""

    def __init__(self, oauth: OAuth, origin: Origin):
        self.oauth = oauth
        self.origin = origin
        self.pending: dict[str, Pending] = {}
        self.codes: dict[str, Code] = {}
        self.provider = Provider(self)
        self.authenticator = HashedSecrets(self.provider)

    # -- the page's entry ----------------------------------------------------------------

    def check(self, params: Mapping[str, str]) -> AuthRequest:
        """Check an `/authorize` request's parameters. A bad client or redirect raises
        `PageError` (no redirect); any other fault raises `RedirectError`. An absent
        `resource` is O/mcp."""
        client_id = params.get("client_id") or ""
        client = self.oauth.store.client(client_id) if client_id else None
        if client is None:
            raise PageError(400, UNKNOWN_CLIENT)
        redirect = params.get("redirect_uri") or ""
        registered = [str(u) for u in (client["metadata"].get("redirect_uris") or ())]
        if redirect not in registered or redirect not in self.oauth.redirect_uris:
            raise PageError(400, BAD_REDIRECT)
        state = params.get("state") or ""

        def refuse(error: str, text: str):
            raise RedirectError(construct_redirect_uri(
                redirect, error=error, error_description=text, state=state or None,
                iss=self.origin.issuer), error)

        if params.get("response_type") != "code":
            refuse("unsupported_response_type", "response_type must be code")
        if params.get("code_challenge_method") != "S256":
            refuse("invalid_request", "code_challenge_method must be S256")
        challenge = params.get("code_challenge") or ""
        if not _CHALLENGE.fullmatch(challenge):
            refuse("invalid_request", "a code_challenge is required")
        if not state:
            refuse("invalid_request", "a state is required")
        resource = params.get("resource")
        if resource not in (None, "", self.origin.resource):
            refuse("invalid_target", "this server's resource is " + self.origin.resource)
        return AuthRequest(client_id, client["metadata"].get("client_name"), redirect,
                           challenge, state, self.origin.resource)

    def open(self, request: AuthRequest) -> str:
        """Keep a checked request for `PENDING_LIFE`; its id. Raises `TooMany` when
        `PENDING_MAX` are live."""
        self._expire()
        if len(self.pending) >= PENDING_MAX:
            raise TooMany()
        pid = secrets.token_urlsafe(32)
        self.pending[pid] = Pending(request, self.oauth.clock() + PENDING_LIFE)
        return pid

    def waiting(self, pid: str) -> Pending | None:
        """The live pending request `pid`, or None."""
        self._expire()
        return self.pending.get(pid)

    def miss(self, pid: str) -> int:
        """Count a wrong proof against pending request `pid`; the tries it has left. At
        `PENDING_MISSES` it is dropped."""
        found = self.waiting(pid)
        if found is None:
            return 0
        found.misses += 1
        if found.misses >= PENDING_MISSES:
            del self.pending[pid]
            return 0
        return PENDING_MISSES - found.misses

    def issue(self, pid: str, *, label: str | None = None) -> str:
        """Spend pending request `pid` and make its OAuth code (single use, `CODE_LIFE`,
        bound to the client, the redirect URI, the challenge and O/mcp). Returns the URL to
        redirect the browser to: the redirect URI with `code`, `state` and `iss`. Raises
        `KeyError` when the request is gone. No await: the spend is one step on the loop."""
        found = self.waiting(pid)
        if found is None:
            raise KeyError(pid)
        del self.pending[pid]
        req = found.request
        code = grants.new_token()
        self._expire()
        self.codes[grants.digest(code)] = Code(
            code="", scopes=[SCOPE], expires_at=self.oauth.clock() + CODE_LIFE,
            client_id=req.client_id, code_challenge=req.code_challenge,
            redirect_uri=req.redirect_uri, redirect_uri_provided_explicitly=True,
            resource=req.resource, label=label)
        return construct_redirect_uri(req.redirect_uri, code=code, state=req.state,
                                      iss=self.origin.issuer)

    def drop_all(self) -> None:
        """Drop every pending request and OAuth code: their origin is no longer O."""
        self.pending.clear()
        self.codes.clear()

    def _expire(self) -> None:
        now = self.oauth.clock()
        for pid in [p for p, v in self.pending.items() if v.expires <= now]:
            del self.pending[pid]
        for h in [h for h, c in self.codes.items() if c.expires_at <= now]:
            del self.codes[h]

    # -- the routes ----------------------------------------------------------------------

    def metadata(self) -> OAuthMetadata:
        """The authorization server's metadata (RFC 8414), every value from O."""
        o = self.origin
        return OAuthMetadata(
            issuer=o.issuer, authorization_endpoint=o.authorization_endpoint,
            token_endpoint=o.token_endpoint, registration_endpoint=o.registration_endpoint,
            revocation_endpoint=o.revocation_endpoint, scopes_supported=[SCOPE],
            response_types_supported=["code"],
            grant_types_supported=["authorization_code", "refresh_token"],
            token_endpoint_auth_methods_supported=list(AUTH_METHODS),
            revocation_endpoint_auth_methods_supported=list(AUTH_METHODS),
            code_challenge_methods_supported=["S256"],
            authorization_response_iss_parameter_supported=True)

    def resource_document(self) -> dict[str, Any]:
        """The protected resource's metadata (RFC 9728), every value from O."""
        return {"resource": self.origin.resource,
                "authorization_servers": [self.origin.issuer],
                "bearer_methods_supported": ["header"]}

    def mount(self) -> Mount:
        oauth = self.oauth
        limits = oauth.limits
        registration = RegistrationHandler(
            self.provider, ClientRegistrationOptions(enabled=True, default_scopes=[SCOPE]))
        token = TokenHandler(self.provider, self.authenticator)
        revoke = RevocationHandler(self.provider, self.authenticator)
        page = oauth.page(self)
        routes = {
            PRM_PATH: methods({"GET": _json_app(self.resource_document())}),
            ASM_PATH: methods({"GET": request_response(MetadataHandler(self.metadata())
                                                       .handle)}),
            "/register": methods({"POST": limited(limits, "register",
                                                  _sdk(registration.handle))}),
            "/authorize": methods({"GET": limited(limits, "authorize-get", page),
                                   "POST": limited(limits, "authorize-post", page)}),
            "/token": methods({"POST": limited(limits, "token", _sdk(token.handle))}),
            "/revoke": methods({"POST": _sdk(revoke.handle)}),
        }
        backend = BearerAuthBackend(self.provider,
                                    resource_server_url=self.origin.resource)  # type: ignore[arg-type]

        def guard(app: ASGIApp) -> ASGIApp:
            inner = RequireAuthMiddleware(
                _with_grant(app), required_scopes=[],
                resource_metadata_url=self.origin.resource_metadata_url)  # type: ignore[arg-type]
            return AuthenticationMiddleware(inner, backend=backend)

        return Mount(routes=routes, guard=guard)


# -- the SDK's provider --------------------------------------------------------------------

class Provider:
    """The SDK's `OAuthAuthorizationServerProvider` and `TokenVerifier`, over the grants
    file and one origin's codes."""

    def __init__(self, under: Under):
        self.under = under
        self.oauth = under.oauth
        self.store = under.oauth.store

    # clients

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        found = self.store.client(client_id)
        if found is None:
            return None
        return OAuthClientInformationFull.model_validate(
            {**found["metadata"], "client_id": client_id, "client_secret": None})

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        allowed = self.oauth.redirect_uris
        uris = [str(u) for u in client_info.redirect_uris or ()]
        bad = [u for u in uris if u not in allowed]
        if bad or not uris:
            raise RegistrationError("invalid_redirect_uri",
                                    "redirect_uris may hold only Claude's callbacks")
        secret = client_info.client_secret
        metadata = client_info.model_dump(mode="json", exclude_none=True,
                                          exclude={"client_id", "client_secret"})
        try:
            await asyncio.to_thread(self.store.add_client, client_info.client_id, metadata,
                                    grants.digest(secret) if secret else None)
        except grants.Full:
            raise RegistrationError("invalid_client_metadata", TOO_MANY_CLIENTS) from None
        await self.oauth.event(
            "register", collapse="register", client=client_info.client_id,
            client_name=grants._shown(client_info.client_name),
            auth_method=client_info.token_endpoint_auth_method)

    async def authorize(self, client, params) -> str:
        raise NotImplementedError("the sign-in page serves /authorize")

    # codes

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> Code | None:
        self.under._expire()
        found = self.under.codes.get(grants.digest(authorization_code))
        if found is None or found.client_id != client.client_id:
            return None
        return found

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: Code) -> OAuthToken:
        spent = None
        for h, c in list(self.under.codes.items()):
            if c is authorization_code:
                spent = self.under.codes.pop(h)
        if spent is None:
            raise TokenError("invalid_grant", "authorization code does not exist")
        access, refresh = grants.new_token(), grants.new_token()
        try:
            gid = await asyncio.to_thread(
                self.store.add_grant, client.client_id or "", label=spent.label,
                resource=spent.resource or self.under.origin.resource,
                access_hash=grants.digest(access), refresh_hash=grants.digest(refresh))
        except grants.Gone:
            raise TokenError("invalid_grant", "the client is no longer known") from None
        await self.oauth.event("token", grant=gid, client=client.client_id,
                               label=spent.label)
        return _pair(access, refresh)

    # refresh tokens

    async def load_refresh_token(self, client: OAuthClientInformationFull,
                                 refresh_token: str) -> Refresh | None:
        h = grants.digest(refresh_token)
        verdict = self.store.judge(h, client.client_id or "")
        if verdict.kind in ("reuse", "expired"):
            acted = await asyncio.to_thread(self.store.refresh, h, client.client_id or "")
            await self._after(acted, client)
            return None
        if verdict.kind not in ("live", "grace") or verdict.grant is None:
            return None
        grant = self.store.grant(verdict.grant)
        if grant is None or grants.is_stale(grant, self.under.origin.resource):
            return None
        return Refresh(token="", client_id=client.client_id or "", scopes=[SCOPE],
                       resource=grant["resource"], grant_id=verdict.grant, hash=h,
                       verdict=verdict.kind)

    async def exchange_refresh_token(self, client: OAuthClientInformationFull,
                                     refresh_token: Refresh, scopes: list[str]) -> OAuthToken:
        access, refresh = grants.new_token(), grants.new_token()
        acted = await asyncio.to_thread(
            self.store.refresh, refresh_token.hash, client.client_id or "",
            access_hash=grants.digest(access), refresh_hash=grants.digest(refresh))
        await self._after(acted, client)
        if acted.kind not in ("live", "grace"):
            raise TokenError("invalid_grant", "refresh token does not exist")
        self.oauth._used.pop(acted.grant or "", None)
        return _pair(access, refresh)

    async def _after(self, acted: grants.Verdict, client) -> None:
        if acted.kind == "reuse":
            await self.oauth.event("refresh-reuse", grant=acted.grant, client=client.client_id)
        elif acted.kind == "expired":
            await self.oauth.event("grant-dropped", grant=acted.grant, why="lifetime")
        elif acted.kind in ("live", "grace"):
            await self.oauth.event("refresh", grant=acted.grant, client=client.client_id,
                                   grace=acted.kind == "grace")

    # access tokens

    async def load_access_token(self, token: str) -> Access | None:
        found = self.store.by_access(grants.digest(token))
        if found is None:
            return None
        gid, grant = found
        now = self.oauth.clock()
        if (not grants.is_live(grant, now, self.oauth.refresh_days)
                or now >= grant["access_expires"]
                or grants.is_stale(grant, self.under.origin.resource)):
            return None
        await self.oauth.used(gid, grant)
        return Access(token="", client_id=grant["client"], scopes=[SCOPE],
                      resource=grant["resource"], grant_id=gid, label=grant.get("label"))

    async def verify_token(self, token: str) -> Access | None:
        return await self.load_access_token(token)

    async def revoke_token(self, token: Access | Refresh) -> None:
        gid = getattr(token, "grant_id", None)
        if gid is None:
            return
        if await asyncio.to_thread(self.store.revoke, gid, by="client"):
            await self.oauth.event("revoke", grant=gid, by="client")


def _pair(access: str, refresh: str) -> OAuthToken:
    return OAuthToken(access_token=access, token_type="Bearer", expires_in=grants.ACCESS_LIFE,
                      scope=SCOPE, refresh_token=refresh)


class HashedSecrets(ClientAuthenticator):
    """The SDK's `ClientAuthenticator`, over client secrets the file keeps only as hashes.

    The presented secret (form field or Basic header) is hashed and compared with the
    stored hash; the SDK's own check then runs against a copy of the client that carries
    the presented secret when it matched, and a random one when it didn't."""

    async def authenticate_request(self, request):
        form = await request.form()
        presented = _presented_secret(request.headers.get("authorization", ""),
                                      form.get("client_secret"))
        return await ClientAuthenticator(_Presented(self.provider, presented)) \
            .authenticate_request(request)


class _Presented:
    def __init__(self, provider: Provider, presented: str | None):
        self.provider = provider
        self.presented = presented

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        client = await self.provider.get_client(client_id)
        if client is None:
            return None
        stored = (self.provider.store.client(client_id) or {}).get("secret")
        if stored:
            ok = self.presented is not None and grants.same(grants.digest(self.presented),
                                                            stored)
            client.client_secret = self.presented if ok else secrets.token_hex(32)
        return client


def _presented_secret(authorization: str, posted: Any) -> str | None:
    if authorization.startswith("Basic "):
        try:
            decoded = base64.b64decode(authorization[6:]).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return None
        if ":" in decoded:
            return unquote(decoded.split(":", 1)[1])
        return None
    return posted if isinstance(posted, str) else None


# -- ASGI pieces ---------------------------------------------------------------------------

async def _answer(send, status: int, body: bytes = b"", headers=()) -> None:
    head = [(b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store"),
            *headers]
    if body:
        head.insert(0, (b"content-type", b"text/plain; charset=utf-8"))
    await send({"type": "http.response.start", "status": status, "headers": head})
    await send({"type": "http.response.body", "body": body})


def methods(table: Mapping[str, ASGIApp]) -> ASGIApp:
    """One path's handlers by method; any other method is 405 with `Allow`."""
    allow = ", ".join(sorted(table)).encode()

    async def app(scope, receive, send) -> None:
        handler = table.get(scope.get("method", ""))
        if handler is None:
            await _answer(send, 405, headers=((b"allow", allow),))
            return
        await handler(scope, receive, send)

    return app


def limited(limits: Limits, kind: str, inner: ASGIApp) -> ASGIApp:
    """`inner`, past the minute's cap for `kind` answered 429."""

    async def app(scope, receive, send) -> None:
        if not limits.take(kind):
            await _answer(send, 429, SLOW_DOWN, headers=((b"retry-after", b"60"),))
            return
        await inner(scope, receive, send)

    return app


def _sdk(handler) -> ASGIApp:
    return RequestBodyLimitMiddleware(request_response(handler), MAX_BODY)


def _json_app(document: Mapping[str, Any]) -> ASGIApp:
    body = json.dumps(document).encode()

    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    return app


def _with_grant(app: ASGIApp) -> ASGIApp:
    """Set `scope[GRANT]` from the token the SDK's backend admitted."""

    async def inner(scope, receive, send) -> None:
        user = scope.get("user")
        if isinstance(user, AuthenticatedUser) and isinstance(user.access_token, Access):
            token = user.access_token
            scope[GRANT] = Grant(token.grant_id, token.label, token.client_id)
        await app(scope, receive, send)

    return inner


__all__ = ["AUTH_METHODS", "Access", "AuthRequest", "CAPS", "CODE_LIFE", "Code",
           "HashedSecrets", "Limits", "OAuth", "PENDING_LIFE", "PENDING_MAX",
           "PENDING_MISSES", "PageError", "Pending", "Provider", "RedirectError", "Refresh",
           "SCOPE", "TooMany", "UNKNOWN_CLIENT", "Under", "methods", "limited"]

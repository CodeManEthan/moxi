"""Who may use the gateway: pairing, passkey sign-in, the gateway's sessions, and the
open routes that need no credential.

The open routes, which every caller reaches once the provider check has passed:

    GET  /pair, /signin          the two pages, each sent with `Clear-Site-Data: "cache",
                                 "storage"` at a URL never used before, so nothing left in
                                 the browser before them runs after
    GET  /pair.js, /signin.js    their scripts, static files sent `no-store`
    POST /api/pair/begin         {"code", "name"}: spend a pairing code, make a pairing
                                 state, answer the creation options
    POST /api/pair/finish        {"pairing", "credential"}: take the state, check the new
                                 passkey (or none), write the device, sign in
    POST /api/signin/begin       answer request options with a signed challenge; stores
                                 nothing
    POST /api/signin/finish      {"credential"}: check the challenge, the device and the
                                 assertion, spend the challenge, sign in

`POST /api/signout` needs the cookie and the key, and ends the session that sent it.

No caller without a credential can stop the user's device from pairing or signing in: the
open routes keep no state such a caller can fill. A sign-in begin stores nothing, only a
finish whose assertion verifies adds to the spent set, only a spent pairing code makes a
pairing state, and no count refuses an open route.

A signed-in phone gets the cookie `__Host-moxi-gw` (Secure, HttpOnly, SameSite=Strict,
Path=/) and a key in the body, which the page keeps in the origin's storage and sends as
`X-Moxi-Key`: cookies ignore ports, so another target on the same host would get the cookie,
but storage is per origin. Sessions live in this shell's memory only and end with it, and
end after 12 hours unused or 7 days old, at sign-out, when their device is revoked or goes
stale, and when the gateway turns off or its profile is rebuilt for a new port, host or
provider. At most 4 per device and 16 in all, the oldest evicted first.

The credential each request needs, after the provider check, by its one entry:

    the worker entry          nothing while `Service-Worker: script` is present
    a navigation              nothing; the router answers with the shell document or the
                              303 to `/signin`. The gateway's worker sends a navigation on
                              as a plain GET past the browser's cache, marked with
                              `X-Moxi-Navigation: 1`, and it counts as one
    the open entries          nothing
    an asset, no entry        the cookie
    routes, acts, the push,   the cookie and the key
    /api/signout

There is no token class.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable
from importlib import resources
from typing import Any

from moxi_loopback.api import Entry, RequestView, Table, response_headers

from .. import session as _session
from ..router import NAVIGATION
from ..session import SessionStore, Static, admitted, cookie_ids, key_of
from . import pairing, passkeys
from .address import Kept
from .devices import Device, Devices, Full
from .listener import Parts

__all__ = ["COOKIE", "DURABLE", "EVENTS", "IDLE", "LIMIT", "MAX_AGE", "NAVIGATION_HEADER",
           "OPEN_PATHS", "PAGES", "PER_DEVICE", "REFUSALS", "Access", "is_navigation", "origin",
           "session_hash", "signin_redirect"]

_log = logging.getLogger("moxi.ui.gateway")

#: The gateway's session cookie. `__Host-` binds it to this origin's host, `https` and `/`.
COOKIE = "__Host-moxi-gw"
#: A session ends this long after it was made, used or not.
MAX_AGE = 7 * 24 * 3600
#: A session ends after this long unused.
IDLE = 12 * 3600
#: At most this many sessions per device, and in all.
PER_DEVICE = 4
LIMIT = 16

PAIR_PAGE = "/pair"
SIGNIN_PAGE = "/signin"
PAIR_SCRIPT = "/pair.js"
SIGNIN_SCRIPT = "/signin.js"
PAIR_BEGIN = "/api/pair/begin"
PAIR_FINISH = "/api/pair/finish"
SIGNIN_BEGIN = "/api/signin/begin"
SIGNIN_FINISH = "/api/signin/finish"
SIGNOUT = "/api/signout"

#: The paths that need no credential: the pages, their scripts and the sign-in APIs, and
#: the PWA's own files, which the phone loads before it can sign in.
OPEN_PATHS = frozenset({
    PAIR_PAGE, SIGNIN_PAGE, PAIR_SCRIPT, SIGNIN_SCRIPT, PAIR_BEGIN, PAIR_FINISH,
    SIGNIN_BEGIN, SIGNIN_FINISH, "/manifest.webmanifest", "/sw.js", "/offline", "/offline.js",
})

#: The header the gateway's worker adds to a navigation it sends on. A browser sends a
#: worker's fetch as a plain same-origin GET, whatever it was, and the worker must fetch
#: past the HTTP cache, so the header is how the navigation still reads as one.
NAVIGATION_HEADER = b"x-moxi-navigation"


#: The pages and scripts this layer sends, by path: the file in the ui's built web
#: directory, and its content type.
PAGES = {
    PAIR_PAGE: ("pair.html", "text/html; charset=utf-8"),
    SIGNIN_PAGE: ("signin.html", "text/html; charset=utf-8"),
    PAIR_SCRIPT: ("pair.js", "text/javascript; charset=utf-8"),
    SIGNIN_SCRIPT: ("signin.js", "text/javascript; charset=utf-8"),
}

#: The audit codes of the refusals this layer makes, each a 403.
REFUSALS = frozenset({"pairing", "credential", "challenge", "stale", "revoked"})
#: The events this layer writes: a pairing (`pair`, or `re-pair` when it takes a stale
#: device's place), a sign-in, a sign-out, a revoke, and devices marked stale.
EVENTS = frozenset({"pair", "re-pair", "signin", "signout", "revoke", "mark-stale"})
#: The events written durably.
DURABLE = frozenset({"pair", "re-pair", "signin", "signout", "revoke"})

_BEGIN_CAP = 4 * 1024
_FINISH_CAP = 64 * 1024
_NAME_MAX = 64
_CLEAR = ("Clear-Site-Data", '"cache", "storage"')


def _nothing(code: str, fields: dict[str, Any]) -> None:
    return None


def session_hash(id_hash: str) -> str:
    """The 12 hex the audit rows name a session by: the start of its id's SHA-256."""
    return id_hash[:12]


def origin(kept: Kept) -> str:
    """The gateway's origin for an address: `https://<host>`, with `:<port>` unless 443."""
    return f"https://{kept.host}" if kept.port == 443 else f"https://{kept.host}:{kept.port}"


def is_navigation(method: str, headers: Iterable[tuple[bytes, bytes]]) -> bool:
    """Whether the request loads a document: a browser's navigation, or a GET the gateway's
    worker sent on for one, marked `X-Moxi-Navigation: 1`. Either gets only the shell
    document or the 303 to `/signin`."""
    headers = list(headers)
    if _session.is_navigation(method, headers):
        return True
    marks = [value for name, value in headers if name.lower() == NAVIGATION_HEADER]
    return method == "GET" and marks == [b"1"]


def _fallback(base: Callable[[RequestView], Entry | None] | None
              ) -> Callable[[RequestView], Entry | None]:
    """`base`, the navigation fallback, also giving the navigation entry to a navigation
    the worker sent on."""
    def fallback(view: RequestView) -> Entry | None:
        if is_navigation(view.method, view.headers):
            return NAVIGATION
        return base(view) if base is not None else None
    return fallback


def web_file(name: str) -> bytes | None:
    """A file of the ui's built web directory, or None when it isn't there."""
    try:
        return resources.files("moxi_ui").joinpath("web", name).read_bytes()
    except (OSError, ValueError):
        return None


# --- answers ----------------------------------------------------------------------------


def _headers(extra: Iterable[tuple[str, str]]) -> list[tuple[bytes, bytes]]:
    return response_headers(extra=list(extra), policy="gateway")


def _plain(status: int) -> Static:
    return status, _headers([("Content-Length", "0")]), b""


def _json(status: int, value: Any) -> Static:
    body = json.dumps(value, separators=(",", ":")).encode("ascii")
    return status, _headers([("Content-Type", "application/json"),
                             ("Content-Length", str(len(body)))]), body


def _cookie(session_id: str | None) -> tuple[bytes, bytes]:
    value = (f"{COOKIE}={session_id}; Secure; HttpOnly; SameSite=Strict; Path=/; "
             f"Max-Age={MAX_AGE}" if session_id is not None
             else f"{COOKIE}=; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
    return b"set-cookie", value.encode("ascii")


def signed_in_answer(session_id: str, key: str, device: str) -> Static:
    """A sign-in's answer: the cookie, and the key and the device id in the body."""
    status, headers, body = _json(200, {"key": key, "device": device})
    return status, [*headers, _cookie(session_id)], body


def signin_redirect() -> Static:
    """A navigation without a valid cookie: a 303 to `/signin?n=<16 hex>`, `n` new each
    time, so no cached answer can match it."""
    return 303, _headers([("Location", f"{SIGNIN_PAGE}?n={secrets.token_hex(8)}"),
                          ("Content-Length", "0")]), b""


async def _send(send: Callable[..., Any], answer: Static) -> None:
    status, headers, body = answer
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _body(receive: Callable[..., Any], cap: int) -> bytes | None:
    body = b""
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            return None
        body += message.get("body", b"")
        if len(body) > cap:
            return None
        if not message.get("more_body"):
            return body


class _Bad(Exception):
    def __init__(self, answer: Static):
        self.answer = answer


def _object(body: bytes | None, keys: set[str], *, empty: bool = False) -> dict[str, Any]:
    if body is None:
        raise _Bad(_plain(413))
    if empty and not body.strip():
        return {}
    try:
        data = json.loads(body)
    except ValueError:
        raise _Bad(_json(400, {"error": "the body is not JSON"})) from None
    if not isinstance(data, dict) or set(data) != keys:
        names = ", ".join(sorted(keys)) or "no keys"
        raise _Bad(_json(400, {"error": f"the body is an object of {names}"}))
    return data


def _name(value: object) -> str:
    """A device's name as the user typed it: 1 to 64 printable characters."""
    if isinstance(value, str):
        name = unicodedata.normalize("NFC", value).strip()
        if 0 < len(name) <= _NAME_MAX and all(ch.isprintable() for ch in name):
            return name
    raise _Bad(_json(400, {"error": f"the name is 1 to {_NAME_MAX} printable characters"}))


# --- the gateway's sign-in state ------------------------------------------------------


class Access:
    """The gateway's devices, pairing states, sign-in challenges and sessions, in the shell.

    `state_dir` and `runtime_dir` are the ui's; `kept()` gives the address the gateway's
    profile stands on now (None while none stands); `audit(code, fields)` writes one audit
    row: a refusal code of `REFUSALS`, or an event of `EVENTS`. `pages(name)` gives a file
    of the ui's built web directory.
    """

    def __init__(self, *, state_dir: str | os.PathLike, runtime_dir: str | os.PathLike,
                 kept: Callable[[], Kept | None],
                 audit: Callable[[str, dict[str, Any]], None] = _nothing,
                 pages: Callable[[str], bytes | None] = web_file,
                 clock: Callable[[], float] = time.time):
        self.devices = Devices.of(state_dir, clock=clock)
        self.tickets = pairing.tickets(runtime_dir, clock=clock)
        self.pairings = pairing.PairingStates(clock=clock)
        self.challenges = passkeys.Challenges(clock=clock)
        self.store = SessionStore(epoch=None, path=None, clock=clock, max_age=MAX_AGE,
                                  idle=IDLE, limit=LIMIT, per_owner=PER_DEVICE)
        self.kept = kept
        self.audit = audit
        self.pages = pages
        self.open_paths = OPEN_PATHS
        #: The credential ids of devices revoked while this shell ran.
        self._revoked: set[str] = set()
        self._known: dict[str, Device] = {}
        self._routes: dict[tuple[str, str], Callable[..., Awaitable[Static]]] = {
            ("POST", PAIR_BEGIN): self._pair_begin,
            ("POST", PAIR_FINISH): self._pair_finish,
            ("POST", SIGNIN_BEGIN): self._signin_begin,
            ("POST", SIGNIN_FINISH): self._signin_finish,
            ("POST", SIGNOUT): self._signout,
        }

    # --- plugging in ------------------------------------------------------------------

    def parts(self, base: Parts | None = None) -> Parts:
        """`base` with this layer plugged in: its session callable, its routes in front of
        `base.router`, its entries added to each table, and its hooks."""
        base = base or Parts()
        table = base.table

        def tables(mount: Any) -> Table:
            return self.add_entries(table(mount))

        return Parts(table=tables, session=self, fallback=_fallback(base.fallback),
                     router=self.layer(base.router), end_sessions=self.end_sessions,
                     stale=self.stale, audit=self.audit, tick=self.tick,
                     refusals=base.refusals)

    def add_entries(self, table: Table) -> Table:
        """Add this layer's entries to `table` and return it."""
        for path in PAGES:
            table.add("GET", path)
        for _method, path in self._routes:
            table.add("POST", path, "act")
        return table

    def layer(self, inner: Callable[..., Any]) -> Callable[..., Any]:
        """An ASGI layer answering this layer's paths and passing the rest to `inner`."""

        async def routed(scope: dict[str, Any], receive: Callable[..., Any],
                         send: Callable[..., Any]) -> None:
            method = scope.get("method", "")
            raw = (scope.get("raw_path") or b"").decode("latin-1")
            if scope.get("type") == "http" and method == "GET" and raw in PAGES:
                await _send(send, self.page(raw))
                return
            handler = self._routes.get((method, raw)) if scope.get("type") == "http" else None
            if handler is None:
                await inner(scope, receive, send)
                return
            try:
                answer = await handler(scope, receive)
            except _Bad as bad:
                answer = bad.answer
            await _send(send, answer)

        return routed

    # --- the session callable ---------------------------------------------------------

    def __call__(self, view: RequestView, entry: Entry | None) -> bool:
        """Gate 4 on the gateway's profile: True to admit, by the class of the one entry
        `check` found. A request a session admits has its id hash in `admitted`."""
        admitted.set(None)
        headers = view.headers
        if entry is not None and entry.raw_path == _session.WORKER_PATH:
            return entry.method == "GET" and _session.is_worker_request(headers)
        if is_navigation(view.method, headers):
            return True
        if entry is not None and entry.raw_path in self.open_paths:
            return True
        if entry is None or entry.kind == "asset":
            return self._admit(self.signed_in(headers))
        return self._admit(self.signed_in(headers, need_key=True))

    @staticmethod
    def _admit(id_hash: str | None) -> bool:
        if id_hash is None:
            return False
        admitted.set(id_hash)
        return True

    def signed_in(self, headers: Iterable[tuple[bytes, bytes]], *,
                  need_key: bool = False) -> str | None:
        """The id hash of the live gateway session the request's cookie (and, with
        `need_key`, its key) names, or None."""
        headers = list(headers)
        ids = cookie_ids(headers, COOKIE)
        if not ids:
            return None
        return self.store.find(ids, key_of(headers), need_key=need_key)

    def device_of(self, id_hash: str | None) -> str | None:
        """The device id of the live session `id_hash`."""
        return self.store.owner_of(id_hash) if id_hash is not None else None

    # --- the hooks --------------------------------------------------------------------

    def end_sessions(self) -> None:
        """End every gateway session."""
        self.store.clear()

    def stale(self, old: Kept) -> None:
        """Mark stale the devices of the address the gateway just left."""
        try:
            marked = self.devices.stale_of(old.host, old.provider)
        except (OSError, ValueError):
            _log.exception("the gateway's devices can't be marked stale")
            return
        if marked:
            self.audit("mark-stale", {"devices": marked})

    def tick(self) -> None:
        """The 2 s tick: mark stale every device of another host or provider than the
        current address, and end the sessions of every device the file no longer holds
        or marks stale. Blocking; runs off the event loop."""
        kept = self.kept()
        try:
            if kept is not None:
                marked = self.devices.stale_unless(kept.host, kept.provider)
                if marked:
                    self.audit("mark-stale", {"devices": marked})
        except (OSError, ValueError):
            _log.exception("the gateway's devices can't be marked stale")
        devices = {d.id: d for d in self.devices.all()}
        for gone in set(self._known) - set(devices):
            credential = self._known[gone].credential
            if credential is not None:
                self._revoked.add(credential)
        self._known = devices
        live = {d.id for d in devices.values() if not d.stale}
        self.store.end_owners(lambda owner: owner is None or owner not in live)

    def revoke(self, device_id: str) -> bool:
        """Delete a device and end its sessions at once (the desktop's revoke)."""
        device = self.devices.get(device_id)
        if not self.devices.revoke(device_id):
            return False
        if device is not None and device.credential is not None:
            self._revoked.add(device.credential)
        self._known.pop(device_id, None)
        self.store.end_owners(lambda owner: owner == device_id)
        self.audit("revoke", {"device": device_id})
        return True

    # --- the pages --------------------------------------------------------------------

    def page(self, path: str) -> Static:
        """`/pair`, `/signin` or a script, sent `no-store`; the two pages also clear the
        origin's HTTP cache and storage before they run (never its cookies, which would
        clear those of every target on the host)."""
        name, kind = PAGES[path]
        body = self.pages(name)
        if body is None:
            _log.warning("the gateway's %s is missing from the ui's web files", name)
            return _plain(404)
        extra = [("Content-Type", kind), ("Content-Length", str(len(body)))]
        if kind.startswith("text/html"):
            extra.append(_CLEAR)
        return 200, _headers(extra), body

    # --- the routes -------------------------------------------------------------------

    def _refuse(self, code: str, path: str, **fields: Any) -> Static:
        self.audit(code, {"path": path, **fields})
        return _plain(403)

    def _where(self) -> Kept:
        kept = self.kept()
        if kept is None:
            raise _Bad(_plain(503))
        return kept

    def _sign_in(self, device: Device) -> Static:
        session_id, key = self.store.mint(owner=device.id)
        id_hash = hashlib.sha256(session_id.encode("ascii")).hexdigest()
        self.audit("signin", {"device": device.id, "session": session_hash(id_hash)})
        return signed_in_answer(session_id, key, device.id)

    async def _pair_begin(self, scope: dict[str, Any], receive: Callable[..., Any]) -> Static:
        data = _object(await _body(receive, _BEGIN_CAP), {"code", "name"})
        name = _name(data["name"])
        return await asyncio.to_thread(self._begin_pairing, data["code"], name)

    def _begin_pairing(self, code: object, name: str) -> Static:
        kept = self._where()
        if not self.tickets.redeem(code):
            return self._refuse("pairing", PAIR_BEGIN)
        state = self.pairings.begin(name)
        options = passkeys.creation_options(rp_id=kept.host, user_id=state.user_id,
                                            name=name, challenge=state.challenge)
        return _json(200, {"pairing": state.id, "options": options})

    async def _pair_finish(self, scope: dict[str, Any], receive: Callable[..., Any]) -> Static:
        data = _object(await _body(receive, _FINISH_CAP), {"pairing", "credential"})
        credential = data["credential"]
        if credential is not None and not isinstance(credential, dict):
            raise _Bad(_json(400, {"error": "the credential is an object or null"}))
        return await asyncio.to_thread(self._finish_pairing, data["pairing"], credential)

    def _finish_pairing(self, pairing_id: object, credential: dict | None) -> Static:
        kept = self._where()
        state = self.pairings.take(pairing_id)
        if state is None:
            return self._refuse("pairing", PAIR_FINISH)
        made = None
        if credential is not None:
            try:
                made = passkeys.verify_creation(credential, challenge=state.challenge,
                                                origin=origin(kept), rp_id=kept.host)
            except passkeys.Refused as refused:
                _log.info("a pairing's passkey was refused: %s", refused)
                return self._refuse(refused.code, PAIR_FINISH)
        replaces = next((d for d in self.devices.all()
                         if d.stale and d.name == state.name), None)
        try:
            device = self.devices.add(
                name=state.name, credential=made.credential if made else None,
                public_key=made.public_key if made else None,
                sign_count=made.sign_count if made else 0, host=kept.host,
                provider=kept.provider, change=kept.change,
                replaces=replaces.id if replaces else None)
        except Full as full:
            return _json(409, {"error": str(full)})
        self._known[device.id] = device
        if replaces is not None:
            self._known.pop(replaces.id, None)
            self.audit("re-pair", {"device": device.id, "replaces": replaces.id,
                                   "passkey": made is not None})
        else:
            self.audit("pair", {"device": device.id, "passkey": made is not None})
        return self._sign_in(device)

    async def _signin_begin(self, scope: dict[str, Any], receive: Callable[..., Any]) -> Static:
        _object(await _body(receive, _BEGIN_CAP), set(), empty=True)
        kept = self._where()
        options = await asyncio.to_thread(passkeys.request_options, rp_id=kept.host,
                                          challenge=self.challenges.mint())
        return _json(200, {"options": options})

    async def _signin_finish(self, scope: dict[str, Any],
                             receive: Callable[..., Any]) -> Static:
        data = _object(await _body(receive, _FINISH_CAP), {"credential"})
        return await asyncio.to_thread(self._finish_signin, data["credential"])

    def _finish_signin(self, credential: object) -> Static:
        kept = self._where()
        try:
            credential_id, challenge = passkeys.assertion_parts(credential)
        except passkeys.Refused as refused:
            return self._refuse(refused.code, SIGNIN_FINISH)
        expires = self.challenges.expiry(challenge)
        if expires is None:
            return self._refuse("challenge", SIGNIN_FINISH)
        device = self.devices.by_credential(credential_id)
        if device is None:
            code = "revoked" if credential_id in self._revoked else "credential"
            return self._refuse(code, SIGNIN_FINISH)
        if (device.stale or device.host.lower() != kept.host.lower()
                or device.provider != kept.provider):
            return self._refuse("stale", SIGNIN_FINISH, device=device.id)
        assert device.public_key is not None
        try:
            asserted = passkeys.verify_assertion(
                credential, challenge=challenge, origin=origin(kept), rp_id=kept.host,
                public_key=device.public_key, sign_count=device.sign_count)
        except passkeys.Refused as refused:
            if _SIGN_COUNT.search(str(refused)):
                _log.warning("device %s sent a sign count that didn't move forward; "
                             "its passkey may have been copied", device.id)
            return self._refuse(refused.code, SIGNIN_FINISH, device=device.id)
        if not self.challenges.spend(challenge, expires):
            return self._refuse("challenge", SIGNIN_FINISH, device=device.id)
        try:
            self.devices.used(device.id, asserted.sign_count)
        except (OSError, ValueError):
            _log.exception("device %s's sign-in can't be noted", device.id)
        return self._sign_in(device)

    async def _signout(self, scope: dict[str, Any], receive: Callable[..., Any]) -> Static:
        await _body(receive, _BEGIN_CAP)
        id_hash = admitted.get()
        device = self.device_of(id_hash)
        if id_hash is not None and self.store.end_hash(id_hash):
            self.audit("signout", {"device": device, "session": session_hash(id_hash)})
        status, headers, body = _json(200, {"ok": True})
        return status, [*headers, _cookie(None)], body


_SIGN_COUNT = re.compile(r"sign count", re.IGNORECASE)

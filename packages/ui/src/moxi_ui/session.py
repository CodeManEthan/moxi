"""The browser session: tickets, the cookie and the key, the owner check, and the session
callable the shell's `browser` profile runs inside loopback's `check`.

A browser signs in with a one-use ticket that `moxi ui open` mints. The sign-in answer
sets the cookie `moxi-ui` (HttpOnly, SameSite=Strict) and returns a key, which the page
keeps in the origin's storage and sends as `X-Moxi-Key`. Cookies ignore ports and storage
does not, so a cookie that leaks to another user's server on another port is useless
without the key.

The credential a request needs is decided from the one entry `check` looked up:

    the worker entry          nothing while `Service-Worker: script` is present; refused
                              without it (the worker layer gives every such request that
                              entry, and the router answers it with a fixed script)
    a navigation              nothing; the router answers with the shell document or the
                              signed-out page, both static
    /signin, the sign-in API  nothing: the ticket is the proof
    /api/prove                nothing (loopback exempts it before the callable runs)
    the token routes          the shell's token, on a request with no Cookie, Origin,
                              Referer or Sec-Fetch-* header
    /, /m/<module>, assets    the cookie
    routes and the push       the cookie and the key
    no entry (the 404)        the cookie, so a caller without one learns nothing of
                              which paths exist

On Linux other than WSL every class that asks for the cookie also asks that the
connection's client socket belongs to this user, read from `/proc/net/tcp` and `tcp6`.
So a cookie and key taken by any means work only from this user's own processes. WSL
has no such check: a Windows browser reaches the shell through the host's relay, whose
socket in the VM may not be this user's.

Sessions are kept in memory, or, for a shell with an epoch, also in
`<state_dir>/sessions.json` tagged with that epoch. A shell whose epoch differs from the
file's honours none of the file's sessions. A shell with no epoch never reads or writes
the file, so its sessions end with it. Only hashes of the session id and key are kept.

A request a session admitted is marked with that session (`admitted`), and the store
calls back when a session ends (`SessionStore.watch`), so the router can end a push
stream or a proxied request opened under it.
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moxi import files
from moxi_loopback.api import Entry, RequestView, response_headers, token_matches

#: The session cookie's name.
COOKIE = "moxi-ui"
#: The header that carries the session key.
KEY_HEADER = b"x-moxi-key"
#: The `localStorage` item the sign-in page stores the key in, which the shell's script
#: reads back.
KEY_STORAGE = "moxi-ui-key"

#: A session ends this long after it was made, used or not.
MAX_AGE = 90 * 24 * 3600
#: A session ends after this long unused.
IDLE = 30 * 24 * 3600
#: At most this many sessions are kept; the oldest goes first.
LIMIT = 20
#: The last-used time is written to the store at most this often.
TOUCH_EVERY = 3600
#: A ticket can be redeemed for this long after it is minted.
TICKET_TTL = 30

STORE_FILE = "sessions.json"
TICKETS_DIR = "tickets"

SIGNIN_PATH = "/signin"
SIGNIN_API = "/m/ui/api/signin"
SIGNOUT_API = "/m/ui/api/signout"
PROVE_PATH = "/api/prove"
HEALTH_PATH = "/m/ui/api/health"
SERVERS_PATH = "/m/ui/api/servers"
STOP_PATH = "/m/ui/api/stop"
PUSH_PATH = "/m/ui/api/push"
WORKER_PATH = "/m/ui/api/worker"
#: The routes only the CLI and the doctor reach, with the shell's token.
TOKEN_PATHS = frozenset({HEALTH_PATH, SERVERS_PATH, STOP_PATH, SIGNOUT_API})
_OPEN_PATHS = frozenset({SIGNIN_PATH, SIGNIN_API, PROVE_PATH})

_log = logging.getLogger("moxi.ui")

_TOKEN_SHAPE = re.compile(r"\A[A-Za-z0-9_-]{43}\Z")
_STORE_FORMAT = 1
_BROWSER_HEADERS = (b"cookie", b"origin", b"referer")

#: The connection the current request came in on, `(client, server)` as ASGI gives them.
#: The worker layer sets it for each request before `check` runs, so the session callable,
#: which sees only the request view, can run the owner check.
connection: contextvars.ContextVar[tuple[Any, Any] | None] = contextvars.ContextVar(
    "moxi_ui_connection", default=None)
#: The id hash of the session that admitted the current request, set by the session
#: callable inside `check`, so the router can end a long-lived request with its session.
admitted: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "moxi_ui_admitted", default=None)


# --- the request's headers ------------------------------------------------------------


def _values(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> list[bytes]:
    return [value for key, value in headers if key.lower() == name]


def _single(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    found = _values(headers, name)
    return found[0] if len(found) == 1 else None


def is_worker_request(headers: Iterable[tuple[bytes, bytes]]) -> bool:
    """Whether the request carries `Service-Worker: script`, a worker's update check."""
    return any(value.strip().lower() == b"script"
               for value in _values(headers, b"service-worker"))


def is_navigation(method: str, headers: Iterable[tuple[bytes, bytes]]) -> bool:
    """Whether the request is a GET a browser sends to load a document."""
    headers = list(headers)
    return (method == "GET" and _single(headers, b"sec-fetch-mode") == b"navigate"
            and _single(headers, b"sec-fetch-dest") == b"document")


def cookie_ids(headers: Iterable[tuple[bytes, bytes]]) -> list[str]:
    """Every value of the session cookie the request carries, in order.

    Cookies ignore ports, so another server on 127.0.0.1 can add a `moxi-ui` cookie of
    its own beside ours; each is tried, and one that names no session is ignored.
    """
    found = []
    for value in _values(headers, b"cookie"):
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError:
            continue
        for part in text.split(";"):
            name, sep, item = part.strip().partition("=")
            if sep and name == COOKIE and _TOKEN_SHAPE.match(item):
                found.append(item)
    return found


def _key(headers: Iterable[tuple[bytes, bytes]]) -> str | None:
    value = _single(headers, KEY_HEADER)
    if value is None or not value.isascii():
        return None
    text = value.decode("ascii")
    return text if _TOKEN_SHAPE.match(text) else None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


# --- WSL and the owner check ----------------------------------------------------------


def is_wsl(root: str | os.PathLike = "/proc") -> bool:
    """Whether this Linux runs under WSL: `osrelease` names `microsoft` in any case, as
    every stock WSL kernel does, or `WSLInterop` exists (a custom kernel with interop)."""
    root = Path(root)
    try:
        release = (root / "sys/kernel/osrelease").read_text(encoding="utf-8", errors="replace")
    except OSError:
        release = ""
    if "microsoft" in release.lower():
        return True
    return (root / "sys/fs/binfmt_misc/WSLInterop").exists()


def _address(text: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, int] | None:
    """A `/proc/net/tcp` address, `0100007F:1F90`: each 32-bit word of the address in
    this machine's byte order, then the port in hex."""
    host, sep, port = text.partition(":")
    if not sep or len(host) not in (8, 32):
        return None
    try:
        raw = b"".join(int(host[i:i + 8], 16).to_bytes(4, sys.byteorder)
                       for i in range(0, len(host), 8))
        return ipaddress.ip_address(raw), int(port, 16)
    except ValueError:
        return None


def _endpoint(pair: Any) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, int] | None:
    if not isinstance(pair, (tuple, list)) or len(pair) != 2:
        return None
    host, port = pair
    if not isinstance(host, str) or not isinstance(port, int):
        return None
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]), port
    except ValueError:
        return None


def socket_owner(client: Any, server: Any, *, root: str | os.PathLike = "/proc") -> int | None:
    """The uid that owns the client end of the TCP connection from `client` to `server`
    (each `(host, port)`), read from `/proc/net/tcp` or `tcp6`: the row whose local
    address is the client's and whose remote address is the server's. None when no row,
    or rows of more than one uid, match."""
    peer, local = _endpoint(client), _endpoint(server)
    if peer is None or local is None:
        return None
    table = "tcp6" if peer[0].version == 6 else "tcp"
    try:
        lines = (Path(root) / "net" / table).read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    uids = set()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 8:
            continue
        if _address(fields[1]) == peer and _address(fields[2]) == local:
            try:
                uids.add(int(fields[7]))
            except ValueError:
                return None
    return uids.pop() if len(uids) == 1 else None


@dataclass(frozen=True)
class OwnerCheck:
    """Whether a connection's client socket is this user's. Disabled where it can't be
    read: macOS, Windows and WSL."""

    enabled: bool
    uid: int | None = None
    root: str = "/proc"

    @classmethod
    def here(cls) -> OwnerCheck:
        """The check for this system: on Linux other than WSL, against this user's uid."""
        if sys.platform != "linux" or is_wsl():
            return cls(False)
        return cls(True, os.getuid())

    def allows(self, client: Any, server: Any) -> bool:
        if not self.enabled:
            return True
        return socket_owner(client, server, root=self.root) == self.uid

    def allows_current(self) -> bool:
        """The check on the connection the worker layer recorded for this request."""
        if not self.enabled:
            return True
        current = connection.get()
        if current is None:
            return False
        return self.allows(*current)


# --- tickets --------------------------------------------------------------------------


class Tickets:
    """One-use sign-in tickets: files in the shell's runtime dir, named by the first 32
    hex of the ticket's SHA-256, each holding its expiry.

    Only the redemption whose unlink succeeds spends a ticket, so two can't both win.
    """

    def __init__(self, directory: str | os.PathLike, *, ttl: float = TICKET_TTL,
                 clock: Callable[[], float] = time.time):
        self.directory = Path(directory)
        self.ttl = ttl
        self.clock = clock

    def _path(self, ticket: str) -> Path:
        return self.directory / _hash(ticket)[:32]

    def mint(self) -> str:
        """A new ticket, written (0600) in a directory only this user can enter."""
        files.private_dir(self.directory)
        ticket = secrets.token_urlsafe(32)
        data = json.dumps({"expires": self.clock() + self.ttl}).encode("ascii")
        files.atomic_write(self._path(ticket), data, mode=0o600)
        return ticket

    def redeem(self, ticket: object) -> bool:
        """Spend `ticket`. True only for a ticket minted here, not expired, not spent."""
        try:
            if not isinstance(ticket, str) or not _TOKEN_SHAPE.match(ticket):
                return False
            path = self._path(ticket)
            expires = _expiry(path)
            try:
                os.unlink(path)
            except OSError:
                return False
            return expires is not None and self.clock() < expires
        finally:
            self.sweep()

    def sweep(self) -> None:
        """Remove every expired or unreadable ticket file."""
        try:
            names = os.listdir(self.directory)
        except OSError:
            return
        now = self.clock()
        for name in names:
            if name.startswith("."):
                continue
            path = self.directory / name
            expires = _expiry(path)
            if expires is None or now >= expires:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _expiry(path: Path) -> float | None:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    expires = data.get("expires") if isinstance(data, dict) else None
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        return None
    return float(expires)


# --- the session store ----------------------------------------------------------------


@dataclass
class _Record:
    key: str
    created: float
    used: float
    saved_used: float


class SessionStore:
    """The live sessions, by the SHA-256 of their ids.

    With an `epoch` and a `path`, every change is written to the file, tagged with the
    epoch, and the last-used time at most once an hour; without an epoch, nothing is.
    Build one with `open_store`.

    `watch` calls back when a session ends, however it ends: signed out, ended by a
    worker's update check, evicted past the limit, or found expired.
    """

    def __init__(self, *, epoch: str | None, path: Path | None,
                 clock: Callable[[], float] = time.time):
        self.epoch = epoch
        self.path = path if epoch is not None else None
        self.clock = clock
        self._sessions: dict[str, _Record] = {}
        self._watches: dict[str, list[Callable[[], None]]] = {}
        self._ending: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    # The file.

    def _load(self) -> None:
        assert self.path is not None
        data = _read_store(self.path)
        if data is None or data.get("epoch") != self.epoch:
            self._save()
            return
        sessions = data.get("sessions")
        if isinstance(sessions, dict):
            for id_hash, item in sessions.items():
                record = _record(item)
                if record is not None and isinstance(id_hash, str):
                    self._sessions[id_hash] = record
        self._prune()
        self._save()

    def _save(self) -> None:
        if self.path is None:
            return
        data = {"format": _STORE_FORMAT, "epoch": self.epoch, "sessions": {
            id_hash: {"key": r.key, "created": r.created, "used": r.used}
            for id_hash, r in self._sessions.items()}}
        text = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"
        files.update(self.path, lambda _old: text, mode=0o600)
        for record in self._sessions.values():
            record.saved_used = record.used

    # The sessions.

    def _prune(self) -> bool:
        now = self.clock()
        gone = [h for h, r in self._sessions.items()
                if now - r.created >= MAX_AGE or now - r.used >= IDLE]
        for id_hash in gone:
            self._drop(id_hash)
        return bool(gone)

    def _drop(self, id_hash: str) -> bool:
        """Remove one session, holding the lock; its watches are called once the lock is
        let go (`_ended`)."""
        if self._sessions.pop(id_hash, None) is None:
            return False
        self._ending.extend(self._watches.pop(id_hash, ()))
        return True

    def _ended(self) -> None:
        """Call the watches of the sessions dropped under the lock just let go."""
        with self._lock:
            calls, self._ending = self._ending, []
        for call in calls:
            try:
                call()
            except Exception:  # noqa: BLE001 - one watch never stops the others
                _log.exception("a session watch failed")

    def watch(self, id_hash: str, on_end: Callable[[], None]) -> Callable[[], None]:
        """Call `on_end()` once, from whichever thread ends it, when the session
        `id_hash` ends; at once when it is already gone. Returns the call that drops the
        watch, for a request that ended first."""
        with self._lock:
            if id_hash in self._sessions:
                self._watches.setdefault(id_hash, []).append(on_end)

                def stop() -> None:
                    with self._lock:
                        found = self._watches.get(id_hash)
                        if found is not None and on_end in found:
                            found.remove(on_end)
                            if not found:
                                del self._watches[id_hash]

                return stop
        on_end()
        return lambda: None

    def mint(self) -> tuple[str, str]:
        """A new session: `(session id, key)`. The oldest is evicted past the limit."""
        session_id, key = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = self.clock()
        with self._lock:
            self._prune()
            while len(self._sessions) >= LIMIT:
                self._drop(min(self._sessions, key=lambda h: self._sessions[h].created))
            self._sessions[_hash(session_id)] = _Record(_hash(key), now, now, now)
            self._save()
        self._ended()
        return session_id, key

    def find(self, session_ids: Sequence[str], key: str | None = None, *,
             need_key: bool) -> str | None:
        """The id hash of the first live session among `session_ids` (and, with
        `need_key`, whose key is `key`), marked used; or None."""
        try:
            with self._lock:
                if self._prune():
                    self._save()
                for session_id in session_ids:
                    id_hash = _hash(session_id)
                    record = self._sessions.get(id_hash)
                    if record is None:
                        continue
                    if need_key and (key is None
                                     or not hmac.compare_digest(record.key, _hash(key))):
                        continue
                    now = self.clock()
                    record.used = now
                    if now - record.saved_used >= TOUCH_EVERY:
                        self._save()
                    return id_hash
            return None
        finally:
            self._ended()

    def end(self, session_ids: Iterable[str]) -> int:
        """End the sessions these ids name; returns how many there were."""
        with self._lock:
            gone = 0
            for session_id in session_ids:
                if self._drop(_hash(session_id)):
                    gone += 1
            if gone:
                self._save()
        self._ended()
        return gone

    def clear(self) -> None:
        """End every session."""
        with self._lock:
            for id_hash in list(self._sessions):
                self._drop(id_hash)
            self._save()
        self._ended()

    def live(self) -> int:
        """How many sessions are live now."""
        with self._lock:
            if self._prune():
                self._save()
            count = len(self._sessions)
        self._ended()
        return count


def _read_store(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("format") == _STORE_FORMAT else None


def _record(item: object) -> _Record | None:
    if not isinstance(item, dict):
        return None
    key, created, used = item.get("key"), item.get("created"), item.get("used")
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
        return None
    if any(isinstance(t, bool) or not isinstance(t, (int, float)) for t in (created, used)):
        return None
    return _Record(key, float(created), float(used), float(used))


def store_path(state_dir: str | os.PathLike) -> Path:
    return Path(state_dir) / STORE_FILE


def open_store(state_dir: str | os.PathLike, epoch: str | None, *,
               clock: Callable[[], float] = time.time) -> SessionStore:
    """The shell's session store for the hold it serves under.

    `epoch` names the hold: the socket units' activation, when the shell has one, else
    None. With an epoch, the sessions in `<state_dir>/sessions.json` tagged with that same
    epoch are honoured, and a file tagged with any other is rewritten with this epoch and
    no sessions. With None the file is neither read nor written and sessions live in
    memory only, so they end with the shell.
    """
    if epoch is not None and (not isinstance(epoch, str) or not epoch):
        raise ValueError("an epoch is a non-empty string, or None")
    store = SessionStore(epoch=epoch, path=store_path(state_dir), clock=clock)
    if epoch is not None:
        store._load()
    return store


def clear_stored(state_dir: str | os.PathLike) -> bool:
    """End every session in `<state_dir>/sessions.json`, keeping its epoch, for a signout
    while no shell runs. False when there is no such file."""
    path = store_path(state_dir)
    data = _read_store(path)
    if data is None:
        return False
    text = json.dumps({"format": _STORE_FORMAT, "epoch": data.get("epoch"), "sessions": {}},
                      separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"
    files.update(path, lambda _old: text, mode=0o600)
    return True


# --- the session callable -------------------------------------------------------------


class Sessions:
    """The shell's browser session: the session callable for its `browser` profile, and
    what the router asks of a request's cookie.

    `token()` gives the shell's runtime token once its runtime file is written, None
    before. `owner` is the connection owner check (`OwnerCheck.here()` in the shell).
    """

    def __init__(self, store: SessionStore, tickets: Tickets, *, owner: OwnerCheck,
                 token: Callable[[], str | None]):
        self.store = store
        self.tickets = tickets
        self.owner = owner
        self.token = token

    def __call__(self, view: RequestView, entry: Entry | None) -> bool:
        """Gate 4 of `check`: True to admit, by the class of the one entry `check` found.
        A request a session admits has that session's id hash in `admitted`."""
        admitted.set(None)
        headers = view.headers
        if entry is not None and entry.raw_path == WORKER_PATH:
            return entry.method == "GET" and is_worker_request(headers)
        if is_navigation(view.method, headers):
            return True
        if entry is None:
            return self._admit(self.signed_in(headers))
        path = entry.raw_path
        if path in _OPEN_PATHS:
            return True
        if path in TOKEN_PATHS:
            return self._token_class(view)
        if entry.kind == "asset" or path == "/" or _is_module_door(path):
            return self._admit(self.signed_in(headers))
        return self._admit(self.signed_in(headers, need_key=True))

    def _admit(self, id_hash: str | None) -> bool:
        if id_hash is None:
            return False
        admitted.set(id_hash)
        return True

    def _token_class(self, view: RequestView) -> bool:
        token = self.token()
        if token is None:
            return False
        if any(key.lower() in _BROWSER_HEADERS or key.lower().startswith(b"sec-fetch-")
               for key, _ in view.headers):
            return False
        return token_matches(view, token)

    def signed_in(self, headers: Iterable[tuple[bytes, bytes]], *,
                  need_key: bool = False) -> str | None:
        """The id hash of the live session the request's cookie (and, with `need_key`,
        its key) names, on a connection the owner check passes; else None."""
        headers = list(headers)
        ids = cookie_ids(headers)
        if not ids or not self.owner.allows_current():
            return None
        return self.store.find(ids, _key(headers), need_key=need_key)

    def end_named(self, headers: Iterable[tuple[bytes, bytes]]) -> int:
        """End every session the request's cookie names (a worker's update check)."""
        return self.store.end(cookie_ids(headers))

    def sign_in(self, ticket: object) -> tuple[str, str] | None:
        """Redeem `ticket` and mint a session: `(session id, key)`, or None.

        On a connection another user owns, the ticket is left unspent.
        """
        if not self.owner.allows_current():
            return None
        if not self.tickets.redeem(ticket):
            return None
        return self.store.mint()


def _is_module_door(path: str) -> bool:
    # `/m/<module>` with no trailing slash, which redirects to the module's panel.
    from moxi.names import is_module_name

    parts = path.split("/")
    return len(parts) == 3 and parts[1] == "m" and is_module_name(parts[2])


# --- the static answers ---------------------------------------------------------------

#: An answer the router sends as it is: status, ASGI header pairs and body.
Static = tuple[int, list[tuple[bytes, bytes]], bytes]

_WORKER_SCRIPT = (
    b'self.addEventListener("install", () => self.skipWaiting());\n'
    b'self.addEventListener("activate", (event) => '
    b"event.waitUntil(self.registration.unregister()));\n"
)


def worker_answer() -> Static:
    """The answer to a worker's update check: a script that installs at once and then
    unregisters itself, so a worker the shell never registered is gone. No
    `Clear-Site-Data`: with it, a browser may never install the script."""
    headers = response_headers(extra=[
        ("Content-Type", "text/javascript; charset=utf-8"),
        ("Service-Worker-Allowed", "/"),
        ("Content-Length", str(len(_WORKER_SCRIPT))),
    ])
    return 200, headers, _WORKER_SCRIPT


_SIGNIN_SCRIPT = f"""(async () => {{
  const say = document.getElementById("say");
  const ticket = new URLSearchParams(location.hash.slice(1)).get("t");
  history.replaceState(null, "", location.pathname);
  const fail = () => {{
    say.textContent = "This sign-in link was used or has expired. Run moxi ui open for a new one.";
  }};
  if (!ticket) {{ fail(); return; }}
  try {{
    const answer = await fetch("{SIGNIN_API}", {{
      method: "POST", credentials: "same-origin",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{ticket}}),
    }});
    if (!answer.ok) {{ fail(); return; }}
    const data = await answer.json();
    localStorage.setItem("{KEY_STORAGE}", data.key);
    location.replace("/m/ui/");
  }} catch (err) {{ fail(); }}
}})();"""

_SIGNIN_PAGE = (
    '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    "<title>Moxi: signing in</title></head>\n"
    '<body><p id="say">Signing in...</p>\n'
    f"<script>{_SIGNIN_SCRIPT}</script></body></html>\n"
).encode("utf-8")

_SIGNED_OUT_PAGE = (
    '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    "<title>Moxi: signed out</title></head>\n"
    "<body><p>This browser is not signed in to Moxi. Run <code>moxi ui open</code> "
    "to sign in.</p></body></html>\n"
).encode("utf-8")


def _with_script_hash(headers: list[tuple[bytes, bytes]], script: str) -> list[tuple[bytes, bytes]]:
    # The base's policy allows scripts from the origin only. The sign-in page can load
    # none before sign-in, so its one inline script is allowed by its hash instead.
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    out = []
    for name, value in headers:
        if name == b"content-security-policy":
            changed = value.replace(b"script-src 'self'", f"script-src 'sha256-{digest}'".encode())
            if changed == value:
                raise RuntimeError("the base's policy no longer has script-src 'self'")
            value = changed
        out.append((name, value))
    return out


def signin_page() -> Static:
    """The sign-in page. Its answer clears the origin's HTTP cache and storage before the
    page runs, so nothing a squatter left there outlasts a sign-in; it never clears
    cookies, which would clear those of every port on 127.0.0.1."""
    headers = response_headers(extra=[
        ("Content-Type", "text/html; charset=utf-8"),
        ("Clear-Site-Data", '"cache", "storage"'),
        ("Content-Length", str(len(_SIGNIN_PAGE))),
    ])
    return 200, _with_script_hash(headers, _SIGNIN_SCRIPT), _SIGNIN_PAGE


def signed_out_page() -> Static:
    """The page a navigation without a valid cookie gets."""
    headers = response_headers(extra=[
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(_SIGNED_OUT_PAGE))),
    ])
    return 200, headers, _SIGNED_OUT_PAGE


def signin_answer(session_id: str, key: str) -> Static:
    """The sign-in API's answer: the cookie, and the key in the body."""
    body = json.dumps({"key": key}, separators=(",", ":")).encode("ascii")
    headers = response_headers(extra=[
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ])
    headers.append((b"set-cookie", (f"{COOKIE}={session_id}; HttpOnly; SameSite=Strict; "
                                    f"Path=/; Max-Age={MAX_AGE}").encode("ascii")))
    return 200, headers, body


def signin_url(port: int, ticket: str) -> str:
    """The sign-in URL: a fresh `n` each time, so no cached answer can match it, and the
    ticket in the fragment, which is never sent in a request line or logged."""
    return f"http://127.0.0.1:{port}{SIGNIN_PATH}?n={secrets.token_hex(8)}#t={ticket}"

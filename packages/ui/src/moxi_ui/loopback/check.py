"""The request check: five gates in one call, on every listener.

The threat model: module pages are trusted code like their Python half; Moxi defends
against the network and other users on the machine, not a module you chose to install.

Everything a gate decides on is in `check`'s two arguments: the request view, and a
profile holding the credential, the route table and the admitted Host names. A profile
and its table never change once built. A listener builds its profile at start and may
replace it with a new one, never change it, so each request is checked against the one
profile current when it arrived. No gate reads anything else.

The gates, in order:

1. Host. On the `browser` profile, a name in the profile's names with any port. On the
   `upstream` profile over TCP, exactly `127.0.0.1:<bound port>`, the gate that holds
   against DNS rebinding even if the token leaks. Not run on a Unix socket, which no
   browser can open. On the `gateway` profile, exactly the tunnel's host name, with no
   port or the outside port, on either transport.
2. OPTIONS is refused: no preflight ever passes, so a page can add a header such as
   `X-Moxi-Token` to a cross-origin request only by failing here.
3. Origin and Fetch Metadata on the `browser` profile, and on the `gateway` profile
   with the tunnel's `https` origin, where a request carrying `X-Moxi-Token` is refused
   too. On the `upstream` profile over TCP, any `Origin`, `Cookie` or `Sec-Fetch-*`
   header is refused: the shell strips them, so a request that still carries one came
   straight from a browser.
4. The credential: the session callable on the `browser` and `gateway` profiles, or
   `X-Moxi-Token` on the `upstream` profile. `GET /api/prove` alone is exempt.
5. The table: a path or method it does not hold is an empty 404, and an act without
   `Content-Type: application/json` is a 403.

The request is looked up once, before gate 3, and gates 3 to 5 and the session callable
all read that one entry. A refusal at gates 1 to 4, and an act without JSON, is a 403;
a path outside the table is a 404 after the credential, so a caller without one learns
nothing about which paths exist. A 405 is never sent.

This module imports only the standard library.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from moxi_ui.loopback.routes import PROVE_PATH, Entry, Table

# Only the names `moxi_ui.loopback.api` re-exports are public; the rest are the
# package's own.
__all__ = [
    "Profile",
    "Refusal",
    "RequestView",
    "browser",
    "check",
    "gateway",
    "proof",
    "token_matches",
    "upstream",
]

#: The Host names the shell's listener may admit without a further check.
BASE_NAMES = frozenset({"127.0.0.1", "localhost"})

#: The one token shape, `secrets.token_urlsafe(32)`, and the nonce's too.
TOKEN_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{43}\Z")
_PORT = re.compile(r"\A[0-9]{1,5}\Z")
#: A name under `.localhost`, which RFC 6761 reserves, so no one can rebind it.
_LOCALHOST_NAME = re.compile(r"\A(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+localhost\Z")
#: A DNS host name, folded: labels of letters, digits and inner hyphens, joined by dots.
_DNS_NAME = re.compile(r"\A(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                       r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\Z")
#: Four dotted numbers: an IPv4 address, never a gateway's host name.
_DOTTED_NUMBERS = re.compile(r"\A[0-9]+(?:\.[0-9]+){3}\Z")

_PROVE_TEXT = b"moxi-loopback-prove\n"
_PROVE_RAW = PROVE_PATH.encode("ascii")
_TOKEN = b"x-moxi-token"
_NONCE = b"x-moxi-nonce"
_HOST = b"host"
_ORIGIN = b"origin"
#: Headers a browser adds that the shell strips before it forwards a request upstream.
_UPSTREAM_REFUSED = (b"cookie", b"origin")
#: What marks a request as a browser's on the shell's listener: those, and `Referer`.
_BROWSER_HEADERS = (b"cookie", b"origin", b"referer")


@dataclass(frozen=True)
class RequestView:
    """What the check reads of one request.

    `raw_path` is the path bytes as received, before any percent-decoding, without the
    query string. `headers` are `(name, value)` byte pairs as ASGI gives them. `transport`
    is `tcp` or `unix`, and `port` is the listener's bound port, None on a Unix socket.
    """

    method: str
    raw_path: bytes
    headers: Sequence[tuple[bytes, bytes]]
    transport: str
    port: int | None

    def values(self, name: bytes) -> list[bytes]:
        """Every value of the header `name`, compared without regard to case."""
        name = name.lower()
        return [value for key, value in self.headers if key.lower() == name]

    def has(self, name: bytes) -> bool:
        name = name.lower()
        return any(key.lower() == name for key, _ in self.headers)


@dataclass(frozen=True)
class Refusal:
    """A refused request: `status` is 403 or 404, `gate` the gate that refused, 1 to 5."""

    status: int
    gate: int


@dataclass(frozen=True)
class Profile:
    """One listener's check: the credential, the frozen table and the admitted names.

    Build one with `upstream`, `browser` or `gateway`, never directly. `port` is the
    module server's bound port on `upstream`, and the outside port on `gateway`; `host`
    is the gateway's one host name.
    """

    kind: str
    table: Table
    token: str | None = None
    port: int | None = None
    session: Callable[[RequestView, Entry | None], bool] | None = None
    names: frozenset[str] = frozenset()
    fallback: Callable[[RequestView], Entry | None] | None = None
    host: str | None = None


def upstream(table: Table, *, token: str, port: int | None = None) -> Profile:
    """A module server's profile. `port` is its bound TCP port, None on a Unix socket.

    Freezes `table`. Raises `ValueError` for a token of another shape or a port outside
    1 to 65535.
    """
    if not isinstance(table, Table):
        raise ValueError("a profile's table is a Table")
    if not isinstance(token, str) or not TOKEN_PATTERN.match(token):
        raise ValueError("the token is 43 URL-safe base64 characters")
    if port is not None and (not isinstance(port, int) or isinstance(port, bool)
                             or not 1 <= port <= 65535):
        raise ValueError("the port is 1 to 65535, or None on a Unix socket")
    return Profile("upstream", table.freeze(), token=token, port=port)


def _names(names: Iterable[str]) -> frozenset[str]:
    if isinstance(names, (str, bytes)) or not isinstance(names, Iterable):
        raise ValueError("names is a set of host names")
    folded = set()
    for name in names:
        if not isinstance(name, str):
            raise ValueError("names is a set of host names")
        name = name.lower()
        if name not in BASE_NAMES and not _LOCALHOST_NAME.match(name):
            raise ValueError(f"{name!r} is not 127.0.0.1, localhost or a .localhost name")
        folded.add(name)
    # A browser resolves a .localhost name to ::1 as well as 127.0.0.1, so it is safe
    # exactly when localhost is.
    if "localhost" not in folded and any(name.endswith(".localhost") for name in folded):
        raise ValueError("a .localhost name is admitted only beside localhost")
    return frozenset(folded)


def browser(
    table: Table,
    *,
    session: Callable[[RequestView, Entry | None], bool],
    names: Iterable[str],
    fallback: Callable[[RequestView], Entry | None] | None = None,
) -> Profile:
    """The shell's browser-facing profile.

    `session(view, entry)` is gate 4: it returns True to admit. It gets the request's one
    entry, or None for a request that will be the 404, and is called either way.
    `fallback(view)` answers a request `table.find` does not hold: only a GET `builtin`
    entry it returns for a GET request counts, and it is never asked about a raw path
    holding `%`. `names` admits only `127.0.0.1`, `localhost` and names under
    `.localhost`, those only beside `localhost`; any other name raises `ValueError`.
    Freezes `table`.
    """
    if not isinstance(table, Table):
        raise ValueError("a profile's table is a Table")
    if not callable(session):
        raise ValueError("session is a callable taking the view and the entry")
    if fallback is not None and not callable(fallback):
        raise ValueError("fallback is a callable taking the view, or None")
    folded = _names(names)
    return Profile("browser", table.freeze(), session=session, names=folded, fallback=fallback)


def gateway(
    table: Table,
    *,
    session: Callable[[RequestView, Entry | None], bool],
    host: str,
    port: int,
    fallback: Callable[[RequestView], Entry | None] | None = None,
) -> Profile:
    """The profile of the listener a tunnel forwards a phone to.

    `host` is the tunnel's host name and `port` the outside port the phone uses. Gate 1
    admits exactly one `Host` naming `host`, folded to lower case, with no port or with
    `port`; `127.0.0.1`, `localhost` and every other name are refused, on a Unix socket
    too. Gate 3 is the `browser` profile's with the origin `https://<host>`, followed by
    `:<port>` unless the port is 443, and it refuses every request that carries
    `X-Moxi-Token`, so no token route is reachable here. No header a proxy in front adds,
    such as `X-Forwarded-For` or a `Tailscale-*` header, is read. `session` and `fallback`
    are as on `browser`. Raises `ValueError` for a host that is not a DNS name, or is
    `localhost`, a `.localhost` name or an address, and for a port outside 1 to 65535.
    Freezes `table`.
    """
    if not isinstance(table, Table):
        raise ValueError("a profile's table is a Table")
    if not callable(session):
        raise ValueError("session is a callable taking the view and the entry")
    if fallback is not None and not callable(fallback):
        raise ValueError("fallback is a callable taking the view, or None")
    if not isinstance(host, str) or not host.isascii():
        raise ValueError("the host is an ASCII DNS name")
    folded = host.lower()
    if (not _DNS_NAME.match(folded) or folded in BASE_NAMES or folded.endswith(".localhost")
            or _DOTTED_NUMBERS.match(folded)):
        raise ValueError(f"{host!r} is not a host name a tunnel gives")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("the port is 1 to 65535")
    return Profile("gateway", table.freeze(), session=session, port=port, fallback=fallback,
                   host=folded)


def token_matches(view: RequestView, token: str) -> bool:
    """Whether the view carries exactly one `X-Moxi-Token` header equal to `token`.

    None, or two, is False. The compare is constant-time after a length check, and a
    non-ASCII value is refused rather than compared.
    """
    supplied = view.values(_TOKEN)
    if len(supplied) != 1 or not isinstance(token, str) or not token or not token.isascii():
        return False
    value = supplied[0]
    if not value.isascii():
        return False
    expected = token.encode("ascii")
    if len(value) != len(expected):
        return False
    return hmac.compare_digest(value, expected)


def proof(token: str, server_id: str, nonce: str) -> str:
    """The hex HMAC-SHA256, keyed by `token`, of the prove text, `nonce` and `server_id`.

    A server answers `GET /api/prove` with it, and a client computes the same from the
    runtime file to tell the server that wrote the file from any other. Raises
    `ValueError` for a nonce or token of another shape, or a non-ASCII `server_id`.
    """
    if not isinstance(nonce, str) or not TOKEN_PATTERN.match(nonce):
        raise ValueError("the nonce is 43 URL-safe base64 characters")
    if not isinstance(token, str) or not token or not token.isascii():
        raise ValueError("the token is a non-empty ASCII string")
    if not isinstance(server_id, str) or not server_id or not server_id.isascii():
        raise ValueError("the server_id is a non-empty ASCII string")
    message = _PROVE_TEXT + nonce.encode("ascii") + b"\n" + server_id.encode("ascii")
    return hmac.new(token.encode("ascii"), message, hashlib.sha256).hexdigest()


def prove_nonce(view: RequestView) -> str | None:
    """The view's one well-formed `X-Moxi-Nonce`, or None (the server's 400)."""
    values = view.values(_NONCE)
    if len(values) != 1 or not values[0].isascii():
        return None
    nonce = values[0].decode("ascii")
    return nonce if TOKEN_PATTERN.match(nonce) else None


def _single(view: RequestView, name: bytes) -> bytes | None:
    values = view.values(name)
    return values[0] if len(values) == 1 else None


def _upstream_host(view: RequestView, port: int | None) -> bool:
    if port is None:
        return False
    return view.values(_HOST) == [b"127.0.0.1:%d" % port]


def _browser_host(view: RequestView, names: frozenset[str]) -> bool:
    values = view.values(_HOST)
    if len(values) != 1 or not values[0].isascii():
        return False
    host = values[0].decode("ascii")
    if not host or "," in host:
        return False
    if host.startswith("["):
        close = host.find("]")
        if close < 0:
            return False
        name, rest = host[: close + 1], host[close + 1 :]
        if rest and not rest.startswith(":"):
            return False
        digits = rest[1:] if rest else None
    elif ":" in host:
        name, _, digits = host.rpartition(":")
    else:
        name, digits = host, None
    if digits is not None and (not _PORT.match(digits) or not 1 <= int(digits) <= 65535):
        return False
    return name.lower() in names


def _gateway_host(view: RequestView, host: str | None, port: int | None) -> bool:
    values = view.values(_HOST)
    if host is None or len(values) != 1 or not values[0].isascii():
        return False
    name, colon, digits = values[0].decode("ascii").partition(":")
    if colon and (not _PORT.match(digits) or int(digits) != port):
        return False
    return name.lower() == host


def _gateway_origin(profile: Profile) -> bytes:
    suffix = "" if profile.port == 443 else f":{profile.port}"
    return f"https://{profile.host}{suffix}".encode("ascii")


def _lookup(view: RequestView, profile: Profile) -> Entry | None:
    entry = profile.table.find(view.method, view.raw_path)
    if entry is not None or profile.kind == "upstream" or profile.fallback is None:
        return entry
    if b"%" in view.raw_path or view.method != "GET":
        return None
    answer = profile.fallback(view)
    if isinstance(answer, Entry) and answer.kind == "builtin" and answer.method == "GET":
        return answer
    return None


def _carries(view: RequestView, names: tuple[bytes, ...]) -> bool:
    return any(key.lower() in names or key.lower().startswith(b"sec-fetch-")
               for key, _ in view.headers)


def _gate3_browser(view: RequestView, entry: Entry | None) -> bool:
    # A token request carries none of the headers a browser sends; a page can add the
    # token to a cross-origin request only after a preflight, which gate 2 refuses.
    if view.has(_TOKEN) and not _carries(view, _BROWSER_HEADERS):
        return True
    host = _single(view, _HOST)
    if host is None:
        return False
    return _origin_rules(view, entry, b"http://" + host.lower())


def _gate3_gateway(view: RequestView, entry: Entry | None, profile: Profile) -> bool:
    # The gateway has no token class: a request carrying the token is never a phone's.
    if view.has(_TOKEN):
        return False
    return _origin_rules(view, entry, _gateway_origin(profile))


def _origin_rules(view: RequestView, entry: Entry | None, origin: bytes) -> bool:
    """Origin and Fetch Metadata for a browser's request, against the listener's `origin`."""
    origins = view.values(_ORIGIN)
    if len(origins) > 1:
        return False
    origin_ok = not origins or origins[0] == origin
    sites = view.values(b"sec-fetch-site")
    if len(sites) > 1:
        return False
    site = sites[0] if sites else None
    if view.method == "GET" and (entry is None or entry.kind != "act"):
        if origin_ok and site in (None, b"same-origin", b"none"):
            return True
        if entry is not None and entry.kind in ("asset", "builtin"):
            # A bookmark or a link opens the shell; a cross-site fetch of a file does not.
            return (_single(view, b"sec-fetch-mode") == b"navigate"
                    and _single(view, b"sec-fetch-dest") == b"document")
        return False
    # Acts, and any request that is not a GET: Origin is required. Every browser sends
    # one on a POST.
    return bool(origins) and origin_ok and site in (None, b"same-origin")


def _json_content(view: RequestView) -> bool:
    value = _single(view, b"content-type")
    if value is None:
        return False
    return value.split(b";", 1)[0].strip().lower() == b"application/json"


def check(view: RequestView, profile: Profile) -> Refusal | None:
    """None when the request may be dispatched, else the `Refusal` to answer with."""
    upstream_unix = False
    if profile.kind == "upstream":
        if view.transport not in ("tcp", "unix"):
            return Refusal(403, 1)
        upstream_unix = view.transport == "unix"
        if not upstream_unix and not _upstream_host(view, profile.port):
            return Refusal(403, 1)
    elif profile.kind == "browser":
        if not _browser_host(view, profile.names):
            return Refusal(403, 1)
    elif profile.kind == "gateway":
        if not _gateway_host(view, profile.host, profile.port):
            return Refusal(403, 1)
    else:
        return Refusal(403, 1)

    if view.method == "OPTIONS":
        return Refusal(403, 2)

    entry = _lookup(view, profile)

    if profile.kind == "upstream":
        if not upstream_unix and _carries(view, _UPSTREAM_REFUSED):
            return Refusal(403, 3)
    elif profile.kind == "gateway":
        if not _gate3_gateway(view, entry, profile):
            return Refusal(403, 3)
    elif not _gate3_browser(view, entry):
        return Refusal(403, 3)

    # Only the table's own `GET /api/prove` is exempt, never an entry a fallback made, and
    # never on the gateway, which answers no token route and so has no server to prove.
    exempt = (profile.kind != "gateway" and view.method == "GET"
              and view.raw_path == _PROVE_RAW
              and profile.table.find("GET", _PROVE_RAW) is not None)
    if not exempt:
        if profile.kind == "upstream":
            admitted = token_matches(view, profile.token)
        else:
            admitted = profile.session(view, entry) is True
        if not admitted:
            return Refusal(403, 4)

    if entry is None:
        return Refusal(404, 5)
    if entry.kind == "act" and not _json_content(view):
        return Refusal(403, 5)
    return None

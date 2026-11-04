"""`moxi connector check`: the one networked check, and the nonces the door keeps for it.

The door's side, `Probes`: the check registers a nonce over the control socket
(`probe_expect`), and the door remembers a sighting of it when a request carries it as
`X-Moxi-Probe` (`Front` looks at every request, before the Host gate). At most `KEEP`
nonces are registered at once, each for `LIFE` seconds; a fifth drops the oldest. A
value nobody registered is ignored, so a caller can't plant a sighting.

The check's side, `run(origin, ...)`, in the verb's process, in order:

1. `probe_expect` gives a fresh nonce;
2. `GET O/.well-known/oauth-protected-resource/mcp` with `User-Agent: Claude-User` and
   `X-Moxi-Probe: <nonce>`. It passes when the answer is 200 with this door's `resource`
   and the door saw the nonce (`probe_seen`): the request reached this process and wasn't
   answered by an edge's page;
3. an unauthenticated `POST O/mcp`, which must answer 401 with a `resource_metadata`
   challenge.

Nothing here runs unless the user types the verb. The HTTP call is an argument, so a test
drives the steps against a door on loopback.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: The request header the check's nonce travels in, as the ASGI scope writes it.
HEADER = b"x-moxi-probe"
#: The user agent Claude's calls carry, which an edge must let through.
USER_AGENT = "Claude-User"
#: Nonces registered at once.
KEEP = 4
#: Seconds a registered nonce lives.
LIFE = 60.0
#: Seconds each HTTP step may take.
STEP_WAIT = 10.0


class Probes:
    """The registered nonces and their sightings. Thread-safe."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 keep: int = KEEP, life: float = LIFE):
        self._clock = clock
        self.keep = keep
        self.life = life
        self._lock = threading.Lock()
        #: nonce -> [expires, seen], oldest first
        self._nonces: OrderedDict[str, list] = OrderedDict()

    def _drop_expired(self, now: float) -> None:
        for nonce in [n for n, (expires, _) in self._nonces.items() if expires <= now]:
            del self._nonces[nonce]

    def expect(self) -> str:
        """Register a fresh nonce and return it."""
        nonce = secrets.token_hex(16)
        with self._lock:
            now = self._clock()
            self._drop_expired(now)
            while len(self._nonces) >= self.keep:
                self._nonces.popitem(last=False)
            self._nonces[nonce] = [now + self.life, False]
        return nonce

    def saw(self, value: str | None) -> bool:
        """Note a request carrying `value`. True when it was a live registered nonce."""
        if not value:
            return False
        with self._lock:
            self._drop_expired(self._clock())
            entry = self._nonces.get(value.strip())
            if entry is None:
                return False
            entry[1] = True
            return True

    def seen(self, nonce: str) -> bool:
        """Whether a request carried the live registered `nonce`."""
        with self._lock:
            self._drop_expired(self._clock())
            entry = self._nonces.get(nonce)
            return bool(entry and entry[1])


@dataclass
class Reply:
    """One HTTP answer: status, headers by lower-case name, body."""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


#: `fetch(method, url, headers, body)` -> `Reply`, raising `OSError` when it can't connect.
Fetch = Callable[[str, str, dict[str, str], bytes | None], Reply]


def fetch(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Reply:
    """One HTTPS request, through the network, with no redirect followed."""
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with opener.open(req, timeout=STEP_WAIT) as answer:
            return Reply(answer.status, {k.lower(): v for k, v in answer.headers.items()},
                         answer.read(64 * 1024))
    except urllib.error.HTTPError as e:
        return Reply(e.code, {k.lower(): v for k, v in e.headers.items()}, e.read(64 * 1024))
    except urllib.error.URLError as e:
        raise OSError(str(e.reason)) from None


@dataclass
class Step:
    """One step of the check: its name, whether it passed, and what it shows."""

    name: str
    passed: bool
    text: str


def run(origin, *, ask: Callable[[str, object], object], fetch: Fetch = fetch,
        provider: str | None = None) -> list[Step]:
    """The check's three steps against `origin` (a `moxi_connector.origin.Origin`).
    `ask(method, params)` calls the control socket. `provider` is tunnel's provider of
    the address (`tailscale`, `cloudflare`) or None for a typed `public_url`."""
    steps: list[Step] = []
    answer = ask("probe_expect", None)
    nonce = answer.get("nonce") if isinstance(answer, dict) else None
    if not isinstance(nonce, str):
        return [Step("register", False, "the connector didn't give a probe nonce")]
    steps.append(Step("register", True, "the connector registered a probe nonce"))

    url = origin.resource_metadata_url
    try:
        got = fetch("GET", url, {"User-Agent": USER_AGENT, "X-Moxi-Probe": nonce}, None)
    except OSError as e:
        steps.append(Step("reach", False, f"GET {url} didn't connect: {e}"))
        return steps
    seen = ask("probe_seen", {"nonce": nonce})
    seen = isinstance(seen, dict) and seen.get("seen") is True
    resource = _resource(got.body)
    if got.status == 200 and resource == origin.resource and seen:
        steps.append(Step("reach", True,
                          f"GET {url} as {USER_AGENT} reached this connector and answered "
                          "its metadata: the edge lets Claude's user agent through"))
    elif not seen:
        steps.append(Step("reach", False,
                          f"GET {url} answered {got.status}, but the request never reached "
                          f"this connector: something on the way answered it (an edge page, "
                          f"a bot rule, another server on the port)"))
    elif got.status != 200:
        steps.append(Step("reach", False, f"GET {url} reached this connector and answered "
                                          f"{got.status}, not 200"))
    else:
        steps.append(Step("reach", False, f"GET {url} reached this connector, but its "
                                          f"metadata names the resource {resource!r}, not "
                                          f"{origin.resource!r}"))

    mcp = origin.resource
    try:
        got = fetch("POST", mcp, {"User-Agent": USER_AGENT, "Content-Type": "application/json",
                                  "Accept": "application/json, text/event-stream"},
                    b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
    except OSError as e:
        steps.append(Step("challenge", False, f"POST {mcp} didn't connect: {e}"))
    else:
        challenge = got.headers.get("www-authenticate", "")
        if got.status == 401 and "resource_metadata=" in challenge:
            steps.append(Step("challenge", True, f"POST {mcp} without a token answered 401 "
                                                 "pointing at the metadata, as Claude needs"))
        else:
            steps.append(Step("challenge", False,
                              f"POST {mcp} without a token answered {got.status}"
                              + ("" if got.status != 401 else " with no resource_metadata")
                              + ", not 401 with resource_metadata"))
    if provider == "tailscale":
        steps.append(Step("note", True,
                          "A request from this machine to its own ts.net name may stay on "
                          "the tailnet and not cross Funnel; the real test is a phone off "
                          "the tailnet."))
    return steps


def _resource(body: bytes) -> str | None:
    try:
        doc = json.loads(body)
    except ValueError:
        return None
    value = doc.get("resource") if isinstance(doc, dict) else None
    return value if isinstance(value, str) else None


def passed(steps: list[Step]) -> bool:
    return bool(steps) and all(s.passed for s in steps)


__all__ = ["Fetch", "HEADER", "KEEP", "LIFE", "Probes", "Reply", "STEP_WAIT", "Step",
           "USER_AGENT", "fetch", "passed", "run"]

"""Pairing by QR: the pairing code, its URL, and the pairing state a spent code makes.

The signed-in desktop (or `moxi ui gateway pair`) mints a pairing code, a one-use ticket
file in `<runtime_dir>/gateway-pair/` that expires 120 s later: the shell's own ticket
files, the ones `moxi ui open` signs in with. The URL carries the code in its fragment,
which no browser sends in a request line, so it is never logged; `n` is new for each URL,
so no cached answer can match it.

The phone spends the code at `/api/pair/begin`: only the redemption whose unlink succeeds
spends it, so two can't both win. Only a spent code makes a pairing state, which lives
120 s in memory, and exactly one finish takes it, by compare-and-delete under one lock:
the passkey finish or the finish without one, never both and never twice.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..session import Tickets

__all__ = ["PAIR_DIR", "PAIR_TTL", "NoAddress", "Pairing", "PairingStates", "mint", "pair_url",
           "tickets"]

#: The pairing codes' directory under the ui's runtime directory.
PAIR_DIR = "gateway-pair"
#: Seconds a pairing code, and the pairing state it makes, live.
PAIR_TTL = 120
PAIR_PATH = "/pair"


class NoAddress(Exception):
    """The gateway has no address to pair at."""


def tickets(runtime_dir: str | os.PathLike, *,
            clock: Callable[[], float] = time.time) -> Tickets:
    """The pairing codes of the shell whose runtime directory is `runtime_dir`."""
    return Tickets(Path(runtime_dir) / PAIR_DIR, ttl=PAIR_TTL, clock=clock)


def pair_url(host: str, port: int, code: str) -> str:
    """`https://<host>[:<port>]/pair?n=<16 hex>#c=<code>`, with no port for 443."""
    origin = f"https://{host}" if port == 443 else f"https://{host}:{port}"
    return f"{origin}{PAIR_PATH}?n={secrets.token_hex(8)}#c={code}"


def mint(runtime_dir: str | os.PathLike, address: Any, *,
         clock: Callable[[], float] = time.time) -> str:
    """Mint a pairing code for `address` (the tunnel's `Address`) and return its URL.
    Raises `NoAddress` when there is none."""
    if address is None:
        raise NoAddress("the gateway has no address; run `moxi ui gateway enable`")
    code = tickets(runtime_dir, clock=clock).mint()
    return pair_url(address.host, address.port, code)


@dataclass(frozen=True)
class Pairing:
    """One pairing state: its id, the device's name, the passkey's `user.id`, the creation
    challenge, and when it ends."""

    id: str
    name: str
    user_id: bytes
    challenge: bytes
    expires: float


class PairingStates:
    """The live pairing states, in memory."""

    def __init__(self, *, ttl: float = PAIR_TTL, clock: Callable[[], float] = time.time):
        self.ttl = ttl
        self.clock = clock
        self._states: dict[str, Pairing] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        for old in [i for i, state in self._states.items() if state.expires <= now]:
            del self._states[old]

    def begin(self, name: str) -> Pairing:
        """A new state, for a code the caller has just spent."""
        now = self.clock()
        state = Pairing(secrets.token_urlsafe(32), name, secrets.token_bytes(16),
                        secrets.token_bytes(32), now + self.ttl)
        with self._lock:
            self._prune(now)
            self._states[state.id] = state
        return state

    def take(self, pairing_id: object) -> Pairing | None:
        """The live state `pairing_id` names, removed in the same step; None when none
        is live. Only one caller ever gets a given state."""
        if not isinstance(pairing_id, str):
            return None
        with self._lock:
            self._prune(self.clock())
            return self._states.pop(pairing_id, None)

    def live(self) -> int:
        with self._lock:
            self._prune(self.clock())
            return len(self._states)

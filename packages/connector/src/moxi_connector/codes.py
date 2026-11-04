"""The one-time sign-in codes, minted at the owner's terminal, and the gate in front of the
mint.

A code is 8 characters of Crockford base32 from `secrets`, shown as `XXXX-XXXX` (40 bits).
The connector keeps only its SHA-256, its label and an expiry `LIFE` ahead, in this
process's memory: no code is ever written to disk, to the config or to the log. At most
`LIVE_MAX` codes live at once, and one more drops the oldest; only the gate's callers can
mint, so only they evict.

Typed into the sign-in page, a code is normalised (upper case, no dash or spaces, `O` read
as `0`, `I` and `L` as `1`), hashed and compared with each live code in constant time. A
match spends it. A miss counts toward `BURN_AFTER` misses across every sign-in in
`BURN_WINDOW` seconds; at that count every live code is dropped.

**The mint gate.** `mint_code`, a method of the control socket, mints only when at least
one started provider declares a gate and no declared gate refuses. Each gate's `peer`
check runs on the socket's peer before every mint; a check that raises refuses. With no
gate, nothing is minted: a connector that can't tell an agent's process from the owner's
terminal can't keep the sign-in his alone. The verb's own `shell` check runs in
`moxi connector code`, before it asks the socket.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("moxi.connector.codes")

#: Crockford's base32: no I, L, O or U.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
#: Characters in a code.
LENGTH = 8
#: Seconds a code lives.
LIFE = 300
#: Codes live at once; one more drops the oldest.
LIVE_MAX = 3
#: Misses across every sign-in, within `BURN_WINDOW` seconds, that drop every live code.
BURN_AFTER = 20
BURN_WINDOW = 600
#: The longest label a code (and the grant it makes) carries.
LABEL_MAX = 64

#: The control socket's errors for a refused mint, outside JSON-RPC's reserved range.
REFUSED = 4030      # a gate refused
NO_GATE = 4031      # no started provider declares a gate
BAD_PARAMS = -32602

NO_GATE_TEXT = ("No installed module can tell an agent session from your terminal, so no "
                "code is minted. comms provides that check.")
GATE_FAILED = ("The mint gate of {module} failed, so no code is minted. The connector's "
               "log says why.")
USE_IT = ("Type this into the sign-in page that Claude opened. Use it only for a sign-in "
          "you started yourself.")

_READ_AS = str.maketrans({"O": "0", "I": "1", "L": "1"})


def new_code() -> str:
    """A fresh code, as the terminal shows it: `XXXX-XXXX`."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


def normalise(typed: str) -> str:
    """A typed code as it was minted: upper case, no dash or white space, `O` as `0`, `I`
    and `L` as `1`."""
    kept = "".join(ch for ch in typed.upper() if ch != "-" and not ch.isspace())
    return kept.translate(_READ_AS)


def digest(code: str) -> str:
    """The SHA-256 hex of a code, normalised."""
    return hashlib.sha256(normalise(code).encode()).hexdigest()


def clean_label(label: Any) -> str | None:
    """A label as a code keeps it: text with control and format characters removed, cut to
    `LABEL_MAX`; None when there is none left."""
    from .grants import _shown

    kept = _shown(label, LABEL_MAX)
    kept = kept.strip() if kept else ""
    return kept or None


@dataclass
class Live:
    """One live code: its hash, label and expiry."""

    hash: str
    label: str | None
    expires: float


@dataclass(frozen=True)
class Minted:
    """What a mint answers: the code as shown, its expiry and label."""

    code: str
    expires: float
    label: str | None


class Codes:
    """The live codes of one process, and the misses that can burn them."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.live: list[Live] = []
        self._misses: deque[float] = deque()

    def mint(self, label: str | None = None) -> Minted:
        """A new code, kept as its hash; the oldest live code is dropped past `LIVE_MAX`."""
        self._expire()
        code = new_code()
        expires = self.clock() + LIFE
        self.live.append(Live(digest(code), clean_label(label), expires))
        while len(self.live) > LIVE_MAX:
            self.live.pop(0)
        return Minted(code, expires, clean_label(label))

    def take(self, typed: str) -> Live | None:
        """Spend the live code `typed` names and return it, or None. Every live code is
        compared, in constant time, whatever matches first."""
        self._expire()
        h = digest(typed).encode()
        found = None
        for live in self.live:
            if hmac.compare_digest(live.hash.encode(), h) and found is None:
                found = live
        if found is not None:
            self.live.remove(found)
        return found

    def miss(self) -> bool:
        """Count one miss. True when it reached `BURN_AFTER` in `BURN_WINDOW` and every
        live code was dropped."""
        now = self.clock()
        self._misses.append(now)
        while self._misses and self._misses[0] <= now - BURN_WINDOW:
            self._misses.popleft()
        if len(self._misses) < BURN_AFTER:
            return False
        self._misses.clear()
        self.live.clear()
        return True

    def count(self) -> int:
        self._expire()
        return len(self.live)

    def _expire(self) -> None:
        now = self.clock()
        self.live = [c for c in self.live if c.expires > now]


# -- the mint, behind the gate -------------------------------------------------------------

def mint_method(oauth, registry):
    """The control socket's `mint_code` for `oauth` (whose `codes` it mints into and whose
    call log it writes), behind the gates of `registry`'s started providers.

    Params: `{"label": <text>}` or nothing. Answers `{"code", "expires", "label"}`.
    Refuses with `moxi.ipc.RpcError`: `NO_GATE` when no gate is loaded, `REFUSED` with the
    gate's own words when one refuses (its module in the error's data)."""

    async def mint_code(peer, params: object) -> dict[str, Any]:
        from moxi import ipc

        if params is None:
            params = {}
        if not isinstance(params, dict) or not isinstance(params.get("label", ""),
                                                          (str, type(None))):
            raise ipc.RpcError(BAD_PARAMS, "mint_code takes {\"label\": <text>}")
        gates = registry.gates()
        if not gates:
            await oauth.event("code-refused", check="gate", refusal=NO_GATE_TEXT)
            raise ipc.RpcError(NO_GATE, NO_GATE_TEXT, {"check": "gate"})
        for module, gate in gates:
            try:
                said = await asyncio.to_thread(gate.peer, peer)
            except Exception:  # noqa: BLE001 - a gate that fails never lets a mint through
                log.exception("the mint gate of %s failed on pid %s", module,
                              getattr(peer, "pid", None))
                said = GATE_FAILED.format(module=module)
            if said is not None:
                said = str(said)
                await oauth.event("code-refused", check="peer", module=module,
                                  refusal=said, pid=getattr(peer, "pid", None))
                raise ipc.RpcError(REFUSED, said, {"check": "peer", "module": module})
        minted = oauth.codes.mint(params.get("label"))
        await oauth.event("code-minted", label=minted.label, expires=minted.expires,
                          pid=getattr(peer, "pid", None))
        return {"code": minted.code, "expires": minted.expires, "label": minted.label}

    return mint_code


__all__ = ["ALPHABET", "BURN_AFTER", "BURN_WINDOW", "Codes", "LABEL_MAX", "LENGTH", "LIFE",
           "LIVE_MAX", "Live", "Minted", "NO_GATE", "NO_GATE_TEXT", "REFUSED", "USE_IT",
           "clean_label", "digest", "mint_method", "new_code", "normalise"]

"""The grants file: the sign-in's clients and grants, as hashes only.

    STATE/connector/grants/grants.json     the directory 0700, the file 0600

    {"version": 1,
     "clients": {"<client id>": {"registered": t, "held": false, "secret": "<sha256>" | null,
                                 "metadata": {... what the client registered, no secret}}},
     "grants":  {"<8 hex>": {"client": id, "label": str | null, "resource": "O/mcp",
                             "made": t, "used": t | null, "refreshed": t,
                             "access": "<sha256>", "access_expires": t,
                             "refresh": "<sha256>", "retired": ["<sha256>", ...],
                             "previous": "<sha256>" | null, "rotated": t | null,
                             "replacement_used": bool,
                             "revoked": t | null, "revoked_by": str | null}}}

No token, code or client secret rests here in clear: each is kept as its SHA-256, so a copy
of the file grants nothing.

**No writer undoes another's change.** Every write goes through `Store.change`: it takes
the file's lock, reads the file again under it, changes only the fields its caller owns,
and writes the whole file atomically (`moxi.files.update`). The writers and their fields:

- the connector's process: new clients, the drop of clients that never held a grant past
  `UNFINISHED_KEEP`, new grants and the client's `held` mark, rotations and retired
  hashes, last use, the drop of a grant past its lifetime, the revoke of a grant on
  refresh reuse, and the revoke of every grant at a stop with the switch off;
- `moxi connector revoke <id>`: that grant's revoked mark;
- `moxi connector revoke --all`: every grant's revoked mark, and the removal of every
  client;
- `moxi connector disable`: every grant's revoked mark.

A reader (`Store.view`) keeps the file's parsed form and reads it again whenever its
mtime, size or inode changes, so a revoke from the command line takes effect on the next
call whether the process was up or down when it ran.

A grant is **live** while it is not revoked and less than its lifetime has passed since
its last refresh. It is **stale** while it is live and its resource is not the current
`O/mcp`: an address change, answered 401 until the address comes back.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("moxi.connector.grants")

MODULE = "connector"
#: The store's directory under the module's state directory, and the file in it.
GRANTS_DIR = "grants"
GRANTS_FILE = "grants.json"
VERSION = 1
#: At most this many clients that never held a grant exist at once.
UNFINISHED_MAX = 64
#: Seconds a client that never held a grant is kept after it registered.
UNFINISHED_KEEP = 24 * 3600
#: Seconds an access token lives.
ACCESS_LIFE = 3600
#: Seconds after a rotation within which the retired refresh token is still answered, while
#: the pair that replaced it has never been used.
GRACE = 600
#: Retired refresh-token hashes a grant keeps (about 83 days at one rotation an hour).
RETIRED_KEEP = 2000
#: Seconds in a day, for `refresh_days`.
DAY = 86400


def digest(secret: str) -> str:
    """The SHA-256 of a token, code or secret, in hex: the only form the file keeps."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def same(a: str | None, b: str | None) -> bool:
    """Two hashes compared in constant time; None never matches."""
    return a is not None and b is not None and hmac.compare_digest(a, b)


def new_token() -> str:
    """A fresh access token, refresh token or OAuth code: 256 bits."""
    return secrets.token_urlsafe(32)


def grants_path(env: Mapping[str, str] | None = None) -> Path:
    """`STATE/connector/grants/grants.json`."""
    from moxi.paths import Layout

    return Layout(MODULE).state_dir(env) / GRANTS_DIR / GRANTS_FILE


def empty() -> dict[str, Any]:
    return {"version": VERSION, "clients": {}, "grants": {}}


def parse(raw: bytes | None) -> dict[str, Any]:
    """The file's bytes as its mapping; None (no file) is empty. Raises `ValueError` for a
    file that isn't the grants file, so no writer overwrites one it can't read."""
    if raw is None or not raw.strip():
        return empty()
    data = json.loads(raw)
    if (not isinstance(data, dict) or not isinstance(data.get("clients"), dict)
            or not isinstance(data.get("grants"), dict)):
        raise ValueError("the grants file doesn't hold clients and grants")
    return data


def dump(data: Mapping[str, Any]) -> bytes:
    return (json.dumps(data, indent=1, sort_keys=True) + "\n").encode("utf-8")


class Full(Exception):
    """Registration refused: `UNFINISHED_MAX` clients that never held a grant exist."""


class Gone(Exception):
    """The client a grant would be made for is no longer in the file."""


@dataclass(frozen=True)
class Verdict:
    """What a presented refresh token is to its grant: `live` (the grant's current one),
    `grace` (the one just retired, inside its grace), `reuse` (retired, outside it),
    `expired` (the grant's lifetime is over), `revoked`, or `unknown`."""

    kind: str
    grant: str | None = None


def is_live(grant: Mapping[str, Any], now: float, days: int) -> bool:
    return not grant.get("revoked") and now < grant["refreshed"] + days * DAY


def is_stale(grant: Mapping[str, Any], resource: str | None) -> bool:
    """Whether a grant's resource differs from the current `O/mcp`. With no origin nothing
    is served, so nothing reads as stale."""
    return resource is not None and grant.get("resource") != resource


class Store:
    """The grants file, for the process and the command line alike.

    `clock` gives seconds since the epoch; `days` is `refresh_days`."""

    def __init__(self, path: str | os.PathLike | None = None, *, days: int = 30,
                 clock: Callable[[], float] = time.time):
        self.path = Path(path) if path is not None else grants_path()
        self.days = days
        self.clock = clock
        self._lock = threading.Lock()
        self._data = empty()
        self._stamp: tuple[int, int, int] | None = None
        self._access: dict[str, str] = {}
        self._refresh: dict[str, str] = {}
        self._retired: dict[str, str] = {}

    # -- reading ---------------------------------------------------------------------------

    def exists(self) -> bool:
        return self.path.exists()

    def view(self) -> dict[str, Any]:
        """The file as it is now, read again when it changed since the last read. A file
        that can't be read reads as empty, logged: every token is then refused."""
        try:
            st = os.stat(self.path)
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        except FileNotFoundError:
            stamp = (0, 0, 0)
        with self._lock:
            if stamp == self._stamp:
                return self._data
            try:
                data = parse(self.path.read_bytes()) if stamp != (0, 0, 0) else empty()
            except FileNotFoundError:
                data = empty()
            except (OSError, ValueError) as e:
                log.error("the grants file can't be read, so every token is refused: %s", e)
                data = empty()
            self._index(data)
            self._data, self._stamp = data, stamp
            return data

    def _index(self, data: Mapping[str, Any]) -> None:
        self._access, self._refresh, self._retired = {}, {}, {}
        for gid, grant in data["grants"].items():
            if grant.get("access"):
                self._access[grant["access"]] = gid
            if grant.get("refresh"):
                self._refresh[grant["refresh"]] = gid
            for h in grant.get("retired") or ():
                self._retired[h] = gid

    def client(self, client_id: str) -> dict[str, Any] | None:
        found = self.view()["clients"].get(client_id)
        return copy.deepcopy(found) if found is not None else None

    def grant(self, gid: str) -> dict[str, Any] | None:
        found = self.view()["grants"].get(gid)
        return copy.deepcopy(found) if found is not None else None

    def by_access(self, token_hash: str) -> tuple[str, dict[str, Any]] | None:
        data = self.view()
        with self._lock:
            gid = self._access.get(token_hash)
        if gid is None or gid not in data["grants"]:
            return None
        grant = data["grants"][gid]
        return (gid, grant) if same(grant.get("access"), token_hash) else None

    def judge(self, token_hash: str, client_id: str, now: float | None = None) -> Verdict:
        """What a presented refresh token is, read only (`Verdict`)."""
        return _judge(self.view(), token_hash, client_id,
                      self.clock() if now is None else now, self.days, self._find(token_hash))

    def _find(self, token_hash: str) -> str | None:
        self.view()
        with self._lock:
            return self._refresh.get(token_hash) or self._retired.get(token_hash)

    # -- writing ---------------------------------------------------------------------------

    def change(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        """Run `fn` on the file as it is under its lock, write what it leaves, and return
        what `fn` returned. The next `view` reads the file again."""
        from moxi import files

        out: list[Any] = []

        def apply(raw: bytes | None) -> bytes:
            data = parse(raw)
            out.append(fn(data))
            return dump(data)

        files.private_dir(self.path.parent)
        files.update(self.path, apply)
        with self._lock:
            self._stamp = None
        return out[0]

    def add_client(self, client_id: str, metadata: Mapping[str, Any],
                   secret_hash: str | None) -> None:
        """Keep a newly registered client; raise `Full` when `UNFINISHED_MAX` clients that
        never held a grant exist. Drops those past `UNFINISHED_KEEP` first."""
        now = self.clock()

        def fn(data):
            _drop_unfinished(data, now)
            unfinished = sum(1 for c in data["clients"].values() if not c.get("held"))
            if unfinished >= UNFINISHED_MAX:
                raise Full()
            data["clients"][client_id] = {"registered": now, "held": False,
                                          "secret": secret_hash, "metadata": dict(metadata)}

        self.change(fn)

    def tidy(self) -> dict[str, list[str]]:
        """Drop the clients that never held a grant past `UNFINISHED_KEEP`, and the grants
        past their lifetime. Returns what went, by kind. Writes only when something goes."""
        now = self.clock()
        data = self.view()
        if not (any(_unfinished_old(c, now) for c in data["clients"].values())
                or any(_expired(g, now, self.days) for g in data["grants"].values())):
            return {"clients": [], "grants": []}

        def fn(data):
            return {"clients": _drop_unfinished(data, now),
                    "grants": _drop_expired(data, now, self.days)}

        return self.change(fn)

    def add_grant(self, client_id: str, *, label: str | None, resource: str,
                  access_hash: str, refresh_hash: str) -> str:
        """Make a grant for `client_id` and mark the client as having held one. Returns the
        grant's 8-hex id. Raises `Gone` when the client was dropped meanwhile."""
        now = self.clock()

        def fn(data):
            client = data["clients"].get(client_id)
            if client is None:
                raise Gone(client_id)
            client["held"] = True
            gid = secrets.token_hex(4)
            while gid in data["grants"]:
                gid = secrets.token_hex(4)
            data["grants"][gid] = {
                "client": client_id, "label": label, "resource": resource, "made": now,
                "used": None, "refreshed": now, "access": access_hash,
                "access_expires": now + ACCESS_LIFE, "refresh": refresh_hash,
                "retired": [], "previous": None, "rotated": None,
                "replacement_used": False, "revoked": None, "revoked_by": None}
            return gid

        return self.change(fn)

    def refresh(self, token_hash: str, client_id: str, *, access_hash: str | None = None,
                refresh_hash: str | None = None) -> Verdict:
        """Act on a presented refresh token under the lock, judged again there:

        - `live` or `grace`, with a new pair given: rotate. The presented token is retired;
          in its grace the unused replacement is retired too, and no further grace is left;
        - `reuse`: revoke the whole grant;
        - `expired`: drop the grant;
        - anything else: nothing.

        Without a new pair, `live` and `grace` change nothing. Returns the verdict acted
        on."""
        now = self.clock()

        def fn(data):
            gid = _locate(data, token_hash)
            verdict = _judge(data, token_hash, client_id, now, self.days, gid)
            grant = data["grants"].get(gid) if gid else None
            if verdict.kind == "reuse" and grant is not None:
                grant["revoked"], grant["revoked_by"] = now, "refresh-reuse"
            elif verdict.kind == "expired" and grant is not None:
                del data["grants"][gid]
            elif (verdict.kind in ("live", "grace") and grant is not None
                  and access_hash is not None and refresh_hash is not None):
                retired = list(grant.get("retired") or ())
                if verdict.kind == "live":
                    retired.append(grant["refresh"])
                    grant["previous"] = grant["refresh"]
                else:
                    retired.append(grant["refresh"])
                    grant["previous"] = None
                grant["retired"] = retired[-RETIRED_KEEP:]
                grant["refresh"] = refresh_hash
                grant["access"] = access_hash
                grant["access_expires"] = now + ACCESS_LIFE
                grant["refreshed"] = now
                grant["rotated"] = now
                grant["replacement_used"] = False
            return verdict

        return self.change(fn)

    def note_used(self, gid: str, at: float | None = None, *, pair: bool = True) -> None:
        """Write a grant's last use and, unless `pair` is False, that its current pair has
        been used. A writer that doesn't know which pair was used passes False: a use
        before a rotation says nothing about the pair the rotation made."""
        now = self.clock() if at is None else at

        def fn(data):
            grant = data["grants"].get(gid)
            if grant is not None:
                grant["used"] = max(now, grant.get("used") or 0)
                if pair:
                    grant["replacement_used"] = True

        self.change(fn)

    def revoke(self, gid: str, *, by: str) -> bool:
        """Mark one grant revoked. False when there is no such grant."""
        now = self.clock()

        def fn(data):
            grant = data["grants"].get(gid)
            if grant is None:
                return False
            if not grant.get("revoked"):
                grant["revoked"], grant["revoked_by"] = now, by
            return True

        return self.change(fn)

    def revoke_all(self, *, by: str, drop_clients: bool = False) -> int:
        """Mark every grant revoked, and with `drop_clients` remove every client. Returns
        how many live grants were revoked. Writes nothing when there is no file."""
        if not self.exists():
            return 0
        now = self.clock()

        def fn(data):
            count = 0
            for grant in data["grants"].values():
                if not grant.get("revoked"):
                    if is_live(grant, now, self.days):
                        count += 1
                    grant["revoked"], grant["revoked_by"] = now, by
            if drop_clients:
                data["clients"] = {}
            return count

        return self.change(fn)


def _locate(data: Mapping[str, Any], token_hash: str) -> str | None:
    for gid, grant in data["grants"].items():
        if same(grant.get("refresh"), token_hash) or token_hash in (grant.get("retired") or ()):
            return gid
    return None


def _judge(data: Mapping[str, Any], token_hash: str, client_id: str, now: float, days: int,
           gid: str | None) -> Verdict:
    grant = data["grants"].get(gid) if gid else None
    if grant is None or grant.get("client") != client_id:
        return Verdict("unknown")
    if grant.get("revoked"):
        return Verdict("revoked", gid)
    if now >= grant["refreshed"] + days * DAY:
        return Verdict("expired", gid)
    if same(grant.get("refresh"), token_hash):
        return Verdict("live", gid)
    if token_hash in (grant.get("retired") or ()):
        if (same(grant.get("previous"), token_hash) and not grant.get("replacement_used")
                and grant.get("rotated") is not None and now - grant["rotated"] < GRACE):
            return Verdict("grace", gid)
        return Verdict("reuse", gid)
    return Verdict("unknown")


def _unfinished_old(client: Mapping[str, Any], now: float) -> bool:
    return not client.get("held") and now - client.get("registered", 0) >= UNFINISHED_KEEP


def _drop_unfinished(data: dict[str, Any], now: float) -> list[str]:
    gone = [cid for cid, c in data["clients"].items() if _unfinished_old(c, now)]
    for cid in gone:
        del data["clients"][cid]
    return gone


def _expired(grant: Mapping[str, Any], now: float, days: int) -> bool:
    return now >= grant["refreshed"] + days * DAY


def _drop_expired(data: dict[str, Any], now: float, days: int) -> list[str]:
    gone = [gid for gid, g in data["grants"].items() if _expired(g, now, days)]
    for gid in gone:
        del data["grants"][gid]
    return gone


def listing(data: Mapping[str, Any], *, resource: str | None, now: float,
            days: int) -> list[dict[str, Any]]:
    """One row per grant the file keeps, for `moxi connector grants` and `status`: its id,
    label, client name, when made and last used, its state (`live`, `revoked`,
    `expired`), and whether it is stale. Newest first."""
    rows = []
    for gid, grant in data["grants"].items():
        client = data["clients"].get(grant.get("client")) or {}
        name = (client.get("metadata") or {}).get("client_name")
        if grant.get("revoked"):
            state = "revoked"
        elif not is_live(grant, now, days):
            state = "expired"
        else:
            state = "live"
        rows.append({"id": gid, "label": grant.get("label"),
                     "client_name": _shown(name), "client": grant.get("client"),
                     "made": grant.get("made"), "used": grant.get("used"), "state": state,
                     "stale": state == "live" and is_stale(grant, resource)})
    rows.sort(key=lambda r: r["made"] or 0, reverse=True)
    return rows


def _shown(name: Any, limit: int = 64) -> str | None:
    """A name a client gave itself, as text: control and format characters removed, cut
    to `limit` characters."""
    if not isinstance(name, str):
        return None
    import unicodedata

    kept = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    return kept[:limit]


__all__ = ["ACCESS_LIFE", "DAY", "Full", "GRACE", "GRANTS_DIR", "GRANTS_FILE", "Gone",
           "RETIRED_KEEP", "Store", "UNFINISHED_KEEP", "UNFINISHED_MAX", "Verdict", "digest",
           "grants_path", "is_live", "is_stale", "listing", "new_token", "parse", "same"]

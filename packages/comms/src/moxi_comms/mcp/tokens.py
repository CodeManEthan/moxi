"""The per-session token of comms' tools: minting it, the daemon's registry, revocation.

A wrapper that launches a session with an identity mints one token (`mint`) and puts it
in the session's environment, and nowhere else. The session's adapter keeps only its
hash (`digest`) and registers that hash with the daemon after every bind, on the
connection that holds the binding (the daemon's `mcp_token` method). The daemon keeps one
row per hash in comms.db's `mcp_tokens`, never the token, and the active rows in memory as
well: a map from hash to row, loaded at boot and written through on every change. A call
to a tool costs one hash and one lookup (`Registry.verify`).

A row maps its token to one identity, the one the registering adapter's claim binds. It
survives a daemon restart and a rebind: a release and the boot fence only clear its
`binding_id` (`bindings.BindingHistory`), and the adapter's next registration sets it
again. A call while the row has no binding sends nothing; the session is reconnecting.

Registering a hash, in one transaction:

  * no row has it: a new row for this claim and identity, bound to this binding. Every
    other active row of the same claim is revoked `superseded`;
  * the same claim and identity's row, not revoked: it is bound to this binding again;
  * another claim's row, or this claim's row for another identity: refused
    `mcp-token-taken`. One hash maps to one claim and one identity, so a session can't
    point another session's token at itself;
  * a revoked row: refused `mcp-token-revoked`.

A revoked token never comes back: its value may have reached someone else, and a running
harness can't take a new value. So a token is revoked only on positive evidence that its
cause holds, never on a read that failed. The causes, each a `revoked_why`:

  * `session-ended`: the claim file of the row's claim is gone, or every root process
    the file names is gone or has another start time. A read that fails proves nothing,
    and neither does a claim directory that isn't there at all. Checked at every release,
    at boot after the fence, and at every call; there is no timer;
  * `name-lost`: a bind of the same identity under another claim (or naming no claim);
  * `superseded`: the same claim registered a new hash;
  * `port-taken`: a live listener holds the endpoint's port and is shown not to be
    comms' own socket unit (`Registry.revoke_all`);
  * `uninstall`: comms' own uninstall revokes every active row (`revoke_store`).

Nothing here logs, stores or raises with a token's value. Standard library only, beside
comms' own modules.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from .. import claimfile, wire
from ..bindings import stamp
from ..membership import DEFAULT_PROC, ProcView, PsutilView, Root

log = logging.getLogger("comms.mcp.tokens")

#: Why a row was revoked, as `mcp_tokens.revoked_why` spells it.
SESSION_ENDED = "session-ended"
NAME_LOST = "name-lost"
SUPERSEDED = "superseded"
PORT_TAKEN = "port-taken"
UNINSTALL = "uninstall"
CAUSES = (SESSION_ENDED, NAME_LOST, SUPERSEDED, PORT_TAKEN, UNINSTALL)

#: What a registration did.
REGISTERED, REBOUND = "registered", "rebound"

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
#: A claim key names a claim file (`claimfile.file_for`): a session id, `<pid>-<start>`,
#: or `srv-<pid>-<start>-<qualified>`. Nothing that could leave the claim directory.
_CLAIM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@:+-]{0,255}")


# --------------------------------------------------------------------------- the token

def mint() -> str:
    """A new token: 32 random bytes, URL-safe base64."""
    return secrets.token_urlsafe(32)


def digest(token: str) -> str:
    """The token's SHA-256, lowercase hex: all the daemon and the adapter keep."""
    return hashlib.sha256(token.encode()).hexdigest()


def is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def valid_claim_key(value: object) -> bool:
    return (isinstance(value, str) and _CLAIM_RE.fullmatch(value) is not None
            and ".." not in value)


# --------------------------------------------------------------------------- evidence

def root_ended(root: Root, proc: ProcView | None = None) -> bool:
    """Is `root` proved ended: its pid gone, or the pid now with another start time?
    False when the read can't tell (access refused, any error)."""
    proc = proc or DEFAULT_PROC
    try:
        st = proc.stat(root.pid)
    except Exception:                                            # noqa: BLE001
        return False
    if st is not None:
        return st[1] != root.start
    return _pid_gone(proc, root.pid)


def _pid_gone(proc: ProcView, pid: int) -> bool:
    """`proc.stat` answered nothing: is there positively no such process? A view may
    answer itself (`gone(pid)`); else psutil off Linux, else the view's `/proc` entry."""
    gone = getattr(proc, "gone", None)
    if callable(gone):
        try:
            return gone(pid) is True
        except Exception:                                        # noqa: BLE001
            return False
    if isinstance(proc, PsutilView):
        try:
            import psutil

            return not psutil.pid_exists(int(pid))
        except Exception:                                        # noqa: BLE001
            return False
    base = getattr(proc, "root", "")
    if not base:
        return False
    try:
        os.stat(f"{base}/{int(pid)}")
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return False                      # there, but its stat couldn't be read


def claim_ended(key: str, proc: ProcView | None = None) -> bool:
    """Has the session of claim `key` positively ended? Its claim file is gone from a
    claim directory that is there, or every root the file names is proved ended. Any
    read that fails, a file that doesn't parse, or a file with no roots: not ended."""
    if not valid_claim_key(key):
        return False
    try:
        path = claimfile.file_for(key)
    except Exception:                                            # noqa: BLE001
        return False
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        try:
            return path.parent.is_dir()
        except Exception:                                        # noqa: BLE001
            return False
    except Exception:                                            # noqa: BLE001
        return False
    try:
        record = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(record, dict):
        return False
    roots = claimfile.roots_of(record)
    return bool(roots) and all(root_ended(r, proc) for r in roots)


# --------------------------------------------------------------------------- the registry

@dataclass(frozen=True)
class Row:
    """One active row: what a token maps to. `binding_id` is the `bindings` row of the
    claim's live binding, None between bindings."""
    token_hash: str
    claim_key: str
    identity: str
    adapter: str
    binding_id: int | None
    created_at: str

    @property
    def bound(self) -> bool:
        return self.binding_id is not None

    def __repr__(self) -> str:        # the hash is no secret, but logs don't need it
        return (f"Row(identity={self.identity!r}, claim_key={self.claim_key!r}, "
                f"adapter={self.adapter!r}, binding_id={self.binding_id!r})")


class Refused(Exception):
    """A registration the daemon refuses; `code` is its comms wire code."""

    code = wire.FAILED

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Taken(Refused):
    code = wire.MCP_TOKEN_TAKEN


class Revoked(Refused):
    code = wire.MCP_TOKEN_REVOKED


class Registry:
    """The daemon's registry of tool tokens: `mcp_tokens`, and its active rows in memory.

    It writes through `history.write`, the binding history's connection and lock, so a
    registration and a release never interleave. Lock order: the daemon's lock, then
    this registry's, then the history's. `proc` is the process view the evidence reads
    with, or a callable that answers it at each read (the daemon's `proc_view`, which a
    test may replace)."""

    def __init__(self, history, *, clock: Callable[[], float] = time.time,
                 proc: ProcView | Callable[[], ProcView] | None = None):
        self.history = history
        self.clock = clock
        if proc is None or isinstance(proc, ProcView):
            self._proc = lambda: proc or DEFAULT_PROC
        else:
            self._proc = proc
        self._lock = threading.RLock()
        self._active: dict[str, Row] = {}

    # -- reads ---------------------------------------------------------------
    def active(self) -> list[Row]:
        """The active rows, oldest first."""
        with self._lock:
            return sorted(self._active.values(), key=lambda r: (r.created_at, r.claim_key))

    def verify(self, token: object) -> Row | None:
        """How a call maps to an identity: the active row of `token`'s hash, or None.
        None for a token no row has or a revoked one; a row whose claim has positively
        ended is revoked `session-ended` here and gives None. A claim whose liveness
        can't be read is not ended. The caller sends as the row's identity, and only
        while it is `bound`."""
        if not isinstance(token, str) or not token:
            return None
        h = digest(token)
        with self._lock:
            row = self._active.get(h)
        if row is None:
            return None
        if claim_ended(row.claim_key, self._proc()):
            self._revoke_claims({row.claim_key}, SESSION_ENDED)
            return None
        return row

    # -- boot ----------------------------------------------------------------
    def load(self) -> int:
        """Read the active rows into memory (after the boot fence, so none is bound),
        then revoke those whose session has positively ended. Returns how many rows
        stay active. A store that can't be read loads nothing, and says so."""
        try:
            rows = self.history.write(lambda conn: conn.execute(
                "SELECT token_hash, claim_key, identity, adapter, binding_id, created_at "
                "FROM mcp_tokens WHERE revoked_at IS NULL").fetchall())
        except (sqlite3.Error, RuntimeError) as e:
            log.warning("tool tokens weren't loaded (%s: %s); a session's tools answer "
                        "401 until its adapter registers again", type(e).__name__, e)
            return 0
        with self._lock:
            self._active = {r[0]: Row(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows}
        self.check_ended()
        with self._lock:
            return len(self._active)

    # -- registering ---------------------------------------------------------
    def register(self, token_hash: str, *, claim: str, identity: str, adapter: str,
                 binding_id: int) -> str:
        """Register `token_hash` for this binding (see the module docstring). Returns
        `REGISTERED` or `REBOUND`; raises `Taken` or `Revoked`, which write nothing.
        A store write that fails raises `sqlite3.Error` (or `RuntimeError` when the
        store isn't open) and changes nothing in memory."""
        if not is_digest(token_hash):
            raise ValueError("a token hash is 64 lowercase hex digits")
        if not valid_claim_key(claim):
            raise ValueError("not a claim key")
        at = stamp(self.clock())

        def step(conn):
            row = conn.execute(
                "SELECT claim_key, identity, created_at, revoked_at FROM mcp_tokens "
                "WHERE token_hash = ?", (token_hash,)).fetchone()
            if row is None:
                gone = [r[0] for r in conn.execute(
                    "SELECT token_hash FROM mcp_tokens WHERE claim_key = ? "
                    "AND revoked_at IS NULL", (claim,))]
                conn.execute(
                    "UPDATE mcp_tokens SET revoked_at = ?, revoked_why = ? "
                    "WHERE claim_key = ? AND revoked_at IS NULL", (at, SUPERSEDED, claim))
                conn.execute(
                    "INSERT INTO mcp_tokens(token_hash, claim_key, identity, adapter, "
                    "binding_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (token_hash, claim, identity, adapter, int(binding_id), at))
                return REGISTERED, at, gone
            if row[3] is not None:
                raise Revoked("this session's token for comms' tools was revoked; the "
                              "session has no tools until it is relaunched")
            if row[0] != claim:
                log.warning("tool token refused: claim %s registered a token claim %s "
                            "holds", claim, row[0])
                raise Taken("this token for comms' tools belongs to another session")
            if row[1] != identity:
                log.warning("tool token refused: claim %s registered its token for %s "
                            "as %s", claim, row[1], identity)
                raise Taken(f"this token for comms' tools sends as {row[1]}, not as "
                            f"{identity}")
            conn.execute("UPDATE mcp_tokens SET binding_id = ? WHERE token_hash = ?",
                         (int(binding_id), token_hash))
            return REBOUND, row[2], []

        with self._lock:
            outcome, created, gone = self.history.write(step)
            for h in gone:
                self._active.pop(h, None)
            self._active[token_hash] = Row(token_hash, claim, identity, adapter,
                                           int(binding_id), created)
        if gone:
            log.info("tool token: claim %s registered a new token for %s; %d older "
                     "one(s) revoked %s", claim, identity, len(gone), SUPERSEDED)
        else:
            log.info("tool token %s: claim %s as %s", outcome, claim, identity)
        return outcome

    def released(self, binding_id: int | None) -> None:
        """A binding was released: its rows lose it in memory. The store's side is in
        the release's own transaction (`BindingHistory.release`)."""
        if binding_id is None:
            return
        with self._lock:
            for h, row in list(self._active.items()):
                if row.binding_id == binding_id:
                    self._active[h] = replace(row, binding_id=None)

    # -- revoking ------------------------------------------------------------
    def name_bound(self, identity: str, claim: str | None) -> int:
        """`identity` was bound under `claim` (None: a bind that named no claim). The
        active rows of that identity under any other claim are revoked `name-lost`.
        Returns how many."""
        with self._lock:
            hashes = [h for h, r in self._active.items()
                      if r.identity == identity and r.claim_key != claim]
            n = self._revoke(hashes, NAME_LOST)
        if n:
            log.info("tool token: %s was bound by another session; %d token(s) of the "
                     "last one revoked %s", identity, n, NAME_LOST)
        return n

    def check_ended(self, claims: Iterable[str] | None = None) -> int:
        """Revoke `session-ended` the active rows of each claim in `claims` (every
        active claim when None) whose session has positively ended. The evidence is read
        outside the lock. Returns how many rows were revoked."""
        with self._lock:
            held = {r.claim_key for r in self._active.values()}
        wanted = held if claims is None else held & set(claims)
        proc = self._proc()
        ended = {k for k in wanted if claim_ended(k, proc)}
        return self._revoke_claims(ended, SESSION_ENDED) if ended else 0

    def revoke_all(self, why: str) -> int:
        """Revoke every active row with `why` (`port-taken`). Returns how many."""
        with self._lock:
            n = self._revoke(list(self._active), why)
        if n:
            log.warning("tool tokens: %d revoked %s", n, why)
        return n

    def _revoke_claims(self, claims: set, why: str) -> int:
        with self._lock:
            hashes = [h for h, r in self._active.items() if r.claim_key in claims]
            n = self._revoke(hashes, why)
        if n:
            log.info("tool tokens: %d of claim(s) %s revoked %s", n,
                     ", ".join(sorted(claims)), why)
        return n

    def _revoke(self, hashes: list, why: str) -> int:
        """Revoke `hashes` with `why`, under the registry's lock. Memory drops them even
        when the store's write fails, so this daemon refuses them for its life; the
        failure is logged, and the next boot's checks run again."""
        if why not in CAUSES:
            raise ValueError(f"not a revocation cause: {why!r}")
        if not hashes:
            return 0
        at = stamp(self.clock())

        def step(conn):
            conn.executemany(
                "UPDATE mcp_tokens SET revoked_at = ?, revoked_why = ? "
                "WHERE token_hash = ? AND revoked_at IS NULL",
                [(at, why, h) for h in hashes])

        with self._lock:
            try:
                self.history.write(step)
            except (sqlite3.Error, RuntimeError) as e:
                log.error("tool tokens: the store didn't record %d revocation(s) %s "
                          "(%s: %s); this daemon refuses them anyway", len(hashes), why,
                          type(e).__name__, e)
            for h in hashes:
                self._active.pop(h, None)
        return len(hashes)


def revoke_store(conn: sqlite3.Connection, why: str = UNINSTALL,
                 at: float | None = None) -> int:
    """Revoke every active row in the store `conn` opened for writing, in the caller's
    transaction: comms' uninstall, which runs with no daemon to ask. Returns how many."""
    if why not in CAUSES:
        raise ValueError(f"not a revocation cause: {why!r}")
    cur = conn.execute(
        "UPDATE mcp_tokens SET revoked_at = ?, revoked_why = ? WHERE revoked_at IS NULL",
        (stamp(time.time() if at is None else at), why))
    return cur.rowcount or 0


__all__ = ["CAUSES", "NAME_LOST", "PORT_TAKEN", "REBOUND", "REGISTERED", "Refused",
           "Registry", "Revoked", "Row", "SESSION_ENDED", "SUPERSEDED", "Taken",
           "UNINSTALL", "claim_ended", "digest", "is_digest", "mint", "revoke_store",
           "root_ended", "valid_claim_key"]

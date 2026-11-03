"""The phone's confirmed acts: prepare, commit and state, while the acts are mounted.

    POST /gw/act/prepare  {"module", "path", "body"}  -> {"act", "module", "path", "body",
                                                          "expires"}
    POST /gw/act/commit   {"act"}                     -> 202 {"act", "outcome", ...}
    GET  /gw/act/state?act=<id>                       -> {"act", "state", ...}

- **Prepare** checks that the pair is a confirmed act the gateway reaches and its body
  against the act's declared fields, canonicalises the body (sorted keys, compact), and
  stores it under a new act id, `secrets.token_urlsafe(32)`, which is a valid
  `Idempotency-Key`. The stored act names the session's device; the phone's sheet shows the
  echo, not the page's copy.
- **Commit** spends the id under one lock, so a second commit gets the first's state; writes
  the intent row durably; then sends the stored body once, with `Idempotency-Key: <act id>`,
  on a newly proved connection, and answers 202 with the outcome of the one outcome table.
- **State** gives `prepared`, `sent`, `accepted` (with the status path), `refused` (with the
  status and text) or `unknown`, for the act's device on any of its sessions. After an act
  leaves memory, the audit rows of today and the day before answer with less: an outcome
  row gives `accepted` with the module and no status path, `refused` with the status and no
  text, or `unknown`; an intent row alone gives `unknown`.

An act id reaches a module at most once: it is spent before its intent row, the row is on
disk before the send, and nothing sends it again. Prepare and commit check the mount again
before anything else; when it fails the answer is the unmounted path's 404, the act stays
unspent and nothing is sent. An unspent act expires 120 s after prepare; one act in flight
per session and six commits a minute across the gateway, past either a 429 with nothing
queued; a spent act stays in memory 10 minutes.
"""

from __future__ import annotations

import asyncio
import collections
import datetime as _dt
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from moxi_loopback.api import response_headers

from ..session import Static, admitted
from . import audit as _audit
from .outcome import NOT_SENT, outcome_of
from .shapes import BODY_LIMIT, BadBody, acts_of, canonical, check_body

__all__ = ["ACT_TTL", "COMMIT", "COMMITS_PER_MINUTE", "KEPT", "PATHS", "PREPARE", "STATE",
           "Acts"]

_log = logging.getLogger("moxi.ui.gateway")

PREPARE = "/gw/act/prepare"
COMMIT = "/gw/act/commit"
STATE = "/gw/act/state"
#: The act paths, `(method, path, kind)`, in the gateway's table only while acts mount.
PATHS = (("POST", PREPARE, "act"), ("POST", COMMIT, "act"), ("GET", STATE, "read"))
#: Seconds an unspent act lives after its prepare.
ACT_TTL = 120.0
#: Seconds a spent act stays in memory.
KEPT = 600.0
#: Commits across the gateway in any minute.
COMMITS_PER_MINUTE = 6
_ID = re.compile(r"\A[A-Za-z0-9_-]{43}\Z")
_PREPARE_CAP = BODY_LIMIT + 4096
_COMMIT_CAP = 1024


def _headers(extra: list[tuple[str, str]]) -> list[tuple[bytes, bytes]]:
    return response_headers(extra=extra, policy="gateway")


def _json(status: int, value: Any) -> Static:
    body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return status, _headers([("Content-Type", "application/json"),
                             ("Content-Length", str(len(body)))]), body


def _empty(status: int) -> Static:
    return status, _headers([("Content-Length", "0")]), b""


@dataclass
class _Act:
    id: str
    module: str
    path: str
    body: bytes
    device: str | None
    session: str | None
    expires: float
    spent: bool = False
    state: dict[str, Any] = field(default_factory=lambda: {"state": "prepared"})
    done: float | None = None


class Refused(Exception):
    """A request the act paths refuse: its answer and its audit code (None for a 400,
    which is a request row only)."""

    def __init__(self, answer: Static, code: str | None):
        self.answer = answer
        self.code = code


class Acts:
    """The act store and its three handlers, in the shell.

    `mounts()` gives the exports the gateway mounts (module to export); `mounted()` checks
    the act condition now; `client` sends to the modules' servers (the proxy's `Client`);
    `audit` is the gateway's `Audit`; `key()` the gateway's key; `device_of(id_hash)` the
    device of a live session.
    """

    def __init__(self, *, mounts: Callable[[], Mapping[str, Mapping[str, Any]]],
                 mounted: Callable[[], bool], client: Any, audit: _audit.Audit,
                 key: Callable[[], bytes], device_of: Callable[[str | None], str | None],
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic):
        self.mounts, self.mounted, self.client = mounts, mounted, client
        self.audit, self.key, self.device_of = audit, key, device_of
        self._clock, self._now = clock, monotonic
        self._acts: dict[str, _Act] = {}
        self._lock = threading.Lock()
        self._flying: set[str | None] = set()
        self._commits: collections.deque[float] = collections.deque()

    # --- the handlers ------------------------------------------------------------------

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any]
                       ) -> tuple[Static, str | None]:
        """Answer one request to an act path: `(answer, refusal code or None)`."""
        method, raw = scope.get("method"), (scope.get("raw_path") or b"").decode("latin-1")
        try:
            if not self.mounted():
                raise Refused(_empty(404), "route")
            if method == "POST" and raw == PREPARE:
                return await self.prepare(await _read(receive, _PREPARE_CAP)), None
            if method == "POST" and raw == COMMIT:
                return await self.commit(await _read(receive, _COMMIT_CAP)), None
            if method == "GET" and raw == STATE:
                return await self.state(scope.get("query_string") or b""), None
            raise Refused(_empty(404), "route")
        except Refused as refused:
            return refused.answer, refused.code

    def _who(self) -> tuple[str | None, str | None]:
        id_hash = admitted.get()
        return self.device_of(id_hash), id_hash

    def _sweep(self) -> None:
        now = self._now()
        for act_id, act in list(self._acts.items()):
            if (not act.spent and act.expires <= now) or (
                    act.done is not None and act.done + KEPT <= now):
                del self._acts[act_id]

    async def prepare(self, raw: bytes) -> Static:
        data = _object(raw, {"module", "path", "body"})
        module, path, body = data["module"], data["path"], data["body"]
        if not isinstance(module, str) or not isinstance(path, str):
            raise Refused(_json(400, {"error": "module and path are strings"}), None)
        route = acts_of(self.mounts().get(module)).get(path)
        if route is None:
            raise Refused(_empty(404), "route")
        try:
            check_body(body, route)
        except BadBody as bad:
            raise Refused(_json(400, {"error": str(bad)}), None) from None
        device, session = self._who()
        stored = canonical(body)
        act = _Act(secrets.token_urlsafe(32), module, path, stored, device, session,
                   self._now() + ACT_TTL)
        with self._lock:
            self._sweep()
            self._acts[act.id] = act
        expires = _dt.datetime.fromtimestamp(self._clock() + ACT_TTL, _dt.UTC)
        return _json(200, {"act": act.id, "module": module, "path": path,
                           "body": json.loads(stored),
                           "expires": expires.strftime("%Y-%m-%dT%H:%M:%SZ")})

    async def commit(self, raw: bytes) -> Static:
        act_id = _object(raw, {"act"})["act"]
        device, session = self._who()
        with self._lock:
            self._sweep()
            act = self._acts.get(act_id) if isinstance(act_id, str) else None
            if act is None or act.device != device:
                raise Refused(_empty(404), "route")
            if act.spent:
                return _json(202, {"act": act.id, **_as_outcome(act.state)})
            now = self._now()
            while self._commits and self._commits[0] <= now - 60:
                self._commits.popleft()
            if session in self._flying or len(self._commits) >= COMMITS_PER_MINUTE:
                raise Refused(_json(429, {"error": "busy"}), "rate")
            act.spent = True
            act.state = {"state": "sent"}
            self._flying.add(session)
            self._commits.append(now)
        try:
            outcome = await self._send(act)
        finally:
            with self._lock:
                self._flying.discard(session)
                act.done = self._now()
        return _json(202, {"act": act.id, **outcome})

    async def _send(self, act: _Act) -> dict[str, Any]:
        rows = {"module": act.module, "path": act.path, "device": act.device,
                "act_hash": hashlib.sha256(act.id.encode("ascii")).hexdigest(),
                "body_hmac": hmac.new(self.key(), act.body, hashlib.sha256).hexdigest()}
        try:
            await asyncio.to_thread(self.audit.act, "intent", rows)
        except (OSError, ValueError):
            _log.exception("the intent row of an act can't be written; it isn't sent")
            act.state = {"state": "refused", "code": 503, "text": NOT_SENT}
            return _as_outcome(act.state)
        status, data = await self._exchange(act)
        outcome = outcome_of(status, data)
        act.state = {"state": outcome["outcome"],
                     **{k: v for k, v in outcome.items() if k != "outcome"}}
        try:
            await asyncio.to_thread(self.audit.act, "outcome",
                                    {**rows, "status": status, "outcome": outcome["outcome"]})
        except (OSError, ValueError):
            _log.exception("the outcome row of an act can't be written")
        return outcome

    async def _exchange(self, act: _Act) -> tuple[int, Any]:
        """Send the stored body once; `(status, parsed body)`, status 0 for no answer."""
        from ..proxy import UpstreamError

        headers = [(b"content-type", b"application/json"),
                   (b"idempotency-key", act.id.encode("ascii"))]
        try:
            response = await self.client.request(act.module, "POST", act.path,
                                                 headers=headers, body=act.body, act=True)
        except UpstreamError as err:
            return err.status, err.body
        except Exception:  # noqa: BLE001 - anything after the send may have acted
            _log.warning("the act on %s got no answer", act.module, exc_info=True)
            return 0, None
        async with response:
            try:
                raw = b""
                async for chunk in response.chunks():
                    raw += chunk
                    if len(raw) > BODY_LIMIT:
                        return response.status, None
                return response.status, json.loads(raw) if raw else None
            except Exception:  # noqa: BLE001 - the status stands; the body is unreadable
                return response.status, None

    async def state(self, query: bytes) -> Static:
        match = re.fullmatch(rb"act=([A-Za-z0-9_-]{43})", query)
        if match is None:
            raise Refused(_json(400, {"error": "the query is act=<id>"}), None)
        act_id = match.group(1).decode("ascii")
        device, _session = self._who()
        with self._lock:
            self._sweep()
            act = self._acts.get(act_id)
            if act is not None:
                if act.device != device:
                    raise Refused(_empty(404), "route")
                return _json(200, {"act": act_id, **act.state})
        found = await asyncio.to_thread(self._from_rows, act_id, device)
        if found is None:
            raise Refused(_empty(404), "route")
        return _json(200, {"act": act_id, **found})

    def _from_rows(self, act_id: str, device: str | None) -> dict[str, Any] | None:
        wanted = hashlib.sha256(act_id.encode("ascii")).hexdigest()
        seen: dict[str, Any] | None = None
        for row in _audit.read_rows(self.audit.state_dir, days=2, now=self._clock()):
            if row.get("act_hash") != wanted or row.get("kind") not in ("intent", "outcome"):
                continue
            if row.get("device") != device:
                return None
            if row["kind"] == "intent":
                seen = seen or {"state": "unknown"}
            else:
                seen = _from_outcome(row)
        return seen


def _from_outcome(row: Mapping[str, Any]) -> dict[str, Any]:
    outcome = row.get("outcome")
    if outcome == "accepted":
        return {"state": "accepted", "module": row.get("module")}
    if outcome == "refused":
        return {"state": "refused", "code": row.get("status")}
    return {"state": "unknown"}


def _as_outcome(state: Mapping[str, Any]) -> dict[str, Any]:
    """An act's state as a commit's outcome: `prepared` and `sent` read as `unknown`."""
    word = state.get("state")
    if word in ("accepted", "refused", "unknown"):
        return {"outcome": word, **{k: v for k, v in state.items() if k != "state"}}
    return {"outcome": "unknown", "text": "the act is still being sent"}


async def _read(receive: Callable[..., Any], cap: int) -> bytes:
    body = b""
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            raise Refused(_empty(400), None)
        body += message.get("body", b"")
        if len(body) > cap:
            raise Refused(_empty(413), "size")
        if not message.get("more_body"):
            return body


def _object(raw: bytes, keys: set[str]) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except ValueError:
        raise Refused(_json(400, {"error": "the body is not JSON"}), None) from None
    if not isinstance(data, dict) or set(data) != keys:
        names = ", ".join(sorted(keys))
        raise Refused(_json(400, {"error": f"the body is an object of {names}"}), None)
    return data

"""The gateway's audit log: one JSON object per line, one file per UTC day.

    <state_dir>/gateway/audit/<YYYY-MM-DD>.jsonl     the directory 0700, each file 0600
    <state_dir>/gateway/key                           32 random bytes, 0600

Each row is `{"at": <UTC time>, "kind": <kind>, ...}`. The kinds: `request` (every request
the gateway's router answered), a refusal code of `CODES`, `held` (a hold the shell
couldn't take), `acts` (the phone's acts mounted or not), the sign-in events (`pair`,
`re-pair`, `signin`, `signout`, `revoke`, `mark-stale`), an act's `intent` and `outcome`,
and `summary`.

- **No row is lost.** Rows are appended through the core's `append_record` with no size
  cap, so no file ever rolls, and only files older than the retention are deleted. An act's
  rows, the sign-in events and `held` rows are written durably: on disk when the write
  returns. A request or refusal row is not, so a power loss can lose the last second of
  those.
- **The day is bounded.** Every row a caller without a credential can cause is in a
  collapsed class: one per refusal code, and `open` for requests to the open entries that no
  session made. Past ten rows of one class in a minute, the rest of that minute become one
  `summary` row with their count; once the day's file is past 64 MiB, those classes write one
  `summary` row an hour instead. Rows of signed-in requests, acts and the sign-in events are
  always written.
- **No secrets.** No row holds a query, a header, a cookie, a key, a token, a pairing code, a
  challenge, a request or response body or a session id: the writer refuses those field names.

The key keys the HMACs of an act's body and the gateway's path handles. Deleting it makes a
new one at the next use, which changes every handle.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from moxi import files

from .devices import gateway_dir

__all__ = [
    "AUDIT_DIR", "CODES", "COLLAPSE_AFTER", "DAY_CAP", "DURABLE", "FORBIDDEN", "KEY_FILE",
    "RETAIN_DAYS", "Audit", "audit_dir", "key", "last_held", "read_rows",
]

_log = logging.getLogger("moxi.ui.gateway")

AUDIT_DIR = "audit"
KEY_FILE = "key"
#: Days of files kept; older ones are deleted at start and at each UTC midnight.
RETAIN_DAYS = 30
#: Rows of one collapsed class written in a minute before the rest become a summary.
COLLAPSE_AFTER = 10
#: Past this many bytes in a day's file, collapsed classes write one summary an hour.
DAY_CAP = 64 * 1024 * 1024
#: The closed list of refusal codes.
CODES = frozenset({
    "provider", "host", "origin", "options", "credential", "challenge", "pairing", "route",
    "content-type", "rate", "size", "stale", "revoked", "upstream",
})
#: The kinds written durably.
DURABLE = frozenset({"held", "tether", "acts", "intent", "outcome", "pair", "re-pair",
                     "signin", "signout", "revoke"})
#: The field names no row may hold.
FORBIDDEN = frozenset({
    "query", "query_string", "header", "headers", "cookie", "cookies", "key", "token",
    "code", "pairing_code", "challenge", "body", "request_body", "response_body",
    "session_id", "secret", "credential",
})
#: The class of requests to the open entries that no session made.
OPEN = "open"


def audit_dir(state_dir: str | os.PathLike) -> Path:
    return gateway_dir(state_dir) / AUDIT_DIR


def key(state_dir: str | os.PathLike) -> bytes:
    """The gateway's 32-byte key, made at first use (0600). A file that isn't 32 bytes is
    made again."""
    path = gateway_dir(state_dir) / KEY_FILE
    try:
        data = path.read_bytes()
        if len(data) == 32:
            return data
    except FileNotFoundError:
        pass
    files.private_dir(path.parent)

    def make(old: bytes | None) -> bytes:
        return old if old is not None and len(old) == 32 else secrets.token_bytes(32)

    return files.update(path, make)


def _utc(now: float) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(now, _dt.UTC)


def _stamp(now: float) -> str:
    return _utc(now).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _day(now: float) -> str:
    return _utc(now).strftime("%Y-%m-%d")


class Audit:
    """The writer, one per shell. `state_dir` is the ui's. Call it as `audit(kind,
    fields)`, the hook every gateway part writes through; `request` and `refusal` take a
    `signed_in` flag that keeps a row out of the collapse. Thread-safe."""

    def __init__(self, state_dir: str | os.PathLike, *, clock: Callable[[], float] = time.time):
        self.state_dir = Path(state_dir)
        self.dir = audit_dir(state_dir)
        self._clock = clock
        self._lock = threading.Lock()
        #: class -> [window start, rows seen, rows collapsed, "minute" | "hour"]
        self._buckets: dict[str, list[Any]] = {}
        self._pruned: str | None = None
        self._made = False

    # --- writing ----------------------------------------------------------------------

    def __call__(self, kind: str, fields: Mapping[str, Any]) -> None:
        """Write one row: a refusal code (collapsed) or any other kind (always written)."""
        if kind in CODES:
            self.refusal(kind, fields, signed_in=False)
        else:
            self._write(kind, fields, durable=kind in DURABLE)

    def request(self, fields: Mapping[str, Any], *, signed_in: bool) -> None:
        """A request the router answered; collapsed in `open` unless a session made it."""
        if signed_in:
            self._write("request", fields)
        else:
            self._collapsed(OPEN, "request", fields)

    def refusal(self, code: str, fields: Mapping[str, Any], *, signed_in: bool) -> None:
        """A refusal by `code`; collapsed in its code's class unless a session made it."""
        if code not in CODES:
            raise ValueError(f"{code!r} is not an audit refusal code")
        if signed_in:
            self._write(code, fields)
        else:
            self._collapsed(code, code, fields)

    def act(self, kind: str, fields: Mapping[str, Any]) -> None:
        """An act's `intent` or `outcome` row, durable: on disk when this returns."""
        if kind not in ("intent", "outcome"):
            raise ValueError(f"{kind!r} is not an act row")
        self._write(kind, fields, durable=True)

    def flush(self) -> None:
        """Write the summaries of the windows that have ended, and prune once a day."""
        now = self._clock()
        with self._lock:
            for name in list(self._buckets):
                start, _seen, _collapsed, per = self._buckets[name]
                if self._window(now, per) != start:
                    self._summary(name, self._buckets.pop(name))
        self.prune()

    def close(self) -> None:
        """Write every pending summary."""
        with self._lock:
            for name in list(self._buckets):
                self._summary(name, self._buckets.pop(name))

    def prune(self) -> None:
        """Delete the day files older than `RETAIN_DAYS`, once per UTC day."""
        now = self._clock()
        today = _day(now)
        if self._pruned == today:
            return
        self._pruned = today
        oldest = (_utc(now).date() - _dt.timedelta(days=RETAIN_DAYS - 1)).isoformat()
        try:
            names = os.listdir(self.dir)
        except OSError:
            return
        for name in names:
            stem, dot, suffix = name.partition(".")
            if suffix == "jsonl" and dot and _is_day(stem) and stem < oldest:
                try:
                    os.unlink(self.dir / name)
                except OSError:
                    _log.warning("the audit file %s can't be deleted", name, exc_info=True)

    # --- inside -------------------------------------------------------------------------

    @staticmethod
    def _window(now: float, per: str) -> int:
        return int(now // (3600 if per == "hour" else 60))

    def _collapsed(self, name: str, kind: str, fields: Mapping[str, Any]) -> None:
        now = self._clock()
        with self._lock:
            per = "hour" if self._size(now) >= DAY_CAP else "minute"
            window = self._window(now, per)
            bucket = self._buckets.get(name)
            if bucket is not None and (bucket[0] != window or bucket[3] != per):
                self._summary(name, self._buckets.pop(name))
                bucket = None
            if bucket is None:
                bucket = self._buckets[name] = [window, 0, 0, per]
            bucket[1] += 1
            if per == "minute" and bucket[1] <= COLLAPSE_AFTER:
                self._append(now, kind, fields, durable=False)
            else:
                bucket[2] += 1

    def _summary(self, name: str, bucket: list[Any]) -> None:
        start, _seen, collapsed, per = bucket
        if not collapsed:
            return
        span = 3600 if per == "hour" else 60
        self._append(self._clock(), "summary",
                     {"class": name, "count": collapsed, "per": per,
                      "since": _stamp(start * span)}, durable=False)

    def _write(self, kind: str, fields: Mapping[str, Any], *, durable: bool = False) -> None:
        now = self._clock()
        with self._lock:
            self._append(now, kind, fields, durable=durable)

    def _append(self, now: float, kind: str, fields: Mapping[str, Any], *, durable: bool) -> None:
        bad = FORBIDDEN.intersection(fields)
        if bad:
            raise ValueError(f"an audit row may not hold {', '.join(sorted(bad))}")
        row = {"at": _stamp(now), "kind": kind, **fields}
        if not self._made:
            files.private_dir(self.dir)
            self._made = True
        files.append_record(self.dir / f"{_day(now)}.jsonl", row, max_bytes=None,
                            durable=durable)

    def _size(self, now: float) -> int:
        try:
            return os.stat(self.dir / f"{_day(now)}.jsonl").st_size
        except OSError:
            return 0


def _is_day(text: str) -> bool:
    try:
        _dt.date.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == 10


def read_rows(state_dir: str | os.PathLike, *, days: int = 1,
              now: float | None = None) -> Iterator[dict[str, Any]]:
    """The rows of today's file and the `days - 1` days before it, oldest first. A line
    that isn't a JSON object is skipped."""
    when = _utc(time.time() if now is None else now).date()
    folder = audit_dir(state_dir)
    for back in range(max(days, 1) - 1, -1, -1):
        path = folder / f"{(when - _dt.timedelta(days=back)).isoformat()}.jsonl"
        try:
            handle = path.open("rb")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row


def last_held(state_dir: str | os.PathLike, *, now: float | None = None) -> dict[str, Any] | None:
    """The latest `held` row of today and the day before (a hold the shell couldn't take:
    `at` and `reason`), or None."""
    last = None
    for row in read_rows(state_dir, days=2, now=now):
        if row.get("kind") == "held":
            last = row
    return last

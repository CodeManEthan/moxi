"""The record of what tunnel applied: `STATE/tunnel/applied.json`, its event log and the
copies of foreign config a `--replace` took over.

The record
----------

`applied.json` holds format 1, written whole and atomically by `moxi.files.update` through
one function, `commit`, so a reader without a lock always sees one whole version:

    {"format": 1, "id": "<32 hex>", "last_generation": 7,
     "warned_ct": ["box.tail1234.ts.net"], "cloudflared_started": null,
     "entries": {"ui/gateway": {"reach": "own-devices", "provider": "tailscale",
                                "form": "http", "host": "box.tail1234.ts.net",
                                "port": 8461, "target": 8461, "state": "on",
                                "generation": 7, "from": null, "displaced": null,
                                "detail": null, "applied_at": "2026-10-30T12:00:00Z"}}}

- An entry's state is one of `State`: `applying`, `on` or `removing`. What the live config
  says about an entry is computed by `check`, never written here.
- `id` is a random 128-bit hex value written once, when the file is created.
  `last_generation` goes up by one on every `commit` that adds, changes or drops an entry,
  and never goes down; each entry that commit adds or changes takes the new value as its
  `generation`. An entry's change token is `"<id>.<generation>"`, so a token is never
  given to two different entry contents: within one file generations only rise, and a new
  file has a new id.
- `from` holds the old provider, host and port (and the old target and reach) while a
  provider switch is under way. A `from` that names a Tailscale entry is one of the
  record's Tailscale entries until the switch settles (`old_tailscale`): whatever
  removes, counts or reports Tailscale entries counts it too, since the old Funnel or
  Serve entry may still be live. `displaced` the final name of the copy a `--replace` wrote; `detail` a provider's
  stderr while a failure leaves an entry `applying` or `removing`.
- `warned_ct` lists the hosts whose certificate-log warning the user has confirmed. It
  lives here, not in the event log, so it never ages out.
- `cloudflared_started` is the digest of the `cloudflared.yml` text the unit last started
  on, or null.

Readers ignore nothing they don't know: a top-level or entry key this version doesn't
know is kept and written back unchanged. An entry whose `form` (or `state`) this version
doesn't know was written by a newer tunnel: it is read as it is, and `commit` refuses to
change or drop it (exit 3). A record whose `format` is not 1 is read as far as it can be
and never written: `commit` refuses (exit 3).

`commit` runs only under `tunnel.lock` (`locks.tunnel`). When the Cloudflare set changes
(the Cloudflare entries in state `applying` or `on`, one rule each), it also needs the
core lock held through `locks.core`, and refuses the write without it.

The event log
-------------

`events.jsonl` beside the record gets one line per act through `event`: the time, the act,
the target, the provider, the port, and the old and new host and provider where they
changed. It rolls at 1 MB and keeps one `.1`. A line never holds a credential.

Displaced copies
----------------

`displaced/` holds the copies of foreign config a confirmed `--replace` took over, 0600.
`<UTC time>-<port>.json` is a copy whose config is gone from the live config; tunnel never
deletes one. `<UTC time>-<port>.pending.json` is the copy of a replace that has not
settled: written first (`write_pending`), renamed to its final name once the foreign
config is gone (`rename_pending`), and deleted (`delete_pending`) only when the copy is
not the only one. An entry names a pending file when its `displaced` holds the file's
final name and the file still has its pending name (`names_pending`).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from moxi import files, paths

from . import locks
from .errors import Refused

MODULE = "tunnel"
#: The record format this version reads and writes.
FORMAT = 1
RECORD_FILE = "applied.json"
EVENTS_FILE = "events.jsonl"
DISPLACED_DIR = "displaced"
#: The event log's size before it rolls to its one `.1`.
EVENTS_MAX = 1_000_000

#: The entry forms this version knows.
FORMS = frozenset({"http"})
PROVIDERS = ("tailscale", "cloudflare")
REACHES = ("own-devices", "public")
#: The acts the event log records.
EVENTS = ("add", "remove", "repair", "replace", "rename", "switch", "forget", "rollback",
          "overwrite")
#: Why an entry this version doesn't know is left as it is.
NEWER = "written by a newer tunnel"

_EMPTY: Mapping = MappingProxyType({})
_UNSET: Any = object()


class State(StrEnum):
    """An entry's state in the record, in `Address.state` and in `--json`."""

    APPLYING = "applying"
    ON = "on"
    REMOVING = "removing"


_STATES = frozenset(s.value for s in State)


def normal_host(host: str) -> str:
    """The one host form: lower-case, with no trailing dot."""
    return host.lower().rstrip(".")


def stamp(now: datetime | None = None) -> str:
    """A UTC time as the record writes it, `2026-10-30T12:00:00Z`."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


# -- paths --------------------------------------------------------------------------------

def state_dir() -> Path:
    return paths.Layout(MODULE).state_dir()


def record_path() -> Path:
    return state_dir() / RECORD_FILE


def events_path() -> Path:
    return state_dir() / EVENTS_FILE


def displaced_dir() -> Path:
    return state_dir() / DISPLACED_DIR


# -- entries and the record ---------------------------------------------------------------

#: An entry's keys this version knows, in the order it writes them.
ENTRY_KEYS = ("reach", "provider", "form", "host", "port", "target", "state", "generation",
              "from", "displaced", "detail", "applied_at")
#: The record's top-level keys this version knows, in the order it writes them.
RECORD_KEYS = ("format", "id", "last_generation", "warned_ct", "cloudflared_started",
               "entries")


@dataclass(frozen=True)
class Entry:
    """One recorded target. `extra` holds the keys this version doesn't know."""

    reach: str
    provider: str
    host: str
    port: int
    target: int
    state: str
    form: str = "http"
    generation: int = 0
    from_: Mapping | None = None
    displaced: str | None = None
    detail: str | None = None
    applied_at: str | None = None
    extra: Mapping = field(default=_EMPTY)

    @property
    def known(self) -> bool:
        """False for an entry a newer tunnel wrote: its form or state is not one of ours."""
        return self.form in FORMS and self.state in _STATES

    @property
    def exposure(self) -> str:
        """Who can reach the address: `internet` for every Cloudflare entry and a Tailscale
        `public` one, `tailnet` only for a Tailscale `own-devices` entry."""
        if self.provider == "tailscale" and self.reach == "own-devices":
            return "tailnet"
        return "internet"

    def to_json(self) -> dict:
        out = {"reach": self.reach, "provider": self.provider, "form": self.form,
               "host": self.host, "port": self.port, "target": self.target,
               "state": str(self.state), "generation": self.generation,
               "from": None if self.from_ is None else dict(self.from_),
               "displaced": self.displaced, "detail": self.detail,
               "applied_at": self.applied_at}
        for key, value in self.extra.items():
            if key not in out:
                out[key] = value
        return out

    def content(self) -> dict:
        """Every field but `generation` and `applied_at`: what a change listener compares."""
        out = self.to_json()
        del out["generation"], out["applied_at"]
        return out

    @classmethod
    def from_json(cls, name: str, raw: object, *, strict: bool = True) -> Entry:
        """The entry `name` from its JSON object. With `strict`, an entry of a form this
        version knows must have every known key with the right type, else `Refused`."""
        if not isinstance(raw, dict):
            raise _damaged(f"its entry {name!r} is not an object")
        extra = MappingProxyType({k: v for k, v in raw.items() if k not in ENTRY_KEYS})
        values = {key: raw.get(key) for key in ENTRY_KEYS}
        entry = cls(reach=values["reach"], provider=values["provider"],
                    host=values["host"], port=values["port"], target=values["target"],
                    state=_state(values["state"]), form=values["form"],
                    generation=values["generation"], from_=values["from"],
                    displaced=values["displaced"], detail=values["detail"],
                    applied_at=values["applied_at"], extra=extra)
        if strict and entry.form in FORMS:
            problem = _entry_problem(raw)
            if problem:
                raise _damaged(f"its entry {name!r} {problem}")
        return entry


def old_tailscale(entry: Entry) -> Entry | None:
    """The Tailscale entry a provider switch off Tailscale may have left live, read from
    the entry's `from`, or None when `from` names none. Until the switch settles it is
    one of the record's Tailscale entries: whatever removes, counts or reports those
    counts it too."""
    old = entry.from_
    if (not entry.known or entry.provider == "tailscale" or not isinstance(old, Mapping)
            or old.get("provider") != "tailscale"):
        return None
    host, port, target = old.get("host"), old.get("port"), old.get("target")
    if not isinstance(host, str) or not _is_int(port) or not 0 < port < 65536:
        return None
    if not _is_int(target) or not 0 < target < 65536:
        target = entry.target
    reach = old.get("reach") if old.get("reach") in REACHES else entry.reach
    return Entry(reach=reach, provider="tailscale", host=host, port=port, target=target,
                 state=State.ON)


def _state(value: object) -> object:
    return State(value) if value in _STATES else value


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _entry_problem(raw: dict) -> str | None:
    for key in ENTRY_KEYS:
        if key not in raw:
            return f"has no {key!r}"
    if raw["reach"] not in REACHES:
        return f"has reach {raw['reach']!r}"
    if raw["provider"] not in PROVIDERS:
        return f"has provider {raw['provider']!r}"
    if not isinstance(raw["host"], str):
        return "has a host that is not a string"
    for key in ("port", "target"):
        if not _is_int(raw[key]) or not 0 < raw[key] < 65536:
            return f"has a {key} that is not a port"
    if not _is_int(raw["generation"]) or raw["generation"] < 0:
        return "has a generation that is not a count"
    if raw["from"] is not None and not isinstance(raw["from"], dict):
        return "has a 'from' that is not an object"
    if raw["displaced"] is not None and not is_copy_name(raw["displaced"]):
        return "names a displaced copy that is not one tunnel writes"
    for key in ("detail", "applied_at"):
        if raw[key] is not None and not isinstance(raw[key], str):
            return f"has a {key} that is not a string"
    return None


@dataclass(frozen=True)
class Record:
    """The record as one read gave it. `exists` is False when there is no file yet."""

    format: object = FORMAT
    id: str | None = None
    last_generation: int = 0
    warned_ct: tuple[str, ...] = ()
    cloudflared_started: str | None = None
    entries: Mapping[str, Entry] = field(default=_EMPTY)
    extra: Mapping = field(default=_EMPTY)
    exists: bool = False

    @property
    def writable(self) -> bool:
        """False for a record of another format: nothing writes it."""
        return self.format == FORMAT and _is_int(self.format)

    def token(self, name: str) -> str | None:
        """Entry `name`'s change token, `"<id>.<generation>"`, or None with no entry."""
        entry = self.entries.get(name)
        return None if entry is None else f"{self.id}.{entry.generation}"

    def cloudflare_set(self) -> frozenset[tuple[str, str, int]]:
        """The rules of the Cloudflare entries in state `applying` or `on`: one
        (target, hostname, loopback port) each."""
        return frozenset((name, e.host, e.target) for name, e in self.entries.items()
                         if e.known and e.provider == "cloudflare"
                         and e.state in (State.APPLYING, State.ON))

    @classmethod
    def from_json(cls, raw: object) -> Record:
        if not isinstance(raw, dict):
            raise _damaged("it is not a JSON object")
        fmt = raw.get("format")
        strict = fmt == FORMAT and _is_int(fmt)
        entries_raw = raw.get("entries", {})
        if not isinstance(entries_raw, dict):
            if strict:
                raise _damaged("its entries are not an object")
            entries_raw = {}
        entries = {}
        for name, value in entries_raw.items():
            try:
                entries[name] = Entry.from_json(name, value, strict=strict)
            except Refused:
                if strict:
                    raise
        if strict:
            problem = _record_problem(raw)
            if problem:
                raise _damaged(problem)
        warned = raw.get("warned_ct")
        last = raw.get("last_generation")
        return cls(format=fmt, id=raw.get("id"),
                   last_generation=last if _is_int(last) else 0,
                   warned_ct=tuple(h for h in warned if isinstance(h, str))
                   if isinstance(warned, list) else (),
                   cloudflared_started=raw.get("cloudflared_started"),
                   entries=MappingProxyType(entries),
                   extra=MappingProxyType({k: v for k, v in raw.items()
                                           if k not in RECORD_KEYS}),
                   exists=True)


_ID = re.compile(r"[0-9a-f]{32}")


def _record_problem(raw: dict) -> str | None:
    if not isinstance(raw.get("id"), str) or not _ID.fullmatch(raw["id"]):
        return "it has no id"
    last = raw.get("last_generation")
    if not _is_int(last) or last < 0:
        return "its last_generation is not a count"
    warned = raw.get("warned_ct", [])
    if not isinstance(warned, list) or not all(isinstance(h, str) for h in warned):
        return "its warned_ct is not a list of hosts"
    started = raw.get("cloudflared_started")
    if started is not None and not isinstance(started, str):
        return "its cloudflared_started is not a digest"
    return None


def _damaged(problem: str) -> Refused:
    return Refused(f"tunnel's record {record_path()} can't be read: {problem}",
                   "restore the file from a backup, or move it aside and add each target "
                   "again with moxi tunnel add")


def read() -> Record:
    """The record as it is now, with no lock: a missing file is an empty record. Raises
    `Refused` (exit 3) for a file that isn't a record."""
    return _parse(_read_bytes(record_path()))


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _parse(data: bytes | None) -> Record:
    if data is None:
        return Record()
    return Record.from_json(_loads(data))


def _loads(data: bytes) -> object:
    try:
        return json.loads(data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _damaged(f"it is not JSON ({exc})") from None


# -- commit -------------------------------------------------------------------------------

def commit(entries: Mapping[str, Entry | None] | None = None, *,
           warned: Iterable[str] = (), cloudflared_started: str | None = _UNSET) -> Record:
    """Write the record once, through `moxi.files.update`, and return what was written.

    `entries` maps a target to its new entry, or to None to drop it; the targets it leaves
    out stay as they are. An entry whose content (every key but `generation`) is unchanged
    keeps its generation. When any entry is added, changed or dropped, `last_generation`
    goes up by one and every added or changed entry takes it as its `generation`; the
    `generation` a caller passes is ignored. `warned` adds hosts to `warned_ct`, and
    `cloudflared_started`, when given, replaces that digest; neither changes a generation.

    When the Cloudflare set changed, the Cloudflare step runs once the write is done
    (`moxi_tunnel.cloudflare.step`), and its `StepFailed` is raised with the record as
    written.

    Refuses (`Refused`, exit 3, nothing written) a record of another format or one that
    isn't a record, a change to or drop of an entry a newer tunnel wrote, and a change of
    the Cloudflare set while this thread holds no core lock through `locks.core`. Raises
    `RuntimeError` outside `locks.tunnel`.
    """
    if not locks.holds_tunnel():
        raise RuntimeError("tunnel's record is written only under tunnel.lock")
    changes = dict(entries or {})
    hosts = [normal_host(h) for h in warned]
    seen: dict[str, Record] = {}

    def change(data: bytes | None) -> bytes:
        before = _parse(data)
        if data is not None and not before.writable:
            raise Refused(f"tunnel's record {record_path()} has format "
                          f"{before.format!r}, which this version doesn't write",
                          "upgrade moxi-tunnel")
        raw = _loads(data) if data is not None else _new_record()
        after_raw = _changed(raw, before, changes, hosts, cloudflared_started)
        after = Record.from_json(after_raw)
        if before.cloudflare_set() != after.cloudflare_set() and not locks.holds_core():
            raise Refused("a change to the Cloudflare targets needs the core lock",
                          "take it with moxi_tunnel.locks.held(core=True)")
        seen["before"], seen["after"] = before, after
        return (json.dumps(after_raw, indent=2, ensure_ascii=False) + "\n").encode()

    files.update(record_path(), change)
    return _after_commit(seen["before"], seen["after"])


def _after_commit(before: Record, after: Record) -> Record:
    """What runs once a commit's write is done and its file lock let go: the Cloudflare
    step, when the Cloudflare set changed, and the record as it is after. Never inside
    `files.update`, whose lock is not re-entrant and which the step's own record write
    takes again. The step's `StepFailed` goes to the caller, the record already written
    and held in its `record`."""
    if before.cloudflare_set() == after.cloudflare_set():
        return after
    from . import cloudflare

    return cloudflare.step(after)


def _new_record() -> dict:
    return {"format": FORMAT, "id": secrets.token_hex(16), "last_generation": 0,
            "warned_ct": [], "cloudflared_started": None, "entries": {}}


def _changed(raw: dict, before: Record, changes: Mapping[str, Entry | None],
             hosts: list[str], started: object) -> dict:
    out = dict(raw)
    entries = dict(raw.get("entries", {}))
    written: list[str] = []
    dropped = False
    for name, new in changes.items():
        old = before.entries.get(name)
        if old is not None and not old.known:
            raise Refused(f"the entry for {name} was {NEWER}", "upgrade moxi-tunnel")
        if new is None:
            if name in entries:
                del entries[name]
                dropped = True
            continue
        if not new.known:
            raise Refused(f"the entry for {name} has a form or state this version "
                          "doesn't know", "upgrade moxi-tunnel")
        value = new.to_json()
        if name in entries and _same(entries[name], value):
            continue
        entries[name] = value
        written.append(name)
    if written or dropped:
        top = max([out.get("last_generation", 0),
                   *(e.generation for e in before.entries.values()
                     if _is_int(e.generation))])
        out["last_generation"] = top + 1
        for name in written:
            entries[name]["generation"] = top + 1
    out["entries"] = entries
    if hosts:
        warned = list(out.get("warned_ct", []))
        out["warned_ct"] = warned + [h for h in dict.fromkeys(hosts) if h not in warned]
    if started is not _UNSET:
        out["cloudflared_started"] = started
    return out


def _same(old: object, new: dict) -> bool:
    if not isinstance(old, dict):
        return False
    return ({k: v for k, v in old.items() if k != "generation"}
            == {k: v for k, v in new.items() if k != "generation"})


# -- the event log ------------------------------------------------------------------------

def event(kind: str, target: str | None, *, provider: str | None = None,
          port: int | None = None, now: datetime | None = None, **fields: object) -> None:
    """Append one line for an act to `events.jsonl`: the time, the act, the target, the
    provider and the port, then `fields` (such as `old_host`, `new_host`, `old_provider`
    and `new_provider` where they changed). A field never holds a credential."""
    if kind not in EVENTS:
        raise ValueError(f"{kind!r} is not one of tunnel's events: {', '.join(EVENTS)}")
    line = {"at": stamp(now), "event": kind, "target": target, "provider": provider,
            "port": port, **fields}
    files.append_record(events_path(), line, max_bytes=EVENTS_MAX)


# -- displaced copies ---------------------------------------------------------------------

_COPY = re.compile(r"(?P<at>[0-9]{8}T[0-9]{12}Z)-(?P<port>[0-9]{1,5})\.json")
_PENDING = ".pending.json"


def is_copy_name(name: object) -> bool:
    """True for a final copy name tunnel writes, `<UTC time>-<port>.json`."""
    return isinstance(name, str) and bool(_COPY.fullmatch(name))


@dataclass(frozen=True)
class Displaced:
    """One file in `displaced/`: its final name, its port, and whether it is pending."""

    name: str
    port: int
    pending: bool
    path: Path


def pending_path(name: str) -> Path:
    """The pending file of the copy whose final name is `name`."""
    _check_name(name)
    return displaced_dir() / (name.removesuffix(".json") + _PENDING)


def final_path(name: str) -> Path:
    _check_name(name)
    return displaced_dir() / name


def _check_name(name: str) -> None:
    if not is_copy_name(name):
        raise ValueError(f"{name!r} is not the name of a displaced copy")


def write_pending(port: int, copy: Mapping, *, now: datetime | None = None) -> str:
    """Write `copy`, the foreign config a `--replace` takes over, to a new pending file for
    `port` (0600, atomically) and return the copy's final name."""
    at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    while True:
        name = f"{at.strftime('%Y%m%dT%H%M%S%f')}Z-{port}.json"
        if not (pending_path(name).exists() or final_path(name).exists()):
            break
        at += timedelta(microseconds=1)
    text = json.dumps(dict(copy), indent=2, ensure_ascii=False) + "\n"
    files.atomic_write(pending_path(name), text, mode=0o600)
    return name


def is_pending(name: str) -> bool:
    """True while the copy named `name` still has its pending name."""
    return pending_path(name).exists()


def names_pending(entry: Entry | None) -> bool:
    """True when `entry` names a pending file: its `displaced` holds that file's final
    name and the file still has its pending name."""
    return (entry is not None and is_copy_name(entry.displaced)
            and is_pending(entry.displaced))


def rename_pending(name: str) -> None:
    """Give the pending copy `name` its final name, once its foreign config is gone. A
    rename, never a write and a delete, so no state lies between the two names."""
    os.replace(pending_path(name), final_path(name))
    _sync_dir(displaced_dir())


def delete_pending(name: str) -> None:
    """Delete the pending copy `name`; a copy with its final name is never deleted here.
    A file already gone is no error."""
    try:
        os.remove(pending_path(name))
    except FileNotFoundError:
        pass


def read_copy(name: str) -> dict:
    """The JSON of the copy `name`, pending or final."""
    path = pending_path(name) if is_pending(name) else final_path(name)
    return json.loads(path.read_bytes())


def listing() -> list[Displaced]:
    """Every copy in `displaced/`, by name, a pending one marked."""
    try:
        names = sorted(os.listdir(displaced_dir()))
    except FileNotFoundError:
        return []
    out = []
    for file in names:
        pending = file.endswith(_PENDING)
        name = file.removesuffix(_PENDING) + ".json" if pending else file
        match = _COPY.fullmatch(name)
        if match is None:
            continue
        out.append(Displaced(name=name, port=int(match["port"]), pending=pending,
                             path=displaced_dir() / file))
    return out


def _sync_dir(directory: Path) -> None:
    if sys.platform == "win32":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


__all__ = ["FORMAT", "FORMS", "PROVIDERS", "REACHES", "EVENTS", "NEWER", "State", "Entry",
           "Record", "old_tailscale", "normal_host", "stamp", "record_path", "events_path",
           "displaced_dir",
           "read", "commit", "event", "Displaced", "is_copy_name", "pending_path",
           "final_path", "write_pending", "is_pending", "names_pending", "rename_pending",
           "delete_pending", "read_copy", "listing"]

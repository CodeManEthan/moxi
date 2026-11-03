"""A frozen copy of what tunnel 0.1 accepts: its record reader and write, and the one
extension group it reads its targets from.

Taken from moxi-tunnel 0.1.0's `record.py` and `declare.py`, cut to the pure rules: no
file, lock or path is touched, and a refusal raises `Refused01`. Never change it to match
a newer tunnel. It stands for the tunnel a user downgrades to, which never changes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

#: The extension group 0.1 reads targets from, and the only one.
GROUP = "moxi.tunnel"

FORMAT = 1
FORMS = frozenset({"http"})
PROVIDERS = ("tailscale", "cloudflare")
REACHES = ("own-devices", "public")
STATES = frozenset({"applying", "on", "removing"})
ENTRY_KEYS = ("reach", "provider", "form", "host", "port", "target", "state", "generation",
              "from", "displaced", "detail", "applied_at")
RECORD_KEYS = ("format", "id", "last_generation", "warned_ct", "cloudflared_started",
               "entries")

_EMPTY: Mapping = MappingProxyType({})
_ID = re.compile(r"[0-9a-f]{32}")
_COPY = re.compile(r"(?P<at>[0-9]{8}T[0-9]{12}Z)-(?P<port>[0-9]{1,5})\.json")


class Refused01(Exception):
    """What 0.1 refuses: a record it can't read, or a write it won't make."""


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_copy_name(name: object) -> bool:
    return isinstance(name, str) and bool(_COPY.fullmatch(name))


@dataclass(frozen=True)
class Entry:
    reach: object
    provider: object
    host: object
    port: object
    target: object
    state: object
    form: object = "http"
    generation: object = 0
    from_: object = None
    displaced: object = None
    detail: object = None
    applied_at: object = None
    extra: Mapping = field(default=_EMPTY)

    @property
    def known(self) -> bool:
        return (self.form in FORMS and self.state in STATES
                and not _unreadable_tailscale_from(self))

    def to_json(self) -> dict:
        out = {"reach": self.reach, "provider": self.provider, "form": self.form,
               "host": self.host, "port": self.port, "target": self.target,
               "state": self.state, "generation": self.generation,
               "from": None if self.from_ is None else dict(self.from_),
               "displaced": self.displaced, "detail": self.detail,
               "applied_at": self.applied_at}
        for key, value in self.extra.items():
            if key not in out:
                out[key] = value
        return out

    @classmethod
    def from_json(cls, name: str, raw: object, *, strict: bool = True) -> Entry:
        if not isinstance(raw, dict):
            raise Refused01(f"its entry {name!r} is not an object")
        extra = MappingProxyType({k: v for k, v in raw.items() if k not in ENTRY_KEYS})
        v = {key: raw.get(key) for key in ENTRY_KEYS}
        entry = cls(reach=v["reach"], provider=v["provider"], host=v["host"], port=v["port"],
                    target=v["target"], state=v["state"], form=v["form"],
                    generation=v["generation"], from_=v["from"], displaced=v["displaced"],
                    detail=v["detail"], applied_at=v["applied_at"], extra=extra)
        if strict and entry.form in FORMS:
            problem = _entry_problem(raw)
            if problem:
                raise Refused01(f"its entry {name!r} {problem}")
        return entry


def _unreadable_tailscale_from(entry: Entry) -> bool:
    old = entry.from_
    if (entry.provider == "tailscale" or not isinstance(old, Mapping)
            or old.get("provider") != "tailscale"):
        return False
    host, port = old.get("host"), old.get("port")
    return not isinstance(host, str) or not _is_int(port) or not 0 < port < 65536


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


@dataclass(frozen=True)
class Record:
    format: object
    entries: Mapping[str, Entry]
    extra: Mapping

    @property
    def writable(self) -> bool:
        return self.format == FORMAT and _is_int(self.format)


def read(raw: object) -> Record:
    """The record as 0.1 reads the parsed JSON `raw`, or `Refused01`."""
    if not isinstance(raw, dict):
        raise Refused01("it is not a JSON object")
    fmt = raw.get("format")
    strict = fmt == FORMAT and _is_int(fmt)
    entries_raw = raw.get("entries", {})
    if not isinstance(entries_raw, dict):
        if strict:
            raise Refused01("its entries are not an object")
        entries_raw = {}
    entries = {}
    for name, value in entries_raw.items():
        try:
            entries[name] = Entry.from_json(name, value, strict=strict)
        except Refused01:
            if strict:
                raise
    if strict:
        problem = _record_problem(raw)
        if problem:
            raise Refused01(problem)
    return Record(format=fmt, entries=MappingProxyType(entries),
                  extra=MappingProxyType({k: v for k, v in raw.items()
                                          if k not in RECORD_KEYS}))


def write(raw: dict, changes: Mapping[str, Entry | None]) -> dict:
    """The JSON 0.1's `commit` writes over `raw` for `changes` (target to its new entry, or
    None to drop it), or `Refused01` where 0.1 refuses the write."""
    before = read(raw)
    if not before.writable:
        raise Refused01(f"format {before.format!r}, which this version doesn't write")
    out = dict(raw)
    entries = dict(raw.get("entries", {}))
    written: list[str] = []
    dropped = False
    for name, new in changes.items():
        old = before.entries.get(name)
        if old is not None and not old.known:
            raise Refused01(f"the entry for {name} was written by a newer tunnel")
        if new is None:
            if name in entries:
                del entries[name]
                dropped = True
            continue
        if not new.known:
            raise Refused01(f"the entry for {name} has a form or state this version "
                            "doesn't know")
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
    read(out)
    return out


def _same(old: object, new: dict) -> bool:
    if not isinstance(old, dict):
        return False
    return ({k: v for k, v in old.items() if k != "generation"}
            == {k: v for k, v in new.items() if k != "generation"})

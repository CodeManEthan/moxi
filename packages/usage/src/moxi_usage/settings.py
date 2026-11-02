"""usage's config, read through `moxi.config` at each use.

The schema is `settings.toml` in this package; the file it governs is
`CONFIG/usage/config.toml`. Most keys are plain values the schema checks whole. The one it
cannot check whole is `model`, the `[[model]]` override rows over the model table: the
schema knows only that it is a list. `overrides(settings)` reads those rows and refuses a
bad one with `moxi.config.ConfigError`, and `check(text)` (the announcement's
`CONFIG_CHECK`) runs the schema and that reader over a config text, so `moxi config set`
never writes a file this module would then refuse.

A `[[model]]` row:

    id       the model id, or with match = "prefix" the start of the ids it covers
    match    "exact" (the default) or "prefix"; a prefix must carry a digit, so it never
             covers every future model
    input, output, cache_read, cache_write_5m, cache_write_1h
             prices in dollars per million tokens
    window, max_output
             token counts
    tiers    {name = {input = .., output = .., ..., above_tokens = .., service_tier = ..,
             speed = ..}}
    provider the provider whose default cache multipliers fill a cache price the row
             leaves out
    from, to a price period, matched on each request's own time: `from` inclusive, `to`
             exclusive; a date (UTC midnight) or a date and time; either may be left out
    note     text shown beside the row

A row must give at least one fact.

The other key the schema checks only as a list is `claude.archive_roots`, more Claude Code
trees for the token index to read: each a `[[claude.archive_roots]]` row with `path`, a
directory holding a `projects/` tree, and `id`, which names its lane (`archive:<id>`).
`archive_roots(settings)` reads them in config order, and `check` runs it too.

The worker ceiling's roots must be absolute paths and each `ceiling.allow_commands` entry
a list of words; `moxi_usage.ceiling.rules_of` reads them, and `check` runs it too.
"""

from __future__ import annotations

import datetime
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from moxi.config import ConfigError, Schema, Settings, check_text, load

MODULE = "usage"
SCHEMA_RESOURCE = "settings.toml"
KEY = "model"

PRICE_FIELDS = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")
LIMIT_FIELDS = ("window", "max_output")
_TIER_FIELDS = frozenset({*PRICE_FIELDS, "above_tokens", "service_tier", "speed"})
_ROW_FIELDS = frozenset({"id", "match", "provider", "tiers", "from", "to", "note",
                         *PRICE_FIELDS, *LIMIT_FIELDS})
_DIGIT = re.compile(r"\d")

_schema: Schema | None = None


def schema() -> Schema:
    """The module's schema, parsed once per process."""
    global _schema
    if _schema is None:
        _schema = Schema.from_resource(__package__, SCHEMA_RESOURCE)
    return _schema


def current(env: Mapping[str, str] | None = None) -> Settings:
    """The settings now. Raises `moxi.config.ConfigError` on a file that cannot be read."""
    return load(MODULE, schema(), env=env)


@dataclass(frozen=True, slots=True)
class Override:
    """One `[[model]]` row, checked. `start` and `end` are epoch seconds, or None for an
    open end; `prices` holds only the prices the row gives."""

    id: str
    match: str = "exact"
    prices: Mapping[str, float] = field(default_factory=dict)
    limits: Mapping[str, int] = field(default_factory=dict)
    tiers: Mapping[str, Mapping[str, Any]] | None = None
    provider: str | None = None
    start: float | None = None
    end: float | None = None
    note: str | None = None
    #: The row's place in the file, from 1.
    number: int = 0

    @property
    def dated(self) -> bool:
        return self.start is not None or self.end is not None

    def covers(self, at: float) -> bool:
        """True when `at` (epoch seconds) falls in the row's period, or it has none."""
        return ((self.start is None or at >= self.start)
                and (self.end is None or at < self.end))

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "match": self.match, **self.prices,
                               **self.limits}
        if self.tiers is not None:
            out["tiers"] = {name: dict(tier) for name, tier in self.tiers.items()}
        if self.provider is not None:
            out["provider"] = self.provider
        for name, bound in (("from", self.start), ("to", self.end)):
            if bound is not None:
                out[name] = _iso(bound)
        if self.note is not None:
            out["note"] = self.note
        return out


def overrides(settings: Settings, *, file: Any = None) -> list[Override]:
    """The `[[model]]` rows of `settings`, checked. Raises `ConfigError` naming the row."""
    rows = settings[KEY]
    where = file if file is not None else settings.file
    return [_override(row, n, where) for n, row in enumerate(rows, start=1)]


def check(text: str) -> None:
    """The announcement's `CONFIG_CHECK`: the whole of usage's config reader over a
    config text alone. Raises `ConfigError` when usage would refuse the text."""
    settings = check_text(MODULE, schema(), text)
    overrides(settings)
    archive_roots(settings)
    from .ceiling import rules_of

    rules_of(settings)


# --- archive roots ----------------------------------------------------------------------

ARCHIVE_KEY = "claude.archive_roots"
_ARCHIVE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class ArchiveRoot:
    """One archive root: `id` names its lane (`archive:<id>`), `path` is the directory
    holding its `projects/` tree (`~` expanded), and `number` its place in the config,
    from 1."""

    id: str
    path: str
    number: int

    @property
    def lane(self) -> str:
        return f"archive:{self.id}"


def archive_roots(settings: Settings, *, file: Any = None) -> list[ArchiveRoot]:
    """The `[[claude.archive_roots]]` rows of `settings` in config order, checked. Raises
    `ConfigError` naming the row: a row that is not a table, has no `path` or `id`, has
    another key, or repeats an `id`."""
    where = file if file is not None else settings.file
    fix = 'write each as [[claude.archive_roots]] with path = "/a/dir" and id = "name"'
    out: list[ArchiveRoot] = []
    seen: set[str] = set()
    for n, row in enumerate(settings[ARCHIVE_KEY], start=1):
        if not isinstance(row, dict):
            raise ConfigError(where, ARCHIVE_KEY, f"row {n} is not a table", fix)
        extra = sorted(set(row) - {"path", "id"})
        if extra:
            raise ConfigError(where, ARCHIVE_KEY,
                              f"row {n} has unknown keys: {', '.join(extra)}",
                              "give each row only path and id")
        path, ident = row.get("path"), row.get("id")
        if not isinstance(path, str) or not path.strip():
            raise ConfigError(where, ARCHIVE_KEY, f"row {n} has no path", fix)
        if not isinstance(ident, str) or not _ARCHIVE_ID.match(ident):
            raise ConfigError(where, ARCHIVE_KEY, f"row {n} has id {ident!r}",
                              "give it an id of letters, digits, _ or -")
        if ident in seen:
            raise ConfigError(where, ARCHIVE_KEY, f"row {n} repeats the id {ident!r}",
                              "give every archive root its own id")
        seen.add(ident)
        expanded = os.path.expanduser(path.strip())
        if not os.path.isabs(expanded):
            raise ConfigError(where, ARCHIVE_KEY, f"row {n} ({ident}) has a relative path",
                              "give the archive root's absolute path")
        out.append(ArchiveRoot(ident, os.path.normpath(expanded), n))
    return out


# --- one row --------------------------------------------------------------------------

def _refuse(where: Any, n: int, problem: str, fix: str) -> ConfigError:
    return ConfigError(where, KEY, f"row {n} {problem}", fix)


def _number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _tokens(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _override(row: Any, n: int, where: Any) -> Override:
    if not isinstance(row, dict):
        raise _refuse(where, n, "is not a table", "write each row as a [[model]] table")
    model = row.get("id")
    if not isinstance(model, str) or not model.strip():
        raise _refuse(where, n, "has no id", "give the row the model id it is for")
    unknown = sorted(set(row) - _ROW_FIELDS)
    if unknown:
        raise _refuse(where, n, f"({model}) has unknown keys: {', '.join(unknown)}",
                      f"use only {', '.join(sorted(_ROW_FIELDS))}")
    match = row.get("match", "exact")
    if match not in ("exact", "prefix"):
        raise _refuse(where, n, f"({model}) has match {match!r}",
                      'set match to "exact" or "prefix"')
    if match == "prefix" and not _DIGIT.search(model):
        raise _refuse(where, n, f"({model}) is a prefix with no digit in it, which could "
                      "cover every future model", "give a prefix with a version in it, "
                      'such as "claude-opus-6-"')
    prices: dict[str, float] = {}
    for name in PRICE_FIELDS:
        if name in row:
            if not _number(row[name]):
                raise _refuse(where, n, f"({model}) has {name} {row[name]!r}",
                              f"set {name} to a price in dollars per million tokens, 0 "
                              "or more")
            prices[name] = float(row[name])
    limits: dict[str, int] = {}
    for name in LIMIT_FIELDS:
        if name in row:
            if not _tokens(row[name]):
                raise _refuse(where, n, f"({model}) has {name} {row[name]!r}",
                              f"set {name} to a whole number of tokens above 0")
            limits[name] = row[name]
    tiers = None
    if "tiers" in row:
        tiers = _tiers(row["tiers"], n, model, where)
    provider = row.get("provider")
    if provider is not None and (not isinstance(provider, str) or not provider):
        raise _refuse(where, n, f"({model}) has provider {provider!r}",
                      "name the provider as a string, such as \"anthropic\"")
    note = row.get("note")
    if note is not None and not isinstance(note, str):
        raise _refuse(where, n, f"({model}) has a note that is not text",
                      "write the note as a string")
    start = _bound(row.get("from"), "from", n, model, where)
    end = _bound(row.get("to"), "to", n, model, where)
    if start is not None and end is not None and start >= end:
        raise _refuse(where, n, f"({model}) has from at or after to",
                      "make from earlier than to")
    if not prices and not limits and tiers is None and provider is None:
        raise _refuse(where, n, f"({model}) gives no fact",
                      "give a price, window, max_output, tiers or provider, or remove "
                      "the row")
    return Override(id=model, match=match, prices=prices, limits=limits, tiers=tiers,
                    provider=provider, start=start, end=end, note=note, number=n)


def _tiers(value: Any, n: int, model: str, where: Any) -> dict[str, dict[str, Any]]:
    fix = ("write tiers as {name = {input = .., output = ..}}, with prices, above_tokens, "
           "service_tier or speed in each")
    if not isinstance(value, dict):
        raise _refuse(where, n, f"({model}) has tiers that are not a table", fix)
    out: dict[str, dict[str, Any]] = {}
    for name, tier in value.items():
        if not isinstance(tier, dict) or set(tier) - _TIER_FIELDS:
            raise _refuse(where, n, f"({model}) has a tier {name!r} it cannot read", fix)
        for key, item in tier.items():
            ok = (_number(item) if key in PRICE_FIELDS
                  else _tokens(item) if key == "above_tokens"
                  else isinstance(item, str) and bool(item))
            if not ok:
                raise _refuse(where, n, f"({model}) has tier {name!r} {key} {item!r}", fix)
        out[name] = {key: (float(item) if key in PRICE_FIELDS else item)
                     for key, item in tier.items()}
    return out


def _bound(value: Any, name: str, n: int, model: str, where: Any) -> float | None:
    """A period bound as epoch seconds: a date is UTC midnight, a date and time with no
    offset is UTC."""
    if value is None:
        return None
    moment: datetime.datetime | None = None
    if isinstance(value, datetime.datetime):
        moment = value
    elif isinstance(value, datetime.date):
        moment = datetime.datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        try:
            moment = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            moment = None
    if moment is None:
        raise _refuse(where, n, f"({model}) has {name} {value!r}",
                      f"set {name} to a date such as 2026-11-01, or a date and time")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.timestamp()


def _iso(seconds: float) -> str:
    moment = datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
    if moment.time() == datetime.time(0):
        return moment.date().isoformat()
    return moment.isoformat().replace("+00:00", "Z")

"""`moxi usage prices update`: a new snapshot layer from LiteLLM's current price file.

Opt in: nothing else in usage fetches from the web (with turns, `limits` may ask Codex's
app-server for a reading, which is turns' call, not a fetch). `update` fetches the file from config
`prices.url`, runs the converter (`moxi_usage.convert`) with the shipped supplement,
refuses a result with fewer than half the shipped table's rows, works out which models
the change adds and reprices in the table in effect, which models LiteLLM no longer lists
(against the snapshot it replaces, or the shipped table when there is none), and which
of the models the token index has seen it reprices, prices for the first time or leaves
unpriced, and writes `DATA/usage/models-snapshot.json` with `moxi.files.atomic_write`
unless it is a dry run.
`revert` removes the snapshot, so the shipped table rules again.

The fetch is a parameter (`fetch(url) -> bytes`), so a test hands in the bytes and never
touches the network.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import time
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import convert, models
from .settings import PRICE_FIELDS

SUPPLEMENT = Path(__file__).resolve().parent / "data" / "supplement.toml"
#: How long a fetch may take, in seconds.
FETCH_TIMEOUT = 60.0
#: A snapshot is refused when it holds fewer rows than this share of the shipped table.
MIN_SHARE = 0.5

Fetch = Callable[[str], bytes]


class FetchError(Exception):
    """The fetch failed; trying again later may work (exit 75)."""


class Refused(Exception):
    """The fetched file converted to a table usage will not use (exit 3)."""


def fetch(url: str, *, timeout: float = FETCH_TIMEOUT) -> bytes:
    """The bytes at `url`. Raises `FetchError`."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "moxi-usage"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FetchError(f"fetching {url} failed: {exc}") from exc


@dataclass(frozen=True)
class Update:
    """What an update did, or would do on a dry run."""

    url: str
    path: Path
    snapshot: Mapping[str, Any]
    rows: int
    shipped_rows: int
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    repriced: list[str] = field(default_factory=list)
    #: Supplement rows left out because their model is no longer in LiteLLM and they give
    #: no input or output price, so the table below keeps that model's price.
    dropped: list[str] = field(default_factory=list)
    written: bool = False
    #: What the change does to the models the token index has seen (`InIndex`), or None
    #: when there is no index to read.
    in_index: InIndex | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"url": self.url, "path": str(self.path), "snapshot": dict(self.snapshot),
                "rows": self.rows, "shipped_rows": self.shipped_rows,
                "added": self.added, "removed": self.removed, "repriced": self.repriced,
                "dropped": self.dropped, "written": self.written,
                "in_index": None if self.in_index is None else self.in_index.as_dict()}


@dataclass(frozen=True)
class InIndex:
    """The models the token index's claimed requests name, and what a new snapshot does
    to their prices as a report would look them up now: `repriced` (a price changed),
    `priced` (unpriced before, priced after) and `unpriced` (unpriced after)."""

    models: int
    repriced: list[str] = field(default_factory=list)
    priced: list[str] = field(default_factory=list)
    unpriced: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"models": self.models, "repriced": self.repriced, "priced": self.priced,
                "unpriced": self.unpriced}


def supplement() -> Mapping[str, Any]:
    """The shipped supplement, parsed."""
    return tomllib.loads(SUPPLEMENT.read_text(encoding="utf-8"))


def update(*, fetcher: Fetch | None = None, now: float | None = None,
           dry_run: bool = False, env: Mapping[str, str] | None = None) -> Update:
    """Fetch, convert, compare and (unless `dry_run`) write the snapshot layer.

    Raises `FetchError` when the fetch fails or returns no JSON document, `Refused` when
    the converter refuses the file or the result is too small, and
    `moxi.config.ConfigError` when usage's config cannot be read.
    """
    from moxi.files import atomic_write

    from . import settings

    url = settings.current(env)["prices.url"]
    get = fetcher or fetch
    raw = get(url)
    try:
        litellm = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FetchError(f"{url} did not return a JSON document ({exc})") from exc
    when = time.time() if now is None else now
    date = datetime.datetime.fromtimestamp(when, datetime.timezone.utc).date().isoformat()
    dropped: list[str] = []
    try:
        doc = convert.convert(litellm, supplement(), date=date,
                              sha256=hashlib.sha256(raw).hexdigest(), url=url,
                              dropped=dropped)
    except convert.ConvertError as exc:
        raise Refused(str(exc)) from exc
    shipped = models.shipped()
    shipped_rows = len(shipped["models"])
    rows = len(doc["models"])
    if rows < shipped_rows * MIN_SHARE:
        raise Refused(f"the converted table has {rows} rows, under half the shipped "
                      f"table's {shipped_rows}; it is not used")
    before = models.table(env)
    after = models.Table(shipped, doc)
    added, _, repriced = _changes(before.rows, after.rows)
    removed = _gone(before.snapshot or shipped, doc)
    in_index = _in_index(before, models.Table(shipped, doc, before.overrides),
                         index_models(env), at=when)
    path = models.snapshot_path(env)
    if not dry_run:
        atomic_write(path, convert.dumps(doc))
    return Update(url=url, path=path, snapshot=doc["snapshot"], rows=rows,
                  shipped_rows=shipped_rows, added=added, removed=removed,
                  repriced=repriced, dropped=sorted(dropped), written=not dry_run,
                  in_index=in_index)


def index_models(env: Mapping[str, str] | None = None) -> list[tuple[Any, Any]] | None:
    """Each `(model, provider)` the token index's claimed requests name, or None when
    there is no index, or none this usage can read. The index is only read."""
    import sqlite3

    from moxi.store import StoreError

    from . import store

    if not store.index_path(env).exists():
        return None
    try:
        conn = store.connect(env=env, create=False)
    except (StoreError, sqlite3.Error):
        return None
    try:
        return [(row[0], row[1]) for row in conn.execute(
            "SELECT DISTINCT COALESCE(model, response_model), provider FROM requests "
            "WHERE won = 1")]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _price_key(facts: models.ModelFacts) -> tuple:
    def plain(value: Any) -> Any:
        return None if value is models.UNKNOWN else value

    tiers = facts.tiers if isinstance(facts.tiers, Mapping) else {}
    return (*(plain(getattr(facts, name)) for name in PRICE_FIELDS),
            json.dumps(tiers, sort_keys=True))


def _in_index(before: models.Table, after: models.Table,
              seen: list[tuple[Any, Any]] | None, *, at: float) -> InIndex | None:
    if seen is None:
        return None
    names: set[str] = set()
    repriced: set[str] = set()
    priced: set[str] = set()
    unpriced: set[str] = set()
    for model, provider in seen:
        name = model if isinstance(model, str) and model else "(no model)"
        names.add(name)
        old = before.lookup(model, provider=provider, at=at)
        new = after.lookup(model, provider=provider, at=at)
        if not new.priced:
            unpriced.add(name)
        elif not old.priced:
            priced.add(name)
        elif _price_key(old) != _price_key(new):
            repriced.add(name)
    return InIndex(models=len(names), repriced=sorted(repriced), priced=sorted(priced),
                   unpriced=sorted(unpriced))


def revert(*, dry_run: bool = False, env: Mapping[str, str] | None = None) -> Path | None:
    """Remove the snapshot layer. Returns its path when there was one, else None."""
    path = models.snapshot_path(env)
    if not path.exists():
        return None
    if not dry_run:
        os.remove(path)
    return path


def _prices(row: Mapping[str, Any]) -> dict[str, Any]:
    return {name: row.get(name) for name in (*PRICE_FIELDS, "tiers")}


def _gone(previous: Mapping[str, Any], doc: Mapping[str, Any]) -> list[str]:
    """The LiteLLM models of `previous` (the snapshot layer this update replaces, or the
    shipped table when there is none) that the new snapshot `doc` no longer holds. The
    table in effect may still price them from a lower layer; LiteLLM no longer lists
    them."""
    new = doc.get("models", {})
    return sorted(key for key, row in previous.get("models", {}).items()
                  if row.get("provenance", {}).get("source") == "litellm"
                  and key not in new)


def _changes(before: Mapping[str, Mapping[str, Any]],
             after: Mapping[str, Mapping[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    repriced = sorted(key for key in set(before) & set(after)
                      if _prices(before[key]) != _prices(after[key]))
    return added, removed, repriced

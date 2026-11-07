"""Model facts: price, cache prices, window and tiers, from one table in three layers.

The layers, a later one winning for a model id:

1. `shipped`: `data/models.json` in this package, made by the converter
   (`moxi_usage.convert`) from a dated LiteLLM snapshot and the supplement.
2. `snapshot`: `DATA/usage/models-snapshot.json`, the same shape, written by
   `moxi usage prices update` (`moxi_usage.prices`). A snapshot that cannot be read is
   skipped, and `Table.problems` says why.
3. `override`: the `[[model]]` rows of usage's config (`moxi_usage.settings`). A row
   with `from` or `to` applies only to a request whose own time falls in that period; a
   dated row that covers the time wins over an undated one, and among equals the first
   in the file wins. A row that gives any price replaces every price and tier of the
   layer under it, so a cache price never stays behind at an old rate; `window`,
   `max_output` and `provider` replace only themselves.

The lookup ladder for a model id, the first match winning:

1. `<synthetic>`, a harness's stand-in for a request no model made, is free.
2. The id as it is.
3. The id under a provider prefix the table uses: the request's provider, then
   `anthropic/` and `openai/`.
4. An id with a bracket suffix (`claude-opus-5-5[1m]`): the base id's facts (by steps 2,
   3, 5 and 6) with the supplement's variant for that suffix laid over them, or the base
   id's facts alone when there is no variant for it.
5. The id with a date suffix (`-20251001` or `-2025-10-01`) taken off, by steps 2 and 3.
6. The longest prefix among the override and supplement prefix rows.
7. Unknown: every fact is `UNKNOWN`, so the tokens are counted as unpriced, never as $0.

When a row gives an input price and its provider has default cache multipliers in the
table, each cache price the row leaves out is the input price times the multiplier. Every
answer carries its `Provenance`: the layer, the ladder step, the row matched, the date of
the snapshot the row came from, and which fields an override or a default gave.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from moxi_sessions.api import UNKNOWN

from .settings import LIMIT_FIELDS, PRICE_FIELDS, Override

SHIPPED = Path(__file__).resolve().parent / "data" / "models.json"
SNAPSHOT_FILE = "models-snapshot.json"
SYNTHETIC = "<synthetic>"
#: The prefixes step 3 tries after the request's own provider.
PROVIDER_PREFIXES = ("anthropic", "openai")
CACHE_FIELDS = ("cache_read", "cache_write_5m", "cache_write_1h")
FACT_FIELDS = (*PRICE_FIELDS, *LIMIT_FIELDS)

SHIPPED_LAYER = "shipped"
SNAPSHOT_LAYER = "snapshot"
OVERRIDE_LAYER = "override"

#: Each ladder step's number and name.
STEPS = {1: "synthetic", 2: "exact", 3: "provider prefix", 4: "bracket suffix",
         5: "date suffix", 6: "prefix", 7: "unknown"}

_BRACKET = re.compile(r"^(.+?)(\[[^\[\]]+\])$")
_DATE = re.compile(r"^(.+?)-(?:\d{8}|\d{4}-\d{2}-\d{2})$")


# --- answers --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Provenance:
    """Where one answer came from.

    * `layer`: `shipped`, `snapshot` or `override`; None for a synthetic or unknown model.
    * `step` and `rule`: the ladder step that matched (`STEPS`).
    * `row`: the table row or prefix matched, or None.
    * `snapshot`: the date of the LiteLLM snapshot the row came from, or None.
    * `source`: the row's own record of its sources (LiteLLM's key, supplement rows).
    * `overridden`: the fields an override row gave; `period` its `from` and `to`;
      `note` its note.
    * `filled`: the cache prices a provider's default multipliers gave.
    * `variant`: the bracket suffix whose variant was laid over the base, if any.
    """

    layer: str | None
    step: int
    row: str | None = None
    snapshot: str | None = None
    source: Mapping[str, Any] | None = None
    overridden: tuple[str, ...] = ()
    period: Mapping[str, str] | None = None
    note: str | None = None
    filled: tuple[str, ...] = ()
    variant: str | None = None

    @property
    def rule(self) -> str:
        return STEPS[self.step]

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"layer": self.layer, "step": self.step, "rule": self.rule}
        for name in ("row", "snapshot", "source", "period", "note", "variant"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        for name in ("overridden", "filled"):
            value = getattr(self, name)
            if value:
                out[name] = list(value)
        return out


@dataclass(frozen=True, slots=True)
class ModelFacts:
    """One model's facts. Each of `input`, `output`, `cache_read`, `cache_write_5m` and
    `cache_write_1h` (dollars per million tokens), `window` and `max_output` (tokens) is a
    number or `UNKNOWN`; `tiers` maps a tier's name to its facts (`{}` when the table
    knows none, `UNKNOWN` for an unknown model); `provider` is the table's provider or
    `UNKNOWN`. `model` is the id asked about."""

    model: str | None
    input: Any = UNKNOWN
    output: Any = UNKNOWN
    cache_read: Any = UNKNOWN
    cache_write_5m: Any = UNKNOWN
    cache_write_1h: Any = UNKNOWN
    window: Any = UNKNOWN
    max_output: Any = UNKNOWN
    tiers: Any = UNKNOWN
    provider: Any = UNKNOWN
    provenance: Provenance = field(default_factory=lambda: Provenance(None, 7))

    @property
    def priced(self) -> bool:
        """True when both the input and the output price are known."""
        return self.input is not UNKNOWN and self.output is not UNKNOWN

    @property
    def known(self) -> bool:
        """False exactly when the ladder found nothing (step 7)."""
        return self.provenance.step != 7

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready: an `UNKNOWN` fact is left out and named in `"unknown"`."""
        out: dict[str, Any] = {"model": self.model}
        unknown: list[str] = []
        for name in (*FACT_FIELDS, "tiers", "provider"):
            value = getattr(self, name)
            if value is UNKNOWN:
                unknown.append(name)
            elif name == "tiers":
                out[name] = {tier: dict(facts) for tier, facts in value.items()}
            else:
                out[name] = value
        out["provenance"] = self.provenance.as_dict()
        if unknown:
            out["unknown"] = unknown
        return out


# --- the table ------------------------------------------------------------------------

class TableError(ValueError):
    """A table document that is not in the converter's shape."""


def _check_doc(doc: Any) -> Mapping[str, Any]:
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        raise TableError("not a model table of schema 1")
    for key in ("models", "prefixes", "defaults", "variants"):
        if not isinstance(doc.get(key, {}), dict):
            raise TableError(f"its {key} is not an object")
    if not isinstance(doc.get("snapshot", {}), dict):
        raise TableError("its snapshot is not an object")
    return doc


class Table:
    """The three layers, merged, and the ladder over them.

    `Table(shipped, snapshot=None, overrides=())` takes the shipped document, the
    snapshot document or None, and the checked override rows. `problems` lists what was
    skipped and why.
    """

    def __init__(self, shipped: Mapping[str, Any], snapshot: Mapping[str, Any] | None = None,
                 overrides: Iterable[Override] = (), *, problems: Iterable[str] = ()):
        self.shipped = _check_doc(shipped)
        self.snapshot = None if snapshot is None else _check_doc(snapshot)
        self.problems = list(problems)
        self.dates = {SHIPPED_LAYER: _date(self.shipped)}
        self.rows: dict[str, Mapping[str, Any]] = dict(self.shipped.get("models", {}))
        self.layer_of: dict[str, str] = dict.fromkeys(self.rows, SHIPPED_LAYER)
        self.prefixes: dict[str, Mapping[str, Any]] = dict(self.shipped.get("prefixes", {}))
        self.prefix_layer: dict[str, str] = dict.fromkeys(self.prefixes, SHIPPED_LAYER)
        self.defaults: dict[str, Mapping[str, Any]] = dict(self.shipped.get("defaults", {}))
        self.variants: dict[str, Mapping[str, Any]] = dict(self.shipped.get("variants", {}))
        self.variant_layer: dict[str, str] = dict.fromkeys(self.variants, SHIPPED_LAYER)
        if self.snapshot is not None:
            self.dates[SNAPSHOT_LAYER] = _date(self.snapshot)
            for key, row in self.snapshot.get("models", {}).items():
                self.rows[key] = row
                self.layer_of[key] = SNAPSHOT_LAYER
            for key, row in self.snapshot.get("prefixes", {}).items():
                self.prefixes[key] = row
                self.prefix_layer[key] = SNAPSHOT_LAYER
            self.defaults.update(self.snapshot.get("defaults", {}))
            for key, row in self.snapshot.get("variants", {}).items():
                self.variants[key] = row
                self.variant_layer[key] = SNAPSHOT_LAYER
        self.overrides = tuple(overrides)
        self._dated = any(row.dated for row in self.overrides)
        self._memo: dict[tuple, ModelFacts] = {}

    @property
    def effective_date(self) -> str | None:
        """The date of the snapshot in effect: the snapshot layer's, else the shipped
        table's."""
        return self.dates.get(SNAPSHOT_LAYER) or self.dates.get(SHIPPED_LAYER)

    # -- the ladder --------------------------------------------------------------------

    def lookup(self, model: Any, *, provider: Any = None, at: float | None = None) -> ModelFacts:
        """The facts for `model`, by the ladder. `provider` is the request's provider
        (step 3); `at` is the request's time in epoch seconds, which picks the override
        rows whose period covers it (now when None)."""
        if model is UNKNOWN or not isinstance(model, str) or not model:
            return ModelFacts(model if isinstance(model, str) else None)
        if provider is UNKNOWN or not isinstance(provider, str) or not provider:
            provider = None
        when = time.time() if at is None else at
        active = self._active(when)
        key = (model, provider, tuple(row.number for row in active) if self._dated else ())
        hit = self._memo.get(key)
        if hit is None:
            hit = self._resolve(model, provider, active)
            if len(self._memo) < 65536:
                self._memo[key] = hit
        return hit

    def ids(self, *, at: float | None = None) -> list[str]:
        """Every model id the table answers for exactly: its rows and the exact override
        rows in effect at `at` (now when None)."""
        when = time.time() if at is None else at
        exact = {row.id for row in self._active(when) if row.match == "exact"}
        return sorted(set(self.rows) | exact)

    def _active(self, when: float) -> list[Override]:
        rows = [row for row in self.overrides if row.covers(when)]
        # A dated row that covers the time comes before an undated one.
        return sorted(rows, key=lambda row: (not row.dated, row.number))

    def _resolve(self, model: str, provider: str | None,
                 active: list[Override]) -> ModelFacts:
        if model == SYNTHETIC:
            return ModelFacts(model, **dict.fromkeys(PRICE_FIELDS, 0.0), tiers={},
                              provenance=Provenance(None, 1))
        exact: dict[str, Override] = {}
        for row in active:
            if row.match == "exact":
                exact.setdefault(row.id, row)
        found = self._direct(model, provider, exact)
        if found is not None:
            return self._facts(model, found[0], found[1], exact)
        bracket = _BRACKET.match(model)
        if bracket:
            return self._variant(model, bracket.group(1), bracket.group(2), provider,
                                 active, exact)
        return self._rest(model, model, provider, active, exact)

    def _direct(self, model: str, provider: str | None,
                exact: Mapping[str, Override]) -> tuple[str, int] | None:
        """Steps 2 and 3: the row id and the step, or None."""
        if model in self.rows or model in exact:
            return model, 2
        for prefix in dict.fromkeys((provider, *PROVIDER_PREFIXES)):
            if prefix and not model.startswith(f"{prefix}/"):
                candidate = f"{prefix}/{model}"
                if candidate in self.rows or candidate in exact:
                    return candidate, 3
        return None

    def _rest(self, asked: str, model: str, provider: str | None, active: list[Override],
              exact: Mapping[str, Override]) -> ModelFacts:
        """Steps 5, 6 and 7 for `model` (which may be `asked` with its bracket off)."""
        dated = _DATE.match(model)
        if dated:
            found = self._direct(dated.group(1), provider, exact)
            if found is not None:
                return self._facts(asked, found[0], 5, exact)
        best: tuple[int, int, str, Any] | None = None
        for row in active:
            if row.match == "prefix" and model.startswith(row.id):
                rank = (len(row.id), 1, row.id, row)
                best = rank if best is None or rank[:2] > best[:2] else best
        for prefix, row in self.prefixes.items():
            if model.startswith(prefix):
                rank = (len(prefix), 0, prefix, row)
                best = rank if best is None or rank[:2] > best[:2] else best
        if best is not None:
            return self._prefix(asked, best[2], best[3])
        return ModelFacts(asked)

    def _variant(self, asked: str, base: str, suffix: str, provider: str | None,
                 active: list[Override], exact: Mapping[str, Override]) -> ModelFacts:
        found = self._direct(base, provider, exact)
        inner = (self._facts(asked, found[0], 4, exact) if found is not None
                 else self._rest(asked, base, provider, active, exact))
        variant = self.variants.get(suffix)
        if variant is None:
            if not inner.known:
                return inner
            return _replace(inner, provenance=_with(inner.provenance, step=4))
        laid = {name: variant[name] for name in FACT_FIELDS if name in variant}
        if inner.known:
            provenance = _with(inner.provenance, step=4, variant=suffix)
        else:
            layer = self.variant_layer.get(suffix, SHIPPED_LAYER)
            provenance = Provenance(layer, 4, snapshot=self.dates.get(layer),
                                    source={"source": "supplement",
                                            "variant": dict(variant)},
                                    variant=suffix)
        return _replace(inner, provenance=provenance, **laid)

    # -- composing an answer -----------------------------------------------------------

    def _facts(self, asked: str, row_id: str, step: int,
               exact: Mapping[str, Override]) -> ModelFacts:
        row = self.rows.get(row_id)
        over = exact.get(row_id)
        facts: dict[str, Any] = dict(row) if row is not None else {}
        source = facts.pop("provenance", None)
        layer = self.layer_of.get(row_id)
        overridden: tuple[str, ...] = ()
        if over is not None:
            facts, overridden = _overlay(facts, over)
            layer = OVERRIDE_LAYER
            if row is None:
                source = {"source": "config", "row": over.number}
        snapshot = self.dates.get(self.layer_of.get(row_id, "")) if row is not None else None
        return self._answer(asked, facts, Provenance(
            layer, step, row=row_id, snapshot=snapshot, source=source,
            overridden=overridden, period=_period(over), note=over.note if over else None))

    def _prefix(self, asked: str, prefix: str, row: Any) -> ModelFacts:
        if isinstance(row, Override):
            facts, overridden = _overlay({}, row)
            return self._answer(asked, facts, Provenance(
                OVERRIDE_LAYER, 6, row=prefix, source={"source": "config",
                                                       "row": row.number},
                overridden=overridden, period=_period(row), note=row.note))
        facts = dict(row)
        source = facts.pop("provenance", None)
        layer = self.prefix_layer.get(prefix, SHIPPED_LAYER)
        return self._answer(asked, facts, Provenance(
            layer, 6, row=prefix, snapshot=self.dates.get(layer), source=source))

    def _answer(self, asked: str, facts: dict[str, Any], provenance: Provenance) -> ModelFacts:
        filled = []
        defaults = self.defaults.get(facts.get("provider", ""))
        price = facts.get("input")
        if defaults and isinstance(price, (int, float)):
            for name in CACHE_FIELDS:
                if name not in facts and isinstance(defaults.get(name), (int, float)):
                    facts[name] = _tidy(price * defaults[name])
                    filled.append(name)
        if filled:
            provenance = _with(provenance, filled=tuple(filled))
        values = {name: facts[name] for name in FACT_FIELDS if name in facts}
        tiers = facts.get("tiers") or {}
        return ModelFacts(asked, **values, tiers=dict(tiers),
                          provider=facts.get("provider", UNKNOWN), provenance=provenance)


def _overlay(facts: dict[str, Any], over: Override) -> tuple[dict[str, Any], tuple[str, ...]]:
    """An override row over a row's facts: any price replaces every price and tier."""
    out = dict(facts)
    given: list[str] = []
    if over.prices or over.tiers is not None:
        for name in (*PRICE_FIELDS, "tiers"):
            out.pop(name, None)
        out.update(over.prices)
        given.extend(over.prices)
        if over.tiers is not None:
            out["tiers"] = {name: dict(tier) for name, tier in over.tiers.items()}
            given.append("tiers")
    out.update(over.limits)
    given.extend(over.limits)
    if over.provider is not None:
        out["provider"] = over.provider
        given.append("provider")
    return out, tuple(given)


def _period(over: Override | None) -> dict[str, str] | None:
    if over is None or not over.dated:
        return None
    out = over.as_dict()
    return {name: out[name] for name in ("from", "to") if name in out}


def _with(provenance: Provenance, **changes: Any) -> Provenance:
    values = {name: getattr(provenance, name) for name in Provenance.__slots__}
    values.update(changes)
    return Provenance(**values)


def _replace(facts: ModelFacts, **changes: Any) -> ModelFacts:
    values = {name: getattr(facts, name) for name in ModelFacts.__slots__}
    values.update(changes)
    return ModelFacts(**values)


def _tidy(value: float) -> float:
    return float(f"{value:.12g}")


def _date(doc: Mapping[str, Any]) -> str | None:
    date = (doc.get("snapshot") or {}).get("date")
    return date if isinstance(date, str) else None


# --- the table in effect --------------------------------------------------------------

_shipped: Mapping[str, Any] | None = None
_cached: tuple[tuple, Table] | None = None


def shipped() -> Mapping[str, Any]:
    """The shipped table document, read once per process."""
    global _shipped
    if _shipped is None:
        _shipped = _check_doc(json.loads(SHIPPED.read_text(encoding="utf-8")))
    return _shipped


def snapshot_path(env: Mapping[str, str] | None = None) -> Path:
    """Where the snapshot layer lives: `DATA/usage/models-snapshot.json`."""
    from moxi.paths import Layout

    return Layout("usage").data_dir(env) / SNAPSHOT_FILE


def read_snapshot(path: Path) -> tuple[Mapping[str, Any] | None, str | None]:
    """The snapshot document and None, or None and why it was skipped (None, None when
    there is no snapshot)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"the snapshot {path} cannot be read ({exc}); the shipped table rules"
    try:
        return _check_doc(json.loads(text)), None
    except (ValueError, TableError) as exc:
        return None, (f"the snapshot {path} is {exc}; the shipped table rules; run "
                      "`moxi usage prices update` again, or `--revert` to remove it")


def _stamp(path: Path) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns, st.st_ino)


def table(env: Mapping[str, str] | None = None) -> Table:
    """The table in effect: the shipped table, the snapshot layer and the config's
    override rows, built again only when the snapshot or the config file changed. Raises
    `moxi.config.ConfigError` when the config file, or a `[[model]]` row in it, cannot be
    read."""
    from moxi.paths import Layout

    from . import settings

    global _cached
    snap = snapshot_path(env)
    config = Layout("usage").config_file(env)
    key = (str(snap), _stamp(snap), str(config), _stamp(config))
    if _cached is not None and _cached[0] == key:
        return _cached[1]
    current = settings.current(env)
    rows = settings.overrides(current)
    snapshot, problem = read_snapshot(snap)
    built = Table(shipped(), snapshot, rows, problems=[problem] if problem else [])
    _cached = (key, built)
    return built


def summary(current: Table) -> dict[str, Any]:
    """What the table `current` is made of: its effective date, how many model ids it
    answers for exactly, each layer (`{layer, date, rows}`, the snapshot's with its
    `path`, the override layer's with each `[[model]]` row as `Override.as_dict()` gives
    it), its prefix and variant rows, and the problems skipped when it was built."""
    layers: list[dict[str, Any]] = [{"layer": SHIPPED_LAYER,
                                     "date": current.dates.get(SHIPPED_LAYER),
                                     "rows": len(current.shipped.get("models", {}))}]
    if current.snapshot is not None:
        layers.append({"layer": SNAPSHOT_LAYER, "date": current.dates.get(SNAPSHOT_LAYER),
                       "rows": len(current.snapshot.get("models", {})),
                       "path": str(snapshot_path())})
    if current.overrides:
        layers.append({"layer": OVERRIDE_LAYER, "rows": len(current.overrides),
                       "overrides": [row.as_dict() for row in current.overrides]})
    return {"effective_date": current.effective_date, "rows": len(current.rows),
            "layers": layers, "prefixes": sorted(current.prefixes),
            "variants": sorted(current.variants), "problems": list(current.problems)}


def model_facts(model: Any, *, provider: Any = None, at: float | None = None,
                env: Mapping[str, str] | None = None) -> ModelFacts:
    """`model`'s facts from the table in effect (`table`), by the ladder."""
    return table(env).lookup(model, provider=provider, at=at)


def models(*, at: float | None = None, env: Mapping[str, str] | None = None) -> list[ModelFacts]:
    """Every row of the table in effect, each with its provenance, sorted by id."""
    current = table(env)
    return [current.lookup(model, at=at) for model in current.ids(at=at)]


def context_window(model: Any, reported: Any = None, *, provider: Any = None,
                   env: Mapping[str, str] | None = None) -> Any:
    """A model's context window in tokens: the `reported` one when the harness gave one
    (a positive number), else the table's by the ladder, else `UNKNOWN`."""
    if (reported is not None and reported is not UNKNOWN
            and isinstance(reported, (int, float)) and not isinstance(reported, bool)
            and reported > 0):
        return int(reported)
    return model_facts(model, provider=provider, env=env).window

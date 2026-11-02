"""Restart-point marks in tokens, per model and population: the equations and nothing guessed.

The model is Wilson's economic order quantity. Restarting a session costs a cache write of
its settled context; carrying on costs a cache read of the whole context on every call,
and the context grows each call. All costs are in units of the base input price, so no
dollar figure enters anywhere. For one population's measured parameters `S` (the settled
context), `P` (the prefix a fresh session reads from cache), `g` (the growth per
call) and `w` (the model's own cache-write price over its input price, measured with the
row), and the model's read multiplier `r` (its cache-read price over its input price,
from the model table):

    W       = w(S - P) + rP                 what one restart costs
    N*      = sqrt(2W / (rg))               the restart interval, in calls
    C(1x)   = S + gN*
    C(1.5x) = S + g(1.5 + sqrt(1.25))N*     ~= S + 2.618gN*
    C(2x)   = S + g(2 + sqrt(3))N*          ~= S + 3.732gN*

The exact coefficients are used, not the rounded ones. A mark's threshold is the smallest
whole token count at or above the exact C (`ceil`), so `tokens >= threshold.tokens` on an
integer count is the same comparison as `tokens >= C` on the real number.

**Unavailable is an answer, not a fallback.** A mark needs a measured row for the model
and population (`moxi_usage.measure` writes them; a subagent population with no row of
its own carries the same model's `main` row, marked `fallback: main`), a read multiplier
from the model table, and parameters that give a restart point. Without all three the
answer is `unavailable` with the reason, and no mark is computed.

For a caller on a hook path (the warn-at listener): this module imports only `math` and
`os`, and reads the measure and the model table at the first call that needs them.
`threshold(model, mark, population)` answers from the measure and table in effect;
`stamp()` is a few `stat` calls that change whenever either does, so a caller that keeps
thresholds per (model, mark) compares stamps and asks again only on a change.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from typing import Any

#: The selectable marks and their multiples of N*. `off` is not a mark: it is the absence
#: of one.
MARK_MULTIPLES = {
    "1x": 1.0,
    "1.5x": 1.5 + math.sqrt(1.25),
    "2x": 2.0 + math.sqrt(3.0),
}
MARKS = tuple(MARK_MULTIPLES)
OFF = "off"

#: The populations a measure has rows for: a main session, and a subagent (a workflow's
#: agents included).
MAIN = "main"
SUBAGENT = "subagent"
POPULATIONS = (MAIN, SUBAGENT)
#: The `fallback` a subagent row carries when it is the same model's main row.
FALLBACK_MAIN = MAIN

#: The measure's file, under usage's state directory.
PARAMS_FILE = "restart-point.json"
#: The schema of the measure's file this code reads and writes.
PARAMS_SCHEMA = 1

MEASURE_FIX = "run `moxi usage restart-point --measure`"


class ParamsError(Exception):
    """The measure's file is there and is not something the equations can read."""


class Threshold:
    """One mark for one model and population, or why there is none.

    `model` is the base model the row is keyed by (a bracket suffix and a date suffix
    taken off), `mark` one of `MARKS`, `population` `main` or `subagent`. `tokens` is the
    threshold, None exactly when the mark is unavailable, and then `reason` says why.
    `exact` is C as a real number. `fallback` is `main` when a subagent's mark comes from
    the model's main row."""

    __slots__ = ("model", "mark", "population", "tokens", "reason", "exact", "fallback")

    def __init__(self, model: str | None, mark: str, population: str, tokens: int | None,
                 reason: str | None = None, exact: float | None = None,
                 fallback: str | None = None):
        self.model = model
        self.mark = mark
        self.population = population
        self.tokens = tokens
        self.reason = reason
        self.exact = exact
        self.fallback = fallback

    @property
    def available(self) -> bool:
        return self.tokens is not None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"model": self.model, "mark": self.mark,
                               "population": self.population}
        if self.tokens is None:
            out["reason"] = self.reason
        else:
            out["tokens"] = self.tokens
            out["exact"] = self.exact
        if self.fallback is not None:
            out["fallback"] = self.fallback
        return out

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Threshold):
            return NotImplemented
        return all(getattr(self, name) == getattr(other, name) for name in self.__slots__)

    def __repr__(self) -> str:
        what = self.tokens if self.tokens is not None else f"unavailable: {self.reason}"
        return f"Threshold({self.model!r}, {self.mark!r}, {self.population!r}, {what})"


# --- the equations ----------------------------------------------------------------------

def write_multiplier(facts: Any, share_1h: float | None = None
                     ) -> tuple[float | None, str | None]:
    """w, one model's cache-write price over its input price, from its model-table
    answer `facts`, and None; or None and why there is none.

    Only the row's own write prices count: a price the lookup filled from the provider's
    default (`provenance.filled`) is not the row's. With both a 5-minute and a 1-hour
    price, `w` weighs them by `share_1h`, the 1-hour share of the model's cache writes
    (0 when no request stated it); with one of them, that price over the input price;
    with neither, 1.0, since the writes bill as input. A model with no input price, or
    one of 0, has no `w`."""
    from moxi_sessions.api import UNKNOWN

    model = facts.model
    if not facts.known:
        return None, f"the model table does not know {model}"
    base = facts.input
    if base is UNKNOWN:
        return None, f"the model table has no input price for {model}"
    if not base > 0:
        return None, f"the model table's input price for {model} is not above 0"
    filled = set(facts.provenance.filled)
    own = {name: float(getattr(facts, name)) for name in ("cache_write_5m", "cache_write_1h")
           if name not in filled and getattr(facts, name) is not UNKNOWN}
    five, hour = own.get("cache_write_5m"), own.get("cache_write_1h")
    if five is not None and hour is not None:
        share = share_1h or 0.0
        return (hour * share + five * (1.0 - share)) / float(base), None
    if five is not None or hour is not None:
        return (five if five is not None else hour) / float(base), None
    return 1.0, None


def restart_cost(S: float, P: float, w: float, r: float) -> float:
    """W = w(S - P) + rP: what one restart costs, in base-input-price tokens."""
    return w * (S - P) + r * P


def optimum_calls(W: float, r: float, g: float) -> float:
    """N* = sqrt(2W / (rg)): the restart interval, in calls, that costs least."""
    return math.sqrt(2.0 * W / (r * g))


def mark_exact(S: float, P: float, g: float, w: float, r: float, mark: str) -> float:
    """C(mark) as a real number of context tokens."""
    W = restart_cost(S, P, w, r)
    return S + g * MARK_MULTIPLES[mark] * optimum_calls(W, r, g)


# --- model ids --------------------------------------------------------------------------

def base_model(model: Any) -> str | None:
    """The id a measured row is keyed by: `claude-opus-5-5[1m]` and
    `claude-opus-5-5-20260101` are `claude-opus-5-5`. None for no model."""
    if not isinstance(model, str) or not model:
        return None
    base = model
    if base.endswith("]") and "[" in base:
        cut = base.rindex("[")
        if cut > 0:
            base = base[:cut]
    tail = base.rsplit("-", 1)
    if len(tail) == 2 and tail[0] and len(tail[1]) == 8 and tail[1].isdigit():
        base = tail[0]
    else:
        parts = base.rsplit("-", 3)
        if (len(parts) == 4 and parts[0] and len(parts[1]) == 4 and len(parts[2]) == 2
                and len(parts[3]) == 2 and (parts[1] + parts[2] + parts[3]).isdigit()):
            base = parts[0]
    return base


def population_of(kind: Any) -> str:
    """The population a file's `kind` samples with: `main`, else `subagent` (a workflow's
    agents are marked as agents)."""
    return MAIN if kind == MAIN else SUBAGENT


# --- the measure's file -----------------------------------------------------------------

def params_path(env: Mapping[str, str] | None = None) -> Any:
    """`STATE/usage/restart-point.json`."""
    from moxi.paths import Layout

    return Layout("usage").state_dir(env) / PARAMS_FILE


def load_params(path: Any) -> dict | None:
    """The measure's document, or None when the file does not exist. A file that is
    there and does not read as one raises `ParamsError`."""
    import json

    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ParamsError(f"the restart-point measure {path} cannot be read: {exc}") from exc
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise ParamsError(f"the restart-point measure {path} is not JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise ParamsError(f"the restart-point measure {path} is not a JSON object")
    schema = doc.get("schema")
    if schema != PARAMS_SCHEMA:
        raise ParamsError(f"the restart-point measure {path} has schema {schema!r}; this "
                          f"usage reads {PARAMS_SCHEMA}")
    return doc


def _stat(path: Any) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns, st.st_ino)


def _paths(env: Mapping[str, str] | None) -> tuple:
    from moxi.paths import Layout

    from .models import SHIPPED, SNAPSHOT_FILE

    layout = Layout("usage")
    return (layout.state_dir(env) / PARAMS_FILE, layout.data_dir(env) / SNAPSHOT_FILE,
            layout.config_file(env), SHIPPED)


def stamp(env: Mapping[str, str] | None = None) -> tuple:
    """What the thresholds rest on, as `stat` facts: the measure's file, the model
    table's snapshot layer, usage's config (its `[[model]]` rows) and the shipped table.
    Two equal stamps mean every threshold is as it was; the cost is four `stat` calls."""
    return tuple((str(path), _stat(path)) for path in _paths(env))


_params_cached: tuple[tuple, Any] | None = None


def params(env: Mapping[str, str] | None = None) -> dict | None:
    """The measure in effect, read again only when its file changed. Raises
    `ParamsError` as `load_params` does."""
    global _params_cached
    path = params_path(env)
    key = (str(path), _stat(path))
    if _params_cached is None or _params_cached[0] != key:
        try:
            value: Any = load_params(path)
        except ParamsError as exc:
            value = exc
        _params_cached = (key, value)
    value = _params_cached[1]
    if isinstance(value, ParamsError):
        raise value
    return value


def read_multiplier(model: Any, *, table: Any = None,
                    env: Mapping[str, str] | None = None) -> tuple[float | None, str | None]:
    """r, the model's cache-read price over its input price, from the model table in
    effect (or `table`), and None; or None and why there is none."""
    from moxi_sessions.api import UNKNOWN

    if table is None:
        from .models import table as current

        table = current(env)
    facts = table.lookup(model)
    if not facts.known:
        return None, f"the model table does not know {model}"
    read, base = facts.cache_read, facts.input
    if read is UNKNOWN or base is UNKNOWN:
        missing = " and ".join(name for name, value in (("input", base),
                                                        ("cache-read", read))
                               if value is UNKNOWN)
        return None, f"the model table has no {missing} price for {model}"
    if not base > 0:
        return None, f"the model table's input price for {model} is not above 0"
    return float(read) / float(base), None


# --- the thresholds ---------------------------------------------------------------------

def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


_UNSET: Any = object()


def threshold(model: Any, mark: str, population: str = MAIN, *, doc: Any = _UNSET,
              table: Any = None, env: Mapping[str, str] | None = None) -> Threshold:
    """The token threshold for `model` at `mark` in `population` (`main` or
    `subagent`), or `unavailable` with its reason.

    `doc` is the measure's document (None for no measure); left out, it is the measure
    in effect (`params`). `table` is the model table; left out, the table in effect.
    A config or model table that cannot be read gives `unavailable` with why."""
    if mark not in MARK_MULTIPLES:
        return Threshold(None, mark, population, None, f"{mark!r} is not a mark")
    if population not in POPULATIONS:
        return Threshold(None, mark, population, None,
                         f"{population!r} is not a population ({', '.join(POPULATIONS)})")
    base = base_model(model)
    if base is None:
        return Threshold(None, mark, population, None, "no model is known for the session")
    if doc is _UNSET:
        try:
            doc = params(env)
        except ParamsError as exc:
            return Threshold(base, mark, population, None, str(exc))
    if doc is None:
        return Threshold(base, mark, population, None,
                         f"the restart point has not been measured; {MEASURE_FIX}")
    models = doc.get("models")
    rows = models.get(base) if isinstance(models, Mapping) else None
    row = rows.get(population) if isinstance(rows, Mapping) else None
    if not isinstance(row, Mapping):
        return Threshold(base, mark, population, None,
                         f"the restart-point measure has no {population} row for {base}")
    fallback = row.get("fallback") if row.get("fallback") == FALLBACK_MAIN else None
    try:
        r, why = read_multiplier(base, table=table, env=env)
    except Exception as exc:                 # a config or table that cannot be read
        from moxi.config import ConfigError

        if not isinstance(exc, ConfigError):
            raise
        return Threshold(base, mark, population, None,
                         f"the model table cannot be read: {exc}", fallback=fallback)
    if r is None:
        return Threshold(base, mark, population, None, why, fallback=fallback)
    S, P, g, w = (_number(row.get("S")), _number(row.get("P")), _number(row.get("g")),
                  _number(row.get("w")))
    if S is None or P is None or g is None or w is None:
        return Threshold(base, mark, population, None,
                         f"the measured {population} row for {base} is incomplete",
                         fallback=fallback)
    if g <= 0 or r <= 0 or restart_cost(S, P, w, r) <= 0:
        return Threshold(base, mark, population, None,
                         f"the measured {population} row for {base} gives no restart "
                         "point", fallback=fallback)
    exact = mark_exact(S, P, g, w, r, mark)
    return Threshold(base, mark, population, math.ceil(exact), None, exact, fallback)


def all_marks(model: Any, population: str = MAIN, *, doc: Any = _UNSET, table: Any = None,
              env: Mapping[str, str] | None = None) -> dict[str, Threshold]:
    """Every mark's `Threshold` for `model` in `population`."""
    if doc is _UNSET:
        try:
            doc = params(env)
        except ParamsError:
            doc = _UNSET                     # each threshold names the error
    return {mark: threshold(model, mark, population, doc=doc, table=table, env=env)
            for mark in MARKS}


__all__ = ["MARK_MULTIPLES", "MARKS", "OFF", "MAIN", "SUBAGENT", "POPULATIONS",
           "FALLBACK_MAIN", "PARAMS_FILE",
           "PARAMS_SCHEMA", "ParamsError", "Threshold", "write_multiplier", "restart_cost",
           "optimum_calls", "mark_exact", "base_model", "population_of", "params_path",
           "load_params", "stamp", "params", "read_multiplier", "threshold", "all_marks"]

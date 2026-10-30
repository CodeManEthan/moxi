"""Token and cost reports over the index's stored claims.

Each report is a function `(conn, *, since, until, harness, project, model, tz)` that
returns a `Report`:

* `daily`, `monthly`: by day or month and model.
* `sessions`: by session, the main file and its subagents together, with its title,
  `cwd`, project and first and last request time.
* `models`: by model, with where its price came from.
* `projects`: by the project key the pass stored, and by `cwd` for rows with none.
* `status`: the index itself, by lane: its state at the last pass, its files, excluded,
  held and duplicate counts, the held rows' reasons, the last pass and the schema
  version. It counts no tokens, so the sections below are about the other five.

**What a report reads.** Only the requests that hold their key's claim (`won = 1`), joined
to their files, through the `requests_won_ts` index for a time range. It never reads the
`claimed` view, which works the claim out again over the whole index. Nothing here looks a
path up in projects: the `projects` report and the `project` filter read only the key the
pass stored, so a report never depends on a directory that may be gone.

**Days.** Days and months are cut when the report runs, from each request's own time, in
the zone `tz` names (an IANA name such as `"Europe/Paris"`, or `"UTC"`), local time when
`tz` is None. The zone's offset is worked out in Python for each stretch between its
changes in the report's range, so a day across a daylight-saving change is still the
zone's own day. `since` and `until` are dates in the same zone, both inclusive (a
`YYYY-MM-DD` text, a `date`, a `datetime`, or epoch seconds, where `until` is then the end,
exclusive); None leaves that end open.

**Filters.** `harness` is one harness; `model` is one model id as the request names it;
`project` matches the stored project key, and, for files with no key, their `cwd`, so a
row the `projects` report shows under a `cwd` can be asked for by that `cwd`.

**Rows.** Every row carries `requests`, the token sums by class (`input`, the whole prompt;
`fresh_input`; `cache_read`; `cache_write`, with `cache_write_1h` the 1-hour part of it;
`output`, reasoning included) and `tokens` (`input + output`), `cost` in dollars,
`unpriced_tokens` and the `unpriced_models` behind them, and `unknown_rows`. `cost` is
`UNKNOWN` only when nothing in the row could be priced; a row that is partly unpriced
shows the priced part, with `unpriced_tokens` above 0. A row with `unknown_rows` above 0
had a token count the harness did not report, so its sums are at least what they show;
`complete` is false for either. `rules` names a provider rule that stood in for an
unknown cache-write count (`moxi_usage.cost`). `harness_cost` is the harness's own cost
figure where it gave one (OpenCode), beside usage's, else None.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from moxi_sessions.api import UNKNOWN

from . import cost
from .cost import Sums

if TYPE_CHECKING:
    import sqlite3

    from .models import Table

KINDS = ("daily", "monthly", "sessions", "models", "projects", "status")
HARNESSES = ("claude", "codex", "opencode")

#: The model a request names: the one asked for, else the one that answered.
MODEL_SQL = "COALESCE(r.model, r.response_model)"
#: Requests whose `unknown` names input, cache read or output; or a cache-write column.
_CORE_UNKNOWN = ("input", "cache_read", "output")
_WRITE_UNKNOWN = ("cache_write", "cache_write_1h")
#: The longest stretch between two looks at a zone's offset, in seconds.
_OFFSET_STEP = 86_400
#: How many ids one `IN (...)` list holds.
_CHUNK = 500


class ReportError(ValueError):
    """A report's arguments cannot be used (exit 2)."""

    exit_code = 2


# --- the report -----------------------------------------------------------------------

@dataclass(frozen=True)
class Report:
    """One report's answer.

    `kind` is the report; `rows` its rows (dicts, see the module's text); `total` the same
    sums over every row; `since` and `until` the range in epoch seconds (None for an open
    end); `tz` the zone's name (`"local"` for local time); `filters` the filters given;
    `unpriced_models` every model the report could not price; `rules` every provider rule
    that applied; `index` what the report knows of the index it read: `passed` (a pass ran
    first), `last_pass` (epoch seconds, or None), and `note`, set when the report read the
    index without a fresh pass, or found none.
    """

    kind: str
    rows: list[dict[str, Any]]
    total: dict[str, Any]
    since: float | None = None
    until: float | None = None
    tz: str = "local"
    filters: Mapping[str, Any] = field(default_factory=dict)
    unpriced_models: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    index: Mapping[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """False when any count was unknown or any tokens unpriced."""
        return bool(self.total.get("complete", True))

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready: an `UNKNOWN` value is left out and named in its object's
        `"unknown"` list."""
        return {"kind": self.kind, "since": self.since, "until": self.until, "tz": self.tz,
                "filters": dict(self.filters), "rows": [_json(row) for row in self.rows],
                "total": _json(self.total), "unpriced_models": list(self.unpriced_models),
                "rules": list(self.rules), "index": dict(self.index)}


def _json(row: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    unknown = []
    for name, value in row.items():
        if value is UNKNOWN:
            unknown.append(name)
        else:
            out[name] = value
    if unknown:
        out["unknown"] = unknown
    return out


# --- zones and ranges -----------------------------------------------------------------

class Zone:
    """The zone days are cut in: an IANA name, `"UTC"`, or None for local time."""

    def __init__(self, tz: Any = None):
        if tz is None or tz == "local":
            self.name, self.tzinfo = "local", None
        elif isinstance(tz, dt.tzinfo):
            self.name, self.tzinfo = str(tz), tz
        elif isinstance(tz, str) and tz.upper() == "UTC":
            self.name, self.tzinfo = "UTC", dt.timezone.utc
        elif isinstance(tz, str):
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                self.name, self.tzinfo = tz, ZoneInfo(tz)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ReportError(f"{tz!r} is not a time zone this system knows") from exc
        else:
            raise ReportError(f"{tz!r} is not a time zone")

    @property
    def fixed(self) -> bool:
        return self.tzinfo is dt.timezone.utc

    def offset(self, ts: float) -> int:
        """The zone's offset from UTC at `ts`, in seconds."""
        if self.tzinfo is None:
            moment = dt.datetime.fromtimestamp(ts).astimezone()
        else:
            moment = dt.datetime.fromtimestamp(ts, self.tzinfo)
        return int(moment.utcoffset().total_seconds())

    def start_of(self, day: dt.date) -> float:
        """When `day` starts in the zone, in epoch seconds."""
        moment = dt.datetime(day.year, day.month, day.day, tzinfo=self.tzinfo)
        return moment.timestamp()

    def stamp(self, moment: dt.datetime) -> float:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.tzinfo)
        return moment.timestamp()


def bound(value: Any, zone: Zone, *, end: bool) -> float | None:
    """`since` or `until` as epoch seconds. A date is the start of that day in the zone;
    as `until` (`end`) it is the start of the next day, so the day is included."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ReportError(f"{value!r} is not a date or a time")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            if len(text) == 8 and text.isdigit():
                value = dt.date(int(text[:4]), int(text[4:6]), int(text[6:]))
            elif len(text) == 10:
                value = dt.date.fromisoformat(text)
            else:
                value = dt.datetime.fromisoformat(text)
        except ValueError as exc:
            raise ReportError(f"{value!r} is not a date (YYYY-MM-DD) or a time "
                              "(YYYY-MM-DDTHH:MM)") from exc
    if isinstance(value, dt.datetime):
        return zone.stamp(value)
    if isinstance(value, dt.date):
        return zone.start_of(value + dt.timedelta(days=1) if end else value)
    raise ReportError(f"{value!r} is not a date or a time")


def offset_sql(zone: Zone, lo: float | None, hi: float | None) -> str:
    """An SQL expression for the zone's offset at `r.ts`, exact over `[lo, hi]`: one
    `CASE` arm per change of offset in that range (a daylight-saving change, twice a year
    in most zones)."""
    if zone.fixed:
        return "0"
    if lo is None or hi is None:
        import time

        return str(zone.offset(time.time()))
    start, stop = int(lo) - 1, int(hi) + 1
    offset = zone.offset(start)
    changes: list[tuple[int, int]] = []
    at = start
    while at < stop:
        step = min(at + _OFFSET_STEP, stop)
        later = zone.offset(step)
        if later != offset:
            low, high = at, step                 # the first second at the new offset
            while high - low > 1:
                mid = (low + high) // 2
                if zone.offset(mid) == offset:
                    low = mid
                else:
                    high = mid
            changes.append((high, later))
            offset = later
        at = step
    if not changes:
        return str(offset)
    first = zone.offset(start)
    arms = []
    current = first
    for when, after in changes:
        arms.append(f"WHEN r.ts < {when} THEN {current}")
        current = after
    return f"(CASE {' '.join(arms)} ELSE {current} END)"


# --- the grouped query ----------------------------------------------------------------

@dataclass(frozen=True)
class _Query:
    since: float | None
    until: float | None
    zone: Zone
    harness: str | None
    project: str | None
    model: str | None

    def where(self) -> tuple[str, list[Any]]:
        clauses, args = ["r.won = 1"], []
        if self.since is not None:
            clauses.append("r.ts >= ?")
            args.append(self.since)
        if self.until is not None:
            clauses.append("r.ts < ?")
            args.append(self.until)
        if self.harness is not None:
            clauses.append("f.harness = ?")
            args.append(self.harness)
        if self.project is not None:
            clauses.append("(f.project = ? OR (f.project IS NULL AND f.cwd = ?))")
            args.extend([self.project, self.project])
        if self.model is not None:
            clauses.append(f"{MODEL_SQL} = ?")
            args.append(self.model)
        return " AND ".join(clauses), args

    def filters(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in ("harness", "project", "model")
                if getattr(self, name) is not None}


def _span(conn: sqlite3.Connection, query: _Query) -> tuple[float | None, float | None]:
    """The range the report covers: its bounds, an open end taken from the claimed
    requests' first or last time."""
    lo, hi = query.since, query.until
    if lo is None:
        row = conn.execute("SELECT MIN(ts) FROM requests WHERE won = 1").fetchone()
        lo = row[0]
    if hi is None:
        row = conn.execute("SELECT MAX(ts) FROM requests WHERE won = 1").fetchone()
        hi = None if row[0] is None else row[0] + 1
    return lo, hi


def _periods(table: Table, lo: float | None, hi: float | None) -> list[float]:
    """The times within the range at which an override row's price period starts or
    ends, sorted."""
    edges = set()
    for row in table.overrides:
        for edge in (row.start, row.end):
            if edge is not None and (lo is None or edge > lo) and (hi is None or edge < hi):
                edges.add(float(edge))
    return sorted(edges)


def _case(column: str, edges: list[float]) -> str:
    """The period a time falls in: 0 before the first edge, 1 before the second..."""
    if not edges:
        return "0"
    arms = " ".join(f"WHEN {column} < {edge!r} THEN {n}" for n, edge in enumerate(edges))
    return f"(CASE {arms} ELSE {len(edges)} END)"


def _size_bounds(conn: sqlite3.Connection, query: _Query, table: Table,
                 times: list[float]) -> list[int]:
    """The prompt sizes above which a tier of a model in range prices differently."""
    where, args = query.where()
    found: set[int] = set()
    for model, provider in conn.execute(
            f"SELECT DISTINCT {MODEL_SQL}, r.provider FROM requests r "
            f"JOIN files f ON f.id = r.file_id WHERE {where}", args):
        for at in times:
            facts = table.lookup(model, provider=provider, at=at)
            tiers = facts.tiers if isinstance(facts.tiers, Mapping) else {}
            for tier in tiers.values():
                above = tier.get("above_tokens") if isinstance(tier, Mapping) else None
                if isinstance(above, (int, float)) and not isinstance(above, bool):
                    found.add(int(above))
    return sorted(found)


def _size_case(bounds: list[int]) -> str:
    """The highest bound a request's whole prompt is above, else 0."""
    if not bounds:
        return "0"
    arms = " ".join(f"WHEN r.input > {b} THEN {b}" for b in sorted(bounds, reverse=True))
    return f"(CASE {arms} ELSE 0 END)"


def _unknown_sql(names: Iterable[str]) -> str:
    tests = " OR ".join(f"(',' || r.unknown || ',') LIKE '%,{name},%'" for name in names)
    return f"SUM(CASE WHEN r.unknown IS NULL THEN 0 WHEN {tests} THEN 1 ELSE 0 END)"


def _grouped_sql(query: _Query, keys: Mapping[str, str], sizes: list[int],
                 edges: list[float]) -> tuple[str, list[Any]]:
    """The grouped query and its arguments. Every group column has an alias no table
    column has, so `GROUP BY` cannot read a table's column of the same name."""
    where, args = query.where()
    columns = [f"{expr} AS k_{n}" for n, expr in enumerate(keys.values())]
    group = [f"k_{n}" for n in range(len(keys))]
    group += ["g_model", "g_provider", "g_speed", "g_tier", "g_size", "g_period"]
    sql = (
        f"SELECT {''.join(column + ', ' for column in columns)}"
        f"{MODEL_SQL} AS g_model, r.provider AS g_provider, r.speed AS g_speed,"
        " r.service_tier AS g_tier,"
        f" {_size_case(sizes)} AS g_size, {_case('r.ts', edges)} AS g_period,"
        " MIN(r.ts) AS first_ts, MAX(r.ts) AS last_ts, COUNT(*) AS requests,"
        " SUM(r.input) AS input, SUM(r.cache_read) AS cache_read,"
        " SUM(r.cache_write) AS cache_write, SUM(r.cache_write_1h) AS cache_write_1h,"
        " SUM(r.output) AS output,"
        " SUM(MAX(COALESCE(r.input, 0) - COALESCE(r.cache_read, 0)"
        " - COALESCE(r.cache_write, 0), 0)) AS fresh,"
        " SUM(MAX(COALESCE(r.cache_write, 0) - COALESCE(r.cache_write_1h, 0), 0))"
        " AS write_5m,"
        " SUM(r.harness_cost) AS harness_cost,"
        f" {_unknown_sql(_CORE_UNKNOWN)} AS unknown_core,"
        f" {_unknown_sql(_CORE_UNKNOWN + _WRITE_UNKNOWN)} AS unknown_any,"
        f" {_unknown_sql(_WRITE_UNKNOWN)} AS unknown_write"
        " FROM requests r JOIN files f ON f.id = r.file_id"
        f" WHERE {where}"
        f" GROUP BY {', '.join(group)}")
    return sql, args


def grouped(conn: sqlite3.Connection, query: _Query, keys: Mapping[str, str],
            table: Table, *, span: tuple[float | None, float | None] | None = None,
            ) -> list[tuple[dict[str, Any], cost.Priced, dict[str, Any]]]:
    """The claimed requests in range, summed per report key and price group, and priced.

    `keys` maps a name to its SQL expression over `r` (requests) and `f` (files). Each
    result is `(key values, the group's price, the group's facts)`, where the facts hold
    its model, provider, provenance, first and last time and harness cost. `span` is
    the range `_span` gives, when the caller has it already.
    """
    lo, hi = span if span is not None else _span(conn, query)
    edges = _periods(table, lo, hi)
    sizes = _size_bounds(conn, query, table, [0.0 if lo is None else lo, *edges])
    sql, args = _grouped_sql(query, keys, sizes, edges)
    names = list(keys)
    out = []
    for row in conn.execute(sql, args):
        model, provider = row["g_model"], row["g_provider"]
        facts = table.lookup(model, provider=provider, at=row["first_ts"])
        defaults = (table.defaults.get(facts.provider)
                    if isinstance(facts.provider, str) else None)
        sums = Sums(**{name: row[name] or 0 for name in Sums.__dataclass_fields__})
        priced = cost.price(sums, facts, defaults, size=row["g_size"],
                            service_tier=row["g_tier"], speed=row["g_speed"])
        values = {name: row[f"k_{n}"] for n, name in enumerate(names)}
        extra = {"model": model, "provider": provider, "facts": facts, "sums": sums,
                 "first_ts": row["first_ts"], "last_ts": row["last_ts"],
                 "harness_cost": row["harness_cost"]}
        out.append((values, priced, extra))
    return out


def explain(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
            tz: Any = "UTC") -> list[str]:
    """SQLite's plan for the grouped query of a `daily` report over `since..until`, one
    line per step: the way to see which index its range read uses."""
    query = _query(since, until, None, None, None, tz)
    offset = offset_sql(query.zone, *_span(conn, query))
    keys = {"day": f"strftime('%Y-%m-%d', r.ts + {offset}, 'unixepoch')"}
    sql, args = _grouped_sql(query, keys, [200_000], [])
    return [row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, args)]


# --- folding groups into rows ---------------------------------------------------------

_SUMMED = ("requests", "input", "fresh_input", "cache_read", "cache_write", "cache_write_1h",
           "output", "tokens", "unpriced_tokens", "unknown_rows")


class _Row:
    """One report row being summed."""

    def __init__(self, values: Mapping[str, Any]):
        self.values = dict(values)
        self.sums = dict.fromkeys(_SUMMED, 0)
        self.cost = 0.0
        self.priced = False
        self.unpriced: set[str] = set()
        self.rules: set[str] = set()
        self.harness_cost: float | None = None
        self.first_ts: float | None = None
        self.last_ts: float | None = None
        self.models: set[str] = set()
        self.provenance: list[dict[str, Any]] = []

    def add(self, priced: cost.Priced, extra: Mapping[str, Any]) -> None:
        sums: Sums = extra["sums"]
        for name, value in (("requests", sums.requests), ("input", sums.input),
                            ("fresh_input", sums.fresh), ("cache_read", sums.cache_read),
                            ("cache_write", sums.cache_write),
                            ("cache_write_1h", sums.cache_write_1h),
                            ("output", sums.output), ("tokens", sums.tokens),
                            ("unpriced_tokens", priced.unpriced_tokens),
                            ("unknown_rows", priced.unknown_rows)):
            self.sums[name] += value
        model = extra["model"]
        if priced.priced:
            self.cost += priced.cost
            self.priced = True
        else:
            self.unpriced.add(_label(model))
        if priced.rule:
            self.rules.add(priced.rule)
        if extra["harness_cost"] is not None:
            self.harness_cost = (self.harness_cost or 0.0) + extra["harness_cost"]
        for name, pick in (("first_ts", min), ("last_ts", max)):
            value = extra[name]
            if value is not None:
                current = getattr(self, name)
                setattr(self, name, value if current is None else pick(current, value))
        self.models.add(_label(model))
        provenance = extra["facts"].provenance.as_dict()
        if provenance not in self.provenance:
            self.provenance.append(provenance)

    def done(self, *, times: bool = False, models: bool = False,
             provenance: bool = False) -> dict[str, Any]:
        row = dict(self.values)
        row.update(self.sums)
        row["cost"] = (round(self.cost, 10) if self.priced or not self.sums["unpriced_tokens"]
                       else UNKNOWN)
        row["unpriced_models"] = sorted(self.unpriced)
        row["complete"] = not self.sums["unknown_rows"] and not self.sums["unpriced_tokens"]
        row["rules"] = sorted(self.rules)
        row["harness_cost"] = self.harness_cost
        if times:
            row["first_ts"], row["last_ts"] = self.first_ts, self.last_ts
        if models:
            row["models"] = sorted(self.models)
        if provenance:
            row["provenance"] = self.provenance
        return row


def _label(model: Any) -> str:
    return model if isinstance(model, str) and model else "(no model)"


def _fold(groups, key: Callable[[Mapping[str, Any], Mapping[str, Any]], tuple],
          values: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
          ) -> tuple[dict[tuple, _Row], _Row]:
    rows: dict[tuple, _Row] = {}
    total = _Row({})
    for found, priced, extra in groups:
        k = key(found, extra)
        row = rows.get(k)
        if row is None:
            row = rows[k] = _Row(values(found, extra))
        row.add(priced, extra)
        total.add(priced, extra)
    return rows, total


def _report(kind: str, query: _Query, rows: list[dict[str, Any]], total: _Row) -> Report:
    done = total.done()
    for name in ("rules", "unpriced_models"):
        done.pop(name)
    return Report(kind=kind, rows=rows, total=done, since=query.since, until=query.until,
                  tz=query.zone.name, filters=query.filters(),
                  unpriced_models=sorted(total.unpriced), rules=sorted(total.rules))


def _query(since: Any, until: Any, harness: str | None, project: str | None,
           model: str | None, tz: Any) -> _Query:
    zone = Zone(tz)
    if harness is not None and harness not in HARNESSES:
        raise ReportError(f"{harness!r} is not a harness ({', '.join(HARNESSES)})")
    start, end = bound(since, zone, end=False), bound(until, zone, end=True)
    if start is not None and end is not None and end <= start:
        raise ReportError("--until is before --since")
    return _Query(start, end, zone, harness, project or None, model or None)


def _table(table: Table | None) -> Table:
    if table is not None:
        return table
    from . import models

    return models.table()


# --- the reports ----------------------------------------------------------------------

def _by_time(kind: str, fmt: str, conn: sqlite3.Connection, *, since: Any, until: Any,
             harness: str | None, project: str | None, model: str | None, tz: Any,
             table: Table | None) -> Report:
    query = _query(since, until, harness, project, model, tz)
    current = _table(table)
    span = _span(conn, query)
    offset = offset_sql(query.zone, *span)
    name = "day" if kind == "daily" else "month"
    groups = grouped(conn, query, {name: f"strftime('{fmt}', r.ts + {offset}, 'unixepoch')"},
                     current, span=span)
    rows, total = _fold(groups, lambda k, e: (k[name], _label(e["model"])),
                        lambda k, e: {name: k[name], "model": e["model"]})
    ordered = [rows[k].done() for k in sorted(rows, key=lambda k: (k[0] or "", k[1]))]
    return _report(kind, query, ordered, total)


def daily(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
          harness: str | None = None, project: str | None = None, model: str | None = None,
          tz: Any = None, table: Table | None = None) -> Report:
    """Tokens and cost by day and model, oldest day first."""
    return _by_time("daily", "%Y-%m-%d", conn, since=since, until=until, harness=harness,
                    project=project, model=model, tz=tz, table=table)


def monthly(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
            harness: str | None = None, project: str | None = None, model: str | None = None,
            tz: Any = None, table: Table | None = None) -> Report:
    """Tokens and cost by month (`YYYY-MM`) and model, oldest month first."""
    return _by_time("monthly", "%Y-%m", conn, since=since, until=until, harness=harness,
                    project=project, model=model, tz=tz, table=table)


#: A session: the main session's id for a subagent or a child session, else its own; a
#: file with no session id is a session of its own.
_SESSION_SQL = "COALESCE(f.parent_session, f.session_id, 'file:' || f.id)"


def sessions(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
             harness: str | None = None, project: str | None = None,
             model: str | None = None, tz: Any = None, table: Table | None = None) -> Report:
    """Tokens and cost by session, a main session and its subagents together, with the
    session's title, `cwd` and project from its main file, its models and its first and
    last request in range; the earliest session first."""
    query = _query(since, until, harness, project, model, tz)
    groups = grouped(conn, query, {"harness": "f.harness", "session": _SESSION_SQL},
                     _table(table))
    rows, total = _fold(groups, lambda k, e: (k["harness"], k["session"]),
                        lambda k, e: {"harness": k["harness"], "session_id": k["session"]})
    facts = _session_facts(conn, list(rows))
    out = []
    for k, row in rows.items():
        done = row.done(times=True, models=True)
        found = facts.get(k, {})
        if k[1].startswith("file:"):
            done["session_id"] = None
        for name in ("title", "cwd", "project"):
            done[name] = found.get(name)
        out.append(done)
    out.sort(key=lambda r: (r["first_ts"] is None, r["first_ts"] or 0.0, r["harness"]))
    return _report("sessions", query, out, total)


def _session_facts(conn: sqlite3.Connection,
                   keys: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Each session's title, `cwd` and project, from its main file when it has one, else
    from the first of its files that gives each."""
    ids = sorted({sid for _, sid in keys if not sid.startswith("file:")})
    file_ids = sorted({int(sid[5:]) for _, sid in keys if sid.startswith("file:")})
    found: dict[tuple[str, str], dict[str, Any]] = {}
    rows: list[Any] = []
    for n in range(0, len(ids), _CHUNK):
        chunk = ids[n:n + _CHUNK]
        rows.extend(conn.execute(
            "SELECT harness, session_id AS session, kind, parent_session, title, cwd, project"
            f" FROM files WHERE session_id IN ({','.join('?' * len(chunk))})"
            " ORDER BY (kind = 'main' AND parent_session IS NULL) DESC, rank, id", chunk))
    for n in range(0, len(file_ids), _CHUNK):
        chunk = file_ids[n:n + _CHUNK]
        rows.extend(conn.execute(
            "SELECT harness, 'file:' || id AS session, kind, parent_session, title, cwd,"
            f" project FROM files WHERE id IN ({','.join('?' * len(chunk))})", chunk))
    for row in rows:
        if row["parent_session"] is not None and not row["session"].startswith("file:"):
            continue                              # a child session's own row
        slot = found.setdefault((row["harness"], row["session"]), {})
        for name in ("title", "cwd", "project"):
            if slot.get(name) is None and row[name] is not None:
                slot[name] = row[name]
    return found


def models_report(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
                  harness: str | None = None, project: str | None = None,
                  model: str | None = None, tz: Any = None,
                  table: Table | None = None) -> Report:
    """Tokens and cost by model, with where each price came from (`provenance`, one
    entry per distinct answer of the model table); the most tokens first."""
    query = _query(since, until, harness, project, model, tz)
    groups = grouped(conn, query, {}, _table(table))
    rows, total = _fold(groups, lambda k, e: (_label(e["model"]),),
                        lambda k, e: {"model": e["model"]})
    out = [row.done(provenance=True) for row in rows.values()]
    out.sort(key=lambda r: (-r["tokens"], _label(r["model"])))
    return _report("models", query, out, total)


def projects(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
             harness: str | None = None, project: str | None = None,
             model: str | None = None, tz: Any = None, table: Table | None = None) -> Report:
    """Tokens and cost by the project key the pass stored, and by `cwd` for files with
    none: a row has `project` set, or `project` None and its `cwd`. The most tokens
    first."""
    query = _query(since, until, harness, project, model, tz)
    groups = grouped(conn, query, {"project": "f.project",
                                   "cwd": "CASE WHEN f.project IS NULL THEN f.cwd END"},
                     _table(table))
    rows, total = _fold(groups, lambda k, e: (k["project"], k["cwd"]),
                        lambda k, e: {"project": k["project"], "cwd": k["cwd"]})
    out = [row.done(models=True) for row in rows.values()]
    out.sort(key=lambda r: (-r["tokens"], r["project"] or "", r["cwd"] or ""))
    return _report("projects", query, out, total)


#: How many held rows `status` names before it only counts them.
HELD_SHOWN = 20
#: `status`'s totals over its lanes.
_STATUS_SUMS = ("files", "excluded", "held", "requests", "duplicates")


def status(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
           harness: str | None = None, project: str | None = None,
           model: str | None = None, tz: Any = None, table: Table | None = None) -> Report:
    """The index itself: one row per lane, the lanes in the order a pass runs them.

    A row holds what the last pass learnt of the lane (`state`, `reason`, `listed`,
    `root`, `rank`, `compressed`, `unreadable`, `checked_at`, and for Codex the imports
    file's facts), with what the index holds for it now: `files`, `excluded` (rollouts
    left out as imported), `held` and `held_reasons` (each reason's count), `requests`
    and `duplicates` (copies of a request another file holds, counted once). `total`
    sums those and adds `held_rows` (the first `HELD_SHOWN` held rows with their
    reasons), `last_pass` (epoch seconds, or None) and `index_version`.

    `harness` keeps one harness's lanes. The index has no range, project or model to
    filter, so `status` refuses those; `tz` and `table` are not used.
    """
    import json

    from . import store

    _status_args(since, until, harness, project, model)
    try:
        known = json.loads(store.get_meta(conn, store.META_LANES) or "{}")
    except ValueError:
        known = {}
    if not isinstance(known, dict):
        known = {}
    counts: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
            "SELECT lane, harness, count(*) AS files, count(excluded) AS excluded,"
            " count(held) AS held FROM files GROUP BY lane, harness"):
        counts[row["lane"]] = {"harness": row["harness"], "files": row["files"],
                               "excluded": row["excluded"], "held": row["held"]}
    for row in conn.execute(
            "SELECT f.lane, count(*) AS requests, sum(r.won = 0) AS duplicates"
            " FROM requests r JOIN files f ON f.id = r.file_id GROUP BY f.lane"):
        counts.setdefault(row["lane"], {}).update(requests=row["requests"],
                                                  duplicates=row["duplicates"] or 0)
    reasons: dict[str, dict[str, int]] = {}
    for row in conn.execute("SELECT lane, held, count(*) FROM files WHERE held IS NOT NULL"
                            " GROUP BY lane, held ORDER BY lane, held"):
        reasons.setdefault(row[0], {})[row[1]] = row[2]
    rows = []
    for lane in sorted(set(known) | set(counts),
                       key=lambda name: (_lane_rank(known.get(name)), name)):
        learnt = known.get(lane) if isinstance(known.get(lane), dict) else {}
        held = counts.get(lane, {})
        row = {"lane": lane, "harness": learnt.get("harness", held.get("harness")),
               "state": learnt.get("state"), "reason": learnt.get("reason")}
        row.update({name: value for name, value in learnt.items() if name not in row})
        for name in _STATUS_SUMS:
            row[name] = int(held.get(name) or 0)
        row["held_reasons"] = reasons.get(lane, {})
        if harness is None or row["harness"] == harness:
            rows.append(row)
    lanes = [row["lane"] for row in rows]
    held_rows = []
    if lanes:
        marks = ",".join("?" * len(lanes))
        held_rows = [dict(row) for row in conn.execute(
            f"SELECT lane, path, part, held AS reason FROM files WHERE held IS NOT NULL"
            f" AND lane IN ({marks}) ORDER BY lane, path, part LIMIT ?",
            (*lanes, HELD_SHOWN))]
    last = store.get_meta(conn, store.META_LAST_PASS)
    total = _status_total(rows, held_rows,
                          last_pass=None if last is None else float(last),
                          version=conn.execute("PRAGMA user_version").fetchone()[0])
    return Report(kind="status", rows=rows, total=total,
                  filters={} if harness is None else {"harness": harness})


def _status_args(since: Any, until: Any, harness: str | None, project: str | None,
                 model: str | None) -> None:
    given = [name for name, value in (("since", since), ("until", until),
                                      ("project", project), ("model", model))
             if value is not None]
    if given:
        raise ReportError(f"status covers the whole index; it takes no {', '.join(given)}")
    if harness is not None and harness not in HARNESSES:
        raise ReportError(f"{harness!r} is not a harness ({', '.join(HARNESSES)})")


def _lane_rank(learnt: Any) -> float:
    rank = learnt.get("rank") if isinstance(learnt, dict) else None
    return float(rank) if isinstance(rank, (int, float)) else float("inf")


def _status_total(rows: list[dict[str, Any]], held_rows: list[dict[str, Any]], *,
                  last_pass: float | None, version: int) -> dict[str, Any]:
    total: dict[str, Any] = {name: sum(row[name] for row in rows) for name in _STATUS_SUMS}
    total.update(lanes=len(rows), held_rows=held_rows, last_pass=last_pass,
                 index_version=version)
    return total


REPORTS: dict[str, Callable[..., Report]] = {
    "daily": daily, "monthly": monthly, "sessions": sessions, "models": models_report,
    "projects": projects, "status": status,
}


# --- a report from the API ------------------------------------------------------------

def run_pass(*, env: Mapping[str, str] | None = None) -> None:
    """The pass a fresh report runs first (`moxi_usage.index.pass_`), under `env`. It
    raises `moxi.files.LockTimeout` when another pass held the index past the wait."""
    from . import index

    index.pass_(env=env)


def report(kind: str, *, since: Any = None, until: Any = None, harness: str | None = None,
           project: str | None = None, model: str | None = None, tz: Any = None,
           fresh: bool = True, env: Mapping[str, str] | None = None,
           table: Table | None = None) -> Report:
    """One report by `kind` (`KINDS`). With `fresh`, the pass runs first, and when
    another pass holds the index past the wait the report reads the index as it is and
    says so in `Report.index["note"]`. With no index yet, the report is empty and says so.
    Raises `ReportError` for an argument it cannot use."""
    from moxi.files import LockTimeout

    if kind not in REPORTS:
        raise ReportError(f"{kind!r} is not a report ({', '.join(KINDS)})")
    passed, note = False, None
    if fresh:
        try:
            run_pass(env=env)
            passed = True
        except LockTimeout as exc:
            note = (f"another pass held the index ({exc}); this report reads the index "
                    "as it is")
    else:
        note = "no pass ran first; this report reads the index as it is"
    return read(kind, since=since, until=until, harness=harness, project=project,
                model=model, tz=tz, passed=passed, note=note, env=env, table=table)


def read(kind: str, *, since: Any = None, until: Any = None, harness: str | None = None,
         project: str | None = None, model: str | None = None, tz: Any = None,
         passed: bool = False, note: str | None = None,
         env: Mapping[str, str] | None = None, table: Table | None = None) -> Report:
    """One report by `kind` from the index as it is, running no pass: what `report`
    does after its pass. `passed` and `note` go into `Report.index` as given, for a
    caller that ran the pass itself (`moxi usage index`)."""
    from . import store

    fn = REPORTS.get(kind)
    if fn is None:
        raise ReportError(f"{kind!r} is not a report ({', '.join(KINDS)})")
    path = store.index_path(env)
    if not path.exists():
        empty = _empty(kind, since, until, harness, project, model, tz)
        return replace(empty, index={"passed": passed, "last_pass": None,
                                     "note": "there is no index yet; run "
                                             "`moxi usage index` to build it"})
    conn = store.connect(env=env, create=False)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != store.INDEX_VERSION:
            import moxi.store

            if version > store.INDEX_VERSION:
                raise moxi.store.StoreTooNew(str(path), version, store.INDEX_VERSION)
            empty = _empty(kind, since, until, harness, project, model, tz, version=version)
            return replace(empty, index={
                "passed": passed, "last_pass": None,
                "note": f"the index is at version {version}, before this usage's "
                        f"{store.INDEX_VERSION}; run `moxi usage index` to bring it up"})
        result = fn(conn, since=since, until=until, harness=harness, project=project,
                    model=model, tz=tz, table=table)
        last = store.get_meta(conn, store.META_LAST_PASS)
    finally:
        conn.close()
    return replace(result, index={"passed": passed,
                                  "last_pass": None if last is None else float(last),
                                  "note": note})


def _empty(kind: str, since: Any, until: Any, harness: str | None, project: str | None,
           model: str | None, tz: Any, *, version: int = 0) -> Report:
    if kind == "status":
        _status_args(since, until, harness, project, model)
        return Report(kind="status", rows=[],
                      total=_status_total([], [], last_pass=None, version=version),
                      filters={} if harness is None else {"harness": harness})
    query = _query(since, until, harness, project, model, tz)
    return _report(kind, query, [], _Row({}))

"""Token and cost reports over the index's stored claims.

Each report is a function `(conn, *, since, until, harness, project, folder, model,
tz)` that returns a `Report`:

* `daily`, `monthly`: by day or month and model.
* `sessions`: by session, the main file and its subagents together, with its title,
  `cwd`, project and first and last request time.
* `models`: by model, with where its price came from.
* `projects`: by the project key the pass stored, and by `cwd` for rows with none; or,
  with `by="folder"`, by `cwd` alone.
* `session`: one session, which it takes as `session=` beside the rest, by agent and
  model: the main file as agent `main`, each subagent or workflow file under its own
  agent id. Its sessions are the `sessions` report's, so its totals equal that
  session's `sessions` row over the same range and filters.
* `terms`: every term of a cost (`moxi_usage.cost`), by model, day, month or, with
  `session=`, by the session's agents (`by=`): each row's price groups with their five
  terms, and its cost split by token class and price tier; its total says what caching
  saved (`saving`), or None when it cannot.
* `requests`: one row per request of one session's agent (`session=`, and `agent=`,
  `main` by default), in the order the agent made them, each priced on its own, with
  the session's compactions placed between them.
* `status`: the index itself, by lane: its state at the last pass, its files, excluded,
  held and duplicate counts, the held rows' reasons, the last pass and the schema
  version. It counts no tokens, so the sections below are about the others.

Beside the reports, `session_context` and `session_contexts` read how full a session's
context was at its newest request, one session at a time.

**What a report reads.** Only the requests that hold their key's claim (`won = 1`), joined
to their files, through the `requests_won_ts` index for a time range (the `session`
report goes by its own files instead). It never reads the `claimed` view, which works the
claim out again over the whole index. Nothing here looks a
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
row the `projects` report shows under a `cwd` can be asked for by that `cwd`. `folder` is
a folder's whole path and matches the files whose `cwd` it is, whatever key they carry:
the pass stores a key only while projects is installed, and a file read before it was
removed keeps its key, so a caller that groups by folder asks by `folder`.

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
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from moxi_sessions.api import UNKNOWN

from . import cost
from .cost import Sums

if TYPE_CHECKING:
    import sqlite3

    from .models import Table

KINDS = ("daily", "monthly", "sessions", "models", "projects", "session", "terms",
         "requests", "status")
HARNESSES = ("claude", "codex", "opencode")

#: The model a request names: the one asked for, else the one that answered.
MODEL_SQL = "COALESCE(r.model, r.response_model)"
#: A file's session: the main session's id for a subagent or a child session, else its
#: own; a file with no session id is a session of its own. The `sessions` report groups
#: by it and the `session` report filters on it, so the two count the same files.
_SESSION_SQL = "COALESCE(f.parent_session, f.session_id, 'file:' || f.id)"
#: Requests whose `unknown` names input, cache read or output; or a cache-write column.
_CORE_UNKNOWN = ("input", "cache_read", "output")
_WRITE_UNKNOWN = ("cache_write", "cache_write_1h")
#: One request's fresh input and 5-minute write, floored at 0: the grouped query sums
#: them and the requests report reads them per request, so both price the same terms.
_FRESH_SQL = ("MAX(COALESCE(r.input, 0) - COALESCE(r.cache_read, 0)"
              " - COALESCE(r.cache_write, 0), 0)")
_WRITE_5M_SQL = "MAX(COALESCE(r.cache_write, 0) - COALESCE(r.cache_write_1h, 0), 0)"
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
        `"unknown"` list (after the token columns a `requests` row names there)."""
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
        elif isinstance(value, Mapping):
            out[name] = _json(value)
        elif isinstance(value, list):
            out[name] = [_json(item) if isinstance(item, Mapping) else item
                         for item in value]
        else:
            out[name] = value
    if unknown:
        # a row that names its own unknowns (a request's unreported token columns) keeps
        # them, and its unknown facts join them
        own = out.get("unknown")
        out["unknown"] = [*own, *unknown] if isinstance(own, list) else unknown
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
    session: str | None = None
    agent: str | None = None
    folder: str | None = None

    def where(self, *, claimed: bool = True) -> tuple[str, list[Any]]:
        """The `WHERE` text over `r` and `f` and its arguments: the claimed requests (every
        copy, with `claimed` false) in range, under the filters, of the session and, when
        `agent` is set, of that agent's files."""
        # One session's read goes by its files and their requests' primary key; the `+`
        # keeps SQLite from taking the won-and-time index over the whole index instead.
        clauses, args = [], []
        if claimed:
            clauses.append("r.won = 1" if self.session is None else "+r.won = 1")
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
        if self.folder is not None:
            clauses.append("f.cwd = ?")
            args.append(self.folder)
        if self.model is not None:
            clauses.append(f"{MODEL_SQL} = ?")
            args.append(self.model)
        if self.session is not None:
            agent = f" AND {_AGENT_SQL} = ?" if self.agent is not None else ""
            clauses.append(f"r.file_id IN (SELECT f.id FROM files f WHERE {_SESSION_SQL} = ?"
                           f"{agent})")
            args.append(self.session)
            if self.agent is not None:
                args.append(self.agent)
        return " AND ".join(clauses) or "1", args

    def filters(self) -> dict[str, Any]:
        return {name: getattr(self, name)
                for name in ("harness", "project", "folder", "model", "session", "agent")
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


def _unknown_case(names: Iterable[str]) -> str:
    """1 for a request whose `unknown` names one of `names`, else 0."""
    tests = " OR ".join(f"(',' || r.unknown || ',') LIKE '%,{name},%'" for name in names)
    return f"(CASE WHEN r.unknown IS NULL THEN 0 WHEN {tests} THEN 1 ELSE 0 END)"


def _unknown_sql(names: Iterable[str]) -> str:
    return f"SUM{_unknown_case(names)}"


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
        f" SUM({_FRESH_SQL}) AS fresh,"
        f" SUM({_WRITE_5M_SQL}) AS write_5m,"
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
    its model, provider, provenance, first and last time, harness cost, prompt-size
    bound (`size`) and price period (`period`, an index into this call's edges). `span`
    is the range `_span` gives, when the caller has it already.
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
                 "harness_cost": row["harness_cost"], "size": row["g_size"],
                 "period": row["g_period"]}
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

    #: Whether `add` keeps the distinct provenances (`done(provenance=True)`).
    keeps_provenance = True

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
        if self.keeps_provenance:
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
          make: Callable[[Mapping[str, Any]], _Row] = _Row, total: _Row | None = None,
          ) -> tuple[dict[tuple, _Row], _Row]:
    rows: dict[tuple, _Row] = {}
    total = make({}) if total is None else total
    for found, priced, extra in groups:
        k = key(found, extra)
        row = rows.get(k)
        if row is None:
            row = rows[k] = make(values(found, extra))
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
           model: str | None, tz: Any, session: str | None = None,
           agent: str | None = None, *, folder: str | None = None) -> _Query:
    zone = Zone(tz)
    folder = _folder(folder)
    if harness is not None and harness not in HARNESSES:
        raise ReportError(f"{harness!r} is not a harness ({', '.join(HARNESSES)})")
    start, end = bound(since, zone, end=False), bound(until, zone, end=True)
    if start is not None and end is not None and end <= start:
        raise ReportError("--until is before --since")
    return _Query(start, end, zone, harness, project or None, model or None, session,
                  agent, folder)


def _folder(folder: str | None) -> str | None:
    """The `folder` filter as files hold a `cwd`: a whole path, with no slash after it."""
    if not folder:
        return None
    if not isinstance(folder, str) or not os.path.isabs(folder):
        raise ReportError(f"{folder!r} is not a folder's whole path")
    return folder.rstrip("/") or "/"


def _table(table: Table | None) -> Table:
    if table is not None:
        return table
    from . import models

    return models.table()


# --- the reports ----------------------------------------------------------------------

def _by_time(kind: str, fmt: str, conn: sqlite3.Connection, *, since: Any, until: Any,
             harness: str | None, project: str | None, model: str | None, tz: Any,
             table: Table | None, folder: str | None) -> Report:
    query = _query(since, until, harness, project, model, tz, folder=folder)
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
          tz: Any = None, table: Table | None = None, folder: str | None = None) -> Report:
    """Tokens and cost by day and model, oldest day first."""
    return _by_time("daily", "%Y-%m-%d", conn, since=since, until=until, harness=harness,
                    project=project, model=model, tz=tz, table=table, folder=folder)


def monthly(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
            harness: str | None = None, project: str | None = None, model: str | None = None,
            tz: Any = None, table: Table | None = None,
            folder: str | None = None) -> Report:
    """Tokens and cost by month (`YYYY-MM`) and model, oldest month first."""
    return _by_time("monthly", "%Y-%m", conn, since=since, until=until, harness=harness,
                    project=project, model=model, tz=tz, table=table, folder=folder)


def sessions(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
             harness: str | None = None, project: str | None = None,
             model: str | None = None, tz: Any = None, table: Table | None = None,
             folder: str | None = None) -> Report:
    """Tokens and cost by session, a main session and its subagents together, with the
    session's title, `cwd` and project from its main file, its models and its first and
    last request in range; the earliest session first."""
    query = _query(since, until, harness, project, model, tz, folder=folder)
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


def _window(reported: Any, facts: Any) -> Any:
    """The window a request ran against: the one its harness reported, else the model
    table's for its model, provider and time, else `UNKNOWN`."""
    if isinstance(reported, int) and not isinstance(reported, bool) and reported > 0:
        return reported
    window = facts.window
    if isinstance(window, (int, float)) and not isinstance(window, bool) and window > 0:
        return window
    return UNKNOWN


#: The newest claimed request of a session's main file. It goes by the session's files
#: and their requests' primary key; the `+` keeps SQLite off the won-and-time index.
_NEWEST_SQL = (
    f"SELECT r.input, {MODEL_SQL}, r.provider, r.ts, r.context_window"
    " FROM files f JOIN requests r ON r.file_id = f.id"
    " WHERE f.session_id = ? AND f.harness = ? AND f.kind = 'main'"
    " AND f.parent_session IS NULL AND +r.won = 1"
    " ORDER BY r.ts IS NULL, r.ts DESC, f.id DESC, r.seq DESC LIMIT 1")


def session_context(conn: sqlite3.Connection, harness: str | None, session_id: str, *,
                    table: Table | None = None) -> dict[str, Any] | None:
    """How full one session's context was at its newest request: `{tokens, window}`,
    where `tokens` is the whole prompt of the newest claimed request of the session's
    main file, whenever it was made, and `window` the one it ran against (the `requests`
    report's rule: the harness's own, else the model table's, else None).

    None when the session has no main file in the index, its main file holds no claimed
    request, or that request's prompt size is unknown. `harness` None finds the one
    harness that holds a main file of that id, and gives None when two do. One read by
    the session's files; nothing is summed."""
    if not session_id:
        return None
    if harness is None:
        found = conn.execute(
            "SELECT DISTINCT harness FROM files WHERE session_id = ? AND kind = 'main'"
            " AND parent_session IS NULL LIMIT 2", (session_id,)).fetchall()
        if len(found) != 1:
            return None
        harness = found[0][0]
    cursor = conn.cursor()
    cursor.row_factory = None
    row = cursor.execute(_NEWEST_SQL, (session_id, harness)).fetchone()
    if row is None or row[0] is None:
        return None
    tokens, name, provider, ts, reported = row
    window = _window(reported, _table(table).lookup(name, provider=provider, at=ts))
    return {"tokens": tokens, "window": None if window is UNKNOWN else int(window)}


def readable(env: Mapping[str, str] | None = None) -> bool:
    """True when there is an index at this usage's version, so a report over it is a
    read and not the empty answer given for no index or an older one. Raises
    `moxi.store.StoreTooNew` for an index a newer usage wrote, as a report does."""
    from . import store

    path = store.index_path(env)
    if not path.exists():
        return False
    conn = store.connect(env=env, create=False)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    if version > store.INDEX_VERSION:
        import moxi.store

        raise moxi.store.StoreTooNew(str(path), version, store.INDEX_VERSION)
    return version == store.INDEX_VERSION


def session_contexts(keys: Iterable[tuple[str | None, str]], *,
                     env: Mapping[str, str] | None = None, table: Table | None = None,
                     ) -> dict[tuple[str | None, str], dict[str, Any] | None]:
    """`session_context` for each `(harness, session id)` of `keys`, from the index as
    it is: one read per key, no pass, and nothing written. Every key is None when there
    is no index or it is at another version, as a report over it would be empty."""
    from . import store

    wanted = list(dict.fromkeys(keys))
    out: dict[tuple[str | None, str], dict[str, Any] | None] = dict.fromkeys(wanted)
    if not wanted or not store.index_path(env).exists():
        return out
    conn = store.connect(env=env, create=False)
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] != store.INDEX_VERSION:
            return out
        current = _table(table)
        for harness, session_id in wanted:
            out[harness, session_id] = session_context(conn, harness, session_id,
                                                       table=current)
    finally:
        conn.close()
    return out


def session_origins(keys: Iterable[tuple[str | None, str]], *,
                    env: Mapping[str, str] | None = None,
                    ) -> dict[tuple[str | None, str], dict[str, Any] | None]:
    """What the index holds of where and when each session of `keys` began, for each
    `(harness, session id)`: `{harness, cwd, first_ts}`, from the index as it is, with
    no pass and nothing written.

    `cwd` is the folder the `sessions` report gives the session: its main file's, else
    the first of its files that has one. `first_ts` is the time of the earliest request
    the index holds in any of its files, whatever range a report is cut to; None when
    none of them holds a request with a time. `harness` None finds the one harness that
    holds a main file of that id, and `harness` in the answer says which.

    A key is None when the index holds no file of that session, when `harness` is None
    and no harness or more than one holds a main file of that id, and when there is no
    index or it is at another version. One read per key, by the session's files."""
    from . import store

    wanted = list(dict.fromkeys(keys))
    out: dict[tuple[str | None, str], dict[str, Any] | None] = dict.fromkeys(wanted)
    if not wanted or not store.index_path(env).exists():
        return out
    conn = store.connect(env=env, create=False)
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] != store.INDEX_VERSION:
            return out
        cursor = conn.cursor()
        cursor.row_factory = None
        for harness, session_id in wanted:
            if not session_id:
                continue
            files = cursor.execute(
                "SELECT harness, kind = 'main', cwd, first_ts FROM files"
                " WHERE session_id = ? AND parent_session IS NULL"
                " ORDER BY kind = 'main' DESC, rank, id", (session_id,)).fetchall()
            if harness is None:
                holders = {row[0] for row in files if row[1]}
                if len(holders) != 1:
                    continue
                held_by = holders.pop()
            else:
                held_by = harness
            files = [row for row in files if row[0] == held_by]
            if not files:
                continue
            firsts = [row[3] for row in files if row[3] is not None]
            out[harness, session_id] = {
                "harness": held_by,
                "cwd": next((row[2] for row in files if row[2] is not None), None),
                "first_ts": min(firsts) if firsts else None}
    finally:
        conn.close()
    return out


#: A file's agent in its session: `main` for the session's main file, else the file's
#: agent id (a child session's is its own session id).
_AGENT_SQL = ("CASE WHEN f.kind = 'main' AND f.parent_session IS NULL THEN 'main'"
             " ELSE COALESCE(f.agent_id, f.session_id, 'file:' || f.id) END")


def session(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
            harness: str | None = None, project: str | None = None,
            model: str | None = None, tz: Any = None, table: Table | None = None,
            folder: str | None = None, session: str | None = None) -> Report:
    """One session by agent and model: the main file's requests as agent `main`, each
    subagent or workflow file's under its agent id, with the file's `kind` and title (for
    an agent of several titled files, the title of the one whose request in range comes
    first), and each row's first and last request in range. `main` comes first, then each agent's
    rows together, the agents by their first request.

    `session` is the id a `sessions` row gives, and the files it takes are the ones that
    report groups under that id, so the rows sum to that row over the same range and
    filters. `harness` tells apart two harnesses' sessions of the same id. Without
    `session` the report is refused."""
    if not session:
        raise ReportError("the session report needs a session id")
    query = _query(since, until, harness, project, model, tz, session, folder=folder)
    groups = grouped(conn, query, {"agent": _AGENT_SQL, "kind": "f.kind", "title": "f.title"},
                     _table(table))
    # An agent of several titled files takes the title of the one with the earliest request
    # in range (the lesser title on a tie), whatever order the groups come in.
    firsts: dict[str, tuple[bool, float, str]] = {}
    for found, _, extra in groups:
        if found["title"] is not None:
            first = extra["first_ts"]
            rank = (first is None, first or 0.0, found["title"])
            firsts[found["agent"]] = min(firsts.get(found["agent"], rank), rank)
    titles = {agent: rank[2] for agent, rank in firsts.items()}
    rows, total = _fold(groups, lambda k, e: (k["agent"], k["kind"], _label(e["model"])),
                        lambda k, e: {"agent": k["agent"], "kind": k["kind"],
                                      "model": e["model"]})
    out = []
    starts: dict[str, float] = {}
    for row in rows.values():
        done = row.done(times=True)
        done["title"] = titles.get(done["agent"])
        if done["first_ts"] is not None:
            starts[done["agent"]] = min(starts.get(done["agent"], done["first_ts"]),
                                        done["first_ts"])
        out.append(done)
    out.sort(key=lambda r: (r["agent"] != "main", r["agent"] not in starts,
                            starts.get(r["agent"], 0.0), r["agent"],
                            r["first_ts"] is None, r["first_ts"] or 0.0, _label(r["model"])))
    return _report("session", query, out, total)


def models_report(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
                  harness: str | None = None, project: str | None = None,
                  model: str | None = None, tz: Any = None,
                  table: Table | None = None, folder: str | None = None) -> Report:
    """Tokens and cost by model, with where each price came from (`provenance`, one
    entry per distinct answer of the model table); the most tokens first."""
    query = _query(since, until, harness, project, model, tz, folder=folder)
    groups = grouped(conn, query, {}, _table(table))
    rows, total = _fold(groups, lambda k, e: (_label(e["model"]),),
                        lambda k, e: {"model": e["model"]})
    out = [row.done(provenance=True) for row in rows.values()]
    out.sort(key=lambda r: (-r["tokens"], _label(r["model"])))
    return _report("models", query, out, total)


def projects(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
             harness: str | None = None, project: str | None = None,
             model: str | None = None, tz: Any = None, table: Table | None = None,
             folder: str | None = None, by: str | None = "project") -> Report:
    """Tokens and cost by the project key the pass stored, and by `cwd` for files with
    none: a row has `project` set, or `project` None and its `cwd`. With `by="folder"`
    (`PROJECTS_BY`) it is by `cwd` alone, whatever key a file carries: every row has
    `project` None and its `cwd`, so a key stored by an earlier pass groups nothing. The
    most tokens first."""
    by = _projects_by(by)
    query = _query(since, until, harness, project, model, tz, folder=folder)
    keys = ({"cwd": "f.cwd"} if by == "folder" else
            {"project": "f.project", "cwd": "CASE WHEN f.project IS NULL THEN f.cwd END"})
    groups = grouped(conn, query, keys, _table(table))
    rows, total = _fold(groups, lambda k, e: (k.get("project"), k["cwd"]),
                        lambda k, e: {"project": k.get("project"), "cwd": k["cwd"]})
    out = [row.done(models=True) for row in rows.values()]
    out.sort(key=lambda r: (-r["tokens"], r["project"] or "", r["cwd"] or ""))
    return _report("projects", query, out, total)


#: What a `projects` report's rows can be keyed by.
PROJECTS_BY = ("project", "folder")


def _projects_by(by: Any) -> str:
    by = "project" if by is None else by
    if by not in PROJECTS_BY:
        raise ReportError(f"{by!r} is not a key of the projects report "
                          f"({', '.join(PROJECTS_BY)})")
    return by


# --- the cost terms -------------------------------------------------------------------

#: What a `terms` report's rows can be keyed by.
TERMS_BY = ("model", "day", "month", "agent")
#: The dollar sums of a `terms` total's `saving`, as `cost.Saving` names them.
_SAVING_SUMS = ("reads_at_input", "reads_cost", "write_extra")


def _terms_by(by: Any, session: str | None) -> str:
    """`by` as the `terms` report takes it: `model` when None. `agent` needs `session`,
    since an agent id names an agent only within its session."""
    if by is None:
        return "model"
    if by not in TERMS_BY:
        raise ReportError(f"{by!r} is not a way to group the terms ({', '.join(TERMS_BY)})")
    if by == "agent" and not session:
        raise ReportError("the terms report by agent needs a session id: an agent id names "
                          "an agent only within its session")
    return by


class _TermsRow(_Row):
    """A `terms` row being summed: a report row with its price groups and its cost split
    by class and tier. A price group here is one model, provider, tier, prompt-size bound
    and price period; the grouped query's groups that pay the same prices fold into it,
    the priced and the unpriced apart, so its cost is still the sum of its terms."""

    def __init__(self, values: Mapping[str, Any]):
        super().__init__(values)
        self.groups: dict[tuple, dict[str, Any]] = {}
        self.split: dict[tuple[str, str | None], list] = {}
        self.with_groups = True
        #: What caching saved, summed for a total only: the three sums, or `UNKNOWN`
        #: once a group that holds cache reads or writes could not give its part.
        self.with_saving = False
        self.saving: Any = [0.0, 0.0, 0.0]

    def add(self, priced: cost.Priced, extra: Mapping[str, Any]) -> None:
        super().add(priced, extra)
        if self.with_saving and self.saving is not UNKNOWN:
            part = cost.saving(priced)
            if part is UNKNOWN:
                self.saving = UNKNOWN
            else:
                for n, name in enumerate(_SAVING_SUMS):
                    self.saving[n] += getattr(part, name)
        paid = priced.prices
        tier = None if paid is None else paid.tier
        if priced.priced:
            for term in priced.terms:
                if term.tokens:
                    slot = self.split.setdefault((term.cls, tier), [0, 0.0])
                    slot[0] += term.tokens
                    slot[1] += term.cost
        if not self.with_groups:
            return
        model, facts = extra["model"], extra["facts"]
        key = (_label(model), repr(extra["provider"]), extra["period"], tier, paid,
               priced.priced, priced.why if paid is None else None)
        group = self.groups.get(key)
        if group is None:
            tiers = facts.tiers if isinstance(facts.tiers, Mapping) else {}
            above = (tiers.get(tier) or {}).get("above_tokens") if tier is not None else None
            period = facts.provenance.period
            self.groups[key] = {
                "model": model, "provider": extra["provider"], "tier": tier,
                "above": int(above) if isinstance(above, (int, float)) else 0,
                "period": None if period is None else dict(period),
                "provenance": facts.provenance.as_dict(), "requests": extra["sums"].requests,
                "terms": [term.as_dict() for term in priced.terms], "cost": priced.cost,
                "unpriced_tokens": priced.unpriced_tokens,
                "unknown_rows": priced.unknown_rows, "rule": priced.rule, "why": priced.why}
            return
        group["requests"] += extra["sums"].requests
        group["unpriced_tokens"] += priced.unpriced_tokens
        group["unknown_rows"] += priced.unknown_rows
        group["rule"] = group["rule"] or priced.rule
        for slot, term in zip(group["terms"], priced.terms, strict=True):
            slot["tokens"] += term.tokens
            if slot["cost"] is not UNKNOWN:
                slot["cost"] += term.cost
        if priced.priced:
            group["cost"] += priced.cost
        elif paid is not None:
            missing = [slot["class"] for slot in group["terms"]
                       if slot["tokens"] and slot["price"] is UNKNOWN]
            group["why"] = f"no {', '.join(missing)} price"

    def done(self, **kwargs: Any) -> dict[str, Any]:
        row = super().done(**kwargs)
        if self.with_groups:
            row["groups"] = [self.groups[k] for k in sorted(
                self.groups, key=lambda k: (k[0], k[1], k[2], k[3] is not None, k[3] or "",
                                            not k[5]))]
        order = {name: n for n, name in enumerate(cost.PRICE_TERMS)}
        row["split"] = [{"class": cls, "tier": tier, "tokens": tokens, "cost": amount}
                        for (cls, tier), (tokens, amount) in sorted(
                            self.split.items(),
                            key=lambda item: (order[item[0][0]], item[0][1] is not None,
                                              item[0][1] or ""))]
        if self.with_saving:
            row["saving"] = self._saving()
        return row

    def _saving(self) -> dict[str, Any] | None:
        """The total's saving, or None: with no cache read there is nothing to state,
        and with a part unknown the sum is not the answer's."""
        if self.saving is UNKNOWN or not self.sums["cache_read"]:
            return None
        reads_at_input, reads_cost, write_extra = self.saving
        return {"input_tokens": self.sums["input"], "read_tokens": self.sums["cache_read"],
                "reads_at_input": round(reads_at_input, 10),
                "reads_cost": round(reads_cost, 10),
                "write_extra": round(write_extra, 10),
                "saved": round(reads_at_input - reads_cost - write_extra, 10)}


def _terms_total() -> _TermsRow:
    total = _TermsRow({})
    total.with_groups = False
    total.with_saving = True
    return total


def terms(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
          harness: str | None = None, project: str | None = None, model: str | None = None,
          tz: Any = None, table: Table | None = None, folder: str | None = None,
          by: str | None = "model", session: str | None = None) -> Report:
    """Every term of a cost: one row per key `by` names (`TERMS_BY`), a model, a day, a
    month, or an agent of the one session `session` names (it needs `session`; `session`
    alone narrows the other keys to that session).

    A row carries the sums every report row carries, `groups` (its price groups, each with
    its model, provider, `tier`, `above` (the tier's prompt-size bound, 0 for none),
    `period` (the dated override row's `from` and `to`, or None), `provenance`, `requests`,
    the five `terms` of `moxi_usage.cost`, `cost`, `unpriced_tokens`, `unknown_rows`,
    `rule` and `why`) and `split`: `{class, tier, tokens, cost}` summed over its priced
    groups, by class and then tier (None first). An unpriced group adds nothing to `split`.
    A row's cost is the sum of its groups' costs and of its split's. `total` carries the
    sums and `split` over every row, and `saving`: what caching saved over the answer, an
    estimate of the same tokens with no cache at the same prices (`moxi_usage.cost`), as
    `{input_tokens, read_tokens, reads_at_input, reads_cost, write_extra, saved}`.
    `input_tokens` and `read_tokens` are the total's `input` and `cache_read`;
    `reads_at_input` is each group's cache reads at that group's own input price,
    `reads_cost` what those reads did cost, `write_extra` each group's cache writes at
    their write price less its input price, and `saved` the first less the other two, so
    it is 0 or less where caching cost more than it saved. `saving` is None when the
    answer holds no cache read, and when a group that holds cache reads or writes is
    unpriced or lacks a price one of those products needs.

    Each `by` groups its requests as the report it reads beside does, so their costs
    agree: `model` as `models`, `day` and `month` as `daily` and `monthly`, `agent` as
    `session`. Rows by model come by cost, highest first, then any wholly unpriced; by day
    or month oldest first; by agent as the `session` report orders its agents."""
    by = _terms_by(by, session)
    query = _query(since, until, harness, project, model, tz, session, folder=folder)
    current = _table(table)
    if by in ("day", "month"):
        span = _span(conn, query)
        offset = offset_sql(query.zone, *span)
        fmt = "%Y-%m-%d" if by == "day" else "%Y-%m"
        groups = grouped(conn, query, {by: f"strftime('{fmt}', r.ts + {offset}, 'unixepoch')"},
                         current, span=span)
        rows, total = _fold(groups, lambda k, e: (k[by],), lambda k, e: {by: k[by]},
                            _TermsRow, _terms_total())
        ordered = [rows[k].done() for k in sorted(rows, key=lambda k: k[0] or "")]
    elif by == "agent":
        groups = grouped(conn, query, {"agent": _AGENT_SQL, "kind": "f.kind",
                                       "title": "f.title"}, current)
        rows, total = _fold(groups, lambda k, e: (k["agent"], k["kind"]),
                            lambda k, e: {"agent": k["agent"], "kind": k["kind"]},
                            _TermsRow, _terms_total())
        ordered = [row.done(times=True) for row in rows.values()]
        starts: dict[str, float] = {}
        for row in ordered:
            if row["first_ts"] is not None:
                starts[row["agent"]] = min(starts.get(row["agent"], row["first_ts"]),
                                           row["first_ts"])
        ordered.sort(key=lambda r: (r["agent"] != "main", r["agent"] not in starts,
                                    starts.get(r["agent"], 0.0), r["agent"],
                                    r["first_ts"] is None, r["first_ts"] or 0.0, r["kind"]))
    else:
        groups = grouped(conn, query, {}, current)
        rows, total = _fold(groups, lambda k, e: (_label(e["model"]),),
                            lambda k, e: {"model": e["model"]}, _TermsRow,
                            _terms_total())
        ordered = [row.done() for row in rows.values()]
        ordered.sort(key=lambda r: (r["cost"] is UNKNOWN,
                                    0.0 if r["cost"] is UNKNOWN else -r["cost"],
                                    _label(r["model"])))
    return _report("terms", query, ordered, total)


# --- the requests ---------------------------------------------------------------------

class _RequestsTotal(_Row):
    """The requests report's total: the sums of its rows, one priced request at a time."""

    keeps_provenance = False


def requests(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
             harness: str | None = None, project: str | None = None,
             model: str | None = None, tz: Any = None, table: Table | None = None,
             folder: str | None = None, session: str | None = None,
             agent: str | None = "main") -> Report:
    """One row per request of one session's agent: `agent` is `main` (the default) or an
    agent id the `session` report gives, and its files are that report's files for that
    agent. Without `session` the report is refused.

    The rows are the agent's claimed requests in range, ordered by their file's first
    request, the file, then the request's place in it. Each row: `n` (1 to N, the
    request axis), `ts`, `model`, `tier` (the price tier its prices came from, or None),
    `turn` and `turn_start`, `context` (its whole prompt, None when unknown), `window`
    (the window the harness reported, else the model table's for the request's model,
    provider and time, else `UNKNOWN`), its tokens by class (`fresh_input`, `cache_read`,
    `cache_write_5m`, `cache_write_1h`, `output`, reasoning inside output), `unknown`
    (the token columns those read that the harness did not report), `synthetic`, `cost` and `split` (its
    five terms as `{class, cost}`). Each request is priced on its own, as a group of one.

    `turn` is None for a request with no turn id, else 1 plus how many times the turn id
    changed over the rows before it that have one; `turn_start` is true where it changes.

    `total` carries the rows' sums, and `marks` (the compactions, each `{after, ts,
    trigger, pre_tokens}`, `after` being how many rows come before it), `turns` (None when
    no row has a turn id), `claimed_elsewhere` (the agent's requests in range that
    another file's copy counts), `copied_marks` (compactions copied from another file,
    not placed) and `marks_known` (false while a file of the agent is still to have its
    compactions read)."""
    from . import store

    if not session:
        raise ReportError("the requests report needs a session id")
    agent = agent or "main"
    query = _query(since, until, harness, project, model, tz, session, agent,
                   folder=folder)
    current = _table(table)
    lo, hi = _span(conn, query)
    edges = _periods(current, lo, hi)
    sizes = _size_bounds(conn, query, current, [0.0 if lo is None else lo, *edges])
    where, args = query.where(claimed=False)
    sql = (
        "SELECT r.won, r.file_id, f.first_ts, r.seq, r.ts, "
        f"{MODEL_SQL}, r.provider, r.speed, r.service_tier,"
        f" {_size_case(sizes)}, {_case('r.ts', edges)},"
        " r.input, r.cache_read, r.cache_write, r.cache_write_1h, r.output,"
        f" {_FRESH_SQL}, {_WRITE_5M_SQL},"
        f" {_unknown_case(_CORE_UNKNOWN)}, {_unknown_case(_CORE_UNKNOWN + _WRITE_UNKNOWN)},"
        f" {_unknown_case(_WRITE_UNKNOWN)},"
        " r.harness_cost, r.unknown, r.synthetic, r.turn_id, r.context_window"
        " FROM requests r JOIN files f ON f.id = r.file_id"
        f" WHERE {where}"
        " ORDER BY f.first_ts IS NULL, f.first_ts, f.id, r.seq, r.key")
    cursor = conn.cursor()
    cursor.row_factory = None
    total = _RequestsTotal({})
    memo: dict[tuple, cost.Prices] = {}
    rows: list[dict[str, Any]] = []
    places: list[tuple] = []
    elsewhere = 0
    turn, last_turn = 0, None
    for (won, file_id, file_first, seq, ts, name, provider, speed, service_tier, size,
         period, input_, cache_read, cache_write, cache_write_1h, output, fresh, write_5m,
         unknown_core, unknown_any, unknown_write, harness_cost, unknown, synthetic,
         turn_id, reported) in cursor.execute(sql, args):
        if not won:
            elsewhere += 1
            continue
        sums = Sums(requests=1, input=input_ or 0, fresh=fresh, cache_read=cache_read or 0,
                    cache_write=cache_write or 0, write_5m=write_5m,
                    cache_write_1h=cache_write_1h or 0, output=output or 0,
                    unknown_core=unknown_core, unknown_any=unknown_any,
                    unknown_write=unknown_write)
        facts = current.lookup(name, provider=provider, at=ts)
        defaults = (current.defaults.get(facts.provider)
                    if isinstance(facts.provider, str) else None)
        paid = None
        if facts.known:
            key = (name, provider, period, size, service_tier, speed)
            paid = memo.get(key)
            if paid is None:
                paid = memo[key] = cost.prices(facts, defaults, size=size,
                                               service_tier=service_tier, speed=speed)
        priced = cost.price(sums, facts, defaults, size=size, service_tier=service_tier,
                            speed=speed, paid=paid)
        total.add(priced, {"sums": sums, "model": name, "facts": facts,
                           "harness_cost": harness_cost, "first_ts": ts, "last_ts": ts})
        start = False
        if turn_id is not None:
            if turn_id != last_turn:
                start = last_turn is not None
                turn += 1
                last_turn = turn_id
        window = _window(reported, facts)
        rows.append({
            "n": len(rows) + 1, "ts": ts, "model": name,
            "tier": None if priced.prices is None else priced.prices.tier,
            "turn": None if turn_id is None else turn, "turn_start": start,
            "context": input_, "window": window, "fresh_input": fresh,
            "cache_read": sums.cache_read, "cache_write_5m": write_5m,
            "cache_write_1h": sums.cache_write_1h, "output": sums.output,
            "unknown": [name for name in store.unknown_columns(unknown)
                        if name in _CORE_UNKNOWN + _WRITE_UNKNOWN],
            "synthetic": bool(synthetic),
            "cost": priced.cost,
            "split": [{"class": term.cls, "cost": term.cost} for term in priced.terms]})
        places.append(((file_first is None, file_first or 0.0, file_id), seq))
    found = _report("requests", query, rows, total)
    files = _agent_files(conn, query)
    placed, copied = _compactions(conn, query, files, places)
    found.total.update(marks=placed, turns=turn or None, claimed_elsewhere=elsewhere,
                       copied_marks=copied,
                       marks_known=all(row["marks_read"] for row in files.values()))
    return found


def _agent_files(conn: sqlite3.Connection, query: _Query) -> dict[int, Any]:
    """The `requests` report's files: the session's files of its agent, under the
    filters a file carries (harness and project), by id."""
    clauses = [f"{_SESSION_SQL} = ?", f"{_AGENT_SQL} = ?"]
    args: list[Any] = [query.session, query.agent]
    if query.harness is not None:
        clauses.append("f.harness = ?")
        args.append(query.harness)
    if query.project is not None:
        clauses.append("(f.project = ? OR (f.project IS NULL AND f.cwd = ?))")
        args.extend([query.project, query.project])
    return {row["id"]: row for row in conn.execute(
        f"SELECT f.id, f.first_ts, f.marks_read FROM files f WHERE {' AND '.join(clauses)}",
        args)}


def _compactions(conn: sqlite3.Connection, query: _Query, files: Mapping[int, Any],
                 places: list[tuple]) -> tuple[list[dict[str, Any]], int]:
    """The agent's compactions placed among its rows, and how many copies were left out.

    `places` holds each row's `(file order, seq)`, in row order. The rules, in order:
    markers of one file with no request of that file between them, claimed or not, are
    one compaction, with the first marker's time and the first trigger and size known;
    a compaction whose nearest requests of its file, before and after where they exist,
    are all counted under another file is a copy (a resumed or forked file carrying the
    original's marker), counted and not placed; one with no request of its file on
    either side is kept. A compaction is kept when its time lies in the range, and one
    with no time when the range is open."""
    import bisect

    if not files:
        return [], 0
    ids = sorted(files)
    found: dict[int, list] = {}
    for n in range(0, len(ids), _CHUNK):
        chunk = ids[n:n + _CHUNK]
        for row in conn.execute(
                "SELECT file_id, seq, ts, trigger, pre_tokens FROM marks"
                f" WHERE kind = 'compaction' AND file_id IN ({','.join('?' * len(chunk))})"
                " ORDER BY file_id, seq", chunk):
            found.setdefault(row[0], []).append(tuple(row)[1:])
    placed: list[dict[str, Any]] = []
    copied = 0
    for file_id, marks in found.items():
        requests = conn.execute("SELECT seq, won FROM requests WHERE file_id = ?"
                                " ORDER BY seq", (file_id,)).fetchall()
        seqs = [row[0] for row in requests]
        merged: list[list] = []
        for mark in marks:
            if merged and (bisect.bisect_right(seqs, mark[0])
                           == bisect.bisect_right(seqs, merged[-1][-1][0])):
                merged[-1].append(mark)
            else:
                merged.append([mark])
        file = files[file_id]
        order = (file["first_ts"] is None, file["first_ts"] or 0.0, file_id)
        for group in merged:
            first, last = group[0][0], group[-1][0]
            before = bisect.bisect_right(seqs, first)
            after = bisect.bisect_right(seqs, last)
            sides = ([requests[before - 1]] if before else []) + (
                [requests[after]] if after < len(requests) else [])
            ts = group[0][1]
            if ts is None:
                kept = query.since is None and query.until is None
            else:
                kept = ((query.since is None or ts >= query.since)
                        and (query.until is None or ts < query.until))
            if sides and not any(side[1] for side in sides):
                copied += kept
                continue
            if not kept:
                continue
            trigger = next((m[2] for m in group if m[2] is not None), None)
            pre_tokens = next((m[3] for m in group if m[3] is not None), None)
            placed.append({"after": bisect.bisect_right(places, (order, first)), "ts": ts,
                           "trigger": trigger, "pre_tokens": pre_tokens})
    placed.sort(key=lambda mark: (mark["after"], mark["ts"] is None, mark["ts"] or 0.0))
    return placed, copied


#: How many held rows `status` names before it only counts them.
HELD_SHOWN = 20
#: How many duplicate files `status` names before it only counts them.
DUPLICATES_SHOWN = 20
#: `status`'s totals over its lanes.
_STATUS_SUMS = ("files", "excluded", "held", "requests", "duplicates")


def status(conn: sqlite3.Connection, *, since: Any = None, until: Any = None,
           harness: str | None = None, project: str | None = None,
           model: str | None = None, tz: Any = None, table: Table | None = None,
           folder: str | None = None) -> Report:
    """The index itself: one row per lane, the lanes in the order a pass runs them.

    A row holds what the last pass learnt of the lane (`state`, `reason`, `listed`,
    `root`, `rank`, `compressed`, `unreadable`, `checked_at`, and for Codex the imports
    file's facts), with what the index holds for it now: `files`, `excluded` (rollouts
    left out as imported), `held` and `held_reasons` (each reason's count), `requests`
    and `duplicates` (copies of a request another file holds, counted once). `total`
    sums those and adds `held_rows` (the first `HELD_SHOWN` held rows with their
    reasons), `duplicate_files` (how many files have every request claimed by another
    file) and `duplicate_rows` (the first `DUPLICATES_SHOWN` of them, each naming the
    file that claims its first request, from `store.duplicates`), `last_pass` (epoch
    seconds, or None) and `index_version`.

    `harness` keeps one harness's lanes. The index has no range, project, folder or
    model to filter, so `status` refuses those; `tz` and `table` are not used.
    """
    import json

    from . import store

    _status_args(since, until, harness, project, model, folder)
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
    duplicate_files = store.duplicates(conn, lanes=lanes) if lanes else []
    last = store.get_meta(conn, store.META_LAST_PASS)
    total = _status_total(rows, held_rows,
                          last_pass=None if last is None else float(last),
                          version=conn.execute("PRAGMA user_version").fetchone()[0])
    total["duplicate_files"] = len(duplicate_files)
    total["duplicate_rows"] = duplicate_files[:DUPLICATES_SHOWN]
    return Report(kind="status", rows=rows, total=total,
                  filters={} if harness is None else {"harness": harness})


def _status_args(since: Any, until: Any, harness: str | None, project: str | None,
                 model: str | None, folder: str | None = None) -> None:
    given = [name for name, value in (("since", since), ("until", until),
                                      ("project", project), ("folder", folder),
                                      ("model", model))
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
    "projects": projects, "session": session, "terms": terms, "requests": requests,
    "status": status,
}

#: The keywords beyond the filters that some reports read: per kind, the ones it takes
#: and, of those, the ones it needs. Every other kind refuses them, so a caller never
#: reads an unfiltered answer as a filtered one.
_KEYWORDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "projects": (("by",), ()),
    "session": (("session",), ("session",)),
    "terms": (("by", "session"), ()),
    "requests": (("session", "agent"), ("session",)),
}


def _kind_args(kind: str, **given: Any) -> dict[str, Any]:
    """The keywords `given` (`session`, `by`, `agent`) as the report `kind` takes them:
    each it needs must be there, and one it does not take is refused."""
    takes, needs = _KEYWORDS.get(kind, ((), ()))
    for name in needs:
        if not given.get(name):
            raise ReportError(f"the {kind} report needs a {name} id")
    for name, value in given.items():
        if value is not None and name not in takes:
            others = [k for k in KINDS if name in _KEYWORDS.get(k, ((), ()))[0]]
            if not others:
                only = "no report does"
            elif len(others) == 1:
                only = f"only the {others[0]} report does"
            else:
                only = f"only the {' and '.join(others)} reports do"
            raise ReportError(f"the {kind} report takes no {name}; {only}")
    if kind == "terms":
        _terms_by(given.get("by"), given.get("session"))
    if kind == "projects":
        _projects_by(given.get("by"))
    return {name: given[name] for name in takes if given.get(name) is not None}


# --- a report from the API ------------------------------------------------------------

def run_pass(*, env: Mapping[str, str] | None = None) -> None:
    """The pass a fresh report runs first (`moxi_usage.index.pass_`), under `env`. It
    raises `moxi.files.LockTimeout` when another pass held the index past the wait."""
    from . import index

    index.pass_(env=env)


def report(kind: str, *, since: Any = None, until: Any = None, harness: str | None = None,
           project: str | None = None, model: str | None = None, tz: Any = None,
           fresh: bool = True, env: Mapping[str, str] | None = None,
           table: Table | None = None, session: str | None = None, by: str | None = None,
           agent: str | None = None, folder: str | None = None) -> Report:
    """One report by `kind` (`KINDS`). With `fresh`, the pass runs first, and when
    another pass holds the index past the wait the report reads the index as it is and
    says so in `Report.index["note"]`. With no index yet, the report is empty and says so.
    `folder` is a folder's whole path: the report keeps the files whose `cwd` it is,
    whatever project key they carry.
    `session` is the session the `session` and `requests` reports read; they need it,
    `terms` may take it, and the others refuse it. `by` is how `terms` groups its rows
    (`TERMS_BY`) and `projects` its own (`PROJECTS_BY`), and the others refuse it; `agent`
    is the agent `requests` reads (`main` when None), and the others refuse it. Raises `ReportError` for an argument it cannot use, before
    any pass runs."""
    from moxi.files import LockTimeout

    if kind not in REPORTS:
        raise ReportError(f"{kind!r} is not a report ({', '.join(KINDS)})")
    _kind_args(kind, session=session, by=by, agent=agent)
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
                model=model, tz=tz, passed=passed, note=note, env=env, table=table,
                session=session, by=by, agent=agent, folder=folder)


def read(kind: str, *, since: Any = None, until: Any = None, harness: str | None = None,
         project: str | None = None, model: str | None = None, tz: Any = None,
         passed: bool = False, note: str | None = None,
         env: Mapping[str, str] | None = None, table: Table | None = None,
         session: str | None = None, by: str | None = None,
         agent: str | None = None, folder: str | None = None) -> Report:
    """One report by `kind` from the index as it is, running no pass: what `report`
    does after its pass. `passed` and `note` go into `Report.index` as given, for a
    caller that ran the pass itself (`moxi usage index`)."""
    from . import store

    fn = REPORTS.get(kind)
    if fn is None:
        raise ReportError(f"{kind!r} is not a report ({', '.join(KINDS)})")
    extra = _kind_args(kind, session=session, by=by, agent=agent)
    path = store.index_path(env)
    if not path.exists():
        empty = _empty(kind, since, until, harness, project, model, tz, session=session,
                       agent=agent, folder=folder)
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
            empty = _empty(kind, since, until, harness, project, model, tz,
                           version=version, session=session, agent=agent, folder=folder)
            return replace(empty, index={
                "passed": passed, "last_pass": None,
                "note": f"the index is at version {version}, before this usage's "
                        f"{store.INDEX_VERSION}; run `moxi usage index` to bring it up"})
        result = fn(conn, since=since, until=until, harness=harness, project=project,
                    model=model, tz=tz, table=table, folder=folder, **extra)
        last = store.get_meta(conn, store.META_LAST_PASS)
    finally:
        conn.close()
    return replace(result, index={"passed": passed,
                                  "last_pass": None if last is None else float(last),
                                  "note": note})


def _empty(kind: str, since: Any, until: Any, harness: str | None, project: str | None,
           model: str | None, tz: Any, *, version: int = 0, session: str | None = None,
           agent: str | None = None, folder: str | None = None) -> Report:
    if kind == "status":
        _status_args(since, until, harness, project, model, folder)
        return Report(kind="status", rows=[],
                      total=_status_total([], [], last_pass=None, version=version),
                      filters={} if harness is None else {"harness": harness})
    if kind == "requests":
        query = _query(since, until, harness, project, model, tz, session, agent or "main",
                       folder=folder)
        found = _report(kind, query, [], _RequestsTotal({}))
        # no index at this version has read a compaction yet
        found.total.update(marks=[], turns=None, claimed_elsewhere=0, copied_marks=0,
                           marks_known=False)
        return found
    query = _query(since, until, harness, project, model, tz, session, folder=folder)
    return _report(kind, query, [], _terms_total() if kind == "terms" else _Row({}))

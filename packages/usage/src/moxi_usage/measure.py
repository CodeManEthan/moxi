"""The restart-point measure: the per-model parameters the warn-at marks are worked out from.

`moxi usage restart-point --measure` and the weekly timer's `moxi-usage-measure` run it.
It reads the token index, never a transcript: the index already holds every request of
every session, so nothing is read twice.

The sample, step by step:

* Files whose last request falls in the last `restart_point.days` (default 21), not left
  out of the totals, and their claimed requests (a request copied into two files counts
  in the one that holds its claim), in order. Synthetic requests are dropped and, in a
  main session's file, sidechain requests.
* Two populations per model: `main`, and `subagent` (a workflow's agent files sample
  with `subagent`, since the warn-at listener marks them as agents). A file's model is
  its first request's, with a bracket suffix and a date suffix taken off.
* For `main`, only sessions a person ran at the keyboard, by default: a Claude session
  whose entrypoint is `cli` (`sdk-cli` is a headless or SDK run), a Codex rollout whose
  source is `cli` or whose originator is in `restart_point.interactive_originators`.
  OpenCode writes no such mark, so its main sessions join only when widened. The rule: a
  session counts as interactive only by a mark the harness writes for a person at the
  keyboard, never by its transport. `restart_point.interactive_only = false` takes every
  main session.
* A file with fewer than 5 requests is skipped, and so is one with a request whose input
  or cache-read count is unknown, since its context or growth cannot be read.

Per population (`thresholds` has the equations they feed):

* S, the median context of the 4th request; P, the median cache read of the 1st; g, the
  mean growth over warm consecutive pairs, a pair being skipped when the second
  request's context is below 60% of the first's (a compaction) or its cache read is
  below 50% of the first's context (an expired cache); `out`, the mean output per
  request; `n`, the file count. A request's context is its whole prompt (the index's
  `input`, cache reads and writes included).
* A population of fewer than 20 files gets no row, so a thin sample never gives a mark.
* Over the whole sample, the 1-hour share of cache writes (counting the requests that
  split their writes by lifetime), and from it `w = 2.0 x share + 1.25 x (1 - share)`.
  With no cache writes in the sample there is no `w` and no row.
* A subagent population with no row of its own carries the same model's `main` row,
  marked `fallback: main`.

The result goes to `STATE/usage/restart-point.json` with `moxi.files.atomic_write`.
`measure()` reads the clock and the config only through its arguments, so the whole
measure runs against fixtures.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from . import thresholds
from .thresholds import MAIN, SUBAGENT

if TYPE_CHECKING:
    import sqlite3

DEFAULT_DAYS = 21
MIN_CALLS = 5
MIN_FILES = 20
SETTLE_CALL = 4                  # S is the context of the 4th request
COMPACTION_RATIO = 0.6
EXPIRY_RATIO = 0.5
DEFAULT_ORIGINATORS = ("codex-tui", "Codex Desktop")

#: The marks a harness writes for a person at the keyboard, per harness: the `origin`
#: (Claude's entrypoint, Codex's source) that means interactive.
INTERACTIVE_ORIGINS = {"claude": "cli", "codex": "cli"}

_DAY = 86400.0

_SAMPLE_SQL = """
SELECT f.id AS file_id, f.harness, f.kind, f.origin, f.originator,
       COALESCE(r.model, r.response_model) AS model, r.input, r.cache_read,
       r.cache_write, r.cache_write_1h, r.output
FROM files f JOIN requests r ON r.file_id = f.id
WHERE f.excluded IS NULL AND f.last_ts >= ? AND r.won = 1 AND r.synthetic = 0
  AND (f.kind != 'main' OR r.sidechain = 0)
ORDER BY f.id, r.seq, r.key
"""


@dataclass(frozen=True)
class Rules:
    """What the sample takes: `days` back, whether `main` takes only interactive
    sessions, and the Codex originators that count as interactive."""

    days: int = DEFAULT_DAYS
    interactive_only: bool = True
    interactive_originators: tuple[str, ...] = DEFAULT_ORIGINATORS

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> Rules:
        return cls(days=int(settings["restart_point.days"]),
                   interactive_only=bool(settings["restart_point.interactive_only"]),
                   interactive_originators=tuple(
                       settings["restart_point.interactive_originators"]))


def interactive(harness: Any, origin: Any, originator: Any,
                originators: Iterable[str] = DEFAULT_ORIGINATORS) -> bool:
    """True when the harness marked the session as run by a person at the keyboard."""
    if origin is not None and INTERACTIVE_ORIGINS.get(harness) == origin:
        return True
    return harness == "codex" and originator is not None and originator in set(originators)


@dataclass
class Sampled:
    """One file of the sample: its harness, kind, origin marks and its requests, in
    order, each `(model, input, cache_read, cache_write, cache_write_1h, output)`."""

    harness: str
    kind: str
    origin: str | None
    originator: str | None
    calls: list[tuple] = field(default_factory=list)


def _files(conn: sqlite3.Connection, cutoff: float) -> list[Sampled]:
    found: dict[int, Sampled] = {}
    for row in conn.execute(_SAMPLE_SQL, (cutoff,)):
        each = found.get(row["file_id"])
        if each is None:
            each = found[row["file_id"]] = Sampled(row["harness"], row["kind"], row["origin"],
                                                 row["originator"])
        each.calls.append((row["model"], row["input"], row["cache_read"],
                           row["cache_write"], row["cache_write_1h"], row["output"]))
    return list(found.values())


def _round(value: float) -> float:
    return round(float(value), 3)


def _row(group: list[list[tuple]]) -> dict[str, Any] | None:
    """One population's parameters, or None when no pair is warm."""
    S = statistics.median(calls[SETTLE_CALL - 1][1] for calls in group)
    P = statistics.median(calls[0][2] for calls in group)
    growth = []
    for calls in group:
        for a, b in zip(calls, calls[1:]):
            ca, cb = a[1], b[1]
            if cb < COMPACTION_RATIO * ca:
                continue
            if b[2] < EXPIRY_RATIO * ca:
                continue
            growth.append(cb - ca)
    if not growth:
        return None
    row: dict[str, Any] = {"n": len(group), "S": _round(S), "P": _round(P),
                           "g": _round(statistics.mean(growth))}
    outputs = [c[5] for calls in group for c in calls if c[5] is not None]
    if outputs:
        row["out"] = _round(statistics.mean(outputs))
    return row


def measure(conn: sqlite3.Connection, *, now: float, rules: Rules = Rules()) -> dict:
    """The measure's document over the index on `conn` at `now` (epoch seconds)."""
    return document(_files(conn, now - rules.days * _DAY), now=now, rules=rules)


def document(files: Iterable[Sampled], *, now: float, rules: Rules = Rules()) -> dict:
    """The measure's document over the sample's files."""
    skipped = {"not_interactive": 0, "short": 0, "unknown_counts": 0, "no_model": 0}
    groups: dict[tuple[str, str], list[list[tuple]]] = {}
    kept: list[list[tuple]] = []
    for each in files:
        population = thresholds.population_of(each.kind)
        if (population == MAIN and rules.interactive_only
                and not interactive(each.harness, each.origin, each.originator,
                                    rules.interactive_originators)):
            skipped["not_interactive"] += 1
            continue
        if len(each.calls) < MIN_CALLS:
            skipped["short"] += 1
            continue
        if any(c[1] is None or c[2] is None for c in each.calls):
            skipped["unknown_counts"] += 1
            continue
        model = thresholds.base_model(each.calls[0][0])
        if model is None:
            skipped["no_model"] += 1
            continue
        groups.setdefault((model, population), []).append(each.calls)
        kept.append(each.calls)

    split = [(c[3], c[4]) for calls in kept for c in calls
             if c[3] is not None and c[4] is not None]
    write_all = sum(total for total, _ in split)
    write_1h = sum(min(hour, total) for total, hour in split)
    share = write_1h / write_all if write_all else None
    w = thresholds.write_multiplier(share) if share is not None else None

    models: dict[str, dict[str, Any]] = {}
    thin: dict[str, dict[str, int]] = {}
    for (model, population), group in sorted(groups.items()):
        if len(group) < MIN_FILES:
            thin.setdefault(model, {})[population] = len(group)
            continue
        if w is None:
            continue
        row = _row(group)
        if row is not None:
            models.setdefault(model, {})[population] = row
    for rows in models.values():
        if SUBAGENT not in rows and MAIN in rows:
            rows[SUBAGENT] = {**rows[MAIN], "fallback": thresholds.FALLBACK_MAIN}

    population_files = {MAIN: 0, SUBAGENT: 0}
    for (_, population), group in groups.items():
        population_files[population] += len(group)
    return {
        "schema": thresholds.PARAMS_SCHEMA,
        "measured_at": datetime.fromtimestamp(now, timezone.utc).isoformat(
            timespec="seconds"),
        "days": rules.days,
        "interactive_only": rules.interactive_only,
        "interactive_originators": list(rules.interactive_originators),
        "sample": {"files": len(kept), "requests": sum(len(calls) for calls in kept),
                   "main_files": population_files[MAIN],
                   "subagent_files": population_files[SUBAGENT],
                   "skipped": skipped},
        "write_share_1h": None if share is None else _round(share),
        "w": None if w is None else _round(w),
        "models": models,
        "thin": thin,
    }


def measured_epoch(doc: Mapping[str, Any]) -> float | None:
    """When a measure's document was made, in epoch seconds, or None."""
    value = doc.get("measured_at")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


# --- reading it back -------------------------------------------------------------------

@dataclass(frozen=True)
class RestartPoint:
    """The measure in effect and the marks it gives.

    `doc` is the measure's document, None when there is none or it cannot be read, and
    then `problem` says which. `rows` holds one `(model, population, row, marks)` per
    measured population, `row` the measured parameters and `marks` each mark's
    `thresholds.Threshold`, a fallback subagent row among them."""

    path: str
    doc: dict | None = None
    problem: str | None = None
    rows: tuple[tuple[str, str, dict, dict], ...] = ()

    @property
    def measured_at(self) -> float | None:
        return None if self.doc is None else measured_epoch(self.doc)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.path, "measured": self.doc is not None}
        if self.problem is not None:
            out["problem"] = self.problem
        if self.doc is not None:
            for name in ("measured_at", "days", "interactive_only", "sample",
                         "write_share_1h", "w", "thin"):
                out[name] = self.doc.get(name)
        out["rows"] = [{"model": model, "population": population, **row,
                        "marks": {mark: found.as_dict() for mark, found in marks.items()}}
                       for model, population, row, marks in self.rows]
        return out


def restart_point(*, env: Mapping[str, str] | None = None, table: Any = None) -> RestartPoint:
    """The measure in effect (`STATE/usage/restart-point.json`) and every mark it gives,
    per model and population, with the model table in effect (or `table`). Raises
    `moxi.config.ConfigError` when usage's config cannot be read."""
    path = thresholds.params_path(env)
    try:
        doc = thresholds.load_params(path)
    except thresholds.ParamsError as exc:
        return RestartPoint(str(path), problem=str(exc))
    if doc is None:
        return RestartPoint(str(path), problem="the restart point has not been measured; "
                                               f"{thresholds.MEASURE_FIX}")
    if table is None:
        from .models import table as current

        table = current(env)
    rows = []
    models = doc.get("models") if isinstance(doc.get("models"), Mapping) else {}
    for model in sorted(models):
        populations = models[model] if isinstance(models[model], Mapping) else {}
        for population in thresholds.POPULATIONS:
            row = populations.get(population)
            if not isinstance(row, Mapping):
                continue
            marks = thresholds.all_marks(model, population, doc=doc, table=table)
            rows.append((model, population, dict(row), marks))
    return RestartPoint(str(path), doc, None, tuple(rows))


# --- one run ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Run:
    """One run of the measure: the document it wrote, where, and a note when the pass
    could not run first."""

    doc: dict
    path: str
    note: str | None = None

    def line(self) -> str:
        sample = self.doc["sample"]
        rows = sum(1 for rows in self.doc["models"].values() for row in rows.values()
                   if "fallback" not in row)
        line = (f"restart point measured over {sample['files']} files and "
                f"{sample['requests']} requests of the last {self.doc['days']} days: "
                f"{rows} population row{'s' if rows != 1 else ''} for "
                f"{len(self.doc['models'])} model{'s' if len(self.doc['models']) != 1 else ''}")
        if self.doc["w"] is None and sample["files"]:
            line += "; the sample holds no cache writes, so no row"
        return line


class MeasureError(Exception):
    """The measure could not run; nothing was written."""


def run(*, now: float | None = None, fresh: bool = True,
        env: Mapping[str, str] | None = None) -> Run:
    """Run the index's pass (unless `fresh` is false), measure over the index and write
    `STATE/usage/restart-point.json`. When another pass holds the index past the wait,
    the measure reads the index as it is and says so in `Run.note`. Raises
    `moxi.config.ConfigError` for a config it cannot read, `moxi.store.StoreTooNew` for an
    index newer than this code, and `MeasureError`, writing nothing, when there is no
    index at this code's version to measure (so a measure already written stays)."""
    import time

    import moxi.files
    import moxi.store

    from . import reports, settings, store

    rules = Rules.from_settings(settings.current(env))
    when = time.time() if now is None else now
    note = None
    if fresh:
        try:
            reports.run_pass(env=env)
        except moxi.files.LockTimeout as exc:
            note = (f"another pass held the index ({exc}); the measure read the index as "
                    "it is")
    path = store.index_path(env)
    if not path.exists():
        raise MeasureError("there is no token index to measure; run `moxi usage index` "
                           "to build it")
    conn = store.connect(env=env, create=False)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > store.INDEX_VERSION:
            raise moxi.store.StoreTooNew(str(path), version, store.INDEX_VERSION)
        if version < store.INDEX_VERSION:
            raise MeasureError(f"the token index is at version {version}, before this "
                               f"usage's {store.INDEX_VERSION}; run `moxi usage index` "
                               "to bring it up")
        doc = measure(conn, now=when, rules=rules)
    finally:
        conn.close()
    out = thresholds.params_path(env)
    moxi.files.atomic_write(out, json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return Run(doc, str(out), note)


def main(argv: Sequence[str] | None = None) -> int:
    """`moxi-usage-measure`, the weekly timer's command: one run, one line."""
    import logging

    import moxi.store
    from moxi import exit as codes
    from moxi.config import ConfigError

    parser = argparse.ArgumentParser(
        prog="moxi-usage-measure",
        description="Measure the restart point from the token index's last "
                    "restart_point.days of sessions and write it for the warn-at marks.")
    parser.parse_args(argv)
    try:
        done = run()
    except ConfigError as exc:
        print(f"moxi-usage-measure: {exc}", file=sys.stderr)
        return exc.exit_code
    except moxi.store.StoreTooNew as exc:
        print(f"moxi-usage-measure: {exc}", file=sys.stderr)
        return codes.REFUSED
    except MeasureError as exc:
        print(f"moxi-usage-measure: {exc}", file=sys.stderr)
        return codes.ERROR
    print(done.line())
    if done.note:
        logging.getLogger(__name__).warning("%s", done.note)
        print(f"note: {done.note}")
    return codes.OK


__all__ = ["measure", "Sampled", "RestartPoint", "restart_point", "run", "main", "Run", "Rules", "interactive", "measured_epoch",
           "document", "MeasureError", "MIN_CALLS", "MIN_FILES", "DEFAULT_DAYS"]


if __name__ == "__main__":
    sys.exit(main())

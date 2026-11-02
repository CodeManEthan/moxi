"""usage's doctor checks: `rows()`, which the announcement's `DOCTOR` names.

The core's own checks cover each declared store's existence, mode and size. These look
at what usage's answers rest on:

* `models.snapshot`: `warn` when the model table in effect (the snapshot `moxi usage
  prices update` wrote, else the shipped table) is older than `prices.max_age_days`, or
  when a snapshot is on disk but cannot be read, so the shipped table rules.
* `models.unpriced`: `warn` naming each model the token index saw in the last 30 days
  that the table cannot price or gives no window for.
* `index`: `ok` with the last pass and the index's counts; `warn` when an archive lane was
  offline on the last pass, when rows are held (naming each lane's reasons), or when the
  last pass is older than 7 days. No index yet is `ok`: the first pass builds it.
* `codex.imports`: `ok` when Codex's imports file is missing (nothing was imported);
  `warn` when the last pass could not read it, so a rollout imported from elsewhere may
  be counted twice, or when rollouts carrying only the desktop app's `originator` mark
  were counted, naming how many.
* `restart_point`: `warn` naming each model and population the index saw in the last 30
  days that the restart-point measure has no row for (so it has no warn-at mark), when
  the measure is older than 14 days, or when its file cannot be read.
* `marks`: deletes the warn-at mark's fired-mark files older than `marks.max_age_days`
  and the worker ceiling's agent markers older than `ceiling.max_age_days`, and says how
  many it deleted and kept; `warn` when one could not be deleted, or when the worker
  ceiling runs (by `ceiling.on`, or by turns' `guards.on` as turns' guard loader reports
  it) while `ceiling.warn_tokens` or `ceiling.hard_tokens` is 0, so it allows every call.
* `turns`: `off` when turns is not installed, saying what needs it; else `ok`, naming
  the events usage registers with turns and what each is for, and the worker ceiling's
  guard.

Every other check only reads: the index is opened as it is, and no pass runs.
"""

from __future__ import annotations

import calendar
import datetime
import time
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from moxi.config import ConfigError
from moxi.doctor import Row

if TYPE_CHECKING:
    import sqlite3

MODULE = "usage"

#: How far back `models.unpriced` looks for models the index saw, in days.
SEEN_DAYS = 30
#: An index whose last pass is older than this many days is stale.
INDEX_STALE_DAYS = 7
#: The most held reasons the `index` row names per lane.
REASONS_SHOWN = 3
#: The most models `models.unpriced` names.
MODELS_SHOWN = 12
#: A restart-point measure older than this many days is stale.
MEASURE_STALE_DAYS = 14

_PRICES_UPDATE = "moxi usage prices update"
_INDEX = "moxi usage index"
_CONFIG = "moxi config usage"
_MEASURE = "moxi usage restart-point --measure"
_DAY = 86400.0


def rows(*, now: float | None = None,
         env: Mapping[str, str] | None = None) -> Iterator[Row]:
    """Every check's row, in the order the module docstring gives. `now` is epoch
    seconds (the clock when None); `env` the environment the roots are read from."""
    when = time.time() if now is None else now
    found = open_index(env)
    try:
        yield from model_rows(found, now=when, env=env)
        yield index_row(found, now=when)
        yield imports_row(found)
        yield restart_point_row(found, now=when, env=env)
    finally:
        found.close()
    yield marks_row(now=when, env=env)
    yield turns_row()


# --- the model table --------------------------------------------------------------------

def model_rows(found: IndexOpen, *, now: float,
               env: Mapping[str, str] | None = None) -> list[Row]:
    """`models.snapshot` and `models.unpriced`, the second over the index in `found`."""
    from . import models, settings

    try:
        max_age = settings.current(env)["prices.max_age_days"]
        table = models.table(env)
    except ConfigError as exc:
        why = f"usage's config cannot be read, so the model table is unknown: {exc}"
        return [Row(MODULE, "models.snapshot", "warn", why, _CONFIG),
                Row(MODULE, "models.unpriced", "warn", why, _CONFIG)]
    return [snapshot_row(table, max_age, now=now), unpriced_row(table, found, now=now)]


def snapshot_row(table: Any, max_age_days: int, *, now: float) -> Row:
    """`warn` when the table in effect is older than `max_age_days`, or a snapshot on
    disk was skipped."""
    from .models import SNAPSHOT_LAYER

    if table.problems:
        return Row(MODULE, "models.snapshot", "warn", "; ".join(table.problems),
                   _PRICES_UPDATE)
    layer = ("the snapshot `moxi usage prices update` wrote"
             if SNAPSHOT_LAYER in table.dates else "the shipped table")
    date = table.effective_date
    start = _date_epoch(date)
    if start is None:
        return Row(MODULE, "models.snapshot", "warn",
                   f"the model table in effect, {layer}, carries no date", _PRICES_UPDATE)
    days = int(max(now - start, 0) // _DAY)
    said = f"the model table in effect is {layer}, of {date}, {_days(days)} old"
    if days > max_age_days:
        return Row(MODULE, "models.snapshot", "warn",
                   f"{said}, past prices.max_age_days ({max_age_days}); prices may have "
                   "changed since", _PRICES_UPDATE)
    return Row(MODULE, "models.snapshot", "ok",
               f"{said} (a warning past {max_age_days} days)")


def unpriced_row(table: Any, found: IndexOpen, *, now: float) -> Row:
    """`warn` naming each model seen in the last `SEEN_DAYS` days that has no price or
    no window."""
    from moxi_sessions.api import UNKNOWN

    if found.conn is None:
        if found.problem is not None:
            return Row(MODULE, "models.unpriced", "warn",
                       f"the models in use cannot be read: {found.problem}", found.fix)
        return Row(MODULE, "models.unpriced", "ok",
                   "no token index yet, so no model has been seen")
    import sqlite3

    try:
        seen = found.conn.execute(
            "SELECT COALESCE(model, response_model) AS name, provider, max(ts) AS last"
            " FROM requests WHERE won = 1 AND synthetic = 0 AND ts >= ?"
            " AND COALESCE(model, response_model) IS NOT NULL"
            " GROUP BY name, provider ORDER BY name, provider",
            (now - SEEN_DAYS * _DAY,)).fetchall()
    except sqlite3.Error as exc:
        return Row(MODULE, "models.unpriced", "warn",
                   f"the models in use cannot be read from the token index: {exc}", _INDEX)
    lacking: dict[str, list[str]] = {}
    names = set()
    for name, provider, last in seen:
        names.add(name)
        facts = table.lookup(name, provider=provider, at=last)
        missing = [what for what, gone in (("no price", not facts.priced),
                                           ("no window", facts.window is UNKNOWN)) if gone]
        if missing and name not in lacking:
            lacking[name] = missing
    if not lacking:
        return Row(MODULE, "models.unpriced", "ok",
                   f"every model seen in the last {SEEN_DAYS} days ({len(names)}) has a "
                   "price and a window")
    shown = [f"{name} ({', '.join(why)})" for name, why in sorted(lacking.items())]
    more = len(shown) - MODELS_SHOWN
    text = ", ".join(shown[:MODELS_SHOWN]) + (f" and {more} more" if more > 0 else "")
    return Row(MODULE, "models.unpriced", "warn",
               f"{len(lacking)} of the models seen in the last {SEEN_DAYS} days lack a "
               f"fact: {text}; their tokens are counted as unpriced. A [[model]] row in "
               "usage's config gives one", _PRICES_UPDATE)


# --- the token index --------------------------------------------------------------------

class IndexOpen:
    """The token index opened as it is, for reading: `conn` when it opened at this
    code's schema version, else None with `problem` (and the `fix`) when it is there but
    cannot be read, or both None when there is no index yet."""

    def __init__(self, conn: sqlite3.Connection | None = None, problem: str | None = None,
                 fix: str | None = None):
        self.conn, self.problem, self.fix = conn, problem, fix
        #: The index's `status` report and why it could not be read, once worked out.
        self.status: tuple[Any, str | None] | None = None

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None


def open_index(env: Mapping[str, str] | None = None) -> IndexOpen:
    """The token index as `IndexOpen`. Never creates or migrates it."""
    import sqlite3

    import moxi.store

    from . import store

    path = store.index_path(env)
    if not path.exists():
        return IndexOpen()
    try:
        conn = store.connect(env=env, create=False)
    except (moxi.store.StoreError, sqlite3.Error, OSError) as exc:
        return IndexOpen(problem=f"the token index {path} cannot be opened: {exc}")
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error as exc:
        conn.close()
        return IndexOpen(problem=f"the token index {path} cannot be read: {exc}")
    if version > store.INDEX_VERSION:
        conn.close()
        return IndexOpen(problem=f"the token index {path} is at schema version {version}, "
                                 f"newer than this usage's {store.INDEX_VERSION}",
                         fix="upgrade moxi-usage")
    if version < store.INDEX_VERSION:
        conn.close()
        return IndexOpen(problem=f"the token index {path} is at schema version {version}, "
                                 f"before this usage's {store.INDEX_VERSION}",
                         fix=_INDEX)
    return IndexOpen(conn)


def _status(found: IndexOpen) -> tuple[Any, str | None]:
    """The index's `status` report, or None and why it could not be read. Worked out
    once per open index, so the rows that read it share one."""
    import sqlite3

    from . import reports

    if found.status is None:
        try:
            found.status = (reports.status(found.conn), None)
        except sqlite3.Error as exc:
            found.status = (None, f"the token index cannot be read: {exc}")
    return found.status


def index_row(found: IndexOpen, *, now: float) -> Row:
    """`ok` with the last pass and the counts; `warn` for an offline archive lane, held
    rows or a stale index."""
    if found.conn is None:
        if found.problem is not None:
            return Row(MODULE, "index", "warn", found.problem, found.fix)
        return Row(MODULE, "index", "ok",
                   "no token index yet: `moxi usage index` builds it, and each report "
                   "builds it first")
    status, problem = _status(found)
    if status is None:
        return Row(MODULE, "index", "warn", problem, _INDEX)
    total = status.total
    problems = []
    for lane in status.rows:
        if lane["lane"].startswith("archive:") and lane.get("state") == "offline":
            why = lane.get("reason")
            problems.append(f"{lane['lane']} was offline on the last pass"
                            + (f" ({why})" if why else "")
                            + f"; its {lane['files']} files are kept")
    for lane in status.rows:
        if lane["held"]:
            reasons = sorted(lane["held_reasons"].items(), key=lambda kv: (-kv[1], kv[0]))
            said = "; ".join(f"{why} ({count})" for why, count in reasons[:REASONS_SHOWN])
            more = len(reasons) - REASONS_SHOWN
            if more > 0:
                said += f"; {more} more reason{'s' if more != 1 else ''}"
            problems.append(f"{lane['lane']} holds {lane['held']} "
                            f"row{'s' if lane['held'] != 1 else ''}: {said}")
    last = total.get("last_pass")
    if last is None:
        problems.append("no pass has finished")
        when = "no pass has finished"
    else:
        age = max(now - last, 0)
        when = f"last pass {_utc(last)} ({_ago(age)})"
        if age > INDEX_STALE_DAYS * _DAY:
            problems.append(f"the last pass is {_ago(age)}, past {INDEX_STALE_DAYS} days")
    counts = (f"{_n(total['requests'], 'request')} in {_n(total['files'], 'file')} over "
              f"{_n(total['lanes'], 'lane')}, {_n(total['duplicates'], 'copy', 'copies')} "
              f"counted once, {_n(total['excluded'], 'rollout')} left out as imported")
    if problems:
        return Row(MODULE, "index", "warn", "; ".join(problems) + f". {when}: {counts}",
                   _INDEX)
    return Row(MODULE, "index", "ok", f"{when}: {counts}")


def imports_row(found: IndexOpen) -> Row:
    """What the last pass learnt of Codex's imports file and import marks."""
    if found.conn is None:
        if found.problem is not None:
            return Row(MODULE, "codex.imports", "warn", found.problem, found.fix)
        return Row(MODULE, "codex.imports", "ok", "no pass has read Codex's rollouts yet")
    status, problem = _status(found)
    if status is None:
        return Row(MODULE, "codex.imports", "warn", problem, _INDEX)
    lane = next((row for row in status.rows if row["lane"] == "codex"), None)
    if lane is None or lane.get("state") == "absent":
        return Row(MODULE, "codex.imports", "off", "no Codex rollouts here")
    reason = lane.get("imports_reason")
    path = lane.get("imports_path")
    where = f" ({path})" if path else ""
    imported = int(lane.get("imported") or 0)
    alone = int(lane.get("originator_only") or 0)
    problems = []
    if isinstance(reason, str) and reason.startswith("imports file unreadable"):
        problems.append(f"the last pass could not read Codex's imports file{where}: "
                        f"{reason}; a rollout imported from elsewhere may be counted twice")
    if alone:
        problems.append(f"the last pass counted {alone} rollout{'s' if alone != 1 else ''} "
                        "whose only import mark is the desktop app's originator; any the "
                        "desktop app imported from elsewhere is counted twice")
    if problems:
        return Row(MODULE, "codex.imports", "warn", "; ".join(problems))
    if reason == "imports file not found":
        return Row(MODULE, "codex.imports", "ok",
                   f"Codex has no imports file{where}: nothing was imported")
    return Row(MODULE, "codex.imports", "ok",
               f"{imported} rollout{'s' if imported != 1 else ''} left out as imported, "
               "by their turn ids or the imports file")


# --- the restart point ------------------------------------------------------------------

_SEEN_SQL = """
SELECT DISTINCT f.kind, COALESCE(r.model, r.response_model) AS model
FROM requests r JOIN files f ON f.id = r.file_id
WHERE r.won = 1 AND r.synthetic = 0 AND r.ts >= ? AND f.excluded IS NULL
"""


def seen_populations(found: IndexOpen, *, now: float) -> list[tuple[str, str]]:
    """Each `(base model, population)` the index saw in the last `SEEN_DAYS` days."""
    import sqlite3

    from . import thresholds

    if found.conn is None:
        return []
    seen: set[tuple[str, str]] = set()
    try:
        for row in found.conn.execute(_SEEN_SQL, (now - SEEN_DAYS * _DAY,)):
            model = thresholds.base_model(row["model"])
            if model is not None:
                seen.add((model, thresholds.population_of(row["kind"])))
    except sqlite3.Error:
        return []                        # the index row says the index cannot be read
    return sorted(seen)


def restart_point_row(found: IndexOpen, *, now: float,
                      env: Mapping[str, str] | None = None) -> Row:
    """`warn` naming the models seen in the last `SEEN_DAYS` days with no measured row,
    and when the measure is older than `MEASURE_STALE_DAYS` days or cannot be read."""
    from . import measure, thresholds

    path = thresholds.params_path(env)
    try:
        doc = thresholds.load_params(path)
    except thresholds.ParamsError as exc:
        return Row(MODULE, "restart_point", "warn",
                   f"{exc}; no warn-at mark is available until it is measured again",
                   _MEASURE)
    seen = seen_populations(found, now=now)
    if doc is None:
        if not seen:
            return Row(MODULE, "restart_point", "ok",
                       f"not measured yet, and the token index saw no model in the last "
                       f"{_days(SEEN_DAYS)}")
        return Row(MODULE, "restart_point", "warn",
                   "the restart point has not been measured, so no warn-at mark is "
                   f"available for {_named(seen)}", _MEASURE)
    problems = []
    when = measure.measured_epoch(doc)
    if when is None:
        problems.append("the measure carries no date")
        said = "measured at an unknown time"
    else:
        days = int(max(now - when, 0) // _DAY)
        said = f"measured {_utc(when)}, {_days(days)} ago"
        if days > MEASURE_STALE_DAYS:
            problems.append(f"{said}, past {MEASURE_STALE_DAYS} days")
    models = doc.get("models") if isinstance(doc.get("models"), Mapping) else {}
    missing = [(model, population) for model, population in seen
               if not isinstance((models.get(model) or {}).get(population), Mapping)]
    if missing:
        problems.append(f"no measured row, so no warn-at mark, for {_named(missing)}")
    if problems:
        return Row(MODULE, "restart_point", "warn", "; ".join(problems), _MEASURE)
    rows = sum(1 for populations in models.values() for row in populations.values()
               if isinstance(row, Mapping) and "fallback" not in row)
    return Row(MODULE, "restart_point", "ok",
               f"{said}: {_n(rows, 'population row')} for {_n(len(models), 'model')}; "
               f"every model seen in the last {_days(SEEN_DAYS)} has one")


def _named(pairs: list[tuple[str, str]]) -> str:
    shown = ", ".join(f"{model} ({population})" for model, population in pairs[:MODELS_SHOWN])
    more = len(pairs) - MODELS_SHOWN
    return shown + (f" and {more} more" if more > 0 else "")


# --- marks ------------------------------------------------------------------------------

def marks_row(*, now: float, env: Mapping[str, str] | None = None) -> Row:
    """Sweep the fired-mark files and the ceiling's markers past their ages, and count."""
    from . import marks
    from .settings import current

    try:
        settings = current(env)
        marks_days = float(settings["marks.max_age_days"])
        ceiling_days = float(settings["ceiling.max_age_days"])
    except ConfigError as exc:
        return Row(MODULE, "marks", "warn", f"usage's config cannot be read, so nothing "
                   f"was swept: {exc}", _CONFIG)
    swept = marks.sweep(now=now, marks_days=marks_days, ceiling_days=ceiling_days, env=env)
    detail = (f"{_n(swept.kept, 'session')} with fired marks kept; deleted "
              f"{_n(swept.marks, 'fired-mark file')} older than {_days(int(marks_days))} "
              f"and the ceiling markers of {_n(swept.ceiling, 'agent')} older than "
              f"{_days(int(ceiling_days))}")
    if swept.problems:
        shown = "; ".join(swept.problems[:REASONS_SHOWN])
        return Row(MODULE, "marks", "warn", f"{detail}; could not delete: {shown}")
    unset = ceiling_unset(settings, env=env)
    if unset is not None:
        return Row(MODULE, "marks", "warn", f"{detail}; {unset}", CEILING_FIX)
    return Row(MODULE, "marks", "ok", detail)


#: The fix the ceiling's warning names.
CEILING_FIX = ("moxi config set usage ceiling.warn_tokens <tokens> and "
               "moxi config set usage ceiling.hard_tokens <tokens>")


def ceiling_unset(settings: Any, *, env: Mapping[str, str] | None = None) -> str | None:
    """Why the worker ceiling runs with nothing to do, or None: it is on (by
    `ceiling.on`, or by turns' guard loader reporting `usage.ceiling` on) while
    `ceiling.warn_tokens` or `ceiling.hard_tokens` is 0, so it allows every call."""
    from .ceiling import GUARD_ID, SWITCH

    warn, hard = settings["ceiling.warn_tokens"], settings["ceiling.hard_tokens"]
    if warn > 0 and hard > 0:
        return None
    by = SWITCH if settings[SWITCH] else ceiling_on_in_turns(env)
    if by is None:
        return None
    zero = " and ".join(name for name, value in (("ceiling.warn_tokens", warn),
                                                 ("ceiling.hard_tokens", hard))
                        if value <= 0)
    return (f"the worker ceiling is on ({by}) but {zero} is 0, so it allows every "
            f"call; set both above 0, or turn {GUARD_ID} off")


def ceiling_on_in_turns(env: Mapping[str, str] | None = None) -> str | None:
    """What turned `usage.ceiling` on, as turns' guard loader reports it (`guards.on`,
    `ceiling.on` or both), or None when it is off, turns is absent or the loader can't
    be read.

    This reaches past turns' API: turns' public names (`moxi_turns`) offer no read of
    which guards are on, so it imports turns' guard loader, `moxi_turns.guards`, and
    reads `load(env=).get(id).by`. A failure there leaves `ceiling.on` as the only
    answer; the ceiling's tests run the real loader, so a rename fails them."""
    import importlib

    import moxi.modules

    from .ceiling import GUARD_ID, SWITCH

    if not moxi.modules.available("turns"):
        return None
    try:
        loader = importlib.import_module("moxi_turns.guards")
        found = loader.load(env=env).get(GUARD_ID)
    except Exception:  # noqa: BLE001 - a loader that can't be read leaves ceiling.on alone
        return None
    if found is None:
        return None
    said = {"list": "turns' guards.on", "switch": SWITCH}
    return " and ".join(said.get(b, b) for b in found.by)


# --- turns ------------------------------------------------------------------------------

#: What each event usage registers with turns is for.
TURNS_EVENTS = {
    "after-tool": "the warn-at mark, with its floor",
    "gone": "forgetting a session's fired marks",
    "usage": "Claude limits",
    "codex_limits": "Codex limits from the app-server",
}


def turns_row() -> Row:
    """`off` when turns is not installed; else `ok`, naming what usage registers."""
    import moxi.modules

    if not moxi.modules.available("turns"):
        from .module import OPTIONAL

        return Row(MODULE, "turns", "off",
                   f"turns is not installed: {OPTIONAL['turns']} need turns")
    from .ceiling import GUARD_ID
    from .listeners import LISTENERS

    said = []
    for row in LISTENERS:
        for event in row[0]:
            said.append(f"{event} ({TURNS_EVENTS.get(event, row[1])})")
    return Row(MODULE, "turns", "ok", "turns is installed; usage registers "
               + ", ".join(said) + f", and the guard {GUARD_ID} (the worker ceiling, "
               "off until turned on)")


# --- text -------------------------------------------------------------------------------

def _date_epoch(date: Any) -> float | None:
    """A `YYYY-MM-DD` date's UTC midnight in epoch seconds, or None."""
    if not isinstance(date, str):
        return None
    try:
        day = datetime.date.fromisoformat(date)
    except ValueError:
        return None
    return float(calendar.timegm(day.timetuple()))


def _n(count: int, word: str, words: str | None = None) -> str:
    return f"{count} {word if count == 1 else (words or word + 's')}"


def _days(days: int) -> str:
    return _n(days, "day")


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < _DAY:
        return f"{int(seconds // 3600)}h ago"
    return f"{_days(int(seconds // _DAY))} ago"


def _utc(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(epoch))


__all__ = ["rows", "model_rows", "snapshot_row", "unpriced_row", "index_row",
           "imports_row", "restart_point_row", "seen_populations", "marks_row", "ceiling_unset",
           "ceiling_on_in_turns", "turns_row", "open_index", "IndexOpen", "MODULE"]

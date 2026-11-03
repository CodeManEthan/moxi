"""The handlers of usage's page, the pass it starts, and the poller that feeds its stream.

**Reads.** Each read is a plain `def`, run on one of the page server's worker threads,
and thin over `moxi_usage.api`: it calls `limits()` or `report()`, takes `as_dict()`, and
cuts the answer to its route's declared shape. It runs no SQL of its own. Every report
is read with `fresh=False`, so a read never runs a pass and never waits on the index's
lock; its `index.note` says it read the index as it is. Where the API's answer holds a
map with open keys, the cut turns it into a list of `{name, value}` rows (a report's
`filters`, a lane's `held_reasons`, the dollar figures `limits.dollars` finds in a
window's `extra`) or leaves it out (a price's `source`, any lane fact not declared). An
argument the API refuses (`ReportError`), an index written by a newer usage
(`StoreTooNew`) and a config usage can't read are a 422 with the error's text.

**The pass.** `POST /api/pass` starts `report("status", fresh=True)`, which runs the pass,
through `moxi.aio.offload`, holds `busy("pass")` until it ends, and answers at once with
`{started, running}`. While a pass it started runs, a second call starts nothing. With no
index, or an index at another version, it is refused (422) with the API's own note, which
names `moxi usage index`: a first build or a migration never runs in the page server. A
pass over more than `index.POOL_ABOVE` new sources reads them on the pass's process pool,
as it does from the command line. The `index` topic is published when a pass starts and
when it ends: `{running, last_pass, note}`, where `note` is the report's note or what
stopped the pass.

**The stream.** One stream route carries two topics, `limits` (`/api/limits`'s value
without `now`) and `index`. The `limits` topic is fed by one poller, an asyncio task
started by the first call to the stream route and run for the page server's life; loopback
stops an idle server once nothing is open, and a Codex pull stays bounded by
`limits.codex_pull_after`. It gathers at once and then every `PERIOD` seconds, and its
`Snapshot` publishes only when the windows changed; a window's age is left to the page,
worked out from `observed_at`, so time passing alone changes nothing. The poller survives
every exception: a failed gather is logged once per class and the next period gathers
again.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import math
import threading
from collections.abc import Callable, Mapping
from typing import Any

import moxi.config
import moxi.store
from moxi import aio, modules

from . import api, store
from .limits import dollars
from .page import TOP_DEFAULT, WINDOW

_loopback = modules.load("loopback", interface=1)
if not _loopback.ok:
    raise ImportError(f"usage's page needs the loopback module: {_loopback.reason}")
lb = _loopback.api

#: Seconds between the limits poller's gathers.
PERIOD = 60.0
TOPICS = ("limits", "index")
#: The days `/api/cost` covers: today and the six before it.
COST_DAYS = 7

log = logging.getLogger("moxi.usage.page")

_logged: set[type] = set()
_logged_lock = threading.Lock()
#: The errors a read or the pass answers with their own text: each says what to fix.
_OWN_WORDS = (api.ReportError, moxi.store.StoreError, moxi.config.ConfigError)


def _log_once(err: BaseException, what: str) -> None:
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
    if first:
        log.error("%s (%s)", what, type(err).__name__, exc_info=err)


# --- values cut to their declared types ----------------------------------------------------

def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _words(value: Any) -> list[str]:
    return [str(item) for item in value or ()]


def _named(found: Mapping[str, Any], cut: Callable[[Any], Any]) -> list[dict[str, Any]]:
    return [{"name": str(name), "value": cut(value)} for name, value in found.items()]


# --- reports -------------------------------------------------------------------------------

_SUM_NAMES = ("requests", "input", "fresh_input", "cache_read", "cache_write",
              "cache_write_1h", "output", "tokens", "unpriced_tokens", "unknown_rows")


def _sums(row: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {name: _int(row.get(name)) or 0 for name in _SUM_NAMES}
    out["cost"] = _num(row.get("cost"))
    out["complete"] = bool(row.get("complete", True))
    out["harness_cost"] = _num(row.get("harness_cost"))
    out["unknown"] = _words(row.get("unknown"))
    return out


def _row_sums(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**_sums(row), "unpriced_models": _words(row.get("unpriced_models")),
            "rules": _words(row.get("rules"))}


def _index(found: Mapping[str, Any]) -> dict[str, Any]:
    return {"passed": bool(found.get("passed")), "last_pass": _num(found.get("last_pass")),
            "note": _str(found.get("note"))}


def _report(answer: Mapping[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "since": _num(answer.get("since")),
        "until": _num(answer.get("until")),
        "tz": str(answer.get("tz") or "local"),
        "filters": _named(answer.get("filters") or {}, str),
        "rows": rows,
        "total": _sums(answer.get("total") or {}),
        "unpriced_models": _words(answer.get("unpriced_models")),
        "rules": _words(answer.get("rules")),
        "index": _index(answer.get("index") or {}),
    }


def _day(value: Any) -> str | None:
    # A day the page sends; an empty one leaves that end open.
    return value or None


def _read(kind: str, query: Mapping[str, Any], **given: Any) -> dict[str, Any]:
    """`report(kind)` over the index as it is, with the route's filters, as a dict."""
    args = {"since": _day(query.get("since")), "until": _day(query.get("until")),
            "harness": query.get("harness"), "project": query.get("project"),
            "model": query.get("model"), "tz": "UTC" if query.get("utc") else None}
    args.update(given)
    try:
        return api.report(kind, fresh=False, **args).as_dict()
    except _OWN_WORDS as exc:
        raise lb.Refused(str(exc)) from None


def _period_row(row: Mapping[str, Any]) -> dict[str, Any]:
    period = row.get("day", row.get("month"))
    return {"period": _str(period), "model": _str(row.get("model")), **_row_sums(row)}


def daily(call: Any) -> dict[str, Any]:
    """`GET /api/daily`: tokens and cost by day (or by month, `by=month`) and model."""
    by = call.query.get("by") or "day"
    answer = _read("daily" if by == "day" else "monthly", call.query)
    return {**_report(answer, [_period_row(row) for row in answer["rows"]]), "by": by}


def _session_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"harness": str(row.get("harness")), "session_id": _str(row.get("session_id")),
            "title": _str(row.get("title")), "cwd": _str(row.get("cwd")),
            "project": _str(row.get("project")), "models": _words(row.get("models")),
            "first_ts": _num(row.get("first_ts")), "last_ts": _num(row.get("last_ts")),
            **_row_sums(row)}


def _by_cost(row: Mapping[str, Any]) -> tuple:
    cost = row["cost"]
    return (cost is None, -(cost or 0.0), -row["tokens"])


def sessions(call: Any) -> dict[str, Any]:
    """`GET /api/sessions`: the `top` sessions by cost (200 unless asked, at most 1,000),
    the dearest first, and in `more` how many the range holds past them."""
    top = call.query.get("top")
    top = TOP_DEFAULT if top is None else top
    if top < 1:
        raise lb.Refused("top is a number of sessions, 1 or more")
    answer = _read("sessions", call.query)
    rows = sorted((_session_row(row) for row in answer["rows"]), key=_by_cost)
    return {**_report(answer, rows[:top]), "top": top, "more": max(0, len(rows) - top)}


def session(call: Any) -> dict[str, Any]:
    """`GET /api/session?session=ID`: one session by agent and model, over the same
    filters, so it sums to that session's row of `/api/sessions`."""
    answer = _read("session", call.query, session=call.query["session"])
    rows = [{"agent": str(row.get("agent")), "kind": str(row.get("kind")),
             "title": _str(row.get("title")), "model": _str(row.get("model")),
             "first_ts": _num(row.get("first_ts")), "last_ts": _num(row.get("last_ts")),
             **_row_sums(row)} for row in answer["rows"]]
    return _report(answer, rows)


def projects(call: Any) -> dict[str, Any]:
    """`GET /api/projects`: tokens and cost by project key, or by `cwd` for none."""
    answer = _read("projects", call.query)
    rows = [{"project": _str(row.get("project")), "cwd": _str(row.get("cwd")),
             "models": _words(row.get("models")), **_row_sums(row)}
            for row in answer["rows"]]
    return _report(answer, rows)


def _provenance(found: Mapping[str, Any]) -> dict[str, Any]:
    period = found.get("period")
    return {"layer": _str(found.get("layer")), "step": _int(found.get("step")) or 0,
            "rule": str(found.get("rule") or ""), "row": _str(found.get("row")),
            "snapshot": _str(found.get("snapshot")),
            "overridden": _words(found.get("overridden")),
            "period": None if not isinstance(period, Mapping) else {
                "from": _str(period.get("from")), "to": _str(period.get("to"))},
            "note": _str(found.get("note")), "filled": _words(found.get("filled")),
            "variant": _str(found.get("variant"))}


def models(call: Any) -> dict[str, Any]:
    """`GET /api/models`: tokens and cost by model, with where each price came from."""
    answer = _read("models", call.query)
    rows = [{"model": _str(row.get("model")),
             "provenance": [_provenance(found) for found in row.get("provenance") or ()],
             **_row_sums(row)} for row in answer["rows"]]
    return _report(answer, rows)


def _today() -> dt.date:
    return dt.date.today()


def cost(call: Any) -> dict[str, Any]:
    """`GET /api/cost`: today and the six days before it, by day and model, in the page
    server's local zone; `today` names the last of them."""
    today = _today()
    since = today - dt.timedelta(days=COST_DAYS - 1)
    answer = _read("daily", {}, since=since.isoformat())
    return {**_report(answer, [_period_row(row) for row in answer["rows"]]),
            "today": today.isoformat()}


# --- the index -----------------------------------------------------------------------------

_LANE_COUNTS = ("files", "excluded", "held", "requests", "duplicates")


def _lane(row: Mapping[str, Any]) -> dict[str, Any]:
    unreadable = [{"path": str(pair[0]), "reason": str(pair[1])}
                  for pair in row.get("unreadable") or ()
                  if isinstance(pair, (list, tuple)) and len(pair) == 2]
    out = {"lane": str(row.get("lane")), "harness": _str(row.get("harness")),
           "state": _str(row.get("state")), "reason": _str(row.get("reason")),
           "root": _str(row.get("root")), "listed": _int(row.get("listed")),
           "compressed": _int(row.get("compressed")),
           "checked_at": _num(row.get("checked_at")), "unreadable": unreadable,
           "imports_path": _str(row.get("imports_path")),
           "imports_reason": _str(row.get("imports_reason")),
           "imported": _int(row.get("imported")),
           "originator_only": _int(row.get("originator_only")),
           "held_reasons": _named(row.get("held_reasons") or {}, lambda n: _int(n) or 0)}
    out.update({name: _int(row.get(name)) or 0 for name in _LANE_COUNTS})
    return out


def _file(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"lane": str(row.get("lane")), "path": str(row.get("path")),
            "part": str(row.get("part") or "")}


def _status(answer: Mapping[str, Any]) -> dict[str, Any]:
    total = answer.get("total") or {}
    cut = {name: _int(total.get(name)) or 0
           for name in (*_LANE_COUNTS, "lanes", "duplicate_files", "index_version")}
    cut["held_rows"] = [{**_file(row), "reason": str(row.get("reason"))}
                        for row in total.get("held_rows") or ()]
    cut["duplicate_rows"] = [
        {**_file(row), "session_id": _str(row.get("session_id")),
         "requests": _int(row.get("requests")) or 0, "of_lane": str(row.get("of_lane")),
         "of_path": str(row.get("of_path")), "of_part": str(row.get("of_part") or "")}
        for row in total.get("duplicate_rows") or ()]
    cut["last_pass"] = _num(total.get("last_pass"))
    return {"lanes": [_lane(row) for row in answer.get("rows") or ()], "total": cut,
            "index": _index(answer.get("index") or {})}


def _status_now() -> dict[str, Any]:
    try:
        return api.report("status", fresh=False).as_dict()
    except _OWN_WORDS as exc:
        raise lb.Refused(str(exc)) from None


def status(call: Any) -> dict[str, Any]:
    """`GET /api/status`: the index's lanes, held rows, last pass and note."""
    return _status(_status_now())


# --- limits --------------------------------------------------------------------------------

_WINDOW_KEYS = frozenset(WINDOW.items)


def _window(row: Mapping[str, Any]) -> dict[str, Any]:
    extra = row.get("extra")
    return {
        "harness": str(row.get("harness")), "limit_id": str(row.get("limit_id")),
        "window_minutes": _int(row.get("window_minutes")), "label": str(row.get("label")),
        "state": str(row.get("state")), "reason": _str(row.get("reason")),
        "slot": _str(row.get("slot")), "used_percent": _num(row.get("used_percent")),
        "resets_at": _str(row.get("resets_at")), "observed_at": _str(row.get("observed_at")),
        "source": _str(row.get("source")), "session_id": _str(row.get("session_id")),
        "plan_type": _str(row.get("plan_type")), "full_at": _str(row.get("full_at")),
        "projection": _str(row.get("projection")), "fit": _int(row.get("fit")) or 0,
        "note": _str(row.get("note")),
        "dollars": [{"name": name, "value": value} for name, value
                    in dollars(extra if isinstance(extra, Mapping) else {})],
        "unknown": [name for name in _words(row.get("unknown")) if name in _WINDOW_KEYS],
    }


def _limits_value(answer: Mapping[str, Any]) -> dict[str, Any]:
    pull = answer.get("pull")
    return {
        "rows": [_window(row) for row in answer.get("rows") or ()],
        "pull": None if not isinstance(pull, Mapping) else {
            "tried": bool(pull.get("tried")), "ok": bool(pull.get("ok")),
            "reason": _str(pull.get("reason")), "via": _str(pull.get("via")),
            "added": _int(pull.get("added")) or 0},
        "notes": _words(answer.get("notes")),
    }


def _limits() -> tuple[float, dict[str, Any]]:
    try:
        found = api.limits(pull=True)
    except _OWN_WORDS as exc:
        raise lb.Refused(str(exc)) from None
    return float(found.now), _limits_value(found.as_dict())


def gather_limits() -> dict[str, Any]:
    """The `limits` topic's value: `/api/limits`'s without `now`."""
    return _limits()[1]


def limits(call: Any) -> dict[str, Any]:
    """`GET /api/limits`: each plan window now, with a Codex pull through turns when the
    newest Codex reading is older than `limits.codex_pull_after`."""
    now, value = _limits()
    return {"now": now, **value}


# --- the pass, the topics and the poller ---------------------------------------------------

class _Feed:
    """The topics, the limits Snapshot and poller, and the pass, on one event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.topics = lb.Topics(*TOPICS)
        self.snapshot = lb.Snapshot(gather_limits, self.topics, "limits")
        self.poller: asyncio.Task[None] | None = None
        self.pass_task: asyncio.Task[None] | None = None

    # The poller.

    def start_poller(self) -> None:
        if self.poller is None:
            self.poller = self.loop.create_task(self._poll(), name="usage limits poller")

    async def _poll(self) -> None:
        while True:
            try:
                await self.snapshot.changed()
            except Exception as err:
                _log_once(err, "the limits poller's gather failed")
            await asyncio.sleep(PERIOD)

    # The pass.

    def running(self) -> bool:
        return self.pass_task is not None and not self.pass_task.done()

    def publish_index(self, *, running: bool, last_pass: float | None,
                      note: str | None) -> None:
        self.topics.publish("index", {"running": running, "last_pass": last_pass,
                                      "note": note})

    def start(self, hold: contextlib.ExitStack, last_pass: float | None) -> None:
        self.publish_index(running=True, last_pass=last_pass, note=None)
        self.pass_task = self.loop.create_task(self._pass(hold, last_pass),
                                               name="usage index pass")

    async def _pass(self, hold: contextlib.ExitStack, last_pass: float | None) -> None:
        with hold:
            try:
                answer = await aio.offload(_fresh_status)
            except _OWN_WORDS as exc:
                note = str(exc)
            except Exception as err:
                _log_once(err, "the page's pass failed")
                note = (f"the pass failed ({type(err).__name__}); the page server's log "
                        "has the details")
            else:
                found = _index(answer.get("index") or {})
                last_pass, note = found["last_pass"], found["note"]
            self.publish_index(running=False, last_pass=last_pass, note=note)


def _fresh_status() -> dict[str, Any]:
    return api.report("status", fresh=True).as_dict()


_current: _Feed | None = None


def _feed() -> _Feed:
    # A page server runs one loop for its life; a loop of its own per in-process test
    # gets a fresh feed, since a task and its Snapshot belong to one loop.
    global _current
    loop = asyncio.get_running_loop()
    if _current is None or _current.loop is not loop:
        _current = _Feed(loop)
    return _current


async def start_pass(call: Any) -> dict[str, Any]:
    """`POST /api/pass`: start a pass unless one this server started still runs, and
    answer at once. Refused (422) with no index or one at another version."""
    feed = _feed()
    if feed.running():
        return {"started": False, "running": True}
    answer = await aio.offload(_status_now)
    total, found = answer.get("total") or {}, answer.get("index") or {}
    if total.get("index_version") != store.INDEX_VERSION:
        raise lb.Refused(found.get("note") or "run `moxi usage index` to build the index")
    if feed.running():                            # another call started one meanwhile
        return {"started": False, "running": True}
    hold = contextlib.ExitStack()
    hold.enter_context(call.busy("pass"))
    feed.start(hold, _num(total.get("last_pass")))
    return {"started": True, "running": True}


async def events(call: Any) -> Any:
    """`GET /api/events`: the stream over `limits` and `index`. The first call starts
    the limits poller, which then runs for the page server's life."""
    feed = _feed()
    feed.start_poller()
    return call.stream(feed.topics)


__all__ = ["COST_DAYS", "PERIOD", "TOPICS", "cost", "daily", "events", "gather_limits",
           "limits", "models", "projects", "session", "sessions", "start_pass", "status"]

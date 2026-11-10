"""The handlers of usage's page, the pass it starts, and the poller that feeds its stream.

**Reads.** Each read is a plain `def`, run on one of the page server's worker threads,
and thin over `moxi_usage.api`: it calls `limits()` or `report()`, takes `as_dict()`, and
cuts the answer to its route's declared shape. It runs no SQL of its own. Every report
is read with `fresh=False`, so a read never runs a pass and never waits on the index's
lock; its `index.note` says it read the index as it is. Each report's answer, the
status and the limits carry `today`, the page server's local date. Where the API's
answer holds a map with open keys, the cut turns it into a list of `{name, value}` rows
(a report's `filters`, a lane's `held_reasons`, the dollar figures `limits.dollars`
finds in a window's `extra`) or leaves it out (a price's `source`, any lane fact not
declared). An argument the API refuses (`ReportError`), an index written by a newer usage
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
`limits.codex_pull_after_s`. It gathers at once and then every `PERIOD` seconds, and its
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
from pathlib import Path
from typing import Any

import moxi.config
import moxi.store
from moxi import aio, modules

from . import api, store
from .limits import dollars
from .page import TOP_DEFAULT, WINDOW

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"usage's page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

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


def _today() -> dt.date:
    return dt.date.today()


def _home() -> str | None:
    """The account's home folder, or None when it cannot be said."""
    try:
        return str(Path.home())
    except RuntimeError:
        return None


def _report(answer: Mapping[str, Any], rows: list[dict[str, Any]],
            total: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "today": _today().isoformat(),
        "since": _num(answer.get("since")),
        "until": _num(answer.get("until")),
        "tz": str(answer.get("tz") or "local"),
        "filters": _named(answer.get("filters") or {}, str),
        "rows": rows,
        "total": _sums(answer.get("total") or {}) if total is None else total,
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
            "folder": query.get("folder") or None, "model": query.get("model"), "tz": "UTC" if query.get("utc") else None}
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
    """`GET /api/projects`: tokens and cost by project key, or by `cwd` for none; with
    `by=folder`, by `cwd` alone, so a key an earlier pass stored groups nothing. `home`
    is the account's home folder in both."""
    by = call.query.get("by") or "project"
    answer = _read("projects", call.query, by=by)
    rows = [{"project": _str(row.get("project")), "cwd": _str(row.get("cwd")),
             "models": _words(row.get("models")), **_row_sums(row)}
            for row in answer["rows"]]
    return {**_report(answer, rows), "by": by, "home": _home()}


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


def cost(call: Any) -> dict[str, Any]:
    """`GET /api/cost`: today and the six days before it, by day and model, in the page
    server's local zone; `today` names the last of them."""
    today = _today()
    since = today - dt.timedelta(days=COST_DAYS - 1)
    answer = _read("daily", {}, since=since.isoformat())
    return {**_report(answer, [_period_row(row) for row in answer["rows"]]),
            "today": today.isoformat()}


# --- the cost terms and the requests -------------------------------------------------------

def _split(found: Any) -> list[dict[str, Any]]:
    return [{"class": str(item.get("class")), "tier": _str(item.get("tier")),
             "tokens": _int(item.get("tokens")) or 0, "cost": _num(item.get("cost")) or 0.0}
            for item in found or ()]


def _term(found: Mapping[str, Any]) -> dict[str, Any]:
    return {"class": str(found.get("class")), "tokens": _int(found.get("tokens")) or 0,
            "price": _num(found.get("price")), "cost": _num(found.get("cost")),
            "price_from": _str(found.get("price_from")), "unknown": _words(found.get("unknown"))}


def _group(found: Mapping[str, Any]) -> dict[str, Any]:
    period = found.get("period")
    return {"model": _str(found.get("model")), "provider": _str(found.get("provider")),
            "tier": _str(found.get("tier")), "above": _int(found.get("above")) or 0,
            "period": None if not isinstance(period, Mapping) else {
                "from": _str(period.get("from")), "to": _str(period.get("to"))},
            "provenance": _provenance(found.get("provenance") or {}),
            "requests": _int(found.get("requests")) or 0,
            "terms": [_term(term) for term in found.get("terms") or ()],
            "cost": _num(found.get("cost")),
            "unpriced_tokens": _int(found.get("unpriced_tokens")) or 0,
            "unknown_rows": _int(found.get("unknown_rows")) or 0,
            "rule": _str(found.get("rule")), "why": _str(found.get("why")),
            "unknown": _words(found.get("unknown"))}


def terms(call: Any) -> dict[str, Any]:
    """`GET /api/terms`: every term of each cost, by model (the default), day, month or,
    with `session`, agent; `session` alone narrows the other keys to that session."""
    by = call.query.get("by") or "model"
    given: dict[str, Any] = {"by": by}
    if call.query.get("session"):
        given["session"] = call.query["session"]
    answer = _read("terms", call.query, **given)
    rows = [{"model": _str(row.get("model")), "period": _str(row.get("day", row.get("month"))),
             "agent": _str(row.get("agent")), "kind": _str(row.get("kind")),
             **_row_sums(row), "groups": [_group(group) for group in row.get("groups") or ()],
             "split": _split(row.get("split"))} for row in answer["rows"]]
    total = answer.get("total") or {}
    return {**_report(answer, rows, {**_sums(total), "split": _split(total.get("split"))}),
            "by": by}


def _mark(found: Mapping[str, Any]) -> dict[str, Any]:
    return {"after": _int(found.get("after")) or 0, "ts": _num(found.get("ts")),
            "trigger": _str(found.get("trigger")), "pre_tokens": _int(found.get("pre_tokens"))}


_REQUEST_CLASSES = ("fresh_input", "cache_read", "cache_write_5m", "cache_write_1h", "output")


def _request_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"n": _int(row.get("n")) or 0, "ts": _num(row.get("ts")),
            "model": _str(row.get("model")), "tier": _str(row.get("tier")),
            "turn": _int(row.get("turn")), "turn_start": bool(row.get("turn_start")),
            "context": _int(row.get("context")), "window": _int(row.get("window")),
            **{name: _int(row.get(name)) or 0 for name in _REQUEST_CLASSES},
            "unknown": _words(row.get("unknown")), "synthetic": bool(row.get("synthetic")),
            "cost": _num(row.get("cost")),
            "split": [{"class": str(item.get("class")), "cost": _num(item.get("cost"))}
                      for item in row.get("split") or ()]}


def requests(call: Any) -> dict[str, Any]:
    """`GET /api/requests?session=ID&agent=ID`: one row per request of one session's
    agent (`main` unless `agent` names another), with its compactions placed between
    the rows and what the report can't show."""
    answer = _read("requests", call.query, session=call.query["session"],
                   agent=call.query.get("agent") or "main")
    total = answer.get("total") or {}
    cut = {**_sums(total), "marks": [_mark(mark) for mark in total.get("marks") or ()],
           "turns": _int(total.get("turns")),
           "claimed_elsewhere": _int(total.get("claimed_elsewhere")) or 0,
           "copied_marks": _int(total.get("copied_marks")) or 0,
           "marks_known": bool(total.get("marks_known"))}
    return _report(answer, [_request_row(row) for row in answer["rows"]], cut)


# --- the rates -----------------------------------------------------------------------------

#: The most model ids `/api/prices?models=` takes, and the most matches `?q=` sends.
PRICES_MAX = 100
MATCHES_MAX = 50
_PRICES = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")


def _prices_of(found: Mapping[str, Any]) -> dict[str, Any]:
    return {name: _num(found.get(name)) for name in _PRICES}


def _conditions(found: Mapping[str, Any]) -> dict[str, Any]:
    return {"above_tokens": _int(found.get("above_tokens")),
            "service_tier": _str(found.get("service_tier")), "speed": _str(found.get("speed"))}


def _tiers(facts: Any, defaults: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Each tier of `facts` with its conditions and the prices a request under it pays,
    as `moxi_usage.cost.prices` works them out, and which of those came from the tier's
    input price or the model's own row."""
    import dataclasses

    from . import cost

    tiers = facts.tiers if isinstance(facts.tiers, Mapping) else {}
    out = []
    for name in sorted(tiers):
        tier = tiers[name]
        if not isinstance(tier, Mapping):
            continue
        paid = cost.prices(dataclasses.replace(facts, tiers={name: tier}), defaults,
                           size=_int(tier.get("above_tokens")) or 0,
                           service_tier=_str(tier.get("service_tier")),
                           speed=_str(tier.get("speed")))
        out.append({"name": str(name), "conditions": _conditions(tier),
                    "prices": {field: _num(getattr(paid, field)) for field in _PRICES},
                    "from_tier_input": list(paid.from_tier_input),
                    "from_row": list(paid.from_row)})
    return out


def _rate(facts: Any, table: Any, asked: str | None) -> dict[str, Any]:
    data = facts.as_dict()
    defaults = table.defaults.get(facts.provider) if isinstance(facts.provider, str) else None
    window, most = data.get("window"), data.get("max_output")
    return {"model": _str(facts.model), "asked_provider": asked,
            **_prices_of(data), "window": _int(window), "max_output": _int(most),
            "provider": _str(data.get("provider")), "tiers": _tiers(facts, defaults),
            "provenance": _provenance(data.get("provenance") or {}),
            "unknown": _words(data.get("unknown"))}


def _override(row: Mapping[str, Any]) -> dict[str, Any]:
    tiers = row.get("tiers") if isinstance(row.get("tiers"), Mapping) else {}
    return {"id": str(row.get("id")), "match": str(row.get("match") or "exact"),
            "prices": _prices_of(row), "window": _int(row.get("window")),
            "max_output": _int(row.get("max_output")),
            "tiers": [{"name": str(name), "conditions": _conditions(tier),
                       "prices": _prices_of(tier)} for name, tier in tiers.items()],
            "provider": _str(row.get("provider")), "from": _str(row.get("from")),
            "to": _str(row.get("to")), "note": _str(row.get("note"))}


def _ids(text: str | None) -> list[str]:
    return [] if text is None else [part.strip() for part in text.split(",")]


def prices(call: Any) -> dict[str, Any]:
    """`GET /api/prices`: the model table's rates, read with no pass and no SQL. Always
    its effective date, layers, `[[model]]` rows and problems; with `models=ID,...` (up
    to `PRICES_MAX`, each looked up now under the provider at the same place of
    `providers=`, none when that place is empty) those models' facts; with `q=TEXT` the
    table's ids that hold it, ignoring case, the first `MATCHES_MAX` by id, and how many
    matched."""
    query = call.query
    asked, text, given = query.get("models"), query.get("q"), query.get("providers")
    if asked is not None and text is not None:
        raise lb.Refused("ask for models or search with q, not both")
    if given is not None and asked is None:
        raise lb.Refused("providers go with models, one for each id")
    from . import models as tables

    try:
        table = tables.table()
    except _OWN_WORDS as exc:
        raise lb.Refused(str(exc)) from None
    found: list[dict[str, Any]] = []
    matched = 0
    if asked is not None:
        ids = _ids(asked)
        if len(ids) > PRICES_MAX:
            raise lb.Refused(f"ask for at most {PRICES_MAX} models at once, not {len(ids)}")
        providers = _ids(given) if given is not None else [""] * len(ids)
        if len(providers) != len(ids):
            raise lb.Refused(f"{len(providers)} providers for {len(ids)} models: give one "
                             "for each id, empty for none")
        for model, provider in zip(ids, providers, strict=True):
            if model:
                found.append(_rate(table.lookup(model, provider=provider or None), table,
                                   provider or None))
    elif text is not None:
        if len(text.strip()) < 2:
            raise lb.Refused("search with 2 characters or more")
        needle = text.strip().casefold()
        hits = [model for model in table.ids() if needle in model.casefold()]
        matched = len(hits)
        found = [_rate(table.lookup(model), table, None) for model in hits[:MATCHES_MAX]]
    summary = tables.summary(table)
    overrides = next((layer.get("overrides") or [] for layer in summary["layers"]
                      if layer["layer"] == "override"), [])
    return {"effective_date": _str(summary.get("effective_date")),
            "rows": _int(summary.get("rows")) or 0,
            "layers": [{"layer": str(layer["layer"]), "date": _str(layer.get("date")),
                        "rows": _int(layer.get("rows")) or 0} for layer in summary["layers"]],
            "overrides": [_override(row) for row in overrides],
            "problems": _words(summary.get("problems")),
            "models": found, "matched": matched}


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
    return {"today": _today().isoformat(),
            "lanes": [_lane(row) for row in answer.get("rows") or ()], "total": cut,
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
        "today": _today().isoformat(),
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
    newest Codex reading is older than `limits.codex_pull_after_s`."""
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


__all__ = ["COST_DAYS", "MATCHES_MAX", "PERIOD", "PRICES_MAX", "TOPICS", "cost", "daily",
           "events", "gather_limits", "limits", "models", "prices", "projects", "requests",
           "session", "sessions", "start_pass", "status", "terms"]

"""The handlers of the launcher's page.

The reads answer from the launcher's own calls: the catalog (`catalog.offered`'s rows, as
the UI shell's form reads them), the presets file checked (`presets.check`), the launch
log (`store.log`) and the runs with their events (`store.runs`, `store.run_events`). Each
holds `busy("read")` for its own length only.

A handler cuts each answer to its route's declared keys, and turns any failure it did not
expect into `state` `error` with a fixed `detail` naming only the exception's class, so no
message text, which may hold a path, reaches the browser. The class and its traceback are
logged once. A query the read can't take (a `before` that is not a time, a `limit` under
1, `request` with paging) is refused (422) with what to send instead.

A route the page declares whose handler this version does not have yet answers 422, saying
so, and runs nothing.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from moxi import modules
from moxi import store as core_store
from moxi.config import ConfigError

from . import catalog, presets, slots, store
from .page import LAUNCH, LIMIT, RUN

_loopback = modules.load("loopback", interface=1)
if not _loopback.ok:
    raise ImportError(f"the launcher's page needs the loopback module: {_loopback.reason}")
lb = _loopback.api

#: The detail of a read that failed in a way it did not expect.
UNEXPECTED = "the launcher's page server hit an unexpected error ({}); its log has the details"
#: The catalog rows whose text a preset's values never send: a brief or standing
#: instructions written inline.
WITHHELD = ("brief", "instructions")
#: The run event detail key never sent: a notice's whole text, which the run's other events
#: already describe.
EVENT_TEXT = "text"

log = logging.getLogger("moxi.launcher.page")

_logged: set[type] = set()
_logged_lock = threading.Lock()


def _log_once(err: BaseException, what: str) -> None:
    with _logged_lock:
        first = type(err) not in _logged
        _logged.add(type(err))
    if first:
        log.error("%s (%s)", what, type(err).__name__, exc_info=err)


def _failed(err: BaseException, what: str, **empty: Any) -> dict[str, Any]:
    _log_once(err, what)
    return {"state": "error", "detail": UNEXPECTED.format(type(err).__name__), **empty}


def _up(**answer: Any) -> dict[str, Any]:
    return {"state": "up", "detail": None, **answer}


# -- spelling values as the command line does ---------------------------------------------


def spelled(value: object) -> str | None:
    """A scalar value as the command line spells it: `true`/`false`, a number's digits,
    a string as it is; None for a value that sets nothing (None or "")."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _spelled_items(value: object) -> list[str] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    return [str(item) for item in value]


def _value_of(row: catalog.Row | None, value: object) -> tuple[str | None, list[str] | None]:
    """`(value, items)` for one value of `row`: a list row's items, any other's spelling.
    A switch with an optional value that is off spells nothing."""
    if isinstance(value, (list, tuple)):
        return None, _spelled_items(value)
    if row is not None and row.optional_value and value is False:
        return None, None
    return spelled(value), None


# -- /api/catalog ---------------------------------------------------------------------------


def _available() -> Callable[[str], bool]:
    # Looked up at each call, so it answers for the modules installed now.
    return slots.links


def _offered_values(row: catalog.Row, available: Callable[[str], bool],
                    harness: str) -> tuple[list[str] | None, list[str]]:
    """The words of `row` this install offers, and a note for each it doesn't: an `enum`
    value whose own modules are absent, or whose condition does not hold here. A value
    that needs only another row's value (a target) stays; the launch's check refuses it
    with its own words."""
    if not row.values:
        return None, []
    words, notes = [], []
    for word in row.values:
        need = row.when.get(word)
        missing = need is not None and (
            not all(available(module) for module in need.requires)
            or (need.condition is not None
                and catalog.CONDITIONS[need.condition](harness) is not None))
        if missing:
            notes.append(f"{word} is not offered here: {need.refused}")
        else:
            words.append(str(word))
    return words, notes


def field(row: catalog.Row, available: Callable[[str], bool], harness: str) -> dict[str, Any]:
    """One catalog row as the UI shell's form reads it, for a `harness` launch."""
    why = row.unoffered(available, harness)
    values, notes = _offered_values(row, available, harness)
    default, items = _value_of(row, row.default)
    return {
        "name": row.name, "type": row.type, "doc": row.doc,
        "default": default, "value_items": items,
        "values": values, "items": row.items,
        "flag": row.option, "since": row.since,
        "repeatable": row.repeatable, "optional_value": row.optional_value,
        "hidden": row.hidden, "posture": row.posture,
        # The form reads one module's name; a row needing several says so in `note`.
        "requires": row.requires if isinstance(row.requires, str) else None,
        "needs_absent": why is not None,
        "note": why[0] if why is not None else ("; ".join(notes) or None),
    }


def fields(harness: str, available: Callable[[str], bool]) -> list[dict[str, Any]]:
    """Every row a user may set for a `harness` launch, the launcher's then the harness's,
    in catalog order, with those this install doesn't offer marked so."""
    rows = [*catalog.launcher(), *catalog.load(harness)]
    return [field(row, available, harness) for row in rows if row.settable]


def read_catalog(call: Any) -> dict[str, Any]:
    """`GET /api/catalog`: per harness, the rows a launch may set."""
    with call.busy("read"):
        try:
            available = _available()
            harnesses = [{"name": harness, "fields": fields(harness, available)}
                         for harness in catalog.HARNESSES]
        except Exception as err:
            return _failed(err, "reading the catalog failed", harnesses=[])
        return _up(harnesses=harnesses)


# -- /api/presets ---------------------------------------------------------------------------


def _preset(preset: presets.Preset) -> dict[str, Any]:
    harness = preset.harness if isinstance(preset.harness, str) else None
    rows = []
    for name, value in preset.values.items():
        found = catalog.row(harness, name) if harness in catalog.HARNESSES else (
            catalog.launcher().rows.get(name))
        if name in WITHHELD:
            shown, items = None, None
        else:
            shown, items = _value_of(found, value)
        rows.append({"name": name, "value": shown, "items": items})
    return {"kind": preset.kind, "name": preset.name, "harness": harness, "values": rows}


def _file_problem(err: ConfigError) -> dict[str, Any]:
    return {"where": "file", "key": err.key if isinstance(err.key, str) else None,
            "problem": str(err.problem), "hint": str(err.fix), "code": "bad-file",
            "level": presets.ERROR}


def read_presets(call: Any) -> dict[str, Any]:
    """`GET /api/presets`: every seat and project preset, and what is wrong with each. A
    presets file that can't be read is one problem, `where` `file`."""
    with call.busy("read"):
        try:
            try:
                found = presets.read()
            except ConfigError as err:
                return _up(presets=[], problems=[_file_problem(err)])
            problems = presets.check(available=_available(), presets=found)
            return _up(presets=[_preset(p) for p in found.all()],
                       problems=[p.as_json() for p in problems])
        except Exception as err:
            return _failed(err, "reading the presets failed", presets=[], problems=[])


# -- the launch log -------------------------------------------------------------------------


@contextmanager
def _store() -> Iterator[sqlite3.Connection | None]:
    """The launch log, or None when it was never made; nothing is made to say so."""
    try:
        conn = store.connect(create=False)
    except core_store.StoreError as err:
        if isinstance(err, core_store.StoreTooNew):
            raise
        yield None
        return
    try:
        yield conn
    finally:
        conn.close()


def _cut(data: Mapping[str, Any], shape: Any) -> dict[str, Any]:
    """`data` cut to the keys `shape` declares; a key it lacks is null."""
    return {key: data.get(key) for key in shape.items}


def _paging(query: Mapping[str, Any]) -> tuple[int, str | None]:
    limit = query.get("limit", LIMIT)
    if limit < 1:
        raise lb.Refused("limit is 1 or more")
    before = query.get("before")
    if before is not None:
        try:
            store.parse_time(before)
        except ValueError:
            raise lb.Refused("before is a time as the launch log writes it, such as a row's "
                             "created_at") from None
    return limit, before


def _alone(query: Mapping[str, Any], key: str) -> None:
    if key in query and len(query) > 1:
        raise lb.Refused(f"{key} is asked alone, without {', '.join(sorted(set(query) - {key}))}")


def launch_row(row: store.LaunchRow, stale_after: float, now: datetime) -> dict[str, Any]:
    """One launch log row as the page shows it."""
    data = {key: getattr(row, key, None) for key in LAUNCH.items}
    return {**data, "stale": row.stale(stale_after, now)}


def refusal(detail: Mapping[str, Any]) -> dict[str, Any]:
    """A refusal recorded in an event's detail, as the page sends it."""
    def text(key: str) -> str | None:
        value = detail.get(key)
        return value if isinstance(value, str) else None

    return {"code": text("code") or "refused", "row": text("row"),
            "problem": text("problem") or "", "hint": text("hint") or ""}


def _requested(conn: sqlite3.Connection, request_id: str
               ) -> tuple[list[store.LaunchRow], dict[str, Any] | None]:
    row = store.get(conn, request_id)
    if row is not None:
        return [row], None
    refused = [e for e in store.events(conn, request_id) if e.kind == store.LAUNCH_REFUSED]
    return [], refusal(refused[-1].detail) if refused else None


def launches(*, limit: int = LIMIT, before: str | None = None,
             request: str | None = None) -> dict[str, Any]:
    """The launch log page the route and the `launches` topic send: newest first, or the
    one launch `request` names, with the refusal of a page launch refused before its row
    was written."""
    rows: list[store.LaunchRow] = []
    refused = None
    with _store() as conn:
        if conn is not None and request is not None:
            rows, refused = _requested(conn, request)
        elif conn is not None:
            rows = store.log(conn, limit=limit, before=before)
    after, now = store.stale_after_s(), datetime.now(timezone.utc)
    return _up(launches=[launch_row(row, after, now) for row in rows], refused=refused)


def read_launches(call: Any) -> dict[str, Any]:
    """`GET /api/launches`: the launch log, paged; with `request`, one launch."""
    query = call.query
    _alone(query, "request")
    limit, before = _paging(query)
    with call.busy("read"):
        try:
            return launches(limit=limit, before=before, request=query.get("request"))
        except Exception as err:
            return _failed(err, "reading the launch log failed", launches=[], refused=None)


# -- /api/runs ------------------------------------------------------------------------------


def _event(event: store.RunEvent) -> dict[str, Any]:
    detail = {k: v for k, v in event.detail.items() if k != EVENT_TEXT}
    return {"at": event.at, "kind": event.kind,
            "detail": json.dumps(detail, sort_keys=True, ensure_ascii=False, default=str)}


def run_entry(conn: sqlite3.Connection, run: store.Run) -> dict[str, Any]:
    """One run as the page shows it, with its events, oldest first."""
    return {**_cut(run.as_json(), RUN),
            "events": [_event(e) for e in store.run_events(conn, run.run_id)]}


def read_runs(call: Any) -> dict[str, Any]:
    """`GET /api/runs`: one run (`run`), one launch's runs (`request`, paged), or the
    newest runs (paged), each with its events."""
    query = call.query
    _alone(query, "run")
    limit, before = _paging(query)
    with call.busy("read"):
        try:
            with _store() as conn:
                if conn is None:
                    found: list[store.Run] = []
                elif "run" in query:
                    one = store.get_run(conn, query["run"])
                    found = [] if one is None else [one]
                else:
                    found = store.runs(conn, request_id=query.get("request"), limit=limit,
                                       before=before)
                return _up(runs=[run_entry(conn, run) for run in found])
        except Exception as err:
            return _failed(err, "reading the runs failed", runs=[])


# -- the routes still to come ----------------------------------------------------------------


def _not_yet(route: str) -> Callable[[Any], Any]:
    def handler(call: Any) -> Any:
        raise lb.Refused(f"this version of the launcher's page does not answer {route} yet")

    handler.__name__ = handler.__qualname__ = route.rsplit("/", 1)[-1]
    return handler


read_sessions = _not_yet("/api/sessions")
read_state = _not_yet("/api/state")
preview = _not_yet("/api/preview")
launch = _not_yet("/api/launch")
resume = _not_yet("/api/resume")
events = _not_yet("/api/events")

__all__ = ["EVENT_TEXT", "UNEXPECTED", "WITHHELD", "events", "field", "fields",
           "launch", "launch_row", "launches", "preview", "read_catalog", "read_launches",
           "read_presets", "read_runs", "read_sessions", "read_state", "refusal", "resume",
           "run_entry", "spelled"]

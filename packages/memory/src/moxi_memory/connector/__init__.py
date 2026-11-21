"""memory's provider for the connector, the public MCP host: `search_memory`, past
sessions searched in the Claude apps, made safe for a chat. Offered through one entry
point:

    [project.entry-points."moxi.connector.providers"]
    memory = "moxi_memory.connector:PROVIDER"

Only the host loads this package, and nothing else in memory imports it. It uses nothing
of the host but its interface 1, `moxi_connector.provider`, reached through
`moxi.modules.load("connector", interface=1)` (`_interface`); no import statement in
memory names the host's package. When that load fails, importing this package raises
`ImportError` with the reason.

* The tool redacts through secrets' `chat_redact` alone and refuses `redaction-off` when
  it can't, or when memory's `redact.sets` lacks `credentials` or `broad` (`redaction`).
* At most two calls run at once, each in a thread of its own, ended at 20 seconds: the
  host's `Limit`, one for the provider. The walk stops itself 16 seconds after the call
  began (`tool.WALK_S`).
* `start(env)` keeps the environment memory's config and index are read with, and never
  raises: a missing index or set shows in `status()`, and the tool refuses per call.
* `status()` gives the `search` row and the `redaction` row. The tool refuses
  `no-sessions`, and the `search` row fails saying why, when the sessions installed
  doesn't offer the interface memory reads it at.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Mapping
from typing import Any

from moxi import modules

from . import tool as tool_mod
from .redaction import Gate, sets_missing

#: The host's module name and the interface this provider is written against.
MODULE = "connector"
INTERFACE = 1


def _interface():
    """The host's provider interface. Only the host loads this package, so the host is
    installed whenever this runs; a host that is missing, broken or lacks interface 1
    raises `ImportError`."""
    found = modules.load(MODULE, interface=INTERFACE)
    if not found.ok or found.api is None:
        raise ImportError(f"memory's connector provider needs the connector module, "
                          f"interface {INTERFACE}: {found.reason or found.state}")
    return found.api


class Memory:
    """The provider's state for one connector process. `gate` opens each call's
    redaction; `searcher` keyword arguments (`sessions`, `clock`) go to the tool's work
    (a test hands in its own)."""

    def __init__(self, api, *, gate: Gate | None = None, **searcher: Any):
        self.api = api
        self.gate = gate or Gate()
        self.limit = api.Limit()
        self.env: dict[str, str] = dict(os.environ)
        self.searcher = tool_mod.Searcher(api.Result, self.gate, lambda: self.env,
                                          **searcher)

    async def start(self, env: Mapping[str, str]) -> None:
        self.env = dict(env)

    async def stop(self) -> None:
        return None

    def status(self):
        return [self.api.StatusRow(*search_row(self.env, sessions=self.searcher.sessions)),
                self.api.StatusRow(*self.gate.row())]

    async def search_memory(self, args: Mapping[str, Any], ctx) -> Any:
        started = self.searcher.clock()
        out = await self.limit.run(functools.partial(self.searcher, dict(args), started))
        result, logged = out if isinstance(out, tuple) else (out, {})
        ctx.log(refusal=result.refusal, **logged)
        return result

    def provider(self):
        api = self.api
        tools = (api.Tool("search_memory", tool_mod.DESCRIPTION, tool_mod.INPUT,
                          self.search_memory, output_schema=tool_mod.OUTPUT,
                          annotations=dict(tool_mod.ANNOTATIONS), title="Search memory"),)
        return api.Provider(tools=tools, start=self.start, stop=self.stop,
                            status=self.status)


def search_row(env: Mapping[str, str] | None, *,
               sessions: Callable[[], Any] = tool_mod._sessions) -> tuple[str, str, str]:
    """The `search` status row: the index's generation and rows; `warn` with no index
    yet, while the first read fills it, at schema 1, or with rows that record no writer;
    `fail` when the index or the state store can't be read, `redact.sets` lacks a set
    the stored cuts rely on, or `sessions` (sessions' API at the interface memory reads
    it at) raises `RuntimeError`, the installed sessions not offering it."""
    from .. import index, search
    from .tool import _sets

    try:
        sessions()
    except RuntimeError as exc:
        return ("search", "fail", f"{exc}; the tool refuses no-sessions")
    sets = _sets(env or {})
    if sets is None:
        return ("search", "fail", "memory's config can't be read; the tool refuses")
    missing = sets_missing(sets)
    if missing:
        return ("search", "fail", f"memory's redact.sets lacks {' and '.join(missing)}, "
                                  "so a stored cut could split a secret; the tool refuses")
    try:
        reader = index.open_read(env)
    except index.IndexMissing:
        return ("search", "warn", "no index yet; run moxi memory reindex")
    except index.IndexUnreadable as exc:
        return ("search", "fail", f"memory's index can't be read: {exc}")
    with reader as r:
        try:
            if r.attach_state(search.STATE):
                search._has_sessions(r.conn)
            total = r.conn.execute("SELECT count(*) FROM rows").fetchone()[0]
            unwritten = index.rows_without_writer(r.conn)
            schema = index.schema_version(r.conn)
            filling = search.filling(r.conn)
        except Exception as exc:  # noqa: BLE001 - the row reports it
            return ("search", "fail", f"memory's index or state store can't be read: {exc}")
        generation = r.generation
    said = f"generation {generation}, {total} rows"
    warns = []
    if unwritten is None:
        warns.append(f"the index is at schema {schema}; it is updated when memory next "
                     "writes")
    elif unwritten:
        warns.append(f"{unwritten} rows stored with no writer; run moxi memory reindex to "
                     "search them from the chat")
    if filling is not None:
        warns.append("the first read is still filling it")
    if warns:
        return ("search", "warn", f"{said}; " + "; ".join(warns))
    return ("search", "ok", said)


def build(api=None, **kw):
    """A provider over the interface `api` (the host's, by default):
    `(Memory, Provider)`."""
    memory = Memory(api or _interface(), **kw)
    return memory, memory.provider()


MEMORY, PROVIDER = build()

__all__ = ["INTERFACE", "MEMORY", "MODULE", "PROVIDER", "Memory", "build", "search_row"]

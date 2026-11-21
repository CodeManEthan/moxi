"""sessions' provider for the connector, the public MCP host: the session list and
transcript reads in the Claude apps, each made safe for a chat. Offered through one entry
point:

    [project.entry-points."moxi.connector.providers"]
    sessions = "moxi_sessions.connector:PROVIDER"

Only the host loads this package, and nothing else in sessions imports it. It uses nothing
of the host but its interface 1, `moxi_connector.provider`, reached through
`moxi.modules.load("connector", interface=1)` (`_interface`); no import statement in
sessions names the host's package. When that load fails, importing this package raises
`ImportError` with the reason.

* Every tool redacts through secrets' `chat_redact` alone and refuses `redaction-off`
  when it can't (`redaction`).
* At most two calls run at once, each in a thread of its own, ended at 20 seconds: the
  host's `Limit`, one for the provider.
* `start(env)` keeps the environment the roots are read with for the status rows, and
  never raises: a missing root or set shows in `status()`, and the tools refuse per call.
* `status()` gives the `redaction` row and the `roots` row.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Mapping
from typing import Any

from moxi import modules

from . import reading
from . import tools as tools_mod
from .redaction import Gate

#: The host's module name and the interface this provider is written against.
MODULE = "connector"
INTERFACE = 1


def _interface():
    """The host's provider interface. Only the host loads this package, so the host is
    installed whenever this runs; a host that is missing, broken or lacks interface 1
    raises `ImportError`."""
    found = modules.load(MODULE, interface=INTERFACE)
    if not found.ok or found.api is None:
        raise ImportError(f"sessions' connector provider needs the connector module, "
                          f"interface {INTERFACE}: {found.reason or found.state}")
    return found.api


class Sessions:
    """The provider's state for one connector process. `gate` opens each call's
    redaction; `reads` hands the list its `newest` and the transcript reader its
    `locate`, `subagents`, `turns_last` and `turns_around` (a test hands in its own)."""

    def __init__(self, api, *, gate: Gate | None = None, **reads: Any):
        self.api = api
        self.gate = gate or Gate()
        self.limit = api.Limit()
        self.env: dict[str, str] = dict(os.environ)
        self.lister = tools_mod.Lister(api.Result, self.gate,
                                       **{k: v for k, v in reads.items() if k == "newest"})
        self.reader = reading.Reader(api.Result, self.gate,
                                     **{k: v for k, v in reads.items() if k != "newest"})

    async def start(self, env: Mapping[str, str]) -> None:
        self.env = dict(env)
        self.reader.env = self.env

    async def stop(self) -> None:
        return None

    def status(self):
        return [self.api.StatusRow(*self.gate.row()), self.api.StatusRow(*self._roots())]

    def _roots(self) -> tuple[str, str, str]:
        from ..shape import HARNESSES
        from ..source import root

        said, failed = [], []
        for harness in HARNESSES:
            try:
                where = root(harness, self.env)
            except Exception as exc:  # noqa: BLE001 - the row reports it
                failed.append(f"{harness}: {exc}")
                continue
            path = os.fspath(where)
            if os.path.isdir(path):
                try:
                    os.listdir(path)
                except OSError as exc:
                    failed.append(f"{harness} {path}: {exc.strerror or exc}")
                    continue
            said.append(f"{harness} {path}" + ("" if os.path.exists(path) else " (absent)"))
        if failed:
            return ("roots", "warn", "can't be listed: " + "; ".join(failed))
        return ("roots", "ok", "; ".join(said))

    def _call(self, work):
        """A tool's call: `work(args, log)` under the provider's `Limit`, then its
        call-log fields, which `work` puts in `log`."""
        async def call(args, ctx):
            log: dict[str, Any] = {}
            result = await self.limit.run(functools.partial(work, dict(args), log))
            ctx.log(refusal=result.refusal, **reading.logged(result, log))
            return result
        return call

    def provider(self):
        api = self.api
        tools = [api.Tool("list_sessions", tools_mod.LIST_DESCRIPTION, tools_mod.LIST_INPUT,
                          self._call(self.lister), output_schema=tools_mod.LIST_OUTPUT,
                          annotations=dict(tools_mod.ANNOTATIONS),
                          title="List sessions"),
                 api.Tool("read_session", reading.READ_DESCRIPTION, reading.READ_INPUT,
                          self._call(self.reader), output_schema=reading.READ_OUTPUT,
                          annotations=dict(tools_mod.ANNOTATIONS),
                          title="Read a session")]
        return api.Provider(tools=tuple(tools), start=self.start, stop=self.stop,
                            status=self.status)


def build(api=None, **kw):
    """A provider over the interface `api` (the host's, by default):
    `(Sessions, Provider)`."""
    sessions = Sessions(api or _interface(), **kw)
    return sessions, sessions.provider()


SESSIONS, PROVIDER = build()

__all__ = ["INTERFACE", "MODULE", "PROVIDER", "SESSIONS", "Sessions", "build"]


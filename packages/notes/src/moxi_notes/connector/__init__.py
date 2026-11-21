"""notes' provider for the connector, the public MCP host: notes' read tools in the Claude
apps. Offered through one entry point:

    [project.entry-points."moxi.connector.providers"]
    notes = "moxi_notes.connector:PROVIDER"

Only the host loads this package, and nothing else in notes imports it. It uses nothing of
the host but its interface 1, `moxi_connector.provider`. The host comes with the
`connector` extra, so this package reaches it only through
`moxi.modules.load("connector", interface=1)` (`_interface`), and no import statement in
notes names the host's package. When that load fails, importing this package raises
`ImportError` with the reason.

The tools read only the notes root, notes' own `root` setting, under the path rules of
`pathrules`, and every text they return goes through `moxi_secrets.chat_redact` first.

* `list_notes` and `read_note` (`tools`), run under the host's `Limit`.
* `start(env)` checks the root and the chat redaction, and never raises: a tool refuses
  per call, and the status rows say why.
* `status()` gives two rows: `root`, the notes root or why it can't be read, and
  `redaction`, whether every chat set runs and on which engine.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Mapping

from moxi import modules

from . import pathrules
from . import tools as tools_mod

#: The host's module name and the interface this provider is written against.
MODULE = "connector"
INTERFACE = 1


def _interface():
    """The host's provider interface, `moxi.modules.load("connector", interface=1).api`.
    A host that is missing, broken or lacks interface 1 raises `ImportError`."""
    found = modules.load(MODULE, interface=INTERFACE)
    if not found.ok or found.api is None:
        raise ImportError(f"notes' connector provider needs the connector module, "
                          f"interface {INTERFACE}: {found.reason or found.state}")
    return found.api


def redaction_problem() -> str | None:
    """Why the chat redaction can't run, or None when every chat set runs: secrets must
    load and redact an empty text to an empty text."""
    try:
        out = pathrules.chat_redact("")
    except pathrules.RedactionOff as exc:
        return str(exc)
    if out != "":
        return "secrets couldn't scan an empty text"
    return None


def _engine() -> str:
    """The engine secrets runs on: `RE2`, or `fallback`."""
    try:
        from moxi_secrets import engine

        return "fallback" if engine.re2_module() is None else "RE2"
    except Exception:  # noqa: BLE001 - shown, never raised
        return "unknown"


class Notes:
    """The provider's state for one connector process: the last verdict on the chat
    redaction, which every call refreshes."""

    def __init__(self, api):
        self.api = api
        self.redaction: str | None = None
        self.started = False
        #: At most two calls at once, a third refused `busy` after 5 s, each ended at 20 s.
        self.limit = api.Limit()
        self.tools = tools_mod.Tools(result=api.Result,
                                     check_redaction=self.check_redaction)

    def check_redaction(self) -> str | None:
        """Run the chat redaction's check now and keep its verdict for `status`."""
        self.redaction = redaction_problem()
        return self.redaction

    async def start(self, env: Mapping[str, str]) -> None:
        import asyncio

        try:
            await asyncio.to_thread(self.check_redaction)
        except Exception as exc:  # noqa: BLE001 - a start never raises; the row says why
            self.redaction = f"{type(exc).__name__}: {exc}"
        self.started = True

    async def stop(self) -> None:
        return None

    def status(self):
        row = self.api.StatusRow
        problem = pathrules.root_problem()
        rows = [row("root", "fail", f"{problem}; every tool refuses") if problem
                else row("root", "ok", str(_root_shown()))]
        if not self.started:
            rows.append(row("redaction", "warn", "not checked yet: the provider hasn't "
                                                 "started"))
        elif self.redaction is not None:
            rows.append(row("redaction", "fail",
                            f"a chat set can't run ({self.redaction}); every tool refuses"))
        elif _engine() == "fallback":
            rows.append(row("redaction", "warn",
                            "the chat sets load, on the fallback engine: a long text may "
                            "come back unscanned and is then refused"))
        else:
            rows.append(row("redaction", "ok", f"the chat sets load, on {_engine()}"))
        return rows

    def tool(self, name: str):
        """The coroutine the host calls for the tool `name`: its work under the limit,
        then its call-log fields."""
        work = getattr(self.tools, name)

        async def call(args, ctx):
            started, fields = time.monotonic(), {}
            result = await self.limit.run(functools.partial(work, dict(args), fields,
                                                            started))
            if result.refusal in ("busy", "timeout"):
                ctx.log(refusal=result.refusal)     # the work may still be filling fields
            else:
                ctx.log(**fields, refusal=result.refusal)
            return result

        call.__name__ = name
        return call

    def provider(self):
        api = self.api
        schemas = tools_mod.inputs()
        return api.Provider(
            tools=tuple(api.Tool(name, tools_mod.DESCRIPTIONS[name], schemas[name],
                                 self.tool(name), output_schema=tools_mod.OUTPUTS[name],
                                 annotations=tools_mod.ANNOTATIONS[name],
                                 title=tools_mod.TITLES[name])
                        for name in tools_mod.NAMES),
            start=self.start, stop=self.stop, status=self.status)


def _root_shown() -> str:
    from .. import api

    return str(api.root())


def build(api=None):
    """A provider over the interface `api` (the host's, by default): `(Notes, Provider)`."""
    notes = Notes(api or _interface())
    return notes, notes.provider()


NOTES, PROVIDER = build()

__all__ = ["INTERFACE", "MODULE", "NOTES", "Notes", "PROVIDER", "build",
           "redaction_problem"]

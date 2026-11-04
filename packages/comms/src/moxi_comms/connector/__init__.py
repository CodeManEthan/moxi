"""comms' provider for the connector, the public MCP host: comms' four tools in the Claude
apps, the identity they send and receive as, and the mint gate for the host's sign-in
codes. Offered through one entry point:

    [project.entry-points."moxi.connector.providers"]
    comms = "moxi_comms.connector:PROVIDER"

Only the host loads this package, and nothing else in comms imports it. It uses nothing
of the host but its interface 1, `moxi_connector.provider`, what a provider builds on. The
host comes with the `connector` extra, an optional module, so this package reaches it only
through `moxi.modules.load("connector", interface=1)` (`_interface`), and no import
statement in comms names the host's package. When that load fails, importing this package
raises `ImportError` with the reason.

* `start(env)` reads `[connector]` from comms' config, provisions the identity as a
  standing name when it is missing, and binds it as the adapter kind `connector` (`link`).
* `stop()` closes the binding; the daemon puts the mail it held back to waiting.
* `status()` says whether the identity is bound, and names the holder when it isn't.
* `gate` is comms' mint gate (`gate`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from moxi import modules

from .. import config as config_mod
from . import gate as gate_mod
from . import tools as tools_mod
from .link import Link

log = logging.getLogger("comms.connector")

#: The host's module name and the interface this provider is written against.
MODULE = "connector"
INTERFACE = 1


def _interface():
    """The host's provider interface, `moxi.modules.load("connector", interface=1).api`.
    Only the host loads this package, so the host is installed whenever this runs; a
    host that is missing, broken or lacks interface 1 raises `ImportError`."""
    found = modules.load(MODULE, interface=INTERFACE)
    if not found.ok or found.api is None:
        raise ImportError(f"comms' connector provider needs the connector module, "
                          f"interface {INTERFACE}: {found.reason or found.state}")
    return found.api


class Comms:
    """The provider's state for one connector process: the config read at start, the
    link and the tools. `make_link` builds the link (a test hands in its own roots)."""

    def __init__(self, api, *, make_link=None, record=None):
        self.api = api
        self.make_link = make_link or Link
        self.record = record
        self.link: Link | None = None
        self.tools: tools_mod.Tools | None = None
        self.config: config_mod.ConnectorConfig | None = None

    async def start(self, env: Mapping[str, str]) -> None:
        import asyncio

        cfg = config_mod.load(dict(env))
        self.config = cfg.connector
        link = self.make_link(cfg.connector.identity, operator=cfg.provision.operator)
        self.link = link
        self.tools = tools_mod.Tools(
            link, result=self.api.Result, refused=self.api.Result.refused,
            wait_default=cfg.connector.wait_default, wait_max=cfg.connector.wait_max,
            record=self.record, env=dict(env))
        await asyncio.to_thread(link.start)

    async def stop(self) -> None:
        import asyncio

        if self.link is not None:
            await asyncio.to_thread(self.link.stop)

    def status(self):
        if self.link is None:
            return [self.api.StatusRow("identity", "warn", "not started")]
        return [self.api.StatusRow(*row) for row in self.link.status()]

    def tool(self, name: str):
        async def call(args, ctx):
            if self.tools is None:
                return self.api.Result.refused("offline", tools_mod.OFFLINE_TEXT)
            return await getattr(self.tools, name)(args, ctx)
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
            start=self.start, stop=self.stop, status=self.status,
            gate=api.Gate(peer=gate_mod.peer, shell=gate_mod.shell))


def build(api=None, **kw):
    """A provider over the interface `api` (the host's, by default): `(Comms, Provider)`."""
    comms = Comms(api or _interface(), **kw)
    return comms, comms.provider()


COMMS, PROVIDER = build()

__all__ = ["COMMS", "Comms", "INTERFACE", "MODULE", "PROVIDER", "build"]

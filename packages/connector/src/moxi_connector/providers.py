"""Loading the providers of `moxi.connector.providers`, running their start and stop, and
the tool table the door serves.

`load()` reads the group through `moxi.modules.extensions` and imports each entry's
target, and nothing else of the providing module. An entry is refused, and named in the
providers' status, when its name is not the module of the distribution that declares it,
when its target can't be imported, or when it is not a `Provider`.

`Registry` holds what loaded. `start(env)` runs every provider's start, each bounded by
`START_WAIT`; a provider whose start raises or takes longer loads no tools and gets a
`fail` row, and the others serve. `tools()` is the table the door serves: every tool of
every started provider, less each name two providers declare, which is refused for both
and named in a `fail` row. The table is data built from the providers, so a later rule
(a per-tool allowlist, a per-provider switch) can filter it without touching the provider
interface.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from moxi import modules, names

from .provider import GROUP, LEVELS, Provider, StatusRow, Tool

log = logging.getLogger("moxi.connector.providers")

#: Seconds a provider's start may take before it counts as failed.
START_WAIT = 30.0
#: Seconds a provider's stop may take before the process goes on without it.
STOP_WAIT = 5.0


@dataclass
class Loaded:
    """One provider entry and what became of it. `state` is `loaded` (not yet started),
    `started`, `failed` (its start raised or timed out) or `refused` (the entry is bad);
    `why` says why for the last two."""

    module: str
    distribution: str
    provider: Provider | None
    state: str
    why: str | None = None


def _dist_module(distribution: str) -> str | None:
    """The module a distribution named `moxi-<module>` holds, else None."""
    normal = re.sub(r"[-_.]+", "-", distribution).lower()
    prefix = f"{names.NAMESPACE}-"
    if not normal.startswith(prefix):
        return None
    module = normal[len(prefix):]
    return module if names.is_module_name(module) else None


def _import(target: str) -> object:
    module_name, _, attr = target.partition(":")
    module = importlib.import_module(module_name.strip())
    value: object = module
    for part in attr.strip().split(".") if attr.strip() else ():
        value = getattr(value, part)
    return value


def load() -> list[Loaded]:
    """Every entry of `moxi.connector.providers`, imported or refused. Never raises."""
    out: list[Loaded] = []
    for ext in modules.extensions(GROUP):
        owner = _dist_module(ext.distribution)
        if owner is None or ext.name != owner:
            out.append(Loaded(ext.name, ext.distribution, None, "refused",
                              f"its name {ext.name!r} is not the module of "
                              f"{ext.distribution}"))
            continue
        try:
            value = _import(ext.target)
        except Exception as e:  # noqa: BLE001 - a broken provider is a row, not a crash
            log.exception("the provider %s couldn't be imported", ext.name)
            out.append(Loaded(ext.name, ext.distribution, None, "refused",
                              f"{ext.target} couldn't be imported: {type(e).__name__}"))
            continue
        if not isinstance(value, Provider):
            out.append(Loaded(ext.name, ext.distribution, None, "refused",
                              f"{ext.target} is not a moxi_connector.provider.Provider"))
            continue
        out.append(Loaded(ext.name, ext.distribution, value, "loaded"))
    return out


@dataclass
class Registry:
    """The providers this process loaded, and the tools it serves from them."""

    entries: list[Loaded] = field(default_factory=list)

    @classmethod
    def from_group(cls) -> Registry:
        return cls(load())

    @classmethod
    def of(cls, providers: Mapping[str, Provider]) -> Registry:
        """A registry of `providers` by module name, as if each had loaded (tests)."""
        return cls([Loaded(name, f"moxi-{name}", p, "loaded") for name, p in providers.items()])

    async def start(self, env: Mapping[str, str], *, wait: float = START_WAIT) -> None:
        """Run each loaded provider's start once, in name order."""
        for entry in self.entries:
            if entry.state != "loaded" or entry.provider is None:
                continue
            try:
                await asyncio.wait_for(entry.provider.start(dict(env)), timeout=wait)
            except TimeoutError:
                entry.state, entry.why = "failed", f"its start took longer than {wait:g} s"
                log.error("the provider %s didn't start within %g s", entry.module, wait)
            except Exception as e:  # noqa: BLE001 - a provider's fault stays its own
                entry.state, entry.why = "failed", f"its start failed: {type(e).__name__}"
                log.exception("the provider %s failed to start", entry.module)
            else:
                entry.state = "started"

    async def stop(self, *, wait: float = STOP_WAIT) -> None:
        """Run each started provider's stop once, in reverse order. Never raises."""
        for entry in reversed(self.entries):
            if entry.state != "started" or entry.provider is None:
                continue
            entry.state = "stopped"
            try:
                await asyncio.wait_for(entry.provider.stop(), timeout=wait)
            except TimeoutError:
                log.warning("the provider %s didn't stop within %g s", entry.module, wait)
            except Exception:  # noqa: BLE001 - the process's stop goes on
                log.exception("the provider %s failed while stopping", entry.module)

    def clashes(self) -> dict[str, list[str]]:
        """Each tool name more than one started provider declares, with those providers."""
        by_name: dict[str, list[str]] = {}
        for entry in self.entries:
            if entry.state != "started" or entry.provider is None:
                continue
            for name in {t.name for t in entry.provider.tools}:
                by_name.setdefault(name, []).append(entry.module)
        return {name: mods for name, mods in by_name.items() if len(mods) > 1}

    def tools(self) -> dict[str, tuple[str, Tool]]:
        """The tools served: name to (provider module, tool), in provider and tool order,
        every clashing name left out."""
        clashing = self.clashes()
        out: dict[str, tuple[str, Tool]] = {}
        for entry in self.entries:
            if entry.state != "started" or entry.provider is None:
                continue
            for tool in entry.provider.tools:
                if tool.name not in clashing and tool.name not in out:
                    out[tool.name] = (entry.module, tool)
        return out

    def gates(self) -> list[tuple[str, object]]:
        """Each started provider's mint gate, with its module."""
        return [(e.module, e.provider.gate) for e in self.entries
                if e.state == "started" and e.provider is not None
                and e.provider.gate is not None]

    def status_rows(self) -> list[StatusRow]:
        """The providers' own rows: one per entry that failed or was refused, one per
        clashing tool name, then each started provider's `status()`, its names prefixed
        with the module. A `status()` that raises is a `fail` row of its own."""
        rows: list[StatusRow] = []
        for entry in self.entries:
            if entry.state in ("failed", "refused"):
                rows.append(StatusRow(entry.module, "fail", entry.why or entry.state))
        for name, mods in sorted(self.clashes().items()):
            rows.append(StatusRow(f"tool {name}", "fail",
                                  f"declared by {' and '.join(mods)}; served by neither"))
        for entry in self.entries:
            if entry.state != "started" or entry.provider is None:
                continue
            try:
                for row in entry.provider.status():
                    row = StatusRow(*row)
                    level = row.level if row.level in LEVELS else "fail"
                    rows.append(StatusRow(f"{entry.module} {row.name}", level, str(row.text)))
            except Exception as e:  # noqa: BLE001
                rows.append(StatusRow(entry.module, "fail",
                                      f"its status failed: {type(e).__name__}"))
        return rows


__all__ = ["Loaded", "Registry", "START_WAIT", "STOP_WAIT", "load"]

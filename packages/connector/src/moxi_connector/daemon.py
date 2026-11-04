"""`moxi-connector-server`: the connector's daemon, the public MCP door.

The core writes it as a unit behind the `enabled` switch, with a socket unit that holds
`127.0.0.1:<port>` while the process restarts. Started by hand with the switch off, it
refuses and exits 3.

What one run does, in order:

1. takes its listening socket (`listen.take`): the socket unit's, else its own bound on
   `port`. A port it can't have stops it, with a doctor record naming why;
2. loads every provider of `moxi.connector.providers` and runs each start (`Registry`);
3. reads the origin and builds the app from it (`origin.read`, `app.build_app`), then
   serves it (`server.Endpoint`);
4. every `origin.POLL` seconds reads the origin again and swaps the app when the swap
   rule says so (`origin.Watch`), and flushes the call log;
5. at SIGTERM, SIGINT or SIGHUP: stops listening, stops the apps, reads the switch again
   and tells the sign-in (`SignIn.stopping(switch_on)`), stops the providers, closes the
   call log.

`Daemon` is that run without the process around it: tests drive it on their own loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections.abc import Callable
from importlib import metadata

from moxi import aio, doctor
from moxi import exit as codes

from . import calllog, listen
from . import origin as origins
from . import settings as connector_settings
from .app import Front, Host, build_app
from .providers import Registry
from .server import Endpoint
from .signin import Closed

log = logging.getLogger("moxi.connector")

MODULE = "connector"
#: The doctor record a run leaves when it can't listen.
LISTEN_RECORD = "listen"


class Daemon:
    """The door on one listening socket, run on the caller's loop.

    `read_origin()` gives O now (blocking: it reads the config file and tunnel's record);
    `switch()` the daemon switch now."""

    def __init__(self, sock, host: Host, *,
                 read_origin: Callable[[], origins.Origin | None],
                 switch: Callable[[], bool], poll: float = origins.POLL,
                 env: dict[str, str] | None = None):
        self.sock = sock
        self.host = host
        self.read_origin = read_origin
        self.switch = switch
        self.poll = poll
        self.env = dict(os.environ if env is None else env)
        self.front: Front | None = None
        self.endpoint: Endpoint | None = None
        self.watch: origins.Watch | None = None
        self._poller: asyncio.Task | None = None

    async def start(self) -> None:
        await self.host.registry.start(self.env)
        found = await _blocking(self.read_origin)
        self.watch = origins.Watch(found)
        app = build_app(found, self.host)
        await app.start()
        self.front = Front(app, self.host)
        self.endpoint = Endpoint(self.sock, self.front)
        await self.endpoint.start()
        log.info("the connector listens on 127.0.0.1:%d, origin %s", self.endpoint.port,
                 found.url if found else "none")
        self._poller = asyncio.get_running_loop().create_task(self._poll(),
                                                              name="connector-origin")

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(self.poll)
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - one bad read never stops the door
                log.exception("the origin couldn't be read")

    async def tick(self) -> None:
        """One read of the origin, and the swap it calls for; then the call log's flush."""
        assert self.watch is not None and self.front is not None
        found = await _blocking(self.read_origin)
        verdict = self.watch.see(found)
        if verdict is not None:
            new = None if verdict == origins.Watch.NONE else verdict
            old = self.front.origin
            await self.front.swap(build_app(new, self.host))
            log.info("the connector's origin is now %s", new.url if new else "none")
            await _blocking(calllog.event, self.host.calllog, "origin-changed",
                            old=old.url if old else None, new=new.url if new else None)
        await _blocking(self.host.calllog.flush)

    async def stop(self) -> None:
        if self._poller is not None:
            self._poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poller
        if self.endpoint is not None:
            await self.endpoint.stop()
        if self.front is not None:
            await self.front.close()
        try:
            switch_on = bool(await _blocking(self.switch))
        except Exception:  # noqa: BLE001 - a switch that can't be read reads off
            log.exception("the switch couldn't be read at the stop; it reads off")
            switch_on = False
        try:
            await self.host.signin.stopping(switch_on)
        except Exception:  # noqa: BLE001
            log.exception("the sign-in failed at the stop")
        await self.host.registry.stop()
        with contextlib.suppress(Exception):
            await _blocking(self.host.calllog.close)


async def _blocking(fn, *args, **kwargs):
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*args, **kwargs))


def _version() -> str:
    try:
        return metadata.version("moxi-connector")
    except metadata.PackageNotFoundError:
        return ""


def _read_origin() -> origins.Origin | None:
    return origins.read(connector_settings.load()["public_url"])


async def _serve(daemon: Daemon) -> int:
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stopping.set)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - Windows
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stopping.set))
    try:
        await daemon.start()
    except Exception as e:
        log.error("the connector couldn't start: %s", e)
        await daemon.stop()
        return codes.ERROR
    await stopping.wait()
    await daemon.stop()
    return codes.OK


def main(argv: list[str] | None = None) -> int:
    """The daemon's entry point."""
    import moxi.log

    moxi.log.setup(MODULE, "info")
    try:
        values = connector_settings.load()
    except Exception as e:  # noqa: BLE001 - a config the door can't read stops it
        print(f"moxi-connector-server: {e}", file=sys.stderr)
        return codes.ERROR
    if not values["enabled"]:
        print("moxi-connector-server: the connector is off; moxi connector on turns it on",
              file=sys.stderr)
        return codes.REFUSED
    taken = listen.take(values["port"])
    if taken.sock is None:
        log.error("the connector can't listen (%s): %s", taken.why, taken.detail)
        with contextlib.suppress(Exception):
            doctor.record(MODULE, f"the door couldn't listen ({taken.why}): {taken.detail}",
                          key=LISTEN_RECORD, fix="moxi config set connector port <free port>")
        return codes.ERROR
    with contextlib.suppress(Exception):
        doctor.clear(MODULE, LISTEN_RECORD)
    host = Host(Registry.from_group(), signin=Closed(), calllog=calllog.MemoryLog(),
                redirect_uris=tuple(values["redirect_uris"]), version=_version())
    daemon = Daemon(taken.sock, host, read_origin=_read_origin,
                    switch=connector_settings.enabled)
    return aio.run(lambda: _serve(daemon))


__all__ = ["Daemon", "LISTEN_RECORD", "main"]

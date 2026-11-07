"""The gateway's listener: a second listener of the shell process, on the gateway endpoint.

`Gateway` lives inside the shell and shares its one event loop. Every 2 s its tick reads the
switch (`gateway.enabled`), whether the `gateway` extra is installed, and the tunnel's
address for `ui/gateway`, and from them:

- **takes or lets go of the hold.** The endpoint is held while the switch is on or the
  address is `applying`, `on` or `removing`, whether or not the extra is installed, and let
  go only once the address is gone and the switch is off. A shell whose own environment
  moves its runtime directory takes no hold at all. While the record can't be read the hold
  stays as it was; a shell that starts that way follows the switch alone until a read works;
- **builds or drops the profile**, by the address rule (`address.step`);
- **mounts or unmounts the phone's acts** (`mount.decide`), rewriting `gateway.acts`.

While held, the endpoint serves the gateway's request path when the gateway is on, and the
off answer (a 503, `no-store`, plain text, running no code) at every other moment. The
listener runs loopback's `serve` with `signals=False`, so opening and closing it never
changes the process's signal handling: the shell's own `serve` stays the one signal owner,
and the shell's stop closes this listener with it.

A hold the shell can't take (a TCP port another program holds, or a socket path that fails
its check, at the bind or at a tick) removes the `ui/gateway` tunnel entry, so the provider
stops forwarding to whatever holds the endpoint, and writes a `held` audit row. A removal
that fails is tried again at the next tick.

What later parts plug in comes through `Parts`: the gateway's route table, its session
callable, its router, and the hooks that end sessions, mark devices stale and write audit
rows. Its defaults admit nothing, so the gateway is whole, if empty, on its own.
"""

from __future__ import annotations

import asyncio
import errno
import importlib.util
import logging
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moxi.paths import Layout
from moxi_ui.loopback.api import (
    Entry,
    NotPrivate,
    Profile,
    RequestView,
    Table,
    bind_tcp,
    bind_unix,
    gateway,
    guard,
    response_headers,
    serve,
)

from .. import config
from ..router import worker_layer
from . import endpoint, provider
from .address import OFF, REFUSED, Kept, step
from .mount import ACTS_FILE, Mount, decide, write_acts

__all__ = ["LIVE", "OFF_BODY", "TICK_S", "Gateway", "Parts", "Reading", "TunnelRead",
           "extra_installed", "read_tunnel", "remove_entry"]

_log = logging.getLogger("moxi.ui.gateway")

#: Seconds between two ticks.
TICK_S = 2.0
#: The address states in which the gateway serves its request path and holds the endpoint.
LIVE = frozenset({"applying", "on", "removing"})
#: The off answer's body, which the phone's worker shows as the offline state.
OFF_BODY = b"Moxi's gateway is off.\n"
#: The default most bytes of an unfinished request head; the shell passes its own.
_HEAD_LIMIT = 4 * 1024 * 1024
#: A bind refused because another program holds the port.
_IN_USE = frozenset(code for code in (
    errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", None), getattr(errno, "WSAEACCES", None),
) if code is not None)

ASGIApp = Callable[..., Any]


def _empty_table(mount: Mount) -> Table:
    return Table()


def _admit_none(view: RequestView, entry: Entry | None) -> bool:
    return False


async def _not_found(scope: dict[str, Any], receive: Callable[..., Any],
                     send: Callable[..., Any]) -> None:
    await _answer(send, 404)


def _nothing(*args: Any) -> None:
    return None


def _no_refusals() -> Mapping[str, str]:
    return {}


def _same(inner: Any, profile: Any) -> Any:
    return inner


@dataclass
class Parts:
    """What the gateway's later parts plug in.

    `table(mount)` gives the gateway's route table for the acts' mount (a fresh `Table`,
    which the profile freezes); `session` is gate 4 and `fallback` the navigation fallback,
    as on loopback's `gateway` profile; `router` answers what the guard admits.
    `end_sessions()` ends every gateway session; `stale(kept)` marks the devices of the
    address `kept` stale; `audit(code, fields)` writes one audit row (`provider`, `held`,
    `acts`). `tick()` runs once on every tick, off the event loop. `layer(inner, profile)`
    wraps the check (`guard` and what it guards) inside the provider check, where the
    audit sees every request and every refusal. `refusals()` gives the modules whose
    export the gateway refuses, module to why, for `gateway.acts`.
    """

    table: Callable[[Mount], Table] = _empty_table
    session: Callable[[RequestView, Entry | None], bool] = _admit_none
    fallback: Callable[[RequestView], Entry | None] | None = None
    router: ASGIApp = _not_found
    end_sessions: Callable[[], None] = _nothing
    stale: Callable[[Kept], None] = _nothing
    audit: Callable[[str, dict[str, Any]], None] = _nothing
    tick: Callable[[], None] = _nothing
    layer: Callable[[ASGIApp, Callable[[], Profile | None]], ASGIApp] = _same
    refusals: Callable[[], Mapping[str, str]] = _no_refusals


@dataclass(frozen=True)
class TunnelRead:
    """One read of the tunnel: the address (an `Address`, None, or `REFUSED`), whether the
    installed tunnel offers interface 2, and why not, or the record's failure."""

    address: Any
    current: bool
    reason: str | None = None


@dataclass(frozen=True)
class Reading:
    """What one tick reads."""

    switch: bool
    extra: bool
    tunnel: TunnelRead


def extra_installed() -> bool:
    """Whether the `gateway` extra is installed, looked up without importing it."""
    return importlib.util.find_spec("webauthn") is not None


def read_tunnel() -> TunnelRead:
    """The `ui/gateway` address through tunnel's interface 2; through interface 1 when the
    installed tunnel lacks 2 (so an entry it left still keeps the endpoint held)."""
    from moxi import modules

    found = modules.load("tunnel", interface=2)
    current = found.state == modules.PRESENT and found.api is not None
    reason = None if current else (found.reason or "tunnel isn't installed")
    if not current:
        found = modules.load("tunnel", interface=1)
        if found.state != modules.PRESENT or found.api is None:
            return TunnelRead(None, False, reason)
    try:
        return TunnelRead(found.api.address("ui", "gateway"), current, reason)
    except Exception as err:  # noqa: BLE001 - a record that can't be read is a row of its own
        return TunnelRead(REFUSED, current, str(err) or type(err).__name__)


def remove_entry() -> None:
    """Remove the `ui/gateway` tunnel entry, confirming its lines. Raises as tunnel does."""
    from moxi import modules

    found = modules.load("tunnel", interface=1)
    if found.state == modules.PRESENT and found.api is not None:
        found.api.remove("ui", "gateway", confirm=lambda lines: True)


async def _answer(send: Callable[..., Any], status: int, body: bytes = b"") -> None:
    extra = [("Content-Length", str(len(body)))]
    if body:
        extra.insert(0, ("Content-Type", "text/plain; charset=utf-8"))
    await send({"type": "http.response.start", "status": status,
                "headers": response_headers(extra=extra, policy="gateway")})
    await send({"type": "http.response.body", "body": body})


@dataclass
class _Held:
    sock: Any
    stop: asyncio.Event
    task: asyncio.Future[Any]


class Gateway:
    """The gateway's endpoint, its hold, its profile and its acts' mount, in the shell.

    `kind` is resolved from `system` and `endpoint.UNIX_PASSED` unless given. `settings`
    gives the ui's settings (`config.current`), `extra()` whether the extra is installed,
    `tunnel()` a `TunnelRead`, `find_uid(provider)` the provider's uid, `remove()` removes
    the tunnel entry, and `environ` is the environment the runtime-directory check reads.
    Tests pass their own; the shell passes none.
    """

    def __init__(self, layout: Layout | None = None, *, parts: Parts | None = None,
                 kind: str | None = None, system: str | None = None,
                 settings: Callable[[], Mapping[str, Any]] = config.current,
                 extra: Callable[[], bool] = extra_installed,
                 tunnel: Callable[[], TunnelRead] = read_tunnel,
                 find_uid: Callable[[str], int | None] = provider.provider_uid,
                 remove: Callable[[], None] = remove_entry,
                 environ: Mapping[str, str] | None = None, proc: str = "/proc",
                 head_limit: int = _HEAD_LIMIT, tick_s: float = TICK_S):
        self.layout = layout or Layout("ui")
        self.parts = parts or Parts()
        self.system = system or endpoint.system()
        self.kind = kind or endpoint.resolve_kind(self.system)
        self._settings, self._extra, self._tunnel = settings, extra, tunnel
        self._find_uid, self._remove, self._environ = find_uid, remove, environ
        self.proc, self.head_limit, self.tick_s = proc, head_limit, tick_s
        self.runtime_dir: Path = self.layout.runtime_dir(environ)
        self.path = os.path.join(self.runtime_dir, endpoint.SOCKET_FILE)
        #: Main's locator text when this shell's environment moves its runtime directory.
        self.moved: str | None = None
        self.port: int | None = None
        self.shell_port: int | None = None
        self.switch = False
        self.extra = False
        self.current = False
        #: Why the gateway is off on the tunnel's side, or the record's failure.
        self.tunnel_reason: str | None = None
        self.address: Any = None
        self.refused = False
        self.ever_read = False
        self.kept: Kept | None = None
        self.profile: Profile | None = None
        self.uid: int | None = None
        self.mount = Mount(False, "off")
        self._wrote: tuple[Any, ...] | None = None
        #: The text of the hold the shell couldn't take, or of the setting that stops it.
        self.problem: str | None = None
        self._port_problem: str | None = None
        self._held: _Held | None = None
        self._removing: asyncio.Future[Any] | None = None
        self._inner = worker_layer(self.parts.layer(
            guard(self.parts.router, lambda: self.profile), lambda: self.profile))

    # --- reading --------------------------------------------------------------------

    def start(self) -> None:
        """Read what is read once: the runtime-directory check and the two ports."""
        self.moved = endpoint.runtime_problem(self._environ)
        if self.moved:
            _log.warning("the gateway takes no hold: %s", self.moved)
            return
        try:
            settings = self._settings()
            self.port = _port(settings["gateway.port"], "gateway.port")
            self.shell_port = _port(settings["port"], "port")
        except Exception as err:  # noqa: BLE001 - said by the hold and the doctor
            self._port_problem = f"the gateway's port can't be read: {err}"

    @property
    def endpoint(self) -> str | int | None:
        """The endpoint the tunnel's target must name: the socket path, or the TCP port."""
        return self.path if self.kind == "unix" else self.port

    @property
    def holding(self) -> bool:
        """Whether the endpoint is held now."""
        return self._held is not None

    @property
    def on(self) -> bool:
        """Whether the endpoint serves the gateway's request path now (else the off answer):
        the switch on, the extra and tunnel's interface 2 there, a profile standing, and
        the address live (or the record unreadable while a profile stands)."""
        if not (self.switch and self.extra and self.current and self.profile is not None):
            return False
        return self.refused or (self.address is not None and self.address.state in LIVE)

    def _read(self) -> Reading:
        try:
            switch = self._settings()["gateway.enabled"] is True
        except Exception:  # noqa: BLE001 - an unreadable file keeps the last value
            _log.debug("the ui settings can't be read; the switch stays %s", self.switch,
                       exc_info=True)
            switch = self.switch
        return Reading(switch, bool(self._extra()), self._tunnel())

    # --- the tick -------------------------------------------------------------------

    async def run(self, should_exit: asyncio.Event) -> None:
        """Tick every `tick_s` until `should_exit` is set, then let go of the endpoint."""
        try:
            while not should_exit.is_set():
                try:
                    await self.tick()
                except Exception:  # noqa: BLE001 - one failed tick never ends the shell
                    _log.exception("the gateway's tick failed")
                try:
                    await asyncio.wait_for(should_exit.wait(), self.tick_s)
                except TimeoutError:
                    pass
        finally:
            await self.close()

    async def tick(self) -> None:
        """One tick: read, apply the address rule and the mount, then the hold, then the
        later parts' own tick."""
        if self.moved:
            return
        reading = await asyncio.to_thread(self._read)
        find = self.apply(reading)
        if find is None and self._uid_missing():
            # Not found at the build (the provider wasn't answering yet): every request
            # would be refused until the address changed, so look again at each tick.
            find = self.kept.provider if self.kept is not None else None
        if find is not None:
            self.uid = await asyncio.to_thread(self._find_uid, find)
            self._write_acts()
        await self._hold()
        await asyncio.to_thread(self.parts.tick)

    def apply(self, reading: Reading) -> str | None:
        """Apply one reading to the profile and the mount. Returns the provider whose uid
        is to be found again, or None."""
        self.switch, self.extra = reading.switch, reading.extra
        self.current, self.tunnel_reason = reading.tunnel.current, reading.tunnel.reason
        read = reading.tunnel.address
        self.refused = read == REFUSED
        if not self.refused:
            self.address, self.ever_read = read, True
        off = not (self.switch and self.extra and self.current)
        done = step(self.kept, OFF if off else read)
        if done.ends:
            self.parts.end_sessions()
        if done.stale is not None:
            self.parts.stale(done.stale)
        self.kept = done.kept
        if self.kept is None:
            self.profile, self.uid = None, None
        elif done.build or self.profile is None:
            self._build(self.mount)
        self._mount()
        return self.kept.provider if done.find_uid and self.kept is not None else None

    def _build(self, mount: Mount) -> None:
        assert self.kept is not None
        try:
            self.profile = gateway(self.parts.table(mount), session=self.parts.session,
                                   host=self.kept.host, port=self.kept.port,
                                   fallback=self.parts.fallback)
        except ValueError as err:
            _log.warning("the gateway's profile can't be built for %s: %s",
                         self.kept.host, err)
            self.kept, self.profile, self.uid = None, None, None

    def _mount(self) -> None:
        mount = decide(on=self.on, kind=self.kind, endpoint=self.endpoint,
                       address=self.address, refused=self.refused)
        if mount.mounted != self.mount.mounted and self.kept is not None:
            # The table holds the act paths only while the acts are mounted.
            self._build(mount)
            mount = decide(on=self.on, kind=self.kind, endpoint=self.endpoint,
                           address=self.address, refused=self.refused)
        changed = mount != self.mount
        self.mount = mount
        if changed:
            self.parts.audit("acts", mount.as_dict())
        self._write_acts()

    def _uid_missing(self) -> bool:
        return self.kept is not None and self.uid is None and provider.checks(self.system)

    @property
    def uid_found(self) -> bool | None:
        """Whether this shell has found the provider's uid: None where no check runs or no
        profile stands."""
        if self.kept is None or not provider.checks(self.system):
            return None
        return self.uid is not None

    def _write_acts(self) -> None:
        try:
            refusals = dict(self.parts.refusals())
        except Exception:  # noqa: BLE001 - the mount is still written without them
            _log.exception("the gateway's refused modules can't be read")
            refusals = {}
        wrote = (self.mount, self.uid_found, tuple(sorted(refusals.items())))
        if wrote != self._wrote:
            try:
                write_acts(self.runtime_dir, self.mount, uid_found=self.uid_found,
                           refusals=refusals)
                self._wrote = wrote
            except OSError:
                _log.exception("gateway.acts can't be written")

    def refresh(self) -> None:
        """Build the profile again from the current table (the shell's module snapshot
        changed), keeping the address, the sessions and the mount."""
        if self.kept is not None and self.profile is not None:
            self._build(self.mount)

    def acts_now(self) -> Mount:
        """The mount as the inputs stand now, for a prepare or a commit to check again."""
        return decide(on=self.on, kind=self.kind, endpoint=self.endpoint,
                      address=self.address, refused=self.refused)

    # --- the hold -------------------------------------------------------------------

    def wants_hold(self) -> bool:
        """Whether the endpoint should be held now."""
        if self.moved:
            return False
        if self.refused:
            return self.holding if self.ever_read else self.switch
        return self.switch or (self.address is not None and self.address.state in LIVE)

    async def _hold(self) -> None:
        want = self.wants_hold()
        if want and self._held is None:
            await self._take()
        elif want and self.kind == "unix":
            try:
                endpoint.check_socket_path(self.path)
            except endpoint.PathRefused as err:
                await self._let_go()
                await self._failed(str(err))
        elif not want and self._held is not None:
            await self._let_go()
        if not want:
            self.problem = None

    async def _take(self) -> None:
        try:
            sock = self._bind()
        except _Stopped as err:
            self.problem = str(err)
            return
        except (endpoint.PathRefused, NotPrivate) as err:
            await self._failed(str(err))
            return
        except OSError as err:
            if self.kind == "tcp" and (err.errno in _IN_USE
                                       or getattr(err, "winerror", None) in (10013, 10048)):
                text = (f"port {self.port} was held by another program; the tunnel entry "
                        f"was removed; free the port and run `moxi ui gateway on`")
            else:
                text = f"the gateway's endpoint can't be bound: {err}"
            await self._failed(text)
            return
        stop = asyncio.Event()
        task = asyncio.ensure_future(serve(
            self.app, [sock], should_exit=stop, head_limit=self.head_limit, signals=False,
            peer=self.kind == "unix"))
        self._held = _Held(sock, stop, task)
        self.problem = None
        if self.kind == "tcp":
            endpoint.write_hold(self.runtime_dir, self.port)
        _log.info("the gateway holds %s", self.path if self.kind == "unix"
                  else f"127.0.0.1:{self.port}")

    def _bind(self) -> Any:
        if self.kind == "unix":
            endpoint.check_socket_path(self.path)
            return bind_unix(self.path)
        if self.port is None:
            raise _Stopped(self._port_problem or "the gateway's port can't be read")
        if self.port == self.shell_port:
            raise _Stopped(f"gateway.port is {self.port}, the shell's own port; choose "
                           "another with `moxi config set ui gateway.port N`")
        return bind_tcp(self.port)

    async def _failed(self, text: str) -> None:
        if text != self.problem:
            _log.warning("the gateway can't hold its endpoint: %s", text)
            where = self.path if self.kind == "unix" else f"127.0.0.1:{self.port}"
            self.parts.audit("held", {"reason": text, "endpoint": where,
                                      "removed": None if self.address is None else "ui/gateway"})
        self.problem = text
        if self.address is not None and (self._removing is None or self._removing.done()):
            self._removing = asyncio.ensure_future(asyncio.to_thread(self._remove_quietly))
            await asyncio.shield(self._removing)

    def _remove_quietly(self) -> None:
        try:
            self._remove()
        except Exception:  # noqa: BLE001 - tried again at the next tick
            _log.warning("the ui/gateway tunnel entry can't be removed yet", exc_info=True)

    async def _let_go(self) -> None:
        held, self._held = self._held, None
        if held is None:
            return
        if self.kind == "tcp":
            endpoint.remove_hold(self.runtime_dir)
        held.stop.set()
        try:
            await held.task
        except Exception:  # noqa: BLE001 - the listener is closing either way
            _log.exception("the gateway's listener ended with an error")
        held.sock.close()
        if self.kind == "unix":
            _unlink_socket(self.path)
        _log.info("the gateway let go of its endpoint")

    async def close(self) -> None:
        """Let go of the endpoint and remove `gateway.acts`; the shell's stop calls it."""
        await self._let_go()
        try:
            (self.runtime_dir / ACTS_FILE).unlink()
        except OSError:
            pass

    # --- serving --------------------------------------------------------------------

    async def app(self, scope: dict[str, Any], receive: Callable[..., Any],
                  send: Callable[..., Any]) -> None:
        """The endpoint's ASGI app: the off answer, or the provider check and then the
        gateway's request path."""
        if scope.get("type") != "http":
            return
        if not self.on:
            await _answer(send, 503, OFF_BODY)
            return
        if provider.checks(self.system):
            peer = provider.peer_of(scope, self.kind, proc=self.proc)
            if self.uid is None or peer != self.uid:
                self.parts.audit("provider", {"path": scope.get("path"), "peer": peer})
                await _answer(send, 403)
                return
        await self._inner(scope, receive, send)


class _Stopped(Exception):
    """A setting keeps the shell from taking a TCP hold; nothing else holds the port."""


def _port(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError(f"ui's {key} is {value!r}, not a port from 1 to 65535")
    return value


def _unlink_socket(path: str) -> None:
    try:
        if stat.S_ISSOCK(os.lstat(path).st_mode):
            os.unlink(path)
    except OSError:
        pass


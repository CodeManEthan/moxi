"""A stand-in for the core's writer, `moxi.units`, for tunnel's one daemon.

Tests put it in place of the `units` that `moxi_tunnel.cloudflare` calls through, and in
place of the unit apply `moxi.config.set` runs after a write of a daemon switch, so no
test reaches a supervisor. The switch itself is the real setting in the test's config
file. The fake keeps one unit, `cloudflared`: whether it is installed, its run state, and
the text of `cloudflared.yml` it last started on. It records every call, with whether the
core lock was held through tunnel's locks then, and can be told to fail. Its records are
the core's public ones (`UnitChange`, `Applied`, `Acted`, `UnitState`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from moxi.units import Acted, Applied, UnitChange, UnitState

from moxi_tunnel import cloudflare, locks, settings

NAME = "cloudflared"


@dataclass
class FakeUnits:
    #: what `backend()` answers; None is a system with no supervisor.
    supervisor: str | None = "systemd"
    #: the unit's state while installed (`active`, `inactive`, `failed`); None: not
    #: installed.
    unit: str | None = None
    #: the text of cloudflared.yml the unit last started on.
    started_on: str | None = None
    #: plan refuses every unit as an editable install does.
    editable: bool = False
    #: acts that fail: "install", "remove", "restart".
    fail: set = field(default_factory=set)
    #: an exception a call raises, by call name, once: a crash at that point.
    crash: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    #: (call, core lock held) for every call that writes or acts.
    locked: list = field(default_factory=list)
    starts: int = 0
    #: (the switch's new value, cloudflared.yml's text then) at each write of the switch.
    switched: list = field(default_factory=list)

    # -- the calls tunnel makes -----------------------------------------------------------

    def backend(self):
        return self.supervisor

    def plan(self, module, *, only=None):
        self._call("plan", module, tuple(only or ()))
        entry = {"command": "moxi-tunnel-cloudflared", "switch": cloudflare.SWITCH}
        if self.supervisor is None:
            return [self._change("skip", "nobackend", "no supervisor", entry)]
        if self.editable:
            return [self._change("refuse", "editable", "moxi-tunnel is an editable install",
                                 entry)]
        if not settings.load()[cloudflare.SWITCH]:
            if self.unit is not None:
                return [self._change("remove", "switch", "switched off", entry)]
            return [self._change("skip", "switch", "switched off", entry)]
        if self.unit is None:
            return [self._change("add", None, "add", entry)]
        return [self._change("keep", None, "keep", entry)]

    def apply(self, changes, *, say=print, replace=False, restart=False, runner=None):
        self._call("apply", tuple(c.action for c in changes))
        out = []
        for change in changes:
            if change.action == "refuse":
                out.append(Applied(change, change.cause == "editable",
                                   (f"{change.unit}: {change.reason}",)))
            elif change.action in ("skip", "keep"):
                out.append(Applied(change, True, (f"{change.unit}: {change.reason}",)))
            elif change.action == "add":
                if "install" in self.fail:
                    out.append(Applied(change, False, (f"{change.unit}: Job failed",)))
                    continue
                self.unit = "active"
                self._start()
                out.append(Applied(change, True, (f"{change.unit} add",)))
            elif change.action == "remove":
                if "remove" in self.fail:
                    out.append(Applied(change, False, (f"{change.unit}: remove failed",)))
                    continue
                self.unit = None
                self.started_on = None
                out.append(Applied(change, True, (f"{change.unit} remove",)))
        return out

    def restart(self, module, names, *, foreign=False, runner=None):
        self._call("restart", module, tuple(names))
        if self.unit is None:
            return [Acted(module, NAME, "restart", False, ("not installed",))]
        if "restart" in self.fail:
            self.unit = "failed"
            return [Acted(module, NAME, "restart", False, ("cloudflared: start failed",))]
        self.unit = "active"
        self._start()
        return [Acted(module, NAME, "restart", True, ())]

    def status(self, modules=None, *, foreign=(), runner=None):
        self._call("status")
        backend = self.supervisor
        if backend is None:
            state = "nobackend"
        elif self.unit is None:
            state = "missing" if settings.load()[cloudflare.SWITCH] else "off"
        else:
            state = self.unit
        return [UnitState("tunnel", NAME, "daemon", backend, state)]

    def apply_switch(self, module, key, *, say=print, runner=None):
        """What `moxi.config.set` runs after a write of the switch."""
        on = settings.load()[cloudflare.SWITCH]
        self.switched.append((on, cloudflare.read_file().text))
        self._call("switch", module, key, on)
        return self.apply(self.plan(module, only=[NAME]))

    # -- helpers --------------------------------------------------------------------------

    def _call(self, name, *args):
        self.calls.append((name, *args))
        if name not in ("status", "plan"):
            self.locked.append((name, locks.holds_core()))
        raised = self.crash.pop(name, None)
        if raised is not None:
            raise raised

    def _change(self, action, cause, reason, entry):
        return UnitChange("tunnel", NAME, "daemon", action, cause, (), None, reason,
                          entry=entry)

    def _start(self):
        self.starts += 1
        self.started_on = cloudflare.read_file().text

    def acts(self):
        """The calls that wrote or acted, by name, in order."""
        return [c[0] for c in self.calls if c[0] not in ("status", "plan")]

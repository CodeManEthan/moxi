"""tunnel's doctor rows: `DOCTOR = "moxi_tunnel.doctor:rows"`.

The core calls `rows()` with no arguments, in a worker thread with ten seconds; a target
that overruns becomes one `fail` row with none of its detail. So one run sets one
deadline, `BUDGET` seconds from its start, and every wait in it is cut to what is left:

- the local rows come first: the record, the declarations, the Cloudflare rows (files,
  and the core's unit status, waited for at most `READ_WAIT`) and a connect to each
  recorded target's loopback port, each waiting at most `CONNECT_WAIT`;
- then the `tailscale` reads, each run once and shared by every row and by the entries'
  comparison: `version`, `status --json`, `serve status --json` and `debug prefs`, each
  waiting at most `READ_WAIT`;
- the first read that times out makes its row `fail`, "tailscale did not answer", with
  `tailscale status` to try by hand, and no further `tailscale` read runs: a hung
  tailscaled answers none;
- a row whose read was skipped, or not reached by the deadline, is `warn`, "not checked".

After the deadline only rows are built, so every row, with its detail, comes back inside
the core's ten seconds. The rows about Tailscale are `off` while no Tailscale target is
recorded, and run no `tailscale` command then; `moxi tunnel add` checks Tailscale's
readiness itself before it records the first one.

The waits and the clock are arguments, so a test can make them short.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from moxi.doctor import Row

from . import acts, api, cloudflare as cf_, declare, record
from . import tailscale as ts_
from .errors import ReadError, Refused, TunnelError
from .record import Entry, Record

MODULE = "tunnel"
#: Seconds one run may take, of the core's ten.
BUDGET = 8.0
#: Seconds each `tailscale` read may take.
READ_WAIT = 2.0
#: Seconds each connect to a target's loopback port may take.
CONNECT_WAIT = 0.5
#: The oldest Tailscale whose `serve` and `funnel` take the forms tunnel runs.
MIN_VERSION = (1, 52)
#: The first Tailscale whose tailscaled renews its certificates in the background.
RENEWAL_VERSION = (1, 102, 5)

#: The rows about Tailscale, in the order they are shown.
TAILSCALE_ROWS = ("tailscale CLI", "certificate renewal", "logged in", "HTTPS certificates",
                  "Funnel allowed", "may change serve config", "shields up")

_NO_ANSWER = "tailscale did not answer"
_TRY = "tailscale status"


def _ran_out(budget: float) -> str:
    return f"the doctor's {budget:g} s ran out"


@dataclass
class _Read:
    """One `tailscale` read's outcome: `ok` (with `value`), `error` (it failed at once,
    `text` says how), `timeout` (it did not answer in its wait) or `skipped` (not run,
    `text` says why)."""

    kind: str
    value: object = None
    text: str = ""


class _Reads:
    """The run's `tailscale` reads, made once each, in order, inside the deadline."""

    def __init__(self, ts: ts_.Tailscale, *, wait: float, deadline: float, budget: float,
                 clock: Callable[[], float]):
        self.ts = ts
        self.wait = wait
        self.deadline = deadline
        self.budget = budget
        self.clock = clock
        self.stopped: str | None = None
        self.got: dict[str, _Read] = {}

    def read(self, name: str, fn: Callable[[], object]) -> _Read:
        if self.stopped is not None:
            got = _Read("skipped", text=self.stopped)
        elif self.deadline - self.clock() <= 0:
            self.stopped = _ran_out(self.budget)
            got = _Read("skipped", text=self.stopped)
        else:
            cut = self.deadline - self.clock() < self.wait
            try:
                got = _Read("ok", fn())
            except ReadError as exc:
                if not exc.timeout:
                    got = _Read("error", text=exc.reason)
                elif cut:
                    # The deadline, not tailscale, cut this read short.
                    self.stopped = _ran_out(self.budget)
                    got = _Read("skipped", text=self.stopped)
                else:
                    self.stopped = _NO_ANSWER
                    got = _Read("timeout", text=f"tailscale did not answer in "
                                                f"{self.wait:g} s")
            except TunnelError as exc:
                got = _Read("error", text=exc.reason)
        self.got[name] = got
        return got


def _guard(name: str, fn: Callable[[], list[Row]]) -> list[Row]:
    """`fn()`'s rows, or one `fail` row naming `name` when it raised: one broken row never
    hides the others."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - the doctor reports, it never raises
        reason = exc.reason if isinstance(exc, TunnelError) else f"{type(exc).__name__}: {exc}"
        return [Row(MODULE, name, "fail", f"could not be checked: {reason}")]


def _public(entry: Entry) -> bool:
    """True for a Tailscale entry on Funnel: a public one, or the old Tailscale entry of a
    switch that has not settled, when that one was public."""
    old = entry if entry.provider == "tailscale" else record.old_tailscale(entry)
    return old is not None and old.reach == "public"


def rows(*, budget: float = BUDGET, read_wait: float = READ_WAIT,
         connect_wait: float = CONNECT_WAIT,
         clock: Callable[[], float] = time.monotonic) -> list[Row]:
    """Every row of tunnel's doctor, inside `budget` seconds of `clock`."""
    deadline = clock() + budget

    # -- the local rows ---------------------------------------------------------------
    rec, record_rows = _record_rows()
    decls, decl_rows = _declaration_rows()
    entries = dict(rec.entries) if rec is not None else {}
    answers = _answer_rows(entries, connect_wait=connect_wait, deadline=deadline,
                           budget=budget, clock=clock)
    cloudflare = _guard("cloudflare", lambda: [_setup_row(entries)])
    in_step = _guard("cloudflare in step",
                     lambda: [_in_step_row(wait=read_wait, deadline=deadline, budget=budget,
                                           clock=clock)])

    # -- the tailscale reads, then the rows that need them ----------------------------
    tailscale_keys = sorted(k for k, e in entries.items()
                            if e.provider == "tailscale" or record.old_tailscale(e))
    public = any(_public(e) for e in entries.values())
    reads = None
    if tailscale_keys:
        ts = ts_.Tailscale(deadline=deadline, wait=read_wait, clock=clock)
        reads = _read_tailscale(ts, public=public, wait=read_wait, deadline=deadline,
                                budget=budget, clock=clock)
        tailscale = _tailscale_rows(reads, public=public)
    else:
        tailscale = [Row(MODULE, name, "off", "no Tailscale target is recorded")
                     for name in TAILSCALE_ROWS]
    entry_rows = _entry_rows(rec, decls, tailscale_keys, reads) if rec is not None else []

    return [*record_rows, *decl_rows, *tailscale, *entry_rows, *answers, *cloudflare,
            *in_step]


# -- the record and the declarations -------------------------------------------------------

def _record_rows() -> tuple[Record | None, list[Row]]:
    try:
        rec = record.read()
    except Refused as exc:
        return None, [Row(MODULE, "record", "fail", exc.reason, exc.fix)]
    if rec.exists and not rec.writable:
        return rec, [Row(MODULE, "record", "fail",
                         f"{record.record_path()} has format {rec.format!r}, which this "
                         "version reads as far as it can and never writes",
                         "upgrade moxi-tunnel")]
    count = len(rec.entries)
    detail = ("nothing recorded yet" if count == 0 else
              f"{count} {'address' if count == 1 else 'addresses'} recorded")
    return rec, [Row(MODULE, "record", "ok", detail)]


def _declaration_rows() -> tuple[declare.Declarations, list[Row]]:
    try:
        decls = declare.read()
    except Exception as exc:  # noqa: BLE001 - the doctor reports, it never raises
        return (declare.Declarations(targets={}, bad=()),
                [Row(MODULE, "declarations", "fail", f"could not be read: {exc}")])
    return decls, [Row(MODULE, f"declaration {bad.entry}", "fail",
                       f"bad declaration from {bad.distribution}: {bad.reason}", bad.fix)
                   for bad in decls.bad]


# -- Cloudflare ----------------------------------------------------------------------------

def _in_step_row(*, wait: float, deadline: float, budget: float,
                 clock: Callable[[], float]) -> Row:
    """The `cloudflare in step` row, which reads the unit's install state from the core's
    `moxi.units.status`: a supervisor call with a limit of its own, longer than this run's.
    So it runs in a thread and is waited for at most `wait`, cut to what is left of the
    deadline; past that the row is `warn`, not checked."""
    name = "cloudflare in step"
    left = min(wait, deadline - clock())
    if left <= 0:
        return Row(MODULE, name, "warn", f"not checked: {_ran_out(budget)}")
    got: dict[str, object] = {}

    def read() -> None:
        try:
            got["row"] = cf_.in_step_row()
        except Exception as exc:  # noqa: BLE001 - handed to the caller's guard
            got["error"] = exc

    worker = threading.Thread(target=read, name="tunnel-doctor-unit-state", daemon=True)
    worker.start()
    worker.join(left)
    if worker.is_alive():
        return Row(MODULE, name, "warn", f"not checked: the cloudflared unit's state did not "
                   f"come back in {left:g} s", "moxi doctor tunnel, once the user's unit "
                   "manager answers")
    if "error" in got:
        raise got["error"]
    return got["row"]


def _setup_row(entries: Mapping[str, Entry]) -> Row:
    if not any(e.provider == "cloudflare" for e in entries.values()):
        return Row(MODULE, "cloudflare", "off", "no Cloudflare target is recorded")
    return cf_.setup_row()


# -- each target answers -------------------------------------------------------------------

def _answer_rows(entries: Mapping[str, Entry], *, connect_wait: float, deadline: float,
                 budget: float, clock: Callable[[], float]) -> list[Row]:
    out = []
    for key in sorted(entries):
        entry = entries[key]
        if not entry.known:
            continue
        name = f"{key} answers"
        module = key.partition("/")[0]
        where = f"127.0.0.1:{entry.target}"
        left = deadline - clock()
        if left <= 0:
            out.append(Row(MODULE, name, "warn", f"not checked: {_ran_out(budget)}"))
            continue
        try:
            with socket.create_connection(("127.0.0.1", entry.target),
                                          timeout=min(connect_wait, left)):
                pass
        except OSError:
            out.append(Row(MODULE, name, "warn",
                           f"nothing answers on {where}: {module} is not serving it, so "
                           f"the address answers 502", f"moxi doctor {module}"))
            continue
        out.append(Row(MODULE, name, "ok", f"{where} answers"))
    return out


# -- Tailscale -----------------------------------------------------------------------------

def _read_tailscale(ts: ts_.Tailscale, *, public: bool, wait: float, deadline: float,
                    budget: float, clock: Callable[[], float]) -> _Reads:
    reads = _Reads(ts, wait=wait, deadline=deadline, budget=budget, clock=clock)
    if ts.binary() is None:
        missing = ts._missing()
        reads.stopped = "no tailscale CLI"
        reads.got["binary"] = _Read("error", text=missing.reason, value=missing.fix)
        return reads
    reads.read("version", ts.version)
    reads.read("status", ts.status)
    reads.read("serve", ts.serve_config)
    if sys.platform.startswith("linux") or public:
        reads.read("prefs", ts.prefs)
    return reads


def _tailscale_rows(reads: _Reads, *, public: bool) -> list[Row]:
    got = reads.got
    if "binary" in got:
        miss = got["binary"]
        return [Row(MODULE, "tailscale CLI", "fail", miss.text, str(miss.value)),
                *(_off_row(name, public) or
                  Row(MODULE, name, "warn", "not checked: no tailscale CLI")
                  for name in TAILSCALE_ROWS[1:])]
    out = []
    version, status = got["version"], got["status"]
    prefs = got.get("prefs")

    # the CLI and its version
    if version.kind == "ok":
        text = str(version.value)
        number = ts_.version_tuple(text)
        if number is None or number < MIN_VERSION:
            out.append(Row(MODULE, "tailscale CLI", "fail",
                           f"tailscale {text} is older than "
                           f"{'.'.join(map(str, MIN_VERSION))}, whose serve and funnel "
                           "forms tunnel runs", f"update Tailscale: {ts_.INSTALL}"))
        else:
            out.append(Row(MODULE, "tailscale CLI", "ok",
                           f"tailscale {text} at {reads.ts.binary()}"))
        if number is not None and number >= RENEWAL_VERSION:
            out.append(Row(MODULE, "certificate renewal", "ok",
                           "tailscaled renews its certificates in the background"))
        else:
            out.append(Row(MODULE, "certificate renewal", "warn",
                           f"tailscale {text} does not renew its certificates in the "
                           f"background (from {'.'.join(map(str, RENEWAL_VERSION))} it "
                           "does), so an address can lapse", "update Tailscale"))
    else:
        out.append(_owner("tailscale CLI", version, "check the tailscale CLI"))
        out.append(_behind("certificate renewal", version, "tailscale version"))

    # logged in, HTTPS certificates, Funnel allowed: tailscale status
    if status.kind == "ok":
        out += _status_rows(reads.ts, status.value, public=public)
    else:
        out.append(_owner("logged in", status, "start Tailscale, then tailscale up"))
        out.append(_behind("HTTPS certificates", status, "tailscale status"))
        out.append(_off_row("Funnel allowed", public) or
                   _behind("Funnel allowed", status, "tailscale status"))

    # may change serve config, shields up: tailscale debug prefs
    out += _prefs_rows(prefs, public=public)
    return out


def _off(name: str, public: bool) -> str | None:
    """Why row `name` does not apply here, or None when it does."""
    if name in ("Funnel allowed", "shields up") and not public:
        return "no public Tailscale target is recorded"
    if name == "may change serve config" and not sys.platform.startswith("linux"):
        return "only Linux keeps an operator for the serve config"
    return None


def _off_row(name: str, public: bool) -> Row | None:
    why = _off(name, public)
    return Row(MODULE, name, "off", why) if why else None


def _owner(name: str, got: _Read, fix: str) -> Row:
    """The row a read belongs to: `fail` when the read failed or timed out, `warn` when
    it was skipped."""
    if got.kind == "timeout":
        return Row(MODULE, name, "fail", got.text, _TRY)
    if got.kind == "error":
        return Row(MODULE, name, "fail", f"tailscale could not be read: {got.text}", fix)
    return Row(MODULE, name, "warn", f"not checked: {got.text}")


def _behind(name: str, got: _Read, what: str) -> Row:
    """A row behind a read that gave nothing: `warn`, not checked."""
    if got.kind == "timeout":
        return Row(MODULE, name, "warn", f"not checked: {_NO_ANSWER}")
    if got.kind == "error":
        return Row(MODULE, name, "warn", f"not checked: {what} could not be read")
    return Row(MODULE, name, "warn", f"not checked: {got.text}")


def _status_rows(ts: ts_.Tailscale, status: object, *, public: bool) -> list[Row]:
    status = status if isinstance(status, dict) else {}
    out = []
    state = status.get("BackendState")
    if state == "Running":
        out.append(Row(MODULE, "logged in", "ok", "Tailscale is logged in and running"))
    else:
        out.append(Row(MODULE, "logged in", "fail",
                       f"Tailscale is not logged in and running (its state is {state!r})",
                       "tailscale up"))
    tailnet = status.get("CurrentTailnet")
    magic = isinstance(tailnet, dict) and tailnet.get("MagicDNSEnabled") is True
    domains = [d for d in status.get("CertDomains") or [] if isinstance(d, str)]
    caps = ts.capmap()
    missing = [what for what, ok in (("MagicDNS is off", magic),
                                     ("the machine has no certificate domain", bool(domains)),
                                     ("HTTPS is not in the machine's capabilities",
                                      "https" in caps)) if not ok]
    if missing:
        out.append(Row(MODULE, "HTTPS certificates", "fail", "; ".join(missing),
                       f"turn on MagicDNS and HTTPS certificates in the admin console, "
                       f"{ts_.ADMIN_DNS}"))
    else:
        out.append(Row(MODULE, "HTTPS certificates", "ok",
                       f"MagicDNS and HTTPS certificates are on ({', '.join(domains)})"))
    off = _off_row("Funnel allowed", public)
    if off is not None:
        out.append(off)
    elif "funnel" in caps and ts.funnel_ports() is not None:
        ports = ", ".join(map(str, ts.funnel_ports() or ()))
        out.append(Row(MODULE, "Funnel allowed", "ok",
                       f"Funnel is allowed{f' on ports {ports}' if ports else ''}"))
    else:
        out.append(Row(MODULE, "Funnel allowed", "fail",
                       "Funnel is not allowed for this machine",
                       f"add the funnel node attribute to the tailnet policy file, "
                       f"{ts_.ADMIN_POLICY}"))
    return out


def _prefs_rows(prefs: _Read | None, *, public: bool) -> list[Row]:
    out = []
    owned = False
    for name in ("may change serve config", "shields up"):
        off = _off_row(name, public)
        if off is not None:
            out.append(off)
            continue
        if prefs is None or prefs.kind == "skipped":
            out.append(Row(MODULE, name, "warn",
                           f"not checked: {prefs.text if prefs else 'not read'}"))
            continue
        if prefs.kind == "timeout":
            # The first row that reads the prefs owns the read that timed out.
            out.append(Row(MODULE, name, "warn", f"not checked: {_NO_ANSWER}") if owned
                       else Row(MODULE, name, "fail", prefs.text, _TRY))
            owned = True
            continue
        value = prefs.value if prefs.kind == "ok" else None
        if name == "may change serve config":
            out.append(_operator_row(value))
        else:
            out.append(_shields_row(value))
    return out


def _operator_row(prefs: object) -> Row:
    name = "may change serve config"
    if not isinstance(prefs, dict) or "OperatorUser" not in prefs:
        return Row(MODULE, name, "off", "tailscale debug prefs does not say who may")
    if ts_._is_root():
        return Row(MODULE, name, "ok", "tunnel runs as root")
    user = ts_._this_user()
    if prefs.get("OperatorUser") == user:
        return Row(MODULE, name, "ok", f"{user} is Tailscale's operator")
    return Row(MODULE, name, "fail",
               f"{user or 'this user'} is not Tailscale's operator, so it may not change "
               "the serve config", "sudo tailscale set --operator=$USER")


def _shields_row(prefs: object) -> Row:
    name = "shields up"
    if not isinstance(prefs, dict) or "ShieldsUp" not in prefs:
        return Row(MODULE, name, "off", "tailscale debug prefs does not say")
    if prefs.get("ShieldsUp") is True:
        return Row(MODULE, name, "fail", "shields are up, so Funnel can't take connections",
                   "tailscale set --shields-up=false")
    return Row(MODULE, name, "ok", "shields are down")


# -- each entry ----------------------------------------------------------------------------

class _NotChecked(Exception):
    def __init__(self, row: Row):
        super().__init__(row.detail)
        self.row = row


def _entry_rows(rec: Record, decls: declare.Declarations, tailscale_keys: list[str],
                reads: _Reads | None) -> list[Row]:
    ports_now = acts._ports_now(rec, decls)
    clash = declare.clashes(ports_now)
    bad = acts._bad_keys(decls)
    cloud = acts._Live()
    owner = tailscale_keys[0] if tailscale_keys else None
    out = []
    for key in sorted(rec.entries):
        entry = rec.entries[key]

        def live(e: Entry, key: str = key) -> str | None:
            if e.provider != "tailscale":
                return cloud(e)
            return _tailscale_live(e, reads, owner=key == owner, name=key)

        try:
            state = acts._state(key, entry, decls, bad, ports_now, clash, live)
        except _NotChecked as exc:
            out.append(exc.row)
            continue
        except Exception as exc:  # noqa: BLE001 - the doctor reports, it never raises
            out.append(Row(MODULE, key, "fail", f"could not be checked: {exc}"))
            continue
        if state.check == "ok":
            url = api.url(entry.host, entry.port)
            out.append(Row(MODULE, key, "ok", f"{url} ({entry.provider}) is live as recorded"))
        else:
            out.append(Row(MODULE, key, "fail", f"{state.check}: {state.detail}", state.fix))
    return out


def _tailscale_live(entry: Entry, reads: _Reads | None, *, owner: bool, name: str) -> str | None:
    if reads is None:
        raise _NotChecked(Row(MODULE, name, "warn", "not checked: tailscale was not read"))
    status = reads.got.get("status")
    serve = reads.got.get("serve")
    for got, what in ((status, "tailscale status"), (serve, "tailscale serve status")):
        if got is None:
            raise _NotChecked(Row(MODULE, name, "warn", "not checked: no tailscale CLI"))
        if got.kind == "ok":
            continue
        if got.kind == "timeout" and what == "tailscale serve status" and owner:
            raise _NotChecked(Row(MODULE, name, "fail", got.text, _TRY))
        raise _NotChecked(_behind(name, got, what))
    try:
        host = reads.ts.host()
    except ReadError as exc:
        return exc.reason
    return ts_.live_state(serve.value, entry, host)


__all__ = ["BUDGET", "READ_WAIT", "CONNECT_WAIT", "MIN_VERSION", "RENEWAL_VERSION",
           "TAILSCALE_ROWS", "rows"]

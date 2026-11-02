"""The Cloudflare provider: one named tunnel the user made, and Moxi's one config file for it.

The user creates the tunnel and its DNS routes outside Moxi, then sets two settings,
`cloudflare.tunnel_id` and `cloudflare.credentials` (the path to the tunnel's credentials
JSON). Tunnel keeps one `cloudflared.yml` in its state directory that covers every
Cloudflare target, and `cloudflared` runs on it as tunnel's one daemon, behind the switch
`cloudflare.enabled`.

The writer
----------

- The credential is referenced by path and never copied or opened: tunnel checks only that
  the file exists, is a regular file and belongs to the user.
- The file is written 0600 and atomically, through `moxi.files.atomic_write`.
- The ingress list has one `hostname` and `service: http://127.0.0.1:<target>` rule per
  target, then the catch-all `service: http_status:404`, last.
- It refuses a tunnel id that is not a UUID, a hostname that fails the label check (each
  label `[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?`, two labels at least, 253 characters
  at most) and a path that holds a control character.
- Every string value is written as a JSON string, which is a valid double-quoted YAML
  scalar, so a `#`, `: `, a leading quote or a Windows backslash in a path can't change
  the parse.
- `metrics: 127.0.0.1:<cloudflare.metrics_port>`, so the health check can read `/ready`.
- The first line is `# moxi-tunnel sha256:<hex>`, the digest of the rest of the file. A
  file whose rest does not hash to that line, or that has no such line, was edited by
  hand. A file whose line holds but whose text differs from what the record renders is
  one tunnel wrote for an earlier record (a crash, or a step that stopped part way).

The whole file is Moxi's. It is rendered from the record's Cloudflare set: the Cloudflare
entries in state `applying` or `on`, one rule each. While the record holds a Cloudflare
entry a newer tunnel wrote, whose rule this version can't read, the file is kept as it
is: the step refuses, and the acts that would change the set refuse at plan.

The step
--------

After any act that changes the Cloudflare set, the file, the switch, the record and the
unit's run state agree: while the set is not empty, the file is what the record renders,
the switch is on, and the unit is installed and was last started on the file's current
text; while it is empty, the switch is off, the unit is not installed and the file is
gone. The file is written before the switch turns the unit on, and the switch goes off
before the file goes. `step()` brings them into agreement from wherever they are, a crash
or a hand edit included. `record.commit` runs it whenever the set changes, and `repair`
runs it always. It needs the core lock, held through `locks.core`, since turning the
switch and restarting the unit plan and apply units.

When one of the core's unit acts fails, the step raises `StepFailed` with the core's text
once the record, the file and the switch agree; when the file can't be written or deleted
(its mode or owner changed by hand) it raises `StepFailed` with `file=True` before it
touches the switch. Which way the act then goes is the act's to decide.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import stat
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from moxi import config, files, units
from moxi.doctor import Row

from . import locks, record, settings
from .errors import Refused, TunnelError
from .provider import Problem
from .record import Entry, Record, normal_host

MODULE = "tunnel"
#: The daemon's name in `DAEMONS`, and its switch.
DAEMON = "cloudflared"
SWITCH = "cloudflare.enabled"
CONFIG_FILE = "cloudflared.yml"
#: The first line's prefix; the digest of the rest of the file follows it.
DIGEST_PREFIX = "# moxi-tunnel sha256:"
CATCH_ALL = "http_status:404"
#: The one outside port of every Cloudflare target.
PORT = 443
INSTALL = "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/"
#: Seconds the health check waits for `/ready`.
HEALTH_WAIT = 2.0
#: Unit states that mean the unit is installed.
INSTALLED = ("active", "inactive", "failed")

_LABEL = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_REPAIR = "moxi tunnel repair"


class StepFailed(TunnelError):
    """The step could not bring the file, the switch and the unit into agreement with the
    record. `file` is True when the file could not be written or deleted, before the
    switch was touched; else a unit act of the core failed. `record` is the record as the
    step left it."""

    def __init__(self, reason: str, fix: str | None = None, *, file: bool = False,
                 rec: Record | None = None):
        super().__init__(reason, fix)
        self.file = file
        self.record = rec


# -- checks -------------------------------------------------------------------------------

def check_tunnel_id(value: object) -> str:
    """The tunnel id, refused (exit 3) unless it is a UUID."""
    if not isinstance(value, str) or not settings.is_uuid(value):
        raise Refused(f"the Cloudflare tunnel id {value!r} is not a UUID",
                      "moxi config set tunnel cloudflare.tunnel_id <the id cloudflared "
                      "tunnel list shows>")
    return value


def check_hostname(value: object) -> str:
    """The hostname in the one host form, refused (exit 3) unless every label passes the
    label check, there are two labels at least and 253 characters at most."""
    host = normal_host(value) if isinstance(value, str) else ""
    labels = host.split(".")
    if (not host or len(host) > 253 or len(labels) < 2
            or not all(_LABEL.fullmatch(label) for label in labels)):
        raise Refused(f"{value!r} is not a hostname Moxi writes into cloudflared.yml",
                      "use a name such as app.example.com: letters, digits and inner "
                      "hyphens, two labels at least")
    return host


def check_path(value: object, what: str) -> str:
    """A path, refused (exit 3) when it is empty or holds a control character."""
    text = "" if value is None else str(value)
    if not text:
        raise Refused(f"{what} is not set", f"moxi config set tunnel {_KEYS[what]} <path>")
    if settings.has_control(text):
        raise Refused(f"{what} holds a control character", "set the path without it")
    return text


_KEYS = {"the Cloudflare credentials path": "cloudflare.credentials"}


def credentials_problem(path: str) -> str | None:
    """Why the credentials file can't be used, or None. Only its metadata is read: the file
    is never opened."""
    try:
        info = os.stat(path)
    except OSError as exc:
        return f"the Cloudflare credentials {path} can't be found ({exc.strerror})"
    if not stat.S_ISREG(info.st_mode):
        return f"the Cloudflare credentials {path} is not a regular file"
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        return f"the Cloudflare credentials {path} belongs to another user"
    return None


# -- the file -----------------------------------------------------------------------------

def config_path() -> Path:
    return record.state_dir() / CONFIG_FILE


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def rules(rec: Record) -> list[tuple[str, int]]:
    """The record's Cloudflare set as ingress rules, (hostname, loopback port), sorted."""
    return sorted({(normal_host(host), target) for _, host, target in rec.cloudflare_set()})


def render(pairs: Iterable[tuple[str, int]], *, tunnel_id: object, credentials: object,
           metrics_port: int) -> str:
    """The whole `cloudflared.yml` for `pairs`, its digest line first. Refuses (exit 3) a
    bad tunnel id, hostname or path. The credentials path is checked for its form only:
    whether the file is there and the user's is for `ready` and the doctor, so a remove
    still takes a rule out of the file after the credentials file is gone."""
    tid = check_tunnel_id(tunnel_id)
    cred = check_path(credentials, "the Cloudflare credentials path")
    if not 0 < int(metrics_port) < 65536:
        raise Refused(f"the metrics port {metrics_port} is not a port",
                      "moxi config set tunnel cloudflare.metrics_port <port>")
    q = json.dumps
    lines = [f"tunnel: {q(tid)}", f"credentials-file: {q(cred)}",
             f"metrics: {q(f'127.0.0.1:{int(metrics_port)}')}", "ingress:"]
    for host, target in pairs:
        lines.append(f"  - hostname: {q(check_hostname(host))}")
        lines.append(f"    service: {q(f'http://127.0.0.1:{int(target)}')}")
    lines.append(f"  - service: {q(CATCH_ALL)}")
    rest = "\n".join(lines) + "\n"
    return f"{DIGEST_PREFIX}{digest(rest)}\n{rest}"


def render_record(rec: Record) -> str:
    """The file the record's Cloudflare set renders, with the settings now."""
    values = settings.load()
    return render(rules(rec), tunnel_id=values["cloudflare.tunnel_id"],
                  credentials=values["cloudflare.credentials"],
                  metrics_port=values["cloudflare.metrics_port"])


@dataclass(frozen=True)
class OnDisk:
    """`cloudflared.yml` as it is now. `text` is None when there is no file; `by_hand` is
    True when its digest line is absent or does not hold."""

    text: str | None
    by_hand: bool = False

    @property
    def digest(self) -> str | None:
        """The digest its first line gives, when that line holds."""
        if self.text is None or self.by_hand:
            return None
        return self.text.partition("\n")[0].removeprefix(DIGEST_PREFIX)

    def rules(self) -> dict[str, str]:
        """Its ingress rules, hostname -> service, as far as they can be read."""
        return parse_rules(self.text or "")


def read_file() -> OnDisk:
    try:
        text = config_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return OnDisk(None)
    except (OSError, UnicodeDecodeError):
        return OnDisk("", by_hand=True)
    first, _, rest = text.partition("\n")
    held = first.startswith(DIGEST_PREFIX) and first.removeprefix(DIGEST_PREFIX) == digest(rest)
    return OnDisk(text, by_hand=not held)


def parse_rules(text: str) -> dict[str, str]:
    """The `hostname` rules of a file in the writer's form: hostname -> service."""
    out: dict[str, str] = {}
    host = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- hostname:"):
            host = _scalar(stripped.removeprefix("- hostname:"))
        elif stripped.startswith("service:") and host is not None:
            service = _scalar(stripped.removeprefix("service:"))
            if isinstance(host, str) and isinstance(service, str):
                out[normal_host(host)] = service
            host = None
        elif stripped.startswith("- "):
            host = None
    return out


def _scalar(text: str) -> object:
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        return text.strip("'\"") or None


def _writable(path: Path) -> None:
    """Refuse a file whose mode or owner changed by hand: tunnel doesn't write over it."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    bad = None
    if not stat.S_ISREG(info.st_mode):
        bad = "it is not a regular file"
    elif hasattr(os, "getuid") and info.st_uid != os.getuid():
        bad = "it belongs to another user"
    elif sys.platform != "win32" and stat.S_IMODE(info.st_mode) != 0o600:
        bad = f"its mode is {stat.S_IMODE(info.st_mode):04o}, not 0600"
    if bad:
        raise StepFailed(f"tunnel can't write {path}: {bad}",
                         f"chmod 600 {path}, or delete it, then {_REPAIR}", file=True)


def write_file(text: str) -> None:
    """Write `cloudflared.yml` whole, 0600, atomically."""
    path = config_path()
    _writable(path)
    try:
        files.atomic_write(path, text, mode=0o600)
    except OSError as exc:
        raise StepFailed(f"tunnel can't write {path}: {exc}",
                         f"fix the file's mode or owner, then {_REPAIR}", file=True) from None


def delete_file() -> None:
    path = config_path()
    _writable(path)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise StepFailed(f"tunnel can't delete {path}: {exc}",
                         f"fix the file's mode or owner, then {_REPAIR}", file=True) from None


def _overwrite(on_disk: OnDisk) -> None:
    if on_disk.text is not None and on_disk.by_hand:
        record.event("overwrite", None, provider="cloudflare", file=str(config_path()),
                     replaced=on_disk.text)


# -- the switch and the unit --------------------------------------------------------------

def switch_on() -> bool:
    return bool(settings.load()[SWITCH])


def unit_state() -> str:
    """The `cloudflared` unit's state, as `moxi.units.status` reads it."""
    for row in units.status(MODULE):
        if row.name == DAEMON and row.kind == "daemon":
            return row.state
    return "missing"


def _applied_failure(applied: Iterable) -> str | None:
    """The core's text when an `Applied` list means the unit was not acted on: a failed
    step, any `refuse` (an editable install counts as not acted on), or no supervisor."""
    bad = []
    for item in applied:
        change = item.change
        if (not item.ok or change.action == "refuse"
                or (change.action == "skip" and change.cause == "nobackend")):
            bad += list(item.lines) or [change.reason]
    return "; ".join(bad) if bad else None


def _set_switch(value: bool) -> list:
    """Set the switch through `moxi.config.set`, which applies the unit. What it prints
    goes into each `Applied`'s lines, not to this process's stdout. A config file the
    core refuses to change (a hand edit its check refuses) is a `StepFailed` with
    `file=True`: the switch was not touched."""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return config.set(MODULE, SWITCH, value, by=MODULE)
    except config.ConfigError as exc:
        raise StepFailed(f"tunnel can't set {SWITCH}: {exc.problem}",
                         f"{exc.fix}, then {_REPAIR}", file=True) from None


def _with_record(fn, value, rec: Record):
    try:
        return fn(value)
    except StepFailed as exc:
        exc.record = rec
        raise


def _apply_unit() -> list:
    return units.apply(units.plan(MODULE, only=[DAEMON]), say=lambda line: None)


def _unit_failed(text: str, rec: Record) -> StepFailed:
    return StepFailed(f"the cloudflared unit: {text}", _REPAIR, rec=rec)


# -- the step -----------------------------------------------------------------------------

def newer(rec: Record) -> list[str]:
    """The Cloudflare entries a newer tunnel wrote. While one is recorded this version
    can't render `cloudflared.yml`, since it can't read that entry's rule."""
    return sorted(k for k, e in rec.entries.items()
                  if not e.known and e.provider == "cloudflare")


def newer_text(names: list[str]) -> str:
    return (f"{', '.join(names)} {'was' if len(names) == 1 else 'were'} {record.NEWER}, "
            "so this version can't render cloudflared.yml and keeps it as it is")


def step(rec: Record | None = None) -> Record:
    """Bring the file, the switch and the unit into agreement with record `rec` (the
    record now when None), and return the record as it is after. Raises `StepFailed`
    when a unit act or the file write failed, and `RuntimeError` without the core lock."""
    if not locks.holds_core():
        raise RuntimeError("the Cloudflare step runs only under the core lock")
    rec = rec if rec is not None else record.read()
    names = newer(rec)
    if names:
        raise StepFailed(newer_text(names), "upgrade moxi-tunnel", file=True, rec=rec)
    on_disk = read_file()
    if rec.cloudflare_set():
        return _step_on(rec, on_disk)
    return _step_off(rec, on_disk)


def _step_on(rec: Record, on_disk: OnDisk) -> Record:
    try:
        text = render_record(rec)
    except Refused as exc:
        raise StepFailed(exc.reason, exc.fix, file=True, rec=rec) from None
    if on_disk.text != text:
        try:
            _writable(config_path())
            _overwrite(on_disk)
            write_file(text)
        except StepFailed as exc:
            exc.record = rec
            raise
    started = False
    if not switch_on():
        applied = _with_record(_set_switch, True, rec)
        failed = _applied_failure(applied)
        if failed is None and not applied:
            failed = "switching it on applied no unit"
        if failed:
            raise _unit_failed(failed, rec)
        started = any(a.change.action == "add" for a in applied)
    else:
        state = unit_state()
        if state == "nobackend":
            raise _unit_failed("no supervisor on this system", rec)
        if state == "missing":
            applied = _apply_unit()
            failed = _applied_failure(applied)
            if failed:
                raise _unit_failed(failed, rec)
            started = any(a.change.action == "add" for a in applied)
    want = digest(text.partition("\n")[2])
    if not started and (unit_state() != "active" or rec.cloudflared_started != want):
        acted = units.restart(MODULE, [DAEMON])
        bad = [line for a in acted if not a.ok for line in (a.lines or (f"{a.act} failed",))]
        if bad or not acted:
            raise _unit_failed("; ".join(bad) or "the restart did nothing", rec)
    if rec.cloudflared_started != want:
        rec = record.commit(cloudflared_started=want)
    return rec


def _step_off(rec: Record, on_disk: OnDisk) -> Record:
    failed = None
    state = unit_state()
    if switch_on() or state in INSTALLED:
        applied = _with_record(_set_switch, False, rec)
        failed = _applied_failure(applied)
        if failed is None and unit_state() in INSTALLED:
            failed = "the unit is still installed after the switch went off"
    try:
        if on_disk.text is not None:
            _writable(config_path())
            _overwrite(on_disk)
        delete_file()
    except StepFailed as exc:
        exc.record = rec
        raise
    if rec.cloudflared_started is not None:
        rec = record.commit(cloudflared_started=None)
    if failed:
        raise _unit_failed(failed, rec)
    return rec


# -- agreement, for repair and the doctor -------------------------------------------------

def agreement(rec: Record | None = None) -> tuple[str, str]:
    """Whether the file, the switch, the unit and the record agree: (`ok`, `warn` or
    `fail`, what disagrees). `fail` when the file differs from what the record renders,
    since a hostname is then served or dropped against the record; `warn` when only the
    switch, the unit's install state or the started digest disagrees."""
    rec = rec if rec is not None else record.read()
    names = newer(rec)
    if names:
        return "warn", newer_text(names)
    on_disk = read_file()
    on = switch_on()
    state = unit_state()
    if rec.cloudflare_set():
        try:
            text = render_record(rec)
        except Refused as exc:
            return "fail", f"cloudflared.yml can't be rendered: {exc.reason}"
        if on_disk.text != text:
            if on_disk.text is None:
                return "fail", "cloudflared.yml is missing"
            why = "was edited by hand" if on_disk.by_hand else "is behind the record"
            return "fail", f"cloudflared.yml {why}"
        if not on:
            return "warn", f"{SWITCH} is off while a Cloudflare target is recorded"
        if state not in INSTALLED:
            return "warn", f"the cloudflared unit is not installed ({state})"
        if rec.cloudflared_started != on_disk.digest:
            return "warn", "the cloudflared unit was not started on cloudflared.yml as it is"
        return "ok", "cloudflared.yml, the switch and the unit agree with the record"
    if on_disk.text is not None:
        why = "edited by hand" if on_disk.by_hand else "left behind"
        return "fail", f"cloudflared.yml is there ({why}) with no Cloudflare target"
    if on:
        return "warn", f"{SWITCH} is on with no Cloudflare target"
    if state in INSTALLED:
        return "warn", "the cloudflared unit is still installed with no Cloudflare target"
    return "ok", "no Cloudflare target, no switch, no unit and no file"


def in_step_row() -> Row:
    """The doctor's `cloudflare in step` row."""
    rec = record.read()
    state, detail = agreement(rec)
    fix = None if state == "ok" else "upgrade moxi-tunnel" if newer(rec) else _REPAIR
    return Row(MODULE, "cloudflare in step", state, detail, fix)


def setup_problems() -> list[Problem]:
    """The binary, the tunnel id, the credentials path and the file's mode."""
    out = []
    values = settings.load()
    if find_binary() is None:
        out.append(Problem("the cloudflared binary is not installed (not on PATH)",
                           f"install cloudflared ({INSTALL}), or moxi config set tunnel "
                           "cloudflare.binary <path>"))
    for check in (lambda: check_tunnel_id(values["cloudflare.tunnel_id"]),
                  lambda: _credentials(values["cloudflare.credentials"])):
        try:
            check()
        except Refused as exc:
            out.append(Problem(exc.reason, exc.fix or ""))
    try:
        _writable(config_path())
    except StepFailed as exc:
        out.append(Problem(exc.reason, exc.fix or ""))
    return out


def _credentials(value: object) -> None:
    path = check_path(value, "the Cloudflare credentials path")
    problem = credentials_problem(path)
    if problem:
        raise Refused(problem, "moxi config set tunnel cloudflare.credentials <path>")


def setup_row() -> Row:
    """The doctor's `cloudflare` row."""
    problems = setup_problems()
    if not problems:
        return Row(MODULE, "cloudflare", "ok", "cloudflared, the tunnel id and the "
                   "credentials are in place")
    return Row(MODULE, "cloudflare", "fail", "; ".join(p.reason for p in problems),
               problems[0].fix)


def find_binary() -> str | None:
    """`cloudflared`: the `cloudflare.binary` setting, else `PATH`; None when neither."""
    setting = settings.load()["cloudflare.binary"]
    if setting:
        return str(setting) if Path(setting).is_file() else None
    return shutil.which("cloudflared")


# -- the health check ---------------------------------------------------------------------

def health() -> Row:
    """The daemon's `HEALTH` row: `GET /ready` on the metrics port."""
    port = settings.load()["cloudflare.metrics_port"]
    where = f"http://127.0.0.1:{port}/ready"
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=HEALTH_WAIT)
    try:
        conn.request("GET", "/ready")
        answer = conn.getresponse()
        body = answer.read(4096).decode("utf-8", "replace")
    except OSError as exc:
        return Row(MODULE, DAEMON, "fail", f"cloudflared doesn't answer on {where} ({exc})",
                   "moxi doctor tunnel, then moxi tunnel repair")
    finally:
        conn.close()
    if answer.status == 200:
        try:
            ready = json.loads(body).get("readyConnections")
        except (ValueError, AttributeError):
            ready = None
        more = f", {ready} connections to Cloudflare" if isinstance(ready, int) else ""
        return Row(MODULE, DAEMON, "ok", f"ready on {where}{more}")
    return Row(MODULE, DAEMON, "fail", f"cloudflared is not ready: {where} answered "
               f"{answer.status}", "check the tunnel in the Cloudflare dashboard")


# -- the provider -------------------------------------------------------------------------

class Cloudflare:
    """The Cloudflare provider for one act or check. Its live config is Moxi's own file and
    the switch; the file is written by the step, so `apply` and `remove` only check."""

    name = "cloudflare"

    def __init__(self, *, deadline: float | None = None):
        self.deadline = deadline

    def ready(self, reach: str) -> list[Problem]:
        """What would stop the unit running, checked before an act puts an entry onto
        Cloudflare: no supervisor, a plan the core refuses (an editable install), then the
        binary, the tunnel id and the credentials."""
        out = []
        if units.backend() is None:
            out.append(Problem("this system has no supervisor to run cloudflared",
                               "use the Tailscale provider here"))
        else:
            for change in units.plan(MODULE, only=[DAEMON]):
                if change.action == "refuse":
                    fix = (f"moxi install {MODULE}" if change.cause == "editable"
                           else "moxi doctor tunnel")
                    out.append(Problem(change.reason, fix))
        return out + [p for p in setup_problems() if "cloudflared.yml" not in p.reason]

    def host(self, entry: Entry | None = None) -> str:
        if entry is None:
            raise Refused("a Cloudflare target needs its own hostname", "add --hostname H")
        return normal_host(entry.host)

    def apply(self, entry: Entry) -> None:
        if self.live_state(entry) is not None:
            raise StepFailed(f"cloudflared.yml does not serve {entry.host}", _REPAIR)

    def remove(self, entry: Entry) -> None:
        if normal_host(entry.host) in read_file().rules():
            raise StepFailed(f"cloudflared.yml still serves {entry.host}", _REPAIR)

    def live(self) -> dict[str, str]:
        """The file's ingress rules, hostname -> service; empty while the switch is off."""
        return read_file().rules() if switch_on() else {}

    def live_state(self, entry: Entry) -> str | None:
        """`missing` when its rule is absent or not the entry's, or the switch is off,
        whoever changed the file; else `changed` when the file was edited by hand; None
        when it matches."""
        on_disk = read_file()
        want = f"http://127.0.0.1:{entry.target}"
        if on_disk.rules().get(normal_host(entry.host)) != want or not switch_on():
            return "missing"
        if on_disk.text is not None and on_disk.by_hand:
            return "cloudflared.yml was edited by hand"
        return None


__all__ = ["DAEMON", "SWITCH", "PORT", "StepFailed", "Cloudflare", "check_tunnel_id",
           "check_hostname", "check_path", "credentials_problem", "config_path", "digest",
           "rules", "render", "render_record", "OnDisk", "read_file", "parse_rules",
           "write_file", "delete_file", "switch_on", "unit_state", "newer", "newer_text",
           "step", "agreement",
           "in_step_row", "setup_problems", "setup_row", "find_binary", "health"]

"""The Tailscale provider: Serve for an own-devices target, Funnel for a public one.

Tunnel drives the `tailscale` CLI through `moxi.proc.run`, each call with a 30 s timeout
(or what is left of a caller's deadline), and reads only its JSON output:
`tailscale status --json`, `tailscale serve status --json` and `tailscale debug prefs`,
and the first line of `tailscale version`.

    apply   tailscale serve  --bg --yes --https=<port> http://127.0.0.1:<target>
            tailscale funnel --bg --yes --https=<port> http://127.0.0.1:<target>
    remove  tailscale serve  --https=<port> off
            tailscale funnel --https=<port> off

`--bg` makes the config persist across reboots. Tunnel changes only config it applied, or
foreign config the user named with a confirmed `--replace`, and never runs `serve reset`,
which clears every entry.

The host is `Self.DNSName` from `tailscale status --json`, lower-cased with no trailing dot,
and every host read from Tailscale is put in that form before it is compared.

A live entry matches a record entry when, in `serve status --json`, `TCP["<port>"]` has
`HTTPS: true`; `Web["<host>:<port>"].Handlers` has exactly one key, `/`, whose handler has
exactly one field, `Proxy`, equal to `http://127.0.0.1:<target>`; and
`AllowFunnel["<host>:<port>"]` is true exactly when the reach is `public`. An entry matches
only when its whole handler set is Moxi's: a mount added beside it outside Moxi would be
served (for a Funnel, published) under Moxi's entry, so the entry reads `changed`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from pathlib import Path

from moxi import proc

from . import settings
from .errors import ApplyError, ReadError, Refused
from .provider import Problem
from .record import Entry, normal_host

#: Each `tailscale` call's timeout, in seconds.
TIMEOUT = 30.0
#: The CapMap key whose `ports=` query lists the ports the tailnet lets Funnel use.
FUNNEL_PORTS_CAP = "https://tailscale.com/cap/funnel-ports"
INSTALL = "https://tailscale.com/download"
ADMIN_DNS = "https://login.tailscale.com/admin/dns"
ADMIN_POLICY = "https://login.tailscale.com/admin/acls/file"
#: Where the CLI is when it is not on PATH.
PLATFORM_PATHS = {"darwin": "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
                  "win32": r"C:\Program Files\Tailscale\tailscale.exe"}

_UNSET = object()


def upstream(target: int) -> str:
    """The one upstream form: `http://127.0.0.1:<target>`."""
    return f"http://127.0.0.1:{target}"


def verb(reach: str) -> str:
    """The CLI form for a reach: `funnel` for public, `serve` for own-devices."""
    return "funnel" if reach == "public" else "serve"


# -- reading serve status ---------------------------------------------------------------

def _hostport(key: object) -> tuple[str, str] | None:
    if not isinstance(key, str) or ":" not in key:
        return None
    host, _, port = key.rpartition(":")
    return normal_host(host), port


def _section(serve: Mapping, name: str) -> dict:
    value = serve.get(name) if isinstance(serve, Mapping) else None
    return value if isinstance(value, dict) else {}


def _by_hostport(section: dict) -> dict[str, object]:
    out = {}
    for key, value in section.items():
        hp = _hostport(key)
        if hp is not None:
            out[f"{hp[0]}:{hp[1]}"] = value
    return out


def parts(serve: Mapping, port: int) -> dict:
    """Everything `serve status --json` holds for `port`: its `TCP` key and the `Web` and
    `AllowFunnel` keys of every `<host>:<port>`, hosts in the one form. `{}` is a port
    that holds nothing."""
    out: dict = {}
    tcp = _section(serve, "TCP")
    if str(port) in tcp:
        out["TCP"] = {str(port): tcp[str(port)]}
    for name in ("Web", "AllowFunnel"):
        got = {k: v for k, v in _by_hostport(_section(serve, name)).items()
               if k.rpartition(":")[2] == str(port)}
        if got:
            out[name] = got
    return out


def tcp_ports(serve: Mapping) -> list[int]:
    """The ports with a `TCP` key."""
    return sorted(int(p) for p in _section(serve, "TCP") if str(p).isdigit())


def handler_ok(web: object, target: int | Iterable[int]) -> bool:
    """True when `web` (one `Web` value) has exactly Moxi's handler set: `/` alone, with
    `Proxy` alone, to one of `target`."""
    targets = {target} if isinstance(target, int) else set(target)
    handlers = web.get("Handlers") if isinstance(web, dict) else None
    if not isinstance(handlers, dict) or list(handlers) != ["/"]:
        return False
    one = handlers["/"]
    return (isinstance(one, dict) and list(one) == ["Proxy"]
            and one["Proxy"] in {upstream(t) for t in targets})


def matches(serve: Mapping, entry: Entry, *, target: int | None = None,
            host: str | None = None) -> bool:
    """True when the live config holds exactly `entry` (with `target` and `host` in place
    of the entry's own, when given). An entry of a form this version doesn't know never
    matches."""
    if entry.form != "http":
        return False
    key = f"{normal_host(host or entry.host)}:{entry.port}"
    tcp = _section(serve, "TCP").get(str(entry.port))
    if not isinstance(tcp, dict) or tcp.get("HTTPS") is not True:
        return False
    if not handler_ok(_by_hostport(_section(serve, "Web")).get(key),
                      entry.target if target is None else target):
        return False
    funnel = _by_hostport(_section(serve, "AllowFunnel")).get(key) is True
    return funnel == (entry.reach == "public")


def only_ours(serve: Mapping, entry: Entry, *, hosts: Iterable[str],
              targets: Iterable[int]) -> bool:
    """True when `entry`'s port holds nothing, or only Moxi's own config for it: under
    `hosts`, with its handler to one of `targets`, Funnel on exactly when it is public.
    An apply over anything else would take config Moxi has no copy of."""
    got = parts(serve, entry.port)
    if not got:
        return True
    hosts = {normal_host(h) for h in hosts}
    targets = set(targets)
    for value in got.get("TCP", {}).values():
        if not isinstance(value, dict) or set(value) - {"HTTPS"} or value.get("HTTPS") is not True:
            return False
    for key, web in got.get("Web", {}).items():
        if key.rpartition(":")[0] not in hosts or not handler_ok(web, targets):
            return False
    for key, value in got.get("AllowFunnel", {}).items():
        if key.rpartition(":")[0] not in hosts:
            return False
        if (value is True) != (entry.reach == "public"):
            return False
    return True


def describe(port_parts: Mapping) -> str:
    """A short description of a port's foreign config, for a refusal."""
    webs = port_parts.get("Web", {})
    shown = []
    for key, web in webs.items():
        handlers = web.get("Handlers") if isinstance(web, dict) else None
        paths = ", ".join(f"{p} -> {_target_text(h)}" for p, h in (handlers or {}).items())
        funnel = port_parts.get("AllowFunnel", {}).get(key) is True
        shown.append(f"{'funnel' if funnel else 'serve'} {key} ({paths or 'no handler'})")
    if not shown:
        tcp = port_parts.get("TCP", {})
        shown = [f"TCP {json.dumps(v, sort_keys=True)}" for v in tcp.values()]
    return "; ".join(shown) or "nothing"


def _target_text(handler: object) -> str:
    if isinstance(handler, dict):
        for field in ("Proxy", "Path", "Text"):
            if field in handler:
                return f"{field.lower()} {handler[field]}"
    return json.dumps(handler, sort_keys=True)


def copy_of(serve: Mapping, port: int, host: str) -> dict:
    """The copy a `--replace` keeps of the foreign config on `port`: its `TCP`, `Web` and
    `AllowFunnel` parts, the command that removes it and the commands that would restore
    it."""
    got = parts(serve, port)
    funnel = any(v is True for v in got.get("AllowFunnel", {}).values())
    form = "funnel" if funnel else "serve"
    restore = []
    for key, web in got.get("Web", {}).items():
        handlers = web.get("Handlers") if isinstance(web, dict) else None
        on = "funnel" if got.get("AllowFunnel", {}).get(key) is True else "serve"
        for path, handler in (handlers or {}).items():
            where = "" if path == "/" else f" --set-path={path}"
            if isinstance(handler, dict) and "Proxy" in handler:
                restore.append(f"tailscale {on} --bg --https={port}{where} {handler['Proxy']}")
            elif isinstance(handler, dict) and "Path" in handler:
                restore.append(f"tailscale {on} --bg --https={port}{where} {handler['Path']}")
            elif isinstance(handler, dict) and "Text" in handler:
                restore.append(f"tailscale {on} --bg --https={port}{where} "
                               f"text:{handler['Text']}")
            else:
                restore.append(f"restore {key}{path} by hand from the JSON above")
    if not restore:
        restore.append("restore it by hand from the JSON above")
    return {"port": port, "host": normal_host(host), "parts": got, "form": form,
            "remove": f"tailscale {form} --https={port} off", "restore": restore}


def live_state(serve: Mapping, entry: Entry, host_now: str) -> str | None:
    """How the live config stands against a Tailscale `entry`: `renamed` (the record's
    host is not the machine's), `missing` (nothing for it on its host), `changed` (it no
    longer matches), or None when it matches."""
    if normal_host(entry.host) != normal_host(host_now):
        return "renamed"
    key = f"{normal_host(entry.host)}:{entry.port}"
    if (key not in _by_hostport(_section(serve, "Web"))
            and key not in _by_hostport(_section(serve, "AllowFunnel"))):
        return "missing"
    return None if matches(serve, entry) else "changed"


# -- the provider -----------------------------------------------------------------------

def find_binary() -> str | None:
    """The `tailscale` CLI: the `tailscale.binary` setting, else `PATH`, else the
    platform's usual place; None when none is there."""
    setting = settings.load()["tailscale.binary"]
    if setting:
        return str(setting) if Path(setting).is_file() else None
    found = shutil.which("tailscale")
    if found:
        return found
    default = PLATFORM_PATHS.get(sys.platform)
    return default if default and Path(default).is_file() else None


def _this_user() -> str | None:
    try:
        import pwd

        return pwd.getpwuid(os.geteuid()).pw_name
    except (ImportError, KeyError, AttributeError):
        return None


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


class Tailscale:
    """The Tailscale provider for one act or check. Each read is made once and kept;
    `fresh=True` reads again."""

    name = "tailscale"

    def __init__(self, *, deadline: float | None = None, binary: object = _UNSET,
                 wait: float | None = None, clock=time.monotonic):
        self.deadline = deadline
        #: Each call's own limit, in seconds, in place of `TIMEOUT` (the doctor's 2 s).
        self.wait = wait
        #: The clock `deadline` is read against.
        self.clock = clock
        self._binary = binary
        self._version: str | None = None
        self._status: dict | None = None
        self._serve: dict | None = None
        self._prefs: object = _UNSET

    # the CLI

    def binary(self) -> str | None:
        if self._binary is _UNSET:
            self._binary = find_binary()
        return self._binary  # type: ignore[return-value]

    def _missing(self) -> Problem:
        setting = settings.load()["tailscale.binary"]
        if setting:
            return Problem(f"the tailscale CLI {setting} (the tailscale.binary setting) is "
                           "not there", "install Tailscale, or moxi config set tunnel "
                           "tailscale.binary <path>")
        return Problem("the tailscale CLI is not installed (not on PATH)",
                       f"install Tailscale ({INSTALL}), or moxi config set tunnel "
                       "tailscale.binary <path>")

    def _timeout(self) -> float:
        limit = TIMEOUT if self.wait is None else self.wait
        if self.deadline is None:
            return limit
        left = self.deadline - self.clock()
        if left <= 0:
            raise ReadError("tailscale was not asked: the time for it ran out",
                            "tailscale status", timeout=True)
        return min(limit, left)

    def run(self, *args: str) -> subprocess.CompletedProcess:
        exe = self.binary()
        if exe is None:
            missing = self._missing()
            raise Refused(missing.reason, missing.fix)
        timeout = self._timeout()
        try:
            return proc.run([exe, *args], timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ReadError(f"tailscale did not answer in {timeout:g} s "
                            f"(tailscale {' '.join(args)})", "tailscale status",
                            timeout=True) from None
        except OSError as exc:
            raise ReadError(f"tailscale could not be run: {exc}",
                            "check the tailscale CLI") from None

    def _json(self, *args: str) -> dict:
        done = self.run(*args)
        if done.returncode != 0:
            raise ReadError(_text(done.stderr).strip() or
                            f"tailscale {' '.join(args)} exited {done.returncode}",
                            "tailscale status")
        try:
            value = json.loads(_text(done.stdout) or "{}")
        except ValueError:
            raise ReadError(f"tailscale {' '.join(args)} did not print JSON",
                            "check the tailscale CLI") from None
        if not isinstance(value, dict):
            raise ReadError(f"tailscale {' '.join(args)} did not print a JSON object",
                            "check the tailscale CLI")
        return value

    # reads

    def version(self) -> str:
        """`tailscale version`: its first line, such as `1.104.1`."""
        if self._version is None:
            done = self.run("version")
            if done.returncode != 0:
                raise ReadError(_text(done.stderr).strip() or
                                f"tailscale version exited {done.returncode}",
                                "check the tailscale CLI")
            lines = _text(done.stdout).strip().splitlines()
            if not lines or not lines[0].strip():
                raise ReadError("tailscale version printed nothing",
                                "check the tailscale CLI")
            self._version = lines[0].strip()
        return self._version

    def status(self, *, fresh: bool = False) -> dict:
        """`tailscale status --json`."""
        if self._status is None or fresh:
            self._status = self._json("status", "--json")
        return self._status

    def serve_config(self, *, fresh: bool = False) -> dict:
        """`tailscale serve status --json`."""
        if self._serve is None or fresh:
            self._serve = self._json("serve", "status", "--json")
        return self._serve

    def prefs(self) -> dict | None:
        """`tailscale debug prefs`, or None when it can't be read (a debug command)."""
        if self._prefs is _UNSET:
            try:
                self._prefs = self._json("debug", "prefs")
            except ReadError as exc:
                if exc.timeout:
                    raise
                self._prefs = None
        return self._prefs  # type: ignore[return-value]

    def host(self, entry: Entry | None = None) -> str:
        """The machine's host now, `Self.DNSName` in the one host form."""
        me = self.status().get("Self")
        name = me.get("DNSName") if isinstance(me, dict) else None
        if not isinstance(name, str) or not name.strip("."):
            raise ReadError("tailscale status gives this machine no DNS name",
                            "tailscale up, and turn on MagicDNS")
        return normal_host(name)

    def capmap(self) -> dict:
        me = self.status().get("Self")
        caps = me.get("CapMap") if isinstance(me, dict) else None
        return caps if isinstance(caps, dict) else {}

    def funnel_ports(self) -> tuple[int, ...] | None:
        """The ports the tailnet's funnel-ports grant lists, or None when it lists none."""
        found: list[int] = []
        seen = False
        for key in self.capmap():
            if isinstance(key, str) and key.startswith(FUNNEL_PORTS_CAP + "?"):
                seen = True
                query = key.partition("?")[2]
                for pair in query.split("&"):
                    name, _, value = pair.partition("=")
                    if name == "ports":
                        found += [int(p) for p in value.split(",") if p.strip().isdigit()]
        return tuple(sorted(set(found))) if seen else None

    def ready(self, reach: str) -> list[Problem]:
        """What would make an apply for `reach` fail, each with its fix, checked before any
        command runs, so the CLI's interactive enable flow never starts. A missing CLI is
        the first problem and the only one then."""
        if self.binary() is None:
            return [self._missing()]
        try:
            status = self.status()
        except ReadError as exc:
            return [Problem(f"tailscale status can't be read: {exc.reason}",
                            "start Tailscale, then tailscale up")]
        out: list[Problem] = []
        if status.get("BackendState") != "Running":
            out.append(Problem(f"Tailscale is not logged in and running (its state is "
                               f"{status.get('BackendState')!r})", "tailscale up"))
        tailnet = status.get("CurrentTailnet")
        if not (isinstance(tailnet, dict) and tailnet.get("MagicDNSEnabled") is True):
            out.append(Problem("MagicDNS is off for this tailnet",
                               f"turn on MagicDNS in the admin console, {ADMIN_DNS}"))
        domains = [normal_host(d) for d in status.get("CertDomains") or []
                   if isinstance(d, str)]
        caps = self.capmap()
        if not domains or "https" not in caps:
            out.append(Problem("HTTPS certificates are not enabled for this tailnet",
                               f"enable HTTPS certificates in the admin console, {ADMIN_DNS}"))
        else:
            try:
                host = self.host()
            except ReadError as exc:
                out.append(Problem(exc.reason, exc.fix or "tailscale up"))
            else:
                if host not in domains:
                    out.append(Problem(f"this machine's name {host} is not one of its "
                                       f"certificate domains ({', '.join(domains)})",
                                       f"enable HTTPS certificates in the admin console, "
                                       f"{ADMIN_DNS}"))
        prefs = self.prefs() if sys.platform.startswith("linux") or reach == "public" \
            else None
        if sys.platform.startswith("linux") and isinstance(prefs, dict) \
                and "OperatorUser" in prefs and not _is_root():
            user = _this_user()
            if prefs.get("OperatorUser") != user:
                out.append(Problem(f"{user or 'this user'} may not change Tailscale's serve "
                                   "config (it is not Tailscale's operator)",
                                   "sudo tailscale set --operator=$USER"))
        if reach == "public":
            grant = any(isinstance(k, str) and k.startswith(FUNNEL_PORTS_CAP + "?")
                        for k in caps)
            if "funnel" not in caps or not grant:
                out.append(Problem("Funnel is not allowed for this machine",
                                   "add the funnel node attribute to the tailnet policy "
                                   f"file, {ADMIN_POLICY}"))
            if isinstance(prefs, dict) and prefs.get("ShieldsUp") is True:
                out.append(Problem("shields are up, so Funnel can't take connections",
                                   "tailscale set --shields-up=false"))
        return out

    # changes

    def _change(self, *args: str) -> None:
        try:
            done = self.run(*args)
        except ReadError as exc:
            self._serve = None
            raise ApplyError(exc.reason) from None
        self._serve = None
        if done.returncode != 0:
            raise ApplyError(_text(done.stderr) or
                             f"tailscale {' '.join(args)} exited {done.returncode}")

    def apply(self, entry: Entry) -> None:
        """Put `entry` live on this machine's host: Serve or Funnel on its port, proxying
        to its loopback port."""
        self._change(verb(entry.reach), "--bg", "--yes", f"--https={entry.port}",
                     upstream(entry.target))

    def remove(self, entry: Entry) -> None:
        """Take `entry` out of the live config, only when the live config matches it.
        Refuses (exit 3) when it doesn't."""
        if not matches(self.serve_config(), entry):
            raise Refused(f"port {entry.port} no longer holds what Moxi applied",
                          f"tailscale {verb(entry.reach)} --https={entry.port} off")
        self._change(verb(entry.reach), f"--https={entry.port}", "off")

    def remove_copy(self, copy: Mapping) -> None:
        """Remove the foreign config a `--replace` copied, with the copy's own CLI form."""
        self._change(str(copy["form"]), f"--https={int(copy['port'])}", "off")

    def live(self) -> dict[int, dict]:
        """What Serve and Funnel hold now, each port's parts keyed by the port."""
        serve = self.serve_config()
        return {port: parts(serve, port) for port in tcp_ports(serve)}

    def live_state(self, entry: Entry) -> str | None:
        return live_state(self.serve_config(), entry, self.host())


def version_tuple(text: str) -> tuple[int, ...] | None:
    """`1.104.1` (or `1.104.1-t1234abcd`) as `(1, 104, 1)`; None when it isn't one."""
    head = text.strip().split()[0] if text.strip() else ""
    numbers = head.split("-", 1)[0].split(".")
    if not numbers or not all(n.isdigit() for n in numbers):
        return None
    return tuple(int(n) for n in numbers)


def _text(data: object) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return data or ""


__all__ = ["TIMEOUT", "FUNNEL_PORTS_CAP", "PLATFORM_PATHS", "Tailscale", "upstream", "verb",
           "parts", "tcp_ports", "handler_ok", "matches", "only_ours", "describe", "copy_of",
           "live_state", "find_binary", "version_tuple"]

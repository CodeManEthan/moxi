"""A fake `tailscale` CLI for tests: a small script on PATH that keeps a serve config in a
JSON file beside it, in the shapes Tailscale 1.104.1 prints.

It answers `status --json`, `serve status --json` (and `funnel status --json`), `debug
prefs`, `version` (the state's `version`, 1.104.1 unless set), an apply
(`serve|funnel --bg --yes --https=<port> <url>`) and an `off`
(`serve|funnel --https=<port> off`). An apply sets the `/` handler of the machine's
current `<host>:<port>` and keeps any other mount there; `off` takes out the port's `TCP`
key and the current host's `Web` and `AllowFunnel` keys for it, and leaves keys under any
other host. `fail` in the state makes a command fail: `{"apply": {"stderr": "..."}}`, with
`"after": true` to make the change first and fail after it. `hang` lists commands that
never answer. Every call is appended to `calls`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPT = r'''
import json, os, sys, time
from pathlib import Path

STATE = Path(__file__).with_name("tailscale-state.json")
state = json.loads(STATE.read_text())
args = sys.argv[1:]
state["calls"].append(args)

def save():
    tmp = STATE.with_name(f"{STATE.name}.{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, STATE)

def kind():
    if args[:2] == ["status", "--json"]:
        return "status"
    if len(args) > 1 and args[0] in ("serve", "funnel") and args[1] == "status":
        return "serve-status"
    if args[:2] == ["debug", "prefs"]:
        return "prefs"
    if args[:1] == ["version"]:
        return "version"
    if args and args[-1] == "off":
        return "off"
    return "apply"

what = kind()
save()
if what in state.get("hang", []):
    time.sleep(3600)
fail = state.get("fail", {}).get(what)
if fail and not fail.get("after"):
    sys.stderr.write(fail.get("stderr", "error"))
    sys.exit(fail.get("code", 1))
host = state["status"]["Self"]["DNSName"].rstrip(".").lower()
serve = state["serve"]
if what == "status":
    print(json.dumps(state["status"]))
elif what == "serve-status":
    print(json.dumps({k: v for k, v in serve.items() if v}))
elif what == "prefs":
    print(json.dumps(state["prefs"]))
elif what == "version":
    print(state.get("version", "1.104.1"))
    print("  tailscale commit: 0123456789abcdef")
else:
    port = next(a.split("=", 1)[1] for a in args if a.startswith("--https="))
    key = f"{host}:{port}"
    if what == "off":
        serve["TCP"].pop(port, None)
        serve["Web"].pop(key, None)
        serve["AllowFunnel"].pop(key, None)
    else:
        serve["TCP"][port] = {"HTTPS": True}
        web = serve["Web"].setdefault(key, {"Handlers": {}})
        web["Handlers"]["/"] = {"Proxy": args[-1]}
        if args[0] == "funnel":
            serve["AllowFunnel"][key] = True
        else:
            serve["AllowFunnel"].pop(key, None)
    save()
if fail:
    sys.stderr.write(fail.get("stderr", "error"))
    sys.exit(fail.get("code", 1))
'''

HOST = "box.tail1234.ts.net"
FUNNEL_CAP = "https://tailscale.com/cap/funnel-ports?ports=443,8443,10000"


def status(host: str = HOST, **over) -> dict:
    """A `tailscale status --json` of a logged-in machine with HTTPS and Funnel on."""
    got = {"BackendState": "Running",
           "Self": {"DNSName": host + ".", "HostName": host.split(".")[0],
                    "CapMap": {"https": None, "funnel": None, FUNNEL_CAP: None}},
           "CertDomains": [host],
           "CurrentTailnet": {"Name": "me@example.com", "MagicDNSSuffix": "tail1234.ts.net",
                              "MagicDNSEnabled": True}}
    got.update(over)
    return got


def this_user() -> str:
    import pwd

    return pwd.getpwuid(os.geteuid()).pw_name


class FakeTailscale:
    """The fake's state file, read and written whole."""

    def __init__(self, bin_dir: Path):
        self.bin = bin_dir
        self.path = bin_dir / "tailscale-state.json"
        bin_dir.mkdir(parents=True, exist_ok=True)
        script = bin_dir / "tailscale"
        script.write_text(f"#!{sys.executable} -IS\n" + SCRIPT)
        script.chmod(0o755)
        self.save({"status": status(), "prefs": {"OperatorUser": this_user(),
                                                 "ShieldsUp": False, "Hostname": "box"},
                   "serve": {"TCP": {}, "Web": {}, "AllowFunnel": {}}, "fail": {},
                   "hang": [], "calls": []})

    def load(self) -> dict:
        return json.loads(self.path.read_text())

    def save(self, state: dict) -> None:
        self.path.write_text(json.dumps(state, indent=1))

    def edit(self, fn) -> None:
        state = self.load()
        fn(state)
        self.save(state)

    @property
    def serve(self) -> dict:
        return self.load()["serve"]

    @property
    def calls(self) -> list[list[str]]:
        return self.load()["calls"]

    def changes(self) -> list[list[str]]:
        """The calls that change config."""
        return [c for c in self.calls if c[0] in ("serve", "funnel") and c[1] != "status"]

    def set_serve(self, serve: dict) -> None:
        self.edit(lambda s: s.update(serve={"TCP": {}, "Web": {}, "AllowFunnel": {},
                                            **serve}))

    def fail(self, what: str, stderr: str = "error: refused\n", *, after: bool = False):
        self.edit(lambda s: s["fail"].update({what: {"stderr": stderr, "after": after}}))

    def ok(self) -> None:
        self.edit(lambda s: s.update(fail={}))

    def rename(self, host: str) -> None:
        self.edit(lambda s: s.update(status=status(host)))

    def foreign(self, port: int, proxy: str = "http://127.0.0.1:3000", *,
                funnel: bool = False, host: str | None = None, extra: dict | None = None):
        """Put config Moxi did not apply on `port`."""
        def put(s):
            h = host or s["status"]["Self"]["DNSName"].rstrip(".")
            s["serve"]["TCP"][str(port)] = {"HTTPS": True}
            s["serve"]["Web"][f"{h}:{port}"] = {"Handlers": {"/": {"Proxy": proxy},
                                                             **(extra or {})}}
            if funnel:
                s["serve"]["AllowFunnel"][f"{h}:{port}"] = True
        self.edit(put)


def install(bin_dir: Path, monkeypatch) -> FakeTailscale:
    fake = FakeTailscale(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return fake

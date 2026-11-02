"""`moxi tunnel` from the command line, the change token a reader keeps, and the change
listeners."""

from __future__ import annotations

import json
import random
import subprocess
import sys

import pytest

from fake_tailscale import HOST
from moxi_tunnel import acts, api, cli, record

NEW = "laptop.tail1234.ts.net"


def run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_add_status_and_remove(alpha, tailscale, capsys):
    code, out, _ = run(capsys, "add", "alpha/gateway", "--yes")
    assert code == 0 and f"https://{HOST}:8461/" in out and "Certificate Transparency" in out
    code, out, _ = run(capsys, "status", "--json", "--check")
    got = json.loads(out)
    (gateway,) = [t for t in got["targets"] if t["name"] == "gateway"]
    assert set(gateway) >= {"module", "name", "reach", "exposure", "provider", "state", "url",
                            "host", "port", "target", "change", "displaced", "applied_at",
                            "detail", "check"}
    assert (gateway["state"], gateway["check"], gateway["exposure"]) == ("on", "ok", "tailnet")
    (connector,) = [t for t in got["targets"] if t["name"] == "connector"]
    assert connector["state"] is None and connector["reach"] == "public"
    code, out, _ = run(capsys, "status")
    assert "alpha/gateway" in out and "alpha/connector  public  off" in out
    assert run(capsys, "remove", "gateway", "--yes")[0] == 0
    assert record.read().entries == {}


def test_no_terminal_and_no_yes_refuses(alpha, tailscale, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", open("/dev/null"))
    code, _, err = run(capsys, "add", "alpha/gateway")
    assert code == 3 and "no terminal" in err and "--yes" in err
    assert tailscale.changes() == []


def test_a_terminal_answer_of_no_refuses(alpha, tailscale, capsys, monkeypatch):
    class Tty:
        def isatty(self):
            return True

        def readline(self):
            return "n\n"

    monkeypatch.setattr(sys, "stdin", Tty())
    code, out, _ = run(capsys, "add", "alpha/gateway")
    assert code == 3 and "[y/N]" in out and tailscale.changes() == []


def test_a_missing_prerequisite_exits_3_naming_its_fix_and_json_says_it(alpha, tailscale,
                                                                        capsys):
    tailscale.edit(lambda s: s["status"].update(BackendState="NeedsLogin"))
    code, out, _ = run(capsys, "add", "alpha/gateway", "--yes", "--json")
    assert code == 3
    assert json.loads(out) == {"error": json.loads(out)["error"], "fix": "tailscale up"}


def test_a_provider_failure_exits_1_with_its_stderr(alpha, tailscale, capsys):
    tailscale.fail("apply", "error: tailscaled says no\n")
    code, _, err = run(capsys, "add", "alpha/gateway", "--yes")
    assert code == 1 and "error: tailscaled says no" in err


@pytest.mark.parametrize("argv, code", [
    (["add", "alpha/gateway", "--hostname", "a.example.com", "--yes"], 2),
    (["add", "alpha/gateway", "--provider", "cloudflare", "--replace", "--yes"], 2),
    (["add", "alpha/gateway", "--port", "70000"], 2),
    (["frobnicate"], 2),
    # The Cloudflare provider is not built yet; this case changes when it is.
    (["add", "alpha/gateway", "--provider", "cloudflare", "--hostname", "a.example.com",
      "--yes"], 3),
    (["add", "nosuch", "--yes"], 3),
    (["remove", "alpha/gateway", "--yes"], 3),
])
def test_exit_codes(alpha, tailscale, capsys, argv, code):
    assert run(capsys, *argv)[0] == code


def test_repair_from_the_command_line(alpha, tailscale, capsys):
    run(capsys, "add", "alpha/gateway", "--yes")
    tailscale.set_serve({})
    code, out, _ = run(capsys, "repair", "--yes", "--json")
    assert code == 0 and json.loads(out)["code"] == 0
    assert acts.check()[0].check == "ok"


def test_python_dash_m_runs_the_cli(alpha, tailscale):
    done = subprocess.run([sys.executable, "-m", "moxi_tunnel", "--help"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and "moxi tunnel" in done.stdout


# -- the change token ----------------------------------------------------------------------

def test_a_reader_that_keeps_the_token_sees_every_change(alpha, tailscale, yes, config_file):
    seen = []

    def read():
        got = api.address("alpha", "gateway")
        seen.append(None if got is None else got.change)

    acts.add("alpha/gateway", ask=yes); read()
    acts.remove("alpha/gateway", ask=yes); read()
    acts.add("alpha/gateway", ask=yes); read()             # the same host again
    config_file("alpha", "port = 8500\n")
    acts.repair(ask=yes); read()                            # a moved repair
    tailscale.rename(NEW)
    acts.repair(ask=yes); read()                            # a rename repair
    tailscale.fail("off")
    with pytest.raises(Exception):
        acts.remove("alpha/gateway", ask=yes)               # rolled back
    tailscale.ok(); read()
    acts.remove("alpha/gateway", ask=yes, forget=True); read()
    record.record_path().unlink()                           # a new record file
    tailscale.set_serve({})
    acts.add("alpha/gateway", ask=yes); read()
    tokens = [t for t in seen if t is not None]
    assert len(tokens) == len(set(tokens))
    assert seen[1] is None and seen[-2] is None


def test_no_token_is_given_to_two_contents_over_random_acts(alpha, tailscale, yes,
                                                            config_file):
    rng = random.Random(7)
    given: dict[str, dict] = {}
    for _ in range(30):
        choice = rng.choice(["add", "remove", "forget", "rename", "move", "fail", "repair"])
        try:
            if choice == "add":
                acts.add(rng.choice(["alpha/gateway", "alpha/connector"]), ask=yes)
            elif choice in ("remove", "forget"):
                acts.remove(rng.choice(["alpha/gateway", "alpha/connector"]), ask=yes,
                            forget=choice == "forget")
            elif choice == "rename":
                tailscale.rename(rng.choice([HOST, NEW]))
            elif choice == "move":
                config_file("alpha", f"port = {rng.choice([8461, 8500])}\n")
            elif choice == "fail":
                tailscale.fail(rng.choice(["apply", "off"]), after=rng.random() < 0.5)
            else:
                tailscale.ok()
                acts.repair(ask=yes)
        except Exception:  # noqa: BLE001 - refusals and failures are part of the walk
            pass
        rec = record.read()
        for key, entry in rec.entries.items():
            token = rec.token(key)
            assert given.setdefault(token, entry.content()) == entry.content()


# -- the change listeners ------------------------------------------------------------------

LISTENER = """
CALLS = []

def on_change(target, old, new):
    from moxi_tunnel import locks
    CALLS.append((target, None if old is None else old.host,
                  None if new is None else new.host, locks.holds_tunnel()))

def broken(target, old, new):
    raise RuntimeError("listener down")
"""


@pytest.fixture
def listening(alpha):
    alpha.write("moxi_beta/__init__.py", "")
    alpha.write("moxi_beta/module.py", "SUMMARY = 'A listener.'\n")
    alpha.write("moxi_beta/listen.py", LISTENER)
    alpha.write("moxi_beta-0.1.0.dist-info/METADATA",
                "Metadata-Version: 2.1\nName: moxi-beta\nVersion: 0.1.0\n")
    alpha.write("moxi_beta-0.1.0.dist-info/entry_points.txt",
                "[moxi.modules]\nbeta = moxi_beta.module\n\n[moxi.tunnel.changes]\n"
                "beta = moxi_beta.listen:on_change\nbeta.broken = moxi_beta.listen:broken\n")
    import importlib

    return importlib.import_module("moxi_beta.listen").CALLS


def test_listeners_hear_each_change_once_after_the_locks_go(listening, tailscale, yes):
    acts.add("alpha/gateway", ask=yes)
    acts.add("alpha/gateway", ask=yes)                      # nothing changed
    tailscale.rename(NEW)
    acts.repair(ask=yes)
    tailscale.fail("off")
    with pytest.raises(Exception):
        acts.remove("alpha/gateway", ask=yes)               # rolled back: no call
    tailscale.ok()
    acts.remove("alpha/gateway", ask=yes)
    assert listening == [("alpha/gateway", None, HOST, False),
                         ("alpha/gateway", HOST, NEW, False),
                         ("alpha/gateway", NEW, None, False)]

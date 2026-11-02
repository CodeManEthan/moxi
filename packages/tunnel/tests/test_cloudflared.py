"""The daemon: its declaration, the `moxi-tunnel-cloudflared` script (exec on POSIX, a
waited child on Windows, idle exit when there is nothing to serve), the `/ready` health
check, and Cloudflare through the CLI."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import moxi.modules
from moxi.units import switch_problem
from moxi_tunnel import cli, cloudflare, cloudflared, module, settings


def test_the_daemon_is_declared_behind_its_switch():
    found = moxi.modules.load("tunnel")
    assert found.state == "present", found.reason
    assert found.daemons == {"cloudflared": {"command": "moxi-tunnel-cloudflared",
                                             "doc": "the Cloudflare named tunnel",
                                             "switch": "cloudflare.enabled"}}
    assert found.health == {"cloudflared": "moxi_tunnel.cloudflare:health"}
    assert switch_problem(settings.schema(), "cloudflare.enabled") is None
    assert module.DAEMONS["cloudflared"]["switch"] == cloudflare.SWITCH


# -- the script ----------------------------------------------------------------------------

@pytest.fixture
def logged(monkeypatch):
    """The lines the script logs."""
    lines = []

    class Logger:
        def info(self, text, *args):
            lines.append(text % args)

        error = info

    monkeypatch.setattr(cloudflared.log, "setup", lambda module: Logger())
    return lines


@pytest.fixture
def served(cf, alpha, units, yes):
    """A cloudflared.yml with one target rule, as an add leaves it."""
    from moxi_tunnel import acts

    acts.add("alpha/gateway", provider_name="cloudflare", hostname="a.example.com", ask=yes)
    return cloudflare.config_path()


@pytest.mark.parametrize("text", [None, "# by hand\ningress:\n  - service: http_status:404\n"])
def test_with_nothing_to_serve_it_logs_one_line_and_exits_0(cf, monkeypatch, logged, text):
    if text is not None:
        cloudflare.write_file(text)
    monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("cloudflared was started"))
    assert cloudflared.main([]) == 0
    assert len(logged) == 1 and "no Cloudflare target" in logged[0]


def test_on_posix_it_replaces_its_process_with_cloudflared(served, monkeypatch):
    got = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "execv", lambda path, argv: got.append((path, argv)))
    cloudflared.main([])
    (path, argv), = got
    assert path == cloudflare.find_binary() and path.endswith("cloudflared")
    assert argv == [path, "--no-autoupdate", "tunnel", "--config", str(served), "run"]


def test_the_binary_setting_wins_over_path(cf, tmp_path, config_file):
    assert cloudflare.find_binary().endswith("cloudflared")
    other = tmp_path / "other-cloudflared"
    other.write_text("")
    config_file("tunnel", f"[cloudflare]\nbinary = '{other}'\n")
    assert cloudflare.find_binary() == str(other)


def test_on_windows_it_waits_for_a_child_with_no_breakaway_flag(served, monkeypatch):
    seen = {}

    class Child:
        def __init__(self, argv, **kwargs):
            seen.update(argv=argv, kwargs=kwargs)

        def wait(self):
            return 7

    monkeypatch.setattr(cloudflare, "find_binary", lambda: "C:/cloudflared.exe")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", Child)
    monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("exec on Windows"))
    assert cloudflared.main([]) == 7
    assert seen["kwargs"] == {}  # no creationflags: it stays in the gate's job
    assert seen["argv"][1:] == ["--no-autoupdate", "tunnel", "--config", str(served), "run"]


@pytest.mark.skipif(sys.platform == "win32", reason="a signal ends a child on POSIX only")
def test_a_child_ended_by_a_signal_exits_non_zero():
    argv = [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"]
    assert cloudflared.run_child(argv) == 128 + signal.SIGTERM
    assert cloudflared.run_child([sys.executable, "-c", "raise SystemExit(3)"]) == 3


def test_a_missing_binary_is_a_failure_not_an_idle_exit(served, monkeypatch, logged):
    monkeypatch.setattr(cloudflare, "find_binary", lambda: None)
    assert cloudflared.main([]) == 1
    assert "not installed" in logged[0]


# -- the health check ----------------------------------------------------------------------

@pytest.fixture
def ready(config_file):
    """A fake cloudflared metrics server on loopback; set `.status` to what /ready
    answers."""
    class Handler(BaseHTTPRequestHandler):
        status = 200

        def do_GET(self):  # noqa: N802 - http.server's name
            body = json.dumps({"status": Handler.status, "readyConnections": 4}).encode()
            self.send_response(Handler.status if self.path == "/ready" else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config_file("tunnel", f"[cloudflare]\nmetrics_port = {server.server_port}\n")
    yield Handler
    server.shutdown()
    server.server_close()


def test_health_reads_ready(ready):
    row = cloudflare.health()
    assert (row.module, row.state) == ("tunnel", "ok") and "4 connections" in row.detail
    ready.status = 503
    row = cloudflare.health()
    assert row.state == "fail" and "503" in row.detail


def test_health_fails_when_nothing_answers(config_file):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config_file("tunnel", f"[cloudflare]\nmetrics_port = {port}\n")
    row = cloudflare.health()
    assert row.state == "fail" and "doesn't answer" in row.detail


def test_health_fails_with_its_detail_when_the_port_does_not_speak_http(config_file):
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    config_file("tunnel", f"[cloudflare]\nmetrics_port = {srv.getsockname()[1]}\n")

    def serve():
        conn, _ = srv.accept()
        conn.recv(1024)
        conn.sendall(b"garbage\r\n\r\n")
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    try:
        row = cloudflare.health()
    finally:
        srv.close()
    assert row.state == "fail" and "doesn't answer" in row.detail


# -- the CLI -------------------------------------------------------------------------------

def test_cloudflare_through_the_cli(cf, alpha, units, capsys):
    code = cli.main(["add", "alpha/gateway", "--provider", "cloudflare", "--hostname",
                     "A.example.com", "--yes", "--json"])
    out, err = capsys.readouterr()
    assert code == 0, out + err
    assert json.loads(out)["address"]["url"] == "https://a.example.com/"
    assert "Rocket Loader" in err
    assert cli.main(["status", "--json", "--check"]) == 0
    (row,) = [t for t in json.loads(capsys.readouterr().out)["targets"]
              if t["name"] == "gateway"]
    assert (row["provider"], row["exposure"], row["check"]) == ("cloudflare", "internet", "ok")
    assert cli.main(["remove", "alpha/gateway", "--yes"]) == 0
    assert cloudflare.read_file().text is None and not cloudflare.switch_on()

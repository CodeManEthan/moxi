"""The seam between the door and its providers, and the daemon behind its switch."""

from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from kit import GRANT_OF, Fake, bearer, call, echo_tool, provider, request

from moxi_connector import providers as providers_mod
from moxi_connector.provider import TOOL_FAILED, Provider, Result, Tool

SRC = Path(__file__).resolve().parents[1] / "src" / "moxi_connector"


def run(coro):
    return asyncio.run(coro)


def test_a_providers_tool_is_called_with_its_ctx_and_after_answer_runs_true(front, fake):
    async def main():
        f = await front()
        got = await request(f, "POST", "/mcp", call("echo", {"say": "hi"}), headers=bearer())
        assert got.status == 200
        [(name, args, ctx)] = fake.calls
        assert (name, args) == ("echo", {"say": "hi"})
        assert (ctx.grant_id, ctx.grant_label) == (GRANT_OF.id, GRANT_OF.label)
        assert fake.written == [True]
        row, durable, collapse = f.host.calllog.written[-1]
        assert row["request"] == ctx.request_id
        assert (row["tool"], row["provider"], row["grant"]) == ("echo", "demo", GRANT_OF.id)
        assert (row["recipients"], row["returned"]) == (["planner"], 1)
        assert durable is True and collapse is None
        await f.close()
    run(main())


def test_after_answer_runs_false_when_the_send_raises(front, fake):
    async def main():
        f = await front()
        await request(f, "POST", "/mcp", call("echo"), headers=bearer(), send_fails=True)
        assert fake.written == [False]
        await f.close()
    run(main())


def test_after_answer_runs_false_when_no_final_body_comes(front, fake):
    started = asyncio.Event()

    async def stuck(args, ctx):
        ctx.after_answer(fake.written.append)
        started.set()
        await asyncio.Event().wait()

    async def main():
        f = await front(providers={"demo": provider(fake, Tool("stuck", "x", {}, stuck))})
        pending = asyncio.ensure_future(request(f, "POST", "/mcp", call("stuck"),
                                                headers=bearer()))
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert fake.written == [False]
        await f.close()
    run(main())


def test_a_caller_that_hangs_up_on_a_real_socket_cancels_the_tool_and_settles_false(
        make_host, fake):
    """uvicorn's send returns quietly once the peer is gone, and it never cancels the app:
    the host's own watch on the connection is what ends the tool and settles False."""
    from conftest import ORIGIN
    from kit import Running, hang_up, http, wait_for

    began, ended = [], []

    async def slow(args, ctx):
        ctx.after_answer(fake.written.append)
        began.append(time.monotonic())
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            ended.append(time.monotonic())
            raise
        return Result("late")

    host = run(make_host(providers={"demo": provider(fake, echo_tool(fake),
                                                     Tool("slow", "x", {}, slow))}))
    with Running(host, ORIGIN) as d:
        # A caller that stays gets its answer, settled True, on the same socket path.
        status, _, _ = http(d.port, "POST", "/mcp", headers={
            "Authorization": "Bearer good-token", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"},
            body=json.dumps(call("echo", {"say": "hi"})).encode())
        assert status == 200
        wait_for(lambda: fake.written == [True])
        hang_up(d.port, call("slow"), until=lambda: began)
        wait_for(lambda: ended, within=5)
        wait_for(lambda: fake.written == [True, False])
        assert ended[0] - began[0] < 5
        wait_for(lambda: len([r for r in host.calllog.rows if r.get("tool") == "slow"]))
    [row] = [r for r in host.calllog.rows if r.get("tool") == "slow"]
    assert row["dropped"] is True and row["kind"] == "request"
    [kept] = [r for r in host.calllog.rows if r.get("tool") == "echo"]
    assert "dropped" not in kept


def test_a_tool_name_two_providers_declare_is_refused_for_both(front):
    one, two = Fake(), Fake()

    async def main():
        f = await front(providers={
            "alpha": provider(one, echo_tool(one), echo_tool(one, "only_alpha")),
            "beta": provider(two, echo_tool(two))})
        tools = (await request(f, "POST", "/mcp", {"jsonrpc": "2.0", "id": 2,
                                                   "method": "tools/list", "params": {}},
                               headers=bearer())).json()["result"]["tools"]
        assert [t["name"] for t in tools] == ["only_alpha"]
        got = await request(f, "POST", "/mcp", call("echo"), headers=bearer())
        assert got.json()["result"]["isError"]
        assert one.calls == two.calls == []
        rows = f.host.registry.status_rows()
        assert ("tool echo", "fail", "declared by alpha and beta; served by neither") in rows
        await f.close()
    run(main())


def test_a_provider_whose_start_raises_loads_no_tools_while_the_other_serves(front):
    bad, good = Fake(), Fake()

    async def main():
        f = await front(providers={
            "bad": provider(bad, echo_tool(bad, "bad_tool"), fail_start=True),
            "good": provider(good, echo_tool(good))})
        tools = (await request(f, "POST", "/mcp", {"jsonrpc": "2.0", "id": 2,
                                                   "method": "tools/list", "params": {}},
                               headers=bearer())).json()["result"]["tools"]
        assert [t["name"] for t in tools] == ["echo"]
        rows = f.host.registry.status_rows()
        assert rows[0] == ("bad", "fail", "its start failed: RuntimeError")
        assert ("good bound", "ok", "the fake is up") in rows
        await f.close()
        await f.host.registry.stop()
        assert (bad.stopped, good.stopped) == (0, 1)
    run(main())


def test_a_tool_that_raises_answers_the_error_result_and_writes_tool_error(front, fake):
    async def boom(args, ctx):
        raise KeyError("a secret detail")

    async def main():
        f = await front(providers={"demo": provider(fake, Tool("boom", "x", {}, boom))})
        got = await request(f, "POST", "/mcp", call("boom"), headers=bearer())
        result = got.json()["result"]
        assert result["isError"] and result["content"][0]["text"] == TOOL_FAILED
        assert b"secret" not in got.body
        errors = [(r, d) for r, d, _ in f.host.calllog.written if r["kind"] == "tool-error"]
        [(row, durable)] = errors
        assert (row["tool"], row["provider"], row["error"], durable) == (
            "boom", "demo", "KeyError", True)
        await f.close()
    run(main())


@pytest.mark.parametrize("answers", ["raises", "refuses", "not-a-result"])
def test_a_call_that_raised_or_answered_an_error_runs_its_callbacks_false(
        front, fake, answers):
    """The error answer is written, but it is not the tool's answer: what the tool
    registered before it failed or refused runs with False."""
    async def failing(args, ctx):
        ctx.after_answer(fake.written.append)
        if answers == "raises":
            raise RuntimeError("while rendering")
        if answers == "refuses":
            return Result.refused("offline", "comms is offline. Nothing was sent or read.")
        return "not a Result"

    async def main():
        f = await front(providers={"demo": provider(fake, Tool("failing", "x", {},
                                                                failing))})
        got = await request(f, "POST", "/mcp", call("failing"), headers=bearer())
        assert got.status == 200 and got.json()["result"]["isError"]
        assert fake.written == [False]
        await f.close()
    run(main())


def test_a_tool_call_without_its_requests_context_is_refused_and_logged(make_host, fake):
    """Were an SDK update to run the handler outside the request's context, the call
    would have no grant and its callbacks would never settle: it is refused instead."""
    from moxi_connector.app import Tools

    async def main():
        host = await make_host()
        got = await Tools(host).call_tool(None, SimpleNamespace(name="echo",
                                                                arguments={"say": "x"}))
        assert got.is_error and got.structured_content == {"refusal": "no-context"}
        assert fake.calls == []
        [(row, durable, collapse)] = host.calllog.written
        assert (row["kind"], row["tool"], row["provider"], row["error"]) == (
            "tool-error", "echo", "demo", "no-context")
        assert durable is True and collapse is None
    run(main())


def test_ctx_log_with_a_refused_field_raises_and_writes_nothing(front, fake):
    caught = []

    async def leaky(args, ctx):
        for fields in ({"token": "x"}, {"meta": {"nested": {"Text": "body"}}},
                       {"ok": 1, "authorization": "Bearer x"}):
            try:
                ctx.log(**fields)
            except ValueError as e:
                caught.append(str(e))
        return Result("fine")

    async def main():
        f = await front(providers={"demo": provider(fake, Tool("leaky", "x", {}, leaky))})
        await request(f, "POST", "/mcp", call("leaky"), headers=bearer())
        assert len(caught) == 3 and all("refuses the field" in c for c in caught)
        row = f.host.calllog.rows[-1]
        assert not {"token", "meta", "ok", "authorization"} & set(row)
        await f.close()
    run(main())


def test_arguments_that_do_not_fit_the_schema_never_reach_the_provider(front, fake):
    async def main():
        f = await front()
        got = await request(f, "POST", "/mcp", call("echo", {"say": 5}), headers=bearer())
        result = got.json()["result"]
        assert result["isError"] and result["structuredContent"]["refusal"] == (
            "invalid-arguments")
        assert fake.calls == []
        await f.close()
    run(main())


def test_a_refusal_carries_its_code():
    from moxi_connector.app import to_mcp

    out = to_mcp(Result.refused("name-held", "Someone holds it.", holder="x"))
    assert out.is_error and out.structured_content == {"refusal": "name-held", "holder": "x"}


# -- loading the group ------------------------------------------------------------------

def test_an_entry_not_named_for_its_distribution_is_refused(monkeypatch):
    from moxi import modules

    entries = [SimpleNamespace(name="comms", target="tests_fake:P", distribution="moxi-notes"),
               SimpleNamespace(name="notes", target="no.such.module:P",
                               distribution="moxi-notes"),
               SimpleNamespace(name="loadout", target="json:loads",
                               distribution="moxi-loadout")]
    monkeypatch.setattr(modules, "extensions", lambda group: entries)
    found = {e.module: e for e in providers_mod.load()}
    assert found["comms"].state == "refused" and "not the module of" in found["comms"].why
    assert found["notes"].state == "refused" and "couldn't be imported" in found["notes"].why
    assert found["loadout"].state == "refused" and "not a" in found["loadout"].why


def test_a_good_entry_loads_its_provider(monkeypatch):
    from moxi import modules

    monkeypatch.setitem(sys.modules, "fake_provider_mod", SimpleNamespace(P=Provider()))
    monkeypatch.setattr(modules, "extensions", lambda group: [SimpleNamespace(
        name="demo", target="fake_provider_mod:P", distribution="moxi-demo")])
    [entry] = providers_mod.load()
    assert entry.state == "loaded" and isinstance(entry.provider, Provider)


# -- the seam rule: the host imports nothing of a provider --------------------------------

_HOST_MAY_IMPORT = {"moxi", "mcp", "uvicorn", "jsonschema", "starlette", "moxi_connector"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def test_the_host_imports_only_the_core_its_libraries_and_itself():
    for path in SRC.glob("*.py"):
        foreign = {m for m in _imports(path) - _HOST_MAY_IMPORT
                   if m not in sys.stdlib_module_names}
        assert not foreign, (path.name, foreign)


def test_the_provider_interface_is_standard_library_only():
    found = _imports(SRC / "provider.py")
    assert all(m in sys.stdlib_module_names or m == "__future__" for m in found), found


def test_loading_the_whole_host_imports_nothing_of_comms():
    code = ("import sys, importlib, pkgutil, moxi_connector\n"
            "for m in pkgutil.iter_modules(moxi_connector.__path__):\n"
            "    importlib.import_module('moxi_connector.' + m.name)\n"
            "print(sorted(n for n in sys.modules if n.startswith(('moxi_comms', 'moxi_ui'))))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=60, check=True)
    assert out.stdout.strip() == "[]", out.stderr


# -- the daemon behind its switch -------------------------------------------------------

def test_the_core_reads_the_daemon_behind_its_switch_with_its_socket_unit():
    from moxi import modules, units

    found = modules.load("connector")
    assert found.state == modules.PRESENT, found.reason
    daemon = found.daemons["server"]
    assert daemon["command"] == "moxi-connector-server" and daemon["switch"] == "enabled"
    socket = daemon["socket"]
    assert (socket["setting"], list(socket["addresses"]), socket["start"]) == (
        "port", ["127.0.0.1"], "login")
    assert units.switched_daemons("connector", "enabled") == ["server"]
    assert units.starts(found) == {"server": "login"}
    decl = units.declarations("connector")
    assert set(decl.daemons) == {"server"}


def test_setting_the_switch_applies_the_units_through_the_core(monkeypatch, config_file):
    import moxi.config

    from moxi_connector import settings

    applied = []
    monkeypatch.setattr(moxi.config, "apply_units",
                        lambda module, key, **kw: applied.append((module, key)) or [])
    config_file("")
    moxi.config.set("connector", "enabled", True, by="test")
    assert settings.enabled() is True
    moxi.config.set("connector", "port", 8453, by="test")
    moxi.config.unset("connector", "enabled", by="test")
    assert settings.enabled() is False
    # The port is the socket unit's setting, so the core writes the units after it too.
    assert applied == [("connector", "enabled"), ("connector", "port"),
                       ("connector", "enabled")]


def test_the_daemon_refuses_to_start_while_switched_off(capsys):
    from moxi_connector import daemon

    assert daemon.main([]) == 3
    assert "the connector is off" in capsys.readouterr().err


def test_the_daemon_serves_on_its_own_socket_and_tells_the_sign_in_the_switch(make_host):
    import http.client

    from conftest import ORIGIN

    from moxi_connector import listen
    from moxi_connector.daemon import Daemon

    async def main():
        host = await make_host()
        sock = listen.bind(0)
        port = sock.getsockname()[1]
        switch = [True]
        d = Daemon(sock, host, read_origin=lambda: ORIGIN, switch=lambda: switch[0], env={})
        await d.start()

        def get():
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/mcp", body=b"{}", headers={"Host": ORIGIN.host})
            status = conn.getresponse().status
            conn.close()
            return status

        assert await asyncio.to_thread(get) == 401
        switch[0] = False
        await d.stop()
        assert host.signin.stopped == [False]
        assert host.calllog.closed
    run(main())


def test_a_port_another_listener_holds_is_port_taken():
    from moxi_connector import listen

    held = listen.bind(0)
    try:
        port = held.getsockname()[1]
        taken = listen.take(port, environ={}, unit_state=lambda: "missing", retry_for=0.2)
        assert taken.sock is None and taken.why == "port-taken"
    finally:
        held.close()

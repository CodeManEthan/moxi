"""Withheld tools: the setting `tools.withheld` lists tool names the door serves to no
grant. A withheld tool is absent from `tools/list`, a call to it answers as an unknown
tool, and status and the doctor name it."""

from __future__ import annotations

import asyncio

import pytest
from conftest import ORIGIN
from kit import Admit, Fake, Running, bearer, call, echo_tool, provider, request, rpc

from moxi.config import ConfigError
from moxi_connector import checks, cli, doctor, settings
from moxi_connector.app import Front, Host, build_app
from moxi_connector.calllog import MemoryLog
from moxi_connector.providers import Registry
from test_doctor import ANSWER, by_name, snap


def _registry(fake: Fake, withheld=()) -> Registry:
    return Registry.of({"demo": provider(fake, echo_tool(fake), echo_tool(fake, "read_note")),
                        "other": provider(Fake(), echo_tool(Fake(), "agents"))},
                       withheld=withheld)


async def _front(registry: Registry) -> Front:
    await registry.start({"HOME": "/nowhere"})
    host = Host(registry, signin=Admit(), calllog=MemoryLog())
    app = build_app(ORIGIN, host)
    await app.start()
    return Front(app, host)


def test_the_table_leaves_out_each_withheld_name_whichever_provider_declares_it(fake):
    async def main():
        registry = _registry(fake, withheld=["read_note", "agents", "no_such_tool"])
        await registry.start({})
        return registry

    registry = asyncio.run(main())
    assert list(registry.tools()) == ["echo"]
    assert not [r for r in registry.status_rows() if r.level != "ok"]


def test_a_withheld_tool_is_absent_from_the_list_and_answers_as_unknown(fake):
    async def main():
        f = await _front(_registry(fake, withheld=["read_note"]))
        tools = (await request(f, "POST", "/mcp", rpc("tools/list"),
                               headers=bearer())).json()["result"]["tools"]
        assert [t["name"] for t in tools] == ["echo", "agents"]
        withheld = (await request(f, "POST", "/mcp", call("read_note", {"say": "x"}),
                                  headers=bearer())).json()["result"]
        missing = (await request(f, "POST", "/mcp", call("read_nothing", {"say": "x"}),
                                 headers=bearer())).json()["result"]
        assert withheld["isError"] and missing["isError"]
        assert withheld["structuredContent"] == missing["structuredContent"] == {
            "refusal": "unknown-tool"}
        assert withheld["content"][0]["text"] == "There is no tool named read_note."
        assert missing["content"][0]["text"] == "There is no tool named read_nothing."
        assert fake.calls == []
        await f.close()

    asyncio.run(main())


def test_with_nothing_withheld_every_tool_is_served(fake):
    async def main():
        registry = _registry(fake)
        await registry.start({})
        return registry

    assert list(asyncio.run(main()).tools()) == ["echo", "read_note", "agents"]


# -- status and the doctor -------------------------------------------------------------------

def test_the_control_status_names_the_withheld_tools(fake):
    registry = _registry(fake, withheld=["read_note", "agents"])
    asyncio.run(registry.start({}))
    host = Host(registry, calllog=MemoryLog())
    with Running(host, ORIGIN):
        got = checks.ask("status")
    assert got["withheld"] == ["agents", "read_note"]
    assert got["tools"] == [{"name": "echo", "provider": "demo"}]


def test_the_providers_row_counts_served_tools_and_names_each_withheld_one():
    answer = {**ANSWER, "withheld": ["send", "search_notes"]}
    row = by_name(doctor.build(snap(answer=answer)))["providers"]
    assert row.state == "ok"
    assert row.detail == ("comms, 3 tools; withheld: send (comms), "
                          "search_notes (no provider declares it)")


def test_a_failing_or_empty_providers_row_still_names_the_withheld_tools():
    answer = {**ANSWER, "providers": [], "gates": [], "rows": [], "withheld": ["send"]}
    row = by_name(doctor.build(snap(answer=answer)))["providers"]
    assert row.state == "warn"
    assert row.detail.startswith(doctor.NO_PROVIDER)
    assert row.detail.endswith("; withheld: send (no provider declares it)")
    answer = {**ANSWER, "providers": [*ANSWER["providers"],
                                      {"module": "notes", "state": "failed",
                                       "why": "its start failed", "tools": []}],
              "withheld": ["send"]}
    row = by_name(doctor.build(snap(answer=answer)))["providers"]
    assert row.state == "fail" and row.detail.endswith("; withheld: send (comms)")


def test_nothing_withheld_leaves_the_row_as_it_was():
    assert by_name(doctor.build(snap()))["providers"].detail == "comms, 4 tools"


def test_status_text_names_the_withheld_tools(make_host, fake, config_file, capsys,
                                              monkeypatch):
    from moxi_connector import origin as origins

    monkeypatch.setattr(origins, "tunnel_address", lambda: None)
    config_file('enabled = true\npublic_url = "https://door.example.net"\n')
    registry = _registry(fake, withheld=["read_note"])
    asyncio.run(registry.start({}))
    with Running(Host(registry, calllog=MemoryLog()), ORIGIN):
        assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "withheld: read_note" in out
    assert "tools: echo\n" in out


# -- the setting ---------------------------------------------------------------------------

def test_the_setting_defaults_to_nothing_and_reads_a_list(config_file):
    config_file("")
    assert settings.load()["tools.withheld"] == []
    config_file('[tools]\nwithheld = ["read_note", "search_memory"]\n')
    assert settings.load()["tools.withheld"] == ["read_note", "search_memory"]


@pytest.mark.parametrize("bad", ["", " read_note", "read note"])
def test_the_check_refuses_an_entry_that_is_not_a_tool_name(bad):
    with pytest.raises(ConfigError, match="tools.withheld"):
        settings.check(f'[tools]\nwithheld = ["{bad}"]\n')
    settings.check('[tools]\nwithheld = ["read_note"]\n')

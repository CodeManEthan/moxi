"""The grants file and who may change it: `moxi connector grants` and `revoke`, with the
process up and down, revokes racing the process's writes, the doctor's grants view, and the
rule that a switch-off leaves no grant valid."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from conftest import ORIGIN
from kit import Running, echo_tool, provider
from signin_kit import CALLBACKS

from moxi_connector import cli, doctor, grants
from moxi_connector import origin as origins
from moxi_connector.oauth import OAuth, PageError
from moxi_connector.origin import Origin

OTHER = Origin("other.example.net", 8443)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def no_tunnel(monkeypatch):
    monkeypatch.setattr(origins, "tunnel_address", lambda: None)


@pytest.fixture
def applied(monkeypatch):
    import moxi.config

    got = []
    monkeypatch.setattr(moxi.config, "apply_units",
                        lambda module, key, **kw: got.append((module, key)) or [])
    return got


def made(store: grants.Store, *, resource=ORIGIN.resource, label="phone") -> str:
    """A client and a grant, written as the process would write them."""
    cid = "client-" + grants.new_token()[:8]
    store.add_client(cid, {"client_name": "Claude", "redirect_uris": list(CALLBACKS)}, None)
    return store.add_grant(cid, label=label, resource=resource,
                           access_hash=grants.digest(grants.new_token()),
                           refresh_hash=grants.digest(grants.new_token()))


def test_a_grant_keeps_its_last_2000_retired_refresh_tokens(config_file):
    """Past 2000, a rotation forgets the oldest: presented again it is unknown, while the
    next oldest is still reuse."""
    config_file("")
    store = grants.Store()
    gid = made(store)
    grant = store.grant(gid)
    old = [grants.digest(f"retired-{n}") for n in range(2000)]
    store.change(lambda data: data["grants"][gid].update(retired=list(old)))
    acted = store.refresh(grant["refresh"], grant["client"],
                          access_hash=grants.digest(grants.new_token()),
                          refresh_hash=grants.digest(grants.new_token()))
    assert acted.kind == "live"
    retired = store.grant(gid)["retired"]
    assert len(retired) == 2000
    assert retired[0] == old[1] and retired[-1] == grant["refresh"]
    assert store.judge(old[0], grant["client"]).kind == "unknown"
    assert store.judge(old[1], grant["client"]).kind == "reuse"


# -- revoke from the command line -------------------------------------------------------------

def test_revoke_with_the_process_up_takes_effect_on_the_next_call(door, config_file, capsys):
    config_file("")

    async def main():
        d = await door()
        _, tokens = await d.sign_in()
        gid = d.grant_id()
        assert (await d.mcp(tokens["access_token"])).status == 200
        assert cli.main(["revoke", gid]) == 0
        assert (await d.mcp(tokens["access_token"])).status == 401
        await d.close()
        return gid

    gid = run(main())
    assert f"Grant {gid} is revoked" in capsys.readouterr().out


def test_revoke_with_the_process_down_holds_when_it_starts(door, config_file):
    config_file("")

    async def first():
        d = await door()
        client, tokens = await d.sign_in()
        await d.close()
        return client, tokens, d.grant_id()

    client, tokens, gid = run(first())
    assert cli.main(["revoke", gid]) == 0

    async def second():
        d = await door()
        assert (await d.mcp(tokens["access_token"])).status == 401
        assert (await d.refresh(client, tokens["refresh_token"])).status == 400
        # A fresh sign-in on the same client still works.
        await d.sign_in(client)
        await d.close()

    run(second())


def test_revoke_of_an_unknown_grant_is_refused(config_file, capsys):
    config_file("")
    assert cli.main(["revoke", "deadbeef"]) == 3
    assert "there is no grant deadbeef" in capsys.readouterr().err
    assert cli.main(["revoke"]) == 3


def test_revoke_all_revokes_every_grant_and_drops_every_client(door, config_file, capsys):
    config_file("")

    async def main():
        d = await door()
        client, tokens = await d.sign_in()
        assert cli.main(["revoke", "--all"]) == 0
        assert (await d.mcp(tokens["access_token"])).status == 401
        assert d.oauth.store.view()["clients"] == {}
        with pytest.raises(PageError):
            d.page(client)
        await d.close()

    run(main())
    assert "remove the connector and add it again" in capsys.readouterr().out


def test_a_revoke_racing_the_processs_writes_survives_them(config_file):
    config_file("")
    process = grants.Store()
    gid = made(process)
    process.view()                                   # the process holds the file's old form
    assert grants.Store().revoke(gid, by="cli")      # the command line, another process
    process.note_used(gid)                           # the process's write after it
    assert process.grant(gid)["revoked_by"] == "cli"
    # And with the writes truly at once, from threads.
    gid = made(process)
    start = threading.Event()

    def uses():
        start.wait()
        for _ in range(30):
            process.note_used(gid)

    threads = [threading.Thread(target=uses) for _ in range(3)]
    for t in threads:
        t.start()
    start.set()
    grants.Store().revoke(gid, by="cli")
    for t in threads:
        t.join(30)
    assert grants.Store().grant(gid)["revoked_by"] == "cli"


def test_revoke_all_racing_the_daily_drop_leaves_no_client_and_no_live_grant(config_file):
    config_file("")
    clock = SimpleNamespace(t=0.0)
    process = grants.Store(clock=lambda: clock.t)
    clock.t = time.time() - grants.UNFINISHED_KEEP - 1
    gid = made(process)
    for n in range(10):
        process.add_client(f"unfinished-{n}", {"redirect_uris": list(CALLBACKS)}, None)
    clock.t = time.time()
    start = threading.Event()

    def drop():
        start.wait()
        process.tidy()

    t = threading.Thread(target=drop)
    t.start()
    start.set()
    count = grants.Store().revoke_all(by="cli", drop_clients=True)
    t.join(30)
    data = grants.Store().view()
    assert data["clients"] == {} and count == 1
    assert data["grants"][gid]["revoked"]


def test_a_grant_cannot_be_made_for_a_client_revoke_all_dropped(config_file):
    config_file("")
    process = grants.Store()
    process.add_client("c1", {"redirect_uris": list(CALLBACKS)}, None)
    grants.Store().revoke_all(by="cli", drop_clients=True)
    with pytest.raises(grants.Gone):
        process.add_grant("c1", label=None, resource=ORIGIN.resource, access_hash="a" * 64,
                          refresh_hash="b" * 64)


def test_a_grants_file_that_cant_be_read_refuses_every_token_and_is_never_overwritten(
        config_file):
    config_file("")
    store = grants.Store()
    gid = made(store)
    store.path.write_text("{not json")
    assert store.view()["grants"] == {}
    with pytest.raises(ValueError):
        store.revoke(gid, by="cli")
    assert store.path.read_text() == "{not json"


# -- the listing and the doctor ------------------------------------------------------------

def test_grants_lists_each_grant_with_its_state_and_staleness(config_file, monkeypatch,
                                                              capsys):
    config_file(f'public_url = "{OTHER.url}"\n')
    store = grants.Store()
    old = made(store, resource=ORIGIN.resource, label="desk")
    new = made(store, resource=OTHER.resource, label="phone")
    gone = made(store, resource=OTHER.resource, label="old")
    store.revoke(gone, by="cli")
    assert cli.main(["grants", "--json"]) == 0
    rows = {r["id"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows[old]["stale"] and rows[old]["state"] == "live"
    assert not rows[new]["stale"] and rows[new]["label"] == "phone"
    assert rows[gone]["state"] == "revoked" and not rows[gone]["stale"]
    assert rows[new]["client_name"] == "Claude" and rows[new]["used"] is None
    assert cli.main(["grants"]) == 0
    out = capsys.readouterr().out
    assert f"{old}  desk  client Claude" in out and "live, stale" in out
    assert doctor.STALE in out


def test_grants_with_none_says_so(config_file, no_tunnel, capsys):
    config_file("")
    assert cli.main(["grants"]) == 0
    assert "No grants" in capsys.readouterr().out


def test_the_doctors_view_counts_live_grants_and_the_stale_ones(config_file):
    config_file("")
    assert doctor.grants_view(ORIGIN) is None          # no file: no grants row
    store = grants.Store()
    made(store)
    made(store, resource=OTHER.resource)
    store.revoke(made(store), by="cli")
    view = doctor.grants_view(ORIGIN)
    assert (view.count, view.stale) == (2, 1)
    assert doctor.grants_view(None).stale == 0          # with no origin nothing is stale
    row = {r.name: r for r in doctor.build(_snap(view))}["grants"]
    assert row.state == "warn" and doctor.STALE in row.detail


def test_the_doctor_reads_the_grants_with_the_origin_it_reads(config_file, monkeypatch):
    config_file(f'enabled = true\npublic_url = "{ORIGIN.url}"\n')
    store = grants.Store()
    made(store)
    snap = doctor.take(ask=lambda *a: {"pid": 1, "url": ORIGIN.url, "providers": [],
                                       "tools": [], "rows": []}, tunnel=lambda: None)
    assert (snap.grants.count, snap.grants.stale) == (1, 0)


def _snap(view):
    return doctor.Snapshot(True, 8452, "", None, ORIGIN, {"providers": [], "tools": []},
                           None, None, None, view)


# -- the switch-off rule ------------------------------------------------------------------

def test_disable_revokes_every_grant(config_file, applied, no_tunnel, capsys):
    config_file("enabled = true\n")
    store = grants.Store()
    a, b = made(store), made(store)
    assert cli.main(["disable"]) == 0
    data = grants.Store().view()["grants"]
    assert data[a]["revoked_by"] == data[b]["revoked_by"] == "disable"
    assert cli.REVOKED_ALL in capsys.readouterr().out


def test_disable_with_no_grants_file_writes_none(config_file, applied, no_tunnel):
    config_file("enabled = true\n")
    assert cli.main(["disable"]) == 0
    assert not grants.Store().exists()


def test_a_stop_with_the_switch_off_revokes_and_a_restart_with_it_on_keeps(config_file):
    config_file("")
    store = grants.Store()
    gid = made(store)
    oauth = OAuth(grants.Store(), _log(), redirect_uris=CALLBACKS)
    run(oauth.stopping(True))
    assert not grants.Store().grant(gid)["revoked"]
    run(oauth.stopping(False))
    assert grants.Store().grant(gid)["revoked_by"] == "stop"


def test_the_daemon_stopping_with_the_switch_off_revokes_every_grant(make_host, fake,
                                                                     config_file):
    config_file("")
    gid = made(grants.Store())
    switch = [True]

    async def host_():
        host = await make_host(providers={"demo": provider(fake, echo_tool(fake))})
        host.signin = OAuth(grants.Store(), host.calllog, redirect_uris=CALLBACKS)
        return host

    with Running(run(host_()), ORIGIN, switch=lambda: switch[0]):
        pass
    assert not grants.Store().grant(gid)["revoked"]       # a restart: the switch was on
    with Running(run(host_()), ORIGIN, switch=lambda: switch[0]):
        switch[0] = False
    assert grants.Store().grant(gid)["revoked_by"] == "stop"


def _log():
    from moxi_connector.calllog import MemoryLog

    return MemoryLog()

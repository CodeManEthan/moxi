"""A user may go back to tunnel 0.1 after this version wrote the record. Every record shape
this version writes is one 0.1 reads without refusing: 0.1 keeps the `retired` list as an
unknown key and writes it back unchanged, reads a `unix` entry as a newer tunnel's and
refuses to change or drop it, and acts on its `http` entries as before. A target declared in
`moxi.tunnel.targets` is one 0.1 never reads.

`tunnel_0_1` is a frozen copy of 0.1's rules, so this file fails when a change here writes
something 0.1 would refuse.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
import tunnel_0_1 as v01

import moxi.modules
from moxi_tunnel import declare, locks, record
from moxi_tunnel.record import Entry, State

SOCKET = "/run/user/1000/moxi/ui/gateway.sock"
COPY = "20261030T120000123456Z-8461.json"
AT = "2026-10-30T12:00:00Z"


@pytest.fixture(autouse=True)
def no_cloudflare_step(monkeypatch):
    """The shapes alone are under test, not the Cloudflare step a commit may run."""
    from moxi_tunnel import cloudflare

    monkeypatch.setattr(cloudflare, "step", lambda rec=None: rec)


def commit(*args, **kw):
    with locks.held(core=True, timeout=5):
        return record.commit(*args, **kw)


def raw() -> dict:
    return json.loads(record.record_path().read_text())


def put(data: dict) -> None:
    record.record_path().write_text(json.dumps(data, indent=2) + "\n")


def http(**kw) -> Entry:
    base = dict(reach="own-devices", provider="tailscale", host="box.tail1234.ts.net",
                port=8443, target=9000, state=State.ON, applied_at=AT)
    return Entry(**{**base, **kw})


def unix(**kw) -> Entry:
    return http(**{"port": 443, "form": "unix", "target": SOCKET, **kw})


def shapes() -> list[tuple[str, dict]]:
    """Records this version writes, one after each commit of a run that covers every
    entry form, state and record field it writes."""
    out = []

    def snap(what: str) -> None:
        out.append((what, raw()))

    commit()
    snap("a new record")
    commit({"ui/gateway": unix(state=State.APPLYING, detail="serve failed: busy")})
    snap("a unix entry applying, with detail")
    commit({"ui/gateway": unix(), "alpha/web": http()},
           warned=["Box.Tail1234.ts.net."], retire=[("Old.Example.ORG.", 443)])
    snap("a unix entry on beside an http one, a warned host and a retired origin")
    commit({"alpha/pub": http(reach="public", provider="cloudflare", host="pub.example.org",
                              port=443, target=9100, state=State.APPLYING,
                              detail="cloudflared said no")},
           cloudflared_started="0" * 64)
    snap("a Cloudflare entry applying, and the started digest")
    web = raw()["entries"]["alpha/web"]
    commit({"alpha/web": http(provider="cloudflare", host="web.example.org", port=443,
                              state=State.APPLYING, displaced=COPY,
                              from_={k: web[k] for k in ("provider", "host", "port",
                                                         "target", "reach", "form")})})
    snap("a provider switch under way, with a displaced copy")
    commit({"alpha/odd": http(provider="cloudflare", host="odd.example.org", port=443,
                              state=State.APPLYING,
                              from_={"provider": "tailscale", "host": "box.tail1234.ts.net",
                                     "port": 10000, "target": SOCKET,
                                     "reach": "own-devices", "form": "unix"})})
    snap("an http entry switching off a Tailscale unix one")
    commit({"alpha/pub": replace(Entry.from_json("alpha/pub", raw()["entries"]["alpha/pub"]),
                                 state=State.REMOVING), "alpha/web": None})
    snap("an entry removing and one dropped")
    put(v01.write(raw(), {"alpha/late": v01.Entry(
        reach="own-devices", provider="tailscale", host="box.tail1234.ts.net", port=10001,
        target=9200, state="on", applied_at=AT)}))
    commit(warned=["late.example.org"])
    snap("after 0.1 added an http entry the stored list lacks")
    data = raw()
    del data["retired"]
    put(data)
    commit({"alpha/odd": None})
    snap("the first write over a record with no retired list")
    commit({"ui/gateway": None, "alpha/pub": None, "alpha/late": None})
    snap("every entry gone")
    return out


def test_every_shape_this_version_writes_is_one_tunnel_0_1_reads_and_keeps():
    for what, data in shapes():
        old = v01.read(data)
        assert old.writable, what
        assert "retired" in data and old.extra["retired"] == data["retired"], what
        for name, value in data["entries"].items():
            assert old.entries[name].known == (value["form"] == "http"), (what, name)

        added = v01.write(data, {"zeta/new": v01.Entry(
            reach="own-devices", provider="tailscale", host="box.tail1234.ts.net",
            port=10002, target=9300, state="on", applied_at=AT)})
        assert added["retired"] == data["retired"], what
        assert {k: v for k, v in added["entries"].items() if k != "zeta/new"} \
            == data["entries"], what
        now = record.Record.from_json(added)
        assert now.retired >= record.Record.from_json(data).retired, what

        for name, value in data["entries"].items():
            if value["form"] != "http":
                with pytest.raises(v01.Refused01, match="newer tunnel"):
                    v01.write(data, {name: None})


def test_tunnel_0_1_reads_no_target_declared_in_the_new_group(site):
    site.module("alpha", targets_group={"alpha.web": "WEB"}, values="""
        WEB = {"reach": "own-devices", "port_setting": "port",
               "upstream": "moxi_alpha.tunnel_decl:up",
               "retired": "moxi_alpha.tunnel_decl:old"}
        def up():
            return "http://127.0.0.1:8461"
        def old():
            return []
    """)
    assert list(declare.read().targets) == ["alpha/web"]
    assert declare.TARGETS_GROUP != v01.GROUP
    assert moxi.modules.extensions(v01.GROUP) == []

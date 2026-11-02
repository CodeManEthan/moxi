"""The record: format 1, `commit` and the change token, keys a newer tunnel wrote, the
Cloudflare set's lock, the event log, and the displaced copies."""

from __future__ import annotations

import json
import os
import random
import stat
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from moxi_tunnel import api, locks, record
from moxi_tunnel.errors import Refused
from moxi_tunnel.record import Entry, State

NOW = datetime(2026, 10, 30, 12, 0, 0, 123456, tzinfo=timezone.utc)


def entry(**kw) -> Entry:
    base = dict(reach="own-devices", provider="tailscale", host="box.tail1234.ts.net",
                port=8461, target=8461, state=State.ON, applied_at="2026-10-30T12:00:00Z")
    return Entry(**{**base, **kw})


def commit(*args, core=False, **kw):
    with locks.held(core=core, timeout=5):
        return record.commit(*args, **kw)


def raw() -> dict:
    return json.loads(record.record_path().read_text())


def test_a_missing_record_reads_empty():
    got = record.read()
    assert not got.exists and got.entries == {} and got.writable and got.id is None


def test_the_first_commit_writes_format_1_with_a_random_id_privately():
    got = commit({"ui/gateway": entry()})
    data = raw()
    assert list(data) == ["format", "id", "last_generation", "warned_ct",
                          "cloudflared_started", "entries"]
    assert data["format"] == 1 and len(data["id"]) == 32 and int(data["id"], 16) >= 0
    assert data["last_generation"] == 1
    assert data["entries"]["ui/gateway"] == {
        "reach": "own-devices", "provider": "tailscale", "form": "http",
        "host": "box.tail1234.ts.net", "port": 8461, "target": 8461, "state": "on",
        "generation": 1, "from": None, "displaced": None, "detail": None,
        "applied_at": "2026-10-30T12:00:00Z"}
    assert got == record.read() and got.token("ui/gateway") == f"{data['id']}.1"
    if os.name == "posix":
        assert stat.S_IMODE(record.record_path().stat().st_mode) == 0o600
    other = record.record_path()
    other.unlink()
    assert commit({"ui/gateway": entry()}).id != data["id"]


def test_commit_runs_only_under_tunnel_lock():
    with pytest.raises(RuntimeError, match="only under tunnel.lock"):
        record.commit({"ui/gateway": entry()})
    assert not record.record_path().exists()


def test_generations_rise_on_every_entry_change_and_only_then():
    commit({"a/x": entry()})
    commit({"b/y": entry(port=9000)})
    rec = record.read()
    assert (rec.last_generation, rec.entries["a/x"].generation,
            rec.entries["b/y"].generation) == (2, 1, 2)
    # The same content again, a caller's generation ignored: nothing moves.
    rec = commit({"a/x": entry(generation=99)})
    assert rec.last_generation == 2 and rec.entries["a/x"].generation == 1
    # A write of top-level fields only changes no entry, so it gives no new token.
    rec = commit(warned=["Box.Tail1234.ts.net."], cloudflared_started="sha")
    assert rec.last_generation == 2 and rec.warned_ct == ("box.tail1234.ts.net",)
    assert rec.cloudflared_started == "sha"
    rec = commit({"a/x": entry(state=State.APPLYING)})
    assert rec.last_generation == 3 and rec.entries["a/x"].generation == 3
    rec = commit({"b/y": None})
    assert rec.last_generation == 4 and "b/y" not in rec.entries
    # A re-added entry gets a value above every one it had.
    rec = commit({"b/y": entry(port=9000)})
    assert rec.entries["b/y"].generation == 5
    rec = commit({"nosuch/drop": None})
    assert rec.last_generation == 5


def test_keys_this_version_does_not_know_are_kept_and_written_back():
    commit({"a/x": entry()})
    data = raw()
    data["retired"] = [{"host": "old.ts.net"}]
    data["entries"]["a/x"]["asks"] = {"upstream": "unix"}
    record.record_path().write_text(json.dumps(data))
    rec = record.read()
    assert rec.extra == {"retired": [{"host": "old.ts.net"}]}
    assert rec.entries["a/x"].extra == {"asks": {"upstream": "unix"}}
    commit({"a/x": replace(rec.entries["a/x"], state=State.REMOVING),
            "b/y": entry(port=9000)})
    data = raw()
    assert data["retired"] == [{"host": "old.ts.net"}]
    assert data["entries"]["a/x"]["asks"] == {"upstream": "unix"}
    assert data["entries"]["a/x"]["state"] == "removing"


def test_an_entry_a_newer_tunnel_wrote_is_kept_and_never_changed():
    commit({"a/x": entry()})
    data = raw()
    newer = {"reach": "own-devices", "provider": "tailscale", "form": "unix",
             "socket": "/run/x.sock", "state": "on", "generation": 1}
    data["entries"]["n/w"] = newer
    record.record_path().write_text(json.dumps(data))
    rec = record.read()
    assert not rec.entries["n/w"].known and rec.entries["a/x"].known
    assert api.address("n", "w") is None
    for change in ({"n/w": None}, {"n/w": entry()}):
        with pytest.raises(Refused, match="written by a newer tunnel") as caught:
            commit(change)
        assert caught.value.exit_code == 3
    commit({"b/y": entry(port=9000)})
    assert raw()["entries"]["n/w"] == newer
    with pytest.raises(Refused, match="doesn't know"):
        commit({"c/z": entry(form="unix")})


def test_a_record_of_another_format_is_read_as_it_can_be_and_never_written():
    path = record.record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"format": 2, "id": "x", "entries": {
        "a/x": {"provider": "tailscale", "host": "box.ts.net", "port": 1, "state": "on",
                "form": "http"}}})
    path.write_text(text)
    rec = record.read()
    assert rec.format == 2 and not rec.writable and rec.entries["a/x"].host == "box.ts.net"
    with pytest.raises(Refused, match="format 2") as caught:
        commit({"b/y": entry()})
    assert caught.value.exit_code == 3 and path.read_text() == text


@pytest.mark.parametrize("text, problem", [
    ("not json", "not JSON"),
    ("[]", "not a JSON object"),
    ('{"format": 1, "last_generation": 0, "entries": {}}', "no id"),
    ('{"format": 1, "id": "%s", "last_generation": 0, "entries": {"a/x": {"reach": '
     '"own-devices", "provider": "tailscale", "form": "http", "host": "h", "port": "1", '
     '"target": 1, "state": "on", "generation": 1, "from": null, "displaced": null, '
     '"detail": null, "applied_at": null}}}' % ("0" * 32), "port that is not a port"),
    ('{"format": 1, "id": "%s", "last_generation": 0, "entries": {"a/x": {"form": '
     '"http"}}}' % ("0" * 32), "has no 'reach'"),
])
def test_a_damaged_record_is_refused_and_left_as_it_is(text, problem):
    path = record.record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    with pytest.raises(Refused, match=problem):
        record.read()
    with pytest.raises(Refused, match=problem):
        commit({"b/y": entry()})
    assert path.read_text() == text


def test_a_change_to_the_cloudflare_set_needs_the_core_lock():
    cf = entry(provider="cloudflare", host="ui.example.com", port=443, target=8461)
    with pytest.raises(Refused, match="needs the core lock"):
        commit({"ui/gateway": cf})
    assert not record.record_path().exists()
    rec = commit({"ui/gateway": cf}, core=True)
    assert rec.cloudflare_set() == {("ui/gateway", "ui.example.com", 8461)}
    # Tailscale entries, and a Cloudflare entry that stays in the set, need no core lock.
    commit({"a/x": entry()})
    commit({"ui/gateway": replace(cf, state=State.APPLYING, detail="unit failed")})
    for change in ({"ui/gateway": replace(cf, state=State.REMOVING)},
                   {"ui/gateway": replace(cf, target=8470)}, {"ui/gateway": None}):
        with pytest.raises(Refused, match="needs the core lock"):
            commit(change)
    assert commit({"ui/gateway": None}, core=True).cloudflare_set() == frozenset()


def test_no_token_is_ever_given_to_two_different_entry_contents():
    rng = random.Random(20261030)
    names = ["a/x", "b/y", "c/z"]
    given: dict[str, dict] = {}
    for step in range(400):
        roll = rng.random()
        if roll < 0.03:
            record.record_path().unlink(missing_ok=True)
        else:
            name = rng.choice(names)
            current = record.read().entries.get(name)
            if roll < 0.25:
                change = {name: None}
            elif current is not None and roll < 0.45:
                change = {name: current}  # a rewrite of the same content
            else:
                change = {name: entry(state=rng.choice(list(State)),
                                      host=rng.choice(["box.ts.net", "new.ts.net"]),
                                      port=rng.choice([443, 8461]),
                                      detail=rng.choice([None, "stderr"]))}
            commit(change)
        rec = record.read()
        for name, e in rec.entries.items():
            token = rec.token(name)
            assert given.setdefault(token, e.content()) == e.content(), (step, token)


def test_events_are_lines_that_roll_while_warned_hosts_stay(monkeypatch):
    record.event("add", "ui/gateway", provider="tailscale", port=8461, now=NOW,
                 new_host="box.ts.net")
    (line,) = [json.loads(x) for x in record.events_path().read_text().splitlines()]
    assert line == {"at": "2026-10-30T12:00:00Z", "event": "add", "target": "ui/gateway",
                    "provider": "tailscale", "port": 8461, "new_host": "box.ts.net"}
    with pytest.raises(ValueError, match="not one of tunnel's events"):
        record.event("teleport", "ui/gateway")
    commit(warned=["box.ts.net"])
    monkeypatch.setattr(record, "EVENTS_MAX", 400)
    for _ in range(20):
        record.event("repair", "ui/gateway", provider="tailscale", port=8461, now=NOW)
    rolled = record.events_path().with_name("events.jsonl.1")
    assert rolled.exists() and record.events_path().stat().st_size <= 400
    assert record.read().warned_ct == ("box.ts.net",)


def test_a_pending_copy_is_written_privately_then_renamed_or_deleted():
    copy = {"TCP": {"9000": {"HTTPS": True}}, "cli": "serve"}
    name = record.write_pending(9000, copy, now=NOW)
    assert name == "20261030T120000123456Z-9000.json" and record.is_copy_name(name)
    pending = record.pending_path(name)
    assert pending.name == "20261030T120000123456Z-9000.pending.json" and pending.exists()
    if os.name == "posix":
        assert stat.S_IMODE(pending.stat().st_mode) == 0o600
        assert stat.S_IMODE(record.displaced_dir().stat().st_mode) == 0o700
    assert record.read_copy(name) == copy
    named = entry(state=State.APPLYING, displaced=name)
    assert record.names_pending(named) and not record.names_pending(entry())
    second = record.write_pending(9000, {"other": 1}, now=NOW)
    assert second != name and second.endswith("-9000.json")
    assert [(d.name, d.pending) for d in record.listing()] == [(name, True), (second, True)]
    record.rename_pending(name)
    assert not pending.exists() and record.final_path(name).exists()
    assert not record.names_pending(named) and record.read_copy(name) == copy
    # Only a pending file is ever deleted here: a final copy stays.
    record.delete_pending(name)
    record.delete_pending(second)
    assert record.final_path(name).exists() and not record.pending_path(second).exists()
    assert [(d.name, d.port, d.pending) for d in record.listing()] == [(name, 9000, False)]


@pytest.mark.parametrize("name", ["../x-1.json", "20261030T120000123456Z-1.pending.json",
                                  "x.json", None])
def test_only_names_tunnel_writes_are_copy_names(name):
    assert not record.is_copy_name(name)
    if isinstance(name, str):
        with pytest.raises(ValueError):
            record.pending_path(name)


def test_an_address_says_who_can_reach_it():
    commit({"ui/gateway": entry(port=8461),
            "comms/connector": entry(reach="public", port=443, target=9100),
            "web/site": entry(provider="cloudflare", host="site.example.com", port=443,
                              state=State.APPLYING)}, core=True)
    rec = record.read()
    gateway = api.address("ui", "gateway")
    assert gateway == api.Address(
        module="ui", name="gateway", reach="own-devices", provider="tailscale",
        exposure="tailnet", url="https://box.tail1234.ts.net:8461/",
        host="box.tail1234.ts.net", port=8461, target=8461, state="on",
        change=rec.token("ui/gateway"), applied_at="2026-10-30T12:00:00Z")
    connector = api.address("comms", "connector")
    assert connector.exposure == "internet" and connector.url == "https://box.tail1234.ts.net/"
    site = api.address("web", "site")
    assert (site.exposure, site.state) == ("internet", "applying")
    assert api.address("ui", "nosuch") is None
    assert record.normal_host("Box.Tail1234.TS.net.") == "box.tail1234.ts.net"

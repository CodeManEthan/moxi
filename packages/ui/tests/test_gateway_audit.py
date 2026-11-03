"""The gateway's outcome table, table and export checks, act body check, redaction and audit
writer, as units."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from moxi_loopback.api import Field, Out, Page, Route, export

from moxi_ui.gateway import audit as audit_mod
from moxi_ui.gateway.outcome import outcome_of
from moxi_ui.gateway.shapes import (
    BadBody, acts_of, check_body, gateway_problem, handle, redact, shape_data, table,
)

VECTORS = Path(__file__).parents[1] / "web" / "test" / "outcome-vectors.json"
KEY = b"k" * 32


def test_every_shared_outcome_vector_maps_as_the_desk_maps_it() -> None:
    vectors = json.loads(VECTORS.read_text())
    assert vectors
    for item in vectors:
        assert outcome_of(item["status"], item["data"]) == item["outcome"], item


def _export(*routes: Route) -> dict:
    return export(Page(routes=routes))


READ = Route("/api/rows", "read", "pkg.mod:rows", gateway=True,
             returns={"rows": Out("list", items=Out("table", items={
                 "name": Out("string"), "log": Out("string", mark="path"),
                 "secret": Out("string", mark="redact", nullable=True)}))})
STATUS = Route("/api/job", "read", "pkg.mod:job", gateway=True,
               query={"job": Field("string")}, returns={"state": Out("string")})
ACT = Route("/api/go", "act", "pkg.mod:go", gateway=True, confirmed=True, status="/api/job", returns={},
            body={"name": Field("string", max=8), "n": Field("int", required=False)})
STREAM = Route("/api/events", "read", "pkg.mod:events", gateway=True, stream=True,
               topics={"rows": Out("table", items={"x": Out("int")})})


def test_the_table_holds_only_gateway_reads_and_never_a_stream_or_a_confirmed_act() -> None:
    desk = Route("/api/desk", "read", "pkg.mod:desk", returns={})
    built = table({"fixture": _export(READ, STATUS, ACT, STREAM, desk)},
                  entries=[("GET", "/sw.js", "asset")])
    paths = {entry.raw_path for entry in built}
    assert paths == {"/sw.js", "/m/fixture/api/rows", "/m/fixture/api/job"}
    assert acts_of(_export(READ, STATUS, ACT))["/api/go"]["confirmed"] is True


def test_an_export_breaking_the_act_rules_gets_no_mount_and_a_named_reason() -> None:
    plain = Route("/api/go", "act", "pkg.mod:go", gateway=True, returns={})
    assert "isn't a confirmed act" in gateway_problem(_export(READ, plain))
    desk_status = Route("/api/job", "read", "pkg.mod:job", query={"job": Field("string")},
                        returns={})
    bad = {"routes": [*_export(READ, ACT, STATUS)["routes"]], "assets": [], "format": 1}
    bad["routes"][2] = {**bad["routes"][2], "gateway": False}
    assert "status route /api/job" in gateway_problem(bad)
    heard = []
    built = table({"fixture": bad}, problem=lambda module, text: heard.append(module))
    assert len(built) == 0 and heard == ["fixture"]
    del desk_status


def test_redaction_changes_exactly_the_marked_values_at_every_depth() -> None:
    returns = shape_data(READ.returns)
    value = {"rows": [{"name": "a", "log": "/home/someone/.local/x.log", "secret": 5}]}
    out = redact(value, returns, KEY)
    row = out["rows"][0]
    assert row["name"] == "a" and row["secret"] is None
    assert row["log"] == handle("/home/someone/.local/x.log", KEY)
    assert row["log"].startswith("@h/") and row["log"].endswith("/x.log")
    assert handle("/a/b/", KEY) == handle("/a/b/", KEY) != handle("/a/c/", KEY)
    assert handle("/a/" + "é" * 200, KEY).encode().endswith("é".encode())
    assert len(handle("/a/" + "é" * 200, KEY).split("/", 2)[2].encode()) <= 255


def test_an_act_body_is_checked_against_its_declared_fields() -> None:
    route = _export(ACT, STATUS)["routes"][0]
    check_body({"name": "abc"}, route)
    for body, said in (({}, "name: missing"), ({"name": "x" * 9}, "over its max length"),
                       ({"name": "a", "n": True}, "not an integer"),
                       ({"name": "a", "other": 1}, "unknown key"), ([], "not a JSON object")):
        with pytest.raises(BadBody, match=said):
            check_body(body, route)


def _rows(folder: Path) -> list[dict]:
    rows = []
    for path in sorted(folder.glob("*.jsonl")):
        rows += [json.loads(line) for line in path.read_text().splitlines()]
    return rows


def test_the_writer_refuses_each_forbidden_field(tmp_path: Path) -> None:
    writer = audit_mod.Audit(tmp_path)
    for name in sorted(audit_mod.FORBIDDEN):
        with pytest.raises(ValueError):
            writer("signin", {name: "x"})
    assert _rows(audit_mod.audit_dir(tmp_path)) == []


def test_a_class_past_ten_rows_a_minute_becomes_one_summary(tmp_path: Path) -> None:
    now = [1_000_000.0]
    writer = audit_mod.Audit(tmp_path, clock=lambda: now[0])
    for _ in range(25):
        writer("origin", {"path": "/x"})
    for _ in range(3):
        writer.request({"path": "/m/ui/"}, signed_in=True)
    now[0] += 61
    writer.flush()
    rows = _rows(audit_mod.audit_dir(tmp_path))
    assert sum(1 for row in rows if row["kind"] == "origin") == 10
    assert [row["count"] for row in rows if row["kind"] == "summary"] == [15]
    assert sum(1 for row in rows if row["kind"] == "request") == 3
    folder = audit_mod.audit_dir(tmp_path)
    assert oct(os.stat(folder).st_mode & 0o777) == "0o700"
    assert all(oct(os.stat(p).st_mode & 0o777) == "0o600" for p in folder.glob("*.jsonl"))


def test_past_the_day_cap_a_class_writes_one_summary_an_hour(tmp_path: Path, monkeypatch) -> None:
    now = [1_000_000.0]
    monkeypatch.setattr(audit_mod, "DAY_CAP", 10)
    writer = audit_mod.Audit(tmp_path, clock=lambda: now[0])
    writer.request({"path": "/a"}, signed_in=True)
    for _ in range(30):
        writer("host", {"path": "/x"})
    now[0] += 3600
    writer.flush()
    rows = _rows(audit_mod.audit_dir(tmp_path))
    assert [row["kind"] for row in rows] == ["request", "summary"]
    assert rows[1]["count"] == 30 and rows[1]["per"] == "hour"


def test_a_day_file_past_two_mib_keeps_every_row_and_retention_deletes_old_days(
        tmp_path: Path) -> None:
    now = [1_000_000.0]
    writer = audit_mod.Audit(tmp_path, clock=lambda: now[0])
    for index in range(2200):
        writer.request({"path": "/m/ui/", "n": index, "pad": "x" * 1000}, signed_in=True)
    folder = audit_mod.audit_dir(tmp_path)
    (only,) = folder.glob("*.jsonl")
    assert only.stat().st_size > 2 * 1024 * 1024 and not list(folder.glob("*.1"))
    assert len(only.read_text().splitlines()) == 2200
    old = folder / "1970-01-01.jsonl"
    old.write_text("{}\n")
    now[0] += 40 * 86400
    writer.prune()
    assert not old.exists() and not only.exists()


def test_the_key_is_made_once_and_kept(tmp_path: Path) -> None:
    first = audit_mod.key(tmp_path)
    assert len(first) == 32 and audit_mod.key(tmp_path) == first
    path = tmp_path / "gateway" / "key"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    path.unlink()
    assert audit_mod.key(tmp_path) != first


def test_the_last_held_row_is_read_from_disk(tmp_path: Path) -> None:
    writer = audit_mod.Audit(tmp_path)
    assert audit_mod.last_held(tmp_path) is None
    writer("held", {"reason": "port 8461 was held by another program"})
    assert audit_mod.last_held(tmp_path)["reason"].startswith("port 8461")

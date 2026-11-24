"""A scoped sender is no member of the roster: `moxi comms agents` and its `--json`,
`api.roster()`, `api.roster_rows()`, the launch snapshot and comms' page list none, and the
roster deltas announce no join or leave for it, however often it sends."""

from __future__ import annotations

import json
import sys

import pytest

from moxi import exit as moxi_exit
from moxi_comms import api
from moxi_comms.adapters import shim_roster
from moxi_comms.paths import daemon_socket

from test_scoped_send import FORMED, PARENT, digest, send  # noqa: F401

NAME = FORMED.split("@")[0]


def named(text: str) -> bool:
    return NAME in text


@pytest.fixture
def sent(digest):  # noqa: F811
    """`main@proj-a` bound, and two sends under its scoped sender."""
    digest.shim(name="main", scope="proj-a")
    for text in ("one", "two"):
        result = send(PARENT, text)
        assert result.outcome == "sent", result.lines()
    return digest


def test_agents_and_its_json_list_no_scoped_sender(sent):
    code, out = sent.cli(["agents"])
    assert code == moxi_exit.OK, out
    assert PARENT in out and not named(out)
    code, out = sent.cli(["agents", "--json"])
    assert code == moxi_exit.OK, out
    rows = [r["qualified"] for r in json.loads(out)["rows"]]
    assert PARENT in rows and FORMED not in rows


def test_the_api_roster_and_its_rows_list_no_scoped_sender(sent):
    roster = api.roster()
    names = ([b.qualified for b in roster.bound] + [s.qualified for s in roster.standing]
             + [w.qualified for w in roster.waiting])
    assert PARENT in names and FORMED not in names
    rows, _ = api.roster_rows(with_roster=True)
    assert PARENT in [r.qualified for r in rows]
    assert FORMED not in [r.qualified for r in rows]
    assert FORMED not in sent.daemon.bindings


def test_the_launch_snapshot_lists_no_scoped_sender(sent):
    text, online = shim_roster.snapshot(daemon_socket(), env={})
    assert online and PARENT in text and not named(text)


def test_the_deltas_announce_no_join_or_leave_for_it(digest, tmp_path):  # noqa: F811
    digest.shim(name="main", scope="proj-a")
    state = tmp_path / "roster.json"
    shim_roster.delta(daemon_socket(), state, env={})          # the baseline
    assert send(PARENT, "one").outcome == "sent"
    envelope, online = shim_roster.delta(daemon_socket(), state, env={})
    assert online and envelope is None
    assert FORMED not in json.loads(state.read_text())


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="comms' page is served through loopback, as test_page does")
def test_the_page_lists_no_scoped_sender(sent):
    from test_page import get, run, served

    status, body = run(get(served(), "/api/roster"))
    assert status == 200
    rows = [r["qualified"] for r in body["rows"]]
    assert PARENT in rows and FORMED not in rows
    assert FORMED not in [w["qualified"] for w in body["waiting"]]

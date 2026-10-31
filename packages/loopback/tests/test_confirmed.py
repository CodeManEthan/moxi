"""Confirmed acts: the route shape, the status route's checks, the `Idempotency-Key` and
the key map, driven in process through `guard` and `app` with no listener."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sys
import textwrap

import pytest

from asgi_kit import PORT, SERVER_ID, TOKEN, drive, scope
from moxi_loopback import server
from moxi_loopback.api import gate_matrix
from moxi_loopback.check import upstream
from moxi_loopback.routes import Field, Out, Page, PageError, Route, Table, build_table, export
from moxi_loopback.server import app, guard, prove

PACKAGE = "lbconfirmed"

HANDLERS = '''
import asyncio

from moxi_loopback.server import Accepted, Conflict, Refused

STATE = {}
CALLS = []


async def act(call):
    CALLS.append((call.key, dict(call.body)))
    mode = STATE.get("mode", "accept")
    if mode == "wait":
        await STATE["release"].wait()
    elif mode == "conflict":
        raise Conflict("busy elsewhere")
    elif mode == "refused":
        raise Refused("not that one")
    elif mode == "boom":
        raise RuntimeError("the act broke")
    elif mode == "dict":
        return {}
    return Accepted(f"/api/status?job={len(CALLS)}")


async def plain(call):
    CALLS.append((call.key, dict(call.body)))
    return {"ok": True}


def status(call):
    return {"state": "done"}


async def wait(call):
    await STATE["release"].wait()
    return {}
'''


@pytest.fixture(scope="session")
def confirmed_package(tmp_path_factory):
    root = tmp_path_factory.mktemp("confirmed")
    pkg = root / PACKAGE
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "handlers.py").write_text(textwrap.dedent(HANDLERS))
    sys.path.insert(0, str(root))
    import importlib

    return importlib.import_module(f"{PACKAGE}.handlers")


@pytest.fixture
def h(confirmed_package):
    confirmed_package.STATE.clear()
    confirmed_package.CALLS.clear()
    yield confirmed_package
    confirmed_package.STATE.clear()


H = f"{PACKAGE}.handlers"
STATUS = Route("/api/status", "read", f"{H}:status", query={"job": Field("string")},
               returns={"state": Out("string")}, gateway=True)


def act(path="/api/act", **kw):
    kw.setdefault("confirmed", True)
    kw.setdefault("status", "/api/status")
    kw.setdefault("body", {"n": Field("int", required=False)})
    kw.setdefault("returns", {})
    return Route(path, "act", f"{H}:act", **kw)


def make_page(*extra):
    return Page(routes=(
        STATUS,
        act(body_cap=32),
        act("/api/other"),
        Route("/api/plain", "act", f"{H}:plain", body={"n": Field("int", required=False)},
              returns={"ok": Out("bool")}),
        Route("/api/wait", "read", f"{H}:wait", returns={}),
        *extra,
    ))


def served(page=None):
    inner = app(page or make_page(), module="usage", server_id=SERVER_ID)
    table, _ = build_table(inner.page)
    profile = upstream(table, token=TOKEN, port=PORT)
    return guard(prove(inner, token=TOKEN, server_id=SERVER_ID), lambda: profile), inner


def key():
    return secrets.token_urlsafe(32)


def post(path="/api/act", key_=None, *, keys=None):
    headers = [(b"content-type", b"application/json")]
    for value in (keys if keys is not None else [key_]):
        if value is not None:
            headers.append((b"idempotency-key", value.encode("latin-1")))
    return scope("POST", path, headers=headers)


def run(coro):
    return asyncio.run(coro)


# --- the route shape -------------------------------------------------------------------


def test_a_route_without_confirmed_is_not_confirmed_and_names_no_status():
    route = Route("/api/x", "act", f"{H}:plain", returns={})
    assert route.confirmed is False
    assert route.status is None


def test_confirmed_belongs_only_to_an_act():
    with pytest.raises(PageError, match="only an act is confirmed"):
        Route("/api/x", "read", f"{H}:status", returns={}, confirmed=True, status="/api/status")


def test_a_confirmed_act_names_its_status_route():
    with pytest.raises(PageError, match="names its status route"):
        act(status=None)


def test_only_a_confirmed_act_names_a_status_route():
    with pytest.raises(PageError, match="only a confirmed act"):
        act(confirmed=False)


@pytest.mark.parametrize("status", ["api/status", "/api/status?job=1", "/API", "", 3])
def test_a_status_is_a_route_path(status):
    with pytest.raises(PageError, match="status is a route path"):
        act(status=status)


def test_confirmed_is_true_or_false():
    with pytest.raises(PageError, match="confirmed is True or False"):
        act(confirmed=1)


def test_the_status_route_must_be_on_the_page():
    with pytest.raises(PageError, match="is not on this page"):
        Page(routes=(act(),))


@pytest.mark.parametrize("status_route, problem", [
    (Route("/api/status", "act", f"{H}:act", body={"job": Field("string")}, returns={}),
     "is not a plain read"),
    (Route("/api/status", "read", f"{H}:status", query={"job": Field("string")}, stream=True,
           topics={"state": Out("string")}),
     "is not a plain read"),
    (Route("/api/status", "read", f"{H}:status", returns={}), "takes a string job"),
    (Route("/api/status", "read", f"{H}:status", query={"job": Field("int")}, returns={}),
     "takes a string job"),
])
def test_the_status_route_is_a_read_taking_a_string_job(status_route, problem):
    with pytest.raises(PageError, match=problem):
        Page(routes=(status_route, act()))


def test_a_job_key_that_is_optional_still_counts():
    status = Route("/api/status", "read", f"{H}:status",
                   query={"job": Field("string", required=False)}, returns={})
    Page(routes=(status, act()))


def test_a_gateway_act_naming_a_desk_only_status_route_is_refused():
    desk_status = Route("/api/status", "read", f"{H}:status", query={"job": Field("string")},
                        returns={})
    with pytest.raises(PageError, match="the gateway reaches this act"):
        Page(routes=(desk_status, act(gateway=True)))


def test_a_desk_only_act_naming_a_gateway_status_route_is_accepted():
    page = Page(routes=(STATUS, act(gateway=False)))
    assert page.routes[1].gateway is False
    assert page.routes[0].gateway is True


def test_a_gateway_act_naming_a_gateway_status_route_is_accepted():
    Page(routes=(STATUS, act(gateway=True)))


# --- the export ------------------------------------------------------------------------


def test_the_export_carries_confirmed_and_status():
    data = json.loads(json.dumps(export(make_page())))
    routes = {route["path"]: route for route in data["routes"]}
    assert routes["/api/act"]["confirmed"] is True
    assert routes["/api/act"]["status"] == "/api/status"
    assert routes["/api/plain"]["confirmed"] is False
    assert routes["/api/plain"]["status"] is None
    assert routes["/api/status"]["confirmed"] is False
    assert routes["/api/status"]["status"] is None


def test_an_export_with_a_confirmed_act_still_mounts():
    table = Table()
    table.mount("/m/usage", json.loads(json.dumps(export(make_page()))))
    found = table.find("POST", "/m/usage/api/act")
    assert found is not None and found.kind == "act"


def test_gate_matrix_passes_a_page_with_a_confirmed_act():
    rows = gate_matrix(make_page())
    assert ("/api/act", "unknown key", 400, 400) in rows


# --- the key ---------------------------------------------------------------------------


@pytest.mark.parametrize("keys, reason", [
    ([], "missing header"),
    ([key(), key()], "repeated header"),
    (["A" * 42], "malformed header"),
    (["A" * 44], "malformed header"),
    (["A" * 42 + "+"], "malformed header"),
    (["A" * 42 + "="], "malformed header"),
    (["A" * 42 + "."], "malformed header"),
    (["A" * 42 + "é"], "malformed header"),
])
def test_a_missing_repeated_or_malformed_key_is_a_400_naming_the_header(h, keys, reason):
    chain, _ = served()
    answer = run(drive(chain, post(keys=keys), b"{}"))
    assert answer.status == 400
    assert answer.json() == {"error": f"{reason}: Idempotency-Key"}
    for value in keys:
        assert value.encode("latin-1") not in answer.body
    assert h.CALLS == []


def test_a_key_from_token_urlsafe_32_passes_and_reaches_the_handler_as_call_key(h):
    chain, _ = served()
    value = secrets.token_urlsafe(32)
    assert len(value) == 43
    answer = run(drive(chain, post(key_=value), b'{"n":1}'))
    assert answer.status == 202
    assert answer.json() == {"status": "/api/status?job=1"}
    assert h.CALLS == [(value, {"n": 1})]


def test_a_route_without_confirmed_behaves_as_before_and_ignores_the_header(h):
    chain, _ = served()
    plain = run(drive(chain, post("/api/plain", keys=[]), b"{}"))
    odd = run(drive(chain, post("/api/plain", keys=["x", "y"]), b"{}"))
    assert (plain.status, plain.json()) == (200, {"ok": True})
    assert (odd.status, odd.json()) == (200, {"ok": True})
    assert h.CALLS == [(None, {}), (None, {})]


# --- the key map -----------------------------------------------------------------------


def test_two_requests_with_one_key_sent_together_run_the_handler_once(h):
    chain, _ = served()
    value = key()

    async def go():
        h.STATE["mode"] = "wait"
        h.STATE["release"] = asyncio.Event()
        first = asyncio.create_task(drive(chain, post(key_=value), b"{}"))
        while not h.CALLS:
            await asyncio.sleep(0.005)
        second = await drive(chain, post(key_=value), b"{}")
        h.STATE["release"].set()
        return await first, second

    first, second = run(go())
    assert first.status == 202
    assert second.status == 409
    assert second.json() == {"conflict": "this act is still running"}
    assert len(h.CALLS) == 1


@pytest.mark.parametrize("mode, status, body", [
    ("accept", 202, {"status": "/api/status?job=1"}),
    ("conflict", 409, {"conflict": "busy elsewhere"}),
    ("refused", 422, {"refused": "not that one"}),
    ("boom", 500, None),
    ("dict", 500, None),
])
def test_a_repeat_after_finishing_gets_the_first_answer_with_no_call(h, mode, status, body):
    chain, _ = served()
    value = key()
    h.STATE["mode"] = mode
    first = run(drive(chain, post(key_=value), b'{"n":2}'))
    h.STATE["mode"] = "accept"
    again = run(drive(chain, post(key_=value), b'{"n":2}'))
    for answer in (first, again):
        assert answer.status == status
        if body is None:
            assert answer.body == b""
        else:
            assert answer.json() == body
            assert answer.header("content-type") == "application/json"
    assert len(h.CALLS) == 1


def test_a_confirmed_act_returning_a_dict_is_a_logged_500(h, caplog):
    chain, _ = served()
    h.STATE["mode"] = "dict"
    with caplog.at_level(logging.ERROR, logger="moxi.usage.page"):
        answer = run(drive(chain, post(key_=key()), b"{}"))
    assert answer.status == 500
    assert any("returned dict, not Accepted" in r.getMessage() for r in caplog.records)


def test_the_same_key_on_another_path_is_a_422(h):
    chain, _ = served()
    value = key()
    run(drive(chain, post(key_=value), b"{}"))
    other = run(drive(chain, post("/api/other", value), b"{}"))
    assert other.status == 422
    assert other.json() == {"refused": "this Idempotency-Key was used for another request"}
    assert len(h.CALLS) == 1


def test_the_same_key_with_another_body_is_a_422(h):
    chain, _ = served()
    value = key()
    run(drive(chain, post(key_=value), b'{"n":1}'))
    other = run(drive(chain, post(key_=value), b'{"n":2}'))
    assert other.status == 422
    assert other.json() == {"refused": "this Idempotency-Key was used for another request"}
    assert len(h.CALLS) == 1


def test_another_body_while_the_first_still_runs_is_a_422(h):
    chain, _ = served()
    value = key()

    async def go():
        h.STATE["mode"] = "wait"
        h.STATE["release"] = asyncio.Event()
        first = asyncio.create_task(drive(chain, post(key_=value), b'{"n":1}'))
        while not h.CALLS:
            await asyncio.sleep(0.005)
        other = await drive(chain, post(key_=value), b'{"n":2}')
        h.STATE["release"].set()
        await first
        return other

    assert run(go()).status == 422
    assert len(h.CALLS) == 1


def test_past_the_kept_finished_keys_the_oldest_is_new_again(h):
    chain, inner = served()
    values = [key() for _ in range(server.KEYS_KEPT + 1)]

    async def go():
        for value in values:
            await drive(chain, post(key_=value), b"{}")
        kept = await drive(chain, post(key_=values[1]), b"{}")
        calls = len(h.CALLS)
        oldest = await drive(chain, post(key_=values[0]), b"{}")
        return kept, calls, oldest

    kept, calls, oldest = run(go())
    assert server.KEYS_KEPT == 1024
    assert calls == len(values)  # The second key was still kept: answered with no call.
    assert kept.status == 202
    assert oldest.status == 202
    assert len(h.CALLS) == len(values) + 1
    assert h.CALLS[-1][0] == values[0]
    assert len(inner._keys) == server.KEYS_KEPT


def test_a_key_answered_503_past_the_in_flight_cap_is_not_claimed(h):
    chain, inner = served()
    value = key()

    async def go():
        h.STATE["release"] = asyncio.Event()
        tasks = [asyncio.create_task(drive(chain, scope(path="/api/wait")))
                 for _ in range(server.INFLIGHT_CAP)]
        while inner.activity.requests < server.INFLIGHT_CAP:
            await asyncio.sleep(0.005)
        over = await drive(chain, post(key_=value), b"{}")
        h.STATE["release"].set()
        await asyncio.gather(*tasks)
        again = await drive(chain, post(key_=value), b"{}")
        return over, again

    over, again = run(go())
    assert over.status == 503
    assert again.status == 202
    assert h.CALLS == [(value, {})]


@pytest.mark.parametrize("bad, status", [
    (b'{"n":' + b"1" * 40 + b"}", 413),
    (b'{"x":1}', 400),
    (b"not json", 400),
])
def test_a_key_answered_400_or_413_for_its_body_is_not_claimed(h, bad, status):
    chain, _ = served()
    value = key()
    first = run(drive(chain, post(key_=value), bad))
    again = run(drive(chain, post(key_=value), b'{"n":3}'))
    assert first.status == status
    assert again.status == 202
    assert h.CALLS == [(value, {"n": 3})]


def test_a_handler_cut_short_keeps_its_key_claimed_with_a_500(h):
    chain, _ = served()
    value = key()

    async def go():
        h.STATE["mode"] = "wait"
        h.STATE["release"] = asyncio.Event()
        first = asyncio.create_task(drive(chain, post(key_=value), b"{}"))
        while not h.CALLS:
            await asyncio.sleep(0.005)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        h.STATE["mode"] = "accept"
        return await drive(chain, post(key_=value), b"{}")

    again = run(go())
    assert again.status == 500
    assert len(h.CALLS) == 1

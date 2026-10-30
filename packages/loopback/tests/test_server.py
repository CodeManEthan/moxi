"""The base in process: `guard`, `app` and `prove`, driven as ASGI callables with no
listener. Dispatch, every status, the header set, assets, health, busy holds, the caps and
the idle counts."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time

import pytest

from asgi_kit import (
    NONCE,
    PORT,
    SERVER_ID,
    TOKEN,
    UNIX,
    assert_header_set,
    drive,
    json_headers,
    make_page,
    scope,
)
from moxi_loopback import server
from moxi_loopback.check import proof, upstream
from moxi_loopback.routes import PageError, Route, Table, build_table, export
from moxi_loopback.server import (
    CHECKED,
    HANDED,
    Accepted,
    Activity,
    Conflict,
    Refused,
    app,
    busy,
    guard,
    prove,
    settle,
)


def run(coro):
    return asyncio.run(coro)


def make_app(pages, **kwargs):
    kwargs.setdefault("module", "usage")
    kwargs.setdefault("server_id", SERVER_ID)
    page = kwargs.pop("page", None) or make_page()
    return app(page, **kwargs)


def served(pages, *, stopping=lambda: False, **kwargs):
    """The runner's chain: guard, then prove, then the app, on an upstream profile."""
    inner = make_app(pages, **kwargs)
    table, _ = build_table(inner.page)
    profile = upstream(table, token=TOKEN, port=PORT)
    return guard(prove(inner, token=TOKEN, server_id=SERVER_ID, stopping=stopping),
                 lambda: profile), inner


# --- dispatch and the statuses ----------------------------------------------------------


def test_a_read_answers_200_with_the_projected_json(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, scope(path="/api/summary", query=b"days=7")))
    assert answer.status == 200
    assert answer.json() == {"total": 7, "log": "/var/log/x"}
    assert answer.header("content-type") == "application/json"
    assert answer.body == b'{"total":7,"log":"/var/log/x"}'
    assert_header_set(answer)
    assert pages.CALLS == [("summary", {"days": 7})]


def test_an_act_answers_200_from_an_async_handler(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, scope("POST", "/api/refresh", headers=json_headers()),
                       b'{"force": true}'))
    assert answer.status == 200
    assert answer.json() == {"ok": True, "module": "usage"}


def test_a_body_sent_in_chunks_is_read_whole(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, scope("POST", "/api/refresh", headers=json_headers()),
                       chunks=[b'{"for', b'ce": ', b"false}"]))
    assert answer.status == 200
    assert pages.CALLS == [("refresh", {"force": False})]


def test_a_plain_def_handler_runs_off_the_loop(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, scope(path="/api/thread")))
    assert answer.status == 200
    assert answer.json()["name"] != threading.main_thread().name


def test_accepted_is_a_202_with_its_status_path(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, scope("POST", "/api/accepted", headers=json_headers()), b"{}"))
    assert answer.status == 202
    assert answer.json() == {"status": "/api/status"}


@pytest.mark.parametrize("query, key", [
    (b"bogus=1", "bogus"),
    (b"days=seven", "days"),
    (b"days=400", "days"),
    (b"days=1&days=2", "days"),
])
def test_an_unknown_or_mistyped_key_is_a_400_naming_the_key(pages, query, key):
    chain, _ = served(pages)
    answer = run(drive(chain, scope(path="/api/summary", query=query)))
    assert answer.status == 400
    assert key in answer.json()["error"]
    for value in (b"seven", b"400"):
        assert value not in answer.body
    assert pages.CALLS == []
    assert_header_set(answer)


@pytest.mark.parametrize("body", [b"{not json", b"[]", b'{"force": 1}', b'{"x": true}',
                                  b"\xff\xfe"])
def test_a_malformed_or_mistyped_body_is_a_400(pages, body):
    chain, _ = served(pages)
    answer = run(drive(chain, scope("POST", "/api/refresh", headers=json_headers()), body))
    assert answer.status == 400
    assert pages.CALLS == []


def test_headers_over_16_kib_are_a_400(pages):
    chain, _ = served(pages)
    padding = [(b"x-pad", b"a" * (server.HEADER_CAP + 1))]
    answer = run(drive(chain, scope(path="/api/summary", headers=padding)))
    assert answer.status == 400
    assert pages.CALLS == []
    assert_header_set(answer)


def test_guard_passes_a_megabyte_of_headers_and_app_answers_it_400(pages):
    # The head limit the shell raises on its own listener: guard and check let a large
    # head through to whatever is behind them, and the base's own count is app's alone.
    seen = []

    async def inner(sc, receive, send):
        seen.append(sc)

    table, _ = build_table(make_page())
    profile = upstream(table, token=TOKEN, port=PORT)
    headers = [(b"x-pad-%d" % n, b"a" * 1000) for n in range(1024)]
    sc = scope(path="/api/summary", headers=headers)
    run(drive(guard(inner, lambda: profile), sc))
    assert len(seen) == 1 and seen[0][CHECKED] is True

    answer = run(drive(guard(make_app(pages), lambda: profile), scope(path="/api/summary",
                                                                     headers=headers)))
    assert answer.status == 400
    assert pages.CALLS == []


def test_headers_just_under_the_cap_are_served(pages):
    chain, _ = served(pages)
    base = sum(len(k) + len(v) + 4 for k, v in scope()["headers"])
    pad = server.HEADER_CAP - base - len(b"x-pad") - 4
    answer = run(drive(chain, scope(path="/api/summary", headers=[(b"x-pad", b"a" * pad)])))
    assert answer.status == 200


def test_an_act_without_json_is_a_403_from_the_check(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, scope("POST", "/api/refresh"), b"{}"))
    assert answer.status == 403
    assert answer.body == b""
    assert_header_set(answer)
    assert pages.CALLS == []


@pytest.mark.parametrize("path", ["/api/nothing", "/api/%73ummary", "/API/summary",
                                  "/api/summary/", "/../api/summary"])
def test_a_path_outside_the_table_is_an_empty_404(pages, path):
    chain, _ = served(pages)
    answer = run(drive(chain, scope(path=path)))
    assert answer.status == 404
    assert answer.body == b""
    assert_header_set(answer)


def test_the_wrong_method_is_a_404_never_a_405(pages):
    chain, _ = served(pages)
    assert run(drive(chain, scope("POST", "/api/summary", headers=json_headers()), b"{}")).status == 404
    assert run(drive(chain, scope("GET", "/api/refresh"))).status == 404


def test_conflict_is_a_409_and_refused_a_422_with_its_text(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, scope("POST", "/api/conflict", headers=json_headers()), b"{}"))
    assert answer.status == 409
    assert answer.json() == {"conflict": "already running"}
    answer = run(drive(chain, scope("POST", "/api/refused", headers=json_headers()), b"{}"))
    assert answer.status == 422
    assert answer.json() == {"refused": "not today: déjà"}


def test_conflict_and_refused_carry_strings_only():
    with pytest.raises(TypeError):
        Conflict(3)
    with pytest.raises(TypeError):
        Refused(None)
    with pytest.raises(TypeError):
        Accepted(b"/x")


def test_an_exception_is_an_empty_500_logged_with_its_traceback(pages, caplog):
    chain, _ = served(pages)
    with caplog.at_level(logging.ERROR, logger="moxi.usage.page"):
        answer = run(drive(chain, scope(path="/api/boom")))
    assert answer.status == 500
    assert answer.body == b""
    assert_header_set(answer)
    assert "the handler broke" in caplog.text
    assert "Traceback" in caplog.text


def test_a_value_outside_its_shape_is_a_logged_500(pages, caplog):
    chain, _ = served(pages)
    pages.STATE["shape"] = {"total": "seven"}
    with caplog.at_level(logging.ERROR, logger="moxi.usage.page"):
        answer = run(drive(chain, scope(path="/api/shape")))
    assert answer.status == 500
    assert answer.body == b""
    assert "seven" not in caplog.text
    assert "/api/shape" in caplog.text


def test_a_value_that_is_not_a_table_is_a_500(pages):
    chain, _ = served(pages)
    pages.STATE["shape"] = ["a", "list"]
    assert run(drive(chain, scope(path="/api/shape"))).status == 500


def test_projection_keeps_a_nullable_null_and_drops_an_undeclared_key_once(pages, caplog):
    chain, _ = served(pages)
    pages.STATE["shape"] = {"total": 1, "note": None, "secret": "x"}
    with caplog.at_level(logging.WARNING, logger="moxi.usage.page"):
        first = run(drive(chain, scope(path="/api/shape")))
        second = run(drive(chain, scope(path="/api/shape")))
    assert first.json() == second.json() == {"total": 1, "note": None}
    dropped = [r for r in caplog.records if "secret" in r.getMessage()]
    assert len(dropped) == 1


def test_strict_projection_makes_an_undeclared_key_a_500(pages):
    chain, _ = served(pages, strict=True)
    pages.STATE["shape"] = {"total": 1, "secret": "x"}
    assert run(drive(chain, scope(path="/api/shape"))).status == 500
    pages.STATE["shape"] = {"total": 1}
    assert run(drive(chain, scope(path="/api/shape"))).status == 200


# --- the body cap ----------------------------------------------------------------------


def test_a_content_length_over_the_cap_is_a_413_before_any_read(pages):
    chain, _ = served(pages)
    reads = []

    async def go():
        sc = scope("POST", "/api/big", headers=json_headers() + [(b"content-length", b"999999")])

        async def receive():
            reads.append(1)
            return {"type": "http.request", "body": b"", "more_body": False}

        sent = []

        async def send(message):
            sent.append(message)

        await chain(sc, receive, send)
        return sent

    sent = run(go())
    assert sent[0]["status"] == 413
    assert reads == []
    assert pages.CALLS == []
    headers = dict(sent[0]["headers"])
    assert headers[b"cache-control"] == b"no-store"
    assert b"content-security-policy" in headers


def test_a_body_that_passes_the_cap_while_read_is_a_413_and_the_handler_never_runs(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, scope("POST", "/api/big", headers=json_headers()),
                       chunks=[b'{"text": "', b"a" * 10, b'"}']))
    assert answer.status == 413
    assert answer.body == b""
    assert_header_set(answer)
    assert pages.CALLS == []


def test_a_body_at_the_cap_is_read(pages):
    chain, _ = served(pages)
    body = b'{"text":"abcde"}'
    assert len(body) == 16
    answer = run(drive(chain, scope("POST", "/api/big", headers=json_headers()), body))
    assert answer.status == 200
    assert pages.CALLS == [("big", {"text": "abcde"})]


# --- guard -----------------------------------------------------------------------------


def test_guard_answers_a_refusal_with_an_empty_body_and_the_header_set(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, scope(path="/api/summary", token=False)))
    assert answer.status == 403
    assert answer.body == b""
    assert_header_set(answer)
    assert pages.CALLS == []


def test_guard_refuses_on_the_listener_it_arrived_on(pages):
    chain, _ = served(pages)
    # TCP needs the exact Host; a Unix socket runs no Host gate.
    assert run(drive(chain, scope(host=False))).status == 403
    assert run(drive(chain, scope(host=False, server=UNIX))).status == 200
    # A scope with no server tells guard nothing about the listener: refused.
    assert run(drive(chain, scope(extra={"server": None}))).status == 403
    assert run(drive(chain, scope(extra={"server": ("127.0.0.1", "4321")}))).status == 403


def test_guard_refuses_browser_headers_upstream(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, scope(headers=[(b"origin", b"http://127.0.0.1:%d" % PORT)])))
    assert answer.status == 403


def test_guard_reads_the_profile_once_per_request(pages):
    inner = make_app(pages)
    table, _ = build_table(inner.page)
    first = upstream(table, token=TOKEN, port=PORT)
    table2, _ = build_table(inner.page)
    other = "U" * 43
    second = upstream(table2, token=other, port=PORT)
    current = [first]
    reads = []

    def profile():
        reads.append(1)
        return current[0]

    chain = guard(inner, profile)
    assert run(drive(chain, scope())).status == 200
    assert reads == [1]
    current[0] = second
    assert run(drive(chain, scope())).status == 403
    sc = scope(token=False, headers=[(b"x-moxi-token", other.encode())])
    assert run(drive(chain, sc)).status == 200


def test_a_check_that_raises_is_a_500_with_the_header_set(pages, caplog):
    def profile():
        raise RuntimeError("no profile")

    with caplog.at_level(logging.ERROR):
        answer = run(drive(guard(make_app(pages), profile), scope()))
    assert answer.status == 500
    assert_header_set(answer)


def test_app_refuses_a_scope_without_the_checked_key(pages):
    inner = make_app(pages)
    answer = run(drive(inner, scope()))
    assert answer.status == 403
    assert answer.body == b""
    assert_header_set(answer)
    assert pages.CALLS == []
    assert inner.activity.requests == 0


def test_app_takes_the_checked_key_only_from_guard(pages):
    inner = make_app(pages)
    profile = upstream(build_table(inner.page)[0], token=TOKEN, port=PORT)
    # A client cannot set a scope key, but a refusal must not pass one through either.
    sc = scope(token=False, extra={CHECKED: True})
    assert run(drive(guard(inner, lambda: profile), sc)).status == 403


# --- the base in process, under a prefix -----------------------------------------------


def shell_chain(pages, **kwargs):
    inner = make_app(pages, module="ui", prefix="/m/ui", **kwargs)
    table = Table()
    table.mount("/m/ui", export(inner.page))
    table.add("GET", "/m/ui/api/health")
    profile = upstream(table, token=TOKEN, port=PORT)
    return guard(inner, lambda: profile), inner


def test_app_serves_under_its_prefix(pages):
    chain, _ = shell_chain(pages)
    answer = run(drive(chain, scope(path="/m/ui/api/summary", query=b"days=3")))
    assert answer.status == 200
    assert answer.json()["total"] == 3
    asset = run(drive(chain, scope(path="/m/ui/app.js")))
    assert asset.status == 200
    index = run(drive(chain, scope(path="/m/ui/")))
    assert index.body.startswith(b"<!doctype html>")


def test_app_without_its_prefix_is_a_404(pages):
    chain, inner = shell_chain(pages)
    assert run(drive(chain, scope(path="/api/summary"))).status == 404
    # And from app itself, past the check.
    for path in ["/api/summary", "/m/uiapi/summary", "/m/ui", "/m/ui/api/prove"]:
        assert run(drive(inner, scope(path=path, checked=True))).status == 404, path
    assert pages.CALLS == []


def test_app_answers_health_with_the_callers_server_id(pages):
    chain, _ = shell_chain(pages, version="0.3.0")
    answer = run(drive(chain, scope(path="/m/ui/api/health")))
    assert answer.status == 200
    data = answer.json()
    assert data["server_id"] == SERVER_ID
    assert data["module"] == "ui"
    assert data["version"] == "0.3.0"


def test_app_does_not_answer_prove(pages):
    inner = make_app(pages)
    sc = scope(path="/api/prove", checked=True, headers=[(b"x-moxi-nonce", NONCE.encode())])
    assert run(drive(inner, sc)).status == 404


def test_app_refuses_a_bad_prefix(pages):
    for prefix in ("m/ui", "/m/ui/", "/m/ü"):
        with pytest.raises(ValueError):
            make_app(pages, prefix=prefix)


def test_app_refuses_a_handler_it_cannot_load(pages):
    page = make_page(routes=(Route("/api/x", "read", "lbpages.handlers:missing", returns={}),))
    with pytest.raises(PageError, match="missing"):
        make_app(pages, page=page)
    page = make_page(routes=(Route("/api/x", "read", "lbpages.nowhere:x", returns={}),))
    with pytest.raises(PageError, match="nowhere"):
        make_app(pages, page=page)
    page = make_page(routes=(Route("/api/x", "read", "lbpages.handlers:STATE", returns={}),))
    with pytest.raises(PageError, match="not callable"):
        make_app(pages, page=page)


# --- prove -----------------------------------------------------------------------------


def prove_scope(*nonces, **kwargs):
    headers = [(b"x-moxi-nonce", n.encode()) for n in nonces]
    return scope(path="/api/prove", token=False, headers=headers, **kwargs)


def test_prove_answers_204_with_the_proof_and_no_token(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, prove_scope(NONCE)))
    assert answer.status == 204
    assert answer.body == b""
    assert answer.header("x-moxi-proof") == proof(TOKEN, SERVER_ID, NONCE)
    assert_header_set(answer)


@pytest.mark.parametrize("nonces", [(), ("short",), (NONCE, NONCE), ("N" * 42 + "!",)])
def test_a_missing_or_malformed_nonce_is_a_400(pages, nonces):
    chain, _ = served(pages)
    answer = run(drive(chain, prove_scope(*nonces)))
    assert answer.status == 400
    assert answer.header("x-moxi-proof") is None
    assert_header_set(answer)


def test_prove_answers_503_and_closes_once_the_stop_is_decided(pages):
    stopping = [False]
    chain, _ = served(pages, stopping=lambda: stopping[0])
    assert run(drive(chain, prove_scope(NONCE))).status == 204
    stopping[0] = True
    answer = run(drive(chain, prove_scope(NONCE)))
    assert answer.status == 503
    assert answer.header("connection") == "close"
    assert answer.header("x-moxi-proof") is None
    assert_header_set(answer)


def test_prove_still_runs_the_other_gates(pages):
    chain, _ = served(pages)
    assert run(drive(chain, prove_scope(NONCE, host=False))).status == 403
    sc = prove_scope(NONCE)
    sc["headers"].append((b"cookie", b"a=b"))
    assert run(drive(chain, sc)).status == 403


def test_prove_refuses_an_unchecked_scope_and_a_bad_token(pages):
    layer = prove(make_app(pages), token=TOKEN, server_id=SERVER_ID)
    assert run(drive(layer, prove_scope(NONCE))).status == 403
    with pytest.raises(ValueError):
        prove(make_app(pages), token="", server_id=SERVER_ID)


# --- assets ----------------------------------------------------------------------------


def test_an_asset_has_its_type_a_strong_etag_and_no_cache(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, scope(path="/app.js")))
    assert answer.status == 200
    assert answer.body == b"console.log('page');\n"
    assert answer.header("content-type") == "text/javascript; charset=utf-8"
    import hashlib

    tag = '"' + hashlib.sha256(answer.body).hexdigest()[:16] + '"'
    assert answer.header("etag") == tag
    assert_header_set(answer, cache="no-cache")


def test_slash_serves_index_html(pages):
    chain, _ = served(pages)
    answer = run(drive(chain, scope(path="/")))
    assert answer.status == 200
    assert answer.header("content-type") == "text/html; charset=utf-8"
    assert answer.body == b"<!doctype html><title>page</title>"


@pytest.mark.parametrize("value, status", [
    (None, 304), ('"0000000000000000"', 200), ("*", 304), ("W/{tag}", 304),
    ('"0000000000000000", {tag}', 304),
])
def test_if_none_match_gives_a_304(pages, value, status):
    chain, _ = served(pages)
    tag = run(drive(chain, scope(path="/style.css"))).header("etag")
    value = tag if value is None else value.format(tag=tag)
    answer = run(drive(chain, scope(path="/style.css", headers=[(b"if-none-match", value.encode())])))
    assert answer.status == status
    if status == 304:
        assert answer.body == b""
        assert answer.header("etag") == tag
        assert_header_set(answer, cache="no-cache")


@pytest.mark.parametrize("path", ["/../app.js", "/%2e%2e/app.js", "//app.js", "/app.js/",
                                  "/web/app.js", "/App.js", "/app.js%00", "/.%2fapp.js"])
def test_asset_traversal_forms_are_404(pages, path):
    chain, inner = served(pages)
    assert run(drive(chain, scope(path=path))).status == 404
    assert run(drive(inner, scope(path=path, checked=True))).status == 404


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="a file mode that refuses reading")
def test_an_asset_that_cannot_be_read_is_a_refused_page(tmp_path, monkeypatch):
    from moxi_loopback.routes import Page

    web = tmp_path / "pagepkg_unreadable" / "web"
    web.mkdir(parents=True)
    (web.parent / "__init__.py").write_text("")
    (web / "app.js").write_text("x")
    (web / "app.js").chmod(0)
    monkeypatch.syspath_prepend(str(tmp_path))
    page = Page(routes=(), assets="web", package="pagepkg_unreadable")
    with pytest.raises(PageError, match="app.js cannot be read"):
        app(page, module="m", server_id="s")


# --- health ----------------------------------------------------------------------------


def test_health_reports_the_server_and_its_check(pages):
    chain, _ = served(pages, version="1.2.3")
    answer = run(drive(chain, scope(path="/api/health")))
    assert answer.status == 200
    data = answer.json()
    assert data["ok"] is True
    assert data["check"] == [True, "fine"]
    assert data["module"] == "usage"
    assert data["server_id"] == SERVER_ID
    assert data["version"] == "1.2.3"
    assert isinstance(data["pid"], int)
    assert data["uptime_s"] >= 0
    assert data["open"] == {"requests": 0, "streams": 0, "busy": []}


def test_health_needs_the_token(pages):
    chain, _ = served(pages)
    assert run(drive(chain, scope(path="/api/health", token=False))).status == 403


def test_a_failing_health_check_is_reported_and_the_route_still_answers(pages):
    chain, _ = served(pages)
    pages.STATE["health"] = (False, "disk full")
    assert run(drive(chain, scope(path="/api/health"))).json()["check"] == [False, "disk full"]
    pages.STATE["health_raises"] = True
    data = run(drive(chain, scope(path="/api/health"))).json()
    assert data["check"] == [False, "raised"]
    assert data["ok"] is False
    pages.STATE.pop("health_raises")
    pages.STATE["health"] = "yes"
    assert run(drive(chain, scope(path="/api/health"))).json()["check"][0] is False


def test_a_health_check_that_blocks_past_its_limit_answers_timed_out(pages, monkeypatch):
    monkeypatch.setattr(server, "HEALTH_LIMIT", 0.2)
    chain, _ = served(pages)
    pages.STATE["health_sleep"] = 0.6
    started = time.monotonic()
    answer = run(drive(chain, scope(path="/api/health")))
    assert time.monotonic() - started < 0.6 + 0.3
    assert answer.status == 200
    assert answer.json()["check"] == [False, "timed out"]


def test_a_page_without_health_reports_ok_and_no_check(pages):
    chain, _ = served(pages, page=make_page(health=None))
    data = run(drive(chain, scope(path="/api/health"))).json()
    assert data["ok"] is True and data["check"] is None


# --- the idle counts and busy holds ------------------------------------------------------


def test_health_probes_and_proofs_never_count_and_never_restart_an_idle_run(pages):
    chain, inner = served(pages)
    activity = inner.activity

    async def go():
        first = activity.quiet()
        await asyncio.sleep(0.05)
        await drive(chain, scope(path="/api/health"))
        await drive(chain, prove_scope(NONCE))
        await drive(chain, scope(path="/api/summary", token=False))
        return first, activity.quiet()

    first, after = run(go())
    assert first is not None and after is not None
    assert after >= first + 0.05


def test_a_counted_request_restarts_the_idle_run_when_it_ends(pages):
    chain, inner = served(pages)
    activity = inner.activity

    async def go():
        await asyncio.sleep(0.05)
        before = activity.quiet()
        pages.STATE["wait"] = asyncio.Event()
        task = asyncio.get_running_loop().create_task(drive(chain, scope(path="/api/wait")))
        await asyncio.sleep(0.02)
        during = (activity.quiet(), activity.requests, activity.open())
        pages.STATE["wait"].set()
        await task
        return before, during, activity.quiet()

    before, during, after = run(go())
    assert before >= 0.05
    assert during[0] is None and during[1] == 1
    assert during[2]["requests"] == 1
    assert after < before


def test_a_busy_hold_holds_idle_from_any_thread(pages):
    chain, inner = served(pages)
    activity = inner.activity

    async def go():
        pages.STATE["held"] = threading.Event()
        pages.STATE["release"] = threading.Event()
        task = asyncio.get_running_loop().create_task(drive(chain, scope(path="/api/hold")))
        while not pages.STATE["held"].is_set():
            await asyncio.sleep(0.01)
        health = (await drive(chain, scope(path="/api/health"))).json()
        pages.STATE["release"].set()
        await task
        return health

    health = run(go())
    assert health["open"]["busy"] == ["launch"]

    seen = []

    def work():
        with busy("import"):
            seen.append(activity.quiet())
    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    assert seen == [None]
    assert activity.quiet() is not None and activity.quiet() < 0.5


def test_a_busy_hold_needs_a_name():
    with pytest.raises(ValueError):
        with busy(""):
            pass


def test_a_handed_off_request_holds_quiet_until_guard_settles_it(pages):
    chain, inner = served(pages)
    activity = inner.activity

    async def go():
        results = []
        for sc in (scope(path="/api/health"), scope(token=False), scope()):
            activity.hand_off(sc)
            activity.hand_off(sc)  # A second hand-off of one request counts once.
            results.append((activity.pending, activity.quiet()))
            await drive(chain, sc)
            results.append((activity.pending, HANDED in sc))
        return results

    results = run(go())
    assert results == [(1, None), (0, False)] * 3
    sc = scope()
    activity.hand_off(sc)
    settle(sc)
    settle(sc)
    assert activity.pending == 0


def test_a_handed_off_request_is_counted_in_its_tasks_first_step(pages):
    # The hand-off and the count meet in one loop step, so an idle look between them
    # finds the request either pending or counted, never neither.
    chain, inner = served(pages)
    activity = inner.activity

    async def go():
        pages.STATE["wait"] = asyncio.Event()
        sc = scope(path="/api/wait")
        activity.hand_off(sc)
        task = asyncio.get_running_loop().create_task(drive(chain, sc))
        before = (activity.pending, activity.requests)
        await asyncio.sleep(0)
        after = (activity.pending, activity.requests)
        pages.STATE["wait"].set()
        await task
        return before, after

    assert run(go()) == ((1, 0), (0, 1))


def test_activity_quiet_counts_from_the_last_end():
    activity = Activity()
    now = time.monotonic()
    assert activity.quiet(now + 10) >= 10
    activity._enter("streams")
    assert activity.quiet() is None
    activity._leave("streams")
    assert activity.quiet() < 0.5


# --- the caps --------------------------------------------------------------------------


def test_the_in_flight_cap_answers_503_with_retry_after(pages):
    chain, inner = served(pages)

    async def go():
        pages.STATE["wait"] = asyncio.Event()
        loop = asyncio.get_running_loop()
        tasks = [loop.create_task(drive(chain, scope(path="/api/wait")))
                 for _ in range(server.INFLIGHT_CAP)]
        while inner.activity.requests < server.INFLIGHT_CAP:
            await asyncio.sleep(0.01)
        over = await drive(chain, scope(path="/api/summary"))
        probe = await drive(chain, scope(path="/api/health"))
        pages.STATE["wait"].set()
        done = await asyncio.gather(*tasks)
        after = await drive(chain, scope(path="/api/summary"))
        return over, probe, done, after

    over, probe, done, after = run(go())
    assert over.status == 503
    assert over.header("retry-after") == "1"
    assert over.body == b""
    assert_header_set(over)
    assert probe.status == 503
    assert all(answer.status == 200 for answer in done)
    assert after.status == 200
    assert inner.activity.requests == 0


# --- logs ------------------------------------------------------------------------------


def test_one_debug_line_per_request_with_no_query_and_no_token(pages, caplog):
    chain, _ = served(pages)
    with caplog.at_level(logging.DEBUG):
        run(drive(chain, scope(path="/api/summary", query=b"name=s3cr3t-query")))
        run(drive(chain, scope(path="/api/summary", token=False)))
        run(drive(chain, scope(path="/api/nothing")))
        run(drive(chain, prove_scope(NONCE)))
        run(drive(chain, scope(path="/api/health")))
        run(drive(chain, scope("POST", "/api/refresh", headers=json_headers()), b'{"force":true}'))
    text = caplog.text
    assert TOKEN not in text
    assert proof(TOKEN, SERVER_ID, NONCE) not in text
    assert "s3cr3t-query" not in text
    page_lines = [r.getMessage() for r in caplog.records if r.name == "moxi.usage.page"]
    assert "GET /api/summary 200" in page_lines[0]
    assert page_lines[0].endswith("ms")
    assert any(line.startswith("POST /api/refresh 200") for line in page_lines)


def test_a_blocking_async_handler_makes_the_loop_guard_log_once(pages, caplog):
    from moxi import aio

    chain, _ = served(pages)
    pages.STATE["stall"] = 0.7

    async def main():
        await asyncio.sleep(0.1)  # Let the guard take its first look,
        answer = await drive(chain, scope(path="/api/stall"))
        await asyncio.sleep(0.1)  # and its look after the block.
        return answer

    with caplog.at_level(logging.WARNING, logger="moxi.aio"):
        answer = aio.run(main, workers=4, slow=0.5)
    assert answer.status == 200
    blocked = [r for r in caplog.records if r.name == "moxi.aio"]
    assert len(blocked) == 1
    assert "blocked" in blocked[0].getMessage()


# --- import weight ---------------------------------------------------------------------


def test_importing_the_server_loads_neither_starlette_nor_uvicorn():
    import subprocess
    import sys

    code = ("import sys, moxi_loopback.server; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('starlette', 'uvicorn')))")
    out = subprocess.run([sys.executable, "-P", "-c", code], capture_output=True, text=True,
                         check=True)
    assert json.loads(out.stdout.replace("'", '"')) == []


def test_the_server_imports_starlette_and_uvicorn_only_inside_functions_and_keeps_one_clock():
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(server.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            for name in names:
                assert name.split(".")[0] not in ("starlette", "uvicorn"), name
    # Every window and deadline is on the monotonic clock, never the wall clock.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "time":
                assert node.attr == "monotonic", f"time.{node.attr}"

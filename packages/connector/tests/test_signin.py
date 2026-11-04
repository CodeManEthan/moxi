"""The sign-in, driven by a fake Claude client: registration, the page's entry in place of
the page, `/token`, the bearer guard, rotation and reuse, revocation, the origin swap and the
limits on callers without a token."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import ORIGIN
from kit import HTTPX, initialize, modern, request, rpc
from signin_kit import CALLBACK, CALLBACKS, pkce

from moxi_connector import grants
from moxi_connector.oauth import (AUTH_METHODS, CAPS, PENDING_MAX, PENDING_MISSES,
                                  PageError, RedirectError, TooMany, UNKNOWN_CLIENT)
from moxi_connector.origin import ASM_PATH, PRM_PATH, Origin
from moxi_connector.signin import GRANT

OTHER = Origin("other.example.net", 8443)


def run(coro):
    return asyncio.run(coro)


# -- the whole sign-in ----------------------------------------------------------------------

def test_the_full_sign_in_from_registration_to_tools_list(door):
    async def main():
        d = await door()
        o = d.origin
        got = await d.mcp(None)
        assert got.status == 401
        assert f'resource_metadata="{o.resource_metadata_url}"' in got.header("www-authenticate")
        prm = await request(d.front, "GET", PRM_PATH, user_agent=HTTPX)
        assert prm.status == 200 and prm.json() == {
            "resource": o.resource, "authorization_servers": [o.issuer],
            "bearer_methods_supported": ["header"]}
        asm = await request(d.front, "GET", ASM_PATH, user_agent=HTTPX)
        assert asm.status == 200 and asm.json()["authorization_endpoint"] == o.url + "/authorize"
        client = await d.client()
        assert client["client_id"] and client["client_secret"]
        verifier, query = d.page(client)
        assert query["iss"] == o.issuer
        started = time.perf_counter()
        got = await d.token(client, query["code"], verifier)
        took = time.perf_counter() - started
        assert got.status == 200, got.body
        assert took < 1.0
        assert got.header("cache-control") == "no-store"
        tokens = got.json()
        assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
        assert tokens["scope"] == "mcp" and tokens["refresh_token"]
        access = tokens["access_token"]
        # Both ways in, then the lists, all as Claude-User.
        got = await d.mcp(access, initialize())
        assert got.status == 200 and "tools" in got.json()["result"]["capabilities"]
        body, headers = modern("server/discover")
        got = await d.mcp(access, body, headers=headers)
        assert got.status == 200 and "tools" in got.json()["result"]["capabilities"]
        got = await d.mcp(access, rpc("tools/list", rid=2))
        assert [t["name"] for t in got.json()["result"]["tools"]] == ["echo"]
        for method, key in (("prompts/list", "prompts"), ("resources/list", "resources")):
            got = await d.mcp(access, rpc(method, rid=4))
            assert got.json()["result"][key] == []
        # The grant reaches the call's row: id, client, and the label the page gave it.
        gid = d.grant_id()
        mcp_rows = [r for r in d.rows if r.get("kind") == "request" and r["route"] == "mcp"
                    and r["status"] == 200]
        assert mcp_rows and all(r["grant"] == gid and r["client"] == client["client_id"]
                                for r in mcp_rows)
        kinds = [r["kind"] for r in d.rows]
        assert "register" in kinds and "token" in kinds
        await d.close()
    run(main())


def test_the_guard_hands_the_grant_to_the_mcp_app(door):
    async def main():
        d = await door()
        _, tokens = await d.sign_in(label="desk")
        seen = []
        guard = d.oauth.current.mount().guard

        async def app(scope, receive, send):
            seen.append(scope[GRANT])
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        from kit import scope as make_scope
        s = make_scope(headers=[("authorization", f"Bearer {tokens['access_token']}")])

        async def receive():
            return {"type": "http.request", "body": b""}

        sent = []

        async def send(m):
            sent.append(m)

        await guard(app)(s, receive, send)
        assert seen[0].id == d.grant_id() and seen[0].label == "desk"
        assert seen[0].client_id
        await d.close()
    run(main())


def test_the_grants_file_keeps_no_token_code_or_secret_in_clear(door):
    async def main():
        d = await door()
        client = await d.client()
        verifier, query = d.page(client)
        tokens = (await d.token(client, query["code"], verifier)).json()
        again = (await d.refresh(client, tokens["refresh_token"])).json()
        text = d.oauth.store.path.read_text()
        for secret in (client["client_secret"], query["code"], verifier,
                       tokens["access_token"], tokens["refresh_token"],
                       again["access_token"], again["refresh_token"]):
            assert secret not in text
        assert grants.digest(again["refresh_token"]) in text
        mode = d.oauth.store.path.stat().st_mode & 0o777
        assert mode == 0o600
        await d.close()
    run(main())


def test_a_client_with_basic_auth_or_none_signs_in_and_a_wrong_secret_is_401(door):
    async def main():
        d = await door()
        client = await d.client(method="client_secret_basic")
        verifier, query = d.page(client)
        creds = base64.b64encode(
            f"{client['client_id']}:{client['client_secret']}".encode()).decode()
        form = {"grant_type": "authorization_code", "code": query["code"],
                "redirect_uri": CALLBACK, "client_id": client["client_id"],
                "code_verifier": verifier}
        got = await request(d.front, "POST", "/token", form=form, user_agent=HTTPX,
                            headers=[("authorization", f"Basic {creds}")])
        assert got.status == 200, got.body
        public = await d.client(method="none")
        assert "client_secret" not in public
        await d.sign_in(public)
        wrong = {**(await d.client()), "client_secret": "not-it"}
        verifier, query = d.page(wrong)
        got = await d.token(wrong, query["code"], verifier)
        assert got.status == 401 and got.json()["error"] == "invalid_client"
        await d.close()
    run(main())


def test_a_code_is_single_use_bound_to_its_client_and_verifier_and_lives_60_s(door, clock):
    async def main():
        d = await door()
        client = await d.client()
        verifier, query = d.page(client)
        bad = await d.token(client, query["code"], pkce()[0])
        assert bad.status == 400 and bad.json()["error"] == "invalid_grant"
        other = await d.client()
        got = await d.token(other, query["code"], verifier)
        assert got.json()["error"] == "invalid_grant"
        assert (await d.token(client, query["code"], verifier)).status == 200
        assert (await d.token(client, query["code"], verifier)).json()["error"] == \
            "invalid_grant"
        verifier, query = d.page(client)
        clock.advance(61)
        assert (await d.token(client, query["code"], verifier)).json()["error"] == \
            "invalid_grant"
        await d.close()
    run(main())


# -- routes ---------------------------------------------------------------------------------

def test_each_route_is_served_by_its_one_handler_and_the_metadata_comes_from_o(door):
    async def main():
        marks = []

        def page(under):
            async def app(scope, receive, send):
                marks.append((under.origin, scope["method"]))
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"page"})
            return app

        d = await door(page=page)
        o = d.origin
        assert sorted(d.oauth.current.mount().routes) == sorted(
            [PRM_PATH, ASM_PATH, "/register", "/authorize", "/token", "/revoke"])
        asm = (await request(d.front, "GET", ASM_PATH)).json()
        assert asm == {
            "issuer": o.issuer, "authorization_endpoint": o.url + "/authorize",
            "token_endpoint": o.url + "/token", "registration_endpoint": o.url + "/register",
            "revocation_endpoint": o.url + "/revoke", "scopes_supported": ["mcp"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": AUTH_METHODS,
            "revocation_endpoint_auth_methods_supported": AUTH_METHODS,
            "authorization_response_iss_parameter_supported": True}
        for method in ("GET", "POST"):
            got = await request(d.front, method, "/authorize")
            assert got.body == b"page"
        assert marks == [(o, "GET"), (o, "POST")]
        for path, allowed in ((PRM_PATH, "GET"), (ASM_PATH, "GET"), ("/register", "POST"),
                              ("/token", "POST"), ("/revoke", "POST"),
                              ("/authorize", "GET, POST")):
            wrong = "DELETE"
            got = await request(d.front, wrong, path)
            assert (got.status, got.header("allow")) == (405, allowed), path
        assert (await request(d.front, "GET", "/mcp")).status == 405
        for path in ("/", "/oauth/authorize", "/.well-known/openid-configuration",
                     "/token/", "/.well-known/oauth-protected-resource"):
            got = await request(d.front, "GET", path)
            assert (got.status, got.body) == (404, b""), path
        await d.close()
    run(main())


def test_without_the_page_authorize_answers_503_and_signs_nobody_in(door):
    async def main():
        d = await door()
        got = await request(d.front, "GET", "/authorize")
        assert got.status == 503
        await d.close()
    run(main())


# -- the origin -----------------------------------------------------------------------------

def test_an_origin_change_swaps_the_sign_ins_values_drops_codes_and_stales_grants(door):
    async def main():
        d = await door()
        client, tokens = await d.sign_in()
        access = tokens["access_token"]
        gid = d.grant_id()
        old = d.oauth.current
        verifier, query = d.page(client)               # a code made under the old origin
        waiting = old.open(old.check({                  # and a pending request
            "client_id": client["client_id"], "redirect_uri": CALLBACK,
            "response_type": "code", "code_challenge": pkce()[1],
            "code_challenge_method": "S256", "state": "s"}))
        await d.swap(OTHER)
        new = d.oauth.current
        assert new is not old and new.origin == OTHER
        # Both documents, the challenge and the resource check are the new origin's.
        prm = (await request(d.front, "GET", PRM_PATH, **d.at())).json()
        assert prm["resource"] == OTHER.resource and prm["authorization_servers"] == [OTHER.url]
        asm = (await request(d.front, "GET", ASM_PATH, **d.at())).json()
        assert asm["issuer"] == OTHER.url and asm["token_endpoint"] == OTHER.url + "/token"
        got = await d.mcp(None)
        assert f'resource_metadata="{OTHER.resource_metadata_url}"' in \
            got.header("www-authenticate")
        # The old grant is stale: 401 here, a warn in the doctor's view, a row in the log.
        assert (await d.mcp(access)).status == 401
        await asyncio.sleep(0)
        for task in list(d.oauth._tasks):
            await task
        assert [r for r in d.rows if r["kind"] == "grant-stale"][0]["grant"] == gid
        view = grants.listing(d.oauth.store.view(), resource=OTHER.resource, now=time.time(),
                              days=30)
        assert view[0]["stale"]
        # The old origin's pending request and code are gone.
        assert old.pending == {} and old.codes == {}
        with pytest.raises(KeyError):
            old.issue(waiting)
        got = await d.token(client, query["code"], verifier)
        assert got.json()["error"] == "invalid_grant"
        # A refresh of the stale grant is refused too, and the grant is kept.
        assert (await d.refresh(client, tokens["refresh_token"])).status == 400
        # Back to the first origin, the grant answers again.
        await d.swap(ORIGIN)
        assert (await d.mcp(access)).status == 200
        await d.close()
    run(main())


def test_a_token_made_under_one_origin_is_refused_by_the_resource_check_of_another(door):
    async def main():
        d = await door()
        _, tokens = await d.sign_in()
        await d.swap(OTHER)
        provider = d.oauth.current.provider
        # The verifier refuses the stale grant, and the SDK's resource check would too.
        assert await provider.load_access_token(tokens["access_token"]) is None
        from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
        backend = BearerAuthBackend(provider, resource_server_url=OTHER.resource)
        assert not backend._issued_for_this_resource(ORIGIN.resource)
        assert backend._issued_for_this_resource(OTHER.resource)
        await d.close()
    run(main())


# -- registration ---------------------------------------------------------------------------

def test_registration_refuses_another_redirect_uri(door):
    async def main():
        d = await door()
        got = await d.register(redirect_uris=["https://evil.example.com/cb"])
        assert got.status == 400 and got.json()["error"] == "invalid_redirect_uri"
        got = await d.register(redirect_uris=[CALLBACK, "https://evil.example.com/cb"])
        assert got.json()["error"] == "invalid_redirect_uri"
        assert d.oauth.store.view()["clients"] == {}
        assert (await d.register(redirect_uris=list(CALLBACKS))).status == 201
        await d.close()
    run(main())


def test_the_65th_unfinished_client_is_refused_and_one_is_dropped_after_24_h(door, clock):
    async def main():
        d = await door()
        first = None
        for n in range(grants.UNFINISHED_MAX):
            if n and n % CAPS["register"] == 0:
                clock.advance(60)
            got = await d.register()
            assert got.status == 201, (n, got.body)
            first = first or got.json()["client_id"]
        clock.advance(60)
        got = await d.register()
        assert got.status == 400 and got.json() == {
            "error": "invalid_client_metadata",
            "error_description": "too many clients; try again later"}
        # A day after the first registered, it is dropped, and room is made.
        clock.advance(grants.UNFINISHED_KEEP)
        assert (await d.register()).status == 201
        assert d.oauth.store.client(first) is None
        await d.close()
    run(main())


def test_a_client_that_held_a_grant_is_kept_after_it_expires_or_is_revoked(door, clock):
    async def main():
        d = await door()
        client, _ = await d.sign_in()
        cid = client["client_id"]
        gid = d.grant_id()
        clock.advance(grants.UNFINISHED_KEEP + 60)
        assert (await d.register()).status == 201        # its drop runs here
        assert d.oauth.store.client(cid)["held"]
        assert d.oauth.store.revoke(gid, by="cli")
        clock.advance(31 * grants.DAY)
        d.oauth.store.tidy()
        assert d.oauth.store.grant(gid) is None and d.oauth.store.client(cid) is not None
        # The same client signs in again with a fresh proof.
        await d.sign_in(client)
        await d.close()
    run(main())


def test_held_clients_do_not_count_against_the_64(door, clock):
    async def main():
        d = await door()
        for n in range(3):
            await d.sign_in()
            clock.advance(60)
        store = d.oauth.store
        assert sum(1 for c in store.view()["clients"].values() if c["held"]) == 3
        for n in range(grants.UNFINISHED_MAX):
            if n and n % CAPS["register"] == 0:
                clock.advance(60)
            assert (await d.register()).status == 201
        await d.close()
    run(main())


# -- tokens ---------------------------------------------------------------------------------

def test_an_access_token_expires_after_an_hour_and_a_refresh_rotates_the_pair(door, clock):
    async def main():
        d = await door()
        client, tokens = await d.sign_in()
        clock.advance(3601)
        assert (await d.mcp(tokens["access_token"])).status == 401
        got = await d.refresh(client, tokens["refresh_token"])
        assert got.status == 200 and got.header("cache-control") == "no-store"
        new = got.json()
        assert new["access_token"] != tokens["access_token"]
        assert new["refresh_token"] != tokens["refresh_token"]
        assert (await d.mcp(new["access_token"])).status == 200
        assert [r["grace"] for r in d.rows if r["kind"] == "refresh"] == [False]
        await d.close()
    run(main())


def test_a_retired_refresh_token_in_its_grace_answers_a_new_pair_once(door, clock):
    async def main():
        d = await door()
        client, first = await d.sign_in()
        lost = (await d.refresh(client, first["refresh_token"])).json()   # answer lost
        clock.advance(120)
        got = await d.refresh(client, first["refresh_token"])
        assert got.status == 200, got.body
        third = got.json()
        assert (await d.mcp(third["access_token"])).status == 200
        # The unused replacement was retired with it: presenting it now is reuse.
        assert (await d.refresh(client, lost["refresh_token"])).status == 400
        assert (await d.mcp(third["access_token"])).status == 401
        assert [r["kind"] for r in d.rows if r["kind"].startswith("refresh")] == [
            "refresh", "refresh", "refresh-reuse"]
        await d.close()
    run(main())


def test_a_grace_is_given_once(door, clock):
    async def main():
        d = await door()
        client, first = await d.sign_in()
        await d.refresh(client, first["refresh_token"])
        assert (await d.refresh(client, first["refresh_token"])).status == 200
        got = await d.refresh(client, first["refresh_token"])
        assert got.status == 400 and got.json()["error"] == "invalid_grant"
        kept = d.oauth.store.view()["grants"]
        assert [g["revoked_by"] for g in kept.values()] == ["refresh-reuse"]
        await d.close()
    run(main())


def test_the_retired_token_replayed_2_minutes_after_the_new_one_was_used_revokes(door, clock):
    async def main():
        d = await door()
        client, first = await d.sign_in()
        new = (await d.refresh(client, first["refresh_token"])).json()
        assert (await d.mcp(new["access_token"])).status == 200          # the new pair used
        clock.advance(120)
        got = await d.refresh(client, first["refresh_token"])
        assert got.status == 400 and got.json()["error"] == "invalid_grant"
        assert (await d.mcp(new["access_token"])).status == 401
        assert (await d.refresh(client, new["refresh_token"])).status == 400
        reuse = [r for r in d.rows if r["kind"] == "refresh-reuse"]
        assert len(reuse) == 1 and reuse[0]["grant"]
        await d.close()
    run(main())


def test_reuse_of_a_token_retired_more_than_10_minutes_ago_revokes(door, clock):
    async def main():
        d = await door()
        client, first = await d.sign_in()
        new = (await d.refresh(client, first["refresh_token"])).json()
        clock.advance(grants.GRACE + 1)
        assert (await d.refresh(client, first["refresh_token"])).status == 400
        assert (await d.mcp(new["access_token"])).status == 401
        await d.close()
    run(main())


def test_reuse_is_found_among_stored_hashes_after_a_restart(door, clock):
    async def main():
        d = await door()
        client, first = await d.sign_in()
        tokens = first
        for _ in range(3):
            clock.advance(3600)
            tokens = (await d.refresh(client, tokens["refresh_token"])).json()
        await d.close()
        again = await door()                     # a new process over the same file
        got = await again.refresh(client, first["refresh_token"])
        assert got.status == 400
        assert (await again.mcp(tokens["access_token"])).status == 401
        await again.close()
    run(main())


def test_a_grant_lives_refresh_days_from_its_last_refresh(door, clock):
    async def main():
        d = await door()
        client, tokens = await d.sign_in()
        clock.advance(29 * grants.DAY)
        tokens = (await d.refresh(client, tokens["refresh_token"])).json()
        clock.advance(29 * grants.DAY)
        tokens = (await d.refresh(client, tokens["refresh_token"])).json()
        assert "access_token" in tokens
        clock.advance(30 * grants.DAY + 1)
        assert (await d.refresh(client, tokens["refresh_token"])).status == 400
        assert d.oauth.store.view()["grants"] == {}
        assert [r["kind"] for r in d.rows if r["kind"] == "grant-dropped"] == ["grant-dropped"]
        await d.sign_in(client)
        await d.close()
    run(main())


def test_revoke_at_the_endpoint_revokes_the_whole_grant(door):
    async def main():
        d = await door()
        client, tokens = await d.sign_in()
        form = {"token": tokens["access_token"], "client_id": client["client_id"],
                "client_secret": client["client_secret"]}
        got = await request(d.front, "POST", "/revoke", form=form, user_agent=HTTPX)
        assert got.status == 200
        assert (await d.mcp(tokens["access_token"])).status == 401
        assert (await d.refresh(client, tokens["refresh_token"])).status == 400
        assert [r["by"] for r in d.rows if r["kind"] == "revoke"] == ["client"]
        await d.sign_in(client)
        await d.close()
    run(main())


def test_last_use_is_written_at_most_once_a_minute_and_at_the_stop(door, clock):
    async def main():
        d = await door()
        _, tokens = await d.sign_in()
        gid = d.grant_id()
        writes = []
        real = d.oauth.store.note_used

        def counted(g, at=None):
            writes.append(at)
            real(g, at)

        d.oauth.store.note_used = counted
        for _ in range(5):
            assert (await d.mcp(tokens["access_token"])).status == 200
            clock.advance(5)
        assert len(writes) == 1
        clock.advance(60)
        await d.mcp(tokens["access_token"])
        assert len(writes) == 2
        clock.advance(10)
        await d.mcp(tokens["access_token"])
        await d.oauth.stopping(True)
        assert len(writes) == 3 and d.oauth.store.grant(gid)["used"] == clock()
        await d.close()
    run(main())


# -- the limits on callers without a token --------------------------------------------------

@pytest.mark.parametrize("kind,method,path", [("register", "POST", "/register"),
                                              ("authorize-get", "GET", "/authorize"),
                                              ("authorize-post", "POST", "/authorize"),
                                              ("token", "POST", "/token")])
def test_past_its_cap_in_a_minute_a_route_answers_429(door, clock, kind, method, path):
    async def main():
        d = await door()
        for _ in range(CAPS[kind]):
            got = await request(d.front, method, path, raw=b"{}")
            assert got.status != 429
        got = await request(d.front, method, path, raw=b"{}")
        assert got.status == 429 and got.header("retry-after") == "60"
        clock.advance(60)
        assert (await request(d.front, method, path, raw=b"{}")).status != 429
        await d.close()
    run(main())


def test_a_401_on_mcp_is_never_capped(door):
    async def main():
        d = await door()
        for _ in range(100):
            assert (await d.mcp("nothing")).status == 401
        await d.close()
    run(main())


# -- the page's entry -----------------------------------------------------------------------

def test_the_page_entry_checks_a_request_before_keeping_it(door):
    async def main():
        d = await door()
        client = await d.client()
        under = d.oauth.current
        good = {"client_id": client["client_id"], "redirect_uri": CALLBACK,
                "response_type": "code", "code_challenge": pkce()[1],
                "code_challenge_method": "S256", "state": "s1"}
        assert under.check(good).resource == ORIGIN.resource
        assert under.check({**good, "resource": ORIGIN.resource}).resource == ORIGIN.resource
        with pytest.raises(PageError) as e:
            under.check({**good, "client_id": "nobody"})
        assert e.value.status == 400 and e.value.text == UNKNOWN_CLIENT
        with pytest.raises(PageError):
            under.check({**good, "redirect_uri": "https://claude.com/api/mcp/auth_callback"})
        for bad, error in ((("response_type", "token"), "unsupported_response_type"),
                           (("code_challenge_method", "plain"), "invalid_request"),
                           (("code_challenge", ""), "invalid_request"),
                           (("state", ""), "invalid_request"),
                           (("resource", OTHER.resource), "invalid_target")):
            with pytest.raises(RedirectError) as e:
                under.check({**good, bad[0]: bad[1]})
            assert e.value.error == error
            query = parse_qs(urlsplit(e.value.url).query)
            assert query["error"] == [error] and query["iss"] == [ORIGIN.issuer]
        await d.close()
    run(main())


def test_pending_requests_are_bounded_and_dropped_after_their_misses(door, clock):
    async def main():
        d = await door()
        client = await d.client()
        under = d.oauth.current
        req = under.check({"client_id": client["client_id"], "redirect_uri": CALLBACK,
                           "response_type": "code", "code_challenge": pkce()[1],
                           "code_challenge_method": "S256", "state": "s"})
        ids = [under.open(req) for _ in range(PENDING_MAX)]
        with pytest.raises(TooMany):
            under.open(req)
        assert all(under.waiting(i) for i in ids)
        for left in range(PENDING_MISSES - 1, -1, -1):
            assert under.miss(ids[0]) == left
        assert under.waiting(ids[0]) is None
        under.open(req)
        clock.advance(601)
        assert under.waiting(ids[1]) is None
        await d.close()
    run(main())


def test_no_secret_reaches_the_call_log(door):
    async def main():
        d = await door()
        client, tokens = await d.sign_in()
        await d.refresh(client, tokens["refresh_token"])
        text = json.dumps(d.rows)
        for secret in (client["client_secret"], tokens["access_token"],
                       tokens["refresh_token"]):
            assert secret not in text
        await d.close()
    run(main())

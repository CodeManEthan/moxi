"""The sign-in page and the one-time codes: the page's checks and headers, its POST's
browser checks, misses and the burn, the race on one code, the bounds; the mint behind the
gate on the control socket, and `moxi connector code`."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from urllib.parse import urlencode

import pytest
from conftest import ORIGIN
from kit import CLAUDE, HTTPX, Running, echo_tool, http, initialize, provider, rpc
from signin_kit import CALLBACK, CALLBACKS, Clock, pending_of, pkce, query_of

from moxi_connector import cli, codes, grants
from moxi_connector.oauth import (CAPS, OAuth, PENDING_MAX, PENDING_MISSES,
                                  TOO_MANY_PENDING, UNKNOWN_CLIENT)
from moxi_connector.page import DROPPED, FORBIDDEN_POST, GET_A_CODE, GONE
from moxi_connector.provider import Gate

CODE_SHAPE = re.compile(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}")


def run(coro):
    return asyncio.run(coro)


def kinds(rows, kind):
    return [r for r in rows if r.get("kind") == kind]


# -- GET /authorize ---------------------------------------------------------------------------

def test_the_page_is_one_form_with_browser_true_headers(door):
    async def main():
        d = await door()
        client = await d.client()
        _, params = d.params(client)
        got = await d.authorize(params)
        assert got.status == 200
        assert got.header("content-type") == "text/html; charset=utf-8"
        assert got.header("cache-control") == "no-store"
        assert got.header("referrer-policy") == "same-origin"
        assert got.header("x-content-type-options") == "nosniff"
        csp = got.header("content-security-policy")
        nonce = re.search(r"style-src 'nonce-([^']+)'", csp).group(1)
        assert csp == (f"default-src 'none'; style-src 'nonce-{nonce}'; form-action 'self' "
                       "https://claude.ai https://claude.com; frame-ancestors 'none'")
        page = got.body.decode()
        assert f'<style nonce="{nonce}">' in page
        assert "<script" not in page.lower()
        assert page.count("<form") == 1 and 'method="post" action="/authorize"' in page
        assert "claude.ai" in page and "the name the client gave itself" in page
        assert GET_A_CODE in page
        pid = pending_of(got.body)
        found = d.oauth.current.waiting(pid)
        assert found is not None and found.request.state == params["state"]
        await d.close()
    run(main())


def test_every_bad_parameter_and_a_bad_client_gets_no_redirect(door):
    async def main():
        d = await door()
        client = await d.client()
        _, good = d.params(client)
        # The client or the redirect can't be trusted: a page, no redirect, nothing kept.
        for bad in ({"client_id": "nobody"}, {"client_id": None},
                    {"redirect_uri": "https://claude.com/api/mcp/auth_callback"},
                    {"redirect_uri": "https://evil.example.org/cb"},
                    {"redirect_uri": None}):
            got = await d.authorize({k: v for k, v in {**good, **bad}.items()
                                     if v is not None})
            assert got.status == 400 and got.header("location") is None, bad
            assert got.header("content-security-policy"), bad
        unknown = await d.authorize({**good, "client_id": "nobody"})
        assert UNKNOWN_CLIENT.encode() in unknown.body
        twice = await d.authorize(list(good.items()) + [("client_id", "other")])
        assert twice.status == 400 and twice.header("location") is None
        # Any other fault goes back to the client, with the error, the state and the issuer.
        for bad, error in ((("response_type", "token"), "unsupported_response_type"),
                           (("response_type", None), "unsupported_response_type"),
                           (("code_challenge_method", "plain"), "invalid_request"),
                           (("code_challenge_method", None), "invalid_request"),
                           (("code_challenge", None), "invalid_request"),
                           (("code_challenge", "short"), "invalid_request"),
                           (("state", None), "invalid_request"),
                           (("resource", "https://other.example.net/mcp"), "invalid_target")):
            params = {k: v for k, v in {**good, bad[0]: bad[1]}.items() if v is not None}
            got = await d.authorize(params)
            assert got.status == 303, bad
            location = got.header("location")
            assert location.startswith(CALLBACK + "?"), bad
            query = query_of(location)
            assert query["error"] == error and query["iss"] == ORIGIN.issuer, bad
            if "state" in params:
                assert query["state"] == params["state"]
        repeated = await d.authorize(list(good.items()) + [("state", "again")])
        assert repeated.status == 303
        assert query_of(repeated.header("location"))["error"] == "invalid_request"
        assert d.oauth.current.pending == {}
        await d.close()
    run(main())


def test_an_absent_resource_is_bound_to_o_mcp(door):
    async def main():
        d = await door()
        client = await d.client()
        for resource in (None, ORIGIN.resource):
            _, params = d.params(client, resource=resource)
            got = await d.authorize(params)
            assert got.status == 200
            pending = d.oauth.current.waiting(pending_of(got.body))
            assert pending.request.resource == ORIGIN.resource
        await d.close()
    run(main())


def test_a_client_name_is_shown_escaped_cleaned_and_cut_to_64(door):
    async def main():
        d = await door()
        name = "<b>Claude</b>\x07‮​&\"'" + "y" * 200
        client = await d.client(name=name)
        _, params = d.params(client)
        page = (await d.authorize(params)).body.decode()
        cleaned = "<b>Claude</b>&\"'" + "y" * 48
        assert len(cleaned) == 64
        assert ("&lt;b&gt;Claude&lt;/b&gt;&amp;&quot;&#x27;" + "y" * 48 + "</span>") in page
        assert "<b>Claude" not in page and "y" * 49 not in page
        for raw in ("\x07", "‮", "​"):
            assert raw not in page
        await d.close()
    run(main())


def test_a_ninth_pending_request_is_refused_while_the_eight_stay(door):
    async def main():
        d = await door()
        client = await d.client()
        ids = []
        for _ in range(PENDING_MAX):
            got = await d.authorize(d.params(client)[1])
            assert got.status == 200
            ids.append(pending_of(got.body))
        ninth = await d.authorize(d.params(client)[1])
        assert ninth.status == 429 and TOO_MANY_PENDING.encode() in ninth.body
        assert ninth.header("location") is None
        assert all(d.oauth.current.waiting(i) for i in ids)
        # Each of the eight still finishes.
        done = await d.finish(ids[0], d.mint())
        assert done.status == 303
        await d.close()
    run(main())


def test_get_authorize_is_capped_per_minute(door, clock):
    async def main():
        d = await door()
        cap = CAPS["authorize-get"]
        for _ in range(cap):
            assert (await d.authorize({"client_id": "nobody"})).status == 400
        over = await d.authorize({"client_id": "nobody"})
        assert over.status == 429 and over.header("retry-after") == "60"
        clock.advance(60)
        assert (await d.authorize({"client_id": "nobody"})).status == 400
        await d.close()
    run(main())


# -- POST /authorize -------------------------------------------------------------------------

async def opened(d, client=None):
    client = client or await d.client()
    verifier, params = d.params(client)
    got = await d.authorize(params)
    return client, verifier, params, pending_of(got.body)


def test_a_right_code_answers_303_and_its_grant_takes_the_label(door):
    async def main():
        d = await door()
        client, verifier, params, pid = await opened(d)
        typed = d.mint("desk")
        done = await d.finish(pid, typed)
        assert done.status == 303
        assert done.header("cache-control") == "no-store"
        query = query_of(done.header("location"))
        assert query["state"] == params["state"] and query["iss"] == ORIGIN.issuer
        got = await d.token(client, query["code"], verifier)
        assert got.status == 200
        assert d.oauth.store.view()["grants"][d.grant_id()]["label"] == "desk"
        # The code and the pending request are spent together.
        assert d.oauth.codes.count() == 0 and d.oauth.current.waiting(pid) is None
        again = await d.finish(pid, typed)
        assert again.status == 400 and GONE.encode() in again.body
        [row] = kinds(d.rows, "code-redeemed")
        assert row["label"] == "desk" and row["client"] == client["client_id"]
        assert typed not in json.dumps(d.rows)
        await d.close()
    run(main())


def test_a_typed_code_is_normalised(door):
    async def main():
        d = await door()
        _, _, _, pid = await opened(d)
        typed = d.mint()
        sloppy = " " + typed.lower().replace("0", "o").replace("1", "l").replace("-", " - ")
        done = await d.finish(pid, sloppy)
        assert done.status == 303
        await d.close()
    run(main())


def test_origin_and_sec_fetch_site_refusals_and_origin_null_reads_as_absent(door):
    async def main():
        d = await door()
        o = ORIGIN.page_origin
        refused = ([("origin", "https://evil.example.org")],
                   [("origin", o + ":8443")],
                   [("origin", "http://door.example.net")],
                   [("sec-fetch-site", "cross-site")],
                   [("sec-fetch-site", "same-site")],
                   [("sec-fetch-site", "none")],
                   [("origin", o), ("sec-fetch-site", "cross-site")],
                   [("origin", "null"), ("sec-fetch-site", "cross-site")],
                   [("origin", "https://evil.example.org"), ("sec-fetch-site", "same-origin")])
        client, _, _, pid = await opened(d)
        typed = d.mint()
        for headers in refused:
            got = await d.finish(pid, typed, headers=headers)
            assert got.status == 403 and FORBIDDEN_POST.encode() in got.body, headers
        # A refused POST spends nothing and counts no miss.
        assert d.oauth.codes.count() == 1 and d.oauth.current.waiting(pid).misses == 0
        admitted = ([("origin", o), ("sec-fetch-site", "same-origin")],
                    [("origin", "null"), ("sec-fetch-site", "same-origin")],
                    [("origin", "null")],
                    [("origin", o)],
                    [("sec-fetch-site", "same-origin")],
                    [])
        for headers in admitted:
            _, _, _, pid = await opened(d, client)
            got = await d.finish(pid, d.mint(), headers=headers)
            assert got.status == 303, headers
        await d.close()
    run(main())


def test_five_misses_drop_a_pending_request(door):
    async def main():
        d = await door()
        _, _, _, pid = await opened(d)
        typed = d.mint()
        for left in range(PENDING_MISSES - 1, 0, -1):
            got = await d.finish(pid, "WRONG-CODE")
            assert got.status == 400
            tries = "1 try" if left == 1 else f"{left} tries"
            assert f"{tries} left".encode() in got.body
            assert pending_of(got.body) == pid   # the form again, for another try
        last = await d.finish(pid, "WRONG-CODE")
        assert last.status == 400 and DROPPED.encode() in last.body
        assert d.oauth.current.waiting(pid) is None
        late = await d.finish(pid, typed)
        assert late.status == 400 and GONE.encode() in late.body
        assert d.oauth.codes.count() == 1        # the right code wasn't spent
        rows = kinds(d.rows, "code-miss")
        assert [r["tries"] for r in rows] == [4, 3, 2, 1, 0]
        await d.close()
    run(main())


def test_twenty_misses_burn_every_live_code(door, clock):
    async def main():
        d = await door()
        client = await d.client()
        typed = [d.mint(), d.mint()]
        pids = [(await opened(d, client))[3] for _ in range(5)]
        for pid in pids:
            for _ in range(PENDING_MISSES - 1):
                assert (await d.finish(pid, "WRONG-CODE")).status == 400
        assert d.oauth.codes.count() == 0
        assert len(kinds(d.rows, "code-burned")) == 1
        assert kinds(d.rows, "code-burned")[0]["misses"] == codes.BURN_AFTER
        clock.advance(60)                        # past the minute's cap on posts
        _, _, _, pid = await opened(d, client)
        for code in typed:
            got = await d.finish(pid, code)
            assert got.status == 400
        await d.close()
    run(main())


def test_two_finishes_racing_on_one_code_exactly_one_wins(door):
    async def main():
        d = await door()
        client = await d.client()
        # Two sign-ins, one code.
        a = (await opened(d, client))[3]
        b = (await opened(d, client))[3]
        typed = d.mint()
        got = await asyncio.gather(d.finish(a, typed), d.finish(b, typed))
        assert sorted(g.status for g in got) == [303, 400]
        # One sign-in, posted twice with its code.
        c = (await opened(d, client))[3]
        typed = d.mint()
        got = await asyncio.gather(d.finish(c, typed), d.finish(c, typed))
        assert sorted(g.status for g in got) == [303, 400]
        assert GONE.encode() in [g for g in got if g.status == 400][0].body
        assert len(kinds(d.rows, "code-redeemed")) == 2
        await d.close()
    run(main())


def test_an_expired_code_and_an_expired_pending_request(door, clock):
    async def main():
        d = await door()
        client = await d.client()
        _, _, _, pid = await opened(d, client)
        typed = d.mint()
        clock.advance(codes.LIFE + 1)
        got = await d.finish(pid, typed)
        assert got.status == 400 and b"wrong or has expired" in got.body
        _, _, _, pid = await opened(d, client)
        typed = d.mint()
        clock.advance(601)
        got = await d.finish(pid, typed)
        assert got.status == 400 and GONE.encode() in got.body
        await d.close()
    run(main())


def test_a_post_without_a_live_pending_request_or_too_large_is_refused(door):
    async def main():
        from kit import request

        d = await door()
        typed = d.mint()
        got = await d.finish("not-a-pending-id", typed)
        assert got.status == 400 and GONE.encode() in got.body
        big = await request(d.front, "POST", "/authorize",
                            raw=urlencode({"pending": "x" * 5000, "code": typed}).encode(),
                            headers=d.browser_headers(), host=ORIGIN.host)
        assert big.status == 413
        assert d.oauth.codes.count() == 1
        await d.close()
    run(main())


def test_post_authorize_is_capped_per_minute(door, clock):
    async def main():
        d = await door()
        for _ in range(CAPS["authorize-post"]):
            assert (await d.finish("none", "x")).status == 400
        over = await d.finish("none", "x")
        assert over.status == 429 and over.header("retry-after") == "60"
        await d.close()
    run(main())


# -- the codes --------------------------------------------------------------------------------

def test_codes_are_crockford_kept_as_hashes_three_at_most_five_minutes():
    clock = Clock()
    box = codes.Codes(clock)
    minted = [box.mint(f"l{i}") for i in range(4)]
    assert all(CODE_SHAPE.fullmatch(m.code) for m in minted)
    assert all(m.expires == clock() + codes.LIFE for m in minted)
    # Only hashes are kept, and the fourth dropped the oldest.
    kept = repr(box.__dict__)
    assert not any(m.code in kept or m.code.replace("-", "") in kept for m in minted)
    assert box.count() == codes.LIVE_MAX
    assert box.take(minted[0].code) is None
    assert box.take(minted[1].code).label == "l1"
    assert box.take(minted[1].code) is None          # spent
    clock.advance(codes.LIFE)
    assert box.take(minted[2].code) is None          # expired


def test_normalise_reads_o_as_zero_and_i_l_as_one():
    assert codes.normalise(" ab1o-il0z ") == "AB10110Z"
    assert codes.digest("AB10-110Z") == codes.digest("ab1o il0z")


def test_misses_burn_only_within_ten_minutes():
    clock = Clock()
    box = codes.Codes(clock)
    box.mint()
    for _ in range(codes.BURN_AFTER - 1):
        assert box.miss() is False
    clock.advance(codes.BURN_WINDOW)
    box.mint()
    assert box.miss() is False and box.count() == 1  # the old misses fell out
    for _ in range(codes.BURN_AFTER - 2):
        assert box.miss() is False
    assert box.miss() is True and box.count() == 0


def test_a_label_is_cleaned_and_cut():
    assert codes.clean_label("  phone\x1b[31m  ") == "phone[31m"
    assert codes.clean_label("x" * 100) == "x" * codes.LABEL_MAX
    assert codes.clean_label("\x00") is None and codes.clean_label(None) is None


# -- the mint behind the gate (fake gates) ----------------------------------------------------

class FakeGate:
    """A mint gate whose checks answer what the test set, and remember what they saw."""

    def __init__(self, peer=None, shell=None):
        self.peer_says = peer
        self.shell_says = shell
        self.peers = []
        self.shells = []

    def peer(self, peer):
        self.peers.append(peer)
        if isinstance(self.peer_says, Exception):
            raise self.peer_says
        return self.peer_says

    def shell(self, env):
        self.shells.append(dict(env))
        if isinstance(self.shell_says, Exception):
            raise self.shell_says
        return self.shell_says

    def gate(self) -> Gate:
        return Gate(peer=self.peer, shell=self.shell)


@pytest.fixture
def minting(make_host, fake):
    """`minting(*gates, fail=())`: a running daemon with the real sign-in, a provider per
    gate (`None` for one that declares none; a name in `fail` fails its start), and
    `mint_code` on its control socket."""
    from moxi_connector.codes import mint_method

    def make(*gates, fail=()):
        providers = {f"p{i}": provider(fake, echo_tool(fake, f"t{i}"),
                                       gate=g.gate() if g else None,
                                       fail_start=f"p{i}" in fail)
                     for i, g in enumerate(gates)}
        host = asyncio.run(make_host(providers=providers))
        oauth = OAuth(grants.Store(), host.calllog, redirect_uris=CALLBACKS)
        host.signin, host.redirect_uris = oauth, CALLBACKS
        return Running(host, ORIGIN, methods={"mint_code": mint_method(oauth, host.registry)})

    return make


def ask_mint(label="phone"):
    from moxi_connector import checks

    return checks.ask("mint_code", {"label": label})


def test_a_gate_that_lets_through_mints_and_runs_on_every_mint(minting):
    gate = FakeGate()
    with minting(gate) as d:
        got = ask_mint("desk")
        assert CODE_SHAPE.fullmatch(got["code"]) and got["label"] == "desk"
        assert got["expires"] == pytest.approx(time.time() + codes.LIFE, abs=5)
        ask_mint()
        oauth = d.host.signin
        assert oauth.codes.count() == 2
    assert [p.pid for p in gate.peers] == [os.getpid(), os.getpid()]
    rows = d.host.calllog.rows
    assert [r["label"] for r in kinds(rows, "code-minted")] == ["desk", "phone"]
    assert got["code"] not in json.dumps(rows)


def test_with_no_gate_loaded_the_mint_is_refused(minting):
    from moxi import ipc

    with minting(None) as d:
        with pytest.raises(ipc.RpcError) as e:
            ask_mint()
        assert e.value.code == codes.NO_GATE and e.value.message == codes.NO_GATE_TEXT
        assert d.host.signin.codes.count() == 0
    [row] = kinds(d.host.calllog.rows, "code-refused")
    assert row["check"] == "gate"


def test_a_gate_whose_provider_failed_to_start_is_not_loaded(minting):
    from moxi import ipc

    gate = FakeGate()
    with minting(gate, fail={"p0"}):
        with pytest.raises(ipc.RpcError) as e:
            ask_mint()
        assert e.value.code == codes.NO_GATE
    assert gate.peers == []


def test_a_refusing_gate_refuses_in_its_own_words_and_names_its_module(minting):
    from moxi import ipc

    words = "This process is inside an agent session."
    ok, no = FakeGate(), FakeGate(peer=words)
    with minting(ok, no) as d:
        with pytest.raises(ipc.RpcError) as e:
            ask_mint()
        assert e.value.code == codes.REFUSED and e.value.message == words
        assert e.value.data == {"check": "peer", "module": "p1"}
        assert d.host.signin.codes.count() == 0
    [row] = kinds(d.host.calllog.rows, "code-refused")
    assert row["refusal"] == words and row["module"] == "p1"


def test_a_gate_that_raises_refuses(minting):
    from moxi import ipc

    with minting(FakeGate(peer=RuntimeError("the walk broke"))) as d:
        with pytest.raises(ipc.RpcError) as e:
            ask_mint()
        assert e.value.code == codes.REFUSED and "p0" in e.value.message
        assert d.host.signin.codes.count() == 0


# -- moxi connector code ---------------------------------------------------------------------

@pytest.fixture
def verb(monkeypatch, config_file, capsys):
    """`verb(*local_gates, enabled=True, env=None)`: run `moxi connector code --label
    phone` with these gates installed here; (exit, stdout, stderr)."""

    def call(*gates, enabled=True, env=None, label="phone"):
        config_file(f"enabled = {'true' if enabled else 'false'}\n")
        monkeypatch.setattr(cli, "_local_gates",
                            lambda: [(f"p{i}", g.gate()) for i, g in enumerate(gates)])
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        argv = ["code"] + (["--label", label] if label else [])
        code = cli.main(argv)
        out = capsys.readouterr()
        return code, out.out, out.err

    return call


def test_the_verb_prints_a_code_its_expiry_and_the_line(minting, verb):
    gate = FakeGate()
    with minting(gate) as d:
        code, out, err = verb(gate)
        assert code == 0, err
        lines = out.splitlines()
        assert CODE_SHAPE.fullmatch(lines[0])
        assert "until" in lines[1] and "label phone" in lines[1]
        assert lines[2] == codes.USE_IT
        assert d.host.signin.codes.count() == 1
    assert len(gate.shells) == 1 and len(gate.peers) == 1


def test_the_verb_refuses_a_shell_its_gate_refuses_before_asking(minting, verb):
    words = ("This shell is inside the agent session planner (MOXI_COMMS_NAME or a claim "
             "socket is set). Run moxi connector code in your own terminal, outside every "
             "agent session.")
    gate = FakeGate(shell=words)
    with minting(gate) as d:
        code, out, err = verb(gate, env={"MOXI_COMMS_NAME": "planner"})
        assert code == cli.NOPERM and words in err and out == ""
        assert d.host.signin.codes.count() == 0
    assert gate.peers == [] and gate.shells[0]["MOXI_COMMS_NAME"] == "planner"


def test_the_verb_refuses_when_a_shell_check_raises(minting, verb):
    gate = FakeGate(shell=RuntimeError("no"))
    with minting(gate):
        code, _, err = verb(gate)
    assert code == cli.NOPERM and "p0" in err and gate.peers == []


def test_with_no_gate_installed_the_verb_refuses(minting, verb):
    with minting(FakeGate()):
        code, _, err = verb()
    assert code == cli.NOPERM and codes.NO_GATE_TEXT in err


def test_the_verb_passes_on_the_sockets_refusal(minting, verb):
    words = "This process is inside an agent session, or the connector couldn't tell."
    here = FakeGate()
    with minting(FakeGate(peer=words)):
        code, _, err = verb(here)
    assert code == cli.NOPERM and words in err
    with minting(None):
        code, _, err = verb(here)
    assert code == cli.NOPERM and codes.NO_GATE_TEXT in err


def test_the_verb_exits_75_when_the_connector_is_off_or_down(verb):
    code, _, err = verb(FakeGate(), enabled=False)
    assert code == 75 and "off" in err
    code, _, err = verb(FakeGate())
    assert code == 75 and "doesn't answer" in err


# -- the whole sign-in over loopback, with a code from the control socket ---------------------

def test_the_full_sign_in_over_loopback_with_a_code_from_the_control_socket(minting):
    with minting(FakeGate()) as d:
        status, heads, _ = http(d.port, "POST", "/mcp", body=json.dumps(initialize()).encode(),
                                headers={"User-Agent": CLAUDE,
                                         "Content-Type": "application/json"})
        assert status == 401 and "resource_metadata=" in heads["www-authenticate"]
        status, _, body = http(d.port, "POST", "/register", headers={
            "User-Agent": HTTPX, "Content-Type": "application/json"},
            body=json.dumps({"redirect_uris": [CALLBACK], "client_name": "Claude",
                             "token_endpoint_auth_method": "none"}).encode())
        assert status == 201, body
        client = json.loads(body)
        verifier, challenge = pkce()
        query = urlencode({"client_id": client["client_id"], "redirect_uri": CALLBACK,
                           "response_type": "code", "code_challenge": challenge,
                           "code_challenge_method": "S256", "state": "s-1"})
        status, heads, body = http(d.port, "GET", "/authorize?" + query)
        assert status == 200 and "form-action" in heads["content-security-policy"]
        typed = ask_mint("loopback")["code"]
        form = urlencode({"pending": pending_of(body), "code": typed}).encode()
        status, heads, _ = http(d.port, "POST", "/authorize", body=form, headers={
            "Origin": ORIGIN.page_origin, "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/x-www-form-urlencoded"})
        assert status == 303
        back = query_of(heads["location"])
        assert back["state"] == "s-1" and back["iss"] == ORIGIN.issuer
        started = time.perf_counter()
        status, heads, body = http(d.port, "POST", "/token", headers={
            "User-Agent": HTTPX, "Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode({"grant_type": "authorization_code", "code": back["code"],
                            "redirect_uri": CALLBACK, "client_id": client["client_id"],
                            "code_verifier": verifier}).encode())
        assert time.perf_counter() - started < 1.0
        assert status == 200 and heads["cache-control"] == "no-store", body
        access = json.loads(body)["access_token"]
        mcp = {"User-Agent": CLAUDE, "Authorization": f"Bearer {access}",
               "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
        status, _, body = http(d.port, "POST", "/mcp", headers=mcp,
                               body=json.dumps(initialize()).encode())
        assert status == 200 and "tools" in json.loads(body)["result"]["capabilities"]
        status, _, body = http(d.port, "POST", "/mcp", headers=mcp,
                               body=json.dumps(rpc("tools/list", rid=2)).encode())
        assert [t["name"] for t in json.loads(body)["result"]["tools"]] == ["t0"]
        grant = next(iter(d.host.signin.store.view()["grants"].values()))
        assert grant["label"] == "loopback"

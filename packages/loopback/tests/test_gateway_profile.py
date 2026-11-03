"""The `gateway` profile, with no sockets: the listener a tunnel forwards a phone to."""

import pytest

from moxi_loopback.check import Refusal, RequestView, check, gateway
from moxi_loopback.routes import Entry, Field, Out, Page, Route, Table, export

HOST = "box.example-net.ts.net"
PORT = 8443
ORIGIN = b"https://box.example-net.ts.net:8443"
TOKEN = "Tk0_" + "t" * 35 + "-Tk0"
JSON = (b"content-type", b"application/json")

PAGE = Page(routes=(
    Route("/api/summary", "read", "pkg.h:summary", query={"days": Field("int", required=False)},
          returns={"total": Out("int")}),
    Route("/api/refresh", "act", "pkg.h:refresh", returns={}),
))

#: Headers a proxy in front may add, or any local process may forge; none is read.
PROXY_HEADERS = [
    (b"tailscale-user-login", b"someone@example.com"),
    (b"tailscale-user-name", b"Someone"),
    (b"tailscale-funnel-request", b"?1"),
    (b"tailscale-headers-info", b"https://example.com"),
    (b"x-forwarded-for", b"203.0.113.9"),
    (b"x-forwarded-host", b"localhost"),
    (b"x-forwarded-proto", b"https"),
    (b"forwarded", b"for=203.0.113.9;host=localhost;proto=https"),
]


def table():
    t = Table()
    t.add("GET", "/")
    t.add("GET", "/signin")
    t.add("GET", "/api/prove")
    t.mount("/m/usage", export(PAGE))
    return t


def cookie(view, entry):
    return b"gw=ok" in view.values(b"cookie")


def phone(session=cookie, host=HOST, port=PORT, fallback=None):
    return gateway(table(), session=session, host=host, port=port, fallback=fallback)


def req(method="GET", path=b"/m/usage/api/summary", extra=(), host=HOST.encode() + b":8443",
        signed_in=True, transport="unix"):
    headers = [] if host is None else [(b"host", host)]
    if signed_in:
        headers.append((b"cookie", b"gw=ok"))
    port = None if transport == "unix" else 8461
    return RequestView(method, path, headers + list(extra), transport, port)


def act(origin=ORIGIN, site=b"same-origin", extra=(), **kwargs):
    headers = [JSON, (b"sec-fetch-mode", b"cors"), (b"sec-fetch-dest", b"empty")]
    if origin is not None:
        headers.append((b"origin", origin))
    if site is not None:
        headers.append((b"sec-fetch-site", site))
    return req("POST", b"/m/usage/api/refresh", headers + list(extra), **kwargs)


# --- gate 1: the tunnel's host alone ---------------------------------------------------


@pytest.mark.parametrize("transport", ["unix", "tcp"])
@pytest.mark.parametrize("host", [
    b"box.example-net.ts.net", b"box.example-net.ts.net:8443", b"Box.Example-Net.TS.net:8443",
    b"BOX.EXAMPLE-NET.TS.NET",
])
def test_the_host_passes_with_no_port_or_the_outside_port_case_folded(host, transport):
    assert check(req(host=host, transport=transport), phone()) is None


@pytest.mark.parametrize("transport", ["unix", "tcp"])
@pytest.mark.parametrize("host", [
    b"127.0.0.1", b"127.0.0.1:8461", b"localhost", b"localhost:8443", b"[::1]:8443",
    b"other.example-net.ts.net:8443", b"box.example-net.ts.net.:8443",
    b"evil.box.example-net.ts.net", b"box.example-net.ts.net:8461",
    b"box.example-net.ts.net:443", b"box.example-net.ts.net:", b"box.example-net.ts.net:84a3",
    b"box.example-net.ts.net:8443:1", b"box.example-net.ts.net, localhost", b"",
    "böx.example-net.ts.net".encode(),
])
def test_every_other_host_is_a_403_at_gate_1(host, transport):
    assert check(req(host=host, transport=transport), phone()) == Refusal(403, 1)


def test_an_absent_or_doubled_host_is_refused():
    assert check(req(host=None), phone()) == Refusal(403, 1)
    doubled = req(extra=[(b"host", HOST.encode())], host=HOST.encode())
    assert check(doubled, phone()) == Refusal(403, 1)


def test_on_port_443_the_origin_has_no_port_and_host_may_name_443():
    profile = phone(port=443)
    assert check(req(host=HOST.encode()), profile) is None
    assert check(req(host=HOST.encode() + b":443"), profile) is None
    origin = HOST.encode()
    assert check(act(origin=b"https://" + origin, host=origin), profile) is None
    assert check(act(origin=b"https://" + origin + b":443", host=origin), profile) == \
        Refusal(403, 3)


# --- gate 2 ------------------------------------------------------------------------------


def test_options_is_refused():
    assert check(req("OPTIONS"), phone()) == Refusal(403, 2)


# --- gate 3: the https origin, and no token class -----------------------------------------


def test_a_same_origin_act_passes_and_needs_the_https_origin():
    assert check(act(), phone()) is None
    assert check(act(site=None), phone()) is None
    assert check(act(origin=None), phone()) == Refusal(403, 3)


@pytest.mark.parametrize(("origin", "site"), [
    (b"http://box.example-net.ts.net:8443", b"same-origin"),
    (b"https://box.example-net.ts.net", b"same-origin"),
    (b"https://box.example-net.ts.net:8461", b"same-origin"),
    (b"https://other.example-net.ts.net:8443", b"same-origin"),
    (b"http://127.0.0.1:8461", b"same-origin"),
    (b"null", b"same-origin"),
    (ORIGIN, b"same-site"),
    (ORIGIN, b"cross-site"),
    (ORIGIN, b"none"),
])
def test_an_act_from_anywhere_but_the_gateway_origin_is_refused(origin, site):
    assert check(act(origin=origin, site=site), phone()) == Refusal(403, 3)


def test_two_origins_are_refused():
    assert check(act(extra=[(b"origin", ORIGIN)]), phone()) == Refusal(403, 3)


@pytest.mark.parametrize(("site", "origin", "ok"), [
    (None, None, True),
    (b"same-origin", None, True),
    (b"none", None, True),
    (b"same-origin", ORIGIN, True),
    (b"cross-site", None, False),
    (b"same-site", None, False),
    (b"same-origin", b"https://other.example-net.ts.net:8443", False),
])
def test_a_read_passes_only_from_the_gateway_origin_or_with_no_site(site, origin, ok):
    extra = []
    if site is not None:
        extra.append((b"sec-fetch-site", site))
    if origin is not None:
        extra.append((b"origin", origin))
    assert (check(req(extra=extra), phone()) is None) is ok


def test_a_cross_site_navigation_opens_the_shell_but_a_cross_site_read_does_not():
    nav = [(b"sec-fetch-site", b"cross-site"), (b"sec-fetch-mode", b"navigate"),
           (b"sec-fetch-dest", b"document")]
    assert check(req(path=b"/", extra=nav), phone()) is None
    assert check(req(extra=nav), phone()) == Refusal(403, 3)


@pytest.mark.parametrize("path", [b"/", b"/api/prove", b"/m/usage/api/summary"])
def test_a_request_carrying_the_token_is_refused_whatever_else_it_has(path):
    token = [(b"x-moxi-token", TOKEN.encode())]
    assert check(req(path=path, extra=token), phone()) == Refusal(403, 3)
    assert check(req(path=path, extra=token, signed_in=False), phone()) == Refusal(403, 3)
    assert check(act(extra=token), phone()) == Refusal(403, 3)


# --- the headers a proxy adds are never read ----------------------------------------------


def test_the_proxy_headers_never_change_a_decision():
    signed_out = req(extra=PROXY_HEADERS, signed_in=False)
    assert check(signed_out, phone()) == Refusal(403, 4)
    assert check(req(extra=PROXY_HEADERS), phone()) is None
    assert check(act(extra=PROXY_HEADERS), phone()) is None
    assert check(act(origin=None, extra=PROXY_HEADERS), phone()) == Refusal(403, 3)
    assert check(req(host=b"localhost", extra=PROXY_HEADERS), phone()) == Refusal(403, 1)


def test_a_tailscale_login_with_no_cookie_is_refused():
    login = [(b"tailscale-user-login", b"someone@example.com")]
    assert check(req(extra=login, signed_in=False), phone()) == Refusal(403, 4)


# --- gates 4 and 5: as on the browser profile ----------------------------------------------


def test_the_session_gets_the_one_entry_and_none_for_a_404():
    seen = []

    def session(view, entry):
        seen.append(entry)
        return True

    profile = phone(session=session)
    assert check(req(), profile) is None
    assert check(req(path=b"/m/usage/api/nothing"), profile) == Refusal(404, 5)
    assert isinstance(seen[0], Entry) and seen[0].raw_path == "/m/usage/api/summary"
    assert seen[1] is None


def test_only_true_from_the_session_admits_and_prove_is_not_exempt():
    assert check(req(), phone(session=lambda v, e: 1)) == Refusal(403, 4)
    assert check(req(path=b"/api/prove", signed_in=False), phone()) == Refusal(403, 4)
    assert check(req(path=b"/api/prove"), phone()) is None


def test_an_act_without_json_is_a_403_at_the_route_gate():
    headers = [(b"origin", ORIGIN), (b"sec-fetch-site", b"same-origin")]
    assert check(req("POST", b"/m/usage/api/refresh", headers), phone()) == Refusal(403, 5)


def test_the_fallback_answers_a_get_the_table_lacks():
    page = Entry("GET", "/shell-page", "builtin")
    profile = phone(fallback=lambda view: page if view.raw_path == b"/m/usage/x" else None)
    assert check(req(path=b"/m/usage/x"), profile) is None
    assert check(req(path=b"/m/other/x"), profile) == Refusal(404, 5)


# --- building one ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", [
    "localhost", "LOCALHOST", "phone.localhost", "127.0.0.1", "10.0.0.1", "::1", "[::1]", "",
    "box example", "box.example.", ".box.example", "-box.example", "box_1.example",
    "box.example:8443", "böx.example", "a" * 64 + ".example", 42, None,
])
def test_a_host_that_no_tunnel_gives_is_refused_at_build(host):
    with pytest.raises(ValueError):
        gateway(table(), session=cookie, host=host, port=PORT)


@pytest.mark.parametrize("port", [0, 65536, -1, True, "8443", None, 8443.0])
def test_a_port_outside_1_to_65535_is_refused_at_build(port):
    with pytest.raises(ValueError):
        gateway(table(), session=cookie, host=HOST, port=port)


def test_a_session_and_fallback_must_be_callable_and_the_table_a_table():
    with pytest.raises(ValueError):
        gateway(table(), session=None, host=HOST, port=PORT)
    with pytest.raises(ValueError):
        gateway(table(), session=cookie, host=HOST, port=PORT, fallback="no")
    with pytest.raises(ValueError):
        gateway({}, session=cookie, host=HOST, port=PORT)


def test_the_profile_keeps_the_folded_host_and_freezes_its_table():
    t = table()
    profile = gateway(t, session=cookie, host="Box.Example-Net.TS.net", port=PORT)
    assert profile.kind == "gateway"
    assert profile.host == HOST and profile.port == PORT and profile.token is None
    with pytest.raises(Exception):  # noqa: B017 - whichever error the frozen table raises
        t.add("GET", "/more")

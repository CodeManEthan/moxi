"""The request check, with no sockets: every gate over both profiles."""

import hashlib
import hmac
import secrets

import pytest

from moxi_ui.loopback.check import (
    Refusal,
    RequestView,
    browser,
    check,
    proof,
    prove_nonce,
    token_matches,
    upstream,
)
from moxi_ui.loopback.routes import Entry, Field, Out, Page, Route, Table, build_table, export

# Fixed, not minted: both appear in parametrized test ids, which every pytest-xdist worker
# must collect alike.
TOKEN = "Tk0_" + "t" * 35 + "-Tk0"
OTHER = "Ot1-" + "o" * 35 + "_Ot1"
PORT = 41000
SHELL_PORT = 8440
JSON = (b"content-type", b"application/json")

PAGE = Page(routes=(
    Route("/api/summary", "read", "pkg.h:summary", query={"days": Field("int", required=False)},
          returns={"total": Out("int")}),
    Route("/api/refresh", "act", "pkg.h:refresh", returns={}),
    Route("/api/events", "read", "pkg.h:events", stream=True, topics={"today": Out("int")}),
))


def view(method="GET", path=b"/api/summary", headers=(), transport="tcp", port=PORT):
    return RequestView(method, path, list(headers), transport, port)


# --- the upstream profile -----------------------------------------------------------


def up(port=PORT):
    return upstream(build_table(PAGE)[0], token=TOKEN, port=port)


def tcp(method="GET", path=b"/api/summary", extra=(), host=b"127.0.0.1:%d" % PORT, token=TOKEN):
    headers = [] if host is None else [(b"host", host)]
    if token is not None:
        headers.append((b"x-moxi-token", token.encode()))
    return view(method, path, headers + list(extra))


def unix(method="GET", path=b"/api/summary", extra=(), token=TOKEN):
    headers = [(b"host", b"localhost")]
    if token is not None:
        headers.append((b"x-moxi-token", token.encode()))
    return view(method, path, headers + list(extra), transport="unix", port=None)


def test_a_good_upstream_read_and_act_pass():
    profile = up()
    assert check(tcp(), profile) is None
    assert check(tcp("POST", b"/api/refresh", [JSON]), profile) is None
    assert check(unix(), up(None)) is None


@pytest.mark.parametrize("host", [
    b"127.0.0.1:%d" % (PORT + 1), b"127.0.0.1", b"localhost:%d" % PORT, b"[::1]:%d" % PORT,
    b"127.0.0.1:%d " % PORT, b"127.0.0.1:0%d" % PORT, b"evil.example:%d" % PORT,
    b"127.0.0.1:%d,127.0.0.1:%d" % (PORT, PORT), b"", None,
])
def test_upstream_tcp_admits_exactly_its_own_host_and_port(host):
    assert check(tcp(host=host), up()) == Refusal(403, 1)


def test_upstream_tcp_refuses_a_duplicate_host():
    request = tcp(extra=[(b"host", b"127.0.0.1:%d" % PORT)])
    assert check(request, up()) == Refusal(403, 1)


def test_upstream_tcp_without_a_bound_port_refuses_every_host():
    assert check(tcp(), up(None)) == Refusal(403, 1)


def test_upstream_unix_runs_no_host_gate():
    for headers in ([], [(b"host", b"evil.example")], [(b"host", b"a"), (b"host", b"b")]):
        request = view(headers=headers + [(b"x-moxi-token", TOKEN.encode())],
                       transport="unix", port=None)
        assert check(request, up(None)) is None


def test_an_unknown_transport_is_refused():
    request = view(headers=[(b"host", b"127.0.0.1:%d" % PORT)], transport="pipe")
    assert check(request, up()) == Refusal(403, 1)


@pytest.mark.parametrize("profile", ["up", "browser"])
def test_options_is_refused_on_both_profiles(profile):
    if profile == "up":
        assert check(tcp("OPTIONS"), up()) == Refusal(403, 2)
    else:
        assert check(br("OPTIONS", b"/m/usage/api/summary"), shell()) == Refusal(403, 2)


@pytest.mark.parametrize("header", [
    (b"origin", b"http://127.0.0.1:%d" % PORT), (b"cookie", b"s=1"),
    (b"sec-fetch-site", b"same-origin"), (b"sec-fetch-mode", b"cors"),
    (b"Sec-Fetch-Dest", b"empty"), (b"Origin", b"null"),
])
def test_upstream_tcp_refuses_any_browser_header_even_with_the_token(header):
    assert check(tcp(extra=[header]), up()) == Refusal(403, 3)
    assert check(unix(extra=[header]), up(None)) is None


def test_a_referer_alone_is_not_a_browser_header_upstream():
    # The shell strips it, but the upstream refusal names Origin, Cookie and Sec-Fetch-*.
    assert check(tcp(extra=[(b"referer", b"http://x/")]), up()) is None


@pytest.mark.parametrize("token", [
    None, OTHER, TOKEN[:-1], TOKEN + "A", TOKEN.lower() if TOKEN.lower() != TOKEN else OTHER, "",
])
def test_upstream_credential_refusals(token):
    assert check(tcp(token=token), up()) == Refusal(403, 4)
    assert check(unix(token=token), up(None)) == Refusal(403, 4)


def test_a_non_ascii_or_doubled_token_is_refused():
    non_ascii = TOKEN[:-1].encode() + b"\xc3"
    request = tcp(token=None, extra=[(b"x-moxi-token", non_ascii)])
    assert check(request, up()) == Refusal(403, 4)
    doubled = tcp(extra=[(b"x-moxi-token", TOKEN.encode())])
    assert check(doubled, up()) == Refusal(403, 4)


def test_an_act_without_json_is_a_403_at_the_route_gate():
    profile = up()
    assert check(tcp("POST", b"/api/refresh"), profile) == Refusal(403, 5)
    for value in (b"text/plain", b"application/jsonx", b"application/x-www-form-urlencoded"):
        request = tcp("POST", b"/api/refresh", [(b"content-type", value)])
        assert check(request, profile) == Refusal(403, 5)
    doubled = tcp("POST", b"/api/refresh", [JSON, JSON])
    assert check(doubled, profile) == Refusal(403, 5)
    charset = tcp("POST", b"/api/refresh", [(b"content-type", b"Application/JSON; charset=utf-8")])
    assert check(charset, profile) is None


@pytest.mark.parametrize(("method", "path"), [
    ("GET", b"/api/%73ummary"), ("GET", b"/api/summary/"), ("GET", b"/api/Summary"),
    ("POST", b"/api/summary"), ("GET", b"/api/refresh"), ("PUT", b"/api/refresh"),
    ("HEAD", b"/api/summary"), ("GET", b"/nowhere"), ("GET", b"/api/summary%00"),
    ("GET", b"/../api/summary"), ("GET", b"//api/summary"), ("GET", b"/api/summary\xff"),
])
def test_a_path_or_method_outside_the_table_is_a_404_after_the_credential(method, path):
    assert check(tcp(method, path), up()) == Refusal(404, 5)
    assert check(tcp(method, path, token=None), up()) == Refusal(403, 4)


def test_prove_passes_without_a_token_and_no_other_route_does():
    profile = up()
    assert check(tcp("GET", b"/api/prove", token=None), profile) is None
    assert check(unix("GET", b"/api/prove", token=None), up(None)) is None
    for path in (b"/api/health", b"/api/summary", b"/api/events"):
        assert check(tcp("GET", path, token=None), profile) == Refusal(403, 4)
    assert check(tcp("POST", b"/api/prove", [JSON], token=None), profile) == Refusal(403, 4)
    assert check(tcp("GET", b"/api/%70rove", token=None), profile) == Refusal(403, 4)


def test_prove_still_runs_the_host_and_browser_header_gates():
    assert check(tcp("GET", b"/api/prove", host=b"evil.example", token=None), up()) == Refusal(403, 1)
    request = tcp("GET", b"/api/prove", [(b"origin", b"http://x")], token=None)
    assert check(request, up()) == Refusal(403, 3)


def test_upstream_rejects_a_bad_token_or_port_at_build():
    for token in ("", "short", TOKEN + "!", None):
        with pytest.raises(ValueError):
            upstream(Table(), token=token)
    for port in (0, 65536, True, "80"):
        with pytest.raises(ValueError):
            upstream(Table(), token=TOKEN, port=port)


# --- the browser profile ------------------------------------------------------------

SHELL_HOST = b"127.0.0.1:%d" % SHELL_PORT
ORIGIN = b"http://" + SHELL_HOST


def shell_table():
    table = Table()
    table.add("GET", "/")
    table.add("GET", "/api/prove")
    table.add("POST", "/api/stop")
    table.mount("/m/usage", export(PAGE))
    return table


def cookie_or_token(view, entry):
    return (b"s=ok" in view.values(b"cookie")) or token_matches(view, TOKEN)


def shell(session=cookie_or_token, names=("127.0.0.1", "localhost"), fallback=None):
    return browser(shell_table(), session=session, names=names, fallback=fallback)


def br(method="GET", path=b"/m/usage/api/summary", extra=(), host=SHELL_HOST, cookie=True):
    headers = [] if host is None else [(b"host", host)]
    if cookie:
        headers.append((b"cookie", b"s=ok"))
    return view(method, path, headers + list(extra), port=SHELL_PORT)


def act_headers(origin=ORIGIN, site=b"same-origin"):
    headers = [JSON, (b"sec-fetch-mode", b"cors"), (b"sec-fetch-dest", b"empty")]
    if origin is not None:
        headers.append((b"origin", origin))
    if site is not None:
        headers.append((b"sec-fetch-site", site))
    return headers


@pytest.mark.parametrize("host", [
    b"127.0.0.1:8440", b"127.0.0.1:9000", b"127.0.0.1:1", b"127.0.0.1:65535", b"127.0.0.1",
    b"localhost:9000", b"LocalHost:9000", b"localhost",
])
def test_browser_host_admits_its_names_on_any_port(host):
    origin = b"http://" + host.lower()
    assert check(br(host=host, extra=[(b"origin", origin)]), shell()) is None


@pytest.mark.parametrize("host", [
    b"127.0.0.1:0", b"127.0.0.1:65536", b"127.0.0.1:", b"127.0.0.1:+80", b"127.0.0.1: 80",
    b"127.0.0.1:1_0", b"127.0.0.1:080080", b"127.0.0.2:8440", b"evil.example:8440",
    b"evil.example", b"localhost.:8440", b"localhost.", b"[::1]:8440", b"[::1]", b"::1",
    b"[::1", b"[::1]x", b"127.0.0.1:8440,127.0.0.1:8440", b"", b"localhost:8440\xc3\xa9",
    b"moxi.localhost:8440", b"0x7f.0.0.1:8440", b"2130706433:8440",
])
def test_browser_host_refuses_everything_else(host):
    assert check(br(host=host), shell()) == Refusal(403, 1)


def test_browser_host_refuses_an_absent_or_duplicate_host():
    assert check(br(host=None), shell()) == Refusal(403, 1)
    assert check(br(extra=[(b"host", SHELL_HOST)]), shell()) == Refusal(403, 1)


def test_browser_host_refuses_localhost_when_names_lack_it():
    profile = shell(names={"127.0.0.1"})
    assert check(br(host=b"localhost:8440"), profile) == Refusal(403, 1)
    assert check(br(), profile) is None


def test_a_localhost_name_beside_localhost_is_admitted():
    profile = shell(names={"127.0.0.1", "localhost", "moxi.localhost"})
    assert check(br(host=b"moxi.localhost:8440"), profile) is None


@pytest.mark.parametrize("names", [
    {"127.0.0.1"}, {"127.0.0.1", "localhost"}, {"localhost", "moxi.localhost"},
    {"LOCALHOST", "Moxi.Localhost"}, set(),
])
def test_browser_names_admits_the_base_names_and_localhost_names_beside_localhost(names):
    shell(names=names)


@pytest.mark.parametrize("names", [
    {"127.0.0.1", "moxi.localhost"}, {"[::1]"}, {"127.0.0.1", "example.com"}, {"::1"},
    {"localhost", "localhost."}, {"localhost", ".localhost"}, {"localhost", "a..localhost"},
    {"localhost", "-a.localhost"}, {"0.0.0.0"}, "localhost", {"localhost", 1},
])
def test_browser_names_refuses_any_other_name(names):
    with pytest.raises(ValueError):
        shell(names=names)


def test_browser_requires_a_callable_session_and_fallback():
    with pytest.raises(ValueError):
        browser(Table(), session=None, names={"127.0.0.1"})
    with pytest.raises(ValueError):
        browser(Table(), session=cookie_or_token, names={"127.0.0.1"}, fallback="x")


# The Origin and Fetch Metadata matrix.

def test_a_same_origin_act_passes():
    assert check(br("POST", b"/m/usage/api/refresh", act_headers()), shell()) is None
    assert check(br("POST", b"/m/usage/api/refresh", act_headers(site=None)), shell()) is None


@pytest.mark.parametrize(("origin", "site"), [
    (None, b"same-origin"),
    (None, None),
    (b"http://127.0.0.1:8441", b"same-site"),
    (b"http://127.0.0.1:8441", None),
    (b"http://localhost:8440", b"same-site"),
    (b"https://127.0.0.1:8440", b"same-origin"),
    (b"http://evil.example", b"cross-site"),
    (b"null", b"cross-site"),
    (b"", b"same-origin"),
    (ORIGIN, b"same-site"),
    (ORIGIN, b"cross-site"),
    (ORIGIN, b"none"),
    (ORIGIN + b"/", b"same-origin"),
])
def test_an_act_from_anywhere_but_this_origin_is_refused(origin, site):
    request = br("POST", b"/m/usage/api/refresh", act_headers(origin, site))
    assert check(request, shell()) == Refusal(403, 3)


def test_two_origins_or_two_sites_are_refused():
    request = br("POST", b"/m/usage/api/refresh", act_headers() + [(b"origin", ORIGIN)])
    assert check(request, shell()) == Refusal(403, 3)
    request = br("GET", extra=[(b"sec-fetch-site", b"same-origin")] * 2)
    assert check(request, shell()) == Refusal(403, 3)


@pytest.mark.parametrize(("site", "origin", "ok"), [
    (None, None, True),
    (b"same-origin", None, True),
    (b"none", None, True),
    (b"same-origin", ORIGIN, True),
    (b"same-site", None, False),
    (b"cross-site", None, False),
    (b"same-origin", b"http://127.0.0.1:8441", False),
    (None, b"http://evil.example", False),
])
def test_a_read_passes_only_from_this_origin_or_with_no_site(site, origin, ok):
    extra = [(b"sec-fetch-mode", b"cors")]
    if site is not None:
        extra.append((b"sec-fetch-site", site))
    if origin is not None:
        extra.append((b"origin", origin))
    for path in (b"/m/usage/api/summary", b"/m/usage/api/events"):
        assert check(br("GET", path, extra), shell()) == (None if ok else Refusal(403, 3))


def navigation(site=b"cross-site"):
    return [(b"sec-fetch-site", site), (b"sec-fetch-mode", b"navigate"),
            (b"sec-fetch-dest", b"document")]


def test_a_cross_site_navigation_opens_a_page_or_asset_but_not_a_read():
    profile = shell()
    assert check(br("GET", b"/", navigation()), profile) is None
    table = shell_table()
    table.mount("/m/web", {"format": 1, "routes": [], "assets": ["index.html", "app.js"]})
    profile = browser(table, session=cookie_or_token, names={"127.0.0.1"})
    assert check(br("GET", b"/m/web/", navigation()), profile) is None
    assert check(br("GET", b"/m/usage/api/summary", navigation()), profile) == Refusal(403, 3)


@pytest.mark.parametrize(("mode", "dest"), [
    (b"no-cors", b"script"), (b"cors", b"empty"), (b"navigate", b"iframe"),
    (b"no-cors", b"document"), (None, b"document"), (b"navigate", None),
])
def test_a_cross_site_fetch_of_a_page_or_file_is_refused(mode, dest):
    extra = [(b"sec-fetch-site", b"cross-site")]
    if mode is not None:
        extra.append((b"sec-fetch-mode", mode))
    if dest is not None:
        extra.append((b"sec-fetch-dest", dest))
    assert check(br("GET", b"/", extra), shell()) == Refusal(403, 3)


# The token class.

def token_post(extra=()):
    headers = [(b"host", SHELL_HOST), (b"x-moxi-token", TOKEN.encode()), JSON]
    return view("POST", b"/api/stop", headers + list(extra), port=SHELL_PORT)


def test_a_token_request_with_no_browser_header_passes_gate_3_with_no_origin():
    assert check(token_post(), shell()) is None


@pytest.mark.parametrize("header", [
    (b"cookie", b"s=other"), (b"origin", b"http://evil.example"), (b"referer", b"http://x/"),
    (b"sec-fetch-site", b"same-origin"), (b"sec-fetch-mode", b"cors"),
    (b"Sec-Fetch-User", b"?1"),
])
def test_a_token_request_with_any_browser_header_takes_the_act_rule(header):
    # With no Origin, the act rule refuses it.
    assert check(token_post([header]), shell()) == Refusal(403, 3)


def test_a_token_request_with_a_matching_origin_passes_the_act_rule():
    assert check(token_post([(b"origin", ORIGIN), (b"sec-fetch-site", b"same-origin")]),
                 shell()) is None


def test_a_token_request_with_a_wrong_token_is_refused_at_the_credential():
    headers = [(b"host", SHELL_HOST), (b"x-moxi-token", OTHER.encode()), JSON]
    assert check(view("POST", b"/api/stop", headers, port=SHELL_PORT), shell()) == Refusal(403, 4)


def test_token_matches_a_right_a_wrong_a_doubled_and_a_missing_token():
    def with_tokens(*tokens):
        return view(headers=[(b"x-moxi-token", t) for t in tokens])

    assert token_matches(with_tokens(TOKEN.encode()), TOKEN)
    assert token_matches(view(headers=[(b"X-Moxi-Token", TOKEN.encode())]), TOKEN)
    assert not token_matches(with_tokens(OTHER.encode()), TOKEN)
    assert not token_matches(with_tokens(TOKEN.encode(), TOKEN.encode()), TOKEN)
    assert not token_matches(with_tokens(), TOKEN)
    assert not token_matches(with_tokens(TOKEN.encode()[:-1]), TOKEN)
    assert not token_matches(with_tokens(TOKEN.encode()[:-2] + "é".encode()), TOKEN)
    assert not token_matches(with_tokens(b""), "")
    assert not token_matches(with_tokens(b"x"), None)


# Credential and the one lookup.

def test_the_session_gets_the_one_entry_and_none_for_a_404():
    calls = []

    def session(view, entry):
        calls.append(entry)
        return True

    profile = shell(session=session)
    assert check(br(), profile) is None
    assert calls == [Entry("GET", "/m/usage/api/summary", "read")]
    assert calls[0] is profile.table.find("GET", b"/m/usage/api/summary")
    calls.clear()
    assert check(br("GET", b"/m/usage/api/nowhere"), profile) == Refusal(404, 5)
    assert calls == [None]


def test_a_session_refusal_gives_403_before_the_404():
    profile = shell(session=lambda view, entry: False)
    assert check(br("GET", b"/m/usage/api/nowhere"), profile) == Refusal(403, 4)
    assert check(br(), profile) == Refusal(403, 4)


def test_only_true_from_the_session_admits():
    assert check(br(), shell(session=lambda view, entry: 1)) == Refusal(403, 4)
    assert check(br(), shell(session=lambda view, entry: "yes")) == Refusal(403, 4)


def test_prove_is_exempt_on_the_browser_profile_and_the_session_is_not_asked():
    calls = []
    profile = shell(session=lambda view, entry: calls.append(entry) or False)
    assert check(br("GET", b"/api/prove", cookie=False), profile) is None
    assert calls == []
    assert check(br("GET", b"/m/usage/api/prove", cookie=False), profile) == Refusal(403, 4)


def test_prove_is_not_exempt_when_the_table_lacks_it():
    table = Table()
    profile = browser(table, session=lambda v, e: False, names={"127.0.0.1"},
                      fallback=lambda v: Entry("GET", "/api/prove", "builtin"))
    assert check(br("GET", b"/api/prove", cookie=False), profile) == Refusal(403, 4)


def test_the_fallback_is_asked_only_when_the_table_has_no_entry():
    asked = []

    def fallback(view):
        asked.append(view.raw_path)
        return Entry("GET", "/shell-page", "builtin")

    seen = []
    profile = shell(session=lambda v, e: seen.append(e) or True, fallback=fallback)
    assert check(br("GET", b"/m/usage/api/summary"), profile) is None
    assert asked == []
    assert check(br("GET", b"/m/usage/somewhere"), profile) is None
    assert asked == [b"/m/usage/somewhere"]
    assert seen[-1] == Entry("GET", "/shell-page", "builtin")


def test_the_fallback_is_never_asked_for_a_raw_path_with_a_percent():
    asked = []
    profile = shell(fallback=lambda view: asked.append(1) or Entry("GET", "/x", "builtin"))
    assert check(br("GET", b"/m/usage/%61pi/summary"), profile) == Refusal(404, 5)
    assert check(br("GET", b"/%2e%2e/x"), profile) == Refusal(404, 5)
    assert asked == []


@pytest.mark.parametrize("answer", [
    Entry("GET", "/x", "read"),
    Entry("GET", "/x", "asset"),
    Entry("POST", "/x", "builtin"),
    Entry("POST", "/x", "act"),
    "builtin",
    None,
])
def test_a_fallback_answer_other_than_a_get_builtin_gives_the_404(answer):
    profile = shell(fallback=lambda view: answer)
    assert check(br("GET", b"/somewhere"), profile) == Refusal(404, 5)


def test_a_post_is_never_given_to_the_fallback():
    asked = []
    profile = shell(fallback=lambda view: asked.append(1) or Entry("GET", "/x", "builtin"))
    request = br("POST", b"/somewhere", act_headers())
    assert check(request, profile) == Refusal(404, 5)
    assert asked == []


def test_entry_kinds_on_a_mounted_export_drive_gate_3():
    profile = shell()
    # The stream read takes the read rule: a cross-site navigation does not open it.
    assert check(br("GET", b"/m/usage/api/events", navigation()), profile) == Refusal(403, 3)
    assert profile.table.find("GET", b"/m/usage/api/events").stream


def test_building_a_profile_freezes_its_table():
    table = shell_table()
    profile = browser(table, session=cookie_or_token, names={"127.0.0.1"})
    assert profile.table is table and table.frozen
    with pytest.raises(ValueError):
        table.add("GET", "/more")
    with pytest.raises(ValueError):
        table.mount("/m/other", export(PAGE))
    table2, _ = build_table(PAGE)
    upstream(table2, token=TOKEN)
    with pytest.raises(ValueError):
        table2.add("GET", "/more")


def test_a_replaced_profile_holds_from_the_next_request_on():
    first = shell()
    table = shell_table()
    table.mount("/m/new", export(PAGE))
    second = browser(table, session=cookie_or_token, names={"127.0.0.1"})
    current = [first]

    def checked(request):
        return check(request, current[0])

    request = br("GET", b"/m/new/api/summary")
    assert checked(request) == Refusal(404, 5)
    current[0] = second
    assert checked(request) is None
    assert check(request, first) == Refusal(404, 5)
    assert check(br(host=b"localhost:8440"), first) is None
    assert check(br(host=b"localhost:8440"), second) == Refusal(403, 1)


def test_the_profile_cannot_be_changed():
    profile = shell()
    with pytest.raises(AttributeError):
        profile.names = frozenset({"evil.example"})


# --- the proof ----------------------------------------------------------------------


def test_proof_is_the_hmac_of_the_prove_text_the_nonce_and_the_server_id():
    nonce = secrets.token_urlsafe(32)
    server_id = secrets.token_hex(16)
    want = hmac.new(TOKEN.encode(), b"moxi-loopback-prove\n" + nonce.encode() + b"\n"
                    + server_id.encode(), hashlib.sha256).hexdigest()
    assert proof(TOKEN, server_id, nonce) == want
    assert proof(OTHER, server_id, nonce) != want
    assert proof(TOKEN, secrets.token_hex(16), nonce) != want
    assert proof(TOKEN, server_id, secrets.token_urlsafe(32)) != want


@pytest.mark.parametrize(("token", "server_id", "nonce"), [
    (TOKEN, "a" * 32, "short"),
    (TOKEN, "a" * 32, "x" * 42 + "!"),
    (TOKEN, "a" * 32, None),
    ("", "a" * 32, "n" * 43),
    (TOKEN, "", "n" * 43),
    (TOKEN, "é", "n" * 43),
])
def test_proof_refuses_a_bad_nonce_token_or_server_id(token, server_id, nonce):
    with pytest.raises(ValueError):
        proof(token, server_id, nonce)


def test_prove_nonce_reads_one_well_formed_nonce():
    nonce = secrets.token_urlsafe(32)
    assert prove_nonce(view(headers=[(b"X-Moxi-Nonce", nonce.encode())])) == nonce
    assert prove_nonce(view()) is None
    assert prove_nonce(view(headers=[(b"x-moxi-nonce", nonce.encode())] * 2)) is None
    for bad in (b"short", nonce.encode() + b"x", nonce.encode()[:-1] + b"!", b"\xc3" * 43):
        assert prove_nonce(view(headers=[(b"x-moxi-nonce", bad)])) is None

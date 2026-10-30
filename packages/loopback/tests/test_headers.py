"""The fixed header set and its assembler's injection guards."""

import pytest

from moxi_loopback import headers
from moxi_loopback.headers import CSP, NO_CACHE, SECURITY_HEADERS, complete, csp, response_headers

EXPECTED_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'; require-trusted-types-for 'script'"
)


def test_the_csp_is_the_one_constant():
    assert CSP == EXPECTED_CSP


def test_every_response_carries_the_whole_set_and_no_store():
    got = dict(response_headers())
    assert got == {
        b"content-security-policy": EXPECTED_CSP.encode(),
        b"x-content-type-options": b"nosniff",
        b"referrer-policy": b"no-referrer",
        b"cross-origin-opener-policy": b"same-origin",
        b"cross-origin-resource-policy": b"same-origin",
        b"permissions-policy": b"camera=(), microphone=(), geolocation=(), usb=(), payment=()",
        b"cache-control": b"no-store",
    }


def test_an_asset_gets_no_cache_and_its_own_headers_follow_the_set():
    got = response_headers(cache=NO_CACHE, extra=[("Content-Type", "text/css"), ("ETag", '"ab"')])
    assert (b"cache-control", b"no-cache") in got
    assert got[-2:] == [(b"content-type", b"text/css"), (b"etag", b'"ab"')]
    assert all(name == name.lower() for name, _ in got)


@pytest.mark.parametrize("cache", ["public", "max-age=60", ""])
def test_cache_takes_only_no_store_or_no_cache(cache):
    with pytest.raises(ValueError):
        response_headers(cache=cache)


@pytest.mark.parametrize("name", ["Content-Security-Policy", "cache-control", "Referrer-Policy",
                                  "Set-Cookie", "Access-Control-Allow-Origin"])
def test_an_extra_header_cannot_replace_the_set_or_add_a_cookie_or_cors(name):
    with pytest.raises(ValueError):
        response_headers(extra=[(name, "x")])


@pytest.mark.parametrize("value", ["a\r\nSet-Cookie: x=1", "a\nb", "a\rb", "a\0b", "café"])
def test_an_extra_value_with_a_newline_nul_or_non_ascii_is_refused(value):
    with pytest.raises(ValueError):
        response_headers(extra=[("X-Thing", value)])


@pytest.mark.parametrize("name", ["X Thing", "X:Thing", "", "X-Thing\n", "café"])
def test_an_extra_name_that_is_not_a_token_is_refused(name):
    with pytest.raises(ValueError):
        response_headers(extra=[(name, "x")])


def test_csp_keeps_the_order_given_and_emits_a_valueless_directive_bare():
    assert csp([("b-src", "'self'"), ("a-src", "'none'"), ("upgrade-insecure-requests", "")]) == (
        "b-src 'self'; a-src 'none'; upgrade-insecure-requests"
    )


@pytest.mark.parametrize("directives", [
    [("default-src\n", "'none'")],
    [("default src", "'none'")],
    [("default-src", "'none'; script-src *")],
    [("default-src", "'none', *")],
    [("default-src", "'none'\r\nX: y")],
    [("default-src", "'self'"), ("Default-Src", "'none'")],
    [],
])
def test_csp_refuses_injection_shapes(directives):
    with pytest.raises(ValueError):
        csp(directives)


def test_complete_adds_only_what_is_missing():
    got = complete([(b"Content-Type", b"text/plain"), (b"cache-control", b"no-cache"),
                    (b"X-Content-Type-Options", b"nosniff")])
    names = [name.lower() for name, _ in got]
    assert len(names) == len(set(names))
    assert set(names) >= {name.lower().encode() for name, _ in SECURITY_HEADERS}
    assert dict((n.lower(), v) for n, v in got)[b"cache-control"] == b"no-cache"


def test_complete_adds_no_store_when_cache_control_is_missing():
    assert (b"cache-control", b"no-store") in complete([])


def test_the_module_exports_what_it_names():
    assert set(headers.__all__) <= set(dir(headers))

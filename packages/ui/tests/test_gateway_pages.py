"""The gateway's pairing and sign-in pages as the ui ships them: each a document that loads
its script from its own open path, with no inline script or style, so the gateway's one
policy serves them unchanged."""

from __future__ import annotations

import re

from moxi_ui.loopback.api import response_headers
from test_gateway_redact import Gate

from moxi_ui.gateway.access import PAGES, Access, web_file

_SCRIPT = re.compile(rb"<script\b([^>]*)>(.*?)</script>", re.S)


def _access(tmp_path):
    return Access(state_dir=tmp_path / "state", runtime_dir=tmp_path / "run", kept=lambda: None)


def test_each_page_and_script_is_shipped_in_the_ui_web_files():
    for name, _kind in PAGES.values():
        body = web_file(name)
        assert body, name


def test_the_pages_load_only_their_own_scripts_and_hold_no_inline_script_or_style():
    for page, script in (("pair.html", b"/pair.js"), ("signin.html", b"/signin.js")):
        body = web_file(page)
        scripts = _SCRIPT.findall(body)
        assert len(scripts) == 1, page
        attrs, inline = scripts[0]
        assert b'src="' + script + b'"' in attrs, page
        assert inline.strip() == b"", page
        assert not re.search(rb"<style|\sstyle=|\son[a-z]+=", body, re.I), page


def test_the_pages_are_sent_with_the_gateway_policy_and_clear_the_origin_first(tmp_path):
    access = _access(tmp_path)
    policy = dict(response_headers(policy="gateway"))[b"content-security-policy"]
    for path, (_name, kind) in PAGES.items():
        status, headers, body = access.page(path)
        found = dict(headers)
        assert status == 200, path
        assert found[b"content-security-policy"] == policy, path
        assert found[b"content-type"] == kind.encode(), path
        assert found[b"cache-control"] == b"no-store", path
        assert body == web_file(PAGES[path][0]), path
        if kind.startswith("text/html"):
            assert found[b"clear-site-data"] == b'"cache", "storage"', path
        else:
            assert b"clear-site-data" not in found, path


#: The ways a phone can ask for a page at its bare path: no fragment reaches the server, and
#: any query may come with it, the sign-in redirect's `n` included.
_QUERIES = ("", "?", "?n=0123456789abcdef", "?x=1&x=2", "?c=abc")
_HOW = {
    "fetch": (),
    "navigation": (("sec-fetch-mode", "navigate"), ("sec-fetch-dest", "document")),
    "the worker's navigation": (("x-moxi-navigation", "1"), ("sec-fetch-mode", "same-origin"),
                                ("sec-fetch-dest", "empty"), ("cache-control", "no-cache")),
    "cross-site": (("sec-fetch-site", "cross-site"),),
}
_METHODS = ("GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH")


def test_no_answer_for_a_bare_page_can_be_kept_by_the_http_cache(tmp_path):
    """A history entry left at a bare `/pair` or `/signin` loads again from the network:
    whatever the method, query, headers or session, every answer through the gateway's
    request path is sent `no-store`, so the browser's HTTP cache keeps none of them. The
    worker passes their navigations to the browser and stores no answer, and the off answer
    in front of the router is `no-store` too."""
    signed_out = Gate(tmp_path / "out")
    signed_in = Gate(tmp_path / "in").paired()
    missing = Gate(tmp_path / "missing")
    missing.phone.access.pages = {}.get
    statuses = set()
    for who, gate in (("signed out", signed_out), ("signed in", signed_in),
                      ("no page file", missing)):
        for page in ("/pair", "/signin"):
            for query in _QUERIES:
                for how, headers in _HOW.items():
                    for method in _METHODS:
                        answer = gate.phone.request(method, page + query, headers=headers)
                        where = (who, method, page + query, how, answer.status)
                        assert answer.header("cache-control") == ["no-store"], where
                        statuses.add(answer.status)
    assert {200, 403, 404} <= statuses

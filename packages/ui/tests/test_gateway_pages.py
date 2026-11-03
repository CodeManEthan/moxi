"""The gateway's pairing and sign-in pages as the ui ships them: each a document that loads
its script from its own open path, with no inline script or style, so the gateway's one
policy serves them unchanged."""

from __future__ import annotations

import re

from moxi_loopback.api import response_headers

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

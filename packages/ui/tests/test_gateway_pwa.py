"""The PWA as the gateway serves it: the worker at `/sw.js` and as the answer to every
worker script request, the manifest, the icons, the offline page and its script, each with
the gateway's policy; and a navigation the worker sends on, which still gets the shell
document or the way to sign in."""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from moxi.paths import Layout
from test_gateway_redact import VALUE, Gate, _rows_body, gateway_policy

from moxi_ui import document as _document
from moxi_ui.gateway.access import OPEN_PATHS, web_file
from moxi_ui.gateway.mount import Mount
from moxi_ui.gateway.router import ICONS, PWA_FILES, document

_TOKENS = Path(__file__).resolve().parents[1] / "web" / "tokens.json"
_SCRIPT = re.compile(rb"<script\b([^>]*)>(.*?)</script>", re.S)
_FORWARDED = (("x-moxi-navigation", "1"), ("sec-fetch-mode", "same-origin"),
              ("sec-fetch-dest", "empty"), ("cache-control", "no-cache"))


def _gate(tmp_path: Path, *, paired: bool = False) -> Gate:
    gate = Gate(tmp_path)
    gate.router.files = web_file
    return gate.paired() if paired else gate


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_every_pwa_file_ships_in_the_ui_web_files_and_is_open() -> None:
    for path, (name, _kind) in PWA_FILES.items():
        assert web_file(name), path
        assert path in OPEN_PATHS | ICONS, path


def test_the_manifest_starts_the_shell_in_the_default_themes_light_colour_and_two_maskable_icons() -> None:
    manifest = json.loads(web_file("manifest.json"))
    tokens = json.loads(_TOKENS.read_text())
    light = next(iter(tokens["$themes"].values()))["light"]
    background = tokens["bg"]["$extensions"]["moxi"]["sets"][light]
    assert (light, background) == ("plain-day", "#fafaf8")
    assert manifest["name"] == "Moxi"
    assert (manifest["start_url"], manifest["scope"], manifest["display"]) == (
        "/m/ui/", "/", "standalone")
    assert manifest["background_color"] == manifest["theme_color"] == background
    sizes = []
    for icon in manifest["icons"]:
        assert icon["type"] == "image/png" and "maskable" in icon["purpose"].split()
        assert icon["src"] in ICONS and icon["src"] in PWA_FILES
        width, height = _png_size(web_file(PWA_FILES[icon["src"]][0]))
        assert icon["sizes"] == f"{width}x{height}"
        sizes.append(width)
    assert sorted(sizes) == [192, 512]


def test_the_offline_page_loads_only_its_own_script_and_holds_no_inline_script_or_style() -> None:
    body = web_file("offline.html")
    scripts = _SCRIPT.findall(body)
    assert len(scripts) == 1
    attrs, inline = scripts[0]
    assert b'src="/offline.js"' in attrs and inline.strip() == b""
    assert not re.search(rb"<style|\sstyle=|\son[a-z]+=", body, re.I)


def test_before_sign_in_each_pwa_file_is_sent_as_shipped_with_the_gateway_policy(
        tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    for path, (name, kind) in PWA_FILES.items():
        answer = gate.get(path)
        assert answer.status == 200, path
        assert answer.body == web_file(name), path
        assert answer.header("content-type") == [kind], path
        assert answer.header("cache-control") == ["no-store"], path
        assert gateway_policy(answer), path


def test_every_worker_script_request_gets_sw_js_whatever_its_url(tmp_path: Path) -> None:
    for paired in (False, True):
        gate = _gate(tmp_path / str(paired), paired=paired)
        for path in ("/sw.js", "/planted/worker.js", "/m/ui/client.js", "/m/fixture/x.js?v=2"):
            answer = gate.get(path, headers=(("service-worker", "script"),))
            assert answer.status == 200, path
            assert answer.body == web_file("sw.js"), path
            assert answer.header("service-worker-allowed") == ["/"], path
            assert answer.header("cache-control") == ["no-store"], path
            assert answer.header("content-type") == ["text/javascript; charset=utf-8"], path
            assert gateway_policy(answer), path


def test_a_navigation_the_worker_sent_on_gets_the_document_or_the_way_to_sign_in(
        tmp_path: Path) -> None:
    gate = _gate(tmp_path, paired=True)
    for path in ("/", "/m/ui/", "/m/fixture/rows?x=1"):
        answer = gate.get(path, headers=_FORWARDED)
        assert answer.status == 200, path
        assert b'data-gateway="on"' in answer.body, path
        assert b'<link rel="manifest" href="/manifest.webmanifest">' in answer.body, path
        assert gateway_policy(answer), path
    assert gate.get("/m/ui/", headers=_FORWARDED, cookie=False).status == 303
    signed_out = _gate(tmp_path / "out")
    answer = signed_out.get("/m/ui/", headers=_FORWARDED)
    assert answer.status == 303 and answer.header("location")[0].startswith("/signin?n=")


def test_only_one_mark_of_1_on_a_get_makes_a_navigation(tmp_path: Path) -> None:
    gate = _gate(tmp_path, paired=True)
    plain = _FORWARDED[1:]
    assert gate.get("/m/ui/", headers=plain).status == 404
    assert gate.get("/m/ui/", headers=(("x-moxi-navigation", "yes"), *plain)).status == 404
    assert gate.get("/m/ui/", headers=(*_FORWARDED, ("x-moxi-navigation", "1"))).status == 404
    posted = gate.phone.request("POST", "/m/ui/", {}, headers=_FORWARDED)
    assert posted.status in (403, 404)
    assert b"data-gateway" not in posted.body


#: Every kind of path a marked GET could try for: a module's route and file, the push
#: stream, the act paths, the shell's files and routes, sign-out, the PWA's files.
_MARKED_PATHS = ("/m/fixture/api/rows?x=1", "/m/ui/api/push", "/gw/act/prepare",
                 "/gw/act/commit", "/gw/act/state?act=x", "/m/ui/client.js", "/m/ui/theme.css",
                 "/m/fixture/page.js", "/m/ui/api/devices", "/api/signout",
                 "/m/ui/api/modules", "/manifest.webmanifest", "/offline", "/sw.js",
                 "/nosuch")


def test_a_marked_get_on_any_path_gets_only_the_document_or_the_way_to_sign_in(
        tmp_path: Path) -> None:
    """The navigation mark relaxes no gate and reaches no route's data: with a live cookie
    it gets the shell document, without one the redirect to sign in, whatever the path and
    whether or not the key comes with it."""
    gate = _gate(tmp_path, paired=True)
    gate.reads.answers["/api/rows?x=1"] = _rows_body(VALUE)
    plain = gate.get("/m/fixture/api/rows?x=1")
    assert plain.status == 200 and b"/plain/text" in plain.body
    for headers in (_FORWARDED[:1], _FORWARDED):
        for path in _MARKED_PATHS:
            for cookie, key in ((True, True), (True, False), (False, True), (False, False)):
                answer = gate.get(path, headers=headers, cookie=cookie, key=key)
                where = (path, headers, cookie, key)
                assert b"/plain/text" not in answer.body, where
                if cookie:
                    assert answer.status == 200, where
                    assert b'data-gateway="on"' in answer.body, where
                else:
                    assert answer.status == 303, where
                    assert answer.header("location")[0].startswith("/signin?n="), where


def test_a_marked_cross_site_get_is_refused(tmp_path: Path) -> None:
    gate = _gate(tmp_path, paired=True)
    for path in ("/m/ui/", "/m/fixture/api/rows?x=1", "/nosuch"):
        answer = gate.get(path, headers=(*_FORWARDED[:1], ("sec-fetch-site", "cross-site")))
        assert answer.status == 403, path


def test_the_desktops_document_neither_links_the_manifest_nor_marks_the_gateway() -> None:
    body = _document.body("system", "plain")
    assert b"manifest" not in body and b"data-gateway" not in body


def test_the_gateways_document_carries_the_mode_and_the_theme_before_its_own_marks() -> None:
    _status, _headers, body = document(Mount(mounted=True, reason=None), "dark", "midnight-neon")
    assert (b'<html lang="en" data-theme="dark" data-theme-name="midnight-neon"'
            b' data-gateway="on" data-gateway-acts="on">') in body
    assert body.count(b"<html") == 1
    _status, _headers, body = document(Mount(mounted=False, reason="tcp"))
    assert (b'<html lang="en" data-theme="system" data-theme-name="plain"'
            b' data-gateway="on" data-gateway-acts="off" data-gateway-reason="tcp">') in body
    path = Layout("ui").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[theme]\nmode = "light"\npalette = "midnight-neon"\n')
    _status, _headers, body = document(Mount(mounted=True, reason=None))
    assert b'<html lang="en" data-theme="light" data-theme-name="midnight-neon" data-gateway="on"' in body
    path.write_text("this is not toml = = =\n")
    _status, _headers, body = document(Mount(mounted=True, reason=None))
    assert b'<html lang="en" data-theme="system" data-theme-name="plain" data-gateway="on"' in body

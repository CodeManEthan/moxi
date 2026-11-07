"""The built web files: the theme, the shell document, client.js and their serving.

These read the committed build output and need no Node, except the last, which runs the
client's own Node tests when Node and the root `npm ci` are there.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from moxi.paths import Layout
from moxi_ui.loopback.api import app, export, guard, prove
from moxi_ui import document, page
from moxi_ui.router import Router, shell_profile, shell_table, worker_layer
from shell_kit import FETCH, NAVIGATE, SERVER_ID, TOKEN, Harness, _drive, scope

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[1]
WEB = importlib.resources.files("moxi_ui").joinpath("web")
TOKENS = json.loads((PACKAGE / "web" / "tokens.json").read_text())


def _built(name: str) -> str:
    return WEB.joinpath(name).read_text(encoding="utf-8")


# --- the theme --------------------------------------------------------------------------


def test_tokens_become_theme_css_26_tokens_each_colour_a_light_dark_pair():
    names = [name for name in TOKENS if not name.startswith("$")]
    assert len(names) == 26
    css = _built("theme.css")
    props = dict(re.findall(r"^  --moxi-([a-z0-9-]+): (.*);$", css, re.M))
    assert sorted(props) == sorted(names)
    colours = [name for name in names if TOKENS[name]["$type"] == "color"]
    assert len(colours) == 19
    for name in colours:
        token = TOKENS[name]
        assert props[name] == f"light-dark({token['$value']}, {token['$extensions']['moxi']['dark']})"
    for name in set(names) - set(colours):
        assert "light-dark" not in props[name]
    assert ":root {\n  color-scheme: light dark;" in css
    assert ':root[data-theme="light"] { color-scheme: light; }' in css
    assert ':root[data-theme="dark"] { color-scheme: dark; }' in css


# --- the document -----------------------------------------------------------------------


def test_the_document_loads_the_theme_and_the_client_and_holds_no_inline_code():
    status, headers, body = document.answer("dark")
    names = {k: v for k, v in headers}
    assert status == 200 and names[b"content-type"].startswith(b"text/html")
    assert names[b"cache-control"] == b"no-store"
    text = body.decode()
    assert '<html lang="en" data-theme="dark">' in text
    assert '<link rel="stylesheet" href="/m/ui/theme.css">' in text
    assert '<script type="module" src="/m/ui/client.js"></script>' in text
    assert 'id="moxi-shell"' in text and 'id="moxi-panel"' in text and 'id="moxi-menu-button"' in text
    # The page's policy allows scripts and styles from the origin only.
    assert re.search(r"<script(?![^>]*\bsrc=)", text) is None
    assert "<style" not in text and "style=" not in text


def test_the_document_takes_its_mode_from_the_setting():
    assert b'data-theme="system"' in document.answer()[2]
    path = Layout("ui").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[theme]\nmode = "light"\n')
    assert b'data-theme="light"' in document.answer()[2]
    path.write_text("this is not toml = = =\n")
    assert b'data-theme="system"' in document.answer()[2]
    with pytest.raises(ValueError):
        document.body("sepia")


def test_a_navigation_with_a_cookie_gets_the_document(tmp_path):
    shell = Harness(tmp_path / "state")
    cookie, _ = shell.sign_in()
    answer = shell.send("GET", "/m/fixture/some/route", headers=[cookie, *NAVIGATE])
    assert answer.status == 200 and b'id="moxi-shell"' in answer.body
    answer = shell.send("GET", "/m/fixture/some/route", headers=list(NAVIGATE))
    assert answer.status == 200 and b"not signed in" in answer.body


# --- the assets -------------------------------------------------------------------------


def test_the_shell_page_serves_the_built_files_to_a_cookie(tmp_path):
    shell = Harness(tmp_path / "state")
    own = app(page.PAGE, module="ui", server_id=SERVER_ID, prefix="/m/ui")
    router = Router(shell.sessions, page=own, push=shell.push, proxy=shell.proxy)
    table = shell_table(["fixture"], {"ui": export(page.PAGE)})
    profile = shell_profile(table, shell.sessions, ("127.0.0.1",))
    asgi = worker_layer(guard(prove(router, token=TOKEN, server_id=SERVER_ID), lambda: profile))
    cookie, _ = shell.sign_in()
    script = [(b"sec-fetch-site", b"same-origin"), (b"sec-fetch-mode", b"cors"),
              (b"sec-fetch-dest", b"script")]
    for name, kind in [("client.js", "text/javascript"), ("theme.css", "text/css"),
                       ("shell.css", "text/css")]:
        answer = asyncio.run(_drive(asgi, scope("GET", f"/m/ui/{name}",
                                                headers=[cookie, *script]), b""))
        assert answer.status == 200, name
        assert answer.header("content-type").startswith(kind)
        assert answer.body == WEB.joinpath(name).read_bytes()
    # Without the cookie an asset is refused.
    answer = asyncio.run(_drive(asgi, scope("GET", "/m/ui/client.js", headers=list(FETCH)), b""))
    assert answer.status == 403


def test_client_js_is_the_contract_and_imports_nothing():
    text = _built("client.js")
    assert re.search(r"\bimport\s*[\w{*]", text) is None and 'from"' not in text
    decl = (REPO / "tools" / "web" / "client.d.ts").read_text()
    for name in ("installed", "components", "place", "api", "subscribe", "navigate", "setTheme"):
        assert f"export declare function {name}(" in decl
    assert "export declare const CONTRACT: number;" in decl
    assert "\nimport " not in decl and "declare module" not in decl
    # The form renderer is the client's, with its descriptor and event types.
    for name in ("FieldDescriptor", "FieldEntry", "FormChange", "FormSubmit", "FormElement"):
        assert f"export interface {name} " in decl
    assert '"moxi-ui-form": FormElement;' in decl
    assert '"moxi-ui-form"' in text
    # client.js holds no Lit; each component bundles its own.
    assert "lit-html" not in text and "LitElement" not in text


def test_the_shells_own_components_are_placed_from_its_build():
    from moxi_ui import module
    from moxi_ui.snapshot import check_components

    assets = export(page.PAGE)["assets"]
    placed, refused = check_components("ui", module.COMPONENTS, page=True, assets=assets)
    assert refused == []
    assert {item["name"]: item["element"] for item in placed} == {
        "page": "moxi-ui-page", "servers": "moxi-ui-servers"}
    for item in placed:
        text = _built(item["script"])
        # Each imports only the shared client, by its absolute URL, and defines its
        # element once.
        # (esbuild keeps one import statement per source file that names it).
        assert set(re.findall(r'from"([^"]+)"', text)) == {"/m/ui/client.js"}
        assert re.search(r"\bimport\s*\(", text) is None
        assert f'"{item["element"]}"' in text and "customElements.get(" in text
        # Its own Lit, with Lit's licence kept at the end.
        assert "lit-html" in text and "SPDX-License-Identifier: BSD-3-Clause" in text


# --- the client's Node tests ------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None or not (REPO / "node_modules").is_dir(),
                    reason="needs node and the root npm ci")
def test_the_clients_node_tests_pass():
    done = subprocess.run([shutil.which("node"), "--test", "test/*.test.mjs"],
                          cwd=PACKAGE / "web", capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr

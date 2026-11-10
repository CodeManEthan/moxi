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
from moxi_ui import config, document, page
from moxi_ui.router import Router, shell_profile, shell_table, worker_layer
from shell_kit import FETCH, NAVIGATE, SERVER_ID, TOKEN, Harness, _drive, scope

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[1]
WEB = importlib.resources.files("moxi_ui").joinpath("web")
TOKENS = json.loads((PACKAGE / "web" / "tokens.json").read_text())


def _built(name: str) -> str:
    return WEB.joinpath(name).read_text(encoding="utf-8")


# --- the theme --------------------------------------------------------------------------


def _block(css: str, selector: str) -> dict[str, str]:
    """The `--moxi-` properties of the one block of `css` under `selector`."""
    (block,) = re.findall(r"^" + re.escape(selector) + r" \{\n(.*?)^\}", css, re.M | re.S)
    return dict(re.findall(r"^  --moxi-([a-z0-9-]+): (.*);$", block, re.M))


def test_tokens_become_theme_css_41_tokens_on_the_root_and_a_block_per_other_theme():
    names = [name for name in TOKENS if not name.startswith("$")]
    assert len(names) == 41
    themes = TOKENS["$themes"]
    assert themes == {"plain": {"light": "plain-day", "dark": "plain-night"},
                      "midnight-neon": {"light": "calm-workbench", "dark": "midnight-neon"}}
    assert list(themes) == ["plain", "midnight-neon"], "the first theme is the default"
    assert {name: entry["mode"] for name, entry in TOKENS["$sets"].items()} == {
        "plain-day": "light", "plain-night": "dark",
        "calm-workbench": "light", "midnight-neon": "dark"}
    css = _built("theme.css")
    colours = [name for name in names if TOKENS[name]["$type"] == "color"]
    assert len(colours) == 34

    def pair(name: str, theme: str) -> str:
        values = TOKENS[name]["$extensions"]["moxi"]["sets"]
        return f"light-dark({values[themes[theme]['light']]}, {values[themes[theme]['dark']]})"

    # The default theme, and every token that is no colour, on the root.
    props = _block(css, ":root")
    assert sorted(props) == sorted(names)
    for name in colours:
        assert props[name] == pair(name, "plain")
    for name in set(names) - set(colours):
        assert "light-dark" not in props[name]
    # Each other theme's colours where the root names it; the default has no block.
    assert re.findall(r'^:root\[data-theme-name="([^"]*)"\] \{$', css, re.M) == ["midnight-neon"]
    other = _block(css, ':root[data-theme-name="midnight-neon"]')
    assert sorted(other) == sorted(colours)
    for name in colours:
        assert other[name] == pair(name, "midnight-neon")
    assert ":root {\n  color-scheme: light dark;" in css
    assert ':root[data-theme="light"] { color-scheme: light; }' in css
    assert ':root[data-theme="dark"] { color-scheme: dark; }' in css
    header = css[:css.index("*/")]
    assert "The default theme, plain: light plain-day, dark plain-night." in header
    assert "midnight-neon: light calm-workbench, dark midnight-neon." in header


def test_the_theme_palettes_the_setting_takes_are_the_themes_tokens_json_names():
    assert list(config.THEME_PALETTES) == list(TOKENS["$themes"])
    key = config.schema().keys["theme.palette"]
    assert tuple(key.values) == config.THEME_PALETTES and key.default == config.THEME_PALETTES[0]


def test_the_theme_palette_falls_back_to_the_default():
    assert config.theme_palette() == "plain"
    path = Layout("ui").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[theme]\npalette = "midnight-neon"\n')
    assert config.theme_palette() == "midnight-neon" and config.theme_mode() == "system"
    path.write_text("this is not toml = = =\n")
    assert config.theme_palette() == "plain"
    assert config.theme_palette({"theme.palette": "sepia"}) == "plain"
    assert config.theme_palette({"theme.palette": "midnight-neon"}) == "midnight-neon"


# --- the document -----------------------------------------------------------------------


def test_the_document_loads_the_theme_and_the_client_and_holds_no_inline_code():
    status, headers, body = document.answer("dark", "midnight-neon")
    names = {k: v for k, v in headers}
    assert status == 200 and names[b"content-type"].startswith(b"text/html")
    assert names[b"cache-control"] == b"no-store"
    text = body.decode()
    assert '<html lang="en" data-theme="dark" data-theme-name="midnight-neon">' in text
    assert text.count("<html") == 1
    assert '<link rel="stylesheet" href="/m/ui/theme.css">' in text
    assert '<script type="module" src="/m/ui/client.js"></script>' in text
    assert 'id="moxi-shell"' in text and 'id="moxi-panel"' in text and 'id="moxi-menu-button"' in text
    # The page's policy allows scripts and styles from the origin only.
    assert re.search(r"<script(?![^>]*\bsrc=)", text) is None
    assert "<style" not in text and "style=" not in text


def test_the_document_takes_its_mode_and_its_theme_from_the_settings():
    assert b'<html lang="en" data-theme="system" data-theme-name="plain">' in document.answer()[2]
    path = Layout("ui").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[theme]\nmode = "light"\n')
    assert b'<html lang="en" data-theme="light" data-theme-name="plain">' in document.answer()[2]
    path.write_text('[theme]\nmode = "dark"\npalette = "midnight-neon"\n')
    assert (b'<html lang="en" data-theme="dark" data-theme-name="midnight-neon">'
            in document.answer()[2])
    path.write_text('[theme]\npalette = "midnight-neon"\n')
    assert (b'<html lang="en" data-theme="system" data-theme-name="midnight-neon">'
            in document.answer()[2])
    path.write_text("this is not toml = = =\n")
    assert b'<html lang="en" data-theme="system" data-theme-name="plain">' in document.answer()[2]
    with pytest.raises(ValueError):
        document.body("sepia", "plain")
    with pytest.raises(ValueError):
        document.body("dark", "sepia")
    with pytest.raises(ValueError):
        document.body("dark", 'plain" onload="x')


def test_the_pages_shown_before_sign_in_name_no_mode_and_no_theme():
    for name in ("pair.html", "signin.html", "offline.html"):
        text = _built(name)
        assert '<html lang="en">' in text, name
        assert "data-theme" not in text, name


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

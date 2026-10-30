"""The announcement and interface 1, as page modules, the shell and tests reach them."""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest
from moxi import modules

import asgi_kit
import moxi_loopback
from moxi_loopback import api, check, headers, module, routes, runtime, serve, server, transport

ROOT = Path(__file__).resolve().parents[1]

THREAT_MODEL = (
    "module pages are trusted code like their Python half; Moxi defends against the "
    "network and other users on the machine, not a module you chose to install."
)

# Each name of interface 1 and the file that defines it; `api` re-exports the same object.
SURFACE = {
    routes: {"Field", "Out", "Page", "PageError", "Route", "Entry", "Table", "build_table",
             "export"},
    check: {"Profile", "Refusal", "RequestView", "browser", "check", "proof", "token_matches",
            "upstream"},
    transport: {"BrowserSockets", "NotLoopback", "NotOurServer", "bind_browser",
                "browser_names", "call", "open_connection"},
    runtime: {"RuntimeInfo", "read_runtime", "remove_runtime", "write_runtime"},
    server: {"Accepted", "Call", "Conflict", "Refused", "Snapshot", "Topics", "app", "busy",
             "guard", "prove"},
    serve: {"serve"},
    headers: {"complete", "response_headers"},
}


# --- the announcement --------------------------------------------------------------


def test_the_announcement_offers_interface_1_and_nothing_else():
    assert module.INTERFACES == frozenset({1})
    assert module.API == "moxi_loopback.api"
    assert module.CLI is None and module.SETTINGS is None and module.SETUP is None
    assert module.OPTIONAL == {} and module.GATED == {} and module.INTERNAL_ENV == {}


def test_load_finds_the_module_and_its_api():
    modules.reset()
    found = modules.load("loopback", interface=1)
    assert found.ok, found
    assert found.api is api
    modules.reset()
    assert not modules.load("loopback", interface=2).ok
    modules.reset()


# --- the surface -------------------------------------------------------------------


def test_interface_1_holds_exactly_its_names():
    expected = set().union(*SURFACE.values()) | {"gate_matrix"}
    assert set(api.__all__) == expected
    assert len(api.__all__) == len(expected)


def test_every_name_is_the_defining_files_own_object():
    for home, names in SURFACE.items():
        for name in names:
            assert getattr(api, name) is getattr(home, name), name
            assert name in home.__all__, f"{home.__name__} does not list {name}"


def _shape(fn):
    """Each parameter as (name, kind, default), the default None when there is none."""
    kinds = {inspect.Parameter.POSITIONAL_OR_KEYWORD: "pos",
             inspect.Parameter.KEYWORD_ONLY: "kw",
             inspect.Parameter.VAR_POSITIONAL: "*"}
    out = []
    for p in inspect.signature(fn).parameters.values():
        default = None if p.default is inspect.Parameter.empty else p.default
        out.append((p.name, kinds[p.kind], default))
    return out


@pytest.mark.parametrize(("name", "shape"), [
    ("RequestView", [("method", "pos", None), ("raw_path", "pos", None),
                     ("headers", "pos", None), ("transport", "pos", None),
                     ("port", "pos", None)]),
    ("Entry", [("method", "pos", None), ("raw_path", "pos", None), ("kind", "pos", None),
               ("stream", "pos", False)]),
    ("upstream", [("table", "pos", None), ("token", "kw", None), ("port", "kw", None)]),
    ("browser", [("table", "pos", None), ("session", "kw", None), ("names", "kw", None),
                 ("fallback", "kw", None)]),
    ("check", [("view", "pos", None), ("profile", "pos", None)]),
    ("token_matches", [("view", "pos", None), ("token", "pos", None)]),
    ("bind_browser", [("port", "pos", 0)]),
    ("browser_names", [("sockets", "pos", None)]),
    ("write_runtime", [("module", "pos", None), ("transport", "kw", None),
                       ("address", "kw", None), ("port", "kw", None),
                       ("version", "kw", None)]),
    ("remove_runtime", [("module", "pos", None), ("server_id", "pos", None)]),
    ("read_runtime", [("module", "pos", None)]),
    ("proof", [("token", "pos", None), ("server_id", "pos", None), ("nonce", "pos", None)]),
    ("Topics", [("names", "*", None)]),
    ("Snapshot", [("gather", "pos", None), ("topics", "pos", None), ("name", "pos", None)]),
    ("guard", [("inner", "pos", None), ("profile", "pos", None)]),
    ("open_connection", [("info", "pos", None), ("timeout", "kw", 5.0)]),
    ("call", [("info", "pos", None), ("method", "pos", None), ("path", "pos", None),
              ("body", "pos", None), ("timeout", "kw", 5.0)]),
    ("gate_matrix", [("page", "pos", None)]),
])
def test_the_signatures_are_fixed(name, shape):
    assert _shape(getattr(api, name)) == shape


def test_app_and_serve_keep_the_in_process_signatures():
    # app adds `version` and `strict`, serve adds `head_limit`; both after the fixed ones.
    assert _shape(api.app)[:4] == [("page", "pos", None), ("module", "kw", None),
                                   ("server_id", "kw", None), ("prefix", "kw", "")]
    assert _shape(api.serve)[:4] == [("app", "pos", None), ("sockets", "pos", None),
                                     ("should_exit", "kw", None), ("drain", "kw", None)]
    assert _shape(api.serve)[4] == ("head_limit", "kw", None)
    assert inspect.iscoroutinefunction(api.serve)
    assert inspect.iscoroutinefunction(api.open_connection)


def test_importing_the_api_loads_no_server_library():
    # A hook or a page module that imports the api pays for no HTTP server; uvicorn,
    # Starlette and h11 load only inside `app`, `serve` and `call`.
    code = ("import sys, moxi_loopback.api\n"
            "print(sorted({m.split('.')[0] for m in sys.modules} & "
            "{'uvicorn', 'starlette', 'h11', 'anyio'}))\n")
    done = subprocess.run([sys.executable, "-P", "-c", code], capture_output=True, text=True,
                          timeout=60, check=True)
    assert done.stdout.strip() == "[]", done.stdout + done.stderr


def test_the_api_docstring_says_how_a_module_without_loopback_reaches_it():
    assert 'moxi.modules.load("loopback", interface=1).api' in api.__doc__


def test_the_threat_model_rides_verbatim_in_the_package_and_the_readme():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert THREAT_MODEL in " ".join(moxi_loopback.__doc__.split())
    assert THREAT_MODEL in " ".join(readme.split())


# --- gate_matrix -------------------------------------------------------------------


def _cases(page):
    reads = sum(1 for r in page.routes if r.kind == "read")
    acts = len(page.routes) - reads
    return reads * 6 + acts * 7


def test_gate_matrix_passes_over_a_sample_page_and_runs_no_handler(pages):
    page = asgi_kit.make_page()
    rows = api.gate_matrix(page)
    assert len(rows) == _cases(page)
    assert all(expected == got for _, _, expected, got in rows)
    by_case = {(path, case): got for path, case, _, got in rows}
    assert by_case[("/api/summary", "wrong method")] == 404
    assert by_case[("/api/summary", "unknown key")] == 400
    assert by_case[("/api/refresh", "missing token")] == 403
    assert by_case[("/api/refresh", "act without JSON")] == 403
    assert by_case[("/api/events", "refused gate: Origin")] == 403
    # /api/big caps its body at 16 bytes, and a one-key body fits.
    assert by_case[("/api/big", "unknown key")] == 400
    assert pages.CALLS == []


def test_gate_matrix_imports_no_handler_module():
    page = routes.Page(routes=(
        routes.Route("/api/a", "read", "no_such_package.handlers:a", returns={}),
        routes.Route("/api/b", "act", "no_such_package.handlers:b",
                     body={"x": routes.Field("int")}, returns={}),
    ))
    rows = api.gate_matrix(page)
    assert len(rows) == 13
    assert "no_such_package" not in sys.modules
    # The undeclared key is chosen around the declared ones.
    assert ("/api/b", "unknown key", 400, 400) in rows


def test_gate_matrix_names_a_case_the_check_lets_through(monkeypatch, pages):
    monkeypatch.setattr(server, "check", lambda view, profile: None)
    with pytest.raises(AssertionError) as caught:
        api.gate_matrix(asgi_kit.make_page())
    said = str(caught.value)
    assert "/api/refresh, missing token: reached the handler" in said
    assert "/api/summary, refused gate: Host" in said
    assert pages.CALLS == []


def test_gate_matrix_names_a_case_the_allowlist_lets_through(monkeypatch, pages):
    monkeypatch.setattr(server, "parse_query", lambda route, query: {})
    with pytest.raises(AssertionError) as caught:
        api.gate_matrix(asgi_kit.make_page())
    assert "/api/summary, unknown key: reached the handler" in str(caught.value)
    assert "/api/refresh" not in str(caught.value)


def test_gate_matrix_checks_the_400_names_the_key(monkeypatch, pages):
    monkeypatch.setattr(server, "_bad_request", lambda err: server._json(400, {"error": "bad"}))
    with pytest.raises(AssertionError, match="expected the 400 to say 'unknown key: x'"):
        api.gate_matrix(asgi_kit.make_page())


def test_gate_matrix_takes_only_a_page():
    with pytest.raises(TypeError):
        api.gate_matrix({"routes": []})

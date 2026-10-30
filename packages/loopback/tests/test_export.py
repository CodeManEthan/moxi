"""The export command: spawned as `python -P -m moxi_loopback.export`, over fake modules
installed in a site directory of the test's own."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import moxi.modules
import pytest

from moxi_loopback import export as export_command
from moxi_loopback.routes import PageError, build_table, export

ANNOUNCEMENT = {
    "INTERFACES": "frozenset({1})", "API": "None", "CLI": "None",
    "SUMMARY": "'A module made for a test.'", "OPTIONAL": "{}", "GATED": "{}",
    "INTERNAL_ENV": "{}", "SETTINGS": "None", "SETUP": "None", "SEGMENT": "None",
}

GOOD_PAGE = """
from moxi_loopback.routes import Field, Out, Page, Route

PAGE = Page(
    routes=(
        Route("/api/summary", "read", "moxi_pgood.handlers:summary",
              query={"days": Field("int", required=False, max=366)},
              returns={"total": Out("int"), "log": Out("string", mark="path")},
              gateway=True),
        Route("/api/refresh", "act", "moxi_pgood.handlers:refresh",
              body={"force": Field("bool", required=False)},
              returns={"ok": Out("bool")}),
        Route("/api/events", "read", "moxi_pgood.handlers:events", stream=True,
              topics={"today": Out("table", items={"total": Out("int")})}),
    ),
    assets="web",
    health="moxi_pgood.handlers:health",
    budget={"rss": "100M"},
)
"""

BAD_ASSETS_PAGE = """
from moxi_loopback.routes import Out, Page, Route

PAGE = Page(routes=(Route("/api/x", "read", "moxi_passets.handlers:x",
                          returns={"n": Out("int")}),), assets="web")
"""

REFUSED_PAGE = """
from moxi_loopback.routes import Page

PAGE = Page(routes=(), idle_s=1)
"""

PRINTING_PAGE = """
import os
from moxi_loopback.routes import Page

print("printed while imported")
os.write(1, b"written to fd 1 while imported\\n")
PAGE = Page(routes=())
"""


class Site:
    """A directory of fake installed modules, each a package and its distribution."""

    def __init__(self, root: Path):
        self.root = root

    def write(self, relpath: str, text: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        return path

    def module(self, name: str, *, page: str | None = None,
               page_target: str | None = None, files: dict[str, str] | None = None) -> str:
        package = f"moxi_{name}"
        fields = dict(ANNOUNCEMENT)
        if page is not None:
            self.write(f"{package}/page.py", page)
            fields["PAGE"] = repr(page_target or f"{package}.page:PAGE")
        elif page_target is not None:
            fields["PAGE"] = repr(page_target)
        self.write(f"{package}/__init__.py", "")
        self.write(f"{package}/module.py", "".join(f"{k} = {v}\n" for k, v in fields.items()))
        for relpath, text in (files or {}).items():
            self.write(f"{package}/{relpath}", text)
        info = f"moxi_{name}-0.1.0.dist-info"
        self.write(f"{info}/METADATA",
                   f"Metadata-Version: 2.1\nName: moxi-{name}\nVersion: 0.1.0\n")
        self.write(f"{info}/entry_points.txt", f"[moxi.modules]\n{name} = {package}.module\n")
        return package


@pytest.fixture
def site(tmp_path):
    return Site(tmp_path / "site")


@pytest.fixture
def marker(tmp_path):
    return tmp_path / "imported"


@pytest.fixture
def modules(site, marker):
    """The fake modules every test here may name."""
    handlers = f"""
        open({str(marker)!r}, "w").write("a handler module was imported")
        raise RuntimeError("a handler module must never be imported by the export")
    """
    site.module("pgood", page=GOOD_PAGE, files={
        "handlers.py": handlers,
        "web/index.html": "<!doctype html>",
        "web/app.js": "export {};",
    })
    site.module("pbroken", page=GOOD_PAGE.replace("moxi_pgood", "moxi_pbroken"),
                page_target="moxi_pbroken.page:MISSING")
    site.module("passets", page=BAD_ASSETS_PAGE, files={
        "web/index.html": "<!doctype html>", "web/nested/deep.js": "",
    })
    site.module("prefused", page=REFUSED_PAGE)
    site.module("pnopage")
    site.module("pprints", page=PRINTING_PAGE)
    return site


@pytest.fixture
def cwd(tmp_path):
    """A working directory only this user can write, as every spawner passes."""
    path = tmp_path / "cwd"
    path.mkdir(mode=0o700)
    return path


def run(site, cwd, *modules, safe=True):
    argv = [sys.executable, *(["-P"] if safe else []), "-m", "moxi_loopback.export", *modules]
    env = {**os.environ, "PYTHONPATH": str(site.root)}
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


@pytest.fixture
def in_process(site, monkeypatch):
    """The site on this process's import path, for the in-process reference values."""
    monkeypatch.syspath_prepend(str(site.root))
    moxi.modules.reset()
    yield lambda name: importlib.import_module(name)
    moxi.modules.reset()
    for name in list(sys.modules):
        if name.split(".")[0].startswith("moxi_p"):
            del sys.modules[name]


# --- spawned ------------------------------------------------------------------------


def test_one_json_object_per_module_and_exit_3_when_any_is_refused(modules, cwd, marker,
                                                                    in_process):
    done = run(modules, cwd, "pgood", "pbroken", "passets", "prefused", "pnopage")
    assert done.returncode == 3, done.stderr
    lines = done.stdout.splitlines()
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert list(result) == ["pgood", "pbroken", "passets", "prefused", "pnopage"]

    page = in_process("moxi_pgood.page").PAGE
    assert result["pgood"] == {"ok": True, "export": export(page)}
    good = result["pgood"]["export"]
    assert good["format"] == 1
    assert good["assets"] == ["app.js", "index.html"]
    assert good["budget"] == {"rss": "100M", "fds": 64}
    assert [r["path"] for r in good["routes"]] == ["/api/summary", "/api/refresh",
                                                   "/api/events"]
    assert "moxi_pgood.handlers" not in lines[0]
    assert "/api/health" not in lines[0] and "/api/prove" not in lines[0]

    for name in ("pbroken", "passets", "prefused", "pnopage"):
        assert result[name]["ok"] is False
        assert "\n" not in result[name]["error"] and result[name]["error"]
    assert "MISSING" in result["pbroken"]["error"]
    assert "idle_s" in result["prefused"]["error"]
    assert "PAGE" in result["pnopage"]["error"]
    assert not marker.exists(), "the export imported a handler module"


def test_the_command_refuses_the_asset_table_build_table_refuses(modules, cwd, in_process):
    page = in_process("moxi_passets.page").PAGE
    with pytest.raises(PageError) as refused:
        build_table(page)
    result = json.loads(run(modules, cwd, "passets").stdout)
    assert result["passets"] == {"ok": False, "error": str(refused.value)}


def test_exit_0_when_every_module_is_read(modules, cwd, marker):
    done = run(modules, cwd, "pgood")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["pgood"]["ok"] is True
    assert not marker.exists()


def test_exit_2_with_no_module(modules, cwd):
    done = run(modules, cwd)
    assert done.returncode == 2
    assert done.stdout == "" and "usage" in done.stderr


def test_a_module_that_prints_while_imported_keeps_stdout_one_json_line(modules, cwd):
    done = run(modules, cwd, "pprints", "pgood")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["pprints"]["ok"] is True
    assert "printed while imported" in done.stderr
    assert "written to fd 1 while imported" in done.stderr


def _plant(directory: Path, package: str, marker: Path) -> None:
    (directory / package).mkdir()
    (directory / package / "__init__.py").write_text(
        f"open({str(marker)!r}, 'a').write({package!r} + '\\n')\n")


def test_started_without_safe_path_it_exits_3_before_importing_anything(modules, cwd,
                                                                       marker):
    for package in ("moxi", "uvicorn", "starlette"):
        _plant(cwd, package, marker)
    done = run(modules, cwd, "pgood", safe=False)
    assert done.returncode == 3
    assert "python -P" in done.stderr
    assert done.stdout == ""
    assert not marker.exists(), marker.read_text()


def test_a_planted_moxi_loopback_in_the_working_directory_is_never_imported(modules, cwd,
                                                                           marker):
    _plant(cwd, "moxi_loopback", marker)
    (cwd / "moxi_loopback" / "export.py").write_text(
        f"open({str(marker)!r}, 'a').write('export\\n')\n")
    done = run(modules, cwd, "pgood")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["pgood"]["ok"] is True
    assert not marker.exists()


# --- in process ---------------------------------------------------------------------


def test_load_page_gives_the_page_and_its_package(modules, in_process):
    page = export_command.load_page("pgood")
    assert page is in_process("moxi_pgood.page").PAGE
    assert page.package == "moxi_pgood"


@pytest.mark.parametrize("name, words", [
    ("pbroken", "has no MISSING"),
    ("pnopage", "declares no PAGE"),
    ("pmissing", "absent"),
])
def test_load_page_refuses_in_one_line(modules, in_process, name, words):
    with pytest.raises(export_command.PageLoadError) as refused:
        export_command.load_page(name)
    assert words in str(refused.value)


def test_load_page_raises_a_refused_declaration_as_it_is(modules, in_process):
    with pytest.raises(PageError):
        export_command.load_page("prefused")


def test_load_page_refuses_a_target_that_is_not_a_page(site, in_process):
    site.module("pother", page="PAGE = {'routes': []}\n")
    with pytest.raises(export_command.PageLoadError, match="not a Page"):
        export_command.load_page("pother")


def test_a_target_whose_module_fails_to_import_is_one_line(site, in_process):
    site.module("pfails", page="raise ImportError('no such thing\\non two lines')\n")
    result = export_command._one("pfails")
    assert result["ok"] is False
    assert "ImportError" in result["error"] and "\n" not in result["error"]


@pytest.mark.parametrize("argv", [[], ["--help"], ["pgood", "-x"]])
def test_main_without_modules_is_usage(capsys, argv):
    assert export_command.main(argv) == 2
    assert capsys.readouterr().out == ""

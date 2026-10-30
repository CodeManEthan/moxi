"""Strays: module distributions outside the environment moxi runs in, told once."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

import moxi.cli as cli
import moxi.strays as strays

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="the fixtures lay out POSIX environments")

PYVER = f"python{sys.version_info.major}.{sys.version_info.minor}"


def dist_info(site: Path, dist: str) -> Path:
    """`<dist>.dist-info` in `site`; a `moxi_<m>` one declares its module's entry point."""
    info = site / f"{dist}.dist-info"
    info.mkdir(parents=True)
    if dist.startswith("moxi_"):
        module = dist.split("-")[0].removeprefix("moxi_")
        (info / "entry_points.txt").write_text(
            f"[moxi.modules]\n{module} = moxi_{module}.module\n")
    return info


def env_with(root: Path, *dists: str) -> Path:
    """A fake environment at `root` whose site-packages holds each `moxi_<m>-<v>`."""
    site = root / "lib" / PYVER / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    for dist in dists:
        dist_info(site, dist)
    return site


@pytest.fixture
def box(tmp_path, monkeypatch):
    """Every place the scan reads, under tmp: uv's tool directory, the user site and a
    PATH holding nothing but what a test puts there."""
    tools = tmp_path / "tools"
    tools.mkdir()
    user = tmp_path / "user-site"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    environ = {"UV_TOOL_DIR": str(tools), "PATH": str(bindir),
               "MOXI_HOME": str(tmp_path / "home")}
    monkeypatch.setattr(strays, "_user_site", lambda: str(user))
    monkeypatch.setattr(strays, "_own_script", lambda: None)

    class Box:
        pass

    b = Box()
    b.tools, b.user, b.bin, b.env, b.root = tools, user, bindir, environ, tmp_path
    return b


def test_nothing_outside_is_no_stray(box):
    assert strays.scan(box.env) == []


def test_a_uv_tool_holding_a_module_is_a_stray(box):
    env_with(box.tools / "moxi-cache", "moxi_cache-0.1.0", "psutil-7.0.0")
    env_with(box.tools / "ruff")
    found = strays.scan(box.env)
    assert [(s.module, s.version, s.kind, s.tool) for s in found] == [
        ("cache", "0.1.0", "tool", "moxi-cache")]
    assert found[0].line() == (
        'moxi: moxi-cache is installed outside this moxi (uv tool "moxi-cache"), where '
        "moxi can't see it. Install it here: moxi install cache. Then remove the other: "
        "uv tool uninstall moxi-cache")


def test_this_environment_s_own_tool_is_not_a_stray(box, monkeypatch):
    own = box.tools / "moxi"
    env_with(own, "moxi_comms-0.1.0")
    monkeypatch.setattr(sys, "prefix", str(own))
    assert strays.scan(box.env) == []


def test_the_user_site_holding_a_module_is_a_stray(box):
    box.user.mkdir()
    dist_info(box.user, "moxi_turns-0.2.0")
    (found,) = strays.scan(box.env)
    assert (found.module, found.kind, found.where) == ("turns", "user", str(box.user))
    assert "(the user site " in found.line()
    assert found.line().endswith("python -m pip uninstall moxi-turns")


def test_another_moxi_on_path_brings_its_environment_s_modules(box):
    other = box.root / "venvs" / "old"
    env_with(other, "moxi_comms-0.1.0", "moxi_turns-0.1.0")
    (other / "bin").mkdir()
    script = other / "bin" / "moxi"
    script.write_text("#!/bin/sh\n")
    (box.bin / "moxi").symlink_to(script)
    found = strays.scan(box.env)
    assert [(s.module, s.kind, s.where) for s in found] == [
        ("comms", "path", str(other)), ("turns", "path", str(other))]
    assert found[0].remove_command() == f"{other}/bin/python -m pip uninstall moxi-comms"


def test_this_environment_s_moxi_on_path_is_not_a_stray(box, monkeypatch):
    other = box.root / "venvs" / "mine"
    env_with(other, "moxi_comms-0.1.0")
    (other / "bin").mkdir()
    script = other / "bin" / "moxi"
    script.write_text("#!/bin/sh\n")
    (box.bin / "moxi").symlink_to(script)   # uv's link to this environment's script
    monkeypatch.setattr(strays, "_own_script", lambda: str(script))
    assert strays.scan(box.env) == []


def test_a_moxi_on_path_inside_a_uv_tool_is_found_once(box):
    tool = box.tools / "moxi-comms"
    env_with(tool, "moxi_comms-0.1.0")
    (tool / "uv-receipt.toml").write_text("[tool]\n")
    (tool / "bin").mkdir()
    (tool / "bin" / "moxi").write_text("#!/bin/sh\n")
    (box.bin / "moxi").symlink_to(tool / "bin" / "moxi")
    found = strays.scan(box.env)
    assert [(s.module, s.kind, s.tool) for s in found] == [("comms", "tool", "moxi-comms")]


def test_the_default_tool_directory_follows_uv(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    assert strays.tool_dir({"UV_TOOL_DIR": "/t"}) == "/t"
    assert strays.tool_dir({"XDG_DATA_HOME": "/x", "HOME": "/h"}) == "/x/uv/tools"
    assert strays.tool_dir({"HOME": "/h"}) == "/h/.local/share/uv/tools"
    tool = tmp_path / "tools" / "moxi"
    tool.mkdir(parents=True)
    (tool / "uv-receipt.toml").write_text("")
    monkeypatch.setattr(sys, "prefix", str(tool))
    assert strays.tool_dir({"HOME": "/h"}) == str(tmp_path / "tools")


def test_the_windows_tool_directory_is_under_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(strays, "_windows", lambda: True)
    assert strays.tool_dir({"APPDATA": "C:/Users/a/AppData/Roaming"}) == os.path.join(
        "C:/Users/a/AppData/Roaming", "uv", "data", "tools")


# -- the cache ---------------------------------------------------------------------------

def test_an_unchanged_key_uses_the_cached_answer(box, monkeypatch):
    env_with(box.tools / "moxi-cache", "moxi_cache-0.1.0")
    first = strays.find(box.env)
    record = json.loads(strays.record_path(box.env).read_text())
    assert record["key"] == strays.cache_key(box.env)
    assert record["strays"] == [s.as_json() for s in first]

    monkeypatch.setattr(strays, "scan", lambda environ=None: pytest.fail("scanned again"))
    assert strays.find(box.env) == first


def test_a_new_tool_changes_the_key(box):
    assert strays.find(box.env) == []
    env_with(box.tools / "moxi-cache", "moxi_cache-0.1.0")
    os.utime(box.tools, ns=(1, 1))   # a different mtime, however coarse the clock
    assert [s.module for s in strays.find(box.env)] == ["cache"]


def test_a_changed_path_changes_the_key(box):
    assert strays.cache_key(box.env) != strays.cache_key({**box.env, "PATH": "/elsewhere"})


# -- told once -------------------------------------------------------------------------

def test_each_stray_is_told_once(box):
    env_with(box.tools / "moxi-cache", "moxi_cache-0.1.0")
    out = io.StringIO()
    told = strays.tell(out, box.env)
    assert [s.module for s in told] == ["cache"]
    assert out.getvalue().count("\n") == 1
    out = io.StringIO()
    assert strays.tell(out, box.env) == [] and out.getvalue() == ""


def test_a_stray_that_goes_and_comes_back_is_told_again(box):
    tool = env_with(box.tools / "moxi-cache", "moxi_cache-0.1.0")
    strays.tell(io.StringIO(), box.env)
    shutil.rmtree(tool / "moxi_cache-0.1.0.dist-info")
    os.utime(box.tools, ns=(2, 2))
    strays.tell(io.StringIO(), box.env)
    dist_info(tool, "moxi_cache-0.1.0")
    os.utime(box.tools, ns=(3, 3))
    assert [s.module for s in strays.tell(io.StringIO(), box.env)] == ["cache"]



def test_each_environment_tells_a_stray_once(box, monkeypatch, tmp_path):
    """Runs that alternate between two environments, each seeing other strays, don't
    wipe each other's record."""
    venv, tool = tmp_path / "venv", box.tools / "moxi"
    env_with(venv)
    env_with(tool, "moxi_cache-0.1.0")
    (tool / "uv-receipt.toml").write_text("[tool]\n")
    told = []
    for prefix in (venv, tool, venv, tool, venv):
        monkeypatch.setattr(sys, "prefix", str(prefix))
        out = io.StringIO()
        strays.tell(out, box.env)
        told.append(out.getvalue().count("\n"))
    # From the venv, the tool's cache is a stray; from the tool, nothing is.
    assert told == [1, 0, 0, 0, 0]


def test_a_moxi_distribution_that_is_no_module_is_no_stray(box):
    site = env_with(box.tools / "moxi-dev")
    info = site / "moxi_dev-0.1.0.dist-info"
    info.mkdir()
    (info / "entry_points.txt").write_text("[console_scripts]\nmoxi-dev = moxi_dev:main\n")
    (site / "moxi_bare-0.1.0.dist-info").mkdir()
    assert strays.scan(box.env) == []

# -- the commands ------------------------------------------------------------------------

@pytest.fixture
def console(box, monkeypatch):
    """`moxi` as the console script runs it, with the box's places."""
    for key, value in box.env.items():
        monkeypatch.setenv(key, value)

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["moxi", *argv])
        return cli.main()

    return run


def test_a_command_tells_a_new_stray_once(console, box, site, capsys):
    env_with(box.tools / "moxi-cache", "moxi_cache-0.1.0")
    assert console("modules") == 0
    assert capsys.readouterr().err.count("moxi-cache is installed outside this moxi") == 1
    assert console("modules") == 0
    assert "outside this moxi" not in capsys.readouterr().err


@pytest.mark.parametrize("argv", [("help",), ("version",), ("--help",), ("-h",),
                                  ("run", "x")])
def test_help_version_and_run_do_not_check(console, box, site, capsys, argv):
    env_with(box.tools / "moxi-cache", "moxi_cache-0.1.0")
    console(*argv)
    assert "outside this moxi" not in capsys.readouterr().err
    assert not strays.record_path(box.env).exists()


def test_an_in_process_caller_runs_the_command_alone(box, monkeypatch, site, capsys):
    for key, value in box.env.items():
        monkeypatch.setenv(key, value)
    env_with(box.tools / "moxi-cache", "moxi_cache-0.1.0")
    assert cli.main(["modules"]) == 0
    assert "outside this moxi" not in capsys.readouterr().err


def test_a_broken_check_never_stops_the_command(console, monkeypatch, site, capsys):
    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(strays, "tell", boom)
    assert console("modules") == 0


def test_an_absent_module_with_a_stray_copy_says_where_it_is(box, monkeypatch, site,
                                                             capsys):
    for key, value in box.env.items():
        monkeypatch.setenv(key, value)
    env_with(box.tools / "moxi-cache", "moxi_cache-0.1.0")
    assert cli.main(["cache", "status"]) == 2
    err = capsys.readouterr().err
    assert (f"a copy is installed at {box.tools / 'moxi-cache'}; `moxi install cache` "
            f"puts it where moxi sees it") in err
    assert not strays.record_path(box.env).exists()   # the message writes nothing

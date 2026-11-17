"""The install listener: what a render loads is compiled at every module's install, and
the doctor's bytecode row names what isn't.

Bytecode goes under a prefix in the test's tmp (`sys.pycache_prefix`), so compiling the
core writes nothing beside its sources."""

import sys

import pytest
from statusline_kit import FROM_INPUT

from moxi_statusline import compile as listener
from moxi_statusline.doctor import bytecode_row
from moxi_statusline.render import RENDER_IMPORTS


@pytest.fixture
def prefix(tmp_path, monkeypatch):
    path = tmp_path / "pycache"
    monkeypatch.setattr(sys, "pycache_prefix", str(path))
    return path


@pytest.fixture
def alpha(seen):
    seen.module("alpha", """
        from . import helper
        from .sub import deep
        import json
        import moxi_beta.thing
        from moxi_alpha import other
    """, helper="X = 1\n", other="from .sub import also\n", unused="Y = 2\n")
    seen.write("moxi_alpha/sub/__init__.py", "")
    seen.write("moxi_alpha/sub/deep.py", "")
    seen.write("moxi_alpha/sub/also.py", "")
    seen.write("moxi_alpha/sub/never.py", "")
    seen.module("beta", None, thing="")
    return seen


def rel(paths, root):
    return sorted(str(p).removeprefix(str(root) + "/") for p in paths)


def test_the_closure_inside_the_segments_package(alpha):
    assert rel(listener.closure("moxi_alpha.segment"), alpha.root) == [
        "moxi_alpha/__init__.py", "moxi_alpha/helper.py", "moxi_alpha/other.py",
        "moxi_alpha/segment.py", "moxi_alpha/sub/__init__.py", "moxi_alpha/sub/also.py",
        "moxi_alpha/sub/deep.py"]


def test_segment_sources_add_the_announcement(alpha):
    got = rel(listener.segment_sources("alpha"), alpha.root)
    assert "moxi_alpha/module.py" in got
    assert "moxi_alpha/segment.py" in got
    assert listener.segment_sources("beta") == []
    assert listener.segment_sources("nosuchmodule") == []


def test_current_follows_the_source(tmp_path, prefix):
    import compileall

    source = tmp_path / "plain.py"
    source.write_text("A = 1\n")
    assert not listener.current(str(source))
    compileall.compile_file(str(source), quiet=2)
    assert listener.current(str(source))
    source.write_text("A = 12\n")
    assert not listener.current(str(source))


def test_install_compiles_the_core_this_package_and_each_segment(alpha, prefix):
    said = []
    listener.on_install("alpha", None, say=said.append)
    assert said == []
    for name, path in listener.render_sources(RENDER_IMPORTS).items():
        assert listener.current(path), name
    for path in listener.segment_sources("alpha"):
        assert listener.current(path), path
    assert listener.current(str(alpha.root / "moxi_alpha/sub/deep.py"))
    assert not listener.current(str(alpha.root / "moxi_alpha/unused.py"))
    assert set(listener.present_segments()) >= {"alpha"}
    assert "beta" not in listener.present_segments()


def test_uninstall_has_nothing_to_do():
    assert listener.on_uninstall("alpha", say=pytest.fail) is None


def test_the_doctor_row_names_a_stale_core_module(alpha, prefix):
    listener.compile_all()
    assert bytecode_row(["alpha"]).state == "ok"
    import importlib.util

    config_py = listener.render_sources(["moxi.config"])["moxi.config"]
    pyc = importlib.util.cache_from_source(config_py)
    with open(pyc, "r+b") as fh:
        fh.write(b"\0\0\0\0")
    row = bytecode_row(["alpha"])
    assert row.state == "warn"
    assert "moxi.config" in row.detail
    assert row.fix == "moxi install statusline"


def test_the_doctor_row_names_a_stale_segment_file(alpha, prefix):
    listener.compile_all()
    (alpha.root / "moxi_alpha/helper.py").write_text("X = 100\n")
    row = bytecode_row(["alpha"])
    assert row.state == "warn"
    assert "moxi_alpha/helper.py" in row.detail
    assert bytecode_row([]).state == "ok"


def test_a_segment_module_with_no_imports_is_compiled_too(seen, prefix):
    seen.module("gamma", FROM_INPUT)
    listener.compile_all()
    assert all(listener.current(p) for p in listener.segment_sources("gamma"))

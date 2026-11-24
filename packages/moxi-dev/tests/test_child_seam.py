"""The seam every child the tests start passes through: what it adds to a child's
environment, what it refuses, the byte-code prefix an isolated Python child gets, and the
audit hook's check on `os.exec*`, `os.spawn*` and `os.posix_spawn`."""

from __future__ import annotations

import json
import ntpath
import os
import subprocess
import sys
from pathlib import Path

import pytest

from moxi import _mark

from moxi_dev import testing
from moxi_dev.testing import FenceError

PY = sys.executable


def refused(action) -> str:
    """Run `action`, which the fence must refuse; take the refusal off the record, so
    this test can go on to pass, and return its message."""
    start = len(testing._GUARD.breaches)
    with pytest.raises(FenceError) as caught:
        action()
    del testing._GUARD.breaches[start:]
    return str(caught.value)


@pytest.fixture
def outside(tmp_path_factory) -> str:
    """A folder that exists outside the run's roots: the one that holds the run's (or
    under xdist the worker's) temp root."""
    where = str(tmp_path_factory.getbasetemp().parent)
    assert not testing._in_roots(where)
    return where


def _system_env() -> dict[str, str]:
    """An environment built from nothing: on Windows, the system's own variables a
    process needs to start at all."""
    if not testing.WINDOWS:
        return {}
    return {key: os.environ[key] for key in testing.WINDOWS_KEPT_ENV if key in os.environ}


# --- what the seam adds ----------------------------------------------------------------

def test_an_empty_env_gets_every_child_key_a_temp_dir_and_a_home():
    env = testing._child_env({})
    for key, value in testing._RUN.child.items():
        assert env[key] == value, key
    assert env["TMPDIR"] == os.environ["TMPDIR"]
    assert env["HOME"] == os.environ["HOME"]
    if testing.WINDOWS:
        for key in ("TEMP", "TMP", *testing.WINDOWS_HOMES):
            assert env[key] == os.environ[key], key


def test_what_an_env_sets_is_kept(tmp_path):
    own = {"HOME": str(tmp_path / "h"), "TMPDIR": str(tmp_path / "t"),
           "UV_CACHE_DIR": str(tmp_path / "cache"), "UV_PYTHON": "3.12",
           "PYTHONDONTWRITEBYTECODE": "1", "OTHER": "x"}
    env = testing._child_env(own)
    assert {key: env[key] for key in own} == own


def test_an_empty_uv_value_is_filled_and_an_empty_byte_code_one_is_judged(tmp_path):
    env = testing._child_env({"UV_CACHE_DIR": "", "PYTHONDONTWRITEBYTECODE": "1"})
    assert env["UV_CACHE_DIR"] == testing._RUN.child["UV_CACHE_DIR"]
    assert "byte code on" in refused(
        lambda: testing._child_env({"PYTHONDONTWRITEBYTECODE": ""}))


def test_on_windows_a_missing_profile_is_laid_out_under_the_env_s_own_home(monkeypatch):
    monkeypatch.setattr(testing, "WINDOWS", True)
    for own_key in ("USERPROFILE", "HOME"):
        own = "C:\\Users\\probe-that-is-not-there"
        env = testing._child_env({own_key: own})
        for key, rel in testing.WINDOWS_HOMES.items():
            want = own if rel == "." else ntpath.join(own, *rel.split("/"))
            assert env[key] == want, (own_key, key)


def test_a_child_given_an_env_starts_with_the_keys_added():
    done = subprocess.run([PY, "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
                          env=_system_env(), capture_output=True, text=True,
                          timeout=testing.PROC_WAIT_S, check=True)
    seen = json.loads(done.stdout)
    for key, value in testing._RUN.child.items():
        assert seen[key] == value, key


# --- what the seam refuses -------------------------------------------------------------

@pytest.mark.parametrize("key", testing.UV_DIRS_ENV)
def test_a_uv_folder_outside_the_roots_is_refused_existing_or_not(key, outside):
    for where in (outside, os.path.join(outside, "nothing-here")):
        assert "outside the run's roots" in refused(lambda: testing._child_env({key: where}))


def test_byte_code_on_needs_a_prefix_under_the_roots(tmp_path, outside):
    on = {"PYTHONDONTWRITEBYTECODE": ""}
    refused(lambda: testing._child_env(on))
    refused(lambda: testing._child_env({**on, "PYTHONPYCACHEPREFIX": outside}))
    env = testing._child_env({**on, "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache")})
    assert env["PYTHONDONTWRITEBYTECODE"] == ""


@pytest.mark.parametrize("key", ["HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "TMPDIR",
                                 "TEMP", "MOXI_HOME", "MOXI_COMMS_HOME", "CODEX_HOME"])
def test_a_moved_variable_set_to_a_folder_outside_is_refused(key, outside):
    assert "outside the run's roots" in refused(lambda: testing._child_env({key: outside}))


def test_a_placeholder_that_names_nothing_passes(outside):
    nowhere = os.path.join(outside, "nowhere-at-all")
    env = testing._child_env({"HOME": nowhere, "MOXI_HOME": nowhere})
    assert env["HOME"] == env["MOXI_HOME"] == nowhere


def test_the_opt_in_tiers_may_name_the_account_s_folders_but_not_uv_s(outside):
    def lax_then_back(attr, value):
        holder = testing._RUN if attr == "real_supervisor" else testing._GUARD
        saved = getattr(holder, attr)
        setattr(holder, attr, value)
        try:
            assert testing._child_env({"HOME": outside})["HOME"] == outside
            refused(lambda: testing._child_env({"UV_CACHE_DIR": outside}))
            refused(lambda: testing._child_env({"PYTHONDONTWRITEBYTECODE": ""}))
        finally:
            setattr(holder, attr, saved)

    lax_then_back("network", False)          # a live test
    lax_then_back("real_supervisor", True)   # a run with the real supervisor
    refused(lambda: testing._child_env({"HOME": outside}))


def test_a_refused_child_never_starts(tmp_path, outside):
    marker = tmp_path / "started"
    code = f"open({str(marker)!r}, 'w').close()"
    refused(lambda: subprocess.run([PY, "-c", code], env={"UV_CACHE_DIR": outside},
                                   timeout=testing.PROC_WAIT_S))
    assert not marker.exists()


# --- an isolated Python child's byte-code prefix ---------------------------------------

@pytest.mark.parametrize("rest,isolated", [
    (["-I", "-c", "x"], True),
    (["-E", "-m", "mod"], True),
    (["-sI", "script.py"], True),
    (["-X", "utf8", "-I", "-c", "x"], True),
    (["-Wignore", "-I", "-c", "x"], True),
    (["-W", "ignore", "-I", "-c", "x"], True),
    (["-c", "x"], False),
    (["-c", "x", "-I"], False),                 # the program's own argument
    (["-m", "mod", "-I"], False),
    (["script.py", "-I"], False),
    (["-IB", "-c", "x"], False),                # writes no byte code
    (["-I", "-B", "-c", "x"], False),
], ids=testing.param_id)
def test_an_isolated_python_child_gets_the_run_s_prefix(rest, isolated):
    want = [PY, "-X", f"pycache_prefix={testing._RUN.pycache}", *rest] if isolated else None
    assert testing._with_prefix([PY, *rest]) == want


def test_only_a_python_first_gets_a_prefix():
    assert testing._with_prefix(["uv", "run", "python", "-I", "-c", "x"]) is None
    assert testing._with_prefix([]) is None


def test_the_prefix_goes_by_the_program_s_name_and_keeps_its_type():
    named = os.path.join(os.path.dirname(PY), "python3.99")
    assert testing._with_prefix([named, "-I", "-c", "x"])[:3] == [
        named, "-X", f"pycache_prefix={testing._RUN.pycache}"]
    path = Path(PY)
    assert testing._with_prefix([path, "-I", "-c", "x"])[0] is path
    assert testing._with_prefix(f"{PY} -I -c x") is None      # a shell string


def test_a_prefix_of_its_own_is_kept_under_the_roots_and_refused_outside(tmp_path,
                                                                         outside):
    mine = str(tmp_path / "pycache")
    assert testing._with_prefix([PY, "-I", "-X", f"pycache_prefix={mine}", "-c", "x"]) is None
    assert testing._with_prefix([PY, "-I", f"-Xpycache_prefix={mine}", "-c", "x"]) is None
    assert "outside the run's roots" in refused(
        lambda: testing._with_prefix([PY, "-I", "-X", f"pycache_prefix={outside}"]))
    assert "outside the run's roots" in refused(
        lambda: testing._with_prefix([PY, "-I", f"-Xpycache_prefix={outside}"]))


def test_an_isolated_child_writes_its_byte_code_under_the_run():
    code = "import json, sys; print(json.dumps([sys.pycache_prefix, sys.orig_argv]))"
    for env in (None, _system_env()):
        done = subprocess.run([PY, "-I", "-c", code], env=env, capture_output=True,
                              text=True, timeout=testing.PROC_WAIT_S, check=True)
        prefix, orig = json.loads(done.stdout)
        assert prefix == testing._RUN.pycache
        assert testing._in_roots(prefix)
        assert orig[1:3] == ["-X", f"pycache_prefix={testing._RUN.pycache}"]


@pytest.mark.parametrize("module", ["moxi_comms.adapters.respawn", "moxi_secrets.fallback"])
def test_the_core_s_mark_names_a_prefixed_child_as_it_named_it(monkeypatch, module):
    argv = testing._with_prefix([PY, "-I", "-m", module])
    assert argv is not None
    monkeypatch.setattr(sys, "orig_argv", argv)
    monkeypatch.setattr(sys, "argv", [f"/lib/{module.replace('.', '/')}.py"])
    assert _mark._program() == f"python -m {module}"


# --- os.exec, os.spawn and os.posix_spawn ----------------------------------------------

def _start(env: dict[str, str]) -> int:
    """Start a Python child that exits 0 by the platform's raw start, with `env`, and
    return its exit code."""
    argv = [PY, "-c", "pass"]
    if hasattr(os, "posix_spawn"):
        pid = os.posix_spawn(PY, argv, env)
        return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
    return os.spawnve(os.P_WAIT, PY, argv, env)


def test_a_raw_start_needs_every_child_key_and_is_judged_as_the_seam_judges(outside):
    full = dict(os.environ)
    assert _start(full) == 0
    lacking = {key: value for key, value in full.items() if key != "UV_CACHE_DIR"}
    assert "lacks UV_CACHE_DIR" in refused(lambda: _start(lacking))
    assert "outside the run's roots" in refused(lambda: _start({**full, "HOME": outside}))
    assert "byte code on" in refused(
        lambda: _start({**full, "PYTHONDONTWRITEBYTECODE": ""}))

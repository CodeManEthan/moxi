"""The guard in this folder's conftest.py: a run without the test kit's fence fails every
test at setup, and the config fixtures refuse a path outside the test's own tmp."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent

PROBE = '''
def test_writes_the_config(write_config):
    write_config("x = 1\\n")


def test_needs_nothing():
    pass
'''


def test_a_run_without_the_fence_fails_every_test_at_setup(tmp_path):
    copy = tmp_path / "copy"
    copy.mkdir()
    shutil.copy(HERE / "conftest.py", copy / "conftest.py")
    (copy / "test_probe.py").write_text(PROBE, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rE", str(copy)],
        cwd=copy, capture_output=True, text=True, timeout=120, check=False)
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert "2 errors" in out, out
    assert "fixture 'fence' not found" in out, out
    assert "passed" not in out, out


@pytest.mark.parametrize("fixture", ["config_file", "write_config"])
def test_a_config_path_outside_the_tests_tmp_fails_the_test(fixture, monkeypatch, request,
                                                            tmp_path):
    outside = tmp_path.parent.parent / "elsewhere"
    monkeypatch.setenv("MOXI_ROUNDS_HOME", str(outside))
    with pytest.raises(pytest.fail.Exception, match="outside the test's own tmp"):
        request.getfixturevalue(fixture)
    assert not outside.exists()


def test_the_config_file_is_the_tests_own(write_config, config_file, fence):
    assert write_config("x = 1\n") == config_file
    assert config_file.resolve().is_relative_to(fence.short.resolve())

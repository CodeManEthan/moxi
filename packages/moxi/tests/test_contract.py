"""The module contract check over the core's own package.

It runs with the package's suite, so `moxi-dev check` and CI hold every change to it, over
the working tree and, staged, over git's index.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from moxi_dev.contract import check

ROOT = Path(__file__).resolve().parents[1]


def _in_git() -> bool:
    if shutil.which("git") is None:
        return False
    done = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
                          capture_output=True, text=True, check=False)
    return done.returncode == 0 and done.stdout.strip() == "true"


def test_the_core_keeps_the_contract():
    assert [str(finding) for finding in check(ROOT)] == []


@pytest.mark.skipif(not _in_git(), reason="not a git checkout, so there is no index")
def test_the_core_keeps_the_contract_as_staged():
    assert [str(finding) for finding in check(ROOT, staged=True)] == []

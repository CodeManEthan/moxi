"""The module contract check over this package, as `moxi check` runs it.

It runs with the package's suite, so `moxi-dev check` and CI hold every change to it.
"""

from pathlib import Path

from moxi.contract import check

ROOT = Path(__file__).resolve().parents[1]


def test_the_repo_keeps_the_module_contract():
    assert [str(finding) for finding in check(ROOT)] == []

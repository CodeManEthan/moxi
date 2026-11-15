"""The module contract check over this package: imports, dependencies, extras, names."""

from pathlib import Path

import pytest

# The release test installs the package's wheel and its own test tools only; moxi-dev,
# a workspace member, isn't there, and this check belongs to the workspace's run.
check = pytest.importorskip("moxi_dev.contract").check

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_keeps_the_module_contract():
    assert [str(finding) for finding in check(ROOT)] == []

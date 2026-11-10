"""The module contract check over this package: imports, dependencies, extras, names."""

from pathlib import Path

from moxi_dev.contract import check

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_keeps_the_module_contract():
    assert [str(finding) for finding in check(ROOT)] == []

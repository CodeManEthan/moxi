"""The module contract check over moxi-dev itself.

moxi-dev is the contributors' package, not a module, so the check holds it to the rules
every package keeps and passes by the module's own (its imports of other modules and its
names).
"""

from pathlib import Path

from moxi_dev.contract import check

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_keeps_the_contract():
    assert [str(finding) for finding in check(ROOT)] == []

"""The module contract check, where a package's tests import it from.

    from moxi_dev.contract import check

Today this re-exports three names of `moxi.contract` unchanged, the same objects:
`check`, the `Finding` it returns and the `CheckError` it raises. A package's
`tests/test_contract.py` imports them from here, so it keeps its import when the check's
code moves behind this module. Nothing else of `moxi.contract` is promised here.
"""

from __future__ import annotations

from moxi.contract import CheckError, Finding, check

__all__ = ["CheckError", "Finding", "check"]

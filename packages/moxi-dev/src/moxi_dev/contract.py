"""The module contract check, where a package's tests import it from.

    from moxi_dev.contract import check

Today this re-exports `moxi.contract` unchanged: the same `check`, `Finding`, `CheckError`
and constants, the same objects. A package's `tests/test_contract.py` imports them from
here, so it keeps its import when the check's code moves behind this module.
"""

from __future__ import annotations

import moxi.contract
from moxi.contract import *  # noqa: F403 - the whole public surface, re-exported
from moxi.contract import (ANNOUNCEMENT, CORE, CORE_MODULES, CORE_SCRIPT,  # noqa: F401
                           GROUP, CheckError, Finding, check, main)

__all__ = list(moxi.contract.__all__)

"""`moxi-dev check`: the checks a change must pass before it merges."""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    print("moxi-dev check: not built yet", file=sys.stderr)
    return 2

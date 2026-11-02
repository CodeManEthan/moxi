"""`python -m moxi_loadout`: the same as `moxi loadout`."""

import sys

from moxi_loadout.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], prog="moxi loadout"))

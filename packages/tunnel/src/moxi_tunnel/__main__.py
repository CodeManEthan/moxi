"""`python -m moxi_tunnel`: the same as `moxi tunnel`."""

import sys

from moxi_tunnel.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], prog="moxi tunnel"))

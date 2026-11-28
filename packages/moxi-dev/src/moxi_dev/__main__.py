"""`python -m moxi_dev <verb> ...`: the `moxi-dev` command, as the memory scope's inner
starts it (`moxi_dev.scope`)."""

import sys

from moxi_dev.cli import main

sys.exit(main())

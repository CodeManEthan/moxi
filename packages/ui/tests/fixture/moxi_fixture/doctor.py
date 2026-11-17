"""The fixture's doctor check: one row, `check`, which fails while the file `fail-check`
exists in the fixture's state directory and passes otherwise."""

from moxi.doctor import Row
from moxi.paths import Layout

#: What the failing row says, and the command it gives as its fix.
DETAIL = "The fixture's check was told to fail"
FIX = "moxi fixture mend"


def rows():
    if (Layout("fixture").state_dir() / "fail-check").exists():
        yield Row("fixture", "check", "fail", DETAIL, FIX)
    else:
        yield Row("fixture", "check", "ok", "The fixture's check passes")

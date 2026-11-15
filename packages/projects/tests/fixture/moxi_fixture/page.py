"""The fixture's page server: one read, `/api/facts`, and its components' scripts in `web/`.

The read answers the report a test wrote to `report.json` in the fixture's state
directory, as JSON text (`null` while there is none). A test steers it with files there:

- `fail`: while it exists, the read is refused;
- `reads`: one line per read the server answered or refused, so a test can see whether
  the component still reads.
"""

import os

from moxi import modules
from moxi.paths import Layout

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"the fixture's page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback
Out, Page, Refused, Route = lb.Out, lb.Page, lb.Refused, lb.Route

STATE = Layout("fixture").state_dir()


def facts(call):
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "reads", "a", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()}\n")
    if (STATE / "fail").exists():
        raise Refused("the fixture's read was told to fail")
    try:
        report = (STATE / "report.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        report = "null"
    return {"report": report}


PAGE = Page(
    routes=(Route("/api/facts", "read", "moxi_fixture.page:facts",
                  returns={"report": Out("string")}),),
    assets="web",
)

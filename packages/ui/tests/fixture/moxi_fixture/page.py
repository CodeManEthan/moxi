"""The fixture's page: a read, an act and a stream of two topics, and its components'
scripts in `web/`."""

from moxi_loopback.api import Field, Out, Page, Route

H = "moxi_fixture.handlers"

PAGE = Page(
    routes=(
        Route("/api/summary", "read", H + ":summary",
              returns={"count": Out("int"), "note": Out("string"), "pid": Out("int")}),
        Route("/api/add", "act", H + ":add",
              body={"by": Field("int", required=False),
                    "note": Field("string", required=False)},
              returns={"count": Out("int")}),
        Route("/api/events", "read", H + ":events", stream=True,
              topics={"count": Out("table", items={"n": Out("int")}),
                      "note": Out("table", items={"text": Out("string")})}),
    ),
    assets="web",
    health=H + ":health",
)

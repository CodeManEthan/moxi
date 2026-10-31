"""The fixture's page: two reads (one that waits while a test holds it), an act, a
confirmed act and its status route, a stream of two topics, and its components' scripts
in `web/`."""

from moxi_loopback.api import Field, Out, Page, Route

H = "moxi_fixture.handlers"

PAGE = Page(
    routes=(
        Route("/api/summary", "read", H + ":summary",
              returns={"count": Out("int"), "note": Out("string"), "pid": Out("int")}),
        Route("/api/wait", "read", H + ":wait", returns={"pid": Out("int")}),
        Route("/api/add", "act", H + ":add",
              body={"by": Field("int", required=False),
                    "note": Field("string", required=False)},
              returns={"count": Out("int")}),
        Route("/api/job", "read", H + ":job", query={"job": Field("string", max=64)},
              returns={"job": Out("string"), "state": Out("string")}),
        Route("/api/confirm", "act", H + ":confirm",
              body={"by": Field("int", required=False),
                    "mode": Field("string", required=False),
                    "pad": Field("string", required=False)},
              returns={}, confirmed=True, status="/api/job"),
        Route("/api/events", "read", H + ":events", stream=True,
              topics={"count": Out("table", items={"n": Out("int")}),
                      "note": Out("table", items={"text": Out("string")})}),
    ),
    assets="web",
    health=H + ":health",
)

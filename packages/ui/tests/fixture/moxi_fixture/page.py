"""The fixture's page: two reads (one that waits while a test holds it), an act, a
confirmed act and its status route, a stream of three topics, and its components' scripts
in `web/`. The gateway reaches a read with a `redact` and a `path` value, the stream (whose
`phone` topic carries both marks), and a confirmed act with its status route; everything
else is the desk's."""

from moxi import modules

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"the fixture's page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback
Field, Out, Page, Route = lb.Field, lb.Out, lb.Page, lb.Route

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
        Route("/api/phone", "read", H + ":phone", gateway=True,
              returns={"count": Out("int"), "secret": Out("string", mark="redact"),
                       "file": Out("string", mark="path"),
                       "rows": Out("list", items=Out("table", items={
                           "name": Out("string"), "where": Out("string", mark="path"),
                           "token": Out("string", mark="redact")}))}),
        Route("/api/phone-job", "read", H + ":phone_job", gateway=True,
              query={"job": Field("string", max=64)},
              returns={"job": Out("string"), "state": Out("string")}),
        Route("/api/phone-act", "act", H + ":phone_act",
              body={"by": Field("int", required=False)},
              returns={}, confirmed=True, status="/api/phone-job", gateway=True),
        Route("/api/events", "read", H + ":events", stream=True, gateway=True,
              topics={"count": Out("table", items={"n": Out("int")}),
                      "note": Out("table", items={"text": Out("string")}),
                      "phone": Out("table", items={"n": Out("int"),
                                                   "secret": Out("string", mark="redact"),
                                                   "file": Out("string", mark="path")})}),
    ),
    assets="web",
    health=H + ":health",
)

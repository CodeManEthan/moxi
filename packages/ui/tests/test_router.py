"""The worker layer, the shell table and the router, through `worker_layer(guard(...))`
with the shell's real profile."""

from __future__ import annotations

import asyncio

import pytest
from moxi_ui.router import fallback, shell_table
from shell_kit import FETCH, NAVIGATE, WORKER, Harness, Recorder


@pytest.fixture
def shell(tmp_path):
    return Harness(tmp_path / "state")


WORKER_PATHS = ["/a/b/c/sw.js", "/m/ui/a%20b/sw.js", "/m/fixture/%2e%2e/sw.js",
                "/nothing/holds/this.js", "/m/fixture/api/summary"]


@pytest.mark.parametrize("path", WORKER_PATHS)
def test_every_worker_update_check_gets_the_worker_answer(shell, path):
    cookie, _ = shell.sign_in()
    answer = shell.send("GET", path, headers=[cookie, *WORKER], query=b"v=1")
    assert answer.status == 200
    assert answer.header("content-type").startswith("text/javascript")
    assert answer.header("service-worker-allowed") == "/"
    assert answer.header("cache-control") == "no-store"
    assert answer.header("clear-site-data") is None
    assert shell.store.live() == 0  # The session its cookie named ended.
    assert shell.proxy.scopes == [] and shell.push.scopes == []


@pytest.mark.parametrize("path", WORKER_PATHS)
def test_a_foreign_host_is_refused_and_ends_no_session(shell, path):
    cookie, _ = shell.sign_in()
    answer = shell.send("GET", path, headers=[cookie, *WORKER], host=b"evil.example:4321")
    assert answer.status == 403
    assert shell.store.live() == 1


@pytest.mark.parametrize("path", ["/m/ui/a%20b/sw.js", "/m/fixture/%2e%2e/sw.js"])
def test_without_the_header_a_percent_path_is_404(shell, path):
    cookie, _ = shell.sign_in()
    assert shell.send("GET", path, headers=[cookie, *FETCH]).status == 404
    assert shell.send("GET", path, headers=[cookie, *NAVIGATE]).status == 404


def test_the_worker_path_without_the_header_is_refused(shell):
    cookie, key = shell.sign_in()
    assert shell.send("GET", "/m/ui/api/worker", headers=[cookie, key, *FETCH]).status == 403
    assert shell.store.live() == 1


def test_the_url_map(shell):
    cookie, _ = shell.sign_in()
    answer = shell.send("GET", "/m/fixture", headers=[cookie, *FETCH])
    assert answer.status == 308 and answer.header("location") == "/m/fixture/"
    answer = shell.send("GET", "/m/ui", headers=[cookie, *NAVIGATE])
    assert answer.status == 308 and answer.header("location") == "/m/ui/"
    answer = shell.send("GET", "/m/fixture/days/2026-10-30", headers=[cookie, *NAVIGATE])
    assert answer.status == 200 and answer.header("content-type").startswith("text/html")
    assert b"not signed in" not in answer.body
    assert shell.send("GET", "/m/nope/", headers=[cookie, *FETCH]).status == 404
    assert shell.send("GET", "/m/nope", headers=[cookie, *FETCH]).status == 404
    assert shell.proxy.scopes == []


def test_the_shell_page_gets_only_the_forwarded_headers(tmp_path):
    from moxi_ui.router import shell_profile
    from shell_kit import FIXTURE_EXPORT

    page = Recorder("page")
    shell = Harness(tmp_path / "state", page=page)
    ui_export = {"format": 1, "routes": [{"path": "/api/modules", "kind": "read"}],
                 "assets": []}
    shell.profile = shell_profile(shell_table(["fixture"], {"fixture": FIXTURE_EXPORT,
                                                            "ui": ui_export}),
                                  shell.sessions, ("127.0.0.1", "localhost"))
    cookie, key = shell.sign_in()
    jar = (b"cookie", cookie[1] + b"; " + b"; ".join(b"c%d=%s" % (n, b"v" * 4000)
                                                    for n in range(180)))
    headers = [jar, key, *FETCH, (b"accept", b"*/*"), (b"accept-language", b"en"),
               (b"if-none-match", b'"x"'), (b"referer", b"http://127.0.0.1:4321/m/ui/")]
    assert shell.send("GET", "/m/ui/api/modules", headers=headers).status == 299
    (seen,) = page.scopes
    assert [name for name, _ in seen["headers"]] == [b"accept", b"accept-language",
                                                    b"if-none-match"]


def test_the_shell_page_answers_its_health(shell):
    answer = shell.send("GET", "/m/ui/api/health", headers=[(b"x-moxi-token", b"T" * 43)])
    assert answer.status == 200 and answer.json()["module"] == "ui"


@pytest.mark.parametrize("names", [("127.0.0.1",), ("127.0.0.1", "localhost")])
def test_localhost_is_admitted_only_while_the_listeners_name_it(tmp_path, names):
    """The listeners leave `localhost` out while another socket holds `[::1]` on the
    port; a request naming it is then refused, though its cookie and key are good."""
    from shell_kit import HOST

    shell = Harness(tmp_path / "state", names=names)
    cookie, key = shell.sign_in()
    for host, admitted in ((b"localhost:4321", "localhost" in names), (HOST, True)):
        answer = shell.send("GET", "/m/fixture/api/summary", headers=[cookie, key, *FETCH],
                            host=host)
        assert answer.status == (299 if admitted else 403), host


class Held:
    """A stand-in for the push or the proxy: opens its answer, then holds it until its
    browser leaves."""

    def __init__(self):
        self.opened = asyncio.Event()
        self.left = False

    async def __call__(self, sc, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"open", "more_body": True})
        self.opened.set()
        while (await receive())["type"] != "http.disconnect":
            pass
        self.left = True


@pytest.mark.parametrize(("path", "finished"), [("/m/ui/api/push", True),
                                                ("/m/fixture/api/events", False)])
@pytest.mark.parametrize("ending", ["signout", "worker"])
def test_a_held_request_ends_when_its_session_does(tmp_path, path, finished, ending):
    from shell_kit import scope

    shell = Harness(tmp_path / "state")
    held = Held()
    shell.router.push = shell.router.proxy = held
    cookie, key = shell.sign_in()
    other, _ = shell.sign_in()
    sent = []

    async def browser_stays():
        await asyncio.Event().wait()

    async def record(message):
        sent.append(message)

    async def run():
        request = asyncio.ensure_future(
            shell.asgi(scope("GET", path, headers=[cookie, key, *FETCH]), browser_stays, record))
        await asyncio.wait_for(held.opened.wait(), 5)
        assert not request.done()
        if ending == "signout":
            shell.store.clear()
        else:
            # Another session ending leaves the request open.
            shell.store.end([other[1].decode().split("=", 1)[1]])
            await asyncio.sleep(0.05)
            assert not request.done()
            shell.store.end([cookie[1].decode().split("=", 1)[1]])
        await asyncio.wait_for(request, 5)

    asyncio.run(run())
    assert held.left
    last = sent[-1]
    assert (last == {"type": "http.response.body", "body": b""}) is finished
    assert [m.get("body") for m in sent[1:]] == ([b"open", b""] if finished else [b"open"])


def test_the_fallback_answers_navigations_only():
    from moxi_loopback.api import RequestView

    nav = RequestView("GET", b"/m/x/y", NAVIGATE, "tcp", 4321)
    assert fallback(nav).kind == "builtin"
    assert fallback(RequestView("GET", b"/m/x/y", FETCH, "tcp", 4321)) is None


def test_the_table_refuses_a_name_that_is_no_module():
    with pytest.raises(ValueError):
        shell_table(["Bad-Name"])
    table = shell_table(["fixture"])
    assert table.find("GET", "/m/fixture") is not None and table.find("GET", "/m/ui") is not None


def test_signout_ends_every_session(shell):
    shell.sign_in()
    shell.sign_in()
    answer = shell.send("POST", "/m/ui/api/signout", body=b"{}",
                        headers=[(b"x-moxi-token", b"T" * 43),
                                 (b"content-type", b"application/json")])
    assert answer.status == 200 and shell.store.live() == 0

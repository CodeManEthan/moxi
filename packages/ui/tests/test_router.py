"""The worker layer, the shell table and the router, through `worker_layer(guard(...))`
with the shell's real profile."""

from __future__ import annotations

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

"""comms' page's acts through a real test shell, with no browser: a request without the
shell's key or cookie is refused by the shell before comms' page server starts, and one
with both reaches it.

The shell keeps every file under its own home, so its comms page server finds no
`comms.db` and no `MOXI_COMMS_OPERATOR` there, and answers `operator-unknown` without
asking any daemon.
"""

from __future__ import annotations

import http.client
import json
import sys

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="comms runs on Linux only")

ACTS = ("/m/comms/api/check", "/m/comms/api/send")
BODY = {"to": "main@proj-a", "text": "hello", "posture": "none", "held": False}


@pytest.fixture(scope="module")
def ui_shell_options():
    return {"child_args": ["--idle-s", "1"]}


def post(port: int, path: str, headers: dict[str, str]) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("POST", path, body=json.dumps(BODY).encode(), headers=headers)
        answer = conn.getresponse()
        answer.read()
    finally:
        conn.close()
    return answer.status


def not_started(shell) -> bool:
    row = shell.server("comms")
    return row is None or row["pid"] is None


def test_an_act_without_the_key_or_the_cookie_is_refused_before_the_page_server(ui_shell):
    shell = ui_shell
    session = shell.session()
    full = session.headers(act=True)
    without_key = {k: v for k, v in full.items() if k != "X-Moxi-Key"}
    without_cookie = {k: v for k, v in full.items() if k != "Cookie"}
    wrong_key = {**full, "X-Moxi-Key": "k" * len(session.key)}
    assert not_started(shell)
    for path in ACTS:
        for headers in (without_key, without_cookie, wrong_key):
            assert post(shell.port, path, headers) == 403, (path, sorted(headers))
    assert not_started(shell), "a refused act never reaches comms' page server"

    for path in ACTS:
        status, answer = session.post(path, BODY)
        assert status == 200, (path, status)
        assert answer == {"outcome": "refused", "message_id": None, "recipients": [],
                          "refusal": {"code": "operator-unknown"}}
    assert not not_started(shell)

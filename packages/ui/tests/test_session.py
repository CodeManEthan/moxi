"""The browser session: the credential classes, tickets, the owner checks, the store and
its epoch, the sign-in page and the worker answer."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import threading

import pytest
from moxi_ui import session
from moxi_ui.serve import may_exit_idle
from moxi_ui.session import (
    IDLE,
    LIMIT,
    MAX_AGE,
    OwnerCheck,
    Tickets,
    clear_stored,
    open_store,
)
from shell_kit import (
    ACT,
    CLIENT,
    FETCH,
    HOST,
    NAVIGATE,
    NONCE,
    PORT,
    SERVER,
    TOKEN,
    WORKER,
    Harness,
    connection_rows,
    record_proc,
)


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def shell(tmp_path):
    return Harness(tmp_path / "state")


# --- the credential table ---------------------------------------------------------------


def test_open_paths_need_nothing(shell):
    answer = shell.send("GET", "/signin", headers=NAVIGATE)
    assert answer.status == 200 and b"Signing in" in answer.body
    answer = shell.send("GET", "/api/prove", headers=[(b"x-moxi-nonce", NONCE.encode())])
    assert answer.status == 204 and answer.header("x-moxi-proof")


def test_sign_in_needs_only_a_ticket(shell):
    ticket = shell.tickets.mint()
    body = json.dumps({"ticket": ticket}).encode()
    answer = shell.send("POST", "/m/ui/api/signin", headers=ACT, body=body)
    assert answer.status == 200
    cookie = answer.header("set-cookie")
    assert re.fullmatch(r"moxi-ui=[A-Za-z0-9_-]{43}; HttpOnly; SameSite=Strict; Path=/; "
                        r"Max-Age=7776000", cookie)
    key = answer.json()["key"]
    session_id = cookie.split(";")[0].split("=", 1)[1]
    assert shell.store.find([session_id], key, need_key=True) is not None
    # The ticket is spent.
    again = shell.send("POST", "/m/ui/api/signin", headers=ACT, body=body)
    assert again.status == 403


def _with_own_page(shell):
    """`shell` with the shell's own page export mounted, as the shell mounts it, and a
    recorder answering for the page."""
    from moxi_loopback.api import export
    from moxi_ui.page import PAGE
    from moxi_ui.router import shell_profile, shell_table
    from shell_kit import FIXTURE_EXPORT, Recorder

    shell.router.page = Recorder("page")
    table = shell_table(["fixture"], {"fixture": FIXTURE_EXPORT, "ui": export(PAGE)})
    shell.profile = shell_profile(table, shell.sessions, ("127.0.0.1", "localhost"))
    return shell


_POSTED = {session.STOP_PATH, session.SIGNOUT_API}


@pytest.mark.parametrize("path", sorted(session.TOKEN_PATHS))
def test_token_routes(shell, path):
    shell = _with_own_page(shell)
    method, body = ("POST", b"{}") if path in _POSTED else ("GET", b"")
    json_type = (b"content-type", b"application/json")

    def send(*headers):
        return shell.send(method, path, headers=[json_type, *headers], body=body).status

    token = (b"x-moxi-token", TOKEN.encode())
    assert send(token) in (200, 299)
    assert send() == 403
    assert send((b"x-moxi-token", b"X" * 43)) == 403
    cookie, key = shell.sign_in()
    for extra in ([cookie], [(b"sec-fetch-site", b"same-origin")], [(b"referer", b"http://x/")],
                  [(b"origin", b"http://" + HOST)]):
        assert send(token, *extra) == 403
    # A browser's own session is not the token.
    assert send(cookie, key, *FETCH) == 403
    assert send(cookie, key, *ACT) == 403
    assert shell.store.live() == 1


def test_doors_need_the_cookie(shell):
    cookie, _ = shell.sign_in()
    answer = shell.send("GET", "/", headers=[cookie, *FETCH])
    assert answer.status == 302 and answer.header("location") == "/m/ui/"
    answer = shell.send("GET", "/m/fixture", headers=[cookie, *FETCH])
    assert answer.status == 308 and answer.header("location") == "/m/fixture/"
    assert shell.send("GET", "/", headers=FETCH).status == 403
    assert shell.send("GET", "/m/fixture", headers=FETCH).status == 403


def test_a_navigation_without_a_cookie_learns_nothing(shell):
    for path in ("/", "/m/fixture", "/m/nothing", "/m/fixture/days/2026", "/m/ui/"):
        answer = shell.send("GET", path, headers=NAVIGATE)
        assert answer.status == 200 and b"not signed in" in answer.body, path
    assert shell.proxy.scopes == []


def test_assets_need_the_cookie_and_routes_the_key(shell):
    cookie, key = shell.sign_in()
    assert shell.send("GET", "/m/fixture/page.js", headers=[cookie, *FETCH]).status == 299
    assert shell.send("GET", "/m/fixture/page.js", headers=FETCH).status == 403
    assert shell.send("GET", "/m/fixture/api/summary", headers=[cookie, key, *FETCH]).status == 299
    assert shell.send("GET", "/m/fixture/api/summary", headers=[cookie, *FETCH]).status == 403
    assert shell.send("GET", "/m/fixture/api/summary", headers=[key, *FETCH]).status == 403
    assert shell.send("POST", "/m/fixture/api/refresh", headers=[cookie, key, *ACT],
                      body=b"{}").status == 299
    assert shell.send("POST", "/m/fixture/api/refresh", headers=[cookie, *ACT],
                      body=b"{}").status == 403
    assert len(shell.proxy.scopes) == 3


def test_push_needs_the_cookie_and_key(shell):
    cookie, key = shell.sign_in()
    assert shell.send("GET", "/m/ui/api/push", headers=[cookie, key, *FETCH]).status == 299
    assert shell.send("GET", "/m/ui/api/push", headers=[cookie, *FETCH]).status == 403
    assert len(shell.push.scopes) == 1


def test_an_unknown_path_is_404_only_with_a_cookie(shell):
    cookie, _ = shell.sign_in()
    assert shell.send("GET", "/m/fixture/nope", headers=[cookie, *FETCH]).status == 404
    assert shell.send("GET", "/m/fixture/nope", headers=FETCH).status == 403


def test_a_key_of_another_session_is_refused(shell):
    cookie, _ = shell.sign_in()
    _, other_key = shell.sign_in()
    path = "/m/fixture/api/summary"
    assert shell.send("GET", path, headers=[cookie, other_key, *FETCH]).status == 403


def test_a_foreign_cookie_beside_ours_is_ignored(shell):
    cookie, key = shell.sign_in()
    mixed = (b"cookie", b"moxi-ui=" + b"A" * 43 + b"; " + cookie[1])
    assert shell.send("GET", "/m/fixture/api/summary", headers=[mixed, key, *FETCH]).status == 299


def test_forged_header_classes_get_only_static_answers(shell):
    cookie, key = shell.sign_in()
    # A forged navigation with a valid cookie and key gets the shell document, never the
    # route or the proxy.
    answer = shell.send("GET", "/m/fixture/api/summary", headers=[cookie, key, *NAVIGATE])
    assert answer.status == 200 and answer.header("content-type").startswith("text/html")
    # A forged worker header gets the worker answer, never the route.
    answer = shell.send("GET", "/m/fixture/api/summary", headers=[cookie, key, *WORKER])
    assert answer.status == 200 and answer.header("content-type").startswith("text/javascript")
    assert shell.proxy.scopes == [] and shell.push.scopes == []


# --- tickets ----------------------------------------------------------------------------


def test_a_ticket_is_spent_once_under_racing_redemptions(tmp_path):
    tickets = Tickets(tmp_path / "tickets")
    for _ in range(20):
        ticket = tickets.mint()
        barrier = threading.Barrier(2)
        results = []

        def redeem():
            barrier.wait()
            results.append(tickets.redeem(ticket))

        threads = [threading.Thread(target=redeem) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == [False, True]


def test_ticket_files_are_private_and_hold_no_ticket(tmp_path):
    tickets = Tickets(tmp_path / "tickets")
    ticket = tickets.mint()
    assert stat.S_IMODE(os.stat(tmp_path / "tickets").st_mode) == 0o700
    (path,) = (tmp_path / "tickets").iterdir()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.name == hashlib.sha256(ticket.encode()).hexdigest()[:32]
    assert ticket.encode() not in path.read_bytes()


def test_tickets_expire_and_are_swept(tmp_path):
    clock = Clock()
    tickets = Tickets(tmp_path / "tickets", clock=clock)
    old, kept = tickets.mint(), None
    clock.now += 31
    kept = tickets.mint()
    assert tickets.redeem(old) is False
    assert tickets.redeem("x" * 43) is False
    assert tickets.redeem(kept) is True
    stale = tickets.mint()
    clock.now += 31
    tickets.redeem("not a ticket")
    assert list((tmp_path / "tickets").iterdir()) == []
    assert tickets.redeem(stale) is False


# --- the owner checks -------------------------------------------------------------------


def _owned(tmp_path, uid, *, client=CLIENT, server=SERVER, six=False):
    rows = connection_rows(client, server, uid)
    root = record_proc(tmp_path / "proc", **({"tcp6": rows} if six else {"tcp": rows}))
    return OwnerCheck(True, os.getuid(), str(root))


def test_socket_owner_reads_the_client_row(tmp_path):
    root = record_proc(tmp_path / "proc", tcp=connection_rows(CLIENT, SERVER, 4242),
                       tcp6=connection_rows(("::1", 50001), ("::1", PORT), 4343))
    assert session.socket_owner(CLIENT, SERVER, root=root) == 4242
    assert session.socket_owner(("::1", 50001), ("::1", PORT), root=root) == 4343
    assert session.socket_owner(("127.0.0.1", 1), SERVER, root=root) is None
    assert session.socket_owner(None, SERVER, root=root) is None


@pytest.mark.parametrize("six", [False, True])
def test_a_ticket_redeemed_over_another_users_socket_is_refused_and_kept(tmp_path, six):
    client, server = (("::1", 50001), ("::1", PORT)) if six else (CLIENT, SERVER)
    host = b"localhost:%d" % PORT if six else HOST
    headers = [(b"origin", b"http://" + host), (b"sec-fetch-site", b"same-origin"),
               (b"content-type", b"application/json")]
    for uid, expected in ((os.getuid() + 1, 403), (None, 403), (os.getuid(), 200)):
        if uid is None:
            root = record_proc(tmp_path / "empty")
            owner = OwnerCheck(True, os.getuid(), str(root))
        else:
            owner = _owned(tmp_path / str(uid), uid, client=client, server=server, six=six)
        shell = Harness(tmp_path / f"state-{uid}", owner=owner, names=("127.0.0.1", "localhost"))
        ticket = shell.tickets.mint()
        answer = shell.send("POST", "/m/ui/api/signin", headers=headers,
                            body=json.dumps({"ticket": ticket}).encode(), host=host,
                            client=client, server=server)
        assert answer.status == expected, uid
        left = list((tmp_path / f"state-{uid}" / "tickets").iterdir())
        assert len(left) == (0 if expected == 200 else 1)


def test_a_session_on_another_users_socket_is_refused(tmp_path):
    theirs = Harness(tmp_path / "a", owner=_owned(tmp_path / "a", os.getuid() + 1))
    cookie, key = theirs.sign_in()
    for path, extra in (("/m/fixture/api/summary", [key]), ("/m/fixture/page.js", []),
                        ("/", [])):
        assert theirs.send("GET", path, headers=[cookie, *extra, *FETCH]).status == 403
    # A navigation still gets a static page, but the signed-out one.
    answer = theirs.send("GET", "/m/fixture/", headers=[cookie, *NAVIGATE])
    assert b"not signed in" in answer.body
    ours = Harness(tmp_path / "b", owner=_owned(tmp_path / "b", os.getuid()))
    cookie, key = ours.sign_in()
    assert ours.send("GET", "/m/fixture/api/summary", headers=[cookie, key, *FETCH]).status == 299


# --- WSL --------------------------------------------------------------------------------


@pytest.mark.parametrize("release,interop,expected", [
    ("6.6.36.6-microsoft-standard-WSL2\n", False, True),
    ("4.4.0-19041-Microsoft\n", False, True),
    ("6.6.0-custom\n", True, True),
    ("6.17.1-300.fc43.x86_64\n", False, False),
])
def test_wsl_is_read_from_osrelease_or_interop(tmp_path, release, interop, expected):
    (tmp_path / "sys/kernel").mkdir(parents=True)
    (tmp_path / "sys/kernel/osrelease").write_text(release)
    if interop:
        (tmp_path / "sys/fs/binfmt_misc").mkdir(parents=True)
        (tmp_path / "sys/fs/binfmt_misc/WSLInterop").write_text("enabled\n")
    assert session.is_wsl(tmp_path) is expected


def test_under_wsl_there_is_no_owner_check(tmp_path, monkeypatch):
    monkeypatch.setattr(session.sys, "platform", "linux")
    monkeypatch.setattr(session, "is_wsl", lambda root="/proc": True)
    assert OwnerCheck.here().enabled is False
    # A recorded table where the connection is another user's: with the check off, a
    # sign-in and a request pass; with it on, both are 403.
    other = os.getuid() + 1
    for owner, expected, routed in ((OwnerCheck.here(), 200, 299),
                                    (_owned(tmp_path / "on", other), 403, 403)):
        shell = Harness(tmp_path / f"s{expected}", owner=owner)
        ticket = shell.tickets.mint()
        answer = shell.send("POST", "/m/ui/api/signin", headers=ACT,
                            body=json.dumps({"ticket": ticket}).encode())
        assert answer.status == expected
        cookie, key = shell.sign_in()
        assert shell.send("GET", "/m/fixture/api/summary",
                          headers=[cookie, key, *FETCH]).status == routed


def test_the_owner_check_is_off_outside_linux(monkeypatch):
    monkeypatch.setattr(session.sys, "platform", "darwin")
    assert OwnerCheck.here().enabled is False


# --- the store and its epoch ------------------------------------------------------------


def test_the_file_holds_only_hashes_and_is_private(tmp_path):
    store = open_store(tmp_path, "ui-shell.socket=abc")
    session_id, key = store.mint()
    path = tmp_path / "sessions.json"
    data = path.read_bytes()
    assert session_id.encode() not in data and key.encode() not in data
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    record = json.loads(data)
    assert record["epoch"] == "ui-shell.socket=abc"
    assert list(record["sessions"]) == [hashlib.sha256(session_id.encode()).hexdigest()]


def test_sessions_last_across_shells_of_one_epoch_only(tmp_path):
    first = open_store(tmp_path, "epoch-1")
    session_id, key = first.mint()
    again = open_store(tmp_path, "epoch-1")
    assert again.find([session_id], key, need_key=True) is not None
    later = open_store(tmp_path, "epoch-2")
    assert later.find([session_id], key, need_key=True) is None
    record = json.loads((tmp_path / "sessions.json").read_bytes())
    assert record["epoch"] == "epoch-2" and record["sessions"] == {}
    # A session copied in from the earlier epoch's file is refused too.
    assert open_store(tmp_path, "epoch-1").find([session_id], key, need_key=True) is None


def test_a_shell_with_no_epoch_neither_reads_nor_writes_the_file(tmp_path):
    stored = open_store(tmp_path, "epoch-1")
    session_id, key = stored.mint()
    before = (tmp_path / "sessions.json").read_bytes()
    memory = open_store(tmp_path, None)
    assert memory.find([session_id], key, need_key=True) is None
    mine, my_key = memory.mint()
    assert memory.find([mine], my_key, need_key=True) is not None
    memory.clear()
    assert (tmp_path / "sessions.json").read_bytes() == before
    open_store(tmp_path / "fresh", None).mint()
    assert not (tmp_path / "fresh").exists()


def test_idle_and_absolute_expiry(tmp_path):
    clock = Clock()
    store = open_store(tmp_path, None, clock=clock)
    idle_id, idle_key = store.mint()
    clock.now += IDLE - 1
    assert store.find([idle_id], idle_key, need_key=True) is not None
    clock.now += IDLE
    assert store.find([idle_id], idle_key, need_key=True) is None
    used_id, used_key = store.mint()
    for _ in range(int(MAX_AGE // (24 * 3600)) - 1):
        clock.now += 24 * 3600
        assert store.find([used_id], used_key, need_key=True) is not None
    clock.now += 24 * 3600
    assert store.find([used_id], used_key, need_key=True) is None


def test_the_oldest_is_evicted_past_the_limit(tmp_path):
    clock = Clock()
    store = open_store(tmp_path, None, clock=clock)
    made = []
    for _ in range(LIMIT + 1):
        clock.now += 1
        made.append(store.mint())
    assert store.live() == LIMIT
    assert store.find([made[0][0]], need_key=False) is None
    assert all(store.find([sid], need_key=False) for sid, _ in made[1:])


def test_last_used_is_written_at_most_once_an_hour(tmp_path):
    clock = Clock()
    store = open_store(tmp_path, "e", clock=clock)
    session_id, _ = store.mint()
    path = tmp_path / "sessions.json"
    written = path.read_bytes()
    clock.now += 600
    store.find([session_id], need_key=False)
    assert path.read_bytes() == written
    clock.now += 3000
    store.find([session_id], need_key=False)
    assert path.read_bytes() != written


def test_a_watch_is_called_once_however_its_session_ends(tmp_path):
    clock = Clock()
    store = open_store(tmp_path, None, clock=clock)
    called = []

    def watched():
        session_id, _ = store.mint()
        id_hash = hashlib.sha256(session_id.encode()).hexdigest()
        store.watch(id_hash, lambda: called.append(session_id))
        return session_id

    # Ended by id (a worker's update check), and by a signout.
    first = watched()
    store.end([first])
    store.end([first])
    assert called == [first]
    second = watched()
    store.clear()
    assert called == [first, second]
    # Evicted past the limit.
    third = watched()
    for _ in range(LIMIT):
        store.mint()
    assert called[-1] == third
    # Found expired.
    store.clear()
    fourth = watched()
    clock.now += IDLE
    assert store.live() == 0 and called[-1] == fourth
    # Already gone: called at once. A dropped watch is never called.
    store.watch("0" * 64, lambda: called.append("gone"))
    assert called[-1] == "gone"
    session_id, _ = store.mint()
    stop = store.watch(hashlib.sha256(session_id.encode()).hexdigest(),
                       lambda: called.append("dropped"))
    stop()
    store.clear()
    assert "dropped" not in called and len(called) == 5


def test_signout_while_no_shell_runs_keeps_the_epoch(tmp_path):
    store = open_store(tmp_path, "e")
    session_id, _ = store.mint()
    assert clear_stored(tmp_path) is True
    assert open_store(tmp_path, "e").find([session_id], need_key=False) is None
    assert json.loads((tmp_path / "sessions.json").read_bytes())["epoch"] == "e"
    assert clear_stored(tmp_path / "none") is False


def test_idle_exit_never_loses_a_session_or_the_port():
    assert may_exit_idle(inherited=False, epoch=None, live_sessions=0) is False
    assert may_exit_idle(inherited=False, epoch="e", live_sessions=0) is False
    assert may_exit_idle(inherited=True, epoch=None, live_sessions=1) is False
    assert may_exit_idle(inherited=True, epoch=None, live_sessions=0) is True
    assert may_exit_idle(inherited=True, epoch="e", live_sessions=3) is True


# --- the sign-in page and the worker answer ---------------------------------------------


def test_the_sign_in_url_is_fresh_each_time():
    urls = {session.signin_url(8460, "t" * 43) for _ in range(50)}
    assert len(urls) == 50
    for url in urls:
        assert re.fullmatch(r"http://127\.0\.0\.1:8460/signin\?n=[0-9a-f]{16}#t=t{43}", url)


def test_the_sign_in_page_clears_cache_and_storage_but_not_cookies(shell):
    answer = shell.send("GET", "/signin", headers=NAVIGATE, query=b"n=0123456789abcdef")
    assert answer.header("clear-site-data") == '"cache", "storage"'
    assert "cookies" not in answer.header("clear-site-data")
    # Its one inline script runs by its hash, not by any looser policy.
    script = re.search(rb"<script>(.*)</script>", answer.body, re.S).group(1)
    digest = base64.b64encode(hashlib.sha256(script).digest()).decode()
    policy = answer.header("content-security-policy")
    assert f"script-src 'sha256-{digest}'" in policy and "'self'" not in policy.split(
        "script-src")[1].split(";")[0]
    assert answer.header("cache-control") == "no-store"


def test_the_worker_answer():
    status, headers, body = session.worker_answer()
    named = {k.decode(): v.decode() for k, v in headers}
    assert status == 200
    assert named["content-type"].startswith("text/javascript")
    assert named["service-worker-allowed"] == "/"
    assert named["cache-control"] == "no-store"
    assert "clear-site-data" not in named
    assert b"skipWaiting" in body and b"unregister" in body

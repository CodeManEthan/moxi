"""`moxi comms exit-session`: the verb against claim holders on real claim sockets.

Each holder stands in for one session's adapter: a `ClaimHolder` whose claim file is in
the test's runtime directory and whose socket answers `end` as an adapter's does. The
wrapper's own run of a headless session, with the verb ending it, is in
`test_codex_wrapper.py`.
"""

from __future__ import annotations

import os
import threading

import pytest

from moxi import exit as moxi_exit
from moxi_comms import cli
from moxi_comms.claimhold import ClaimHolder
from moxi_comms.membership import SELF, TREE, root_of


class Said:
    def __init__(self):
        self.lines: list = []

    def say(self, text: str) -> None:
        self.lines.append(text)


@pytest.fixture
def holders(tmp_path):
    made: list = []

    def build(key="x-1", name="x", scope="proj-a", *, listen=True, **claim):
        h = ClaimHolder(kind="codex", key=key, keying="pid",
                        socket_path=tmp_path / f"{key}.sock",
                        roots=[root_of(os.getpid(), SELF if listen else TREE)])
        qualified = name if scope == "global" else f"{name}@{scope}"
        h.start_launch({"name": name, "scope": scope, "global": scope == "global",
                        "qualified": qualified, "directors": [], **claim})
        if listen:
            h.listen()
        made.append(h)
        return h

    yield build
    for h in made:
        h.close()


def run(*argv):
    said = Said()
    ctx = cli.Context(env=os.environ)
    ctx.say = said.say
    return cli.main(["exit-session", *argv], ctx), said.lines


def test_it_ends_a_headless_session_and_waits_for_the_name_to_go(holders):
    h = holders(headless=True, exit=["done"])
    asked = threading.Event()

    def end():
        # As an adapter does: the answer goes first, and the session ends after it.
        asked.set()
        threading.Timer(0.2, h.close).start()

    h.on_end = end
    code, lines = run("x", "--scope", "proj-a")
    assert (code, lines) == (moxi_exit.OK, ["x@proj-a has ended"])
    assert asked.is_set()


def test_the_bound_record_is_ended_when_a_second_launch_still_binds(holders):
    """A second launch of the name, `offline` while its bind is refused, holds the name
    too until it ends: the verb ends the session that is bound."""
    binding = holders(key="a-1", headless=True, exit=["done"])
    binding.on_end = lambda: pytest.fail("the launch still binding is not the session")
    h = holders(key="b-2", headless=True, exit=["done"])
    h.bound({"identity": {"qualified": "x@proj-a"}})
    asked = threading.Event()

    def end():
        asked.set()
        threading.Timer(0.2, lambda: (h.close(), binding.close())).start()

    h.on_end = end
    code, lines = run("x", "--scope", "proj-a")
    assert (code, lines) == (moxi_exit.OK, ["x@proj-a has ended"])
    assert asked.is_set()


def test_a_bare_name_is_the_global_identity(holders):
    h = holders(name="herald", scope="global", headless=True, exit=["done"])
    h.on_end = lambda: threading.Timer(0.2, h.close).start()
    assert run("herald")[0] == moxi_exit.OK


def test_a_name_nobody_holds_is_refused(holders):
    holders(name="other", headless=True, exit=["done"])
    assert run("x", "--scope", "proj-a") == (moxi_exit.REFUSED, ["x@proj-a is not held"])


def test_a_session_that_is_not_headless_is_refused_at_the_record(holders):
    h = holders()
    h.on_end = lambda: pytest.fail("a session with a person is not ended")
    code, lines = run("x", "--scope", "proj-a")
    assert code == moxi_exit.REFUSED
    assert lines == ["x@proj-a is not a headless session; exit it where it runs"]


def test_an_adapter_that_refuses_end_is_refused(holders):
    """A record that says headless whose adapter has no end to run answers the socket's
    refusal, which the verb reads as the record's."""
    holders(headless=True, exit=["done"])
    code, lines = run("x", "--scope", "proj-a")
    assert code == moxi_exit.REFUSED and "not a headless session" in lines[0]


def test_a_socket_that_does_not_answer_is_exit_1(holders):
    holders(listen=False, headless=True, exit=["done"])
    code, lines = run("x", "--scope", "proj-a")
    assert code == moxi_exit.ERROR and "its adapter did not answer" in lines[0]


def test_a_wait_that_runs_out_is_exit_1(holders):
    h = holders(headless=True, exit=["done"])
    h.on_end = lambda: None
    code, lines = run("x", "--scope", "proj-a", "--wait-s", "0.3")
    assert code == moxi_exit.ERROR
    assert lines == ["x@proj-a is ending but still held after 0.3s; its adapter's log "
                     "says why"]


@pytest.mark.parametrize("argv", [["x@proj-a"], ["bad name"], ["x", "--scope", "a b"],
                                  ["x", "--wait-s", "-1"]])
def test_a_bad_name_or_wait_is_usage(holders, argv):
    assert run(*argv)[0] == moxi_exit.USAGE


def test_scope_and_global_are_one_choice():
    with pytest.raises(SystemExit) as raised:
        run("x", "--scope", "proj-a", "--global")
    assert raised.value.code == moxi_exit.USAGE

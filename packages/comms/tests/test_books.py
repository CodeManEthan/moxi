"""The delivery books in comms.db: rows, digests, the ack and its cursor, release, fence."""

from __future__ import annotations

import threading

import pytest

from moxi_comms.store import books as books_mod
from moxi_comms.store.books import Books

NAME = "reviewer@proj-a"
OTHER = "planner@proj-a"


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def books(env, clock, tmp_path):
    b = Books(tmp_path / "comms.db", clock=clock)
    b.open()
    yield b
    b.close()


class Faulty:
    """A connection that raises on the first statement containing `needle`."""

    def __init__(self, conn, needle: str):
        self._conn, self._needle, self.fired = conn, needle, False

    def execute(self, sql, *args):
        if not self.fired and self._needle in sql:
            self.fired = True
            raise RuntimeError(f"fault at {self._needle!r}")
        return self._conn.execute(sql, *args)

    def __getattr__(self, attr):
        return getattr(self._conn, attr)


def state(books, name=NAME) -> dict:
    return {r.message_id: (r.state, r.digest_id) for r in books.rows(name)}


def snapshot(books) -> tuple:
    conn = books._conn
    return tuple(conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()
                 for t in ("queue", "digests", "cursors", "dropped"))


def faulted(books, needle, call):
    before = snapshot(books)
    real = books._conn
    books._conn = Faulty(real, needle)
    try:
        with pytest.raises(RuntimeError):
            call()
        assert books._conn.fired, f"no statement matched {needle!r}"
    finally:
        books._conn = real
    assert snapshot(books) == before, "a fault inside the transaction landed part of it"


# ------------------------------------------------------------------ rows

def test_a_routed_message_holds_a_row_waiting_or_handed(books):
    assert books.add(NAME, 101) is True
    assert books.add(NAME, 102, binding_id=7) is True
    assert books.add(NAME, 101) is False, "a row that exists is left as it is"
    assert state(books) == {"101": ("waiting", None), "102": ("handed", None)}
    assert books.rows(NAME, states=["handed"])[0].binding_id == 7


def test_a_push_that_failed_puts_its_row_back_to_waiting(books):
    books.hand(NAME, 101, 7)
    assert state(books) == {"101": ("handed", None)}
    assert books.add(NAME, 101) is True
    assert state(books) == {"101": ("waiting", None)}


def test_hand_takes_a_waiting_row_and_makes_one_for_a_live_send(books):
    books.add(NAME, 101)
    books.hand(NAME, 101, 3)
    books.hand(NAME, 102, 3)
    assert state(books) == {"101": ("handed", None), "102": ("handed", None)}


# ------------------------------------------------------------------ open_digest

def test_open_digest_puts_the_rows_in_one_digest(books):
    for mid in (101, 102):
        books.hand(NAME, mid, 3)
    opened = books.open_digest(NAME, 3, ["101", "102", "999"])
    assert opened.ids == ["101", "102"] and opened.dropped == []
    assert state(books) == {"101": ("in_digest", opened.digest),
                            "102": ("in_digest", opened.digest)}
    assert books.digest(opened.digest)["ids"] == ["101", "102"]
    assert {r.message_id for r in books.rows(NAME)} == {"101", "102"}
    row = books._conn.execute("SELECT digested_once FROM queue WHERE message_id = 101")
    assert row.fetchone()[0] == 1


def test_open_digest_drops_withdrawn_and_expired_rows(books, clock):
    conn = books._conn
    for mid, withdrawn, expires in ((101, "2027-01-01T00:00:00Z", None),
                                    (102, None, int(clock.t) - 1),
                                    (103, None, int(clock.t) + 60)):
        conn.execute("INSERT INTO messages(id, room_id, author_id, author_kind, author_display, "
                     "content, timestamp, withdrawn_at, expires_at) VALUES "
                     "(?, 'r', 'a', 'identity', 'a', 'x', 't', ?, ?)", (mid, withdrawn, expires))
        books.hand(NAME, mid, 3)
    opened = books.open_digest(NAME, 3, [101, 102, 103])
    assert opened.ids == ["103"]
    assert opened.dropped == [{"id": "101", "reason": "withdrawn"},
                              {"id": "102", "reason": "expired"}]
    assert state(books) == {"103": ("in_digest", opened.digest)}
    dropped = conn.execute("SELECT message_id, name, reason FROM dropped ORDER BY 1")
    assert dropped.fetchall() == [(101, NAME, "withdrawn"), (102, NAME, "expired")]
    again = books.open_digest(NAME, 3, [101])
    assert again.ids == [] and again.dropped == [{"id": "101", "reason": "withdrawn"}]


def test_a_fault_inside_open_digest_lands_nothing(books):
    books.hand(NAME, 101, 3)
    faulted(books, "UPDATE queue SET state = 'in_digest'",
            lambda: books.open_digest(NAME, 3, [101]))


# ------------------------------------------------------------------ the ack

def test_handled_deletes_rows_and_moves_the_cursor(books):
    for mid in (101, 102):
        books.hand(NAME, mid, 3)
    d = books.open_digest(NAME, 3, [101, 102]).digest
    ended = books.turn_ended(NAME, [d], [])
    assert ended.handled == ["101", "102"] and ended.cursor == "102"
    assert books.rows(NAME) == [] and books.cursor(NAME) == "102"
    assert books.digest(d)["outcome"] == "answer"


def test_unhandled_puts_rows_back_to_waiting_and_moves_nothing(books):
    books.hand(NAME, 101, 3)
    d = books.open_digest(NAME, 3, [101]).digest
    ended = books.turn_ended(NAME, [], [d])
    assert ended.unhandled == ["101"] and ended.cursor is None
    assert state(books) == {"101": ("waiting", None)}
    assert books.digest(d)["outcome"] == "unhandled"


def test_the_cursor_stays_below_a_held_row_and_never_goes_back(books):
    books.add(NAME, 100, held=True)                 # waits for a release point
    books.hand(NAME, 105, 3)
    d = books.open_digest(NAME, 3, [105]).digest
    assert books.turn_ended(NAME, [d], []).cursor == "99"
    # the acked one above the held row has no row now, and the cursor is below it
    assert [r.message_id for r in books.rows(NAME)] == ["100"]
    books.hand(NAME, 100, 3)
    d2 = books.open_digest(NAME, 3, [100]).digest
    assert books.turn_ended(NAME, [d2], []).cursor == "100"
    books.hand(NAME, 90, 3)
    d3 = books.open_digest(NAME, 3, [90]).digest
    assert books.turn_ended(NAME, [d3], []).cursor == "100", "never back"


def test_an_ack_names_only_the_names_own_open_digests(books):
    books.hand(NAME, 101, 3)
    books.hand(OTHER, 101, 4)
    mine = books.open_digest(NAME, 3, [101]).digest
    theirs = books.open_digest(OTHER, 4, [101]).digest
    assert books.turn_ended(NAME, [theirs], []).handled == []
    assert books.turn_ended(NAME, [mine], []).handled == ["101"]
    assert books.turn_ended(NAME, [mine], []).handled == [], "an ended digest is ignored"
    assert state(books, OTHER) == {"101": ("in_digest", theirs)}


def test_a_fault_inside_the_ack_lands_nothing(books):
    for mid in (101, 102):
        books.hand(NAME, mid, 3)
    a = books.open_digest(NAME, 3, [101]).digest
    b = books.open_digest(NAME, 3, [102]).digest
    faulted(books, "INSERT INTO cursors", lambda: books.turn_ended(NAME, [a], [b]))
    assert books.turn_ended(NAME, [a], [b]).cursor == "101"


def test_injected_and_started_mark_the_digest(books):
    books.hand(NAME, 101, 3)
    d = books.open_digest(NAME, 3, [101]).digest
    assert books.last_digest(NAME) is None
    assert books.injected(NAME, d, "the text") is True
    assert books.started(NAME, [d]) == 1
    got = books.last_digest(NAME)
    assert got["text"] == "the text" and got["started_at"] is not None


# ------------------------------------------------------------------ release, fence

def test_release_puts_handed_and_digested_rows_back(books):
    books.add(NAME, 100)
    books.hand(NAME, 101, 3)
    books.hand(NAME, 102, 3)
    d = books.open_digest(NAME, 3, [102]).digest
    assert books.release(NAME) == ["101", "102"]
    assert state(books) == {m: ("waiting", None) for m in ("100", "101", "102")}
    assert books.digest(d)["outcome"] == "released"


def test_an_agent_n_release_deletes_its_rows(books):
    books.hand("agent-3", 101, 3)
    assert books.release("agent-3", ephemeral=True) == []
    assert books.rows("agent-3") == []


def test_the_boot_fence_puts_every_open_row_back_and_closes_open_digests(books):
    books.hand(NAME, 101, 3)
    books.hand(OTHER, 102, 4)
    d = books.open_digest(OTHER, 4, [102]).digest
    assert books.fence() == (2, 1)
    assert state(books) == {"101": ("waiting", None)}
    assert state(books, OTHER) == {"102": ("waiting", None)}
    assert books.digest(d)["outcome"] == "fenced"


def test_a_fault_inside_the_fence_lands_nothing(books):
    books.hand(NAME, 101, 3)
    books.open_digest(NAME, 3, [101])
    faulted(books, "UPDATE digests SET ended_at", books.fence)


def test_a_fault_inside_a_release_lands_nothing(books):
    books.hand(NAME, 101, 3)
    books.open_digest(NAME, 3, [101])
    faulted(books, "UPDATE digests SET ended_at", lambda: books.release(NAME))


def test_the_books_serve_many_threads_on_one_connection(books):
    errors = []

    def work(n):
        try:
            for i in range(50):
                mid = n * 1000 + i
                books.hand(NAME, mid, n)
                d = books.open_digest(NAME, n, [mid]).digest
                books.turn_ended(NAME, [d], [])
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(1, 7)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and books.rows(NAME) == []


def test_read_cursor_reads_from_outside_the_daemon(books, tmp_path):
    books.hand(NAME, 101, 3)
    books.turn_ended(NAME, [books.open_digest(NAME, 3, [101]).digest], [])
    got = books_mod.read_cursor(NAME, path=tmp_path / "comms.db")
    assert got["cursor"] == "101"
    assert books_mod.read_cursor(OTHER, path=tmp_path / "comms.db") is None

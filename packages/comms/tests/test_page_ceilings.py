"""comms' page's reads against their ceilings, over a full record.

The record holds what the local backend keeps: 90 days of mail at 500 messages a day.
The books' `dropped` table is never trimmed, so it holds a row for each message of a
year of mail, most of them for messages the record has since trimmed. Over that:

- the first read of a thread between two names, and of all mail with one name, each
  within 5 s, a sixth of the proxy's 30 s to a read's first byte;
- the refresh of all mail with one name (`after` and `oldest`), which reads the books'
  rows of every recipient of the messages the page holds, within the same 5 s;
- `/api/messages` with its `earlier`, and the read of the day its cursor names, each
  within 1 s.

Each read goes through loopback's strict app, as the page server answers it, and is
timed as the best of three: load from outside the test only ever adds to a read's time.
The test prints every time it measures, with the machine's load beside it.
"""

from __future__ import annotations

import os
import random
import sys
import time

import pytest

from moxi.store import transaction
from moxi_comms import machine_line, pagereads, store
from moxi_comms.local_record import LocalRecord
from moxi_comms.paths import comms_db
from moxi_comms.provision import NAMES, provision
from moxi_comms.record import AUTHOR_IDENTITY, AUTHOR_OPERATOR
from moxi_comms.store.schema import DROP_REASONS, META_NEXT_ID

from conftest import FakeClock, build_store
from test_page_messages import fresh_log  # noqa: F401 -- a fixture, found by pytest by name
from test_page_messages import read as read_messages
from test_page_thread import read as read_thread

pytestmark = [
    pytest.mark.skipif(not sys.platform.startswith("linux"),
                       reason="comms runs on Linux only"),
    pytest.mark.slow,
    pytest.mark.timed,
]

DAYS = 90
PER_DAY = 500
#: The days of mail the books' `dropped` table holds a row for, one row a message.
DROPPED_DAYS = 365
THREAD_CEILING_S = 5.0
MESSAGES_CEILING_S = 1.0
TRIES = 3

OPERATOR_ID = "900001"
MAIN = "main@proj-a"
PLANNER = "planner@proj-a"
#: Every agent address that writes or is written to. Each `main` and `planner` spells
#: the two names a thread is read for, so the scan parses their line zero too.
AGENTS = [f"{name}@{scope}"
          for scope in ("proj-a", "proj-b", "proj-c")
          for name in ("main", "planner", "builder-1", "builder-2", "reviewer", "tester")]
OTHERS = [a for a in AGENTS if a not in (MAIN, PLANNER)]
WORDS = ("the build is green again after the fix to the reader so take the next slice "
         "when you can and tell me what the check says about the page and its reads ")


def body(rng: random.Random, to: list[str], **kw) -> str:
    size = rng.randint(40, 800)
    text = (WORDS * (size // len(WORDS) + 1))[:size]
    return machine_line.compose(to, text, **kw)


def one_message(rng: random.Random) -> tuple[str, str, str]:
    """(author kind, author, content) of one message of a busy box: a share between
    `main` and `planner`, a share with `main` and others, the operator's, prose, and
    the rest among the other names."""
    roll = rng.random()
    posture = rng.choice(machine_line.POSTURES)
    if roll < 0.12:
        by, to = rng.choice([(MAIN, PLANNER), (PLANNER, MAIN)])
        return AUTHOR_IDENTITY, by, body(rng, [to], posture=posture)
    if roll < 0.25:
        other = rng.sample(OTHERS, rng.randint(1, 2))
        if rng.random() < 0.5:
            return AUTHOR_IDENTITY, MAIN, body(rng, other, posture=posture)
        return AUTHOR_IDENTITY, other[0], body(rng, [MAIN, *other[1:]], posture=posture)
    if roll < 0.28:
        return AUTHOR_OPERATOR, OPERATOR_ID, body(rng, [rng.choice(AGENTS)])
    if roll < 0.31:
        return AUTHOR_IDENTITY, rng.choice(AGENTS), body(rng, ["operator"], posture="once")
    if roll < 0.33:
        return AUTHOR_IDENTITY, rng.choice(AGENTS), "just words, no line"
    by = rng.choice(OTHERS)
    to = rng.sample([a for a in OTHERS if a != by], rng.randint(1, 3))
    return AUTHOR_IDENTITY, by, body(rng, to, posture=posture)


@pytest.fixture
def full_record():
    """The seeded store, with the record closed: {first, last} message ids."""
    rng = random.Random(90)
    clock = FakeClock()
    now = clock.t
    record = LocalRecord(clock=clock)
    try:
        prov = provision(record, comms_db(),
                         standing=[tuple(a.split("@")) for a in AGENTS],
                         operator=(OPERATOR_ID, "operator"), store=build_store("local"))
        room = prov.rooms[NAMES]
        step = 86400 / PER_DAY
        trimmed = (DROPPED_DAYS - DAYS) * PER_DAY
        sent: list[tuple[int, list[str]]] = []
        with record._write() as conn:
            # The ids of the year's mail the record has trimmed, gone before the first.
            conn.execute("UPDATE meta SET value = value + ? WHERE key = ?",
                         (trimmed, META_NEXT_ID))
            first_trimmed = int(conn.execute("SELECT value FROM meta WHERE key = ?",
                                             (META_NEXT_ID,)).fetchone()[0]) - trimmed + 1
            start = now - DAYS * 86400
            for i in range(DAYS * PER_DAY):
                clock.t = start + (i + 0.5) * step
                kind, by, content = one_message(rng)
                msg = record._insert(conn, channel_id=room, author_id=by, author_kind=kind,
                                     author_display="operator" if kind == AUTHOR_OPERATOR
                                     else by, content=content)
                line = machine_line.parse(content)
                sent.append((int(msg.id), list(line.recipients) if line else []))
    finally:
        record.close()
    first, last = sent[0][0], sent[-1][0]
    stamp = pagereads.stamp(now - DAYS * 86400)
    dropped = [(mid, rng.choice(AGENTS), rng.choice(DROP_REASONS), stamp)
               for mid in range(first_trimmed, last + 1)]
    # The last day's mail still waits for some of its recipients.
    queued = [(rng.choice(to), mid, stamp) for mid, to in sent[-PER_DAY:]
              if to and rng.random() < 0.5]
    conn = store.open_write()
    try:
        with transaction(conn):
            conn.executemany("INSERT OR IGNORE INTO dropped(message_id, name, reason, at) "
                             "VALUES (?,?,?,?)", dropped)
            conn.executemany("INSERT OR IGNORE INTO queue(name, message_id, queued_at) "
                             "VALUES (?,?,?)", queued)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        counted = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                   for table in ("messages", "dropped", "queue")}
    finally:
        conn.close()
    assert counted["messages"] == DAYS * PER_DAY
    assert counted["dropped"] == DROPPED_DAYS * PER_DAY
    return {"first": first, "last": last, "counted": counted}


def best_of(read, **query) -> tuple[float, list[float], float, dict]:
    """The best of `TRIES` reads' seconds, every try's, the one-minute load before the
    first, and the last answer."""
    load = os.getloadavg()[0]
    times = []
    for _ in range(TRIES):
        began = time.perf_counter()
        answer = read(**query)
        times.append(time.perf_counter() - began)
    return min(times), times, load, answer


def test_the_page_reads_answer_within_their_ceilings_over_a_full_record(full_record):
    measured: list[tuple[str, float, list[float], float, float]] = []

    def timed(what, ceiling, read, **query) -> dict:
        best, times, load, answer = best_of(read, **query)
        assert answer["state"] == "up", (what, answer)
        measured.append((what, best, times, load, ceiling))
        return answer

    pair = timed("thread, first read", THREAD_CEILING_S, read_thread, a=MAIN, b=PLANNER)
    assert len(pair["messages"]) == pagereads.THREAD_LIMIT
    assert pair["earlier"]["count"] > DAYS * PER_DAY // 20

    everyone = timed("all mail with one name, first read", THREAD_CEILING_S, read_thread,
                     a=MAIN)
    assert len(everyone["messages"]) == pagereads.THREAD_LIMIT
    assert everyone["earlier"]["count"] > pair["earlier"]["count"]

    # The page refreshes above the newest message it holds, with the rows from the
    # oldest it holds.
    held = [m["message_id"] for m in everyone["messages"]]
    refreshed = timed("all mail with one name, refresh", THREAD_CEILING_S, read_thread,
                      a=MAIN, after=held[-1], oldest=held[0])
    assert refreshed["changed"] and refreshed["earlier"] is None

    listed = timed("messages with earlier", MESSAGES_CEILING_S, read_messages)
    cursor = listed["earlier"]
    assert cursor is not None and cursor["count"] > (DAYS - 2) * PER_DAY * 0.9
    day = timed("messages, the day earlier names", MESSAGES_CEILING_S, read_messages,
                day=cursor["day"], before=cursor["before"])
    assert day["messages"]

    lines = [f"seeded: {full_record['counted']}"]
    lines += [f"{what}: best {best:.3f}s of {', '.join(f'{t:.3f}' for t in times)}"
              f" (ceiling {ceiling:.0f}s, load {load:.2f})"
              for what, best, times, load, ceiling in measured]
    print("\n" + "\n".join(lines))
    over = [what for what, best, _, _, ceiling in measured if best > ceiling]
    assert not over, "over the ceiling: " + "; ".join(lines)

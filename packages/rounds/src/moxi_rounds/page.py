"""rounds' page: the routes its page server answers, declared as data.

    GET /api/rounds   the board: every open round as lines of its steps, what waits on
                      the operator, the builds last acted on and the closed rounds, with
                      the server's `now`
    GET /api/record   `?what=&round=&column=&stage=&id=`: one marker's record, one waiting
                      item's or one round's card, from the last gather; `gone` is true,
                      and `record` null, for a round no longer open or an item no longer
                      waiting
    GET /api/events   the stream; its one topic, `rounds`, is the board without `now`, so
                      time passing alone publishes nothing

Every route is a read and writes nothing, and none is the gateway's. A path is marked
`path` and free text that may hold one is marked `scrub`, so a later gateway route is a
change of flags alone.

`/api/record` answers 200 with `gone` for a round it holds no part of: loopback has no
handler-level 404, and the page says the round is no longer open.

The browser UI shell starts the page server on the first request for rounds' page, and
loopback stops it when it has been idle. Only that server and loopback's export command
import this module, each in a process of its own: nothing in rounds' announcement, API,
CLI or listeners does. rounds' distribution doesn't require ui, which comes with the
`ui` extra, so this module reaches loopback, ui's page-server base, only through
`moxi.modules.load("ui", ...)` and raises `ImportError` when that fails.
"""

from __future__ import annotations

from typing import Any

from moxi import modules

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"rounds' page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback


def _s(mark: str | None = None) -> Any:
    return lb.Out("string", mark=mark, nullable=True)


def _list(items: Any) -> Any:
    return lb.Out("list", items=items)


TEXT = lb.Out("string")
INT = lb.Out("int")
BOOL = lb.Out("bool")
WORDS = _list(TEXT)

#: One step's cell: its state (one of the board's eight), its short word, the count of
#: the operator's items, whether it is settled or where its line is, rounds' own state
#: word for the step, who does it and, when known, that one's name. A null cell is a
#: column the round has no step in.
CELL = lb.Out("table", nullable=True, items={
    "state": TEXT, "word": _s(), "count": lb.Out("int", nullable=True),
    "settled": BOOL, "here": BOOL, "step_state": _s(), "by": _s(), "who": _s(),
})

#: A "where it is" phrase: its parts in order, each with its weight (`yours`, `bad`,
#: `waits`, `moves` or `quiet`).
PHRASE = _list(lb.Out("table", items={"text": lb.Out("string", mark="scrub"),
                                       "weight": TEXT}))

COLUMN = lb.Out("table", items={"id": TEXT, "label": TEXT, "part": TEXT})

OPTION = lb.Out("table", items={"key": TEXT, "text": lb.Out("string", mark="scrub")})

#: A question or a decision as what waits on the operator gives it. `blocks` is the
#: stages it holds; `blocks_word` is `all` or `none` where the item says that in place of
#: a list.
#: The keys after `at` are present only where the item has them.
ENTRY = lb.Out("table", items={
    "holder": TEXT, "id": TEXT, "text": _s("scrub"), "from": _s("scrub"),
    "blocks": WORDS, "blocks_word": _s(), "state": _s(), "by": _s(), "at": _s(),
    "builder": _s(), "left_as": _s(), "until": _s(), "options": _list(OPTION),
    "moved_from": _s(), "stage": _s(), "weight": _s(),
    "forecloses": lb.Out("bool", nullable=True), "alone": lb.Out("bool", nullable=True),
    "not_put": _s(),
})

#: A job for the operator: the round that holds it, its id (`B<n>.W<k>`), its stage and
#: text, the report that asked for it, who wrote that and when.
WAIT = lb.Out("table", items={
    "round": TEXT, "id": TEXT, "stage": _s(), "text": _s("scrub"),
    "report": _s("scrub"), "by": _s(), "at": _s(),
})

#: A ruling not yet shown to the operator, as a project's read-back list holds it.
SHOWN = lb.Out("table", items={
    "holder": TEXT, "id": TEXT, "text": _s("scrub"), "rules": _s(), "record": _s("scrub"),
    "supersedes": _s(), "revokes": _s(), "at": _s(),
})

WAITING = lb.Out("table", items={
    "questions": _list(ENTRY), "decisions": _list(ENTRY), "waits": _list(WAIT),
    "not_shown": _list(lb.Out("table", items={"project": TEXT, "through": _s(),
                                              "rows": _list(SHOWN)})),
    "parked": INT,
})

#: One build of the strip: its round and stage, the line of its commission, whether the
#: round has closed, its act in words and when, its builder and branch, and its stage's
#: steps with their cells.
RECENT = lb.Out("table", items={
    "round": TEXT, "project": TEXT, "stage": TEXT, "build": INT, "closed": BOOL,
    "act_at": _s(),
    "act": lb.Out("string", mark="scrub"), "builder": _s(), "branch": _s(),
    "steps": WORDS, "cells": _list(CELL),
})

LINE = lb.Out("table", items={
    "kind": TEXT, "stage": _s(), "branch": _s(), "fold": _s(), "phrase": PHRASE,
    "cells": _list(CELL),
})

FOLDS = lb.Out("table", items={
    "more": INT, "landed": INT,
    "not_started": lb.Out("table", items={"n": INT, "ready": INT, "held": INT,
                                          "child": INT}),
})

#: One open round: what its show says of it, the step `next` reads for it and the
#: rounds listing's counts, its group, phrase, how many of the operator's items it holds,
#: its newest event, a short hash of the whole (`rev`), its lines and its folds.
ROUND = lb.Out("table", items={
    "name": TEXT, "project": TEXT, "slug": TEXT, "description": _s("scrub"),
    "flow": _s(), "standing": _s(), "version": lb.Out("int", nullable=True),
    "step": lb.Out("table", nullable=True, items={"step": TEXT, "stage": _s(),
                                                  "state": TEXT}),
    "counts": lb.Out("table", items={"open": lb.Out("int", nullable=True),
                                     "parked": lb.Out("int", nullable=True),
                                     "not_shown": lb.Out("int", nullable=True)}),
    "group": TEXT, "phrase": PHRASE, "mine": INT, "last_at": _s(), "rev": TEXT,
    "lines": _list(LINE), "folds": FOLDS,
})

CLOSED = lb.Out("table", items={"name": TEXT, "project": TEXT, "flow": _s(),
                                "ended_at": _s(), "how": _s()})

UNREADABLE = lb.Out("table", items={"name": TEXT, "reason": lb.Out("string", mark="scrub"),
                                    "fix": _s("scrub")})

#: The board, as the route and the topic carry it. `state` is `up`, or `error` when the
#: reads every row needs failed, with the failure whole in `detail`.
BOARD = {
    "today": TEXT, "home": _s("path"), "read_at": TEXT, "state": TEXT,
    "detail": _s("scrub"), "columns": _list(COLUMN), "waiting": WAITING,
    "recent": _list(RECENT), "rounds": _list(ROUND), "closed": _list(CLOSED),
    "unreadable": _list(UNREADABLE),
}

EVENT = lb.Out("table", items={"n": lb.Out("int", nullable=True), "at": _s(), "by": _s(),
                               "type": _s(), "words": _s(), "of": _s("scrub")})

FINDINGS = lb.Out("table", items={"blocker": INT, "major": INT, "minor": INT,
                                  "note": INT})

#: A step's record: its head (round, column, stage, the cell's state and word) and each
#: section, empty or null when it has nothing. Keys of a card and of a waiting item's
#: record follow; a record holds only its own kind's.
RECORD = lb.Out("table", nullable=True, items={
    # a step's record
    "round": TEXT, "column": TEXT, "stage": _s(), "state": _s(), "word": _s(),
    "next": lb.Out("table", nullable=True, items={
        "state": TEXT, "by": _s(), "who": _s(),
        "waits": _list(lb.Out("table", items={"reason": lb.Out("string", mark="scrub"),
                                              "fix": _s("scrub")})),
        "then": _list(lb.Out("string", mark="scrub")),
        "reads": _list(lb.Out("string", mark="scrub")), "write": _s("scrub"),
        "questions": WORDS}),
    "facts": _list(lb.Out("table", items={"key": TEXT,
                                          "value": lb.Out("string", mark="scrub")})),
    "verdicts": _list(lb.Out("table", items={
        "version": lb.Out("int", nullable=True), "verdict": _s(), "pass": _s(),
        "at": _s(), "by": _s(), "model": _s(), "report": _s("scrub"),
        "findings": FINDINGS, "open": lb.Out("int", nullable=True)})),
    "findings": lb.Out("table", nullable=True, items={
        "open": _list(lb.Out("table", items={
            "id": TEXT, "severity": _s(), "state": _s(), "text": _s("scrub"),
            "step": _s(), "raised": lb.Out("int", nullable=True),
            "set_by": lb.Out("int", nullable=True)})),
        "rest": INT}),
    "ci": lb.Out("table", nullable=True, items={
        "commit": _s(), "at": _s(), "reading": _s(),
        "jobs": _list(lb.Out("table", items={"name": _s(), "reading": _s(), "url": _s(),
                                             "workflow": _s()}))}),
    "runs": _list(lb.Out("table", items={
        "step": _s(), "exit_code": lb.Out("int", nullable=True),
        "duration_s": lb.Out("float", nullable=True), "commit": _s(), "at": _s(),
        "output": _s("path")})),
    "drift": lb.Out("table", nullable=True, items={
        "why": _s("scrub"), "owed": BOOL, "moved": lb.Out("int", nullable=True),
        "meeting": _list(lb.Out("string", mark="path")),
        "shared": _list(lb.Out("string", mark="path"))}),
    "gates": lb.Out("table", nullable=True, items={
        "unmet": _list(lb.Out("table", items={"number": lb.Out("int", nullable=True),
                                              "reason": lb.Out("string", mark="scrub"),
                                              "fix": _s("scrub")})),
        "actions": _list(lb.Out("table", items={"id": TEXT, "kind": _s(), "state": _s(),
                                                "landing": _s(), "n": INT, "at": _s()}))}),
    "decisions": lb.Out("table", nullable=True, items={"n": INT, "majors": INT,
                                                       "unruled": INT}),
    "items": WORDS,
    "settled": _list(lb.Out("table", items={"id": TEXT, "text": _s("scrub"),
                                            "chose": _s(), "ruling": _s("scrub")})),
    "close": _list(lb.Out("string", mark="scrub")),
    "fixes": _list(lb.Out("table", items={"id": TEXT, "since": _s()})),
    "events": _list(EVENT),
    "rev": _s(),
    # a round's card
    "name": TEXT, "project": TEXT, "description": _s("scrub"), "flow": _s(),
    "opened": _s(), "standing": _s(),
    "stages": lb.Out("table", items={"finished": INT, "building": INT,
                                     "not_started": INT}),
    "rulings": INT,
    "newest_rulings": _list(lb.Out("table", items={"id": TEXT, "text": _s("scrub"),
                                                   "at": _s()})),
    "phrase": PHRASE,
    "ended": lb.Out("table", nullable=True, items={"how": _s(), "at": _s()}),
    "ends": _list(lb.Out("table", items={"stage": TEXT, "how": TEXT})),
    # a waiting item's record: the item as what waits on the operator has it, the command
    # that records an answer today, and a question's passage
    "what": TEXT,
    "item": lb.Out("table", items={
        **ENTRY.items, **WAIT.items, **SHOWN.items, "project": _s(), "ruling": _s("scrub"),
        "chose": _s(), "holder": _s(), "round": _s(), "id": TEXT}),
    "answer": lb.Out("string", mark="scrub"),
    "passage": _s("scrub"),
})

#: The longest value the record route's query takes, in characters.
QUERY_MAX = 200

#: The record route's query: what the record is of (`step`, `card`, `question`,
#: `decision`, `wait` or `shown`), the round (`<project>/<slug>`, or the project alone
#: for a project-level item), and the column and stage of a step or the id of an item.
#: The handler checks which of these `what` needs.
RECORD_QUERY = {key: lb.Field("string", required=False, max=QUERY_MAX)
                for key in ("what", "round", "column", "stage", "id")}

PAGE = lb.Page(
    routes=(
        lb.Route("/api/rounds", "read", "moxi_rounds.pagehandlers:rounds",
                 returns={"now": lb.Out("float"), **BOARD}),
        lb.Route("/api/record", "read", "moxi_rounds.pagehandlers:record",
                 query=RECORD_QUERY, returns={"gone": BOOL, "record": RECORD}),
        lb.Route("/api/events", "read", "moxi_rounds.pagehandlers:events", stream=True,
                 topics={"rounds": lb.Out("table", items=BOARD)}),
    ),
    assets="web",
)

__all__ = ["BOARD", "CELL", "PAGE", "QUERY_MAX", "RECORD", "RECORD_QUERY"]

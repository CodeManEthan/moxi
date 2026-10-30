"""comms.db's schema: the steps `moxi.store.migrate` walks, one per `user_version`.

Version 1 is the whole store. Each table names what it holds; the rules about who writes
which table live in `store/__init__.py`.

Times are the record's own spelling, `%Y-%m-%dT%H:%M:%SZ` in UTC, which sorts as it
reads, so a containment test is a string comparison. The one exception is
`messages.expires_at`, a whole Unix second, the same number line zero's `[expires:...]`
token carries, so the store compares it with the clock without parsing.

Ids are integers from `ID_BASE` that only grow, drawn from `meta.next_id`. Rooms,
principals and messages share the counter.
"""

from __future__ import annotations

#: The counter's floor. Larger than every id an older record issued (ids of that era
#: stay below it until 2049), and well inside `INTEGER PRIMARY KEY`'s signed 64 bits, so
#: no id issued here sorts before an older one.
ID_BASE = 2 ** 62

#: `meta` keys.
META_NEXT_ID = "next_id"
#: Set by the move of comms 0.2's files: "0.2", with the time and the moved files'
#: fingerprint beside it. A store that has it is never moved into again.
META_MIGRATED_FROM = "migrated_from"
#: The conversion markers comms 0.2 kept in its record. The move of 0.2's files carries
#: them; on a fresh store the daemon's first start finds nothing older and sets them.
META_AUTHOR_STAMPS = "author_stamps"
META_QUEUE_MIGRATED = "queue_migrated"

#: The values a column may take, where the schema checks them.
QUEUE_STATES = ("waiting", "handed", "in_digest")
BINDING_KINDS = ("session", "reserved")
IDENTITY_KINDS = ("agent", "send-only")
IDENTITY_STATUSES = ("active", "retired")
RESERVED_STATES = ("active", "conflict", "unowned")
DROP_REASONS = ("expired", "withdrawn")
ROOM_KINDS = ("names",)


def _among(column: str, values) -> str:
    return f"CHECK ({column} IN ({', '.join(repr(v) for v in values)}))"


V1 = f"""
CREATE TABLE meta (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL
);
INSERT INTO meta(key, value) VALUES ('{META_NEXT_ID}', '{ID_BASE}');

-- The record's channels and the directory's rooms, one row per room. A room the record
-- made has no kind until provisioning names it.
CREATE TABLE rooms (
    room_id      TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    kind         TEXT {_among("kind", ROOM_KINDS).replace("CHECK (", "CHECK (kind IS NULL OR ")},
    created_at   TEXT NOT NULL
);

CREATE TABLE messages (
    id             INTEGER PRIMARY KEY,
    room_id        TEXT NOT NULL,
    author_id      TEXT NOT NULL,
    author_kind    TEXT NOT NULL,      -- identity or user (the operator's lane)
    author_display TEXT NOT NULL,
    content        TEXT NOT NULL,
    timestamp      TEXT NOT NULL,
    interrupt      INTEGER NOT NULL DEFAULT 0 CHECK (interrupt IN (0, 1)),
    expires_at     INTEGER,            -- Unix second; NULL: the message never expires
    withdrawn_at   TEXT                -- NULL: not withdrawn
);
CREATE INDEX messages_room ON messages(room_id, id);
CREATE INDEX messages_expiry ON messages(expires_at) WHERE expires_at IS NOT NULL;

-- One row per (recipient name, message) until the message is acked or dropped.
CREATE TABLE queue (
    name           TEXT NOT NULL,
    message_id     INTEGER NOT NULL,
    queued_at      TEXT NOT NULL,
    held           INTEGER NOT NULL DEFAULT 0 CHECK (held IN (0, 1)),
    state          TEXT NOT NULL DEFAULT 'waiting' {_among("state", QUEUE_STATES)},
    binding_id     INTEGER,            -- the binding a handed row went to
    digest_id      INTEGER,            -- the digest an in_digest row is in
    digested_once  INTEGER NOT NULL DEFAULT 0 CHECK (digested_once IN (0, 1)),
    PRIMARY KEY (name, message_id)
);
CREATE INDEX queue_message ON queue(message_id);
CREATE INDEX queue_digest ON queue(digest_id) WHERE digest_id IS NOT NULL;

-- One row per digest an adapter opened: the ids it kept, its text, and how its turn went.
CREATE TABLE digests (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    binding_id     INTEGER,
    opened_at      TEXT NOT NULL,
    ids            TEXT NOT NULL,      -- a JSON list of message ids
    text           TEXT NOT NULL,
    started_at     TEXT,
    ended_at       TEXT,
    outcome        TEXT
);
CREATE INDEX digests_open ON digests(name) WHERE ended_at IS NULL;

-- Each name's delivery cursor: the highest id acked, clamped below its lowest live row.
CREATE TABLE cursors (
    name           TEXT PRIMARY KEY,
    message_id     INTEGER NOT NULL,
    updated_at     TEXT NOT NULL
);

-- The binding history: who held a name, when. A row is history, never a live binding.
CREATE TABLE bindings (
    id             INTEGER PRIMARY KEY,
    qualified      TEXT NOT NULL,      -- the match-gate compares this to the display
    name           TEXT NOT NULL,
    scope          TEXT NOT NULL,
    tier           TEXT NOT NULL,      -- the tier a verified match reports
    adapter        TEXT NOT NULL,
    bound_at       TEXT NOT NULL,
    released_at    TEXT,               -- NULL while the binding is live
    fenced         INTEGER NOT NULL DEFAULT 0 CHECK (fenced IN (0, 1)),
    kind           TEXT NOT NULL DEFAULT 'session' {_among("kind", BINDING_KINDS)}
);
CREATE INDEX bindings_identity ON bindings(name, scope);
CREATE INDEX bindings_open ON bindings(id) WHERE released_at IS NULL;

-- Standing identities, and retired ones.
CREATE TABLE identities (
    name           TEXT NOT NULL,
    scope          TEXT NOT NULL DEFAULT 'global',
    status         TEXT NOT NULL DEFAULT 'active' {_among("status", IDENTITY_STATUSES)},
    kind           TEXT NOT NULL DEFAULT 'agent' {_among("kind", IDENTITY_KINDS)},
    created_at     TEXT NOT NULL,
    PRIMARY KEY (name, scope)
);

CREATE TABLE operators (
    user_id        TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    token_hash     TEXT
);

-- Send-only names a module declared, and the names that couldn't be reserved.
CREATE TABLE reserved (
    name           TEXT PRIMARY KEY,
    module         TEXT,               -- NULL for an unowned row
    token_hash     TEXT,
    state          TEXT NOT NULL {_among("state", RESERVED_STATES)},
    reserved_at    TEXT,
    freed_at       TEXT,
    note           TEXT
);

-- What left a name's queue without being delivered, and why.
CREATE TABLE dropped (
    message_id     INTEGER NOT NULL,
    name           TEXT NOT NULL,
    reason         TEXT NOT NULL {_among("reason", DROP_REASONS)},
    at             TEXT NOT NULL,
    PRIMARY KEY (message_id, name)
);

CREATE TABLE principals (
    principal_id   TEXT PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE
);
CREATE TABLE denied (
    principal_id   TEXT NOT NULL,
    channel_id     TEXT NOT NULL,
    PRIMARY KEY (principal_id, channel_id)
);
"""

#: The steps, oldest first; the store's `user_version` counts those run.
STEPS = (V1,)

#: The tables version 1 makes, in the order above.
TABLES = ("meta", "rooms", "messages", "queue", "digests", "cursors", "bindings",
          "identities", "operators", "reserved", "dropped", "principals", "denied")

__all__ = ["ID_BASE", "META_NEXT_ID", "META_MIGRATED_FROM", "META_AUTHOR_STAMPS",
           "META_QUEUE_MIGRATED", "QUEUE_STATES", "BINDING_KINDS",
           "IDENTITY_KINDS", "IDENTITY_STATUSES", "RESERVED_STATES", "DROP_REASONS",
           "ROOM_KINDS", "STEPS", "TABLES", "V1"]

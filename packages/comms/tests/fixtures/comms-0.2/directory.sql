BEGIN TRANSACTION;
CREATE TABLE agents (   -- standing identities, and retired ones
    name        TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'global',
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'agent' CHECK (kind IN ('agent','send-only')),
    PRIMARY KEY (name, scope)
);
INSERT INTO "agents" VALUES('reviewer','proj-a','active','2026-10-30T18:15:16+00:00','agent');
INSERT INTO "agents" VALUES('planner','global','active','2026-10-30T18:15:16+00:00','agent');
INSERT INTO "agents" VALUES('herald','global','active','2026-10-30T18:15:16+00:00','send-only');
INSERT INTO "agents" VALUES('old','proj-a','retired','2026-10-30T18:15:16+00:00','agent');
CREATE TABLE meta (     -- e.g. 'writer' -> the one named directory writer
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
INSERT INTO "meta" VALUES('writer','provisioner','2026-10-30T18:15:16+00:00');
CREATE TABLE operators (
    user_id     TEXT PRIMARY KEY,     -- the operator's user id
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
INSERT INTO "operators" VALUES('pr-4611686018427387906','operator','2026-10-30T18:15:16+00:00');
CREATE TABLE rooms (    -- no 'inbox' kind; rooms have no owner
    channel_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL CHECK (kind IN ('names')),
    created_at  TEXT NOT NULL
);
INSERT INTO "rooms" VALUES('ch-4611686018427387905','#names','names','2026-10-30T18:15:16+00:00');
COMMIT;

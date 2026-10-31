BEGIN TRANSACTION;
CREATE TABLE bindings (
    id           INTEGER PRIMARY KEY,
    qualified    TEXT NOT NULL,     -- the match-gate compares THIS to the display
    name         TEXT NOT NULL,
    scope        TEXT NOT NULL,
    tier         TEXT NOT NULL,     -- the tier a verified match reports
    adapter      TEXT NOT NULL,
    bound_at     TEXT NOT NULL,     -- %Y-%m-%dT%H:%M:%SZ, UTC
    released_at  TEXT,              -- NULL while the binding is LIVE
    fenced       INTEGER NOT NULL DEFAULT 0   -- closed by a boot fence, not by a release
);
INSERT INTO "bindings" VALUES(1,'reviewer@proj-a','reviewer','proj-a','standing','claude-shim','2026-10-30T18:05:16Z','2026-10-30T18:10:16Z',0);
INSERT INTO "bindings" VALUES(2,'planner','planner','global','standing','claude-shim','2026-10-30T18:13:16Z',NULL,0);
COMMIT;

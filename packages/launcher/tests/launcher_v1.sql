-- A launch log at schema version 1, as the version 1 store wrote it (its insert, state
-- writes, claim and re-claim, learned ids, events and picks), dumped with iterdump.
-- The migration tests load it and open it with the current store.
BEGIN TRANSACTION;
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  request_id TEXT,
  session_id TEXT,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail_json TEXT NOT NULL
);
INSERT INTO "events" VALUES(1,'r-slot','a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d','2026-10-29T11:00:00.000000Z','reclaimed','{"endpoint":"4194903@1761735600.000000","was":"4194902@1761728401.500000"}');
INSERT INTO "events" VALUES(2,'r-slot','3c1d2e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f','2026-10-29T09:00:00.000000Z','screen','{"screen":"trust","outcome":"answered"}');
CREATE TABLE launches (
  request_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  harness TEXT NOT NULL,
  preset_kind TEXT CHECK (preset_kind IN ('seat','project') OR preset_kind IS NULL),
  preset_name TEXT,
  target TEXT NOT NULL CHECK (target IN ('terminal','headless','slot')),
  slot TEXT,
  pane_id TEXT,
  cwd TEXT NOT NULL,
  project_key TEXT,
  identity TEXT,
  scope TEXT,
  session_id TEXT,
  values_json TEXT NOT NULL,
  argv_json TEXT NOT NULL,
  env_json TEXT NOT NULL,
  posture TEXT NOT NULL,
  brief TEXT,
  handoff_path TEXT,
  relaunch_of TEXT,
  resume_of TEXT,
  pid INTEGER,
  pid_started REAL,
  endpoint TEXT,
  state TEXT NOT NULL,
  reason TEXT,
  updated_at TEXT NOT NULL
);
INSERT INTO "launches" VALUES('r-slot','2026-10-29T09:00:00.000000Z','someone','claude','seat','lead','slot','3','%7','/work/one',NULL,'worker-a','global','3c1d2e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f','{"model":"opus","director":["lead"]}','["claude","--session-id","3c1d2e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"]','{"MOXI_LAUNCHER_REQUEST":"r-slot","MOXI_TURNS_COMMANDS":"exit"}','operator','Do the thing.','/work/one/handoffs/h1.md',NULL,NULL,4194901,1761728400.25,'4194903@1761735600.000000','first-sent',NULL,'2026-10-29T11:00:00.000000Z');
INSERT INTO "launches" VALUES('r-codex','2026-10-29T12:00:00.000000Z','someone','codex',NULL,NULL,'terminal',NULL,NULL,'/work/two',NULL,NULL,NULL,'thread-1','{"mode":"host"}','["moxi-turns-codex-host","--cwd","/work/two"]','{"MOXI_LAUNCHER_REQUEST":"r-codex"}','go',NULL,NULL,NULL,NULL,NULL,NULL,'4194904@1761739200.000000','spawning',NULL,'2026-10-29T12:00:00.000000Z');
INSERT INTO "launches" VALUES('r-bare','2026-10-29T13:00:00.000000Z','someone','codex',NULL,NULL,'headless',NULL,NULL,'/work/three',NULL,NULL,NULL,NULL,'{"mode":"exec"}','["codex","exec","go"]','{"MOXI_LAUNCHER_REQUEST":"r-bare"}','go',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'failed','exited','2026-10-29T13:00:00.000000Z');
INSERT INTO "launches" VALUES('r-head','2026-10-29T14:00:00.000000Z','other','claude',NULL,NULL,'headless',NULL,NULL,'/work/four',NULL,NULL,NULL,'d4e5f6a7-b8c9-4d0e-8f1a-2b3c4d5e6f7a','{"model":"sonnet"}','["claude","-p","x"]','{}','go',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'spawning',NULL,'2026-10-29T14:00:00.000000Z');
CREATE TABLE picks (
  path TEXT NOT NULL,
  key TEXT NOT NULL,
  session_id TEXT,
  request_id TEXT,
  how TEXT NOT NULL CHECK (how IN ('given','shown')),
  at TEXT NOT NULL
);
INSERT INTO "picks" VALUES('/work/one/handoffs/h1.md','seat:lead','3c1d2e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f','r-slot','given','2026-10-29T09:00:00.000000Z');
INSERT INTO "picks" VALUES('/work/one/handoffs/h0.md','seat:lead','3c1d2e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f','r-slot','shown','2026-10-29T09:00:00.000000Z');
CREATE TABLE session_ids (
  session_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL REFERENCES launches,
  source TEXT NOT NULL,
  seen_at TEXT NOT NULL
);
INSERT INTO "session_ids" VALUES('3c1d2e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f','r-slot','minted','2026-10-29T09:00:00.000000Z');
INSERT INTO "session_ids" VALUES('7e8f9a0b-1c2d-4e3f-9a5b-6c7d8e9f0a1b','r-slot','clear','2026-10-29T10:00:00.000000Z');
INSERT INTO "session_ids" VALUES('a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d','r-slot','compact','2026-10-29T10:00:00.000000Z');
INSERT INTO "session_ids" VALUES('thread-1','r-codex','sender','2026-10-29T12:00:00.000000Z');
INSERT INTO "session_ids" VALUES('thread-2','r-codex','clear','2026-10-29T12:00:00.000000Z');
INSERT INTO "session_ids" VALUES('d4e5f6a7-b8c9-4d0e-8f1a-2b3c4d5e6f7a','r-head','minted','2026-10-29T14:00:00.000000Z');
CREATE INDEX launches_session_id ON launches(session_id);
CREATE INDEX launches_created_at ON launches(created_at);
CREATE INDEX events_request_at ON events(request_id, at);
CREATE INDEX events_session_at ON events(session_id, at);
CREATE INDEX picks_key_at ON picks(key, at);
COMMIT;
PRAGMA user_version = 1;

BEGIN TRANSACTION;
CREATE TABLE channels  (channel_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL);
INSERT INTO "channels" VALUES('ch-4611686018427387905','#names','2026-10-30T18:15:16Z');
CREATE TABLE denied    (principal_id TEXT NOT NULL, channel_id TEXT NOT NULL,
                        PRIMARY KEY (principal_id, channel_id));
CREATE TABLE messages  (id INTEGER PRIMARY KEY, channel_id TEXT NOT NULL,
                        author_id TEXT NOT NULL, author_kind TEXT NOT NULL,
                        author_display TEXT NOT NULL, content TEXT NOT NULL,
                        timestamp TEXT NOT NULL);
INSERT INTO "messages" VALUES(4611686018427387907,'ch-4611686018427387905','planner','identity','planner','::to reviewer@proj-a [no reply]
the first, handled','2026-10-30T18:15:16Z');
INSERT INTO "messages" VALUES(4611686018427387908,'ch-4611686018427387905','planner','identity','planner','::to reviewer@proj-a, planner [held] [no reply]
held for both','2026-10-30T18:15:16Z');
INSERT INTO "messages" VALUES(4611686018427387909,'ch-4611686018427387905','planner','identity','planner','::to reviewer@proj-a [no reply]
handed live, never handled','2026-10-30T18:15:16Z');
INSERT INTO "messages" VALUES(4611686018427387910,'ch-4611686018427387905','reviewer@proj-a','identity','reviewer@proj-a','::to planner [no reply]
to planner, never handled','2026-10-30T18:15:16Z');
INSERT INTO "messages" VALUES(4611686018427387911,'ch-4611686018427387905','planner','identity','planner','::to agent-3 [no reply]
to an ephemeral name','2026-10-30T18:15:16Z');
INSERT INTO "messages" VALUES(4611686018427387912,'ch-4611686018427387905','planner','identity','planner','::to herald [no reply]
to a send-only name','2026-10-30T18:15:16Z');
INSERT INTO "messages" VALUES(4611686018427387913,'ch-4611686018427387905','pr-4611686018427387906','user','operator','::to planner
from the operator','2026-10-30T18:15:16Z');
CREATE TABLE meta      (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "meta" VALUES('next_id','4611686018427387913');
INSERT INTO "meta" VALUES('operator_token_hash','c4f99fd308ab770a32842806104621272ea041cd4ffbfb7d69ed3146b470bef2');
INSERT INTO "meta" VALUES('author_stamps','1');
INSERT INTO "meta" VALUES('queue_migrated','1793384116');
CREATE TABLE principals(principal_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE);
INSERT INTO "principals" VALUES('pr-4611686018427387906','operator');
CREATE TABLE queue     (message_id INTEGER NOT NULL, name TEXT NOT NULL,
                        PRIMARY KEY (name, message_id));
INSERT INTO "queue" VALUES(4611686018427387908,'reviewer@proj-a');
INSERT INTO "queue" VALUES(4611686018427387908,'planner');
CREATE INDEX messages_channel ON messages(channel_id, id);
COMMIT;

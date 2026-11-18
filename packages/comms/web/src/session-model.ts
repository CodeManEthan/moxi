// What sessions says of a bound address's session, where launcher says nothing of where it
// runs, worked out with no DOM so the Node tests can drive it. With sessions installed the
// page reads sessions' `/api/session` once for each live row that has a session id and a
// harness (the harness comms' kinds give its adapter kind) and that launcher gives no where:
// with launcher installed, only once launcher's read has answered. A read that gives a header
// is kept; one that gives nothing is asked again after `AGAIN_MS`. The session's header
// then gives line one's dim where, `<folder> · started <time> · <harness>`, and the opened
// row's `Session` line, its name (else its folder) the same way, with `Sessions` linking to
// its transcript. A read that fails, or a header that names neither a folder nor a start,
// gives nothing: no gap, no word.
//
// Every string here goes into the page as text.

import type { RosterRow } from "./feed.ts";
import type { Launched } from "./launched-model.ts";
import type { Clock } from "./mail-model.ts";
import { LOCAL } from "./mail-model.ts";
import { whenWords } from "./words.ts";

/** A session to ask sessions for: the harness and the session id. */
export interface SessionKey {
  harness: string;
  session: string;
}

/** What the page shows of sessions' `/api/session`. `folder` is the path the session's
 * record names, `started` its start in epoch seconds, `name` one typed for it in the
 * harness; each null where sessions has none. */
export interface SessionHeader {
  harness: string;
  session: string;
  name: string | null;
  folder: string | null;
  started: number | null;
}

/** One session's read: still out, or answered at `at` (epoch ms) with a header or with
 * nothing to show (any status but 200, or a value that isn't one). */
export type SessionRead = { state: "out" } | { state: "read"; header: SessionHeader | null; at: number };

/** How long a read that gave nothing waits before the session is asked again: a session
 * just bound may not be in sessions' list yet. A read that gave a header is kept. */
export const AGAIN_MS = 30_000;

/** Whether `read` is due: never asked, or answered with nothing `AGAIN_MS` ago or more. */
export function isDue(read: SessionRead | undefined, nowMs: number): boolean {
  if (read === undefined) return true;
  return read.state === "read" && read.header === null && nowMs - read.at >= AGAIN_MS;
}

/** The key a session's read is held under. */
export function keyOf(key: SessionKey): string {
  return `${key.harness}/${key.session}`;
}

/** sessions' `/api/session` path for one session. */
export function sessionPath(key: SessionKey): string {
  return `api/session?harness=${encodeURIComponent(key.harness)}&session=${encodeURIComponent(key.session)}`;
}

/** The session a roster row's session id and harness name; null for a row that isn't live or
 * lacks either. */
export function sessionOf(row: RosterRow): SessionKey | null {
  if (!row.live || typeof row.harness !== "string" || row.harness === "") return null;
  if (typeof row.session_id !== "string" || row.session_id === "") return null;
  return { harness: row.harness, session: row.session_id };
}

/** The sessions to read for `rows`: each live row's that launcher gives no where (`launched`,
 * by qualified name; null without launcher), once each, in the rows' order. */
export function wantedSessions(rows: readonly RosterRow[], launched: ReadonlyMap<string, Launched> | null)
  : SessionKey[] {
  const seen = new Set<string>();
  const out: SessionKey[] = [];
  for (const row of rows) {
    if (launched?.get(row.qualified)?.where) continue;
    const key = sessionOf(row);
    if (key === null || seen.has(keyOf(key))) continue;
    seen.add(keyOf(key));
    out.push(key);
  }
  return out;
}

const text = (value: unknown): string | null => (typeof value === "string" && value !== "" ? value : null);

/** `/api/session`'s value for `key`, or null when it isn't one of that session. */
export function parseSessionHeader(data: unknown, key: SessionKey): SessionHeader | null {
  if (typeof data !== "object" || data === null || Array.isArray(data)) return null;
  const value = data as Record<string, unknown>;
  if (value.harness !== key.harness || value.session !== key.session) return null;
  const started = typeof value.started === "number" && Number.isFinite(value.started) && value.started > 0
    ? value.started : null;
  return { harness: key.harness, session: key.session, name: text(value.name), folder: text(value.folder), started };
}

/** A folder's last segment, as launcher's where names one: never a path. */
export function folderWord(folder: string): string {
  const parts = folder.split(/[\\/]+/).filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : folder;
}

/** `started today 15:50`, `started yesterday 21:15`, `started Nov 3 08:52`; the time alone
 * after `started` with no `today` held. */
export function startedWords(at: number, today: string | null, clock: Clock = LOCAL): string {
  if (today !== null && clock.day(at) === today) return `started today ${clock.time(at)}`;
  return `started ${whenWords(at, today, clock)}`;
}

/** The pieces after the first: when it started, and its harness. */
function rest(header: SessionHeader, today: string | null, clock: Clock): string[] {
  return [...(header.started !== null ? [startedWords(header.started, today, clock)] : []), header.harness];
}

/** Line one's dim where from the session's header: `<folder> · started <time> · <harness>`;
 * null for no header, and for one that names neither a folder nor a start. */
export function sessionWhere(header: SessionHeader | null, today: string | null, clock: Clock = LOCAL)
  : string | null {
  if (header === null || (header.folder === null && header.started === null)) return null;
  return [...(header.folder !== null ? [folderWord(header.folder)] : []), ...rest(header, today, clock)].join(" · ");
}

/** The opened row's `Session` line from the header: its name, else its folder, then when it
 * started and its harness, with the route of its transcript on the Sessions page; null where
 * `sessionWhere` is. */
export function sessionLine(header: SessionHeader | null, today: string | null, clock: Clock = LOCAL)
  : { value: string; route: string } | null {
  if (sessionWhere(header, today, clock) === null || header === null) return null;
  const first = header.name ?? (header.folder !== null ? folderWord(header.folder) : null);
  return {
    value: [...(first !== null ? [first] : []), ...rest(header, today, clock)].join(" · "),
    route: [header.harness, header.session].map(encodeURIComponent).join("/"),
  };
}

/** The header read for a live row, or null: none read, still out, or nothing to show. */
export function headerOf(reads: ReadonlyMap<string, SessionRead>, row: RosterRow | null): SessionHeader | null {
  const key = row === null ? null : sessionOf(row);
  if (key === null) return null;
  const read = reads.get(keyOf(key));
  return read?.state === "read" ? read.header : null;
}

// Whether a recipient's session has a turn open, from turns, worked out with no DOM so the
// Node tests can drive it. With turns installed the page reads turns' `/api/sessions` while
// a send box is open. A session's turn is open when its last facts say so, and those facts
// speak for the session id they were read under; a bound roster row joins them by its own
// session id. A row with no session, a session turns doesn't list, facts not yet read, a
// read that failed, or turns not installed: nothing is known, and the dry-run line says
// nothing of it.

import type { RosterRow } from "./feed.ts";

/** The route the page reads the sessions from, relative to turns' page. */
export const TURNS_PATH = "api/sessions";

/** The session ids whose turn is open, or null when the value isn't turns' list. */
export function openTurns(data: unknown): ReadonlySet<string> | null {
  if (typeof data !== "object" || data === null || Array.isArray(data)) return null;
  const { sessions } = data as Record<string, unknown>;
  if (!Array.isArray(sessions)) return null;
  const open = new Set<string>();
  for (const session of sessions) {
    if (typeof session !== "object" || session === null) continue;
    const { facts } = session as Record<string, unknown>;
    if (typeof facts !== "object" || facts === null) continue;
    const { session_id, turn_open } = facts as Record<string, unknown>;
    if (turn_open === true && typeof session_id === "string" && session_id !== "") open.add(session_id);
  }
  return open;
}

/** Whether the address `qualified` is held by a session whose turn is open: its bound row's
 * session id is among `open`. Always false without a read. */
export function workingIn(rows: readonly RosterRow[], open: ReadonlySet<string> | null): (qualified: string) => boolean {
  if (open === null || open.size === 0) return () => false;
  return (qualified) => rows.some((row) =>
    row.qualified === qualified && row.live && row.session_id !== null && open.has(row.session_id));
}

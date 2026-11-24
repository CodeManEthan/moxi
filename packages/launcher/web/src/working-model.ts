// What turns says of the launcher's running launches on the Projects page: which of them
// are working now, worked out with no DOM so the Node tests can drive it. With turns
// installed, at the desk, the `project-facts` component reads turns' `/api/sessions` and
// follows its topic `sessions`; a running launch is working while the one turns session
// that carries its session id has a turn open.
//
// The read and the pairing are the Sessions page's for its running rows, and the rule is
// turns' own: its Live sessions panel puts a session in its `working` group while
// `facts.turn_open` is true. A launch turns has no session for, or shares one with another
// launch, says nothing, and neither does any launch while turns isn't read or its last
// read failed.

import type { RunningEntry } from "./running-model.ts";

/** The route the component reads turns' sessions from, relative to turns' page. */
export const LIVE_PATH = "api/sessions";
/** The topic of turns' stream that carries the same list. */
export const LIVE_TOPIC = "sessions";
/** A running line's word while its session's turn is open. */
export const WORKING = "working";

/** One session turns can reach: the fields the component reads. */
export interface LiveSession {
  key: string;
  session_id: string | null;
  /** Whether a turn is open; null until turns' first read of the session answers. */
  turn_open: boolean | null;
}

/** The value of turns' `/api/sessions` and of its topic `sessions`. */
export interface Live {
  sessions: LiveSession[];
}

function isTable(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseSession(value: unknown): LiveSession | null {
  if (!isTable(value) || typeof value.key !== "string") return null;
  const facts = isTable(value.facts) && typeof value.facts.turn_open === "boolean" ? value.facts : null;
  return {
    key: value.key,
    session_id: typeof value.session_id === "string" ? value.session_id : null,
    turn_open: facts === null ? null : facts.turn_open as boolean,
  };
}

/** The route's or the topic's value, or null for anything else. A session with no text
 * `key` is left out. */
export function parseLive(data: unknown): Live | null {
  if (!isTable(data) || typeof data.listed !== "boolean" || !Array.isArray(data.sessions)) return null;
  return { sessions: data.sessions.map(parseSession).filter((one): one is LiveSession => one !== null) };
}

/**
 * The turns session of each running launch, by request id: only where one launch and one
 * turns session carry the same session id. A launch with no session id pairs with none.
 */
export function pairsOf(entries: readonly RunningEntry[], live: Live): Map<string, LiveSession> {
  const count = (ids: (string | null)[]): Map<string, number> => {
    const seen = new Map<string, number>();
    for (const id of ids) if (id) seen.set(id, (seen.get(id) ?? 0) + 1);
    return seen;
  };
  const launches = count(entries.map((entry) => entry.session_id));
  const sessions = count(live.sessions.map((session) => session.session_id));
  const paired = new Map<string, LiveSession>();
  for (const entry of entries) {
    const id = entry.session_id;
    if (!id || launches.get(id) !== 1 || sessions.get(id) !== 1) continue;
    paired.set(entry.request_id, live.sessions.find((session) => session.session_id === id) as LiveSession);
  }
  return paired;
}

/** The request ids of the running launches whose session turns says is working: a turn is
 * open. `entries` is every running launch, so a session id two launches carry pairs with
 * neither. */
export function workingOf(entries: readonly RunningEntry[], live: Live): Set<string> {
  const working = new Set<string>();
  for (const [request, session] of pairsOf(entries, live)) if (session.turn_open === true) working.add(request);
  return working;
}

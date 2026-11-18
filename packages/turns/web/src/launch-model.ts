// What launcher says of the sessions it runs, joined to turns' rows, worked out with no DOM
// so the Node tests can drive it. With launcher installed (and not on the phone) the panel
// reads launcher's `/api/running`, then its topic `running`. A launch names a row only when
// the session id pairs them one to one: exactly one running entry and exactly one turns
// row carry it. When two of either carry it (a launch resumed while another process holds
// the session, a `--resume` by hand of a launched session), every row with that id keeps
// its folder name. An entry with no id names no row. A read that fails or answers `error`
// names none.
//
// A joined row takes the entry's name as launcher gives it, its fallback included, and
// `whereText` as its dim where, so Home's running row and this panel name a launch in the
// same words. `whereText` is launcher's own, copied here; a test holds them equal.
//
// Every string here goes into the page as text.

import type { Session } from "./feed.ts";
import type { Launch } from "./rows-model.ts";

/** Where a running launch runs. `folder` is its directory's last segment, never a path. */
export interface Where {
  target: string;
  slot: string | null;
  project_key: string | null;
  folder: string;
}

/** One launch running now: the fields the panel reads. */
export interface RunningEntry {
  session_id: string | null;
  name: string;
  where: Where;
}

/** The value of launcher's `/api/running` and of its topic `running`. */
export interface Running {
  state: "up" | "error";
  sessions: RunningEntry[];
}

const isTable = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const text = (value: unknown): string | null => (typeof value === "string" ? value : null);

function parseEntry(value: unknown): RunningEntry | null {
  if (!isTable(value) || typeof value.request_id !== "string" || typeof value.name !== "string") return null;
  const where = isTable(value.where) ? value.where : {};
  return {
    session_id: text(value.session_id),
    name: value.name,
    where: { target: text(where.target) ?? "", slot: text(where.slot), project_key: text(where.project_key),
             folder: text(where.folder) ?? "" },
  };
}

/** The route's or the topic's value, or null for anything else. An entry that is not one
 * is left out. */
export function parseRunning(data: unknown): Running | null {
  if (!isTable(data) || (data.state !== "up" && data.state !== "error") || !Array.isArray(data.sessions)) {
    return null;
  }
  const sessions = data.sessions.map(parseEntry).filter((entry): entry is RunningEntry => entry !== null);
  return { state: data.state, sessions };
}

/** Where a session runs, as launcher's row says it: `slot N`, `terminal` or `headless`,
 * then its project's key, else its folder. Launcher's own words, copied. */
export function whereText(where: Where): string {
  const runs = where.target === "slot" ? (where.slot ? `slot ${where.slot}` : "slot") : where.target;
  const place = where.project_key || where.folder;
  return [runs, place].filter(Boolean).join(" · ");
}

/** The launch that names each turns row, by the row's key: only where one entry and one
 * row carry the same session id. */
export function launchesOf(running: Running | null, sessions: readonly Session[]): Map<string, Launch> {
  const joined = new Map<string, Launch>();
  if (running === null || running.state !== "up") return joined;
  const count = (ids: (string | null)[]): Map<string, number> => {
    const seen = new Map<string, number>();
    for (const id of ids) if (id) seen.set(id, (seen.get(id) ?? 0) + 1);
    return seen;
  };
  const entries = count(running.sessions.map((entry) => entry.session_id));
  const rows = count(sessions.map((session) => session.session_id));
  for (const session of sessions) {
    const id = session.session_id;
    if (!id || entries.get(id) !== 1 || rows.get(id) !== 1) continue;
    const entry = running.sessions.find((one) => one.session_id === id) as RunningEntry;
    joined.set(session.key, { name: entry.name, where: whereText(entry.where) || null });
  }
  return joined;
}

// What turns' `project-facts` component reports to the Projects page, as plain data, so the
// Node tests check it directly. projects' README, "Facts on the Projects page", is the
// contract it fills.
//
// turns knows each session's folder, not its project, so it names no project: its report
// carries `live`, which says, by session claim, whether each session it can read is in a
// turn. The Projects page joins that onto the lines that claim the same session.
//
// Every fact comes from `/api/sessions` (or its topic `sessions`, which carries the same
// list). A read that failed, was refused, found turns gone or can't be parsed reports
// null, as does a list whose first scan hasn't finished. A session is in `live` only when
// turns can say: it has a harness and a session id, its facts were read under that same
// id, they are one moment (not `stale`), and no run of unread readings is going on. Two
// sessions with one claim are both left out. `in_turn` is the facts' `turn_open`, and
// `state` the panel's word for it (`stateOf`).

import type { Feed, Session, SessionList } from "./feed.ts";
import { stateOf } from "./rows-model.ts";

/** The event a facts component dispatches, with its report as the `detail`. */
export const FACTS_EVENT = "moxi-project-facts";
/** The contract's version. */
export const VERSION = 1;
/** Where turns' facts sit among other modules'. */
export const ORDER = 20;
/** How often the component reads `/api/sessions` while the document shows. */
export const READ_MS = 30_000;
/** The most sessions `live` carries; past it the oldest are left out. */
export const LIVE_MAX = 100;

/** What turns says of one session: whether it is in a turn now, and its word for that. */
export interface Live {
  in_turn: boolean;
  state: string;
}

export interface Report {
  version: number;
  order: number;
  /** Always empty: turns names sessions, not projects. */
  projects: Record<string, never>;
  /** By claim, `<harness>/<session id>`, newest first. */
  live: Record<string, Live>;
}

/** The route's value while its last read gave one, else null. */
export function goodValue<V>(feed: Pick<Feed<V>, "value" | "failure" | "gone">): V | null {
  return feed.failure === null && !feed.gone ? feed.value : null;
}

const isText = (value: unknown): value is string => typeof value === "string" && value !== "";

/**
 * The session a row names, as the contract spells a claim: `<harness>/<session id>`, the
 * harness as turns spells it (`claude`, `codex`, `opencode`) and the id the harness wrote.
 * Null when either is missing: such a row claims nothing.
 */
export function claimOf(session: Session): string | null {
  return isText(session.harness) && isText(session.session_id) ? `${session.harness}/${session.session_id}` : null;
}

/** What turns says of the session, or null when it can't say now. */
export function liveEntry(session: Session): Live | null {
  const facts = session.facts;
  if (claimOf(session) === null || typeof facts !== "object" || facts === null) return null;
  if (facts.session_id !== session.session_id || facts.stale !== false || session.unread_since !== null) return null;
  if (typeof facts.turn_open !== "boolean") return null;
  const state = stateOf(session);
  return state === null ? null : { in_turn: facts.turn_open, state };
}

/** Every session turns can say of, by claim, in the list's order (newest first), at most
 * `LIVE_MAX`; a claim two rows carry is left out. */
export function liveOf(list: SessionList): Record<string, Live> {
  const counts = new Map<string, number>();
  for (const session of list.sessions) {
    const claim = claimOf(session);
    if (claim !== null) counts.set(claim, (counts.get(claim) ?? 0) + 1);
  }
  const live: Record<string, Live> = {};
  let held = 0;
  for (const session of list.sessions) {
    if (held >= LIVE_MAX) break;
    const claim = claimOf(session);
    if (claim === null || counts.get(claim) !== 1) continue;
    const entry = liveEntry(session);
    if (entry === null) continue;
    live[claim] = entry;
    held += 1;
  }
  return live;
}

/** What the component reports for the list: null when there is none or its first scan
 * hasn't finished, else `live`. */
export function factsReport(list: SessionList | null): Report | null {
  if (list === null || !list.listed) return null;
  return { version: VERSION, order: ORDER, projects: {}, live: liveOf(list) };
}

// What an opened agent row shows under its lines, worked out with no DOM. The lines that
// speak for one address (`Session`, `Cache`, `Held`, `Waiting`, `Unread`, `Waits on`, `Details`) speak
// for the address the box writes to: the row's lead, or the address pressed. `Directs` and
// `Directed by` gather the name's addresses. Each line is there only when its fact is:
// `Session` comes with launcher's join (`launched-model.ts`), with the model and effort the
// launch was started with, and where launcher gives none, with sessions' header of the
// session (`session-model.ts`). Launcher's cache read wins over comms' on the `Cache` line as
// it does on the row, and a warm one ends in the next keep-alive where launcher gives one.
// Every string here goes into the page as text.

import type { AgentRow, Ask } from "./agents-model.ts";
import type { RosterRow } from "./feed.ts";
import type { Read } from "./frame-model.ts";
import { keepAliveWords, SESSION_LABEL } from "./launched-model.ts";
import { namesWords } from "./mail-model.ts";
import { byHim } from "./messages-model.ts";
import type { Queue } from "./messages-model.ts";
import { nameOf } from "./words.ts";

export const ADDRESS_LABEL = "Address";
export const CACHE_LABEL = "Cache";
export const CACHE_LINK = "Cache";
export const COLD_TAIL = "the session still runs; mail does not wake it";
export const WAITS_ON = "Waits on";
export const THREAD_LINK = "Thread";
export const DIRECTS = "Directs";
export const DIRECTED_BY = "Directed by";
export const DETAILS = "Details";
export const HELP = "A name is held by one session at a time.";
export const SHOW_ALL = "Show all";
export const COPY_ID = "Copy id";
export const READING = "Reading…";

/** A link at a line's right end. */
export type Link = { kind: "cache" } | { kind: "thread"; a: string; b: string } | { kind: "sessions"; route: string };

/** One line: a dim label, its value, a dim tail and a link at the right. */
export interface Line {
  key: string;
  label: string;
  value: string;
  tail: string | null;
  link: Link | null;
}

/** One message of the address's queue, in full. */
export interface QueueItem {
  message_id: string;
  time: string;
  /** `you`, or the author's name. */
  author: string;
  text: string;
  /** The operator's own, and the books say it can still be taken back. */
  takeable: boolean;
}

/** `Held` (bound) or `Waiting` (unbound) for rows not yet in its session, `Unread` for
 * those in it. */
export interface QueueGroup {
  label: "Held" | "Waiting" | "Unread";
  items: QueueItem[];
}

/** An address the opened row can speak for; `current` is the one it speaks for now. */
export interface Choice {
  qualified: string;
  current: boolean;
}

export interface OpenedAgent {
  /** The row's addresses with mail or a session, when there is more than one. */
  choices: Choice[];
  /** `Session` and `Cache`, before the queue. */
  before: Line[];
  queue: QueueGroup[];
  /** `<n> more`, past the queue's cap. */
  more: string | null;
  /** `Reading…` while the queue's first read is out, or why it can't be read. */
  queueNote: string | null;
  /** `Waits on`, `Directs`, `Directed by`, after the queue. */
  after: Line[];
  /** The `Details` lines, while they are shown; null while folded. */
  details: Line[] | null;
  /** The session id `Copy id` puts on the clipboard, in `Details`. */
  sessionId: string | null;
}

export interface OpenedFacts {
  row: AgentRow;
  to: string;
  /** Every row of the roster the page drew, for `Directs`. */
  roster: readonly RosterRow[];
  asks: readonly Ask[];
  queue: Read<Queue>;
  now: number;
  when: (at: number) => string;
  /** The cache module is installed. */
  hasCache: boolean;
  /** The sessions module is installed: `Session` links to the session's transcript. */
  hasSessions?: boolean;
  /** sessions' `Session` line for the address's session (`session-model.ts`), drawn where
   * launcher gives none; null or absent where sessions gives nothing. */
  session?: { value: string; route: string } | null;
  /** A read that can bring the `Session` line is still out (launcher's running read, or
   * sessions' read of the session): the line is drawn reading `Reading…` till it answers,
   * so its value arriving moves nothing under it. */
  sessionOut?: boolean;
  showsDetails: boolean;
}

const unique = (items: readonly string[]): string[] => [...new Set(items)];

/** The opened row's lines for the address `to`. */
export function openedAgent(facts: OpenedFacts): OpenedAgent {
  const { row, to, when } = facts;
  const address = row.addresses.find((one) => one.qualified === to) ?? null;
  const rosterRow = address?.row ?? null;
  const bound = address?.bound ?? false;

  const choices = unique([row.lead, ...row.note.lines.map((line) => line.qualified), to]);
  const before: Line[] = [];
  const launched = bound ? address?.launched ?? null : null;
  if (launched !== null) {
    const { name, harness, model, effort, route } = launched.session;
    before.push({ key: "session", label: SESSION_LABEL, value: [name, harness, model, effort].filter(Boolean).join(" · "),
                  tail: null,
                  link: facts.hasSessions && route !== null ? { kind: "sessions", route } : null });
  } else if (bound && facts.session) {
    before.push({ key: "session", label: SESSION_LABEL, value: facts.session.value, tail: null,
                  link: facts.hasSessions ? { kind: "sessions", route: facts.session.route } : null });
  } else if (bound && facts.sessionOut) {
    before.push({ key: "session", label: SESSION_LABEL, value: READING, tail: null, link: null });
  }
  const cacheLink: Link | null = facts.hasCache ? { kind: "cache" } : null;
  const cache = rosterRow?.cache ?? null;
  const theirs = launched?.cache ?? null;
  if (theirs !== null) {
    const est = theirs.estimated ? ", est." : "";
    const at = theirs.until !== null ? ` ${when(theirs.until)}` : "";
    const value = theirs.warm ? `warm${at !== "" ? ` until${at}` : ""}${est}` : `cold${at !== "" ? ` since${at}` : ""}`;
    const kept = [theirs.kept, keepAliveWords(theirs.next, facts.now, when)].filter((part) => part !== null).join(", ");
    before.push({ key: "cache", label: CACHE_LABEL, value, tail: theirs.warm ? kept || null : COLD_TAIL, link: cacheLink });
  } else if (bound && cache !== null) {
    const warm = cache.kind === "warm" && cache.until > facts.now;
    before.push({ key: "cache", label: CACHE_LABEL, value: `${warm ? "warm until" : "cold since"} ${when(cache.until)}`,
                  tail: warm ? null : COLD_TAIL, link: cacheLink });
  }

  const queue: QueueGroup[] = [];
  const value = facts.queue.value;
  if (value !== null && value.state === "up") {
    const held: QueueItem[] = [];
    const unread: QueueItem[] = [];
    for (const message of value.messages) {
      const own = message.rows.find((one) => one.qualified === to) ?? message.rows[0];
      if (own === undefined) continue;
      const item = { message_id: message.message_id, time: when(message.at),
                     author: byHim(message) ? "you" : nameOf(message.author), text: message.text,
                     takeable: byHim(message) && own.takeable };
      (own.state === "waiting" ? held : unread).push(item);
    }
    if (held.length > 0) queue.push({ label: bound ? "Held" : "Waiting", items: held });
    if (unread.length > 0) queue.push({ label: "Unread", items: unread });
  }
  let queueNote: string | null = null;
  if (value === null && facts.queue.loading) queueNote = READING;
  const why = facts.queue.failure?.why ?? (value?.state === "error" ? value.detail : null);
  if (why) queueNote = `The mail held for ${to} can't be read: ${why.replace(/\.$/, "")}.`;

  const after: Line[] = [];
  for (const awaited of rosterRow?.awaiting ?? []) {
    const ask = facts.asks.find((one) => one.asker === to && one.awaited === awaited) ?? null;
    const name = nameOf(awaited);
    let tail: string | null = null;
    if (ask !== null) {
      tail = `for one reply to its ${when(ask.at)} message`;
      if (ask.read === false) {
        tail += `, which ${name} has not read`;
        if (facts.hasCache) tail += `: it is held until ${name}'s next turn`;
      }
    }
    after.push({ key: `waits:${awaited}`, label: WAITS_ON, value: name, tail, link: { kind: "thread", a: to, b: awaited } });
  }
  const mine = new Set([row.name, ...row.addresses.map((one) => one.qualified)]);
  const directs = unique(facts.roster.filter((other) => nameOf(other.qualified) !== row.name
    && other.directors.some((director) => mine.has(director))).map((other) => nameOf(other.qualified))).sort();
  if (directs.length > 0) {
    after.push({ key: "directs", label: DIRECTS, value: namesWords(directs),
                 tail: directs.length === 1 ? `${directs[0]} takes direction from ${row.name}` : null, link: null });
  }
  const directors = unique(row.addresses.flatMap((one) => one.row?.directors ?? []).map(nameOf)).sort();
  if (directors.length > 0) {
    after.push({ key: "directed-by", label: DIRECTED_BY, value: namesWords(directors), tail: null, link: null });
  }

  let details: Line[] | null = null;
  if (facts.showsDetails) {
    details = [{ key: "address", label: ADDRESS_LABEL, value: to, tail: null, link: null }];
    const add = (key: string, label: string, text: string | null): void => {
      if (text) details?.push({ key, label, value: text, tail: null, link: null });
    };
    add("adapter", "Adapter", rosterRow?.adapter ?? null);
    add("tier", "Tier", rosterRow?.tier ?? null);
    add("bound-at", "Bound at", bound && rosterRow?.bound_at != null ? when(rosterRow.bound_at) : null);
    add("session", "Session", bound ? rosterRow?.session_id ?? null : null);
  }

  return {
    choices: choices.length > 1 ? choices.map((qualified) => ({ qualified, current: qualified === to })) : [],
    before, queue, more: value !== null && value.more > 0 ? `${value.more} more` : null, queueNote, after, details,
    sessionId: bound ? rosterRow?.session_id ?? null : null,
  };
}

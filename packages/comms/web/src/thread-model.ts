// A thread opened in place, worked out with no DOM: the messages between two addresses (or,
// with one, every message by or to it), oldest first, from `/api/thread`. Its header names
// the pair, how many of its messages are today and who owes one reply; each message says
// who wrote it, when, what it wants back and, while unread, whose next turn it waits for;
// `Show earlier` adds twenty above; the foot writes to either side and copies the newest
// message's id. A refresh reads only what is newer than the newest message held, and the
// books' rows of the messages held, so a message read or taken back since is redrawn.
//
// The reads go through a `get` the page passes in, so the Node tests drive them. Every id
// stays a string and is never worked on as a number; every string here goes into the page
// as text.

import type { ApiResult } from "/m/ui/client.js";

import type { RosterRow } from "./feed.ts";
import { failureWhy } from "./feed.ts";
import { byHim, parseMessages } from "./messages-model.ts";
import type { Message } from "./messages-model.ts";
import type { Delivery } from "./words.ts";
import { readWords, wantsWords } from "./words.ts";

/** How many messages the first read gives, and how many `Show earlier` adds. */
export const THREAD_FIRST = 6;
export const THREAD_MORE = 20;
export const SHOW_EARLIER = "Show earlier";
export const ALL_MAIL_WITH = "All mail with";
export const COPY_LAST_ID = "Copy the last message's id";
export const YOU = "you";
export const TAKEN_BACK = "taken back";

/** The thread's messages before the oldest the read gave. */
export interface ThreadEarlier {
  count: number;
  first_at: number;
}

/** One message's rows in the books, as a refresh sends them. */
export interface Changed {
  message_id: string;
  rows: Delivery[];
}

/** The answer of `/api/thread`. */
export interface ThreadAnswer {
  state: "up" | "error";
  detail: string | null;
  today: string | null;
  messages: Message[];
  count_today: number;
  earlier: ThreadEarlier | null;
  /** On a refresh, the rows of the messages from `oldest` to `after`; null on a first read. */
  changed: Changed[] | null;
}

const isTable = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const isId = (value: unknown): value is string => typeof value === "string" && /^\d+$/.test(value);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const isDay = (value: unknown): value is string => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);

function parseChanged(data: unknown): Changed[] | null {
  if (!Array.isArray(data)) return null;
  const out: Changed[] = [];
  for (const entry of data) {
    if (!isTable(entry) || !isId(entry.message_id) || !Array.isArray(entry.rows)) continue;
    // A message's rows parse as a message's do.
    const [parsed] = parseMessages([{ message_id: entry.message_id, at: 0, author: "", to: [], text: "",
                                      rows: entry.rows }]);
    out.push({ message_id: entry.message_id, rows: parsed?.rows ?? [] });
  }
  return out;
}

/** The answer of `/api/thread`, or null when it isn't one. */
export function parseThread(data: unknown): ThreadAnswer | null {
  if (!isTable(data)) return null;
  const { state, detail, today, messages, count_today, earlier, changed } = data;
  if (state !== "up" && state !== "error") return null;
  if (detail !== null && detail !== undefined && typeof detail !== "string") return null;
  const up = state === "up";
  return {
    state, detail: detail ?? null, today: isDay(today) ? today : null,
    messages: up ? parseMessages(messages) : [],
    count_today: Number.isInteger(count_today) && (count_today as number) > 0 ? count_today as number : 0,
    earlier: up && isTable(earlier) && Number.isInteger(earlier.count) && (earlier.count as number) > 0
      && finite(earlier.first_at) ? { count: earlier.count as number, first_at: earlier.first_at } : null,
    changed: up ? parseChanged(changed) : null,
  };
}

/** The route of a read of `/api/thread`. A refresh names `after`, the newest message held,
 * and `oldest`, the oldest, so the server reads the rows of what the page holds and no more. */
export function threadPath({ a, b, before = null, after = null, oldest = null, limit = null }
  : { a: string; b: string | null; before?: string | null; after?: string | null; oldest?: string | null;
      limit?: number | null }): string {
  const query = new URLSearchParams({ a });
  if (b !== null) query.set("b", b);
  if (before !== null) query.set("before", before);
  if (after !== null) query.set("after", after);
  if (oldest !== null) query.set("oldest", oldest);
  if (limit !== null) query.set("limit", String(limit));
  return `api/thread?${query}`;
}

/** Whether id `a` is below id `b`: by length, then by digits. */
const idBelow = (a: string, b: string): boolean => (a.length !== b.length ? a.length < b.length : a < b);
const byId = (x: Message, y: Message): number =>
  (x.message_id === y.message_id ? 0 : idBelow(x.message_id, y.message_id) ? -1 : 1);

/** What the page holds of a thread: its messages oldest first, those before them, and how
 * many of its messages are today. */
export interface ThreadHeld {
  messages: Message[];
  earlier: ThreadEarlier | null;
  count_today: number;
}

/** The thread after a refresh: the messages above the newest held added, and each held
 * message's rows set from `changed`. Between two addresses `changed` carries their rows
 * alone, so the other recipients' rows are kept; by or to one it carries every row, so a
 * message it does not list has none. */
export function refreshed(held: ThreadHeld, answer: ThreadAnswer, b: string | null, a: string): ThreadHeld {
  const changed = new Map((answer.changed ?? []).map((entry) => [entry.message_id, entry.rows]));
  const pair = new Set([a, ...(b !== null ? [b] : [])]);
  const kept = held.messages.map((message): Message => {
    const rows = changed.get(message.message_id) ?? [];
    if (b === null) return { ...message, rows };
    return { ...message, rows: [...message.rows.filter((row) => !pair.has(row.qualified)), ...rows]
      .sort((x, y) => (x.qualified < y.qualified ? -1 : x.qualified > y.qualified ? 1 : 0)) };
  });
  const ids = new Set(kept.map((message) => message.message_id));
  return { ...held, count_today: answer.count_today,
           messages: [...kept, ...answer.messages.filter((message) => !ids.has(message.message_id))].sort(byId) };
}

/** The thread after `Show earlier`: the messages it read put above, and what is left
 * before them. */
export function withEarlier(held: ThreadHeld, answer: ThreadAnswer): ThreadHeld {
  const ids = new Set(held.messages.map((message) => message.message_id));
  return { ...held, earlier: answer.earlier,
           messages: [...answer.messages.filter((message) => !ids.has(message.message_id)), ...held.messages].sort(byId) };
}

/** Whether a tag names an address: the address itself, or `operator` for one of his. */
const names = (tag: string, side: string, him: ReadonlySet<string>): boolean =>
  tag === side || (tag === "operator" && him.has(side));
/** Whether a message is by an address. */
const isBy = (message: Message, side: string, him: ReadonlySet<string>): boolean =>
  message.author === side || (byHim(message) && him.has(side));

/** Whether a message is between `a` and `b`: by one, with a tag naming the other. */
export function between(message: Message, a: string, b: string, him: ReadonlySet<string>): boolean {
  return (isBy(message, a, him) && message.to.some((tag) => names(tag, b, him)))
    || (isBy(message, b, him) && message.to.some((tag) => names(tag, a, him)));
}

/** What the thread's words read beyond its messages. */
export interface ThreadFacts {
  a: string;
  b: string | null;
  roster: readonly RosterRow[];
  /** The `owed` messages to him: their authors. */
  owedBy: readonly string[];
  /** The addresses that are the operator. */
  him: ReadonlySet<string>;
  cache: boolean;
  when: (at: number) => string;
}

/** One message as the thread draws it: line one's author and the dim rest, then the text. */
export interface ThreadMessage {
  message_id: string;
  author: string;
  /** `· 18:28 · wants one reply · not read yet, held until builder-1's next turn`. */
  rest: string;
  text: string;
}

export interface ThreadView {
  /** `planner and builder-1 · 4 messages today · builder-1 owes one reply`, or `All mail
   * with planner · 9 messages today`. */
  header: string;
  /** `31 messages, back to <time>` after `Show earlier`, and the id it reads below. */
  earlier: { text: string; before: string } | null;
  messages: ThreadMessage[];
  /** `Message <address>`, for each side that is not him. */
  write: string[];
  /** The newest message's id, for `Copy the last message's id`. */
  lastId: string | null;
}

const countWords = (n: number): string => (n === 1 ? "1 message" : `${n} messages`);

/** The words of the thread the page holds. */
export function threadView(held: ThreadHeld, facts: ThreadFacts): ThreadView {
  const { a, b, him, when } = facts;
  const word = (side: string): string => (him.has(side) ? YOU : side);
  const parts = [b === null ? `${ALL_MAIL_WITH} ${word(a)}` : `${word(a)} and ${word(b)}`];
  if (held.count_today > 0) parts.push(`${countWords(held.count_today)} today`);
  const owes = b === null ? null : owesWords(a, b, facts);
  if (owes !== null) parts.push(owes);
  const bound = (qualified: string): boolean => facts.roster.some((row) => row.qualified === qualified && row.live);
  // His own copy has no read state on his page: he is reading it.
  const shown = (row: Delivery): boolean => !him.has(row.qualified)
    && (b === null || row.qualified === a || row.qualified === b);
  const messages = held.messages.map((message): ThreadMessage => {
    const said = message.withdrawn ? [TAKEN_BACK]
      : [...new Set(message.rows.filter(shown).map((row) => readWords(row, { bound: bound(row.qualified),
                                                                             cache: facts.cache, thread: true }))
        .filter((words): words is string => words !== null))];
    const rest = [when(message.at), wantsWords(message.posture, message.reply_to), ...said]
      .filter((part): part is string => part !== null && part !== "");
    return { message_id: message.message_id, author: byHim(message) ? YOU : message.author,
             rest: rest.map((part) => `· ${part}`).join(" "), text: message.text };
  });
  const oldest = held.messages[0]?.message_id ?? null;
  return {
    header: parts.join(" · "),
    earlier: held.earlier !== null && oldest !== null
      ? { text: `${countWords(held.earlier.count)}, back to ${when(held.earlier.first_at)}`, before: oldest } : null,
    messages,
    write: [a, ...(b !== null ? [b] : [])].filter((side) => !him.has(side)),
    lastId: held.messages.length > 0 ? held.messages[held.messages.length - 1].message_id : null,
  };
}

/** Who owes one reply between `a` and `b`: a side the other's `awaiting` names, or him when
 * an `owed` message is the other side's. */
function owesWords(a: string, b: string, facts: ThreadFacts): string | null {
  const awaits = (asker: string, awaited: string): boolean =>
    facts.roster.some((row) => row.qualified === asker && row.awaiting.includes(awaited));
  if (awaits(a, b)) return `${b} owes one reply`;
  if (awaits(b, a)) return `${a} owes one reply`;
  for (const [side, other] of [[a, b], [b, a]]) {
    if (facts.him.has(side) && facts.owedBy.includes(other)) return "you owe one reply";
  }
  return null;
}

export type Get = (path: string) => Promise<ApiResult>;

export interface ThreadOptions {
  get: Get;
  /** Called after every change. */
  onChange: () => void;
  /** The newest message id the page holds in its lists, a cursor for an empty thread's
   * refresh; null when it holds none. */
  newestKnown: () => string | null;
}

/** The reads of one opened thread. */
export class ThreadReads {
  readonly a: string;
  readonly b: string | null;
  held: ThreadHeld | null = null;
  /** The newest `today` a read gave. */
  today: string | null = null;
  /** The first read is out. */
  loading = true;
  /** Why the last read failed, or null. */
  failure: string | null = null;
  /** `Show earlier`'s read is out. */
  showing = false;

  #options: ThreadOptions;
  #closed = false;
  #running: Promise<void> | null = null;
  #again = false;
  /** The cursor a refresh reads above when the thread holds no message. */
  #cursor: string | null = null;

  constructor(a: string, b: string | null, options: ThreadOptions) {
    this.a = a;
    this.b = b;
    this.#options = options;
  }

  close(): void {
    this.#closed = true;
  }

  /** Reads one path; the answer, or null after setting `failure`. */
  async #read(path: string): Promise<ThreadAnswer | null> {
    const result = await this.#options.get(path);
    if (this.#closed) return null;
    const answer = result.status === 200 ? parseThread(result.data) : null;
    if (answer === null || answer.state !== "up") {
      this.failure = answer?.detail ?? failureWhy(result.status);
      return null;
    }
    this.failure = null;
    if (answer.today !== null && (this.today === null || answer.today > this.today)) this.today = answer.today;
    return answer;
  }

  /** The first read, then a refresh above the newest message held; one at a time, and a
   * call while one is out makes one more after it. */
  refresh(): Promise<void> {
    if (this.#running !== null) {
      this.#again = true;
      return this.#running;
    }
    this.#running = (async () => {
      try {
        do {
          this.#again = false;
          await this.#once();
        } while (this.#again && !this.#closed);
      } finally {
        this.#running = null;
      }
    })();
    return this.#running;
  }

  async #once(): Promise<void> {
    const { a, b } = this;
    const held = this.held;
    const newest = held !== null && held.messages.length > 0 ? held.messages[held.messages.length - 1].message_id
      : this.#cursor;
    if (held === null || newest === null) {
      const cursor = this.#options.newestKnown();
      const answer = await this.#read(threadPath({ a, b, limit: THREAD_FIRST }));
      if (this.#closed) return;
      this.loading = false;
      if (answer !== null) {
        this.held = { messages: answer.messages, earlier: answer.earlier, count_today: answer.count_today };
        this.#cursor = cursor;
      }
    } else {
      // Holding none, the page needs no rows: the bound is the newest id itself.
      const oldest = held.messages.length > 0 ? held.messages[0].message_id : newest;
      const answer = await this.#read(threadPath({ a, b, after: newest, oldest }));
      if (this.#closed) return;
      if (answer !== null) this.held = refreshed(this.held ?? held, answer, b, a);
    }
    this.#options.onChange();
  }

  /** `Show earlier`: twenty more above the oldest message held. */
  async showEarlier(): Promise<void> {
    const held = this.held;
    if (this.showing || held === null || held.messages.length === 0 || held.earlier === null) return;
    this.showing = true;
    this.#options.onChange();
    try {
      const answer = await this.#read(threadPath({ a: this.a, b: this.b, before: held.messages[0].message_id,
                                                   limit: THREAD_MORE }));
      if (answer !== null && this.held !== null) this.held = withEarlier(this.held, answer);
    } finally {
      this.showing = false;
      if (!this.#closed) this.#options.onChange();
    }
  }
}

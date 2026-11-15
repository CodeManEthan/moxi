// The Comms page's messages, worked out with no DOM: `Today`, newest first, with `Earlier
// today · N more · Show` under it, and `Before today · N messages, back to <day> · Show`,
// which adds that day under its own label and moves down below it. From `/api/messages`.
// Every id stays a string and is never worked on as a number; every string here goes into
// the page as text.

import type { Read } from "./frame-model.ts";
import { LOCAL, namesWords } from "./mail-model.ts";
import type { Clock } from "./mail-model.ts";
import { byHim, parseMessages } from "./messages-model.ts";
import type { Message } from "./messages-model.ts";
import { dayWord, fullDayWord, nameOf, readWords } from "./words.ts";

/** How many of today's messages the page reads first, and how many each `Show` adds. */
export const FIRST = 12;
export const MORE = 30;
/** The most messages one read of `/api/messages` gives. */
export const MOST = 500;
export const TODAY = "Today";
export const EARLIER_TODAY = "Earlier today";
export const BEFORE_TODAY = "Before today";
export const SHOW = "Show";
export const OPEN = "Open";
export const YOU = "you";
export const TAKEN_BACK = "taken back";

/** Everything before a day, and the cursor that reads the newest of those days. */
export interface Earlier {
  day: string;
  count: number;
  first_at: number;
  before: string;
}

/** The answer of `/api/messages`. */
export interface Messages {
  state: "up" | "error";
  detail: string | null;
  today: string | null;
  day: string | null;
  messages: Message[];
  more: number;
  earlier: Earlier | null;
}

const isId = (value: unknown): value is string => typeof value === "string" && /^\d+$/.test(value);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const isDay = (value: unknown): value is string => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);

function parseEarlier(data: unknown): Earlier | null {
  if (typeof data !== "object" || data === null) return null;
  const { day, count, first_at, before } = data as Record<string, unknown>;
  if (!isDay(day) || !Number.isInteger(count) || (count as number) <= 0 || !finite(first_at) || !isId(before)) return null;
  return { day, count: count as number, first_at, before };
}

/** The answer of `/api/messages`, or null when it isn't one. */
export function parseMessagesAnswer(data: unknown): Messages | null {
  if (typeof data !== "object" || data === null) return null;
  const { state, detail, today, day, messages, more, earlier } = data as Record<string, unknown>;
  if (state !== "up" && state !== "error") return null;
  if (detail !== null && detail !== undefined && typeof detail !== "string") return null;
  return {
    state, detail: detail ?? null, today: isDay(today) ? today : null, day: isDay(day) ? day : null,
    messages: state === "up" ? parseMessages(messages) : [],
    more: Number.isInteger(more) && (more as number) > 0 ? more as number : 0,
    earlier: state === "up" ? parseEarlier(earlier) : null,
  };
}

/** Whether id `a` is below id `b`: both strings of digits, compared by length, then by
 * their digits. */
export function idBelow(a: string, b: string): boolean {
  return a.length !== b.length ? a.length < b.length : a < b;
}

/** The route of a read of `/api/messages`. */
export function messagesPath({ day = null, before = null, limit }: { day?: string | null; before?: string | null;
                                                                     limit: number }): string {
  const query = new URLSearchParams();
  if (day !== null) query.set("day", day);
  if (before !== null) query.set("before", before);
  query.set("limit", String(Math.min(Math.max(limit, 1), MOST)));
  return `api/messages?${query}`;
}

/** One message row: line one `<author>` and, dim, `to <recipients>` with each recipient's
 * read state; its time at the right; the text on line two, on one line; `Open` opens the
 * thread between the author and its first recipient. */
export interface MessageRow {
  message_id: string;
  name: string;
  where: string;
  time: string;
  text: string;
  thread: { a: string; b: string } | null;
}

/** A day of messages as a group: its label, its rows, and `N more messages · Show`. */
export interface DayGroup {
  key: string;
  label: string;
  rows: MessageRow[];
  /** `38 more messages`, or null. */
  more: string | null;
  /** The id `Show` reads below, and the day it reads. */
  show: { day: string | null; before: string } | null;
}

export interface MessagesView {
  groups: DayGroup[];
  /** `Before today · 447 messages, back to Mon Oct 12 · Show`, under the groups. */
  before: { label: string; text: string; earlier: Earlier } | null;
}

/** What the page knows of an address when it words a recipient's row. */
export interface Readers {
  bound: (qualified: string) => boolean;
  cache: boolean;
  /** The operator's names: a tag naming one of them reads `you`. */
  him: (tag: string) => boolean;
}

/** `1 more message`, `38 more messages`. */
const moreWords = (n: number): string => (n === 1 ? "1 more message" : `${n} more messages`);
const countWords = (n: number): string => (n === 1 ? "1 message" : `${n} messages`);

/** One message as its row says it. */
export function messageRow(message: Message, readers: Readers, when: (at: number) => string): MessageRow {
  const names = message.to.map((tag) => (readers.him(tag) ? YOU : tag));
  let said: string[];
  if (message.withdrawn) said = [TAKEN_BACK];
  else {
    said = [...new Set(message.rows.map((row) => readWords(row, { bound: readers.bound(row.qualified),
                                                                     cache: readers.cache }))
      .filter((words): words is string => words !== null))];
  }
  const to = names.length > 0 ? `to ${namesWords(names)}` : "";
  const where = [to, said.join("; ")].filter((part) => part !== "").join(" · ");
  const first = message.to[0] ?? null;
  return {
    message_id: message.message_id,
    name: byHim(message) ? YOU : message.author,
    where,
    time: when(message.at),
    text: message.text.replace(/\s+/g, " ").trim(),
    thread: first === null || first === message.author ? null : { a: message.author, b: first },
  };
}

/** A day's label as a group: `Yesterday`, else its weekday and date. */
export function dayLabel(day: string, today: string | null): string {
  return today !== null && dayWord(day, today) === "yesterday" ? "Yesterday" : fullDayWord(day);
}

/** A day the page has read, with the cursor below its oldest message. */
export interface ReadDay {
  day: string;
  messages: Message[];
  more: number;
  /** Everything before this day, from the day's first read. */
  earlier: Earlier | null;
}

/** Joins a day's next page onto what the page holds: the ids already held are not added
 * twice, newest first. */
export function joined(held: readonly Message[], page: readonly Message[]): Message[] {
  const ids = new Set(held.map((message) => message.message_id));
  return [...held, ...page.filter((message) => !ids.has(message.message_id))]
    .sort((x, y) => (x.message_id === y.message_id ? 0 : idBelow(x.message_id, y.message_id) ? 1 : -1));
}

/**
 * The messages as the page draws them. `today` is today's read: its messages and `more`
 * (`Earlier today`), and its `earlier`; `days` are the days before today the page added,
 * newest first, each with its own `more` and the `earlier` read with it. `Today` is absent
 * with no message today; `Before today` stays while there is anything before.
 */
export function messagesView(today: ReadDay | null, days: readonly ReadDay[], readers: Readers,
                             when: (at: number) => string, todayDay: string | null, clock: Clock = LOCAL)
  : MessagesView {
  const groups: DayGroup[] = [];
  const group = (key: string, label: string, read: ReadDay, day: string | null): void => {
    if (read.messages.length === 0) return;
    const oldest = read.messages[read.messages.length - 1].message_id;
    groups.push({ key, label, rows: read.messages.map((message) => messageRow(message, readers, when)),
                  more: read.more > 0 ? moreWords(read.more) : null,
                  show: read.more > 0 ? { day, before: oldest } : null });
  };
  if (today !== null) group("today", TODAY, today, null);
  for (const read of days) group(`day:${read.day}`, dayLabel(read.day, todayDay), read, read.day);
  const last = days.length > 0 ? days[days.length - 1] : today;
  const earlier = last?.earlier ?? null;
  const lastDay = days.length > 0 ? days[days.length - 1].day : null;
  const label = lastDay === null ? BEFORE_TODAY
    : `Before ${dayLabel(lastDay, todayDay) === "Yesterday" ? "yesterday" : fullDayWord(lastDay)}`;
  return {
    groups,
    before: earlier === null ? null
      : { label, text: `${countWords(earlier.count)}, back to ${fullDayWord(clock.day(earlier.first_at))}`, earlier },
  };
}

/** The author of a message the page holds, by id; null when it holds none. */
export function authorIn(reads: ReadonlyArray<ReadDay | null>, messageId: string): string | null {
  for (const read of reads) {
    const found = read?.messages.find((message) => message.message_id === messageId);
    if (found !== undefined) return found.author;
  }
  return null;
}

/** Today's read as a day the page holds, or null while there is none. */
export function todayOf(read: Read<Messages>): ReadDay | null {
  const value = read.value;
  if (value === null || value.state !== "up") return null;
  return { day: value.day ?? "", messages: value.messages, more: value.more, earlier: value.earlier };
}

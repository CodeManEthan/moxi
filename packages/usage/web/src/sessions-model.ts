// Sessions as the page names and lists them, with no DOM: a session's name, the
// Sessions section's two lists (the range's last seven days, then its days before
// those), their headings and the lines for what they do not show yet, and the share
// a row takes of a whole. A session is named from its name, folder and start, never
// from its id, the row's `cwd` or its first request in the range.

import { costWords, countWords, rangeDays, rangeLabel } from "./cost-model.ts";
import type { Range, Sum } from "./cost-model.ts";
import { addDays, daysFrom, isDay, LOCAL, startWord } from "./days.ts";
import type { Clock } from "./days.ts";
import type { Report, SessionRef, SessionRow, Sums } from "./feed.ts";
import { stateKeys } from "./filter-model.ts";
import type { PageState } from "./filter-model.ts";
import { harnessName, percentWords } from "./limits-model.ts";

// --- names ----------------------------------------------------------------------------------

/** Each harness as the Sessions page names one in a session's name. */
const HARNESS_WORDS: Record<string, string> = { claude: "Claude Code", codex: "Codex", opencode: "OpenCode" };

/** A harness as a session's name carries it: `Claude Code`, `Codex`, `OpenCode`. */
export function sessionHarness(harness: string): string {
  return HARNESS_WORDS[harness] ?? harnessName(harness);
}

/** A folder's last segment: `proj-a` for `/work/proj-a/`. */
export function lastSegment(folder: string): string {
  const cut = folder.replace(/\/+$/, "");
  const last = cut.slice(cut.lastIndexOf("/") + 1);
  return last !== "" ? last : folder;
}

/** The first line's two words for a session: its name, and the dim `where` after it. */
export interface SessionName {
  name: string;
  /** The folder's last segment beside a typed name; null otherwise. */
  where: string | null;
}

type Naming = Pick<SessionRef, "harness" | "name" | "folder" | "started">;

const folderOf = (ref: Naming): string | null =>
  (typeof ref.folder === "string" && ref.folder.trim() !== "" ? lastSegment(ref.folder.trim()) : null);
const typedOf = (ref: Naming): string =>
  (typeof ref.name === "string" ? ref.name.replace(/\s+/g, " ").trim() : "");

/**
 * A session's name, by the Sessions page's rule. With a typed name: the name, and the
 * folder's last segment as `where`. Without one: `<folder's last segment> · started
 * <day> <time>`; with no start, the folder's last segment alone; with no folder,
 * `<harness> session` in its place. A session whose record is gone is named the same
 * way from the folder and first request the index holds for it. The start is worded as
 * the Sessions page words it, against `today`: `today 11:00`, `yesterday 22:25`, else
 * the date, `Nov 3 08:52`.
 */
export function sessionName(ref: Naming, today: string | null, clock: Clock = LOCAL): SessionName {
  const folder = folderOf(ref);
  const typed = typedOf(ref);
  if (typed !== "") return { name: typed, where: folder };
  const what = folder ?? `${sessionHarness(ref.harness)} session`;
  if (typeof ref.started !== "number" || !Number.isFinite(ref.started)) return { name: what, where: null };
  return { name: `${what} · started ${startWord(ref.started, today, clock)}`, where: null };
}

/** A row's `where`: its project when projects is installed and the row has one, else
 * the folder's last segment under a typed name, else none. */
export function sessionWhere(row: Naming & Pick<SessionRow, "project">, projects: boolean): string | null {
  if (projects && typeof row.project === "string" && row.project !== "") return row.project;
  return typedOf(row) !== "" ? folderOf(row) : null;
}

// --- the two lists ----------------------------------------------------------------------------

/** The first list's days, and the most rows its first read asks for. */
export const WEEK_DAYS = 7;
export const FIRST_TOP = 10;
/** How many more rows each `Show` reads. */
export const PAGE = 30;
/** The most rows one read of `/api/sessions` sends. */
export const TOP_MAX = 200;

/** A list's days, both included; `since` is null for the days of all time before the
 * first list. */
export interface ListDays {
  since: string | null;
  until: string;
}

/** Which days each list of the opened section holds. */
export interface SessionLists {
  /** The range's last seven days, or the whole range when it is shorter. */
  first: ListDays & { since: string };
  /** The range's days before those; null when there are none. */
  earlier: ListDays | null;
  /** The first list is the seven days ending today, so its words say `this week`. */
  week: boolean;
  today: string;
}

/** The two lists for a range; null when `today` or the range's days are not days. */
export function sessionLists(range: Range, today: string): SessionLists | null {
  if (!isDay(today)) return null;
  const days = rangeDays(range, today);
  const until = days?.until ?? today;
  const since = days?.since ?? null;
  if (!isDay(until) || (since !== null && (!isDay(since) || since > until))) return null;
  const weekStart = addDays(until, 1 - WEEK_DAYS) as string;
  const first = { since: since !== null && since > weekStart ? since : weekStart, until };
  const before = addDays(first.since, -1) as string;
  const earlier = since === null || since <= before ? { since, until: before } : null;
  const week = until === today && daysFrom(first.since, until) === WEEK_DAYS - 1;
  return { first, earlier, week, today };
}

/** `/api/sessions` under the filters in force: over `days`, or over the range itself
 * when `days` is null (the closed row's read). */
export function sessionsPath(state: PageState, today: string, days: ListDays | null, top: number, skip = 0): string {
  const own: [string, string][] = days === null ? [] : [
    ...(days.since === null ? [] : [["since", days.since] as [string, string]]), ["until", days.until]];
  const keys = days === null ? stateKeys(state, today) : [...own, ...stateKeys(state, today, ["since", "until"])];
  keys.push(["top", String(top)]);
  if (skip > 0) keys.push(["skip", String(skip)]);
  return `api/sessions?${new URLSearchParams(keys).toString()}`;
}

/** The first list's heading: `This week, dearest first` for the seven days ending today,
 * `Today, dearest first` for today alone, else the list's own days. */
export function firstHeading(lists: SessionLists): string {
  const { since, until } = lists.first;
  if (lists.week) return "This week, dearest first";
  if (since === until && until === lists.today) return "Today, dearest first";
  return `${daysWords(since, until, lists.today)}, dearest first`;
}

/** Days in words: `Oct 10 to Nov 1`, `Nov 3`, and `up to Nov 1` from no first day. */
function daysWords(since: string | null, until: string, today: string): string {
  if (since === null) return `up to ${rangeLabel({ kind: "custom", since: until, until }, today)}`;
  return rangeLabel({ kind: "custom", since, until }, today);
}

/** One row's place in a list: one per harness and session. A row with no session id is
 * one file, told apart by its folder and first request. */
export function rowKey(row: Pick<SessionRow, "harness" | "session_id" | "cwd" | "folder" | "first_ts">): string {
  return row.session_id !== null ? `${row.harness}/${row.session_id}`
    : `${row.harness}//${row.cwd ?? row.folder ?? ""}/${row.first_ts ?? ""}`;
}

/** A list as the page holds it between reads: its rows in the order shown, how far
 * into the order it has read, and its last answer's counts and total. */
export interface ListRead {
  rows: SessionRow[];
  /** Places of the order read so far: the next `Show` skips them. */
  read: number;
  /** Sessions after the last answer's rows. */
  more: number;
  /** Every session of the list's days. */
  count: number;
  /** The list's whole sums, for what its rows leave out. */
  total: Sums;
}

/** A list from its first answer. */
export function listRead(answer: Report<SessionRow>): ListRead {
  return addPage({ rows: [], read: 0, more: 0, count: 0, total: answer.total }, answer);
}

/** A list with one more answer's rows below what it shows. A row it already shows is
 * dropped: a pass between two reads can move a row to a later place. */
export function addPage(list: ListRead, answer: Report<SessionRow>): ListRead {
  const held = new Set(list.rows.map(rowKey));
  const added = answer.rows.filter((row) => {
    const key = rowKey(row);
    if (held.has(key)) return false;
    held.add(key);
    return true;
  });
  return {
    rows: [...list.rows, ...added],
    read: (answer.skip ?? list.read) + answer.rows.length,
    more: answer.more ?? 0,
    count: answer.count ?? list.count,
    total: answer.total,
  };
}

/** The read a list's `Show` makes: thirty more from where it stopped. */
export function showPath(state: PageState, today: string, days: ListDays, list: ListRead): string {
  return sessionsPath(state, today, days, PAGE, list.read);
}

/** The reads that bring a list back to the depth it was read to, after a pass ended,
 * each at most `TOP_MAX` rows; fold their answers with `listRead` and `addPage`. */
export function depthPages(read: number, first = FIRST_TOP): { top: number; skip: number }[] {
  const pages: { top: number; skip: number }[] = [];
  const depth = Math.max(read, first);
  for (let skip = 0; skip < depth; skip += TOP_MAX) pages.push({ top: Math.min(TOP_MAX, depth - skip), skip });
  return pages;
}

/** What a list's rows leave out of its total: the total less the rows shown. A part
 * that holds tokens with no price or a count the harness did not report is not whole. */
export function restOf(list: ListRead): Pick<Sum, "cost" | "complete"> {
  const add = (pick: (row: SessionRow) => number): number => list.rows.reduce((total, row) => total + pick(row), 0);
  const cost = list.total.cost === null ? null
    : Math.max(0, Math.round((list.total.cost - add((row) => row.cost ?? 0)) * 1e6) / 1e6);
  const unpriced = list.total.unpriced_tokens - add((row) => row.unpriced_tokens);
  const unknown = list.total.unknown_rows - add((row) => row.unknown_rows);
  return { cost, complete: unpriced <= 0 && unknown <= 0 };
}

/** A line under a list's rows with its act. */
export interface ListLine {
  text: string;
  act: "Show";
}

/** `15 more this week, $10.09 between them · Show` under the first list, `15 more,
 * …` when it is not this week's and under the earlier list (`week` false). One left
 * reads `1 more, $0.20`. Null when the list shows every session. */
export function remainder(list: ListRead, week: boolean): ListLine | null {
  if (list.more <= 0) return null;
  const n = list.more.toLocaleString("en-US");
  const cost = costWords(restOf(list));
  const between = list.more === 1 ? cost : `${cost} between them`;
  return { text: `${n} more${week ? " this week" : ""}, ${between}`, act: "Show" };
}

/** `Earlier · 29 sessions, Oct 10 to Nov 1, $20.47 · Show`, from one `top=1` read over
 * the earlier days; over all time, `up to Nov 1`. Null with no earlier days or no
 * session in them. Once shown, the line stands over its rows without the act. */
export function earlierLine(lists: SessionLists, answer: Report<SessionRow> | null): ListLine | null {
  if (lists.earlier === null || answer === null) return null;
  const count = answer.count ?? 0;
  if (count <= 0) return null;
  const days = daysWords(lists.earlier.since, lists.earlier.until, lists.today);
  return { text: `Earlier · ${countWords(count, "session")}, ${days}, ${costWords(answer.total)}`, act: "Show" };
}

// --- shares -----------------------------------------------------------------------------------

/** A row's share of a whole: `34%`, rounded down; `under 1%` above zero and below one.
 * Null when either figure is not known or the whole is not above zero. */
export function shareWords(part: number | null, whole: number | null): string | null {
  if (part === null || whole === null || !Number.isFinite(part) || !Number.isFinite(whole) || whole <= 0 || part < 0) {
    return null;
  }
  return percentWords((part / whole) * 100);
}

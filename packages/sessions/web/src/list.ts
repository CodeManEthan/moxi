// The list of sessions as the page draws it, worked out with no DOM, no Lit and no request
// so the Node tests can read it. Three parts:
//
// - the requests: which query a state of the filters and the find field makes;
// - the kept list: the rows loaded so far, how a newer `/api/list` answer and each
//   `/api/more` answer change them, and which reads a change asks for;
// - the drawing: for the kept list, the groups with their labels and count lines, and for
//   each row the values the row element takes. A piece that is not drawn is null: the
//   view places what it is given and decides nothing about what shows.
//
// Every string here goes into the page as text.

import { charCount, cutAt, fmtCount, HARNESS_LABELS, isHarness, routeOf } from "./views.ts";
import type { Group, ListAnswer, ListRow, MoreAnswer, Root } from "./list-feed.ts";

// -- days and times -------------------------------------------------------------------------

/** How a time becomes words. The list is cut into days by the page server, in its own
 * zone; the page words each day against the `today` an answer carries, and reads a time's
 * own day and clock time through this. */
export interface Zone {
  /** The time of day, as ui shows one. */
  clock(epoch: number): string;
  /** The calendar day a time falls on, `YYYY-MM-DD`. */
  day(epoch: number): string;
  /** The first moment of a calendar day, epoch seconds. */
  start(day: string): number;
}

const two = (n: number): string => String(n).padStart(2, "0");

/** A day as its year, month (1 to 12) and day of the month; null when it isn't one. */
function dayParts(day: string): [number, number, number] | null {
  const found = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  return found === null ? null : [Number(found[1]), Number(found[2]), Number(found[3])];
}

/** This browser's zone and ui's clock format. The page is read at the desk, where the
 * browser and the page server share a zone. */
export const LOCAL: Zone = {
  clock: (epoch) => new Date(epoch * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  day: (epoch) => {
    const d = new Date(epoch * 1000);
    return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
  },
  start: (day) => {
    const parts = dayParts(day);
    return parts === null ? NaN : new Date(parts[0], parts[1] - 1, parts[2]).getTime() / 1000;
  },
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** The calendar day `n` days after `day` (before it, for a negative `n`). */
export function dayAfter(day: string, n: number): string {
  const parts = dayParts(day);
  if (parts === null) return day;
  const d = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2] + n));
  return `${d.getUTCFullYear()}-${two(d.getUTCMonth() + 1)}-${two(d.getUTCDate())}`;
}

/** A day as a date: `Nov 6`, with its year (`Nov 6, 2025`) when that is not `today`'s. */
export function dateWords(day: string, today: string): string {
  const parts = dayParts(day);
  if (parts === null) return day;
  const date = `${MONTHS[parts[1] - 1] ?? parts[1]} ${parts[2]}`;
  return day.slice(0, 4) === today.slice(0, 4) ? date : `${date}, ${parts[0]}`;
}

/** A day inside a sentence: `today`, `yesterday`, else its date. */
export function dayWords(day: string, today: string): string {
  if (day === today) return "today";
  if (day === dayAfter(today, -1)) return "yesterday";
  return dateWords(day, today);
}

/** A day group's label: `Today`, `Yesterday`, else `Fri Nov 6`. */
export function dayLabel(day: string, today: string): string {
  if (day === today) return "Today";
  if (day === dayAfter(today, -1)) return "Yesterday";
  const parts = dayParts(day);
  if (parts === null) return day;
  const weekday = WEEKDAYS[new Date(Date.UTC(parts[0], parts[1] - 1, parts[2])).getUTCDay()];
  return `${weekday} ${dateWords(day, today)}`;
}

/** A time as it stands beside something of the day `on`: its clock time alone when it
 * falls on that day, else with its own day before it (`yesterday 22:50`, `Nov 6 22:10`),
 * so a time never reads as one of a day it was not. */
export function timeWords(at: number, on: string, today: string, zone: Zone = LOCAL): string {
  const day = zone.day(at);
  return day === on ? zone.clock(at) : `${dayWords(day, today)} ${zone.clock(at)}`;
}

// -- the filters and the requests -----------------------------------------------------------

/** The find field sends its text once it holds this many characters. */
export const FIND_MIN = 2;
/** The longest text the find field sends, in characters. */
export const FIND_MAX = 256;
/** How long the find field waits after the last key before it sends, in milliseconds. */
export const FIND_WAIT_MS = 250;
/** How often the list is read again while its tab is visible, in milliseconds. */
export const REFRESH_MS = 30_000;

/** The `When` filter: any time, today, the last 7 or 30 days, or a range of days (either
 * end may be open). */
export type When =
  | { kind: "any" }
  | { kind: "today" }
  | { kind: "last"; days: number }
  | { kind: "range"; from: string | null; to: string | null };

/** The filter line and the find field as the person left them. */
export interface Filters {
  folder: string | null;
  harness: string | null;
  when: When;
  find: string;
}

export const NO_FILTERS: Filters = { folder: null, harness: null, when: { kind: "any" }, find: "" };

/** What a list is asked with: each filter as the route takes it, null when it is not set. */
export interface Query {
  harness: string | null;
  /** Changed at or after it, and before it: epoch seconds. */
  since: number | null;
  until: number | null;
  folder: string | null;
  find: string | null;
}

const usable = (seconds: number): number | null => (Number.isFinite(seconds) ? seconds : null);

/**
 * The query a state of the filters makes on the server's day `today`.
 *
 * `When` is in whole calendar days: `today` is from the day's first moment; the last 7
 * days are today and the six days before it; a range runs from the first moment of its
 * first day to the last of its last. The find field's text is sent trimmed, and only once
 * it holds two characters: shorter, the list is asked without it and stays as it is.
 */
export function queryOf(filters: Filters, today: string, zone: Zone = LOCAL): Query {
  const when = filters.when;
  let since: number | null = null;
  let until: number | null = null;
  if (when.kind === "today") since = usable(zone.start(today));
  if (when.kind === "last") since = usable(zone.start(dayAfter(today, 1 - Math.max(1, when.days))));
  if (when.kind === "range") {
    if (when.from !== null) since = usable(zone.start(when.from));
    if (when.to !== null) until = usable(zone.start(dayAfter(when.to, 1)));
  }
  const typed = filters.find.trim();
  return {
    harness: filters.harness !== null && filters.harness !== "" ? filters.harness : null,
    since,
    until,
    folder: filters.folder !== null && filters.folder !== "" ? filters.folder : null,
    find: charCount(typed) >= FIND_MIN ? cutAt(typed, FIND_MAX) : null,
  };
}

/** Whether a query narrows the list: any filter or the find field is set. */
export function isFiltered(query: Query): boolean {
  return query.harness !== null || query.since !== null || query.until !== null
    || query.folder !== null || query.find !== null;
}

const sameQuery = (a: Query, b: Query): boolean =>
  a.harness === b.harness && a.since === b.since && a.until === b.until && a.folder === b.folder && a.find === b.find;

/** The filters as query keys. A filter that is not set is left out: the routes refuse an
 * empty value. */
function filterKeys(query: Query, folder = true): URLSearchParams {
  const keys = new URLSearchParams();
  if (query.harness !== null) keys.set("harness", query.harness);
  if (query.since !== null) keys.set("since", String(query.since));
  if (query.until !== null) keys.set("until", String(query.until));
  if (folder && query.folder !== null) keys.set("folder", query.folder);
  if (query.find !== null) keys.set("find", query.find);
  return keys;
}

const pathOf = (route: string, keys: URLSearchParams): string => {
  const text = keys.toString();
  return text === "" ? route : `${route}?${text}`;
};

/** `/api/list`'s request for a query. */
export function listPath(query: Query): string {
  return pathOf("api/list", filterKeys(query));
}

/** `/api/folders`' request: the folders under every filter but `folder`. */
export function foldersPath(query: Query): string {
  return pathOf("api/folders", filterKeys(query, false));
}

/** `/api/session`'s request for one session of the list. */
export function sessionPath(harness: string, session: string): string {
  return `api/session?${new URLSearchParams({ harness, session })}`;
}

/** One read of `/api/more`, and how many reads follow it. */
export interface MoreRequest {
  /** `recent`, `found`, `earlier`, `undated` or a day's key. */
  group: string;
  /** `main` or `program` for `recent` and a day; null for `found` and `earlier`, whose
   * sessions are of both kinds. */
  kind: "main" | "program" | null;
  /** The place to read from; null for a day's program sessions from their start. */
  before: string | null;
  /** The `now` of the list answer being paged. */
  now: number;
  /** How many pages to read, this one counted. */
  pages: number;
  /** A read from the start that only checks rows already shown: when its first row is
   * the first one shown, the shown rows stand and nothing more is read. */
  check: boolean;
}

/** `/api/more`'s request: the group, its place, the paged list's `now` and its filters. */
export function morePath(request: MoreRequest, query: Query): string {
  const keys = new URLSearchParams({ group: request.group });
  if (request.kind !== null) keys.set("kind", request.kind);
  if (request.before !== null) keys.set("before", request.before);
  keys.set("now", String(request.now));
  for (const [key, value] of filterKeys(query)) keys.set(key, value);
  return `api/more?${keys}`;
}

// -- the kept list --------------------------------------------------------------------------

/** A day's sessions that a program started, behind their line. */
export interface ProgramState {
  count: number;
  /** The line was opened: its rows show under it. */
  open: boolean;
  rows: ListRow[];
  before: string | null;
  /** How many pages of them were read. */
  pages: number;
}

/** One group of the kept list. */
export interface GroupState {
  key: string;
  /** `recent`, a day: its sessions started in a terminal. `found`: its matches. `earlier`:
   * the sessions not yet shown as days. */
  count: number;
  rows: ListRow[];
  before: string | null;
  /** How many pages were read after the ones the list answer brought. */
  pages: number;
  program: ProgramState | null;
  /** `earlier`: the oldest change among its sessions. */
  oldest: number | null;
  /** A day that `earlier` opened. */
  older: boolean;
}

/** The list as the page keeps it between reads: one row per harness and session. */
export interface ListState {
  query: Query;
  now: number;
  today: string;
  all: number;
  total: number;
  unreadable: number | null;
  roots: Root[];
  harnesses: { harness: string; count: number }[];
  groups: GroupState[];
  /** `earlier` as the list answer gave it, and how many times it was opened since. */
  earlier: { count: number; before: string | null; pages: number } | null;
  /** The rows opened in place, by key. */
  opened: string[];
}

/** A row's key: its harness and its session id. */
export const keyOf = (row: { harness: string; session: string }): string => `${row.harness}/${row.session}`;

const kindOf = (group: string): "main" | null => (group === "found" || group === "earlier" ? null : "main");

/** The same session, unchanged since. */
const sameRow = (a: ListRow | undefined, b: ListRow | undefined): boolean =>
  a === undefined || b === undefined ? a === b : keyOf(a) === keyOf(b) && a.changed === b.changed;

const closed = (count: number): ProgramState => ({ count, open: false, rows: [], before: null, pages: 0 });

function groupState(group: Group, older: boolean): GroupState {
  const programs = group.program?.count ?? 0;
  return {
    key: group.key,
    count: group.count,
    rows: group.rows ?? [],
    before: group.before ?? null,
    pages: 0,
    program: programs > 0 ? closed(programs) : null,
    oldest: group.oldest ?? null,
    older,
  };
}

function shownKeys(groups: GroupState[]): Set<string> {
  const keys = new Set<string>();
  for (const group of groups) {
    for (const row of group.rows) keys.add(keyOf(row));
    for (const row of group.program?.rows ?? []) keys.add(keyOf(row));
  }
  return keys;
}

/** `rows` without any session in `seen`, each one then added to `seen`. */
function unseen(rows: ListRow[], seen: Set<string>): ListRow[] {
  return rows.filter((row) => {
    const key = keyOf(row);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** A newer list, and the reads that bring it to the depth the kept one was opened to. */
export interface Refreshed {
  state: ListState;
  reads: MoreRequest[];
}

/**
 * The kept list after a newer `/api/list` answer.
 *
 * The one rule: a kept row never shows a session where the server no longer has it. The
 * answer's rows replace the kept ones and sit where the answer puts them. Rows a group
 * read past the answer's own stand only while the group's count and first row are the
 * answer's; when either changed they are dropped and the group is read again to the same
 * depth (`reads`). The same holds for the days `earlier` opened, by its count and the
 * place it starts at. A day's opened program sessions are not in a list answer: when
 * their count changed they are dropped and read again, and otherwise they stand while a
 * read from their start checks their first row. Rows opened in place stay open, and an
 * opened program line stays open.
 *
 * A list asked with another query keeps nothing but the opened rows' keys.
 */
export function refresh(kept: ListState | null, answer: ListAnswer, query: Query): Refreshed {
  const old = kept !== null && sameQuery(kept.query, query) ? kept : null;
  const now = answer.now;
  const reads: MoreRequest[] = [];
  const fresh = new Set<string>();
  for (const group of answer.groups) for (const row of group.rows ?? []) fresh.add(keyOf(row));

  const groups = answer.groups.map((group): GroupState => {
    const next = groupState(group, false);
    const before = old?.groups.find((one) => one.key === group.key);
    if (before === undefined || group.key === "earlier") return next;
    if (before.pages > 0) {
      if (before.count === next.count && sameRow(before.rows[0], next.rows[0])) {
        next.rows = [...next.rows, ...before.rows.filter((row) => !fresh.has(keyOf(row)))];
        next.before = before.before;
        next.pages = before.pages;
      } else if (next.before !== null) {
        reads.push({ group: next.key, kind: kindOf(next.key), before: next.before, now, pages: before.pages, check: false });
      }
    }
    if (next.program !== null && before.program?.open === true) {
      const stands = before.program.count === next.program.count && before.program.rows.length > 0;
      next.program = stands
        ? { ...before.program, rows: before.program.rows.filter((row) => !fresh.has(keyOf(row))) }
        : { ...next.program, open: true };
      reads.push({ group: next.key, kind: "program", before: null, now,
                   pages: Math.max(1, before.program.pages), check: stands });
    }
    return next;
  });

  const earlier = groups.find((group) => group.key === "earlier");
  let opened = earlier === undefined ? null : { count: earlier.count, before: earlier.before, pages: 0 };
  if (earlier !== undefined && opened !== null && old !== null && old.earlier !== null && old.earlier.pages > 0) {
    if (old.earlier.count === earlier.count && old.earlier.before === earlier.before) {
      const keys = new Set(groups.map((group) => group.key));
      const days = old.groups.filter((group) => group.older && !keys.has(group.key)).map((group) => ({
        ...group,
        rows: group.rows.filter((row) => !fresh.has(keyOf(row))),
        program: group.program === null ? null
          : { ...group.program, rows: group.program.rows.filter((row) => !fresh.has(keyOf(row))) },
      }));
      const left = old.groups.find((group) => group.key === "earlier");
      groups.splice(groups.indexOf(earlier), 1, ...days, ...(left !== undefined ? [left] : []));
      opened = old.earlier;
    } else if (earlier.before !== null) {
      reads.push({ group: "earlier", kind: null, before: earlier.before, now, pages: old.earlier.pages, check: false });
    }
  }

  return {
    state: {
      query,
      now,
      today: answer.today,
      all: answer.all,
      total: answer.total,
      unreadable: answer.unreadable ?? null,
      roots: answer.roots,
      harnesses: answer.harnesses,
      groups,
      earlier: opened,
      opened: kept?.opened ?? [],
    },
    reads,
  };
}

/** The list after one `/api/more` answer, and the read that follows it, if one does. */
export interface Added {
  state: ListState;
  next: MoreRequest | null;
}

/**
 * The kept list after the answer to `request`.
 *
 * Rows are added under the rows of their group; the days `earlier` answers are placed
 * above it, and its line counts what is left (and goes when nothing is). A session
 * already shown anywhere is not added again. An answer to a request made of another list
 * (an older `now`), from a place the group has moved on from, or for a group or a program
 * line that is no longer there or no longer open, changes nothing.
 */
export function addMore(state: ListState, request: MoreRequest, answer: MoreAnswer): Added {
  const same: Added = { state, next: null };
  if (request.now !== state.now) return same;
  const index = state.groups.findIndex((group) => group.key === request.group);
  const group = state.groups[index];
  if (group === undefined) return same;
  const seen = shownKeys(state.groups);
  const following = (before: string | null): MoreRequest | null =>
    request.pages > 1 && before !== null ? { ...request, before, pages: request.pages - 1, check: false } : null;
  const put = (groups: GroupState[], more: Partial<ListState> = {}): ListState => ({ ...state, ...more, groups });
  const swap = (next: GroupState): GroupState[] => state.groups.map((one, at) => (at === index ? next : one));

  if (answer.groups !== undefined) {
    if (request.group !== "earlier" || state.earlier === null || request.before !== group.before) return same;
    const keys = new Set(state.groups.map((one) => one.key));
    const days = answer.groups.filter((one) => !keys.has(one.key)).map((one) => {
      const day = groupState(one, true);
      return { ...day, rows: unseen(day.rows, seen) };
    });
    const before = answer.before ?? null;
    const left = answer.left ?? 0;
    const rest = left > 0 && before !== null ? [{ ...group, count: left, before }] : [];
    const groups = [...state.groups.slice(0, index), ...days, ...rest, ...state.groups.slice(index + 1)];
    return {
      state: put(groups, { earlier: { ...state.earlier, pages: state.earlier.pages + 1 } }),
      next: rest.length > 0 ? following(before) : null,
    };
  }

  if (answer.rows === undefined || (answer.group !== undefined && answer.group !== request.group)) return same;
  const before = answer.before ?? null;

  if (request.kind === "program") {
    const program = group.program;
    if (program === null || !program.open) return same;
    if (request.before !== null) {
      if (request.before !== program.before) return same;
      const rows = [...program.rows, ...unseen(answer.rows, seen)];
      return { state: put(swap({ ...group, program: { ...program, rows, before, pages: program.pages + 1 } })),
               next: following(before) };
    }
    // From the start: the first read of an opened line, or a newer list's check of it.
    const stands = request.check && program.pages > 1 && program.rows.length > 0
      && sameRow(program.rows[0], answer.rows[0]);
    const others = shownKeys(state.groups.map((one, at) => (at === index ? { ...one, program: null } : one)));
    const first = unseen(answer.rows, others);
    if (stands) {
      const rows = [...first, ...unseen(program.rows, others)];
      return { state: put(swap({ ...group, program: { ...program, rows } })), next: null };
    }
    return { state: put(swap({ ...group, program: { ...program, rows: first, before, pages: 1 } })),
             next: following(before) };
  }

  if (request.before === null || request.before !== group.before || group.key === "earlier") return same;
  const rows = [...group.rows, ...unseen(answer.rows, seen)];
  return { state: put(swap({ ...group, rows, before, pages: group.pages + 1 })), next: following(before) };
}

/** The read a group's `Show` makes: its next rows, or `earlier`'s next older days. Null
 * when the group holds nothing more. */
export function showMore(state: ListState, key: string): MoreRequest | null {
  const group = state.groups.find((one) => one.key === key);
  if (group === undefined || group.before === null) return null;
  return { group: key, kind: kindOf(key), before: group.before, now: state.now, pages: 1, check: false };
}

/** The read the `Show` under a day's opened program sessions makes: the next of them. */
export function showMoreProgram(state: ListState, key: string): MoreRequest | null {
  const program = state.groups.find((one) => one.key === key)?.program ?? null;
  if (program === null || !program.open || program.before === null) return null;
  return { group: key, kind: "program", before: program.before, now: state.now, pages: 1, check: false };
}

/** A day's program line opened (`Show`), with the read of its first rows, or hidden
 * again (`Hide`), which lets its rows go. */
export function toggleProgram(state: ListState, key: string): { state: ListState; read: MoreRequest | null } {
  const group = state.groups.find((one) => one.key === key);
  const program = group?.program ?? null;
  if (group === undefined || program === null) return { state, read: null };
  const next = program.open ? closed(program.count) : { ...closed(program.count), open: true };
  return {
    state: { ...state, groups: state.groups.map((one) => (one === group ? { ...group, program: next } : one)) },
    read: next.open ? { group: key, kind: "program", before: null, now: state.now, pages: 1, check: false } : null,
  };
}

/** A row opened in place, or closed again. */
export function toggleOpen(state: ListState, key: string): ListState {
  const opened = state.opened.includes(key) ? state.opened.filter((one) => one !== key) : [...state.opened, key];
  return { ...state, opened };
}

// -- the drawing ----------------------------------------------------------------------------

/** Line two's text is handed to the row up to this many characters; the row cuts what
 * does not fit its one line. */
export const REST_MAX = 300;

/** A harness as a sentence names it: `Claude Code`; one the page does not know, as sent. */
export function harnessWords(harness: string): string {
  return isHarness(harness) ? HARNESS_LABELS[harness] : harness;
}

/** A folder's last segment: `proj-a` for `/work/proj-a/`. */
export function lastSegment(folder: string): string {
  const cut = folder.replace(/\/+$/, "");
  const last = cut.slice(cut.lastIndexOf("/") + 1);
  return last !== "" ? last : folder;
}

const count = (n: number, one: string, many: string): string => `${fmtCount(n)} ${n === 1 ? one : many}`;

/**
 * Line one of a row. With a typed name: the name, and the folder's last segment as
 * `where`. Without one: `<folder's last segment> · started <day> <time>`, the folder's
 * last segment alone when the start was not read, and `<harness> session` in the
 * folder's place when the folder was not. Never an id, and never a title a harness made.
 */
export function rowName(row: ListRow, today: string, zone: Zone = LOCAL): { name: string; where: string | null } {
  const folder = typeof row.folder === "string" && row.folder !== "" ? lastSegment(row.folder) : null;
  if (typeof row.name === "string" && row.name.trim() !== "") return { name: row.name.trim(), where: folder };
  const what = folder ?? `${harnessWords(row.harness)} session`;
  if (typeof row.started !== "number") return { name: what, where: null };
  const day = dayWords(zone.day(row.started), today);
  return { name: `${what} · started ${day} ${zone.clock(row.started)}`, where: null };
}

/** `2 subagents`, the phrase leading line two; null when the session has none or they
 * were not counted. */
export function subagentsLead(row: ListRow): string | null {
  return typeof row.subagents === "number" && row.subagents > 0 ? count(row.subagents, "subagent", "subagents") : null;
}

/** One row as the row element takes it. Null means the piece is not drawn and its
 * attribute is left off. */
export interface RowView {
  key: string;
  harness: string;
  session: string;
  /** The page's route for the session's transcript; null for a harness the page has no
   * view for. */
  route: string | null;
  name: string;
  where: string | null;
  /** The right end of line one. Nothing the list reads has a place there, so it is null
   * on every row. */
  fact: string | null;
  /** `2 subagents`. */
  lead: string | null;
  /** The time of the typed message on line two. */
  time: string | null;
  /** `you` when line two is a message the record marks as typed. */
  saidBy: "you" | null;
  /** That message's text. */
  rest: string | null;
  /** How many lines the row has. A row with one line has no act: it opens by its line. */
  lines: 1 | 2;
  /** The row's one act, at the right end of line two. */
  act: "Open" | "Close" | null;
  open: boolean;
}

/**
 * A row of the list, in a group whose rows are of the day `on`.
 *
 * Line two is the session's newest typed message and nothing else: its time, `You:` and
 * its text. A session without one has no time, no `You:` and no text there, whatever was
 * said in it last; a session a program started never has them. `n subagents` leads line
 * two when there are any. A row with neither is one line.
 */
export function rowView(row: ListRow, on: string, today: string, open = false, zone: Zone = LOCAL): RowView {
  const { name, where } = rowName(row, today, zone);
  const prompt = row.by_program === true ? null : row.prompt;
  const text = typeof prompt?.text === "string" ? prompt.text.replace(/\s+/g, " ").trim() : "";
  const said = prompt !== null && prompt !== undefined && text !== "";
  const lead = subagentsLead(row);
  const lines = said || lead !== null ? 2 : 1;
  return {
    key: keyOf(row),
    harness: row.harness,
    session: row.session,
    route: isHarness(row.harness) ? routeOf({ harness: row.harness, session: row.session, agent: null }) : null,
    name,
    where,
    fact: null,
    lead,
    time: said && typeof prompt.at === "number" ? timeWords(prompt.at, on, today, zone) : null,
    saidBy: said ? "you" : null,
    rest: said ? cutAt(text, REST_MAX) : null,
    lines,
    act: lines === 1 ? null : open ? "Close" : "Open",
    open,
  };
}

/** A dim line with one act: `12 more` and `Show`. */
export interface LineView {
  text: string;
  act: string;
}

/** A day's line for the sessions a program started, and their rows while it is open. */
export interface ProgramView {
  /** `3 started by a program`. */
  text: string;
  act: "Show" | "Hide";
  open: boolean;
  rows: RowView[];
  /** `12 more` and `Show`, under the opened rows, while there are more of them. */
  more: LineView | null;
}

/** One group as it is drawn, top to bottom: the label, the rows, `N more`, the program
 * line with its rows; `Earlier` is its label and one line. */
export interface GroupView {
  key: string;
  /** `Active recently`, `Today`, `Yesterday`, `Fri Nov 6`, `Earlier`; null for the find
   * field's results, which have no label. */
  label: string | null;
  rows: RowView[];
  /** `12 more` and `Show`: the group's sessions started in a terminal that are not shown. */
  more: LineView | null;
  program: ProgramView | null;
  /** `37 sessions, back to Sep 27` and `Show`. */
  earlier: LineView | null;
}

/** A dim line under the list for a harness whose sessions were not all read. */
export interface Notice {
  harness: string;
  line: string;
  /** Why, in the page server's words: shown at the desk. */
  reason: string | null;
}

/** What stands in the list's place when it has no row. */
export interface EmptyView {
  line: string;
  sentence: string | null;
  /** The link's words, drawn only where the launcher is installed. */
  start: string | null;
}

/** The list as it is drawn. */
export interface ListView {
  /** `62 sessions`, or `15 of 62 sessions` with a filter or the find field set; null when
   * no session was counted. */
  count: string | null;
  groups: GroupView[];
  empty: EmptyView | null;
  notices: Notice[];
}

export const ACTIVE_RECENTLY = "Active recently";
export const EARLIER = "Earlier";
export const UNDATED = "No date";
export const SHOW = "Show";
export const HIDE = "Hide";
export const NO_SESSIONS = "No sessions yet.";
export const NO_SESSIONS_SENTENCE = "Moxi lists every Claude Code, Codex and OpenCode session on this machine, "
  + "whether it was started here or in a terminal. It looked and found none.";
export const START_ONE = "Start one from Home";
export const NO_MATCH = "No sessions match.";

/** A group's label. */
export function groupLabel(key: string, today: string): string | null {
  if (key === "recent") return ACTIVE_RECENTLY;
  if (key === "earlier") return EARLIER;
  if (key === "found") return null;
  if (key === "undated") return UNDATED;
  return dayLabel(key, today);
}

/** The count at the right of the filter line. */
export function countLine(state: ListState): string | null {
  if (state.all === 0) return null;
  if (!isFiltered(state.query)) return count(state.all, "session", "sessions");
  return `${fmtCount(state.total)} of ${count(state.all, "session", "sessions")}`;
}

/** `N more` and `Show`: what a count holds past the rows shown, while there is a place to
 * read more from. */
function moreLine(total: number, shown: number, before: string | null): LineView | null {
  const more = total - shown;
  return before !== null && more > 0 ? { text: `${fmtCount(more)} more`, act: SHOW } : null;
}

/** `Earlier`'s line: how many sessions it holds and how far back they go. */
export function earlierLine(group: GroupState, today: string, zone: Zone = LOCAL): LineView {
  const back = group.oldest !== null ? `, back to ${dateWords(zone.day(group.oldest), today)}` : "";
  return { text: `${count(group.count, "session", "sessions")}${back}`, act: SHOW };
}

/** The line for one harness whose sessions were not all read: a root that could not be
 * listed, or one listed with parts that could not be. */
export function noticeOf(root: Root): Notice {
  const whose = `${harnessWords(root.harness)}'s sessions`;
  if (root.reason === null && typeof root.parts === "number" && root.parts > 0) {
    return { harness: root.harness, reason: null,
             line: `Moxi couldn't read ${count(root.parts, "part", "parts")} of ${whose}; the counts leave them out` };
  }
  return { harness: root.harness, line: `Moxi couldn't read ${whose}`, reason: root.reason };
}

function groupView(group: GroupState, state: ListState, zone: Zone): GroupView | null {
  const today = state.today;
  const label = groupLabel(group.key, today);
  if (group.key === "earlier") {
    if (group.count <= 0) return null;
    return { key: group.key, label, rows: [], more: null, program: null, earlier: earlierLine(group, today, zone) };
  }
  const programCount = group.program?.count ?? 0;
  if (group.rows.length === 0 && group.count <= 0 && programCount <= 0) return null;
  // The day a time in this group is taken to be of, unless it says otherwise.
  const on = /^\d{4}-\d{2}-\d{2}$/.test(group.key) ? group.key : today;
  const view = (row: ListRow): RowView => rowView(row, on, today, state.opened.includes(keyOf(row)), zone);
  const program = group.program;
  return {
    key: group.key,
    label,
    rows: group.rows.map(view),
    more: moreLine(group.count, group.rows.length, group.before),
    program: program === null || program.count <= 0 ? null : {
      text: `${fmtCount(program.count)} started by a program`,
      act: program.open ? HIDE : SHOW,
      open: program.open,
      rows: program.open ? program.rows.map(view) : [],
      more: program.open ? moreLine(program.count, program.rows.length, program.before) : null,
    },
    earlier: null,
  };
}

/**
 * The list as it is drawn: the count, the groups in the kept list's order (a group with
 * no session of either kind is left out), what stands in an empty list's place, and a
 * line for each harness whose sessions were not all read.
 *
 * `No sessions yet.` is drawn only when the machine holds no session and every root was
 * read: with a root unread, an empty list says nothing of what is there. A list that a
 * filter or the find field leaves empty reads `No sessions match.`.
 */
export function listView(state: ListState, { launcher = false, zone = LOCAL }: { launcher?: boolean; zone?: Zone } = {}): ListView {
  const groups = state.groups.map((group) => groupView(group, state, zone)).filter((group): group is GroupView => group !== null);
  const notices = state.roots.map(noticeOf);
  let empty: EmptyView | null = null;
  if (state.all === 0 && state.roots.length === 0) {
    empty = { line: NO_SESSIONS, sentence: NO_SESSIONS_SENTENCE, start: launcher ? START_ONE : null };
  } else if (groups.length === 0 && state.all > 0 && isFiltered(state.query)) {
    empty = { line: NO_MATCH, sentence: null, start: null };
  }
  return { count: countLine(state), groups: empty === null ? groups : [], empty, notices };
}

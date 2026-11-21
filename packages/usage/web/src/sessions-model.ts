// Sessions as the page names and reads them, with no DOM: a session's name, the paths
// of `/api/sessions` for the closed row and the Sessions table, a list read a page at a
// time, and the share a row takes of a whole. A session is named from its name, folder and start, never
// from its id, the row's `cwd` or its first request in the range.

import { LOCAL, startWord } from "./days.ts";
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

// --- the reads of /api/sessions ---------------------------------------------------------------

/** How many more rows a list reads at a time unless it is told. */
export const PAGE = 30;
/** The most rows one read of `/api/sessions` sends. */
export const TOP_MAX = 500;

/** The days a read covers, both included; `since` is null for all time up to `until`. */
export interface ListDays {
  since: string | null;
  until: string;
}

/** `/api/sessions` under the filters in force: over `days`, or over the range itself
 * when `days` is null. */
export function sessionsPath(state: PageState, today: string, days: ListDays | null, top: number, skip = 0): string {
  const own: [string, string][] = days === null ? [] : [
    ...(days.since === null ? [] : [["since", days.since] as [string, string]]), ["until", days.until]];
  const keys = days === null ? stateKeys(state, today) : [...own, ...stateKeys(state, today, ["since", "until"])];
  keys.push(["top", String(top)]);
  if (skip > 0) keys.push(["skip", String(skip)]);
  return `api/sessions?${new URLSearchParams(keys).toString()}`;
}

/** `/api/sessions` for the section's table: the whole range under the filters, in the
 * order picked, `top` rows after `skip`. */
export function boardPath(state: PageState, today: string, by: { sort: string; order: string }, top: number,
                          skip = 0): string {
  const keys = [...stateKeys(state, today), ["sort", by.sort], ["order", by.order], ["top", String(top)]];
  if (skip > 0) keys.push(["skip", String(skip)]);
  return `api/sessions?${new URLSearchParams(keys).toString()}`;
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

/** The reads that bring a list back to the depth it was read to, after a pass ended,
 * each at most `TOP_MAX` rows; fold their answers with `listRead` and `addPage`. */
export function depthPages(read: number, first: number): { top: number; skip: number }[] {
  const pages: { top: number; skip: number }[] = [];
  const depth = Math.max(read, first);
  for (let skip = 0; skip < depth; skip += TOP_MAX) pages.push({ top: Math.min(TOP_MAX, depth - skip), skip });
  return pages;
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

// The Sessions section as the page draws it, with no DOM: the closed row, the row of the
// session the address names in the words its two lines carry, the section's table of
// every session in the order picked, and a list read a page at a time.

import type { ApiResult } from "/m/ui/client.js";

import { COST_SERIES } from "./chart-model.ts";
import { costWords, countWords, sumRows, whyNotWhole } from "./cost-model.ts";
import { LOCAL, whenWord } from "./days.ts";
import type { Clock } from "./days.ts";
import { Feed, parseReport } from "./feed.ts";
import type { Report, SessionRow } from "./feed.ts";
import { launchOf } from "./launched-model.ts";
import type { Launched } from "./launched-model.ts";
import { percentWords } from "./limits-model.ts";
import { tilde } from "./filter-model.ts";
import {
  addPage, depthPages, lastSegment, listRead, PAGE, rowKey, sessionName, sessionWhere, TOP_MAX,
} from "./sessions-model.ts";
import type { ListRead } from "./sessions-model.ts";
import { fmtSpan, fmtTokens } from "./views.ts";

export const SESSIONS_LABEL = "Sessions";
export const OPEN = "Open";
export const SHOW = "Show";

/** The closed row: the range's dearest session and its cost, and how many sessions the
 * range holds, the link that opens the section. */
export interface SessionsClosed {
  /** `most to <name>, $5.35`; null when the dearest has no price. */
  closed: string | null;
  /** `54 sessions`. */
  count: string;
}

/** The closed row from `/api/sessions` over the range at `top=1`; null until it has
 * answered and when the range holds no session. `launched` names a session a running
 * launch holds. */
export function sessionsClosed(answer: Report<SessionRow> | null, today: string | null,
                               clock: Clock = LOCAL, launched: Launched | null = null): SessionsClosed | null {
  if (answer === null) return null;
  const count = answer.count ?? answer.rows.length;
  if (count <= 0) return null;
  const top = answer.rows[0];
  const closed = top === undefined || top.cost === null ? null
    : `most to ${sessionName(top, today, clock, launchOf(launched, top)).name}, ${costWords(sumRows([top]))}`;
  return { closed, count: countWords(count, "session") };
}

/** The two-line row of one session: line one its name, `where` and cost; line two its requests, the
 * times of its first and last, its models and how full its context was. A part whose
 * fact the row does not carry is null and is not drawn. */
export interface SessionLine {
  key: string;
  name: string;
  where: string | null;
  cost: string;
  /** Why the cost is not whole, for its hover; null when it is. */
  why: string | null;
  /** `15 requests`. */
  requests: string;
  /** `Tue 08:52 to 10:20`. */
  span: string | null;
  /** The model ids as the index holds them, joined by commas. */
  models: string | null;
  /** `context 20% full`. */
  context: string | null;
  /** The session the row opens; null for a row with no session id, which does not open. */
  session: { harness: string; id: string } | null;
}

/** `Tue 08:52 to 10:20`, `Tue 08:52 to Wed 01:10`, `today 11:00 to 18:22`; one time when
 * both read the same; null with neither. */
export function spanWords(first: number | null, last: number | null, today: string | null,
                          clock: Clock = LOCAL): string | null {
  const from = first === null ? null : whenWord(first, today, clock, true);
  if (last === null) return from;
  const sameDay = first !== null && clock.day(first) === clock.day(last);
  const to = sameDay ? clock.time(last) : whenWord(last, today, clock, true);
  if (from === null) return to;
  return from === whenWord(last, today, clock, true) ? from : `${from} to ${to}`;
}

/** `context 20% full`, from the newest request's whole prompt against its window; null
 * with no context or no window. */
export function contextWords(context: SessionRow["context"]): string | null {
  if (context === null || context.window === null || !(context.window > 0)) return null;
  return `context ${percentWords((context.tokens / context.window) * 100)} full`;
}

/** The two-line row of the session the address names. `projects` says whether projects
 * is installed, so a row's project is its `where`; `launched` names a session a running
 * launch holds and says where it runs. */
export function sessionLine(row: SessionRow, today: string | null, projects: boolean,
                            clock: Clock = LOCAL, launched: Launched | null = null): SessionLine {
  const sum = sumRows([row]);
  const models = row.models.join(", ");
  const launch = launchOf(launched, row);
  return {
    key: rowKey(row),
    name: sessionName(row, today, clock, launch).name,
    where: sessionWhere(row, projects, launch),
    cost: costWords(sum),
    why: whyNotWhole(sum),
    requests: countWords(row.requests, "request"),
    span: spanWords(row.first_ts, row.last_ts, today, clock),
    models: models === "" ? null : models,
    context: contextWords(row.context),
    session: row.session_id === null ? null : { harness: row.harness, id: row.session_id },
  };
}

/** What the table can be sorted by, as `/api/sessions` takes it. */
export type BoardSort = "cost" | "tokens" | "requests" | "fresh_input" | "cache_write" | "cache_read" | "output"
  | "started" | "active" | "turns" | "subagent_share" | "project";

/** The table's order: a column and its direction. */
export interface BoardOrder {
  sort: BoardSort;
  order: "asc" | "desc";
}

/** The Active column's hover. */
export const ACTIVE_TITLE = "First → last request (not the file's mtime)";
/** The Turns column's hover. */
export const TURNS_TITLE = "Prompts in the main session file; never summed with subagents";
/** The badge after a Codex session's name, and its hover. */
export const CODEX_BADGE = { text: "codex", title: "A Codex session, priced at OpenAI list rates" };
/** A token class cell's bar is this many px wide at the column's most. */
export const SPARKBAR_WIDTH = 44;

/** The table's first order: dearest first. */
export const BOARD_START: BoardOrder = { sort: "cost", order: "desc" };

/** The table's token class columns, each with its sort and the shade the chart draws the
 * class in. */
export const BOARD_CLASSES: readonly { key: "fresh_input" | "cache_write" | "cache_read" | "output"; label: string;
                                       sort: BoardSort; shade: number }[] = ([
  ["fresh_input", "input"], ["cache_write", "cache_write"], ["cache_read", "cache_read"], ["output", "output"],
] as const).map(([key, series]) => {
  const found = COST_SERIES.find((item) => item.key === series);
  return { key, label: found?.label ?? key, sort: key, shade: found?.shade ?? 1 };
});

/** How many rows a read of the table asks for: the first, and each `Load more`. */
export const BOARD_PAGE = TOP_MAX;
/** The act under the table that reads its next page. */
export const LOAD_MORE = "Load more";

/** The table's rows in its box: each `height` px tall; `overscan` more drawn on each side
 * of those in view; the next page read once the last drawn is within `prefetch` rows of
 * the end. */
export const BOARD_ROW = { height: 40, overscan: 6, prefetch: 20 };

/** The rows of the table drawn: `[start, end)`, with the px left above and below them. */
export interface BoardWindow {
  start: number;
  end: number;
  above: number;
  below: number;
  /** The last row drawn is near the end: the next page is to be read. */
  near: boolean;
}

/** Which of `count` rows a box `viewport` px tall scrolled `top` px down draws. */
export function boardWindow(count: number, top: number, viewport: number): BoardWindow {
  const { height, overscan, prefetch } = BOARD_ROW;
  const visible = Math.ceil(Math.max(height, viewport || 400) / height);
  const start = Math.min(count, Math.max(0, Math.floor(top / height) - overscan));
  const end = Math.min(count, start + visible + overscan * 2);
  return { start, end, above: start * height, below: Math.max(0, (count - end) * height), near: end >= count - prefetch };
}

/** The row the table's cursor is on after a key: one down for `ArrowDown`, one up for
 * `ArrowUp`, kept within the rows; any other key leaves it. */
export function moveCursor(at: number, key: string, count: number): number {
  const step = key === "ArrowDown" ? 1 : key === "ArrowUp" ? -1 : 0;
  return Math.max(0, Math.min(count - 1, at + step));
}

/** One column of the table; `sort` null for a column it cannot be sorted by. */
export interface BoardColumn {
  label: string;
  num: boolean;
  sort: BoardSort | null;
  /** What the column counts, for its header's hover; null when its name says it. */
  title: string | null;
  /** The column's `aria-sort`: null for a column with no sort. */
  aria: "ascending" | "descending" | "none" | null;
  /** `↓` or `↑` after the column the table is sorted by. */
  arrow: string | null;
}

/** A token class cell's bar: its value over the most of its column among the rows read,
 * 0 to 1, drawn in the class's `shade` of the chart. */
export interface Sparkbar {
  fraction: number;
  shade: number;
}

/** The Sessions section's table: every session of the range in the order picked, read a
 * page at a time. */
export interface Board {
  columns: BoardColumn[];
  /** The column that holds each row's name, a link to its session. */
  nameAt: number;
  /** `session` is the session the row opens; null for a row with no session id. `share`
   * is the subagent share drawn as a meter in its column, whose cell is empty; null
   * where the share is not known. `bars` stands beside `cells`: a bar after the figure
   * in each token class cell, null in the others. `badge` follows the name of a Codex
   * session. `why` says why the row's cost is not whole, for the mark in its cost cell;
   * null for a whole one. */
  rows: {
    key: string; cells: string[]; bars: (Sparkbar | null)[]; session: { harness: string; id: string } | null;
    share: { value: number; label: string } | null; badge: typeof CODEX_BADGE | null; why: string | null;
  }[];
  /** `54 sessions · showing 30`. */
  line: string;
  /** More sessions are after the rows shown: `Load more` reads the next page. */
  more: boolean;
}

/** The order after a click on a column: the column sorted by turns around; another
 * starts highest first. */
export function nextOrder(now: BoardOrder, sort: BoardSort): BoardOrder {
  return now.sort === sort ? { sort, order: now.order === "desc" ? "asc" : "desc" } : { sort, order: "desc" };
}

/**
 * The table over a list read in one order: a row per session, its project (or, without
 * projects, its folder), name, start, how long it was active, its turns, its four token
 * classes each with a bar against the column's most among the rows read, its subagent
 * share and its cost. A folder is written from `home` (`~/work/proj-a`), the account's
 * home folder that `/api/projects` answers; with no `home` read, as its last segment. A
 * row whose cost is not whole carries why, for the mark in its cost cell. `launched`
 * names a session a running launch holds.
 */
export function sessionsBoard(list: ListRead, by: BoardOrder, today: string | null, projects: boolean,
                              home: string | null, clock: Clock = LOCAL, launched: Launched | null = null): Board {
  const when = (at: number | null): string => (at === null ? "" : whenWord(at, today, clock, true));
  const column = (label: string, num: boolean, sort: BoardSort | null, title: string | null = null): BoardColumn => ({
    label, num, sort, title,
    aria: sort === null ? null : sort === by.sort ? (by.order === "asc" ? "ascending" : "descending") : "none",
    arrow: sort !== null && sort === by.sort ? (by.order === "asc" ? "↑" : "↓") : null,
  });
  const columns = [
    projects ? column("Project", false, "project") : column("Folder", false, "project"),
    column("Session", false, null),
    column("Started", true, "started"), column("Active", true, "active", ACTIVE_TITLE),
    column("Turns", true, "turns", TURNS_TITLE),
    ...BOARD_CLASSES.map((item) => column(item.label, true, item.sort)),
    column("Subagent share", true, "subagent_share"), column("Est. cost", true, "cost"),
  ];
  const most = BOARD_CLASSES.map((item) => Math.max(1, ...list.rows.map((row) => row[item.key])));
  const rows = list.rows.map((row) => ({
    key: rowKey(row),
    session: row.session_id === null ? null : { harness: row.harness, id: row.session_id },
    share: row.subagent_share === null ? null
      : { value: row.subagent_share, label: `Subagent share ${Math.round(row.subagent_share * 100)}%` },
    badge: row.harness === "codex" ? CODEX_BADGE : null,
    why: whyNotWhole(sumRows([row])),
    ...unzip([
      [projects ? row.project ?? "" : folderCell(row.folder ?? row.cwd, home), null],
      [sessionName(row, today, clock, launchOf(launched, row)).name, null],
      [when(row.first_ts), null],
      [row.first_ts === null || row.last_ts === null ? "" : fmtSpan(Math.max(0, row.last_ts - row.first_ts)), null],
      [(row.turns ?? 0).toLocaleString("en-US"), null],
      ...BOARD_CLASSES.map((item, at): [string, Sparkbar] => [fmtTokens(row[item.key]),
        { fraction: Math.min(1, Math.max(0, row[item.key] / most[at])), shade: item.shade }]),
      ["", null],
      [costWords(sumRows([row])), null],
    ]),
  }));
  return {
    columns, nameAt: 1, rows, line: `${countWords(list.count, "session")} · showing ${list.rows.length.toLocaleString("en-US")}`,
    more: list.more > 0,
  };
}

/** A row's cells and the bars beside them, from pairs. */
function unzip(pairs: [string, Sparkbar | null][]): { cells: string[]; bars: (Sparkbar | null)[] } {
  return { cells: pairs.map(([cell]) => cell), bars: pairs.map(([, bar]) => bar) };
}

/** A folder as the table writes it: whole from `home`, else its last segment. */
function folderCell(folder: string | null, home: string | null): string {
  if (folder === null || folder === "") return "";
  return home ? tilde(folder, home) : lastSegment(folder);
}


/** How a list asks for its rows: the path of `/api/sessions` for `top` rows after `skip`. */
export type ListPath = (top: number, skip: number) => string;

/**
 * A list of sessions read as a `Feed`, as the table reads it. Its first read asks for `first` rows; `show`
 * reads `page` more (thirty unless given) from where it stopped and puts them below the
 * rows shown; `refresh` reads the list again to the depth it was read to, as after a pass.
 * A read that fails keeps the rows shown.
 */
export class SessionList {
  readonly feed: Feed<ListRead>;
  #first: number;
  #page: number;
  #show = false;

  constructor(ask: (path: string) => Promise<ApiResult>, path: ListPath, first: number, onChange: () => void,
              page = PAGE) {
    this.#first = first;
    this.#page = page;
    this.feed = new Feed<ListRead>(() => this.#read(ask, path), (data) => this.#fold(data), onChange);
  }

  async #read(ask: (path: string) => Promise<ApiResult>, path: ListPath): Promise<ApiResult> {
    const held = this.feed.value;
    const show = this.#show && held !== null;
    this.#show = false;
    const pages = show ? [{ top: this.#page, skip: held.read }] : depthPages(held?.read ?? 0, this.#first);
    const answers: unknown[] = [];
    for (const page of pages) {
      const answer = await ask(path(page.top, page.skip));
      if (answer.status !== 200) return answer;
      answers.push(answer.data);
    }
    return { status: 200, data: { base: show ? held : null, answers } };
  }

  #fold(data: unknown): ListRead | null {
    const { base, answers } = data as { base: ListRead | null; answers: unknown[] };
    let list = base;
    for (const found of answers) {
      const answer = parseReport<SessionRow>(found);
      if (answer === null) return null;
      list = list === null ? listRead(answer) : addPage(list, answer);
    }
    return list;
  }

  /** A page more rows, below those shown; nothing while a read runs or before the first. */
  show(): Promise<void> {
    if (this.feed.reading || this.feed.value === null) return Promise.resolve();
    this.#show = true;
    return this.feed.refresh();
  }

  /** A page more as the rows drawn near the end, as `show`, but nothing while the last read
   * failed or found the route gone: a failed page is read again only by `show`, on a
   * scroll or a press. */
  showNear(): Promise<void> {
    if (this.feed.failure !== null || this.feed.gone) return Promise.resolve();
    return this.show();
  }

  refresh(): Promise<void> {
    return this.feed.refresh();
  }

  close(): void {
    this.feed.close();
  }
}

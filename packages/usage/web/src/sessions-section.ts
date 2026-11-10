// The Sessions section as the page draws it, with no DOM: the closed row, one row of a
// list in the words its two lines carry, the section's table, and a list read thirty
// rows at a time. The lists' days, headings and remainders are `sessions-model.ts`'.

import type { ApiResult } from "/m/ui/client.js";

import type { Table } from "./chart-model.ts";
import { costWords, countWords, sumRows, whyNotWhole } from "./cost-model.ts";
import { LOCAL, whenWord } from "./days.ts";
import type { Clock } from "./days.ts";
import { Feed, parseReport } from "./feed.ts";
import type { Report, SessionRow } from "./feed.ts";
import { percentWords } from "./limits-model.ts";
import { figuresTable } from "./sections-model.ts";
import { addPage, depthPages, listRead, PAGE, rowKey, sessionName, sessionWhere } from "./sessions-model.ts";
import type { ListRead } from "./sessions-model.ts";

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
 * answered and when the range holds no session. */
export function sessionsClosed(answer: Report<SessionRow> | null, today: string | null,
                               clock: Clock = LOCAL): SessionsClosed | null {
  if (answer === null) return null;
  const count = answer.count ?? answer.rows.length;
  if (count <= 0) return null;
  const top = answer.rows[0];
  const closed = top === undefined || top.cost === null ? null
    : `most to ${sessionName(top, today, clock).name}, ${costWords(sumRows([top]))}`;
  return { closed, count: countWords(count, "session") };
}

/** One session's row: line one its name, `where` and cost; line two its requests, the
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

/** A list's row. `projects` says whether projects is installed, so a row's project is its
 * `where`. */
export function sessionLine(row: SessionRow, today: string | null, projects: boolean,
                            clock: Clock = LOCAL): SessionLine {
  const sum = sumRows([row]);
  const models = row.models.join(", ");
  return {
    key: rowKey(row),
    name: sessionName(row, today, clock).name,
    where: sessionWhere(row, projects),
    cost: costWords(sum),
    why: whyNotWhole(sum),
    requests: countWords(row.requests, "request"),
    span: spanWords(row.first_ts, row.last_ts, today, clock),
    models: models === "" ? null : models,
    context: contextWords(row.context),
    session: row.session_id === null ? null : { harness: row.harness, id: row.session_id },
  };
}

/** The rows of one list as the table shows them, under the words that name the list. */
export interface TablePart {
  label: string;
  rows: SessionRow[];
}

/**
 * The section's table: every row shown, with every figure `/api/sessions` gives. A first
 * column names the list when two lists show rows, since a session with requests on both
 * sides of their cut is a row in each. The project column is there with projects.
 */
export function sessionsTable(parts: TablePart[], today: string | null, projects: boolean,
                              clock: Clock = LOCAL): Table {
  const shown = parts.filter((part) => part.rows.length > 0);
  const two = shown.length > 1;
  const when = (at: number | null): string => (at === null ? "" : whenWord(at, today, clock, true));
  const heads = [...(two ? ["List"] : []), "Session", ...(projects ? ["Project"] : []), "Folder", "Harness",
                 "Models", "First request", "Last request", "Context"];
  return figuresTable(heads, shown.flatMap((part, n) => part.rows.map((row) => ({
    key: `${n}:${rowKey(row)}`,
    sum: sumRows([row]),
    cells: [
      ...(two ? [part.label] : []),
      sessionName(row, today, clock).name,
      ...(projects ? [row.project ?? ""] : []),
      row.folder ?? row.cwd ?? "",
      row.harness,
      row.models.join(", "),
      when(row.first_ts),
      when(row.last_ts),
      row.context === null ? "" : contextCell(row.context),
    ],
  }))));
}

function contextCell(context: NonNullable<SessionRow["context"]>): string {
  const tokens = context.tokens.toLocaleString("en-US");
  return context.window === null ? tokens : `${tokens} of ${context.window.toLocaleString("en-US")}`;
}

/** How a list asks for its rows: the path of `/api/sessions` for `top` rows after `skip`. */
export type ListPath = (top: number, skip: number) => string;

/**
 * One list of the section, read as a `Feed`. Its first read asks for `first` rows; `show`
 * reads thirty more from where it stopped and puts them below the rows shown; `refresh`
 * reads the list again to the depth it was read to, as after a pass. A read that fails
 * keeps the rows shown.
 */
export class SessionList {
  readonly feed: Feed<ListRead>;
  #first: number;
  #show = false;

  constructor(ask: (path: string) => Promise<ApiResult>, path: ListPath, first: number, onChange: () => void) {
    this.#first = first;
    this.feed = new Feed<ListRead>(() => this.#read(ask, path), (data) => this.#fold(data), onChange);
  }

  async #read(ask: (path: string) => Promise<ApiResult>, path: ListPath): Promise<ApiResult> {
    const held = this.feed.value;
    const show = this.#show && held !== null;
    const pages = show ? [{ top: PAGE, skip: held.read }] : depthPages(held?.read ?? 0, this.#first);
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
    if (base !== null) this.#show = false;
    return list;
  }

  /** Thirty more rows, below those shown; nothing while a read runs or before the first. */
  show(): Promise<void> {
    if (this.feed.reading || this.feed.value === null) return Promise.resolve();
    this.#show = true;
    return this.feed.refresh();
  }

  refresh(): Promise<void> {
    return this.feed.refresh();
  }

  close(): void {
    this.feed.close();
  }
}

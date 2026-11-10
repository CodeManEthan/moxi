// The answers of the four routes the list reads, typed once, and each one's check before
// it is drawn. The shapes are the routes' own, as sessions' page server declares them
// (`moxi_sessions/page.py`, "The list"). A time is epoch seconds; null stands for a fact
// that was not read, never for zero or an empty text. No DOM, no Lit and no request here:
// a `Feed` reads a route with one of these checks as its `parse`.

import { SHAPE } from "./feed.ts";

/** `{at, who, text}`: the newest thing said in a session's last turn, whoever said it.
 * The list draws no line from it. */
export interface Said {
  at: number | null;
  /** `you`, `agent` or null. */
  who: string | null;
  text: string | null;
}

/** `{at, text, truncated}`: one message, its text cut by the page server at 64 KiB. */
export interface Message {
  at: number | null;
  text: string | null;
  truncated: boolean;
}

/** One session of a list. */
export interface ListRow {
  harness: string;
  session: string;
  path: string | null;
  /** The last change; null for an OpenCode row without one. */
  changed: number | null;
  size: number | null;
  /** How many subagents; null where they could not be listed. */
  subagents: number | null;
  folder: string | null;
  started: number | null;
  /** True where the record says a program started the session, else null. */
  by_program: boolean | null;
  /** A name typed for the session in its harness, else null. */
  name: string | null;
  model: string | null;
  activity: Said | null;
  /** The newest message of the whole session that its record marks as typed, else null. */
  prompt: Message | null;
  /** A session not started by a program that changed within the recent setting of `now`. */
  recent: boolean;
}

/** One group of a list. Every group has `key`, `count` and `before`; `recent`, a day and
 * `found` have `rows`; a day has `program`; `earlier` has `oldest`. */
export interface Group {
  /** `recent`, `found`, `earlier`, `undated`, or a day as `YYYY-MM-DD`. */
  key: string;
  /** The sessions started in a terminal (`recent`, a day); of both kinds (`found`,
   * `earlier`). */
  count: number;
  /** The place to ask `/api/more` from; null when `rows` holds the whole group. */
  before: string | null;
  rows?: ListRow[];
  /** A day's sessions that a program started: how many. */
  program?: { count: number };
  /** `earlier`: the oldest change among its sessions. */
  oldest?: number | null;
}

/** A harness whose root was not read whole. With a `reason`, the root could not be listed
 * and `parts` is null; with `parts`, the root was listed but that many of its folders or
 * files were not, and the counts are short by their sessions. */
export interface Root {
  harness: string;
  root: string | null;
  reason: string | null;
  parts: number | null;
}

/** `/api/list`. */
export interface ListAnswer {
  shape: number;
  /** The time the answer was cut at; sent back as `now` with every `/api/more`. */
  now: number;
  /** The server's local date, `YYYY-MM-DD`. */
  today: string;
  /** The sessions before the filters, and after them; both of both kinds. */
  all: number;
  total: number;
  unreadable: number | null;
  roots: Root[];
  harnesses: { harness: string; count: number }[];
  groups: Group[];
}

/** `/api/more`: `group`, `kind` and `rows` for `recent`, a day and `found`; `groups` and
 * `left` for `earlier`; `before` in both. The keys of the other form are absent. */
export interface MoreAnswer {
  shape: number;
  before: string | null;
  group?: string;
  /** `main`, `program`, or null for `found`. */
  kind?: string | null;
  rows?: ListRow[];
  groups?: Group[];
  /** How many sessions remain in `earlier` after `groups`. */
  left?: number;
}

/** One subagent of an opened session. */
export interface SessionSubagent {
  description: string | null;
  agent_type: string | null;
  started: number | null;
  last_at: number | null;
  last: string | null;
}

/** `/api/session`: a row, and what an opened session shows under it. */
export interface SessionAnswer extends Omit<ListRow, "subagents"> {
  shape: number;
  /** The model's newest message when no user message follows it, else null. */
  reply: Message | null;
  /** The 8 that changed last, newest first; null when they could not be listed. */
  subagents: SessionSubagent[] | null;
  /** How many were not listed. */
  more: number | null;
  /** The harness's command for picking the session up again, and the folder to run it in
   * (null where it runs anywhere); null for a harness with no command. */
  resume: { command: string; folder: string | null } | null;
}

/** `/api/folders`: the folders the listed sessions ran in, newest change first. */
export interface FoldersAnswer {
  shape: number;
  folders: { folder: string; count: number; changed: number | null }[];
  /** How many folders were left off. */
  more: number;
}

const isList = (value: unknown): value is unknown[] => Array.isArray(value);
const isTable = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isText = (value: unknown): value is string => typeof value === "string";
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const orNull = (value: unknown, is: (value: unknown) => boolean): boolean =>
  value === null || value === undefined || is(value);

function isMessage(value: unknown): boolean {
  return isTable(value) && orNull(value.at, isNumber) && orNull(value.text, isText);
}

/** A row the list can draw: it is named by a harness and an id, and each fact it has is
 * of the type the model reads. */
function isRow(value: unknown): boolean {
  if (!isTable(value) || !isText(value.harness) || !isText(value.session)) return false;
  return orNull(value.changed, isNumber) && orNull(value.started, isNumber) && orNull(value.subagents, isNumber)
    && orNull(value.folder, isText) && orNull(value.name, isText) && orNull(value.prompt, isMessage);
}

function isGroup(value: unknown): boolean {
  if (!isTable(value) || !isText(value.key) || !isNumber(value.count)) return false;
  if (!orNull(value.before, isText) || !orNull(value.oldest, isNumber)) return false;
  if (value.rows !== undefined && !(isList(value.rows) && value.rows.every(isRow))) return false;
  return value.program === undefined || (isTable(value.program) && isNumber(value.program.count));
}

function isRoot(value: unknown): boolean {
  return isTable(value) && isText(value.harness) && orNull(value.reason, isText) && orNull(value.parts, isNumber);
}

const isShape = (data: unknown): data is Record<string, unknown> => isTable(data) && data.shape === SHAPE;

/** `/api/list`'s value, or null when it isn't one. The server declares the shape, so only
 * what the model reads before drawing is checked. */
export function parseList(data: unknown): ListAnswer | null {
  if (!isShape(data) || !isNumber(data.now) || !isText(data.today) || !/^\d{4}-\d{2}-\d{2}$/.test(data.today)) return null;
  if (!isNumber(data.all) || !isNumber(data.total) || !orNull(data.unreadable, isNumber)) return null;
  if (!isList(data.roots) || !data.roots.every(isRoot)) return null;
  if (!isList(data.harnesses)
      || !data.harnesses.every((row) => isTable(row) && isText(row.harness) && isNumber(row.count))) return null;
  if (!isList(data.groups) || !data.groups.every(isGroup)) return null;
  return data as unknown as ListAnswer;
}

/** `/api/more`'s value, or null: the next rows of a group, or the next older days. */
export function parseMore(data: unknown): MoreAnswer | null {
  if (!isShape(data) || !orNull(data.before, isText)) return null;
  if (isList(data.groups)) {
    if (!data.groups.every(isGroup) || !isNumber(data.left)) return null;
  } else if (!isText(data.group) || !orNull(data.kind, isText) || !isList(data.rows) || !data.rows.every(isRow)) {
    return null;
  }
  return data as unknown as MoreAnswer;
}

/** `/api/session`'s value, or null. */
export function parseSession(data: unknown): SessionAnswer | null {
  if (!isShape(data) || !isRow({ ...data, subagents: null }) || !orNull(data.reply, isMessage)) return null;
  if (!orNull(data.subagents, (value) => isList(value) && value.every(isTable))) return null;
  if (!orNull(data.more, isNumber)) return null;
  if (!orNull(data.resume, (value) => isTable(value) && isText(value.command) && orNull(value.folder, isText))) return null;
  return {
    ...(data as unknown as SessionAnswer),
    reply: (data.reply ?? null) as Message | null,
    subagents: (data.subagents ?? null) as SessionSubagent[] | null,
    more: (data.more ?? null) as number | null,
    resume: (data.resume ?? null) as SessionAnswer["resume"],
  };
}

/** `/api/folders`' value, or null. */
export function parseFolders(data: unknown): FoldersAnswer | null {
  if (!isShape(data) || !isList(data.folders) || !isNumber(data.more)) return null;
  if (!data.folders.every((row) => isTable(row) && isText(row.folder) && isNumber(row.count)
                          && orNull(row.changed, isNumber))) return null;
  return data as unknown as FoldersAnswer;
}

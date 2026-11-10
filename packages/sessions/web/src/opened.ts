// What a row of the list shows once it is opened in place, worked out with no DOM, no Lit
// and no request so the Node tests can read it. From `/api/session`'s answer to the lines
// drawn under the row: the newest typed message, the newest reply, the subagents, the
// `Session` line, the harness's own command for picking the session up again, and under
// them the link to the transcript and `Copy id`.
//
// A piece whose fact the answer does not carry is null and is not drawn. The link to the
// transcript and `Copy id` come from the row itself, so they are there while the answer is
// on its way and when it does not come.
//
// Every string here goes into the page as text.

import { charCount, cutAt } from "./views.ts";
import type { FeedState } from "./views.ts";
import type { ListRow, Message, Root, SessionAnswer, SessionSubagent } from "./list-feed.ts";
import { dayWords, GONE, LOCAL, noticeOf, timeWords } from "./list.ts";
import type { RowView, StatusLine, Zone } from "./list.ts";

/** A message is shown up to this many characters; the transcript has the rest. */
export const OPENED_MAX = 600;
/** How long `Copied` stands in for `Copy`, in milliseconds. */
export const COPIED_MS = 1500;

export const YOU = "You";
export const REPLY = "Reply";
export const SUBAGENTS = "Subagents";
export const SESSION = "Session";
export const RESUME = "Resume";
export const COPY = "Copy";
export const COPIED = "Copied";
export const COPY_ID = "Copy id";
export const READ_TRANSCRIPT = "Read the transcript";
/** What an opened row says before its answer. */
export const OPENED_WAITING = "Reading the session…";

/** One message of an opened row: its time and its text on one run of lines. */
export interface SaidView {
  /** `You` or `Reply`. */
  label: string;
  time: string | null;
  text: string;
}

/** One subagent of an opened row. */
export interface SubagentView {
  /** What it was asked to do; without that its type, and without either `Subagent`. */
  name: string;
  /** Dim, after the name: its type and when it ran (`Explore · 07:58 to 08:01`). */
  facts: string | null;
  /** Dim, on a line of its own: the newest thing it said. */
  last: string | null;
}

/** An opened row as it is drawn, top to bottom. A null is a piece that is not drawn. */
export interface OpenedView {
  /** That the answer is on its way, or why there is none. */
  status: StatusLine | null;
  prompt: SaidView | null;
  reply: SaidView | null;
  subagents: {
    label: string;
    items: SubagentView[];
    /** For Codex, when part of its sessions could not be read: the subagents listed may
     * not be all of them. */
    notice: string | null;
  } | null;
  session: {
    label: string;
    /** `claude · claude-sonnet-5-5`. */
    lead: string;
    /** Dim: `today 07:40 to 08:55`. */
    rest: string | null;
  } | null;
  resume: {
    label: string;
    /** The harness's command, the text `Copy` copies. */
    command: string;
    /** Dim, under the command: `run it in /work/proj-a`. */
    where: string | null;
    copy: string;
  } | null;
  /** The link to the session's transcript; null for a harness the page has no view for. */
  transcript: { text: string; route: string } | null;
  copyId: { text: string; id: string };
}

/** A message's text as an opened row shows it: runs of whitespace made one space, cut at
 * `OPENED_MAX` characters. A text that was cut, here or by the page server, ends with an
 * ellipsis. Null for a message with no text. */
export function saidText(message: Message | null | undefined): string | null {
  if (message === null || message === undefined || typeof message.text !== "string") return null;
  const text = message.text.replace(/\s+/g, " ").trim();
  if (text === "") return null;
  if (charCount(text) > OPENED_MAX) return `${cutAt(text, OPENED_MAX).trimEnd()}…`;
  return message.truncated === true ? `${text}…` : text;
}

function said(label: string, message: Message | null | undefined, on: string, today: string, zone: Zone): SaidView | null {
  const text = saidText(message);
  if (text === null || message === null || message === undefined) return null;
  return { label, time: typeof message.at === "number" ? timeWords(message.at, on, today, zone) : null, text };
}

/**
 * From when to when, beside something of the day `on`: `07:58 to 08:01`. Each end says
 * its day when the reader could not tell it: the start when it is not of `on`, the end
 * when it is not of the start's day (`yesterday 22:25 to today 00:10`). With one end not
 * read, the other alone: `started 07:58`, `last change 08:01`. Null with neither.
 */
export function spanWords(start: number | null, end: number | null, on: string, today: string,
                          zone: Zone = LOCAL): string | null {
  const from = typeof start === "number" ? timeWords(start, on, today, zone) : null;
  if (typeof end !== "number") return from !== null ? `started ${from}` : null;
  if (typeof start !== "number" || from === null) return `last change ${timeWords(end, on, today, zone)}`;
  const day = zone.day(end);
  const to = day === zone.day(start) ? zone.clock(end) : `${dayWords(day, today)} ${zone.clock(end)}`;
  return `${from} to ${to}`;
}

const label = (value: unknown): string | null =>
  typeof value === "string" && value.trim() !== "" ? value.replace(/\s+/g, " ").trim() : null;

/** One subagent's lines. */
export function subagentView(sub: SessionSubagent, on: string, today: string, zone: Zone = LOCAL): SubagentView {
  const description = label(sub.description);
  const type = label(sub.agent_type);
  const span = spanWords(sub.started ?? null, sub.last_at ?? null, on, today, zone);
  const facts = [description !== null ? type : null, span].filter((one): one is string => one !== null);
  return {
    name: description ?? type ?? "Subagent",
    facts: facts.length > 0 ? facts.join(" · ") : null,
    last: label(sub.last),
  };
}

/** The `Session` line: the harness as its id and the model, then from the start to the
 * last change, each with its day. A part that was not read is left out. */
export function sessionLine(answer: SessionAnswer, today: string, zone: Zone = LOCAL): { lead: string; rest: string | null } {
  const model = label(answer.model);
  return {
    lead: model !== null ? `${answer.harness} · ${model}` : answer.harness,
    // No day stands beside this line, so both ends say theirs.
    rest: spanWords(answer.started ?? null, answer.changed ?? null, "", today, zone),
  };
}

/** What an opened row says about the read of its session: nothing once the read gave an
 * answer and no later one failed; that it is reading, before the first; else why there is
 * no answer. An answer read before a failure stays drawn under the line. */
export function openedStatus(feed: FeedState<unknown> | null): StatusLine | null {
  if (feed === null) return { state: "waiting", line: OPENED_WAITING };
  if (feed.gone) return { state: "gone", line: GONE };
  if (feed.refused !== null) return { state: "refused", line: `Moxi couldn't read the session: ${feed.refused}` };
  if (feed.failure !== null) return { state: "failed", line: `Moxi couldn't read the session: ${feed.failure.why}` };
  if (feed.value === null) return { state: "waiting", line: OPENED_WAITING };
  return null;
}

/**
 * An opened row as it is drawn, from the row and the read of its session.
 *
 * `You` is the session's newest typed message and `Reply` the model's newest message
 * after it; a session a program started shows no `You`. `Subagents` lists the ones the
 * answer carries and says nothing of how many more there are: the transcript lists them
 * all. A row that had one line when closed shows what its answer carries all the same,
 * which is then the reply, the `Session` line and the command.
 *
 * `roots` are the list's harnesses whose sessions were not all read. A Codex session's
 * subagents are found by reading every Codex session, so when part of those could not be
 * read the line that says so stands under the subagents.
 */
export function openedView(row: RowView, feed: FeedState<SessionAnswer> | null,
                           { today, roots = [], zone = LOCAL }: { today: string; roots?: Root[]; zone?: Zone }): OpenedView {
  const answer = feed?.value ?? null;
  const view: OpenedView = {
    status: openedStatus(feed),
    prompt: null,
    reply: null,
    subagents: null,
    session: null,
    resume: null,
    transcript: row.route !== null ? { text: READ_TRANSCRIPT, route: row.route } : null,
    copyId: { text: COPY_ID, id: row.session },
  };
  if (answer === null) return view;
  view.prompt = answer.by_program === true ? null : said(YOU, answer.prompt, row.on, today, zone);
  view.reply = said(REPLY, answer.reply, row.on, today, zone);
  if (answer.subagents !== null && answer.subagents.length > 0) {
    const unread = answer.harness === "codex"
      ? roots.find((root) => root.harness === "codex" && root.reason === null
                             && typeof root.parts === "number" && root.parts > 0)
      : undefined;
    view.subagents = {
      label: SUBAGENTS,
      items: answer.subagents.map((sub) => subagentView(sub, row.on, today, zone)),
      notice: unread !== undefined ? noticeOf(unread).line : null,
    };
  }
  view.session = { label: SESSION, ...sessionLine(answer, today, zone) };
  if (answer.resume !== null && answer.resume.command !== "") {
    const folder = label(answer.resume.folder);
    view.resume = {
      label: RESUME,
      command: answer.resume.command,
      where: folder !== null ? `run it in ${folder}` : null,
      copy: COPY,
    };
  }
  return view;
}

/** How many subagents an answer says its session has; null when it does not say. */
function subagentCount(answer: SessionAnswer): number | null {
  return answer.subagents !== null && typeof answer.more === "number" ? answer.subagents.length + answer.more : null;
}

/**
 * Whether an opened row's session is read again after a newer list: when the row has no
 * answer yet (the last read failed or was refused), and when the list's row for it is no
 * longer the session the answer was read from: its last change differs, or its count of
 * subagents does. Otherwise the answer stands. A row the list no longer shows (`row`
 * null) is not read, and neither is one whose read is running.
 */
export function readAgain(feed: (FeedState<SessionAnswer> & { reading?: boolean }) | null, row: ListRow | null): boolean {
  if (row === null || feed === null || feed.reading === true || feed.gone) return false;
  const answer = feed.value;
  if (answer === null) return true;
  if ((answer.changed ?? null) !== (row.changed ?? null)) return true;
  const count = subagentCount(answer);
  return count !== null && typeof row.subagents === "number" && count !== row.subagents;
}

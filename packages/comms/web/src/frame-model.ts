// The Comms page's frame worked out with no DOM: which state the page is in, the first
// row when the daemon isn't answering, the agent rows and which one is open, the empty
// state, the foot and the dim lines under it. From the roster's feed, the last roster that
// was up, `/api/mail-state` and the address. Every string here goes into the page as text.
//
// - A roster that is up draws the rows. One that is `offline` or `error` draws the first
//   row in their place and keeps the last rows that were up, dim, with no acts and no box.
// - The address opens a row only once the reads it needs have answered; until then it is
//   pending, and an address the page doesn't know gives a dim line at the foot.

import type { Address } from "./address-model.ts";
import { agentsOf, noteText } from "./agents-model.ts";
import type { AgentRow, Launched, MailState, OtherName, Owed, PageRoster } from "./agents-model.ts";
import type { Failure } from "./feed.ts";
import { LOCAL } from "./mail-model.ts";
import type { Clock } from "./mail-model.ts";
import { whenWords } from "./words.ts";

export const READING = "Reading…";
/** How long the page shows nothing before `Reading…`. */
export const READING_AFTER_MS = 400;
export const DOWN_LABEL = "Comms";
export const DOWN_WHAT = "The comms daemon isn't answering";
export const DOCTOR = "moxi doctor comms";
export const DOWN_LINE = " says why. Nothing can be sent until it answers.";
export const SERVER_ERROR = "comms' page server hit an error";
export const NO_AGENTS = "No agents yet.";
export const WHAT_AN_AGENT_IS = "An agent is a name that one session holds at a time, so other sessions and you can "
  + "write to it. Give a session a name when you launch it and it shows here with its mail.";
export const LAUNCH_FROM_HOME = "Launch one from Home";
export const NAME_COMMAND = "moxi comms claude --name NAME";
export const ANSWERING = "Comms is answering";
export const SETTINGS_LINK = "Comms settings";
export const SETTINGS_ROUTE = "settings/comms";
export const NO_SUCH_VIEW = "There is no such view here.";
export const NO_LONGER_OWED = "That message no longer waits for your reply.";
export const COPY = "Copy";
export const COPIED = "Copied";
export const COPIED_MS = 2000;
export const ACT_WORDS = { reply: "Reply", message: "Message", "keep-warm": "Keep warm" } as const;
export const CLOSE = "Close";

/** A read as the page holds it: a `Feed`'s state. */
export interface Read<V> {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  value: V | null;
}

/** What the frame is drawn from. */
export interface FrameFacts {
  roster: Read<PageRoster>;
  /** The last roster that was up, and its time on the server's clock. */
  kept: { value: PageRoster; at: number } | null;
  mail: Read<MailState>;
  /** The server's time now, epoch seconds; null before the first read. */
  now: number | null;
  /** The newest `today` the page holds. */
  today: string | null;
  cache: boolean;
  launcher: boolean;
  launched?: ReadonlyMap<string, Launched> | null;
  /** How long ago the page opened. */
  waitedMs: number;
  at: Address;
  /** Today's messages have been read (or their read failed), so `authorOf` can answer. */
  messagesRead?: boolean;
  /** The author of a message the page holds, by id, beyond `owed`; null when it holds none. */
  authorOf?: (messageId: string) => string | null;
  /** Where the address was placed once its reads answered, kept until another opens, so a
   * reply sent from the box does not close the row it was sent from. */
  placed?: Placed | null;
}

/** Where an address opened, and the foot's line it gave. */
export interface Placed {
  opened: Opened;
  line: string | null;
}

/** What the address opens, once the reads it needs have answered. */
export type Opened =
  | { kind: "none" }
  /** The address waits for a read before it can be placed. */
  | { kind: "pending" }
  /** A row opened, its box written to `to`; with `reply`, set to answer that message. */
  | { kind: "agent"; name: string; to: string; reply: Owed | null }
  /** `All mail with` an address, under its name's row. */
  | { kind: "mail"; name: string; qualified: string }
  /** An address of `Other names`, shown with its box. */
  | { kind: "other"; qualified: string }
  | { kind: "thread"; a: string; b: string }
  | { kind: "others" };

/** The first row, in the agents' place, while the roster isn't up. */
export interface Down {
  what: string;
  /** Line two after the command, or null; the command is drawn as code before it. */
  line: string | null;
  /** The command `Copy` puts on the clipboard, or null. */
  copy: string | null;
}

/** One agent row as the frame draws it. */
export interface FrameRow {
  row: AgentRow;
  note: string | null;
  open: boolean;
  /** The address the opened row speaks for and its box writes to; null when closed. */
  to: string | null;
}

export interface Empty {
  line: string;
  sentence: string;
  /** `Launch one from Home`, with launcher. */
  launch: boolean;
  /** The command to give a session a name, with `Copy`, without launcher. */
  command: string | null;
}

export interface FootLine {
  key: string;
  text: string;
}

export interface Frame {
  /** `gone`: comms' page server has no roster (a 404), and nothing is drawn. */
  state: "gone" | "loading" | "drawn";
  /** `Reading…`, once the page has waited long enough for a first read. */
  reading: string | null;
  down: Down | null;
  /** `Last read <time>. It may have changed since.` while down, once a read had answered. */
  lastRead: string | null;
  /** The rows are the last ones read while the roster isn't up: dim, no acts, no box. */
  stale: boolean;
  /** `N agents`, or null with no row. */
  label: string | null;
  rows: FrameRow[];
  others: OtherName[];
  empty: Empty | null;
  /** The foot's own line: `Comms is answering · read <time>` while the roster is up. */
  foot: string | null;
  lines: FootLine[];
  opened: Opened;
  /** Where the address was placed, to keep; null while it waits for a read. */
  placed: Placed | null;
}

/** `1 agent`, `6 agents`. */
export function agentsLabel(n: number): string {
  return n === 1 ? "1 agent" : `${n} agents`;
}

/** The line of a read that failed, or answered `error`; null otherwise. A 404 is no line:
 * that piece is absent. */
export function readLine(what: string, read: Read<{ state: string; detail: string | null }>): string | null {
  const answered = read.value !== null && read.value.state === "error" ? read.value.detail ?? SERVER_ERROR : null;
  const why = read.failure?.why ?? answered;
  return why === null ? null : `${what} can't be read: ${why.replace(/\.$/, "")}.`;
}

/** Where the address opens among `rows` and `others`, given which reads have answered. */
export function openedOf(at: Address, rows: readonly AgentRow[], others: readonly OtherName[],
                         { rosterRead, mailRead, messagesRead = true, authorOf }
                           : { rosterRead: boolean; mailRead: boolean; messagesRead?: boolean;
                               authorOf: (id: string) => string | null })
  : Placed {
  const rowOf = (qualified: string): AgentRow | undefined =>
    rows.find((row) => row.addresses.some((address) => address.qualified === qualified));
  switch (at.piece) {
    case "none":
      return { opened: { kind: "none" }, line: null };
    case "unknown":
      return { opened: { kind: "none" }, line: NO_SUCH_VIEW };
    case "others":
      return { opened: { kind: "others" }, line: null };
    case "thread":
      return { opened: { kind: "thread", a: at.a, b: at.b }, line: null };
    case "agent":
    case "mail": {
      if (!rosterRead) return { opened: { kind: "pending" }, line: null };
      const row = rowOf(at.qualified);
      if (row !== undefined) {
        return { opened: at.piece === "agent" ? { kind: "agent", name: row.name, to: at.qualified, reply: null }
          : { kind: "mail", name: row.name, qualified: at.qualified }, line: null };
      }
      if (at.piece === "agent" && others.some((other) => other.qualified === at.qualified)) {
        return { opened: { kind: "other", qualified: at.qualified }, line: null };
      }
      return { opened: { kind: "none" }, line: `${at.qualified} isn't on this page.` };
    }
    case "reply": {
      if (!rosterRead || !mailRead || !messagesRead) return { opened: { kind: "pending" }, line: null };
      for (const row of rows) {
        for (const address of row.addresses) {
          const owed = address.owed.find((entry) => entry.message_id === at.message_id);
          if (owed !== undefined) return { opened: { kind: "agent", name: row.name, to: owed.author, reply: owed }, line: null };
        }
      }
      const author = authorOf(at.message_id);
      const row = author === null ? undefined : rowOf(author);
      return { opened: row !== undefined && author !== null ? { kind: "agent", name: row.name, to: author, reply: null }
        : { kind: "none" }, line: NO_LONGER_OWED };
    }
  }
}

/** A row with nothing heavy: the last rows read, kept while the roster isn't up. */
function dimmed(row: AgentRow): AgentRow {
  return { ...row, fact: row.fact === null ? null : { ...row.fact, heavy: false },
           line: { ...row.line, heavy: false }, acts: [] };
}

/** The frame at `facts.now`. */
export function frameModel(facts: FrameFacts, clock: Clock = LOCAL): Frame {
  const { roster, kept, mail, today } = facts;
  const when = (at: number): string => whenWords(at, today, clock);
  const blank: Frame = { state: "loading", reading: null, down: null, lastRead: null, stale: false, label: null,
                         rows: [], others: [], empty: null, foot: null, lines: [], opened: { kind: "pending" },
                         placed: facts.placed ?? null };
  if (roster.gone) return { ...blank, state: "gone", opened: { kind: "none" } };
  const value = roster.value;
  if (value === null) {
    const lines: FootLine[] = [];
    const failed = roster.failure !== null ? readLine("The agents", roster) : null;
    if (failed !== null) lines.push({ key: "roster", text: failed });
    return { ...blank, reading: roster.failure === null && facts.waitedMs >= READING_AFTER_MS ? READING : null, lines };
  }

  const up = value.state === "up";
  const shown = up ? value : kept?.value ?? null;
  const stale = !up;
  const mailValue = mail.value;
  const agents = shown === null || facts.now === null ? { rows: [], others: [] }
    : agentsOf(shown, mailValue, { now: facts.now, when, cache: facts.cache, launched: facts.launched ?? null });

  let down: Down | null = null;
  if (value.state === "offline") down = { what: DOWN_WHAT, line: DOWN_LINE, copy: DOCTOR };
  else if (value.state === "error") down = { what: value.detail ?? SERVER_ERROR, line: null, copy: null };

  const lines: FootLine[] = [];
  const rosterLine = readLine("The agents", { ...roster, value: null });
  if (rosterLine !== null) lines.push({ key: "roster", text: rosterLine });
  const mailLine = readLine("The agents' mail", mail);
  if (mailLine !== null) lines.push({ key: "mail", text: mailLine });

  const mailRead = mail.gone || mail.failure !== null || mailValue !== null;
  const placed = facts.placed ?? openedOf(facts.at, agents.rows, agents.others,
                                          { rosterRead: shown !== null, mailRead, messagesRead: facts.messagesRead ?? true,
                                            authorOf: facts.authorOf ?? (() => null) });
  if (placed.line !== null) lines.push({ key: "address", text: placed.line });
  const opened = stale ? { kind: "none" as const } : placed.opened;

  const empty = up && value.rows.length === 0 && value.waiting.length === 0 && agents.rows.length === 0
    ? { line: NO_AGENTS, sentence: WHAT_AN_AGENT_IS, launch: facts.launcher, command: facts.launcher ? null : NAME_COMMAND }
    : null;
  const openName = opened.kind === "agent" || opened.kind === "mail" ? opened.name : null;
  const rows = agents.rows.map((row): FrameRow => {
    const open = row.name === openName;
    const to = !open ? null : opened.kind === "agent" ? opened.to : opened.kind === "mail" ? opened.qualified : null;
    return { row: stale ? dimmed(row) : row, note: noteText(row), open, to };
  });
  return {
    state: "drawn",
    reading: null,
    down,
    lastRead: down !== null && kept !== null ? `Last read ${when(kept.at)}. It may have changed since.` : null,
    stale,
    label: rows.length > 0 ? agentsLabel(rows.length) : null,
    rows,
    others: agents.others,
    empty,
    foot: up && kept !== null ? `${ANSWERING} · read ${when(kept.at)}` : null,
    lines,
    opened,
    placed: placed.opened.kind === "pending" ? null : placed,
  };
}

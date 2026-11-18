// turns' panel as a whole, worked out with no DOM so the Node tests can read it: its
// address, its state (loading, a failed read, empty, the first time, or up), the alert row
// a failed read or a failed scan puts first, the groups, and the foot: the reach line and
// the dim lines under it. Every string goes into the page as text, never as markup.

import type { Failure, Reach, Session, SessionList } from "./feed.ts";
import type { GroupView } from "./rows-model.ts";
import { groupsOf, isWaiting, isWatchAlone, LOCAL, timeWord } from "./rows-model.ts";
import type { Clock } from "./rows-model.ts";

// --- addresses ------------------------------------------------------------------------------

/** What follows `/m/turns/`: the panel with nothing opened (`key` null), one session's row
 * opened, or a route the page does not hold (`unknown`), which opens nothing. */
export interface Address {
  key: string | null;
  unknown: boolean;
}

const SESSION_PREFIX = "session/";

/** The address a route names. */
export function parseAddress(route: string): Address {
  const path = route.replace(/^\/+|\/+$/g, "");
  if (path === "") return { key: null, unknown: false };
  if (path.startsWith(SESSION_PREFIX)) {
    const rest = path.slice(SESSION_PREFIX.length);
    if (rest !== "" && !rest.includes("/")) {
      try {
        return { key: decodeURIComponent(rest), unknown: false };
      } catch {
        // Not a percent-encoding: no key can be read from it.
      }
    }
  }
  return { key: null, unknown: true };
}

/** The route for a session's row opened, or for the panel with nothing opened. */
export function addressRoute(key: string | null): string {
  return key === null ? "" : `${SESSION_PREFIX}${encodeURIComponent(key)}`;
}

// --- the reach line -------------------------------------------------------------------------

/** The command the reach line sets as code, with `Copy` after it. */
export const INSTALL_COMMAND = "moxi install turns";

/** The reach line: its words, and where it names the install, the command and the words
 * after it. */
export interface ReachLine {
  text: string;
  command: string | null;
  after: string | null;
}

const BY_HAND = "Codex and OpenCode sessions started by hand don't.";
const LAUNCHES = "Sessions Moxi launches in a terminal or a slot show here; other Codex and OpenCode sessions don't.";
export const ABSENT = "Claude Code wasn't found when turns was installed, so its sessions can't show. Once it is installed, run this again: ";

/**
 * The reach line for `reach` and whether launcher is installed. Each sentence holds for
 * every kind of session it covers: a Claude Code session with no window spawns no
 * endpoint, so only interactive ones are named, and launcher's headless launches are not
 * named at all. Without `codex`, launched Codex sessions don't run under turns' host, so
 * the launch sentences leave them out. With `reach` null, the rows for a `claude` of null.
 */
export function reachLine(reach: Reach | null, launcher: boolean): ReachLine {
  const claude = reach?.claude ?? null;
  const plain = (text: string): ReachLine => ({ text, command: null, after: null });
  if (!launcher) {
    switch (claude) {
      case "start":
        return plain(`Interactive Claude Code sessions show here from their start. ${BY_HAND}`);
      case "setup":
        return { text: "Interactive Claude Code sessions can't show here yet: run ", command: INSTALL_COMMAND,
                 after: `. ${BY_HAND}` };
      case "absent":
        return { text: ABSENT, command: INSTALL_COMMAND, after: null };
      default:
        return plain(BY_HAND);
    }
  }
  const launched = (text: string): string => (reach?.codex === false
    ? text.replace("sessions Moxi launches", "Claude Code and OpenCode sessions Moxi launches")
      .replace("Sessions Moxi launches", "Claude Code and OpenCode sessions Moxi launches")
      .replace("other Codex and OpenCode sessions", "Codex sessions and other OpenCode sessions")
    : text);
  switch (claude) {
    case "start":
      return plain(launched("Interactive Claude Code sessions show here from their start, and sessions Moxi "
                            + "launches in a terminal or a slot; other Codex and OpenCode sessions don't."));
    case "setup":
      return { text: launched(`${LAUNCHES} Interactive Claude Code sessions started by hand can't show yet: run `),
               command: INSTALL_COMMAND, after: null };
    default:
      return plain(launched(LAUNCHES));
  }
}

// --- the frame ------------------------------------------------------------------------------

/** The panel's data as its `Feed` holds it. */
export interface FeedState {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  value: SessionList | null;
  /** When the value was last read, on the server's clock; null before the first read. */
  readAt: number | null;
}

/** The first row a failed read or a failed scan puts at the top: a heavy lead, then its
 * words, and for a failed read when the rows were last read. */
export interface Alert {
  lead: string;
  rest: string;
  lastRead: string | null;
}

/** The panel as it is drawn. */
export interface Frame {
  /** `loading`: no answer yet, the first scan not done, or every listed session waits
   * for its first read; `empty`: no session is running; `first`: none can show yet;
   * `up`: rows to draw; `failed`: the route failed, the last rows kept dim; `gone`: a 404,
   * nothing drawn. */
  state: "loading" | "empty" | "first" | "up" | "failed" | "gone";
  alert: Alert | null;
  groups: GroupView[];
  /** The empty state's line, or null. */
  empty: string | null;
  /** The reach line, or null where it is not drawn. */
  reach: ReachLine | null;
  /** Dim lines after the reach line, each while true. */
  notes: string[];
  /** The opened session's key: the address's, while that session is drawn. */
  open: string | null;
}

export const READING = "Reading…";
export const NO_SESSION = "No session is running.";
export const NONE_CAN_SHOW = "No session can show here yet.";
export const ROUTE_FAILED = "Moxi can't read the live sessions";
export const SCAN_FAILED = "Moxi couldn't look for sessions";
export const NOT_RUNNING = "That session isn't running now.";
/** While a drawn session's watch is down and its reads answer: what shows comes from the
 * page server's timer, which reads every session again within 30 s. */
export const READ_ON_TIMER = "Showing what Moxi read up to 30 s ago";

/** What a failed route's answer says. */
export function failureWords(failure: Failure): string {
  if (failure.status === 0) return "No answer from the shell.";
  if (failure.status === 200) return "turns' page server sent an answer that can't be read.";
  return `turns' page server answered ${failure.status}.`;
}

/** The panel for the feed's data, the address, and whether launcher is installed. */
export function frameOf(data: FeedState, address: Address, launcher: boolean, today: string,
                        clock: Clock = LOCAL): Frame {
  const value = data.value;
  const sessions: Session[] = value?.sessions ?? [];
  const frame: Frame = { state: "loading", alert: null, groups: [], empty: null, reach: null, notes: [], open: null };
  if (data.gone) return { ...frame, state: "gone" };
  const drawn = sessions.filter((session) => !isWaiting(session));
  const groups = groupsOf(drawn);
  const listedKey = (key: string | null): boolean =>
    key !== null && sessions.some((session) => session.key === key);
  const notes = (): string[] => {
    const lines: string[] = [];
    if (drawn.some(isWatchAlone)) lines.push(READ_ON_TIMER);
    if (address.unknown || (address.key !== null && !listedKey(address.key))) lines.push(NOT_RUNNING);
    return lines;
  };
  const open = drawn.some((session) => session.key === address.key) ? address.key : null;
  if (data.failure !== null) {
    const lastRead = data.readAt === null || value === null
      ? null : `Last read ${timeWord(data.readAt, today, clock)}. It may have changed since.`;
    return { ...frame, state: "failed", groups, open: null,
             alert: { lead: ROUTE_FAILED, rest: failureWords(data.failure), lastRead } };
  }
  if (data.loading || value === null || !value.listed) return frame;
  const alert = value.detail === null ? null : { lead: SCAN_FAILED, rest: value.detail, lastRead: null };
  if (sessions.length === 0) {
    if (value.reach?.claude === "absent" && !launcher) {
      return { ...frame, state: "first", alert, empty: NONE_CAN_SHOW, reach: reachLine(value.reach, launcher),
               notes: notes() };
    }
    return { ...frame, state: "empty", alert, empty: NO_SESSION, reach: reachLine(value.reach, launcher),
             notes: notes() };
  }
  if (drawn.length === 0) return { ...frame, alert };
  return { ...frame, state: "up", alert, groups, reach: reachLine(value.reach, launcher), notes: notes(), open };
}

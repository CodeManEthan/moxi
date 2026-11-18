// The Services page's acts, worked out with no DOM so the Node tests can read them: which
// acts a row offers on its closed line and in its foot, by cause and kind; each act's
// confirm by act and row, the slots server's warning confirm from `/api/ends`; the phone's
// act set and its sheets; what a job's state and a press's answer say. Every string here
// goes into the page as text.
//
// The rule: a row offers an act only when the server takes it on that row and it changes
// something in the row's state; an act's confirm and guard belong to the act and the row,
// the same wherever it is pressed (closed row, foot, phone).

import type { Row } from "./feed.ts";
import { named } from "./list-model.ts";

/** An act as `/api/act` names it, and the two a timer's foot tucks away. */
export type ActName = "start" | "stop" | "restart" | "run" | "rerun" | "stop-run" | "enable" | "disable" | "remove";

/** One act a row offers. */
export interface Act {
  name: ActName;
  label: string;
  /** `/api/act`'s body, less the job the page mints per press. */
  body: { act: string; id: string; restart?: boolean };
  /** Tucked and dim in a timer's foot. */
  tucked: boolean;
  /** Drawn in the warning colour: the slots server's Stop and Restart while its last read
   * found something in use or couldn't read. Set by `warned`. */
  warn: boolean;
}

/** ui's shell: its Stop closes the page. */
export const SHELL = "ui/shell";
/** Beside his daemon's Stop. */
export const STOP_LASTS = "Stop lasts until the next login. Turn off keeps it off.";
/** In place of ui's shell's Stop, then its command with `Copy`. */
export const SHELL_STOP = "Stopping it closes this page, so it is done at the terminal:";
export const SHELL_STOP_COMMAND = `moxi services stop ${SHELL}`;
/** Beside ui's shell's Restart. */
export const SHELL_RESTART = "Restart reloads this page.";
/** Added to the label of a run of a timer whose runs launch agent sessions. */
export const SPENDS = " · spends usage";

const LABELS: Record<ActName, string> = {
  start: "Start", stop: "Stop", restart: "Restart", run: "Run now", rerun: "Run again", "stop-run": "Stop run",
  enable: "Turn on", disable: "Turn off", remove: "Remove",
};

function act(row: Row, name: ActName, tucked = false): Act {
  const label = LABELS[name] + ((name === "run" || name === "rerun") && row.launches_session ? SPENDS : "");
  const verb = name === "rerun" ? "run" : name === "stop-run" ? "stop" : name;
  const body: Act["body"] = { act: verb, id: row.id };
  if (name === "rerun") body.restart = true;
  return { name, label, body, tucked, warn: false };
}

const his = (row: Row): boolean => row.form !== "module";
const cause = (row: Row): string | null => (named(row) ? row.verdict.cause : null);

const RUNNING = new Set(["running", "running-off", "no-login", "health", "listening"]);
const TIMER = new Set(["armed", "last-run", "not-wrapped", "armed-off"]);
const NOT_RUN_HERE = new Set(["elsewhere", "removing", "no-supervisor", "not-here"]);

/** His `Turn on` or `Turn off`, as fits its flag; both when the flag can't be read. */
function onOff(row: Row): ActName[] {
  if (row.flag === true) return ["disable"];
  if (row.flag === false) return ["enable"];
  return ["enable", "disable"];
}

/** The acts a row offers, the closed line's (at most one) and the foot's, by its cause and
 * kind, his or Moxi's. A cause the page doesn't name offers the acts of its kind. ui's
 * shell offers no Stop. */
export function actsOf(row: Row): { closed: Act | null; foot: Act[] } {
  const c = cause(row);
  const mine = his(row);
  const names: ActName[] = [];
  let closed: ActName | null = null;
  const tucked: ActName[] = [];
  if (c === null || c === "unknown") {
    if (row.kind === "daemon") names.push("start", "restart", "stop");
    else names.push("run", "rerun", "stop-run");
    if (mine) names.push("enable", "disable", "remove");
  } else if (c === "switched-off") {
    // Its on and off is the module's setting.
  } else if (row.kind === "daemon" && (c === "not-running" || c === "stopped" || (mine && (c === "off" || c === "login-off")))) {
    closed = "start";
    names.push("start");
    if (mine) names.push(...onOff(row), "remove");
  } else if (row.kind === "daemon" && RUNNING.has(c)) {
    if (c === "health" && row.verdict.state === "fail") closed = "restart";
    names.push("restart", "stop");
    if (mine) names.push(...onOff(row), "remove");
  } else if (row.kind === "timer" && TIMER.has(c)) {
    if (c === "last-run") closed = "run";
    names.push("run");
    if (mine) names.push(...onOff(row), "remove");
    tucked.push("rerun", "stop-run");
  } else if (row.kind === "timer" && c === "off") {
    if (mine) {
      names.push("enable", "remove");
      tucked.push("run");
    }
  } else if (c === "missing") {
    if (mine && row.form === "written") names.push(...onOff(row));
    else if (mine) names.push("remove");
  } else if (c === "not-armed" || c === "schedule-failed") {
    if (mine) names.push("enable", "remove");
  } else if (NOT_RUN_HERE.has(c)) {
    if (mine) names.push("remove");
  }
  const keep = (name: ActName): boolean => !(row.id === SHELL && name === "stop");
  const foot = [...names.filter(keep).map((name) => act(row, name)),
                ...tucked.filter(keep).map((name) => act(row, name, true))];
  return { closed: closed !== null && keep(closed) ? act(row, closed) : null, foot };
}

/** The acts the phone offers: none with phone acts off; with them on, the desk's but
 * Remove (Add and Adopt are the panel's, which the phone never has). */
export function phoneActs(row: Row, actsOn: boolean): { closed: Act | null; foot: Act[] } {
  if (!actsOn) return { closed: null, foot: [] };
  const found = actsOf(row);
  return { closed: found.closed, foot: found.foot.filter((a) => a.name !== "remove") };
}

/** The phone's confirmed route for an act, relative to services' page, and its body, with
 * what the slots server's confirm sends (`Confirm.sends`) on a stop or restart. Remove,
 * which the phone never offers, has none. */
export function phoneRoute(a: Act, sends: Sends = {}): { path: string; body: Record<string, unknown> } {
  const body: Record<string, unknown> = { id: a.body.id };
  if (a.body.restart) body.restart = true;
  if (a.body.act === "stop" || a.body.act === "restart") Object.assign(body, sends);
  const paths: Record<string, string> = {
    start: "api/start", stop: "api/stop", restart: "api/restart", run: "api/run", enable: "api/enable",
    disable: "api/disable",
  };
  return { path: paths[a.body.act], body };
}

// -- the confirms -------------------------------------------------------------------------

/** What `/api/ends` answers: what a stop of slots' server would end now. The counts are
 * null when `read` is false: a count Moxi couldn't read is never a number. */
export interface Ends {
  read: boolean;
  why: string | null;
  sessions: { slot: number; label: string | null }[] | null;
  open: number | null;
  jobs: number | null;
}

/** How many things `ends` found in use; null when it couldn't read them. */
export function inUse(ends: Ends): number | null {
  if (!ends.read || ends.sessions === null || ends.open === null || ends.jobs === null) return null;
  return ends.sessions.length + ends.open + ends.jobs;
}

/** Whether the slots server's Stop and Restart show in the warning colour: the last read
 * found something in use or couldn't read. */
export function warned(ends: Ends | null): boolean {
  if (ends === null) return false;
  const n = inUse(ends);
  return n === null || n > 0;
}

/** What the slots server's Stop or Restart sends once its warning is confirmed: `ends`, the
 * count it showed in use, or `ends_unread`, that it said the slots couldn't be read. Never
 * both: `ends` 0 means nothing in use, as read. Empty for any other press. */
export type Sends = { ends?: number; ends_unread?: true };

/** An in-place confirm: its question and lines, the button that goes, the one that keeps,
 * whether it is in the warning colour, and what the press sends with it. */
export interface Confirm {
  ask: string;
  lines: { label: string | null; text: string }[];
  /** A command shown with `Copy`. */
  command: string | null;
  go: string;
  keep: string;
  warn: boolean;
  /** What the slots server's Stop or Restart sends with it; empty for any other. */
  sends: Sends;
}

/** The slots server's confirm when the slots couldn't be read: no count is shown. */
export const UNREAD = "Moxi can't read the slots, so stopping it may end sessions in them.";

const plural = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`;

/** The slots server's confirm for its Stop or Restart from `/api/ends`, or null when
 * nothing is in use (a plain press, no `ends`). `phone` drops the sessions' labels. */
export function slotsConfirm(a: Act, ends: Ends, phone: boolean): Confirm | null {
  const n = inUse(ends);
  const verb = a.body.act === "restart" ? "Restart" : "Stop";
  if (n === null) {
    return { ask: UNREAD,
             lines: [], command: null, go: `${verb} it anyway`, keep: "Keep it running", warn: true,
             sends: { ends_unread: true } };
  }
  if (n === 0) return null;
  const sessions = ends.sessions!;
  const count = sessions.length;
  const ended: string[] = [];
  if (!phone) for (const s of sessions) ended.push(s.label ? `${s.label} in slot ${s.slot}` : `slot ${s.slot}`);
  else if (count) ended.push(plural(count, "session", "sessions"));
  if (ends.open) ended.push(plural(ends.open, "open slot", "open slots"));
  if (ends.jobs) ended.push(plural(ends.jobs, "job", "jobs"));
  const what = count ? `end ${plural(count, "session", "sessions")}` : "end what runs in it";
  return {
    ask: count ? `${verb} the slots server and ${what}?` : `${verb} the slots server and end what runs in it?`,
    lines: [
      { label: "Ends now", text: ended.join(", ") },
      { label: "Stops", text: "launching into a slot, until it is started again" },
      { label: "Stays", text: "sessions outside the slots" },
      { label: null, text: "A working session is cut off mid-turn. This cannot be taken back." },
    ],
    command: null, go: `${verb} it and ${what}`, keep: "Keep it running", warn: true, sends: { ends: n },
  };
}

/** The usage confirm of a run of a timer whose runs launch agent sessions. */
export function usageConfirm(row: Row, a: Act): Confirm | null {
  if (!row.launches_session || (a.name !== "run" && a.name !== "rerun")) return null;
  return { ask: `Run ${row.name} now? Its runs launch agent sessions, so this spends usage.`, lines: [],
           command: null, go: a.name === "run" ? "Run now" : "Run again", keep: "Cancel", warn: false, sends: {} };
}

/** A word for a POSIX shell: as it is when it needs no quoting, else in single quotes. */
export function shellWord(word: string): string {
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(word) ? word : `'${word.replaceAll("'", "'\\''")}'`;
}

/** The command that adds his written row again, as `moxi services add` takes it; null when
 * a word of it didn't reach this page. */
export function addAgain(row: Row): string | null {
  if (row.form !== "written" || row.argv === null || row.argv.some((w) => w === null)) return null;
  const words = ["moxi", "services", "add", row.name];
  if (row.kind === "timer") {
    words.push("--timer");
    const t = row.timing;
    if (t?.every) words.push("--every", t.every);
    else if (t?.at?.length) {
      words.push("--at", t.at.join(","));
      if (t.days?.length) words.push("--days", t.days.join(","));
    }
  }
  if (row.cwd) words.push("--cwd", row.cwd);
  if (row.health !== null) {
    if (row.health.some((w) => w === null)) return null;
    words.push("--health", (row.health as string[]).map(shellWord).join(" "));
  }
  if (row.project) words.push("--project", row.project);
  if (row.launches_session) words.push("--launches-session");
  if (row.doc) words.push("--doc", row.doc);
  return [...words.map(shellWord), "--", ...(row.argv as string[]).map(shellWord)].join(" ");
}

/** Remove's confirm of his row: what goes, what stays, and for a written row the command
 * that adds it again. */
export function removeConfirm(row: Row): Confirm {
  const goes = row.form === "written" ? "its entry and the unit Moxi wrote"
    : row.platform === "win32" && row.kind === "timer" ? "its entry and the small file beside it"
      : "its entry";
  const stays = [row.cwd ? `everything in ${row.cwd}` : null, "its past lines in the system log"]
    .filter((s): s is string => s !== null).join(", ");
  const lines: Confirm["lines"] = [{ label: "Goes", text: goes }, { label: "Stays", text: stays }];
  const command = row.form === "written" ? addAgain(row) : null;
  lines.push({ label: null, text: row.form === "written"
    ? "Moxi keeps no copy. To have it back, add it again with this command:"
    : "Remove takes it off this list only." });
  return { ask: `Remove ${row.name}?`, lines, command, go: "Remove", keep: "Keep it", warn: false, sends: {} };
}

/** The phone's sheet for an act: the desk's confirm without its redacted words, else
 * `<Act> <name>.`; ui's shell's Restart says it reloads the page. */
export function phoneSheet(row: Row, a: Act, ends: Ends | null): string[] {
  const usage = usageConfirm(row, a);
  if (usage !== null) return [usage.ask];
  if (row.ends_slots && ends !== null && (a.body.act === "stop" || a.body.act === "restart")) {
    const confirm = slotsConfirm(a, ends, true);
    if (confirm !== null) return [confirm.ask, ...confirm.lines.map((l) => (l.label ? `${l.label}: ${l.text}` : l.text))];
  }
  const line = `${LABELS[a.name]} ${row.name}.`;
  return row.id === SHELL && a.name === "restart" ? [line, SHELL_RESTART] : [line];
}

// -- a job and a press's answer -----------------------------------------------------------

/** The row's fact while its job runs. */
export function runningFact(a: Act): string {
  switch (a.body.act) {
    case "start": return "starting";
    case "stop": return "stopping";
    case "restart": return "restarting";
    case "run": return "running now";
    case "enable": return "turning on";
    case "disable": return "turning off";
    default: return "removing";
  }
}

/** What a job that ended says in `State`, or null for `done` (the row shows its new state
 * from the topic). `again` is the act offered again: `Start again` for a start, else the
 * same act. `reread` asks for the rows again. */
export function jobSaid(a: Act, state: string): { heavy: string; again: string | null; reread: boolean } | null {
  if (state === "done" || state === "running") return null;
  if (state === "unknown") return { heavy: "It may have been done.", again: null, reread: true };
  const again = a.name === "start" ? "Start again" : a.label;
  return { heavy: state === "refused" ? "Not done." : `${a.label} did not hold.`, again, reread: false };
}

/** What a press's answer that isn't a 202 says, and whether the rows are read again. */
export function transportSaid(status: number, said: string | null): { text: string; reread: boolean } {
  if (status === 0) return { text: "No answer from the shell.", reread: false };
  if (status === 502 || status === 504) return { text: "Services' page server didn't answer; it may have been done.", reread: true };
  if (status === 409) return { text: "Another change holds the lock. Try again.", reread: false };
  if (status === 422 && said) return { text: said, reread: false };
  return { text: said ?? `Services' page server answered ${status}.`, reread: false };
}

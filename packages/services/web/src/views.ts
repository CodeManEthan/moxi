// What services' page and summary block show, as plain data: the rows grouped by
// project, each row's lines, the acts it offers with the confirm sheet of each, what an
// act came to, the add form's body, and the count by verdict. No DOM and no Lit here, so
// the Node tests check it directly.

import type { ConfirmOutcome } from "/m/ui/client.js";

import { VERDICTS } from "./feed.ts";
import type { Failure, Row, Rows, Status, Verdict, VerdictState } from "./feed.ts";

/** The words shown where the phone's gateway left out a value. */
export const HIDDEN = "hidden here";
/** What an act whose answer was lost may have done. */
export const MAY_HAVE_ACTED = "No answer says how the act went, so it may have been done; the list shows what stands now.";
/** The group of the rows that name no project. */
export const NO_PROJECT = "No project";

export type Tone = "ok" | "warn" | "error" | "muted";

/** What a component reads off a feed. */
export interface FeedState<V> {
  value: V | null;
  failure: Failure | null;
  gone: boolean;
  loading: boolean;
}

// -- times --------------------------------------------------------------------------------

/** A UTC time as the page shows it: `2026-11-03 14:05 UTC`. */
export function when(iso: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(:\d{2}(\.\d+)?)?Z$/.exec(iso);
  return match ? `${match[1]} ${match[2]} UTC` : iso;
}

// -- the list -----------------------------------------------------------------------------

export interface Group {
  /** The project key, null for the rows that name none. */
  project: string | null;
  label: string;
  rows: Row[];
}

/** The rows grouped by project: each project's key in order, ignoring case, and then the
 * rows that name none. Rows keep their order within a group. */
export function groups(rows: readonly Row[]): Group[] {
  const by = new Map<string, Row[]>();
  const loose: Row[] = [];
  for (const row of rows) {
    if (row.project === null) {
      loose.push(row);
      continue;
    }
    const list = by.get(row.project) ?? [];
    list.push(row);
    by.set(row.project, list);
  }
  const keys = [...by.keys()].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()) || a.localeCompare(b));
  const found: Group[] = keys.map((key) => ({ project: key, label: key, rows: by.get(key)! }));
  if (loose.length) found.push({ project: null, label: NO_PROJECT, rows: loose });
  return found;
}

export interface ListView {
  state: "loading" | "up" | "empty" | "error" | "failed" | "gone";
  /** The line to show for every state but `up` and `loading`. */
  line: string | null;
  groups: Group[];
  /** The modules whose run-time declarations failed, each as a line. */
  problems: string[];
}

/** The list part's view. A failed read keeps the rows last read, and says so. */
export function listView(feed: FeedState<Rows>): ListView {
  if (feed.gone) return { state: "gone", line: "services is no longer installed.", groups: [], problems: [] };
  const value = feed.value;
  const problems = (value?.problems ?? []).map((p) =>
    `${p.module}: ${p.reason ?? HIDDEN}; its static daemons and timers are listed`);
  const found = groups(value?.rows ?? []);
  if (value === null) {
    if (feed.loading) return { state: "loading", line: null, groups: [], problems: [] };
    return { state: "failed", line: `The rows can't be read: ${failureLine(feed.failure)}.`, groups: [], problems: [] };
  }
  if (feed.failure !== null) {
    return { state: "failed", line: `The rows can't be read again: ${failureLine(feed.failure)}. These are the rows last read.`,
             groups: found, problems };
  }
  if (value.state === "error") {
    return { state: "error", line: `The rows can't be listed: ${value.detail ?? HIDDEN}`, groups: [], problems };
  }
  if (value.rows.length === 0) {
    return { state: "empty", line: "No daemon or timer yet: no installed module declares one and none was added.",
             groups: [], problems };
  }
  return { state: "up", line: null, groups: found, problems };
}

function failureLine(failure: Failure | null): string {
  if (failure === null) return "no answer";
  return failure.said ? `${failure.why} (${failure.said})` : failure.why;
}

// -- one row ------------------------------------------------------------------------------

export function verdictTone(state: VerdictState): Tone {
  return state === "ok" ? "ok" : state === "warn" ? "warn" : state === "fail" ? "error" : "muted";
}

/** The verdict's word and its reason, as one line: `fail: not running`. */
export function verdictLine(verdict: Verdict): string {
  return `${verdict.state}: ${verdict.reason ?? HIDDEN}`;
}

/** The row's on/off: its switch, its flag, or that it has neither. */
export function flagLine(row: Row): string {
  const word = row.flag === null ? "can't be read" : row.flag ? "on" : "off";
  if (!row.has_switch) return "no switch: on while its module is installed";
  if (row.switch !== null) return `switch ${row.switch}: ${word}`;
  return `flag: ${word}`;
}

/** A timer's last run, or how long a daemon has run or since when it was stopped. */
export function timeLine(row: Row): string | null {
  if (row.kind === "timer") {
    const last = row.last_run;
    if (last === null) return row.schedule ? `${row.schedule}; no run recorded` : "no run recorded";
    const at = last.at ? ` at ${when(last.at)}` : "";
    const schedule = row.schedule ? `${row.schedule}; ` : "";
    return `${schedule}last run ${last.text ?? HIDDEN}${at}`;
  }
  if (row.state === "active" && row.since) return `running since ${when(row.since)}`;
  if (row.stopped) return `stopped by hand at ${when(row.stopped)}`;
  if (row.since) return `${row.state ?? "state unknown"} since ${when(row.since)}`;
  return null;
}

/** A running daemon's last health check, or null when there is none to show. */
export function healthLine(row: Row): { tone: Tone; text: string } | null {
  const checked = row.checked;
  if (checked === null) return null;
  const tone: Tone = checked.state === "ok" ? "ok" : checked.state === "fail" ? "error" : checked.state === "warn" ? "warn" : "muted";
  return { tone, text: `health ${checked.state}: ${checked.line ?? HIDDEN}` };
}

/** A command line as one string, words with a space or a quote quoted. */
export function commandLine(argv: readonly (string | null)[] | null): string | null {
  if (argv === null) return null;
  return argv.map((word) => word === null ? HIDDEN : /[\s"'\\]/.test(word) || word === "" ? JSON.stringify(word) : word).join(" ");
}

/** The row's facts for its detail list: label and value, only those it has. */
export function rowFacts(row: Row): { name: string; label: string; value: string }[] {
  const facts: { name: string; label: string; value: string }[] = [];
  const add = (name: string, label: string, value: string | null | undefined): void => {
    if (value !== null && value !== undefined && value !== "") facts.push({ name, label, value });
  };
  add("form", "Form", row.form === "module" ? `declared by ${row.module}` : row.form);
  add("command", "Command", commandLine(row.argv));
  add("unit", "Unit", row.unit);
  add("cwd", "Directory", row.cwd);
  add("schedule", "Schedule", row.kind === "timer" ? row.schedule : null);
  add("health", "Health command", commandLine(row.health));
  add("state", "Supervisor", row.state === null ? null
    : `${row.state}${row.enabled === null ? "" : row.enabled ? ", enabled" : ", not enabled"}${row.pid !== null ? `, pid ${row.pid}` : ""}`);
  add("detail", "Detail", row.detail);
  add("fix", "Fix", row.verdict.fix);
  add("platform", "Added on", row.platform);
  return facts;
}

// -- the acts -----------------------------------------------------------------------------

export type ActName = "start" | "stop" | "restart" | "run" | "rerun" | "enable" | "disable" | "remove";

/** One act a row offers. */
export interface Act {
  name: ActName;
  label: string;
  /** Relative to services' page. */
  path: string;
  body: { id: string; restart?: boolean };
  /** The confirm sheet's lines. */
  sheet: string[];
  /** Done only at the desk: the phone hides it. */
  desk: boolean;
}

/** The line a `launches_session` row's run adds to its sheet. */
export const SPENDS = "Each run launches an agent session, so this run spends usage.";

function sheetOf(row: Row, name: ActName): string[] {
  const it = row.name;
  const lines: string[] = [];
  switch (name) {
    case "start":
      lines.push(`Starts ${it} now, as moxi services start ${row.id} does.`);
      break;
    case "stop":
      lines.push(row.kind === "timer"
        ? `Ends ${it}'s run in progress; its schedule stays as it is.`
        : `Stops ${it} until the next login or start; its flag stays as it is.`);
      break;
    case "restart":
      lines.push(`Stops ${it} and starts it again.`);
      break;
    case "run":
      lines.push(`Runs ${it} once now; its schedule stays as it is.`);
      break;
    case "rerun":
      lines.push(`Ends ${it}'s run in progress, if one runs, and runs it once now.`);
      break;
    case "enable":
      lines.push(row.switch !== null ? `Turns ${it} on through its switch, ${row.switch}.`
        : row.kind === "timer" ? `Turns ${it}'s flag on and arms its schedule.`
        : `Turns ${it}'s flag on: it runs now and starts at login.`);
      break;
    case "disable":
      lines.push(row.switch !== null ? `Turns ${it} off through its switch, ${row.switch}.`
        : row.kind === "timer" ? `Turns ${it}'s flag off; a run in progress finishes.`
        : `Turns ${it}'s flag off: it stops and no longer starts at login.`);
      break;
    case "remove":
      lines.push(row.form === "adopted"
        ? `services forgets ${it}; its unit stays on the system as it was.`
        : `Removes ${it}'s unit, then its row. Add it again to have it back.`);
      break;
  }
  if ((name === "run" || name === "rerun" || name === "start" || name === "restart") && row.launches_session) {
    lines.push(SPENDS);
  }
  return lines;
}

function act(row: Row, name: ActName, label: string, path: string, extra: { restart?: boolean } = {}): Act {
  return { name, label, path, body: { id: row.id, ...extra }, sheet: sheetOf(row, name), desk: name === "remove" };
}

/** The acts a row offers, in the order the page shows them. A timer runs through
 * `/api/run` in place of start and restart; a module row takes no remove; a module unit
 * without a switch takes no enable or disable. */
export function actsOf(row: Row): Act[] {
  const found: Act[] = row.kind === "timer"
    ? [act(row, "run", "Run now", "api/run"), act(row, "rerun", "Run again", "api/run", { restart: true }),
       act(row, "stop", "Stop run", "api/stop")]
    : [act(row, "start", "Start", "api/start"), act(row, "stop", "Stop", "api/stop"),
       act(row, "restart", "Restart", "api/restart")];
  if (row.has_switch) found.push(act(row, "enable", "Enable", "api/enable"), act(row, "disable", "Disable", "api/disable"));
  if (row.form !== "module") found.push(act(row, "remove", "Remove…", "api/remove"));
  return found;
}

/** The acts the page shows here: on the phone, those the phone may run. */
export function shownActs(row: Row, phone: boolean): Act[] {
  return actsOf(row).filter((a) => !(phone && a.desk));
}

// -- what an act came to ------------------------------------------------------------------

export interface Said {
  tone: Tone;
  lines: string[];
}

/** What a `confirm()` that didn't end in a job says, or null for `accepted` and for
 * `cancelled` and `desk`, which say nothing more. */
export function outcomeSaid(outcome: ConfirmOutcome): Said | null {
  switch (outcome.outcome) {
    case "refused":
      return { tone: "error", lines: [outcome.text] };
    case "unknown":
      return { tone: "warn", lines: [outcome.text, MAY_HAVE_ACTED] };
    case "accepted":
    case "cancelled":
    case "desk":
      return null;
  }
}

const ENDED: Record<string, string> = {
  done: "Done.",
  failed: "It failed.",
  refused: "Refused; nothing changed.",
  unknown: "This page server holds no record of the act.",
};

/** What a job's status says: its state as a line, then its lines and fix. */
export function statusSaid(label: string, status: Status): Said {
  if (status.state === "running") return { tone: "muted", lines: [`${label}: running…`] };
  const tone: Tone = status.state === "done" ? "ok" : status.state === "unknown" ? "warn" : "error";
  const lines = [`${label}: ${ENDED[status.state]}`, ...status.lines.map((line) => line ?? HIDDEN)];
  if (status.fix) lines.push(`Fix: ${status.fix}`);
  return { tone, lines };
}

/** What a status read that failed says: the act was taken, so it may still run. */
export function statusLost(label: string, failure: Failure | null): Said {
  return { tone: "warn", lines: [`${label}: taken, but what it came to can't be read (${failureLine(failure)}).`, MAY_HAVE_ACTED] };
}

// -- the add form -------------------------------------------------------------------------

export const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

/** What the add form holds, as typed. */
export interface AddForm {
  name: string;
  kind: "daemon" | "timer";
  /** `command` writes a unit from a command line; `unit` adopts one already there. */
  source: "command" | "unit";
  command: string;
  unit: string;
  /** A written row: start it now. An adopted row: on or off now (`""` until chosen). */
  enable: boolean;
  adoptOn: "" | "on" | "off";
  /** A written timer's schedule: `every` (`5m`, `2h`) or `at` (`09:00, 17:30`). */
  schedule: "every" | "at";
  every: string;
  at: string;
  days: string[];
  cwd: string;
  health: string;
  project: string;
  launchesSession: boolean;
  doc: string;
}

/** A new form. A written row starts off, as `moxi services add` leaves it without
 * `--enable`: the user ticks "Turn it on now" to start it now and at every login. */
export function emptyForm(): AddForm {
  return { name: "", kind: "daemon", source: "command", command: "", unit: "", enable: false, adoptOn: "",
           schedule: "every", every: "", at: "", days: [], cwd: "", health: "", project: "",
           launchesSession: false, doc: "" };
}

/** `/api/add`'s body from the form, or the one line that says what the form still
 * needs. The server checks it again and has the last word. */
export function addBody(form: AddForm): { body: Record<string, unknown> } | { missing: string } {
  const name = form.name.trim();
  if (name === "") return { missing: "Give the row a name." };
  const body: Record<string, unknown> = { name, kind: form.kind };
  if (form.source === "command") {
    const command = form.command.trim();
    if (command === "") return { missing: "Give the command line to run, its first word a full path." };
    body.command = command;
    body.enable = form.enable;
    if (form.kind === "timer") {
      if (form.schedule === "every") {
        const every = form.every.trim();
        if (every === "") return { missing: "Give how often the timer runs, such as 5m or 2h." };
        body.every = every;
      } else {
        const at = form.at.split(/[\s,]+/).filter(Boolean);
        if (at.length === 0) return { missing: "Give the times the timer runs at, such as 09:00, 17:30." };
        body.at = at;
        if (form.days.length) body.days = DAYS.filter((day) => form.days.includes(day));
      }
    }
    const cwd = form.cwd.trim();
    if (cwd !== "") body.cwd = cwd;
  } else {
    const unit = form.unit.trim();
    if (unit === "") return { missing: "Give the unit to adopt, as the supervisor names it." };
    if (form.adoptOn === "") return { missing: "Say whether the adopted row is on or off now." };
    body.unit = unit;
    body.enable = form.adoptOn === "on";
  }
  const health = form.health.trim();
  if (health !== "") body.health = health;
  const project = form.project.trim();
  if (project !== "") body.project = project;
  if (form.launchesSession) body.launches_session = true;
  const doc = form.doc.trim();
  if (doc !== "") body.doc = doc;
  return { body };
}

/** What an add POST's answer says when it isn't a 202: its refusal, or why there was
 * none. */
export function addRefused(status: number, said: string | null): Said {
  if (status === 0) return { tone: "warn", lines: ["No answer from the shell; the add may have been taken. The list shows what stands now."] };
  if (status === 409) return { tone: "error", lines: [said ?? "services is busy; try again."] };
  if (status >= 500) return { tone: "warn", lines: [`services' page server answered ${status}; the add may have been taken. The list shows what stands now.`] };
  return { tone: "error", lines: [said ?? `services' page server answered ${status}`] };
}

// -- the summary block ----------------------------------------------------------------------

export type Counts = Record<VerdictState, number>;

/** How many rows are in each verdict. */
export function counts(rows: readonly Row[]): Counts {
  const found = { ok: 0, warn: 0, fail: 0, off: 0 } as Counts;
  for (const row of rows) found[row.verdict.state] += 1;
  return found;
}

export interface SummaryView {
  state: "loading" | "up" | "error" | "failed" | "gone";
  counts: Counts | null;
  /** The worst verdict any row has, for the block's tone; null without rows. */
  worst: VerdictState | null;
  line: string | null;
}

/** The summary block's view: the counts, and a line when there are none to show. */
export function summaryView(feed: FeedState<Rows>): SummaryView {
  if (feed.gone) return { state: "gone", counts: null, worst: null, line: null };
  const value = feed.value;
  if (value === null) {
    if (feed.loading) return { state: "loading", counts: null, worst: null, line: null };
    return { state: "failed", counts: null, worst: null, line: `Can't reach services' page server: ${failureLine(feed.failure)}.` };
  }
  if (value.state === "error") return { state: "error", counts: null, worst: null, line: `The rows can't be listed: ${value.detail ?? HIDDEN}` };
  const found = counts(value.rows);
  const worst = found.fail ? "fail" : found.warn ? "warn" : found.ok ? "ok" : value.rows.length ? "off" : null;
  const line = value.rows.length === 0 ? "No daemon or timer yet." : null;
  return { state: "up", counts: found, worst, line };
}

export { VERDICTS };

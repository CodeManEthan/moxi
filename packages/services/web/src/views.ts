// What services' page and summary block show, as plain data: each row's lines, what an
// act came to, and the count by verdict. No DOM and no Lit here, so
// the Node tests check it directly.

import type { ConfirmOutcome } from "/m/ui/client.js";

import { VERDICTS } from "./feed.ts";
import type { Failure, Row, Rows, Status, Verdict, VerdictState } from "./feed.ts";

/** The words shown where the phone's gateway left out a value. */
export const HIDDEN = "hidden here";
/** What an act whose answer was lost may have done. */
export const MAY_HAVE_ACTED = "No answer says how the act went, so it may have been done; the list shows what stands now.";

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

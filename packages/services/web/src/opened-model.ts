// The Services page's opened row, worked out with no DOM so the Node tests can read it:
// its labelled lines, each only when its fact is there, in the drawing's order. Every
// string here goes into the page as text.

import type { Ends } from "./acts-model.ts";
import { actsOf, inUse } from "./acts-model.ts";
import type { When } from "./facts.ts";
import type { Row } from "./feed.ts";
import { JOIN, rowFact, timeWords, timingWords } from "./list-model.ts";

/** One run line of `/api/row`: when, its result, its exit code and how long it took. */
export interface Run {
  at: string | null;
  result: string | null;
  exit: number | null;
  seconds: number | null;
}

/** A timer's runs today, of the newest 500 run lines read. */
export interface Today {
  runs: number;
  failed: number;
  more: boolean;
}

/** What only `/api/row` gives an opened row. */
export interface One {
  runs: Run[];
  log: string | null;
  unit: string | null;
  units: string[];
  today: Today | null;
}

/** One labelled line: its label, its text, dim words after it, a command or path shown in
 * mono with `Copy`, and a link. */
export interface Line {
  label: string;
  text: string | null;
  dim?: string | null;
  mono?: string | null;
  /** The mono text has `Copy` beside it. */
  copy?: boolean;
  link?: { text: string; module: string; route: string } | null;
  /** More lines under it (the runs, the details). */
  more?: string[];
}

/** What the opened row is drawn against. */
export interface Opened {
  /** The phone: only the lines whose facts reach it. */
  phone: boolean;
  /** usage is installed: `Usage` links to its page. */
  usage: boolean;
  /** The module page a Moxi row links to, when it has one; else `Modules`. */
  modulePage: string | null;
}

/** How often the page runs his own health check while it is open, in seconds. */
export const HEALTH_EVERY_S = 30;

const sentence = (text: string): string => `${text[0].toUpperCase()}${text.slice(1)}.`;

/** A run's length: `1.9 s`, `7 min 12 s`, `2 h 5 min`. */
export function lengthWords(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds * 10) / 10} s`;
  const whole = Math.round(seconds);
  const h = Math.floor(whole / 3600);
  const min = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  if (h) return min ? `${h} h ${min} min` : `${h} h`;
  return s ? `${min} min ${s} s` : `${min} min`;
}

/** One run line: `02:00 ok · 1.9 s`, `exit 1 · 12.4 s`, `held off by its switch`, `did not
 * start`. */
export function runWords(run: Run, when: When): string {
  const result = run.result === "off" ? "held off by its switch"
    : run.result === "not-started" ? "did not start"
      : run.result === "ran" ? (run.exit === 0 || run.exit === null ? "ok" : `exit ${run.exit}`)
        : "can't be read";
  const time = timeWords(run.at, when);
  const words = time === null ? result : `${time} ${result}`;
  return run.seconds !== null && run.result === "ran" ? `${words}${JOIN}${lengthWords(run.seconds)}` : words;
}

/** `Needs it` on slots' server: what runs in its slots, from `/api/ends`; null when it
 * couldn't be read. The phone gets the count alone. */
export function needsIt(ends: Ends | null, phone: boolean): string | null {
  if (ends === null || inUse(ends) === null) return null;
  if (inUse(ends) === 0) return "Nothing is running in its slots.";
  const sessions = ends.sessions!;
  const extra = [ends.open ? `${ends.open} open slot${ends.open === 1 ? "" : "s"}` : null,
                 ends.jobs ? `${ends.jobs} job${ends.jobs === 1 ? "" : "s"}` : null].filter(Boolean);
  const parts: string[] = [];
  if (sessions.length) {
    const n = sessions.length;
    const head = `${n} session${n === 1 ? " is" : "s are"} running in its slots`;
    parts.push(phone ? `${head}.`
      : `${head}: ${sessions.map((s) => (s.label ? `${s.label} in slot ${s.slot}` : `slot ${s.slot}`)).join(", ")}.`);
  }
  parts.push(["Stopping or restarting it ends them.", ...extra].join(JOIN));
  return parts.join(" ");
}

/** Whether the verdict's fix is one of the foot's acts (`moxi services start <id>`), so it
 * is not shown again as a command. */
function fixIsAct(row: Row, fix: string): boolean {
  const verb = /^moxi services (\S+) \S+$/.exec(fix)?.[1];
  if (verb === undefined) return false;
  return actsOf(row).foot.some((a) => a.body.act === verb);
}

function stateLine(row: Row, one: One | null, when: When): Line {
  const found = rowFact(row, when);
  const text = found.line !== null && found.rise === "fail" ? found.line : sentence(found.fact);
  const more: string[] = [];
  const checked = row.checked;
  if (row.form === "module" && checked !== null && checked.line !== null) {
    const at = timeWords(checked.at, when);
    more.push(checked.state === "ok" ? `healthy: ${checked.line}${at ? `, checked ${at}` : ""}` : checked.line);
  }
  if (row.kind === "timer") {
    const timing = timingWords(row.timing);
    if (timing !== null) more.push(`On schedule: ${timing}`);
    const today = one?.today ?? null;
    if (today !== null) {
      const runs = today.more ? "500+ runs today" : `${today.runs} run${today.runs === 1 ? "" : "s"} today`;
      more.push(`${runs}, ${today.failed ? `${today.failed} failed` : "none failed"}`);
    }
  }
  const fix = row.verdict.fix;
  const shows = (row.verdict.state === "fail" || row.verdict.state === "warn") && fix && !fixIsAct(row, fix);
  return { label: "State", text, dim: row.state === "active" && row.pid !== null ? `pid ${row.pid}` : null,
           mono: shows ? fix : null, copy: Boolean(shows), more };
}

/** An adopted Windows timer, a task Task Scheduler runs: what Moxi can know of it. */
export function adoptedTask(row: Row): boolean {
  return row.platform === "win32" && row.form === "adopted" && row.kind === "timer";
}

export const KNOWN = "Known";
export const NOT_KNOWN = "Not known";
export const TASK_KNOWN = "whether the task is enabled, when it last ran, and that run's result code";
export const TASK_NOT_KNOWN = "whether the program it started is still running, and what it printed: "
  + "Task Scheduler keeps neither.";
/** The advice under `Not known`, dim. */
export const TASK_JUDGED = "It is judged as a timer. If it is meant to keep running, remove it and adopt it again "
  + "as a program that keeps running; it is then judged by whether it runs.";

/** The opened row's lines, in the drawing's order, each only when its fact is there. */
export function openedLines(row: Row, one: One | null, ends: Ends | null, when: When, opened: Opened): Line[] {
  const lines: Line[] = [];
  const desk = !opened.phone;
  const mine = row.form !== "module";
  if (desk && row.doc) lines.push({ label: "For", text: row.doc });
  lines.push(stateLine(row, one, when));
  if (desk && mine && row.checked !== null && row.health !== null) {
    const ok = row.checked.state === "ok";
    const at = timeWords(row.checked.at, when);
    lines.push({ label: "Health", text: `${ok ? "ok" : "failing"}: ${row.checked.line ?? ""}`.trim(),
                 dim: [at ? `checked ${at}` : null, `your own check, run every ${HEALTH_EVERY_S} s while this page is open`]
                   .filter(Boolean).join(JOIN),
                 mono: row.health.every((w) => w !== null) ? row.health.join(" ") : null });
  }
  if (one !== null && one.runs.length) {
    lines.push({ label: "Last runs", text: null, more: [...one.runs].reverse().map((run) => runWords(run, when)) });
  }
  if (row.launches_session) {
    lines.push({ label: "Usage", text: "Its runs launch agent sessions, so they spend usage",
                 link: opened.usage ? { text: "Usage", module: "usage", route: "" } : null });
  }
  if (row.ends_slots) {
    const text = needsIt(ends, opened.phone);
    if (text !== null) lines.push({ label: "Needs it", text });
  }
  if (desk && one?.log) lines.push({ label: "Log", text: null, mono: one.log, copy: true });
  if (desk && mine && row.argv !== null && row.argv.every((w) => w !== null)) {
    const on = row.flag === null ? null : !row.flag ? "off" : row.kind === "daemon" ? "on at login" : "on";
    lines.push({ label: "Command", text: null, mono: row.argv.join(" "),
                 dim: [row.kind === "daemon" ? "keeps running" : "on a schedule", on].filter(Boolean).join(JOIN),
                 more: row.cwd ? [`Folder ${row.cwd}`] : [] });
  }
  if (!mine && row.switch !== null) {
    lines.push({ label: "Switch", text: row.flag === null ? "can't be read" : row.flag ? "on" : "off", dim: row.switch,
                 link: { text: `Settings › ${row.module}`, module: "ui", route: `settings/${row.module}` } });
  }
  if (adoptedTask(row)) {
    lines.push({ label: KNOWN, text: TASK_KNOWN }, { label: NOT_KNOWN, text: TASK_NOT_KNOWN, more: [TASK_JUDGED] });
  }
  const unit = one?.unit ?? null;
  if (unit !== null) {
    const how = row.form === "written" ? "a command you added; Moxi wrote the unit"
      : row.form === "adopted" ? "adopted; Moxi never writes its file"
        : row.has_switch ? `comes with ${row.module}`
          : `comes with ${row.module}${JOIN}on while ${row.module} is installed`;
    lines.push({ label: "Unit", text: null, mono: unit, copy: true, dim: how });
  }
  if (desk) {
    const more = [row.detail, row.verdict.reason, row.verdict.fix, row.id, ...(one?.units ?? [])]
      .filter((s): s is string => typeof s === "string" && s !== "");
    lines.push({ label: "Details", text: null, more: [...new Set(more)] });
  }
  return lines;
}

/** The links under a Moxi row's foot: its module's page, else `Modules`; `Settings ›
 * <module>` when the module has settings. */
export function footLinks(row: Row, opened: Opened): { text: string; module: string; route: string }[] {
  if (row.form !== "module") return [];
  const links = [opened.modulePage !== null
    ? { text: row.module, module: opened.modulePage, route: "" }
    : { text: "Modules", module: "ui", route: "modules" }];
  if (row.has_settings) links.push({ text: `Settings › ${row.module}`, module: "ui", route: `settings/${row.module}` });
  return links;
}

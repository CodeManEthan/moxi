// The Services page's list, worked out with no DOM so the Node tests can read it: each
// row's fact and its weight, line two of a row that rises, what rises and in what order,
// the first group's counts, the groups, line one's dim words and a timer's timing in
// words. Every string here goes into the page as text.
//
// It reads services' own judgment, each row's verdict and its `cause`, by the table in
// `FACTS`: a pair of cause and verdict state the table does not name reads by its verdict
// state alone (`FALLBACK`), never by the verdict's reason. A raw field is read only for a
// time, a count, a name or a word.

import { epochOf, LOCAL, whenWord } from "./facts.ts";
import type { When } from "./facts.ts";
import type { Row, Timing } from "./feed.ts";

/** How a fact is drawn: heavy, in the text colour, or dim. */
export type Weight = "heavy" | "text" | "dim";

/** Why a row rises above the groups, in the order the risen rows go; null when it stays
 * in its group. */
export type Rise = "fail" | "last-run" | "unknown" | null;

/** A row's fact at line one's right, its weight, whether it rises, and line two. */
export interface RowFact {
  fact: string;
  weight: Weight;
  rise: Rise;
  /** Line two's sentence, on a risen row only; null for none. */
  line: string | null;
}

/** The phrases of a line are joined by this. */
export const JOIN = " · ";
/** Line one's dim words for a row whose runs launch agent sessions. */
export const SPENDS = "spends usage";
/** The group of his rows without projects installed, and of Moxi's rows. */
export const YOURS = "Yours";
export const MOXI = "Moxi";
/** The group of his rows that name no project, with projects installed. */
export const NO_PROJECT = "No project";

// -- times --------------------------------------------------------------------------------

/** A time services gives, in words against now (`08:19`, `yesterday 22:10`, `Nov 2
 * 08:52`); null when there is none or it isn't one. */
export function timeWords(iso: string | null | undefined, when: When): string | null {
  const at = epochOf(iso ?? null);
  return at === null ? null : whenWord(at, when.now, when.clock ?? LOCAL);
}

/** `words` then ` <time>` when the time is there: a time leaves out its clause when it is
 * absent (`running since` alone reads `running`). */
function clause(words: string, joiner: string, time: string | null): string {
  return time === null ? words : `${words} ${joiner}${time}`;
}

// -- the facts ----------------------------------------------------------------------------

const PLATFORMS: Record<string, string> = { linux: "Linux", darwin: "macOS", win32: "Windows" };

/** The system a row was added on, in words. */
export function systemWord(platform: string | null): string {
  if (platform === null) return "another system";
  return PLATFORMS[platform] ?? platform;
}

function running(row: Row, when: When): string {
  return clause("running", "since ", timeWords(row.since, when));
}

/** The fact a timer's last run failed reads: `last run failed at <time>`. */
function lastRunFailed(row: Row, when: When): string {
  return clause("last run failed", "at ", timeWords(row.last_run?.at, when));
}

/** A command's first word without its directory. */
function commandName(argv: Row["argv"]): string | null {
  const first = argv?.[0];
  if (first === null || first === undefined) return null;
  const name = first.split(/[\\/]/).pop() ?? "";
  return name === "" ? null : name;
}

/** Line two of a `last-run` row: what its last run, or its run of failed runs, exited
 * with. The command's name only where it reached this page (not on the phone). */
export function lastRunLine(row: Row, when: When): string {
  const code = row.last_run?.exit ?? null;
  const ended = code === null ? "failed." : `exited with code ${code}.`;
  const streak = row.streak;
  if (streak !== null && streak.count > 1) {
    const since = timeWords(streak.since, when);
    const ends = streak.same_exit ? ended : "failed.";
    return since === null ? `Its last ${streak.count} runs ${ends}` : `Every run since ${since} ${ends}`;
  }
  const name = commandName(row.argv);
  return name === null ? `Its last run ${ended}` : `${name} ${ended}`;
}

type Facts = (row: Row, when: When) => RowFact;

const fact = (text: string, weight: Weight, rise: Rise = null, line: string | null = null): RowFact =>
  ({ fact: text, weight, rise, line });

/** The cause table: each pair of `cause` and verdict state the verdict gives, keyed
 * `<cause> <state>`. */
export const FACTS: Readonly<Record<string, Facts>> = {
  "not-running fail": (row) => fact("down", "heavy", "fail",
    row.state === "failed" ? "It failed and is not running." : "It is not running."),
  "missing fail": () => fact("unit missing", "heavy", "fail", "Its unit is missing, so nothing runs it."),
  "missing warn": () => fact("off · unit missing", "text"),
  "not-armed fail": () => fact("not scheduled", "heavy", "fail", "It is on, but its schedule is not armed."),
  "schedule-failed fail": () => fact("schedule failed", "heavy", "fail", "Its schedule failed, so it does not run."),
  "health fail": (row) => fact("failing its health check", "heavy", "fail", row.checked?.line ?? null),
  "last-run warn": (row, when) => fact(lastRunFailed(row, when), "text", "last-run", lastRunLine(row, when)),
  "unknown warn": (row) => fact("can't tell", "text", "unknown", row.verdict.reason || null),
  "running ok": (row, when) => fact(running(row, when), "dim"),
  "health warn": (row, when) => fact(running(row, when), "dim"),
  "listening ok": () => fact("ready, starts on its first connection", "dim"),
  "armed ok": (row, when) => fact(row.last_run === null ? "no run yet"
    : clause("last ran", "", timeWords(row.last_run.at, when)), "dim"),
  "stopped warn": (row, when) => fact(clause("stopped by hand", "at ", timeWords(row.stopped, when)), "text"),
  "off off": () => fact("off", "dim"),
  "switched-off off": () => fact("off", "dim"),
  "running-off warn": (row, when) => fact(`${running(row, when)}${JOIN}off at login`, "dim"),
  "no-login warn": (row, when) => fact(`${running(row, when)}${JOIN}won't start at login`, "text"),
  "login-off warn": () => fact("off, but starts at login", "text"),
  "armed-off warn": () => fact("off, but its schedule is armed", "text"),
  "not-wrapped warn": () => fact("runs not recorded", "text"),
  "removing warn": () => fact("remove not finished", "text"),
  "elsewhere off": (row) => fact(`added on ${systemWord(row.platform)}`, "dim"),
  "not-here off": () => fact("not for this computer", "dim"),
  "no-supervisor off": () => fact("no supervisor here", "dim"),
};

/** A pair the table does not name, by its verdict state alone. */
export const FALLBACK: Readonly<Record<Row["verdict"]["state"], RowFact>> = {
  fail: fact("not working", "heavy", "fail"),
  warn: fact("needs a look", "text"),
  ok: fact("ok", "dim"),
  off: fact("off", "dim"),
};

/** Whether the cause table names the row's pair of cause and verdict state. */
export function named(row: Row): boolean {
  return row.verdict.cause !== null && `${row.verdict.cause} ${row.verdict.state}` in FACTS;
}

/** The row's fact, weight, rise and line two, by the cause table. Line two is kept only
 * on a row that rises. */
export function rowFact(row: Row, when: When): RowFact {
  const found = named(row) ? FACTS[`${row.verdict.cause} ${row.verdict.state}`](row, when)
    : { ...FALLBACK[row.verdict.state] };
  return found.rise === null ? { ...found, line: null } : found;
}

// -- what rises ---------------------------------------------------------------------------

const RISE_ORDER: Record<Exclude<Rise, null>, number> = { fail: 0, "last-run": 1, unknown: 2 };

const byName = (a: Row, b: Row): number => a.name.localeCompare(b.name) || a.id.localeCompare(b.id);

/** The rows that rise, in order: every fail by name; then `last-run`, the oldest last run
 * first, ties and a missing time by name; then `unknown`, by name. */
export function risen(rows: readonly Row[], when: When): Row[] {
  const found = rows.map((row) => ({ row, rise: rowFact(row, when).rise }))
    .filter((item): item is { row: Row; rise: Exclude<Rise, null> } => item.rise !== null);
  const at = (row: Row): number | null => epochOf(row.last_run?.at ?? null);
  return found.sort((a, b) => {
    const rank = RISE_ORDER[a.rise] - RISE_ORDER[b.rise];
    if (rank !== 0) return rank;
    if (a.rise === "last-run") {
      const x = at(a.row);
      const y = at(b.row);
      if (x !== null && y !== null && x !== y) return x - y;
      if ((x === null) !== (y === null)) return x === null ? 1 : -1;
    }
    return byName(a.row, b.row);
  }).map((item) => item.row);
}

// -- the counts ---------------------------------------------------------------------------

/** The first group's buckets, in the order they show, and the pairs each holds. A row
 * that does not rise and is in none of them is `other`. */
export const BUCKETS: readonly { label: string; pairs: readonly string[] }[] = [
  { label: "running", pairs: ["running ok", "running-off warn", "no-login warn", "health warn"] },
  { label: "ready", pairs: ["listening ok"] },
  { label: "on schedule", pairs: ["armed ok", "not-wrapped warn"] },
  { label: "stopped by hand", pairs: ["stopped warn"] },
  { label: "off", pairs: ["off off", "switched-off off", "login-off warn", "armed-off warn", "missing warn"] },
  { label: "being removed", pairs: ["removing warn"] },
  { label: "not run here", pairs: ["elsewhere off", "not-here off", "no-supervisor off"] },
];
export const OTHER = "other";

/** The bucket of a row that does not rise; null for one that rises. */
export function bucketOf(row: Row, when: When): string | null {
  if (rowFact(row, when).rise !== null) return null;
  const pair = `${row.verdict.cause} ${row.verdict.state}`;
  return BUCKETS.find((bucket) => bucket.pairs.includes(pair))?.label ?? OTHER;
}

export interface Count {
  label: string;
  n: number;
}

/** The first group's counts: each bucket that is not zero, in order. Every row that does
 * not rise is in exactly one. */
export function counts(rows: readonly Row[], when: When): Count[] {
  const by = new Map<string, number>();
  for (const row of rows) {
    const bucket = bucketOf(row, when);
    if (bucket !== null) by.set(bucket, (by.get(bucket) ?? 0) + 1);
  }
  return [...BUCKETS.map((bucket) => bucket.label), OTHER]
    .filter((label) => by.has(label)).map((label) => ({ label, n: by.get(label)! }));
}

/** The counts as the first group's dim line: `3 running · 1 ready · 2 off`. */
export function countsLine(found: readonly Count[]): string {
  return found.map((count) => `${count.n} ${count.label}`).join(JOIN);
}

/** The first group's label: `1 service`, `4 services`. */
export function servicesLabel(n: number): string {
  return n === 1 ? "1 service" : `${n} services`;
}

// -- the groups ---------------------------------------------------------------------------

export interface Group {
  label: string;
  /** The project key, null for any other group. */
  project: string | null;
  /** True for the group of Moxi's rows. */
  moxi: boolean;
  rows: Row[];
}

/** The list: the risen rows, in order, and the groups of the rest, each with rows. With
 * projects, one group per project key his rows carry (by key), then `No project`; without,
 * `Yours`. Then `Moxi`. A risen row is lifted out of its group. Within a group by name;
 * in `Moxi` by module then name, rows switched off last. */
export function grouped(rows: readonly Row[], projects: boolean, when: When): { risen: Row[]; groups: Group[] } {
  const up = risen(rows, when);
  const lifted = new Set(up.map((row) => row.id));
  const by = new Map<string, Row[]>();
  const loose: Row[] = [];
  const moxi: Row[] = [];
  for (const row of rows) {
    if (lifted.has(row.id)) continue;
    if (row.form === "module") {
      moxi.push(row);
    } else if (projects && row.project !== null) {
      const list = by.get(row.project) ?? [];
      list.push(row);
      by.set(row.project, list);
    } else {
      loose.push(row);
    }
  }
  const groups: Group[] = [...by.keys()].sort((a, b) => a.localeCompare(b))
    .map((key) => ({ label: key, project: key, moxi: false, rows: by.get(key)!.sort(byName) }));
  if (loose.length) {
    groups.push({ label: projects ? NO_PROJECT : YOURS, project: null, moxi: false, rows: loose.sort(byName) });
  }
  const off = (row: Row): number => (row.verdict.cause === "switched-off" ? 1 : 0);
  moxi.sort((a, b) => off(a) - off(b) || a.module.localeCompare(b.module) || byName(a, b));
  if (moxi.length) groups.push({ label: MOXI, project: null, moxi: true, rows: moxi });
  return { risen: up, groups };
}

// -- line one -----------------------------------------------------------------------------

const DAY_NAMES: Record<string, [string, string]> = {
  mon: ["Mon", "Mondays"], tue: ["Tue", "Tuesdays"], wed: ["Wed", "Wednesdays"], thu: ["Thu", "Thursdays"],
  fri: ["Fri", "Fridays"], sat: ["Sat", "Saturdays"], sun: ["Sun", "Sundays"],
};
const DAY_ORDER = Object.keys(DAY_NAMES);

/** `a`, `a and b`, `a, b and c`. */
function listed(items: readonly string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

const UNITS: Record<string, [string, string]> = { m: ["minute", "minutes"], h: ["hour", "hours"] };

/** A timer's timing in words: `every hour`, `every 6 hours`, `every 15 minutes`, `daily at
 * 02:00`, `Mondays at 04:30`, `Mon and Fri at 09:00`; null for none. A value it can't word
 * reads as declared (`every 90s`). */
export function timingWords(timing: Timing | null): string | null {
  if (timing === null) return null;
  if (timing.every !== null) {
    const match = /^(\d+)([mh])$/.exec(timing.every);
    if (match === null) return `every ${timing.every}`;
    const n = Number(match[1]);
    const [one, many] = UNITS[match[2]];
    return n === 1 ? `every ${one}` : `every ${n} ${many}`;
  }
  if (timing.at === null || timing.at.length === 0) return null;
  const at = `at ${listed(timing.at)}`;
  const days = DAY_ORDER.filter((day) => timing.days?.includes(day));
  if (days.length === 0 || days.length === DAY_ORDER.length) return `daily ${at}`;
  if (days.length === 1) return `${DAY_NAMES[days[0]][1]} ${at}`;
  return `${listed(days.map((day) => DAY_NAMES[day][0]))} ${at}`;
}

/** His command without its directory: the first word's last part and the rest, a word
 * with a space or a quote quoted; null when it didn't reach this page. */
export function commandWords(argv: Row["argv"]): string | null {
  const name = commandName(argv);
  if (argv === null || name === null || argv.some((word) => word === null)) return null;
  const rest = (argv.slice(1) as string[]).map((word) =>
    /[\s"'\\]/.test(word) || word === "" ? JSON.stringify(word) : word);
  return [name, ...rest].join(" ");
}

/** Line one's dim words after the name: his `doc`, else his command without its
 * directory; Moxi's row, its module. On a risen row with projects, his row's project key
 * instead (a risen Moxi row keeps its module). Then a timer's timing, then `spends usage`
 * when its runs launch sessions. Null when there is none. */
export function whereOf(row: Row, options: { projects: boolean; risen: boolean }): string | null {
  const parts: string[] = [];
  let first: string | null;
  if (row.form === "module") {
    first = row.module;
  } else if (options.risen && options.projects && row.project !== null) {
    first = row.project;
  } else {
    first = row.doc || commandWords(row.argv);
  }
  if (first) parts.push(first);
  if (row.kind === "timer") {
    const timing = timingWords(row.timing);
    if (timing !== null) parts.push(timing);
  }
  if (row.launches_session) parts.push(SPENDS);
  return parts.length ? parts.join(JOIN) : null;
}

// -- the list as the page draws it --------------------------------------------------------

/** What the first group says when no row rises. */
export const NOTHING_DOWN = "Nothing is down.";
/** The first group's act, at the desk. */
export const ADD_A_SERVICE = "Add a service";

/** One row as `<moxi-ui-row>` draws it: line one's name, its dim words and its fact with
 * the fact's weight; line two's sentence on a risen row. */
export interface RowView {
  row: Row;
  name: string;
  where: string | null;
  fact: string;
  weight: Weight;
  line: string | null;
}

/** The list: the first group (its label, the risen rows or the sentence and counts, the
 * time of the read), then the groups. */
export interface ListShape {
  label: string;
  risen: RowView[];
  /** `Nothing is down.` when no row rises, else null. */
  sentence: string | null;
  /** The dim counts when no row rises, else null; null too when there is no row. */
  counts: string | null;
  /** `read <time>` from the rows' `at`, null without one. */
  read: string | null;
  groups: { group: Group; rows: RowView[] }[];
}

/** One row's view: a risen row's dim words are its project key (with projects). */
export function rowView(row: Row, options: { projects: boolean; risen: boolean }, when: When): RowView {
  const found = rowFact(row, when);
  return { row, name: row.name, where: whereOf(row, options), fact: found.fact, weight: found.weight,
           line: options.risen ? found.line : null };
}

/** The list the page draws for `rows`, read at `at`. */
export function listShape(rows: readonly Row[], at: string | null, projects: boolean, when: When): ListShape {
  const { risen: up, groups } = grouped(rows, projects, when);
  const read = timeWords(at, when);
  const found = counts(rows, when);
  return {
    label: servicesLabel(rows.length),
    risen: up.map((row) => rowView(row, { projects, risen: true }, when)),
    sentence: up.length ? null : NOTHING_DOWN,
    counts: up.length || !found.length ? null : countsLine(found),
    read: read === null ? null : `read ${read}`,
    groups: groups.map((group) => ({ group, rows: group.rows.map((row) => rowView(row, { projects, risen: false }, when)) })),
  };
}

// The Projects page's list, worked out with no DOM so the Node tests can read it: what
// each project's row draws in each of `<moxi-ui-row>`'s attributes, from projects' own
// list and the facts components' reports. Every string here goes into the page as text.

import { claimedBelow, inOrder, partText } from "./claims.ts";
import type { Report } from "./facts.ts";
import type { Listed } from "./feed.ts";
import { dayWord, homeForm, kindWord, lastActive, LOCAL } from "./words.ts";
import type { Clock } from "./words.ts";

/** Line two's heavy words for a root that is not a directory. */
export const MISSING = "Its folder doesn't exist";
/** What follows them, dim. */
export const MISSING_FIX = "point it at where the folder went, or remove it";
/** A missing root is broken, on Home's `needs` scale. */
export const MISSING_RANK = 30;
/** Line one's fact for a project no source of all activity has seen used. */
export const NOT_USED = "not used yet";
/** The phrases of a line are joined by this. */
export const JOIN = " · ";

/** What the list is drawn from, beside the projects. */
export interface ListFacts {
  /** The page server's home folder, for the `~` form. */
  home: string | null;
  /** Every report held now, null ones left out, in the order the page placed them. */
  reports: readonly Report[];
  /** The key of the open project, or null. */
  open: string | null;
  /** Epoch seconds now, for the day words. */
  now: number;
  clock?: Clock;
}

/** One project's row: its attributes, its act and what orders it. */
export interface ProjectRow {
  key: string;
  /** `name`: the key. */
  name: string;
  /** `where`: the `~` root, then ` · <kind word>` for a kind other than a code project. */
  where: string;
  /** `fact`: `last active <when>`, `not used yet`, or null. */
  fact: string | null;
  /** In `acts-one`: `Open`, or `Close` while the row is open. */
  act: "Open" | "Close";
  /** `lead`, with `lead-heavy`: line two's heavy parts. */
  lead: string | null;
  /** `lead-more`, dim: the missing folder's fix. */
  leadMore: string | null;
  /** `rest`, with `rest-strong`: the ranked parts that aren't heavy, then the warnings. */
  rest: string | null;
  /** `tail`, dim: the other parts. */
  tail: string | null;
  /** `Change folder` in the `acts` slot, beside the missing folder's words. */
  changeFolder: boolean;
  /** The root is not a directory now. */
  missing: boolean;
  /** The lowest rank on line two, the missing folder's included; null for none. */
  rank: number | null;
  /** The newest activity any report gave the key, epoch seconds; null for none. */
  at: number | null;
}

/** The newest `at` any report gives `key`, or null. */
export function newestAt(reports: readonly Report[], key: string): number | null {
  let newest: number | null = null;
  for (const report of reports) {
    const at = report.projects.get(key)?.at ?? null;
    if (at !== null && (newest === null || at > newest)) newest = at;
  }
  return newest;
}

/** Whether the page may say a project was not used: a source of all activity has reported. */
export function saysUnused(reports: readonly Report[]): boolean {
  return reports.some((report) => report.activity === "all");
}

/** Whether any report is an activity source now: it gives some project an `at`. A report
 * that says `activity` but gives no project an `at` (one that knows nothing running) is
 * none. */
export function hasActivity(reports: readonly Report[]): boolean {
  return reports.some((report) => [...report.projects.values()].some((facts) => facts.at !== null));
}

interface Drawn {
  text: string;
  heavy: boolean;
  warn: boolean;
  rank: number | null;
}

/** The reports' parts for `key`, counted, in `order`; a count of 0 left out. */
function drawnParts(reports: readonly Report[], key: string): Drawn[] {
  const drawn: Drawn[] = [];
  for (const report of inOrder(reports)) {
    const facts = report.projects.get(key);
    if (facts === undefined || facts.parts.length === 0) continue;
    const claimed = claimedBelow(reports, key, report.order);
    for (const part of facts.parts) {
      const text = partText(part, claimed);
      if (text !== null) drawn.push({ text, heavy: part.heavy, warn: part.warn, rank: part.rank });
    }
  }
  return drawn;
}

/** By rank, lower first; parts of one rank keep their order. */
const byRank = (parts: Drawn[]): Drawn[] =>
  parts.map((part, index) => ({ part, index }))
    .sort((a, b) => (a.part.rank as number) - (b.part.rank as number) || a.index - b.index)
    .map(({ part }) => part);

const joined = (texts: string[]): string | null => (texts.length === 0 ? null : texts.join(JOIN));

/**
 * One project's row. Line one: the key, the `~` root and the kind's word, at the right
 * `last active <when>` from the newest `at` any report gave it, or `not used yet` while a
 * source of all activity has reported and none gave one; then `Open` or `Close`. Line
 * two, only when it has a part: the missing folder first, then the heavy parts (ranked,
 * lower rank first, then the rest in order), the ranked parts that aren't heavy, the
 * warnings, and the others in order.
 */
export function projectRow(project: Listed, facts: ListFacts): ProjectRow {
  const { reports, home, open, now, clock = LOCAL } = facts;
  const kind = kindWord(project.kind);
  const root = homeForm(project.root, home);
  const at = newestAt(reports, project.key);
  let fact: string | null = null;
  if (at !== null) fact = lastActive(at, now, clock);
  else if (saysUnused(reports)) fact = NOT_USED;

  const missing = project.exists === false;
  const parts = drawnParts(reports, project.key);
  const heavy = parts.filter((part) => part.heavy);
  const light = parts.filter((part) => !part.heavy);
  const lead = [...byRank(heavy.filter((part) => part.rank !== null)), ...heavy.filter((part) => part.rank === null)];
  const rest = [...byRank(light.filter((part) => part.rank !== null)),
                ...light.filter((part) => part.rank === null && part.warn)];
  const tail = light.filter((part) => part.rank === null && !part.warn);
  const ranks = parts.flatMap((part) => (part.rank === null ? [] : [part.rank]));
  if (missing) ranks.push(MISSING_RANK);

  return {
    key: project.key,
    name: project.key,
    where: kind === null ? root : `${root}${JOIN}${kind}`,
    fact,
    act: open === project.key ? "Close" : "Open",
    lead: joined([...(missing ? [MISSING] : []), ...lead.map((part) => part.text)]),
    leadMore: missing ? MISSING_FIX : null,
    rest: joined(rest.map((part) => part.text)),
    tail: joined(tail.map((part) => part.text)),
    changeFolder: missing,
    missing,
    rank: ranks.length === 0 ? null : Math.min(...ranks),
    at,
  };
}

/**
 * The attributes a project's `<moxi-ui-row>` takes, null for one left unset: so a row with
 * no part for line two sets none of line two's, and is one line with its act in
 * `acts-one`. Every project row toggles, and is `open` while it is the open project.
 */
export function rowAttributes(row: ProjectRow): Record<string, string | null> {
  const flag = (on: boolean): string | null => (on ? "" : null);
  return {
    name: row.name, where: row.where, fact: row.fact,
    lead: row.lead, "lead-heavy": flag(row.lead !== null), "lead-more": row.leadMore,
    rest: row.rest, "rest-strong": flag(row.rest !== null), tail: row.tail,
    toggles: "", open: flag(row.act === "Close"),
  };
}

// -- the order, the fold, find and the kind filter --------------------------------------------

/** Past this many projects the list has the find field, the kind filter and the fold. */
export const LONG_PAST = 12;
/** A project whose newest activity is older than this folds under `Earlier`. */
export const EARLIER_DAYS = 30;
/** How long the first order waits for every placed component's first report. */
export const HOLD_MS = 3000;

const DAY_S = 86_400;

/** `<n> project(s)`. */
export function projectsWord(n: number): string {
  return `${n} ${n === 1 ? "project" : "projects"}`;
}

/** Keys compare without case, then as they are, so the order is the same everywhere. */
export function byKey(a: string, b: string): number {
  const lower = a.toLowerCase().localeCompare(b.toLowerCase(), "en");
  return lower !== 0 ? lower : a < b ? -1 : a > b ? 1 : 0;
}

/**
 * Whether the list still holds its first order, by key: until every placed component has
 * reported once or `HOLD_MS` has passed since the page opened, whichever is first.
 */
export function orderHeld(placed: number, reported: number, elapsedMs: number): boolean {
  return reported < placed && elapsedMs < HOLD_MS;
}

/** Whether a row rises above the order by activity: a missing folder or a ranked part. */
export const isRisen = (row: ProjectRow): boolean => row.rank !== null;

/**
 * The rows in the list's order. With an activity source (a report giving some project an
 * `at`): the risen rows first, lowest rank first, then every row by its newest `at`, newest
 * first, and rows with no `at` last by key. With none, or while the first order is held:
 * by key, whatever ranks.
 */
export function ordered(rows: readonly ProjectRow[], { active, held }: { active: boolean; held: boolean }): ProjectRow[] {
  const keyed = [...rows].sort((a, b) => byKey(a.key, b.key));
  if (!active || held) return keyed;
  const byAt = (a: ProjectRow, b: ProjectRow): number => {
    if (a.at !== null && b.at !== null && a.at !== b.at) return b.at - a.at;
    if ((a.at === null) !== (b.at === null)) return a.at === null ? 1 : -1;
    return byKey(a.key, b.key);
  };
  const risen = keyed.filter(isRisen).sort((a, b) => (a.rank as number) - (b.rank as number) || byAt(a, b));
  return [...risen, ...keyed.filter((row) => !isRisen(row)).sort(byAt)];
}

/**
 * `rows` with the open row back at the place it had in `previous`, the keys as last drawn,
 * and the others ordered around it. A row that wasn't drawn before takes its own place.
 */
export function keepOpenPlace(rows: readonly ProjectRow[], open: string | null,
                              previous: readonly string[]): ProjectRow[] {
  const was = open === null ? -1 : previous.indexOf(open);
  const now = rows.findIndex((row) => row.key === open);
  if (was < 0 || now < 0) return [...rows];
  const others = rows.filter((row) => row.key !== open);
  const place = Math.min(was, others.length);
  return [...others.slice(0, place), rows[now], ...others.slice(place)];
}

/** Whether `row` matches what is typed in the find field: its key or `~` root, without case. */
export function matches(row: ProjectRow, root: string, find: string): boolean {
  const typed = find.trim().toLowerCase();
  return typed === "" || row.key.toLowerCase().includes(typed) || root.toLowerCase().includes(typed);
}

/** What the list's head and rows show for a set of choices. */
export interface ListAsk {
  /** The find field, "" for none. */
  find: string;
  /** The kind filter, null for all. */
  kind: string | null;
  /** The first order is still held. */
  held: boolean;
  /** The keys in the order last drawn, so the open row keeps its place. */
  previous: readonly string[];
}

/** The list as it is drawn. */
export interface ListView {
  /** The rows shown, in order. */
  rows: ProjectRow[];
  /** The rows folded under `Earlier`, in order: the newest activity first, never-used by key. */
  earlier: ProjectRow[];
  /** The fold's words after `Earlier · `: `24 projects not used since Oct 10`, null with no fold. */
  earlierLine: string | null;
  /** `<n> projects` at the left, counting the matches while the find field or filter is set. */
  count: string;
  /** `<shown> of <all> projects` beside the field while it or the filter is set. */
  shownOf: string | null;
  /** Past twelve projects: the find field shows. */
  long: boolean;
  /** The kinds the filter offers, by name; none unless the list is long and holds more than one. */
  kinds: string[];
  /** Every key in the order drawn, the folded ones after the rest. */
  order: string[];
}

/**
 * The list. Past `LONG_PAST` projects the find field and the kind filter apply, and,
 * while a source of all activity has reported and the order is no longer held, projects
 * whose newest activity is over `EARLIER_DAYS` days old, or absent, fold under
 * `Earlier`; a risen row, the open row and any row while the find field is set never
 * fold. Twelve or fewer: no field, no filter, no fold.
 */
export function listView(projects: readonly Listed[], facts: ListFacts, ask: ListAsk): ListView {
  const { reports, home, open, now, clock = LOCAL } = facts;
  const all = projects.map((project) => ({ project, row: projectRow(project, facts) }));
  const long = projects.length > LONG_PAST;
  const kindsHeld = [...new Set(projects.map((project) => project.kind))].sort(byKey);
  const kinds = long && kindsHeld.length > 1 ? kindsHeld : [];
  const kind = kinds.length > 0 && ask.kind !== null ? ask.kind : null;
  const find = long ? ask.find.trim() : "";
  const shown = all.filter(({ project, row }) =>
    (kind === null || project.kind === kind) && matches(row, homeForm(project.root, home), find))
    .map(({ row }) => row);

  const sorted = keepOpenPlace(ordered(shown, { active: hasActivity(reports), held: ask.held }), open, ask.previous);
  const folds = long && find === "" && !ask.held && saysUnused(reports);
  const since = now - EARLIER_DAYS * DAY_S;
  const folded = (row: ProjectRow): boolean =>
    folds && !isRisen(row) && row.key !== open && (row.at === null || row.at < since);
  const rows = sorted.filter((row) => !folded(row));
  const earlier = [...sorted.filter(folded)].sort((a, b) =>
    a.at !== null && b.at !== null ? b.at - a.at || byKey(a.key, b.key)
      : a.at === null && b.at === null ? byKey(a.key, b.key) : a.at === null ? 1 : -1);

  let earlierLine: string | null = null;
  if (earlier.length > 0) {
    const newest = earlier[0].at;
    earlierLine = newest === null ? projectsWord(earlier.length)
      : `${projectsWord(earlier.length)} not used since ${dayWord(clock.day(newest), clock.day(now))}`;
  }
  const narrowed = kind !== null || find !== "";
  return {
    rows, earlier, earlierLine, long, kinds,
    count: projectsWord(narrowed ? shown.length : projects.length),
    shownOf: narrowed ? `${shown.length} of ${projectsWord(projects.length)}` : null,
    order: [...rows, ...earlier].map((row) => row.key),
  };
}

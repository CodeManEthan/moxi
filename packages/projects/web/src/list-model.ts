// The Projects page's list, worked out with no DOM so the Node tests can read it: what
// each project's row draws in each of `<moxi-ui-row>`'s attributes, from projects' own
// list and the facts components' reports. Every string here goes into the page as text.

import { claimedBelow, inOrder, partText } from "./claims.ts";
import type { Report } from "./facts.ts";
import type { Listed } from "./feed.ts";
import { homeForm, kindWord, lastActive, LOCAL } from "./words.ts";
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

/** Whether any report is an activity source: it says when a project was last active. */
export function hasActivity(reports: readonly Report[]): boolean {
  return reports.some((report) => report.activity !== null
    || [...report.projects.values()].some((facts) => facts.at !== null));
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

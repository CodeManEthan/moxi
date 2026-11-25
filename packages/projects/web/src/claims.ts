// How the page joins reports without knowing their modules: reports in their `order`;
// counted parts, which count only the sessions no report of a lower order has claimed on
// the same project; and a running line's turn word, joined from one report's line and
// another's `live`. No DOM here.

import type { Line, Part, Report } from "./facts.ts";

/** The reports by `order`, lower first; reports of one order keep the order given. */
export function inOrder(reports: readonly Report[]): Report[] {
  return reports.map((report, index) => ({ report, index }))
    .sort((a, b) => a.report.order - b.report.order || a.index - b.index)
    .map(({ report }) => report);
}

/** Every claim on project `key` by a part or a line of a report whose order is below `order`. */
export function claimedBelow(reports: readonly Report[], key: string, order: number): Set<string> {
  const claimed = new Set<string>();
  const take = (claims: readonly string[]): void => {
    for (const claim of claims) claimed.add(claim);
  };
  for (const report of reports) {
    if (report.order >= order) continue;
    const facts = report.projects.get(key);
    if (facts === undefined) continue;
    for (const part of facts.parts) take(part.claims);
    for (const section of facts.sections) {
      for (const line of section.lines) {
        take(line.claims);
        for (const part of line.parts) take(part.claims);
      }
    }
  }
  return claimed;
}

/**
 * A part's text as drawn. A counted part is `<n> <one|many>`, `n` its claims that
 * `claimed` doesn't hold, each counted once; after lines, `after_one` or `after_many`
 * when it gives them. Null for a count of 0, which is not drawn.
 */
export function partText(part: Part, claimed: ReadonlySet<string>,
                         { afterLines = false }: { afterLines?: boolean } = {}): string | null {
  if (part.count === null) return part.text;
  const n = new Set(part.claims.filter((claim) => !claimed.has(claim))).size;
  if (n === 0) return null;
  const { one, many, after_one, after_many } = part.count;
  const word = n === 1 ? (afterLines ? after_one ?? one : one) : (afterLines ? after_many ?? many : many);
  return `${n} ${word}`;
}

/**
 * The sessions in a turn now, by claim, with the word to draw for each: every report's
 * `live` gathered into one map. A claim two reports disagree on (`in_turn` true in one,
 * false in another) is left out, and so is one no report says is in a turn. The word is
 * the `state` of the lowest order saying so.
 */
export function inTurn(reports: readonly Report[]): Map<string, string> {
  const said = new Map<string, { in_turn: boolean; state: string } | null>();
  for (const report of inOrder(reports)) {
    for (const [claim, live] of report.live) {
      if (!said.has(claim)) said.set(claim, live);
      else if (said.get(claim)?.in_turn !== live.in_turn) said.set(claim, null);
    }
  }
  const words = new Map<string, string>();
  for (const [claim, live] of said) if (live !== null && live.in_turn) words.set(claim, live.state);
  return words;
}

/**
 * The turn word to join onto `line`, a line of `report`'s sections for project `key`, or
 * null to draw it as given. Only a line whose module says its session runs (`runs`) and
 * that claims exactly one session gets one, and only when `words` (from `inTurn`) says that
 * session is in a turn. A session claimed by another line of the project's sections, by
 * any report, or by `report` on another project, can't be told apart: it gets none.
 */
export function turnWord(line: Line, report: Report, reports: readonly Report[], key: string,
                         words: ReadonlyMap<string, string>): string | null {
  if (!line.runs || line.claims.length !== 1) return null;
  const claim = line.claims[0];
  const word = words.get(claim);
  if (word === undefined) return null;
  let lines = 0;
  for (const other of reports) {
    for (const section of other.projects.get(key)?.sections ?? []) {
      for (const each of section.lines) if (each.claims.includes(claim)) lines += 1;
    }
  }
  if (lines !== 1) return null;
  for (const [other, facts] of report.projects) {
    if (other !== key && facts.parts.some((part) => part.claims.includes(claim))) return null;
  }
  return word;
}

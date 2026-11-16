// How the page joins reports without knowing their modules: reports in their `order`, and
// counted parts, which count only the sessions no report of a lower order has claimed on
// the same project. No DOM here.

import type { Part, Report } from "./facts.ts";

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

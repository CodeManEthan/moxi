// What repo's `project-facts` component reports to the Projects page, as plain data: a
// heavy `a git hook broke at <time>` on the closed row of each project with a broken repo,
// and the opened project's `Git hooks` row. No DOM and no Lit here, so the Node tests check
// it directly. projects' README, "Facts on the Projects page", is the contract it fills.
//
// Repo's judgment is three words of its own, each mapped, never a reading of record text:
// a `problems` row's `state`, an open record's `kind`, and, for a `commit` record, the
// `status` of the spec it names. A refusal is a gate doing its job, not a breakage:
//
//   repo's word                                     line two             the opened row
//   a problem whose state is `fail`                 a git hook broke     that, then its detail
//   a record of kind `piece` or `push`              ... broke at <time>  that, then its text's first line
//   a `commit` record whose spec's status is error  as `piece`           as `piece`
//   any other `commit` record (refused, ...)        nothing              its text's first line
//   a problem whose state is `warn`                 nothing              its detail
//
// A repo with any of the first three is broken. An answer whose `state` isn't `up`, and a
// failed read, report nothing (null). A repo belongs to the project its `project` names.

import { encodeName } from "./frame-model.ts";
import type { RecordRow, RepoRow, Repos } from "./feed.ts";
import { runOrder, shownNames } from "./rows-model.ts";
import { firstLine, homeForm, LOCAL, recordSeconds, whenWord } from "./words.ts";
import type { Clock } from "./words.ts";

/** The event a facts component dispatches, with its report as the `detail`. */
export const FACTS_EVENT = "moxi-project-facts";
/** The contract's version. */
export const VERSION = 1;
/** Where repo's parts and section sit among other modules'. */
export const ORDER = 70;
/** What repo gathers under a project, for the Projects page's empty state. */
export const GATHERS = "git hooks";
/** The opened project's row, and its link's words. */
export const LABEL = "Git hooks";
/** Home's rank for something broken. */
export const BROKEN = 30;
/** A breakage's words; with a time, `a git hook broke at <time>`. */
export const BROKE = "a git hook broke";
/** The words after a piece switched off in the repo. */
export const OFF_HERE = "is switched off here";
/** The longest text the contract takes. */
export const TEXT_MAX_CHARS = 200;
/** The most parts one line may have, and the most lines in one section. */
export const PARTS_MAX = 8;
export const LINES_MAX = 6;

/** A project as the Projects page gives it, in the `projects` attribute: the key is all
 * this component reads. */
export interface Given {
  key: string;
}

export interface Part {
  text: string;
  heavy?: boolean;
  rank?: number;
}

export interface Line {
  parts: Part[];
}

export interface Section {
  label: string;
  rank?: number;
  lines: Line[];
  link: { text: string; module: string; route: string };
}

export interface ProjectFacts {
  parts?: Part[];
  sections?: Section[];
}

export interface Report {
  version: number;
  order: number;
  gathers: string;
  projects: Record<string, ProjectFacts>;
}

const isObj = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** The `projects` attribute's projects; those that aren't one are left out, and a value
 * that isn't a JSON list gives none. */
export function parseGiven(text: string | null): Given[] {
  let list: unknown;
  try {
    list = JSON.parse(text ?? "[]");
  } catch {
    return [];
  }
  if (!Array.isArray(list)) return [];
  return list.filter((item): item is Record<string, unknown> => isObj(item) && typeof item.key === "string")
    .map((item) => ({ key: item.key as string }));
}

/** `text` cut to the contract's longest, ending `…` when cut. */
export function clip(text: string): string {
  const chars = Array.from(text);
  return chars.length <= TEXT_MAX_CHARS ? text : `${chars.slice(0, TEXT_MAX_CHARS - 1).join("")}…`;
}

/** How repo judges one open record: `broke` (a piece's run broke, a push failed, or an
 * auto-commit's pass broke) or `said` (anything else an auto-commit had to say, a refusal
 * among them). */
export function recordWeight(record: Pick<RecordRow, "kind" | "spec">, repo: Pick<RepoRow, "specs">): "broke" | "said" {
  if (record.kind === "piece" || record.kind === "push") return "broke";
  if (record.kind === "commit") {
    const spec = repo.specs.find((one) => one.name === record.spec);
    if (spec !== undefined && spec.status === "error") return "broke";
  }
  return "said";
}

/** One of a repo's lines with what it says of the repo's state. */
interface Said {
  parts: Part[];
  broke: boolean;
  /** For a broken record, its `at` in epoch seconds; null when it has none that reads. */
  at: number | null;
}

/** The repo's lines by the table above, the broken ones first, each in repo's order. */
export function repoSaid(repo: RepoRow, today: string | null, clock: Clock = LOCAL): Said[] {
  const broke: Said[] = [];
  const said: Said[] = [];
  for (const problem of repo.problems) {
    if (problem.state === "fail") {
      broke.push({ parts: [{ text: BROKE, heavy: true }, { text: clip(problem.detail) }], broke: true, at: null });
    } else if (problem.state === "warn") {
      said.push({ parts: [{ text: clip(problem.detail) }], broke: false, at: null });
    }
  }
  for (const record of repo.records) {
    const text = clip(firstLine(record.text));
    if (recordWeight(record, repo) === "broke") {
      const at = recordSeconds(record.at);
      const lead = at === null ? BROKE : `${BROKE} at ${whenWord(at, today, clock)}`;
      broke.push({ parts: [{ text: lead, heavy: true }, { text }], broke: true, at });
    } else {
      said.push({ parts: [{ text }], broke: false, at: null });
    }
  }
  return [...broke, ...said].map((one) => ({ ...one, parts: one.parts.filter((part) => part.text !== "") }));
}

/** The repo's pieces line: its enabled pieces by the Git hooks page's names in run order,
 * joined `, `, then `<piece> is switched off here` for each one switched off; null with no
 * piece. */
export function piecesLine(repo: Pick<RepoRow, "pieces">): Part[] | null {
  const pieces = runOrder(repo.pieces);
  if (!pieces.length) return null;
  const names = shownNames(pieces);
  const name = (id: string): string => names.get(id) ?? id;
  const on = pieces.filter((piece) => piece.enabled).map((piece) => name(piece.id));
  const off = pieces.filter((piece) => !piece.enabled).map((piece) => ({ text: clip(`${name(piece.id)} ${OFF_HERE}`) }));
  return [...(on.length ? [{ text: clip(on.join(", ")) }] : []), ...off];
}

/** `parts` kept to `PARTS_MAX`: past it, the first one fewer and `<n> more`. */
function capped(parts: Part[]): Part[] {
  if (parts.length <= PARTS_MAX) return parts;
  return [...parts.slice(0, PARTS_MAX - 1), { text: `${parts.length - (PARTS_MAX - 1)} more` }];
}

/** Lines kept to `LINES_MAX`: past it, the last kept ends `<n> more`, heavy when a line it
 * stands for is. */
export function cappedLines(lines: { parts: Part[]; broke: boolean }[]): Line[] {
  if (lines.length <= LINES_MAX) return lines.map((line) => ({ parts: capped(line.parts) }));
  const kept = lines.slice(0, LINES_MAX);
  const rest = lines.slice(LINES_MAX);
  const more: Part = { text: `${rest.length} more` };
  if (rest.some((line) => line.broke)) more.heavy = true;
  const last = kept[LINES_MAX - 1];
  return [...kept.slice(0, -1).map((line) => ({ parts: capped(line.parts) })),
          { parts: [...last.parts.slice(0, PARTS_MAX - 1), more] }];
}

/** The newest broken record's time among `said`; null when none has one. */
function newest(said: readonly Said[]): number | null {
  const times = said.filter((one) => one.broke && one.at !== null).map((one) => one.at as number);
  return times.length ? Math.max(...times) : null;
}

/** The route of repo's page for project `key`: its first repo opened. */
export function projectRoute(key: string): string {
  return `project/${encodeName(key)}`;
}

/**
 * What the component reports for `value` (repo's `/api/repos` value, or null when the last
 * read failed), with `given` the Projects page's projects and `open` the opened key: null
 * unless the read is good and its `state` is `up`. Each given project with a broken repo
 * gets one heavy part a broken repo on its second line, ranked as broken; the opened one,
 * when it is one of `given` and holds a repo, gets its `Git hooks` row: per repo its
 * lines, broken first, then its pieces; with two or more repos each line starts with the
 * repo's folder.
 */
export function factsReport(value: Repos | null, given: readonly Given[], open: string | null,
                            { clock = LOCAL }: { clock?: Clock } = {}): Report | null {
  if (value === null || value.state !== "up") return null;
  const keys = new Set(given.map((project) => project.key));
  const by = new Map<string, RepoRow[]>();
  for (const repo of value.repos) {
    if (repo.project === null || !keys.has(repo.project)) continue;
    by.set(repo.project, [...(by.get(repo.project) ?? []), repo]);
  }
  const projects: Record<string, ProjectFacts> = {};
  for (const [key, repos] of by) {
    const facts: ProjectFacts = {};
    const each = repos.map((repo) => ({ repo, said: repoSaid(repo, value.today, clock) }));
    const parts: Part[] = [];
    for (const { said } of each) {
      if (!said.some((one) => one.broke)) continue;
      const at = newest(said);
      parts.push({ text: at === null ? BROKE : `${BROKE} at ${whenWord(at, value.today, clock)}`, heavy: true, rank: BROKEN });
    }
    if (parts.length) facts.parts = capped(parts);
    if (key === open) {
      const lines: { parts: Part[]; broke: boolean }[] = [];
      for (const { repo, said } of each) {
        const own: { parts: Part[]; broke: boolean }[] = said.map((one) => ({ parts: one.parts, broke: one.broke }));
        const pieces = piecesLine(repo);
        if (pieces !== null) own.push({ parts: pieces, broke: false });
        const folder = repos.length > 1 ? [{ text: clip(homeForm(repo.root, value.home)) }] : [];
        lines.push(...own.map((line) => ({ parts: [...folder, ...line.parts], broke: line.broke })));
      }
      if (lines.length) {
        const section: Section = { label: LABEL, lines: cappedLines(lines),
                                   link: { text: LABEL, module: "repo", route: projectRoute(key) } };
        if (parts.length) section.rank = BROKEN;
        facts.sections = [section];
      }
    }
    if (Object.keys(facts).length) projects[key] = facts;
  }
  return { version: VERSION, order: ORDER, gathers: GATHERS, projects };
}

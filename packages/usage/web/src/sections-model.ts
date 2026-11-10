// The Projects (or Folders) and Models sections with no DOM: the closed row that states
// the dearest few, the opened rows with their share and cost, the sentence under the
// projects, and each section's table with every figure its route gives. Rows go by cost,
// dearest first, an unpriced row last. A cost that is not whole reads `at least`, one
// nothing in which could be priced `no price`, never a zero.

import type { Table } from "./chart-model.ts";
import { costWords, countWords, sumRows, whyNotWhole } from "./cost-model.ts";
import type { Sum } from "./cost-model.ts";
import type { ModelRow, ProjectRow, Report, RowSums } from "./feed.ts";
import { tilde } from "./filter-model.ts";
import type { PageState } from "./filter-model.ts";
import { shareWords } from "./sessions-model.ts";
import { fmtCost, fmtTokens, provenanceLine, UNKNOWN_MODEL } from "./views.ts";

export type SectionKey = "projects" | "folders" | "models";

/** How many rows the closed row names. Folders are written whole, so fewer fit. */
export const CLOSED: Record<SectionKey, number> = { projects: 3, folders: 2, models: 2 };

/** Each section's name at its left, and the noun its count is in. */
export const SECTION_LABELS: Record<SectionKey, string> = { projects: "Projects", folders: "Folders", models: "Models" };
const NOUNS: Record<SectionKey, string> = { projects: "project", folders: "folder", models: "model" };

export const NO_PROJECT = "no project";
export const NO_FOLDER = "no folder";
export const CLOSE = "Close";
export const TABLE = "Table";
export const HIDE_TABLE = "Hide table";

/** One opened row. */
export interface SectionRow {
  key: string;
  /** A project's key, a folder written whole, a model's id, `no project`. */
  name: string;
  /** Its share of the section's total, rounded down; null when it cannot be said. */
  share: string | null;
  /** `$21.49`, `at least $3.00`, `no price`. */
  cost: string;
  /** Why the cost reads `at least` or `no price`; null for a whole one. */
  why: string | null;
  /** The filter a press on the row sets before it opens Sessions; null for a row that
   * opens nothing. */
  patch: Partial<PageState> | null;
}

/** A piece of the sentence under the list: words, or a folder that filters to itself. */
export interface SentencePart {
  text: string;
  patch: Partial<PageState> | null;
}

export interface Section {
  key: SectionKey;
  label: string;
  /** `atlas-api $21.49, kestrel-web $18.06, harbor-infra $8.63`. */
  closed: string;
  /** `7 projects`: the link that opens the section. */
  count: string;
  rows: SectionRow[];
  /** The sentence under the opened rows; null where there is none. */
  sentence: SentencePart[] | null;
  table: Table;
}

type Costed = Pick<Sum, "cost" | "tokens">;

/** By cost, dearest first, an unpriced row last; then by tokens, most first; then name. */
function byCost<R>(rows: R[], figures: (row: R) => Costed, name: (row: R) => string): R[] {
  return [...rows].sort((one, other) => {
    const a = figures(one);
    const b = figures(other);
    if ((a.cost === null) !== (b.cost === null)) return a.cost === null ? 1 : -1;
    const cost = (b.cost ?? 0) - (a.cost ?? 0);
    if (cost !== 0) return cost;
    if (b.tokens !== a.tokens) return b.tokens - a.tokens;
    return name(one) < name(other) ? -1 : name(one) > name(other) ? 1 : 0;
  });
}

function costRow(key: string, name: string, sum: Sum, whole: number | null,
                 patch: Partial<PageState> | null): SectionRow & { sum: Sum } {
  return { key, name, share: shareWords(sum.cost, whole), cost: costWords(sum), why: whyNotWhole(sum), patch, sum };
}

/** `a`, `a and b`, `a, b and c`, with a separator between each part. */
function listed(parts: SentencePart[]): SentencePart[] {
  return parts.flatMap((part, i) => [
    ...(i === 0 ? [] : [{ text: i === parts.length - 1 ? " and " : ", ", patch: null }]), part]);
}

function closedLine(rows: (SectionRow & { sum: Sum })[], key: SectionKey): string {
  return rows.slice(0, CLOSED[key]).map((row) => `${row.name} ${row.cost}`).join(", ");
}

/** A cost cell of a table: the figure, and the harness's own after it where it wrote one. */
function costCell(sum: Sum): string {
  return `${costWords(sum)}${sum.harness_cost === null ? "" : ` · harness ${fmtCost(sum.harness_cost)}`}`;
}

/** A table whose rows end in their requests, tokens and cost, and a last column saying
 * why a row is not whole, there only when some row is not. */
export function figuresTable(heads: string[], lines: { key: string; cells: string[]; sum: Sum }[]): Table {
  const whys = lines.map((line) => whyNotWhole(line.sum));
  const flagged = whys.some((why) => why !== null);
  return {
    columns: [...heads.map((label) => ({ label, num: false })),
              ...["Requests", "Tokens", "Cost"].map((label) => ({ label, num: true })),
              ...(flagged ? [{ label: "Not whole", num: false }] : [])],
    rows: lines.map((line, i) => ({
      key: line.key,
      cells: [...line.cells, line.sum.requests.toLocaleString("en-US"), fmtTokens(line.sum.tokens), costCell(line.sum),
              ...(flagged ? [whys[i] ?? ""] : [])],
    })),
  };
}

const sumOf = (row: RowSums): Sum => sumRows([row]);

/**
 * The Projects section from `/api/projects` by project, or Folders from it by folder
 * (`folders`). Projects' files with no project are one `no project` row, their sum,
 * and the sentence under the list names their folders, each of which filters to
 * itself; Folders has no such row. A project row sets the project filter and a folder
 * row the folder filter. Null until the report has answered, and with no row.
 */
export function placesSection(report: Report<ProjectRow> | null, folders: boolean): Section | null {
  if (report === null || report.rows.length === 0) return null;
  const key: SectionKey = folders ? "folders" : "projects";
  const whole = report.total.cost;
  const home = report.home;
  const folder = (path: string): string => tilde(path, home);
  const named = (row: ProjectRow): string =>
    folders ? (row.cwd === null ? NO_FOLDER : folder(row.cwd)) : (row.project ?? NO_PROJECT);
  const sorted = byCost(report.rows, (row) => row, named);

  let rows: (SectionRow & { sum: Sum })[];
  let sentence: SentencePart[] | null;
  if (folders) {
    rows = sorted.map((row) => costRow(row.cwd ?? "", named(row), sumOf(row), whole,
                                       row.cwd === null ? null : { folder: row.cwd, project: "" }));
    sentence = [{ text: "A folder row opens the Sessions section above, filtered to it.", patch: null }];
  } else {
    const loose = sorted.filter((row) => row.project === null);
    const own = sorted.filter((row) => row.project !== null).map((row) =>
      costRow(`project:${row.project}`, row.project as string, sumOf(row), whole, { project: row.project as string, folder: "" }));
    const fold = loose.length === 0 ? [] : [costRow("no-project", NO_PROJECT, sumRows(loose), whole, null)];
    rows = byCost([...own, ...fold], (row) => row.sum, (row) => row.name);
    const paths = loose.map((row) => row.cwd).filter((path): path is string => path !== null);
    const parts: SentencePart[] = paths.map((path) => ({ text: folder(path), patch: { folder: path, project: "" } }));
    if (loose.some((row) => row.cwd === null)) parts.push({ text: "files with no folder", patch: null });
    sentence = [...(parts.length ? [{ text: `"${NO_PROJECT}" is `, patch: null }, ...listed(parts), { text: ". ", patch: null }] : []),
                { text: "A project row opens the Sessions section above, filtered to it.", patch: null }];
  }

  const table = folders
    ? figuresTable(["Folder", "Models"], sorted.map((row) => ({
      key: row.cwd ?? NO_FOLDER, cells: [named(row), row.models.join(", ")], sum: sumOf(row) })))
    : figuresTable(["Project", "Folder", "Models"], sorted.map((row) => ({
      key: row.project === null ? `folder:${row.cwd ?? ""}` : `project:${row.project}`,
      cells: [row.project ?? NO_PROJECT, row.cwd === null ? "" : folder(row.cwd), row.models.join(", ")], sum: sumOf(row) })));

  return { key, label: SECTION_LABELS[key], closed: closedLine(rows, key), count: countWords(rows.length, NOUNS[key]),
           rows: rows.map(({ sum, ...row }) => row), sentence, table };
}

/** Where a model's prices came from, as one cell: each source, then the rules applied. */
export function pricedBy(row: ModelRow): string {
  const sources = row.provenance.length ? row.provenance.map(provenanceLine).join("; ") : "no price";
  return row.rules.length ? `${sources}; ${row.rules.join(", ")}` : sources;
}

/** The Models section from `/api/models`: plain rows, each model's id as the index holds
 * it. Null until the report has answered, and with no row. */
export function modelsSection(report: Report<ModelRow> | null): Section | null {
  if (report === null || report.rows.length === 0) return null;
  const named = (row: ModelRow): string => row.model ?? UNKNOWN_MODEL;
  const sorted = byCost(report.rows, (row) => row, named);
  const rows = sorted.map((row) => costRow(row.model ?? "", named(row), sumOf(row), report.total.cost, null));
  const table = figuresTable(["Model", "Priced by"], sorted.map((row) => ({
    key: row.model ?? UNKNOWN_MODEL, cells: [named(row), pricedBy(row)], sum: sumOf(row) })));
  return { key: "models", label: SECTION_LABELS.models, closed: closedLine(rows, "models"),
           count: countWords(rows.length, NOUNS.models), rows: rows.map(({ sum, ...row }) => row), sentence: null, table };
}

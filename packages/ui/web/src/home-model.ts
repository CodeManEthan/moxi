// Home's row frame, worked out with no DOM so the tests can read it: what a row
// component's report says, which rows show and in what order, and how a shown row
// behaves under a click.

/** One part of a row's fact. */
export interface RowPart {
  text: string;
  heavy: boolean;
}

/** A row component's report, as Home reads it. */
export interface RowReport {
  parts: RowPart[];
  /** The dim right end; "" for none. */
  time: string;
  /** 0: does not need him. */
  needs: number;
  opens: boolean;
  /** Only with `opens`: shown opened with no click. */
  open: boolean;
}

/**
 * Reads the `detail` of a `moxi-home-row` event. Null for `null` and for anything that is
 * not a report (no `parts` list), which both mean nothing to report. A part without a
 * text is left out; `needs` that is not a number above 0 reads as 0; `open` counts only
 * with `opens`.
 */
export function readReport(detail: unknown): RowReport | null {
  if (typeof detail !== "object" || detail === null) return null;
  const raw = detail as Record<string, unknown>;
  if (!Array.isArray(raw.parts)) return null;
  const parts: RowPart[] = [];
  for (const part of raw.parts) {
    if (typeof part?.text !== "string" || part.text === "") continue;
    parts.push({ text: part.text, heavy: part.heavy === true });
  }
  const needs = typeof raw.needs === "number" && Number.isFinite(raw.needs) && raw.needs > 0 ? raw.needs : 0;
  const opens = raw.opens === true;
  return { parts, time: typeof raw.time === "string" ? raw.time : "", needs, opens, open: opens && raw.open === true };
}

/** A row component on Home and what it last reported; null for nothing to report. */
export interface RowEntry {
  key: string;
  order: number;
  report: RowReport | null;
}

/**
 * The rows Home shows, top first: those that reported, the ones that need him above every
 * one that does not, lower `needs` first, then by `order`. `entries` come in their fixed
 * places, which settles a tie.
 */
export function shownRows<T extends RowEntry>(entries: readonly T[]): T[] {
  const shown = entries.filter((entry) => entry.report !== null);
  const needs = (entry: T): number => entry.report?.needs || Infinity;
  return shown.map((entry, at) => ({ entry, at }))
    .sort((a, b) => {
      const [x, y] = [needs(a.entry), needs(b.entry)];
      return (x === y ? 0 : x < y ? -1 : 1) || a.entry.order - b.entry.order || a.at - b.at;
    })
    .map((item) => item.entry);
}

/** Whether any row needs him. */
export function anyNeeds(entries: readonly RowEntry[]): boolean {
  return entries.some((entry) => (entry.report?.needs ?? 0) > 0);
}

/**
 * How a shown row behaves. `link`: it goes to its module's page. `toggle`: a click opens
 * the component in place or hides it again. `held`: the component asked to be open, so
 * it is shown open, a click does nothing and there is no `Hide`.
 */
export type RowMode = "link" | "toggle" | "held";

export function rowMode(report: RowReport): RowMode {
  if (!report.opens) return "link";
  return report.open ? "held" : "toggle";
}

/** Whether a row's component is shown under it: held open, or opened by a click (`clicked`)
 * while its report still says it opens. */
export function rowOpen(report: RowReport | null, clicked: boolean): boolean {
  if (report === null || !report.opens) return false;
  return report.open || clicked;
}

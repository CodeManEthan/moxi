// The parts of `<moxi-ui-rail>` that need no document: the states a mark can take, the
// attributes a mark reads, which stretches of the line are solid, a mark's accessible
// name, and the keys that press a mark.

/** The states a mark can take, each its own shape. */
export const STATES = ["done", "none", "yours", "failed", "owed", "waiting", "todo", "child"] as const;
export type MarkState = (typeof STATES)[number];

/** Each state in words: a mark's accessible name when the page gives it no `label`. */
export const STATE_WORDS: Readonly<Record<MarkState, string>> = {
  done: "done",
  none: "done, nothing owed",
  yours: "waiting on you",
  failed: "failed or sent back",
  owed: "owed by an agent",
  waiting: "waiting on something else first",
  todo: "not reached yet",
  child: "built in a round of its own",
};

/** The attributes of a mark that carry text. One that is unset or blank draws nothing. */
export const MARK_TEXT_ATTRIBUTES = ["state", "word", "count", "label", "key"] as const;
/** The attributes of a mark that are on when present, whatever their value. */
export const MARK_FLAG_ATTRIBUTES = ["settled", "here", "open", "quiet", "wide"] as const;
/** Every attribute a mark reads. */
export const MARK_ATTRIBUTES: readonly string[] = [...MARK_TEXT_ATTRIBUTES, ...MARK_FLAG_ATTRIBUTES];
/** The attributes the rail itself reads: `size="small"` and the flag `vertical`. */
export const RAIL_ATTRIBUTES: readonly string[] = ["size", "vertical"];

/** The event a rail sends for a click, Enter or Space on one of its marks. */
export const MARK_EVENT = "moxi-rail-mark";
/** What a vertical rail writes after the label of a `here` mark. */
export const HERE = "◂ here";

/** The state `value` names, or null for one that is unset, blank or not one of the eight. */
export function stateOf(value: string | null): MarkState | null {
  return (STATES as readonly string[]).includes(value ?? "") ? (value as MarkState) : null;
}

/** What the line needs to know of one cell: whether it draws a marker and is settled. */
export interface LineCell {
  state: MarkState | null;
  settled: boolean;
}

/** One half of a cell's line: solid, dashed, or not drawn. */
export type Stretch = "solid" | "dashed" | null;

/** The line through one cell: the half before its middle and the half after. */
export interface CellLine {
  before: Stretch;
  after: Stretch;
}

/**
 * The line through each cell, in order. The line runs from the first cell with a marker
 * to the last; before the first and after the last nothing is drawn. It is solid from the
 * first marker through each settled marker up to and including the first one not
 * settled, and dashed after it. A cell with no marker (no state, or a wide cell) takes
 * the stretch it stands in, on both halves.
 */
export function railLine(cells: readonly LineCell[]): CellLine[] {
  const markers = cells.flatMap((cell, i) => (cell.state === null ? [] : [i]));
  if (markers.length === 0) return cells.map(() => ({ before: null, after: null }));
  const first = markers[0];
  const last = markers[markers.length - 1];
  // The first marker not settled ends the solid line; with none, it runs to the last.
  const open = markers.find((i) => !cells[i].settled);
  const solidTo = open ?? last;
  const stretch = (from: number): Stretch => (from < solidTo ? "solid" : "dashed");
  // The marker at or before each cell: the stretch after a cell starts there.
  let previous = first;
  return cells.map((cell, i) => {
    if (i < first || i > last) return { before: null, after: null };
    if (cell.state !== null) {
      const line = { before: i === first ? null : stretch(previous), after: i === last ? null : stretch(i) };
      previous = i;
      return line;
    }
    const both = stretch(previous);
    return { before: both, after: both };
  });
}

/**
 * A mark's accessible name: its `label` as the page gave it, else its state in words,
 * then its count and its word, each when set. Null for a mark with no state.
 */
export function markName(get: (name: string) => string | null): string | null {
  const text = (name: string): string | null => {
    const value = get(name);
    return value === null || value.trim() === "" ? null : value;
  };
  const state = stateOf(get("state"));
  if (state === null) return null;
  const label = text("label");
  if (label !== null) return label;
  return [STATE_WORDS[state], text("count"), text("word")].filter((part) => part !== null).join(", ");
}

/** Whether a key pressed on a mark presses it: Enter or Space alone, held with nothing,
 * and not a key held down repeating. */
export function isMarkKey(event: { key: string; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean;
                                   shiftKey?: boolean; repeat?: boolean }): boolean {
  return (event.key === "Enter" || event.key === " ") && !event.altKey && !event.ctrlKey && !event.metaKey
    && !event.shiftKey && !event.repeat;
}

// The parts of `<moxi-ui-row>` that need no document: its attributes, the pieces each
// line holds for a set of them, and the key that toggles a row.

/** The attributes that carry text. One that is unset or blank draws nothing. */
export const TEXT_ATTRIBUTES = [
  "name", "where", "fact", "lead", "lead-more", "time", "said-by", "rest", "tail", "note",
] as const;
/** The attributes that are on when present, whatever their value. */
export const FLAG_ATTRIBUTES = ["fact-heavy", "lead-heavy", "rest-strong", "toggles", "open"] as const;
/** Every attribute the row reads. */
export const ROW_ATTRIBUTES: readonly string[] = [...TEXT_ATTRIBUTES, ...FLAG_ATTRIBUTES];

/**
 * The named slots for a row's acts: `acts-one` at line one's right end, `acts` at line
 * two's. Content in either is not the row's: a click or key there sends no toggle.
 */
export const ACT_SLOTS = ["acts-one", "acts"] as const;
export type ActSlot = (typeof ACT_SLOTS)[number];

/** The event a row with `toggles` sends for a click, Enter or Space on its lines. */
export const TOGGLE_EVENT = "moxi-row-toggle";
/** The middle dot between two phrases of line two. */
export const DOT = "·";
/** What `said-by="you"` writes before the rest. */
export const YOU = "You:";

/** One drawn piece of a line. A `dot` stands between two phrases. */
export interface Piece {
  kind: "name" | "where" | "fact" | "lead" | "lead-more" | "time" | "you" | "rest" | "tail" | "dot";
  text: string;
  /** Set in the text colour (`rest`), at weight 600 too (`fact`, `lead`). */
  strong?: true;
}

/**
 * What a row draws for its attributes. An empty line is not drawn; `drawnLines` says
 * which lines the act slots add.
 */
export interface RowPlan {
  /** Line one: the name and where at the left, the fact at the right end. */
  one: Piece[];
  /** Line two's phrases, in order, a dot between each two. */
  two: Piece[];
  /** Line three, or null. */
  note: string | null;
  toggles: boolean;
  open: boolean;
  /**
   * What takes the focus: line one's pieces, as a button, on a row with `toggles`; the
   * lines as a whole, with no role, when such a row has no piece on line one (an act in
   * `acts-one` is not one); nothing without `toggles`.
   */
  stop: "one" | "lines" | null;
}

/**
 * The plan for a row whose attributes `get` reads (`null` for one that is unset, as
 * `getAttribute` gives it).
 *
 * Line two's phrases are the lead, the second lead, the time with the rest, and the tail.
 * The time, `You:` and the rest are one phrase, so no dot parts them; `You:` is written
 * only before a rest.
 */
export function rowPlan(get: (name: string) => string | null): RowPlan {
  const text = (name: string): string | null => {
    const value = get(name);
    return value === null || value.trim() === "" ? null : value;
  };
  const flag = (name: string): boolean => get(name) !== null;
  const piece = (kind: Piece["kind"], value: string, strong = false): Piece =>
    (strong ? { kind, text: value, strong: true } : { kind, text: value });

  const one: Piece[] = [];
  const name = text("name");
  const where = text("where");
  const fact = text("fact");
  if (name !== null) one.push(piece("name", name));
  if (where !== null) one.push(piece("where", where));
  if (fact !== null) one.push(piece("fact", fact, flag("fact-heavy")));

  const phrases: Piece[][] = [];
  const lead = text("lead");
  const more = text("lead-more");
  const time = text("time");
  const rest = text("rest");
  const tail = text("tail");
  if (lead !== null) phrases.push([piece("lead", lead, flag("lead-heavy"))]);
  if (more !== null) phrases.push([piece("lead-more", more)]);
  const said: Piece[] = [];
  if (time !== null) said.push(piece("time", time));
  if (rest !== null) {
    if (text("said-by") === "you") said.push(piece("you", YOU));
    said.push(piece("rest", rest, flag("rest-strong")));
  }
  if (said.length > 0) phrases.push(said);
  if (tail !== null) phrases.push([piece("tail", tail)]);
  const two: Piece[] = [];
  phrases.forEach((phrase, i) => {
    if (i > 0) two.push(piece("dot", DOT));
    two.push(...phrase);
  });

  const toggles = flag("toggles");
  const stop = !toggles ? null : one.length > 0 ? "one" : "lines";
  return { one, two, note: text("note"), toggles, open: flag("open"), stop };
}

/** Which of lines one and two a row draws. */
export interface DrawnLines {
  drawsOne: boolean;
  drawsTwo: boolean;
}

/**
 * Which of lines one and two a row draws for its plan, given the act slots that hold a
 * child. Line one is drawn for a piece or for content in `acts-one`; line two for a
 * phrase or for content in `acts`. So a row whose only act is in `acts-one` is one line.
 */
export function drawnLines(plan: RowPlan, filled: Iterable<ActSlot>): DrawnLines {
  const slots = new Set(filled);
  return {
    drawsOne: plan.one.length > 0 || slots.has("acts-one"),
    drawsTwo: plan.two.length > 0 || slots.has("acts"),
  };
}

/** Whether a key pressed on a row's lines toggles it: Enter or Space alone, held with nothing. */
export function isToggleKey(event: { key: string; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean;
                                     shiftKey?: boolean; repeat?: boolean }): boolean {
  return (event.key === "Enter" || event.key === " ") && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey
    && !event.repeat;
}

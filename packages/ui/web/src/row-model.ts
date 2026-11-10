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

/** The event a row with `toggles` sends for a click or Enter on its lines. */
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

/** What a row draws for its attributes. An empty line is not drawn. */
export interface RowPlan {
  /** Line one: the name and where at the left, the fact at the right end. */
  one: Piece[];
  /** Line two's phrases, in order, a dot between each two. */
  two: Piece[];
  /** Line three, or null. */
  note: string | null;
  toggles: boolean;
  open: boolean;
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

  return { one, two, note: text("note"), toggles: flag("toggles"), open: flag("open") };
}

/** Whether a key pressed on a row's lines toggles it: Enter alone, held with nothing. */
export function isToggleKey(event: { key: string; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean;
                                     shiftKey?: boolean; repeat?: boolean }): boolean {
  return event.key === "Enter" && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey
    && !event.repeat;
}

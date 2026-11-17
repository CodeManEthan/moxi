// The page's draft: changes over the saved config as the page last read it, and the moves
// that order the line. No DOM here, so the Node tests can read it.
//
// The page writes the config only on Save, and its preview always renders what Save
// would write now: the draft's changes over `base`, the stamp of the saved file they were
// made over. When the saved file moves under the draft (another tab's Save, a terminal's
// `moxi statusline use`, a hand edit), the page takes the new file as its base and keeps
// its changes over it (`rebase`).

/** The item in `show` that starts the second row. */
export const ROW = "row";

/** The config's keys the page sets, by their dotted names. */
export const KEYS = ["show", "separator", "color", "dir.parts", "context.warn_at",
                     "context.alert_at"] as const;
export type Key = (typeof KEYS)[number];

/** The config's values, by dotted key. */
export interface Values {
  show: string[];
  separator: string;
  color: boolean;
  "dir.parts": number;
  "context.warn_at": number;
  "context.alert_at": number;
}

/** One change of the draft. */
export interface Change {
  key: Key;
  value: Values[Key];
}

/** Claude Code's `statusLine` as the page server sees it. */
export interface Claude {
  set: boolean;
  command: string | null;
  moxis: boolean;
  here: boolean;
  saved: boolean;
  problem: string | null;
}

/** A module the line can show. */
export interface ModuleItem {
  name: string;
  summary: string;
}

/** `/api/state`, with its values by dotted key. */
export interface State {
  base: string;
  values: Values;
  problem: string | null;
  fields: string[];
  modules: ModuleItem[];
  claude: Claude;
}

/** The body `/api/preview` and `/api/save` take. */
export interface Body {
  base: string;
  changes: Record<string, unknown>;
}

function isObject(value: unknown): value is Record<string, any> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function strings(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? [...value] : null;
}

function int(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

/** `/api/state`'s body as a `State`, or null when it doesn't hold one. */
export function parseState(data: unknown): State | null {
  if (!isObject(data) || typeof data.base !== "string") return null;
  const v = data.values;
  const c = data.claude;
  if (!isObject(v) || !isObject(v.dir) || !isObject(v.context) || !isObject(c)) return null;
  const show = strings(v.show);
  const fields = strings(data.fields);
  const parts = int(v.dir.parts);
  const warn = int(v.context.warn_at);
  const alert = int(v.context.alert_at);
  if (show === null || fields === null || parts === null || warn === null || alert === null
      || typeof v.separator !== "string" || typeof v.color !== "boolean"
      || !Array.isArray(data.modules)) {
    return null;
  }
  const modules: ModuleItem[] = [];
  for (const item of data.modules) {
    if (!isObject(item) || typeof item.name !== "string") return null;
    modules.push({ name: item.name, summary: typeof item.summary === "string" ? item.summary : "" });
  }
  const text = (value: unknown) => (typeof value === "string" ? value : null);
  return {
    base: data.base,
    values: { show, separator: v.separator, color: v.color, "dir.parts": parts,
              "context.warn_at": warn, "context.alert_at": alert },
    problem: text(data.problem),
    fields,
    modules,
    claude: { set: c.set === true, command: text(c.command), moxis: c.moxis === true,
              here: c.here === true, saved: c.saved === true, problem: text(c.problem) },
  };
}

/** Whether two values of a key are the same. */
export function same(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((item, i) => item === b[i]);
  }
  return a === b;
}

/** The draft: `changes` over `saved`, the config file whose stamp is `base`. A change
 * that would leave a key as saved is no change, so `count` is the keys that differ. */
export class Draft {
  base: string;
  saved: Values;
  changes: Change[] = [];

  constructor(state: State) {
    this.base = state.base;
    this.saved = state.values;
  }

  /** How many keys the draft changes. */
  get count(): number {
    return this.changes.length;
  }

  /** Whether the draft differs from the saved config. */
  get dirty(): boolean {
    return this.changes.length > 0;
  }

  /** The values Save would write. */
  values(): Values {
    const out: Values = { ...this.saved, show: [...this.saved.show] };
    for (const change of this.changes) (out as any)[change.key] = change.value;
    return out;
  }

  /** Sets `key` to `value` in the draft. */
  set<K extends Key>(key: K, value: Values[K]): void {
    this.changes = this.changes.filter((change) => change.key !== key);
    if (!same(this.saved[key], value)) {
      this.changes.push({ key, value: Array.isArray(value) ? [...value] : value });
    }
  }

  /** Drops every change: the draft is the saved config again. */
  discard(): void {
    this.changes = [];
  }

  /** Takes `state`'s saved config as the base, keeping the draft's changes over it; a
   * change the new file already holds is dropped. */
  rebase(state: State): void {
    this.base = state.base;
    this.saved = state.values;
    this.changes = this.changes.filter((change) => !same(this.saved[change.key], change.value));
  }

  /** The body of a preview or a Save: `changes` nested as the file holds them, in the
   * order they were made. */
  body(): Body {
    const changes: Record<string, any> = {};
    for (const { key, value } of this.changes) {
      const [head, tail] = key.split(".");
      if (tail === undefined) changes[head] = value;
      else (changes[head] ??= {})[tail] = value;
    }
    return { base: this.base, changes };
  }
}

// --- the order of the line --------------------------------------------------------------

/** Whether `item` is drawn on the page: a base field or a module with a segment. A `show`
 * item that is neither (a module not installed) stays in `show` where it is, undrawn. */
export type Drawn = (item: string) => boolean;

/** The drawn items of row 1 and row 2. */
export function rowsOf(show: readonly string[], drawn: Drawn): [string[], string[]] {
  const at = show.indexOf(ROW);
  const first = (at < 0 ? show : show.slice(0, at)).filter(drawn);
  const second = at < 0 ? [] : show.slice(at + 1).filter(drawn);
  return [first, second];
}

/** `show` with `row` removed when either row has no drawn item. */
function settled(show: string[], drawn: Drawn): string[] {
  const at = show.indexOf(ROW);
  if (at < 0) return show;
  const before = show.slice(0, at).some(drawn);
  const after = show.slice(at + 1).some(drawn);
  return before && after ? show : show.filter((item) => item !== ROW);
}

/** The index of the nearest drawn item or `row` from `from` in `step`'s direction, or -1. */
function neighbour(show: readonly string[], from: number, step: 1 | -1, drawn: Drawn): number {
  for (let i = from + step; i >= 0 && i < show.length; i += step) {
    if (show[i] === ROW || drawn(show[i])) return i;
  }
  return -1;
}

/** Whether `item` can move up and down. */
export function canMove(show: readonly string[], item: string, drawn: Drawn): { up: boolean; down: boolean } {
  const at = show.indexOf(item);
  if (at < 0) return { up: false, down: false };
  const up = neighbour(show, at, -1, drawn) >= 0;
  // Down from the last item of a line of one row starts row 2, unless it is the only item.
  const down = neighbour(show, at, 1, drawn) >= 0
    || (!show.includes(ROW) && rowsOf(show, drawn)[0].length > 1);
  return { up, down };
}

/** `show` with `item` one place up: past the item before it, or from the start of row 2
 * to the end of row 1. */
export function moveUp(show: readonly string[], item: string, drawn: Drawn): string[] {
  const out = [...show];
  const at = out.indexOf(item);
  if (at < 0) return out;
  const to = neighbour(out, at, -1, drawn);
  if (to < 0) return out;
  if (out[to] === ROW) {
    out.splice(at, 1);
    out.splice(to, 0, item);
  } else {
    [out[to], out[at]] = [out[at], out[to]];
  }
  return settled(out, drawn);
}

/** `show` with `item` one place down: past the item after it, from the end of row 1 to
 * the start of row 2, or, from the end of a line of one row, into a new row 2. */
export function moveDown(show: readonly string[], item: string, drawn: Drawn): string[] {
  const out = [...show];
  const at = out.indexOf(item);
  if (at < 0) return out;
  const to = neighbour(out, at, 1, drawn);
  if (to < 0) {
    if (out.includes(ROW)) return out;
    out.splice(at, 1);
    out.push(ROW, item);
  } else if (out[to] === ROW) {
    out.splice(at, 1);
    out.splice(to, 0, item);
  } else {
    [out[to], out[at]] = [out[at], out[to]];
  }
  return settled(out, drawn);
}

/** `show` without `item`. */
export function turnOff(show: readonly string[], item: string, drawn: Drawn): string[] {
  return settled(show.filter((each) => each !== item), drawn);
}

/** `show` with `item` added at the end of row 1. */
export function turnOn(show: readonly string[], item: string, drawn: Drawn): string[] {
  if (show.includes(item)) return [...show];
  const out = [...show];
  const at = out.indexOf(ROW);
  if (at < 0) out.push(item);
  else out.splice(at, 0, item);
  return settled(out, drawn);
}

/** The items the page can draw that `show` leaves out: base fields, then modules. */
export function offItems(show: readonly string[], fields: readonly string[],
                         modules: readonly ModuleItem[]): string[] {
  const all = [...fields, ...modules.map((m) => m.name).filter((name) => !fields.includes(name))];
  return all.filter((item) => !show.includes(item));
}

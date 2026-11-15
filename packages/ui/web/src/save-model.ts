// The Settings page's pending changes and its Save line with no DOM: what each change
// sends, how a row that holds one reads, the Save line's words before, during and after
// a save, each outcome's words beside its row, a change that can hurt and the confirm it
// asks for, and the asks when the page is left with changes not saved.
//
// Nothing is written until Save. A change is held per module, under its key's name (a
// switch table's entry under `<table key>.<entry>`), with the value the page read for it
// (`was`, JSON text). A save sends one module's changes in schema order through
// `POST /api/settings/save`; each result's outcome decides whether its change is dropped
// (it is written) or kept with words beside the row.

import { defaultOf, joined, labelOf, valueOf, valueWords } from "./settings-model.ts";
import type { Setting } from "./settings-model.ts";

/** One change held for a module. */
export interface Change {
  /** The key, or a switch table's entry as `<table key>.<entry>`. */
  name: string;
  /** The new value; a reset's is the default. */
  value: unknown;
  /** True to unset the key, so it reads its default. */
  reset: boolean;
  /** The value the page read for the key, as JSON text. */
  was: string;
}

/** Words beside a row, from a save's answer or a read that found the value moved. */
export interface Note {
  text: string;
  /** A command the note's Copy copies, or null. */
  copy: string | null;
  /** The row's held change reads `still <value>` (it was refused), not `was <value>`. */
  still: boolean;
}

/** `POST /api/settings/save`'s body. */
export interface SaveBody {
  module: string;
  keys: string[];
  values: string[];
  resets: boolean[];
  was: string[];
}

/** One result of a save. */
export interface SaveResult {
  key: string;
  outcome: string;
  problem: string | null;
  fix: string | null;
  mark: string | null;
  warning: string | null;
  lines: string[];
  /** False when the command was stopped before it ended. */
  finished: boolean;
}

/** Whether two JSON values are the same value: `true` is never `1`, and a table's key order
 * doesn't count. */
export function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((item, i) => same(item, b[i]));
  }
  if (a !== null && b !== null && typeof a === "object" && typeof b === "object") {
    const x = a as Record<string, unknown>;
    const y = b as Record<string, unknown>;
    const keys = Object.keys(x);
    return keys.length === Object.keys(y).length && keys.every((key) => key in y && same(x[key], y[key]));
  }
  return false;
}

function parse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

/** A number field's text as the value it sends: a number when it reads as one, else the
 * text as typed, which the shell refuses with its own words. */
export function numberValue(text: string): unknown {
  const trimmed = text.trim();
  const n = Number(trimmed);
  return trimmed !== "" && Number.isFinite(n) ? n : text;
}

/** The key or switch-table entry a change names, with its value as the page read it. */
interface Target {
  key: Setting;
  /** The entry's name, for a switch table's entry. */
  entry: string | null;
  value: unknown;
  position: number;
}

function targetOf(keys: readonly Setting[], name: string): Target | null {
  let position = 0;
  for (const key of keys) {
    if (key.name === name) return { key, entry: null, value: valueOf(key), position };
    if (key.switch_table === true && name.startsWith(`${key.name}.`)) {
      const entries = Array.isArray(key.entries) ? key.entries : [];
      const at = entries.findIndex((entry) => entry?.key === name);
      if (at >= 0) return { key, entry: entries[at].name, value: entries[at].value === true, position: position + at / 1000 };
    }
    position += 1;
  }
  return null;
}

/** The words a change's row is known by on the Save line: its label, an entry's after its table's. */
export function changeLabel(keys: readonly Setting[], name: string): string {
  const target = targetOf(keys, name);
  if (!target) return name;
  return target.entry === null ? labelOf(target.key) : `${labelOf(target.key)} ${target.entry}`;
}

/** A value in words for the row a change names. */
function wordsFor(keys: readonly Setting[], name: string, value: unknown): string {
  const target = targetOf(keys, name);
  if (!target || target.entry !== null) return typeof value === "boolean" ? (value ? "on" : "off") : String(value);
  return valueWords(target.key, value);
}

// --- what can hurt ------------------------------------------------------------------------------

/** One act of a change that can hurt: `Turn off <label>?` and its confirm sentence. */
export interface Hurt {
  name: string;
  /** `Turn off`, `Turn on`, `Add` or `Remove`. */
  act: string;
  /** The item added or removed, for a list. */
  item: string | null;
  ask: string;
  confirm: string;
  says: string;
}

function itemText(item: unknown): string {
  return typeof item === "string" ? item : JSON.stringify(item);
}

/** The acts of `change` on `key` that its `hurts` names: on a bool the value turned on or
 * off as `when` says, on a list each item added or removed as `when` says. */
export function hurtsOf(key: Setting, change: Change): Hurt[] {
  const hurts = key.hurts;
  if (!hurts || change.name !== key.name) return [];
  const label = labelOf(key);
  const before = valueOf(key);
  const base = { name: key.name, confirm: hurts.confirm, says: hurts.says };
  if (key.type === "bool") {
    const wanted = hurts.when === "on";
    if ((hurts.when === "on" || hurts.when === "off") && change.value === wanted && before !== wanted) {
      const act = wanted ? "Turn on" : "Turn off";
      return [{ ...base, act, item: null, ask: `${act} ${label}?` }];
    }
    return [];
  }
  if (key.type === "list") {
    const now = Array.isArray(change.value) ? change.value : [];
    const was = Array.isArray(before) ? before : [];
    if (hurts.when === "add") {
      return now.filter((item) => !was.some((old) => same(old, item))).map((item) => ({
        ...base, act: "Add", item: itemText(item), ask: `Add ${itemText(item)} to ${label}?` }));
    }
    if (hurts.when === "remove") {
      return was.filter((item) => !now.some((kept) => same(kept, item))).map((item) => ({
        ...base, act: "Remove", item: itemText(item), ask: `Remove ${itemText(item)} from ${label}?` }));
    }
  }
  return [];
}

/** Whether `key`'s label and switch stay in the warning colour: its `hurts` says the value
 * held is the danger, and `value` is that value. */
export function staysWarned(key: Setting, value: unknown): boolean {
  const hurts = key.hurts;
  if (!hurts || hurts.stays !== true || key.type !== "bool") return false;
  return (hurts.when === "on" && value === true) || (hurts.when === "off" && value === false);
}

/** The confirm Save turns into when a change can hurt, or null when none can. */
export interface Confirm {
  /** Each act's question and its confirm sentence. */
  asks: { ask: string; confirm: string }[];
  /** `Nothing else in this save can hurt.` */
  rest: string;
  /** The warning-colour button: `Turn off and save`. */
  button: string;
  back: string;
}

export const NOTHING_ELSE = "Nothing else in this save can hurt.";

/** The confirm for a module's changes, or null when none of them can hurt. */
export function confirmOf(keys: readonly Setting[], changes: readonly Change[]): Confirm | null {
  const hurts = changes.flatMap((change) => {
    const target = targetOf(keys, change.name);
    return target && target.entry === null ? hurtsOf(target.key, change) : [];
  });
  if (hurts.length === 0) return null;
  const acts = [...new Set(hurts.map((hurt) => hurt.act))];
  return {
    asks: hurts.map((hurt) => ({ ask: hurt.ask, confirm: hurt.confirm })),
    rest: NOTHING_ELSE,
    button: acts.length === 1 ? `${acts[0]} and save` : "Save anyway",
    back: "Go back",
  };
}

// --- the held changes ---------------------------------------------------------------------------------

/** The line under a row that holds a change. */
export interface HeldLine {
  /** `not saved · was <value>`, `not saved · <says>`, or a refused row's `still <value>`. */
  text: string;
  /** The change can hurt: drawn in the warning colour. */
  hurts: boolean;
}

/**
 * Every change the page holds, per module, and the words beside rows a save or a read
 * left there. Moving between modules or searching keeps them.
 */
export class Pending {
  private readonly held = new Map<string, Map<string, Change>>();
  private readonly notes = new Map<string, Map<string, Note>>();

  private module(module: string): Map<string, Change> {
    let changes = this.held.get(module);
    if (!changes) this.held.set(module, (changes = new Map()));
    return changes;
  }

  private put(module: string, change: Change): void {
    this.module(module).set(change.name, change);
    this.notes.get(module)?.delete(change.name);
  }

  /** A new value for `key`; a value equal to the one read drops the change. */
  edit(module: string, key: Setting, value: unknown): void {
    if (same(value, valueOf(key))) this.undo(module, key.name);
    else this.put(module, { name: key.name, value, reset: false, was: key.json ?? "null" });
  }

  /** A switch table's entry turned on or off. */
  editEntry(module: string, table: Setting, entry: string, on: boolean): void {
    const found = (Array.isArray(table.entries) ? table.entries : []).find((item) => item?.name === entry);
    const was = found?.value === true;
    const name = found?.key ?? `${table.name}.${entry}`;
    if (on === was) this.undo(module, name);
    else this.put(module, { name, value: on, reset: false, was: JSON.stringify(was) });
  }

  /** `Reset`: the key back to its default, held like any change. */
  reset(module: string, key: Setting): void {
    this.put(module, { name: key.name, value: defaultOf(key), reset: true, was: key.json ?? "null" });
  }

  /** `Undo`: the row back to what the page read, its words gone. */
  undo(module: string, name: string): void {
    this.held.get(module)?.delete(name);
    this.notes.get(module)?.delete(name);
  }

  /** `Discard`: every change of the module dropped. */
  discard(module: string): void {
    this.held.delete(module);
    this.notes.delete(module);
  }

  /** The change held for a key, or undefined. */
  changeOf(module: string, name: string): Change | undefined {
    return this.held.get(module)?.get(name);
  }

  /** The words beside a row, or undefined. */
  noteOf(module: string, name: string): Note | undefined {
    return this.notes.get(module)?.get(name);
  }

  /** The module's changes in schema order (`keys`, the module's read). */
  changes(module: string, keys: readonly Setting[] = []): Change[] {
    const list = [...(this.held.get(module)?.values() ?? [])];
    const at = (name: string) => targetOf(keys, name)?.position ?? Number.MAX_SAFE_INTEGER;
    return list.map((change, i) => ({ change, i }))
               .sort((a, b) => at(a.change.name) - at(b.change.name) || a.i - b.i)
               .map(({ change }) => change);
  }

  /** How many changes the module holds. */
  count(module: string): number {
    return this.held.get(module)?.size ?? 0;
  }

  /** The modules holding changes, in the order their first change was made. */
  modules(): string[] {
    return [...this.held].filter(([, changes]) => changes.size > 0).map(([module]) => module);
  }

  /** Every change held, over every module. */
  total(): number {
    return this.modules().reduce((n, module) => n + this.count(module), 0);
  }

  /** The body that saves the module's changes, in schema order. */
  body(module: string, keys: readonly Setting[]): SaveBody {
    const changes = this.changes(module, keys);
    return {
      module,
      keys: changes.map((change) => change.name),
      values: changes.map((change) => (change.reset ? "" : JSON.stringify(change.value ?? null))),
      resets: changes.map((change) => change.reset),
      was: changes.map((change) => change.was),
    };
  }

  /**
   * A new read of the module. A held change whose key now reads another value than its
   * `was` is never moved silently: when the read value is the one held, the change is
   * dropped (the file holds it); else the row keeps what was typed, its `was` follows the
   * read, and it says it moved.
   */
  refresh(module: string, keys: readonly Setting[]): void {
    for (const change of [...(this.held.get(module)?.values() ?? [])]) {
      const target = targetOf(keys, change.name);
      if (!target) continue;
      if (same(target.value, parse(change.was))) continue;
      if (!change.reset && same(target.value, change.value)) {
        this.undo(module, change.name);
        continue;
      }
      change.was = JSON.stringify(target.value ?? null);
      this.note(module, change.name, { text: movedWords(wordsFor(keys, change.name, target.value)), copy: null, still: false });
    }
  }

  private note(module: string, name: string, note: Note): void {
    let notes = this.notes.get(module);
    if (!notes) this.notes.set(module, (notes = new Map()));
    notes.set(name, note);
  }

  /**
   * A save's answer for the module: a written change is dropped, one not written is kept
   * with its words beside the row. `keys` is the read the save was made from.
   */
  answer(module: string, keys: readonly Setting[], results: readonly SaveResult[]): void {
    for (const result of results) {
      const change = this.changeOf(module, result.key);
      const note = outcomeNote(module, keys, result);
      if (dropped(result)) {
        this.held.get(module)?.delete(result.key);
        this.notes.get(module)?.delete(result.key);
      } else if (change && result.outcome === "moved" && typeof result.problem === "string") {
        change.was = result.problem;
      }
      if (note) this.note(module, result.key, note);
    }
  }

  /** The line under a row that holds a change, or null. */
  heldLine(module: string, keys: readonly Setting[], name: string): HeldLine | null {
    const change = this.changeOf(module, name);
    if (!change) return null;
    const was = wordsFor(keys, name, parse(change.was));
    if (this.noteOf(module, name)?.still) return { text: `still ${was}`, hurts: false };
    const target = targetOf(keys, name);
    const hurts = target && target.entry === null ? hurtsOf(target.key, change) : [];
    if (hurts.length > 0) return { text: `not saved · ${hurts[0].says}`, hurts: true };
    return { text: `not saved · was ${was}`, hurts: false };
  }
}

// --- an answer's words ------------------------------------------------------------------------------

/** A result whose value the file holds: saved, unchanged, or written with its daemons
 * failing. The Save line counts these as saved. */
export function saved(result: SaveResult): boolean {
  return result.outcome === "saved" || result.outcome === "unchanged"
         || (result.outcome === "failed" && result.finished !== false && typeof result.warning === "string");
}

/** A result whose change the page stops holding: one saved, and one whose command did not
 * finish, whose row shows what the file holds now. */
export function dropped(result: SaveResult): boolean {
  return saved(result) || (result.outcome === "failed" && result.finished === false);
}

/** Every change of the save was written, or the file held it already: `Save and leave` leaves. */
export function allSaved(results: readonly SaveResult[]): boolean {
  return results.every((result) => result.outcome === "saved" || result.outcome === "unchanged");
}

function sentence(text: string): string {
  const trimmed = text.trim().replace(/[.\s]+$/, "");
  return trimmed === "" ? "" : `${trimmed[0].toUpperCase()}${trimmed.slice(1)}.`;
}

export function movedWords(value: string): string {
  return `Not saved: it changed since this page read it, to ${value}. Save again to write yours.`;
}

export const BUSY = "The settings file was busy. Save again.";
export const NOT_FINISHED = "May not be saved: moxi config did not finish. The row shows what the file holds now.";

/** The words beside a row for its result, or null for one written with nothing to say. */
export function outcomeNote(module: string, keys: readonly Setting[], result: SaveResult): Note | null {
  switch (result.outcome) {
    case "saved":
    case "unchanged":
      return null;
    case "refused": {
      const parts = ["Not saved.", sentence(result.problem ?? ""), sentence(result.fix ?? "")].filter(Boolean);
      return { text: parts.join(" "), copy: null, still: true };
    }
    case "moved": {
      const now = typeof result.problem === "string" ? parse(result.problem) : undefined;
      return { text: movedWords(wordsFor(keys, result.key, now)), copy: null, still: false };
    }
    case "busy":
      return { text: BUSY, copy: null, still: false };
    default:
      if (result.finished === false) return { text: NOT_FINISHED, copy: null, still: false };
      if (typeof result.warning === "string") {
        const command = `moxi install ${module}`;
        return { text: `${changeLabel(keys, result.key)} is saved, and applying its daemons failed. Run ${command}.`,
                 copy: command, still: false };
      }
      return { text: ["Not saved.", sentence(result.problem ?? "")].filter(Boolean).join(" "), copy: null, still: true };
  }
}

/** A mark as the Saved line words it after one label, and after several. */
function markPhrase(mark: string | null): [string, string] {
  const plain = (mark ?? "applies now").replaceAll("`", "");
  if (plain === "applies at next start") return ["applies at the next start", "apply at the next start"];
  if (plain === "turns the daemon on or off now") return ["turns its daemon on or off now", "turn their daemons on or off now"];
  const plural = plain.replace(/^applies\b/, "apply").replace(/^turns\b/, "turn").replace(/^starts\b/, "start");
  return [plain, plural];
}

/** The time a save was answered, as ui writes a clock time. */
export function clockOf(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * The Save line's words for an answer: `Saved <time> · <labels> apply now · ...`, grouped
 * by mark; or, when a change was not written, `<k> of <n> saved at <time> · <label> was
 * refused: it says why beside the field`.
 */
export function answerWords(keys: readonly Setting[], results: readonly SaveResult[], time: string): string {
  const label = (result: SaveResult) => changeLabel(keys, result.key);
  const kept = results.filter((result) => !saved(result));
  if (kept.length === 0) {
    const groups = new Map<string, string[]>();
    for (const result of results) {
      const mark = result.mark ?? "applies now";
      groups.set(mark, [...(groups.get(mark) ?? []), label(result)]);
    }
    const parts = [...groups].map(([mark, labels]) => {
      const [one, many] = markPhrase(mark);
      return `${joined(labels)} ${labels.length === 1 ? one : many}`;
    });
    return [`Saved ${time}`, ...parts].join(" · ");
  }
  const refused = kept.every((result) => result.outcome === "refused");
  const what = kept.length === 1
    ? `${label(kept[0])} was ${refused ? "refused" : "not saved"}: it says why beside the field`
    : `${joined(kept.map(label))} were ${refused ? "refused" : "not saved"}: each says why beside its field`;
  return `${results.length - kept.length} of ${results.length} saved at ${time} · ${what}`;
}

/** The Save line's words when the save got no answer it could read; the changes are kept. */
export function transportWords(status: number): string {
  if (status === 0) return "No answer from the shell; nothing may have been saved.";
  if (status === 403) return "This browser is signed out.";
  return `The shell answered ${status}.`;
}

/** The Save line at rest or while a save runs. */
export function heldWords(keys: readonly Setting[], changes: readonly Change[], saving: boolean): string {
  if (saving) return `Saving ${changes.length}…`;
  if (changes.length === 0) return "Nothing to save";
  return `${changes.length} not saved · ${changes.map((change) => changeLabel(keys, change.name)).join(", ")}`;
}

// --- leaving with changes ------------------------------------------------------------------------------

/** The ask in the Save line's place when the page is left with changes not saved. */
export interface LeaveAsk {
  /** The question, and a line per module when several hold changes. */
  lines: string[];
  /** In the order drawn: `Stay`, `Leave without saving`, `Save and leave` for one module;
   * `Leave without saving`, `Stay` for several. */
  buttons: string[];
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * The leave ask: changes only in the module shown offer `Save and leave`; changes in
 * another module, or several, name each module's and say Save writes one at a time.
 * `labels(module)` gives a module's changes' labels in schema order.
 */
export function leaveAsk(pending: Pending, shown: string | null, labels: (module: string) => string[]): LeaveAsk | null {
  const modules = pending.modules();
  if (modules.length === 0) return null;
  const n = pending.total();
  if (modules.length === 1 && modules[0] === shown) {
    return { lines: [`Leave Settings with ${plural(n, "change")} in ${shown} not saved? ${labels(shown).join(", ")}.`],
             buttons: ["Stay", "Leave without saving", "Save and leave"] };
  }
  return {
    lines: [`Leave Settings with ${plural(n, "change")} not saved?`,
            ...modules.map((module) => `${pending.count(module)} in ${module}: ${labels(module).join(", ")}.`),
            "Save writes one module at a time."],
    buttons: ["Leave without saving", "Stay"],
  };
}

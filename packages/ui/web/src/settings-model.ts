// The Settings page's rows with no DOM: which of a module's keys are drawn, each drawn
// key's row (its label, its control by type, the dim lines under it, `default ... · Reset`),
// the keys before and behind `More`, and the counts of changed keys in the list.
//
// A key is `GET /api/settings`' descriptor: `moxi.config.Key`'s fields, its value as JSON
// text (`json`), its default as JSON text (`default_json`), `changed`, and the page fields
// a module's schema may fill. A key with no page fields is drawn on its own facts: its
// dotted key in the label's place, its doc as the line, and no `More`.
//
// One set of drawn keys per module serves the pane, the counts, Find and the changed
// view: every key but one whose `needs` module is absent and one edited on another page
// (`elsewhere`). ui's `gateway.*` keys are drawn by the Phone group, not among the pane's
// rows, and are drawn keys only while that group shows. The read's `count` leaves them
// out; the page adds them here.

import type { FieldDescriptor } from "./client.ts";

/** The core's name in the list and the read. */
export const CORE = "moxi";
/** The module whose `gateway.*` keys the Phone group draws. */
export const PHONE_MODULE = "ui";
const PHONE_PREFIX = "gateway.";

/** The mark of a key whose change applies at once: it gets no sentence. */
export const MARK_NOW = "applies now";
/** The sentences for the marks the shell gives, where the mark's own words don't read as one. */
const MARK_SENTENCES: Record<string, string> = {
  "applies at next start": "Applies at the next start.",
  "turns the daemon on or off now": "Turns its daemon on or off.",
};

export const LOCKED = "Locked for this run of the shell.";
/** The hint in an empty path field. */
export const PATH_HINT = "an absolute path";

/** A string longer than this, or one holding a line break, is drawn as its first line and Edit. */
export const LONG_TEXT = 80;
/** A list whose items are all at most this long, and hold no space, is drawn as boxes. */
export const SHORT_ITEM = 24;
/** An enum with this many values or more is drawn as a pick list. */
export const PICK_FROM = 5;

/** An enum value and the words the page shows for it. */
export interface Choice {
  value: string;
  words: string;
}

/** `hurts`: the change that can hurt, and what the page says about it. */
export interface Hurts {
  /** `on` or `off` on a bool, `add` or `remove` on a list. */
  when: string;
  says: string;
  confirm: string;
  /** The warning stays while the value held equals `when`. */
  stays: boolean;
}

/** One key of the read, with its page fields. */
export interface Setting extends FieldDescriptor {
  label?: string | null;
  line?: string | null;
  common?: boolean;
  group?: string | null;
  unit?: string | null;
  choices?: Choice[] | null;
  pick?: boolean;
  hurts?: Hurts | null;
  terminal?: { on: string; off: string } | null;
  elsewhere?: string | null;
  /** The value as JSON text. */
  json?: string;
  /** The default as JSON text. */
  default_json?: string;
  /** The value is not the default (a switch table: an entry is on). */
  changed?: boolean;
}

/** One module's section of the read. */
export interface Section {
  module: string;
  keys: Setting[];
  error: string | null;
  fix: string | null;
  readonly: boolean;
  /** The module's config file, the home folder written `~`. */
  file: string | null;
  /** Its drawn keys, ui's `gateway.*` left out. */
  count: number;
}

/** `text` parsed as JSON, or undefined when it is none. */
export function parsed(text: unknown): unknown {
  if (typeof text !== "string") return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

/** The key's value, from its JSON text. */
export function valueOf(key: Setting): unknown {
  return parsed(key.json);
}

/** The key's default, from its JSON text. */
export function defaultOf(key: Setting): unknown {
  return parsed(key.default_json);
}

function words(text: unknown): string | null {
  return typeof text === "string" && text !== "" ? text : null;
}

/** Whether `key` is one of ui's keys the Phone group draws. */
export function isPhoneKey(module: string, key: Setting): boolean {
  return module === PHONE_MODULE && key.name.startsWith(PHONE_PREFIX);
}

/**
 * The module's drawn keys, in schema order: every key but one whose `needs` module is
 * absent and one edited on another page; ui's `gateway.*` keys only while `phone`, the
 * Phone group, shows.
 */
export function drawnKeys(module: string, keys: readonly Setting[], phone: boolean): Setting[] {
  return keys.filter((key) => typeof key?.name === "string" && key.needs_absent !== true && !words(key.elsewhere)
                              && (phone || !isPhoneKey(module, key)));
}

/** The drawn keys the pane draws as rows: the Phone group's keys are its own. */
export function paneKeys(module: string, keys: readonly Setting[]): Setting[] {
  return drawnKeys(module, keys, false);
}

/** The section's drawn keys: the read's `count`, and ui's phone keys while the group shows. */
export function countOf(section: Section, phone: boolean): number {
  const base = Number.isInteger(section.count) ? section.count : paneKeys(section.module, section.keys).length;
  if (!phone) return base;
  return base + drawnKeys(section.module, section.keys, true).filter((key) => isPhoneKey(section.module, key)).length;
}

/** How many of the section's drawn keys are changed from their default. */
export function changedCount(section: Section, phone: boolean): number {
  return drawnKeys(section.module, section.keys, phone).filter((key) => key.changed === true).length;
}

/**
 * The listed modules in the list's order: the core, ui, then the rest A to Z. The read
 * lists only present modules with settings; a name it lists twice is shown once.
 */
export function moduleOrder(listed: readonly string[]): string[] {
  const rest = [...new Set(listed.filter((name) => name !== CORE && name !== PHONE_MODULE))].sort();
  return [CORE, ...(listed.includes(PHONE_MODULE) ? [PHONE_MODULE] : []), ...rest];
}

/**
 * The total of changed drawn keys over every listed module, or null while a listed
 * module's section has not arrived, so the total is never a part read as the whole. A
 * section that can't be read counts none.
 */
export function totalChanged(listed: readonly string[], sections: ReadonlyMap<string, Section>,
                             phone: boolean): number | null {
  let total = 0;
  for (const name of new Set(listed)) {
    const section = sections.get(name);
    if (section === undefined) return null;
    if (section.error === null) total += changedCount(section, phone);
  }
  return total;
}

// --- words for a value ---------------------------------------------------------------------

/** `a`, `a and b`, `a, b and c`. */
export function joined(items: readonly string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function itemWords(item: unknown): string {
  if (typeof item === "string") return item;
  if (item !== null && typeof item === "object" && !Array.isArray(item)) {
    const name = (item as Record<string, unknown>).name;
    if (typeof name === "string" && name !== "") return name;
  }
  return JSON.stringify(item) ?? String(item);
}

/**
 * A value of `key` in words, as a row's `default`, a terminal key's value, `was` and Find
 * say it: `on`/`off`, an enum's `choices` words, a number, the text, `not set` for none or
 * an empty text, a list's items joined (a table row by its `name`), `none` for an empty
 * list, a switch table's entries that are on.
 */
export function valueWords(key: Setting, value: unknown): string {
  if (value === null || value === undefined || value === "") return "not set";
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") {
    const choice = (key.choices ?? []).find((row) => row?.value === value);
    return choice ? choice.words : value;
  }
  if (Array.isArray(value)) return value.length === 0 ? "none" : joined(value.map(itemWords));
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (key.switch_table === true) {
      const on = entries.filter(([, entry]) => entry === true).map(([name]) => name);
      return on.length === 0 ? "none" : joined(on);
    }
    return entries.length === 0 ? "none" : entries.map(([name, entry]) => `${name} = ${itemWords(entry)}`).join(", ");
  }
  return String(value);
}

// --- a row ----------------------------------------------------------------------------------

/** One switch of a switch table: an entry, written as `<table key>.<entry>`. */
export interface Toggle {
  key: string;
  label: string;
  on: boolean;
  /** The entry's mark as a sentence, or null when it applies at once. */
  line: string | null;
  /** The shell refuses a write to the entry for this run. */
  locked: boolean;
}

export type Control =
  /** A switch. */
  | { kind: "switch"; on: boolean }
  /** An enum's values side by side, the selected one boxed. */
  | { kind: "choices"; options: Choice[]; selected: string }
  /** An enum's values in a pick list. */
  | { kind: "pick"; options: Choice[]; selected: string }
  /** A short field, right-aligned, with its unit after it. */
  | { kind: "number"; text: string; unit: string | null; whole: boolean }
  /** A field; a path's hint is `an absolute path`. */
  | { kind: "text"; text: string; path: boolean }
  /** A long text: its first line and Edit, which opens the whole text. */
  | { kind: "long"; text: string; first: string }
  /** A list of short items: each as a box, then Add (`none` when empty). */
  | { kind: "names"; items: string[] }
  /** A list of longer items: `<n> lines · Edit` and the first item. */
  | { kind: "lines"; items: string[]; first: string }
  /** A list of tables: one row per item, the columns the items' keys, Remove per row. */
  | { kind: "rows"; columns: string[]; rows: Record<string, unknown>[] }
  /** A switch table: one switch per entry. */
  | { kind: "switches"; toggles: Toggle[] }
  /** A table: its TOML text, behind Edit. */
  | { kind: "toml"; text: string }
  /** A key changed at the terminal: its value as a word, no control. */
  | { kind: "word"; text: string };

/** One drawn key as the page draws it. */
export interface Row {
  name: string;
  /** The label, or the dotted key when the key has none. */
  label: string;
  /** The key has a label, so its dotted key shows dim beside it. */
  labelled: boolean;
  control: Control;
  /** The dim lines under it, in order. */
  lines: string[];
  /** A terminal key's line, with the command its Copy copies. */
  terminal: { text: string; command: string } | null;
  /** `default <words>`, and whether `· Reset` follows; null on a key not changed. */
  defaults: { words: string; reset: boolean } | null;
  /** The shell refuses a write to the key for this run: its control is off. */
  locked: boolean;
}

/** The plain words a key leads with: its label, else its dotted key. */
export function labelOf(key: Setting): string {
  return words(key.label) ?? key.name;
}

/** A mark as a sentence under the row, or null for one that applies at once. */
export function markLine(mark: unknown): string | null {
  if (typeof mark !== "string" || mark === "" || mark === MARK_NOW) return null;
  const known = MARK_SENTENCES[mark];
  if (known) return known;
  const plain = mark.replaceAll("`", "");
  return `${plain[0].toUpperCase()}${plain.slice(1)}${/[.!?]$/.test(plain) ? "" : "."}`;
}

function terminalOf(key: Setting): { on: string; off: string } | null {
  const terminal = key.terminal;
  return terminal && typeof terminal.on === "string" && typeof terminal.off === "string" ? terminal : null;
}

function texts(items: readonly unknown[]): string[] {
  return items.map((item) => (typeof item === "string" ? item : itemWords(item)));
}

function isTable(item: unknown): item is Record<string, unknown> {
  return item !== null && typeof item === "object" && !Array.isArray(item);
}

/** The columns of a list of tables: every item's keys, in the order first met. */
function columnsOf(items: readonly Record<string, unknown>[]): string[] {
  const out: string[] = [];
  for (const item of items) for (const name of Object.keys(item)) if (!out.includes(name)) out.push(name);
  return out;
}

function enumControl(key: Setting, value: unknown): Control {
  const values = Array.isArray(key.values) ? key.values.filter((v): v is string => typeof v === "string") : [];
  const words = new Map((key.choices ?? []).map((row) => [row.value, row.words]));
  const options = values.map((v) => ({ value: v, words: words.get(v) ?? v }));
  const selected = typeof value === "string" ? value : "";
  return { kind: key.pick === true || values.length >= PICK_FROM ? "pick" : "choices", options, selected };
}

function listControl(key: Setting, value: unknown): Control {
  const items = Array.isArray(value) ? value : [];
  const fallback = defaultOf(key);
  const sample = items.length > 0 ? items : Array.isArray(fallback) ? fallback : [];
  if (sample.length > 0 && sample.every(isTable)) {
    return { kind: "rows", columns: columnsOf(sample as Record<string, unknown>[]),
             rows: items.filter(isTable).map((item) => ({ ...item })) };
  }
  const strings = texts(items);
  if (strings.every((item) => item.length <= SHORT_ITEM && !/\s/.test(item))) return { kind: "names", items: strings };
  return { kind: "lines", items: strings, first: strings[0] ?? "" };
}

/** The control `key` is drawn with, by its type (a terminal key: its value as a word). */
export function controlOf(key: Setting): Control {
  const value = valueOf(key);
  if (terminalOf(key)) return { kind: "word", text: valueWords(key, value) };
  if (key.switch_table === true) {
    const entries = Array.isArray(key.entries) ? key.entries : [];
    return { kind: "switches", toggles: entries.filter((entry) => typeof entry?.key === "string").map((entry) => ({
      key: entry.key, label: entry.name || entry.key, on: entry.value === true, line: markLine(entry.mark),
      locked: entry.locked === true })) };
  }
  switch (key.type) {
    case "bool":
      return { kind: "switch", on: value === true };
    case "enum":
      return enumControl(key, value);
    case "int":
    case "float":
      return { kind: "number", text: typeof value === "number" ? String(value) : "", unit: words(key.unit),
               whole: key.type === "int" };
    case "list":
      return listControl(key, value);
    case "table":
      return { kind: "toml", text: typeof key.value === "string" ? key.value : "" };
    default: {
      const text = typeof value === "string" ? value : "";
      if (text.length > LONG_TEXT || /[\r\n]/.test(text)) return { kind: "long", text, first: text.split(/\r?\n/)[0] };
      return { kind: "text", text, path: key.type === "path" };
    }
  }
}

/**
 * The dim lines under a row, in order: the key's `line` (with a label) or its doc
 * (without one); its mark as a sentence when it does not apply at once; who sets it
 * when the shell's environment does; why a switch table's entries can't all be listed;
 * and the lock.
 */
export function linesOf(key: Setting): string[] {
  const out: string[] = [];
  const said = words(key.label) ? words(key.line) : words(key.line) ?? words(key.doc);
  if (said) out.push(said);
  const mark = markLine(key.mark);
  if (mark) out.push(mark);
  if (key.source === "env" && words(key.env)) {
    out.push(`Set by ${key.env} in the shell's environment, which wins over the file.`);
  }
  if (key.switch_table === true && key.declarations === "unavailable" && words(key.reason)) out.push(key.reason as string);
  if (key.locked === true) out.push(LOCKED);
  return out;
}

/**
 * `default <value>` on a changed key, with `· Reset` only where a write would change the
 * value read: the file holds it, it is not locked and not changed at the terminal.
 */
export function defaultsOf(key: Setting): { words: string; reset: boolean } | null {
  if (key.changed !== true) return null;
  return { words: valueWords(key, defaultOf(key)),
           reset: key.source === "file" && key.locked !== true && terminalOf(key) === null };
}

/** A terminal key's line: the command for the state it is not in, which Copy copies. */
export function terminalLine(key: Setting): { text: string; command: string } | null {
  const terminal = terminalOf(key);
  if (!terminal) return null;
  return valueOf(key) === true ? { text: `Turned off at the terminal: ${terminal.off}`, command: terminal.off }
                               : { text: `Turned on at the terminal: ${terminal.on}`, command: terminal.on };
}

/** The row for one drawn key. */
export function rowOf(key: Setting): Row {
  return {
    name: key.name,
    label: labelOf(key),
    labelled: words(key.label) !== null,
    control: controlOf(key),
    lines: linesOf(key),
    terminal: terminalLine(key),
    defaults: defaultsOf(key),
    locked: key.locked === true,
  };
}

// --- More and the sub-heads ------------------------------------------------------------------

/**
 * The pane's keys before `More` and behind it: the `common` keys, in schema order, then
 * the rest. A module that marks no key common shows every key, with no `More` (null).
 */
export function splitMore(keys: readonly Setting[]): { first: Setting[]; more: Setting[] | null } {
  if (!keys.some((key) => key.common === true)) return { first: [...keys], more: null };
  const more = keys.filter((key) => key.common !== true);
  return { first: keys.filter((key) => key.common === true), more: more.length > 0 ? more : null };
}

/** A run of keys under one sub-head (none for keys with no `group`). */
export interface Block {
  group: string | null;
  keys: Setting[];
}

/** The keys in schema order, a sub-head starting wherever the `group` changes. */
export function blocksOf(keys: readonly Setting[]): Block[] {
  const out: Block[] = [];
  for (const key of keys) {
    const group = words(key.group);
    const last = out[out.length - 1];
    if (last && last.group === group) last.keys.push(key);
    else out.push({ group, keys: [key] });
  }
  return out;
}

/**
 * The `More` line's words after `More`: `<n> settings: <what>`, `what` being the group
 * names in order, then the labels of the keys with no group, each in lower case; then
 * `· <k> changed` when any is.
 */
export function moreWords(more: readonly Setting[]): { text: string; changed: string | null } {
  const groups: string[] = [];
  const loose: string[] = [];
  for (const key of more) {
    const group = words(key.group);
    if (group) {
      if (!groups.includes(group)) groups.push(group);
    } else {
      loose.push(labelOf(key));
    }
  }
  const what = [...groups, ...loose].map((name) => name.toLowerCase()).join(", ");
  const k = more.filter((key) => key.changed === true).length;
  return { text: `${more.length} ${more.length === 1 ? "setting" : "settings"}: ${what}`,
           changed: k > 0 ? `${k} changed` : null };
}

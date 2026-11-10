// What the Options panel of the launcher's piece on Home holds, worked out with no DOM so
// the Node tests can drive it. prompt.ts draws it.
//
// The panel is the launch form's remaining rows, grouped as the catalog says: each row
// names its group, its subgroup and its label. A row is in the panel only when the page
// server says it is shown on this install, so neither a group nor the find field ever
// reaches a row whose module is missing. The rows whose text or file a launch sends as its
// first turn are not in the panel: the box's text is the whole first turn. Nor are the rows
// that say where the session starts: a launch takes one of them, and the line above the
// box sets it.
//
// A value set in the panel is kept by the row's name and goes into the launch request with
// the other rows the piece sets. Each row is drawn by the shell's one form renderer, from
// the descriptor `drawnField` gives it.
//
// Every string here goes into the page as text.

import type { FieldDescriptor, FormChange, FormValue } from "/m/ui/client.js";

import { GROUPS, isListField, leaveOutChoice, same, shownValue } from "./panel.ts";
import { FIRST_TURN } from "./prompt-model.ts";

/** A launch form's field with what the catalog says of its place in the panel. */
export interface PanelField extends FieldDescriptor {
  group?: string | null;
  sub?: string | null;
  /** The row in plain words. */
  label?: string | null;
  /** False where this install lacks a module the row is drawn with. */
  shown?: boolean;
  /** False where a row that offers values takes none but those. */
  typed?: boolean;
  /** The row's values that can do harm. */
  danger?: string[];
  /** False for a row that is never filled from an earlier launch. */
  carry?: boolean;
  /** The command modes the row's flag is passed in. */
  modes?: string[];
}

/** The panel's width, which Home moves its column aside by. */
export const PANEL_WIDTH_PX = 400;

/** The event that tells Home how wide the panel beside its column is; 0 is no panel. */
export const SIDE_EVENT = "moxi-home-side";

/** One group of the panel: the catalog's key, its title, and its subgroups in order. */
export interface GroupShape {
  key: string;
  title: string;
  subs: readonly string[];
}

/** The panel's groups, in the catalog's order. The catalog holds no order of subgroups, so
 * it is here; a subgroup this list lacks follows the listed ones, in the fields' order. */
export const PANEL_GROUPS: readonly GroupShape[] = [
  { key: "access", title: "Access", subs: ["Mode", "Reach"] },
  { key: "tools", title: "Tools", subs: ["Built-in", "MCP", "Web & browser"] },
  { key: "seat", title: "Seat", subs: ["Role", "Comms", "Instructions", "Name & notes"] },
  { key: "runs-in", title: "Runs in", subs: ["Remote"] },
  { key: "lifecycle", title: "Lifecycle", subs: ["Context", "Cache"] },
  { key: "config", title: "Config", subs: ["Layers", "Pass-through"] },
];

/** What a group or subgroup says while every row of it is at its shipped default. */
export const DEFAULTS = "defaults";

/** The mark on a row whose flag the harness does not list in its help. */
export const HIDDEN_FLAG = "hidden flag";

/** What the find field says when no row matches. */
export const NO_MATCH = "No option matches.";

function said(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** A row's name in plain words: its label, else its name with spaces, first letter capital. */
export function rowLabel(field: PanelField): string {
  const label = said(field.label);
  if (label !== "") return label;
  const words = field.name.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The rows that say where the session starts, of which a launch takes one. */
export const PLACE_ROWS: readonly string[] = ["cwd", "project", "scratch"];

/** Whether the panel draws a row: it is in one of the panel's groups, the page server says
 * it is shown here and takes input, and it is neither one of the first turn's rows nor one
 * of the place's. */
export function inPanel(field: PanelField): boolean {
  return PANEL_GROUPS.some((group) => group.key === field.group) && field.shown !== false
    && field.needs_absent !== true && field.owned !== true
    && !(FIRST_TURN as readonly string[]).includes(field.name) && !PLACE_ROWS.includes(field.name);
}

/** The rows the panel draws, in the fields' order. */
export function panelFields(fields: readonly PanelField[]): PanelField[] {
  return fields.filter(inPanel);
}

// --- a row's value -------------------------------------------------------------------------

/** A row's value now: the one set in the panel, else what the form is filled with. */
export function valueNow(field: PanelField, set: Readonly<Record<string, FormValue>>): FormValue {
  return Object.hasOwn(set, field.name) ? set[field.name] : shownValue(field);
}

/** The value a row ships with, whatever the user's defaults or a preset set. */
export function shippedValue(field: PanelField): FormValue {
  return shownValue({ ...field, value: undefined, value_items: undefined });
}

/** A value as the launch takes it: a row left empty launches with its shipped default. */
function settled(field: PanelField, value: FormValue): FormValue {
  if (Array.isArray(value) || field.optional_value === true) return value;
  if (field.type === "bool") return value === "true" ? "true" : "false";
  if (value === null || value === "" || value === leaveOutChoice(field)) {
    return said(field.default) !== "" ? said(field.default) : null;
  }
  return value;
}

/** Whether a value launches something other than the row's shipped default. */
export function differs(field: PanelField, value: FormValue): boolean {
  return !same(settled(field, value), settled(field, shippedValue(field)));
}

/** A row's value in a few words, for a summary: a pick is its word alone, any other row
 * leads with its label. */
export function valueWords(field: PanelField, value: FormValue): string {
  const label = rowLabel(field);
  if (Array.isArray(value)) return `${label} ${value.length === 0 ? "none" : value.length}`;
  if (field.optional_value === true) return `${label} ${value === null ? "off" : value === "" ? "on" : value}`;
  const now = settled(field, value);
  if (field.type === "bool") return `${label} ${now === "true" ? "on" : "off"}`;
  if (now === null) return `${label} empty`;
  return field.type === "enum" ? String(now) : `${label} ${now}`;
}

/** What a closed group or subgroup says of its rows: each value that differs from the
 * shipped default, else `defaults`. */
export function summaryOf(fields: readonly PanelField[], set: Readonly<Record<string, FormValue>>): string {
  const words = fields.filter((field) => differs(field, valueNow(field, set)))
    .map((field) => valueWords(field, valueNow(field, set)));
  return words.length === 0 ? DEFAULTS : words.join(" · ");
}

// --- the groups ----------------------------------------------------------------------------

/** One row as the panel draws it. */
export interface PanelRow {
  name: string;
  label: string;
  /** The harness does not list the row's flag in its help. */
  hidden: boolean;
  field: PanelField;
}

/** One subgroup of a group. `name` is "" for the rows that have none, which come last. */
export interface PanelSub {
  name: string;
  rows: PanelRow[];
  summary: string;
}

export interface PanelGroup {
  key: string;
  title: string;
  subs: PanelSub[];
  summary: string;
}

function rowOf(field: PanelField): PanelRow {
  return { name: field.name, label: rowLabel(field), hidden: field.hidden === true, field };
}

/**
 * The panel's groups for the form's fields and the values set now, in the catalog's order,
 * each with its subgroups in order and the rows with no subgroup last. A group none of
 * whose rows is drawn on this install, or for this harness, is left out.
 */
export function panelGroups(fields: readonly PanelField[], set: Readonly<Record<string, FormValue>>): PanelGroup[] {
  const drawn = panelFields(fields);
  const out: PanelGroup[] = [];
  for (const shape of PANEL_GROUPS) {
    const mine = drawn.filter((field) => field.group === shape.key);
    if (mine.length === 0) continue;
    const names = [...shape.subs];
    for (const field of mine) {
      const sub = said(field.sub);
      if (sub !== "" && !names.includes(sub)) names.push(sub);
    }
    const subs: PanelSub[] = [];
    for (const name of [...names, ""]) {
      const rows = mine.filter((field) => said(field.sub) === name);
      if (rows.length > 0) subs.push({ name, rows: rows.map(rowOf), summary: summaryOf(rows, set) });
    }
    out.push({ key: shape.key, title: shape.title, subs, summary: summaryOf(mine, set) });
  }
  return out;
}

// --- the find field ------------------------------------------------------------------------

/** One row the find field found, with where it sits: `Access › Mode`. */
export interface Found extends PanelRow {
  where: string;
}

/**
 * The rows whose label, name or help text holds `query`, whatever its case, in the panel's
 * order. An empty query finds nothing; a row the panel does not draw is never found.
 */
export function findRows(fields: readonly PanelField[], query: string): Found[] {
  const asked = query.trim().toLowerCase();
  if (asked === "") return [];
  const out: Found[] = [];
  for (const group of panelGroups(fields, {})) {
    for (const sub of group.subs) {
      for (const row of sub.rows) {
        const texts = [row.label, row.name, said(row.field.doc)];
        if (!texts.some((text) => text.toLowerCase().includes(asked))) continue;
        out.push({ ...row, where: sub.name === "" ? group.title : `${group.title} › ${sub.name}` });
      }
    }
  }
  return out;
}

// --- drawing and changing a row ------------------------------------------------------------

/** Whether a row offers its values as the only choices: a text row with values, on an
 * install where the page server says no other value can be typed. */
export function picksOnly(field: PanelField): boolean {
  return field.type === "string" && field.typed === false && !isListField(field)
    && field.optional_value !== true && Array.isArray(field.values) && field.values.length > 0;
}

/**
 * The descriptor the form renderer draws for a row holding `value`: the label in front of
 * the control, every row drawn in place, and the value shown as the control spells it. A
 * row that takes only its offered values is drawn as a pick of them, with a blank choice
 * for none; one that takes a typed value too names the offered ones under the control.
 */
export function drawnField(field: PanelField, value: FormValue): FieldDescriptor {
  const out: FieldDescriptor = { ...field, flag: rowLabel(field), hidden: false, posture: false };
  if (isListField(field)) {
    out.value = null;
    out.value_items = Array.isArray(value) ? [...value] : [];
  } else if (field.optional_value === true) {
    out.value = value === null ? null : value === "" ? true : value;
  } else if (field.type === "bool") {
    out.value = value === "true" ? "true" : "false";
  } else {
    out.value = typeof value === "string" ? value : leaveOutChoice(field) ?? "";
  }
  const offered = field.type === "string" && Array.isArray(field.values) ? field.values.filter((item) => item !== "") : [];
  if (picksOnly(field)) {
    out.type = "enum";
    out.values = ["", ...offered];
  } else if (offered.length > 0 && !isListField(field) && field.optional_value !== true) {
    const line = `Offered: ${offered.join(", ")}`;
    out.note = said(field.note) !== "" ? `${field.note}; ${line}` : line;
  }
  return out;
}

/**
 * A row's value after one change the form renderer reports: a new value, a list item added
 * or removed, or a switch turned off (`reset`). An emptied text is the row's empty state;
 * an item a list already holds is not added again.
 */
export function changedValue(field: PanelField, now: FormValue, change: FormChange): FormValue {
  if (isListField(field)) {
    const items = Array.isArray(now) ? now : [];
    if (typeof change.add === "string") return items.includes(change.add) ? [...items] : [...items, change.add];
    if (typeof change.remove === "string") {
      const at = items.indexOf(change.remove);
      return at < 0 ? [...items] : [...items.slice(0, at), ...items.slice(at + 1)];
    }
    return change.reset === true ? [] : [...items];
  }
  if (change.reset === true) return field.optional_value === true ? null : shownValue(field);
  if (typeof change.value !== "string") return now;
  if (field.optional_value === true || field.type === "bool") return change.value;
  return change.value === "" ? null : change.value;
}

/**
 * The values set in the panel after row `field` takes `value`: a value equal to what the
 * form is filled with is no longer set, so the panel holds only what the user changed.
 * Where the row is one of several of which a launch takes one (a text and its file), a
 * value given to it empties the others among `fields` that hold one, so the panel shows
 * what will launch.
 */
export function withOption(options: Readonly<Record<string, FormValue>>, field: PanelField, value: FormValue,
                           fields: readonly PanelField[] = []): Record<string, FormValue> {
  const out = { ...options };
  const put = (row: PanelField, next: FormValue): void => {
    if (same(next, shownValue(row))) delete out[row.name];
    else out[row.name] = next;
  };
  put(field, value);
  if (value === null || value === "") return out;
  for (const name of GROUPS.find((group) => group.includes(field.name)) ?? []) {
    const other = fields.find((row) => row.name === name && row.name !== field.name && inPanel(row));
    if (other !== undefined && valueNow(other, out) !== null) put(other, null);
  }
  return out;
}

/** The values set in the panel that the form's fields still have a row for, as after
 * another harness's form is read. */
export function keptOptions(options: Readonly<Record<string, FormValue>>,
                            fields: readonly PanelField[]): Record<string, FormValue> {
  const names = new Set(panelFields(fields).map((field) => field.name));
  return Object.fromEntries(Object.entries(options).filter(([name]) => names.has(name)));
}

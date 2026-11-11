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
// A row of the harness's is drawn only where its flag is passed: when its command modes hold
// the mode of the place the button launches in, as `/api/form` names it for each place. A
// row of the launcher's own is drawn in every mode. Where no launch is possible in that
// place (the mode is null) no row of the harness's is drawn.
//
// A value set in the panel is kept by the row's name and goes into the launch request with
// the other rows the piece sets. Each row is drawn by the shell's one form renderer, from
// the descriptor `drawnField` gives it.
//
// A row whose value is not the one the form was filled with is changed: the line under the
// box names each by its label and the word that opens the panel counts them. A value that
// can do harm is marked wherever it is drawn, however it got there, and is named first.
//
// Tools > Built-in is drawn as one line per built-in tool over the three rows that hold
// tools: the session's whole set of them, those it may use without asking and those it may
// not use. A line's `allow` and `deny` change that tool's own entry in the last two and
// nothing else: every other entry, a tool the page does not name included, stays in its
// place, and no line ever changes the whole set.
//
// Every string here goes into the page as text.

import type { FieldDescriptor, FormChange, FormValue } from "/m/ui/client.js";

import { GROUPS, isListField, leaveOutChoice, same, shownValue } from "./panel.ts";
import type { FormMode } from "./panel.ts";
import { BUILT_IN_TOOLS, FIRST_TURN, launchedValue, offPosture } from "./prompt-model.ts";

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

/** The command mode a launch in one place composes: a mode's name, null where no launch is
 * possible there, or undefined while it is not known, which every row is drawn for. */
export type Mode = string | null | undefined;

/** The mode `/api/form` names for a launch in `target`; undefined where it names none. */
export function modeOf(modes: readonly FormMode[], target: string): Mode {
  return modes.find((item) => item.target === target)?.mode;
}

/** Whether a row's flag is passed in `mode`: always for a row of the launcher's own (no
 * modes), never in a mode that is null, and in any mode while it is not known. */
export function runsIn(field: PanelField, mode: Mode): boolean {
  if (mode === undefined || !Array.isArray(field.modes) || field.modes.length === 0) return true;
  return mode !== null && field.modes.includes(mode);
}

/** Whether the panel draws a row: it is in one of the panel's groups, the page server says
 * it is shown here and takes input, its flag is passed in `mode`, and it is neither one of
 * the first turn's rows nor one of the place's. */
export function inPanel(field: PanelField, mode?: Mode): boolean {
  return PANEL_GROUPS.some((group) => group.key === field.group) && field.shown !== false
    && field.needs_absent !== true && field.owned !== true && runsIn(field, mode)
    && !(FIRST_TURN as readonly string[]).includes(field.name) && !PLACE_ROWS.includes(field.name);
}

/** The rows the panel draws in `mode`, in the fields' order. */
export function panelFields(fields: readonly PanelField[], mode?: Mode): PanelField[] {
  return fields.filter((field) => inPanel(field, mode));
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

/** Whether a value launches something other than the row's shipped default. */
export function differs(field: PanelField, value: FormValue): boolean {
  return !same(launchedValue(field, value), launchedValue(field, shippedValue(field)));
}

/** A row's value in a few words, for a summary: a pick is its word alone, any other row
 * leads with its label. */
export function valueWords(field: PanelField, value: FormValue): string {
  const label = rowLabel(field);
  if (Array.isArray(value)) return `${label} ${value.length === 0 ? "none" : value.length}`;
  if (field.optional_value === true) return `${label} ${value === null ? "off" : value === "" ? "on" : value}`;
  const now = launchedValue(field, value);
  if (field.type === "bool") return `${label} ${now === "true" ? "on" : "off"}`;
  if (now === null) return `${label} empty`;
  return field.type === "enum" ? String(now) : `${label} ${now}`;
}

/** Whether a value is one of the row's values that can do harm. */
export function isDanger(field: PanelField, value: FormValue): boolean {
  return typeof value === "string" && Array.isArray(field.danger) && field.danger.includes(value);
}

/** The rows among `fields` whose value in `values` can do harm, by name: what a list of an
 * earlier launch's values, or of a preset's, marks. A row `values` lacks is not named. */
export function dangerNames(fields: readonly PanelField[], values: Readonly<Record<string, FormValue>>): string[] {
  return fields.filter((field) => Object.hasOwn(values, field.name) && isDanger(field, values[field.name]))
    .map((field) => field.name);
}

/** One value in a summary, marked when it can do harm. */
export interface Word {
  text: string;
  danger: boolean;
}

/** What a closed group or subgroup says of its rows: each value that differs from the
 * shipped default, with the ones that can do harm marked; none when every row is at its
 * default. */
export function summaryWords(fields: readonly PanelField[], set: Readonly<Record<string, FormValue>>): Word[] {
  return fields.filter((field) => differs(field, valueNow(field, set)))
    .map((field) => ({ text: valueWords(field, valueNow(field, set)), danger: isDanger(field, valueNow(field, set)) }));
}

/** The summary as one line: its words, else `defaults`. */
export function summaryOf(fields: readonly PanelField[], set: Readonly<Record<string, FormValue>>): string {
  const words = summaryWords(fields, set);
  return words.length === 0 ? DEFAULTS : words.map((word) => word.text).join(" · ");
}

// --- the groups ----------------------------------------------------------------------------

/** One row as the panel draws it. */
export interface PanelRow {
  name: string;
  label: string;
  /** The harness does not list the row's flag in its help. */
  hidden: boolean;
  /** The row's value now can do harm. */
  danger: boolean;
  field: PanelField;
}

/** One subgroup of a group. `name` is "" for the rows that have none, which come last. */
export interface PanelSub {
  name: string;
  rows: PanelRow[];
  summary: string;
  /** The summary's values, each marked when it can do harm; none at the defaults. */
  words: Word[];
}

export interface PanelGroup {
  key: string;
  title: string;
  subs: PanelSub[];
  summary: string;
  words: Word[];
}

function rowOf(field: PanelField, set: Readonly<Record<string, FormValue>>): PanelRow {
  return { name: field.name, label: rowLabel(field), hidden: field.hidden === true,
           danger: isDanger(field, valueNow(field, set)), field };
}

/**
 * The panel's groups for the form's fields and the values set now, in the catalog's order,
 * each with its subgroups in order and the rows with no subgroup last. A group none of
 * whose rows is drawn on this install, for this harness, or in `mode`, is left out.
 */
export function panelGroups(fields: readonly PanelField[], set: Readonly<Record<string, FormValue>>,
                            mode?: Mode): PanelGroup[] {
  const drawn = panelFields(fields, mode);
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
      if (rows.length === 0) continue;
      subs.push({ name, rows: rows.map((field) => rowOf(field, set)), summary: summaryOf(rows, set),
                  words: summaryWords(rows, set) });
    }
    out.push({ key: shape.key, title: shape.title, subs, summary: summaryOf(mine, set), words: summaryWords(mine, set) });
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
 * order, each marked by its value in `set`. An empty query finds nothing; a row the panel
 * does not draw, on this install or in `mode`, is never found.
 */
export function findRows(fields: readonly PanelField[], query: string,
                         set: Readonly<Record<string, FormValue>> = {}, mode?: Mode): Found[] {
  const asked = query.trim().toLowerCase();
  if (asked === "") return [];
  const out: Found[] = [];
  for (const group of panelGroups(fields, set, mode)) {
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
  if (field.name === TOOL_SET && Array.isArray(out.value_items) && out.value_items.length === 0) {
    out.note = said(field.note) !== "" ? `${EVERY_TOOL}; ${field.note}` : EVERY_TOOL;
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

/** The row that holds the session's whole set of built-in tools; empty, it has every one. */
export const TOOL_SET = "tools";

/** What the set of built-in tools reads while it is empty. */
export const EVERY_TOOL = "every built-in tool";

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

/** The values set in the panel whose rows the panel still draws, as after another
 * harness's form is read or the button is set to launch in a place of another `mode`. */
export function keptOptions(options: Readonly<Record<string, FormValue>>,
                            fields: readonly PanelField[], mode?: Mode): Record<string, FormValue> {
  const names = new Set(panelFields(fields, mode).map((field) => field.name));
  return Object.fromEntries(Object.entries(options).filter(([name]) => names.has(name)));
}

// --- what was changed ----------------------------------------------------------------------

/** The word that opens the panel. */
export const OPTIONS = "Options";

/** The word in front of the line under the box that names the changed rows. */
export const CHANGED = "Changed";

/** The control on that line that puts every changed row back. */
export const RESET = "Reset";

/** One changed row as the line under the box names it. */
export interface Change {
  name: string;
  label: string;
  /** Its value now can do harm. */
  danger: boolean;
}

/**
 * The rows the user changed: each row of the panel whose value in `options` is not the one
 * the form is filled with. The form's fill is the baseline, whatever gave it: a value the
 * user's own defaults or a preset set is not a change, and neither is one carried over from
 * an earlier launch, which is not in `options` until the user edits it. The rows whose
 * value can do harm come first, then the rest, each in the panel's order.
 */
export function changes(fields: readonly PanelField[], options: Readonly<Record<string, FormValue>>): Change[] {
  const out: Change[] = [];
  for (const group of panelGroups(fields, options)) {
    for (const sub of group.subs) {
      for (const row of sub.rows) {
        if (!Object.hasOwn(options, row.name) || same(options[row.name], shownValue(row.field))) continue;
        out.push({ name: row.name, label: row.label, danger: row.danger });
      }
    }
  }
  return [...out.filter((change) => change.danger), ...out.filter((change) => !change.danger)];
}

/** The word that opens the panel, with the number of changed rows once there is one. */
export function optionsWord(count: number): string {
  return count > 0 ? `${OPTIONS} · ${count}` : OPTIONS;
}

// --- a value that can do harm, picked by hand -----------------------------------------------

/** A value that can do harm, picked in a row's control and not turned on yet. */
export interface Pending {
  name: string;
  value: string;
}

/** Whether a row's change to `next` waits for a second act: `next` can do harm and the
 * row's value now is another. A value that arrives any other way is never asked for. */
export function asksFirst(field: PanelField, now: FormValue, next: FormValue): boolean {
  return isDanger(field, next) && !same(now, next);
}

/** The values set in the panel that can do harm, by row. Each was turned on by hand, since
 * the panel holds no value the form is filled with, and holds for one launch only. */
export function turnedOn(fields: readonly PanelField[],
                         options: Readonly<Record<string, FormValue>>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of fields) {
    const value = Object.hasOwn(options, field.name) ? options[field.name] : null;
    if (isDanger(field, value)) out[field.name] = value as string;
  }
  return out;
}

/** The values set in the panel once a launch that had `used` turned on has started or had
 * its command copied: a row still at the value it launched with is no longer set, so it is
 * back at what the form is filled with and a pick of the value asks again. A row changed
 * since the press stays. */
export function afterLaunch(options: Readonly<Record<string, FormValue>>,
                             used: Readonly<Record<string, string>>): Record<string, FormValue> {
  return Object.fromEntries(Object.entries(options).filter(([name, value]) => !(Object.hasOwn(used, name)
    && same(value, used[name]))));
}

// --- off the usual permissions --------------------------------------------------------------

/** What gave a permission row its value: the user in the panel, an earlier launch whose
 * value was carried over, or the applied preset. */
export type OffSource = "typed" | "filled" | "preset";

/** A permission row the launch would start off the user's usual value. */
export interface OffRow {
  name: string;
  label: string;
  source: OffSource;
}

/**
 * The permission rows the launch would start off the user's usual value, each with what
 * gave it its value, which decides where the page names it before the button is pressed:
 * a row set in the panel (`options`) is on the line of changed rows, one carried over from
 * an earlier launch (`filled`) is on that launch's item, and any other got its value from
 * the form's own fill, which only a preset moves off the usual. `base` is the form read
 * with no preset. The launch request says it is off the usual exactly when this names a row.
 */
export function offRows(fields: readonly PanelField[], base: readonly PanelField[],
                        options: Readonly<Record<string, FormValue>>,
                        filled: Readonly<Record<string, FormValue>> = {}): OffRow[] {
  const byName = new Map(fields.map((field) => [field.name, field]));
  return offPosture(fields, base, { ...filled, ...options }).map((name) => ({
    name, label: rowLabel(byName.get(name) as PanelField),
    source: Object.hasOwn(options, name) ? "typed" : Object.hasOwn(filled, name) ? "filled" : "preset",
  }));
}

/** The line under the box for the rows a preset sets off the usual: `Preset planner sets
 * Permission mode, Allowed tools off your defaults`; null when it sets none. */
export function presetOffLine(preset: string, rows: readonly OffRow[]): string | null {
  const labels = rows.filter((row) => row.source === "preset").map((row) => row.label);
  return labels.length === 0 ? null : `Preset ${preset} sets ${labels.join(", ")} off your defaults`;
}

// --- Tools > Built-in ----------------------------------------------------------------------

/** The rows of the tools the session may use without asking, and may not use. */
export const ALLOWED = "allowed_tools";
export const DENIED = "disallowed_tools";
export type RuleRow = typeof ALLOWED | typeof DENIED;

/** The items of the three rows the Tools view is over, by row name. */
export interface ToolLists {
  tools: string[];
  allowed_tools: string[];
  disallowed_tools: string[];
}

/** The two words on a tool's line. */
export type ToolWord = "allow" | "deny";

const ROW_OF: Readonly<Record<ToolWord, RuleRow>> = { allow: ALLOWED, deny: DENIED };

/** What a line says whose tool the launch's set of built-in tools leaves out. */
export const NOT_IN_SET = "not in this launch's tools";

/** The heading over the rules no line draws. */
export const OTHER_RULES = "Other rules";

/** Whether a subgroup is drawn as the Tools view: Tools > Built-in, with both rows of rules. */
export function isToolsView(group: string, sub: PanelSub): boolean {
  return group === "tools" && sub.name === "Built-in"
    && [ALLOWED, DENIED].every((name) => sub.rows.some((row) => row.name === name));
}

/** The three rows' items now: the value set in the panel, else the form's fill. */
export function toolLists(fields: readonly PanelField[], set: Readonly<Record<string, FormValue>>): ToolLists {
  const items = (name: string): string[] => {
    const field = fields.find((row) => row.name === name);
    const value = field === undefined ? null : valueNow(field, set);
    return Array.isArray(value) ? [...value] : [];
  };
  return { tools: items(TOOL_SET), allowed_tools: items(ALLOWED), disallowed_tools: items(DENIED) };
}

/** The built-in tool a scoped rule is for, `Bash` for `Bash(git status)`; null for any
 * other entry, a bare name included. */
export function scopedTool(entry: string): string | null {
  const found = /^([A-Za-z]+)\(.*\)$/s.exec(entry);
  return found !== null && (BUILT_IN_TOOLS as readonly string[]).includes(found[1]) ? found[1] : null;
}

/** The word boxed on a tool's line: `deny` while its bare name is a denied entry, which
 * wins over an allowed one, else `allow` while it is an allowed entry, else neither. */
export function boxedWord(from: ToolLists, tool: string): ToolWord | null {
  if (from.disallowed_tools.includes(tool)) return "deny";
  return from.allowed_tools.includes(tool) ? "allow" : null;
}

function copied(from: ToolLists): ToolLists {
  return { tools: [...from.tools], allowed_tools: [...from.allowed_tools], disallowed_tools: [...from.disallowed_tools] };
}

/**
 * The three rows after `word` is pressed on `tool`'s line. The boxed word takes the bare
 * name out of its row; the other puts it last in its own row, where it is not already, and
 * takes it out of the other row. Every other entry stays where it was, and the set of
 * built-in tools is never changed.
 */
export function pressTool(from: ToolLists, tool: string, word: ToolWord): ToolLists {
  const out = copied(from);
  const row = ROW_OF[word];
  if (boxedWord(from, tool) === word) {
    out[row] = from[row].filter((entry) => entry !== tool);
    return out;
  }
  const other: RuleRow = row === ALLOWED ? DENIED : ALLOWED;
  if (!from[row].includes(tool)) out[row] = [...from[row], tool];
  out[other] = from[other].filter((entry) => entry !== tool);
  return out;
}

/** One entry of a row of rules, with the row it is in and its place there. */
export interface ToolRule {
  row: RuleRow;
  entry: string;
  at: number;
}

/** One built-in tool's line. */
export interface ToolLine {
  tool: string;
  boxed: ToolWord | null;
  /** Its scoped rules, the allowed ones first, each row's in its order. */
  rules: ToolRule[];
  /** The launch's set of built-in tools holds names and not this one. */
  dim: boolean;
}

function rulesOf(from: ToolLists): ToolRule[] {
  return ([ALLOWED, DENIED] as const).flatMap((row) => from[row].map((entry, at) => ({ row, entry, at })));
}

/** A line for each of the built-in tools the page names, in its order. */
export function toolLines(from: ToolLists): ToolLine[] {
  const rules = rulesOf(from);
  return BUILT_IN_TOOLS.map((tool) => ({
    tool, boxed: boxedWord(from, tool), rules: rules.filter((rule) => scopedTool(rule.entry) === tool),
    dim: from.tools.length > 0 && !from.tools.includes(tool),
  }));
}

/** Every entry of the two rows of rules no line draws: a tool the page does not name, an
 * MCP tool, anything that is neither a named tool nor one's scoped rule. */
export function otherRules(from: ToolLists): ToolRule[] {
  return rulesOf(from).filter((rule) => scopedTool(rule.entry) === null
    && !(BUILT_IN_TOOLS as readonly string[]).includes(rule.entry));
}

/** The three rows with one rule taken out of its row; the rest stay in their places. */
export function withoutRule(from: ToolLists, rule: ToolRule): ToolLists {
  const out = copied(from);
  if (out[rule.row][rule.at] === rule.entry) out[rule.row].splice(rule.at, 1);
  return out;
}

/** The count of a line's scoped rules: `1 rule`, `2 rules`. */
export function rulesWord(count: number): string {
  return `${count} ${count === 1 ? "rule" : "rules"}`;
}

/** The values set in the panel once the two rows of rules hold what `next` gives them,
 * each set as any row is; a row whose items are those it holds `now` is not written, and
 * the set of built-in tools never is. */
export function withTools(options: Readonly<Record<string, FormValue>>, fields: readonly PanelField[],
                          next: ToolLists, now: ToolLists = toolLists(fields, options)): Record<string, FormValue> {
  let out: Record<string, FormValue> = { ...options };
  for (const name of [ALLOWED, DENIED] as const) {
    const field = fields.find((row) => row.name === name);
    if (field !== undefined && !same(now[name], next[name])) out = withOption(out, field, [...next[name]], fields);
  }
  return out;
}

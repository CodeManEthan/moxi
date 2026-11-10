// What <moxi-ui-form> draws for each field descriptor, and what it sends, with no DOM: the
// element in form.ts renders these plans. A descriptor is `moxi.config.Key`'s shape with
// its value and source (the settings view's), or a launcher catalog row.
//
// Every value travels as the command line spells it: `true`/`false`, a number, the plain
// string, or one TOML value for a table. A list travels item by item.

import type { FieldDescriptor, FieldEntry, FormChange, FormValue } from "./client.ts";

/** The types a field descriptor may have; anything else is drawn as text. */
export const TYPES = ["bool", "int", "float", "string", "path", "enum", "list", "table"] as const;

/** The line under a field or entry the shell refuses to write for this run. */
export const LOCKED = "Locked for this run of the shell.";

/** One toggle of a switch table: an entry, written as `<table key>.<entry>`. */
export interface Toggle {
  key: string;
  label: string;
  checked: boolean;
  /** Lines under the toggle: its mark, and "not set" while no file names it. */
  notes: string[];
  /** Whether "reset" is offered: the file names the entry. */
  resettable: boolean;
  /** Why the toggle is shown off, or null when it takes input. */
  off: string | null;
}

export type Control =
  | { kind: "checkbox"; checked: boolean }
  | { kind: "number"; step: "1" | "any"; text: string }
  | { kind: "text"; text: string; path: boolean }
  | { kind: "select"; options: string[]; selected: string }
  | { kind: "list"; items: string[]; itemType: string }
  | { kind: "toggles"; toggles: Toggle[]; unavailable: string | null }
  | { kind: "toml"; text: string }
  | { kind: "optional"; on: boolean; text: string };

/** One field as the form draws it. */
export interface Plan {
  name: string;
  /** The text in front of the control: the key's name, or a catalog row's flag. */
  label: string;
  /** The doc, as help text. */
  help: string;
  control: Control;
  /** Lines under the control: the mark, where the value came from, the default, a note. */
  notes: string[];
  /** Why the field is shown off, or null when it takes input. */
  off: string | null;
  /** Shown under "more". */
  hidden: boolean;
  /** Part of the permission posture. */
  posture: boolean;
  /** Whether "reset" (the key's `unset`) is offered: the value comes from the file. */
  resettable: boolean;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** The field's value as a string, falling back to its default, then "". */
function scalar(field: FieldDescriptor): string {
  if (typeof field.value === "string") return field.value;
  if (field.value == null && typeof field.default === "string") return field.default;
  return "";
}

/** A list field's items: the settings view's `value_items`, else a catalog row's list. */
export function items(field: FieldDescriptor): string[] {
  if (Array.isArray(field.value_items)) return strings(field.value_items);
  if (Array.isArray(field.value)) return strings(field.value);
  return [];
}

/** A list control: the `list` type, or a catalog row that may be given more than once. */
export function isList(field: FieldDescriptor): boolean {
  return field.type === "list" || field.repeatable === true;
}

function toggles(field: FieldDescriptor): Toggle[] {
  const entries: FieldEntry[] = Array.isArray(field.entries) ? field.entries : [];
  return entries.filter((entry) => typeof entry?.key === "string").map((entry) => {
    const notes: string[] = [];
    if (text(entry.mark)) notes.push(entry.mark as string);
    if (entry.source === "default") notes.push("not set");
    else if (entry.source === "env") notes.push("set by the shell's environment");
    return {
      key: entry.key,
      label: text(entry.name) || entry.key,
      checked: entry.value === true,
      notes,
      resettable: entry.source === "file",
      off: entry.locked === true ? LOCKED : null,
    };
  });
}

function control(field: FieldDescriptor): Control {
  if (field.switch_table === true) {
    return { kind: "toggles", toggles: toggles(field),
             unavailable: field.declarations === "unavailable" ? text(field.reason) || "The module's declarations could not be read." : null };
  }
  if (field.optional_value === true) {
    const value = field.value;
    return { kind: "optional", on: typeof value === "string" || value === true,
             text: typeof value === "string" ? value : "" };
  }
  if (isList(field)) {
    return { kind: "list", items: items(field),
             itemType: field.type === "list" ? text(field.items) || "string" : field.type };
  }
  switch (field.type) {
    case "bool":
      return { kind: "checkbox", checked: scalar(field) === "true" };
    case "int":
      return { kind: "number", step: "1", text: scalar(field) };
    case "float":
      return { kind: "number", step: "any", text: scalar(field) };
    case "enum": {
      const options = strings(field.values);
      const selected = scalar(field);
      return { kind: "select", options: options.includes(selected) || selected === "" ? options : [selected, ...options], selected };
    }
    case "table":
      return { kind: "toml", text: scalar(field) };
    default:
      return { kind: "text", text: scalar(field), path: field.type === "path" };
  }
}

const SOURCES: Record<string, string> = {
  default: "the default",
  file: "set in the settings file",
  env: "set by the shell's environment",
};

/**
 * The plan for each field the form offers, in order. A catalog row the launcher owns is
 * not offered. `installed` says whether a module is installed, for a row that `requires`
 * one.
 */
export function plans(fields: readonly FieldDescriptor[], installed: (module: string) => boolean): Plan[] {
  const out: Plan[] = [];
  for (const field of fields) {
    if (typeof field?.name !== "string" || field.owned === true) continue;
    const notes: string[] = [];
    if (text(field.mark)) notes.push(field.mark as string);
    const switchTable = field.switch_table === true;
    if (!switchTable && typeof field.source === "string" && SOURCES[field.source]) {
      let line = SOURCES[field.source];
      if (field.source !== "default" && typeof field.default === "string" && field.default !== "") {
        line += `; the default is ${field.default}`;
      }
      notes.push(line);
    }
    let off: string | null = null;
    if (field.locked === true) {
      off = LOCKED;
      if (text(field.note)) notes.push(field.note as string);
    } else if (field.needs_absent === true) {
      off = text(field.note) || `works only with the ${text(field.needs)} module, which is not installed`;
    } else if (typeof field.requires === "string" && field.requires !== "" && !installed(field.requires)) {
      off = `works only with the ${field.requires} module, which is not installed`;
    } else if (text(field.note)) {
      notes.push(field.note as string);
    }
    out.push({
      name: field.name,
      label: text(field.flag) || field.name,
      help: text(field.doc),
      control: control(field),
      notes,
      off,
      hidden: field.hidden === true,
      posture: field.posture === true,
      resettable: !switchTable && field.source === "file",
    });
  }
  return out;
}

/** An absolute path on any of the three systems: `/x`, `C:\x` or `\\host\x`. */
const ABSOLUTE = /^(\/|[A-Za-z]:[\\/]|\\\\)/;

/**
 * Why `value` is not a value of `type`, or null when it is. The server checks again; this
 * catches a typing slip before anything is sent. `values` are an enum's words.
 */
export function problem(type: string, value: string, values: readonly string[] = []): string | null {
  switch (type) {
    case "bool":
      return value === "true" || value === "false" ? null : "is true or false";
    case "int":
      return /^[+-]?[0-9]+$/.test(value.trim()) ? null : "is a whole number";
    case "float": {
      const trimmed = value.trim();
      return trimmed !== "" && Number.isFinite(Number(trimmed)) ? null : "is a number";
    }
    case "path":
      return ABSOLUTE.test(value) ? null : "is an absolute path";
    case "enum":
      return values.includes(value) ? null : `is one of ${values.join(", ")}`;
    default:
      return null;
  }
}

/** The check for one list item or a scalar field's text, with the field's name. */
export function check(field: FieldDescriptor, value: string, item = false): string | null {
  const type = item ? (field.type === "list" ? text(field.items) || "string" : field.type) : field.type;
  const why = problem(type, value, strings(field.values));
  return why === null ? null : `${field.name} ${why}`;
}

/** The `moxi-change` detail for a new scalar value of a field, or a toggle's entry. */
export function changeOf(name: string, value: string): FormChange {
  return { name, value };
}

/** The `moxi-change` detail for an item added to or removed from a list. */
export function itemChange(name: string, how: "add" | "remove", item: string): FormChange {
  return how === "add" ? { name, add: item } : { name, remove: item };
}

/** The `moxi-change` detail for "reset": the key (or entry) reads its default again. */
export function resetOf(name: string): FormChange {
  return { name, reset: true };
}

/** What one field holds now, as the user left it. */
export type Held =
  | { kind: "scalar"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "optional"; on: boolean; text: string }
  | { kind: "toggles"; on: Record<string, boolean> };

/** What a plan's control holds before the user touches it. */
export function heldOf(plan: Plan): Held {
  const c = plan.control;
  switch (c.kind) {
    case "checkbox":
      return { kind: "scalar", text: c.checked ? "true" : "false" };
    case "select":
      return { kind: "scalar", text: c.selected };
    case "list":
      return { kind: "list", items: [...c.items] };
    case "optional":
      return { kind: "optional", on: c.on, text: c.text };
    case "toggles":
      return { kind: "toggles",
               on: Object.fromEntries(c.toggles.filter((t) => t.off === null).map((t) => [t.key, t.checked])) };
    default:
      return { kind: "scalar", text: c.text };
  }
}

/**
 * The `moxi-submit` values and the problems that stop it. Every offered field that is
 * not off gives a value: a scalar its text (null when empty), a list its items, a
 * switch with an optional value null when off and its text (perhaps "") when on, and a
 * switch table each entry that is not off as `<table key>.<entry>`: `true` or `false`.
 */
export function submitted(fields: readonly FieldDescriptor[], held: ReadonlyMap<string, Held>,
                          installed: (module: string) => boolean):
    { values: Record<string, FormValue>; problems: Map<string, string> } {
  const values: Record<string, FormValue> = {};
  const problems = new Map<string, string>();
  const byName = new Map(fields.map((field) => [field.name, field]));
  for (const plan of plans(fields, installed)) {
    if (plan.off !== null) continue;
    const field = byName.get(plan.name) as FieldDescriptor;
    const now = held.get(plan.name) ?? heldOf(plan);
    switch (now.kind) {
      case "list": {
        values[plan.name] = [...now.items];
        const bad = now.items.map((item) => check(field, item, true)).find((why) => why !== null);
        if (bad) problems.set(plan.name, bad);
        break;
      }
      case "optional":
        values[plan.name] = now.on ? now.text : null;
        break;
      case "toggles":
        for (const [key, on] of Object.entries(now.on)) values[key] = on ? "true" : "false";
        break;
      default: {
        if (now.text === "") {
          values[plan.name] = null;
          break;
        }
        values[plan.name] = now.text;
        const why = field.type === "table" ? null : check(field, now.text);
        if (why) problems.set(plan.name, why);
      }
    }
  }
  return { values, problems };
}

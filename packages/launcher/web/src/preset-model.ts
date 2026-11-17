// What the Preset item on the line above Home's box offers and applies, worked out with no
// DOM so the Node tests can drive it. prompt.ts draws it.
//
// A launch from Home starts from at most one preset: a seat or a project preset. The
// applied one's values are the form's fill, so they are the baseline the Changed line and
// the count measure from; the permission rows it sets off the user's usual values are named
// on a line of their own under the box.
//
// Which preset is applied follows the line above the box. A project picked there, or the
// project the line opens on, applies that project's preset: the one named like the
// project, else the first by name whose place is that project, else none. While a seat is
// applied a picked place is only the place: the seat stays. A preset picked from the menu
// moves the line to the place it names, when it names one the line can show. Without
// projects a project preset names no place.
//
// Every string here goes into the page as text.

import type { FormValue } from "/m/ui/client.js";

import { NO_PRESET } from "./panel.ts";
import type { Choice, Preset } from "./panel.ts";
import { isDanger, rowLabel, valueNow } from "./options-model.ts";
import type { OffRow, PanelField, Word } from "./options-model.ts";
import { tilde, typedFolder } from "./prompt-model.ts";
import type { Pick } from "./prompt-model.ts";

/** The word in front of the item on the line above the box. */
export const PRESET = "Preset";

/** What the item says with no preset applied, drawn dim. */
export const NONE = "none";

/** The menu's first row, which takes the applied preset off. */
export const NO_PRESET_ROW = "No preset";

/** The menu's sections, in order: the kind of preset each lists, and its heading. */
export const PRESET_SECTIONS: readonly { kind: "project" | "seat"; title: string }[] = [
  { kind: "project", title: "Project presets" },
  { kind: "seat", title: "Agent presets" },
];

/** What a menu row says of a preset whose place is the project on the line now. */
export const THIS_PROJECT = "this project";

/** The rows of a preset's values the menu row names, in this order. */
export const NAMED_ROWS = ["harness", "model", "effort"] as const;

/** Whether two choices are the same preset, or both none. */
export function sameChoice(a: Choice, b: Choice): boolean {
  return a.kind === b.kind && a.name === b.name;
}

/** A preset as the choice that applies it. */
export function choiceFor(preset: Preset): Choice {
  return preset.kind === "seat" || preset.kind === "project" ? { kind: preset.kind, name: preset.name } : NO_PRESET;
}

/** One value a preset sets, as text; null when it sets none or it is not one word. */
function presetValue(preset: Preset, name: string): string | null {
  const found = preset.values.find((row) => row.name === name);
  return found !== undefined && typeof found.value === "string" && found.value !== "" ? found.value : null;
}

/** Whether a seat's `home` is a path: it holds `/` or `\`, or starts with `~` or `.`; any
 * other is a project's key. */
export function homeIsPath(home: string): boolean {
  return /[/\\]/.test(home) || home.startsWith("~") || home.startsWith(".");
}

/**
 * The place a preset puts the launch, as the line above the box would pick it: a seat's
 * `home`, else the preset's `project`, else its `cwd`. A project's key is a place only with
 * projects, and a path only when it is absolute or starts with `~`. Without projects a
 * project preset names no place. Null when there is none the line can show.
 */
export function presetPlace(preset: Preset, projects: boolean): Pick | null {
  if (preset.kind === "project" && !projects) return null;
  const home = preset.kind === "seat" ? presetValue(preset, "home") : null;
  if (home !== null && !homeIsPath(home)) return projects ? { kind: "project", key: home } : null;
  const project = home === null ? presetValue(preset, "project") : null;
  if (project !== null) return projects ? { kind: "project", key: project } : null;
  const path = typedFolder(home ?? presetValue(preset, "cwd") ?? "");
  return path === null ? null : { kind: "folder", path };
}

function byName(a: Preset, b: Preset): number {
  return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
}

/** The project preset a pick of the project `key` applies: the one named like the key,
 * else the first by name whose place is the project; null when none is. */
export function projectPreset(presets: readonly Preset[], key: string): Preset | null {
  const mine = presets.filter((preset) => preset.kind === "project" && presetValue(preset, "project") === key)
    .sort(byName);
  return mine.find((preset) => preset.name === key) ?? mine[0] ?? null;
}

/** The preset applied once `pick` is picked on the line above the box, `applied` being the
 * one applied before. A seat stays. A project applies its own preset, or none when it has
 * none. A folder leaves the preset as it was. */
export function afterPick(applied: Choice, pick: Pick, presets: readonly Preset[]): Choice {
  if (applied.kind === "seat" || pick.kind !== "project") return applied;
  const found = projectPreset(presets, pick.key);
  return found === null ? NO_PRESET : choiceFor(found);
}

/** The preset the line applies when Home opens on `pick`: its project's, when it is one. */
export function atLoad(pick: Pick, presets: readonly Preset[]): Choice {
  return afterPick(NO_PRESET, pick, presets);
}

/** The item's face: the applied preset's name, or `none` dim. */
export function presetFace(applied: Choice): { name: string; dim: boolean } {
  return applied.kind === "" ? { name: NONE, dim: true } : { name: applied.name, dim: false };
}

/**
 * What a menu row says the preset sets: its harness, model and effort as its values give
 * them, then each value of it that can do harm, marked. `fields` are the catalog's rows of
 * the preset's harness, which say which values can do harm.
 */
export function presetSets(preset: Preset, fields: readonly PanelField[]): Word[] {
  const named: Word[] = NAMED_ROWS.map((name) => (name === "harness" ? preset.harness : null) ?? presetValue(preset, name))
    .filter((text): text is string => text !== null).map((text) => ({ text, danger: false }));
  const fieldOf = new Map(fields.map((field) => [field.name, field]));
  const danger: Word[] = preset.values.filter((row) => {
    const field = fieldOf.get(row.name);
    return field !== undefined && isDanger(field, row.value);
  }).map((row) => ({ text: row.value as string, danger: true }));
  return [...named, ...danger];
}

/** Where a menu row says a preset puts the launch: `this project` for the project on the
 * line now, `in <project>` or `in <folder>` for another place, "" for none. */
export function placeWords(place: Pick | null, pick: Pick | null, home: string): string {
  if (place === null || place.kind === "choose") return "";
  if (place.kind === "project") return pick?.kind === "project" && pick.key === place.key ? THIS_PROJECT : `in ${place.key}`;
  return `in ${tilde(place.path, home)}`;
}

/** One preset in the menu. */
export interface PresetRow {
  choice: Choice;
  name: string;
  sets: Word[];
  place: string;
  /** The applied preset, which carries the tick. */
  current: boolean;
}

/** One section of the menu: its heading and its rows, by name. */
export interface PresetSection {
  title: string;
  rows: PresetRow[];
}

/**
 * The menu's sections after `No preset`: the project presets, then the seats, each by name;
 * a kind with none is left out. `fieldsOf` gives the catalog's rows of a harness, null for
 * the harness the form is drawn for now.
 */
export function presetMenu(presets: readonly Preset[], applied: Choice, pick: Pick | null, projects: boolean,
                           home: string, fieldsOf: (harness: string | null) => readonly PanelField[]): PresetSection[] {
  const out: PresetSection[] = [];
  for (const section of PRESET_SECTIONS) {
    const mine = presets.filter((preset) => preset.kind === section.kind)
      .sort(byName);
    if (mine.length === 0) continue;
    out.push({ title: section.title, rows: mine.map((preset) => ({
      choice: choiceFor(preset), name: preset.name, sets: presetSets(preset, fieldsOf(preset.harness)),
      place: placeWords(presetPlace(preset, projects), pick, home),
      current: sameChoice(choiceFor(preset), applied),
    })) });
  }
  return out;
}

/** The line under the box for the applied preset's permission rows off the user's usual
 * values: the lead, each row's label with whether its value can do harm, and the close. */
export interface PresetOff {
  lead: string;
  labels: Word[];
  tail: string;
}

/**
 * The line under the box for the rows `rows` names as the preset's (`offRows` with the
 * form read with no preset as its base), from the moment the preset is applied: `Preset
 * planner sets Permission mode, Allowed tools off your defaults`, each label marked when
 * the row's value now can do harm, those first, then the rest in the fields' order. Null
 * with no preset applied or none of its rows off.
 */
export function presetOff(applied: Choice, fields: readonly PanelField[], options: Readonly<Record<string, FormValue>>,
                          rows: readonly OffRow[]): PresetOff | null {
  if (applied.kind === "") return null;
  const fieldOf = new Map(fields.map((field) => [field.name, field]));
  const labels = rows.filter((row) => row.source === "preset").map((row) => {
    const field = fieldOf.get(row.name);
    return { text: field === undefined ? row.label : rowLabel(field),
             danger: field !== undefined && isDanger(field, valueNow(field, options)) };
  });
  if (labels.length === 0) return null;
  return { lead: `${PRESET} ${applied.name} sets`, labels: [...labels.filter((word) => word.danger),
           ...labels.filter((word) => !word.danger)], tail: "off your defaults" };
}

/** The query `/api/last` and `/api/recents` take for what the line above the box names: the
 * seat while one is applied, else the project, else the folder; null while no place is picked. */
export function lastQuery(applied: Choice, pick: Pick | null): string | null {
  if (applied.kind === "seat") return new URLSearchParams({ seat: applied.name }).toString();
  if (pick === null || pick.kind === "choose") return null;
  return new URLSearchParams(pick.kind === "project" ? { project: pick.key } : { cwd: pick.path }).toString();
}

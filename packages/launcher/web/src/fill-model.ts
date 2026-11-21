// What Home fills from the last launch of what the line above its box names, worked out with
// no DOM so the Node tests can drive it. prompt.ts draws it.
//
// The last launch is keyed as the line names it: the seat while one is applied, else the
// project, else the folder (`lastQuery`), so a launch never fills from another seat's or
// another project's launch, even in the same folder. `/api/last` answers that launch's
// harness and the values its own request gave, never one of a row that is not carried. The
// comms identity comes apart from them, once for each harness: the one launches there of
// that harness bound (a project's seats' launches among them), at the global scope, else
// the default identity. The form fills the one for the harness it is drawn for, whatever
// harness the newest launch ran; with no launch only that fills.
//
// A value fills only where the form has the row, the row is shown on this install, and the
// form leaves it at its shipped default: what the user's `[defaults]` or the applied preset
// set is never filled over, and neither is a row the user set here. The harness goes first:
// when the launch ran another harness and the form leaves the harness at its shipped
// default, the harness fills like a row and the form is read again for it, and the values
// fill against that form; when `[defaults]` or the preset sets another harness, only the
// comms identity for the form's harness fills, since the values are rows of a harness this
// launch does not use. A harness the user picks by hand lets go of every filled value and
// fills the comms identity for that harness, unless the user set the identity here.
//
// A filled comms identity comms refuses a launch under shows the refusal beside it, and one
// whose name a live session holds in a project scope shows a note naming that binding.
//
// A filled value is not a change. It is named on its own item on the line above the box,
// with what it is, where it came from and `Clear`, and under its row in the panel. One the
// user edits becomes his change and leaves the item. A filled value that can do harm asks
// nothing: the launch it came from confirmed it, and the page draws it in the warning
// colour wherever it is named.
//
// Every string here goes into the page as text.

import type { FieldDescriptor, FormValue } from "/m/ui/client.js";

import type { Refusal } from "./feed.ts";
import { isListField, refusalOf, same, shownValue } from "./panel.ts";
import type { Form } from "./panel.ts";
import { dangerNames, panelFields, rowLabel } from "./options-model.ts";
import type { Mode, PanelField } from "./options-model.ts";
import { DAY, HOUR, tilde } from "./prompt-model.ts";
import type { Clock } from "./prompt-model.ts";

type Fields = Record<string, unknown>;

function isTable(value: unknown): value is Fields {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/** The words in front of the item on the line above the box. */
export const FROM_LAST = "From last launch";

/** The words in front of the item when no launch was made there and the default filled. */
export const BY_DEFAULT = "Default";

/** The control that takes a filled value back to what the form is filled with. */
export const CLEAR = "Clear";

/** The row that names the harness, which fills before the rest. */
export const HARNESS_ROW = "harness";

/** The rows the chip draws, which a fill reaches whatever the place the button launches in. */
export const CHIP_ROWS: readonly string[] = [HARNESS_ROW, "model", "effort"];

/** What the line above the box names, which keys the last launch. */
export type LastKey = "seat" | "project" | "cwd";

/** One value of the last launch, spelled as `/api/form` spells a row's value. */
export interface LastValue {
  name: string;
  value: string | null;
  items: string[] | null;
}

/** The comms identity a form drawn for `harness` fills with: the name the newest launch of that
 * harness bound, made `at`, else the default, with a null `at`; `refused` is comms' refusal of a
 * launch under it, null when comms lets one through; `note` says a live session holds the same
 * name in a project scope, null when none does. */
export interface LastIdentity {
  harness: string;
  identity: string;
  at: string | null;
  refused: Refusal | null;
  note: string | null;
}

/** `/api/last`'s answer: when the launch was made and its harness, both null with no launch,
 * the values it carries but the comms identity and scope, and the identity of each harness
 * that has one. */
export interface Last {
  state: "up" | "error";
  detail: string | null;
  at: string | null;
  harness: string | null;
  values: LastValue[];
  identities: LastIdentity[];
}

/** The rows of the comms identity, which fill from `identities`, never from `values`. */
export const IDENTITY_ROWS: readonly string[] = ["identity", "scope"];

export function parseLast(data: unknown): Last | null {
  if (!isTable(data) || (data.state !== "up" && data.state !== "error") || !Array.isArray(data.values)) return null;
  const values = data.values.filter((row): row is Fields => isTable(row) && typeof row.name === "string")
    .map((row) => ({ name: row.name as string, value: text(row.value),
                     items: Array.isArray(row.items) ? row.items.filter((item): item is string => typeof item === "string")
                       : null }));
  const identities = (Array.isArray(data.identities) ? data.identities : [])
    .filter((row): row is Fields => isTable(row) && typeof row.harness === "string" && typeof row.identity === "string")
    .map((row) => ({ harness: row.harness as string, identity: row.identity as string, at: text(row.at),
                     refused: refusalOf(row.refused), note: text(row.note) }));
  return { state: data.state, detail: text(data.detail), at: text(data.at), harness: text(data.harness), values,
           identities };
}

/** Whether an answer fills anything: a launch, or with none an identity. */
export function fills(last: Last): boolean {
  return last.at !== null || last.identities.length > 0;
}

/** The comms identity `last` gives a form drawn for `harness`, if any. */
export function identityFor(last: Last, harness: string): LastIdentity | null {
  return last.identities.find((row) => row.harness === harness) ?? null;
}

/** Which key a query of `/api/last` names: the first given of `seat`, `project`, `cwd`. */
export function lastKey(query: string): LastKey | null {
  const said = new URLSearchParams(query);
  return (["seat", "project", "cwd"] as const).find((key) => (said.get(key) ?? "") !== "") ?? null;
}

/** A value of the last launch as the field's control holds it. */
export function lastValue(field: FieldDescriptor, row: LastValue): FormValue {
  if (isListField(field)) return [...(row.items ?? [])];
  if (field.optional_value === true) return row.value === null ? null : row.value === "true" ? "" : row.value;
  if (field.type === "bool") return row.value === "true" ? "true" : "false";
  return row.value === null || row.value === "" ? null : row.value;
}

/** What the harness of the last launch does to the fill: `same` when the form is drawn for
 * it, `read` when the form leaves the harness at its shipped default so it fills and the
 * form is read again for it, `none` when none of its values fill (no launch, or
 * `[defaults]` or the preset sets another harness); the comms identity for the form's
 * harness fills all the same. With no launch, `same` when there is an identity for the
 * form's harness. */
export type HarnessStep = "same" | "read" | "none";

export function harnessStep(last: Last, form: Form): HarnessStep {
  if (last.at === null) return identityFor(last, form.harness) !== null ? "same" : "none";
  if (last.harness === null) return "none";
  if (last.harness === form.harness) return "same";
  const row = form.rows.find((item) => item.name === HARNESS_ROW);
  return (row?.source ?? "shipped") === "shipped" ? "read" : "none";
}

/** Whether the form leaves a row at its shipped default: it has the row, and nothing laid
 * over the shipped default gave its value. */
function leftShipped(form: Form, name: string): boolean {
  const row = form.rows.find((item) => item.name === name);
  return row !== undefined && row.source === "shipped" && !row.withheld;
}

/** The identity and scope rows `last` gives a form drawn for `harness`: the name at the
 * empty scope, which comms reads as global; none when it gives that harness none. */
export function identityRows(last: Last, harness: string): LastValue[] {
  const found = identityFor(last, harness);
  return found === null ? [] : [{ name: "identity", value: found.identity, items: null },
                                { name: "scope", value: "", items: null }];
}

/**
 * The values `last` fills over the form, by row, in the fields' order: the launch's own
 * values when the form is drawn for its harness, and the comms identity for the form's
 * harness; of them, each value of a row the fields have, shown here, carried from one launch
 * to the next, left at its shipped default by `form`, not named in `held` (a row the user
 * set here), and not the value it holds already. The harness is not among them: it fills by
 * `harnessStep`.
 */
export function fillOf(last: Last, fields: readonly PanelField[], form: Form,
                       held: ReadonlySet<string> = new Set()): Record<string, FormValue> {
  const own = last.harness === form.harness
    ? last.values.filter((row) => !IDENTITY_ROWS.includes(row.name)) : [];
  const given = new Map([...own, ...identityRows(last, form.harness)].map((row) => [row.name, row]));
  const out: Record<string, FormValue> = {};
  for (const field of fields) {
    const row = given.get(field.name);
    if (row === undefined || field.name === HARNESS_ROW || held.has(field.name)) continue;
    if (field.shown === false || field.needs_absent === true || field.carry === false) continue;
    if (!leftShipped(form, field.name)) continue;
    const value = lastValue(field, row);
    if (!same(value, shownValue(field))) out[field.name] = value;
  }
  return out;
}

/** What fills when the user picks another harness by hand: the comms identity `last` gives the
 * form now drawn for it, at the global scope, unless the user set the identity here (`held`).
 * None of the launch's own values fill: the user chose the harness over them. */
export function pickedHarnessFill(last: Last, fields: readonly PanelField[], form: Form,
                                  held: ReadonlySet<string> = new Set()): Record<string, FormValue> {
  return fillOf({ ...last, values: [] }, fields, form, held);
}

/** The answer a harness picked by hand fills from, when it filled nothing for the form Home
 * opened on: `last` itself while it gives some harness a comms identity, else null. Whatever
 * the opening form filled, a later pick of a harness the answer has an identity for fills it. */
export function keptForPick<T extends Last>(last: T | null): T | null {
  return last !== null && last.identities.length > 0 ? last : null;
}

/** The filled values the form still leaves at their shipped default, as after it is read
 * again for another preset: a row it fills now with another value is no longer filled. */
export function keptFill(filled: Readonly<Record<string, FormValue>>, fields: readonly PanelField[],
                         form: Form): Record<string, FormValue> {
  const names = new Set(fields.map((field) => field.name));
  return Object.fromEntries(Object.entries(filled).filter(([name]) => name === HARNESS_ROW
    ? filled[name] === form.harness : names.has(name) && leftShipped(form, name)));
}

/** The filled values a launch in `mode` takes: those of the chip's rows, and of the rows the
 * panel draws in that mode. */
export function fillIn(filled: Readonly<Record<string, FormValue>>, fields: readonly PanelField[],
                       mode: Mode): Record<string, FormValue> {
  const names = new Set([...CHIP_ROWS, ...panelFields(fields, mode).map((field) => field.name)]);
  return Object.fromEntries(Object.entries(filled).filter(([name]) => names.has(name)));
}

/** The filled values less the rows `names`: what `Clear` on a row other than the harness
 * leaves, and what an edit of a filled row leaves, since the row is the user's from then. */
export function withoutFill(filled: Readonly<Record<string, FormValue>>, ...names: string[]): Record<string, FormValue> {
  return Object.fromEntries(Object.entries(filled).filter(([key]) => !names.includes(key)));
}

/** A filled value in a few words: a list's items, a switch on or off, a path with the home
 * folder written `~`. */
export function fillText(field: FieldDescriptor, value: FormValue, home: string): string {
  if (Array.isArray(value)) return value.length === 0 ? "none" : value.map((item) => tilde(item, home)).join(", ");
  if (field.optional_value === true) return value === null ? "off" : value === "" ? "on" : value;
  if (field.type === "bool") return value === "true" ? "on" : "off";
  return value === null ? "empty" : value.startsWith("/") ? tilde(value, home) : value;
}

/** One filled value in the item's list. */
export interface FillRow {
  name: string;
  label: string;
  text: string;
  /** The value can do harm. */
  danger: boolean;
}

/** The filled values as the item's list gives them, in the fields' order. */
export function fillRows(filled: Readonly<Record<string, FormValue>>, fields: readonly PanelField[],
                         home: string): FillRow[] {
  const danger = new Set(dangerNames(fields, filled));
  return fields.filter((field) => Object.hasOwn(filled, field.name)).map((field) => ({
    name: field.name, label: rowLabel(field), text: fillText(field, filled[field.name], home),
    danger: danger.has(field.name),
  }));
}

/** The item's face: the one filled row's label, or `n settings` for several, marked when a
 * value it stands for can do harm; null with nothing filled. */
export function fillFace(rows: readonly FillRow[]): { text: string; danger: boolean } | null {
  if (rows.length === 0) return null;
  return { text: rows.length === 1 ? rows[0].label : `${rows.length} settings`, danger: rows.some((row) => row.danger) };
}

/** When what `filled` holds was set, for a form drawn for `harness`: the last launch's time
 * while one of its own values fills, else the time of the launch that bound the identity
 * filled; null for the default identity. */
export function filledAt(last: Last, filled: Readonly<Record<string, FormValue>>, harness: string): string | null {
  if (Object.keys(filled).some((name) => !IDENTITY_ROWS.includes(name))) return last.at;
  return identityFor(last, harness)?.at ?? null;
}

/** comms' refusal of a launch under the identity filled, for a form drawn for `harness`: shown
 * beside the name, as the launch would meet it; null when none is filled, or comms lets it
 * through. */
export function filledRefusal(last: Last, filled: Readonly<Record<string, FormValue>>,
                              harness: string): Refusal | null {
  const found = identityFor(last, harness);
  return found !== null && filled.identity === found.identity ? found.refused : null;
}

/** The note beside the identity filled, for a form drawn for `harness`, when a live session
 * holds the same name in a project scope; null when none is filled, or no such session. */
export function filledNote(last: Last, filled: Readonly<Record<string, FormValue>>,
                           harness: string): string | null {
  const found = identityFor(last, harness);
  return found !== null && filled.identity === found.identity ? found.note : null;
}

/** A refusal in a sentence: what is wrong, then what to do. */
export function refusalNote(found: Refusal): string {
  return found.hint ? `${found.problem}. ${found.hint}.` : `${found.problem}.`;
}

/** Whose last launch the line names, as the item's sentence says it. */
export const WHOSE: Readonly<Record<LastKey, string>> = {
  seat: "this agent's", project: "this project's", cwd: "this folder's",
};

/** When the last launch was made: `today 18:45`, else the day and the time. Empty for a
 * time that can't be read. */
export function lastWhen(at: string, now: Date, hour: Clock = HOUR, day: Clock = DAY): string {
  const then = new Date(at);
  if (at === "" || Number.isNaN(then.getTime())) return "";
  const today = then.getFullYear() === now.getFullYear() && then.getMonth() === now.getMonth()
    && then.getDate() === now.getDate();
  return today ? `today ${hour(then)}` : `${day(then)} ${hour(then)}`;
}

/** The dim sentence under the item's list: `Filled from this project's last launch, today
 * 18:45. The preset sets nothing for it.`, `them` for several values; with no launch (`when`
 * empty), `Filled with this project's default comms identity, as no launch here set one.`. */
export function fillNote(key: LastKey, when: string, count: number): string {
  const tail = `The preset sets nothing for ${count > 1 ? "them" : "it"}.`;
  if (when === "") return `Filled with ${WHOSE[key]} default comms identity, as no launch here set one. ${tail}`;
  return `Filled from ${WHOSE[key]} last launch, ${when}. ${tail}`;
}

/** The note under a filled value in the panel; with no launch (`when` empty), the default's. */
export function fromLast(when: string): string {
  return when === "" ? "the default, as no launch here set one" : `from the last launch, ${when}`;
}

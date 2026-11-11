// What the launcher's panel sends and what it shows, worked out with no DOM so the Node
// tests can drive it. page.ts draws it.
//
// The launch form is the UI shell's one form renderer over `/api/catalog`, filled from
// `/api/form`: each field shows the value the shipped defaults, the user's `[defaults]` and
// the one preset chosen give it. Every field is in one of two states, a value or empty, and
// the request carries the whole form: a field with a value goes in `values`, an empty one
// in `leave_out`, and the page server compares each with what the layers give. Nothing the
// form sends is saved back to the preset.
//
// Every string here goes into the page as text.

import type { ApiResult, FieldDescriptor, FormValue } from "/m/ui/client.js";

import type { LaunchRow, Launches, Refusal, States } from "./feed.ts";
import { launchLabel, when } from "./views.ts";
import type { FeedState } from "./views.ts";

type Fields = Record<string, unknown>;

function isTable(value: unknown): value is Fields {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function texts(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** `{state, detail}` of a read's answer, or null when it isn't one. */
function standing(data: unknown): { state: "up" | "error"; detail: string | null; fields: Fields } | null {
  if (!isTable(data)) return null;
  const { state, detail } = data;
  if (state !== "up" && state !== "error") return null;
  if (detail !== null && detail !== undefined && typeof detail !== "string") return null;
  return { state, detail: state === "error" ? (detail ?? null) : null, fields: data };
}

function isRefusal(value: unknown): value is Refusal {
  return isTable(value) && typeof value.code === "string" && typeof value.problem === "string";
}

/** A refusal with only its own four fields, each of its type. */
function refusal(value: Refusal): Refusal {
  return { code: value.code, row: text(value.row), problem: value.problem, hint: text(value.hint) ?? "" };
}

// --- the catalog and the presets -------------------------------------------------------

/** One harness's form: the launcher's rows, then the harness's own. */
export interface Harness {
  name: string;
  fields: FieldDescriptor[];
}

/** The value of `/api/catalog`. */
export interface Catalog {
  state: "up" | "error";
  detail: string | null;
  harnesses: Harness[];
}

export function parseCatalog(data: unknown): Catalog | null {
  const read = standing(data);
  if (read === null) return null;
  if (read.state === "error") return { state: "error", detail: read.detail, harnesses: [] };
  const { harnesses } = read.fields;
  if (!Array.isArray(harnesses)) return null;
  const out: Harness[] = [];
  for (const item of harnesses) {
    if (!isTable(item) || typeof item.name !== "string" || !Array.isArray(item.fields)) continue;
    const fields = item.fields.filter((field): field is FieldDescriptor =>
      isTable(field) && typeof field.name === "string" && typeof field.type === "string");
    out.push({ name: item.name, fields });
  }
  return { state: "up", detail: null, harnesses: out };
}

/** The row that picks the harness. */
export const HARNESS = "harness";

/**
 * The fields the form draws for a `harness` launch, each name once. The harness row
 * shows `harness` as its value, so the form reads as the launch it describes. Empty when
 * the catalog has no such harness.
 */
export function fieldsFor(catalog: Catalog | null, harness: string): FieldDescriptor[] {
  const found = catalog?.harnesses.find((item) => item.name === harness);
  if (found === undefined) return [];
  const seen = new Set<string>();
  const out: FieldDescriptor[] = [];
  for (const field of found.fields) {
    if (seen.has(field.name)) continue;
    seen.add(field.name);
    out.push(field.name === HARNESS ? { ...field, default: harness } : field);
  }
  return out;
}

/** One value a preset sets: a list row's in `items`, any other's in `value`. An inline
 * brief or instructions is named with both null. */
export interface PresetValue {
  name: string;
  value: string | null;
  items: string[] | null;
}

export interface Preset {
  kind: string;
  name: string;
  harness: string | null;
  values: PresetValue[];
}

export interface PresetProblem {
  where: string;
  key: string | null;
  problem: string;
  hint: string;
  code: string;
  level: string;
}

/** The value of `/api/presets`. */
export interface Presets {
  state: "up" | "error";
  detail: string | null;
  presets: Preset[];
  problems: PresetProblem[];
}

export function parsePresets(data: unknown): Presets | null {
  const read = standing(data);
  if (read === null) return null;
  if (read.state === "error") return { state: "error", detail: read.detail, presets: [], problems: [] };
  const { presets, problems } = read.fields;
  if (!Array.isArray(presets)) return null;
  const kept: Preset[] = [];
  for (const item of presets) {
    if (!isTable(item) || typeof item.kind !== "string" || typeof item.name !== "string") continue;
    const values = (Array.isArray(item.values) ? item.values : [])
      .filter((row): row is Fields => isTable(row) && typeof row.name === "string")
      .map((row) => ({ name: row.name as string, value: text(row.value),
                       items: Array.isArray(row.items) ? texts(row.items) : null }));
    kept.push({ kind: item.kind, name: item.name, harness: text(item.harness), values });
  }
  const found = (Array.isArray(problems) ? problems : [])
    .filter((row): row is Fields => isTable(row) && typeof row.problem === "string")
    .map((row) => ({ where: text(row.where) ?? "", key: text(row.key), problem: row.problem as string,
                     hint: text(row.hint) ?? "", code: text(row.code) ?? "", level: text(row.level) ?? "error" }));
  return { state: "up", detail: null, presets: kept, problems: found };
}

/** The presets of one kind (`seat` or `project`), by name. */
export function presetsOf(presets: Presets | null, kind: string): Preset[] {
  return (presets?.presets ?? []).filter((item) => item.kind === kind);
}

/** What a preset sets, one line per value: `model opus`, `add_dir /a, /b`. */
export function presetLines(preset: Preset): string[] {
  return preset.values.map((row) => {
    if (row.items !== null) return `${row.name} ${row.items.join(", ") || "(none)"}`;
    if (row.value === null) return `${row.name} (set, not shown here)`;
    return `${row.name} ${row.value}`;
  });
}

/** The problems a choice of presets meets: the file's, and each chosen preset's. */
export function presetProblems(presets: Presets | null, seat: string, project: string): PresetProblem[] {
  const chosen = new Set([seat ? `seat.${seat}` : "", project ? `project.${project}` : ""]);
  return (presets?.problems ?? []).filter((item) => item.where === "file" || chosen.has(item.where));
}

// --- the preset choice ---------------------------------------------------------------------

/** The one preset a launch starts from: a seat, a project preset, or none (`kind` ""). */
export interface Choice {
  kind: "seat" | "project" | "";
  name: string;
}

export const NO_PRESET: Choice = { kind: "", name: "" };

/** The choice as one select's value: `seat:<name>`, `project:<name>`, or "" for none. */
export function choiceKey(choice: Choice): string {
  return choice.kind && choice.name ? `${choice.kind}:${choice.name}` : "";
}

/** The choice a select's value names; anything else reads as none. */
export function choiceOf(key: string): Choice {
  const at = key.indexOf(":");
  const kind = key.slice(0, at);
  const name = key.slice(at + 1);
  return at > 0 && name && (kind === "seat" || kind === "project") ? { kind, name } : NO_PRESET;
}

/** The chosen preset, when the presets list it. */
export function chosenPreset(presets: Presets | null, choice: Choice): Preset | null {
  return (presets?.presets ?? []).find((item) => item.kind === choice.kind && item.name === choice.name) ?? null;
}

/** "the seat planner", "the project preset app". */
function named(choice: Choice): string {
  return `${choice.kind === "seat" ? "the seat" : "the project preset"} ${choice.name}`;
}

// --- filling the form ------------------------------------------------------------------------

/** One row of `/api/form`: its value after the shipped defaults, `[defaults]` and the
 * preset, spelled as a preset's value is. A withheld text comes with no text. */
export interface FormRow {
  name: string;
  value: string | null;
  items: string[] | null;
  /** `shipped`, `defaults` or `preset`. */
  source: string;
  withheld: boolean;
}

/** The command a launch composes in one place the `target` row names; null where the
 * resolver refuses a launch there. */
export interface FormMode {
  target: string;
  mode: string | null;
}

/** The value of `/api/form`: the harness the form is drawn for and its rows, or the
 * resolver's refusal with no rows, and the command each place a launch runs in composes. */
export interface Form {
  state: "up" | "error";
  detail: string | null;
  harness: string;
  rows: FormRow[];
  refused: Refusal | null;
  modes: FormMode[];
}

function formModes(data: unknown): FormMode[] {
  if (!Array.isArray(data)) return [];
  return data.filter((item): item is Fields => isTable(item) && typeof item.target === "string")
    .map((item) => ({ target: item.target as string, mode: typeof item.mode === "string" ? item.mode : null }));
}

export function parseForm(data: unknown): Form | null {
  const read = standing(data);
  if (read === null) return null;
  if (read.state === "error") return { state: "error", detail: read.detail, harness: "", rows: [], refused: null, modes: [] };
  const { harness, rows, refused, modes } = read.fields;
  if (typeof harness !== "string" || !Array.isArray(rows)) return null;
  const kept = rows.filter((row): row is Fields => isTable(row) && typeof row.name === "string")
    .map((row) => ({ name: row.name as string, value: text(row.value),
                     items: Array.isArray(row.items) ? texts(row.items) : null,
                     source: text(row.source) ?? "shipped", withheld: row.withheld === true }));
  return { state: "up", detail: null, harness, rows: kept, refused: isRefusal(refused) ? refusal(refused) : null,
           modes: formModes(modes) };
}

/** The `/api/form` path for a choice, for `harness` when one is asked, and with the comms
 * name the launch holds, `identity`, when it holds one. */
export function formPath(choice: Choice, harness = "", identity = ""): string {
  const query = new URLSearchParams();
  if (choice.kind === "seat" && choice.name) query.set("seat", choice.name);
  if (choice.kind === "project" && choice.name) query.set("project_preset", choice.name);
  if (harness) query.set(HARNESS, harness);
  if (identity) query.set("identity", identity);
  const said = query.toString();
  return said ? `api/form?${said}` : "api/form";
}

/** Where a row's value came from, as a line under its field; null at the shipped default. */
export function sourceNote(source: string, choice: Choice): string | null {
  if (source === "preset" && choice.kind) return `set by ${named(choice)}`;
  if (source === "defaults") return "set in your [defaults]";
  return null;
}

/** What a withheld text is called. */
function textName(name: string): string {
  return name === "instructions" ? "standing instructions" : "brief";
}

/** The line under a withheld text's field. */
export function withheldNote(row: FormRow, choice: Choice): string {
  return `${sourceNote(row.source, choice) ?? "set"}; its text is not shown here`;
}

/** The keep box beside the form for a withheld text: "Keep the brief the seat planner sets". */
export function keepLabel(row: FormRow, choice: Choice): string {
  const what = textName(row.name);
  return row.source === "defaults" ? `Keep the ${what} your [defaults] set`
    : `Keep the ${what} ${choice.kind ? named(choice) : "the preset"} sets`;
}

/**
 * The choice a word enum adds for its empty state, labelled with the word that then
 * launches: "leave out (launches off)". Null for any other field: `harness` has no empty
 * state, and an enum whose shipped default sets nothing gets a blank choice instead.
 */
export function leaveOutChoice(field: FieldDescriptor | undefined): string | null {
  if (field?.type !== "enum" || field.name === HARNESS || field.repeatable === true || field.optional_value === true) {
    return null;
  }
  return typeof field.default === "string" && field.default !== "" ? `leave out (launches ${field.default})` : null;
}

/** Whether a field is shown off: its module or condition is absent here. */
function isOff(field: FieldDescriptor): boolean {
  return field.needs_absent === true;
}

/** Whether a field is drawn as a list: the `list` type, or a row that may be given more than once. */
export function isListField(field: FieldDescriptor): boolean {
  return field.type === "list" || field.repeatable === true;
}

/** `note` with `line` in front, when the field takes input; an off field's note says why. */
function noted(field: FieldDescriptor, line: string | null): string | null | undefined {
  if (line === null || isOff(field)) return field.note;
  return field.note ? `${line}; ${field.note}` : line;
}

/**
 * The fields for `harness` filled from `/api/form`: each field's `value` (`value_items`
 * for a list) is the row's layered value, with a note naming its source. An enum gets its
 * empty state: a blank choice when its shipped default sets nothing, else the "leave out"
 * choice. A withheld text shows empty with its note. A field the answer has no row for
 * (shown off, or every row of a refused answer) keeps the catalog's default.
 */
export function filledFields(fields: readonly FieldDescriptor[], form: Form | null,
                             choice: Choice): FieldDescriptor[] {
  const rows = new Map((form?.rows ?? []).map((row) => [row.name, row]));
  return fields.map((field) => {
    const out: FieldDescriptor = { ...field };
    if (field.type === "enum" && field.name !== HARNESS && Array.isArray(field.values)) {
      const leave = leaveOutChoice(field);
      out.values = leave !== null ? [...field.values, leave]
        : field.values.includes("") ? [...field.values] : ["", ...field.values];
    }
    if (field.name === HARNESS && form !== null) out.value = form.harness;
    const row = rows.get(field.name);
    if (row === undefined) return out;
    if (field.name === HARNESS) {
      // The row holds the preset's harness, which is the one shown unless another is asked.
      if (row.value === form?.harness) out.note = noted(field, sourceNote(row.source, choice));
      return out;
    }
    if (row.withheld) {
      out.value = "";
      out.note = noted(field, withheldNote(row, choice));
      return out;
    }
    if (isListField(field)) out.value_items = row.items ?? [];
    else if (field.optional_value === true) out.value = row.value === null ? null : row.value === "true" ? true : row.value;
    else out.value = row.value ?? "";
    out.note = noted(field, sourceNote(row.source, choice));
    return out;
  });
}

// --- the request, from the whole form -----------------------------------------------------

/** A value a launch request may carry: one string, or a list's items. */
export type Sent = string | string[];

/** What a launch request carries from the form: each field with a value in `values`, each
 * empty one in `leave_out`, both by catalog key. */
export interface FormRequest {
  values: Record<string, Sent>;
  leave_out: string[];
}

/**
 * The request from every field the form sends (`<moxi-ui-form>`'s values; a field shown
 * off sends none and is in neither list). Each kind's empty state goes in `leave_out`: an
 * empty text, a list with no items, a switch off, a bool unticked, the blank or "leave
 * out" choice. A switch on is `"true"` with no text, else its text. `harness` always has a
 * value. A withheld text left empty with its keep box ticked is in neither list, so the
 * launch takes the preset's text.
 */
export function requestOf(fields: readonly FieldDescriptor[], values: Record<string, FormValue>,
                          withheld: ReadonlySet<string>, keep: ReadonlySet<string>): FormRequest {
  const byName = new Map(fields.map((field) => [field.name, field]));
  const out: FormRequest = { values: {}, leave_out: [] };
  for (const [name, value] of Object.entries(values)) {
    const field = byName.get(name);
    if (Array.isArray(value)) {
      if (value.length) out.values[name] = [...value];
      else out.leave_out.push(name);
    } else if (field?.optional_value === true) {
      if (value === null) out.leave_out.push(name);
      else out.values[name] = value === "" ? "true" : value;
    } else if (value === null || (field?.type === "bool" && value === "false") || value === leaveOutChoice(field)) {
      if (name === HARNESS) continue;
      if (value === null && withheld.has(name) && keep.has(name)) continue;
      out.leave_out.push(name);
    } else {
      out.values[name] = value;
    }
  }
  return out;
}

/** A launch as `/api/preview` and `/api/launch` take it. */
export interface LaunchBody {
  seat?: string;
  project_preset?: string;
  values: Record<string, Sent>;
  leave_out: string[];
  off_posture?: boolean;
  /** The rows the page filled from the last launch and the user left as they were. */
  filled?: string[];
}

export function launchBody(choice: Choice, request: FormRequest, offPosture: boolean): LaunchBody {
  const body: LaunchBody = { values: request.values, leave_out: request.leave_out };
  if (choice.kind === "seat" && choice.name) body.seat = choice.name;
  if (choice.kind === "project" && choice.name) body.project_preset = choice.name;
  if (offPosture) body.off_posture = true;
  return body;
}

// --- rows that exclude each other ----------------------------------------------------------

/** The rows of which a launch takes one: the place rows, and each text's two spellings. */
export const GROUPS: readonly (readonly string[])[] = [
  ["cwd", "project", "scratch"], ["brief", "brief_file"], ["instructions", "instructions_file"],
];

/** What a field shows before the user touches it, as the form would send it. */
export function shownValue(field: FieldDescriptor): FormValue {
  if (field.optional_value === true) return field.value === true ? "" : typeof field.value === "string" ? field.value : null;
  if (isListField(field)) {
    const items = Array.isArray(field.value_items) ? field.value_items : Array.isArray(field.value) ? field.value : [];
    return texts(items);
  }
  const said = typeof field.value === "string" ? field.value
    : field.value == null && typeof field.default === "string" ? field.default : "";
  if (field.type === "bool") return said === "true" ? "true" : "false";
  return said === "" ? null : said;
}

/** Whether a value is a field's value state rather than its empty state. */
function isSet(field: FieldDescriptor, value: FormValue | undefined): boolean {
  if (value === undefined || value === null) return false;
  if (Array.isArray(value)) return value.length > 0;
  if (field.optional_value === true) return true;
  if (field.type === "bool") return value === "true";
  return value !== "" && value !== leaveOutChoice(field);
}

/** Whether two values of a field are the same value. */
export function same(a: FormValue | undefined, b: FormValue | undefined): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

/** The field showing its empty state. */
function emptied(field: FieldDescriptor): FieldDescriptor {
  if (isListField(field)) return { ...field, value: null, value_items: [] };
  if (field.optional_value === true) return { ...field, value: null };
  return { ...field, value: field.type === "bool" ? "false" : "" };
}

/**
 * When the user has set one row of a group (its value differs from what its field shows,
 * and is a value), every other row of the group that still shows its filled value is
 * emptied, and a withheld sibling's keep box is unticked, so the form shows what will
 * launch. A row the user changed himself stays as he left it. `fields` comes back as the
 * same array when nothing changed.
 */
export function emptyGroups(fields: FieldDescriptor[], now: Record<string, FormValue>,
                            withheld: ReadonlySet<string>, keep: ReadonlySet<string>):
    { fields: FieldDescriptor[]; keep: Set<string> } {
  const byName = new Map(fields.map((field) => [field.name, field]));
  const replaced = new Map<string, FieldDescriptor>();
  const kept = new Set(keep);
  for (const group of GROUPS) {
    const present = group.filter((name) => byName.has(name) && name in now);
    const set = present.filter((name) => {
      const field = byName.get(name) as FieldDescriptor;
      return isSet(field, now[name]) && !same(now[name], shownValue(field));
    });
    if (set.length === 0) continue;
    for (const name of present) {
      if (set.includes(name)) continue;
      const field = byName.get(name) as FieldDescriptor;
      const shown = shownValue(field);
      if (same(now[name], shown) && isSet(field, shown)) replaced.set(name, emptied(field));
      if (withheld.has(name) && !isSet(field, now[name])) kept.delete(name);
    }
  }
  if (replaced.size === 0) return { fields, keep: kept };
  return { fields: fields.map((field) => replaced.get(field.name) ?? field), keep: kept };
}

// --- the preview ------------------------------------------------------------------------

/** What `/api/preview` answers: what the launch would start, what stops it, and a ticket
 * to launch it when nothing does and it doesn't run in a terminal. */
export interface Preview {
  refused: Refusal[];
  harness: string | null;
  mode: string | null;
  target: string | null;
  argv: string[];
  stamps: { name: string; value: string }[];
  first_turn: string | null;
  not_applied: string[];
  warnings: string[];
  /** The rows the request leaves out. */
  left_out: string[];
  /** The `moxi launcher run` command to copy, for a launch in a terminal. */
  command: string | null;
  request_id: string | null;
  ticket: string | null;
  expires_at: string | null;
}

export function parsePreview(data: unknown): Preview | null {
  if (!isTable(data) || !Array.isArray(data.refused) || !Array.isArray(data.argv)) return null;
  const stamps = (Array.isArray(data.stamps) ? data.stamps : [])
    .filter((row): row is Fields => isTable(row) && typeof row.name === "string" && typeof row.value === "string")
    .map((row) => ({ name: row.name as string, value: row.value as string }));
  return {
    refused: data.refused.filter(isRefusal).map(refusal),
    harness: text(data.harness), mode: text(data.mode), target: text(data.target),
    argv: texts(data.argv), stamps, first_turn: text(data.first_turn),
    not_applied: texts(data.not_applied), warnings: texts(data.warnings), left_out: texts(data.left_out),
    command: text(data.command), request_id: text(data.request_id), ticket: text(data.ticket),
    expires_at: text(data.expires_at),
  };
}

/** A word as a POSIX shell reads it back: bare when it is safe, else in single quotes. */
export function quote(word: string): string {
  if (word !== "" && /^[A-Za-z0-9_@%+=:,./-]+$/.test(word)) return word;
  return `'${word.replace(/'/g, `'"'"'`)}'`;
}

/** An argv as one command line to read. */
export function commandLine(argv: readonly string[]): string {
  return argv.map(quote).join(" ");
}

/** Refusals as lines: `code: problem (row) — hint`. */
export function refusalLine(found: Refusal): string {
  const row = found.row ? ` (${found.row})` : "";
  return `${found.problem}${row}${found.hint ? `. ${found.hint}` : ""}`;
}

// --- launching and resuming --------------------------------------------------------------

/** Where a 202's status path points: the path to read, and the request id in it. */
export interface Status {
  path: string;
  request: string;
}

/** The status path of a launch or resume's 202, as `api` takes it, or null when it isn't
 * one of the launch log's. */
export function statusOf(data: unknown): Status | null {
  const raw = isTable(data) ? text(data.status) : null;
  if (raw === null) return null;
  const path = raw.replace(/^\/+/, "");
  const match = /^api\/launches\?request=([^&]+)$/.exec(path);
  if (match === null) return null;
  let request: string;
  try {
    request = decodeURIComponent(match[1]);
  } catch {
    return null;
  }
  return request ? { path, request } : null;
}

/** How a launch or a resume came back. */
export type Outcome =
  /** 202: a job runs it; its row comes by the stream, or a refusal from `status`. */
  | { kind: "started"; status: Status }
  /** Refused before anything ran; a terminal launch's refusal carries its command too. */
  | { kind: "refused"; refusal: Refusal; command: string | null }
  /** A terminal resume: the command to copy, nothing run. */
  | { kind: "command"; command: string }
  /** 409: the ticket or request was used already. */
  | { kind: "used"; text: string }
  /** Any other answer. */
  | { kind: "failed"; text: string };

function answered(result: ApiResult): string {
  if (result.status === 0) return "no answer from the shell";
  const said = isTable(result.data) ? text(result.data.refused) ?? text(result.data.error) ?? text(result.data.conflict) : null;
  return said ?? `the launcher's page server answered ${result.status}`;
}

/** What `/api/launch` or `/api/resume` answered. */
export function outcomeOf(result: ApiResult): Outcome {
  const data = result.data;
  if (result.status === 202) {
    const status = statusOf(data);
    return status ? { kind: "started", status }
      : { kind: "failed", text: "the launcher's page server started a job but sent no status path to follow" };
  }
  if (result.status === 409) {
    return { kind: "used", text: (isTable(data) ? text(data.conflict) : null) ?? "this was sent already" };
  }
  if (result.status === 200 && isTable(data)) {
    const command = text(data.command);
    if (isRefusal(data.refused)) return { kind: "refused", refusal: refusal(data.refused), command };
    if (data.refused === null && command) return { kind: "command", command };
  }
  return { kind: "failed", text: answered(result) };
}

/** Where a started launch or resume stands, read from its status path. `why` is the
 * sentence recorded for a failed row, null or absent where none was read. */
export type Followed =
  | { kind: "row"; row: LaunchRow; why?: string | null }
  | { kind: "refused"; refusal: Refusal }
  | { kind: "waiting" }
  | { kind: "failed"; text: string };

/** What a status path (`/api/launches?request=`) answered. */
export function followedOf(result: ApiResult): Followed {
  if (result.status !== 200 || !isTable(result.data)) return { kind: "failed", text: answered(result) };
  const data = result.data;
  if (data.state === "error") return { kind: "failed", text: text(data.detail) ?? "the launcher's page server hit an error" };
  if (Array.isArray(data.launches)) {
    const row = data.launches.find((item) => isTable(item) && typeof item.request_id === "string");
    if (row !== undefined) return { kind: "row", row: row as LaunchRow, why: text(data.why) };
  }
  if (isRefusal(data.refused)) return { kind: "refused", refusal: refusal(data.refused) };
  return { kind: "waiting" };
}

/** How long a started launch or resume is followed by its status path, and how often. */
export const FOLLOW_MS = 2000;
export const FOLLOW_FOR_MS = 11 * 60 * 1000;

// --- the launch log ----------------------------------------------------------------------

/** One launch as the panel's list draws it. */
export interface LaunchItem {
  request: string;
  label: string;
  state: string;
  when: string;
  cwd: string;
  preset: string | null;
  reason: string | null;
  /** The session id, when the launch has one. */
  session: string | null;
  /** Its process runs (the stream's live launches hold it); a state chip goes beside it. */
  live: boolean;
  /** Started from this panel in this visit. */
  mine: boolean;
}

export interface LaunchesView {
  /** `loading`, `up`, `empty`, `error`, `failed` (the last rows are kept) or `gone`. */
  state: "loading" | "up" | "empty" | "error" | "failed" | "gone";
  rows: LaunchItem[];
  detail: string | null;
}

/** The launch log's newest rows, each live one marked so from the live launches' states. */
export function launchesView(launches: FeedState<Launches>, states: FeedState<States> | null,
                             mine: ReadonlySet<string> = new Set()): LaunchesView {
  const live = new Set(states?.value?.state === "up" ? states.value.sessions.map((entry) => entry.request_id) : []);
  const rows = (launches.value?.state === "up" ? launches.value.launches : []).map((row) => ({
    request: row.request_id,
    label: launchLabel(row),
    state: row.stale ? `${row.state} (stale)` : row.state,
    when: when(row.created_at),
    cwd: row.cwd,
    preset: row.preset_kind && row.preset_name ? `${row.preset_kind} ${row.preset_name}` : null,
    reason: row.reason,
    session: row.session_id,
    live: live.has(row.request_id),
    mine: mine.has(row.request_id),
  }));
  if (launches.gone) return { state: "gone", rows: [], detail: null };
  if (launches.failure !== null) return { state: "failed", rows, detail: launches.failure.why };
  if (launches.loading || launches.value === null) return { state: "loading", rows: [], detail: null };
  if (launches.value.state === "error") {
    return { state: "error", rows: [], detail: launches.value.detail ?? "the launcher's page server hit an error" };
  }
  return { state: rows.length ? "up" : "empty", rows, detail: null };
}

// --- the past sessions ---------------------------------------------------------------------

/** One entry of `/api/sessions`. */
export interface Past {
  request_id: string;
  created_at: string;
  harness: string;
  target: string;
  slot: string | null;
  cwd: string;
  identity: string | null;
  state: string;
  session_id: string | null;
  live: boolean | null;
  resumable: boolean;
  why: string | null;
  hint: string | null;
  unread: string | null;
  held_by: string | null;
  resume_of: string | null;
  relaunch_of: string | null;
}

export interface Pasts {
  state: "up" | "error";
  detail: string | null;
  sessions: Past[];
}

function isPast(value: unknown): value is Past {
  return isTable(value) && typeof value.request_id === "string" && typeof value.harness === "string"
    && typeof value.target === "string" && typeof value.resumable === "boolean";
}

export function parsePasts(data: unknown): Pasts | null {
  const read = standing(data);
  if (read === null) return null;
  if (read.state === "error") return { state: "error", detail: read.detail, sessions: [] };
  const { sessions } = read.fields;
  if (!Array.isArray(sessions)) return null;
  return { state: "up", detail: null, sessions: sessions.filter(isPast) };
}

/** What a past session's resume does: `slot` runs it as a job, `terminal` gives the
 * command to copy, `none` when it can't be resumed now. */
export type ResumeKind = "slot" | "terminal" | "none";

/** One past session as the panel draws it. */
export interface PastItem {
  request: string;
  session: string | null;
  label: string;
  when: string;
  cwd: string;
  /** `live`, `ended`, or `unknown` when liveness could not be read. */
  live: "live" | "ended" | "unknown";
  resume: ResumeKind;
  /** Why it can't be resumed, and what to do instead; or why its liveness is unknown. */
  why: string | null;
}

export function pastItem(past: Past): PastItem {
  const where = past.target === "slot" && past.slot ? `slot ${past.slot}` : past.target;
  const why = !past.resumable ? [past.why, past.hint].filter((part) => part).join(". ") || null
    : past.unread;
  return {
    request: past.request_id,
    session: past.session_id,
    label: [past.harness, where, past.identity].filter((part) => part).join(" · "),
    when: when(past.created_at),
    cwd: past.cwd,
    live: past.live === null ? "unknown" : past.live ? "live" : "ended",
    resume: !past.resumable || !past.session_id ? "none" : past.target === "slot" ? "slot" : "terminal",
    why,
  };
}

export interface PastView {
  state: "loading" | "up" | "empty" | "error" | "failed" | "gone";
  rows: PastItem[];
  detail: string | null;
}

export function pastView(pasts: FeedState<Pasts>, older: readonly Past[] = []): PastView {
  const kept = pasts.value?.state === "up" ? pasts.value.sessions : [];
  const seen = new Set(kept.map((item) => item.request_id));
  const rows = [...kept, ...older.filter((item) => !seen.has(item.request_id))].map(pastItem);
  if (pasts.gone) return { state: "gone", rows: [], detail: null };
  if (pasts.failure !== null) return { state: "failed", rows, detail: pasts.failure.why };
  if (pasts.loading || pasts.value === null) return { state: "loading", rows: [], detail: null };
  if (pasts.value.state === "error") {
    return { state: "error", rows: [], detail: pasts.value.detail ?? "the launcher's page server hit an error" };
  }
  return { state: rows.length ? "up" : "empty", rows, detail: null };
}

/** The `/api/sessions` path for the page after the oldest row shown. */
export function olderPath(rows: readonly Past[]): string | null {
  const last = rows[rows.length - 1];
  return last ? `api/sessions?before=${encodeURIComponent(last.created_at)}` : null;
}

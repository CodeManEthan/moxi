// What the launcher's panel sends and what it shows, worked out with no DOM so the Node
// tests can drive it. page.ts draws it.
//
// The launch form is the UI shell's one form renderer over `/api/catalog`. The form sends
// a value for every field it offers, but the panel sends only the fields the user changed:
// a default left as it was must not override the user's `[defaults]` or the preset. A
// field counts as changed once its value has differed from what the form first showed,
// and stays so even when the user sets it back, since that is a value chosen on purpose.
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

/** The names every harness's form has: the launcher's own rows, which mean the same
 * whatever the harness. */
export function sharedNames(catalog: Catalog | null): Set<string> {
  const all = catalog?.harnesses ?? [];
  if (all.length === 0) return new Set();
  const [first, ...rest] = all;
  return new Set(first.fields.map((field) => field.name)
    .filter((name) => rest.every((item) => item.fields.some((field) => field.name === name))));
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

/** The harness the chosen presets name: the seat's, else the project preset's; null
 * when neither names one. */
export function presetHarness(presets: Presets | null, seat: string, project: string): string | null {
  const all = presets?.presets ?? [];
  const named = [all.find((item) => item.kind === "seat" && item.name === seat),
                 all.find((item) => item.kind === "project" && item.name === project)];
  for (const preset of named) if (preset?.harness) return preset.harness;
  return null;
}

/** The problems a choice of presets meets: the file's, and each chosen preset's. */
export function presetProblems(presets: Presets | null, seat: string, project: string): PresetProblem[] {
  const chosen = new Set([seat ? `seat.${seat}` : "", project ? `project.${project}` : ""]);
  return (presets?.problems ?? []).filter((item) => item.where === "file" || chosen.has(item.where));
}

// --- which fields the user changed -----------------------------------------------------

function same(a: FormValue | undefined, b: FormValue | undefined): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

/** `touched` with every field whose value in `now` differs from `baseline` added. */
export function markTouched(touched: ReadonlySet<string>, baseline: Record<string, FormValue>,
                            now: Record<string, FormValue>): Set<string> {
  const out = new Set(touched);
  for (const name of Object.keys(now)) if (!same(now[name], baseline[name])) out.add(name);
  return out;
}

/** What the form showed first, after it drew new fields: a field already touched keeps
 * its first value, and every other reads as the form now shows it. */
export function rebase(baseline: Record<string, FormValue>, now: Record<string, FormValue>,
                       touched: ReadonlySet<string>): Record<string, FormValue> {
  const out: Record<string, FormValue> = {};
  for (const name of Object.keys(now)) out[name] = touched.has(name) && name in baseline ? baseline[name] : now[name];
  return out;
}

/** A value a launch request may carry: one string, or a list's items. */
export type Sent = string | string[];

/**
 * One form value as the page server reads it, or null to leave the field out. A switch
 * with an optional value is `false` when off and `true` when on with no value. An empty
 * text field is left out: the request has no spelling for "unset", so the preset's value
 * or the default stands.
 */
export function sentValue(field: FieldDescriptor | undefined, value: FormValue): Sent | null {
  if (Array.isArray(value)) return [...value];
  if (field?.optional_value === true) {
    if (value === null) return "false";
    return value === "" ? "true" : value;
  }
  return value === null || value === "" ? null : value;
}

/**
 * The `values` a launch request sends: only the fields the user changed, each as the
 * page server reads it. `harness` is sent too when the user chose it, or when a changed
 * field is one only some harnesses have, so the server reads that field against the
 * harness the form showed; otherwise the server takes the preset's harness or the
 * user's default.
 */
export function requestValues(values: Record<string, FormValue>, touched: ReadonlySet<string>,
                              fields: readonly FieldDescriptor[], harness: string,
                              shared: ReadonlySet<string>): Record<string, Sent> {
  const byName = new Map(fields.map((field) => [field.name, field]));
  const out: Record<string, Sent> = {};
  let own = false;
  for (const name of Object.keys(values)) {
    if (!touched.has(name)) continue;
    const sent = sentValue(byName.get(name), values[name]);
    if (sent === null) continue;
    out[name] = sent;
    if (!shared.has(name)) own = true;
  }
  if (own && !(HARNESS in out)) out[HARNESS] = harness;
  return out;
}

/** A launch as `/api/preview` and `/api/launch` take it. */
export interface LaunchBody {
  seat?: string;
  project_preset?: string;
  values: Record<string, Sent>;
  off_posture?: boolean;
}

export function launchBody(seat: string, project: string, values: Record<string, Sent>,
                           offPosture: boolean): LaunchBody {
  const body: LaunchBody = { values };
  if (seat) body.seat = seat;
  if (project) body.project_preset = project;
  if (offPosture) body.off_posture = true;
  return body;
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
    not_applied: texts(data.not_applied), warnings: texts(data.warnings),
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

/** Where a started launch or resume stands, read from its status path. */
export type Followed =
  | { kind: "row"; row: LaunchRow }
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
    if (row !== undefined) return { kind: "row", row: row as LaunchRow };
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

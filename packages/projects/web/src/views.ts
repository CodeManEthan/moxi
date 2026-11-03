// What projects' page and components show for each state of their data, worked out with
// no DOM so the Node tests can read it. Every string here goes into the page as text.

import { saidOf } from "./feed.ts";
import type { Failure, Kind, Kinds, One, Project, Projects, Written } from "./feed.ts";

/** A component's data as its `Feed` holds it. */
export interface FeedState<V> {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  value: V | null;
}

/** The kind a project has when the add form names none, as the verb takes it. */
export const DEFAULT_KIND = "project";

/** What a request's failure shows: the answer's own line when it gave one. */
export function failureLine(failure: Failure): string {
  return failure.said ?? failure.why;
}

// -- the picker -----------------------------------------------------------------------------

/** One project the picker offers: its key, and its root, which is only shown. */
export interface Choice {
  key: string;
  root: string;
}

/** The picker as it is drawn. */
export interface PickerView {
  /** `loading`, `up` (projects to choose from), `empty`, `failed` (the read failed, the
   * last list read is kept), or `gone` (a 404: projects went away; nothing is drawn). */
  state: "loading" | "up" | "empty" | "failed" | "gone";
  choices: Choice[];
  /** With `empty` or `failed`, the line to show. */
  line: string | null;
}

/** The line for a list with no project, of `kind` when the picker is filtered. */
export function emptyLine(kind: string | null): string {
  return kind ? `No project has the kind ${kind}.` : "No project is listed yet.";
}

/** The picker for `kind` (null for every kind). */
export function pickerView(data: FeedState<Projects>, kind: string | null): PickerView {
  const choices = (data.value?.projects ?? []).map(({ key, root }) => ({ key, root }));
  if (data.gone) return { state: "gone", choices: [], line: null };
  if (data.failure !== null) return { state: "failed", choices, line: failureLine(data.failure) };
  if (data.loading || data.value === null) return { state: "loading", choices: [], line: null };
  if (choices.length === 0) return { state: "empty", choices, line: emptyLine(kind) };
  return { state: "up", choices, line: null };
}

/** The picker's query: `api/projects`, with the kind when it is filtered. */
export function pickerPath(kind: string | null): string {
  return kind ? `api/projects?kind=${encodeURIComponent(kind)}` : "api/projects";
}

/** The detail of the event the picker sends its host for a choice: the key, and nothing
 * else. A host that needs the root reads it on its own server. */
export function pickedDetail(key: string): { key: string } {
  return { key };
}

// -- the card -------------------------------------------------------------------------------

/** One line of the card: its label and its value as text. */
export interface Field {
  name: string;
  label: string;
  value: string;
}

/** The card as it is drawn. */
export interface CardView {
  /** `loading`, `up`, `refused` (the page server refused the read: no such key, or a
   * store it can't read; `line` says which), `failed` (the request failed; the last
   * project read is kept), or `gone` (projects went away; nothing is drawn). */
  state: "loading" | "up" | "refused" | "failed" | "gone";
  /** The project's key as stored, else the key the card was given. */
  key: string;
  fields: Field[];
  line: string | null;
}

/** The fields the card shows for `project`. */
export function cardFields(project: Project): Field[] {
  return [
    { name: "kind", label: "Kind", value: project.kind },
    { name: "root", label: "Root", value: project.root },
    { name: "lead", label: "Lead", value: project.lead ?? "none" },
    { name: "agents", label: "Associated agents",
      value: project.associated.length ? project.associated.join(", ") : "none" },
  ];
}

/** The card for `key`. */
export function cardView(data: FeedState<One>, key: string): CardView {
  const project = data.value?.project ?? null;
  const base = { key: project?.key ?? key, fields: project ? cardFields(project) : [], line: null };
  if (data.gone) return { ...base, state: "gone", fields: [] };
  if (data.failure !== null) {
    const line = failureLine(data.failure);
    // A refusal is the server's word on this key now: what was read before no longer holds.
    if (data.failure.status === 422) return { key, state: "refused", fields: [], line };
    return { ...base, state: "failed", line };
  }
  if (data.loading || project === null) return { ...base, state: "loading" };
  return { ...base, state: "up" };
}

/** The card's query. */
export function cardPath(key: string): string {
  return `api/project?key=${encodeURIComponent(key)}`;
}

// -- the add form ---------------------------------------------------------------------------

/** What the add form holds, as typed. */
export interface AddForm {
  key: string;
  root: string;
  kind: string;
  lead: string;
  notesRoot: string;
  create: boolean;
}

/** The kind the form starts with: `wanted` when it is one of the kinds, else the default
 * kind when listed, else the first. Null while no kind is known. */
export function startKind(kinds: Kinds | null, wanted: string | null): string | null {
  const names = (kinds?.found ?? []).map((kind) => kind.name);
  if (wanted && names.includes(wanted)) return wanted;
  if (names.includes(DEFAULT_KIND)) return DEFAULT_KIND;
  return names[0] ?? null;
}

/** The kind named `name`, if it is listed. */
export function kindOf(kinds: Kinds | null, name: string): Kind | null {
  return kinds?.found.find((kind) => kind.name === name) ?? null;
}

/** Whether the form offers "create" for kind `name`: not for a kind whose mint is
 * `refuse`, and not while the kinds are unknown. */
export function createShown(kinds: Kinds | null, name: string): boolean {
  const kind = kindOf(kinds, name);
  return kind !== null && kind.mint !== "refuse";
}

/** The line under "create": what it makes for kind `name`. */
export function createLine(kinds: Kinds | null, name: string): string | null {
  const mint = kindOf(kinds, name)?.mint;
  if (mint === "git") return "Makes the directory, with any missing parents, and runs git init in it.";
  if (mint === "dir") return "Makes the directory, with any missing parents; no git.";
  return null;
}

/** The kind's line in the kind menu: its name, and the module that adds it when that
 * isn't projects. */
export function kindLabel(kind: Kind): string {
  return kind.module === "projects" ? kind.name : `${kind.name} (${kind.module})`;
}

/** One line for each contribution that won't load or kind that is blocked. While any is
 * listed, an add is refused until it is fixed or uninstalled. */
export function brokenLines(kinds: Kinds | null): string[] {
  return (kinds?.broken ?? []).map(({ module, reason }) => `${module}: ${reason}`);
}

/** The body of `/api/add` for `form`. An empty field is left out, so the verb's default
 * holds (a module may default the root); the notes root goes only while notes is
 * installed, and create only while the kind offers it. Every value is sent as typed but
 * for the key's and names' surrounding spaces. */
export function addBody(form: AddForm, { kinds, notes }: { kinds: Kinds | null; notes: boolean }):
    Record<string, string | boolean> {
  const body: Record<string, string | boolean> = { key: form.key.trim() };
  if (form.root !== "") body.root = form.root;
  if (form.kind !== "") body.kind = form.kind;
  if (form.lead.trim() !== "") body.lead = form.lead.trim();
  if (notes && form.notesRoot !== "") body.notes_root = form.notesRoot;
  if (form.create && createShown(kinds, form.kind)) body.create = true;
  return body;
}

/** The line for an act whose answer was lost. */
export const MAY_HAVE_ACTED = "No answer came back, so projects may have acted; the list has been read again.";
/** Its second line for an add with create. */
export const MAY_EXIST = "The directory may exist now. Adding again refuses if the first add finished, "
  + "and makes the project in an empty directory left behind if it did not.";
/** The line for an act the shell never sent. */
export const NOT_SENT = "projects' page server didn't get the request; nothing was done.";

/** What an act's answer came to. */
export type Acted<V> =
  | { outcome: "done"; value: V }
  | { outcome: "refused"; code: number; text: string }
  | { outcome: "unknown"; text: string };

function proxyError(data: unknown): string | null {
  if (typeof data !== "object" || data === null) return null;
  const error = (data as Record<string, unknown>).error;
  return typeof error === "string" ? error : null;
}

/**
 * What a plain act's answer came to: a 200 of the route's shape is done; the module's 4xx
 * is refused with its own line; a 503 and the shell's 502 `start` or `unreachable` are
 * refused, since the act was not run; and anything else (no answer, the shell's 502
 * `upstream` or 504, a 500, a 200 that can't be read) is unknown, since projects may
 * have acted.
 */
export function actedOf<V>(status: number, data: unknown, parse: (data: unknown) => V | null): Acted<V> {
  if (status === 200) {
    const value = parse(data);
    if (value !== null) return { outcome: "done", value };
    return { outcome: "unknown", text: MAY_HAVE_ACTED };
  }
  if (status >= 400 && status < 500) {
    return { outcome: "refused", code: status, text: saidOf(data) ?? `projects' page server refused it (${status})` };
  }
  if (status === 503) {
    if (proxyError(data) === "stopping") return { outcome: "refused", code: status, text: NOT_SENT };
    return { outcome: "refused", code: status, text: "projects' page server was too busy to take it; try again" };
  }
  if (status === 502) {
    const error = proxyError(data);
    if (error === "start" || error === "unreachable") return { outcome: "refused", code: status, text: NOT_SENT };
  }
  return { outcome: "unknown", text: MAY_HAVE_ACTED };
}

/** What the add form says after an act. */
export interface AddSaid {
  tone: "ok" | "error" | "warn";
  lines: string[];
}

/** The add form's lines for `acted`, an add of `form` with create ticked or not. */
export function addSaid(acted: Acted<Written>, created: boolean): AddSaid {
  if (acted.outcome === "done") {
    const { project, notes } = acted.value;
    return { tone: "ok", lines: [`Added ${project.key} at ${project.root}.`, ...notes] };
  }
  if (acted.outcome === "refused") return { tone: "error", lines: [acted.text] };
  return { tone: "warn", lines: created ? [acted.text, MAY_EXIST] : [acted.text] };
}

/** An empty form, with `kind`. */
export function emptyForm(kind: string): AddForm {
  return { key: "", root: "", kind, lead: "", notesRoot: "", create: false };
}

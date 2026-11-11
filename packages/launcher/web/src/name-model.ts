// What the session name in the Options panel holds, worked out with no DOM so the Node tests
// can drive it. prompt.ts draws it.
//
// Seat > Name & notes > Session name shows the name a launch would take now, as `/api/name`
// renders it from the template of the project the line names or the folder is in, else the one
// for every launch, for the seat, the place, the model and the comms name the piece holds; it is
// read again whenever one of those changes, so the name follows them. Under it, one dim sentence
// says whose template it is, as the answer names it, and what it follows. A name typed over it
// is the row's value, a change like any other, and the sentence says so with `Use the
// template`, which lets go of it.
//
// The caret opens the template's editor: the pieces in the template, each taken out by a
// press, and the pieces it lacks to add; the date's format; the template as written, typed
// into freely; whether it is kept for the project whose template it was opened on (else the
// line's project) or for every project. Each edit is rendered unsaved by
// `/api/name?template=`, which says why the page server would refuse it, and what saving it
// writes. The save goes through the shell's own settings route, which refuses a key the shell
// was started with locked, in its own words.
//
// Every string here goes into the page as text.

import type { ApiResult, FieldDescriptor } from "/m/ui/client.js";

type Fields = Record<string, unknown>;

function isTable(value: unknown): value is Fields {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/** The form's row that holds a name typed for one launch. */
export const NAME_ROW = "name";

/** The pieces a template is made of, in the order the add list offers them. */
export const PIECES = ["project", "agent", "model", "identity", "scope", "instance", "date", "time"] as const;
export type Piece = (typeof PIECES)[number];

/** The date's formats the editor offers; the first is the shipped template's. */
export const DATE_FORMATS: readonly string[] = ["mmm-dd hh:mm", "mmm-dd", "yyyy-mm-dd", "yyyymmdd", "yyyy-mm-dd hh:mm"];

/** The words of the editor and of the note under the name. */
export const TYPED = "Typed for this launch.";
export const USE_TEMPLATE = "Use the template";
export const PIECES_WORD = "Pieces";
export const ADD = "add";
export const DATE_WORD = "Date";
export const WRITTEN = "Written";
export const KEEP_FOR = "Keep for";
export const EVERY_PROJECT = "every project";
export const CANCEL = "Cancel";
export const SAVE = "Save template";

/** Where a template is kept: a project's own entry, or the one for every launch. */
export type Keep = "project" | "global";

/** A template being edited, where saving it keeps it, and the project it may be kept for, ""
 * for none. */
export interface Draft {
  template: string;
  keep: Keep;
  project: string;
}

/** What a name is rendered for: each "" when the launch has none. */
export interface NameContext {
  seat: string;
  project: string;
  cwd: string;
  model: string;
  identity: string;
  scope: string;
  agent: string;
  harness: string;
}

/** Why the page server would refuse a template, or can't read the settings. */
export interface NameRefusal {
  problem: string;
  hint: string;
}

/** What saving a template writes, as the shell's settings route takes it. */
export interface NameSave {
  key: string;
  value: string;
}

/** `/api/name`'s answer: the name (null when none is rendered), the template it was rendered
 * from, whose it is and the key of its project (null for the one for every launch), why the
 * template is refused, what saving it writes; `failed` is why the read gave nothing, "" when
 * it did. */
export interface Named {
  name: string | null;
  template: string;
  from: Keep;
  project: string | null;
  refused: NameRefusal | null;
  save: NameSave | null;
  failed: string;
}

/** The query `/api/name` takes for `context`, and for a template being edited when `draft` is
 * given; a template is kept for a project only when the draft names one, and that project is
 * the one asked for. */
export function nameQuery(context: NameContext, draft: Draft | null = null): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(context)) if (value !== "") query.set(key, value);
  if (draft !== null) {
    query.set("template", draft.template);
    if (draft.keep === "project" && draft.project !== "") {
      query.set("project", draft.project);
      query.set("keep", "project");
    }
  }
  return query.toString();
}

/** `/api/name`'s answer, or why there is none: `why` names a status that is not 200. */
export function parseName(data: unknown, why: string): Named {
  const failed = (said: string): Named => ({ name: null, template: "", from: "global", project: null, refused: null,
                                              save: null, failed: said });
  if (!isTable(data)) return failed(why);
  if (data.state !== "up") return failed(text(data.detail) ?? why);
  const refused = isTable(data.refused) ? { problem: text(data.refused.problem) ?? "", hint: text(data.refused.hint) ?? "" }
    : null;
  const save = isTable(data.save) && typeof data.save.key === "string" && typeof data.save.value === "string"
    ? { key: data.save.key, value: data.save.value } : null;
  return { name: text(data.name), template: text(data.template) ?? "", from: data.from === "project" ? "project" : "global",
           project: text(data.project), refused, save: refused === null ? save : null, failed: "" };
}

/** The project whose template the name was rendered from: the answer's, else the line's `project`;
 * "" for none. */
export function templateProject(named: Named | null, project: string): string {
  return named?.project ?? project;
}

/** The template the editor opens on: the one the name was rendered from, kept for its project when
 * the answer says it is that project's. */
export function draftOf(named: Named | null, project: string): Draft {
  const owner = templateProject(named, project);
  return { template: named?.template ?? "", keep: named?.from === "project" && owner !== "" ? "project" : "global",
           project: owner };
}

// --- the note under the name ---------------------------------------------------------------

const FOLLOWS: Readonly<Record<Piece, string>> = {
  project: "the project", agent: "the seat", model: "the model", identity: "the comms name", scope: "the comms scope",
  instance: "the launches running", date: "the clock", time: "the clock",
};

function joined(words: readonly string[]): string {
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** The dim sentence under a name rendered from a template: whose template it is (the answer's
 * project, else the line's `project`), what the name follows, and that typing over it names this
 * launch. */
export function nameNote(named: Named, project: string): string {
  const owner = templateProject(named, project);
  const whose = named.from === "project" && owner !== "" ? `From ${owner}'s template.`
    : "From the template for every launch.";
  const follows = [...new Set(tokens(named.template).map((token) => FOLLOWS[token.piece]))];
  const what = follows.length === 0 ? "It is the same for every launch" : `It follows ${joined(follows)}`;
  return `${whose} ${what}; type over it to name this launch yourself.`;
}

// --- the pieces ----------------------------------------------------------------------------

const PIECE = /<(project|agent|model|identity|scope|instance|date(?::[^<>]*)?|time(?::[^<>]*)?)>/g;

/** One piece in a template: which, and where it is written. */
export interface Token {
  piece: Piece;
  start: number;
  end: number;
}

/** The pieces a template holds, in the order written. */
export function tokens(template: string): Token[] {
  return [...template.matchAll(PIECE)].map((hit) => ({
    piece: hit[1].split(":")[0] as Piece, start: hit.index ?? 0, end: (hit.index ?? 0) + hit[0].length,
  }));
}

/** The template with its `at`th piece taken out, with the text between it and the piece before
 * it, or for the first piece the text after it. */
export function withoutPiece(template: string, at: number): string {
  const found = tokens(template);
  const token = found[at];
  if (token === undefined) return template;
  if (at > 0) return template.slice(0, found[at - 1].end) + template.slice(token.end);
  const next = found[at + 1];
  return template.slice(0, token.start) + template.slice(next === undefined ? token.end : next.start);
}

/** The template with `piece` added at its end, after a dash when it holds something; the date
 * comes with the shipped format. */
export function withPiece(template: string, piece: Piece): string {
  const written = piece === "date" ? `<date:${DATE_FORMATS[0]}>` : `<${piece}>`;
  return template === "" ? written : `${template}-${written}`;
}

/** Whether this install draws a row of the form: shown, and its module there. */
function offered(fields: readonly FieldDescriptor[], name: string): boolean {
  const field = fields.find((row) => row.name === name) as (FieldDescriptor & { shown?: boolean }) | undefined;
  return field !== undefined && field.shown !== false && field.needs_absent !== true;
}

/** The pieces the add list offers: those the template lacks, less the comms name and its scope
 * on an install without comms, and the number among live launches without slots. */
export function addList(fields: readonly FieldDescriptor[], template: string): Piece[] {
  const held = new Set(tokens(template).map((token) => token.piece));
  const comms = offered(fields, "identity");
  const slots = offered(fields, "slot");
  return PIECES.filter((piece) => !held.has(piece) && (comms || (piece !== "identity" && piece !== "scope"))
    && (slots || piece !== "instance"));
}

/** Whether `Keep for` is drawn: projects is there and the template has a project to be kept for. */
export function keepShown(withProjects: boolean, project: string): boolean {
  return withProjects && project !== "";
}

// --- the date ------------------------------------------------------------------------------

const DATE = /<date(?::([^<>]*))?>/;

/** The format of the template's first date piece, "" for a bare `<date>`; null with none. */
export function dateFormat(template: string): string | null {
  const hit = DATE.exec(template);
  return hit === null ? null : hit[1] ?? "";
}

/** The template with its first date piece in `format`. */
export function withDate(template: string, format: string): string {
  return template.replace(DATE, format === "" ? "<date>" : `<date:${format}>`);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const TOKEN = /yyyy|yy|mmm|mm|dd|hh|ss/gi;

/** A date format as the name would show it at `now`, read as the page server reads one: `mm` is
 * the month until an `hh` has come before it, then the minutes. A bare `<date>` is `yyyymmdd`;
 * a format holding `%` is shown as written. */
export function dateSample(format: string, now: Date): string {
  if (format.includes("%")) return format;
  const two = (value: number): string => String(value).padStart(2, "0");
  let hours = false;
  return (format === "" ? "yyyymmdd" : format).replace(TOKEN, (found) => {
    const token = found.toLowerCase();
    if (token === "hh") hours = true;
    switch (token) {
      case "yyyy": return String(now.getFullYear());
      case "yy": return two(now.getFullYear() % 100);
      case "mmm": return MONTHS[now.getMonth()];
      case "mm": return hours ? two(now.getMinutes()) : two(now.getMonth() + 1);
      case "dd": return two(now.getDate());
      case "hh": return two(now.getHours());
      default: return two(now.getSeconds());
    }
  });
}

// --- the save ------------------------------------------------------------------------------

/** The body the shell's settings route takes to save a template. */
export function saveBody(save: NameSave): { module: string; key: string; value: string } {
  return { module: "launcher", key: save.key, value: save.value };
}

/** What the page says of a save that did not write, in the shell's words; null for one that did. */
export function saveSaid(answer: ApiResult): string | null {
  const data = isTable(answer.data) ? answer.data : {};
  if (answer.status === 200 && data.ok === true) return null;
  const said = text(data.refused) ?? text(data.conflict);
  if (said !== null && said !== "") return `Not saved: ${said}`;
  return answer.status === 0 ? "Not saved: no answer from the shell" : `Not saved: the shell answered ${answer.status}`;
}

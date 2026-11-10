// What the launcher's piece on Home shows and sets, worked out with no DOM so the Node
// tests can drive it. prompt.ts draws it.
//
// The piece is a launch form with a few rows drawn: the box is the row `brief`, the line
// above it is the place (`project` or `cwd`), and the chip is `harness`, `model` and
// `effort`. Every other row keeps the value `/api/form` gives it.
//
// Whether projects is there is one value, `withProjects`, worked out once when the piece
// opens. Every project thing hangs on it: the line's face, how a place is drawn and what
// a pick of it sets, the list of all projects and the first open. While it is false a
// place is always a folder and nothing names a project.
//
// The button says where the launch runs and nothing else decides it: the request always
// carries `target`, and `slot` only for a slot picked by its number. A launch in the slot
// the button names by default sends no slot, so the launcher takes the lowest free one when
// it starts, and a number that went stale between two reads can only mislabel the button.
//
// Every string here goes into the page as text.

import type { ApiResult, FieldDescriptor, FormValue } from "/m/ui/client.js";

import type { LaunchRow } from "./feed.ts";
import { followedOf, HARNESS, launchBody, leaveOutChoice, NO_PRESET, outcomeOf, parsePreview, requestOf, shownValue } from "./panel.ts";
import type { Form, LaunchBody, Preview, Status } from "./panel.ts";

type Fields = Record<string, unknown>;

function isTable(value: unknown): value is Fields {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// --- the places ----------------------------------------------------------------------------

/** One place launched in before: the project's key when the launch had one, the folder it
 * started in, and when. */
export interface Place {
  project_key: string | null;
  cwd: string;
  at: string;
}

/** The value of `/api/places`: the places, newest first, whether the page server would
 * take a project for a launch's place, and the user's home folder as the page server sees
 * it ("" when the answer names none). */
export interface Places {
  projects: boolean;
  home: string;
  places: Place[];
}

/** `/api/places`'s value, or null when it isn't one or says it could not be read. */
export function parsePlaces(data: unknown): Places | null {
  if (!isTable(data) || data.state !== "up" || !Array.isArray(data.places)) return null;
  const places: Place[] = [];
  for (const row of data.places) {
    if (!isTable(row) || typeof row.cwd !== "string" || row.cwd === "") continue;
    places.push({ project_key: typeof row.project_key === "string" && row.project_key ? row.project_key : null,
                  cwd: row.cwd, at: typeof row.at === "string" ? row.at : "" });
  }
  return { projects: data.projects === true, home: typeof data.home === "string" ? data.home : "", places };
}

/** Whether projects is there: the page server would take a project, and the shell has
 * projects' picker to place. */
export function withProjects(places: Places | null, picker: boolean): boolean {
  return places !== null && places.projects && picker;
}

/** The keys of `/api/projects`' answer, or null when it isn't one. */
export function projectKeys(data: unknown): string[] | null {
  if (!isTable(data) || !Array.isArray(data.projects)) return null;
  return data.projects.filter((row): row is Fields => isTable(row) && typeof row.key === "string" && row.key !== "")
    .map((row) => row.key as string);
}

// --- the pick ------------------------------------------------------------------------------

/** Where the launch starts: a project by its key, a folder by its path, or `choose` while
 * there are several projects and none is picked yet. */
export type Pick =
  | { kind: "project"; key: string }
  | { kind: "folder"; path: string }
  | { kind: "choose" };

/** The home folder, as a launch takes it. */
export const HOME = "~";
const HOME_PICK: Pick = { kind: "folder", path: HOME };

/** What a pick of a place sets: its project with projects, when it has one, else its folder. */
export function pickOf(place: Place, projects: boolean): Pick {
  return projects && place.project_key !== null ? { kind: "project", key: place.project_key }
    : { kind: "folder", path: place.cwd };
}

/** The pick a Home that never launched opens on. With projects: the one project there is,
 * `choose` with several, the home folder with none or when they could not be read (null).
 * Without: the home folder. */
export function firstOpen(projects: boolean, keys: readonly string[] | null): Pick {
  if (!projects || keys === null || keys.length === 0) return HOME_PICK;
  return keys.length === 1 ? { kind: "project", key: keys[0] } : { kind: "choose" };
}

/** A request to a module's page, as the client's `api` reads one. */
export type Read = (module: string, path: string) => Promise<ApiResult>;

/** What the line above the box opens with. */
export interface Where {
  withProjects: boolean;
  /** The user's home folder, "" when the places could not be read. */
  home: string;
  places: Place[];
  pick: Pick;
}

/**
 * Reads the places and works out the line: the newest place when there is one, else the
 * first open, for which projects' own list is read once when projects is there. `picker`
 * says whether the shell lists projects' picker. A places read that fails opens on the
 * home folder, with no projects.
 */
export async function openWhere(read: Read, picker: boolean): Promise<Where> {
  const answer = await read("launcher", "api/places");
  const places = answer.status === 200 ? parsePlaces(answer.data) : null;
  const projects = withProjects(places, picker);
  const rows = places?.places ?? [];
  const home = places?.home ?? "";
  if (rows.length > 0) return { withProjects: projects, home, places: rows, pick: pickOf(rows[0], projects) };
  if (!projects) return { withProjects: false, home, places: rows, pick: HOME_PICK };
  const listed = await read("projects", "api/projects");
  return { withProjects: true, home, places: rows,
           pick: firstOpen(true, listed.status === 200 ? projectKeys(listed.data) : null) };
}

/** Whether the launch has a place: false only while a project is still to be chosen. */
export function placed(pick: Pick): boolean {
  return pick.kind !== "choose";
}

/** The place rows a pick sets: the one it names, with the other emptied. */
export function pickValues(pick: Pick): Record<string, FormValue> {
  return { project: pick.kind === "project" ? pick.key : null, cwd: pick.kind === "folder" ? pick.path : null };
}

// --- folders -------------------------------------------------------------------------------

function bare(path: string): string {
  return path.length > 1 ? path.replace(/\/+$/, "") || "/" : path;
}

/** A path with the user's home folder written `~`. `home` is that folder as the page
 * server names it; a path outside it, and every path while `home` is "", comes back as it
 * is, less its closing slashes. */
export function tilde(path: string, home: string): string {
  const said = bare(path);
  const root = bare(home);
  if (root === "" || root === "/") return said;
  if (said === root) return HOME;
  return said.startsWith(`${root}/`) ? `${HOME}${said.slice(root.length)}` : said;
}

/** A folder as the page draws it: the path before its last segment, which is drawn dim, and
 * the folder's own name. */
export interface FolderFace {
  dim: string;
  name: string;
}

/** The longest folder face drawn whole, in characters. */
export const FACE_MAX = 56;

/**
 * A folder's face: home as `~`, split before the last segment. A face longer than `max` is
 * cut in the dim part only, from its middle, keeping the first segment and as many of the
 * segments next to the name as fit: `~/…/deep/name`. The name is never cut.
 */
export function folderFace(path: string, home: string, max = FACE_MAX): FolderFace {
  const shown = tilde(path, home);
  const at = shown.lastIndexOf("/");
  if (at < 0 || shown === "/") return { dim: "", name: shown };
  const dim = shown.slice(0, at + 1);
  const name = shown.slice(at + 1);
  if (shown.length <= max) return { dim, name };
  const parts = dim.slice(0, -1).split("/");
  // An absolute path's first segment is the one after the root.
  const head = parts[0] === "" ? `/${parts[1] ?? ""}` : parts[0];
  const rest = parts.slice(parts[0] === "" ? 2 : 1);
  if (rest.length === 0) return { dim, name };
  const kept: string[] = [];
  let length = head.length + "/…/".length + name.length;
  while (rest.length > 1) {
    const next = rest[rest.length - 1];
    if (length + next.length + 1 > max) break;
    kept.unshift(rest.pop() as string);
    length += next.length + 1;
  }
  return { dim: `${head}/…/${kept.map((part) => `${part}/`).join("")}`, name };
}

/** A typed folder path as a launch takes it, or null: it must be absolute or start with `~`. */
export function typedFolder(said: string): string | null {
  const path = said.trim();
  return path.startsWith("/") || path.startsWith("~") ? path : null;
}

// --- the line and its menu -----------------------------------------------------------------

/** The line above the box: the word, then the dim part and the name of what is picked. */
export interface WhereFace extends FolderFace {
  word: "Project" | "Folder";
}

export function whereFace(pick: Pick, home: string): WhereFace {
  if (pick.kind === "project") return { word: "Project", dim: "", name: pick.key };
  if (pick.kind === "choose") return { word: "Project", dim: "", name: "choose" };
  return { word: "Folder", ...folderFace(pick.path, home) };
}

function pickKey(pick: Pick, home: string): string {
  return pick.kind === "project" ? `project ${pick.key}`
    : pick.kind === "folder" ? `folder ${tilde(pick.path, home)}` : "";
}

/** Whether two picks name the same place. */
export function samePick(a: Pick, b: Pick, home: string): boolean {
  return pickKey(a, home) !== "" && pickKey(a, home) === pickKey(b, home);
}

/** A time as the browser's locale writes it. */
export type Clock = (at: Date) => string;

const HOUR: Clock = (at) => at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const DAY: Clock = (at) => at.toLocaleDateString([], { month: "short", day: "numeric" });

/** When a place was last launched in: the time of day for a launch on `now`'s day, else
 * the day. Empty for a time that can't be read. */
export function placeTime(at: string, now: Date, hour: Clock = HOUR, day: Clock = DAY): string {
  const then = new Date(at);
  if (at === "" || Number.isNaN(then.getTime())) return "";
  const today = then.getFullYear() === now.getFullYear() && then.getMonth() === now.getMonth()
    && then.getDate() === now.getDate();
  return today ? hour(then) : day(then);
}

/** The longest folder a row of the menu draws whole, in characters. */
export const ROW_MAX = 40;

/** One place in the line's menu. */
export interface PlaceRow extends FolderFace {
  pick: Pick;
  time: string;
  /** The place picked now, which carries the tick. */
  current: boolean;
}

/**
 * The menu's places, newest first. With projects a place that has a project is its key
 * alone, since its folder may lie inside the project and the launch starts at the
 * project's root; any other place, and every place without projects, is its folder. Two
 * places drawn the same are one row, the newer.
 */
export function placeRows(places: readonly Place[], projects: boolean, pick: Pick, home: string, now: Date,
                          hour: Clock = HOUR, day: Clock = DAY): PlaceRow[] {
  const seen = new Set<string>();
  const out: PlaceRow[] = [];
  for (const place of places) {
    const mine = pickOf(place, projects);
    const key = pickKey(mine, home);
    if (seen.has(key)) continue;
    seen.add(key);
    const face = mine.kind === "project" ? { dim: "", name: mine.key } : folderFace(place.cwd, home, ROW_MAX);
    out.push({ ...face, pick: mine, time: placeTime(place.at, now, hour, day), current: samePick(mine, pick, home) });
  }
  return out;
}

// --- the form's values ---------------------------------------------------------------------

/**
 * What the piece's form holds, as the launcher's panel reads its own form: every row that
 * takes input with the value `/api/form` filled it with, and over those the rows set here
 * (`set`: the brief, the place, the model and the effort). A row this install shows off
 * sends nothing, set or not.
 */
export function formValues(fields: readonly FieldDescriptor[],
                           set: Readonly<Record<string, FormValue>>): Record<string, FormValue> {
  const out: Record<string, FormValue> = {};
  for (const field of fields) {
    if (field.needs_absent === true) continue;
    out[field.name] = field.name in set ? set[field.name] : shownValue(field);
  }
  return out;
}

/** One row's value as text, "" when it is empty. */
export function valueOf(fields: readonly FieldDescriptor[], set: Readonly<Record<string, FormValue>>,
                        name: string): string {
  const value = formValues(fields.filter((field) => field.name === name), set)[name];
  return typeof value === "string" ? value : "";
}

// --- the chip ------------------------------------------------------------------------------

/** The harnesses' names, by the row's value. */
export const HARNESS_NAMES: Readonly<Record<string, string>> = { claude: "Claude Code", codex: "Codex" };

/** A harness as the menu names it; one with no name here is drawn as its value. */
export function harnessName(value: string): string {
  return Object.hasOwn(HARNESS_NAMES, value) ? HARNESS_NAMES[value] : value;
}

/** What the chip says for a model left empty. */
export const DEFAULT_MODEL = "default model";

/** The chip's face: the harness, the model, and the effort when one is set. `effort` is
 * null for a harness with no effort row. */
export function chipFace(harness: string, model: string, effort: string | null): string {
  return [harness, model || DEFAULT_MODEL, ...(effort ? [effort] : [])].join(" · ");
}

/** One choice of the chip's menu. An empty `value` is the row's empty state. */
export interface ChipItem {
  value: string;
  label: string;
  current: boolean;
}

/** The chip's menu: three lists side by side. */
export interface ChipMenu {
  harness: ChipItem[];
  /** `Default`, then the names the row offers. */
  model: ChipItem[];
  /** The model set is none of the list's: a full name typed after `older…`. */
  typed: string;
  /** `Default` and the row's levels; null for a harness with no effort row. */
  effort: ChipItem[] | null;
}

/** The label of the choice that empties a row. */
export const DEFAULT_LABEL = "Default";

function choices(field: FieldDescriptor | undefined): string[] {
  const leave = leaveOutChoice(field);
  return (field?.values ?? []).filter((value) => value !== "" && value !== leave);
}

function offered(fields: readonly FieldDescriptor[], name: string): FieldDescriptor | undefined {
  return fields.find((field) => field.name === name && field.needs_absent !== true);
}

/** Whether the form's harness has an effort row. */
export function hasEffort(fields: readonly FieldDescriptor[]): boolean {
  return offered(fields, "effort") !== undefined;
}

/**
 * The chip's menu for the form's fields and the values set now. The harness list is the
 * row's values under their names. The model list leads with `Default`, the way back to the
 * empty value; a harness whose model row offers no names has `Default` alone, and the
 * field after `older…` takes a full name.
 */
export function chipMenu(fields: readonly FieldDescriptor[], harness: string, model: string,
                         effort: string): ChipMenu {
  const item = (value: string, now: string): ChipItem =>
    ({ value, label: value === "" ? DEFAULT_LABEL : value, current: value === now });
  const models = choices(offered(fields, "model"));
  return {
    harness: choices(fields.find((field) => field.name === HARNESS))
      .map((value) => ({ value, label: harnessName(value), current: value === harness })),
    model: ["", ...models].map((value) => item(value, model)),
    typed: model !== "" && !models.includes(model) ? model : "",
    effort: hasEffort(fields) ? ["", ...choices(offered(fields, "effort"))].map((value) => item(value, effort)) : null,
  };
}

// --- where it runs -------------------------------------------------------------------------

/** One slot of `/api/targets`. `taken` is what the session in it is called, when the launch
 * log knows it; `holder` and `until` (seconds since the epoch) are a live hold's. */
export interface Slot {
  number: number;
  state: string;
  launchable: boolean;
  holder: string | null;
  until: number | null;
  taken: string | null;
}

/** Where a launch runs: a slot (by number, or a new one with `slot` null), the user's own
 * terminal, or the background. `named` says the user picked that slot by its number, so
 * the launch asks for it; a slot that is only the default is not asked for. */
export type Run =
  | { target: "slot"; slot: number | null; named: boolean }
  | { target: "terminal" }
  | { target: "headless" };

/** The value of `/api/targets`: the slots in number order, and the choice a launch starts
 * with. `unread` says slots is there and its slots could not be read. */
export interface Targets {
  slots: Slot[];
  default: Run;
  unread: boolean;
}

/** What the button offers when the slots can't be used or read: no slot, and the command
 * to copy as the choice. */
export const NO_TARGETS: Targets = { slots: [], default: { target: "terminal" }, unread: false };

/** The same offer where slots is there and its slots could not be read. */
export const UNREAD_TARGETS: Targets = { ...NO_TARGETS, unread: true };

/** The line under the box while the slots could not be read. */
export const SLOTS_UNREAD = "The slots can't be read right now. Your terminal and Headless still work.";

/** `/api/targets`' value. Anything that isn't one, or says the read failed, reads as no
 * slots, unread where the answer says so. The default is a slot only where the answer
 * says a new one can be made. */
export function parseTargets(data: unknown): Targets {
  if (isTable(data) && data.unread === true) return UNREAD_TARGETS;
  if (!isTable(data) || data.state !== "up" || !Array.isArray(data.slots)) return NO_TARGETS;
  const slots: Slot[] = [];
  for (const row of data.slots) {
    if (!isTable(row) || typeof row.number !== "number" || !Number.isInteger(row.number)) continue;
    slots.push({
      number: row.number, state: typeof row.state === "string" ? row.state : "",
      launchable: row.launchable === true,
      holder: typeof row.holder === "string" && row.holder !== "" ? row.holder : null,
      until: typeof row.until === "number" ? row.until : null,
      taken: isTable(row.taken_by) && typeof row.taken_by.label === "string" ? row.taken_by.label : null,
    });
  }
  slots.sort((a, b) => a.number - b.number);
  const first = isTable(data.default) ? data.default : {};
  if (first.target !== "slot" || data.new_slot !== true) return { slots, default: { target: "terminal" }, unread: false };
  const free = slots.find((slot) => slot.launchable && slot.number === first.slot);
  return { slots, default: { target: "slot", slot: free ? free.number : null, named: false }, unread: false };
}

/** What the button offers after a request for `/api/targets` got no answer to read. The
 * slots are unread only if the reading before it (`last`) had slots, or had them unread:
 * a request that failed can't say whether slots is there. */
export function afterNoAnswer(last: Targets | null): Targets {
  return last !== null && (last.unread || last.default.target === "slot") ? UNREAD_TARGETS : NO_TARGETS;
}

function sameRun(a: Run, b: Run): boolean {
  return a.target === b.target && (a.target !== "slot" || (b.target === "slot" && a.slot === b.slot));
}

/** Where the launch runs now: the user's pick while the listing still offers it, else the
 * default. With no pick (null) it is always the default, so nothing carries over. */
export function runOf(targets: Targets, picked: Run | null): Run {
  if (picked === null) return targets.default;
  if (picked.target !== "slot") return picked;
  return targets.slots.some((slot) => slot.number === picked.slot && slot.launchable) ? picked : targets.default;
}

/** The button's words: the act and where it runs. */
export function buttonWords(run: Run): string {
  if (run.target === "terminal") return "Copy command";
  if (run.target === "headless") return "Launch headless";
  return run.slot === null ? "Launch in a new slot" : `Launch in slot ${run.slot}`;
}

/** One row of the button's menu. `run` is what a pick of it sets, null for a slot that
 * can't be launched in now. */
export interface RunRow {
  lead: string;
  words: string;
  run: Run | null;
  current: boolean;
}

/** The button's menu: the slots, then the choices that need none. */
export interface RunMenu {
  slots: RunRow[];
  others: RunRow[];
}

/** What a slot's row says: `free`, `taken · <label>`, `held for <holder> until <time>` or
 * `not running`. */
export function slotWords(slot: Slot, hour: Clock = HOUR): string {
  if (slot.state === "occupied") return slot.taken === null ? "taken" : `taken · ${slot.taken}`;
  if (slot.state === "missing") return "not running";
  if (slot.holder !== null) {
    return `held for ${slot.holder}${slot.until === null ? "" : ` until ${hour(new Date(slot.until * 1000))}`}`;
  }
  return slot.launchable ? "free" : "not free";
}

/**
 * The menu for the listing and the choice made now. Each slot in number order, clickable
 * while it can be launched in. `A new slot` is offered only while no slot is free: a
 * launch that names no slot takes the lowest free one, so with one free the row would
 * not do what it says. Then the terminal and the background, which need no slot.
 */
export function runMenu(targets: Targets, run: Run, hour: Clock = HOUR): RunMenu {
  const slots: RunRow[] = targets.slots.map((slot) => ({
    lead: `Slot ${slot.number}`, words: slotWords(slot, hour),
    run: slot.launchable ? { target: "slot", slot: slot.number, named: true } : null,
    current: run.target === "slot" && run.slot === slot.number,
  }));
  if (targets.default.target === "slot" && targets.default.slot === null) {
    slots.push({ lead: "A new slot", words: "", run: targets.default, current: sameRun(run, targets.default) });
  }
  return { slots, others: [
    { lead: "Your terminal", words: "copies the command for you to paste", run: { target: "terminal" },
      current: run.target === "terminal" },
    { lead: "Headless", words: "no terminal, runs in the background", run: { target: "headless" },
      current: run.target === "headless" },
  ] };
}

/** How old a read of `/api/targets` may be when the button is pressed, in milliseconds. */
export const TARGETS_FRESH_MS = 5000;

// --- the request ---------------------------------------------------------------------------

/** The rows the button sets, which no line under the box names. */
export const PLACED = ["target", "slot"] as const;

/** The rows whose text or file a launch sends as its session's first turn. */
export const FIRST_TURN = ["brief", "brief_file", "handoff"] as const;

/**
 * The launch as `/api/preview` and `/api/launch` take it: the whole form, as the launcher's
 * panel sends its own, with where it runs set from `run` alone. `target` always has its
 * value; `slot` has one only for a slot picked by its number, and is otherwise left out
 * where the form has the row. The box's text is the whole first turn: of the rows in
 * `FIRST_TURN` the request holds `brief` alone, with the box's text, and only while the box
 * has text. Every other such row the form has is left out, whatever the form filled it
 * with or withheld, so nothing the box does not show is sent as the first turn. Any other
 * text `/api/form` withheld, which the piece does not draw, is kept as the layers give it.
 */
export function launchRequest(fields: readonly FieldDescriptor[], form: Form | null,
                              set: Readonly<Record<string, FormValue>>, run: Run): LaunchBody {
  const values = formValues(fields, set);
  const slotted = "slot" in values;
  for (const name of PLACED) delete values[name];
  const box = typeof set.brief === "string" && set.brief !== "" ? set.brief : null;
  for (const name of FIRST_TURN) if (name in values) values[name] = name === "brief" ? box : null;
  const withheld = new Set((form?.rows ?? []).filter((row) => row.withheld).map((row) => row.name));
  const kept = new Set([...withheld].filter((name) => !(FIRST_TURN as readonly string[]).includes(name)));
  const request = requestOf(fields, values, withheld, kept);
  request.values.target = run.target;
  if (run.target === "slot" && run.named && run.slot !== null) request.values.slot = String(run.slot);
  else if (slotted) request.leave_out.push("slot");
  return launchBody(NO_PRESET, request, false);
}

// --- the launch, and the lines under the box -----------------------------------------------

/** One line under the box. `name` says which line it is, for the page to mark it; `dim`
 * is a note, `problem` what stopped the launch. `command` is a command drawn under the
 * line for the user to copy by hand. */
export interface Line {
  name: "starting" | "started" | "failed" | "refused" | "used" | "late" | "stopped" | "copied" | "not-copied"
    | "warning" | "left-out";
  tone: "plain" | "dim" | "problem";
  text: string;
  command?: string;
}

/**
 * A launch from the button, from the press to its end.
 * - `idle`: nothing under way; the button is back, with the lines of the last launch.
 * - `pressed`: the preview or the launch is being asked; the button is off.
 * - `starting`: a job runs the launch and its row is followed; the button is off.
 * - `late`: the row should have started by now; the button is back and the row is still
 *   followed, so a later start or failure replaces the line.
 */
export interface Flight {
  phase: "idle" | "pressed" | "starting" | "late";
  lines: Line[];
  /** The preview's notes, drawn dim under the lines. */
  notes: Line[];
  /** The status path followed while `starting` or `late`. */
  status: Status | null;
  /** The launch started, so the box is to be emptied. */
  started: boolean;
}

export const IDLE: Flight = { phase: "idle", lines: [], notes: [], status: null, started: false };

/** The press: the button goes off and the last launch's lines go. */
export const PRESSED: Flight = { ...IDLE, phase: "pressed" };

/** What the button shows while a launch is under way. */
export const STARTING = "Starting…";

/** Whether the button is off: from a press to the first end of its launch. */
export function inFlight(flight: Flight): boolean {
  return flight.phase === "pressed" || flight.phase === "starting";
}

/** Whether the launch's row is still read. */
export function following(flight: Flight): boolean {
  return flight.status !== null && (flight.phase === "starting" || flight.phase === "late");
}

function ended(lines: Line[], notes: Line[] = []): Flight {
  return { ...IDLE, lines, notes };
}

function sentence(problem: string, hint: string): string {
  const said = problem.replace(/[.\s]+$/, "");
  return hint ? `${said}. ${hint}` : said;
}

/** What stopped a launch before it ran, in the launcher's words: `<problem>. <hint>`. */
export function refusalWords(found: { problem: string; hint: string }): string {
  return sentence(found.problem, found.hint);
}

export const FAILED = "The launch failed: ";

function failed(reason: string, notes: Line[] = []): Flight {
  return ended([{ name: "failed", tone: "problem", text: `${FAILED}${reason}` }], notes);
}

/** The preview's notes: each warning, and the rows the launch leaves out. The rows the
 * button sets are never named, since the button already says where the launch runs. */
export function notesOf(preview: Preview): Line[] {
  const left = preview.left_out.filter((name) => !(PLACED as readonly string[]).includes(name));
  return [
    ...preview.warnings.map((text): Line => ({ name: "warning", tone: "dim", text })),
    ...(left.length ? [{ name: "left-out", tone: "dim", text: `Left out: ${left.join(", ")}` } as Line] : []),
  ];
}

/** What a preview's answer asks for next: the launch with its ticket, the command copied,
 * or nothing, with the lines that say why. */
export type Previewed =
  | { next: "launch"; ticket: string; notes: Line[] }
  | { next: "copy"; command: string; notes: Line[] }
  | { next: "end"; flight: Flight };

function unanswered(result: ApiResult): string {
  if (result.status === 0) return "no answer from the shell";
  const said = isTable(result.data) && typeof result.data.error === "string" ? `: ${result.data.error}` : "";
  return `the launcher's page server answered ${result.status}${said}`;
}

/** Reads `/api/preview`'s answer. A refused preview ends the launch with each refusal. */
export function previewed(result: ApiResult): Previewed {
  const preview = result.status === 200 ? parsePreview(result.data) : null;
  if (preview === null) return { next: "end", flight: failed(unanswered(result)) };
  if (preview.refused.length > 0) {
    return { next: "end",
             flight: ended(preview.refused.map((found) => ({ name: "refused", tone: "problem", text: refusalWords(found) }))) };
  }
  const notes = notesOf(preview);
  if (preview.command !== null) return { next: "copy", command: preview.command, notes };
  if (preview.ticket !== null) return { next: "launch", ticket: preview.ticket, notes };
  return { next: "end", flight: failed("the launcher's page server gave no ticket to launch with", notes) };
}

export const COPIED = "Copied. Paste it in your terminal.";
export const NOT_COPIED = "The browser did not let this page copy. Copy the command from here and paste it in your terminal:";

/** The end of a launch in the user's terminal: the command is copied, or shown to copy. */
export function copied(command: string, done: boolean, notes: Line[]): Flight {
  return ended([done ? { name: "copied", tone: "plain", text: COPIED }
    : { name: "not-copied", tone: "plain", text: NOT_COPIED, command }], notes);
}

/** The line while a job starts the launch: the slot when the button named one. */
export function startingWords(run: Run): string {
  return run.target === "slot" && run.slot !== null ? `Starting in slot ${run.slot}…` : STARTING;
}

/**
 * Reads `/api/launch`'s answer for a launch sent with `run`. 202 starts the follow. A
 * refusal ends it with the refusal's words, a ticket used already with the answer's own
 * text, and a terminal launch's answer with its command to copy (`copy`).
 */
export function launched(result: ApiResult, run: Run, notes: Line[]):
    { flight: Flight; copy: string | null } {
  const outcome = outcomeOf(result);
  switch (outcome.kind) {
    case "started":
      return { copy: null, flight: { phase: "starting", lines: [{ name: "starting", tone: "plain", text: startingWords(run) }], notes,
                                     status: outcome.status, started: false } };
    case "refused":
      if (outcome.command !== null) return { copy: outcome.command, flight: PRESSED };
      return { copy: null, flight: ended([{ name: "refused", tone: "problem", text: refusalWords(outcome.refusal) }], notes) };
    case "command":
      return { copy: outcome.command, flight: PRESSED };
    case "used":
      return { copy: null, flight: ended([{ name: "used", tone: "problem", text: outcome.text }], notes) };
    default:
      return { copy: null, flight: failed(outcome.text, notes) };
  }
}

/** The launch states that mean the session started. */
export const LAUNCHED: readonly string[] = ["started", "ready", "sending", "first-sent"];

export const LATE = "Not started yet, and it should have been by now. The Launcher page lists it.";
export const STOPPED = "Stopped watching this launch. The Launcher page lists it.";

/** The launcher's page as those two lines name it. */
export const LAUNCHER_PAGE = "Launcher page";

/** A line's text around the launcher's page's name, for the page to make the name a link;
 * null for a line that does not send the user there. */
export function aroundPage(line: Line): [string, string] | null {
  const at = line.name === "late" || line.name === "stopped" ? line.text.indexOf(LAUNCHER_PAGE) : -1;
  return at < 0 ? null : [line.text.slice(0, at), line.text.slice(at + LAUNCHER_PAGE.length)];
}

/** `Started in slot 2 · 19:06`, or `Started headless · 19:06`, from what the row records. */
export function startedWords(row: LaunchRow, hour: Clock = HOUR): string {
  const then = new Date(row.created_at);
  const at = Number.isNaN(then.getTime()) ? "" : ` · ${hour(then)}`;
  const where = row.target === "slot" ? (row.slot ? ` in slot ${row.slot}` : " in a slot")
    : row.target === "headless" ? " headless" : "";
  return `Started${where}${at}`;
}

/**
 * A followed launch after one read of its status path. A row that started ends the launch
 * and empties the box; a failed row, or a refusal met before the row was written, ends it
 * with the reason: the sentence recorded for the failure, else the row's reason word. A row that reads stale while it is still starting gives the button back
 * and is followed on. No row yet, or a read that failed, changes nothing.
 */
export function followed(flight: Flight, result: ApiResult, hour: Clock = HOUR): Flight {
  if (!following(flight)) return flight;
  const read = followedOf(result);
  if (read.kind === "refused") return failed(refusalWords(read.refusal), flight.notes);
  if (read.kind !== "row") return flight;
  const row = read.row;
  if (LAUNCHED.includes(row.state)) {
    return { ...ended([{ name: "started", tone: "plain", text: startedWords(row, hour) }], flight.notes), started: true };
  }
  if (row.state === "failed") {
    return failed(read.why || row.reason || "the launcher gave no reason", flight.notes);
  }
  if (row.stale === true && flight.phase === "starting") {
    return { ...flight, phase: "late", lines: [{ name: "late", tone: "plain", text: LATE }] };
  }
  return flight;
}

/** The end of the follow: the row never came, or never left its first state. */
export function stopped(flight: Flight): Flight {
  return following(flight) ? ended([{ name: "stopped", tone: "plain", text: STOPPED }], flight.notes) : flight;
}

/** An edit of the box: the lines of a launch that has ended go, and a late launch is no
 * longer followed. The lines of a launch still starting stay. */
export function edited(flight: Flight): Flight {
  return inFlight(flight) ? flight : IDLE;
}

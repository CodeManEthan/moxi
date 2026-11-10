// What the launcher's piece on Home shows and sets, worked out with no DOM so the Node
// tests can drive it. prompt.ts draws it.
//
// The piece is a launch form with a few rows drawn: the box is the row `brief` and the line
// above it is the place (`project` or `cwd`). Every other row keeps the value `/api/form`
// gives it.
//
// Whether projects is there is one value, `withProjects`, worked out once when the piece
// opens. Every project thing hangs on it: the line's face, how a place is drawn and what
// a pick of it sets, the list of all projects and the first open. While it is false a
// place is always a folder and nothing names a project.
//
// Every string here goes into the page as text.

import type { ApiResult, FieldDescriptor, FormValue } from "/m/ui/client.js";

import { shownValue } from "./panel.ts";

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

/** The value of `/api/places`: the places, newest first, and whether the page server would
 * take a project for a launch's place. */
export interface Places {
  projects: boolean;
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
  return { projects: data.projects === true, places };
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
  if (rows.length > 0) return { withProjects: projects, places: rows, pick: pickOf(rows[0], projects) };
  if (!projects) return { withProjects: false, places: rows, pick: HOME_PICK };
  const listed = await read("projects", "api/projects");
  return { withProjects: true, places: rows,
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

const HOME_FOLDER = /^(?:\/home\/[^/]+|\/Users\/[^/]+|\/root)(?=\/|$)/;

/** A path with the user's home folder written `~`. The home folder is told by its usual
 * forms (`/home/<name>`, `/Users/<name>`, `/root`); any other path comes back as it is,
 * less its closing slashes. */
export function tilde(path: string): string {
  const bare = path.length > 1 ? path.replace(/\/+$/, "") || "/" : path;
  return bare.replace(HOME_FOLDER, HOME);
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
export function folderFace(path: string, max = FACE_MAX): FolderFace {
  const shown = tilde(path);
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

export function whereFace(pick: Pick): WhereFace {
  if (pick.kind === "project") return { word: "Project", dim: "", name: pick.key };
  if (pick.kind === "choose") return { word: "Project", dim: "", name: "choose" };
  return { word: "Folder", ...folderFace(pick.path) };
}

function pickKey(pick: Pick): string {
  return pick.kind === "project" ? `project ${pick.key}` : pick.kind === "folder" ? `folder ${tilde(pick.path)}` : "";
}

/** Whether two picks name the same place. */
export function samePick(a: Pick, b: Pick): boolean {
  return pickKey(a) !== "" && pickKey(a) === pickKey(b);
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
export function placeRows(places: readonly Place[], projects: boolean, pick: Pick, now: Date,
                          hour: Clock = HOUR, day: Clock = DAY): PlaceRow[] {
  const seen = new Set<string>();
  const out: PlaceRow[] = [];
  for (const place of places) {
    const mine = pickOf(place, projects);
    const key = pickKey(mine);
    if (seen.has(key)) continue;
    seen.add(key);
    const face = mine.kind === "project" ? { dim: "", name: mine.key } : folderFace(place.cwd);
    out.push({ ...face, pick: mine, time: placeTime(place.at, now, hour, day), current: samePick(mine, pick) });
  }
  return out;
}

// --- the form's values ---------------------------------------------------------------------

/**
 * What the piece's form holds, as the launcher's panel reads its own form: every row that
 * takes input with the value `/api/form` filled it with, and over those the rows set here
 * (`set`: the brief and the place). A row this install shows off
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

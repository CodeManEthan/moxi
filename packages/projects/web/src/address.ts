// The page's address and the list's states, with no DOM so the Node tests can drive them.
//
// The address is the route after `/m/projects/`: `""` the list with nothing opened,
// `open/<key>` a project opened, `edit/<key>` a project opened for its edit, `remove/<key>`
// a project opened for its remove, and `add` adding one. A key is matched ignoring case.
// The page writes the address with `history.replaceState` whenever what is open changes,
// so Back leaves the page.

import type { Failure, Listed } from "./feed.ts";

/** What the address opens. */
export type Address =
  | { piece: "list" }
  | { piece: "open" | "edit" | "remove"; key: string }
  | { piece: "add" };

/** The list, nothing opened. */
export const NOTHING_OPEN: Address = { piece: "list" };

const KEYED = new Set(["open", "edit", "remove"]);

/** The address a route names. A route that names nothing the page opens is the list. */
export function parseRoute(route: string): Address {
  const parts = route.replace(/^\/+|\/+$/g, "").split("/");
  if (parts.length === 1 && parts[0] === "add") return { piece: "add" };
  if (parts.length !== 2 || !KEYED.has(parts[0]) || parts[1] === "") return NOTHING_OPEN;
  let key: string;
  try {
    key = decodeURIComponent(parts[1]);
  } catch {
    return NOTHING_OPEN;
  }
  return { piece: parts[0] as "open" | "edit" | "remove", key };
}

/** The route of `address`. */
export function routeOf(address: Address): string {
  if (address.piece === "list") return "";
  if (address.piece === "add") return "add";
  return `${address.piece}/${encodeURIComponent(address.key)}`;
}

/** The path the browser's address bar shows for `address`. */
export function addressPath(address: Address): string {
  return `/m/projects/${routeOf(address)}`;
}

/** The path the page puts in the browser's address for `address`, rewriting the entry it
 * is on: null when the address already says it, or when the document is not at the page. */
export function pathToWrite(address: Address, pathname: string): string | null {
  if (!/^\/m\/projects(\/|$)/.test(pathname)) return null;
  const path = addressPath(address);
  return path === pathname ? null : path;
}

/** The stored key of the project `key` names, matched ignoring case, or null for none. An
 * exact match wins over one that differs only in case. */
export function keyIn(projects: readonly Listed[], key: string): string | null {
  if (projects.some((project) => project.key === key)) return key;
  const lower = key.toLowerCase();
  return projects.find((project) => project.key.toLowerCase() === lower)?.key ?? null;
}

/** The dim line at the list's foot for an address whose key is not a project. */
export function noSuchProject(key: string): string {
  return `No project is named ${key}.`;
}

// -- the list's states ------------------------------------------------------------------------

/** How long a read that is out shows nothing before `Reading…`, in milliseconds. */
export const READING_AFTER_MS = 400;
/** What a read that is out shows in its place. */
export const READING = "Reading…";
/** The first row while projects' page server doesn't answer. */
export const NOT_ANSWERING = "Projects' page server isn't answering.";

/** The list as the page draws it:
 * - `loading`: the first read is out; `line` is `Reading…` once `READING_AFTER_MS` have gone.
 * - `down`: projects' page server isn't answering; the last list read is kept, dim and with
 *   no acts.
 * - `refused`: the store refused the read (unreadable, too new, locked); `line` is its reason
 *   and fix as the page server gives them, and no rows or acts are drawn.
 * - `empty`, `up`: read, with no project or with some.
 * - `gone`: projects went away while the page was open. */
export interface ListState {
  state: "loading" | "down" | "refused" | "empty" | "up" | "gone";
  /** The first row's words, or `Reading…`; null for none. */
  line: string | null;
  /** The rows are drawn (the last ones read, while `down`). */
  rows: boolean;
  /** Acts are offered: `Add a project`, `Open`, `Change folder` and the opened project. */
  acts: boolean;
}

/** A refusal that is the store's own: a 422 or a 409 with its reason and fix. */
function storeRefused(failure: Failure): boolean {
  return failure.said !== null && (failure.status === 422 || failure.status === 409);
}

/** The list's state from its read: the last value, the last failure, whether it went away,
 * and how long the first read has been out. */
export function listState(read: { value: { projects: readonly unknown[] } | null; failure: Failure | null; gone: boolean },
                          waitedMs: number): ListState {
  if (read.gone) return { state: "gone", line: null, rows: false, acts: false };
  if (read.failure !== null) {
    if (storeRefused(read.failure)) return { state: "refused", line: read.failure.said, rows: false, acts: false };
    return { state: "down", line: NOT_ANSWERING, rows: read.value !== null, acts: false };
  }
  if (read.value === null) {
    return { state: "loading", line: waitedMs >= READING_AFTER_MS ? READING : null, rows: false, acts: false };
  }
  return { state: read.value.projects.length === 0 ? "empty" : "up", line: null, rows: true, acts: true };
}

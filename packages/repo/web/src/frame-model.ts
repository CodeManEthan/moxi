// The page's address and its states, with no DOM so the Node tests drive them.
//
// The address is the route after `/m/repo/`: `""` the page with nothing open,
// `repo/<name>` a repo opened (`name` its project's key or its `~` folder),
// `repo/<name>/add` its `Add a piece` list, `project/<key>` the first repo of that project
// opened, `add` the `Add a repo` panel, and `pieces` with `Pieces` shown. A name is one
// path segment, encoded, and decoded once when read. One thing is open at a time; the
// page writes the address with `history.replaceState` whenever what is open changes.

import type { Failure, Repos } from "./feed.ts";
import type { RowView } from "./rows-model.ts";
import { homeForm } from "./words.ts";

/** What the address opens. */
export type Address =
  | { piece: "list" }
  | { piece: "repo" | "repo-add"; name: string }
  | { piece: "project"; key: string }
  | { piece: "add" }
  | { piece: "pieces" };

/** The page, nothing open. */
export const NOTHING_OPEN: Address = { piece: "list" };

function decoded(segment: string): string | null {
  if (segment === "") return null;
  try {
    return decodeURIComponent(segment);
  } catch {
    return null;
  }
}

/** The address a route names; one the page doesn't hold is the page with nothing open. */
export function parseRoute(route: string): Address {
  const parts = route.replace(/^\/+|\/+$/g, "").split("/");
  if (parts.length === 1 && (parts[0] === "add" || parts[0] === "pieces")) return { piece: parts[0] };
  const name = parts.length >= 2 ? decoded(parts[1]) : null;
  if (name === null) return NOTHING_OPEN;
  if (parts[0] === "repo" && parts.length === 2) return { piece: "repo", name };
  if (parts[0] === "repo" && parts.length === 3 && parts[2] === "add") return { piece: "repo-add", name };
  if (parts[0] === "project" && parts.length === 2) return { piece: "project", key: name };
  return NOTHING_OPEN;
}

/** The route of `address`. */
export function routeOf(address: Address): string {
  switch (address.piece) {
    case "list": return "";
    case "add": return "add";
    case "pieces": return "pieces";
    case "repo": return `repo/${encodeURIComponent(address.name)}`;
    case "repo-add": return `repo/${encodeURIComponent(address.name)}/add`;
    case "project": return `project/${encodeURIComponent(address.key)}`;
  }
}

/** The path the page puts in the browser's address for `address`, rewriting the entry it
 * is on: null when the address already says it, or when the document is not at the page. */
export function pathToWrite(address: Address, pathname: string): string | null {
  if (!/^\/m\/repo(\/|$)/.test(pathname)) return null;
  const path = `/m/repo/${routeOf(address)}`;
  return path === pathname ? null : path;
}

/** The row an address names among `rows`: by its name on the page or its root in `~`
 * form; a project's key names that project's first row. Null for none. */
export function rowNamed(address: Address, rows: readonly RowView[], home: string | null): RowView | null {
  if (address.piece === "project") {
    return rows.find((row) => row.repo !== null && row.repo.project === address.key) ?? null;
  }
  if (address.piece !== "repo" && address.piece !== "repo-add") return null;
  return rows.find((row) => row.repo !== null && (row.name === address.name
    || homeForm(row.repo.root, home) === address.name || row.repo.root === address.name)) ?? null;
}

/** The address of a row opened, or of its `Add a piece` list: by its name. */
export function rowAddress(row: Pick<RowView, "name">, add = false): Address {
  return { piece: add ? "repo-add" : "repo", name: row.name };
}

/** The dim line at the page's foot for an address that names no repo. */
export function noSuchRepo(address: Address): string | null {
  if (address.piece === "repo" || address.piece === "repo-add") return `No repo is named ${address.name}.`;
  if (address.piece === "project") return `No repo is named ${address.key}.`;
  return null;
}

// -- the states ---------------------------------------------------------------------------

/** How long a read that is out shows nothing before `Reading…`, in milliseconds. */
export const READING_AFTER_MS = 400;
export const READING = "Reading…";
/** The first line while the repos can't be read. */
export const CANT_READ = "Git hooks can't be read right now.";

/** The page as it draws:
 * - `loading`: the first read is out; `line` is `Reading…` once `READING_AFTER_MS` have gone.
 * - `down`: the last read failed; `line` says so, and the last value read is drawn dim with
 *   no acts.
 * - `error`: the page server's listing failed; `line` is its detail, in the verdict's place.
 * - `up`: read.
 * - `gone`: repo went away while the page was open; nothing is drawn. */
export interface PageState {
  state: "loading" | "down" | "error" | "up" | "gone";
  line: string | null;
  /** The value is drawn: `Hooks`, `Repos` and `Pieces`. */
  drawn: boolean;
  /** Acts are offered. */
  acts: boolean;
}

/** The page's state from its read: the last value, the last failure, whether it went
 * away, and how long the first read has been out. */
export function pageState(read: { value: Repos | null; failure: Failure | null; gone: boolean }, waitedMs: number): PageState {
  if (read.gone) return { state: "gone", line: null, drawn: false, acts: false };
  if (read.failure !== null) return { state: "down", line: CANT_READ, drawn: read.value !== null, acts: false };
  if (read.value === null) {
    return { state: "loading", line: waitedMs >= READING_AFTER_MS ? READING : null, drawn: false, acts: false };
  }
  if (read.value.state === "error") return { state: "error", line: read.value.detail, drawn: false, acts: false };
  return { state: "up", line: null, drawn: true, acts: true };
}

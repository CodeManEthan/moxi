// The Services page's address, with no DOM so the Node tests can drive it.
//
// The address is the route after `/m/services/`: `""` the page with nothing opened,
// `row/<id>` a row opened, `add` and `adopt` the panel on that way, `project/<key>` the
// filter line (with projects installed), and `project/<key>/row/<id>` both. A row's id is
// written as the CLI takes it: a bare name for his row, `<module>/<name>` for Moxi's. One
// thing is open at a time. The page writes the address with `history.replaceState`
// whenever what is open changes, so Back leaves the page.

/** The module that owns his rows: a row id `services/<name>` is written `<name>`. */
const HIS = "services";

/** What the address opens: the filter's project key, the opened row's id, the panel. */
export interface Address {
  project: string | null;
  /** The row's id as `/api/rows` gives it, `<module>/<name>`. */
  row: string | null;
  panel: "add" | "adopt" | null;
}

/** The page, nothing opened. */
export const NOTHING_OPEN: Address = { project: null, row: null, panel: null };

function decoded(part: string): string | null {
  try {
    const text = decodeURIComponent(part);
    return text === "" ? null : text;
  } catch {
    return null;
  }
}

/** A row id as the address writes it, back to `<module>/<name>`; null when it isn't one. */
function rowId(parts: readonly string[]): string | null {
  const words = parts.map(decoded);
  if (words.some((word) => word === null || word.includes("/"))) return null;
  if (words.length === 1) return `${HIS}/${words[0]}`;
  if (words.length === 2) return `${words[0]}/${words[1]}`;
  return null;
}

/** The address a route names. A route that names nothing the page opens is the page
 * with nothing opened; `known` false says so, for the foot's dim line. */
export function parseRoute(route: string): { address: Address; known: boolean } {
  const parts = route.replace(/^\/+|\/+$/g, "").split("/");
  const none = { address: NOTHING_OPEN, known: false };
  if (parts.length === 1 && parts[0] === "") return { address: NOTHING_OPEN, known: true };
  if (parts.length === 1 && (parts[0] === "add" || parts[0] === "adopt")) {
    return { address: { ...NOTHING_OPEN, panel: parts[0] }, known: true };
  }
  let project: string | null = null;
  let rest = parts;
  if (parts[0] === "project") {
    project = parts.length >= 2 ? decoded(parts[1]) : null;
    if (project === null) return none;
    rest = parts.slice(2);
    if (rest.length === 0) return { address: { ...NOTHING_OPEN, project }, known: true };
  }
  if (rest[0] !== "row") return none;
  const row = rowId(rest.slice(1));
  return row === null ? none : { address: { project, row, panel: null }, known: true };
}

/** A row id as the address writes it: `<name>` for his, `<module>/<name>` for Moxi's. */
export function rowRoute(id: string): string {
  const [module, ...name] = id.split("/");
  const words = module === HIS && name.length ? name : [module, ...name];
  return words.map(encodeURIComponent).join("/");
}

/** The route of `address`. The panel is one thing open on its own. */
export function routeOf(address: Address): string {
  if (address.panel !== null) return address.panel;
  const parts: string[] = [];
  if (address.project !== null) parts.push("project", encodeURIComponent(address.project));
  if (address.row !== null) parts.push("row", rowRoute(address.row));
  return parts.join("/");
}

/** The path the browser's address bar shows for `address`. */
export function addressPath(address: Address): string {
  return `/m/services/${routeOf(address)}`;
}

/** The path the page puts in the browser's address for `address`, rewriting the entry it
 * is on: null when the address already says it, or when the document is not at the page. */
export function pathToWrite(address: Address, pathname: string): string | null {
  if (!/^\/m\/services(\/|$)/.test(pathname)) return null;
  const path = addressPath(address);
  return path === pathname ? null : path;
}

/** What the page opens of `address`, given the rows' ids, the project keys his rows carry
 * (null without projects) and whether the panel is offered (the desk): a row the page does
 * not have, a project without projects installed, or the panel on the phone, opens
 * nothing, and `unknown` is the foot's dim line. A key no row carries keeps the filter on
 * it, with no row open, since services can't tell whether the project exists: the list
 * then says no service belongs to it. */
export function resolve(address: Address, ids: readonly string[], keys: readonly string[] | null,
                        panel: boolean): { address: Address; unknown: string | null } {
  if (address.panel !== null) {
    return panel ? { address, unknown: null } : { address: NOTHING_OPEN, unknown: NO_SUCH_ADDRESS };
  }
  if (address.project !== null) {
    if (keys === null) return { address: NOTHING_OPEN, unknown: NO_SUCH_ADDRESS };
    if (!keys.includes(address.project)) return { address: { ...NOTHING_OPEN, project: address.project }, unknown: null };
  }
  if (address.row !== null && !ids.includes(address.row)) {
    return { address: NOTHING_OPEN, unknown: noSuchRow(address.row) };
  }
  return { address, unknown: null };
}

/** The foot's dim line for an address that names nothing the page has. */
export const NO_SUCH_ADDRESS = "This address names nothing on this page.";

export function noSuchRow(id: string): string {
  const [module, ...name] = id.split("/");
  return `No service is named ${module === HIS ? name.join("/") : id}.`;
}

// The page's addresses: what follows `/m/usage/` names the one piece that is open, a
// limit row or a section, and inside Sessions one session and one of its subagents.
// `parseAddress` reads a route into that piece and `addressRoute` writes it back, so
// that every address the page writes is read back as the piece it was written for. The
// range, the filters and the measure are not in the address: they are held for the
// document's life (`filter-model.ts`). No DOM here.

/** The sections that open under the filter line and take nothing after their name. */
export type SectionName = "projects" | "folders" | "models" | "kinds" | "prices";

/** What is open. */
export type Address =
  /** The page with nothing opened. */
  | { piece: "none" }
  /** A route that names nothing the page has: the page with nothing opened, and a line
   * at its foot. */
  | { piece: "unknown" }
  /** One plan window's row. */
  | { piece: "limit"; harness: string; limit: string; minutes: number }
  /** The Sessions section; with `session`, that session of `harness` opened in it; with
   * `agent`, that subagent's requests opened in the session. `agent` is null without a
   * session, and `harness` and `session` are null together. */
  | { piece: "sessions"; harness: string | null; session: string | null; agent: string | null }
  | { piece: SectionName };

export const NOTHING_OPEN: Address = { piece: "none" };
export const SESSIONS: Address = { piece: "sessions", harness: null, session: null, agent: null };

const SECTIONS: readonly SectionName[] = ["projects", "folders", "models", "kinds", "prices"];
const UNKNOWN: Address = { piece: "unknown" };

/** A route's segments decoded; null when one is empty or is not a percent-encoding. */
function segments(path: string): string[] | null {
  const out: string[] = [];
  for (const part of path.split("/")) {
    if (part === "") return null;
    try {
      out.push(decodeURIComponent(part));
    } catch {
      return null;
    }
  }
  return out;
}

/**
 * What a route opens. Slashes around it, a query and a fragment are not part of it.
 *
 * - `""`: nothing.
 * - `limit/<harness>/<limit_id>/<minutes>`: that window's row; the minutes are a whole
 *   number of one or more, written without a leading zero.
 * - `sessions`, `sessions/<harness>/<id>`, `sessions/<harness>/<id>/<agent>`.
 * - `projects`, `folders`, `models`, `kinds`, `prices`.
 *
 * Every other route is unknown, among them the views the page had before its sections:
 * `daily`, `terms` and `session/<id>`.
 */
export function parseAddress(route: string): Address {
  const path = route.split(/[?#]/)[0].replace(/^\/+|\/+$/g, "");
  if (path === "") return NOTHING_OPEN;
  const parts = segments(path);
  if (parts === null) return UNKNOWN;
  const [head, ...rest] = parts;
  if (head === "limit") {
    if (rest.length !== 3 || !/^[1-9]\d*$/.test(rest[2])) return UNKNOWN;
    const minutes = Number(rest[2]);
    if (!Number.isSafeInteger(minutes)) return UNKNOWN;
    return { piece: "limit", harness: rest[0], limit: rest[1], minutes };
  }
  if (head === "sessions") {
    if (rest.length === 0) return SESSIONS;
    if (rest.length < 2 || rest.length > 3) return UNKNOWN;
    return { piece: "sessions", harness: rest[0], session: rest[1], agent: rest[2] ?? null };
  }
  if (rest.length === 0 && (SECTIONS as readonly string[]).includes(head)) return { piece: head as SectionName };
  return UNKNOWN;
}

/** The route that opens `address`: what the page writes after `/m/usage/`. Nothing
 * opened, and an address the page does not know, are the page's own route, `""`. */
export function addressRoute(address: Address): string {
  switch (address.piece) {
    case "none":
    case "unknown":
      return "";
    case "limit":
      return ["limit", address.harness, address.limit, String(address.minutes)].map(encodeURIComponent).join("/");
    case "sessions": {
      if (address.harness === null || address.session === null) return "sessions";
      const parts = ["sessions", address.harness, address.session];
      if (address.agent !== null) parts.push(address.agent);
      return parts.map(encodeURIComponent).join("/");
    }
    default:
      return address.piece;
  }
}

/** The path of the page at `address`. */
export function addressPath(address: Address): string {
  return `/m/usage/${addressRoute(address)}`;
}

/** Whether two addresses open the same piece. */
export function sameAddress(a: Address, b: Address): boolean {
  return a.piece === b.piece && addressRoute(a) === addressRoute(b);
}

/** `projects` and `folders` are two names of one section: the address as the install
 * has it, Folders where projects is not installed and Projects where it is. */
export function settled(address: Address, folders: boolean): Address {
  if (address.piece !== "projects" && address.piece !== "folders") return address;
  return { piece: folders ? "folders" : "projects" };
}

/** One session of the Sessions section, or one of its subagents. */
export function sessionAddress(harness: string, session: string, agent: string | null = null): Address {
  return { piece: "sessions", harness, session, agent };
}

/** One plan window's row. */
export function limitAddress(harness: string, limit: string, minutes: number): Address {
  return { piece: "limit", harness, limit, minutes };
}

/** What the page does to the browser's address when the piece that is open changes: it
 * rewrites the entry it is on and adds none, so Back leaves the page. Null when the
 * address already says it, or when the document is not at the page. */
export function addressWrite(address: Address, pathname: string): string | null {
  if (!/^\/m\/usage(\/|$)/.test(pathname)) return null;
  const path = addressPath(address);
  return path === pathname ? null : path;
}

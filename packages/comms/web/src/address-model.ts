// The Comms page's addresses: what follows `/m/comms/` names the one thing that is open.
// `parseAddress` reads a route into it and `addressRoute` writes it back, so every address
// the page writes is read back as what it was written for. No DOM here.

/** What is open. */
export type Address =
  /** The page with nothing opened. */
  | { piece: "none" }
  /** A route the page has no reading for: nothing opened, and a line at the foot. */
  | { piece: "unknown" }
  /** The row of that address's name, its box written to that address. */
  | { piece: "agent"; qualified: string }
  /** The author's row, its box set to answer that message. */
  | { piece: "reply"; message_id: string }
  /** The thread between two addresses, the pair in name order. */
  | { piece: "thread"; a: string; b: string }
  /** `All mail with` an address, under its name's row. */
  | { piece: "mail"; qualified: string }
  /** `Other names` shown. */
  | { piece: "others" };

export const NOTHING_OPEN: Address = { piece: "none" };
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

/** The thread between two addresses, the pair in name order. */
export function threadAddress(a: string, b: string): Address {
  const [first, second] = a <= b ? [a, b] : [b, a];
  return { piece: "thread", a: first, b: second };
}

/**
 * What a route opens. Slashes around it, a query and a fragment are not part of it.
 *
 * - `""`: nothing.
 * - `agent/<qualified>`, `mail/<qualified>`: an address, written whole.
 * - `reply/<message_id>`: a message id, all digits.
 * - `thread/<a>/<b>`: two addresses, in either order; the page writes them in name order.
 * - `others`.
 *
 * Every other route is unknown.
 */
export function parseAddress(route: string): Address {
  const path = route.split(/[?#]/)[0].replace(/^\/+|\/+$/g, "");
  if (path === "") return NOTHING_OPEN;
  const parts = segments(path);
  if (parts === null) return UNKNOWN;
  const [head, ...rest] = parts;
  if (head === "others" && rest.length === 0) return { piece: "others" };
  if ((head === "agent" || head === "mail") && rest.length === 1) return { piece: head, qualified: rest[0] };
  if (head === "reply" && rest.length === 1 && /^\d+$/.test(rest[0])) return { piece: "reply", message_id: rest[0] };
  if (head === "thread" && rest.length === 2 && rest[0] !== rest[1]) return threadAddress(rest[0], rest[1]);
  return UNKNOWN;
}

/** The route that opens `address`: what the page writes after `/m/comms/`. Nothing opened,
 * and a route the page has no reading for, are the page's own route, `""`. */
export function addressRoute(address: Address): string {
  switch (address.piece) {
    case "none":
    case "unknown":
      return "";
    case "agent":
    case "mail":
      return [address.piece, address.qualified].map(encodeURIComponent).join("/");
    case "reply":
      return `reply/${address.message_id}`;
    case "thread":
      return ["thread", address.a, address.b].map(encodeURIComponent).join("/");
    default:
      return address.piece;
  }
}

/** The path of the page at `address`. */
export function addressPath(address: Address): string {
  return `/m/comms/${addressRoute(address)}`;
}

/** Whether two addresses open the same thing. */
export function sameAddress(a: Address, b: Address): boolean {
  return a.piece === b.piece && addressRoute(a) === addressRoute(b);
}

/** What the page does to the browser's address when what is open changes: it rewrites the
 * entry it is on and adds none, so Back leaves the page. Null when the address already
 * says it, or when the document is not at the page. */
export function addressWrite(address: Address, pathname: string): string | null {
  if (!/^\/m\/comms(\/|$)/.test(pathname)) return null;
  const path = addressPath(address);
  return path === pathname ? null : path;
}

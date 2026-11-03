// The parts of the gateway's worker, and of its offline page, that need no worker: which
// requests the worker answers, the headers it sets on the offline page, what an answer
// says about reaching the gateway, and the offline page it makes from its own bytes.

import { type Reach, reachOf } from "./gateway-model.ts";
import { PUSH_URL } from "./push.ts";

/** The worker's one cache, which holds only when the gateway last answered. Any other
 * cache on the origin is deleted when the worker starts. */
export const CACHE = "moxi-gateway";

/** The offline page, and its script. The worker answers both from its own bytes, on
 * every request: no copy of either is kept where a page could write one. */
export const OFFLINE = "/offline";
export const OFFLINE_SCRIPT = "/offline.js";

/** The headers the worker sets on the offline page and its script: the gateway's one
 * policy, as its listener sends it, and `no-store`. */
export const GATEWAY_HEADERS: ReadonlyArray<readonly [string, string]> = Object.freeze([
  ["Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; "
    + "img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; "
    + "form-action 'self'; frame-ancestors 'none'; require-trusted-types-for 'script'; "
    + "manifest-src 'self'; worker-src 'self'"],
  ["X-Content-Type-Options", "nosniff"],
  ["Referrer-Policy", "no-referrer"],
  ["Cross-Origin-Opener-Policy", "same-origin"],
  ["Cross-Origin-Resource-Policy", "same-origin"],
  ["Permissions-Policy", "camera=(), microphone=(), geolocation=(), usb=(), payment=()"],
  ["Cache-Control", "no-store"],
]);

/** The headers of one of the worker's own files, of `type`: the gateway's, and no others. */
export function ownHeaders(type: string): Headers {
  const headers = new Headers(GATEWAY_HEADERS as [string, string][]);
  headers.set("Content-Type", type);
  return headers;
}

/** The cache key under which the worker keeps when the gateway last answered. Nothing
 * serves this path; it is only a key, and what it holds is read only as a number. */
export const ANSWERED = "/offline/answered";

/** The header the worker adds to a navigation it sends on. Sent with `cache: "reload"`,
 * a navigation reaches the gateway as a plain same-origin GET, and this marks it as the
 * navigation it is. */
export const NAVIGATION_HEADER = "X-Moxi-Navigation";

/** The pages the browser loads itself, past the worker: each clears the origin's cache
 * and storage as it loads, at a fresh URL. */
export const OWN_PAGES = Object.freeze(["/pair", "/signin"]);

/** How often the offline page tries again while it is visible. */
export const RETRY_MS = 30_000;

/** How the worker handles a request: `pass` leaves it to the browser, `own` answers one of
 * the worker's own files from its bytes, `navigation` sends it on as a navigation and shows
 * the offline page when the gateway can't answer, `get` sends it on past the HTTP cache and
 * gives the network's answer or its failure. */
export type Handling = "pass" | "own" | "navigation" | "get";

/** The handling of a request of `method` and `mode` for `url`, on the worker's `origin`.
 * Only same-origin GETs are the worker's. The push stream passes: its fetch asks for
 * `no-store`, so the browser's HTTP cache never answers it, and a stream held open
 * through the worker would end with the worker. */
export function handling(method: string, url: URL, origin: string, mode: string): Handling {
  if (method !== "GET" || url.origin !== origin) return "pass";
  if (url.pathname === OFFLINE || url.pathname === OFFLINE_SCRIPT) return "own";
  if (mode === "navigate") return OWN_PAGES.includes(url.pathname) ? "pass" : "navigation";
  if (url.pathname === PUSH_URL) return "pass";
  return "get";
}

/** What a navigation's answer says. A redirect the worker didn't follow is the gateway's
 * own answer. Any 502, 503 or 504 is the tunnel's or the off answer, since the gateway
 * answers a navigation with its document or a redirect. */
export function navigationReach(status: number, type: string): Reach {
  if (type === "opaqueredirect") return "up";
  if (status === 502 || status === 503 || status === 504) return "down";
  return "up";
}

/** What any other answer says: as `reachOf` reads it, by its status and whether its body
 * is JSON. */
export function answerReach(status: number, contentType: string | null): Reach {
  return reachOf(status, (contentType ?? "").split(";")[0].trim().toLowerCase() === "application/json");
}

/** The earliest time the worker reads as when the gateway last answered: 2020-01-01. */
export const EARLIEST_MS = Date.UTC(2020, 0, 1);
/** How far past now that time may lie and still be read: a clock that moved back a day. */
export const AHEAD_MS = 24 * 60 * 60 * 1000;

/** `text` as when the gateway last answered, in whole milliseconds since the epoch, or
 * null. Any page under the origin can write where the worker keeps it, so only digits
 * that make a time from 2020 to a day past `now` are read; anything else is unknown. */
export function answeredAt(text: string | null | undefined, now: number): number | null {
  if (typeof text !== "string" || !/^[0-9]{1,15}$/.test(text)) return null;
  const ms = Number(text);
  return ms >= EARLIEST_MS && ms <= now + AHEAD_MS ? ms : null;
}

/** The offline page from the worker's own copy: its root says which case the worker saw
 * and, when it knows, when the gateway last answered, in milliseconds since the epoch. */
export function offlineDocument(html: string, reach: Reach, last: number | null): string {
  const when = last === null ? "" : ` data-last-answered="${Math.trunc(last)}"`;
  return html.replace(/<html\b/i, `<html data-reach="${reach === "down" ? "down" : "away"}"${when}`);
}

/** What the offline page says, read from its root. A page the worker didn't make reads as
 * the computer out of reach, and a time that isn't one as unknown. */
export function offlineInfo(data: Readonly<Record<string, string | undefined>>,
                            now: number = Date.now()): { reach: Reach; last: Date | null } {
  const reach: Reach = data.reach === "down" ? "down" : "away";
  const ms = answeredAt(data.lastAnswered, now);
  return { reach, last: ms === null ? null : new Date(ms) };
}

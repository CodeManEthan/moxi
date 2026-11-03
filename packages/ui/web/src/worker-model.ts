// The parts of the gateway's worker, and of its offline page, that need no worker: which
// requests the worker answers, the files it keeps on the phone, what an answer says about
// reaching the gateway, and the offline page it makes from the kept one.

import { type Reach, reachOf } from "./gateway-model.ts";
import { PUSH_URL } from "./push.ts";

/** The worker's one cache. Any other cache on the origin is deleted when it starts. */
export const CACHE = "moxi-gateway";

/** The offline page, and its script. */
export const OFFLINE = "/offline";
export const OFFLINE_SCRIPT = "/offline.js";

/** What the worker keeps on the phone: the app shell's files and the offline page. Each
 * is fetched past the browser's HTTP cache, at install and again while the gateway
 * answers, and is answered from here only when the gateway can't be reached. */
export const KEPT = Object.freeze([
  "/m/ui/client.js", "/m/ui/theme.css", "/manifest.webmanifest", "/icon-192.png",
  "/icon-512.png", OFFLINE, OFFLINE_SCRIPT,
]);

/** The cache key under which the worker keeps when the gateway last answered. Nothing
 * serves this path; it is only a key. */
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

/** How the worker handles a request: `pass` leaves it to the browser, `navigation` sends
 * it on as a navigation and shows the offline page when the gateway can't answer, `get`
 * sends it on past the HTTP cache and answers a kept file from the cache when it can't. */
export type Handling = "pass" | "navigation" | "get";

/** The handling of a request of `method` and `mode` for `url`, on the worker's `origin`.
 * Only same-origin GETs are the worker's. The push stream passes: its fetch asks for
 * `no-store`, so the browser's HTTP cache never answers it, and a stream held open
 * through the worker would end with the worker. */
export function handling(method: string, url: URL, origin: string, mode: string): Handling {
  if (method !== "GET" || url.origin !== origin) return "pass";
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

/** Whether a kept file's answer is unusable, so the kept copy answers in its place: no
 * answer, or a 502, 503 or 504. */
export function useKept(status: number | null): boolean {
  return status === null || status === 502 || status === 503 || status === 504;
}

/** The offline page from the kept one: its root says which case the worker saw and, when
 * it knows, when the gateway last answered, in milliseconds since the epoch. */
export function offlineDocument(html: string, reach: Reach, last: number | null): string {
  const when = last === null ? "" : ` data-last-answered="${Math.trunc(last)}"`;
  return html.replace(/<html\b/i, `<html data-reach="${reach === "down" ? "down" : "away"}"${when}`);
}

/** What the offline page says, read from its root. A page the worker didn't make reads as
 * the computer out of reach. */
export function offlineInfo(data: Readonly<Record<string, string | undefined>>): { reach: Reach; last: Date | null } {
  const reach: Reach = data.reach === "down" ? "down" : "away";
  const ms = Number(data.lastAnswered);
  const last = data.lastAnswered !== undefined && data.lastAnswered !== "" && Number.isFinite(ms) ? new Date(ms) : null;
  return { reach, last };
}

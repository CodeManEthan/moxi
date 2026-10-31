// The parts of `confirm()` that need no document: the key it mints, the outcome one
// answer maps to, and which paths are confirmed acts.

import type { ConfirmOutcome } from "./client.ts";

/** The bytes of randomness in an `Idempotency-Key`. */
export const KEY_BYTES = 32;
/** An `Idempotency-Key`: 32 bytes in base64url without padding, 43 characters. */
export const KEY_PATTERN = /^[A-Za-z0-9_-]{43}$/;

/** The text of a refusal that reached no module. */
export const NOT_SENT = "the module didn't get the act";
/** The text of an outcome nobody can know from the answer. */
export const MAY_HAVE_ACTED = "the module may have acted; check its page";
/** The text of a refusal because this browser holds no session key. */
export const SIGNED_OUT = "this browser is signed out of Moxi";

/** A new `Idempotency-Key` from `KEY_BYTES` bytes of `fill`, which is
 * `crypto.getRandomValues` unless a test gives its own. */
export function mintKey(fill: (bytes: Uint8Array<ArrayBuffer>) => Uint8Array = (bytes) => crypto.getRandomValues(bytes)): string {
  const bytes = fill(new Uint8Array(KEY_BYTES));
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function said(data: unknown): string | null {
  if (typeof data !== "object" || data === null) return null;
  for (const name of ["refused", "conflict", "error"]) {
    const value = (data as Record<string, unknown>)[name];
    if (typeof value === "string" && value !== "") return value;
  }
  return null;
}

function refusalText(code: number, data: unknown): string {
  const text = said(data);
  if (text !== null) return text;
  if (code === 403) return SIGNED_OUT;
  if (code === 404) return "the module has no such act now";
  if (code === 413) return "the request was too large for the module";
  return `the module refused the act (${code})`;
}

function proxyError(data: unknown): string | null {
  if (typeof data !== "object" || data === null) return null;
  const error = (data as Record<string, unknown>).error;
  return typeof error === "string" ? error : null;
}

/**
 * The outcome of one answer to a confirmed act: `status` is the HTTP status, 0 when no
 * answer came, and `data` the parsed JSON body or null.
 *
 * - the module's 202 is `accepted` with its status path;
 * - the module's 4xx is `refused` with its status and its text;
 * - a 503 is `refused`: the module's own is sent before any handler runs, and the
 *   shell's `stopping` means the act was never sent;
 * - the shell's 502 `start` or `unreachable` is `refused`: the act was never sent;
 * - the shell's 502 `upstream` or 504, a 500 or any other 5xx, no answer at all, and any
 *   answer the table doesn't name are `unknown`, since the module may have acted.
 */
export function outcomeOf(status: number, data: unknown): ConfirmOutcome {
  if (status === 202) {
    const path = typeof data === "object" && data !== null ? (data as Record<string, unknown>).status : null;
    if (typeof path === "string") return { outcome: "accepted", status: path };
    return { outcome: "unknown", text: MAY_HAVE_ACTED };
  }
  if (status >= 400 && status < 500) return { outcome: "refused", code: status, text: refusalText(status, data) };
  if (status === 503) {
    if (proxyError(data) === "stopping") return { outcome: "refused", code: status, text: NOT_SENT };
    return { outcome: "refused", code: status, text: "the module was too busy to take the act; try again" };
  }
  if (status === 502) {
    const error = proxyError(data);
    if (error === "start" || error === "unreachable") return { outcome: "refused", code: status, text: NOT_SENT };
  }
  return { outcome: "unknown", text: MAY_HAVE_ACTED };
}

/** The page path `target` (`/m/<module>/<rest>?query`, as `apiTarget` gives it) names on
 * `module`'s page: `/<rest>`, without the query. */
export function pagePath(module: string, target: string): string {
  const prefix = `/m/${module}`;
  const path = target.split("?", 1)[0];
  return path.startsWith(`${prefix}/`) ? path.slice(prefix.length) : path;
}

/** Whether `target` is one of `confirmed`, the paths of `module`'s confirmed acts. */
export function isConfirmed(confirmed: readonly string[], module: string, target: string): boolean {
  return confirmed.includes(pagePath(module, target));
}

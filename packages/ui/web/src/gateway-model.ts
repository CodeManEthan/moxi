// The parts of client.js's gateway side that need no document: what the gateway's shell
// document says about the phone, the desk pointer a confirmed act shows where the phone
// can't run it, the answers of the gateway's prepare and commit, and the band that says
// when the computer can't be reached.

import type { ConfirmOutcome } from "./client.ts";
import { MAY_HAVE_ACTED, NOT_SENT, SIGNED_OUT } from "./confirm-model.ts";

/** What the gateway's shell document carries, as it stood when the shell sent it. */
export interface GatewayInfo {
  /** The page came through the gateway, on the phone. */
  gateway: boolean;
  /** The shell mounts the phone's confirmed acts. */
  acts: boolean;
  /** Why the acts aren't mounted, when they aren't. */
  reason: string | null;
}

/** The values on the document's root: `data-gateway`, `data-gateway-acts` and
 * `data-gateway-reason`. A document without them is the desktop's. */
export function gatewayInfo(data: Readonly<Record<string, string | undefined>>): GatewayInfo {
  const gateway = data.gateway === "on";
  const acts = gateway && data.gatewayActs === "on";
  const reason = gateway && !acts ? (data.gatewayReason || null) : null;
  return { gateway, acts, reason };
}

/** The line for each reason the acts aren't mounted. */
export const REASON_LINES: Readonly<Record<string, string>> = {
  record: "Moxi can't read the tunnel's record on the computer right now, so the phone can't run acts here. `moxi doctor ui` on the computer says why.",
  tcp: "This computer's gateway listens on a port another program could take while Moxi isn't running, so the phone can't run acts here.",
  target: "The tunnel doesn't forward to this Moxi. Run `moxi tunnel repair` on the computer.",
  origin: "This address once served a tunnel entry that outlived Moxi, so the phone can't run acts here. Give the gateway a new address: `moxi doctor ui` on the computer says how.",
};

/** The line for a prepare or commit that got a 404: the acts were unmounted after the
 * page loaded. */
export const LAPSED_LINE = "The phone can't run acts on this computer right now. Reload the page to see why.";

/** The line for a reason this script doesn't know. */
const OTHER_LINE = "The phone can't run acts on this computer right now.";

/** The desk pointer's lines: where to do the act, then why the phone can't. `reason` is
 * the document's reason, or `lapsed` for a 404 from the prepare or the commit. */
export function deskLines(host: string, module: string, reason: string | null): string[] {
  const why = reason === "lapsed" ? LAPSED_LINE : (reason !== null && Object.hasOwn(REASON_LINES, reason) ? REASON_LINES[reason] : OTHER_LINE);
  return [`Do this at the desk, on ${host}'s Moxi page for ${module}.`, why];
}

/** A prepared act: its id and the body the gateway stored, which the sheet shows. */
export interface Prepared {
  act: string;
  body: object;
}

/** The acts were unmounted after the page loaded: the sheet points at the desk. */
export const LAPSED = "lapsed";

const BUSY = "the phone is already running an act, or ran six in the last minute; try again in a minute";

function error(data: unknown): string | null {
  if (typeof data !== "object" || data === null) return null;
  const text = (data as Record<string, unknown>).error;
  return typeof text === "string" && text ? text : null;
}

/** A refusal from the gateway itself, before anything reached the module. */
function gatewayRefusal(status: number, data: unknown): ConfirmOutcome {
  if (status === 403) return { outcome: "refused", code: 403, text: SIGNED_OUT };
  if (status === 429) return { outcome: "refused", code: 429, text: BUSY };
  return { outcome: "refused", code: status, text: error(data) ?? NOT_SENT };
}

/**
 * The answer of `POST /gw/act/prepare`: the prepared act, `LAPSED` for a 404, or the
 * outcome when the gateway refused it. A prepare sends nothing to the module, so every
 * failure, no answer included, is `refused`.
 */
export function prepared(status: number, data: unknown): Prepared | typeof LAPSED | ConfirmOutcome {
  if (status === 404) return LAPSED;
  if (status === 200 && typeof data === "object" && data !== null) {
    const { act, body } = data as Record<string, unknown>;
    if (typeof act === "string" && typeof body === "object" && body !== null) return { act, body };
  }
  if (status === 200 || status === 0 || status >= 500) return { outcome: "refused", code: status, text: NOT_SENT };
  return gatewayRefusal(status, data);
}

/**
 * The answer of `POST /gw/act/commit`: the outcome the gateway gives in its 202, `LAPSED`
 * for a 404 (nothing was sent), the gateway's own refusal (403, 429, 400), or `unknown`
 * when the commit may have reached the gateway: no answer, a 5xx, or a 202 that can't be
 * read.
 */
export function committed(status: number, data: unknown): typeof LAPSED | ConfirmOutcome {
  if (status === 404) return LAPSED;
  if (status === 202) {
    const got = (typeof data === "object" && data !== null ? data : {}) as Record<string, unknown>;
    if (got.outcome === "accepted" && typeof got.status === "string") return { outcome: "accepted", status: got.status };
    if (got.outcome === "refused" && typeof got.code === "number" && typeof got.text === "string") {
      return { outcome: "refused", code: got.code, text: got.text };
    }
    if (got.outcome === "unknown" && typeof got.text === "string") return { outcome: "unknown", text: got.text };
    return { outcome: "unknown", text: MAY_HAVE_ACTED };
  }
  if (status >= 400 && status < 500) return gatewayRefusal(status, data);
  return { outcome: "unknown", text: MAY_HAVE_ACTED };
}

// --- the band ------------------------------------------------------------------------------

/** Whether the computer answered: `away` when nothing answered at all, `down` when the
 * computer answered but Moxi or its gateway didn't, `up` otherwise. */
export type Reach = "up" | "away" | "down";

/**
 * What one answer says about reaching the gateway. `json` is whether its body was JSON:
 * the shell's own 502, 503 and 504 for a module (its proxy's answers) carry JSON, while the
 * provider's answer for a computer with no Moxi behind it, and the gateway's off answer,
 * don't.
 */
export function reachOf(status: number, json: boolean): Reach {
  if (status === 0) return "away";
  if ((status === 502 || status === 503 || status === 504) && !json) return "down";
  return "up";
}

/** The band's text for `reach`, naming the computer by `host`; `last` is when the gateway
 * last answered this page, when it has. */
export function bandText(reach: Reach, host: string, last: Date | null): string {
  const when = last === null ? "" : ` Moxi last answered at ${last.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.`;
  if (reach === "away") {
    return `Can't reach the computer (${host}). It may be asleep or off, or Tailscale may be off on this phone or on the computer.${when}`;
  }
  if (reach === "down") return `The computer (${host}) answered, but Moxi isn't running or the gateway is off.${when}`;
  return "";
}

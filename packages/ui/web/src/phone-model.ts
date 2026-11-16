// What the Phone group on Settings › ui says, worked out with no DOM so the tests can read
// it: the gateway's state as `api/gateway/state` gives it, the line under `Phone access`,
// each paired phone's line, the revoke's ask and its answer, the pairing code's words, and
// the words for a read or an act that failed. Every string here goes into the page as text.

import { clock } from "./health-model.ts";

/** The gateway as the desk's settings read it (`GET /m/ui/api/gateway/state`). */
export interface GatewayState {
  /** The shell runs a gateway and its extra is installed: the Phone group shows. */
  installed: boolean;
  /** `gateway.enabled` as the gateway last read it. */
  switch: boolean;
  /** The gateway serves. */
  on: boolean;
  host: string | null;
  port: number | null;
  /** Who can reach the address: `internet` or `tailnet`. */
  exposure: string | null;
  /** No act of the phone's is mounted. */
  reads_only: boolean;
  /** Why the switch is on and the gateway doesn't serve, when that is known. */
  problem: string | null;
}

/** One paired phone, as `api/gateway/devices` lists it. */
export interface DeviceRow {
  id: string;
  name: string;
  created: number;
  used: number;
  passkey: boolean;
  stale: boolean;
  host: string;
  provider: string;
}

/** One audit row, as `api/gateway/log` lists it: `detail` is its other fields. */
export interface LogRow {
  at: string;
  kind: string;
  device: string | null;
  detail: string;
}

/** The group's sub-head. */
export const PHONE_HEAD = "Phone";
/** In the phones' place when none is paired. */
export const NO_PHONE = "No phone is paired.";
/** The pairing code's link, when it is too long for a QR code. */
export const TOO_LONG = "The pairing link is too long for a QR code; open it on the phone by hand.";
export const EXPIRED = "That pairing code has expired.";
/** What the phone does once the code is scanned, and what it may do after. */
export const PAIR_THEN = "The phone then asks for a name and a passkey. It will read Moxi's pages; it cannot launch, send or change a setting.";
export const PAIR = "Pair a phone";
export const COPY_LINK = "Copy the link instead";
/** The audit rows and sign-ins, tucked at the group's foot. */
export const ACTIVITY = "Activity";
/** The Activity shown with nothing in the log's last two days. */
export const NO_ACTIVITY = "Nothing yet.";
/** How often the device list is read while a pairing code lives, to see the phone arrive. */
export const WATCH_MS = 3_000;
/** How often the gateway's state and the devices are read while ui is shown. */
export const REREAD_MS = 30_000;

const EXPOSURES = ["internet", "tailnet"];

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/** The state from an answer of `api/gateway/state`; null when it is not one. */
export function stateOf(data: unknown): GatewayState | null {
  if (typeof data !== "object" || data === null) return null;
  const d = data as Record<string, unknown>;
  if (typeof d.installed !== "boolean" || typeof d.switch !== "boolean" || typeof d.on !== "boolean") return null;
  const port = typeof d.port === "number" && Number.isInteger(d.port) ? d.port : null;
  const exposure = typeof d.exposure === "string" && EXPOSURES.includes(d.exposure) ? d.exposure : null;
  return { installed: d.installed, switch: d.switch, on: d.on, host: text(d.host), port, exposure,
           reads_only: d.reads_only === true, problem: text(d.problem) };
}

/** Why a read or an act failed, as the Gateway section said it: `what` names it. */
export function failedWords(status: number, data: unknown, what: string): string {
  if (status === 0) return `No answer from the shell, so ${what} failed.`;
  if (status === 403) return "This browser is signed out.";
  const d = (typeof data === "object" && data !== null ? data : {}) as Record<string, unknown>;
  return text(d.refused) ?? text(d.conflict) ?? text(d.error) ?? `The shell answered ${status} for ${what}.`;
}

/** Under the sub-head when the state can't be read; the group is left out. */
export function stateFailed(status: number, data: unknown): string {
  return `The phone's state can't be read: ${failedWords(status, data, "reading it")}`;
}

/**
 * The line under `Phone access`, before its terminal line: with the switch on and the
 * gateway serving, where (`At <host>:<port>.`) and `Phones read only.` while no act is
 * mounted; with the switch on and the gateway not serving, why phones can't reach it.
 * Nothing with the switch off.
 */
export function accessLine(state: GatewayState): string | null {
  if (!state.switch) return null;
  if (state.on) {
    const where = state.host !== null ? `At ${state.host}${state.port !== null ? `:${state.port}` : ""}.` : null;
    const words = [where, state.reads_only ? "Phones read only." : null].filter((part) => part !== null);
    return words.length > 0 ? words.join(" ") : null;
  }
  const why = state.problem?.trim().replace(/\.+$/, "") ?? "";
  return why !== "" ? `Phones can't reach it now: ${why}.` : "Phones can't reach it now.";
}

// --- days and times -------------------------------------------------------------------------

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_MS = 86_400_000;

/** The local day of `at` as a count of days, for telling days apart. */
function dayNumber(at: Date): number {
  return Math.round(Date.UTC(at.getFullYear(), at.getMonth(), at.getDate()) / DAY_MS);
}

/** A day against `now`, as the Comms page words one: `today`, `yesterday`, the weekday
 * within six days (`Wed`), else the date (`Nov 3`). */
export function dayWord(at: Date, now: Date): string {
  const off = dayNumber(at) - dayNumber(now);
  if (off === 0) return "today";
  if (off === -1) return "yesterday";
  if (off === 1) return "tomorrow";
  if (Math.abs(off) <= 6) return WEEKDAYS[at.getDay()];
  return `${MONTHS[at.getMonth()]} ${at.getDate()}`;
}

function dateOf(seconds: number): Date | null {
  return Number.isFinite(seconds) && seconds > 0 ? new Date(seconds * 1000) : null;
}

/** A moment: its day and its time (`today 18:40`, `Nov 2 08:03`); `never` for none. */
export function momentWords(seconds: number, now: Date): string {
  const at = dateOf(seconds);
  return at === null ? "never" : `${dayWord(at, now)} ${clock(at.toISOString())}`;
}

/** A pairing code's end, as the time alone. */
export function untilWords(seconds: number): string {
  const at = dateOf(seconds);
  return at === null ? "" : clock(at.toISOString());
}

// --- the phones ---------------------------------------------------------------------------------

/** A phone's line after its name: `paired <day> · last sign-in <moment> · passkey`, and,
 * when the gateway's address changed since it paired, what to do. */
export function deviceWords(row: DeviceRow, now: Date): string {
  const paired = dateOf(row.created);
  const parts = [
    `paired ${paired === null ? "never" : dayWord(paired, now)}`,
    `last sign-in ${momentWords(row.used, now)}`,
    row.passkey ? "passkey" : "no passkey",
  ];
  if (row.stale) parts.push("the address changed; pair it again");
  return parts.join(" · ");
}

/** The ask in place under a phone when its Revoke is pressed. */
export function revokeAsk(name: string): { ask: string; why: string; keep: string; button: string } {
  return { ask: `Revoke ${name}?`, why: "It is signed out within two seconds and must be paired again to come back.",
           keep: "Keep it", button: `Revoke ${name}` };
}

/** What a revoke that was done says; a phone with a passkey keeps it until removed there. */
export function revokedWords(row: Pick<DeviceRow, "name" | "passkey">): string {
  return row.passkey
    ? `Revoked ${row.name}. Its passkey stays on the phone until you remove it in the phone's settings.`
    : `Revoked ${row.name}.`;
}

/** A phone that arrived while a code was shown: the first listed that was not known. */
export function arrived(rows: readonly DeviceRow[], known: ReadonlySet<string>): DeviceRow | null {
  return rows.find((row) => !known.has(row.id)) ?? null;
}

export function pairedWords(name: string): string {
  return `Paired: ${name}.`;
}

/** The words beside a pairing code: what to do with it, set off, then how long it works and,
 * for an address only the tailnet reaches, for whom. */
export function pairWords(until: number, exposure: string | null): { lead: string; rest: string } {
  const end = untilWords(until);
  const tail = exposure === "tailnet" ? ", and only for a phone on your tailnet" : "";
  return { lead: "Scan this with the phone's camera.", rest: `It works once${end ? `, until ${end}` : ""}${tail}.` };
}

/** Each device's sign-ins in the log, by its name, newest first, for Activity. */
export function signinLines(signins: ReadonlyMap<string, readonly string[]>, devices: readonly DeviceRow[],
                            stamp: (at: string) => string): string[] {
  const names = new Map(devices.map((row) => [row.id, row.name]));
  return [...signins].filter(([, times]) => times.length > 0)
    .map(([id, times]) => `${names.get(id) ?? id} signed in ${times.map(stamp).join(", ")}.`);
}

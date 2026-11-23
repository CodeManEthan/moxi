// What the panel that adds a service holds and says, as plain data: its two ways (a
// command to run, a unit already on this computer), the form as typed, what `/api/unit`
// says of the unit typed, `/api/add`'s body, and what an add that wasn't taken says. No
// DOM and no Lit here, so the Node tests check it directly.

import type { Said } from "./views.ts";

/** The panel's two ways: a command Moxi writes a unit for, or a unit already there. */
export type Way = "add" | "adopt";

export const PANEL_TITLE = "Add a service";
export const WAYS: readonly { way: Way; label: string }[] = [
  { way: "add", label: "A command to run" },
  { way: "adopt", label: "A unit already on this computer" },
];
/** The kind, as the panel asks it. */
export const KIND_WORDS: readonly { kind: "daemon" | "timer"; label: string }[] = [
  { kind: "daemon", label: "keeps running" },
  { kind: "timer", label: "runs on a schedule" },
];
export const GO: Record<Way, string> = { add: "Add service", adopt: "Adopt unit" };

export const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

/** How long after the last key the unit typed is read, in milliseconds. */
export const UNIT_WAIT_MS = 250;

/** What the panel holds, as typed. */
export interface AddForm {
  way: Way;
  name: string;
  /** The way `add`'s kind, as chosen. */
  kind: "daemon" | "timer";
  /** The way `adopt`'s kind, as chosen where the supervisor can't tell it (`""` until
   * chosen: none is picked for the user). Elsewhere the unit's name tells it. */
  adoptAs: "" | "daemon" | "timer";
  command: string;
  unit: string;
  /** A written row: start it now. */
  enable: boolean;
  /** An adopted row: on or off now (`""` until chosen). */
  adoptOn: "" | "on" | "off";
  /** A written timer's schedule: `every` (`5m`, `2h`) or `at` (`09:00, 17:30`). */
  schedule: "every" | "at";
  every: string;
  at: string;
  days: string[];
  cwd: string;
  health: string;
  project: string;
  launchesSession: boolean;
  doc: string;
}

/** A new form on `way`. A written row starts off, as `moxi services add` leaves it
 * without `--enable`: the user turns on "Turn it on now" to start it now and at every login. */
export function emptyForm(way: Way = "add"): AddForm {
  return { way, name: "", kind: "daemon", adoptAs: "", command: "", unit: "", enable: false, adoptOn: "",
           schedule: "every", every: "", at: "", days: [], cwd: "", health: "", project: "",
           launchesSession: false, doc: "" };
}

// -- the command's hint ---------------------------------------------------------------------

/** The hint under `Command`: the first word is a full path, and the shell command that
 * prints the one the user typed. */
export function fullPathHint(command: string): { text: string; command: string } {
  const first = command.trim().split(/\s+/)[0] ?? "";
  const word = first.split("/").filter(Boolean).pop() ?? "";
  return { text: "The first word is a full path.", command: `command -v ${word === "" ? "NAME" : word}` };
}

/** What `Turn it on now` says it does, by kind. */
export function turnOnSays(kind: "daemon" | "timer"): string {
  return kind === "timer" ? "Arms its schedule now and at every login. Left off, it waits for Turn on."
    : "Starts it now and at every login. Left off, it waits for Start.";
}

/** What `More` holds, on its line: its fields by name, the first two named. */
export function moreLine(way: Way): string {
  return way === "add" ? "folder · health check · 2 more" : "health check · 2 more";
}

// -- the unit typed ---------------------------------------------------------------------------

/** `/api/unit`: what the supervisor says of a unit not yet adopted. `found` is null when it
 * wasn't read; `enabled` and `active` null when unread. */
export interface UnitRead {
  found: boolean | null;
  kind: "daemon" | "timer" | null;
  asks_kind: boolean;
  enabled: boolean | null;
  active: boolean | null;
  wraps: boolean;
  refused: string | null;
}

type Obj = Record<string, unknown>;
const isObj = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);
const isBoolOrNull = (value: unknown): value is boolean | null => value === null || typeof value === "boolean";

/** `/api/unit`'s value, or null when it isn't one. */
export function parseUnit(data: unknown): UnitRead | null {
  if (!isObj(data) || !isBoolOrNull(data.found) || !(data.kind === null || data.kind === "daemon" || data.kind === "timer")
      || typeof data.asks_kind !== "boolean" || !isBoolOrNull(data.enabled) || !isBoolOrNull(data.active)
      || typeof data.wraps !== "boolean" || !(data.refused === null || typeof data.refused === "string")) return null;
  return { found: data.found, kind: data.kind, asks_kind: data.asks_kind, enabled: data.enabled, active: data.active,
           wraps: data.wraps, refused: data.refused };
}

/** The route that reads `unit`, as the kind `kind` where the supervisor asks it. */
export function unitRoute(unit: string, kind: "daemon" | "timer" | null): string {
  const query = `ref=${encodeURIComponent(unit.trim())}`;
  return `api/unit?${query}${kind === null ? "" : `&kind=${kind}`}`;
}

/** One read of the unit field: what was typed and the kind asked, and its answer. */
export interface Asked {
  unit: string;
  kind: "daemon" | "timer" | null;
  answer: UnitRead;
}

/** The answer for what the field holds now, or null: an answer for an older value is
 * never drawn. Where the supervisor can't tell the kind, an answer read as another kind
 * than the one chosen is old too, and one read with none chosen knows no kind. */
export function answerNow(asked: Asked | null, form: AddForm): UnitRead | null {
  if (asked === null || asked.unit !== form.unit.trim()) return null;
  if (!asked.answer.asks_kind) return asked.answer;
  if (asked.kind !== (form.adoptAs === "" ? null : form.adoptAs)) return null;
  return { ...asked.answer, kind: asked.kind };
}

/** The kind to read the unit as: the one chosen where the supervisor asks it, else none.
 * `asks` is whether an answer for this unit asked it. */
export function kindToAsk(form: AddForm, asks: boolean): "daemon" | "timer" | null {
  return asks && form.adoptAs !== "" ? form.adoptAs : null;
}

export const ADOPT_KEEPS = "Moxi lists it and acts on it; its file is never rewritten.";
export const ON_OFF_ASK = "Say which is right, so the list starts out true.";
/** A systemd timer's two sentences about the drop-in adopting adds beside it. */
export const WRAPS_SAYS = "Moxi adds one small file beside the service this timer starts, so each run is recorded "
  + "here and Turn off can hold it. Remove takes that file away and leaves the timer as it was.";

/** What the panel says under `Unit` of the answer read for it: found and what it is,
 * missing, its refusal, or that the supervisor couldn't be read. */
export function unitSays(answer: UnitRead): { line: string; refused: boolean } {
  if (answer.refused !== null) return { line: answer.refused, refused: true };
  if (answer.found === null) {
    return { line: "Your system couldn't be read just now; adopting it reads it again.", refused: false };
  }
  if (!answer.found) return { line: "Your system has no unit by that name.", refused: false };
  const what = answer.kind === null ? "a unit of your own"
    : answer.kind === "timer" ? "a timer of your own" : "a service of your own";
  return { line: `Found: ${what}. ${ADOPT_KEEPS}`, refused: false };
}

/** What the panel says under `It is` of what the system says, then why it asks. */
export function onOffSays(answer: UnitRead | null): string {
  if (answer === null || answer.found !== true || answer.enabled === null) return ON_OFF_ASK;
  return `Your system says it is ${answer.enabled ? "on" : "off"}. ${ON_OFF_ASK}`;
}

/** The kind an adopted row is sent with: where the supervisor asks it (`asks`, or the
 * answer says so), the one chosen, or null while none is; else the one the unit was read
 * as, else the one its name says. */
export function adoptKind(form: AddForm, answer: UnitRead | null, asks = false): "daemon" | "timer" | null {
  if (asks || answer?.asks_kind) return form.adoptAs === "" ? null : form.adoptAs;
  return answer?.kind ?? (form.unit.trim().endsWith(".timer") ? "timer" : "daemon");
}

export const KIND_ASK = "Say whether it keeps running or runs on a schedule.";

// -- the body -----------------------------------------------------------------------------------

/** `/api/add`'s body from the form, or the one line that says what it still needs. The
 * server checks it again and has the last word. `answer` is the unit's read, and `asks`
 * whether the supervisor asks its kind, for the way `adopt`'s kind. */
export function addBody(form: AddForm, answer: UnitRead | null = null,
                        asks = false): { body: Record<string, unknown> } | { missing: string } {
  const body: Record<string, unknown> = {};
  if (form.way === "add") {
    const command = form.command.trim();
    if (command === "") return { missing: "Give the command to run, its first word a full path." };
    const name = form.name.trim();
    if (name === "") return { missing: "Give the service a name." };
    Object.assign(body, { name, kind: form.kind, command, enable: form.enable });
    if (form.kind === "timer") {
      if (form.schedule === "every") {
        const every = form.every.trim();
        if (every === "") return { missing: "Give how often it runs, such as 5m or 2h." };
        body.every = every;
      } else {
        const at = form.at.split(/[\s,]+/).filter(Boolean);
        if (at.length === 0) return { missing: "Give the times it runs at, such as 09:00, 17:30." };
        body.at = at;
        if (form.days.length) body.days = DAYS.filter((day) => form.days.includes(day));
      }
    }
    const cwd = form.cwd.trim();
    if (cwd !== "") body.cwd = cwd;
  } else {
    const unit = form.unit.trim();
    if (unit === "") return { missing: "Give the unit to adopt, as your system names it." };
    const kind = adoptKind(form, answer, asks);
    if (kind === null) return { missing: KIND_ASK };
    const name = form.name.trim();
    if (name === "") return { missing: "Give the service a name." };
    if (form.adoptOn === "") return { missing: "Say whether it is on or off now." };
    Object.assign(body, { name, kind, unit, enable: form.adoptOn === "on" });
  }
  const health = form.health.trim();
  if (health !== "") body.health = health;
  const project = form.project.trim();
  if (project !== "") body.project = project;
  if (form.launchesSession) body.launches_session = true;
  const doc = form.doc.trim();
  if (doc !== "") body.doc = doc;
  return { body };
}

/** What an add POST's answer says when it isn't a 202: its refusal, or why there was
 * none. */
export function addRefused(status: number, said: string | null): Said {
  if (status === 0) return { tone: "warn", lines: ["No answer from the shell; the add may have been taken. The list shows what stands now."] };
  if (status === 409) return { tone: "error", lines: [said ?? "services is busy; try again."] };
  if (status >= 500) return { tone: "warn", lines: [`services' page server answered ${status}; the add may have been taken. The list shows what stands now.`] };
  return { tone: "error", lines: [said ?? `services' page server answered ${status}`] };
}

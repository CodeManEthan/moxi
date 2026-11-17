// What Home shows of the earlier launches it can run again, worked out with no DOM so the Node
// tests can drive it. prompt.ts draws it.
//
// The recents are keyed as the last launch is: by what the line above the box names, the seat
// while one is applied, else the project, else the folder (`lastQuery`). `/api/recents` lists at
// most three, newest first, each by its `head` (the first words of its brief, else its model and
// effort, else its harness), when it was made, and where it ran.
//
// Pointing at a recent reads `/api/recent` for it with the button's default, and the card says
// what the click runs: the request resolved today, so the card never shows a value the click
// would not run. It gives the prompt whole, the harness, model and effort, the preset, where
// and when the launch ran, the place when it is not the line's, each permission row off the
// user's defaults (a value that can do harm in the warning colour), the rows this install no
// longer offers, and last what the click does with the default it was read with.
//
// Every string here goes into the page as text.

import type { FormValue } from "/m/ui/client.js";

import type { LaunchBody } from "./panel.ts";
import { rowLabel } from "./options-model.ts";
import type { PanelField, Word } from "./options-model.ts";
import { fillText } from "./fill-model.ts";
import { chipFace, tilde } from "./prompt-model.ts";
import type { Pick, Run, Targets } from "./prompt-model.ts";

type Fields = Record<string, unknown>;

function isTable(value: unknown): value is Fields {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function texts(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** The words in front of the recents under the box. */
export const RUN_AGAIN = "Run again";

/** How long the card's answer is used for a click, in milliseconds; an older one is read again. */
export const CARD_FRESH_MS = 5000;

// --- the list ------------------------------------------------------------------------------

/** One earlier launch `/api/recents` lists: its id, when it was made, what it is called, and
 * where it ran (`slot` the number of the slot, null for none). */
export interface Recent {
  id: string;
  at: string;
  head: string;
  target: string;
  slot: number | null;
}

/** `/api/recents`' list, or null when the answer isn't one or says the read failed. */
export function parseRecents(data: unknown): Recent[] | null {
  if (!isTable(data) || data.state !== "up" || !Array.isArray(data.recents)) return null;
  const out: Recent[] = [];
  for (const row of data.recents) {
    if (!isTable(row) || typeof row.request_id !== "string" || row.request_id === "") continue;
    const slot = typeof row.slot === "number" ? row.slot : typeof row.slot === "string" ? Number(row.slot) : NaN;
    out.push({ id: row.request_id, at: text(row.at) ?? "", head: (text(row.head) ?? "").trim(),
               target: text(row.target) ?? "", slot: Number.isInteger(slot) ? slot : null });
  }
  return out;
}

/** Where a launch ran: its target, and its slot's number, null for none. */
export interface Ran {
  target: string;
  slot: number | null;
}

/** Where a recent ran, as its card says it. */
export function ranWhere(recent: Ran): string {
  if (recent.target === "slot") return recent.slot === null ? "Ran in a slot" : `Ran in slot ${recent.slot}`;
  if (recent.target === "headless") return "Ran headless";
  if (recent.target === "terminal") return "Ran in your terminal";
  return "Ran";
}

// --- the card ------------------------------------------------------------------------------

/** One value of a recent's request, as `/api/form` spells a value. */
export interface RecentValue {
  name: string;
  value: string | null;
  items: string[] | null;
}

/** The request `/api/recent` hands back, to post to `/api/preview` as it is. */
export interface RecentRequest {
  seat: string | null;
  project_preset: string | null;
  values: RecentValue[];
  leave_out: string[];
  off_posture: boolean;
}

/** What that request runs now. `off_posture` names the permission rows off the user's
 * defaults, `danger` the rows holding a value that can do harm, `not_offered` the rows the
 * launch set that this install no longer offers. */
export interface Shows {
  harness: string;
  model: string | null;
  effort: string | null;
  prompt: string | null;
  cwd: string;
  project_key: string | null;
  off_posture: string[];
  danger: string[];
  not_offered: string[];
}

/** Why a recent can't run again: the launcher's refusal. */
export interface CantRun {
  problem: string;
  hint: string;
}

/** `/api/recent`'s answer: the request and what it runs, or why it can't run. */
export type RecentAnswer =
  | { request: RecentRequest; shows: Shows; refused: null }
  | { request: null; shows: null; refused: CantRun };

function valueRow(row: unknown): RecentValue | null {
  if (!isTable(row) || typeof row.name !== "string" || row.name === "") return null;
  return { name: row.name, value: text(row.value), items: Array.isArray(row.items) ? texts(row.items) : null };
}

/** `/api/recent`'s answer. A read that failed, or an answer that isn't one, is a recent that
 * can't run, with `why` as the problem. */
export function parseRecent(data: unknown, why = "the launcher's page server sent something else"): RecentAnswer {
  const cant = (problem: string, hint = ""): RecentAnswer => ({ request: null, shows: null, refused: { problem, hint } });
  if (!isTable(data)) return cant(why);
  if (data.state !== "up") return cant(text(data.detail) ?? why);
  if (isTable(data.refused)) return cant(text(data.refused.problem) ?? why, text(data.refused.hint) ?? "");
  const asked = data.request;
  const shows = data.shows;
  if (!isTable(asked) || !isTable(shows) || !Array.isArray(asked.values) || typeof shows.harness !== "string") return cant(why);
  return {
    request: {
      seat: text(asked.seat) || null, project_preset: text(asked.project_preset) || null,
      values: asked.values.map(valueRow).filter((row): row is RecentValue => row !== null),
      leave_out: texts(asked.leave_out), off_posture: asked.off_posture === true,
    },
    shows: {
      harness: shows.harness, model: text(shows.model) || null, effort: text(shows.effort) || null,
      prompt: text(shows.prompt) || null, cwd: text(shows.cwd) ?? "", project_key: text(shows.project_key) || null,
      off_posture: texts(shows.off_posture), danger: texts(shows.danger), not_offered: texts(shows.not_offered),
    },
    refused: null,
  };
}

/** `/api/recent`'s query for the recent `id` with the button's default `run`: its target, and
 * its slot when the default names one. */
export function recentQuery(id: string, run: Run): string {
  const query = new URLSearchParams({ request: id, target: run.target });
  if (run.target === "slot" && run.slot !== null) query.set("slot", String(run.slot));
  return query.toString();
}

/** Where a recent runs again: the button's default, a slot asked for by number when the
 * default names one, since the card was read for that slot. */
export function againRun(run: Run): Run {
  return run.target === "slot" && run.slot !== null ? { ...run, named: true } : run;
}

/** Whether two defaults are the same place. */
export function sameDefault(a: Run, b: Run): boolean {
  if (a.target !== b.target) return false;
  return a.target !== "slot" || (b.target === "slot" && a.slot === b.slot);
}

/** The request as `/api/preview` takes it: its values as the body's table, a list as its
 * items. It names no filled row: the values are that launch's own. */
export function recentBody(request: RecentRequest): LaunchBody {
  const values: Record<string, string | string[]> = {};
  for (const row of request.values) values[row.name] = (row.items ?? row.value) as string | string[];
  const body: LaunchBody = { values, leave_out: [...request.leave_out] };
  if (request.seat !== null) body.seat = request.seat;
  if (request.project_preset !== null) body.project_preset = request.project_preset;
  if (request.off_posture) body.off_posture = true;
  return body;
}

/** The card's line of what runs: `<harness> · <model> · <effort>`, the effort only when one is set. */
export function runsWith(shows: Shows): string {
  return chipFace(shows.harness, shows.model ?? "", shows.effort);
}

/** The preset the recent starts from, as its card names it; "" for none. */
export function presetWords(request: RecentRequest): string {
  const name = request.seat ?? request.project_preset;
  return name === null ? "" : `preset ${name}`;
}

/** Where the recent starts, when that is not the place the line above the box names: `in
 * <key>` for a project, else `in <folder>`; "" when it is the line's. */
export function placeWords(shows: Shows, pick: Pick | null, home: string): string {
  const said = shows.project_key !== null ? `in ${shows.project_key}` : `in ${tilde(shows.cwd, home)}`;
  if (pick === null || pick.kind === "choose") return said;
  if (pick.kind === "project") return shows.project_key === pick.key ? "" : said;
  return shows.project_key === null && tilde(shows.cwd, home) === tilde(pick.path, home) ? "" : said;
}

/** A recent's value of `name` as its request sets it; undefined for a row it does not set. */
function valueOf(request: RecentRequest, name: string): FormValue | undefined {
  const row = request.values.find((value) => value.name === name);
  return row === undefined ? undefined : row.items ?? row.value;
}

/** A row the card names: its label, with its value where the request sets it. */
function rowWord(name: string, request: RecentRequest, fields: readonly PanelField[], home: string,
                 danger: boolean): Word {
  const field = fields.find((row) => row.name === name);
  const label = field === undefined ? name : rowLabel(field);
  const value = valueOf(request, name);
  return { text: field === undefined || value === undefined ? label : `${label} ${fillText(field, value, home)}`, danger };
}

/** The permission rows a card names: those off the user's defaults (`off`), and those that
 * hold a value that can do harm though it is the user's own default (`own`). The ones that can
 * do harm are marked and come first. */
export interface PostureWords {
  off: Word[];
  own: Word[];
}

export function postureWords(shows: Shows, request: RecentRequest, fields: readonly PanelField[],
                             home: string): PostureWords {
  const harm = new Set(shows.danger);
  const off = shows.off_posture.map((name) => rowWord(name, request, fields, home, harm.has(name)));
  const own = shows.danger.filter((name) => !shows.off_posture.includes(name))
    .map((name) => rowWord(name, request, fields, home, true));
  return { off: [...off.filter((word) => word.danger), ...off.filter((word) => !word.danger)], own };
}

/** The lead words of the two lines of permission rows. */
export const OFF_DEFAULTS = "Off your defaults";
export const OWN_DEFAULTS = "From your defaults";

/** `Left out: <labels>` for the rows this install no longer offers, with no module named;
 * null with none. */
export function leftOutWords(shows: Shows, fields: readonly PanelField[]): string | null {
  if (shows.not_offered.length === 0) return null;
  const label = (name: string) => {
    const field = fields.find((row) => row.name === name);
    return field === undefined ? name : rowLabel(field);
  };
  return `Left out: ${shows.not_offered.map(label).join(", ")}`;
}

/** The card's last line: what a click does, with a dim note beside it. `runs` is false when a
 * click does nothing. */
export interface Act {
  text: string;
  beside: string;
  runs: boolean;
}

function sentence(problem: string, hint: string): string {
  const said = problem.replace(/[.\s]+$/, "");
  return hint ? `${said}. ${hint}` : said;
}

/**
 * The card's last line for an answer read with the default `run`: in slot N, with `slot M is in
 * use` beside it when the slot the recent ran in (`ran`) is taken; in a new slot; the command
 * copied; or why it can't run again.
 */
export function cardAct(answer: RecentAnswer, run: Run, ran: Ran,
                        targets: Targets | null): Act {
  if (answer.refused !== null) {
    return { text: `Can't run again: ${sentence(answer.refused.problem, answer.refused.hint)}`, beside: "", runs: false };
  }
  if (run.target === "terminal") return { text: "Click to copy its command", beside: "", runs: true };
  if (run.target === "headless") return { text: "Click to run again headless", beside: "", runs: true };
  if (run.slot === null) return { text: "Click to run again in a new slot", beside: "", runs: true };
  const taken = ran.target === "slot" && ran.slot !== null && ran.slot !== run.slot
    && (targets?.slots ?? []).some((slot) => slot.number === ran.slot && slot.state === "occupied");
  return { text: `Click to run again in slot ${run.slot}`, beside: taken ? `slot ${ran.slot} is in use` : "", runs: true };
}

// What loadout's page and drift block show for each state of their data, and the body each
// act sends, worked out with no DOM so the Node tests can read it. Every string here goes
// into the page as text.

import { FINE } from "./feed.ts";
import type {
  Drift, Failure, Global, GlobalFile, Item, Job, Listing, Outcome, Projects, Row, Section, Status,
} from "./feed.ts";

/** One route's data as its `Feed` holds it. */
export interface FeedState<V> {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  value: V | null;
}

// -- the drift block ----------------------------------------------------------------------

export interface DriftView {
  /**
   * `loading`; `up`; `problem` (the page server read, but loadout's manifest, ledger or
   * config can't be read); `failed` (the route gave no readable answer; the last count
   * read is kept); or `gone` (a 404: loadout went away; nothing is drawn).
   */
  state: "loading" | "up" | "problem" | "failed" | "gone";
  /** The count of items that aren't ok, or null when none is known. */
  count: number | null;
  /** The count's line, or null when none is known. */
  line: string | null;
  /** How the count is coloured: `ok` for none, `warn` for some, `muted` when unknown. */
  tone: "ok" | "warn" | "muted";
  /** The problem or the failure, in one line. */
  detail: string | null;
  /** The harness homes' note, which the count never includes. */
  note: string | null;
}

/** "Every item is ok.", "1 item isn't ok." or "3 items aren't ok." */
export function countLine(count: number): string {
  if (count === 0) return "Every item is ok.";
  return count === 1 ? "1 item isn't ok." : `${count} items aren't ok.`;
}

export function driftView(data: FeedState<Drift>): DriftView {
  const none = { count: null, line: null, tone: "muted", detail: null, note: null } as const;
  if (data.gone) return { state: "gone", ...none };
  const value = data.value;
  const known = value !== null && value.problem === null;
  const counted = known ? { count: value.count, line: countLine(value.count),
                            tone: value.count ? "warn" : "ok" } as const
    : { count: null, line: null, tone: "muted" } as const;
  if (data.failure !== null) {
    return { state: "failed", ...counted, detail: data.failure.why, note: value?.note ?? null };
  }
  if (value === null) return { state: "loading", ...none };
  if (value.problem !== null) {
    return { state: "problem", ...counted, detail: value.problem, note: value.note };
  }
  return { state: "up", ...counted, detail: null, note: value.note };
}

// -- one read on the page -----------------------------------------------------------------

/**
 * Where one of the page's reads stands: `loading`, `up`, `problem` (it read, but the
 * manifest, ledger or config can't be), `failed` (no readable answer; the last value is
 * kept) or `gone` (loadout went away).
 */
export type ReadState = "loading" | "up" | "problem" | "failed" | "gone";

export function readState<V extends { problem: string | null }>(data: FeedState<V>): {
  state: ReadState; detail: string | null;
} {
  if (data.gone) return { state: "gone", detail: null };
  if (data.failure !== null) return { state: "failed", detail: data.failure.why };
  if (data.value === null) return { state: "loading", detail: null };
  if (data.value.problem !== null) return { state: "problem", detail: data.value.problem };
  return { state: "up", detail: null };
}

// -- acts ---------------------------------------------------------------------------------

/** The item kinds the add form offers, as the manifest names them. */
export const KINDS = ["skill", "agent", "hook", "permissions", "mcp"] as const;

/** One act the page sends: its path relative to loadout's page, and its body. */
export interface Act {
  path: string;
  body: Record<string, unknown>;
}

/** How a section is named to an act: the user section, or a project section by name. */
export type Scope = { user: true } | { project: string };

/** The scope of `section`. */
export function scopeOf(section: Section): Scope {
  return section.where === "user" ? { user: true } : { project: section.name };
}

/** How a line names a section. */
export function sectionLabel(section: Section): string {
  if (section.where === "user") return "Your user section";
  if (section.where === "key") return `Project ${section.name}`;
  return `Project at ${section.root ?? section.name}`;
}

/** The id `enable` and `disable` take for one item and harness (`*` for every harness). */
export function itemId(kind: string, name: string, harness: string): string {
  return `${kind}:${name}@${harness}`;
}

/** The act that switches `kind` `name` on (`on`) or off for `harness` in one section. */
export function toggleAct(kind: string, name: string, harness: string, scope: Scope,
                          on: boolean): Act {
  return { path: on ? "api/enable" : "api/disable",
           body: { id: itemId(kind, name, harness), ...scope } };
}

/** The act that adds or removes `kind` `name` in one section. The user section is the
 * one an `add` or `remove` with no project names. */
export function itemAct(verb: "add" | "remove", kind: string, name: string, scope: Scope): Act {
  return { path: `api/${verb}`,
           body: { kind, name, ...("project" in scope ? { project: scope.project } : {}) } };
}

/** The install act for one choice of the install picker: `""` every section, `user` the
 * user section, else `project:<name>`. */
export function installAct(choice: string): Act {
  if (choice === "user") return { path: "api/install", body: { user: true } };
  if (choice.startsWith("project:")) {
    return { path: "api/install", body: { projects: [choice.slice("project:".length)] } };
  }
  return { path: "api/install", body: {} };
}

/** One choice of a picker. */
export interface Choice {
  value: string;
  label: string;
}

/** The install picker's choices: every section, then each section on its own. */
export function installChoices(listing: Listing | null): Choice[] {
  const out: Choice[] = [{ value: "", label: "Every section" }];
  for (const section of listing?.sections ?? []) {
    out.push(section.where === "user" ? { value: "user", label: "Your user section" }
      : { value: `project:${section.name}`, label: sectionLabel(section) });
  }
  return out;
}

/**
 * The add form's section choices: the user section, each project section the manifest
 * has, then each project by key that it has no section for yet (the project picker). A
 * value is `user` or `project:<name>`.
 */
export function addChoices(listing: Listing | null, projects: Projects | null): Choice[] {
  const out: Choice[] = [{ value: "user", label: "Your user section" }];
  const named = new Set<string>();
  for (const section of listing?.sections ?? []) {
    if (section.where === "user") continue;
    named.add(section.name);
    out.push({ value: `project:${section.name}`, label: sectionLabel(section) });
  }
  for (const project of projects?.projects ?? []) {
    if (named.has(project.key)) continue;
    out.push({ value: `project:${project.key}`, label: `Project ${project.key} (new section)` });
  }
  return out;
}

/** The scope of an add form's choice. */
export function choiceScope(choice: string): Scope {
  return choice.startsWith("project:") ? { project: choice.slice("project:".length) }
    : { user: true };
}

/** What a `--replace` names for one row: `<kind>:<name>@<harness>`, else its target. */
export function replaceValue(row: Row): string | null {
  if (row.kind && row.name && row.harness && row.harness !== "*") {
    return itemId(row.kind, row.name, row.harness);
  }
  return row.target;
}

/** Whether a row's occupant can be replaced from the page: a blocked or modified row
 * that names what to replace. */
export function canReplace(row: Row): boolean {
  return (row.state === "blocked" || row.state === "modified") && replaceValue(row) !== null;
}

/** The confirmed replace act for one row, and the lines its sheet shows. */
export function replaceAct(row: Row): Act & { sheet: string[] } {
  const value = replaceValue(row) ?? "";
  const scope = row.scope === null ? {} : row.scope === "user" ? { user: true }
    : { projects: [row.scope] };
  const where = row.target ?? value;
  return {
    path: "api/replace",
    body: { replace: [value], ...scope },
    sheet: [
      `Runs moxi loadout install --replace ${value}.`,
      `What stands at ${where} now moves to Moxi's displaced folder, and loadout places its own there.`,
    ],
  };
}

/** The lines the sheet shows before `global off`: what comes out of each global file. */
export function globalOffSheet(global: Global | null): string[] {
  const lines = ["Runs moxi loadout global off: the global mode becomes off, and loadout takes "
                 + "its own part out of each global file, leaving your text. An edited copy "
                 + "loadout placed moves to Moxi's displaced folder."];
  for (const file of global?.files ?? []) {
    lines.push(file.placed ? `${file.file}: the copy loadout placed is removed.`
      : `${file.file}: loadout's part comes out.`);
  }
  if (global !== null && global.files.length === 0) lines.push("No global file holds a part of loadout's now.");
  return lines;
}

// -- the answer to an act -----------------------------------------------------------------

type Obj = Record<string, unknown>;

/** The text an answer gives: its `refused`, `conflict` or `error`. */
export function saidOf(data: unknown): string | null {
  if (typeof data !== "object" || data === null) return null;
  for (const name of ["refused", "conflict", "error"]) {
    const value = (data as Obj)[name];
    if (typeof value === "string" && value !== "") return value;
  }
  return null;
}

/** The job's status path a 202 gives, relative to loadout's page (`api/job?job=...`), or
 * null when the answer names none. */
export function jobPathOf(status: unknown): string | null {
  if (typeof status !== "string") return null;
  const path = status.replace(/^\/+/, "");
  return /^api\/job\?job=[A-Za-z0-9_-]+$/.test(path) ? path : null;
}

/** What an act's answer came to: a job to follow, or a line saying why not. */
export type Started = { kind: "job"; path: string } | { kind: "refused"; text: string };

/** What a plain act's answer came to. */
export function startedOf(status: number, data: unknown): Started {
  if (status === 202) {
    const path = jobPathOf(typeof data === "object" && data !== null ? (data as Obj).status : null);
    if (path !== null) return { kind: "job", path };
    return { kind: "refused", text: "loadout's page server started a job but gave no path to follow" };
  }
  if (status === 0) return { kind: "refused", text: "no answer from the shell" };
  return { kind: "refused", text: saidOf(data) ?? `loadout's page server answered ${status}` };
}

// -- a job ----------------------------------------------------------------------------------

/** The command a job runs, as the user would type it. */
export function commandLine(argv: readonly string[] | null): string {
  const words = (argv ?? []).filter((word) => word !== "--json");
  return ["moxi", "loadout", ...words].join(" ");
}

/** What an exit code means, as `moxi loadout` sets it. */
export function exitLine(exit: number | null): string {
  switch (exit) {
    case 0: return "Done.";
    case 1: return "It failed (exit 1).";
    case 2: return "The command was refused as written (exit 2).";
    case 3: return "Refused: nothing in its way was changed (exit 3).";
    case 75: return "Busy: another install held the lock. Run it again (exit 75).";
    case null: return "It ended with no exit code.";
    default: return `It ended with exit ${exit}.`;
  }
}

export interface JobView {
  /** `running`, `done`, `interrupted`, or `failed` (the job can't be read). */
  state: "running" | "done" | "interrupted" | "failed";
  /** `ok`, `warn`, `error` or `muted`. */
  tone: "ok" | "warn" | "error" | "muted";
  /** The command the job runs. */
  command: string;
  /** The headline. */
  title: string;
  /** The lines the job and its outcome said, then its notes. */
  lines: string[];
  /** The outcome's rows that aren't ok or off. */
  rows: Row[];
}

function outcomeLines(outcome: Outcome | null): string[] {
  return outcome === null ? [] : [...outcome.lines, ...outcome.notes];
}

/**
 * What the page shows for the job it follows. `lost` means the page stopped reading it
 * after failures in a row: a job last read as running may still run, so the page says it
 * can't tell.
 */
export function jobView(data: FeedState<Job>, lost = false): JobView {
  const job = data.value;
  const command = commandLine(job?.argv ?? null);
  if (job === null || job.state === "running") {
    if (lost || data.gone) {
      const why = data.failure?.why ?? (data.gone ? "loadout went away" : "no answer");
      return { state: "failed", tone: "error", command, lines: [], rows: [],
               title: `The job can't be read now (${why}). It may still be running; read the page again to see what it did.` };
    }
    return { state: "running", tone: "muted", command, title: job === null ? "Starting…" : "Running…",
             lines: [], rows: [] };
  }
  if (job.state === "interrupted") {
    return { state: "interrupted", tone: "warn", command, title: "Interrupted.", lines: [...job.lines],
             rows: [] };
  }
  const rows = (job.outcome?.rows ?? []).filter((row) => !FINE.has(row.state));
  const tone = job.exit === 0 ? "ok" : job.exit === 3 || job.exit === 75 ? "warn" : "error";
  return { state: "done", tone, command, title: exitLine(job.exit),
           lines: [...job.lines, ...outcomeLines(job.outcome)], rows };
}

// -- the items ------------------------------------------------------------------------------

/** One item for one harness. */
export interface Cell {
  harness: string;
  /** The row's state, or null when status gives no row for it. */
  state: string | null;
  reason: string;
  target: string | null;
  /** The item is on for this harness in this section. */
  on: boolean;
  /** The act that switches it the other way; null while the item is off for every
   * harness (only `@*` turns it on then). */
  toggle: Act | null;
  /** The row, when there is one. */
  row: Row | null;
}

export interface ItemView {
  kind: string;
  name: string;
  source: string;
  /** Switched off for every harness here (`@*`). */
  allOff: boolean;
  /** The act that turns it on for every harness, when `allOff`. */
  enableAll: Act | null;
  cells: Cell[];
  remove: Act;
}

/** The items of one section from one source: the folder (`module` null) or a module. */
export interface Group {
  module: string | null;
  items: ItemView[];
}

export interface SectionView {
  key: string;
  label: string;
  section: Section;
  groups: Group[];
}

export interface ItemsView {
  sections: SectionView[];
  /** The status rows no listed item has: a placement no longer wanted, the global file. */
  others: Row[];
}

const rowKey = (scope: string | null, kind: string | null, name: string | null): string =>
  JSON.stringify([scope, kind, name]);

const itemScope = (section: Section): string => section.where === "user" ? "user" : section.name;

/** The module an item comes from, or null for the folder's own. */
export function moduleOf(item: Item): string | null {
  return item.source.startsWith("module ") ? item.source.slice("module ".length) : null;
}

/**
 * The page's items, by section, each item's harnesses with their state and a toggle.
 * The harnesses are the item's own list, else the harnesses status gives it rows for,
 * else every harness status names; a harness the section switches off is always among
 * them. Module skills are grouped by module after the folder's own items.
 */
export function itemsView(listing: Listing | null, status: Status | null): ItemsView {
  const rows = status?.rows ?? [];
  const byItem = new Map<string, Row[]>();
  for (const row of rows) {
    const key = rowKey(row.scope, row.kind, row.name);
    const list = byItem.get(key);
    if (list) list.push(row);
    else byItem.set(key, [row]);
  }
  const every = [...new Set(rows.map((row) => row.harness)
    .filter((harness): harness is string => harness !== null && harness !== "*"))];
  const used = new Set<string>();
  const sections: SectionView[] = [];
  for (const section of listing?.sections ?? []) {
    const scope = scopeOf(section);
    const label = itemScope(section);
    const groups = new Map<string | null, ItemView[]>();
    for (const item of listing?.items ?? []) {
      if (item.scope !== label) continue;
      const key = rowKey(label, item.kind, item.name);
      used.add(key);
      const own = byItem.get(key) ?? [];
      const allOff = item.disabled.includes("*");
      const names = item.harnesses ?? (own.length ? own.map((row) => row.harness ?? "") : every);
      const harnesses = [...new Set([...names, ...item.disabled.filter((h) => h !== "*")])]
        .filter((harness) => harness !== "" && harness !== "*");
      const cells = harnesses.map((harness): Cell => {
        const row = own.find((r) => r.harness === harness) ?? null;
        const on = !allOff && !item.disabled.includes(harness);
        return { harness, state: row?.state ?? null, reason: row?.reason ?? "",
                 target: row?.target ?? null, on, row,
                 toggle: allOff ? null : toggleAct(item.kind, item.name, harness, scope, !on) };
      });
      const view: ItemView = {
        kind: item.kind, name: item.name, source: item.source, allOff, cells,
        enableAll: allOff ? toggleAct(item.kind, item.name, "*", scope, true) : null,
        remove: itemAct("remove", item.kind, item.name, scope),
      };
      const module = moduleOf(item);
      const list = groups.get(module);
      if (list) list.push(view);
      else groups.set(module, [view]);
    }
    const ordered = [...groups.entries()]
      .sort(([a], [b]) => a === b ? 0 : a === null ? -1 : b === null ? 1 : a.localeCompare(b))
      .map(([module, items]) => ({ module, items }));
    sections.push({ key: label, label: sectionLabel(section), section, groups: ordered });
  }
  const others = rows.filter((row) => !used.has(rowKey(row.scope, row.kind, row.name)));
  return { sections, others };
}

/** A line for one cell: its state, and why when it isn't ok. */
export function cellLine(cell: Cell): string {
  if (cell.state === null) return cell.on ? "no state yet" : "off";
  return cell.reason ? `${cell.state}: ${cell.reason}` : cell.state;
}

/** How a state is coloured. */
export function stateTone(state: string | null): "ok" | "warn" | "muted" {
  if (state === null || state === "off") return "muted";
  return state === "ok" ? "ok" : "warn";
}

// -- the global file ------------------------------------------------------------------------

export interface GlobalView {
  mode: string;
  chosen: boolean;
  /** The mode's line. */
  line: string;
  /** The modes a button sets: the ones not in force. */
  modes: Array<{ mode: "managed" | "if-absent"; label: string; act: Act }>;
  /** Whether `global off` is offered: always but when the manifest chose `off`. */
  canTurnOff: boolean;
  files: GlobalFile[];
}

const MODE_LABELS = { "managed": "Managed", "if-absent": "Only where absent" } as const;

export function globalView(global: Global): GlobalView {
  const line = !global.chosen
    ? "The manifest chooses no global mode yet, so it reads off and loadout touches no global file."
    : global.mode === "off" ? "Off: loadout touches no global file."
    : global.mode === "managed" ? "Managed: your text stays, and loadout adds its own part to each global file."
    : "Only where absent: where a harness has no global file, loadout places global.md there.";
  const modes = (["managed", "if-absent"] as const)
    .filter((mode) => !(global.chosen && global.mode === mode))
    .map((mode) => ({ mode, label: MODE_LABELS[mode],
                      act: { path: "api/global-mode", body: { mode } } }));
  return { mode: global.mode, chosen: global.chosen, line, modes,
           canTurnOff: !(global.chosen && global.mode === "off"), files: global.files };
}

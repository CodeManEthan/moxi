// How loadout's page and drift block read their data: one route each, read on demand, with
// every answer that isn't a readable 200 mapped to a state. The shapes are the routes'
// own, as loadout's page server declares them. No DOM and no Lit here, so the Node tests
// drive it with a stubbed `read`.

import type { ApiResult } from "/m/ui/client.js";

/** One item for one harness, or a placement no longer wanted. `state` is one of `STATES`. */
export interface Row {
  /** `user` or a project section's name; null for a placement whose record names none. */
  scope: string | null;
  kind: string | null;
  name: string | null;
  harness: string | null;
  state: string;
  reason: string;
  target: string | null;
  /** `install --replace` places over what stands in its way: an occupant, one git tracks
   * among them, or a file edited by hand. */
  replaceable: boolean;
}

/** One copy a run kept under the core's `displaced/` folder before it wrote over what
 * wasn't loadout's. */
export interface KeptCopy {
  target: string;
  copy: string;
  /** `copied` (loadout copied it, then wrote the target), `replaced` (the placer moved it
   * aside and placed loadout's there) or `moved` (moved aside on a removal). */
  how: string;
  /** The row's `<kind>:<name>@<harness>`; null for a harness file, which can hold several
   * entries. */
  id: string | null;
  /** The command that puts it back, as the verb wrote it for its platform. */
  back: string;
}

/** What a run came to, as `--json` prints it. */
export interface Outcome {
  rows: Row[];
  /** Each copy the run kept; empty for a run that kept none, and for an outcome recorded
   * before runs named them. */
  kept: KeptCopy[];
  /** The lines said on the way, the core lock's waiting line among them. */
  lines: string[];
  notes: string[];
  /** How many rows are in each state; a state no row is in is left out. */
  counts: Record<string, number>;
  exit: number;
}

/** One item a section lists. */
export interface Item {
  kind: string;
  name: string;
  scope: string;
  /** null for every harness. */
  harnesses: string[] | null;
  /** A path, `module <name>` for a module's skill, or `held by the project`. */
  source: string;
  /** The harnesses it is switched off for there, `*` for all. */
  disabled: string[];
}

/** One section of the manifest. An act names a section by `name`. */
export interface Section {
  name: string;
  /** `user`, `path` or `key`. */
  where: string;
  /** A path section's absolute root, else null. */
  root: string | null;
  /** The key of the project a path section's root is in (at the project's root or under
   * it), as projects says; null for any other section, and for every section without
   * projects. */
  project: string | null;
}

/** One global instruction file and what the next install makes of it. */
export interface GlobalFile {
  file: string;
  /** The harness that reads the file. */
  harness: string;
  /** What the harness reads through loadout: the shared file, then its part while in use. */
  parts: string[];
  changes: boolean;
  /** A unified diff of the change, or, for a file `if-absent` placed, why its state isn't ok. */
  diff: string;
  placed: boolean;
  state: string | null;
}

export interface Project {
  key: string;
  root: string;
}

/** `/api/list`: every item each section lists, and the sections. `problem` is set, and the
 * rest empty, when the manifest can't be read. */
export interface Listing {
  problem: string | null;
  manifest: string | null;
  sections: Section[];
  items: Item[];
}

/** `/api/projects`: the projects a section can name by key, or why there are none. */
export interface Projects {
  projects: Project[];
  reason: string | null;
  /** Why there are none: `absent`, `busy`, `unreadable`, `error`; null when it listed (or an
   * answer that names no cause). */
  cause: string | null;
}

/** `/api/drift`: how many items aren't ok, and the harness homes' note. */
export interface Drift {
  problem: string | null;
  count: number;
  /** The page's harness homes aren't the ones the user's own last run used; never counted. */
  note: string | null;
}

/** `/api/job`: one page job. */
export interface Job {
  job: string;
  argv: string[] | null;
  state: "running" | "done" | "interrupted";
  exit: number | null;
  /** The verb's outcome once done, null when it printed none. */
  outcome: Outcome | null;
  lines: string[];
}

/** A row as `/api/view` gives it: `Row`, and what the view adds. */
export interface ViewRow extends Row {
  /** How loadout places the item: `link`, `junction`, `copy`, `generated`, `entry`, or
   * null (loadout's ignore file). */
  form: string | null;
  /** A `blocked` row whose placement of loadout's was edited where it stands. */
  edited: boolean;
  /** On the Codex rules file's row, the permission items it holds; else null. */
  covers: string[] | null;
  /** An entry the user had before loadout, which loadout keeps as the user's. */
  prior: boolean;
  /** The sections under which `install` applies the row: `section`, `whole`, or null when
   * no run does. */
  applies: string | null;
  /** A `would-commit` row that install covers by writing loadout's ignore file. */
  ignores: boolean;
}

/** Why a section can't be placed this run. */
export interface Refusal {
  /** `no-projects`, `not-found`, `unreadable`, `busy`, `no-root` or `unloadable`. */
  cause: string;
  /** The refusal's line, as the verb prints it. */
  line: string;
  /** The root loadout's ledger recorded when it last placed the section, when its
   * records agree on one; else null. */
  last_root: string | null;
}

/** A section as `/api/view` gives it. */
export interface ViewSection extends Section {
  refused: Refusal | null;
}

/** What an item writes into a harness file; a key the kind has no value for is null. */
export interface What {
  event: string | null;
  matcher: string | null;
  command: string | null;
  url: string | null;
  allow: string[] | null;
  ask: string[] | null;
  deny: string[] | null;
}

/** An item as `/api/view` gives it. */
export interface ViewItem extends Item {
  /** The module a module's skill comes from, else null. */
  module: string | null;
  /** What a hook, permissions item or MCP server writes; null for the rest. */
  what: What | null;
}

/** A note on one item for one harness: `code` one of `trust`, `unconfirmed`, `no-hooks`,
 * `writes-nothing`. */
export interface ItemNote {
  kind: string;
  name: string;
  harness: string;
  code: string;
}

/** A global file as `/api/view` gives it. */
export interface ViewGlobalFile extends GlobalFile {
  /** What loadout adds there: `line`, `block`, `instructions`, `placed`, or `left` where
   * the harness's own file is there; null when not known. */
  form: string | null;
}

/** The global file as `/api/view` gives it. */
export interface ViewGlobal {
  /** The folder's global text, null when the manifest names none. */
  source: string | null;
  mode: string;
  chosen: boolean;
  files: ViewGlobalFile[];
}

/** `/api/view`: everything the page's items draw, from one plan. */
export interface View {
  problem: string | null;
  lines: string[];
  notes: string[];
  counts: Record<string, number>;
  /** The harness homes' note; while set, the page's acts are off. */
  note: string | null;
  home: string | null;
  folder: string | null;
  manifest: string | null;
  /** The harness table's names, in its order. */
  harnesses: string[];
  /** Those on this machine, in the table's order: what Add offers. */
  present: string[];
  /** Those the page draws, in the table's order: the present ones, and any other where
   * something of loadout's, or in its way, stands. */
  shown: string[];
  resolved_all: boolean;
  sections: ViewSection[];
  items: ViewItem[];
  rows: ViewRow[];
  item_notes: ItemNote[];
  global: ViewGlobal;
}

/** Every state an item's row can be in. */
export const STATES = ["ok", "missing", "stale", "modified", "blocked", "would-commit",
                       "bad-source", "off"] as const;
/** The states the drift count leaves out. */
export const FINE: ReadonlySet<string> = new Set(["ok", "off"]);

/**
 * The event the page sends on the document once one of its acts has ended, so that every
 * loadout component on the page reads its route again. A component listens for it; it
 * carries no detail.
 */
export const CHANGED = "moxi-loadout-changed";

/** A request that gave no readable value. */
export interface Failure {
  /** The answer's status: 0 when no answer came, 200 for a body that can't be read. */
  status: number;
  /** One line saying what went wrong. */
  why: string;
  /** The answer's own line (its `refused`, `conflict` or `error`), when it gave one. */
  said: string | null;
}

/** How many times a 503 (the page server's in-flight cap) is read again, 1 s apart. */
export const RETRIES = 3;
export const RETRY_MS = 1000;

// -- the shapes ---------------------------------------------------------------------------

type Obj = Record<string, unknown>;

const isObj = (value: unknown): value is Obj =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isStr = (value: unknown): value is string => typeof value === "string";
const isStrOrNull = (value: unknown): value is string | null => value === null || isStr(value);
const isInt = (value: unknown): value is number => Number.isInteger(value);
const isStrings = (value: unknown): value is string[] => Array.isArray(value) && value.every(isStr);
const isListOf = <T>(value: unknown, check: (item: unknown) => item is T): value is T[] =>
  Array.isArray(value) && value.every(check);

function isRow(value: unknown): value is Row {
  return isObj(value) && isStr(value.state) && isStr(value.reason)
    && isStrOrNull(value.scope) && isStrOrNull(value.kind) && isStrOrNull(value.name)
    && isStrOrNull(value.harness) && isStrOrNull(value.target)
    && typeof value.replaceable === "boolean";
}

function isCounts(value: unknown): value is Record<string, number> {
  return isObj(value) && Object.values(value).every(isInt);
}

function isKeptCopy(value: unknown): value is KeptCopy {
  return isObj(value) && isStr(value.target) && isStr(value.copy) && isStr(value.how)
    && isStrOrNull(value.id) && isStr(value.back);
}

function isOutcome(value: unknown): value is Outcome {
  return isObj(value) && isListOf(value.rows, isRow) && isStrings(value.lines)
    && isStrings(value.notes) && isCounts(value.counts) && isInt(value.exit)
    && (value.kept === undefined || isListOf(value.kept, isKeptCopy));
}

/** `outcome` with `kept` as a list: empty when the answer has none. */
function withKept<O extends Outcome>(outcome: O): O {
  return { ...outcome, kept: outcome.kept ?? [] };
}

function isItem(value: unknown): value is Item {
  return isObj(value) && isStr(value.kind) && isStr(value.name) && isStr(value.scope)
    && (value.harnesses === null || isStrings(value.harnesses)) && isStr(value.source)
    && isStrings(value.disabled);
}

function isSection(value: unknown): value is Section {
  return isObj(value) && isStr(value.name) && isStr(value.where) && isStrOrNull(value.root)
    && isStrOrNull(value.project);
}

function isGlobalFile(value: unknown): value is GlobalFile {
  return isObj(value) && isStr(value.file) && isStr(value.harness) && isStrings(value.parts)
    && typeof value.changes === "boolean"
    && isStr(value.diff) && typeof value.placed === "boolean" && isStrOrNull(value.state);
}

function isProject(value: unknown): value is Project {
  return isObj(value) && isStr(value.key) && isStr(value.root);
}

const isBool = (value: unknown): value is boolean => typeof value === "boolean";
const isStringsOrNull = (value: unknown): value is string[] | null =>
  value === null || isStrings(value);

function isViewRow(value: unknown): value is ViewRow {
  return isRow(value) && isObj(value) && isStrOrNull(value.form) && isBool(value.edited)
    && isStringsOrNull(value.covers) && isBool(value.prior) && isStrOrNull(value.applies)
    && isBool(value.ignores);
}

function isRefusal(value: unknown): value is Refusal {
  return isObj(value) && isStr(value.cause) && isStr(value.line) && isStrOrNull(value.last_root);
}

function isViewSection(value: unknown): value is ViewSection {
  return isSection(value) && isObj(value) && (value.refused === null || isRefusal(value.refused));
}

function isWhat(value: unknown): value is What {
  return isObj(value) && isStrOrNull(value.event) && isStrOrNull(value.matcher)
    && isStrOrNull(value.command) && isStrOrNull(value.url) && isStringsOrNull(value.allow)
    && isStringsOrNull(value.ask) && isStringsOrNull(value.deny);
}

function isViewItem(value: unknown): value is ViewItem {
  return isItem(value) && isObj(value) && isStrOrNull(value.module)
    && (value.what === null || isWhat(value.what));
}

function isItemNote(value: unknown): value is ItemNote {
  return isObj(value) && isStr(value.kind) && isStr(value.name) && isStr(value.harness)
    && isStr(value.code);
}

function isViewGlobal(value: unknown): value is ViewGlobal {
  return isObj(value) && isStrOrNull(value.source) && isStr(value.mode) && isBool(value.chosen)
    && isListOf(value.files, (file: unknown): file is ViewGlobalFile =>
      isGlobalFile(file) && isObj(file) && isStrOrNull(file.form));
}

/** `/api/view`'s value, or null when it isn't one. */
export function parseView(data: unknown): View | null {
  if (!isObj(data) || !isStrOrNull(data.problem) || !isStrings(data.lines)
      || !isStrings(data.notes) || !isCounts(data.counts) || !isStrOrNull(data.note)
      || !isStrOrNull(data.home) || !isStrOrNull(data.folder) || !isStrOrNull(data.manifest)
      || !isStrings(data.harnesses) || !isStrings(data.present) || !isStrings(data.shown)
      || !isBool(data.resolved_all)
      || !isListOf(data.sections, isViewSection) || !isListOf(data.items, isViewItem)
      || !isListOf(data.rows, isViewRow) || !isListOf(data.item_notes, isItemNote)
      || !isViewGlobal(data.global)) return null;
  return {
    problem: data.problem, lines: data.lines, notes: data.notes, counts: data.counts,
    note: data.note, home: data.home, folder: data.folder, manifest: data.manifest,
    harnesses: data.harnesses, present: data.present, shown: data.shown,
    resolved_all: data.resolved_all, sections: data.sections,
    items: data.items, rows: data.rows, item_notes: data.item_notes, global: data.global,
  };
}

/** A skill or agent file in the folder that no section lists. */
export interface Unlisted {
  kind: string;
  name: string;
  source: string;
}

/** `/api/folder`: the loadout folder's files git hasn't committed, its files no section
 * lists, and whether a commit installs it again. */
export interface SourceDir {
  /** `ok`, or `error` when git failed or didn't answer (`detail` says which). */
  state: string;
  detail: string | null;
  /** At most 20 names relative to the folder; null outside a git work tree. */
  uncommitted: string[] | null;
  /** How many uncommitted files past those. */
  more: number;
  unlisted: Unlisted[];
  reinstalls: boolean;
}

function isUnlisted(value: unknown): value is Unlisted {
  return isObj(value) && isStr(value.kind) && isStr(value.name) && isStr(value.source);
}

/** `/api/folder`'s value, or null when it isn't one. */
export function parseSourceDir(data: unknown): SourceDir | null {
  if (!isObj(data) || !isStr(data.state) || !isStrOrNull(data.detail)
      || !(data.uncommitted === null || isStrings(data.uncommitted)) || !isInt(data.more)
      || !isListOf(data.unlisted, isUnlisted) || !isBool(data.reinstalls)) return null;
  return { state: data.state, detail: data.detail, uncommitted: data.uncommitted, more: data.more,
           unlisted: data.unlisted, reinstalls: data.reinstalls };
}

/** One path a Replace's diff leaves out: `why` one of `link`, `large`, `binary`, `many`. */
export interface Skipped {
  path: string;
  why: string;
}

/** `/api/replace-preview`: what a Replace of one row would do, for its confirm. */
export interface ReplacePreview {
  problem: string | null;
  /** `moves` (what is there moves aside under `displaced/`), `keeps` (a copy of the file
   * is kept there, then it is written), or null when the row isn't replaceable. */
  case: string | null;
  target: string | null;
  /** The folder the copy goes to. */
  kept_in: string | null;
  /** How many other rows the run changes. */
  others: number;
  /** `file`, or `entry` for an MCP server its harness's CLI writes. */
  label: string;
  /** A unified diff of what is there (-) against what loadout writes (+). */
  diff: string;
  lines: number;
  skipped: Skipped[];
}

/** `/api/replace-preview`'s value, or null when it isn't one. */
export function parseReplacePreview(data: unknown): ReplacePreview | null {
  if (!isObj(data) || !isStrOrNull(data.problem) || !isStrOrNull(data.case) || !isStrOrNull(data.target)
      || !isStrOrNull(data.kept_in) || !isInt(data.others) || !isStr(data.label)
      || !isStr(data.diff) || !isInt(data.lines)
      || !isListOf(data.skipped, (s: unknown): s is Skipped => isObj(s) && isStr(s.path) && isStr(s.why))) {
    return null;
  }
  return { problem: data.problem, case: data.case, target: data.target, kept_in: data.kept_in,
           others: data.others, label: data.label, diff: data.diff,
           lines: data.lines, skipped: data.skipped };
}

/** `/api/list`'s value, or null when it isn't one. */
export function parseListing(data: unknown): Listing | null {
  if (!isObj(data) || !isStrOrNull(data.problem) || !isStrOrNull(data.manifest)
      || !isListOf(data.sections, isSection) || !isListOf(data.items, isItem)) return null;
  return { problem: data.problem, manifest: data.manifest, sections: data.sections, items: data.items };
}

/** `/api/projects`'s value, or null when it isn't one. */
export function parseProjects(data: unknown): Projects | null {
  if (!isObj(data) || !isListOf(data.projects, isProject) || !isStrOrNull(data.reason)) return null;
  const cause = data.cause === undefined ? null : data.cause;
  if (!isStrOrNull(cause)) return null;
  return { projects: data.projects, reason: data.reason, cause };
}

/** `/api/drift`'s value, or null when it isn't one. */
export function parseDrift(data: unknown): Drift | null {
  if (!isObj(data) || !isStrOrNull(data.problem) || !isInt(data.count) || data.count < 0
      || !isStrOrNull(data.note)) return null;
  return { problem: data.problem, count: data.count, note: data.note };
}

/** `/api/job`'s value, or null when it isn't one. */
export function parseJob(data: unknown): Job | null {
  if (!isObj(data) || !isStr(data.job) || !isStrings(data.lines)) return null;
  const { state, exit, outcome, argv } = data;
  if (state !== "running" && state !== "done" && state !== "interrupted") return null;
  if (exit !== null && !isInt(exit)) return null;
  if (outcome !== null && !isOutcome(outcome)) return null;
  if (argv !== null && !isStrings(argv)) return null;
  return { job: data.job, argv, state, exit, outcome: outcome === null ? null : withKept(outcome),
           lines: data.lines };
}

/** The text an answer gives: its `refused`, `conflict` or `error`. */
export function saidOf(data: unknown): string | null {
  if (!isObj(data)) return null;
  for (const name of ["refused", "conflict", "error"]) {
    const value = data[name];
    if (isStr(value) && value !== "") return value;
  }
  return null;
}

// -- reading ------------------------------------------------------------------------------

/** The line a failure shows. */
export function failureWhy(status: number): string {
  if (status === 0) return "no answer from the shell";
  if (status === 200) return "loadout's page server sent an answer that can't be read";
  return `loadout's page server answered ${status}`;
}

type Wait = (ms: number) => Promise<void>;
const sleep: Wait = (ms) => new Promise((done) => setTimeout(done, ms));

/**
 * One route's data. `refresh()` reads the route; `onChange` is called after every read.
 *
 * - A 200 with a readable body gives the value.
 * - A 503 is read again after 1 s, up to three times; then it is a failure.
 * - A 404 means the module went away while the page was open: `gone`.
 * - Every other answer (any other status, a network failure, a body that isn't JSON or
 *   isn't the route's shape) is a failure, and the last value is kept.
 */
export class Feed<V> {
  /** The last value read, or null before the first. */
  value: V | null = null;
  /** The last request's failure, null once a read succeeds. */
  failure: Failure | null = null;
  /** The route answered 404. */
  gone = false;
  /** No read has finished yet. */
  loading = true;
  /** A read is running. */
  reading = false;

  #read: () => Promise<ApiResult>;
  #parse: (data: unknown) => V | null;
  #onChange: () => void;
  #wait: Wait;
  #running: Promise<void> | null = null;
  #again = false;
  #closed = false;

  constructor(read: () => Promise<ApiResult>, parse: (data: unknown) => V | null,
              onChange: () => void, { wait = sleep }: { wait?: Wait } = {}) {
    this.#read = read;
    this.#parse = parse;
    this.#onChange = onChange;
    this.#wait = wait;
  }

  /** Stops every change: a later answer is dropped. */
  close(): void {
    this.#closed = true;
  }

  /** Reads the route; a call while a read runs makes one more read after it. */
  refresh(): Promise<void> {
    if (this.#running) {
      this.#again = true;
      return this.#running;
    }
    this.reading = true;
    this.#running = (async () => {
      try {
        do {
          this.#again = false;
          await this.#once();
        } while (this.#again && !this.#closed);
      } finally {
        this.#running = null;
        this.reading = false;
        if (!this.#closed) this.#onChange();
      }
    })();
    return this.#running;
  }

  async #once(): Promise<void> {
    let answer = await this.#read();
    for (let tries = 0; answer.status === 503 && tries < RETRIES && !this.#closed; tries += 1) {
      await this.#wait(RETRY_MS);
      if (this.#closed) return;
      answer = await this.#read();
    }
    if (this.#closed) return;
    this.loading = false;
    const value = answer.status === 200 ? this.#parse(answer.data) : null;
    if (value !== null) {
      this.value = value;
      this.failure = null;
      this.gone = false;
    } else if (answer.status === 404) {
      this.gone = true;
      this.failure = null;
    } else {
      this.failure = { status: answer.status, why: failureWhy(answer.status),
                       said: saidOf(answer.data) };
    }
  }
}

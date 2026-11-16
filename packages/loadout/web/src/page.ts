// <moxi-loadout-page route="...">: loadout's panel. Under the shell's top line, from top to
// bottom, each group with its label in the left column:
//
// - `This page`, only while the harness homes' note is set: the page's acts are off, and
//   each act shows as the command he would type, with `Copy`.
// - `Placed`: the verdict, then each problem in three lines (what, where, the fix and its
//   act), then the counts per harness; with nothing needing him, the counts sit at the
//   verdict's right.
// - `Folder`: the loadout folder, its files git hasn't committed, and `Settings › loadout`.
// - `Your items`, a group per module whose skills it took over, then each project
//   section: one row per item and a column per harness of the harness table, each row
//   with `Open`; past twelve items a section folds by kind, and a kind opens in place with
//   its items. The global file is Your items' last row.
// - `Not listed`: each skill or agent file in the folder that no section lists, with Add,
//   which opens in place with its section picker and `Add and place`; and Add by name,
//   always, under an opened Add or at the group's foot.
// - The dim foot lines: the view's notes, why the folder can't be read, and the line for an
//   address whose item is gone.
//
// At `project/<key>` the page is filtered to what sessions in that project load: under a
// line naming the project (a menu of every project) with `All of loadout` at its right,
// Placed counts only the project's section and Your items, then the project's section
// (`<n> of its own`, with a link to the project on the Projects page), `Yours too` (Your
// items) and the skills modules took over. Folder and Not listed are hidden. The page reads
// `/api/projects` for it, and shows all of loadout with a dim line while projects can't say
// which projects there are.
//
// One thing is open at a time: an item (its From line, a line per harness with its act,
// Remove), a kind, the global file (its mode, its files, Turn off and its confirm), Add, or
// Replace's confirm, in place under its problem or harness line, which reads
// `/api/replace-preview` as it opens and closes once its job ends. The page writes what is
// open into its address with `history.replaceState`.
//
// It places what the models (`placed-model.ts`, `items-model.ts`, `opened-model.ts`,
// `job-model.ts`, `frame-model.ts`) return; every word comes from there. No accent and no
// warning colour: a problem rises by its order and weight 600.
//
// It reads `/api/view` and `/api/folder` when it opens, when its tab is shown again, every
// 60 seconds while shown, and after a job ends. An act runs as a page job: the page sends
// it, follows the job's status path, and keeps every act's control off while one runs. One
// dim line under the act names the command and how it ended, with `Show` for the job's
// lines and rows not fine, and what the job kept with the way back, open; it stays until
// the next act ends. The page keeps the job's path in the tab's `sessionStorage` until the
// job ends, so after a reload it follows the job again. When a job ends the page reads
// again and sends `CHANGED` on the document, so every loadout component reads again too.

import { css, html, LitElement, nothing } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { api, navigate } from "/m/ui/client.js";

import { CHANGED, Feed, parseJob, parseProjects, parseReplacePreview, parseSourceDir, parseView } from "./feed.ts";
import type { Job, Projects, ReplacePreview, SourceDir, View, ViewRow } from "./feed.ts";
import { addAct, commandOf, globalOffAct, installAct, jobId, replaceAct } from "./acts-model.ts";
import { ADD_KINDS, ADD_NOTE, BY_NAME, notListed, pickerOf } from "./add-model.ts";
import type { NotListedRow, Picker } from "./add-model.ts";
import { confirmView, replaceRows } from "./confirm-model.ts";
import type { PageAct } from "./acts-model.ts";
import {
  ALL_OF_LOADOUT, closed, filteredView, goneLine, goneLineOf, itemAddress, NOTHING_OPEN, openKind, opensItem, parseRoute,
  pathToWrite, PROJECT_LABEL, PROJECT_LINK, projectChoices, sameAddress, unfilteredLine,
} from "./address-model.ts";
import type { Address, FilteredView } from "./address-model.ts";
import {
  actCommand, COPIED, COPIED_MS, COPY, emptyGlobal, emptyLines, failedLine, fixLine, folderView, footLines,
  GLOBAL_PLACE, GONE, INSTALL_ALL_PLACE, itemPlace, jobPlace, noteGroup, pageState, PLACED_PLACE, problemPlace,
  READING, READING_AFTER_MS, REFRESH_MS, SETTINGS_LINK, SETTINGS_ROUTE,
} from "./frame-model.ts";
import { itemsView, scopeOf } from "./items-model.ts";
import type { CellView, GlobalRow, GroupView, ItemRow, KindRow, Tone } from "./items-model.ts";
import { followLine, keptLines, showView } from "./job-model.ts";
import type { Unfollowed } from "./job-model.ts";
import { fromParts, openedGlobal, openedItem, replaceArgv } from "./opened-model.ts";
import type { HarnessLine, OpenedGlobal, OpenedItem } from "./opened-model.ts";
import { INSTALL_ALL, placedView, problemsOf } from "./placed-model.ts";
import type { PlacedView, Problem, ProblemAct } from "./placed-model.ts";
import { harnessWord, kindFolded, kindWord } from "./words.ts";
import { jobRefusal, parseKept, startedOf } from "./views.ts";
import type { Kept } from "./views.ts";

const TAG = "moxi-loadout-page";

/** How often a running job is read. */
const JOB_POLL_MS = 1000;
/** After this many failed reads in a row the page stops reading a job. */
const JOB_TRIES = 5;
/** The `sessionStorage` key of the job the page follows. */
const KEPT = "moxi-loadout-job";

/** Keeps `job` as the one the tab follows, or forgets it. Without storage, the page
 * follows a job only while it stays open. */
function keep(job: Kept | null): void {
  try {
    if (job === null) sessionStorage.removeItem(KEPT);
    else sessionStorage.setItem(KEPT, JSON.stringify(job));
  } catch {
    // Storage is off or full.
  }
}

/** The job the tab follows, if one is kept. */
function kept(): Kept | null {
  try {
    return parseKept(sessionStorage.getItem(KEPT));
  } catch {
    return null;
  }
}

/** One act the page sent: the command it runs, where on the page it was pressed, and its
 * job as last read; or why it didn't start. */
interface Sent {
  command: string;
  place: string | null;
  job: Job | null;
  stopped: Unfollowed;
  /** Why the act didn't start; null once it did. */
  said: string | null;
  /** What the page opens once the job ends, if what was open when it was sent (`from`)
   * is open still: Replace's confirm and Add close. */
  from: Address;
  after: Address | null;
}

/** The foot line for an address whose thing is gone, and the address the page wrote in its
 * place. The shell may place the page again for that address, as a new element; the line
 * stays on it until he opens something or an act ends. */
let goneHeld: { at: Address; line: string } | null = null;

/** A fresh job id for one press. */
const freshJob = (): string => jobId(crypto.getRandomValues(new Uint8Array(16)));

/** The place of Add's line under its act, while Add is open. */
const ADD_PLACE = "add";

const TONES: Record<Tone, string> = { plain: "", dim: "d", heavy: "h" };

// The Sessions page's groups at its width: a dim label at the left, text tokens only.
const STYLES = css`
  :host { display: block; min-width: 0; max-width: 680px; margin: 0 auto; padding: 4px 0 16px;
          color: var(--moxi-text); font-family: var(--moxi-font-sans); font-size: 14px; line-height: 20px; }
  /* The accent is the focus ring alone, drawn only on a focused piece. */
  * { box-sizing: border-box; outline-color: var(--moxi-accent); }
  p { margin: 0; overflow-wrap: anywhere; }
  button { font: inherit; color: inherit; background: none; border: 0; padding: 0; margin: 0; cursor: pointer;
           text-align: left; }
  button:disabled { cursor: not-allowed; color: var(--moxi-text-dim); }
  a { color: inherit; text-decoration: none; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  code { font: 12px/18px var(--moxi-font-mono); color: var(--moxi-text); overflow-wrap: anywhere; }
  pre { font: 12px/18px var(--moxi-font-mono); color: var(--moxi-text); margin: 6px 0 2px; padding: 7px 10px;
        border: 1px solid var(--moxi-border); border-radius: 7px; white-space: pre; overflow-x: auto; }
  .d, .dim { color: var(--moxi-text-dim); }
  .h { font-weight: 600; }

  .waiting { padding: 12px 2px 0; color: var(--moxi-text-dim); }
  .grps { margin-top: 16px; border-top: 1px solid var(--moxi-border); }
  .grp { display: grid; grid-template-columns: 96px minmax(0, 1fr); border-bottom: 1px solid var(--moxi-border);
         padding: 12px 2px; }
  .grp .gk { color: var(--moxi-text-dim); }
  .grp .gk .n { display: block; font-size: 13px; line-height: 18px; }
  .grp .gl { grid-column: 2; min-width: 0; }

  .line { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; min-width: 0; }
  .line > :first-child { min-width: 0; overflow-wrap: anywhere; }
  .line > .r { flex: none; text-align: right; }
  .sm { font-size: 13px; line-height: 18px; }
  .act { flex: none; font-size: 13px; line-height: 18px; color: var(--moxi-text); }
  .act.d { color: var(--moxi-text-dim); }

  .prob { padding-top: 14px; }
  .prob .l1 b { font-weight: 600; }
  .counts { padding-top: 14px; }
  .cmd { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; padding-top: 2px; }

  /* A line with a name and its act at the right (Not listed). In the item grid, as drawn:
     the name, a 94 px column per harness (--h of them), then Open in 44 px, a line every
     34 px. The kind word wraps whole, never inside itself. */
  .ir { display: flex; align-items: baseline; gap: 12px; padding: 7px 0; }
  .grid .ir { display: grid; grid-template-columns: minmax(0, 1fr) repeat(var(--h, 3), 94px) 44px; gap: 0; }
  .ir.hd { color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; padding: 0 0 4px; }
  .ir .nm { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
  .ir .nm .k { display: inline-block; margin-left: 8px; overflow-wrap: normal; }
  .ir .c { flex: 0 0 94px; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ir .c.k { font-variant-numeric: tabular-nums; }
  .ir .o { flex: 0 0 44px; text-align: right; font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  .two { margin-top: -7px; padding-bottom: 7px; font-size: 13px; line-height: 18px; }
  .kopen { border-top: 1px solid var(--moxi-border); border-bottom: 1px solid var(--moxi-border); margin: 3px 0 5px;
           padding: 2px 0 3px; }

  /* An opened item or global file, under its row. */
  .det { margin: 5px 0 6px; padding: 8px 0 10px; border-top: 1px solid var(--moxi-border);
         border-bottom: 1px solid var(--moxi-border); font-size: 13px; line-height: 18px; }
  .det .r { display: grid; grid-template-columns: 92px minmax(0, 1fr) auto; gap: 0 14px; padding: 3px 0;
            align-items: baseline; }
  .det .r .k { color: var(--moxi-text-dim); }
  .det .r .f { min-width: 0; overflow-wrap: anywhere; }
  .det .r .f .sub { display: block; color: var(--moxi-text-dim); }
  .det .r .x { color: var(--moxi-text-dim); }
  .det .r .x.act { color: var(--moxi-text); }
  .det .r .f .cmd { padding-top: 4px; }
  .det .foot { display: flex; flex-wrap: wrap; gap: 4px 18px; padding: 9px 0 0 106px; }
  .det .foot + .foot { padding-top: 4px; }
  .det .foot .cmd { flex-basis: 100%; }
  .tri { display: inline-flex; flex-wrap: wrap; margin: -5px 0 -5px -8px; }
  .tri > * { padding: 5px 7px; border: 1px solid transparent; border-radius: 6px; color: var(--moxi-text-dim);
             white-space: nowrap; }
  .tri > .s { color: var(--moxi-text); border-color: var(--moxi-border); }
  .cf { margin: 9px 0 4px; border-top: 1px solid var(--moxi-border); padding-top: 10px; font-size: 14px; line-height: 20px; }
  .cf.in { margin: 10px 0 2px 106px; }
  .cf.plain { border-top: 0; padding-top: 4px; }
  .cf .cr { display: grid; grid-template-columns: 76px minmax(0, 1fr); font-size: 13px; line-height: 18px; padding: 3px 0; }
  .cf .cr .k { color: var(--moxi-text-dim); }
  .cf .cr > span { min-width: 0; overflow-wrap: anywhere; }
  .cf .cap { display: block; margin-top: 2px; }
  .pick { font: inherit; color: var(--moxi-text); background: none; border: 1px solid var(--moxi-border);
          border-radius: 7px; padding: 3px 9px; max-width: 100%; }
  .ir .o.add { color: var(--moxi-text); }
  .byname { padding: 4px 0; }
  .ir + .byname { padding-top: 6px; }
  .cf .q { font-size: 14px; line-height: 20px; font-weight: 600; padding-bottom: 4px; }
  .cf .btns { display: flex; gap: 18px; align-items: center; padding: 10px 0 2px; }
  .btn { font-size: 14px; line-height: 20px; color: var(--moxi-text); border: 1px solid var(--moxi-border);
         border-radius: 7px; padding: 5px 13px; }
  .lnk { font-size: 14px; line-height: 20px; color: var(--moxi-text-dim); }

  /* The line under an act once its job ends, what it kept, and what Show opens. */
  .job { padding-top: 6px; font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  .job .line { gap: 18px; }
  .job .kept { padding-top: 4px; }
  .job .kept .back { padding-top: 2px; }
  .job .more { padding: 4px 0 0 12px; }
  .job .more .p + .p { padding-top: 4px; }
  .job .more .p .w { font-weight: 600; color: var(--moxi-text); }

  .quietline { font-size: 22px; line-height: 32px; text-align: center; letter-spacing: -0.005em; padding: 120px 0 14px; }
  .first { color: var(--moxi-text-dim); line-height: 1.6; text-align: center; }
  .first p { max-width: 520px; margin: 0 auto; }
  .first + .grps { margin-top: 40px; }
  .foot { font-size: 13px; line-height: 18px; padding: 12px 2px 0; color: var(--moxi-text-dim); overflow-wrap: anywhere; }

  /* The filtered view's line: the project, its menu, and All of loadout at the right. */
  .filt { position: relative; display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 14px;
          margin-top: 12px; padding: 0 2px; font-size: 14px; line-height: 20px; color: var(--moxi-text-dim); }
  .filt .fl { display: flex; flex-wrap: wrap; row-gap: 4px; min-width: 0; }
  .filt .k { margin-right: 8px; }
  .filt .sep { margin: 0 12px; }
  .filt button.set { color: var(--moxi-text); white-space: nowrap; }
  .filt .v { position: relative; top: 1px; margin-left: 6px; font-size: 15px; line-height: 0; color: var(--moxi-text-dim); }
  .filt + .grps { margin-top: 16px; }
  .anchor { position: relative; display: inline-block; }
  .menu { position: absolute; z-index: 2; left: 0; top: calc(100% + 8px); width: max-content; min-width: 240px;
          max-width: min(400px, calc(100vw - 32px)); padding: 6px 0; border: 1px solid var(--moxi-border-strong);
          border-radius: 10px; background: var(--moxi-bg); color: var(--moxi-text); white-space: normal; }
  .menu button.it { display: grid; grid-template-columns: 20px 1fr auto; align-items: baseline; width: 100%;
                    padding: 7px 14px; overflow-wrap: anywhere; }
  .menu button.it:hover { background: var(--moxi-bg-hover); }
  .menu .t { padding-left: 16px; color: var(--moxi-text-dim); }
  .grp .gk .pl { display: block; font-size: 13px; line-height: 18px; }

  @media (max-width: 720px) {
    :host { padding-top: 10px; }
    .grp { display: block; padding: 8px 0 10px; }
    .grp .gk { padding-bottom: 6px; font-size: 13px; }
    .grp .gk .n { display: inline; margin-left: 8px; }
    .grp .gk .pl { display: inline; margin-left: 8px; }
    .ir { gap: 8px; }
    /* Narrow, an item's name takes its own line, so no word of it breaks, and the cells
       share the line under it; the head row keeps only the harnesses. */
    .grid .ir { display: flex; flex-wrap: wrap; gap: 2px 8px; }
    .grid .ir .nm { flex: 1 0 100%; }
    .grid .ir.hd .nm { display: none; }
    .grid .ir .c { flex: 1 1 0; font-size: 13px; }
    .det .r { grid-template-columns: 76px minmax(0, 1fr) auto; gap: 0 10px; }
    .det .foot { padding-left: 86px; }
    .cf.in { margin-left: 0; }
    .quietline { padding-top: 60px; }
  }
`;

export class LoadoutPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";

  #view: Feed<View> | null = null;
  #dir: Feed<SourceDir> | null = null;
  #openedAt = 0;

  /** What is open, and the dim foot line for an address whose item is gone. */
  #at: Address = NOTHING_OPEN;
  /** The project the page is filtered to, from a `project/<key>` address; while set, the
   * page's address with nothing else open is that one. */
  #filter: string | null = null;
  /** The filter line's project menu is open. */
  #menu = false;
  #goneSaid: string | null = null;
  /** The address came from the route and the view hasn't been read against it yet. */
  #fromRoute = false;
  /** The opened global file's Turn off confirm is open. */
  #confirmOff = false;
  /** The opened global file's files whose diff `Show` opened, by harness. */
  #diffs = new Set<string>();
  /** The item Replace's confirm was opened from, drawn open around it; null when it was
   * opened from Placed or its address. */
  #replaceItem: Address | null = null;
  /** The preview the open confirm reads, by its address's route. */
  #preview: { route: string; feed: Feed<ReplacePreview> } | null = null;
  /** The confirm draws its whole diff. */
  #wholeDiff = false;
  /** `/api/projects`, read each time Add opens. */
  #projects: Feed<Projects> | null = null;
  /** Add's choices: the section (the picker's value), and by name its kind and name. */
  #addTo = "";
  #addKind: string = ADD_KINDS[0];
  #addName = "";

  /** The job the page follows, and whether it has ended. */
  #job: Feed<Job> | null = null;
  #jobEnded = true;
  #jobFails = 0;
  #jobTimer: ReturnType<typeof setTimeout> | undefined;
  /** An act is being sent. */
  #sending = false;
  /** The act whose job runs, and the last act that ended (or didn't start). */
  #running: Sent | null = null;
  #last: Sent | null = null;
  /** `Show` is open under the last act's line. */
  #showing = false;

  /** Which copy act reads `Copied`. */
  #copied: string | null = null;
  #copyTimer: ReturnType<typeof setTimeout> | undefined;
  #waitTimer: ReturnType<typeof setTimeout> | undefined;
  #refreshTimer: ReturnType<typeof setInterval> | undefined;

  #shown = (): void => {
    if (document.visibilityState === "visible") this.#readAll();
  };

  /** A press outside the open project menu closes it. */
  #outside = (event: Event): void => {
    if (!this.#menu) return;
    const inside = event.composedPath().some((node) => node instanceof Element && node.classList.contains("anchor"));
    if (!inside) {
      this.#menu = false;
      this.requestUpdate();
    }
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#openedAt = Date.now();
    const changed = (): void => this.requestUpdate();
    this.#view = new Feed<View>(() => api("loadout", "api/view"), parseView, changed);
    this.#dir = new Feed<SourceDir>(() => api("loadout", "api/folder"), parseSourceDir, changed);
    this.#readAll();
    document.addEventListener("visibilitychange", this.#shown);
    document.addEventListener("click", this.#outside);
    this.#waitTimer = setTimeout(() => this.requestUpdate(), READING_AFTER_MS);
    this.#refreshTimer = setInterval(() => {
      if (document.visibilityState === "visible") this.#readAll();
    }, REFRESH_MS);
    const job = kept();
    if (job !== null) {
      this.#running = { command: job.label, place: job.anchor, job: null, stopped: null, said: null,
                        from: NOTHING_OPEN, after: null };
      this.#follow(job.path);
    }
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    for (const feed of [this.#view, this.#dir, this.#job, this.#projects, this.#preview?.feed]) feed?.close();
    this.#preview = null;
    this.#projects = null;
    this.#view = this.#dir = null;
    this.#job = null;
    clearTimeout(this.#jobTimer);
    clearTimeout(this.#copyTimer);
    clearTimeout(this.#waitTimer);
    clearInterval(this.#refreshTimer);
    document.removeEventListener("visibilitychange", this.#shown);
    document.removeEventListener("click", this.#outside);
  }

  protected willUpdate(changed: Map<PropertyKey, unknown>): void {
    if (changed.has("route")) {
      const address = parseRoute(this.route);
      // The address the page wrote for a gone one, given back: its line stays.
      this.#goneSaid = goneHeld !== null && sameAddress(address, goneHeld.at) ? goneHeld.line : null;
      this.#fromRoute = true;
      this.#filter = address.piece === "project" ? address.key : null;
      this.#menu = false;
      if (this.#filter !== null) this.#readProjects();
      this.#setAt(address);
      this.#replaceItem = null;
    }
    // A filtered view's project is judged once projects is read and says which there are.
    const listed = this.#listedProjects();
    const read = this.#view?.value ?? null;
    if (this.#filter !== null && listed !== null && read !== null) {
      const gone = goneLine({ piece: "project", key: this.#filter }, read, [], null, listed);
      if (gone !== null) {
        this.#filter = null;
        this.#setAt(this.#at.piece === "project" ? NOTHING_OPEN : this.#at);
        this.#sayGone(gone);
      }
    }
    // An address whose item is gone opens the page with nothing open, and says so; an
    // item that goes while it is open (a Remove) just closes.
    const view = this.#view?.value ?? null;
    const dir = this.#dir?.value ?? null;
    // Add on a file is judged once the folder is read.
    const waits = this.#at.piece === "add" && this.#at.kind !== null && dir === null;
    if (view !== null && view.problem === null && !waits) {
      const unlisted = dir === null ? null : new Set(notListed(dir, view).map((row) => row.key));
      const gone = goneLineOf(this.#at, view, problemsOf(view), unlisted);
      if (gone !== null) {
        this.#setAt(NOTHING_OPEN);
        if (this.#fromRoute) this.#sayGone(gone);
      }
      this.#fromRoute = false;
    }
  }

  /** Shows `line` at the foot for the address just left, gone, and holds it for the
   * address written in its place. */
  #sayGone(line: string): void {
    this.#goneSaid = line;
    goneHeld = { at: this.#at, line };
  }

  #readAll(): void {
    void this.#view?.refresh();
    void this.#dir?.refresh();
    if (this.#filter !== null) void this.#projects?.refresh();
  }

  /** The projects `/api/projects` lists, once it has said which there are (none when
   * projects isn't installed); null while it isn't read, or can't say (busy, unreadable). */
  #listedProjects(): Projects["projects"] | null {
    const projects = this.#projects?.value ?? null;
    if (projects === null) return null;
    return projects.cause === null || projects.cause === "absent" ? projects.projects : null;
  }

  /** Why the filtered view can't be drawn now: the page then shows all of loadout. */
  #unfilteredWhy(): string | null {
    const feed = this.#projects;
    if (this.#filter === null || feed === null || this.#listedProjects() !== null) return null;
    const cause = feed.value?.cause ?? (feed.failure !== null ? "unreadable" : null);
    return cause === null ? null : unfilteredLine(this.#filter, cause);
  }

  /** Whether an act is on its way or its job runs: every act's control is off then. */
  get #busy(): boolean {
    return this.#sending || !this.#jobEnded;
  }

  // -- what is open ----------------------------------------------------------------------

  /** With the page filtered, nothing open is the filtered view's address. */
  #rest(address: Address): Address {
    return address.piece === "page" && this.#filter !== null ? { piece: "project", key: this.#filter } : address;
  }

  /** Opens `address`, closing what was open, and writes it as the browser's address. */
  #open(address: Address): void {
    address = this.#rest(address);
    if (sameAddress(address, this.#at)) return;
    this.#goneSaid = null;
    goneHeld = null;
    this.#fromRoute = false;
    this.#setAt(address);
    this.requestUpdate();
  }

  #setAt(address: Address): void {
    address = this.#rest(address);
    if (!sameAddress(address, this.#at)) {
      this.#confirmOff = false;
      this.#diffs = new Set();
      this.#wholeDiff = false;
      this.#preview?.feed.close();
      this.#preview = null;
      this.#addTo = "";
      this.#addName = "";
      this.#addKind = ADD_KINDS[0];
      if (address.piece !== "replace") this.#replaceItem = null;
      if (address.piece === "add") this.#readProjects();
    }
    this.#at = address;
    const path = pathToWrite(address, location.pathname);
    if (path !== null) history.replaceState(history.state, "", path);
  }

  /** Opens Replace's confirm for `id` in `section`, from the opened item `item` or (null)
   * from Placed. */
  #openReplace(section: string, id: string, item: Address | null): void {
    this.#open({ piece: "replace", section, id });
    this.#replaceItem = item;
    this.requestUpdate();
  }

  /** The preview the confirm at `address` reads, read once as it opens. */
  #previewOf(section: string, id: string): Feed<ReplacePreview> {
    const route = `${section}\n${id}`;
    if (this.#preview === null || this.#preview.route !== route) {
      this.#preview?.feed.close();
      const path = `api/replace-preview?id=${encodeURIComponent(id)}&section=${encodeURIComponent(section)}`;
      const feed = new Feed<ReplacePreview>(() => api("loadout", path), parseReplacePreview, () => this.requestUpdate());
      this.#preview = { route, feed };
      void feed.refresh();
    }
    return this.#preview.feed;
  }

  #readProjects(): void {
    if (this.#projects === null) {
      this.#projects = new Feed<Projects>(() => api("loadout", "api/projects"), parseProjects, () => this.requestUpdate());
    }
    void this.#projects.refresh();
  }

  // -- acts ------------------------------------------------------------------------------

  async #act(act: PageAct, place: string | null, after: Address | null = null): Promise<void> {
    if (this.#busy) return;
    this.#sending = true;
    const command = commandOf(act.argv);
    this.requestUpdate();
    try {
      const answer = await api("loadout", act.path, act.body);
      const started = startedOf(answer.status, answer.data);
      if (started.kind === "job") {
        this.#running = { command, place, job: null, stopped: null, said: null, from: this.#at, after };
        keep({ path: started.path, label: command, anchor: place });
        this.#follow(started.path);
      } else {
        this.#last = { command, place, job: null, stopped: null, said: started.text, from: this.#at, after: null };
        this.#showing = false;
      }
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  /** Follows the job at status path `path` until it ends. */
  #follow(path: string): void {
    this.#job?.close();
    clearTimeout(this.#jobTimer);
    this.#jobEnded = false;
    this.#jobFails = 0;
    const feed: Feed<Job> = new Feed<Job>(() => api("loadout", path), parseJob, () => this.#jobRead(feed));
    this.#job = feed;
    void feed.refresh();
  }

  #jobRead(feed: Feed<Job>): void {
    if (feed !== this.#job || this.#jobEnded) return;
    this.#jobFails = feed.failure !== null ? this.#jobFails + 1 : 0;
    const running = this.#running;
    if (running !== null && feed.value !== null) running.job = feed.value;
    const state = feed.value?.state ?? "running";
    // A refused read (no such job) ends it; one that gave no answer may not have.
    const refusal = jobRefusal(feed.failure);
    const lost = feed.gone || this.#jobFails >= JOB_TRIES;
    if (state === "running" && !lost && refusal === null) {
      this.#jobTimer = setTimeout(() => { void feed.refresh(); }, JOB_POLL_MS);
    } else {
      this.#jobEnded = true;
      this.#goneSaid = null;
      goneHeld = null;
      // A job that may still run stays kept, so a reload tries it again.
      if (state !== "running" || refusal !== null) keep(null);
      if (running !== null) {
        if (state === "running") {
          running.stopped = refusal !== null ? { refused: refusal }
            : { lost: feed.failure?.why ?? (feed.gone ? "loadout went away" : "no answer") };
        }
        this.#last = running;
        this.#running = null;
        this.#showing = false;
        if (running.after !== null && sameAddress(this.#at, running.from)) this.#open(running.after);
      }
      this.#readAll();
      document.dispatchEvent(new CustomEvent(CHANGED));
    }
    this.requestUpdate();
  }

  /** Puts `text` on the clipboard; the act named `what` then reads `Copied` for a while. */
  async #copy(what: string, text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    this.#copied = what;
    clearTimeout(this.#copyTimer);
    this.#copyTimer = setTimeout(() => {
      this.#copied = null;
      this.requestUpdate();
    }, COPIED_MS);
    this.requestUpdate();
  }

  #copyButton(what: string, text: string) {
    return html`<button type="button" class="act" data-action="copy" data-copy=${what}
      @click=${() => void this.#copy(what, text)}>${this.#copied === what ? COPIED : COPY}</button>`;
  }

  /** An act as he would type it, with `Copy`, while the note is set. */
  #typed(argv: readonly string[], key: string) {
    const command = commandOf(argv);
    return html`<div class="cmd"><code data-line="command">${command}</code>${this.#copyButton(key, command)}</div>`;
  }

  // -- drawing -----------------------------------------------------------------------------

  render() {
    const feed = this.#view;
    if (feed === null) return nothing;
    let state = pageState(feed, Date.now() - this.#openedAt);
    // A filtered view waits for projects to say which there are, as the page waits for
    // its view.
    const projects = this.#projects;
    if ((state === "up" || state === "empty" || state === "problem") && this.#filter !== null && projects !== null
        && projects.value === null && projects.failure === null) {
      state = Date.now() - this.#openedAt >= READING_AFTER_MS ? "reading" : "waiting";
    }
    if (state === "gone") return html`<p class="waiting" data-line="gone" role="status">${GONE}</p>`;
    if (state === "waiting") return nothing;
    if (state === "reading") return html`<p class="waiting" data-line="reading" role="status">${READING}</p>`;
    if (state === "failed") {
      return html`<p class="foot" data-line="failed" role="status">${failedLine(feed.failure?.why ?? "")}</p>`;
    }
    const view = feed.value!;
    const dir = this.#dir?.value ?? null;
    const typed = view.note !== null;
    const note = noteGroup(view);
    const notes = footLines(view, dir);
    if (feed.failure !== null) notes.push(failedLine(feed.failure.why));
    if (this.#goneSaid !== null) notes.push(this.#goneSaid);
    const unfiltered = this.#unfilteredWhy();
    if (unfiltered !== null) notes.push(unfiltered);
    const listed = this.#listedProjects();
    const filtered = this.#filter !== null && listed !== null ? filteredView(this.#filter, listed, view) : null;
    const placed = state !== "up" ? null : filtered !== null ? filtered.placed : placedView(view);
    const problems = placed?.problems ?? [];
    const confirm = this.#at.piece === "replace" ? this.#confirmAt(view, problems, this.#at.section, this.#at.id) : null;
    const openAt = this.#at.piece === "item" ? this.#at : confirm?.item ?? null;
    const opened = openAt !== null && openAt.piece === "item"
      ? openedItem(view, openAt.section, openAt.kind, openAt.name) : null;
    const drawn = new Set<string>();
    for (const problem of placed?.problems ?? []) drawn.add(problemPlace(problem.key));
    if (placed !== null && placed.problems.length > 0) drawn.add(INSTALL_ALL_PLACE);
    if (opened !== null) drawn.add(itemPlace(JSON.stringify([opened.item.scope, opened.item.kind, opened.item.name])));
    if (state === "up" && this.#at.piece === "global") drawn.add(GLOBAL_PLACE);
    if (this.#at.piece === "add" && filtered === null) drawn.add(ADD_PLACE);
    // The last act's line stays until the next act ends; the running act's line takes its
    // place where both go to the same place.
    const places = new Map<string | null, Sent>();
    for (const sent of [this.#last, this.#running]) {
      if (sent !== null) places.set(jobPlace(sent.place, drawn, { placed: placed !== null }), sent);
    }
    const ctx: Ctx = { view, typed, opened, openAt, confirm, places };
    // The filtered view hides Folder and Not listed.
    const whole = filtered === null;
    const groups = html`
      ${note !== null ? html`<section class="grp" data-group="note"><div class="gk">${note.label}</div><div class="gl">
        <div class="line"><span class="h" data-line="note-heading">${note.heading}</span><span class="r d sm">${note.aside}</span></div>
        <p class="d" data-line="note">${note.note}</p>
        <p class="d" data-line="note-last">${note.last}</p></div></section>` : nothing}
      ${state === "problem" ? html`<section class="grp" data-group="placed"><div class="gk">Placed</div>
        <div class="gl"><p data-line="problem">${view.problem}</p></div></section>` : nothing}
      ${placed !== null ? this.#placed(placed, ctx) : nothing}
      ${whole ? this.#folder(view, dir, state === "empty") : nothing}
      ${state === "up" ? (filtered !== null ? this.#filteredItems(filtered, ctx) : this.#items(ctx)) : nothing}
      ${state === "empty" ? this.#emptyGlobal(view) : nothing}
      ${whole && (state === "up" || state === "empty") ? this.#notListed(ctx, dir) : nothing}`;
    const empty = state === "empty" ? emptyLines(view) : null;
    return html`
      ${filtered !== null ? this.#filterLine(filtered, listed ?? [], view) : nothing}
      ${empty !== null ? html`<div class="quietline" data-line="empty">${empty.line}</div>
        <div class="first"><p data-line="empty-how">${empty.parts.map((part) => (part.code
          ? html`<code>${part.text}</code>` : part.text))}</p></div>` : nothing}
      <div class="grps" data-part="groups">${groups}</div>
      ${this.#jobsAt(null, ctx)}
      ${notes.map((line) => html`<p class="foot" data-line="foot" role="status">${line}</p>`)}`;
  }

  // -- the filtered view -----------------------------------------------------------------

  /** `Project <key> ▾ · what sessions in <root> load`, the key a menu of every project, and
   * `All of loadout` at the right. */
  #filterLine(filtered: FilteredView, projects: Projects["projects"], view: View) {
    const choices = projectChoices(projects, filtered.project, view);
    return html`<div class="filt" data-part="filter" @keydown=${(event: KeyboardEvent) => {
      if (event.key !== "Escape" || !this.#menu) return;
      this.#menu = false;
      this.requestUpdate();
      void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>(".filt button.set")?.focus());
    }}>
      <span class="fl"><span class="k">${PROJECT_LABEL}</span><span class="anchor"><button type="button" class="set"
        data-action="project" aria-haspopup="menu" aria-expanded=${this.#menu ? "true" : "false"}
        @click=${() => { this.#menu = !this.#menu; this.requestUpdate(); }}>${filtered.key}<span class="v"
        aria-hidden="true">▾</span></button>${this.#menu ? html`<div class="menu" role="menu" aria-label=${PROJECT_LABEL}>
          ${choices.map((choice) => html`<button type="button" class="it" role="menuitemradio"
            aria-checked=${choice.picked ? "true" : "false"} data-project=${choice.key} @click=${() => {
              this.#menu = false;
              this.#filter = choice.key;
              this.#open({ piece: "project", key: choice.key });
              this.requestUpdate();
            }}><span aria-hidden="true">${choice.picked ? "✓" : nothing}</span><span>${choice.key}</span><span
            class="t">${choice.root}</span></button>`)}</div>` : nothing}</span><span class="sep">·</span><span
        data-line="filter-where">${filtered.where}</span></span>
      <a href="/m/loadout/" data-action="all" @click=${(event: MouseEvent) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        this.#menu = false;
        this.#filter = null;
        this.#open(NOTHING_OPEN);
      }}>${ALL_OF_LOADOUT}</a>
    </div>`;
  }

  /** The project's own section, `Yours too`, then the skills modules took over. */
  #filteredItems(filtered: FilteredView, ctx: Ctx) {
    const harnesses = ctx.view.harnesses;
    const own = filtered.own;
    const key = filtered.key;
    const link = html`<a class="pl" href=${`/m/projects/open/${encodeURIComponent(key)}`} data-action="project-page"
      @click=${(event: MouseEvent) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigate("projects", `open/${encodeURIComponent(key)}`);
      }}>${PROJECT_LINK}</a>`;
    return html`
      ${own !== null ? this.#group(own.group, harnesses, ctx, { head: true, label: own.label, count: own.count, link }) : nothing}
      ${this.#group(filtered.yours.group, harnesses, ctx,
                    { head: own === null, label: filtered.yours.label, count: filtered.yours.count })}
      ${filtered.modules.map((group) => this.#group(group, harnesses, ctx, { head: false }))}`;
  }

  // -- the line under an act -------------------------------------------------------------

  /** The line under the act the page put at `place`, if any. */
  #jobsAt(place: string | null, ctx: Ctx) {
    const sent = ctx.places.get(place);
    return sent === undefined ? nothing : this.#jobBlock(sent, ctx.view);
  }

  #jobBlock(sent: Sent, view: View) {
    const ended = sent !== this.#running;
    if (sent.said !== null) {
      return html`<div class="job" data-part="job" data-place=${sent.place ?? ""}><p data-line="job">${
        `${sent.command}: ${sent.said}`}</p></div>`;
    }
    const line = followLine(sent.job, sent.command, view, sent.stopped);
    const job = sent.job;
    const kept = ended && job !== null ? keptLines(job.outcome?.kept ?? [], view.home) : [];
    const show = ended && this.#showing && job !== null ? showView(job, view) : null;
    return html`<div class="job" data-part="job" data-place=${sent.place ?? ""}>
      <div class="line"><span data-line="job">${line.line}</span>${ended && line.hasMore
        ? html`<button type="button" class="act d" data-action="show" aria-expanded=${this.#showing ? "true" : "false"}
            @click=${() => { this.#showing = !this.#showing; this.requestUpdate(); }}>${this.#showing ? "Hide" : "Show"}</button>`
        : nothing}</div>
      ${show !== null ? html`<div class="more" data-part="show">
        ${show.problems.map((p) => html`<div class="p"><div class="line"><span>${p.sentence}</span><span class="w">${p.word}</span></div>
          <p>${p.where}</p><p>${p.fix}</p></div>`)}
        ${show.lines.map((text) => html`<p data-line="show-line">${text}</p>`)}</div>` : nothing}
      ${kept.map((k, at) => html`<div class="kept" data-part="kept">
        <div class="line"><span class="t" data-line="kept">${k.text}</span>${this.#copyButton(`kept:${at}`, k.copy)}</div>
        <div class="line back"><span data-line="back">${k.before !== null ? `${k.before} ` : ""}<code>${k.back}</code> ${k.after}</span>${
          this.#copyButton(`back:${at}`, k.back)}</div></div>`)}
    </div>`;
  }

  // -- Placed --------------------------------------------------------------------------------

  /** A problem's act: its button, or with the note set nothing here (the command shows
   * under the fix). Replace opens its confirm under the problem. */
  #actButton(act: ProblemAct | null, typed: boolean, place: string, { quiet = false } = {}) {
    if (act === null || typed) return nothing;
    if (act.verb === "replace") {
      const open = this.#at.piece === "replace" && this.#at.id === act.id && this.#at.section === act.section
        && this.#replaceItem === null;
      return html`<button type="button" class="act" data-action="replace" aria-expanded=${open ? "true" : "false"}
        @click=${() => (open ? this.#open(NOTHING_OPEN) : this.#openReplace(act.section ?? "user", act.id ?? "", null))}>Replace</button>`;
    }
    return html`<button type="button" class="act ${quiet ? "d" : ""}" data-action="install" ?disabled=${this.#busy}
      @click=${() => void this.#act(installAct(act.scope), place)}>${act === INSTALL_ALL ? "Install all" : "Install"}</button>`;
  }

  #command(act: ProblemAct | null, key: string) {
    if (act === null) return nothing;
    const command = actCommand(act);
    return html`<div class="cmd"><code data-line="command">${command}</code>${this.#copyButton(key, command)}</div>`;
  }

  #placed(placed: PlacedView, ctx: Ctx) {
    const typed = ctx.typed;
    const fine = placed.problems.length === 0;
    const allAtVerdict = !fine && placed.installAll === "verdict";
    return html`<section class="grp" data-group="placed"><div class="gk">Placed</div><div class="gl">
      <div class="line"><span class=${placed.heavy ? "h" : ""} data-line="verdict">${placed.verdict}</span>${fine
        ? html`<span class="r d" data-line="counts">${placed.counts}</span>`
        : allAtVerdict ? this.#actButton(INSTALL_ALL, typed, INSTALL_ALL_PLACE) : nothing}</div>
      ${allAtVerdict && typed ? this.#command(INSTALL_ALL, "install-all") : nothing}
      ${allAtVerdict ? this.#jobsAt(INSTALL_ALL_PLACE, ctx) : nothing}
      ${repeat(placed.problems, (problem) => problem.key, (problem) => this.#problem(problem, ctx))}
      ${fine ? nothing : html`<div class="line counts sm"><span class="d" data-line="counts">${placed.counts}</span>${
        allAtVerdict ? nothing : this.#actButton(INSTALL_ALL, typed, INSTALL_ALL_PLACE, { quiet: true })}</div>
        ${!allAtVerdict && typed ? this.#command(INSTALL_ALL, "install-all") : nothing}
        ${allAtVerdict ? nothing : this.#jobsAt(INSTALL_ALL_PLACE, ctx)}`}
      ${this.#jobsAt(PLACED_PLACE, ctx)}
    </div></section>`;
  }

  #problem(problem: Problem, ctx: Ctx) {
    const typed = ctx.typed;
    const place = problemPlace(problem.key);
    return html`<div class="prob" data-problem=${problem.key} data-word=${problem.word}>
      <div class="line l1"><span data-line="sentence">${problem.sentence}</span><b class="r" data-line="word">${problem.word}</b></div>
      <p class="d sm" data-line="where">${problem.where}</p>
      <div class="line sm"><span class="d" data-line="fix">${fixLine(problem, { typed })}${problem.fixNote !== null
        ? html` <span class="d" data-line="fix-note">${problem.fixNote}</span>` : nothing}</span>${
        problem.copy !== null ? this.#copyButton(`copy:${problem.key}`, problem.copy) : this.#actButton(problem.act, typed, place)}</div>
      ${typed ? this.#command(problem.act, problem.key) : nothing}
      ${ctx.confirm !== null && ctx.confirm.problem === problem.key ? this.#confirm(ctx.confirm, ctx, place, NOTHING_OPEN) : nothing}
      ${this.#jobsAt(place, ctx)}
    </div>`;
  }

  // -- Folder ----------------------------------------------------------------------------------

  #folder(view: View, dir: SourceDir | null, empty: boolean) {
    const folder = folderView(view, dir, { empty });
    if (folder === null) return nothing;
    return html`<section class="grp" data-group="folder"><div class="gk">Folder</div><div class="gl">
      <div class="line"><span><span data-line="folder">${folder.path}</span>${folder.line !== null
        ? html`<span class="d" data-line="uncommitted">  ${folder.line}</span>` : nothing}</span>
        <a class="r d sm" href=${`/m/ui/${SETTINGS_ROUTE}`} data-action="settings"
           @click=${(event: MouseEvent) => {
             if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
             event.preventDefault();
             navigate("ui", SETTINGS_ROUTE);
           }}>${SETTINGS_LINK}</a></div>
      ${folder.names !== null ? html`<p class="d sm" data-line="names">${folder.names}</p>` : nothing}
    </div></section>`;
  }

  // -- the items -------------------------------------------------------------------------------

  #items(ctx: Ctx) {
    const items = itemsView(ctx.view);
    return repeat(items.groups, (group) => group.key, (group, at) => this.#group(group, items.harnesses, ctx,
                                                                             { head: at === 0 }));
  }

  #group(group: GroupView, harnesses: readonly string[], ctx: Ctx,
         { head, label = group.label, count = group.count, link = nothing }: GroupLabel) {
    const scope = scopeOf(group.section);
    const openKindHere = group.kinds === null ? null : openKind(ctx.openAt ?? this.#at, scope);
    return html`<section class="grp" data-group=${group.key}><div class="gk"><span data-line="label">${label}</span>
      <span class="n" data-line="count">${count}</span>${link}</div><div class="gl">
      ${group.empty !== null ? html`<p class="d" data-line="empty">${group.empty}</p>` : html`<div class="grid"
        style=${`--h: ${harnesses.length}`}>
        ${head ? html`<div class="ir hd"><span class="nm"></span>${harnesses.map((harness) => html`<span class="c"
          data-harness=${harness}>${harnessWord(harness)}</span>`)}<span class="o"></span></div>` : nothing}
        ${group.kinds !== null
          ? group.kinds.map((kind) => this.#kind(kind, scope, kind.kind === openKindHere, ctx))
          : group.rows.map((row) => this.#item(row, ctx, { folded: false }))}
        ${group.global !== null ? this.#global(group.global, ctx) : nothing}
      </div>`}</div></section>`;
  }

  #cell(cell: CellView) {
    return html`<span class="c ${TONES[cell.tone]}" data-harness=${cell.harness} data-word=${cell.word ?? ""}>${cell.word ?? ""}</span>`;
  }

  /** `Open`, or `Close` on what is open. */
  #openButton(open: boolean, address: Address, back: Address, what: string) {
    return html`<button type="button" class="o" data-action=${open ? "close" : "open"} aria-expanded=${open ? "true" : "false"}
      aria-label=${`${open ? "Close" : "Open"} ${what}`}
      @click=${() => this.#open(open ? back : address)}>${open ? "Close" : "Open"}</button>`;
  }

  #item(row: ItemRow, ctx: Ctx, { folded }: { folded: boolean }) {
    const scope = row.item.scope;
    const open = ctx.openAt !== null && opensItem(ctx.openAt, scope, row.kind, row.name) && ctx.opened !== null;
    return html`<div class="ir" data-item=${row.key}><span class="nm">${row.name}<span class="k d">${
      folded ? "" : row.kindWord}</span></span>
      ${row.cells.map((cell) => this.#cell(cell))}${this.#openButton(open, itemAddress(scope, row.kind, row.name),
                                                                     closed(this.#at, folded), row.name)}</div>
      ${row.second !== null ? html`<p class="two d" data-line="second">${row.second}</p>` : nothing}
      ${open ? this.#opened(ctx.opened!, row.key, ctx) : nothing}`;
  }

  #kind(kind: KindRow, scope: string, open: boolean, ctx: Ctx) {
    return html`<div class="ir" data-kind=${kind.kind}><span class="nm">${kindFolded(kind.kind)}<span class="k d">${
      kind.rows.length}</span></span>
      ${kind.cells.map((cell) => html`<span class="c k ${TONES[cell.tone]}" data-harness=${cell.harness}>${cell.text ?? ""}</span>`)}${
        this.#openButton(open, { piece: "kind", section: scope, kind: kind.kind }, NOTHING_OPEN, kind.label)}</div>
      ${open ? html`<div class="kopen" data-part="kind">${kind.rows.map((row) => this.#item(row, ctx, { folded: true }))}</div>`
        : nothing}`;
  }

  #global(row: GlobalRow, ctx: Ctx) {
    const open = this.#at.piece === "global";
    return html`<div class="ir" data-item="global"><span class="nm">${row.label}<span class="k d" data-line="mode">${
      row.mode}</span></span>
      ${row.cells.map((cell) => this.#cell(cell))}${this.#openButton(open, { piece: "global" }, NOTHING_OPEN, row.label)}</div>
      ${open ? this.#openedGlobal(openedGlobal(ctx.view), ctx) : nothing}`;
  }

  // -- the opened item -------------------------------------------------------------------------

  #opened(opened: OpenedItem, key: string, ctx: Ctx) {
    const from = fromParts(opened.from);
    const place = itemPlace(key);
    const typed = ctx.typed;
    return html`<div class="det" data-part="opened" data-item=${key}>
      <div class="r"><span class="k">From</span><span class="f" data-line="from">${from.lead}${from.rest !== null
        ? html` <span class="d">· ${from.rest}</span>` : nothing}</span><span></span></div>
      ${opened.lines.map((line) => html`${this.#harnessLine(line, typed, opened)}${ctx.confirm !== null
        && ctx.confirm.item !== null && line.replace?.id === ctx.confirm.id ? this.#confirm(ctx.confirm, ctx, place,
                                                                                          ctx.confirm.item, { inItem: true })
        : nothing}`)}
      ${ctx.confirm !== null && ctx.confirm.item !== null && !opened.lines.some((line) => line.replace?.id === ctx.confirm!.id)
        ? this.#confirm(ctx.confirm, ctx, place, ctx.confirm.item, { inItem: true }) : nothing}
      ${opened.enableAll !== null ? html`<div class="foot">${typed ? nothing
        : html`<button type="button" class="act" data-action="enable-all" ?disabled=${this.#busy}
            @click=${() => void this.#act(opened.enableAll!.act, place)}>${opened.enableAll.label}</button>`}
        ${opened.enableAll.note !== "" ? html`<span class="d" data-line="enable-all">${opened.enableAll.note}</span>` : nothing}
        ${typed ? this.#typed(opened.enableAll.act.argv, `${key}:enable-all`) : nothing}</div>` : nothing}
      <div class="foot">${typed ? html`<span class="d">${opened.remove.label}</span>`
        : html`<button type="button" class="act d" data-action="remove" ?disabled=${this.#busy}
            @click=${() => void this.#act(opened.remove.act, place)}>${opened.remove.label}</button>`}
        <span class="d" data-line="remove">${opened.remove.note}</span>
        ${typed ? this.#typed(opened.remove.act.argv, `${key}:remove`) : nothing}</div>
      ${this.#jobsAt(place, ctx)}
    </div>`;
  }

  #harnessLine(line: HarnessLine, typed: boolean, opened: OpenedItem) {
    const heavy = line.word !== null && line.word !== "placed" && line.word !== "off";
    const sub = [line.why, ...line.notes].filter((text): text is string => text !== null && text !== "");
    // A line that can be replaced offers Replace, which opens its confirm under the line;
    // else its toggle.
    const replace = line.replace;
    const toggle = replace === null ? line.toggle : null;
    const argv = line.replace !== null ? replaceArgv(line.replace) : toggle?.act.argv ?? null;
    return html`<div class="r" data-harness=${line.harness}><span class="k">${line.label}</span><span class="f">${
      line.word !== null ? html`<span class=${heavy ? "h" : ""} data-line="word">${line.word}</span>` : nothing}${
      line.where !== null ? html` <span class="d" data-line="where">· ${line.where}</span>` : nothing}${
      sub.length > 0 ? html`<span class="sub" data-line="why">${sub.join(" ")}</span>` : nothing}${
      typed && argv !== null ? this.#typed(argv, `${line.harness}:act`) : nothing}</span>${
      replace !== null && !typed ? html`<button type="button" class="x act" data-action="replace" @click=${() => {
        const item = itemAddress(opened.item.scope, opened.item.kind, opened.item.name);
        const open = this.#at.piece === "replace" && this.#at.id === replace.id;
        if (open) this.#open(item);
        else this.#openReplace(replace.section, replace.id, item);
      }}>Replace</button>`
      : toggle !== null && !typed ? html`<button type="button" class="x" data-action=${toggle.label === "Turn on" ? "turn-on" : "turn-off"}
        ?disabled=${this.#busy} @click=${() => void this.#act(toggle.act, this.#openedPlace())}>${toggle.label}</button>`
        : html`<span></span>`}</div>`;
  }

  /** The place of the item open now. */
  #openedPlace(): string | null {
    const at = this.#at;
    return at.piece === "item" ? itemPlace(JSON.stringify([at.section, at.kind, at.name])) : null;
  }

  // -- Replace's confirm ------------------------------------------------------------------------

  /** Where the confirm for `id` in `section` goes, or null when nothing there can be
   * replaced now. */
  #confirmAt(view: View, problems: readonly Problem[], section: string, id: string): ConfirmAt | null {
    const rows = replaceRows(view, problems, section, id);
    if (rows.length === 0) return null;
    if (this.#replaceItem === null) {
      const problem = problems.find((p) => p.act?.verb === "replace" && p.act.id === id && p.act.section === section);
      if (problem !== undefined) return { section, id, rows, problem: problem.key, item: null };
    }
    const named = /^([a-z]+):([^@]+)@/.exec(id);
    const item = this.#replaceItem ?? (named === null ? null : itemAddress(section, named[1], named[2]));
    return { section, id, rows, problem: null, item };
  }

  /** The confirm in place: the question, what moves or is kept and where, the lines whose
   * fact is set, the target with the diff's legend, the diff, what stays, and `Replace`
   * and `Cancel`. `back` is what Cancel and the job's end open. */
  #confirm(at: ConfirmAt, ctx: Ctx, place: string, back: Address, { inItem = false } = {}) {
    const feed = this.#previewOf(at.section, at.id);
    const conf = confirmView(at.rows, feed.value, ctx.view);
    const said = feed.value?.problem ?? (feed.value === null ? feed.failure?.why ?? null : null);
    const head = (text: string): [string, string] => {
      const cut = text.indexOf(" ");
      return cut < 0 ? [text, ""] : [text.slice(0, cut), text.slice(cut + 1)];
    };
    const scope = conf.scope;
    const diff = [...conf.diff, ...(this.#wholeDiff ? conf.more : [])];
    const legend = [conf.target, conf.legend].filter((part) => part !== null && part !== "").join(" · ");
    return html`<div class="cf ${inItem ? "in" : ""}" data-part="confirm" role="group" aria-label="Replace">
      <p class="q" data-line="question">${conf.question}</p>
      ${feed.value === null && feed.failure === null ? html`<p class="d sm" data-line="reading">${READING}</p>` : nothing}
      ${said !== null ? html`<p class="d sm" data-line="preview-problem">${said}</p>` : nothing}
      ${conf.what !== null ? html`<div class="cr"><span class="k">${head(conf.what)[0]}</span><span data-line="what">${
        head(conf.what)[1]}</span></div>` : nothing}
      ${conf.notes.map((note) => html`<div class="cr"><span class="k"></span><span class="d" data-line="note">${note}</span></div>`)}
      ${conf.what !== null ? html`<div class="cr"><span class="k"></span><span><span class="cap d" data-line="legend">${legend}</span>${
        diff.length > 0 ? html`<pre data-line="diff">${diff.join("\n")}</pre>` : nothing}${
        conf.showAll !== null && !this.#wholeDiff ? html`<button type="button" class="act d" data-action="show-all"
          @click=${() => { this.#wholeDiff = true; this.requestUpdate(); }}>${conf.showAll}</button>` : nothing}</span></div>` : nothing}
      ${conf.stays !== null ? html`<div class="cr"><span class="k">${head(conf.stays)[0]}</span><span data-line="stays">${
        head(conf.stays)[1]}</span></div>` : nothing}
      ${ctx.typed && scope !== null && conf.canReplace ? this.#typed(replaceAct(at.id, scope, "").argv, "confirm-replace") : nothing}
      <div class="btns">${ctx.typed ? nothing : html`<button type="button" class="btn" data-action="confirm-replace"
          ?disabled=${!conf.canReplace || scope === null || this.#busy}
          @click=${() => { if (scope !== null) void this.#act(replaceAct(at.id, scope, freshJob()), place, back); }}>Replace</button>`}
        <button type="button" class="lnk" data-action="cancel" @click=${() => this.#open(back)}>Cancel</button></div>
    </div>`;
  }

  // -- Not listed and Add ------------------------------------------------------------------------

  /** Not listed: each file no section lists, with Add, and Add by name, which is always
   * there: it lists what no file in the folder shows (a hook, a permission rule, an MCP
   * server, or an item another section lists). Its line sits under an opened Add, else
   * at the group's foot. */
  #notListed(ctx: Ctx, dir: SourceDir | null) {
    const rows = notListed(dir, ctx.view);
    const at = this.#at;
    const byName = at.piece === "add" && at.kind === null;
    const addOpen = at.piece === "add" && rows.some((row) => at.kind === row.kind && at.name === row.name);
    const picker = pickerOf(ctx.view, this.#projects?.value ?? null);
    return html`<section class="grp" data-group="unlisted"><div class="gk">Not listed</div><div class="gl">
      ${rows.map((row) => {
        const open = at.piece === "add" && at.kind === row.kind && at.name === row.name;
        return html`<div class="ir" data-unlisted=${row.key}><span class="nm">${row.name}<span class="k d">${row.line}</span></span>
          <button type="button" class="o add" data-action=${open ? "close" : "add"} aria-expanded=${open ? "true" : "false"}
            @click=${() => this.#open(open ? NOTHING_OPEN : { piece: "add", kind: row.kind, name: row.name })}>${
            open ? "Close" : "Add"}</button></div>
          ${open ? this.#addOpen(row, picker, ctx) : nothing}`;
      })}
      ${byName ? this.#addByName(picker, ctx) : addOpen ? nothing : html`<div class="line byname">${this.#byNameButton()}</div>`}
    </div></section>`;
  }

  #picker(picker: Picker) {
    return html`<select class="pick" data-line="add-to" aria-label="Add to" @change=${(event: Event) => {
      this.#addTo = (event.target as HTMLSelectElement).value;
      this.requestUpdate();
    }}>${picker.options.map((option) => html`<option value=${option.value} ?selected=${option.value === this.#addTo}>${
      option.label}</option>`)}</select>${picker.line !== null
      ? html`<span class="sub" data-line="picker">${picker.line}</span>` : nothing}`;
  }

  /** The section Add names: the picker's choice, Your items when it is gone. */
  #addSection(picker: Picker): string | null {
    return picker.options.find((option) => option.value === this.#addTo)?.section ?? null;
  }

  #addButtons(act: PageAct | null, ctx: Ctx) {
    return html`<div class="cf in plain">
      ${ctx.typed && act !== null ? this.#typed(act.argv, "add") : nothing}
      <div class="btns">${ctx.typed ? nothing : html`<button type="button" class="btn" data-action="confirm-add"
          ?disabled=${act === null || this.#busy}
          @click=${() => { if (act !== null) void this.#act(act, ADD_PLACE, NOTHING_OPEN); }}>Add and place</button>`}
        <button type="button" class="lnk" data-action="cancel" @click=${() => this.#open(NOTHING_OPEN)}>Cancel</button></div>
      <p class="d sm" data-line="add-note">${ADD_NOTE}</p>
      ${this.#jobsAt(ADD_PLACE, ctx)}</div>`;
  }

  #addOpen(row: NotListedRow, picker: Picker, ctx: Ctx) {
    const act = addAct(row.kind, row.name, this.#addSection(picker));
    return html`<div class="det" data-part="add">
      <div class="r"><span class="k">Add to</span><span class="f">${this.#picker(picker)}</span><span></span></div>
      <div class="r"><span class="k">From</span><span class="f" data-line="from">${row.from.replace(/^From /, "")}</span><span></span></div>
      ${this.#addButtons(act, ctx)}
      <div class="foot">${this.#byNameButton()}</div>
    </div>`;
  }

  #byNameButton() {
    return html`<button type="button" class="act d" data-action="by-name"
      @click=${() => this.#open({ piece: "add", kind: null, name: null })}>${BY_NAME}</button>`;
  }

  #addByName(picker: Picker, ctx: Ctx) {
    const name = this.#addName.trim();
    const act = name === "" ? null : addAct(this.#addKind, name, this.#addSection(picker));
    return html`<div class="det" data-part="add">
      <div class="r"><span class="k">Kind</span><span class="f"><select class="pick" data-line="kind" aria-label="Kind"
        @change=${(event: Event) => { this.#addKind = (event.target as HTMLSelectElement).value; this.requestUpdate(); }}>${
        ADD_KINDS.map((kind) => html`<option value=${kind} ?selected=${kind === this.#addKind}>${kindWord(kind)}</option>`)}</select></span><span></span></div>
      <div class="r"><span class="k">Name</span><span class="f"><input class="pick" data-line="name" aria-label="Name"
        .value=${this.#addName} @input=${(event: Event) => {
          this.#addName = (event.target as HTMLInputElement).value;
          this.requestUpdate();
        }}></span><span></span></div>
      <div class="r"><span class="k">Add to</span><span class="f">${this.#picker(picker)}</span><span></span></div>
      ${this.#addButtons(act, ctx)}
    </div>`;
  }

  // -- the opened global file ------------------------------------------------------------------

  #openedGlobal(global: OpenedGlobal, ctx: Ctx) {
    const typed = ctx.typed;
    const off = (): void => { this.#confirmOff = true; this.requestUpdate(); };
    return html`<div class="det" data-part="opened" data-item="global">
      ${global.from !== null ? html`<div class="r"><span class="k">From</span><span class="f" data-line="from">${
        global.from.replace(/^From /, "")}</span><span></span></div>` : nothing}
      <div class="r"><span class="k">Mode</span><span class="f"><span class="tri" role="group" aria-label="Mode">${
        global.modes.map((mode) => mode.current
          ? html`<span class="s" data-mode=${mode.mode} aria-current="true">${mode.label}</span>`
          : typed ? html`<span data-mode=${mode.mode}>${mode.label}</span>`
          : html`<button type="button" data-mode=${mode.mode} ?disabled=${this.#busy}
              @click=${() => (mode.act === null ? off() : void this.#act(mode.act, GLOBAL_PLACE))}>${mode.label}</button>`)}
      </span>${typed ? global.modes.filter((mode) => !mode.current).map((mode) => this.#typed(
        mode.act === null ? globalOffAct("").argv : mode.act.argv, `mode:${mode.mode}`)) : nothing}</span><span></span></div>
      <div class="r"><span class="k"></span><span class="f d" data-line="mode-line">${global.line}</span><span></span></div>
      ${global.files.map((file) => {
        const shown = this.#diffs.has(file.harness);
        return html`<div class="r" data-harness=${file.harness}><span class="k">${file.label}</span><span class="f"><span
          data-line="file">${file.file}</span> <span class="d">${[file.adds, file.next].filter((t) => t !== null)
            .map((t) => `· ${t}`).join(" ")}</span>${shown && file.diff !== null
            ? html`<pre data-line="diff">${file.diff}</pre>` : nothing}</span>${file.diff !== null
          ? html`<button type="button" class="x" data-action="diff" aria-expanded=${shown ? "true" : "false"} @click=${() => {
              const next = new Set(this.#diffs);
              if (shown) next.delete(file.harness);
              else next.add(file.harness);
              this.#diffs = next;
              this.requestUpdate();
            }}>${shown ? "Hide" : "Show"}</button>` : html`<span></span>`}</div>`;
      })}
      ${global.turnOff !== null ? html`<div class="foot">${typed ? html`<span class="d">${global.turnOff.label}</span>`
        : html`<button type="button" class="act d" data-action="global-off" ?disabled=${this.#busy || this.#confirmOff}
            @click=${off}>${global.turnOff.label}</button>`}
        <span class="d" data-line="turn-off">${global.turnOff.note}</span>
        ${typed ? this.#typed(globalOffAct("").argv, "global-off") : nothing}</div>` : nothing}
      ${this.#confirmOff && !typed ? html`<div class="cf" data-part="confirm" role="group" aria-label="Turn off">
        <p class="q" data-line="question">${global.turnOff?.confirm ?? ""}</p>
        <div class="btns"><button type="button" class="btn" data-action="confirm-off" ?disabled=${this.#busy} @click=${() => {
          this.#confirmOff = false;
          void this.#act(globalOffAct(jobId(crypto.getRandomValues(new Uint8Array(16)))), GLOBAL_PLACE);
        }}>Turn off</button><button type="button" class="lnk" data-action="cancel" @click=${() => {
          this.#confirmOff = false;
          this.requestUpdate();
        }}>Cancel</button></div></div>` : nothing}
      ${this.#jobsAt(GLOBAL_PLACE, ctx)}
    </div>`;
  }

  #emptyGlobal(view: View) {
    const global = emptyGlobal(view);
    return html`<section class="grp" data-group="global"><div class="gk">Global file</div><div class="gl">
      <p><span data-line="mode">${global.mode}</span>${global.line !== null
        ? html`<span class="d" data-line="mode-line">  ${global.line}</span>` : nothing}</p></div></section>`;
  }
}

/** Where Replace's confirm is drawn: under its problem (`problem` its key), or in the opened
 * item `item` under the harness line whose Replace names `id`. */
interface ConfirmAt {
  section: string;
  id: string;
  rows: ViewRow[];
  problem: string | null;
  item: Address | null;
}

/** How a group is labelled: drawn with the harnesses' head row or not, and in the filtered
 * view under its own label and count, with a link under them. */
interface GroupLabel {
  head: boolean;
  label?: string;
  count?: string;
  link?: unknown;
}

/** What one drawing of the page shares between its parts. */
interface Ctx {
  view: View;
  /** The note is set: every act shows as its command. */
  typed: boolean;
  /** The item open now, or null. */
  opened: OpenedItem | null;
  /** The item's address, open by its own address or around Replace's confirm. */
  openAt: Address | null;
  /** Replace's confirm, when one is open. */
  confirm: ConfirmAt | null;
  /** The line under an act, by the place it goes (null: the foot). */
  places: Map<string | null, Sent>;
}

if (!customElements.get(TAG)) customElements.define(TAG, LoadoutPage);

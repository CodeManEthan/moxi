// <moxi-services-page route="...">: services' panel.
//
// - The address (`address.ts`): the route opens the page (`""`), a row (`row/<id>`), the
//   panel on one of its two ways (`add`, `adopt`), the filter to one project's rows
//   (`project/<key>`, with projects installed), or both (`project/<key>/row/<id>`). One
//   thing is open at a time, and the page rewrites the browser's address with
//   `history.replaceState` whenever what is open changes, so Back leaves the page. A route
//   is opened once the rows are read; one naming a row or project the page doesn't have
//   opens the page with nothing open, and one dim line at the foot says so.
// - The filter line, over the list: `Project <key> ▾` (the menu picks another project),
//   `<n> of <m> services` and `Show all`.
// - The list (`list-model.ts` gives every word), under no heading of its own: the shell's
//   top line names the page. First, `<n> services` with `Add a service` (desk) beside the
//   rows that rise (every fail, then failed last runs, then rows it can't tell), each with
//   its sentence on line two; when none rises, `Nothing is down.`, the dim counts and
//   `read <time>`. Then the groups: one per project key (with projects installed) and `No
//   project`, or `Yours`, then `Moxi`. Each row is a `<moxi-ui-row>`: the name, its dim
//   words and its fact, weighted by the row's cause, and `Open` (`Close` while open).
// - The list's states: nothing for 400 ms, then `Reading…`; while services' page server
//   isn't answering, a first row saying so and when the rows were last read, over those
//   rows, dim and with no acts; a store that can't be read, its sentence (the desk) and
//   nothing else; every row `unknown` for one reason, `Can't read the supervisor` and the
//   reason, the rows dim in their groups; no row of his, `You have added no services yet.`
//   and, under `Yours`, what a service is with `Add a service`.
// - A row opened draws its labelled lines (`opened-model.ts`) from its own read,
//   `/api/row`, and slots' server's `/api/ends`; then its foot's acts and a Moxi row's
//   links.
// - The acts (`acts-model.ts`), at the desk: at most one on the closed row's line, the
//   rest in the opened row's foot, by the row's cause and kind. A press is a plain
//   `/api/act` with a job key minted for it; while its job runs the row's fact says what
//   is being done and every act is off; an act that didn't hold keeps the row open and
//   says so in `State`, with its lines and the act again. The acts that can hurt or spend
//   ask once, in place, under the opened row's foot: Remove of his row (what goes, what
//   stays, and the command that adds a written row again), a run of a row whose runs
//   launch a session, and a Stop or Restart of slots' server while its slots hold
//   something or can't be read, read from `/api/ends` on the press and sent with what it
//   showed; the server's guard refuses one whose count has grown, and the page reads the
//   slots again and asks anew. ui's shell offers no Stop (its opened row gives the
//   command), and after its Restart the page reloads once the shell answers again.
// - The phone, where the shell runs its acts, offers the desk's row acts but Remove, each
//   through the shell's sheet and the act's kept confirmed route: the sheet says the
//   desk's confirm without its redacted words, and slots' server's Stop and Restart give
//   the count they end and send it. Where it runs none, the phone shows no act and no
//   `Open`; the shell says it only reads here.
// - The panel (`panel.ts`), from `Add a service` or the `add` and `adopt` addresses, at the
//   window's right edge: a command to run, or a unit already on this computer to adopt,
//   with the kind asked where the system can't tell it. Its button is one plain
//   `/api/add`, no sheet, followed to its end like an act; the server checks it again. A
//   done add leaves the panel open, its form empty, saying so.
//
// Each act runs as a job on services' page server: the page reads the job's status link
// once a second until it ends, shows its lines under the row, and keeps every act's
// control off while one runs, since the server runs its acts one at a time anyway. When a
// job ends the page reads the rows again. Otherwise it reads `/api/rows` when it opens and
// when its tab is shown again, and follows the topic `rows`.
//
// On the phone the panel, the projects list and Remove are left out: from the phone a
// service is acted on, never added or removed.

import { css, html, LitElement, nothing, unsafeCSS } from "lit";
import { keyed } from "lit/directives/keyed.js";
import { repeat } from "lit/directives/repeat.js";
import { api, components, confirm, gateway, gatewayActs, installed, navigate, subscribe } from "/m/ui/client.js";

import { NO_SUCH_ADDRESS, NOTHING_OPEN, parseRoute, pathToWrite, resolve } from "./address.ts";
import type { Address } from "./address.ts";

import {
  actsOf, jobSaid, phoneActs, phoneRoute, phoneSheet, removeConfirm, runningFact, SHELL, SHELL_RESTART, slotsConfirm,
  STOP_LASTS, transportSaid, usageConfirm, warned,
} from "./acts-model.ts";
import type { Act as RowAct, Confirm, Ends, Sends } from "./acts-model.ts";
import { Feed, parseEnds, parseOne, parseProjects, parseRows, parseStatus, saidOf, statusPathOf } from "./feed.ts";
import type { Projects, Row, Rows, Status } from "./feed.ts";
import {
  ADD_A_SERVICE, filtered, listShape, noServiceBelongs, pageState, problemLines, PROJECT, projectKeys, READING_AFTER_MS, SHOW_ALL,
  WHAT_A_SERVICE_IS, YOURS,
} from "./list-model.ts";
import type { ListShape, PageState, RowView } from "./list-model.ts";
import { footLinks, openedLines } from "./opened-model.ts";
import type { Line, One, Opened } from "./opened-model.ts";
import { outcomeSaid, statusLost, statusSaid } from "./views.ts";
import type { Said } from "./views.ts";
import { PANEL_WIDTH_PX } from "./panel.ts";
import { addRefused } from "./panel-model.ts";
import type { addBody, Way } from "./panel-model.ts";

/** What the panel sends: `/api/add`'s body, or the line saying what it still needs. */
type Built = ReturnType<typeof addBody>;

const TAG = "moxi-services-page";

/** How often a running job is read. */
const JOB_POLL_MS = 1000;
/** After this many failed reads in a row the page stops reading a job. */
const JOB_TRIES = 5;
/** Where the page's line about the add form goes. */
const ADD = "add";

/** The window's width from which the panel moves the column; under it the panel lies over
 * the page. */
const SIDE_FROM_PX = 1100;
/** Half of the column's width. */
const HALF_COLUMN_PX = 340;

const STYLES = css`
  :host { display: block; min-width: 0; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; }
  h2 { margin: 0; font-size: 1.1em; }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .dim { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .ok { color: var(--moxi-ok); }
  .warn { color: var(--moxi-warn); }
  .error { color: var(--moxi-error); }
  .muted { color: var(--moxi-text-dim); }
  .problem { padding: 8px 12px; border-radius: var(--moxi-radius); background: var(--moxi-error-soft); color: var(--moxi-error); }
  .note { padding-left: 8px; border-left: 3px solid var(--moxi-warn); font-size: var(--moxi-font-size-sm); }
  :host { max-width: 680px; margin: 0 auto; padding: 4px 0 16px; font-size: 14px; line-height: 20px; }
  /* Beside the panel, where the window has the room, the column stands at the centre of
     what the panel leaves; under that width the panel lies over the page. */
  @media (min-width: ${unsafeCSS(SIDE_FROM_PX)}px) {
    :host([data-side]) { margin-left: calc(50% - ${unsafeCSS(HALF_COLUMN_PX + PANEL_WIDTH_PX / 2)}px); }
  }
  p.state, p.foot { margin: 16px 0 0; padding: 0 2px; }
  p.foot { color: var(--moxi-text-dim); }
  /* The list is the Projects page's: a dim label at the left of each group, its rows at
     the right, a rule between groups. No accent, and no verdict colour. */
  .grps { margin-top: 16px; border-top: 1px solid var(--moxi-border); }
  .grp { display: grid; grid-template-columns: 96px minmax(0, 1fr); border-bottom: 1px solid var(--moxi-border);
         padding: 5px 2px; }
  .grp .gk { color: var(--moxi-text-dim); padding-top: 7px; overflow-wrap: anywhere; }
  .grp .gk small { display: block; font-size: 13px; line-height: 18px; margin-top: 3px; }
  .grp .gl { grid-column: 2; min-width: 0; }
  .grp .gk button { padding: 0; margin: 0; border: 0; background: none; font: inherit; text-align: left; cursor: pointer; }
  .grp .gk small button { color: var(--moxi-text); }
  .grp .gk button:hover:not(:disabled) { background: none; }
  /* Under 720 px, as on the Projects page, each group's label stands above its rows, so a
     row has the whole width. */
  @media (max-width: 720px) {
    .grp { display: block; padding: 4px 0 6px; }
    /* The count above the rows, with Add a service beside it at the right. */
    .grp .gk { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; padding-top: 9px;
               font-size: 13px; }
    .grp .gk small { margin-top: 0; }
  }
  .item moxi-ui-row > .fact { color: var(--moxi-text); font-variant-numeric: tabular-nums; }
  /* On a phone a fact in the text colour wraps, so it and Open stay inside the row. */
  @media (max-width: 480px) {
    .item moxi-ui-row > .fact { display: inline-block; max-width: 8em; white-space: normal; text-align: right; }
  }
  .one { display: flex; justify-content: space-between; align-items: baseline; gap: 4px 14px; padding: 7px 0;
         flex-wrap: wrap; }
  .one .read { margin-left: auto; color: var(--moxi-text-dim); font-variant-numeric: tabular-nums; }
  .counts { color: var(--moxi-text-dim); padding-bottom: 7px; }
  .item .said { margin: 0 0 7px; }
  .one.state { display: block; }
  .one.state .more { display: block; color: var(--moxi-text-dim); }
  /* The rows last read, or rows Moxi can't judge: every word dim. */
  .grps.dim { --moxi-text: var(--moxi-text-dim); }
  moxi-ui-row > button.do { padding: 0; margin: 0; border: 0; background: none; font: inherit; font-size: 13px;
                            line-height: 18px; color: var(--moxi-text-dim); cursor: pointer; }
  moxi-ui-row > button.do:hover:not(:disabled) { background: none; color: var(--moxi-text); }
  moxi-ui-row > button.do.act { color: var(--moxi-text); }
  .filt { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 14px; padding: 0 2px; }
  .filt .k { margin-right: 8px; color: var(--moxi-text-dim); }
  .filt .sep { margin: 0 12px; color: var(--moxi-text-dim); }
  .filt .n { color: var(--moxi-text-dim); font-variant-numeric: tabular-nums; }
  .filt button { padding: 0; margin: 0; border: 0; background: none; font: inherit; color: var(--moxi-text); cursor: pointer; }
  .filt button:hover:not(:disabled) { background: none; }
  .filt .v { margin-left: 6px; color: var(--moxi-text-dim); }
  .filt .pick { position: relative; }
  .filt .menu { position: absolute; z-index: 1; top: calc(100% + 6px); left: 0; display: grid; min-width: 160px;
                padding: 6px 0; border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius);
                background: var(--moxi-bg-raised); }
  .filt .menu button { padding: 2px 12px; text-align: left; }
  .filt .menu button[aria-current="true"] { font-weight: 600; }
  /* The opened row: a dim label at the left of each line, as the drawing has it. */
  .det { padding: 4px 0 8px; border-top: 1px solid var(--moxi-border); margin-top: 2px; }
  .det .r { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 0 8px; padding: 2px 0; }
  .det .k { color: var(--moxi-text-dim); font-size: 13px; }
  .det .f { min-width: 0; }
  .det .d { color: var(--moxi-text-dim); }
  .det p.d { font-size: 13px; }
  .det code { font-size: 12px; }
  .det .code { display: inline-flex; flex-wrap: wrap; align-items: baseline; gap: 0 8px; min-width: 0; }
  .det details.r { display: block; }
  .det details.r > summary { cursor: pointer; }
  .det details.r > .f { padding-left: 84px; }
  .det button.copy { padding: 0; margin: 0; border: 0; background: none; font: inherit; font-size: 13px; cursor: pointer; }
  .det button.copy:hover:not(:disabled) { background: none; }
  .det a { color: var(--moxi-text); text-decoration: none; }
  .det a:hover { text-decoration: underline; }
  .det .foot { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 18px; padding: 8px 0 0 84px; }
  .det button.act { padding: 0; margin: 0; border: 0; background: none; font: inherit; font-size: 13px;
                    line-height: 18px; color: var(--moxi-text); cursor: pointer; }
  .det button.act.d { color: var(--moxi-text-dim); }
  .det button.act:hover:not(:disabled) { background: none; text-decoration: underline; }
  .det .foot .note { flex: 1 1 14em; text-align: right; font-size: 13px; }
  .det button.act.w, moxi-ui-row > button.do.act.w { color: var(--moxi-danger); }
  /* An act's confirm, in place under the foot. */
  .det .cf { margin: 10px 0 2px 84px; padding-top: 10px; border-top: 1px solid var(--moxi-border); }
  .det .cf .q { margin: 0; padding-bottom: 4px; font-weight: 600; }
  .det .cf.w .q { color: var(--moxi-danger); }
  .det .cf .btns { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 18px; padding: 10px 0 2px; }
  .det .cf button.btn { padding: 5px 13px; background: none; }
  .det .cf button.btn.wn { color: var(--moxi-danger); border-color: var(--moxi-danger); }
  .det .cf button.lnk { padding: 0; border: 0; background: none; color: var(--moxi-text-dim); }
  .det .cf button.btn:hover:not(:disabled), .det .cf button.lnk:hover:not(:disabled) { background: none; }
  .det .links { display: flex; flex-wrap: wrap; gap: 2px 18px; padding: 6px 0 0 84px; font-size: 13px; }
  @media (max-width: 480px) {
    .det .r { grid-template-columns: minmax(0, 1fr); }
    .det details.r > .f, .det .links, .det .foot { padding-left: 0; }
    .det .cf { margin-left: 0; }
    .det .foot .note { text-align: left; }
  }
  .yours { color: var(--moxi-text-dim); padding: 7px 0; }
  .yours button { display: block; padding: 0; margin: 0; border: 0; background: none; font: inherit;
                  color: var(--moxi-text); cursor: pointer; }
  .yours button:hover:not(:disabled) { background: none; }
  .said { display: grid; gap: 2px; padding-left: 8px; border-left: 3px solid var(--moxi-border-strong);
           font-size: 13px; line-height: 18px; }
  .said.ok { border-left-color: var(--moxi-ok); color: var(--moxi-text); }
  .said.error { border-left-color: var(--moxi-error); }
  .said.warn { border-left-color: var(--moxi-warn); color: var(--moxi-text); }
  .said.muted { color: var(--moxi-text-dim); }
  button { font: inherit; color: var(--moxi-text); max-width: 100%; }
  button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg-raised); cursor: pointer; }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button:disabled { cursor: not-allowed; opacity: 0.6; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
`;

/** How long `Copied` stands in for `Copy`, in milliseconds. */
const COPIED_MS = 1500;
/** After a Restart of ui's shell, how often and how long the page reads the rows before it
 * reloads, in milliseconds. */
const RELOAD_POLL_MS = 1000;
const RELOAD_FOR_MS = 30_000;

/** What the page last said, and where: a row's id, `ADD`, or null for the page. */
interface Told {
  where: string | null;
  said: Said;
}

/** The opened row's own reads: `/api/row`, and `/api/ends` for slots' server. `seen` is
 * the row as the list last gave it, so a change to it reads the row again. */
interface OpenReads {
  id: string;
  seen: string;
  one: Feed<One>;
  ends: Feed<Ends> | null;
}

/** The job the page follows; `act` is the row's act it runs, null for an add or an act
 * sent through the shell's sheet. */
interface Job {
  feed: Feed<Status>;
  label: string;
  where: string;
  fails: number;
  ended: boolean;
  act: RowAct | null;
}

/** What a row's act that didn't hold says in its opened `State`: the heavy line, the job's
 * lines and the act offered again. */
interface Ended {
  where: string;
  heavy: string;
  lines: string[];
  again: { label: string; act: RowAct } | null;
}

/** An act's in-place confirm, open under its row's foot. */
interface Asking {
  where: string;
  act: RowAct;
  confirm: Confirm;
}

/** A job's key for `/api/act`: 32 hex characters, new at each press. */
function mintJob(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Acts whose foot button is in the text colour; the rest are dim. */
const PLAIN_ACTS: ReadonlySet<string> = new Set(["start", "stop", "restart", "run"]);

export class ServicesPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";

  #rows: Feed<Rows> | null = null;
  #projects: Feed<Projects> | null = null;
  #stop: (() => void) | null = null;
  #job: Job | null = null;
  #jobTimer: ReturnType<typeof setTimeout> | undefined;
  /** An act is being sent, or its confirm sheet is open. */
  #sending = false;
  #told: Told | null = null;
  #ended: Ended | null = null;
  #asking: Asking | null = null;
  /** slots' server's last `/api/ends` read, by the opened row or a press: its Stop and
   * Restart are in the warning colour while it found something in use or couldn't read. */
  #ends: { id: string; ends: Ends } | null = null;
  #reloadTimer: ReturnType<typeof setTimeout> | undefined;
  /** The panel's key: a new one places a fresh panel, its form empty. */
  #panelKey = 0;
  /** Whether the page came through the gateway, on the phone. */
  #phone = false;

  // What is open: the filter's project key, the opened row's id and the add form's way.
  // The route given is opened once the rows are read; `#unknown` is the foot's line for
  // one that named nothing the page has.
  #filter: string | null = null;
  #open: string | null = null;
  #panel: "add" | "adopt" | null = null;
  #pending: Address | null = null;
  #pendingKnown = true;
  #unknown: string | null = null;
  /** The filter's menu of projects is open. */
  #menu = false;
  /** What was opened by its address takes the view once drawn. */
  #scrollTo: "row" | null = null;
  /** When the page was connected, for the first read's `Reading…`. */
  #since = Date.now();
  #readingTimer: ReturnType<typeof setTimeout> | undefined;
  #reads: OpenReads | null = null;
  /** The text whose `Copy` reads `Copied` for a while. */
  #copied: string | null = null;
  #copyTimer: ReturnType<typeof setTimeout> | undefined;

  #shown = (): void => {
    if (document.visibilityState === "visible") this.#readAll();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#phone = gateway();
    const changed = (): void => this.requestUpdate();
    this.#since = Date.now();
    this.#readingTimer = setTimeout(changed, READING_AFTER_MS);
    this.#take(this.route);
    const rows = new Feed<Rows>(() => api("services", "api/rows"), parseRows, () => this.#listed());
    this.#rows = rows;
    void rows.refresh().then(() => {
      if (this.#rows === rows) this.#stop = subscribe("services", "rows", (data) => rows.topic(data));
    });
    if (!this.#phone) {
      this.#projects = new Feed<Projects>(() => api("services", "api/projects"), parseProjects, changed);
      void this.#projects.refresh();
    }
    document.addEventListener("visibilitychange", this.#shown);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    for (const feed of [this.#rows, this.#projects, this.#job?.feed, this.#reads?.one, this.#reads?.ends]) feed?.close();
    this.#rows = this.#projects = null;
    this.#reads = null;
    clearTimeout(this.#copyTimer);
    this.#job = null;
    this.#stop?.();
    this.#stop = null;
    clearTimeout(this.#jobTimer);
    clearTimeout(this.#readingTimer);
    clearTimeout(this.#reloadTimer);
    document.removeEventListener("visibilitychange", this.#shown);
  }

  protected willUpdate(changed: Map<PropertyKey, unknown>): void {
    if (changed.has("route") && this.isConnected) {
      this.#take(this.route);
      this.#openPending();
    }
    this.#readOpen();
  }

  /** The opened row's own reads: `/api/row` when it opens and whenever the list gives its
   * row a new value, and `/api/ends` for slots' server when it opens. Closing it drops
   * them. */
  #readOpen(): void {
    const id = this.#open;
    const row = id === null ? undefined : this.#rows?.value?.rows.find((item) => item.id === id);
    const reads = this.#reads;
    if (row === undefined || !this.isConnected) {
      if (reads !== null && (id === null || reads.id !== id)) this.#dropReads();
      return;
    }
    const seen = JSON.stringify(row);
    if (reads !== null && reads.id === row.id) {
      if (reads.seen === seen) return;
      reads.seen = seen;
      void reads.one.refresh();
      return;
    }
    this.#dropReads();
    const changed = (): void => this.requestUpdate();
    const query = `?id=${encodeURIComponent(row.id)}`;
    const fresh: OpenReads = {
      id: row.id, seen,
      one: new Feed<One>(() => api("services", `api/row${query}`), parseOne, changed),
      ends: null,
    };
    if (row.ends_slots) {
      const ends: Feed<Ends> = new Feed<Ends>(() => api("services", `api/ends${query}`), parseEnds, () => {
        if (ends.value !== null && ends.failure === null) this.#ends = { id: row.id, ends: ends.value };
        changed();
      });
      fresh.ends = ends;
    }
    this.#reads = fresh;
    void fresh.one.refresh();
    void fresh.ends?.refresh();
  }

  #dropReads(): void {
    this.#reads?.one.close();
    this.#reads?.ends?.close();
    this.#reads = null;
  }

  /** Puts `text` on the clipboard; its `Copy` then reads `Copied` for a while. */
  async #copy(text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    this.#copied = text;
    clearTimeout(this.#copyTimer);
    this.#copyTimer = setTimeout(() => {
      this.#copied = null;
      this.requestUpdate();
    }, COPIED_MS);
    this.requestUpdate();
  }

  protected updated(): void {
    const panel = this.renderRoot.querySelector<HTMLElement>("moxi-services-panel");
    this.toggleAttribute("data-side", panel !== null && !panel.hidden);
    const to = this.#scrollTo;
    if (to === null) return;
    this.#scrollTo = null;
    if (this.#open !== null) {
      this.renderRoot.querySelector(`.item[data-id="${CSS.escape(this.#open)}"]`)?.scrollIntoView({ block: "nearest" });
    }
  }

  #readAll(): void {
    void this.#rows?.refresh();
    void this.#projects?.refresh();
  }

  // -- the address -------------------------------------------------------------------------

  /** Takes `route` as the address to open once the rows are read. */
  #take(route: string): void {
    const { address, known } = parseRoute(route ?? "");
    this.#pending = address;
    this.#pendingKnown = known;
  }

  /** The rows were read: an opened row that is no longer listed closes, and a route given
   * before they were read is opened. */
  #listed(): void {
    const value = this.#rows?.value ?? null;
    if (value !== null && value.state === "up" && this.#rows?.failure === null && this.#open !== null
        && !value.rows.some((row) => row.id === this.#open)) {
      this.#open = null;
      this.#write();
    }
    this.#openPending();
    this.requestUpdate();
  }

  /** Opens the route given, once the rows have been read. */
  #openPending(): void {
    const address = this.#pending;
    const value = this.#rows?.value ?? null;
    if (address === null || value === null || value.state !== "up" || this.#rows?.failure !== null) return;
    this.#pending = null;
    const keys = installed("projects") ? projectKeys(value.rows) : null;
    const found = resolve(address, value.rows.map((row) => row.id), keys, !this.#phone);
    this.#unknown = this.#pendingKnown ? found.unknown : NO_SUCH_ADDRESS;
    this.#filter = found.address.project;
    this.#open = found.address.row;
    this.#panel = found.address.panel;
    this.#menu = false;
    if (this.#panel === null && this.#open !== null) {
      this.#scrollTo = "row";
    }
    this.#write();
    this.requestUpdate();
  }

  /** What is open, as an address. */
  #address(): Address {
    if (this.#panel !== null) return { ...NOTHING_OPEN, panel: this.#panel };
    return { project: this.#filter, row: this.#open, panel: null };
  }

  /** Puts what is open in the browser's address, rewriting the entry the page is on. */
  #write(): void {
    const path = pathToWrite(this.#address(), location.pathname);
    if (path !== null) history.replaceState(history.state, "", path);
  }

  /** `Open` or `Close`: the row opens, or closes, and the address says so. */
  #toggle(id: string): void {
    this.#open = this.#open === id ? null : id;
    this.#panel = null;
    this.#unknown = null;
    if (this.#ended?.where !== this.#open) this.#ended = null;
    if (this.#asking?.where !== this.#open) this.#asking = null;
    this.#write();
    this.requestUpdate();
  }

  /** The filter shows `key`'s rows, or every row with null. A row open outside it closes. */
  #filterTo(key: string | null): void {
    this.#filter = key;
    this.#menu = false;
    this.#panel = null;
    this.#unknown = null;
    if (key !== null && this.#open !== null) {
      const open = this.#rows?.value?.rows.find((row) => row.id === this.#open);
      if (open?.project !== key || open.form === "module") this.#open = null;
    }
    this.#write();
    this.requestUpdate();
  }

  /** An act is on its way or its job runs: every act's control is off then. */
  get #busy(): boolean {
    return this.#sending || (this.#job !== null && !this.#job.ended);
  }

  // -- acts --------------------------------------------------------------------------------

  /** A press of a row's act. One that can hurt or spend asks in place first (`#ask`);
   * slots' server's Stop and Restart read what they would end, and go plain when nothing
   * is in use; every other act is sent at once. */
  async #press(row: Row, a: RowAct): Promise<void> {
    if (this.#busy) return;
    this.#asking = null;
    if (a.name === "remove") {
      this.#ask(row, a, removeConfirm(row));
      return;
    }
    const usage = usageConfirm(row, a);
    if (usage !== null) {
      this.#ask(row, a, usage);
      return;
    }
    if (row.ends_slots && (a.body.act === "stop" || a.body.act === "restart")) {
      await this.#slotsAsk(row, a, null);
      return;
    }
    await this.#send(row, a, {});
  }

  /** Shows `confirm` for the act `a` under its row's foot, opening the row. */
  #ask(row: Row, a: RowAct, confirm: Confirm): void {
    this.#asking = { where: row.id, act: a, confirm };
    this.#told = null;
    this.#ended = null;
    if (this.#open !== row.id) {
      this.#open = row.id;
      this.#panel = null;
      this.#unknown = null;
      this.#write();
    }
    this.requestUpdate();
  }

  /** slots' server's Stop or Restart: reads `/api/ends` now; with nothing in use the act
   * goes plain, else its warning asks in place. `refused` is the guard's sentence when
   * this is a read again after it refused the act: the warning is shown anew, or, when
   * nothing is in use now, the sentence. */
  async #slotsAsk(row: Row, a: RowAct, refused: string | null): Promise<void> {
    this.#sending = true;
    this.#told = null;
    this.#ended = null;
    this.requestUpdate();
    let ends: Ends | null = null;
    try {
      const answer = await api("services", `api/ends?id=${encodeURIComponent(row.id)}`);
      ends = answer.status === 200 ? parseEnds(answer.data) : null;
      if (ends === null) {
        const said = transportSaid(answer.status, saidOf(answer.data));
        this.#told = { where: row.id, said: { tone: "error", lines: [said.text] } };
        return;
      }
      this.#ends = { id: row.id, ends };
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
    const confirm = slotsConfirm(a, ends, this.#phone);
    if (confirm !== null) this.#ask(row, a, confirm);
    else if (refused !== null) this.#told = { where: row.id, said: { tone: "error", lines: [refused] } };
    else await this.#send(row, a, {});
  }

  /** Sends the act `a` as a plain `/api/act` with a new job key and what its confirm
   * showed, then follows its job to its end. */
  async #send(row: Row, a: RowAct, sends: Sends): Promise<void> {
    if (this.#busy) return;
    this.#asking = null;
    this.#sending = true;
    this.#told = null;
    this.#ended = null;
    this.requestUpdate();
    let refused: string | null = null;
    try {
      const answer = await api("services", "api/act", { ...a.body, ...sends, job: mintJob() });
      const status = answer.status === 202 ? (answer.data as { status?: unknown } | null)?.status : undefined;
      if (status !== undefined) {
        this.#follow(status, `${a.label} ${row.name}`, row.id, a);
        return;
      }
      const said = transportSaid(answer.status, saidOf(answer.data));
      if (answer.status === 422 && row.ends_slots && (a.body.act === "stop" || a.body.act === "restart")) {
        refused = said.text;
      } else {
        this.#told = { where: row.id, said: { tone: said.reread ? "warn" : "error", lines: [said.text] } };
        if (said.reread) void this.#rows?.refresh();
      }
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
    // The guard refused it: what runs in the slots changed since he was shown it.
    if (refused !== null) await this.#slotsAsk(row, a, refused);
  }

  /** A press on the phone: the act goes through the shell's sheet to its kept confirmed
   * route, the sheet saying what the desk's confirm says without its redacted words.
   * slots' server's Stop and Restart read what they would end first, and the sheet gives
   * the count and sends it; when the guard refuses one, the slots are read again and the
   * sheet asks anew with its sentence. */
  async #phonePress(row: Row, a: RowAct): Promise<void> {
    if (this.#busy) return;
    const slots = row.ends_slots && (a.body.act === "stop" || a.body.act === "restart");
    this.#sending = true;
    this.#told = null;
    this.#ended = null;
    this.requestUpdate();
    try {
      let refused: string | null = null;
      for (;;) {
        let ends: Ends | null = null;
        if (slots) {
          const answer = await api("services", `api/ends?id=${encodeURIComponent(row.id)}`);
          ends = answer.status === 200 ? parseEnds(answer.data) : null;
          if (ends === null) {
            const said = transportSaid(answer.status, saidOf(answer.data));
            this.#told = { where: row.id, said: { tone: "error", lines: [said.text] } };
            return;
          }
          this.#ends = { id: row.id, ends };
          this.requestUpdate();
        }
        const route = phoneRoute(a, ends !== null ? slotsConfirm(a, ends, true)?.sends ?? {} : {});
        const sheet = phoneSheet(row, a, ends);
        const outcome = await confirm("services", route.path, route.body, refused !== null ? [refused, ...sheet] : sheet);
        if (outcome.outcome === "accepted") {
          this.#follow(outcome.status, `${a.label} ${row.name}`, row.id, a);
          return;
        }
        if (slots && outcome.outcome === "refused" && outcome.code === 422) {
          refused = outcome.text;
          continue;
        }
        // Nothing was sent on cancelled and desk, and confirm() has already shown where to
        // take a desk act.
        const said = outcomeSaid(outcome);
        if (said !== null) this.#told = { where: row.id, said };
        if (outcome.outcome === "unknown") void this.#rows?.refresh();
        return;
      }
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  /** The panel's `Add service` or `Adopt unit`: posts its body to `/api/add` and follows
   * the job, or says what the form still needs. */
  async #add(built: Built): Promise<void> {
    if (this.#busy) return;
    if ("missing" in built) {
      this.#told = { where: ADD, said: { tone: "error", lines: [built.missing] } };
      this.requestUpdate();
      return;
    }
    this.#sending = true;
    this.#told = null;
    this.requestUpdate();
    const label = `${built.body.unit !== undefined ? "Adopt" : "Add"} ${String(built.body.name)}`;
    try {
      const answer = await api("services", "api/add", built.body);
      const status = answer.status === 202 ? (answer.data as { status?: unknown } | null)?.status : undefined;
      if (status !== undefined) {
        this.#follow(status, label, ADD);
        return;
      }
      this.#told = { where: ADD, said: addRefused(answer.status, saidOf(answer.data)) };
      if (answer.status === 0 || answer.status >= 500) void this.#rows?.refresh();
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  /** Follows the job at the status link `status` until it ends. */
  #follow(status: unknown, label: string, where: string, act: RowAct | null = null): void {
    this.#job?.feed.close();
    clearTimeout(this.#jobTimer);
    const path = statusPathOf(status);
    if (path === null) {
      this.#job = null;
      this.#told = { where, said: { tone: "warn", lines: [
        `${label}: services' page server took it but gave no status link to read; the list has been read again.`] } };
      void this.#rows?.refresh();
      return;
    }
    const job: Job = { feed: new Feed<Status>(() => api("services", path), parseStatus, () => this.#jobRead(job)),
                       label, where, fails: 0, ended: false, act };
    this.#job = job;
    if (act === null) this.#told = { where, said: { tone: "muted", lines: [`${label}: sent…`] } };
    void job.feed.refresh();
  }

  /** A row's act ended: `done` leaves the row to show its new state; one that didn't hold
   * keeps the row open with what it came to; one whose end can't be read may have been
   * done, and the rows are read again. */
  #pressEnded(job: Job, a: RowAct, status: Status | null): void {
    const said = jobSaid(a, status?.state ?? "unknown");
    this.#readAgain(job.where);
    // A Restart of ui's shell that didn't fail or get refused reloads the page once the
    // shell answers again.
    if (job.where === SHELL && a.body.act === "restart" && status?.state !== "failed" && status?.state !== "refused") {
      this.#reloadWhenBack(Date.now() + RELOAD_FOR_MS);
    }
    if (said !== null) {
      this.#ended = { where: job.where, heavy: said.heavy,
                      lines: (status?.lines ?? []).filter((line): line is string => line !== null),
                      again: said.again !== null ? { label: said.again, act: a } : null };
      if (this.#open !== job.where) {
        this.#open = job.where;
        this.#panel = null;
        this.#write();
      }
    }
    void this.#rows?.refresh();
  }

  /** After a Restart of ui's shell: reads the rows once a second until the shell answers,
   * then reloads the document; it gives up after `RELOAD_FOR_MS`. */
  #reloadWhenBack(until: number): void {
    clearTimeout(this.#reloadTimer);
    this.#reloadTimer = setTimeout(async () => {
      if (!this.isConnected) return;
      const answer = await api("services", "api/rows");
      if (answer.status === 200) location.reload();
      else if (Date.now() < until) this.#reloadWhenBack(until);
    }, RELOAD_POLL_MS);
  }

  /** After an act on the opened row, its own reads are read again. */
  #readAgain(where: string): void {
    const reads = this.#reads;
    if (reads === null || reads.id !== where) return;
    void reads.one.refresh();
    void reads.ends?.refresh();
  }

  #jobRead(job: Job): void {
    if (job !== this.#job || job.ended) return;
    const feed = job.feed;
    job.fails = feed.failure !== null ? job.fails + 1 : 0;
    const value = feed.failure === null ? feed.value : null;
    if (job.act !== null && ((value !== null && value.state !== "running") || feed.gone || job.fails >= JOB_TRIES)) {
      job.ended = true;
      this.#pressEnded(job, job.act, value !== null && value.state !== "running" ? value : null);
    } else if (value !== null && value.state !== "running") {
      job.ended = true;
      this.#told = { where: job.where, said: statusSaid(job.label, value) };
      this.#readAgain(job.where);
      // A done add leaves the panel open with what it came to, its form empty again.
      if (value.state === "done" && job.where === ADD) this.#panelKey += 1;
      void this.#rows?.refresh();
    } else if (feed.gone || job.fails >= JOB_TRIES) {
      job.ended = true;
      this.#told = { where: job.where, said: statusLost(job.label, feed.failure) };
      void this.#rows?.refresh();
    } else {
      if (value !== null && job.act === null) this.#told = { where: job.where, said: statusSaid(job.label, value) };
      this.#jobTimer = setTimeout(() => { void feed.refresh(); }, JOB_POLL_MS);
    }
    this.requestUpdate();
  }

  // -- rendering ---------------------------------------------------------------------------

  render() {
    const rows = this.#rows;
    if (rows === null) return nothing;
    const when = { now: Date.now() / 1000 };
    const state = pageState(rows, Date.now() - this.#since, this.#phone, when);
    if (state.state === "gone") return html`<p class="problem">${state.line}</p>`;
    const told = this.#told;
    const value = rows.value;
    const all = state.rows && value !== null ? value.rows : null;
    const projects = installed("projects");
    const filter = all !== null && this.#filter !== null ? filtered(all, this.#filter) : null;
    // A key no row of his carries keeps the filter, with its own line in place of the list.
    const none = filter !== null && filter.rows.length === 0;
    const shape = all !== null && !none
      ? listShape(filter?.rows ?? all, value!.at, projects, when, { quiet: state.state === "unread" }) : null;
    const ids = new Set((value?.rows ?? []).map((row) => row.id));
    const loose = told !== null && told.where !== ADD && (told.where === null || !ids.has(told.where)) ? told.said : null;
    return html`<section data-part="list" data-state=${state.state}>
        ${this.#busy ? html`<p class="state dim" data-line="busy">An act is running.</p>` : nothing}
        ${loose !== null ? this.#saidPart(loose) : nothing}
        ${shape === null && !none && state.line !== null ? html`<p class=${state.state === "loading" ? "state dim" : "state problem"}
          data-line=${state.state} role="status">${state.line}</p>` : nothing}
        ${filter !== null && all !== null ? this.#filterLine(all, filter.shown, state.acts) : nothing}
        ${shape !== null ? this.#list(shape, state) : nothing}
        ${none ? html`<p class="state dim" data-line="no-service">${noServiceBelongs(this.#filter!)}</p>` : nothing}
        ${this.#unknown !== null && state.acts ? html`<p class="foot" data-line="unknown">${this.#unknown}</p>` : nothing}
        ${value !== null && state.rows ? problemLines(value).map((line) => html`<p class="foot" data-line="module-problem">${line}</p>`)
          : nothing}
      </section>
      ${this.#panelPart(state.acts)}`;
  }

  /** `Project <key> ▾`, its menu of the projects his rows name, `<n> of <m> services` and
   * `Show all`. */
  #filterLine(rows: readonly Row[], shown: string, acts: boolean) {
    const key = this.#filter!;
    return html`<div class="filt" data-part="filter"><span class="pick"><span class="k">${PROJECT}</span><button
        type="button" data-action="pick" aria-haspopup="menu" aria-expanded=${this.#menu ? "true" : "false"}
        ?disabled=${!acts} @click=${() => { this.#menu = !this.#menu; this.requestUpdate(); }}><span
        data-line="project">${key}</span><span class="v" aria-hidden="true">▾</span></button>
      ${this.#menu && acts ? html`<div class="menu" role="menu">${projectKeys(rows).map((k) => html`<button type="button"
        role="menuitem" aria-current=${k === key ? "true" : "false"} @click=${() => this.#filterTo(k)}>${k}</button>`)}</div>`
        : nothing}</span>
      <span class="n"><span data-line="shown">${shown}</span><span class="sep">·</span><button type="button"
        data-action="show-all" ?disabled=${!acts} @click=${() => this.#filterTo(null)}>${SHOW_ALL}</button></span></div>`;
  }

  #saidPart(said: Said) {
    return html`<div class="said ${said.tone}" data-line="said" data-tone=${said.tone} role="status">
      ${said.lines.map((line) => html`<p>${line}</p>`)}</div>`;
  }

  /** The first group, then the groups. While the page server isn't answering, or every
   * row is `unknown` for one reason, the first group says so in place of what rises. */
  #list(shape: ListShape, state: PageState) {
    const acts = state.acts;
    const add = acts && !this.#phone;
    const row = (item: RowView) => this.#row(item, acts);
    return html`<div class="grps ${state.dim ? "dim" : ""}">
      <section class="grp" data-part="first"><div class="gk"><span data-line="count">${shape.label}</span>${add && shape.his
        ? html`<small><button type="button" data-action="add" @click=${() => this.#toAdd()}>${ADD_A_SERVICE}</button></small>`
        : nothing}</div>
        <div class="gl">${state.line !== null ? html`<div class="one state" role="status"><span data-line=${state.state}>${
            state.line}</span>${state.more !== null ? html`<span class="more" data-line="state-more">${state.more}</span>`
            : nothing}</div>` : nothing}${repeat(shape.risen, (item) => item.row.id, row)}${state.line !== null ? nothing : html`
          ${shape.sentence !== null || shape.read !== null ? html`<div class="one">
            ${shape.sentence !== null ? html`<span data-line="sentence">${shape.sentence}${shape.more !== null
              ? html` <span class="muted" data-line="sentence-more">${shape.more}</span>` : nothing}</span>` : nothing}
            ${shape.read !== null ? html`<span class="read" data-line="read">${shape.read}</span>` : nothing}</div>` : nothing}
          ${shape.counts !== null ? html`<div class="counts" data-line="counts">${shape.counts}</div>` : nothing}`}
        </div></section>
      ${!shape.his && this.#filter === null ? html`<section class="grp" data-part="group" data-group="his" data-project="">
        <div class="gk" data-line="group">${YOURS}</div><div class="gl"><div class="yours"><span data-line="what">${
          WHAT_A_SERVICE_IS}</span>${add ? html`<button type="button" data-action="add" @click=${() => this.#toAdd()}>${
          ADD_A_SERVICE}</button>` : nothing}</div></div></section>` : nothing}
      ${repeat(shape.groups, ({ group }) => `${group.moxi ? "moxi" : "his"}:${group.project ?? group.label}`, ({ group, rows }) => html`
        <section class="grp" data-part="group" data-group=${group.moxi ? "moxi" : group.project !== null ? "project" : "his"}
          data-project=${group.project ?? ""}><div class="gk" data-line="group">${group.label}</div>
          <div class="gl">${repeat(rows, (item) => item.row.id, row)}</div></section>`)}
    </div>`;
  }

  /** One row: line one's name, dim words and fact; a risen row's sentence on line two; the
   * row's acts under it, wrapping on a narrow screen; what its last act came to. A fact in the text
   * colour is drawn by the page at line one's right, since the row draws its own fact
   * either dim or heavy. */
  #row(item: RowView, acts: boolean) {
    const row = item.row;
    const told = this.#told?.where === row.id ? this.#told.said : null;
    // While its act's job runs, the row's fact says what is being done.
    const job = this.#job;
    const doing = job !== null && !job.ended && job.where === row.id && job.act !== null ? runningFact(job.act) : null;
    const own = item.weight === "text" && doing === null;
    const fact = doing ?? item.fact;
    // `Open` on the phone only where it runs acts.
    const opens = acts && (!this.#phone || gatewayActs());
    const open = opens && this.#open === row.id;
    // The closed row's one act; on the phone only where it runs acts.
    const closed = acts && !open ? this.#actsOf(row).closed : null;
    return html`<div class="item" data-id=${row.id} data-cause=${row.verdict.cause ?? ""} data-verdict=${row.verdict.state}
        data-weight=${item.weight} data-kind=${row.kind} data-open=${open ? "true" : "false"}>
      <moxi-ui-row name=${item.name} where=${item.where ?? nothing} fact=${own ? nothing : fact}
        ?fact-heavy=${item.weight === "heavy" && doing === null} lead=${item.line ?? nothing} ?toggles=${opens} ?open=${open}
        @moxi-row-toggle=${() => this.#toggle(row.id)}>
        ${own ? html`<span slot="acts-one" class="fact" data-line="fact">${item.fact}</span>` : nothing}
        ${closed !== null ? this.#button(row, closed, "closed") : nothing}
        ${opens ? html`<button type="button" slot="acts-one" class="do" data-action="open" aria-expanded=${open ? "true" : "false"}
          aria-label=${`${open ? "Close" : "Open"} ${row.name}`} @click=${() => this.#toggle(row.id)}>${open ? "Close" : "Open"}</button>`
          : nothing}
        ${open ? this.#opened(row) : nothing}
      </moxi-ui-row>
      ${told !== null ? this.#saidPart(told) : nothing}
    </div>`;
  }

  /** The opened row's lines, each only when its fact is there, then its module's links. */
  #opened(row: Row) {
    const reads = this.#reads?.id === row.id ? this.#reads : null;
    const opened: Opened = {
      phone: this.#phone, usage: installed("usage"),
      modulePage: components(row.module).some((item) => item.name === "page") ? row.module : null,
    };
    const lines = openedLines(row, reads?.one.value ?? null, reads?.ends?.value ?? null, { now: Date.now() / 1000 }, opened);
    const links = footLinks(row, opened);
    const ended = this.#ended?.where === row.id ? this.#ended : null;
    return html`<div class="det" data-part="opened">
      ${lines.map((line) => line.label === "Details" ? this.#details(line)
        : line.label === "State" && ended !== null ? html`${this.#line(line)}${this.#endedPart(row, ended)}` : this.#line(line))}
      ${this.#foot(row)}
      ${this.#asking?.where === row.id && !this.#phone ? this.#confirmPart(row, this.#asking) : nothing}
      ${links.length ? html`<div class="links" data-part="links">${links.map((link) =>
        this.#link(link, "module-link"))}</div>` : nothing}
    </div>`;
  }

  /** The acts a row offers here: the desk's, or on the phone those it runs, none with its
   * acts off. */
  #actsOf(row: Row): { closed: RowAct | null; foot: RowAct[] } {
    return this.#phone ? phoneActs(row, gatewayActs()) : actsOf(row);
  }

  /** A row's act as a button: `closed` on the closed row's line, `foot` under its lines. */
  #button(row: Row, a: RowAct, where: "closed" | "foot", label = a.label) {
    const warn = this.#warns(row, a);
    const cls = where === "closed" ? `do act${warn ? " w" : ""}`
      : `act ${warn ? "w" : PLAIN_ACTS.has(a.name) && !a.tucked ? "" : "d"}`;
    return html`<button type="button" slot=${where === "closed" ? "acts-one" : nothing} class=${cls} data-action=${a.name}
      data-warn=${warn ? "true" : nothing} ?disabled=${this.#busy} aria-label=${`${label} ${row.name}`}
      @click=${() => void (this.#phone ? this.#phonePress(row, a) : this.#press(row, a))}>${label}</button>`;
  }

  /** slots' server's Stop and Restart are in the warning colour while its last read found
   * something in use or couldn't read. */
  #warns(row: Row, a: RowAct): boolean {
    if (!row.ends_slots || (a.body.act !== "stop" && a.body.act !== "restart")) return false;
    return this.#ends?.id === row.id && warned(this.#ends.ends);
  }

  /** An act's confirm in place: its question, its lines, a command with `Copy`, and the
   * button that goes beside the one that keeps. */
  #confirmPart(row: Row, asking: Asking) {
    const { confirm, act } = asking;
    const keep = (): void => {
      this.#asking = null;
      this.requestUpdate();
    };
    return html`<div class="cf ${confirm.warn ? "w" : ""}" data-part="confirm" data-act=${act.name}
        data-warn=${confirm.warn ? "true" : "false"} role="group" aria-label=${confirm.ask}>
      <p class="q" data-line="ask">${confirm.ask}</p>
      ${confirm.lines.map((line) => html`<div class="r" data-line=${line.label ?? "note"}><span class="k">${line.label ?? ""}</span>
        <div class="f"><p class=${line.label === null ? "d" : ""}>${line.text}</p></div></div>`)}
      ${confirm.command !== null ? this.#line({ label: "", text: null, mono: confirm.command, copy: true }) : nothing}
      <div class="btns"><button type="button" class="btn ${confirm.warn ? "wn" : ""}" data-action="go" ?disabled=${this.#busy}
          @click=${() => void this.#send(row, act, confirm.sends)}>${confirm.go}</button>
        <button type="button" class="lnk" data-action="keep" @click=${keep}>${confirm.keep}</button></div>
    </div>`;
  }

  /** The opened row's foot: its acts, his daemon's word beside Stop, and the tucked acts. */
  #foot(row: Row) {
    const foot = this.#actsOf(row).foot;
    if (!foot.length) return nothing;
    const lasts = row.form !== "module" && row.kind === "daemon" && foot.some((a) => a.name === "stop");
    const reloads = row.id === SHELL && foot.some((a) => a.name === "restart");
    return html`<div class="foot" data-part="foot">${foot.map((a) => this.#button(row, a, "foot"))}${lasts
      ? html`<span class="d note" data-line="stop-lasts">${STOP_LASTS}</span>` : nothing}${reloads
      ? html`<span class="d note" data-line="shell-restart">${SHELL_RESTART}</span>` : nothing}</div>`;
  }

  /** In `State`, what a row's act that didn't hold came to, and the act again. */
  #endedPart(row: Row, ended: Ended) {
    return html`<div class="r" data-line="ended"><span class="k"></span><div class="f">
      <p><b data-line="heavy">${ended.heavy}</b></p>
      ${ended.lines.map((line) => html`<p class="d" data-line="job-line">${line}</p>`)}
      ${ended.again !== null ? html`<p>${this.#button(row, ended.again.act, "foot", ended.again.label)}</p>` : nothing}
    </div></div>`;
  }

  /** One labelled line: its text, or its command in mono with `Copy`, then dim words and
   * a link at the right; a command under text goes on its own line; then its more lines. */
  #line(line: Line) {
    const mono = line.mono ?? null;
    const code = mono === null ? nothing : html`<span class="code"><code data-line="mono">${mono}</code>${line.copy
      ? html`<button type="button" class="copy" data-action="copy" aria-label=${`Copy ${mono}`}
        @click=${() => void this.#copy(mono)}>${this.#copied === mono ? "Copied" : "Copy"}</button>` : nothing}</span>`;
    const first = line.text !== null;
    const runs = line.label === "Last runs";
    return html`<div class="r" data-line=${line.label}><span class="k">${line.label}</span><div class="f">
        <p>${first ? html`<span data-line="text">${line.text}</span>` : code}${line.dim
          ? html`<span class="d" data-line="dim"> · ${line.dim}</span>` : nothing}${line.link
          ? html` <span class="d">·</span> ${this.#link(line.link, "line-link")}` : nothing}</p>
        ${first && mono !== null ? html`<p>${code}</p>` : nothing}
        ${(line.more ?? []).map((more) => html`<p class=${runs ? "" : "d"} data-line="more">${more}</p>`)}
      </div></div>`;
  }

  /** `Details`, folded: the supervisor's words, the verdict's, the row's ids. */
  #details(line: Line) {
    return html`<details class="r" data-line="Details"><summary class="k">${line.label}</summary><div class="f">${
      (line.more ?? []).map((more) => html`<p class="d" data-line="more">${more}</p>`)}</div></details>`;
  }

  /** A link to another page in the shell. */
  #link(link: { text: string; module: string; route: string }, action: string) {
    return html`<a href=${`/m/${link.module}/${link.route}`} data-action=${action}
      @click=${(event: MouseEvent) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigate(link.module, link.route);
      }}>${link.text}</a>`;
  }

  /** `Add a service`: the panel opens on its way for a command, and the address says so. */
  #toAdd(): void {
    this.#panel = "add";
    this.#open = null;
    this.#unknown = null;
    this.#menu = false;
    if (this.#told?.where === ADD) this.#told = null;
    this.#write();
    this.requestUpdate();
  }

  /** The panel, while the address says `add` or `adopt`; it lies hidden while the page
   * offers no acts. A fresh one, its form empty, once an add is done. */
  #panelPart(acts: boolean) {
    const way = this.#panel;
    if (way === null || this.#phone) return nothing;
    const told = this.#told?.where === ADD ? this.#told.said : null;
    const projects = this.#projects;
    return keyed(this.#panelKey, html`<moxi-services-panel way=${way} ?busy=${this.#busy} .said=${told}
      .projects=${projects === null ? null : { value: projects.value, failure: projects.failure?.why ?? null }}
      .withProjects=${installed("projects")} ?hidden=${!acts}
      @moxi-services-panel-close=${() => this.#closePanel()}
      @moxi-services-panel-way=${(event: CustomEvent<Way>) => this.#pickWay(event.detail)}
      @moxi-services-panel-send=${(event: CustomEvent<Built>) => void this.#add(event.detail)}></moxi-services-panel>`);
  }

  /** The panel's other way: the address says it. */
  #pickWay(way: Way): void {
    this.#panel = way;
    if (this.#told?.where === ADD) this.#told = null;
    this.#write();
    this.requestUpdate();
  }

  /** The panel's `Close`, `Cancel` or Escape: nothing is open, the focus back on `Add a
   * service`. */
  #closePanel(): void {
    this.#panel = null;
    if (this.#told?.where === ADD) this.#told = null;
    this.#write();
    this.requestUpdate();
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>('[data-action="add"]')?.focus());
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ServicesPage);

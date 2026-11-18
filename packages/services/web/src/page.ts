// <moxi-services-page route="...">: services' panel.
//
// - The address (`address.ts`): the route opens the page (`""`), a row (`row/<id>`), the
//   add form on one of its two ways (`add`, `adopt`), the filter to one project's rows
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
// - Each row's acts, in its line two: start, stop and restart for a daemon, run now and run again in place
//   of start and restart for a timer, enable and disable, and remove for a row the user
//   added. Every one is a confirmed act: its button opens the shell's confirm sheet,
//   which mints a new key at each confirmation and sends the act once. A run of a row
//   that launches a session says on its sheet that it spends usage.
// - The add form: a command line to run, or a unit already on the system to adopt, with
//   its schedule, directory, health command, project and doc. It is one plain request,
//   no sheet, and the server checks it again.
//
// Each act runs as a job on services' page server: the page reads the job's status link
// once a second until it ends, shows its lines under the row, and keeps every act's
// control off while one runs, since the server runs its acts one at a time anyway. When a
// job ends the page reads the rows again. Otherwise it reads `/api/rows` when it opens and
// when its tab is shown again, and follows the topic `rows`.
//
// On the phone the add form, the projects list and remove are left out: from the phone a
// service is acted on, never added or removed. Where the phone runs no acts here, each
// sheet says to do the act at the desk.

import { css, html, LitElement, nothing } from "lit";
import { live } from "lit/directives/live.js";
import { repeat } from "lit/directives/repeat.js";
import { api, components, confirm, gateway, gatewayActs, installed, navigate, subscribe } from "/m/ui/client.js";

import { NO_SUCH_ADDRESS, NOTHING_OPEN, parseRoute, pathToWrite, resolve } from "./address.ts";
import type { Address } from "./address.ts";

import type { Ends } from "./acts-model.ts";
import { Feed, parseEnds, parseOne, parseProjects, parseRows, parseStatus, saidOf, statusPathOf } from "./feed.ts";
import type { Projects, Row, Rows, Status } from "./feed.ts";
import {
  ADD_A_SERVICE, filtered, listShape, noServiceBelongs, pageState, problemLines, PROJECT, projectKeys, READING_AFTER_MS, SHOW_ALL,
  WHAT_A_SERVICE_IS, YOURS,
} from "./list-model.ts";
import type { ListShape, PageState, RowView } from "./list-model.ts";
import { footLinks, openedLines } from "./opened-model.ts";
import type { Line, One, Opened } from "./opened-model.ts";
import {
  addBody, addRefused, DAYS, emptyForm, outcomeSaid, shownActs, statusLost, statusSaid,
} from "./views.ts";
import type { Act, AddForm, Said } from "./views.ts";

const TAG = "moxi-services-page";

/** How often a running job is read. */
const JOB_POLL_MS = 1000;
/** After this many failed reads in a row the page stops reading a job. */
const JOB_TRIES = 5;
/** Where the page's line about the add form goes. */
const ADD = "add";

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
  .bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); min-width: 0; }
  :host { max-width: 680px; margin: 0 auto; padding: 4px 0 16px; font-size: 14px; line-height: 20px; }
  section[data-part="add"] { display: grid; gap: var(--moxi-gap); min-width: 0; margin-top: 24px; padding: 12px;
                             border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius-lg);
                             background: var(--moxi-bg-raised); }
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
  .grp .gk button, .item .acts button { padding: 0; margin: 0; border: 0; background: none; font: inherit;
                                        text-align: left; cursor: pointer; }
  .grp .gk small button { color: var(--moxi-text); }
  .grp .gk button:hover:not(:disabled), .item .acts button:hover:not(:disabled) { background: none; }
  .item .acts { display: flex; flex-wrap: wrap; gap: 2px 18px; margin: -4px 0 7px; }
  .item .acts:empty { display: none; }
  .item .acts button.do { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
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
  .det .links { display: flex; flex-wrap: wrap; gap: 2px 18px; padding: 6px 0 0 84px; font-size: 13px; }
  @media (max-width: 480px) {
    .det .r { grid-template-columns: minmax(0, 1fr); }
    .det details.r > .f, .det .links { padding-left: 0; }
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
  button, select, input { font: inherit; color: var(--moxi-text); max-width: 100%; }
  button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg-raised); cursor: pointer; }
  button.go { background: var(--moxi-accent); border-color: var(--moxi-accent); color: var(--moxi-bg); }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button.go:hover:not(:disabled) { background: var(--moxi-accent); }
  button:disabled, select:disabled, input:disabled { cursor: not-allowed; opacity: 0.6; }
  select, input[type=text] { min-width: 0; padding: 2px 6px; border: 1px solid var(--moxi-border-strong);
                             border-radius: var(--moxi-radius); background: var(--moxi-bg); }
  form { display: grid; gap: 8px; min-width: 0; }
  form label { display: grid; gap: 2px; min-width: 0; }
  form label > span, legend { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  form input[type=text], form select { width: 100%; }
  fieldset { display: flex; flex-wrap: wrap; gap: 4px var(--moxi-gap); min-width: 0; margin: 0; padding: 4px 8px;
             border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius); }
  fieldset label, label.check { display: flex; align-items: center; gap: 4px; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
`;

/** How long `Copied` stands in for `Copy`, in milliseconds. */
const COPIED_MS = 1500;

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

/** The job the page follows. */
interface Job {
  feed: Feed<Status>;
  label: string;
  where: string;
  fails: number;
  ended: boolean;
}

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
  #form: AddForm = emptyForm();
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
  #scrollTo: "row" | "add" | null = null;
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
      ends: row.ends_slots ? new Feed<Ends>(() => api("services", `api/ends${query}`), parseEnds, changed) : null,
    };
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
    const to = this.#scrollTo;
    if (to === null) return;
    this.#scrollTo = null;
    if (to === "add") {
      this.#focusAdd();
    } else if (this.#open !== null) {
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
    if (this.#panel !== null) {
      this.#form = { ...this.#form, source: this.#panel === "adopt" ? "unit" : "command" };
      this.#scrollTo = "add";
    } else if (this.#open !== null) {
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

  async #act(row: Row, act: Act): Promise<void> {
    if (this.#busy) return;
    this.#sending = true;
    this.#told = null;
    this.requestUpdate();
    const label = `${act.label.replace(/…$/, "")} ${row.name}`;
    try {
      const outcome = await confirm("services", act.path, act.body, act.sheet);
      if (outcome.outcome === "accepted") {
        this.#follow(outcome.status, label, row.id);
        return;
      }
      // Nothing was sent on cancelled and desk, and confirm() has already shown where to
      // take a desk act.
      const said = outcomeSaid(outcome);
      if (said !== null) this.#told = { where: row.id, said };
      if (outcome.outcome === "unknown") void this.#rows?.refresh();
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  async #add(): Promise<void> {
    if (this.#busy) return;
    const built = addBody(this.#form);
    if ("missing" in built) {
      this.#told = { where: ADD, said: { tone: "error", lines: [built.missing] } };
      this.requestUpdate();
      return;
    }
    this.#sending = true;
    this.#told = null;
    this.requestUpdate();
    const label = `Add ${String(built.body.name)}`;
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
  #follow(status: unknown, label: string, where: string): void {
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
                       label, where, fails: 0, ended: false };
    this.#job = job;
    this.#told = { where, said: { tone: "muted", lines: [`${label}: sent…`] } };
    void job.feed.refresh();
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
    if (value !== null && value.state !== "running") {
      job.ended = true;
      this.#told = { where: job.where, said: statusSaid(job.label, value) };
      this.#readAgain(job.where);
      if (value.state === "done" && job.where === ADD) {
        this.#form = emptyForm();
        if (this.#panel !== null) {
          this.#panel = null;
          this.#write();
        }
      }
      void this.#rows?.refresh();
    } else if (feed.gone || job.fails >= JOB_TRIES) {
      job.ended = true;
      this.#told = { where: job.where, said: statusLost(job.label, feed.failure) };
      void this.#rows?.refresh();
    } else {
      if (value !== null) this.#told = { where: job.where, said: statusSaid(job.label, value) };
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
    return html`${this.#phone && !gatewayActs()
        ? html`<p class="note" data-line="desk">This phone runs no acts here; each one's sheet says to do it at the desk.</p>` : nothing}
      <section data-part="list" data-state=${state.state}>
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
      ${this.#phone ? nothing : this.#addPart()}`;
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
    const busy = this.#busy;
    const told = this.#told?.where === row.id ? this.#told.said : null;
    const own = item.weight === "text";
    // `Open` on the phone only where it runs acts.
    const opens = acts && (!this.#phone || gatewayActs());
    const open = opens && this.#open === row.id;
    return html`<div class="item" data-id=${row.id} data-cause=${row.verdict.cause ?? ""} data-verdict=${row.verdict.state}
        data-weight=${item.weight} data-kind=${row.kind} data-open=${open ? "true" : "false"}>
      <moxi-ui-row name=${item.name} where=${item.where ?? nothing} fact=${own ? nothing : item.fact}
        ?fact-heavy=${item.weight === "heavy"} lead=${item.line ?? nothing} ?toggles=${opens} ?open=${open}
        @moxi-row-toggle=${() => this.#toggle(row.id)}>
        ${own ? html`<span slot="acts-one" class="fact" data-line="fact">${item.fact}</span>` : nothing}
        ${opens ? html`<button type="button" slot="acts-one" class="do" data-action="open" aria-expanded=${open ? "true" : "false"}
          aria-label=${`${open ? "Close" : "Open"} ${row.name}`} @click=${() => this.#toggle(row.id)}>${open ? "Close" : "Open"}</button>`
          : nothing}
        ${open ? this.#opened(row) : nothing}
      </moxi-ui-row>
      <div class="acts" data-part="acts">${(acts ? shownActs(row, this.#phone) : []).map((act) => html`
        <button type="button" class="do" data-action=${act.name} ?disabled=${busy}
          aria-label=${`${act.label.replace(/…$/, "")} ${row.name}`}
          @click=${() => void this.#act(row, act)}>${act.label}</button>`)}</div>
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
    return html`<div class="det" data-part="opened">
      ${lines.map((line) => line.label === "Details" ? this.#details(line) : this.#line(line))}
      ${links.length ? html`<div class="links" data-part="links">${links.map((link) =>
        this.#link(link, "module-link"))}</div>` : nothing}
    </div>`;
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

  /** `Add a service`: the add form, on its way for a command, and the address says so. */
  #toAdd(): void {
    this.#panel = "add";
    this.#open = null;
    this.#unknown = null;
    this.#menu = false;
    this.#form = { ...this.#form, source: "command" };
    this.#scrollTo = "add";
    this.#write();
    this.requestUpdate();
  }

  /** The add form takes the view, its first field focused. */
  #focusAdd(): void {
    const field = this.#form.source === "unit" ? "unit" : "name";
    const input = this.renderRoot.querySelector<HTMLInputElement>(`section[data-part="add"] input[name="${field}"]`);
    input?.scrollIntoView({ block: "nearest" });
    input?.focus();
  }

  #addPart() {
    const form = this.#form;
    const busy = this.#busy;
    const told = this.#told?.where === ADD ? this.#told.said : null;
    const projects = this.#projects?.value ?? null;
    const reason = projects?.reason ?? (this.#projects?.failure ? `they can't be listed: ${this.#projects.failure.why}` : null);
    const command = form.source === "command";
    const timer = form.kind === "timer";
    return html`<section data-part="add">
      <h2>Add</h2>
      <p class="dim">A command to run as a daemon or on a schedule, written as a unit, or a unit already on this system to adopt.</p>
      <form @submit=${(event: Event) => { event.preventDefault(); void this.#add(); }}>
        <label><span>Name</span>
          <input type="text" name="name" autocomplete="off" spellcheck="false" ?disabled=${busy}
            .value=${live(form.name)} @input=${this.#text("name")}></label>
        <fieldset><legend>Kind</legend>
          ${(["daemon", "timer"] as const).map((kind) => html`<label><input type="radio" name="kind" value=${kind}
            ?disabled=${busy} .checked=${live(form.kind === kind)} @change=${() => this.#set({ kind })}>${kind}</label>`)}
        </fieldset>
        <fieldset><legend>Runs</legend>
          <label><input type="radio" name="source" value="command" ?disabled=${busy} .checked=${live(command)}
            @change=${() => this.#set({ source: "command" })}>a command line</label>
          <label><input type="radio" name="source" value="unit" ?disabled=${busy} .checked=${live(!command)}
            @change=${() => this.#set({ source: "unit" })}>a unit already here</label>
        </fieldset>
        ${command ? html`
          <label><span>Command line, its first word a full path (<code>command -v NAME</code> in your shell prints it)</span>
            <input type="text" name="command" autocomplete="off" spellcheck="false" ?disabled=${busy}
              .value=${live(form.command)} @input=${this.#text("command")}></label>
          <label class="check"><input type="checkbox" name="enable" ?disabled=${busy} .checked=${live(form.enable)}
            @change=${(event: Event) => this.#set({ enable: (event.target as HTMLInputElement).checked })}>Turn it on now</label>
          ${timer ? html`<fieldset><legend>Schedule</legend>
            <label><input type="radio" name="schedule" value="every" ?disabled=${busy} .checked=${live(form.schedule === "every")}
              @change=${() => this.#set({ schedule: "every" })}>every</label>
            <label><input type="radio" name="schedule" value="at" ?disabled=${busy} .checked=${live(form.schedule === "at")}
              @change=${() => this.#set({ schedule: "at" })}>at times</label>
          </fieldset>
          ${form.schedule === "every" ? html`<label><span>Every, such as 5m or 2h</span>
            <input type="text" name="every" autocomplete="off" ?disabled=${busy} .value=${live(form.every)} @input=${this.#text("every")}></label>`
          : html`<label><span>At, 24-hour times such as 09:00, 17:30</span>
            <input type="text" name="at" autocomplete="off" ?disabled=${busy} .value=${live(form.at)} @input=${this.#text("at")}></label>
            <fieldset><legend>Days (none for every day)</legend>${DAYS.map((day) => html`<label><input type="checkbox" name="days" value=${day}
              ?disabled=${busy} .checked=${live(form.days.includes(day))} @change=${(event: Event) => this.#day(day, (event.target as HTMLInputElement).checked)}>${day}</label>`)}
            </fieldset>`}` : nothing}
          <label><span>Working directory, a full path (optional)</span>
            <input type="text" name="cwd" autocomplete="off" spellcheck="false" ?disabled=${busy}
              .value=${live(form.cwd)} @input=${this.#text("cwd")}></label>`
        : html`
          <label><span>Unit, as the supervisor names it</span>
            <input type="text" name="unit" autocomplete="off" spellcheck="false" ?disabled=${busy}
              .value=${live(form.unit)} @input=${this.#text("unit")}></label>
          <fieldset><legend>Now</legend>
            <label><input type="radio" name="adopt" value="on" ?disabled=${busy} .checked=${live(form.adoptOn === "on")}
              @change=${() => this.#set({ adoptOn: "on" })}>on</label>
            <label><input type="radio" name="adopt" value="off" ?disabled=${busy} .checked=${live(form.adoptOn === "off")}
              @change=${() => this.#set({ adoptOn: "off" })}>off</label>
          </fieldset>`}
        <label><span>Health command (optional; exit 0 is healthy)</span>
          <input type="text" name="health" autocomplete="off" spellcheck="false" ?disabled=${busy}
            .value=${live(form.health)} @input=${this.#text("health")}></label>
        <label><span>Project</span>
          <select name="project" ?disabled=${busy} .value=${live(form.project)}
            @change=${(event: Event) => this.#set({ project: (event.target as HTMLSelectElement).value })}>
            <option value="" ?selected=${form.project === ""}>none</option>
            ${(projects?.projects ?? []).map((project) => html`<option value=${project.key}
              ?selected=${project.key === form.project}>${project.key}</option>`)}
          </select></label>
        ${reason ? html`<p class="dim" data-line="projects">Projects: ${reason}</p>` : nothing}
        <label class="check"><input type="checkbox" name="launches_session" ?disabled=${busy} .checked=${live(form.launchesSession)}
          @change=${(event: Event) => this.#set({ launchesSession: (event.target as HTMLInputElement).checked })}>Its runs launch an agent session (they spend usage)</label>
        <label><span>Doc, one line (optional)</span>
          <input type="text" name="doc" autocomplete="off" ?disabled=${busy} .value=${live(form.doc)} @input=${this.#text("doc")}></label>
        <div class="bar"><button type="submit" class="go" data-action="add" ?disabled=${busy}>Add</button></div>
        ${told !== null ? this.#saidPart(told) : nothing}
      </form>
    </section>`;
  }

  #set(change: Partial<AddForm>): void {
    this.#form = { ...this.#form, ...change };
    // The form's way is the address's, while the address names the form.
    if (change.source !== undefined && this.#panel !== null) {
      this.#panel = change.source === "unit" ? "adopt" : "add";
      this.#write();
    }
    this.requestUpdate();
  }

  #text(name: "name" | "command" | "unit" | "every" | "at" | "cwd" | "health" | "doc") {
    return (event: Event): void => this.#set({ [name]: (event.target as HTMLInputElement).value });
  }

  #day(day: string, on: boolean): void {
    const days = this.#form.days.filter((d) => d !== day);
    this.#set({ days: on ? [...days, day] : days });
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ServicesPage);

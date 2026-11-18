// <moxi-services-page route="...">: services' panel.
//
// - The list (`list-model.ts` gives every word), under no heading of its own: the shell's
//   top line names the page. First, `<n> services` with `Add a service` (desk) beside the
//   rows that rise (every fail, then failed last runs, then rows it can't tell), each with
//   its sentence on line two; when none rises, `Nothing is down.`, the dim counts and
//   `read <time>`. Then the groups: one per project key (with projects installed) and `No
//   project`, or `Yours`, then `Moxi`. Each row is a `<moxi-ui-row>`: the name, its dim
//   words and its fact, weighted by the row's cause.
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
import { api, confirm, gateway, gatewayActs, installed, subscribe } from "/m/ui/client.js";

import { Feed, parseProjects, parseRows, parseStatus, saidOf, statusPathOf } from "./feed.ts";
import type { Projects, Row, Rows, Status } from "./feed.ts";
import { ADD_A_SERVICE, listShape } from "./list-model.ts";
import type { ListShape, RowView } from "./list-model.ts";
import {
  addBody, addRefused, DAYS, emptyForm, listView, outcomeSaid, shownActs, statusLost, statusSaid,
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
  .grp .gk button, .item moxi-ui-row > button { padding: 0; margin: 0; border: 0; background: none; font: inherit;
                                                 text-align: left; cursor: pointer; }
  .grp .gk small button { color: var(--moxi-text); }
  .grp .gk button:hover:not(:disabled), .item moxi-ui-row > button:hover:not(:disabled) { background: none; }
  .item moxi-ui-row > button.do { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  .item moxi-ui-row > .fact { color: var(--moxi-text); font-variant-numeric: tabular-nums; }
  .one { display: flex; justify-content: space-between; align-items: baseline; gap: 4px 14px; padding: 7px 0;
         flex-wrap: wrap; }
  .one .read { margin-left: auto; color: var(--moxi-text-dim); font-variant-numeric: tabular-nums; }
  .counts { color: var(--moxi-text-dim); padding-bottom: 7px; }
  .item .said { margin: 0 0 7px; }
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

/** What the page last said, and where: a row's id, `ADD`, or null for the page. */
interface Told {
  where: string | null;
  said: Said;
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

  #shown = (): void => {
    if (document.visibilityState === "visible") this.#readAll();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#phone = gateway();
    const changed = (): void => this.requestUpdate();
    const rows = new Feed<Rows>(() => api("services", "api/rows"), parseRows, changed);
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
    for (const feed of [this.#rows, this.#projects, this.#job?.feed]) feed?.close();
    this.#rows = this.#projects = null;
    this.#job = null;
    this.#stop?.();
    this.#stop = null;
    clearTimeout(this.#jobTimer);
    document.removeEventListener("visibilitychange", this.#shown);
  }

  #readAll(): void {
    void this.#rows?.refresh();
    void this.#projects?.refresh();
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

  #jobRead(job: Job): void {
    if (job !== this.#job || job.ended) return;
    const feed = job.feed;
    job.fails = feed.failure !== null ? job.fails + 1 : 0;
    const value = feed.failure === null ? feed.value : null;
    if (value !== null && value.state !== "running") {
      job.ended = true;
      this.#told = { where: job.where, said: statusSaid(job.label, value) };
      if (value.state === "done" && job.where === ADD) this.#form = emptyForm();
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
    const view = listView(rows);
    if (view.state === "gone") return html`<p class="problem">${view.line}</p>`;
    const told = this.#told;
    const value = rows.value;
    const shape = view.groups.length && value !== null
      ? listShape(value.rows, value.at, installed("projects"), { now: Date.now() / 1000 }) : null;
    const ids = new Set((value?.rows ?? []).map((row) => row.id));
    const loose = told !== null && told.where !== ADD && (told.where === null || !ids.has(told.where)) ? told.said : null;
    return html`${this.#phone && !gatewayActs()
        ? html`<p class="note" data-line="desk">This phone runs no acts here; each one's sheet says to do it at the desk.</p>` : nothing}
      <section data-part="list" data-state=${view.state}>
        ${this.#busy ? html`<p class="state dim" data-line="busy">An act is running.</p>` : nothing}
        ${loose !== null ? this.#saidPart(loose) : nothing}
        ${view.state === "loading" ? html`<p class="state dim" data-line="reading">Reading the rows…</p>` : nothing}
        ${view.line !== null ? html`<p class=${view.state === "empty" ? "state dim" : "state problem"} data-line=${view.state}>${view.line}</p>` : nothing}
        ${shape !== null ? this.#list(shape) : nothing}
        ${view.problems.map((line) => html`<p class="foot" data-line="module-problem">${line}</p>`)}
      </section>
      ${this.#phone ? nothing : this.#addPart()}`;
  }

  #saidPart(said: Said) {
    return html`<div class="said ${said.tone}" data-line="said" data-tone=${said.tone} role="status">
      ${said.lines.map((line) => html`<p>${line}</p>`)}</div>`;
  }

  /** The first group, then the groups. */
  #list(shape: ListShape) {
    return html`<div class="grps">
      <section class="grp" data-part="first"><div class="gk"><span data-line="count">${shape.label}</span>${this.#phone
        ? nothing : html`<small><button type="button" data-action="add" @click=${() => this.#toAdd()}>${ADD_A_SERVICE}</button></small>`}</div>
        <div class="gl">${repeat(shape.risen, (item) => item.row.id, (item) => this.#row(item))}
          ${shape.sentence !== null || shape.read !== null ? html`<div class="one">
            ${shape.sentence !== null ? html`<span data-line="sentence">${shape.sentence}</span>` : nothing}
            ${shape.read !== null ? html`<span class="read" data-line="read">${shape.read}</span>` : nothing}</div>` : nothing}
          ${shape.counts !== null ? html`<div class="counts" data-line="counts">${shape.counts}</div>` : nothing}
        </div></section>
      ${repeat(shape.groups, ({ group }) => `${group.moxi ? "moxi" : "his"}:${group.project ?? group.label}`, ({ group, rows }) => html`
        <section class="grp" data-part="group" data-group=${group.moxi ? "moxi" : group.project !== null ? "project" : "his"}
          data-project=${group.project ?? ""}><div class="gk" data-line="group">${group.label}</div>
          <div class="gl">${repeat(rows, (item) => item.row.id, (item) => this.#row(item))}</div></section>`)}
    </div>`;
  }

  /** One row: line one's name, dim words and fact; a risen row's sentence on line two; the
   * row's acts at line two's right; what its last act came to under it. A fact in the text
   * colour is drawn by the page at line one's right, since the row draws its own fact
   * either dim or heavy. */
  #row(item: RowView) {
    const row = item.row;
    const busy = this.#busy;
    const told = this.#told?.where === row.id ? this.#told.said : null;
    const own = item.weight === "text";
    return html`<div class="item" data-id=${row.id} data-cause=${row.verdict.cause ?? ""} data-verdict=${row.verdict.state}
        data-weight=${item.weight} data-kind=${row.kind}>
      <moxi-ui-row name=${item.name} where=${item.where ?? nothing} fact=${own ? nothing : item.fact}
        ?fact-heavy=${item.weight === "heavy"} lead=${item.line ?? nothing}>
        ${own ? html`<span slot="acts-one" class="fact" data-line="fact">${item.fact}</span>` : nothing}
        ${shownActs(row, this.#phone).map((act) => html`
          <button type="button" slot="acts" class="do" data-action=${act.name} ?disabled=${busy}
            aria-label=${`${act.label.replace(/…$/, "")} ${row.name}`}
            @click=${() => void this.#act(row, act)}>${act.label}</button>`)}
      </moxi-ui-row>
      ${told !== null ? this.#saidPart(told) : nothing}
    </div>`;
  }

  /** `Add a service`: the add form, its name field focused. */
  #toAdd(): void {
    const name = this.renderRoot.querySelector<HTMLInputElement>('section[data-part="add"] input[name="name"]');
    name?.scrollIntoView({ block: "nearest" });
    name?.focus();
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

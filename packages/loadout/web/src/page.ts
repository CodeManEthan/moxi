// <moxi-loadout-page route="...">: loadout's panel.
//
// - The drift block, placed as any host places it.
// - The job: the act the page last started, read from its status path while it runs
//   (running, then done with its outcome, or interrupted).
// - Install: every section, or one, as `moxi loadout install` does.
// - The items by section, the folder's own first and then each module's skills, each with
//   its harnesses: the state and why, a switch that enables or disables it there (it shows
//   the manifest as last read, so it flips once the job has ended), and
//   Replace for a blocked or modified placement. Then the rows no listed item has, and
//   the add form, whose section picker offers projects by key.
// - The global instruction file: its mode, the buttons that set one, Turn off, and what
//   the next install makes of each global file, with its diff.
//
// Every act runs as a page job: the page sends it, follows the job's status path, and
// keeps every act's control off while one runs. Replace and Turn off are confirmed acts:
// the page's button opens the shell's confirm sheet, which mints a new key at each
// confirmation and sends the act once. When a job ends the page reads its routes again
// and sends `CHANGED` on the document, so the drift block reads again too. The page reads
// its routes when it opens, when its tab is shown again, and on "Read again".

import { css, html, LitElement, nothing } from "lit";
import { live } from "lit/directives/live.js";
import { api, confirm, place } from "/m/ui/client.js";

import {
  CHANGED, Feed, parseGlobal, parseJob, parseListing, parseProjects, parseStatus,
} from "./feed.ts";
import type { Global, Job, Listing, Projects, Row, Status } from "./feed.ts";
import {
  addChoices, canReplace, cellLine, choiceScope, globalOffSheet, globalView, installAct,
  installChoices, itemAct, itemsView, jobPathOf, jobView, KINDS, readState, replaceAct,
  startedOf, stateTone,
} from "./views.ts";
import type { Act, Cell, ItemView, SectionView } from "./views.ts";

const TAG = "moxi-loadout-page";

/** How often a running job is read. */
const JOB_POLL_MS = 1000;
/** After this many failed reads in a row the page stops reading a job. */
const JOB_TRIES = 5;

const STYLES = css`
  :host { display: block; min-width: 0; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; }
  h1 { margin: 0 0 var(--moxi-gap); font-size: 1.4em; }
  h2 { margin: 0; font-size: 1.1em; }
  h3 { margin: 0; font-size: 1em; }
  h4 { margin: 0; font-size: var(--moxi-font-size-sm); color: var(--moxi-text-dim); }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  pre { margin: 0; padding: 6px 8px; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken);
        font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); white-space: pre-wrap; overflow-wrap: anywhere; }
  .lede { margin-bottom: var(--moxi-gap); padding-left: 8px; border-left: 3px solid var(--moxi-accent); color: var(--moxi-text-dim); }
  .drift { min-width: 0; margin-bottom: var(--moxi-gap); }
  .drift:empty { display: none; }
  .dim { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .ok { color: var(--moxi-ok); }
  .warn { color: var(--moxi-warn); }
  .error { color: var(--moxi-error); }
  .muted { color: var(--moxi-text-dim); }
  .problem { padding: 8px 12px; border-radius: var(--moxi-radius); background: var(--moxi-error-soft); color: var(--moxi-error); }
  .note { padding-left: 8px; border-left: 3px solid var(--moxi-warn); font-size: var(--moxi-font-size-sm); }
  .bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); min-width: 0; }
  section { display: grid; gap: var(--moxi-gap); min-width: 0; margin-top: calc(var(--moxi-gap) * 2); padding: 12px;
            border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius-lg); background: var(--moxi-bg-raised); }
  ul { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; min-width: 0; }
  .part { display: grid; gap: 6px; min-width: 0; padding-top: var(--moxi-gap); border-top: 1px solid var(--moxi-border); }
  .item { display: grid; gap: 4px; min-width: 0; padding: 4px 0 4px 8px; border-left: 3px solid var(--moxi-border); }
  .item.warn { border-left-color: var(--moxi-warn); }
  .first { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--moxi-gap); min-width: 0; }
  .tag { padding: 0 6px; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken);
         font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .cells { display: grid; gap: 4px; min-width: 0; }
  .cell { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--moxi-gap); min-width: 0; }
  .switch { min-width: 6em; text-align: left; }
  .switch::before { content: ""; display: inline-block; width: 0.8em; height: 0.8em; margin-right: 6px;
                    border: 1px solid var(--moxi-border-strong); border-radius: 50%; vertical-align: -0.05em; }
  .switch.on::before { background: var(--moxi-accent); border-color: var(--moxi-accent); }
  .cell .why { flex: 1 1 12em; min-width: 0; overflow-wrap: anywhere; font-size: var(--moxi-font-size-sm); }
  button, select, input { font: inherit; color: var(--moxi-text); max-width: 100%; }
  button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg-raised); cursor: pointer; }
  button.go { background: var(--moxi-accent); border-color: var(--moxi-accent); color: var(--moxi-bg); }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button.go:hover:not(:disabled) { background: var(--moxi-accent); }
  button:disabled, select:disabled, input:disabled { cursor: not-allowed; opacity: 0.6; }
  select, input[type=text] { min-width: 0; padding: 2px 6px; border: 1px solid var(--moxi-border-strong);
                             border-radius: var(--moxi-radius); background: var(--moxi-bg); }
  input[type=text] { flex: 1 1 10em; }
  form { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); min-width: 0; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  details { min-width: 0; }
  details > summary { cursor: pointer; color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  details[open] > summary { margin-bottom: 4px; }
`;

/** The page's one line about an act that didn't start, or a confirmed act's outcome. */
interface Said {
  text: string;
  tone: "warn" | "error";
}

export class LoadoutPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";
  #placed = false;
  #missing = false;

  #status: Feed<Status> | null = null;
  #listing: Feed<Listing> | null = null;
  #global: Feed<Global> | null = null;
  #projects: Feed<Projects> | null = null;

  /** The job the page follows, what started it, and whether it has ended. */
  #job: Feed<Job> | null = null;
  #jobLabel = "";
  #jobEnded = true;
  #jobLost = false;
  #jobFails = 0;
  #jobTimer: ReturnType<typeof setTimeout> | undefined;
  /** An act is being sent, or its confirm sheet is open. */
  #sending = false;
  #said: Said | null = null;

  #addKind: string = "skill";
  #addName = "";
  #addWhere = "user";
  #installWhere = "";

  #shown = (): void => {
    if (document.visibilityState === "visible") this.#readAll();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const changed = (): void => this.requestUpdate();
    this.#status = new Feed<Status>(() => api("loadout", "api/status"), parseStatus, changed);
    this.#listing = new Feed<Listing>(() => api("loadout", "api/list"), parseListing, changed);
    this.#global = new Feed<Global>(() => api("loadout", "api/global"), parseGlobal, changed);
    this.#projects = new Feed<Projects>(() => api("loadout", "api/projects"), parseProjects, changed);
    this.#readAll();
    void this.#projects.refresh();
    document.addEventListener("visibilitychange", this.#shown);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    for (const feed of [this.#status, this.#listing, this.#global, this.#projects, this.#job]) feed?.close();
    this.#status = this.#listing = this.#global = this.#projects = null;
    this.#job = null;
    clearTimeout(this.#jobTimer);
    document.removeEventListener("visibilitychange", this.#shown);
  }

  #readAll(): void {
    void this.#status?.refresh();
    void this.#listing?.refresh();
    void this.#global?.refresh();
  }

  /** Whether an act is on its way or its job runs: every act's control is off then. */
  get #busy(): boolean {
    return this.#sending || !this.#jobEnded;
  }

  // -- acts ------------------------------------------------------------------------------

  async #act(act: Act, label: string): Promise<void> {
    if (this.#busy) return;
    this.#sending = true;
    this.#said = null;
    this.requestUpdate();
    try {
      const answer = await api("loadout", act.path, act.body);
      const started = startedOf(answer.status, answer.data);
      if (started.kind === "job") this.#follow(started.path, label);
      else this.#said = { text: started.text, tone: "error" };
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  async #confirmed(act: Act & { sheet: string[] }, label: string): Promise<void> {
    if (this.#busy) return;
    this.#sending = true;
    this.#said = null;
    this.requestUpdate();
    try {
      const outcome = await confirm("loadout", act.path, act.body, act.sheet);
      if (outcome.outcome === "accepted") {
        const path = jobPathOf(outcome.status);
        if (path !== null) this.#follow(path, label);
        else this.#said = { text: "loadout's page server started a job but gave no path to follow", tone: "error" };
      } else if (outcome.outcome === "refused") {
        this.#said = { text: outcome.text, tone: "error" };
      } else if (outcome.outcome === "unknown") {
        this.#said = { text: outcome.text, tone: "warn" };
        this.#ended();
      }
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  /** Follows the job at status path `path` until it ends. */
  #follow(path: string, label: string): void {
    this.#job?.close();
    clearTimeout(this.#jobTimer);
    this.#jobLabel = label;
    this.#jobEnded = false;
    this.#jobLost = false;
    this.#jobFails = 0;
    const feed: Feed<Job> = new Feed<Job>(() => api("loadout", path), parseJob, () => this.#jobRead(feed));
    this.#job = feed;
    void feed.refresh();
  }

  #jobRead(feed: Feed<Job>): void {
    if (feed !== this.#job || this.#jobEnded) return;
    this.#jobFails = feed.failure !== null ? this.#jobFails + 1 : 0;
    const state = feed.value?.state ?? "running";
    const lost = feed.gone || this.#jobFails >= JOB_TRIES;
    if (state === "running" && !lost) {
      this.#jobTimer = setTimeout(() => { void feed.refresh(); }, JOB_POLL_MS);
    } else {
      this.#jobLost = state === "running";
      this.#jobEnded = true;
      this.#ended();
    }
    this.requestUpdate();
  }

  /** An act has ended: read everything again, and tell every loadout component. */
  #ended(): void {
    this.#readAll();
    document.dispatchEvent(new CustomEvent(CHANGED));
  }

  // -- rendering -------------------------------------------------------------------------

  render() {
    const status = this.#status;
    const listing = this.#listing;
    if (status === null || listing === null) return nothing;
    if (status.gone || listing.gone) return html`<p class="problem">loadout is no longer installed.</p>`;
    const reading = status.reading || listing.reading || (this.#global?.reading ?? false);
    return html`
      <h1>Loadout</h1>
      <p class="lede">Your own skills, agents and settings, placed for each harness: Claude Code, Codex and OpenCode. Each change here runs the same <code>moxi loadout</code> command you would run in a terminal.</p>
      <div class="drift" data-part="drift"></div>
      ${this.#missing ? html`<p class="dim" data-line="no-drift">The count can't be shown here. Run <code>moxi loadout status</code> in a terminal.</p>` : nothing}
      <div class="bar">
        <button data-action="read" ?disabled=${reading} @click=${() => this.#readAll()}>Read again</button>
        ${reading && !status.loading ? html`<span class="dim">Reading…</span>` : nothing}
      </div>
      ${this.#jobPart()}
      ${this.#installPart()}
      ${this.#itemsPart()}
      ${this.#globalPart()}`;
  }

  #jobPart() {
    const said = this.#said;
    const feed = this.#job;
    if (feed === null) {
      return said ? html`<section data-part="job" data-state="refused"><p class=${said.tone} data-line="said">${said.text}</p></section>` : nothing;
    }
    const view = jobView(feed, this.#jobLost);
    return html`<section data-part="job" data-state=${view.state} data-exit=${feed.value?.exit ?? nothing}>
      <h2>Last change</h2>
      <p class="dim">${this.#jobLabel}: <code data-line="command">${feed.value ? view.command : "starting"}</code></p>
      <p class=${view.tone} data-line="job">${view.title}</p>
      ${view.lines.length ? html`<ul data-part="job-lines">${view.lines.map((line) => html`<li class="dim">${line}</li>`)}</ul>` : nothing}
      ${view.rows.length ? html`<ul data-part="job-rows">${view.rows.map((row) => html`<li>${this.#rowLine(row)}</li>`)}</ul>` : nothing}
      ${said ? html`<p class=${said.tone} data-line="said">${said.text}</p>` : nothing}
    </section>`;
  }

  #installPart() {
    const choices = installChoices(this.#listing?.value ?? null);
    const busy = this.#busy;
    return html`<section data-part="install">
      <h2>Install</h2>
      <p class="dim">Places every item of the sections chosen for each harness, as <code>moxi loadout install</code> does.</p>
      <form @submit=${(event: Event) => { event.preventDefault(); void this.#act(installAct(this.#installWhere), "Install"); }}>
        <select aria-label="Sections to install" .value=${live(this.#installWhere)} ?disabled=${busy}
                @change=${(event: Event) => { this.#installWhere = (event.target as HTMLSelectElement).value; }}>
          ${choices.map((choice) => html`<option value=${choice.value} ?selected=${choice.value === this.#installWhere}>${choice.label}</option>`)}
        </select>
        <button class="go" type="submit" data-action="install" ?disabled=${busy}>Install</button>
        ${!this.#jobEnded ? html`<span class="dim">A change is running.</span>` : nothing}
      </form>
    </section>`;
  }

  #itemsPart() {
    const status = this.#status!;
    const listing = this.#listing!;
    const listRead = readState(listing);
    const statusRead = readState(status);
    let head: unknown = nothing;
    if (listRead.state === "loading") head = html`<p class="dim">Reading the manifest…</p>`;
    else if (listRead.state === "problem") head = html`<p class="problem" data-line="problem">${listRead.detail}</p>`;
    else if (listRead.state === "failed") head = html`<p class="problem" data-line="failed">The items can't be read: ${listRead.detail}.</p>`;
    else if (statusRead.state === "problem") head = html`<p class="problem" data-line="problem">${statusRead.detail}</p>`;
    else if (statusRead.state === "failed") head = html`<p class="problem" data-line="failed">The states can't be read: ${statusRead.detail}.</p>`;
    const view = itemsView(listing.value, status.value);
    const notes = status.value?.notes ?? [];
    return html`<section data-part="items" data-state=${listRead.state === "up" ? statusRead.state : listRead.state}>
      <h2>Items</h2>
      ${head}
      ${notes.length ? html`<ul data-part="notes">${notes.map((note) => html`<li class="note">${note}</li>`)}</ul>` : nothing}
      ${listing.value && listing.value.items.length === 0 && listRead.state === "up"
        ? html`<p class="dim" data-line="empty">The manifest lists no item yet. Add one below.</p>` : nothing}
      ${view.sections.map((section) => this.#section(section))}
      ${view.others.length ? html`<div class="part" data-part="others"><h3>Other rows</h3>
        <p class="dim">Placements no listed item has, and the global file.</p>
        <ul>${view.others.map((row) => html`<li class="item ${stateTone(row.state) === "warn" ? "warn" : ""}">${this.#rowLine(row)}${this.#replaceButton(row)}</li>`)}</ul></div>` : nothing}
      ${this.#addPart()}
    </section>`;
  }

  #section(section: SectionView) {
    if (section.groups.length === 0) {
      return html`<div class="part" data-section=${section.key}><h3>${section.label}</h3><p class="dim">It lists no item.</p></div>`;
    }
    return html`<div class="part" data-section=${section.key}>
      <h3>${section.label}</h3>
      ${section.groups.map((group) => html`
        ${group.module !== null ? html`<h4 data-module=${group.module}>Adopted from module ${group.module}</h4>` : nothing}
        <ul>${group.items.map((item) => this.#item(item))}</ul>`)}
    </div>`;
  }

  #item(item: ItemView) {
    const busy = this.#busy;
    const bad = item.cells.some((cell) => stateTone(cell.state) === "warn");
    return html`<li class="item ${bad ? "warn" : ""}" data-item=${`${item.kind}:${item.name}`}>
      <div class="first"><span class="tag">${item.kind}</span><strong>${item.name}</strong>
        <button data-action="remove" ?disabled=${busy}
                @click=${() => void this.#act(item.remove, `Remove ${item.kind} ${item.name}`)}>Remove</button></div>
      <p class="dim">From <code>${item.source}</code></p>
      ${item.allOff ? html`<p class="dim" data-line="all-off">Off for every harness here.
        <button data-action="enable-all" ?disabled=${busy}
                @click=${() => void this.#act(item.enableAll!, `Turn on ${item.kind} ${item.name}`)}>Turn on</button></p>` : nothing}
      <div class="cells">${item.cells.map((cell) => this.#cell(item, cell))}</div>
    </li>`;
  }

  #cell(item: ItemView, cell: Cell) {
    const busy = this.#busy;
    const tone = stateTone(cell.state);
    const toggle = cell.toggle;
    return html`<div class="cell" data-harness=${cell.harness} data-state=${cell.state ?? "none"}>
      <button class="switch ${cell.on ? "on" : ""}" role="switch" aria-checked=${cell.on ? "true" : "false"}
        title=${`${cell.on ? "Turn off" : "Turn on"} ${item.kind} ${item.name} for ${cell.harness}`}
        ?disabled=${busy || toggle === null}
        @click=${() => { if (toggle) void this.#act(toggle, `${cell.on ? "Turn off" : "Turn on"} ${item.kind} ${item.name} for ${cell.harness}`); }}>${cell.harness}</button>
      <span class="why ${tone}">${cellLine(cell)}</span>
      ${cell.row ? this.#replaceButton(cell.row) : nothing}
    </div>`;
  }

  #rowLine(row: Row) {
    const what = row.kind === "global" ? "global file" : [row.kind, row.name].filter(Boolean).join(" ");
    const where = [row.harness ? `for ${row.harness}` : "", row.scope && row.scope !== "user" ? `in ${row.scope}` : ""]
      .filter(Boolean).join(" ");
    return html`<div class="first"><span class=${stateTone(row.state)} data-row-state=${row.state}>${row.state}</span>
      <span>${what} ${where}${row.reason ? `: ${row.reason}` : ""}</span></div>
      ${row.target ? html`<code class="dim">${row.target}</code>` : nothing}`;
  }

  #replaceButton(row: Row) {
    if (!canReplace(row)) return nothing;
    const act = replaceAct(row);
    return html`<button data-action="replace" ?disabled=${this.#busy}
      @click=${() => void this.#confirmed(act, `Replace ${row.target ?? act.sheet[0]}`)}>Replace…</button>`;
  }

  #addPart() {
    const busy = this.#busy;
    const projects = this.#projects?.value ?? null;
    const choices = addChoices(this.#listing?.value ?? null, projects);
    if (!choices.some((choice) => choice.value === this.#addWhere)) this.#addWhere = "user";
    const reason = projects?.reason ?? (this.#projects?.failure ? `they can't be listed: ${this.#projects.failure.why}` : null);
    return html`<div class="part" data-part="add">
      <h3>Add an item</h3>
      <form @submit=${(event: Event) => { event.preventDefault(); void this.#add(); }}>
        <select aria-label="Kind" .value=${live(this.#addKind)} ?disabled=${busy}
                @change=${(event: Event) => { this.#addKind = (event.target as HTMLSelectElement).value; }}>
          ${KINDS.map((kind) => html`<option value=${kind} ?selected=${kind === this.#addKind}>${kind}</option>`)}
        </select>
        <input type="text" aria-label="Name" placeholder="name" .value=${live(this.#addName)} ?disabled=${busy}
               @input=${(event: Event) => { this.#addName = (event.target as HTMLInputElement).value; this.requestUpdate(); }}>
        <select aria-label="Section" .value=${live(this.#addWhere)} ?disabled=${busy}
                @change=${(event: Event) => { this.#addWhere = (event.target as HTMLSelectElement).value; }}>
          ${choices.map((choice) => html`<option value=${choice.value} ?selected=${choice.value === this.#addWhere}>${choice.label}</option>`)}
        </select>
        <button type="submit" data-action="add" ?disabled=${busy || !this.#addName.trim()}>Add</button>
      </form>
      ${reason ? html`<p class="dim" data-line="projects">Projects by key: ${reason}</p>` : nothing}
    </div>`;
  }

  async #add(): Promise<void> {
    const name = this.#addName.trim();
    if (!name) return;
    const act = itemAct("add", this.#addKind, name, choiceScope(this.#addWhere));
    await this.#act(act, `Add ${this.#addKind} ${name}`);
    if (this.#job !== null && !this.#jobEnded) this.#addName = "";
  }

  #globalPart() {
    const feed = this.#global;
    if (feed === null) return nothing;
    const read = readState(feed);
    const busy = this.#busy;
    let body: unknown = nothing;
    if (read.state === "loading") body = html`<p class="dim">Reading the global file…</p>`;
    else if (read.state === "problem") body = html`<p class="problem" data-line="problem">${read.detail}</p>`;
    else if (read.state === "failed" && feed.value === null) body = html`<p class="problem" data-line="failed">The global file can't be read: ${read.detail}.</p>`;
    const value = feed.value;
    const view = value !== null && value.problem === null ? globalView(value) : null;
    return html`<section data-part="global" data-mode=${view?.mode ?? nothing} data-chosen=${view ? String(view.chosen) : nothing}>
      <h2>Global instruction file</h2>
      ${body}
      ${view ? html`
        <p data-line="mode">${view.line}</p>
        <div class="bar">
          ${view.modes.map((mode) => html`<button data-action="mode" data-mode=${mode.mode} ?disabled=${busy}
            @click=${() => void this.#act(mode.act, `Set the global mode to ${mode.mode}`)}>${mode.label}</button>`)}
          ${view.canTurnOff ? html`<button data-action="global-off" ?disabled=${busy}
            @click=${() => void this.#confirmed({ path: "api/global-off", body: {}, sheet: globalOffSheet(value) }, "Turn the global file off")}>Turn off…</button>` : nothing}
        </div>
        ${view.files.length ? html`<ul data-part="files">${view.files.map((file) => html`<li class="item" data-file=${file.file}>
          <code>${file.file}</code>
          <p class="dim">${file.changes ? "The next install changes it." : "The next install leaves it as it is."}${file.state ? ` State: ${file.state}.` : ""}</p>
          ${file.diff ? html`<details><summary>${file.placed ? "Why" : "Diff"}</summary><pre>${file.diff}</pre></details>` : nothing}
        </li>`)}</ul>` : html`<p class="dim">No global file is touched in this mode.</p>`}` : nothing}
    </section>`;
  }

  updated(): void {
    const host = this.renderRoot.querySelector(".drift");
    if (host === null || this.#placed) return;
    this.#placed = true;
    const missing = (): void => {
      this.#missing = true;
      this.requestUpdate();
    };
    place(host, "loadout", "drift").then((block) => { if (block === null) missing(); }, missing);
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, LoadoutPage);

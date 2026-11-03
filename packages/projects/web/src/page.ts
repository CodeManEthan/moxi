// <moxi-projects-page route="...">: projects' panel.
//
// - The store file and the git the page server acts with, as `/api/where` gives them. The
//   server takes the browser shell's environment, not the terminal's, so these can differ
//   from what `moxi projects` uses in a terminal; the page shows them and compares nothing.
// - The list, by key, with a kind filter (read through `/api/projects?kind=`) and, beside
//   it, every contribution or kind that can't be used, which blocks an add until it is
//   fixed. Each project opens to its fields, an edit form (root, kind, and the notes root
//   while notes is installed), its lead and associated agents (set or clear the lead, add
//   or remove an agent), and Remove. A save sends only what the user changed from the
//   project as the form was opened. When a read again finds the open project changed, a
//   form not typed in starts again from it, and one typed in keeps the user's values and
//   says the project has changed.
// - Remove is a confirmed act: the page's button opens the shell's confirm sheet, which
//   names the key and root and mints a new key at each confirmation. The page then reads
//   the status link once: removed, or refused with each sentence and the fix.
// - The add form is projects' own component, placed as any host places it. After an add
//   that was made or may have been it sends `CHANGED`, and the page reads its list again;
//   after a lost answer to a create, the form says the directory may exist.
//
// Every act sends what the same `moxi projects` verb takes, and every control that acts is
// off while one is on its way. After an act that was done or may have been, the page
// sends `CHANGED` on the document, so it and every projects component read again. The
// page reads its routes when it opens, when its tab is shown again, and on "Read again".

import { css, html, LitElement, nothing } from "lit";
import { live } from "lit/directives/live.js";
import { repeat } from "lit/directives/repeat.js";
import { api, confirm, installed, place } from "/m/ui/client.js";

import { CHANGED, Feed, parseKinds, parseOne, parseProjects, parseRemoveStatus, parseWhere, parseWritten } from "./feed.ts";
import type { Kinds, Project, Projects, RemoveStatus, Where } from "./feed.ts";
import { BASE } from "./styles.ts";
import {
  actedOf, actSaid, agentBody, brokenLines, clearLeadBody, editBody, editing, filterChoices, kindChoices,
  leadBody, listView, NO_GIT, pickerPath, projectFields, removedSaid, removeSheet, removeStatusPath,
  reread, STALE_LINE, WHERE_LINE, whereView,
} from "./views.ts";
import type { EditForm, Editing, Said } from "./views.ts";

const TAG = "moxi-projects-page";

const STYLES = [BASE, css`
  h1 { margin: 0 0 var(--moxi-gap); font-size: 1.4em; }
  h2 { margin: 0; font-size: 1.1em; }
  h3 { margin: 0; font-size: 1em; }
  .lede { margin-bottom: var(--moxi-gap); padding-left: 8px; border-left: 3px solid var(--moxi-accent);
          color: var(--moxi-text-dim); }
  section { display: grid; gap: var(--moxi-gap); min-width: 0; margin-top: calc(var(--moxi-gap) * 2); padding: 12px;
            border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius-lg); background: var(--moxi-bg-raised); }
  section[data-part="add"] { padding: 0; border: 0; background: none; }
  section[data-part="add"] > div { min-width: 0; }
  .bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); min-width: 0; }
  .bar label { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; min-width: 0; }
  .broken { padding-left: 8px; border-left: 3px solid var(--moxi-error); font-size: var(--moxi-font-size-sm); }
  ul { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; min-width: 0; }
  .project { display: grid; gap: 6px; min-width: 0; padding: 4px 0 4px 8px; border-left: 3px solid var(--moxi-border); }
  .project[data-open="true"] { border-left-color: var(--moxi-accent); }
  .first { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--moxi-gap); min-width: 0; }
  .first .key { font-weight: 600; overflow-wrap: anywhere; text-align: left; }
  .first code { flex: 1 1 12em; min-width: 0; color: var(--moxi-text-dim); }
  .tag { padding: 0 6px; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken);
         font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .detail { display: grid; gap: var(--moxi-gap); min-width: 0; }
  dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 2px var(--moxi-gap); margin: 0; }
  dt { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
  form { display: grid; gap: 6px; min-width: 0; }
  form.line { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); }
  form.line input[type=text] { flex: 1 1 10em; }
  form label { display: grid; gap: 2px; min-width: 0; }
  form label > span { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  form:not(.line) input[type=text], form:not(.line) select { width: 100%; }
  .part { display: grid; gap: 6px; min-width: 0; padding-top: var(--moxi-gap); border-top: 1px solid var(--moxi-border); }
  .agent { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--moxi-gap); min-width: 0; }
  .agent > span { overflow-wrap: anywhere; }
  .said { display: grid; gap: 2px; padding-left: 8px; border-left: 3px solid var(--moxi-border-strong); }
  .said.ok { border-left-color: var(--moxi-ok); }
  .said.error { border-left-color: var(--moxi-error); color: var(--moxi-error); }
  .said.warn { border-left-color: var(--moxi-warn); }
`];

/** What the page last said, and the project it is about (null for the page itself). */
interface Told {
  key: string | null;
  said: Said;
}

export class ProjectsPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";

  #list: Feed<Projects> | null = null;
  #kinds: Feed<Kinds> | null = null;
  #where: Feed<Where> | null = null;
  /** The kind filter, "" for every kind. */
  #kind = "";
  /** The key of the project that is open, as stored. */
  #open: string | null = null;
  #edit: Editing | null = null;
  #lead = "";
  #agent = "";
  /** An act is being sent, or the confirm sheet is open. */
  #sending = false;
  #told: Told | null = null;
  #placed = false;
  #missing = false;

  #again = (): void => {
    void this.#list?.refresh();
  };
  #shown = (): void => {
    if (document.visibilityState === "visible") this.#readAll();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const changed = (): void => this.requestUpdate();
    this.#list = new Feed<Projects>(() => api("projects", pickerPath(this.#kind || null)), parseProjects,
                                    () => this.#listed());
    this.#kinds = new Feed<Kinds>(() => api("projects", "api/kinds"), parseKinds, changed);
    this.#where = new Feed<Where>(() => api("projects", "api/where"), parseWhere, changed);
    this.#readAll();
    document.addEventListener("visibilitychange", this.#shown);
    document.addEventListener(CHANGED, this.#again);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    for (const feed of [this.#list, this.#kinds, this.#where]) feed?.close();
    this.#list = this.#kinds = this.#where = null;
    document.removeEventListener("visibilitychange", this.#shown);
    document.removeEventListener(CHANGED, this.#again);
  }

  #readAll(): void {
    void this.#list?.refresh();
    void this.#kinds?.refresh();
    void this.#where?.refresh();
  }

  /** The list was read: an open project that is no longer listed closes, and the open
   * project's edit form takes in what was read. */
  #listed(): void {
    const projects = this.#list?.value?.projects ?? [];
    if (this.#open !== null && this.#list?.failure === null) {
      const open = projects.find((p) => p.key === this.#open) ?? null;
      if (open === null) {
        this.#open = null;
        this.#edit = null;
      } else if (this.#edit !== null) {
        this.#edit = reread(this.#edit, open);
      }
    }
    this.requestUpdate();
  }

  #find(key: string | null): Project | null {
    if (key === null) return null;
    return this.#list?.value?.projects.find((project) => project.key === key) ?? null;
  }

  // -- acts --------------------------------------------------------------------------------

  /** Something projects keeps may have changed: tell every projects component, this page
   * among them, to read again. */
  #changed(): void {
    document.dispatchEvent(new Event(CHANGED));
  }

  async #act(key: string, path: string, body: object, parse: (data: unknown) => { project: Project; notes?: string[] } | null,
             line: string, done: (project: Project) => void = () => {}): Promise<void> {
    if (this.#sending) return;
    this.#sending = true;
    this.#told = null;
    this.requestUpdate();
    try {
      const answer = await api("projects", path, body);
      const acted = actedOf(answer.status, answer.data, parse);
      this.#told = { key, said: actSaid(acted, line) };
      if (acted.outcome === "done") {
        if (this.#open === key && this.#edit !== null) this.#edit = reread(this.#edit, acted.value.project);
        done(acted.value.project);
      }
      if (acted.outcome !== "refused") this.#changed();
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  #save(project: Project): void {
    const edit = this.#edit;
    const body = edit !== null ? editBody(edit.base, edit.form, { notes: installed("notes") }) : null;
    if (body === null) return;
    void this.#act(project.key, "api/set", body, parseWritten, `Saved ${project.key}.`,
                   (saved) => { if (this.#open === project.key) this.#edit = editing(saved); });
  }

  #setLead(project: Project): void {
    const body = leadBody(project.key, this.#lead);
    if (body.lead === "") return;
    void this.#act(project.key, "api/set", body, parseWritten, `${body.lead} leads ${project.key} now.`,
                   () => { this.#lead = ""; });
  }

  #clearLead(project: Project): void {
    void this.#act(project.key, "api/set", clearLeadBody(project.key), parseWritten,
                   `${project.key} has no lead now.`);
  }

  #addAgent(project: Project): void {
    const body = agentBody(project.key, this.#agent);
    if (body.name === "") return;
    void this.#act(project.key, "api/agents-add", body, parseOne, `Added ${body.name} to ${project.key}.`,
                   () => { this.#agent = ""; });
  }

  #removeAgent(project: Project, name: string): void {
    void this.#act(project.key, "api/agents-remove", agentBody(project.key, name), parseOne,
                   `Took ${name} out of ${project.key}.`);
  }

  async #remove(project: Project): Promise<void> {
    if (this.#sending) return;
    this.#sending = true;
    this.#told = null;
    this.requestUpdate();
    try {
      const outcome = await confirm("projects", "api/remove", { key: project.key }, removeSheet(project));
      if (outcome.outcome === "cancelled") return;
      if (outcome.outcome === "refused") {
        this.#told = { key: project.key, said: { tone: "error", lines: [outcome.text] } };
        return;
      }
      if (outcome.outcome === "unknown") {
        this.#told = { key: project.key, said: { tone: "warn", lines: [outcome.text] } };
        this.#changed();
        return;
      }
      const path = removeStatusPath(outcome.status);
      if (path === null) {
        this.#told = { key: project.key, said: { tone: "warn", lines: [
          "projects' page server took the remove but gave no status link to read; the list has been read again."] } };
        this.#changed();
        return;
      }
      const status = new Feed<RemoveStatus>(() => api("projects", path), parseRemoveStatus, () => {});
      await status.refresh();
      if (status.value !== null) {
        const removed = status.value.state === "removed";
        this.#told = { key: removed ? null : project.key, said: removedSaid(status.value) };
      } else {
        this.#told = { key: project.key, said: { tone: "warn", lines: [
          `The remove was taken, but what it came to can't be read: ${status.failure?.said ?? status.failure?.why ?? "no answer"}.`] } };
      }
      this.#changed();
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  // -- rendering ---------------------------------------------------------------------------

  render() {
    const list = this.#list;
    if (list === null) return nothing;
    if (list.gone) return html`<p class="problem">projects is no longer installed.</p>`;
    return html`<h1>Projects</h1>
      <p class="lede">Named places: each project's key, root, kind and agents. Each change here does what the same <code>moxi projects</code> command does in a terminal.</p>
      ${this.#wherePart()}
      ${this.#listPart()}
      <section data-part="add"><div data-part="add-form"></div>
        ${this.#missing ? html`<p class="problem" data-line="no-add">The add form can't be shown here. Run <code>moxi projects add</code> in a terminal.</p>` : nothing}
      </section>`;
  }

  #wherePart() {
    const feed = this.#where;
    if (feed === null) return nothing;
    const view = whereView(feed);
    if (view.state === "gone") return nothing;
    return html`<section data-part="where" data-state=${view.state}>
      <h2>Where this page acts</h2>
      ${view.state === "loading" ? html`<div class="placeholder"></div>` : nothing}
      ${view.store !== null ? html`<p>Store: <code data-line="store">${view.store}</code></p>
        <p>Git: ${view.git !== null ? html`<code data-line="git">${view.git}</code>`
          : html`<span class="warn" data-line="git">${NO_GIT}</span>`}</p>
        <p class="dim">${WHERE_LINE}</p>` : nothing}
      ${view.line !== null ? html`<p class="problem" data-line="failed">${view.line}</p>` : nothing}
    </section>`;
  }

  #listPart() {
    const list = this.#list!;
    const kinds = this.#kinds?.value ?? null;
    const view = listView(list, this.#kind || null);
    const choices = filterChoices(kinds, view.projects, this.#kind);
    const broken = brokenLines(kinds);
    const told = this.#told;
    const loose = told !== null && (told.key === null || this.#find(told.key) === null) ? told.said : null;
    return html`<section data-part="list" data-state=${view.state}>
      <h2>Projects</h2>
      <div class="bar">
        <label><span class="dim">Show</span>
          <select name="filter" data-action="filter" aria-label="Show the kind" .value=${live(this.#kind)} @change=${this.#filter}>
            ${choices.map((choice) => html`<option value=${choice.value} ?selected=${choice.value === this.#kind}>${choice.label}</option>`)}
          </select></label>
        <button type="button" data-action="read" ?disabled=${list.reading} @click=${() => this.#readAll()}>Read again</button>
      </div>
      ${broken.length ? html`<div data-part="broken">
        <p class="dim">Can't be used now, so every add is refused until each is repaired or uninstalled:</p>
        ${broken.map((line) => html`<p class="broken" data-line="broken">${line}</p>`)}</div>` : nothing}
      ${loose !== null ? this.#saidPart(loose) : nothing}
      ${view.state === "loading" ? html`<div class="placeholder"></div>` : nothing}
      ${view.state === "empty" ? html`<p class="dim" data-line="empty">${view.line}</p>` : nothing}
      ${view.state === "failed" ? html`<p class="problem" data-line="failed">${view.line}</p>` : nothing}
      ${view.projects.length ? html`<ul aria-label="Projects">
        ${repeat(view.projects, (project) => project.key, (project) => this.#project(project))}</ul>` : nothing}
    </section>`;
  }

  #saidPart(said: Said) {
    return html`<div class="said ${said.tone}" data-line="said" data-tone=${said.tone} role="status">
      ${said.lines.map((line) => html`<p>${line}</p>`)}</div>`;
  }

  #project(project: Project) {
    const open = project.key === this.#open;
    return html`<li class="project" data-key=${project.key} data-open=${open ? "true" : "false"}>
      <div class="first">
        <button type="button" class="key" data-action="open" aria-expanded=${open ? "true" : "false"}
          @click=${() => this.#toggle(project)}>${project.key}</button>
        <span class="tag">${project.kind}</span>
        <code>${project.root}</code>
      </div>
      ${open ? this.#detail(project) : nothing}
    </li>`;
  }

  #toggle(project: Project): void {
    if (this.#open === project.key) {
      this.#open = null;
      this.#edit = null;
    } else {
      this.#open = project.key;
      this.#edit = editing(project);
      this.#lead = "";
      this.#agent = "";
    }
    if (this.#told?.key !== null && this.#told?.key !== this.#open) this.#told = null;
    this.requestUpdate();
  }

  #detail(project: Project) {
    const busy = this.#sending;
    const edit = this.#edit ?? editing(project);
    const form = edit.form;
    const notes = installed("notes");
    const kinds = this.#kinds?.value ?? null;
    const changes = editBody(edit.base, form, { notes });
    const told = this.#told?.key === project.key ? this.#told.said : null;
    return html`<div class="detail">
      <dl>${projectFields(project).map((field) => html`<dt>${field.label}</dt><dd data-field=${field.name}>${
        field.name === "root" || field.name === "notes_root" ? html`<code>${field.value}</code>` : field.value}</dd>`)}</dl>
      ${told !== null ? this.#saidPart(told) : nothing}
      <form class="part" data-part="edit" @submit=${(event: Event) => { event.preventDefault(); this.#save(project); }}>
        <h3>Change</h3>
        <label><span>Root, an absolute path</span>
          <input type="text" name="root" autocomplete="off" spellcheck="false" ?disabled=${busy}
            .value=${live(form.root)} @input=${this.#editField("root")}></label>
        <label><span>Kind</span>
          <select name="kind" ?disabled=${busy || kinds === null} .value=${live(form.kind)} @change=${this.#editField("kind")}>
            ${kindChoices(kinds, edit.base.kind).map((choice) => html`<option value=${choice.value}
              ?selected=${choice.value === form.kind}>${choice.label}</option>`)}
          </select></label>
        ${notes ? html`<label><span>Notes root, an absolute path (empty it to clear it)</span>
          <input type="text" name="notes_root" autocomplete="off" spellcheck="false" ?disabled=${busy}
            .value=${live(form.notesRoot)} @input=${this.#editField("notesRoot")}></label>` : nothing}
        ${edit.stale ? html`<p class="warn" data-line="stale" role="status">${STALE_LINE}</p>` : nothing}
        <div class="bar"><button type="submit" class="go" data-action="save" ?disabled=${busy || changes === null}>Save</button></div>
      </form>
      <div class="part" data-part="agents">
        <h3>Agents</h3>
        <div class="agent" data-part="lead"><span>Lead: <strong data-line="lead">${project.lead ?? "none"}</strong></span>
          ${project.lead !== null ? html`<button type="button" data-action="clear-lead" ?disabled=${busy}
            @click=${() => this.#clearLead(project)}>Clear lead</button>` : nothing}</div>
        <form class="line" data-part="set-lead" @submit=${(event: Event) => { event.preventDefault(); this.#setLead(project); }}>
          <input type="text" name="lead" aria-label="Lead agent" placeholder="agent name" autocomplete="off" spellcheck="false"
            ?disabled=${busy} .value=${live(this.#lead)}
            @input=${(event: Event) => { this.#lead = (event.target as HTMLInputElement).value; this.requestUpdate(); }}>
          <button type="submit" data-action="set-lead" ?disabled=${busy || this.#lead.trim() === ""}>Set lead</button>
        </form>
        ${project.associated.length ? html`<ul aria-label="Associated agents">${project.associated.map((name) => html`
          <li class="agent" data-agent=${name}><span>${name}</span>
            <button type="button" data-action="remove-agent" aria-label=${`Remove ${name}`} ?disabled=${busy}
              @click=${() => this.#removeAgent(project, name)}>Remove</button></li>`)}</ul>`
          : html`<p class="dim" data-line="no-agents">No associated agents.</p>`}
        <form class="line" data-part="add-agent" @submit=${(event: Event) => { event.preventDefault(); this.#addAgent(project); }}>
          <input type="text" name="agent" aria-label="Agent name" placeholder="agent name" autocomplete="off" spellcheck="false"
            ?disabled=${busy} .value=${live(this.#agent)}
            @input=${(event: Event) => { this.#agent = (event.target as HTMLInputElement).value; this.requestUpdate(); }}>
          <button type="submit" data-action="add-agent" ?disabled=${busy || this.#agent.trim() === ""}>Add agent</button>
        </form>
      </div>
      <div class="part" data-part="remove"><div class="bar">
        <button type="button" data-action="remove" ?disabled=${busy} @click=${() => void this.#remove(project)}>Remove…</button>
        <span class="dim">Takes it off the list; its files stay.</span>
      </div></div>
    </div>`;
  }

  #editField(name: keyof EditForm) {
    return (event: Event): void => {
      const value = (event.target as HTMLInputElement | HTMLSelectElement).value;
      if (this.#edit !== null) this.#edit = { ...this.#edit, form: { ...this.#edit.form, [name]: value } };
      this.requestUpdate();
    };
  }

  #filter = (event: Event): void => {
    this.#kind = (event.target as HTMLSelectElement).value;
    void this.#list?.refresh();
    this.requestUpdate();
  };

  updated(): void {
    if (this.#placed) return;
    const host = this.renderRoot.querySelector('[data-part="add-form"]');
    if (host === null) return;
    this.#placed = true;
    const missing = (): void => {
      this.#missing = true;
      this.requestUpdate();
    };
    place(host, "projects", "add").then((element) => { if (element === null) missing(); }, missing);
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ProjectsPage);

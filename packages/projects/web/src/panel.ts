// <moxi-projects-panel>: the Projects page's panel, at the window's right edge over the
// column, as Home's Options panel is drawn. The page places it while the add, or a
// project's edit, is open, and takes it away when it closes; it says it wants closing with
// `PANEL_CLOSE`, from `Close`, `Cancel`, Escape, or a save that saved everything.
//
// Add: `Folder` first. 250 ms after the last key it reads `/api/folder` for what is typed
// and says under the field whether the folder exists and is a git repository, or that
// nothing is there yet, with the box that makes it (hidden for a kind whose mint is
// `refuse`), and how the path stands to a project that holds it. Only the answer for what
// the field holds now is drawn. `Name` fills from the folder's last part until he types
// in it, and says when a project already has the name. `More` folds `Kind`, `Lead` and,
// while notes is installed, `Notes folder`, with their values on its line. `Add project`
// sends `/api/add` with the paths as typed, `~` included; until the folder has been read,
// a name it would fill is not sent. What the answer came to shows under the button: the
// project added, its notes and warnings, and the form empty again; a refusal's text, with
// the values kept; or, with no answer, that it may have been added. While `/api/where`
// says a change would be refused, its `note` shows dim above the button; it is read when
// the panel opens.
//
// Edit: `Folder` and `Notes folder` in `~` form, `Kind`, `Lead`, `Also works here` (each
// name with `Take off`, then `Add an agent by name`), then the name as plain text, which
// can't be changed. `Save` sends only what changed from the project as stored, in an
// order that can't trip the rule that a lead never also works there: the names taken
// off, then `/api/set` with the fields, then the names added. It stops at the first
// refusal and says what was saved before it; a save that stopped sends only the rest next
// time. With no answer, it says the project may have been saved. When the project changes
// elsewhere, a form he hasn't changed starts again from it; one he has keeps his values
// and says so. The where note and a broken kind or contribution show as in Add.
//
// After an act that was done or may have been, it sends `CHANGED` on the document, so the
// page and every projects component read again.

import { css, html, LitElement, nothing } from "lit";
import { live } from "lit/directives/live.js";
import { api } from "/m/ui/client.js";

import { CHANGED, parseAdded, parseOne, parseWhere, parseWritten } from "./feed.ts";
import type { Kinds, Listing, Project } from "./feed.ts";
import {
  addedSaid, addPanel, addPanelBody, agentAdded, alsoHere, answerNow, CHANGED_SINCE, createOffered, editPanel,
  editReread, editTouched, filled, FOLDER_WAIT_MS, folderRoute, folderSays, MAY_BE_SAVED, moreLine, NAME_FILLED,
  NAME_STAYS, NAME_TAKEN, named, nameTaken, parseAtPath, partlySaved, saveSteps, takenOff,
} from "./panel-model.ts";
import type { AddPanel, Asked, EditPanel, PanelSaid } from "./panel-model.ts";
import { BASE } from "./styles.ts";
import { actedOf, brokenLines, kindChoices, startKind } from "./views.ts";
import { kindName } from "./words.ts";

const TAG = "moxi-projects-panel";

/** The house arrow a menu draws at its right. */
const ARROW = html`<span class="v" aria-hidden="true">▾</span>`;

/** The event the panel asks to be closed with; it carries no detail. */
export const PANEL_CLOSE = "moxi-projects-panel-close";

/** The panel's width, as Home's Options panel. */
export const PANEL_WIDTH_PX = 400;

/** What the panel opens for: the add, empty or with its folder and name filled, or the
 * edit of the project `key`. */
export type PanelAsk =
  | { mode: "add"; folder?: string; name?: string }
  | { mode: "edit"; key: string };

// Home's Options panel, holding a form; no accent and no warning colour.
const STYLES = [BASE, css`
  :host { display: contents; }
  :host([hidden]) { display: none; }
  .panel { position: fixed; z-index: 5; top: 0; right: 0; bottom: 0; width: ${PANEL_WIDTH_PX}px; max-width: 100vw;
           padding: 20px 20px 24px; overflow: auto; border-left: 1px solid var(--moxi-border);
           background: var(--moxi-bg); color: var(--moxi-text); font-size: 14px; line-height: 20px; }
  .ph { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; }
  .ph b { font-size: 16px; font-weight: 600; }
  button.lnk { padding: 0; border: 0; background: none; color: var(--moxi-text-dim); font: inherit; cursor: pointer; }
  button.lnk:hover:not(:disabled) { background: none; color: var(--moxi-text); }
  form { display: block; min-width: 0; }
  label.f { display: block; margin: 14px 0 4px; color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; }
  form > label.f:first-child { margin-top: 4px; }
  input[type=text], select { display: block; width: 100%; padding: 6px 10px; border: 1px solid var(--moxi-border);
                             border-radius: 7px; background: none; color: var(--moxi-text); font: inherit; }
  input[type=text]:focus { border-color: var(--moxi-border-strong); }
  /* A menu draws the house arrow, dim at its right, in place of the browser's own. */
  .sel { position: relative; display: block; }
  .sel select { appearance: none; padding-right: 30px; cursor: pointer; }
  .sel select:focus { border-color: var(--moxi-border-strong); outline: none; }
  .sel .v { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); color: var(--moxi-text-dim);
            font-size: 13px; pointer-events: none; }
  .hint { margin-top: 5px; color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; }
  label.check { display: flex; align-items: baseline; gap: 8px; margin-top: 8px; font-size: 14px; }
  button.fold { display: flex; justify-content: space-between; gap: 12px; width: 100%; margin-top: 16px; padding: 12px 0 0;
                border: 0; border-top: 1px solid var(--moxi-border); border-radius: 0; background: none;
                color: var(--moxi-text); font: inherit; text-align: left; cursor: pointer; }
  button.fold:hover:not(:disabled) { background: none; }
  button.fold > span { white-space: nowrap; }
  button.fold .ar { display: inline-block; width: 16px; color: var(--moxi-text-dim); font-size: 13px; }
  button.fold .d { min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--moxi-text-dim); }
  .note { margin-top: 18px; }
  .pbtns { display: flex; justify-content: flex-end; align-items: center; gap: 18px; margin-top: 22px; padding-top: 14px;
           border-top: 1px solid var(--moxi-border); }
  button.btn { padding: 5px 13px; border: 1px solid var(--moxi-border-strong); border-radius: 7px; background: none;
               color: var(--moxi-text); font: inherit; }
  button.btn:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  .said { display: grid; gap: 2px; margin-top: 12px; overflow-wrap: anywhere; }
  p.f { margin: 14px 0 4px; color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; }
  .ag { display: flex; justify-content: space-between; gap: 12px; padding: 5px 0; }
  .ag > span { min-width: 0; overflow-wrap: anywhere; }
  .ag button.lnk { flex: none; font-size: 13px; }
  .ag + input { margin-top: 4px; }
  p.ro { padding: 6px 0; overflow-wrap: anywhere; }
  p.ro + .hint { margin-top: 0; }
`];

export class ProjectsPanel extends LitElement {
  static styles = STYLES;

  static properties = {
    listing: { attribute: false },
    kinds: { attribute: false },
  };

  /** The page's last list, for the home, notes and the keys taken. */
  listing: Listing | null = null;
  /** The kinds, as the page last read them. */
  kinds: Kinds | null = null;
  /** What the panel opens for; read when it connects. */
  ask: PanelAsk = { mode: "add" };

  #form: AddPanel = addPanel();
  /** The edit, once its project is found in the list. */
  #edit: EditPanel | null = null;
  #more = false;
  #asked: Asked | null = null;
  /** The folder field's value whose read is waited for, or null. */
  #waiting: string | null = null;
  #timer: ReturnType<typeof setTimeout> | null = null;
  #note: string | null = null;
  #sending = false;
  #said: PanelSaid | null = null;
  #connected = 0;

  connectedCallback(): void {
    super.connectedCallback();
    this.#connected += 1;
    const ask = this.ask;
    if (ask.mode === "add") {
      this.#form = addPanel(ask);
      if (this.#form.folder !== "") this.#readFolder(this.#form.folder);
    }
    void this.#readWhere();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#connected += 1;
    if (this.#timer !== null) clearTimeout(this.#timer);
    this.#timer = null;
  }

  protected firstUpdated(): void {
    this.focus();
  }

  /** The folder field takes the focus. */
  override focus(): void {
    this.renderRoot.querySelector<HTMLInputElement>('input[name="folder"]')?.focus();
  }

  protected willUpdate(changed: Map<PropertyKey, unknown>): void {
    // The kind starts at the default once the kinds are read, and when the one held goes.
    const kinds = this.kinds;
    if (kinds !== null && !kinds.found.some((kind) => kind.name === this.#form.kind)) {
      this.#form = { ...this.#form, kind: startKind(kinds, null) ?? "" };
    }
    // The edit's project, as each list read gives it.
    const ask = this.ask;
    if (ask.mode === "edit" && changed.has("listing")) {
      const project = this.listing?.projects.find((one) => one.key === ask.key) ?? null;
      if (project !== null) {
        this.#edit = this.#edit === null ? editPanel(project, this.#home())
          : editReread(this.#edit, project, this.#facts());
      }
    }
  }

  #home(): string | null {
    return this.listing?.home ?? null;
  }

  #facts(): { home: string | null; notesInstalled: boolean } {
    return { home: this.#home(), notesInstalled: this.listing?.notes_installed ?? false };
  }

  async #readWhere(): Promise<void> {
    const mine = this.#connected;
    const answer = await api("projects", "api/where");
    if (mine !== this.#connected) return;
    this.#note = answer.status === 200 ? parseWhere(answer.data)?.note ?? null : null;
    this.requestUpdate();
  }

  /** Reads what is at `typed` after the wait, unless the field moves on first. */
  #readFolder(typed: string, waitMs = 0): void {
    if (this.#timer !== null) clearTimeout(this.#timer);
    this.#timer = null;
    if (typed.trim() === "") {
      this.#waiting = null;
      return;
    }
    this.#waiting = typed;
    this.#timer = setTimeout(() => {
      this.#timer = null;
      void this.#askFolder(typed);
    }, waitMs);
  }

  async #askFolder(typed: string): Promise<void> {
    const mine = this.#connected;
    let answer;
    try {
      answer = await api("projects", folderRoute(typed));
    } catch {
      answer = { status: 0, data: null };
    }
    if (mine !== this.#connected || this.#form.folder !== typed) return;
    this.#waiting = null;
    const read = answer.status === 200 ? parseAtPath(answer.data) : null;
    if (read !== null) {
      this.#asked = { typed, answer: read };
      this.#form = filled(this.#form, read);
    }
    this.requestUpdate();
  }

  #close(): void {
    this.dispatchEvent(new Event(PANEL_CLOSE));
  }

  #keys = (event: KeyboardEvent): void => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    this.#close();
  };

  #field(name: "kind" | "lead" | "notesRoot") {
    return (event: Event): void => {
      this.#form = { ...this.#form, [name]: (event.target as HTMLInputElement | HTMLSelectElement).value };
      this.#said = null;
      this.requestUpdate();
    };
  }

  #folderTyped = (event: Event): void => {
    const typed = (event.target as HTMLInputElement).value;
    this.#form = { ...this.#form, folder: typed };
    this.#said = null;
    this.#readFolder(typed, FOLDER_WAIT_MS);
    this.requestUpdate();
  };

  #nameTyped = (event: Event): void => {
    this.#form = named(this.#form, (event.target as HTMLInputElement).value);
    this.#said = null;
    this.requestUpdate();
  };

  #tick = (event: Event): void => {
    this.#form = { ...this.#form, create: (event.target as HTMLInputElement).checked };
    this.requestUpdate();
  };

  #submit = async (event: Event): Promise<void> => {
    event.preventDefault();
    if (this.#sending || !this.#ready()) return;
    const form = this.#form;
    const canCreate = createOffered(answerNow(this.#asked, form.folder), this.kinds, form.kind) !== null;
    const body = addPanelBody(form, { notesInstalled: this.listing?.notes_installed ?? false, canCreate });
    this.#sending = true;
    this.#said = null;
    this.requestUpdate();
    try {
      const answer = await api("projects", "api/add", body);
      const acted = actedOf(answer.status, answer.data, parseAdded);
      this.#said = addedSaid(acted, this.listing?.home ?? null);
      if (acted.outcome === "done") {
        this.#form = { ...addPanel(), kind: this.#form.kind };
        this.#asked = null;
        this.#more = false;
      }
      if (acted.outcome !== "refused") document.dispatchEvent(new Event(CHANGED));
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  };

  /** Whether `Add project` may be pressed: a name is there, and none is waiting to be
   * filled from the folder. */
  #ready(): boolean {
    const form = this.#form;
    return form.name.trim() !== "" && (form.named || this.#waiting === null);
  }

  render() {
    if (this.ask.mode === "edit") return this.#edit === null ? nothing : this.#editPart(this.#edit);
    return this.#addPart();
  }

  #addPart() {
    const form = this.#form;
    const busy = this.#sending;
    const kinds = this.kinds;
    const notesInstalled = this.listing?.notes_installed ?? false;
    const answer = answerNow(this.#asked, form.folder);
    const says = answer === null ? "" : folderSays(answer);
    const create = createOffered(answer, kinds, form.kind);
    const taken = nameTaken(form.name, this.listing?.projects ?? []);
    const nameLine = taken ? NAME_TAKEN : !form.named && form.name !== "" ? NAME_FILLED : "";
    const said = this.#said;
    return html`<aside class="panel" aria-label="Add a project" data-part="panel" data-panel="add" @keydown=${this.#keys}>
      <div class="ph"><b>Add a project</b><button type="button" class="lnk" data-action="close"
        @click=${() => this.#close()}>Close</button></div>
      <form @submit=${this.#submit} aria-busy=${busy ? "true" : "false"}>
        <label class="f" for="p-folder">Folder</label><input id="p-folder" type="text" name="folder" autocomplete="off"
          spellcheck="false" ?disabled=${busy} .value=${live(form.folder)} @input=${this.#folderTyped}>
        ${says !== "" ? html`<p class="hint" data-line="folder">${says}</p>` : nothing}
        ${create !== null ? html`<label class="check" data-part="create"><input type="checkbox" name="create"
          ?disabled=${busy} .checked=${live(form.create)} @change=${this.#tick}>${create}</label>` : nothing}
        <label class="f" for="p-name">Name</label><input id="p-name" type="text" name="name" autocomplete="off"
          spellcheck="false" ?disabled=${busy} .value=${live(form.name)} @input=${this.#nameTyped}>
        ${nameLine !== "" ? html`<p class="hint" data-line="name">${nameLine}</p>` : nothing}
        <button type="button" class="fold" data-action="more" aria-expanded=${this.#more ? "true" : "false"}
          @click=${() => { this.#more = !this.#more; this.requestUpdate(); }}><span><span class="ar">${
          this.#more ? "▾" : "▸"}</span>More</span><span class="d" data-line="more">${moreLine(form, { notesInstalled })}</span></button>
        ${this.#more ? this.#moreFields(form, kinds, notesInstalled, busy) : nothing}
        ${this.#note !== null ? html`<p class="hint note" data-line="note">${this.#note}</p>` : nothing}
        <div class="pbtns">
          <button type="button" class="lnk" data-action="cancel" @click=${() => this.#close()}>Cancel</button>
          <button type="submit" class="btn" data-action="add-project" ?disabled=${busy || !this.#ready()}>Add project</button>
        </div>
        ${said !== null ? html`<div class="said" data-line="said" data-outcome=${said.outcome} role="status">
          ${said.lines.map((line) => html`<p>${line}</p>`)}</div>` : nothing}
      </form>
    </aside>`;
  }

  #moreFields(form: AddPanel, kinds: Kinds | null, notesInstalled: boolean, busy: boolean) {
    return html`<label class="f" for="p-kind">Kind</label><span class="sel"><select id="p-kind" name="kind"
        ?disabled=${busy || kinds === null} .value=${live(form.kind)} @change=${this.#field("kind")}>${(kinds?.found ?? []).map((kind) => html`<option
          value=${kind.name} title=${kind.doc} ?selected=${kind.name === form.kind}>${kindName(kind.name)}</option>`)}</select>${ARROW}</span>
      ${brokenLines(kinds).map((line) => html`<p class="hint" data-line="broken">${line}</p>`)}
      <label class="f" for="p-lead">Lead</label><input id="p-lead" type="text" name="lead" autocomplete="off"
        spellcheck="false" ?disabled=${busy} .value=${live(form.lead)} @input=${this.#field("lead")}>
      ${notesInstalled ? html`<label class="f" for="p-notes">Notes folder</label><input id="p-notes" type="text" name="notes_root"
        autocomplete="off" spellcheck="false" ?disabled=${busy} .value=${live(form.notesRoot)} @input=${this.#field("notesRoot")}>`
        : nothing}`;
  }

  // -- Edit ------------------------------------------------------------------------------------

  #editTyped(name: "folder" | "kind" | "lead" | "notesRoot" | "agent") {
    return (event: Event): void => {
      if (this.#edit === null) return;
      this.#edit = { ...this.#edit, [name]: (event.target as HTMLInputElement | HTMLSelectElement).value };
      if (name !== "agent") this.#said = null;
      this.requestUpdate();
    };
  }

  #takeOff(name: string): void {
    if (this.#edit === null) return;
    this.#edit = takenOff(this.#edit, name);
    this.#said = null;
    this.requestUpdate();
  }

  /** Enter in `Add an agent by name` adds the name to the list, and sends nothing. */
  #agentKeys = (event: KeyboardEvent): void => {
    if (event.key !== "Enter" || this.#edit === null) return;
    event.preventDefault();
    this.#edit = agentAdded(this.#edit);
    this.#said = null;
    this.requestUpdate();
  };

  #save = async (event: Event): Promise<void> => {
    event.preventDefault();
    if (this.#sending || this.#edit === null) return;
    this.#edit = agentAdded(this.#edit);
    const steps = saveSteps(this.#edit, this.#facts());
    if (steps.length === 0) return;
    this.#sending = true;
    this.#said = null;
    this.requestUpdate();
    const saved: string[] = [];
    let stopped = false;
    try {
      for (const step of steps) {
        const answer = await api("projects", step.route, step.body);
        const acted = actedOf<{ project: Project }>(answer.status, answer.data,
                                                    step.route === "api/set" ? parseWritten : parseOne);
        if (acted.outcome === "done") {
          // What is stored now is what the rest is told apart from.
          if (this.#edit !== null) this.#edit = { ...this.#edit, base: acted.value.project, stale: false };
          saved.push(...step.parts);
          continue;
        }
        stopped = true;
        this.#said = acted.outcome === "refused"
          ? { outcome: "refused", lines: [partlySaved(saved, acted.text)] }
          : { outcome: "unknown", lines: [MAY_BE_SAVED] };
        if (acted.outcome === "unknown" || saved.length > 0) document.dispatchEvent(new Event(CHANGED));
        break;
      }
      if (!stopped) {
        document.dispatchEvent(new Event(CHANGED));
        this.#close();
      }
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  };

  #editPart(form: EditPanel) {
    const busy = this.#sending;
    const kinds = this.kinds;
    const facts = this.#facts();
    const key = form.base.key;
    const said = this.#said;
    return html`<aside class="panel" aria-label=${`Edit ${key}`} data-part="panel" data-panel="edit"
        @keydown=${this.#keys}>
      <div class="ph"><b>Edit ${key}</b><button type="button" class="lnk" data-action="close"
        @click=${() => this.#close()}>Close</button></div>
      <form @submit=${this.#save} aria-busy=${busy ? "true" : "false"}>
        <label class="f" for="p-folder">Folder</label><input id="p-folder" type="text" name="folder" autocomplete="off"
          spellcheck="false" ?disabled=${busy} .value=${live(form.folder)} @input=${this.#editTyped("folder")}>
        <label class="f" for="p-kind">Kind</label><span class="sel"><select id="p-kind" name="kind"
          ?disabled=${busy || kinds === null} .value=${live(form.kind)} @change=${this.#editTyped("kind")}>${
            kindChoices(kinds, form.base.kind).map((choice) => html`<option value=${choice.value}
              ?selected=${choice.value === form.kind}>${choice.label}</option>`)}</select>${ARROW}</span>
        ${brokenLines(kinds).map((line) => html`<p class="hint" data-line="broken">${line}</p>`)}
        <label class="f" for="p-lead">Lead</label><input id="p-lead" type="text" name="lead" autocomplete="off"
          spellcheck="false" ?disabled=${busy} .value=${live(form.lead)} @input=${this.#editTyped("lead")}>
        <p class="f">Also works here</p>
        ${alsoHere(form).map((name) => html`<div class="ag" data-agent=${name}><span>${name}</span><button
          type="button" class="lnk" data-action="take-off" aria-label=${`Take off ${name}`} ?disabled=${busy}
          @click=${() => this.#takeOff(name)}>Take off</button></div>`)}
        <input type="text" name="agent" aria-label="Add an agent by name" placeholder="Add an agent by name"
          autocomplete="off" spellcheck="false" ?disabled=${busy} .value=${live(form.agent)}
          @input=${this.#editTyped("agent")} @keydown=${this.#agentKeys}>
        ${facts.notesInstalled ? html`<label class="f" for="p-notes">Notes folder</label><input id="p-notes" type="text"
          name="notes_root" autocomplete="off" spellcheck="false" ?disabled=${busy} .value=${live(form.notesRoot)}
          @input=${this.#editTyped("notesRoot")}>` : nothing}
        <p class="f">Name</p><p class="ro" data-line="key">${key}</p>
        <p class="hint" data-line="name">${NAME_STAYS}</p>
        ${form.stale ? html`<p class="hint note" data-line="stale" role="status">${CHANGED_SINCE}</p>` : nothing}
        ${this.#note !== null ? html`<p class="hint note" data-line="note">${this.#note}</p>` : nothing}
        <div class="pbtns">
          <button type="button" class="lnk" data-action="cancel" @click=${() => this.#close()}>Cancel</button>
          <button type="submit" class="btn" data-action="save" ?disabled=${busy || !editTouched(form, facts)}>Save</button>
        </div>
        ${said !== null ? html`<div class="said" data-line="said" data-outcome=${said.outcome} role="status">
          ${said.lines.map((line) => html`<p>${line}</p>`)}</div>` : nothing}
      </form>
    </aside>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ProjectsPanel);

// <moxi-projects-panel>: the Projects page's panel, at the window's right edge over the
// column, as Home's Options panel is drawn. The page places it while the add is open, and
// takes it away when it closes; it says it wants closing with `PANEL_CLOSE`, from `Close`,
// `Cancel` or Escape.
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
// After an add that was made or may have been, it sends `CHANGED` on the document, so the
// page and every projects component read again.

import { css, html, LitElement, nothing } from "lit";
import { live } from "lit/directives/live.js";
import { api } from "/m/ui/client.js";

import { CHANGED, parseAdded, parseWhere } from "./feed.ts";
import type { Kinds, Listing } from "./feed.ts";
import {
  addedSaid, addPanel, addPanelBody, answerNow, createOffered, filled, FOLDER_WAIT_MS, folderRoute, folderSays,
  moreLine, NAME_FILLED, NAME_TAKEN, named, nameTaken, parseAtPath,
} from "./panel-model.ts";
import type { AddPanel, Asked, PanelSaid } from "./panel-model.ts";
import { BASE } from "./styles.ts";
import { actedOf, brokenLines, kindLabel, startKind } from "./views.ts";

const TAG = "moxi-projects-panel";

/** The event the panel asks to be closed with; it carries no detail. */
export const PANEL_CLOSE = "moxi-projects-panel-close";

/** The panel's width, as Home's Options panel. */
export const PANEL_WIDTH_PX = 400;

/** What the panel opens with: the add, empty or with its folder and name filled. */
export interface PanelAsk {
  folder?: string;
  name?: string;
}

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
  /** What the panel opens with; read when it connects. */
  ask: PanelAsk = {};

  #form: AddPanel = addPanel();
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
    this.#form = addPanel(this.ask);
    if (this.#form.folder !== "") this.#readFolder(this.#form.folder);
    void this.#readWhere();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#connected += 1;
    if (this.#timer !== null) clearTimeout(this.#timer);
    this.#timer = null;
  }

  protected firstUpdated(): void {
    this.renderRoot.querySelector<HTMLInputElement>('input[name="folder"]')?.focus();
  }

  protected willUpdate(): void {
    // The kind starts at the default once the kinds are read, and when the one held goes.
    const kinds = this.kinds;
    if (kinds !== null && !kinds.found.some((kind) => kind.name === this.#form.kind)) {
      this.#form = { ...this.#form, kind: startKind(kinds, null) ?? "" };
    }
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
    const offered = createOffered(answerNow(this.#asked, form.folder), this.kinds, form.kind) !== null;
    const body = addPanelBody(form, { notes: this.listing?.notes_installed ?? false, offered });
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
    const form = this.#form;
    const busy = this.#sending;
    const kinds = this.kinds;
    const notes = this.listing?.notes_installed ?? false;
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
          this.#more ? "▾" : "▸"}</span>More</span><span class="d" data-line="more">${moreLine(form, { notes })}</span></button>
        ${this.#more ? this.#moreFields(form, kinds, notes, busy) : nothing}
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

  #moreFields(form: AddPanel, kinds: Kinds | null, notes: boolean, busy: boolean) {
    return html`<label class="f" for="p-kind">Kind</label><select id="p-kind" name="kind"
        ?disabled=${busy || kinds === null} .value=${live(form.kind)} @change=${this.#field("kind")}>${(kinds?.found ?? []).map((kind) => html`<option
          value=${kind.name} title=${kind.doc} ?selected=${kind.name === form.kind}>${kindLabel(kind)}</option>`)}</select>
      ${brokenLines(kinds).map((line) => html`<p class="hint" data-line="broken">${line}</p>`)}
      <label class="f" for="p-lead">Lead</label><input id="p-lead" type="text" name="lead" autocomplete="off"
        spellcheck="false" ?disabled=${busy} .value=${live(form.lead)} @input=${this.#field("lead")}>
      ${notes ? html`<label class="f" for="p-notes">Notes folder</label><input id="p-notes" type="text" name="notes_root"
        autocomplete="off" spellcheck="false" ?disabled=${busy} .value=${live(form.notesRoot)} @input=${this.#field("notesRoot")}>`
        : nothing}`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ProjectsPanel);

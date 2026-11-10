// <moxi-launcher-prompt>: the launcher's piece for Home's main spot: a box to say what the
// agent should do, the line above it that says where the session starts, and the button
// that starts it.
//
// Home places it in its spot and expects no report from it: a main piece is shown as it
// is. The caret is in the box as soon as the piece is drawn.
//
// The line above the box reads `Project <key>` or `Folder <path>`. When the piece connects
// it reads `/api/places` and works out once whether projects is there (the page server
// takes a project, and the shell lists projects' picker); the line opens on the newest
// place, or, on a Home that never launched, on the one project, on `Project choose` with
// several, else on the home folder. Its menu takes a typed folder path, lists the places
// (read again each time it opens) and, with projects, places projects' picker under them.

import { css, html, LitElement, nothing } from "lit";
import { api, components, place } from "/m/ui/client.js";
import type { FormValue } from "/m/ui/client.js";

import {
  openWhere, parsePlaces, pickValues, placed, placeRows, samePick, typedFolder, whereFace,
} from "./prompt-model.ts";
import type { Pick, Place, PlaceRow } from "./prompt-model.ts";

const TAG = "moxi-launcher-prompt";
/** The event projects' picker sends its host on a choice, with `{key}`. */
const PICKED = "moxi-change";

const STYLES = css`
  :host { display: block; position: relative; min-width: 0; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; }
  button { margin: 0; cursor: pointer; }
  .where { position: absolute; left: 0; bottom: calc(100% + 10px); max-width: 100%; padding: 0 2px;
           color: var(--moxi-text-dim); font-size: 14px; line-height: 20px; white-space: nowrap; }
  .where .k { margin-right: 10px; }
  .anchor { position: relative; display: inline-block; }
  .face { padding: 0; border: 0; background: none; color: var(--moxi-text); font: inherit; }
  .p { color: var(--moxi-text-dim); }
  .v { position: relative; top: 1px; margin-left: 6px; color: var(--moxi-text-dim); font-size: 15px; line-height: 0; }
  .menu { position: absolute; z-index: 2; left: 0; top: calc(100% + 8px); width: 420px; max-width: calc(100vw - 36px);
          padding: 0 0 6px; border: 1px solid var(--moxi-border-strong); border-radius: 10px;
          background: var(--moxi-bg); color: var(--moxi-text); font: 14px/1.5 var(--moxi-font-sans);
          text-align: left; white-space: normal; }
  .find { display: block; width: 100%; padding: 11px 14px 10px; border: 0; border-bottom: 1px solid var(--moxi-border);
          outline: 0; background: none; color: var(--moxi-text); font: inherit; }
  .find::placeholder { color: var(--moxi-text-dim); opacity: 1; }
  .hint { margin: 0; padding: 7px 14px 0; color: var(--moxi-text-dim); }
  .places { display: grid; margin-top: 6px; }
  .row { display: grid; grid-template-columns: 20px 1fr auto; align-items: baseline; width: 100%; padding: 7px 14px;
         border: 0; background: none; color: var(--moxi-text); font: inherit; text-align: left; }
  .row:hover { background: var(--moxi-bg-hover); }
  .row .name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .t { padding-left: 10px; color: var(--moxi-text-dim); font-variant-numeric: tabular-nums; }
  .all { margin: 0; padding: 7px 14px 4px 34px; color: var(--moxi-text-dim); }
  .picker { max-height: 240px; padding: 0 14px 2px; overflow: auto; }
  .box { display: flex; flex-direction: column; justify-content: space-between; height: 150px;
         padding: 16px 16px 12px; border: 1px solid var(--moxi-border); border-radius: 12px; }
  textarea { flex: 1 1 auto; width: 100%; min-height: 0; margin: 0 0 8px; padding: 0; border: 0; outline: 0;
             resize: none; background: none; color: var(--moxi-text); font: inherit; font-size: 16px;
             line-height: 1.5; }
  textarea::placeholder { color: var(--moxi-text-dim); opacity: 1; }
  .boxrow { display: flex; justify-content: space-between; align-items: center; }
  .left { display: flex; gap: 8px; align-items: center; min-width: 0; }
  .go { padding: 9px 16px; border: 0; border-radius: 7px; background: var(--moxi-accent);
        color: var(--moxi-on-accent); font: 600 14px/1 var(--moxi-font-sans); }
  .go:disabled { cursor: default; opacity: 0.6; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  textarea:focus-visible, .find:focus-visible { outline: 0; }
  [hidden] { display: none; }
  @media (max-width: 720px) {
    .where { position: static; margin-bottom: 10px; white-space: normal; overflow-wrap: anywhere; }
    .box { height: auto; min-height: 150px; }
    textarea { min-height: 64px; }
    .boxrow { flex-wrap: wrap; justify-content: flex-end; row-gap: 10px; }
    .left { flex: 1 0 100%; }
  }
`;

export class LauncherPrompt extends LitElement {
  static styles = STYLES;

  static properties = {
    brief: { state: true },
    pick: { state: true },
    places: { state: true },
    menu: { state: true },
    typed: { state: true },
  };

  /** The box's text. */
  brief = "";
  /** Where the launch starts; null until the places are read. */
  pick: Pick | null = null;
  places: Place[] = [];
  /** The menu that is open: the line's (`where`), or none. */
  menu: "" | "where" = "";
  /** A typed folder path the menu did not take. */
  typed = "";
  /** Whether projects is there, worked out once when the piece connects. */
  #withProjects = false;
  /** Holds projects' picker, placed the first time the line's menu opens. */
  #picker = document.createElement("div");
  #pickerPlaced = false;
  #opened = 0;

  #outside = (event: Event): void => {
    const open = this.renderRoot.querySelector(".anchor.open");
    if (open !== null && !event.composedPath().includes(open)) this.menu = "";
  };

  constructor() {
    super();
    this.#picker.className = "picker";
    this.#picker.addEventListener(PICKED, (event) => {
      event.stopPropagation();
      const key = (event as CustomEvent).detail?.key;
      if (typeof key === "string" && key !== "") this.#choose({ kind: "project", key });
    });
  }

  connectedCallback(): void {
    super.connectedCallback();
    document.addEventListener("pointerdown", this.#outside);
    void this.#open();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    document.removeEventListener("pointerdown", this.#outside);
    this.#opened += 1;
  }

  protected firstUpdated(): void {
    this.#box()?.focus();
  }

  #box(): HTMLTextAreaElement | null {
    return this.renderRoot.querySelector("textarea");
  }

  /** Whether a launch has all it needs from this piece: a place. */
  get ready(): boolean {
    return this.pick !== null && placed(this.pick);
  }

  /** The rows this piece sets, over what `/api/form` fills: the brief and the place. */
  get set(): Record<string, FormValue> {
    return { brief: this.brief === "" ? null : this.brief, ...(this.pick === null ? {} : pickValues(this.pick)) };
  }

  /** Reads the places and sets the line, once for each time the piece is connected. */
  async #open(): Promise<void> {
    const mine = (this.#opened += 1);
    const picker = components("projects").some((item) => item.name === "picker");
    const where = await openWhere(api, picker);
    if (mine !== this.#opened) return;
    this.#withProjects = where.withProjects;
    this.places = where.places;
    // A place he picked while the read was under way stands.
    this.pick ??= where.pick;
  }

  #toggleWhere(): void {
    this.menu = this.menu === "where" ? "" : "where";
    if (this.menu !== "where") return;
    this.typed = "";
    if (this.#withProjects && !this.#pickerPlaced) {
      this.#pickerPlaced = true;
      void place(this.#picker, "projects", "picker");
    }
    void this.#readPlaces();
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLInputElement>(".find")?.focus());
  }

  async #readPlaces(): Promise<void> {
    const mine = this.#opened;
    const answer = await api("launcher", "api/places");
    const read = answer.status === 200 ? parsePlaces(answer.data) : null;
    if (read !== null && mine === this.#opened) this.places = read.places;
  }

  /** Takes a pick, closes the menu and puts the caret back in the box. */
  #choose(pick: Pick): void {
    this.pick = pick;
    this.menu = "";
    void this.updateComplete.then(() => this.#box()?.focus());
  }

  #typedKey(event: KeyboardEvent): void {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const field = event.target as HTMLInputElement;
    const path = typedFolder(field.value);
    if (path === null) this.typed = field.value.trim();
    else this.#choose({ kind: "folder", path });
  }

  #keys(event: KeyboardEvent): void {
    if (event.key !== "Escape" || this.menu === "") return;
    event.stopPropagation();
    const face = this.renderRoot.querySelector<HTMLElement>(".anchor.open > button");
    this.menu = "";
    face?.focus();
  }

  render() {
    return html`<div class="piece" @keydown=${this.#keys}>
      ${this.pick === null ? nothing : this.#where(this.pick)}
      <div class="box">
        <textarea aria-label="What should the agent do?" placeholder="What should the agent do?"
          .value=${this.brief}
          @input=${(event: Event) => { this.brief = (event.target as HTMLTextAreaElement).value; }}></textarea>
        <div class="boxrow"><div class="left"></div>${this.#button()}</div>
      </div>
    </div>`;
  }

  #where(pick: Pick) {
    const face = whereFace(pick);
    const open = this.menu === "where";
    return html`<div class="where" data-word=${face.word}><span class="k">${face.word}</span><span
      class=${open ? "anchor open" : "anchor"}><button type="button" class="face" aria-haspopup="true"
        aria-expanded=${open ? "true" : "false"} @click=${this.#toggleWhere}>${this.#folder(face.dim, face.name)}<span
        class="v" aria-hidden="true">▾</span></button>${open ? this.#whereMenu(pick) : nothing}</span></div>`;
  }

  #folder(dim: string, name: string) {
    return html`${dim === "" ? nothing : html`<span class="p">${dim}</span>`}${name}`;
  }

  #whereMenu(pick: Pick) {
    const rows = placeRows(this.places, this.#withProjects, pick, new Date());
    return html`<div class="menu" data-menu="where">
      <input class="find" type="text" aria-label="Type a folder path" placeholder="Type a folder path"
        autocomplete="off" spellcheck="false" @keydown=${this.#typedKey}>
      ${this.typed === "" ? nothing
        : html`<p class="hint" role="status">Give an absolute path, or one starting with ~</p>`}
      <div class="places">${rows.map((row) => this.#placeRow(row))}</div>
      ${this.#withProjects ? html`<p class="all">All projects</p>${this.#picker}` : nothing}
    </div>`;
  }

  #placeRow(row: PlaceRow) {
    return html`<button type="button" class="row" data-current=${row.current ? "true" : "false"}
      @click=${() => this.#choose(row.pick)}><span aria-hidden="true">${row.current ? "✓" : ""}</span><span
      class="name">${this.#folder(row.dim, row.name)}</span><span class="t">${row.time}</span></button>`;
  }

  /** The button's place, at the right of the box's last line. It starts nothing yet. */
  #button() {
    return html`<span class="split"><button type="button" class="go" disabled>Launch</button></span>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, LauncherPrompt);

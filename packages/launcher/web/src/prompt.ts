// <moxi-launcher-prompt>: the launcher's piece for Home's main spot: a box to say what the
// agent should do, the line above it that says where the session starts, the chip in it
// that says which harness and model run it, and the button that starts it.
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
//
// The chip reads `/api/catalog` and `/api/form`: its face is the harness, the model and
// the effort the form is filled with, until one is set in its menu. A read of either that
// failed is made again when the tab is shown again and when the button's menu opens. The menu is three
// lists side by side, the harness first; `older…` opens a field for a model's full name.
// Another harness reads `/api/form` again for it and lets go of the model and the effort
// set here, which are the first harness's own.
//
// The button names the act and where it runs: `Launch in slot 2`, `Launch in a new slot`,
// `Copy command`, `Launch headless`. Its caret opens the choices `/api/targets` gives. The
// choice starts at that read's default each time the piece is drawn. The listing is read
// when the piece connects, when the menu opens, on every event of the topic `launches`,
// when the tab is shown again, and before a press when the last read is over five seconds
// old.
//
// A press sends the whole form to `/api/preview` and, with its ticket, to `/api/launch`,
// as the launcher's panel does, then reads the 202's status path until the launch's row
// has started or failed. The button is off from the press to the first end; every end
// leaves a line under the box and gives the button back. A launch in the user's terminal
// is never run here: its command is copied. The lines stay until the next press or an
// edit of the box.
//
// `Options`, beside the chip, opens a panel at the window's right edge, drawn here and
// fixed to the window, with the box and the button still in use beside it. The piece tells
// Home the panel's width with `moxi-home-side` when it opens and 0 when it closes or the
// piece leaves, and Home moves its column aside where the window has the room. The panel
// holds a find field and the launch form's other rows in the catalog's groups, each group
// and subgroup opening in place and saying, while closed, which of its values differ from
// the shipped defaults. Each row is drawn by the shell's form renderer; a value changed
// there is one more row this piece sets, and goes in the launch request with the rest.

import { css, html, LitElement, nothing } from "lit";
import { api, components, navigate, place, subscribe } from "/m/ui/client.js";
import type { ApiResult, FieldDescriptor, FormChange, FormValue } from "/m/ui/client.js";

import {
  changedValue, drawnField, findRows, HIDDEN_FLAG, keptOptions, NO_MATCH, PANEL_WIDTH_PX, panelGroups, SIDE_EVENT, valueNow,
  withOption,
} from "./options-model.ts";
import type { PanelField, PanelGroup, PanelRow, PanelSub } from "./options-model.ts";

import {
  fieldsFor, filledFields, FOLLOW_FOR_MS, FOLLOW_MS, formPath, NO_PRESET, parseCatalog, parseForm,
} from "./panel.ts";
import type { Catalog, Form } from "./panel.ts";
import {
  afterNoAnswer, aroundPage, buttonWords, chipFace, chipMenu, copied, edited, followed, following, hasEffort, IDLE, inFlight, launched,
  LAUNCHER_PAGE, launchRequest, openWhere, parsePlaces, parseTargets, pickValues, placed, placeRows, PRESSED, previewed, runMenu,
  runOf, SLOTS_UNREAD, STARTING, stopped, TARGETS_FRESH_MS, typedFolder, valueOf, whereFace,
} from "./prompt-model.ts";
import type { ChipItem, Flight, Line, Pick, Place, PlaceRow, Run, RunRow, Targets } from "./prompt-model.ts";

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
  .where .anchor { position: static; }
  .face { padding: 0; border: 0; background: none; color: var(--moxi-text); font: inherit; }
  .p { color: var(--moxi-text-dim); }
  .v { position: relative; top: 1px; margin-left: 6px; color: var(--moxi-text-dim); font-size: 15px; line-height: 0; }
  .menu { position: absolute; z-index: 2; left: 0; top: calc(100% + 8px); width: 420px; max-width: calc(100vw - 32px);
          padding: 0 0 6px; border: 1px solid var(--moxi-border); border-radius: 10px;
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
  .row.d { color: var(--moxi-text-dim); }
  .chip { padding: 8px 10px; border: 1px solid var(--moxi-border); border-radius: 7px; background: none;
          color: var(--moxi-text); font: 13px/1 var(--moxi-font-mono); white-space: nowrap; }
  .chip .v { margin-left: 8px; }
  .open > .chip { border-color: var(--moxi-border-strong); }
  .menu.model { top: calc(100% + 6px); width: 440px; max-width: calc(100vw - 66px); padding: 8px 0 4px; }
  .cols { display: grid; grid-template-columns: 1.3fr 1fr 1fr; }
  .cols > div { min-width: 0; }
  .hd { padding: 0 10px 0 34px; color: var(--moxi-text-dim); font-size: 13px; line-height: 24px; }
  .model .row { grid-template-columns: 20px 1fr; padding: 6px 10px 6px 14px; }
  .model .find { margin-top: 4px; border-top: 1px solid var(--moxi-border); border-bottom: 0; }
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
  .go:disabled, .more:disabled { cursor: default; opacity: 0.6; }
  .split { position: relative; display: inline-flex; flex: none; }
  .split .anchor { position: static; display: inline-flex; margin-left: 1px; }
  .split .go { border-radius: 7px 0 0 7px; white-space: nowrap; }
  .more { padding: 9px 10px; border: 0; border-radius: 0 7px 7px 0; background: var(--moxi-accent);
          color: var(--moxi-on-accent); font: 600 14px/1 var(--moxi-font-sans); }
  .more span { position: relative; top: 1px; font-size: 15px; line-height: 0; }
  .menu.run { left: auto; right: 0; width: 440px; max-width: calc(100vw - 49px); padding: 6px 0; }
  .run .row { grid-template-columns: 20px 112px 1fr; }
  .run .row .d { min-width: 0; color: var(--moxi-text-dim); overflow-wrap: anywhere; }
  .run .row.taken { color: var(--moxi-text-dim); cursor: default; }
  .run .row.taken:hover { background: none; }
  .run hr { margin: 6px 0; border: 0; border-top: 1px solid var(--moxi-border); }
  .opt { padding: 8px 6px; border: 0; background: none; color: var(--moxi-text-dim); font: 14px/1 var(--moxi-font-sans); }
  .panel { position: fixed; z-index: 5; top: 0; right: 0; bottom: 0; width: ${PANEL_WIDTH_PX}px; max-width: 100vw;
           padding: 20px 20px 0; overflow: auto; border-left: 1px solid var(--moxi-border);
           background: var(--moxi-bg); color: var(--moxi-text); font-size: 14px; }
  .ph { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; }
  .ph b { font-size: 16px; font-weight: 600; }
  .close { padding: 0; border: 0; background: none; color: var(--moxi-text-dim); font: inherit; }
  .pfind { display: block; width: 100%; margin: 0 0 12px; padding: 6px 10px; border: 1px solid var(--moxi-border);
           border-radius: 7px; background: none; color: var(--moxi-text); font: inherit; }
  .pfind::placeholder { color: var(--moxi-text-dim); opacity: 1; }
  .cat, .sub { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; width: 100%;
               border: 0; background: none; color: var(--moxi-text); font: inherit; text-align: left; }
  .cat { height: 38px; padding: 0; border-top: 1px solid var(--moxi-border); line-height: 38px; }
  .cat b { font-weight: 600; }
  .sub { height: 31px; padding: 0 0 0 16px; line-height: 31px; }
  .cat > span:first-child, .sub > span:first-child { flex: none; }
  .ar { display: inline-block; width: 16px; color: var(--moxi-text-dim); font-size: 13px; }
  .sum { min-width: 0; overflow: hidden; color: var(--moxi-text-dim); text-overflow: ellipsis; white-space: nowrap; }
  .catbody { padding-bottom: 8px; }
  .subopen { padding: 0 0 4px 32px; }
  .loose { padding: 0 0 4px 16px; }
  .prow { padding: 6px 0; }
  .at { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 2px; color: var(--moxi-text-dim);
        font-size: 13px; line-height: 18px; }
  .none { margin: 0; padding: 8px 0; border-top: 1px solid var(--moxi-border); color: var(--moxi-text-dim); }
  .said { margin: 10px 2px 0; font-size: 14px; line-height: 20px; overflow-wrap: anywhere; }
  .said p { margin: 0; }
  .said .dim { color: var(--moxi-text-dim); }
  .said a { color: inherit; }
  .said code { display: block; margin-top: 4px; font: 13px/1.5 var(--moxi-font-mono); user-select: all; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  textarea:focus-visible, .find:focus-visible { outline: 0; }
  [hidden] { display: none; }
  @media (max-width: 720px) {
    .where { position: relative; margin-bottom: 10px; white-space: normal; overflow-wrap: anywhere; }
    .box { height: auto; min-height: 150px; }
    textarea { min-height: 64px; }
    .boxrow { flex-wrap: wrap; justify-content: flex-end; row-gap: 10px; }
    .left { flex: 1 0 100%; }
    .run .row { grid-template-columns: 20px 96px 1fr; }
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
    fields: { state: true },
    chosen: { state: true },
    older: { state: true },
    targets: { state: true },
    picked: { state: true },
    flight: { state: true },
    unread: { state: true },
    panel: { state: true },
    query: { state: true },
    opened: { state: true },
    options: { state: true },
  };

  /** The box's text. */
  brief = "";
  /** Where the launch starts; null until the places are read. */
  pick: Pick | null = null;
  places: Place[] = [];
  /** The menu that is open: the line's (`where`), the chip's (`chip`), the button's
   * (`run`), or none. */
  menu: "" | "where" | "chip" | "run" = "";
  /** A typed folder path the menu did not take. */
  typed = "";
  /** The form's fields for the harness, filled from `/api/form`; empty until both are read. */
  fields: FieldDescriptor[] = [];
  /** The model and the effort set in the chip's menu, null for one emptied there. */
  chosen: Record<string, FormValue> = {};
  /** The field for a model's full name is open. */
  older = false;
  /** Where a launch may run, as last read; null until the first read answers. */
  targets: Targets | null = null;
  /** The choice made in the button's menu; null is the listing's default. */
  picked: Run | null = null;
  /** The launch the button started, and the lines under the box. */
  flight: Flight = IDLE;
  /** Why the launch form could not be read, when it could not. */
  unread = "";
  /** The Options panel is open. */
  panel = false;
  /** The text in the panel's find field. */
  query = "";
  /** The panel's open groups, by key, and open subgroups, by `<group key>/<subgroup>`. */
  opened: ReadonlySet<string> = new Set();
  /** The values set in the panel, by row; a row not named here keeps what the form is
   * filled with. */
  options: Record<string, FormValue> = {};
  /** When the read that gave the listing was asked, by the page's clock; 0 when the last
   * read failed or left the slots unread, so the next press reads again. */
  #targetsAt = 0;
  #targetsAsked = 0;
  /** Counts presses, so an answer for an earlier launch is dropped. */
  #pressed = 0;
  /** The box's text when the button was pressed. */
  #sent = "";
  #followTimer: ReturnType<typeof setInterval> | undefined;
  #followSince = 0;
  #stops: (() => void)[] = [];
  #catalog: Catalog | null = null;
  #form: Form | null = null;
  /** Counts `/api/form` reads, so only the latest one's answer is drawn. */
  #asked = 0;
  /** Whether projects is there, worked out once when the piece connects. */
  #withProjects = false;
  /** The user's home folder as the page server names it, drawn as `~`. */
  #home = "";
  /** Holds projects' picker, placed the first time the line's menu opens. */
  #picker = document.createElement("div");
  #pickerPlaced = false;
  #opened = 0;

  #shown = (): void => {
    if (document.visibilityState !== "visible") return;
    void this.#readTargets();
    this.#readUnread();
  };

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
    document.addEventListener("visibilitychange", this.#shown);
    // Every launch through the launcher writes the log, and may take or free a slot.
    this.#stops.push(subscribe("launcher", "launches", () => void this.#readTargets()));
    void this.#open();
    void this.#readCatalog();
    void this.#readForm("");
    void this.#readTargets();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    document.removeEventListener("pointerdown", this.#outside);
    document.removeEventListener("visibilitychange", this.#shown);
    for (const stop of this.#stops.splice(0)) stop();
    this.#opened += 1;
    this.#asked += 1;
    this.#targetsAsked += 1;
    this.#pressed += 1;
    this.#stopFollow();
    if (this.panel) {
      this.panel = false;
      this.#side(0);
    }
  }

  protected updated(changed: Map<string, unknown>): void {
    if (changed.has("panel") && changed.get("panel") !== undefined) this.#side(this.panel ? PANEL_WIDTH_PX : 0);
  }

  /** Tells Home how wide the panel beside its column is now; 0 is no panel. */
  #side(width: number): void {
    this.dispatchEvent(new CustomEvent(SIDE_EVENT, { bubbles: true, composed: true, detail: { width } }));
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

  /** The harness the form is drawn for; "" until `/api/form` has answered. */
  get harness(): string {
    return this.#form?.harness ?? "";
  }

  /** The rows this piece sets, over what `/api/form` fills: the brief, the place, the
   * model and the effort once the chip's menu set them, and each value set in the panel. */
  get set(): Record<string, FormValue> {
    return { ...this.options, ...this.chosen, brief: this.brief === "" ? null : this.brief,
             ...(this.pick === null ? {} : pickValues(this.pick)) };
  }

  async #readCatalog(): Promise<void> {
    const mine = this.#opened;
    const answer = await api("launcher", "api/catalog");
    if (mine !== this.#opened) return;
    this.#catalog = answer.status === 200 ? parseCatalog(answer.data) : null;
    if (this.#catalog === null || this.#catalog.state === "error") this.unread = unreadWhy(answer, this.#catalog);
    this.#refill();
  }

  /** Reads `/api/form`, for `harness` when one is asked. An answer that can't be read
   * leaves the form as it was. */
  async #readForm(harness: string): Promise<void> {
    const asked = (this.#asked += 1);
    const answer = await api("launcher", formPath(NO_PRESET, harness));
    if (asked !== this.#asked) return;
    const form = answer.status === 200 ? parseForm(answer.data) : null;
    if (form === null || form.state === "error") {
      if (this.#form === null) this.unread = unreadWhy(answer, form);
      return;
    }
    const other = this.#form !== null && form.harness !== this.#form.harness;
    if (other) this.chosen = {};
    this.#form = form;
    this.#refill();
    // A row the other harness has no place for in the panel is no longer set.
    if (other && this.fields.length > 0) this.options = keptOptions(this.options, this.fields);
  }

  /** Reads the catalog and the form again, whichever the last read of it left unread. */
  #readUnread(): void {
    if (this.#catalog === null || this.#catalog.state === "error") void this.#readCatalog();
    if (this.#form === null) void this.#readForm("");
  }

  #refill(): void {
    if (this.#form === null) return;
    if (this.#catalog !== null && this.#catalog.state === "up") this.unread = "";
    this.fields = filledFields(fieldsFor(this.#catalog, this.#form.harness), this.#form, NO_PRESET);
  }

  /** Reads the places and sets the line, once for each time the piece is connected. */
  async #open(): Promise<void> {
    const mine = (this.#opened += 1);
    const picker = components("projects").some((item) => item.name === "picker");
    const where = await openWhere(api, picker);
    if (mine !== this.#opened) return;
    this.#withProjects = where.withProjects;
    this.#home = where.home;
    this.places = where.places;
    // A place he picked while the read was under way stands.
    this.pick ??= where.pick;
  }

  #toggleChip(): void {
    this.menu = this.menu === "chip" ? "" : "chip";
    this.older = false;
  }

  /** Sets the model or the effort; an empty value is the row's empty state. */
  #setChip(name: string, value: string): void {
    this.chosen = { ...this.chosen, [name]: value === "" ? null : value };
    this.older = false;
  }

  #setHarness(value: string): void {
    this.older = false;
    if (value !== this.harness) void this.#readForm(value);
  }

  #openOlder(): void {
    this.older = true;
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLInputElement>(".model .find")?.focus());
  }

  #olderKey(event: KeyboardEvent): void {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const name = (event.target as HTMLInputElement).value.trim();
    if (name !== "") this.#setChip("model", name);
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
    if (event.key !== "Escape" || (this.menu === "" && !this.panel)) return;
    event.stopPropagation();
    if (this.menu === "") {
      this.#closePanel();
      return;
    }
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
          @input=${(event: Event) => this.#edit((event.target as HTMLTextAreaElement).value)}></textarea>
        <div class="boxrow"><div class="left">${this.#chip()}${this.#optionsWord()}</div>${this.#button()}</div>
      </div>
      ${this.#said()}
      ${this.panel ? this.#panel() : nothing}
    </div>`;
  }

  #where(pick: Pick) {
    const face = whereFace(pick, this.#home);
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
    const rows = placeRows(this.places, this.#withProjects, pick, this.#home, new Date());
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

  #chip() {
    if (this.fields.length === 0) return nothing;
    const model = valueOf(this.fields, this.chosen, "model");
    const effort = hasEffort(this.fields) ? valueOf(this.fields, this.chosen, "effort") : null;
    const open = this.menu === "chip";
    return html`<span class=${open ? "anchor open" : "anchor"}><button type="button" class="chip"
      aria-haspopup="true" aria-expanded=${open ? "true" : "false"} aria-label="Harness and model"
      @click=${this.#toggleChip}>${chipFace(this.harness, model, effort)}<span class="v"
      aria-hidden="true">▾</span></button>${open ? this.#chipMenu(model, effort ?? "") : nothing}</span>`;
  }

  #chipMenu(model: string, effort: string) {
    const menu = chipMenu(this.fields, this.harness, model, effort);
    const list = (items: ChipItem[], pick: (value: string) => void) => items.map((item) =>
      html`<button type="button" class="row" data-current=${item.current ? "true" : "false"}
        @click=${() => pick(item.value)}><span aria-hidden="true">${item.current ? "✓" : ""}</span><span
        class="name">${item.label}</span></button>`);
    return html`<div class="menu model" data-menu="chip">
      <div class="cols">
        <div data-list="harness"><div class="hd">Harness</div>${list(menu.harness, (value) => this.#setHarness(value))}</div>
        <div data-list="model"><div class="hd">Model</div>${list(menu.model, (value) => this.#setChip("model", value))}
          <button type="button" class="row d" data-current=${menu.typed === "" ? "false" : "true"}
            @click=${this.#openOlder}><span aria-hidden="true">${menu.typed === "" ? "" : "✓"}</span><span
            class="name">older…</span></button></div>
        ${menu.effort === null ? nothing : html`<div data-list="effort"><div class="hd">Effort</div>${
          list(menu.effort, (value) => this.#setChip("effort", value))}</div>`}
      </div>
      ${this.older ? html`<input class="find" type="text" aria-label="A model's full name"
        placeholder="A model's full name" autocomplete="off" spellcheck="false" .value=${menu.typed}
        @keydown=${this.#olderKey}>` : nothing}
    </div>`;
  }

  // --- the Options panel ----------------------------------------------------------------------

  #togglePanel(): void {
    if (this.panel) this.#closePanel();
    else {
      this.panel = true;
      this.menu = "";
    }
  }

  /** Closes the panel and gives the focus back to the word that opens it. */
  #closePanel(): void {
    this.panel = false;
    this.query = "";
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>(".opt")?.focus());
  }

  /** Opens or closes a group or a subgroup, by its key. */
  #toggleOpen(key: string): void {
    const next = new Set(this.opened);
    if (!next.delete(key)) next.add(key);
    this.opened = next;
  }

  /** Takes one change of a row from the form renderer that draws it. */
  #optionChanged(field: PanelField, event: CustomEvent<FormChange>): void {
    event.stopPropagation();
    this.options = withOption(this.options, field, changedValue(field, valueNow(field, this.options), event.detail),
                              this.fields as PanelField[]);
  }

  /** The word beside the chip that opens the panel, once there is a form to draw in it. */
  #optionsWord() {
    if (this.fields.length === 0) return nothing;
    return html`<button type="button" class="opt" aria-expanded=${this.panel ? "true" : "false"}
      @click=${this.#togglePanel}>Options</button>`;
  }

  #panel() {
    const fields = this.fields as PanelField[];
    const finding = this.query.trim() !== "";
    return html`<aside class="panel" aria-label="Options" data-panel>
      <div class="ph"><b>Options</b><button type="button" class="close" @click=${this.#closePanel}>Close</button></div>
      <input class="pfind" type="text" aria-label="Find an option" placeholder="Find an option" autocomplete="off"
        spellcheck="false" .value=${this.query}
        @input=${(event: Event) => { this.query = (event.target as HTMLInputElement).value; }}>
      ${finding ? this.#found(fields) : panelGroups(fields, this.options).map((group) => this.#group(group))}
    </aside>`;
  }

  #found(fields: PanelField[]) {
    const rows = findRows(fields, this.query);
    if (rows.length === 0) return html`<p class="none" role="status">${NO_MATCH}</p>`;
    return html`<div class="found">${rows.map((row) => this.#optionRow(row, row.where))}</div>`;
  }

  #group(group: PanelGroup) {
    const open = this.opened.has(group.key);
    return html`<button type="button" class="cat" data-group=${group.key} aria-expanded=${open ? "true" : "false"}
        @click=${() => this.#toggleOpen(group.key)}><span><span class="ar" aria-hidden="true">${open ? "▾" : "▸"}</span><b>${
        group.title}</b></span><span class="sum">${open ? "" : group.summary}</span></button>
      ${open ? html`<div class="catbody" data-body=${group.key}>${group.subs.map((sub) => this.#sub(group, sub))}</div>` : nothing}`;
  }

  /** A subgroup: its line, and its rows while it is open. The rows with no subgroup are
   * drawn under the group itself. */
  #sub(group: PanelGroup, sub: PanelSub) {
    if (sub.name === "") return html`<div class="loose">${sub.rows.map((row) => this.#optionRow(row))}</div>`;
    const key = `${group.key}/${sub.name}`;
    const open = this.opened.has(key);
    return html`<button type="button" class="sub" data-sub=${key} aria-expanded=${open ? "true" : "false"}
        @click=${() => this.#toggleOpen(key)}><span><span class="ar" aria-hidden="true">${open ? "▾" : "▸"}</span>${
        sub.name}</span><span class="sum">${open ? "" : sub.summary}</span></button>
      ${open ? html`<div class="subopen">${sub.rows.map((row) => this.#optionRow(row))}</div>` : nothing}`;
  }

  /** One row: where it sits when the find field found it, the mark of a hidden flag, and
   * the form renderer's control for its kind. */
  #optionRow(row: PanelRow, where = "") {
    const field = row.field;
    return html`<div class="prow" data-row=${row.name}>
      ${where === "" && !row.hidden ? nothing : html`<div class="at"><span>${where}</span>${
        row.hidden ? html`<span data-mark="hidden">${HIDDEN_FLAG}</span>` : nothing}</div>`}
      <moxi-ui-form mode="each" .fields=${[drawnField(field, valueNow(field, this.options))]}
        @moxi-change=${(event: CustomEvent<FormChange>) => this.#optionChanged(field, event)}></moxi-ui-form>
    </div>`;
  }

  // --- the button, its menu and the launch ---------------------------------------------------

  /** Reads where a launch may run. Only the latest read's answer is kept; one that fails
   * reads as no slots, unread where slots was there before. */
  async #readTargets(): Promise<void> {
    const asked = (this.#targetsAsked += 1);
    const at = Date.now();
    const answer = await api("launcher", "api/targets");
    if (asked !== this.#targetsAsked) return;
    this.targets = answer.status === 200 ? parseTargets(answer.data) : afterNoAnswer(this.targets);
    this.#targetsAt = answer.status === 200 && !this.targets.unread ? at : 0;
  }

  #toggleRun(): void {
    this.menu = this.menu === "run" ? "" : "run";
    if (this.menu !== "run") return;
    void this.#readTargets();
    this.#readUnread();
  }

  #pickRun(run: Run): void {
    this.picked = run;
    this.menu = "";
    void this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>(".go")?.focus());
  }

  /** Whether the button can start a launch: a place, a form and a listing are there. */
  get #able(): boolean {
    return this.ready && this.fields.length > 0 && this.targets !== null;
  }

  /** The press: the preview, then the launch with its ticket or the command copied. */
  async #press(): Promise<void> {
    if (inFlight(this.flight) || !this.#able || this.targets === null) return;
    const mine = (this.#pressed += 1);
    this.#stopFollow();
    // What the button says at the press is what is sent, whatever a later read says.
    const shown = runOf(this.targets, this.picked);
    const body = launchRequest(this.fields, this.#form, this.set, shown);
    this.#sent = this.brief;
    this.menu = "";
    this.flight = PRESSED;
    let said = shown;
    if (Date.now() - this.#targetsAt > TARGETS_FRESH_MS) {
      await this.#readTargets();
      if (mine !== this.#pressed) return;
      // A slot the launch does not ask for is named by the newest read.
      const now = runOf(this.targets, this.picked);
      if (shown.target === "slot" && !shown.named && now.target === "slot" && !now.named) said = now;
    }
    const answer = previewed(await api("launcher", "api/preview", body));
    if (mine !== this.#pressed) return;
    if (answer.next === "end") {
      this.flight = answer.flight;
      return;
    }
    if (answer.next === "copy") {
      await this.#copy(mine, answer.command, answer.notes);
      return;
    }
    const sent = launched(await api("launcher", "api/launch", { ...body, ticket: answer.ticket }), said,
                          answer.notes);
    if (mine !== this.#pressed) return;
    if (sent.copy !== null) {
      await this.#copy(mine, sent.copy, answer.notes);
      return;
    }
    this.flight = sent.flight;
    if (following(this.flight)) this.#follow(mine);
  }

  async #copy(mine: number, command: string, notes: Line[]): Promise<void> {
    let done = true;
    try {
      await navigator.clipboard.writeText(command);
    } catch {
      done = false;
    }
    if (mine === this.#pressed) this.flight = copied(command, done, notes);
  }

  /** Reads the launch's status path now and then on a timer, until it ends. */
  #follow(mine: number): void {
    this.#followSince = Date.now();
    void this.#check(mine);
    this.#followTimer = setInterval(() => {
      if (Date.now() - this.#followSince > FOLLOW_FOR_MS) this.#landed(stopped(this.flight));
      else void this.#check(mine);
    }, FOLLOW_MS);
  }

  async #check(mine: number): Promise<void> {
    const status = this.flight.status;
    if (status === null || !following(this.flight)) return;
    const answer = await api("launcher", status.path);
    if (mine === this.#pressed) this.#landed(followed(this.flight, answer));
  }

  /** Takes the followed launch's next state. A launch that started empties the box, unless
   * its text was changed since the press. */
  #landed(next: Flight): void {
    if (next === this.flight) return;
    this.flight = next;
    if (!following(next)) this.#stopFollow();
    if (next.started && this.brief === this.#sent) this.brief = "";
  }

  #stopFollow(): void {
    clearInterval(this.#followTimer);
    this.#followTimer = undefined;
  }

  /** An edit of the box. The lines of a launch that has ended go with it. */
  #edit(text: string): void {
    this.brief = text;
    const next = edited(this.flight);
    if (next !== this.flight) {
      this.flight = next;
      this.#stopFollow();
      this.#pressed += 1;
    }
  }

  /** The split button at the right of the box's last line: the act, and a caret that opens
   * where it runs. Until the listing is read it says `Launch` and is off. */
  #button() {
    const busy = inFlight(this.flight);
    const run = this.targets === null ? null : runOf(this.targets, this.picked);
    const open = this.menu === "run" && this.targets !== null && run !== null;
    return html`<span class="split"><button type="button" class="go" data-target=${run?.target ?? nothing}
        ?disabled=${busy || !this.#able} @click=${() => void this.#press()}>${
        busy ? STARTING : run === null ? "Launch" : buttonWords(run)}</button><span
      class=${open ? "anchor open" : "anchor"}><button type="button" class="more" aria-label="Where it runs"
        aria-haspopup="true" aria-expanded=${open ? "true" : "false"} ?disabled=${busy || this.targets === null}
        @click=${this.#toggleRun}><span aria-hidden="true">▾</span></button>${
        open ? this.#runMenu(this.targets as Targets, run as Run) : nothing}</span></span>`;
  }

  #runMenu(targets: Targets, run: Run) {
    const menu = runMenu(targets, run);
    return html`<div class="menu run" data-menu="run">
      ${menu.slots.map((row) => this.#runRow(row))}
      ${menu.slots.length > 0 ? html`<hr>` : nothing}
      ${menu.others.map((row) => this.#runRow(row))}
    </div>`;
  }

  #runRow(row: RunRow) {
    const inner = html`<span aria-hidden="true">${row.current ? "✓" : ""}</span><span>${row.lead}</span><span
      class="d">${row.words}</span>`;
    const pick = row.run;
    if (pick === null) return html`<div class="row taken" data-run=${row.lead}>${inner}</div>`;
    return html`<button type="button" class="row" data-run=${row.lead}
      data-current=${row.current ? "true" : "false"} @click=${() => this.#pickRun(pick)}>${inner}</button>`;
  }

  /** A line's text, with the launcher's page a link where the line names it. */
  #words(line: Line) {
    const around = aroundPage(line);
    if (around === null) return line.text;
    return html`${around[0]}<a href="/m/launcher/" @click=${(event: MouseEvent) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate("launcher");
    }}>${LAUNCHER_PAGE}</a>${around[1]}`;
  }

  /** The lines under the box: the launch's, or why the launch form can't be read, then a
   * dim one while the slots can't be read. */
  #said() {
    const lines: Line[] = [...this.flight.lines, ...this.flight.notes];
    return html`<div class="said" role="status">${
      lines.length === 0 && this.unread !== ""
        ? html`<p data-line="unread">The launch form can't be read: ${this.unread}.</p>` : nothing}${
      lines.map((line) => html`<p class=${line.tone} data-line=${line.name}>${this.#words(line)}</p>${
        line.command === undefined ? nothing : html`<code data-line="command">${line.command}</code>`}`)}${
      this.targets?.unread === true ? html`<p class="dim" data-line="slots-unread">${SLOTS_UNREAD}</p>` : nothing}</div>`;
  }
}

/** Why a read of the launch form gave nothing to draw. */
function unreadWhy(answer: ApiResult, read: { detail: string | null } | null): string {
  if (answer.status === 0) return "no answer from the shell";
  if (answer.status !== 200) return `the launcher's page server answered ${answer.status}`;
  return read?.detail ?? "the launcher's page server sent something else";
}

if (!customElements.get(TAG)) customElements.define(TAG, LauncherPrompt);

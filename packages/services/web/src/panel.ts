// <moxi-services-panel>: the panel that adds a service, at the window's right edge, as the
// Projects page's Add panel is drawn. The page places it while its address says `add` or
// `adopt`, and takes it away when it closes; the panel says it wants closing with
// `PANEL_CLOSE`, from `Close`, `Cancel` or Escape.
//
// Two ways, picked at its top (`PANEL_WAY` tells the page, which writes the address):
//
// - A command to run: `Command`, with the hint that its first word is a full path and the
//   shell command that prints it; `Name`; `It` keeps running or runs on a schedule, with
//   the schedule's fields; `Project`, with projects installed; `Turn it on now`, off at
//   first, as `moxi services add` leaves a row; and under `More`, the folder, the health
//   check, the description and whether its runs launch agent sessions.
// - A unit already on this computer: `Unit`. 250 ms after the last key it reads
//   `/api/unit` and says under the field what the system says of it: found and what it
//   is, missing, or why it can't be adopted. Only the answer for what the field holds now
//   is drawn. Where the system can't tell a unit's kind, `It` asks it with none chosen, the
//   unit is read again as the kind the user picks, and Adopt waits for it. `It is` on or off, which the user must say, with what the system
//   says; `Project`; for a systemd timer the two sentences on the file adopting adds; and
//   under `More`, the health check, the description and whether its runs launch sessions.
//
// `Add service` and `Adopt unit` send the body with `PANEL_SEND`; the page posts it to
// `/api/add`, follows its job, and gives back `busy` and what the add came to (`said`).
// The values stay as typed until the page places a new panel.

import { css, html, LitElement, nothing } from "lit";
import { live } from "lit/directives/live.js";
import { api, readLater } from "/m/ui/client.js";

import type { Projects } from "./feed.ts";
import {
  addBody, adoptKind, answerNow, DAYS, emptyForm, fullPathHint, GO, KIND_WORDS, kindToAsk, moreLine, onOffSays,
  PANEL_TITLE, parseUnit, turnOnSays, UNIT_WAIT_MS, unitRoute, unitSays, WAYS, WRAPS_SAYS,
} from "./panel-model.ts";
import type { AddForm, Asked, Way } from "./panel-model.ts";
import type { Said } from "./views.ts";

const TAG = "moxi-services-panel";

/** The event the panel asks to be closed with; it carries no detail. */
export const PANEL_CLOSE = "moxi-services-panel-close";
/** The event a pick of the other way sends; its detail is the way. */
export const PANEL_WAY = "moxi-services-panel-way";
/** The event `Add service` or `Adopt unit` sends; its detail is `/api/add`'s body. */
export const PANEL_SEND = "moxi-services-panel-send";

/** The panel's width, as the Projects page's. */
export const PANEL_WIDTH_PX = 400;

const ARROW = html`<span class="v" aria-hidden="true">▾</span>`;

// The Projects page's panel, holding a form; no accent and no warning colour.
const STYLES = css`
  :host { display: contents; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  :host([hidden]) { display: none; }
  * { box-sizing: border-box; }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: 12px; overflow-wrap: anywhere; }
  .panel { position: fixed; z-index: 5; top: 0; right: 0; bottom: 0; width: ${PANEL_WIDTH_PX}px; max-width: 100vw;
           padding: 20px 20px 24px; overflow: auto; border-left: 1px solid var(--moxi-border);
           background: var(--moxi-bg); color: var(--moxi-text); font-size: 14px; line-height: 20px; }
  .ph { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; }
  .ph b { font-size: 16px; font-weight: 600; }
  button { font: inherit; color: var(--moxi-text); cursor: pointer; }
  button:disabled, input:disabled, select:disabled { cursor: not-allowed; opacity: 0.6; }
  button.lnk { padding: 0; border: 0; background: none; color: var(--moxi-text-dim); }
  button.lnk:hover:not(:disabled) { color: var(--moxi-text); }
  form { display: block; min-width: 0; }
  .f { display: block; margin: 14px 0 4px; color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; }
  input[type=text], select { display: block; width: 100%; max-width: 100%; min-width: 0; padding: 6px 10px;
                             border: 1px solid var(--moxi-border); border-radius: 7px; background: none;
                             color: var(--moxi-text); font: inherit; }
  input[type=text].mono { font-family: var(--moxi-font-mono); font-size: 12px; }
  input[type=text]:focus, select:focus { border-color: var(--moxi-border-strong); }
  .sel { position: relative; display: block; }
  .sel select { appearance: none; padding-right: 30px; cursor: pointer; }
  .sel .v { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); color: var(--moxi-text-dim);
            font-size: 13px; pointer-events: none; }
  .hint { margin-top: 5px; color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; }
  .hint b { font-weight: 400; color: var(--moxi-text); }
  /* A choice of two or three, the one taken outlined. */
  .tri { display: flex; flex-wrap: wrap; margin: 2px 0 2px -7px; font-size: 13px; line-height: 1; }
  .tri button { padding: 5px 7px; border: 1px solid transparent; border-radius: 6px; background: none;
                color: var(--moxi-text-dim); }
  .tri button[aria-checked="true"] { color: var(--moxi-text);
                                     border-color: var(--moxi-border-strong); }
  .tri button:hover:not(:disabled) { color: var(--moxi-text); }
  .way { margin-bottom: 4px; }
  .trow { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 16px; }
  .trow .d { display: block; color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; }
  /* A switch, drawn over the box it is. */
  input.sw { appearance: none; flex: none; position: relative; width: 26px; height: 14px; margin: 0;
             border: 1px solid var(--moxi-text-dim); border-radius: 7px;
             cursor: pointer; }
  input.sw::after { content: ""; position: absolute; top: 2px; left: 2px; width: 8px; height: 8px; border-radius: 50%;
                    background: var(--moxi-text-dim); }
  input.sw:checked { background: var(--moxi-text); border-color: var(--moxi-text); }
  input.sw:checked::after { left: 14px; background: var(--moxi-bg); }
  label.check { display: flex; align-items: baseline; gap: 8px; margin-top: 12px; }
  .days { display: flex; flex-wrap: wrap; gap: 2px 12px; }
  .days label { display: flex; align-items: center; gap: 4px; }
  button.fold { display: flex; justify-content: space-between; gap: 12px; width: 100%; margin-top: 16px; padding: 12px 0 0;
                border: 0; border-top: 1px solid var(--moxi-border); border-radius: 0; background: none; text-align: left; }
  button.fold > span { white-space: nowrap; }
  button.fold .ar { display: inline-block; width: 16px; color: var(--moxi-text-dim); font-size: 13px; }
  button.fold .d { min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--moxi-text-dim); }
  .pbtns { display: flex; justify-content: flex-end; align-items: center; gap: 18px; margin-top: 22px; padding-top: 14px;
           border-top: 1px solid var(--moxi-border); }
  button.btn { padding: 5px 13px; border: 1px solid var(--moxi-border-strong); border-radius: 7px; background: none; }
  button.btn:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  .said { display: grid; gap: 2px; margin-top: 12px; padding-left: 8px; border-left: 3px solid var(--moxi-border-strong);
          font-size: 13px; line-height: 18px; overflow-wrap: anywhere; }
  .said.muted { color: var(--moxi-text-dim); }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
`;

type Text = "name" | "command" | "unit" | "every" | "at" | "cwd" | "health" | "doc";

export class ServicesPanel extends LitElement {
  static styles = STYLES;

  static properties = {
    way: { type: String },
    busy: { type: Boolean },
    said: { attribute: false },
    projects: { attribute: false },
    withProjects: { attribute: false },
  };

  /** The way the address names. */
  way: Way = "add";
  /** An act or the add is on its way or its job runs. */
  busy = false;
  /** What the last add came to, or the line it still needs. */
  said: Said | null = null;
  /** The projects a row can name, with why there are none; null while unread. */
  projects: { value: Projects | null; failure: string | null } | null = null;
  /** Whether projects is installed, so the panel asks for one. */
  withProjects = false;

  #form: AddForm = emptyForm();
  #more = false;
  #asked: Asked | null = null;
  /** The supervisor can't tell a unit's kind, as an answer said: `It` is asked. */
  #asks = false;
  /** Cancels the unit's read waiting to be sent. */
  #cancelUnit = (): void => {};
  #connected = 0;

  connectedCallback(): void {
    super.connectedCallback();
    this.#connected += 1;
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#connected += 1;
    this.#cancelUnit();
  }

  protected willUpdate(changed: Map<PropertyKey, unknown>): void {
    if (changed.has("way") && this.#form.way !== this.way) this.#form = { ...this.#form, way: this.way };
  }

  protected firstUpdated(): void {
    this.focus();
  }

  /** The way's first field takes the focus. */
  override focus(): void {
    const field = this.#form.way === "adopt" ? "unit" : "command";
    this.renderRoot.querySelector<HTMLInputElement>(`input[name="${field}"]`)?.focus();
  }

  #close(): void {
    this.dispatchEvent(new Event(PANEL_CLOSE));
  }

  #keys = (event: KeyboardEvent): void => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    this.#close();
  };

  #pick(way: Way): void {
    if (way === this.#form.way) return;
    this.#form = { ...this.#form, way };
    this.dispatchEvent(new CustomEvent<Way>(PANEL_WAY, { detail: way }));
    this.requestUpdate();
    void this.updateComplete.then(() => this.focus());
  }

  #set(change: Partial<AddForm>): void {
    const before = this.#form;
    this.#form = { ...before, ...change };
    // A new unit, or a new kind where the system asks it, is read again.
    if (change.unit !== undefined || (this.#asks && change.adoptAs !== undefined && change.adoptAs !== before.adoptAs)) {
      this.#readUnit(UNIT_WAIT_MS);
    }
    this.requestUpdate();
  }

  #text(name: Text) {
    return (event: Event): void => this.#set({ [name]: (event.target as HTMLInputElement).value });
  }

  #day(day: string, on: boolean): void {
    const days = this.#form.days.filter((d) => d !== day);
    this.#set({ days: on ? [...days, day] : days });
  }

  /** Reads the unit typed after the wait, unless the field moves on first. The page root
   * counts the read as out from now, so a press waits for what it fills in. */
  #readUnit(waitMs: number): void {
    this.#cancelUnit();
    const unit = this.#form.unit.trim();
    if (unit === "") return;
    const kind = kindToAsk(this.#form, this.#asks);
    this.#cancelUnit = readLater(() => this.#askUnit(unit, kind), waitMs);
  }

  async #askUnit(unit: string, kind: "daemon" | "timer" | null): Promise<void> {
    const mine = this.#connected;
    let answer;
    try {
      answer = await api("services", unitRoute(unit, kind));
    } catch {
      return;
    }
    if (mine !== this.#connected || this.#form.unit.trim() !== unit) return;
    const read = answer.status === 200 ? parseUnit(answer.data) : null;
    if (read === null) return;
    this.#asked = { unit, kind, answer: read };
    // The kind is asked with none chosen: the first read went without one, so the system's
    // default it was read as is no fact of the unit's.
    if (read.asks_kind) this.#asks = true;
    this.requestUpdate();
  }

  #submit = (event: Event): void => {
    event.preventDefault();
    if (this.busy) return;
    const built = addBody(this.#form, answerNow(this.#asked, this.#form), this.#asks);
    this.dispatchEvent(new CustomEvent(PANEL_SEND, { detail: built }));
  };

  render() {
    const form = this.#form;
    const adopt = form.way === "adopt";
    const said = this.said;
    return html`<aside class="panel" aria-label=${PANEL_TITLE} data-part="panel" data-panel=${form.way} @keydown=${this.#keys}>
      <div class="ph"><b>${PANEL_TITLE}</b><button type="button" class="lnk" data-action="close"
        @click=${() => this.#close()}>Close</button></div>
      ${this.#choice("way", "How it runs", WAYS.map(({ way, label }) => ({ value: way, label })), form.way,
        (way) => this.#pick(way as Way))}
      <form @submit=${this.#submit} aria-busy=${this.busy ? "true" : "false"}>
        ${adopt ? this.#adoptPart(form) : this.#addPart(form)}
        <div class="pbtns">
          <button type="button" class="lnk" data-action="cancel" @click=${() => this.#close()}>Cancel</button>
          <button type="submit" class="btn" data-action="go" ?disabled=${this.busy}>${GO[form.way]}</button>
        </div>
        ${said !== null ? html`<div class="said ${said.tone === "muted" ? "muted" : ""}" data-line="said"
          data-tone=${said.tone} role="status">${said.lines.map((line) => html`<p>${line}</p>`)}</div>` : nothing}
      </form>
    </aside>`;
  }

  /** A choice of two or three as a row of radio buttons, the one taken outlined. */
  #choice(name: string, label: string, options: { value: string; label: string }[], value: string,
          pick: (value: string) => void) {
    return html`<div class="tri ${name}" role="radiogroup" aria-label=${label} data-choice=${name}>${options.map((option) =>
      html`<button type="button" role="radio" aria-checked=${option.value === value ? "true" : "false"}
        data-value=${option.value} ?disabled=${this.busy} @click=${() => pick(option.value)}>${option.label}</button>`)}</div>`;
  }

  #input(name: Text, label: string, form: AddForm, mono = false) {
    return html`<label class="f" for=${`s-${name}`}>${label}</label><input id=${`s-${name}`} type="text" name=${name}
      class=${mono ? "mono" : ""} autocomplete="off" spellcheck="false" ?disabled=${this.busy}
      .value=${live(form[name])} @input=${this.#text(name)}>`;
  }

  #addPart(form: AddForm) {
    const hint = fullPathHint(form.command);
    const timer = form.kind === "timer";
    return html`${this.#input("command", "Command", form, true)}
      <p class="hint" data-line="full-path">${hint.text} <b>${hint.command}</b> in your terminal prints it.</p>
      ${this.#input("name", "Name", form)}
      <p class="f" id="s-it">It</p>
      ${this.#choice("kind", "It", KIND_WORDS.map(({ kind, label }) => ({ value: kind, label })), form.kind,
        (kind) => this.#set({ kind: kind as AddForm["kind"] }))}
      ${timer ? this.#schedulePart(form) : nothing}
      ${this.#projectPart(form)}
      <div class="trow" data-part="turn-on"><span><label for="s-enable">Turn it on now</label><span class="d"
          id="s-enable-d" data-line="turn-on">${turnOnSays(form.kind)}</span></span>
        <input id="s-enable" class="sw" type="checkbox" role="switch" name="enable" aria-describedby="s-enable-d"
          ?disabled=${this.busy} .checked=${live(form.enable)}
          @change=${(event: Event) => this.#set({ enable: (event.target as HTMLInputElement).checked })}></div>
      ${this.#morePart(form, true)}`;
  }

  #schedulePart(form: AddForm) {
    return html`${this.#choice("schedule", "Schedule", [{ value: "every", label: "every" }, { value: "at", label: "at times" }],
        form.schedule, (schedule) => this.#set({ schedule: schedule as AddForm["schedule"] }))}
      ${form.schedule === "every" ? this.#input("every", "Every, such as 5m or 2h", form)
        : html`${this.#input("at", "At, 24-hour times such as 09:00, 17:30", form)}
          <p class="f">Days (none for every day)</p>
          <div class="days" role="group" aria-label="Days">${DAYS.map((day) => html`<label><input type="checkbox" name="days"
            value=${day} ?disabled=${this.busy} .checked=${live(form.days.includes(day))}
            @change=${(event: Event) => this.#day(day, (event.target as HTMLInputElement).checked)}>${day}</label>`)}</div>`}`;
  }

  #adoptPart(form: AddForm) {
    const answer = answerNow(this.#asked, form);
    const says = answer === null ? null : unitSays(answer);
    const asks = this.#asks;
    return html`${this.#input("unit", "Unit", form, true)}
      ${says !== null ? html`<p class="hint" data-line="found" data-refused=${says.refused ? "true" : "false"}>${says.line}</p>`
        : nothing}
      ${asks ? html`<p class="f">It</p>${this.#choice("kind", "It", KIND_WORDS.map(({ kind, label }) => ({ value: kind, label })),
        form.adoptAs, (kind) => this.#set({ adoptAs: kind as AddForm["adoptAs"] }))}` : nothing}
      ${this.#input("name", "Name", form)}
      <p class="f">It is</p>
      ${this.#choice("on", "It is", [{ value: "on", label: "on" }, { value: "off", label: "off" }], form.adoptOn,
        (on) => this.#set({ adoptOn: on as AddForm["adoptOn"] }))}
      <p class="hint" data-line="on-off">${onOffSays(answer)}</p>
      ${this.#projectPart(form)}
      ${answer?.wraps && adoptKind(form, answer, asks) === "timer" && answer.refused === null
        ? html`<p class="hint" data-line="wraps">${WRAPS_SAYS}</p>` : nothing}
      ${this.#morePart(form, false)}`;
  }

  #projectPart(form: AddForm) {
    if (!this.withProjects) return nothing;
    const value = this.projects?.value ?? null;
    const reason = value?.reason ?? (this.projects?.failure ? `they can't be listed: ${this.projects.failure}` : null);
    return html`<label class="f" for="s-project">Project</label><span class="sel"><select id="s-project" name="project"
        ?disabled=${this.busy} .value=${live(form.project)}
        @change=${(event: Event) => this.#set({ project: (event.target as HTMLSelectElement).value })}>
        <option value="" ?selected=${form.project === ""}>none</option>
        ${(value?.projects ?? []).map((project) => html`<option value=${project.key}
          ?selected=${project.key === form.project}>${project.key}</option>`)}</select>${ARROW}</span>
      ${reason !== null ? html`<p class="hint" data-line="projects">Projects: ${reason}</p>` : nothing}`;
  }

  /** `More`, folded: the folder (a command's), the health check, the description and
   * whether its runs launch agent sessions. */
  #morePart(form: AddForm, folder: boolean) {
    return html`<button type="button" class="fold" data-action="more" aria-expanded=${this.#more ? "true" : "false"}
        @click=${() => { this.#more = !this.#more; this.requestUpdate(); }}><span><span class="ar" aria-hidden="true">${
        this.#more ? "▾" : "▸"}</span>More</span><span class="d" data-line="more">${moreLine(form.way)}</span></button>
      ${this.#more ? html`${folder ? this.#input("cwd", "Folder, a full path", form, true) : nothing}
        ${this.#input("health", "Health check, a command whose exit 0 is healthy", form, true)}
        ${this.#input("doc", "Description, one line", form)}
        <label class="check"><input type="checkbox" name="launches_session" ?disabled=${this.busy}
          .checked=${live(form.launchesSession)}
          @change=${(event: Event) => this.#set({ launchesSession: (event.target as HTMLInputElement).checked })}><span>Its
          runs launch agent sessions <span class="hint">(they spend usage)</span></span></label>` : nothing}`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ServicesPanel);

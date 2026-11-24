// <moxi-statusline-page route="...">: statusline's page. The line's items in two rows,
// each with a switch and up and down buttons, the items left off, the line's options, a
// preview of the line rendered by the real command over a sample input, and Claude
// Code's status line with the acts on it.
//
// Every change goes into a draft; the preview renders the draft at once. The config is
// written only when the person presses Save, and Discard drops the draft.

import { css, html, LitElement, nothing } from "lit";
import { live } from "lit/directives/live.js";
import { repeat } from "lit/directives/repeat.js";
import { api, readLater } from "/m/ui/client.js";

import { moveDown, moveUp, turnOff, turnOn } from "./draft.ts";
import type { Key, Values } from "./draft.ts";
import { classes, runs } from "./sgr.ts";
import { Sync } from "./sync.ts";
import { drawnBy, HOLD, OPTION_LABELS, pageView, timeText } from "./views.ts";
import type { ItemView, PageView } from "./views.ts";

const TAG = "moxi-statusline-page";
const MODULE = "statusline";
/** Milliseconds a change waits for the next before the draft is previewed. */
const SETTLE_MS = 120;

const STYLES = css`
  :host { display: block; min-width: 0; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; }
  h1 { margin: 0 0 var(--moxi-gap); font-size: 1.4em; }
  h2 { margin: 0; font-size: 1.1em; }
  h3 { margin: 0; font-size: 1em; }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .lede { margin-bottom: var(--moxi-gap); padding-left: 8px; border-left: 3px solid var(--moxi-accent); color: var(--moxi-text-dim); }
  .dim { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .problem { padding: 8px 12px; border-radius: var(--moxi-radius); background: var(--moxi-error-soft); color: var(--moxi-error); }
  .message { padding: 6px 10px; border-radius: var(--moxi-radius); font-size: var(--moxi-font-size-sm); }
  .message.ok { background: var(--moxi-add-soft); }
  .message.warn { background: var(--moxi-warn-soft); color: var(--moxi-warn); }
  .message.error { background: var(--moxi-error-soft); color: var(--moxi-error); }
  section { display: grid; gap: var(--moxi-gap); min-width: 0; margin-top: var(--moxi-gap); padding: 12px;
            border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius-lg); background: var(--moxi-bg-raised); }
  .head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px var(--moxi-gap); min-width: 0; }
  .bar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px var(--moxi-gap); min-width: 0; }
  .line { margin: 0; padding: 8px 10px; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken);
          font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); white-space: pre-wrap;
          overflow-wrap: anywhere; min-height: 2.6em; }
  .line[data-state=pending] { opacity: 0.6; }
  .line .b { font-weight: 700; }
  .line .d { opacity: 0.65; }
  .c31 { color: var(--moxi-error); }
  .c32 { color: var(--moxi-ok); }
  .c33 { color: var(--moxi-warn); }
  .c34 { color: var(--moxi-accent); }
  .c35 { color: var(--moxi-launching); }
  .c36 { color: var(--moxi-series-4); }
  .c37 { color: var(--moxi-text); }
  ul { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; min-width: 0; }
  li { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; min-width: 0; padding: 4px 6px;
       border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius); background: var(--moxi-bg); }
  li .what { flex: 1 1 10em; min-width: 0; display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 8px; }
  li .moves { display: flex; gap: 4px; }
  .empty { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); padding: 4px 6px; }
  .options { display: grid; gap: 8px; min-width: 0; }
  .option { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; min-width: 0; }
  .option .name { flex: 1 1 12em; min-width: 0; display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 8px; }
  button, input { font: inherit; color: var(--moxi-text); max-width: 100%; }
  button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg-raised); cursor: pointer; }
  button.go { background: var(--moxi-accent); border-color: var(--moxi-accent); color: var(--moxi-bg); }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button.go:hover:not(:disabled) { background: var(--moxi-accent); }
  button:disabled, input:disabled { cursor: not-allowed; opacity: 0.6; }
  button.move { padding: 2px 8px; }
  button.key { padding: 0 4px; border: none; background: none; color: var(--moxi-text-dim);
               font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; text-align: left; }
  button.key:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  .switch { min-width: 3.4em; text-align: left; }
  .switch::before { content: ""; display: inline-block; width: 0.8em; height: 0.8em; margin-right: 6px;
                    border: 1px solid var(--moxi-border-strong); border-radius: 50%; vertical-align: -0.05em; }
  .switch.on::before { background: var(--moxi-selected); border-color: var(--moxi-selected); }
  input[type=text] { flex: 0 1 8em; min-width: 0; padding: 2px 6px; border: 1px solid var(--moxi-border-strong);
                     border-radius: var(--moxi-radius); background: var(--moxi-bg); font-family: var(--moxi-font-mono); }
  input[type=number] { width: 5em; padding: 2px 6px; border: 1px solid var(--moxi-border-strong);
                       border-radius: var(--moxi-radius); background: var(--moxi-bg); }
  .notes { display: grid; gap: 2px; }
`;

export class StatuslinePage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";
  #sync: Sync | null = null;
  /** Cancels the preview waiting to be asked for. */
  #cancelPreview = (): void => {};
  #pending = false;
  #copied: string | null = null;
  #focused = (): void => {
    void this.#sync?.refocus();
  };
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#sync?.refocus();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const sync = new Sync((path, body) => api(MODULE, path, body), () => {
      this.#pending = false;
      this.requestUpdate();
    });
    this.#sync = sync;
    void sync.read().then(() => sync.previewNow());
    window.addEventListener("focus", this.#focused);
    document.addEventListener("visibilitychange", this.#shown);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#sync = null;
    this.#cancelPreview();
    window.removeEventListener("focus", this.#focused);
    document.removeEventListener("visibilitychange", this.#shown);
  }

  /** Puts a change in the draft and previews it once changes settle. */
  #change<K extends Key>(key: K, value: Values[K]): void {
    const sync = this.#sync;
    if (sync?.draft == null) return;
    sync.draft.set(key, value);
    sync.message = null;
    this.#pending = true;
    this.requestUpdate();
    // The page root counts the preview as out from the change, so a press waits for the
    // lines it draws.
    this.#cancelPreview();
    this.#cancelPreview = readLater(() => sync.previewNow(), SETTLE_MS);
  }

  #show(edit: (show: string[], drawn: (item: string) => boolean) => string[]): void {
    const sync = this.#sync;
    if (sync?.draft == null || sync.state === null) return;
    this.#change("show", edit(sync.draft.values().show, drawnBy(sync.state)));
  }

  async #copy(key: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(key);
      this.#copied = key;
    } catch {
      this.#copied = `!${key}`;
    }
    this.requestUpdate();
  }

  render() {
    const sync = this.#sync;
    if (sync === null || sync.gone) return nothing;
    const head = html`<h1>Status line</h1>
      <p class="lede">The line Claude Code shows under its prompt: pick the items, put them in order and see it as it would look. Nothing is written until you press Save.</p>`;
    if (sync.state === null || sync.draft === null) {
      return html`${head}<div data-state=${sync.failure ? "failed" : "loading"}>${sync.failure
        ? html`<p class="problem">${sync.failure}</p>` : html`<p class="dim">Reading the config…</p>`}</div>`;
    }
    const view = pageView(sync.state, sync.draft);
    return html`${head}
      <div data-state="up">
        ${view.problem ? html`<p class="problem" data-problem="config">${view.problem}</p>` : nothing}
        ${this.#preview(sync)}
        ${this.#lists(view, sync)}
        ${this.#options(view)}
        ${this.#claude(view, sync)}
      </div>`;
  }

  #key(key: string) {
    const said = this.#copied === key ? "copied" : this.#copied === `!${key}` ? "select it to copy" : null;
    return html`<button class="key" type="button" data-copy=${key} title="Copy ${key}"
      @click=${() => void this.#copy(key)}>${key}</button>${said ? html`<span class="dim">${said}</span>` : nothing}`;
  }

  #preview(sync: Sync) {
    const shown = sync.preview;
    // A config that can't be read shows the line Claude Code prints for it, marked apart
    // from a rendered preview.
    const state = sync.state?.problem ? "unreadable" : this.#pending ? "pending" : sync.refused ? "refused"
      : shown?.problem ? "failed" : shown ? "up" : "pending";
    return html`<section data-part="preview">
      <h2>Preview</h2>
      <pre class="line" data-state=${state}>${sync.refused === null && shown !== null
        ? shown.lines.map((line, i) => html`${i ? "\n" : ""}${runs(line).map((run) => html`<span class=${classes(run)}>${run.text}</span>`)}`)
        : nothing}</pre>
      ${sync.refused ? html`<p class="problem" data-problem="draft">This draft can't be saved: ${sync.refused}</p>` : nothing}
      ${shown?.problem ? html`<p class="problem" data-problem="preview">${shown.problem}</p>` : nothing}
      ${sync.refused === null && shown !== null ? html`
        <p class="dim" data-time>${timeText(shown.ms)}</p>
        ${shown.notes.length ? html`<div class="notes">${shown.notes.map((note) => html`<p class="dim" data-note>${note}</p>`)}</div>` : nothing}` : nothing}
    </section>`;
  }

  #lists(view: PageView, sync: Sync) {
    const editable = view.editable && !sync.busy;
    const [first, second] = view.rows;
    return html`<section data-part="lists">
      <div class="head"><h2>The line</h2>${this.#key("show")}</div>
      ${view.dirty ? html`<div class="bar" data-part="draft">
        <span data-count>${view.countText}</span>
        <button class="go" data-action="save" ?disabled=${!editable || sync.refused !== null} @click=${() => void sync.save()}>Save</button>
        <button data-action="discard" ?disabled=${sync.busy} @click=${() => sync.discard()}>Discard</button>
      </div>` : nothing}
      ${sync.message ? html`<p class="message ${sync.message.kind}" data-message=${sync.message.kind}>${sync.message.text}</p>` : nothing}
      <h3>Row 1</h3>
      ${this.#list(first, "row1", true, editable)}
      <h3>Row 2</h3>
      ${second.length ? this.#list(second, "row2", true, editable)
        : html`<p class="empty" data-list="row2">Empty. Move an item down from the end of row 1 to start it.</p>`}
      <h3>Off</h3>
      ${view.off.length ? this.#list(view.off, "off", false, editable)
        : html`<p class="empty" data-list="off">Every item is on the line.</p>`}
    </section>`;
  }

  #list(items: ItemView[], name: string, on: boolean, editable: boolean) {
    return html`<ul data-list=${name}>${repeat(items, (item) => item.name, (item) => html`
      <li data-item=${item.name}>
        <button class="switch ${on ? "on" : ""}" role="switch" aria-checked=${on ? "true" : "false"}
          aria-label="${on ? "Turn off" : "Turn on"} ${item.label}" ?disabled=${!editable}
          @click=${() => this.#show((show, drawn) => on ? turnOff(show, item.name, drawn) : turnOn(show, item.name, drawn))}>${on ? "On" : "Off"}</button>
        <span class="what"><span>${item.label}</span>${item.detail ? html`<span class="dim">${item.module ? item.detail : html`<code>${item.detail}</code>`}</span>` : nothing}</span>
        ${on ? html`<span class="moves">
          <button class="move" data-action="up" aria-label="Move ${item.label} up" ?disabled=${!editable || !item.up}
            @click=${() => this.#show((show, drawn) => moveUp(show, item.name, drawn))}>↑</button>
          <button class="move" data-action="down" aria-label="Move ${item.label} down" ?disabled=${!editable || !item.down}
            @click=${() => this.#show((show, drawn) => moveDown(show, item.name, drawn))}>↓</button>
        </span>` : nothing}
      </li>`)}</ul>`;
  }

  #number(key: "dir.parts" | "context.warn_at" | "context.alert_at", value: number, editable: boolean, max: number) {
    return html`<div class="option" data-option=${key}>
      <span class="name"><span>${OPTION_LABELS[key]}</span>${this.#key(key)}</span>
      <input type="number" min="1" max=${max} step="1" .value=${live(String(value))} ?disabled=${!editable}
        aria-label=${OPTION_LABELS[key]}
        @change=${(event: Event) => {
          const input = event.target as HTMLInputElement;
          const number = Number(input.value);
          if (input.value.trim() === "" || !Number.isInteger(number)) {
            input.value = String(value);
            return;
          }
          this.#change(key, number);
        }}>
    </div>`;
  }

  #options(view: PageView) {
    const editable = view.editable && !(this.#sync?.busy ?? false);
    const v = view.values;
    return html`<section data-part="options">
      <h2>Options</h2>
      <div class="options">
        <div class="option" data-option="separator">
          <span class="name"><span>${OPTION_LABELS.separator}</span>${this.#key("separator")}</span>
          <input type="text" .value=${live(v.separator)} ?disabled=${!editable} aria-label=${OPTION_LABELS.separator}
            @input=${(event: Event) => this.#change("separator", (event.target as HTMLInputElement).value)}>
        </div>
        <div class="option" data-option="color">
          <span class="name"><span>${OPTION_LABELS.color}</span>${this.#key("color")}</span>
          <button class="switch ${v.color ? "on" : ""}" role="switch" aria-checked=${v.color ? "true" : "false"}
            aria-label=${OPTION_LABELS.color} ?disabled=${!editable}
            @click=${() => this.#change("color", !v.color)}>${v.color ? "On" : "Off"}</button>
        </div>
        ${this.#number("dir.parts", v["dir.parts"], editable, 20)}
        ${this.#number("context.warn_at", v["context.warn_at"], editable, 100)}
        ${this.#number("context.alert_at", v["context.alert_at"], editable, 100)}
      </div>
    </section>`;
  }

  #claude(view: PageView, sync: Sync) {
    const c = view.claude;
    const off = c.held || sync.busy;
    return html`<section data-part="claude">
      <h2>Claude Code's status line</h2>
      ${c.problem ? html`<p class="problem" data-problem="claude">${c.problem}</p>` : nothing}
      <p data-claude>${c.text}${c.command ? html` <code>${c.command}</code>` : nothing}</p>
      ${c.useShown || c.restoreShown ? html`<div class="bar">
        ${c.useShown ? html`<button data-action="use" ?disabled=${off} @click=${() => void sync.act("use")}>Use Moxi's line</button>` : nothing}
        ${c.restoreShown ? html`<button data-action="restore" ?disabled=${off} @click=${() => void sync.act("restore")}>Put my old line back</button>` : nothing}
        ${c.held ? html`<span class="dim" data-held>${HOLD}</span>` : nothing}
      </div>` : nothing}
    </section>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, StatuslinePage);

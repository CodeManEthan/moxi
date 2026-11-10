// <moxi-ui-health>: the shell's own row on Home. It says in one line whether the doctor's
// checks pass, from the run the shell keeps (the topic `ui.checks`), and reports that line
// to Home with a `moxi-home-row` event. Home sets the attribute `open` to show it in
// place: then it lists only the checks that fail or warn, each with its fix, and a foot
// with the way to the full list and `Check again`.
//
// It asks the shell for the kept run each time it is connected and each time the document
// is shown again; that read is what starts a new run when the kept one is old. On the
// phone it shows no detail, no fix and no act: the gateway cuts those texts, and a run is
// started at the desk.

import { css, html, LitElement, nothing } from "lit";
import { api, gateway, navigate, subscribe } from "/m/ui/client.js";

import { clock, healthEntries, healthLine, healthRest, runFailed } from "./health-model.ts";
import type { Checks, HealthEntry } from "./health-model.ts";

const TAG = "moxi-ui-health";
/** How long `Copied` stands in for `Copy`. */
const COPIED_MS = 1_500;

function isChecks(data: any): data is Checks {
  return typeof data === "object" && data !== null && Array.isArray(data.rows);
}

export class HealthRow extends LitElement {
  static styles = css`
    :host { display: block; min-width: 0; font-size: 14px; }
    * { box-sizing: border-box; }
    .check { padding: 7px 0; }
    .line { display: flex; justify-content: space-between; align-items: baseline; gap: 18px; }
    .line > :first-child { min-width: 0; overflow-wrap: anywhere; }
    .line > :last-child { flex: none; }
    .first { line-height: 20px; }
    .more { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
    .failing { font-weight: 600; }
    code { font: 12px/18px var(--moxi-font-mono); color: var(--moxi-text); }
    .foot { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0 18px; padding: 8px 0 3px;
            font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
    .sep { margin: 0 8px; }
    a { color: var(--moxi-text); text-decoration: none; }
    button { padding: 0; border: 0; background: none; color: inherit; font: inherit; cursor: pointer; }
    button.act { color: var(--moxi-text); }
    a:hover, button:hover { text-decoration: underline; }
    :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  `;

  static properties = {
    open: { type: Boolean, reflect: true },
    value: { state: true },
    asking: { state: true },
    copied: { state: true },
  };

  open = false;
  value: Checks | null = null;
  /** True from `Check again` until the shell says the run began or refused it. */
  asking = false;
  copied: string | null = null;
  #stop: (() => void) | null = null;
  #timer: ReturnType<typeof setTimeout> | undefined;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#read();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#stop = subscribe("ui", "checks", (data) => this.#take(data));
    document.addEventListener("visibilitychange", this.#shown);
    this.#report();
    void this.#read();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#stop?.();
    this.#stop = null;
    document.removeEventListener("visibilitychange", this.#shown);
    clearTimeout(this.#timer);
  }

  #take(data: unknown): void {
    if (!isChecks(data)) return;
    this.value = data;
    this.#report();
  }

  /** Tells Home the row's line. */
  #report(): void {
    this.dispatchEvent(new CustomEvent("moxi-home-row", {
      detail: healthLine(this.value, !gateway()), bubbles: true, composed: true,
    }));
  }

  /** The kept run, from the route that starts a new one when it is old. */
  async #read(): Promise<void> {
    const answer = await api("ui", "api/checks");
    if (answer.status === 200 && this.isConnected) this.#take(answer.data);
  }

  async #again(): Promise<void> {
    this.asking = true;
    const answer = await api("ui", "api/checks/run", {});
    this.asking = false;
    if (answer.status === 200) this.#take(answer.data);
  }

  async #copy(text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    this.copied = text;
    clearTimeout(this.#timer);
    this.#timer = setTimeout(() => { this.copied = null; }, COPIED_MS);
  }

  render() {
    if (!this.open || this.value === null || this.value.at === null) return nothing;
    const value = this.value;
    const desk = !gateway();
    const stamp = clock(value.at);
    const foot = [healthRest(value), stamp ? `checked ${stamp}` : ""].filter(Boolean);
    const busy = value.running || this.asking;
    return html`
      ${runFailed(value) && value.error ? html`<div class="check"><div class="line first">
        <span>${value.error}</span><span></span></div></div>` : nothing}
      ${healthEntries(value).map((entry) => (desk ? this.#entry(entry) : this.#short(entry)))}
      <div class="foot">
        <span><a href="/m/ui/modules" @click=${this.#modules}>All checks on Modules</a></span>
        <span>${foot.map((text, at) => html`${at ? html`<span class="sep">·</span>` : nothing}${text}`)}${desk
          ? html`${foot.length ? html`<span class="sep">·</span>` : nothing}${busy
            ? html`<span role="status">Checking…</span>`
            : html`<button type="button" class="act" @click=${() => this.#again()}>Check again</button>`}`
          : nothing}</span>
      </div>`;
  }

  #entry(entry: HealthEntry) {
    const where = `${entry.module} · ${entry.name}`;
    return html`<div class="check" data-check=${`${entry.module}.${entry.name}`}>
      <div class="line first"><span>${entry.detail ?? where}</span>${this.#state(entry)}</div>
      ${entry.detail ? html`<div class="line more"><span>${where}</span><span></span></div>` : nothing}
      ${entry.fix ? html`<div class="line more"><span><code>${entry.fix}</code></span>
        <button type="button" aria-label=${`Copy the fix for ${entry.name}`}
          @click=${() => this.#copy(entry.fix as string)}>${this.copied === entry.fix ? "Copied" : "Copy"}</button></div>` : nothing}
    </div>`;
  }

  /** The phone's entry: where the check is and how it stands, and no text of the check's
   * own, which can hold a path. */
  #short(entry: HealthEntry) {
    return html`<div class="check" data-check=${`${entry.module}.${entry.name}`}>
      <div class="line first"><span>${entry.module} · ${entry.name}</span>${this.#state(entry)}</div>
    </div>`;
  }

  #state(entry: HealthEntry) {
    return html`<span class=${entry.failing ? "failing" : ""}>${entry.failing ? "failing" : "warning"}</span>`;
  }

  #modules = (event: MouseEvent): void => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate("ui", "modules");
  };
}

if (!customElements.get(TAG)) customElements.define(TAG, HealthRow);

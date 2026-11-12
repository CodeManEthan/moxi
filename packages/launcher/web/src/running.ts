// <moxi-launcher-running>: Home's Sessions row. It says in one line how many launched
// sessions run now, and reports that line to Home with a `moxi-home-row` event; with
// nothing running it reports null and Home shows no row. Home sets the attribute `open` to
// show it in place: then it lists the first four sessions in the row's order, each as a
// `<moxi-ui-row>` with `Open`, and `All sessions (N)` under them.
//
// It reads `/api/running` each time it is connected and each time the document is shown
// again, and at the desk follows the topic `running`. On the phone it holds itself open
// and its rows carry no time, no message and no act.
//
// `Open` goes to the session's transcript on sessions' page, and `All sessions` to that
// page's list; without sessions installed both go to the launcher's page. The row's words
// are worked out in `running-model.ts`.

import { css, html, LitElement, nothing } from "lit";
import type { PropertyValues } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { api, gateway, installed, navigate, subscribe } from "/m/ui/client.js";

import { allLabel, allPlace, hrefOf, openPlace, parseRunning, rowAttrs, rowReport, shownSessions } from "./running-model.ts";
import type { Place, Running, RunningEntry } from "./running-model.ts";

const TAG = "moxi-launcher-running";

export class LauncherRunning extends LitElement {
  static styles = css`
    :host { display: block; min-width: 0; }
    * { box-sizing: border-box; }
    a { color: var(--moxi-text-dim); text-decoration: none; }
    a:hover { text-decoration: underline; }
    :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
    .foot { padding: 6px 0 2px; font-size: 13px; line-height: 18px; }
  `;

  static properties = {
    open: { type: Boolean, reflect: true },
    value: { state: true },
  };

  open = false;
  value: Running | null = null;
  #stop: (() => void) | null = null;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#read();
  };

  connectedCallback(): void {
    super.connectedCallback();
    if (!gateway()) this.#stop = subscribe("launcher", "running", (data) => this.#take(data));
    document.addEventListener("visibilitychange", this.#shown);
    void this.#read();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#stop?.();
    this.#stop = null;
    document.removeEventListener("visibilitychange", this.#shown);
  }

  protected updated(changed: PropertyValues<this>): void {
    if (changed.has("open") || changed.has("value")) this.#report();
  }

  #take(data: unknown): void {
    const value = parseRunning(data);
    if (value !== null) this.value = value;
  }

  async #read(): Promise<void> {
    const answer = await api("launcher", "api/running");
    if (answer.status === 200 && this.isConnected) this.#take(answer.data);
  }

  /** Tells Home the row's line. */
  #report(): void {
    this.dispatchEvent(new CustomEvent("moxi-home-row", {
      detail: rowReport(this.value, { desk: !gateway(), open: this.open }), bubbles: true, composed: true,
    }));
  }

  render() {
    const value = this.value;
    if (!this.open || value === null || value.state !== "up" || value.count === 0) return nothing;
    const desk = !gateway();
    const sessions = installed("sessions");
    const all = allPlace(sessions);
    return html`
      ${repeat(shownSessions(value), (entry) => entry.request_id, (entry) => this.#row(entry, desk, sessions))}
      <div class="foot"><a href=${hrefOf(all)} @click=${(event: MouseEvent) => this.#go(event, all)}>${allLabel(value)}</a></div>`;
  }

  #row(entry: RunningEntry, desk: boolean, sessions: boolean) {
    const row = rowAttrs(entry, desk);
    const opens = openPlace(entry, sessions);
    return html`<moxi-ui-row data-request=${entry.request_id} name=${row.name} where=${row.where ?? nothing}
        fact=${row.fact ?? nothing} ?fact-heavy=${row.factHeavy} lead=${row.lead ?? nothing}
        ?lead-heavy=${row.leadHeavy} time=${row.time ?? nothing} said-by=${row.saidBy ?? nothing}
        rest=${row.rest ?? nothing}>${desk
      ? html`<a slot="acts" href=${hrefOf(opens)} @click=${(event: MouseEvent) => this.#go(event, opens)}>Open</a>`
      : nothing}</moxi-ui-row>`;
  }

  #go(event: MouseEvent, where: Place): void {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(where.module, where.route);
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, LauncherRunning);

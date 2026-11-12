// <moxi-launcher-running>: Home's Sessions row. It says in one line how many launched
// sessions run now, and reports that line to Home with a `moxi-home-row` event; with
// nothing running it reports null and Home shows no row. Home sets the attribute `open` to
// show it in place: then it lists the first four sessions in the row's order, each as a
// `<moxi-ui-row>` with `Open`, and `All sessions (N)` under them.
//
// At the desk an uncovered session that a press would cover also has `Keep warm`, before
// `Open`. A press posts its request id to `/api/keep-warm` and disables the button until
// the route has answered and the next value of `running` has come, or for `PRESS_WAIT_MS`.
// A refusal's words show as the row's note until the next value. When the pressed button
// leaves the list, as it does when the session turns covered, and nothing else holds the
// focus, the focus goes to that session's `Open`.
//
// It reads `/api/running` each time it is connected and each time the document is shown
// again. At the desk it follows the topic `running`; on the phone, which has no stream, it
// reads the route again every `POLL_MS` while the document is shown. On the phone it holds
// itself open and its rows carry no time, no message and no act.
//
// `Open` goes to the session's transcript on sessions' page, and `All sessions` to that
// page's list; without sessions installed both go to the launcher's page. The row's words
// are worked out in `running-model.ts`.

import { css, html, LitElement, nothing } from "lit";
import type { PropertyValues } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { api, gateway, installed, navigate, subscribe } from "/m/ui/client.js";

import {
  actsOf, allLabel, allPlace, answered, expired, hrefOf, KEEP_WARM_PATH, NO_PRESSES, openPlace, parseRunning,
  pressBody, pressed, pressNote, PRESS_WAIT_MS, rowAttrs, rowReport, shownSessions, valueCame,
} from "./running-model.ts";
import type { Place, Presses, Running, RunningEntry } from "./running-model.ts";

const TAG = "moxi-launcher-running";
/** On the phone, how often the row reads `/api/running` again while the document is shown. */
const POLL_MS = 30_000;

export class LauncherRunning extends LitElement {
  static styles = css`
    :host { display: block; min-width: 0; }
    * { box-sizing: border-box; }
    a { color: var(--moxi-text-dim); text-decoration: none; }
    a:hover { text-decoration: underline; }
    :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
    .foot { padding: 6px 0 2px; font-size: 13px; line-height: 18px; }
    button { font: inherit; color: var(--moxi-text-dim); background: none; border: 0; padding: 0; margin: 0;
             cursor: pointer; }
    button:hover:not(:disabled) { text-decoration: underline; }
    button:disabled { color: var(--moxi-text-faint); cursor: default; }
  `;

  static properties = {
    open: { type: Boolean, reflect: true },
    value: { state: true },
    presses: { state: true },
  };

  open = false;
  value: Running | null = null;
  presses: Presses = NO_PRESSES;
  #stop: (() => void) | null = null;
  #timer: ReturnType<typeof setInterval> | undefined;
  /** The press timers, by request id. */
  #waits = new Map<string, ReturnType<typeof setTimeout>>();
  /** The request whose pressed button, once it leaves the list, hands the focus to `Open`. */
  #focusFor: string | null = null;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#read();
  };

  connectedCallback(): void {
    super.connectedCallback();
    if (!gateway()) this.#stop = subscribe("launcher", "running", (data) => this.#take(data));
    else this.#timer = setInterval(this.#shown, POLL_MS);
    document.addEventListener("visibilitychange", this.#shown);
    void this.#read();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#stop?.();
    this.#stop = null;
    clearInterval(this.#timer);
    this.#timer = undefined;
    for (const wait of this.#waits.values()) clearTimeout(wait);
    this.#waits.clear();
    this.presses = NO_PRESSES;
    this.#focusFor = null;
    document.removeEventListener("visibilitychange", this.#shown);
  }

  protected updated(changed: PropertyValues<this>): void {
    if (changed.has("open") || changed.has("value")) this.#report();
    this.#handFocus();
  }

  #take(data: unknown): void {
    const value = parseRunning(data);
    if (value === null) return;
    this.value = value;
    this.presses = valueCame(this.presses, value);
  }

  async #press(entry: RunningEntry): Promise<void> {
    const request = entry.request_id;
    if (this.presses.waiting[request] !== undefined) return;
    this.presses = pressed(this.presses, request);
    this.#focusFor = request;
    clearTimeout(this.#waits.get(request));
    this.#waits.set(request, setTimeout(() => {
      this.#waits.delete(request);
      this.presses = expired(this.presses, request);
    }, PRESS_WAIT_MS));
    const answer = await api("launcher", KEEP_WARM_PATH, pressBody(entry));
    if (this.isConnected) this.presses = answered(this.presses, request, pressNote(answer));
  }

  /** After a render: once the pressed button has left its row, the focus goes to that
   * row's `Open`, unless something else holds it. A press that ended with its button still
   * drawn hands nothing on. */
  #handFocus(): void {
    const request = this.#focusFor;
    if (request === null) return;
    const row = [...(this.renderRoot.querySelectorAll("moxi-ui-row[data-request]"))]
      .find((node) => node.getAttribute("data-request") === request);
    if (row === undefined) {
      this.#focusFor = null;
      return;
    }
    if (row.querySelector("button[data-act='keep-warm']") !== null) {
      if (this.presses.waiting[request] === undefined) this.#focusFor = null;
      return;
    }
    this.#focusFor = null;
    if (this.#focusIsLost()) row.querySelector<HTMLElement>("a[slot='acts']")?.focus();
  }

  /** Whether nothing on the page holds the focus, as when the focused button was removed. */
  #focusIsLost(): boolean {
    if (this.shadowRoot?.activeElement) return false;
    let active = document.activeElement;
    while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
    return active === null || active === document.body || active === this;
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
    const note = desk ? this.presses.notes[entry.request_id] : undefined;
    return html`<moxi-ui-row data-request=${entry.request_id} name=${row.name} where=${row.where ?? nothing}
        fact=${row.fact ?? nothing} ?fact-heavy=${row.factHeavy} lead=${row.lead ?? nothing}
        ?lead-heavy=${row.leadHeavy} time=${row.time ?? nothing} said-by=${row.saidBy ?? nothing}
        rest=${row.rest ?? nothing} note=${note ?? nothing}>${actsOf(entry, desk).map((act) => act.kind === "open"
      ? html`<a slot="acts" href=${hrefOf(opens)} @click=${(event: MouseEvent) => this.#go(event, opens)}>Open</a>`
      : html`<button slot="acts" type="button" data-act="keep-warm" title=${act.title}
          aria-description=${act.title} ?disabled=${this.presses.waiting[entry.request_id] !== undefined}
          @click=${() => void this.#press(entry)}>Keep warm</button>`)}</moxi-ui-row>`;
  }

  #go(event: MouseEvent, where: Place): void {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(where.module, where.route);
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, LauncherRunning);

// <moxi-comms-roster>: who can be messaged. One row per address, in the server's order:
// names held by live sessions, then standing names, with each one's state, adapter, how
// long it has been held, its directors and whom it waits on.
//
// It reads `/api/roster`, then follows the topic `roster`, and reads the route again when
// its tab is shown. It counts how long each name has been held once a second on the
// server's clock, from the `now` of its last read. When cache is installed it places
// cache's chip on each live row, and a new chip when the row's session changes; without
// cache it draws no cell for one and makes no request to cache.

import { css, html, LitElement, nothing } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { api, installed, navigate, place, subscribe } from "/m/ui/client.js";

import { Feed, parseRoster } from "./feed.ts";
import type { Roster } from "./feed.ts";
import { rosterView } from "./views.ts";
import type { RowView } from "./views.ts";

const TAG = "moxi-comms-roster";
const TICK_MS = 1000;

const STYLES = css`
  :host { display: block; min-width: 0; }
  * { box-sizing: border-box; }
  .roster { display: grid; gap: var(--moxi-gap); min-width: 0; padding: 10px 12px;
            border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius-lg);
            background: var(--moxi-bg-raised); color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  h2 { margin: 0; font-size: 1.1em; }
  a { color: var(--moxi-accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .help, .dim { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .problem { color: var(--moxi-error); }
  .cols, .first { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--moxi-gap); min-width: 0; }
  .cols { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .cols span:first-child, .address { flex: 1 1 12em; min-width: 0; }
  ul { display: grid; gap: var(--moxi-gap); margin: 0; padding: 0; list-style: none; }
  li { display: grid; gap: 2px; min-width: 0; padding-top: var(--moxi-gap); border-top: 1px solid var(--moxi-border); }
  li.standing { color: var(--moxi-text-dim); }
  .state { display: inline-flex; flex-wrap: wrap; align-items: baseline; gap: 0 6px; font-size: var(--moxi-font-size-sm); }
  .dot { display: inline-block; width: 0.6em; height: 0.6em; border-radius: 50%; background: var(--moxi-live); }
  .rest { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px calc(var(--moxi-gap) * 2); min-width: 0;
          color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .rest > span { min-width: 0; overflow-wrap: anywhere; }
  .placeholder { height: 2.6em; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken); border-top: 0; }
`;

export class CommsRoster extends LitElement {
  static styles = STYLES;

  static properties = {
    tick: { state: true },
  };

  tick = 0;
  #feed: Feed<Roster> | null = null;
  #stop: (() => void) | null = null;
  #timer: ReturnType<typeof setInterval> | undefined;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#feed?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const feed = new Feed<Roster>(() => api("comms", "api/roster"), parseRoster,
                                  () => this.requestUpdate());
    this.#feed = feed;
    void feed.refresh().then(() => {
      if (this.#feed === feed) this.#stop = subscribe("comms", "roster", (data) => feed.topic(data));
    });
    document.addEventListener("visibilitychange", this.#shown);
    this.#timer = setInterval(() => { this.tick += 1; }, TICK_MS);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#feed = null;
    this.#stop?.();
    this.#stop = null;
    document.removeEventListener("visibilitychange", this.#shown);
    clearInterval(this.#timer);
  }

  render() {
    const feed = this.#feed;
    if (feed === null) return nothing;
    const view = rosterView(feed, feed.now());
    if (view.state === "gone") return nothing;
    const chips = installed("cache");
    return html`<div class="roster" data-state=${view.state}>
      <h2><a href="/m/comms/" @click=${this.#open}>Agents</a></h2>
      <p class="help">An identity is a name only one session can hold at a time.</p>
      ${this.#status(view.state, view.detail)}
      ${view.state === "loading" ? html`<ul>${[0, 1, 2].map(() => html`<li class="placeholder"></li>`)}</ul>` : nothing}
      ${view.rows.length ? html`
        <div class="cols"><span>Address</span><span>State</span></div>
        <ul>${repeat(view.rows, (row) => row.qualified, (row) => this.#row(row, chips))}</ul>` : nothing}
    </div>`;
  }

  #row(row: RowView, chips: boolean) {
    return html`<li class=${row.live ? "live" : "standing"} data-address=${row.qualified} data-live=${String(row.live)}>
      <div class="first">
        <code class="address">${row.qualified}</code>
        <span class="state">${row.live ? html`<span class="dot"></span>` : nothing}${row.tags.map((tag) => html`<span data-tag=${tag}>${tag}</span>`)}</span>
      </div>
      <div class="rest">
        ${row.adapter ? html`<span data-field="adapter">${row.adapter}</span>` : nothing}
        ${row.bound ? html`<span data-field="bound">${row.bound}</span>` : nothing}
        ${row.directors.length ? html`<span data-field="directors">directors: ${row.directors.join(", ")}</span>` : nothing}
        ${row.awaiting.length ? html`<span data-field="awaiting">waiting on ${row.awaiting.join(", ")}</span>` : nothing}
        ${chips && row.session ? html`<span class="cache" data-session=${row.session}></span>` : nothing}
      </div>
    </li>`;
  }

  #status(state: string, detail: string | null) {
    switch (state) {
      case "offline":
        return html`<p class="problem" data-line="offline">The comms daemon isn't answering. <code>moxi doctor comms</code> says why.</p>`;
      case "error":
        return html`<p class="problem" data-line="error">comms answered with an error: ${detail}</p>`;
      case "empty":
        return html`<p class="dim" data-line="empty">No agents yet. A name appears here when a session takes one, for example with <code>moxi comms claude --name NAME</code>.</p>`;
      case "failed":
        return html`<p class="problem" data-line="failed" title=${detail ?? nothing}>Can't reach the comms panel's server.</p>`;
      default:
        return nothing;
    }
  }

  #open = (event: MouseEvent): void => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate("comms");
  };

  /** Places cache's chip in each live row's cell, and a new one when the row's session
   * changes. A chip whose placement finishes after its row moved on is removed. */
  protected updated(changed: Map<PropertyKey, unknown>): void {
    super.updated(changed);
    for (const cell of this.renderRoot.querySelectorAll<HTMLElement>(".cache")) {
      const session = cell.dataset.session ?? "";
      if (cell.dataset.placed === session) continue;
      cell.dataset.placed = session;
      cell.replaceChildren();
      place(cell, "cache", "chip", { session }).then((chip) => {
        if (chip !== null && cell.dataset.placed !== session) chip.remove();
      }, () => null);
    }
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, CommsRoster);

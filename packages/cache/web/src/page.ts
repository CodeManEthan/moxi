// <moxi-cache-page route="...">: cache's panel. A heading, a line saying what a cache
// reading is, and each live session with a clock record: its id, its harness and its chip,
// placed through client.js. It follows the topic `clocks` and redraws its rows when the
// set of sessions changes; each chip keeps its own reading current.

import { css, html, LitElement, nothing } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { api, place, subscribe } from "/m/ui/client.js";

import { Feed, parseClocks } from "./feed.ts";
import type { Clocks } from "./feed.ts";
import { pageView } from "./views.ts";

const TAG = "moxi-cache-page";
const TICK_MS = 1000;

const STYLES = css`
  :host { display: block; min-width: 0; }
  * { box-sizing: border-box; }
  h1 { margin: 0 0 var(--moxi-gap); font-size: 1.4em; }
  p { margin: 0 0 var(--moxi-gap); overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .lede { padding-left: 8px; border-left: 3px solid var(--moxi-accent); color: var(--moxi-text-dim); }
  .dim { color: var(--moxi-text-dim); }
  .warn { color: var(--moxi-warn); }
  .problem { padding: 8px 12px; border-radius: var(--moxi-radius); background: var(--moxi-error-soft); color: var(--moxi-error); }
  ul { display: grid; gap: var(--moxi-gap); margin: calc(var(--moxi-gap) * 2) 0 0; padding: 0; list-style: none; }
  li { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px var(--moxi-gap); min-width: 0;
       padding: 6px 10px; border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius);
       background: var(--moxi-bg-raised); }
  li code { flex: 1 1 16em; min-width: 0; }
  .harness { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
`;

export class CachePage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
    tick: { state: true },
  };

  route = "";
  tick = 0;
  #feed: Feed<Clocks> | null = null;
  #stop: (() => void) | null = null;
  #timer: ReturnType<typeof setInterval> | undefined;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#feed?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const feed = new Feed<Clocks>(() => api("cache", "api/clocks"), parseClocks,
                                  () => this.requestUpdate());
    this.#feed = feed;
    void feed.refresh().then(() => {
      if (this.#feed === feed) this.#stop = subscribe("cache", "clocks", (data) => feed.topic(data));
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
    const view = pageView(feed, feed.now());
    if (view.state === "gone") return nothing;
    return html`
      <h1>Cache</h1>
      <p class="lede">A cache reading says whether a session's prompt cache is still warm, and how long until it goes cold, from the cache watcher's last look at it.</p>
      <div data-state=${view.state}>${this.#status(view.state, view.detail, view.stopped)}</div>
      <ul>${repeat(view.rows, (row) => row.session, (row) => html`
        <li data-session=${row.session}>
          <code>${row.session}</code>
          <span class="harness">${row.harness}</span>
          <span class="cell" data-session=${row.session}></span>
        </li>`)}</ul>`;
  }

  #status(state: string, detail: string | null, stopped: boolean) {
    switch (state) {
      case "loading":
        return html`<p class="dim">Reading the clock records…</p>`;
      case "failed":
        return html`<p class="problem">The clock records can't be read: ${detail}.</p>`;
      case "error":
        return html`<p class="problem">cache's page server hit an error: ${detail}</p>`;
      case "empty":
        return html`<p class="dim">No live Claude Code session has a cache record. The watcher writes one for each: <code>moxi cache watch</code>, or <code>moxi cache service install</code>.</p>`;
      default:
        return stopped ? html`<p class="warn">The cache watcher has stopped, so these readings may be old.</p>` : nothing;
    }
  }

  /** Places a chip in each new row's cell. A row's session never changes, since it is the
   * row's key, so a cell is placed once. */
  protected updated(changed: Map<PropertyKey, unknown>): void {
    super.updated(changed);
    for (const cell of this.renderRoot.querySelectorAll<HTMLElement>(".cell")) {
      const session = cell.dataset.session ?? "";
      if (cell.dataset.placed === session) continue;
      cell.dataset.placed = session;
      place(cell, "cache", "chip", { session }).catch(() => null);
    }
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, CachePage);

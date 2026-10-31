// <moxi-cache-chip session="...">: one session's prompt cache, warm, near or cold with
// the time left, or why it has no reading.
//
// It reads `/api/clocks` and picks the row whose `session` is its own, then follows the
// topic `clocks`. It counts the age once a second on the server's clock, from the `now`
// of its last read, so a browser clock that is off changes nothing. It reads `session`
// once, when it connects: a host that needs a chip for another session places a new one.
// It reads the route again when its tab is shown.

import { css, html, LitElement, nothing } from "lit";
import { api, subscribe } from "/m/ui/client.js";

import { Feed, parseClocks } from "./feed.ts";
import type { Clocks } from "./feed.ts";
import { chipView } from "./views.ts";

const TAG = "moxi-cache-chip";
const TICK_MS = 1000;

const STYLES = css`
  :host { display: inline-block; max-width: 100%; vertical-align: baseline; }
  .chip {
    display: inline-block; box-sizing: border-box; min-width: 7.5em; max-width: 12em;
    padding: 0 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    border-radius: var(--moxi-radius); font-family: var(--moxi-font-sans);
    font-size: var(--moxi-font-size-sm); line-height: 1.6;
    color: var(--moxi-text-dim); background: var(--moxi-bg-sunken);
    outline-color: var(--moxi-accent);
  }
  .chip:focus-visible { outline-style: solid; outline-width: 2px; outline-offset: 1px; }
  .loading { color: transparent; }
  .warm { color: var(--moxi-ok); background: var(--moxi-bg-sunken); }
  .near { color: var(--moxi-warn); background: var(--moxi-warn-soft); }
  .cold { color: var(--moxi-error); background: var(--moxi-error-soft); }
  .word { color: var(--moxi-text); background: var(--moxi-bg-sunken); }
  .muted { color: var(--moxi-text-dim); background: var(--moxi-bg-sunken); }
  .chip.dim { color: var(--moxi-text-faint); }
`;

export class CacheChip extends LitElement {
  static styles = STYLES;

  static properties = {
    tick: { state: true },
  };

  tick = 0;
  #session = "";
  #feed: Feed<Clocks> | null = null;
  #stop: (() => void) | null = null;
  #timer: ReturnType<typeof setInterval> | undefined;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#feed?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#session = this.getAttribute("session") ?? "";
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
    const view = chipView(feed, this.#session, feed.now());
    if (view.state === "gone") return nothing;
    const classes = `chip ${view.tone}${view.dim ? " dim" : ""}`;
    return html`<span class=${classes} tabindex="0" title=${view.title ?? nothing}
      data-state=${view.state} data-shows=${view.shows ?? nothing} data-dim=${String(view.dim)}
      aria-busy=${view.state === "loading" ? "true" : nothing}>${view.text || " "}</span>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, CacheChip);

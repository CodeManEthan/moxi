// <moxi-launcher-state session="...">: one launched session's state, red when it needs a
// look (`blocked` or `failed`), else what is under way or `ok`, with every line of the
// state as its title.
//
// It reads `/api/state?session=<id>`, then follows the topic `sessions`, which holds every
// live launch's state: an event that holds its session replaces the state at once, and
// one that doesn't (the session isn't live) reads the route again. It reads `session` once,
// when it connects: a host that needs another session's state places a new one. It reads
// the route again when its tab is shown.

import { css, html, LitElement, nothing } from "lit";
import { api, subscribe } from "/m/ui/client.js";

import { Feed, parseStates, pickSession } from "./feed.ts";
import type { States } from "./feed.ts";
import { stateView } from "./views.ts";

const TAG = "moxi-launcher-state";

const STYLES = css`
  :host { display: inline-block; max-width: 100%; vertical-align: baseline; }
  .state {
    display: inline-block; box-sizing: border-box; min-width: 3em; max-width: 16em;
    padding: 0 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    border-radius: var(--moxi-radius); font-family: var(--moxi-font-sans);
    font-size: var(--moxi-font-size-sm); line-height: 1.6;
    color: var(--moxi-text-dim); background: var(--moxi-bg-sunken);
    outline-color: var(--moxi-accent);
  }
  .state:focus-visible { outline-style: solid; outline-width: 2px; outline-offset: 1px; }
  .loading { color: transparent; }
  .ok { color: var(--moxi-accent); }
  .red { color: var(--moxi-error); background: var(--moxi-error-soft); font-weight: 600; }
  .muted { color: var(--moxi-text-dim); }
`;

export class LauncherState extends LitElement {
  static styles = STYLES;

  #session = "";
  #feed: Feed<States> | null = null;
  #stop: (() => void) | null = null;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#feed?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#session = this.getAttribute("session") ?? "";
    const path = `api/state?session=${encodeURIComponent(this.#session)}`;
    const feed = new Feed<States>(() => api("launcher", path), parseStates,
                                  () => this.requestUpdate(), { topic: pickSession(this.#session) });
    this.#feed = feed;
    void feed.refresh().then(() => {
      if (this.#feed === feed) this.#stop = subscribe("launcher", "sessions", (data) => feed.topic(data));
    });
    document.addEventListener("visibilitychange", this.#shown);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#feed = null;
    this.#stop?.();
    this.#stop = null;
    document.removeEventListener("visibilitychange", this.#shown);
  }

  render() {
    const feed = this.#feed;
    if (feed === null) return nothing;
    const view = stateView(feed, this.#session);
    if (view.state === "gone") return nothing;
    return html`<span class="state ${view.tone}" tabindex="0" title=${view.title ?? nothing}
      data-state=${view.state} data-red=${String(view.red)} data-word=${view.word ?? nothing}
      aria-busy=${view.state === "loading" ? "true" : nothing}>${view.text || " "}</span>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, LauncherState);

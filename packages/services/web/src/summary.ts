// <moxi-services-summary>: the shell home's block. How many of services' rows are ok,
// warn, fail and off, each verdict as `moxi services list` gives it, with a link to
// services' page.
//
// It reads `/api/rows` when it connects and when its tab is shown again, then follows the
// topic `rows`, which carries the same value each time it changes. Its heading links to
// services' page; on that page, the link marks the current page and goes nowhere.

import { css, html, LitElement, nothing } from "lit";
import { api, navigate, subscribe } from "/m/ui/client.js";

import { Feed, parseRows, VERDICTS } from "./feed.ts";
import type { Rows } from "./feed.ts";
import { summaryView, verdictTone } from "./views.ts";

const TAG = "moxi-services-summary";

const STYLES = css`
  :host { display: block; min-width: 0; }
  * { box-sizing: border-box; }
  .summary { display: grid; gap: 4px; min-width: 0; padding: 10px 12px;
             border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius-lg);
             background: var(--moxi-bg-raised); color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  h2 { margin: 0; font-size: 1.1em; }
  a { color: var(--moxi-accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  a[aria-current] { color: var(--moxi-text); cursor: default; }
  a[aria-current]:hover { text-decoration: none; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  p { margin: 0; overflow-wrap: anywhere; }
  .help, .dim { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  ul { display: flex; flex-wrap: wrap; gap: 4px var(--moxi-gap); margin: 0; padding: 0; list-style: none; }
  li { display: flex; align-items: baseline; gap: 4px; }
  .n { font-weight: 600; }
  .ok { color: var(--moxi-ok); }
  .warn { color: var(--moxi-warn); }
  .error { color: var(--moxi-error); }
  .muted { color: var(--moxi-text-dim); }
  .zero { color: var(--moxi-text-dim); font-weight: 400; }
  .problem { color: var(--moxi-error); }
  .placeholder { height: 1.4em; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken); }
`;

/** Whether the shell is showing services' own page. */
function onPage(): boolean {
  return location.pathname.startsWith("/m/services/");
}

export class ServicesSummary extends LitElement {
  static styles = STYLES;

  #feed: Feed<Rows> | null = null;
  #stop: (() => void) | null = null;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#feed?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const feed = new Feed<Rows>(() => api("services", "api/rows"), parseRows, () => this.requestUpdate());
    this.#feed = feed;
    void feed.refresh().then(() => {
      if (this.#feed === feed) this.#stop = subscribe("services", "rows", (data) => feed.topic(data));
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
    const view = summaryView(feed);
    if (view.state === "gone") return nothing;
    const here = onPage();
    const found = view.counts;
    return html`<div class="summary" data-state=${view.state} data-worst=${view.worst ?? nothing}>
      <h2><a href="/m/services/" aria-current=${here ? "page" : nothing} @click=${this.#open}>Services</a></h2>
      <p class="help">Every daemon and timer on this machine, by verdict.</p>
      ${view.state === "loading" ? html`<div class="placeholder"></div>` : nothing}
      ${found !== null ? html`<ul aria-label="Rows by verdict">${VERDICTS.map((state) => html`
        <li data-verdict=${state} data-count=${found[state]}>
          <span class="n ${found[state] ? verdictTone(state) : "zero"}">${found[state]}</span>
          <span class="dim">${state}</span></li>`)}</ul>` : nothing}
      ${view.line !== null ? html`<p class=${view.state === "up" ? "dim" : "problem"} data-line=${view.state}>${view.line}</p>` : nothing}
    </div>`;
  }

  #open = (event: MouseEvent): void => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (!onPage()) navigate("services");
  };
}

if (!customElements.get(TAG)) customElements.define(TAG, ServicesSummary);

// <moxi-loadout-drift>: how many of the user's items aren't ok for a harness (missing,
// stale, modified, blocked, would-commit or a bad source), with a link to loadout's page.
// When the page's harness homes aren't the ones the user's own last run in a terminal
// used, it shows that line too, never counted.
//
// The count comes from `moxi loadout status`, which runs git, so the block reads
// `/api/drift` only when it connects, when its tab is shown again, and when loadout's page
// says an act has ended (the document event `CHANGED`). It runs no poller. Its heading
// links to loadout's page; on that page, the link marks the current page and goes nowhere.

import { css, html, LitElement, nothing } from "lit";
import { api, navigate } from "/m/ui/client.js";

import { CHANGED, Feed, parseDrift } from "./feed.ts";
import type { Drift } from "./feed.ts";
import { driftView } from "./views.ts";

const TAG = "moxi-loadout-drift";

const STYLES = css`
  :host { display: block; min-width: 0; }
  * { box-sizing: border-box; }
  .drift { display: grid; gap: 4px; min-width: 0; padding: 10px 12px;
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
  .count { font-weight: 600; }
  .count.ok { color: var(--moxi-accent); }
  .count.warn { color: var(--moxi-warn); }
  .note { padding-left: 8px; border-left: 3px solid var(--moxi-warn); font-size: var(--moxi-font-size-sm); }
  .problem { color: var(--moxi-error); }
  .placeholder { height: 1.4em; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken); }
`;

/** Whether the shell is showing loadout's own page. */
function onPage(): boolean {
  return location.pathname.startsWith("/m/loadout/");
}

export class LoadoutDrift extends LitElement {
  static styles = STYLES;

  #feed: Feed<Drift> | null = null;
  #again = (): void => {
    void this.#feed?.refresh();
  };
  #shown = (): void => {
    if (document.visibilityState === "visible") this.#again();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#feed = new Feed<Drift>(() => api("loadout", "api/drift"), parseDrift,
                                 () => this.requestUpdate());
    void this.#feed.refresh();
    document.addEventListener("visibilitychange", this.#shown);
    document.addEventListener(CHANGED, this.#again);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#feed = null;
    document.removeEventListener("visibilitychange", this.#shown);
    document.removeEventListener(CHANGED, this.#again);
  }

  render() {
    const feed = this.#feed;
    if (feed === null) return nothing;
    const view = driftView(feed);
    if (view.state === "gone") return nothing;
    const here = onPage();
    return html`<div class="drift" data-state=${view.state} data-count=${view.count ?? nothing}>
      <h2><a href="/m/loadout/" aria-current=${here ? "page" : nothing} @click=${this.#open}>Loadout</a></h2>
      <p class="help">Your skills, agents and settings, placed for each harness.</p>
      ${view.state === "loading" ? html`<div class="placeholder"></div>` : nothing}
      ${view.line !== null ? html`<p class="count ${view.tone}" data-line="count">${view.line}</p>` : nothing}
      ${view.state === "problem" ? html`<p class="problem" data-line="problem">${view.detail}</p>` : nothing}
      ${view.state === "failed" ? html`<p class="problem" data-line="failed" title=${view.detail ?? nothing}>Can't reach loadout's page server.</p>` : nothing}
      ${view.note !== null ? html`<p class="note" data-line="note">${view.note}</p>` : nothing}
      ${view.count && !here ? html`<p class="dim">Open loadout's page to see which, and to install.</p>` : nothing}
    </div>`;
  }

  #open = (event: MouseEvent): void => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    // Shown again, the page would lose what its forms hold.
    if (!onPage()) navigate("loadout");
  };
}

if (!customElements.get(TAG)) customElements.define(TAG, LoadoutDrift);

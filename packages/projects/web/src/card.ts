// <moxi-projects-card key="...">: one project: its key, kind, root, lead and associated
// agents. For a key that names no project it shows the page server's refusal, which says
// so and how to list the keys.
//
// It reads `/api/project?key=...` when it connects, when its tab is shown again, and when
// one of projects' acts has ended (the document event `CHANGED`). It reads `key` once,
// when it connects: a host that wants another project places a new card.

import { css, html, LitElement, nothing } from "lit";
import { api } from "/m/ui/client.js";

import { CHANGED, Feed, parseOne } from "./feed.ts";
import type { One } from "./feed.ts";
import { BASE } from "./styles.ts";
import { cardPath, cardView } from "./views.ts";

const TAG = "moxi-projects-card";

const STYLES = [BASE, css`
  .card { display: grid; gap: 4px; min-width: 0; padding: 10px 12px; border: 1px solid var(--moxi-border);
          border-left: 3px solid var(--moxi-accent); border-radius: var(--moxi-radius-lg);
          background: var(--moxi-bg-raised); }
  .card[data-state="refused"], .card[data-state="failed"] { border-left-color: var(--moxi-error); }
  h3 { margin: 0; font-size: 1.05em; overflow-wrap: anywhere; }
  dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 2px var(--moxi-gap); margin: 0; }
  dt { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
`];

export class ProjectsCard extends LitElement {
  static styles = STYLES;

  #key = "";
  #feed: Feed<One> | null = null;
  #again = (): void => {
    void this.#feed?.refresh();
  };
  #shown = (): void => {
    if (document.visibilityState === "visible") this.#again();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#key = this.getAttribute("key") ?? "";
    const path = cardPath(this.#key);
    this.#feed = new Feed<One>(() => api("projects", path), parseOne, () => this.requestUpdate());
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
    const view = cardView(feed, this.#key);
    if (view.state === "gone") return nothing;
    return html`<div class="card" data-state=${view.state} data-key=${view.key}>
      <h3>${view.key}</h3>
      ${view.state === "loading" ? html`<div class="placeholder"></div>` : nothing}
      ${view.fields.length ? html`<dl>${view.fields.map((field) => html`
        <dt>${field.label}</dt><dd data-field=${field.name}>${field.name === "root"
          ? html`<code>${field.value}</code>` : field.value}</dd>`)}</dl>` : nothing}
      ${view.line !== null ? html`<p class="problem" data-line=${view.state}>${view.line}</p>` : nothing}
    </div>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ProjectsCard);

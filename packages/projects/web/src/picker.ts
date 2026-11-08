// <moxi-projects-picker kind="...">: choose a project. It lists each project's key and
// root, only those of `kind` when it is given, and on a choice sends its host a bubbling,
// composed `moxi-change` whose detail is `{key}` and nothing else. The root is shown and
// never sent: a host that needs it reads it on its own server, through projects' read
// API's `get(key)`.
//
// It reads `/api/projects` when it connects, when its tab is shown again, and when one of
// projects' acts has ended (the document event `CHANGED`). It reads `kind` once, when it
// connects: a host that wants another kind places a new picker.

import { css, html, LitElement, nothing } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { api } from "/m/ui/client.js";

import { CHANGED, Feed, parseProjects, PICKED } from "./feed.ts";
import type { Projects } from "./feed.ts";
import { BASE } from "./styles.ts";
import { pickedDetail, pickerPath, pickerView } from "./views.ts";

const TAG = "moxi-projects-picker";

const STYLES = [BASE, css`
  ul { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; min-width: 0; }
  li { min-width: 0; }
  .choice { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--moxi-gap); width: 100%;
            min-width: 0; padding: 4px 10px; text-align: left; border-color: var(--moxi-border); }
  .choice[aria-pressed="true"] { border-color: var(--moxi-selected); box-shadow: inset 3px 0 0 var(--moxi-selected); }
  .key { font-weight: 600; overflow-wrap: anywhere; }
  .choice code { flex: 1 1 12em; min-width: 0; color: var(--moxi-text-dim); }
`];

export class ProjectsPicker extends LitElement {
  static styles = STYLES;

  static properties = {
    chosen: { state: true },
  };

  /** The key last chosen here, which the list marks. */
  chosen: string | null = null;
  #kind: string | null = null;
  #feed: Feed<Projects> | null = null;
  #again = (): void => {
    void this.#feed?.refresh();
  };
  #shown = (): void => {
    if (document.visibilityState === "visible") this.#again();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#kind = this.getAttribute("kind") || null;
    const path = pickerPath(this.#kind);
    this.#feed = new Feed<Projects>(() => api("projects", path), parseProjects,
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
    const view = pickerView(feed, this.#kind);
    if (view.state === "gone") return nothing;
    return html`<div class="picker" data-state=${view.state} data-kind=${this.#kind ?? nothing}>
      ${view.state === "loading" ? html`<div class="placeholder"></div>` : nothing}
      ${view.state === "empty" ? html`<p class="dim" data-line="empty">${view.line}</p>` : nothing}
      ${view.state === "failed" ? html`<p class="problem" data-line="failed">${view.line}</p>` : nothing}
      ${view.choices.length ? html`<ul aria-label="Projects">
        ${repeat(view.choices, (choice) => choice.key, (choice) => html`<li>
          <button type="button" class="choice" data-key=${choice.key}
            aria-pressed=${choice.key === this.chosen ? "true" : "false"}
            @click=${() => this.#choose(choice.key)}><span class="key">${choice.key}</span>
            <code>${choice.root}</code></button></li>`)}
      </ul>` : nothing}
    </div>`;
  }

  #choose(key: string): void {
    this.chosen = key;
    this.dispatchEvent(new CustomEvent(PICKED, { detail: pickedDetail(key), bubbles: true, composed: true }));
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ProjectsPicker);

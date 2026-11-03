// <moxi-projects-page route="...">: projects' panel, for now the three components placed
// as any host places them: the picker, the card of the project chosen in it, and the add
// form. The list with its kind filter, the edit forms, the agents and remove come with
// the full page.

import { css, html, LitElement, nothing } from "lit";
import { place } from "/m/ui/client.js";

import { PICKED } from "./feed.ts";
import { BASE } from "./styles.ts";

const TAG = "moxi-projects-page";

const STYLES = [BASE, css`
  h1 { margin: 0 0 var(--moxi-gap); font-size: 1.4em; }
  .lede { margin-bottom: var(--moxi-gap); padding-left: 8px; border-left: 3px solid var(--moxi-accent);
          color: var(--moxi-text-dim); }
  .parts { display: grid; gap: calc(var(--moxi-gap) * 2); min-width: 0; }
  .parts > div { min-width: 0; }
  .parts > div:empty { display: none; }
`];

export class ProjectsPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
    missing: { state: true },
  };

  route = "";
  /** A component the shell would not place. */
  missing = false;
  /** Counts the choices, so a card placed for an earlier one is dropped. */
  #choice = 0;

  render() {
    return html`<h1>Projects</h1>
      <p class="lede">Named places: each project's key, root, kind and agents. Choose one to see it.</p>
      <div class="parts">
        <div data-part="picker" @moxi-change=${this.#picked}></div>
        <div data-part="card"></div>
        <div data-part="add"></div>
      </div>
      ${this.missing ? html`<p class="problem">A part of this page could not be shown; see the modules view.</p>` : nothing}`;
  }

  firstUpdated(): void {
    for (const name of ["picker", "add"]) {
      const host = this.renderRoot.querySelector(`[data-part="${name}"]`);
      if (host !== null) this.#place(host, name, {});
    }
  }

  #picked = (event: Event): void => {
    if (event.type !== PICKED) return;
    const key = (event as CustomEvent<{ key?: unknown }>).detail?.key;
    const host = this.renderRoot.querySelector('[data-part="card"]');
    if (typeof key !== "string" || host === null) return;
    const choice = ++this.#choice;
    host.replaceChildren();
    this.#place(host, "card", { key }, () => choice === this.#choice);
  };

  #place(host: Element, name: string, attrs: Record<string, string>,
         wanted: () => boolean = () => true): void {
    place(host, "projects", name, attrs).then((element) => {
      if (element === null) this.missing = true;
      else if (!wanted()) element.remove();
    }, () => { this.missing = true; });
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ProjectsPage);

// <moxi-launcher-page route="...">: the launcher's panel. For now a heading and the
// launched sessions' state, placed through client.js like any other host places it; the
// launch form, the launch log and the past sessions come with the full panel.

import { css, html, LitElement } from "lit";
import { place } from "/m/ui/client.js";

const TAG = "moxi-launcher-page";

const STYLES = css`
  :host { display: block; min-width: 0; }
  h1 { margin: 0 0 calc(var(--moxi-gap) * 2); font-size: 1.4em; }
  p { margin: 0; color: var(--moxi-warn); }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
`;

export class LauncherPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
    missing: { state: true },
  };

  route = "";
  missing = false;
  #placed = false;

  render() {
    return html`<h1>Launcher</h1><div id="sessions"></div>
      ${this.missing ? html`<p>The sessions could not be shown.</p>` : ""}`;
  }

  protected firstUpdated(): void {
    if (this.#placed) return;
    this.#placed = true;
    const host = this.renderRoot.querySelector("#sessions");
    if (host === null) return;
    place(host, "launcher", "sessions").then((sessions) => { this.missing = sessions === null; },
                                             () => { this.missing = true; });
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, LauncherPage);

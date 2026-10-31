// <moxi-comms-page route="...">: comms' panel, a heading and the roster, placed through
// client.js like any other host places it.

import { css, html, LitElement } from "lit";
import { place } from "/m/ui/client.js";

const TAG = "moxi-comms-page";

const STYLES = css`
  :host { display: block; min-width: 0; }
  h1 { margin: 0 0 calc(var(--moxi-gap) * 2); font-size: 1.4em; }
  p { margin: 0; color: var(--moxi-warn); }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
`;

export class CommsPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
    missing: { state: true },
  };

  route = "";
  missing = false;
  #placed = false;

  render() {
    return html`<h1>Comms</h1><div id="roster"></div>
      ${this.missing ? html`<p>The roster could not be shown.</p>` : ""}`;
  }

  protected firstUpdated(): void {
    if (this.#placed) return;
    this.#placed = true;
    const host = this.renderRoot.querySelector("#roster");
    if (host === null) return;
    place(host, "comms", "roster").then((roster) => { this.missing = roster === null; },
                                        () => { this.missing = true; });
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, CommsPage);

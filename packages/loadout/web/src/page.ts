// <moxi-loadout-page route="...">: loadout's panel. For now it holds the heading and the
// drift block, placed as any host places it; the items, the global file and the acts
// come next.

import { css, html, LitElement, nothing } from "lit";
import { place } from "/m/ui/client.js";

const TAG = "moxi-loadout-page";

const STYLES = css`
  :host { display: block; min-width: 0; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; }
  h1 { margin: 0 0 var(--moxi-gap); font-size: 1.4em; }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .lede { margin-bottom: var(--moxi-gap); padding-left: 8px; border-left: 3px solid var(--moxi-accent); color: var(--moxi-text-dim); }
  .drift { min-width: 0; margin-bottom: calc(var(--moxi-gap) * 2); }
  .drift:empty { display: none; }
  .dim { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
`;

export class LoadoutPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";
  #placed = false;
  #missing = false;

  render() {
    return html`
      <h1>Loadout</h1>
      <p class="lede">Your own skills, agents and settings, placed for each harness: Claude Code, Codex and OpenCode.</p>
      <div class="drift" data-part="drift"></div>
      ${this.#missing ? html`<p class="dim" data-line="no-drift">The count can't be shown here. Run <code>moxi loadout status</code> in a terminal.</p>` : nothing}`;
  }

  updated(): void {
    const host = this.renderRoot.querySelector(".drift");
    if (host === null || this.#placed) return;
    this.#placed = true;
    const missing = (): void => {
      this.#missing = true;
      this.requestUpdate();
    };
    place(host, "loadout", "drift").then((block) => { if (block === null) missing(); }, missing);
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, LoadoutPage);

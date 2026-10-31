// The fixture's home block, <moxi-fixture-block>: its count, in the accent colour.
import { subscribe } from "/m/ui/client.js";

const sheet = new CSSStyleSheet();
sheet.replaceSync(`
  * { box-sizing: border-box; }
  :host { display: block; }
  p { margin: 0; color: var(--moxi-accent); font-family: var(--moxi-font-sans); }
`);

class FixtureBlock extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot === null) {
      const root = this.attachShadow({ mode: "open" });
      root.adoptedStyleSheets = [sheet];
      this.text = document.createElement("p");
      this.text.dataset.testid = "count";
      this.text.textContent = "Count: …";
      root.append(this.text);
    }
    this.unsubscribe = subscribe("fixture", "count", (value) => {
      this.text.textContent = `Count: ${value.n}`;
    });
  }

  disconnectedCallback() {
    this.unsubscribe?.();
    this.unsubscribe = undefined;
  }
}

if (!customElements.get("moxi-fixture-block")) customElements.define("moxi-fixture-block", FixtureBlock);

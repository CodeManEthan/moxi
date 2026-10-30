// The fixture's inline component, <moxi-fixture-tag label="...">: one label, outlined in
// the accent colour. `label` is required.
const sheet = new CSSStyleSheet();
sheet.replaceSync(`
  * { box-sizing: border-box; }
  :host { display: inline-block; }
  span { padding: 0 6px; border: 1px solid var(--moxi-accent); border-radius: var(--moxi-radius);
         color: var(--moxi-text); font-family: var(--moxi-font-sans); font-size: var(--moxi-font-size-sm); }
`);

class FixtureTag extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot !== null) return;
    const root = this.attachShadow({ mode: "open" });
    root.adoptedStyleSheets = [sheet];
    const span = document.createElement("span");
    span.textContent = this.getAttribute("label") ?? "";
    root.append(span);
  }
}

if (!customElements.get("moxi-fixture-tag")) customElements.define("moxi-fixture-tag", FixtureTag);

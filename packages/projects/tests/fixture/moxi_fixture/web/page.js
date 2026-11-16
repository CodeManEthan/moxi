// <moxi-fixture-page route="...">: the fixture's page, where a section's link of the
// fixture goes. It says the route it was opened at. Plain DOM, so it needs no build.

const TAG = "moxi-fixture-page";

const sheet = new CSSStyleSheet();
sheet.replaceSync(`
  :host { display: block; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  p { margin: 0; }
`);

if (!customElements.get(TAG)) {
  customElements.define(TAG, class extends HTMLElement {
    static observedAttributes = ["route"];

    connectedCallback() {
      if (this.shadowRoot === null) {
        const root = this.attachShadow({ mode: "open" });
        root.adoptedStyleSheets = [sheet];
        this.line = document.createElement("p");
        this.line.dataset.testid = "route";
        root.append(this.line);
      }
      this.draw();
    }

    attributeChangedCallback() {
      this.draw();
    }

    draw() {
      if (this.line) this.line.textContent = `The fixture's page at "${this.getAttribute("route") ?? ""}"`;
    }
  });
}

// <moxi-fixture-main>: a piece for Home's main spot, announced only by an install made
// with `fixture_kit.install(site, main=True)`.
const sheet = new CSSStyleSheet();
sheet.replaceSync(`
  :host { display: block; }
  p { margin: 0; color: var(--moxi-accent); font-family: var(--moxi-font-sans); }
`);

class FixtureMain extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot !== null) return;
    const root = this.attachShadow({ mode: "open" });
    root.adoptedStyleSheets = [sheet];
    const text = document.createElement("p");
    text.dataset.testid = "main";
    text.textContent = "The fixture's main piece";
    root.append(text);
  }
}

if (!customElements.get("moxi-fixture-main")) customElements.define("moxi-fixture-main", FixtureMain);

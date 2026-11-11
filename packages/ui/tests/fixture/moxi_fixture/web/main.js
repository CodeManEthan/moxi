// <moxi-fixture-main>: a piece for Home's main spot, announced only by an install made
// with `fixture_kit.install(site, main=True)`. A test steers it with a `moxi-fixture-side`
// event on the window. A detail of `{width}` has it show a panel that wide, fixed to the
// window's right edge, and say so to Home with a `moxi-home-side` event sent from inside
// its shadow root; a width of 0 hides the panel. A detail of `{leave: true}` has the piece
// take itself off the page.
const sheet = new CSSStyleSheet();
sheet.replaceSync(`
  :host { display: block; }
  p { margin: 0; color: var(--moxi-accent); font-family: var(--moxi-font-sans); }
  aside { box-sizing: border-box; position: fixed; top: 0; right: 0; bottom: 0; border-left: 1px solid var(--moxi-border); }
  [hidden] { display: none; }
`);

class FixtureMain extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot === null) {
      const root = this.attachShadow({ mode: "open" });
      root.adoptedStyleSheets = [sheet];
      const text = document.createElement("p");
      text.dataset.testid = "main";
      text.textContent = "The fixture's main piece";
      this.panel = document.createElement("aside");
      this.panel.dataset.testid = "side";
      this.panel.hidden = true;
      root.append(text, this.panel);
    }
    this.told = (event) => {
      if (event.detail?.leave === true) this.remove();
      else this.side(event.detail?.width);
    };
    window.addEventListener("moxi-fixture-side", this.told);
  }

  disconnectedCallback() {
    window.removeEventListener("moxi-fixture-side", this.told);
  }

  side(width) {
    this.panel.hidden = !(width > 0);
    this.panel.style.width = `${width}px`;
    this.panel.dispatchEvent(new CustomEvent("moxi-home-side", { detail: { width }, bubbles: true, composed: true }));
  }
}

if (!customElements.get("moxi-fixture-main")) customElements.define("moxi-fixture-main", FixtureMain);

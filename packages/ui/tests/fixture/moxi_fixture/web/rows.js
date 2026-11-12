// The fixture's rows for the shell's Home. A row reports nothing until a test tells it
// what to report, with a `moxi-fixture-row` event on the window whose detail is
// `{row, report}`: `row` is the component's name and `report` the detail it passes on in
// its `moxi-home-row` event. A report set in `window.moxiFixtureRows[<name>]` before the
// row is placed is its first. What it draws in its own shadow root shows whether Home set
// the attribute `open`, and holds a button, `Act on <name>`, to take the focus. Taken out
// of the document and put back, as Home does when its row moves, it keeps its shadow root
// and reports nothing again.

const sheet = new CSSStyleSheet();
sheet.replaceSync(`
  :host { display: block; }
  p { margin: 0; padding: 7px 0; color: var(--moxi-accent); font-family: var(--moxi-font-sans); }
`);

export function defineRow(name) {
  const tag = `moxi-fixture-${name}`;
  if (customElements.get(tag)) return;
  customElements.define(tag, class extends HTMLElement {
    static observedAttributes = ["open"];

    connectedCallback() {
      if (this.shadowRoot === null) {
        const root = this.attachShadow({ mode: "open" });
        root.adoptedStyleSheets = [sheet];
        this.text = document.createElement("p");
        this.text.dataset.testid = "inside";
        const act = document.createElement("button");
        act.type = "button";
        act.textContent = `Act on ${name}`;
        root.append(this.text, act);
        this.draw();
        this.told = (event) => {
          if (event.detail?.row === name) this.report(event.detail.report);
        };
        const first = window.moxiFixtureRows?.[name];
        if (first !== undefined) this.report(first);
      }
      window.addEventListener("moxi-fixture-row", this.told);
    }

    disconnectedCallback() {
      window.removeEventListener("moxi-fixture-row", this.told);
    }

    attributeChangedCallback() {
      this.draw();
    }

    draw() {
      if (this.text) this.text.textContent = this.hasAttribute("open") ? `Inside ${name}, opened` : `Inside ${name}`;
    }

    report(detail) {
      this.dispatchEvent(new CustomEvent("moxi-home-row", { detail, bubbles: true, composed: true }));
    }
  });
}

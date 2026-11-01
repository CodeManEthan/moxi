// The fixture's panel, <moxi-fixture-page>: its count and note from its topics, a read,
// an act, a confirmed act, its own inline tag, and a link back to the modules view. Plain
// DOM, so it needs no build.
import { api, confirm, navigate, place, subscribe } from "/m/ui/client.js";

const sheet = new CSSStyleSheet();
sheet.replaceSync(`
  * { box-sizing: border-box; }
  :host { display: block; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  h2 { margin: 0 0 var(--moxi-gap); color: var(--moxi-accent); }
  p { margin: 0 0 var(--moxi-gap); overflow-wrap: anywhere; }
  button { font: inherit; padding: 4px 10px; border: 1px solid var(--moxi-border);
           border-radius: var(--moxi-radius); background: var(--moxi-bg-raised);
           color: var(--moxi-text); }
  .row { display: flex; flex-wrap: wrap; gap: var(--moxi-gap); margin-bottom: var(--moxi-gap); }
`);

function line(root, testid, text) {
  const p = document.createElement("p");
  p.dataset.testid = testid;
  p.textContent = text;
  root.append(p);
  return p;
}

function button(row, text, onclick) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  b.addEventListener("click", onclick);
  row.append(b);
  return b;
}

class FixturePage extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot === null) this.build();
    this.unsubscribe = [
      subscribe("fixture", "count", (value) => { this.count.textContent = `Count: ${value.n}`; }),
      subscribe("fixture", "note", (value) => { this.note.textContent = `Note: ${value.text}`; }),
    ];
  }

  disconnectedCallback() {
    for (const end of this.unsubscribe ?? []) end();
    this.unsubscribe = [];
  }

  build() {
    const root = this.attachShadow({ mode: "open" });
    root.adoptedStyleSheets = [sheet];
    const title = document.createElement("h2");
    title.textContent = "Fixture";
    root.append(title);
    line(root, "route", `Route: ${this.getAttribute("route") ?? ""}`);
    this.count = line(root, "count", "Count: …");
    this.note = line(root, "note", "Note: …");
    this.summary = line(root, "summary", "");
    this.status = line(root, "status", "");
    const row = document.createElement("div");
    row.className = "row";
    root.append(row);
    button(row, "Add one", async () => {
      const answer = await api("fixture", "api/add", { by: 1 });
      this.status.textContent = `add: ${answer.status}`;
    });
    button(row, "Confirmed add", async () => {
      const outcome = await confirm("fixture", "api/confirm", { by: 1 },
                                    ["Adds one to the fixture's count."]);
      this.status.textContent = `confirm: ${outcome.outcome} ${outcome.status ?? outcome.text ?? ""}`.trim();
    });
    button(row, "Read", async () => {
      const answer = await api("fixture", "api/summary");
      this.status.textContent = `summary: ${answer.status}`;
      if (answer.data) this.summary.textContent = `Summary: ${answer.data.count} ${answer.data.note}`;
    });
    button(row, "Modules", () => navigate("ui"));
    const tags = document.createElement("div");
    tags.className = "row";
    root.append(tags);
    place(tags, "fixture", "tag", { label: "on the page" });
  }
}

if (!customElements.get("moxi-fixture-page")) customElements.define("moxi-fixture-page", FixturePage);

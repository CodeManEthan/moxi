// <moxi-ui-row>: the two-line row a list page draws, defined by client.js so that every
// page can place it. Like the form, it is built with DOM calls and text nodes and styled
// by one adopted stylesheet in its own shadow root.
//
// It draws what its attributes carry and knows nothing else: line one is `name`, `where`
// and, at the right end, `fact`; line two is `lead`, `lead-more`, `time` with `rest`, and
// `tail`, a middle dot between each two, then the `acts` slot at the right end; line three
// is `note`. A piece whose attribute is unset is not drawn, and neither is a line with no
// piece. The default slot's content shows under the lines while `open` is set.
//
// Only a row with `toggles` can be opened by its lines: it shows a pointer, takes the
// focus, and sends `moxi-row-toggle` for a click or Enter outside `acts`. It does not
// open itself; the page sets `open`.

import { isToggleKey, ROW_ATTRIBUTES, rowPlan, TOGGLE_EVENT } from "./row-model.ts";
import type { Piece } from "./row-model.ts";

const TAG = "moxi-ui-row";

const CSS = `
:host { display: block; min-width: 0; font-size: 14px; color: var(--moxi-text); }
:host([hidden]) { display: none; }
:host([open]) { padding-bottom: 10px; }
*, *::before, *::after { box-sizing: border-box; }
.head { padding: 7px 0; }
:host([open]) .head { padding-bottom: 0; }
:host([toggles]) .head { cursor: pointer; }
.head:focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
.l1, .l2, .l3 { display: flex; justify-content: space-between; align-items: baseline; white-space: nowrap; }
.l1 { justify-content: flex-start; line-height: 20px; }
.l2, .l3 { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
/* Line one is cut at the where, then at the name; the fact is cut only once both are gone. */
.name, .where, .fact { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.name { flex: 0 1 auto; }
.where { flex: 0 100000 auto; color: var(--moxi-text-dim); }
.where::before { content: ""; display: inline-block; width: 10px; }
.fact { flex: 0 0 auto; max-width: 100%; margin-left: auto; padding-left: 14px;
        color: var(--moxi-text-dim); font-variant-numeric: tabular-nums; }
.fact.strong, .lead.strong { color: var(--moxi-text); font-weight: 600; font-variant-numeric: tabular-nums; }
.said { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.l2.acted .said { padding-right: 18px; }
.dot { margin: 0 7px; }
.time { margin-right: 8px; font-variant-numeric: tabular-nums; }
.you { margin-right: 5px; }
.rest.strong { color: var(--moxi-text); }
.tail { font-variant-numeric: tabular-nums; }
.dot.last { margin: 0 6px; }
.acts { display: inline-flex; flex: none; align-items: baseline; gap: 18px; margin-left: auto; }
:host(:not([open])) slot:not([name]) { display: none; }
/* After the lines' own display, so a hidden line is not drawn. */
[hidden] { display: none; }
@media (max-width: 720px) {
  .l2 { white-space: normal; }
  .fact { padding-left: 10px; }
}
`;

function span(cls: string, text?: string): HTMLSpanElement {
  const node = document.createElement("span");
  node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** The node for one piece of line two; the dot before the tail takes the tail's spacing. */
function said(piece: Piece, next: Piece | undefined): HTMLSpanElement {
  if (piece.kind === "dot") return span(next?.kind === "tail" ? "dot last" : "dot", piece.text);
  return span(piece.strong ? `${piece.kind} strong` : piece.kind, piece.text);
}

/** Defines `<moxi-ui-row>` once. */
export function defineRow(): void {
  if (customElements.get(TAG)) return;
  let sheet: CSSStyleSheet | null = null;

  class MoxiRow extends HTMLElement {
    #head = document.createElement("div");
    #one = document.createElement("div");
    #two = document.createElement("div");
    #said = span("said");
    #acts = span("acts");
    #actsSlot = document.createElement("slot");
    #three = document.createElement("div");
    #acted = false;

    static get observedAttributes(): string[] {
      return [...ROW_ATTRIBUTES];
    }

    constructor() {
      super();
      const root = this.attachShadow({ mode: "open" });
      if (sheet === null) {
        sheet = new CSSStyleSheet();
        sheet.replaceSync(CSS);
      }
      root.adoptedStyleSheets = [sheet];
      this.#head.className = "head";
      this.#one.className = "l1";
      this.#two.className = "l2";
      this.#three.className = "l3";
      this.#actsSlot.name = "acts";
      this.#acts.append(this.#actsSlot);
      this.#two.append(this.#said, this.#acts);
      this.#head.append(this.#one, this.#two, this.#three);
      root.append(this.#head, document.createElement("slot"));
      this.#actsSlot.addEventListener("slotchange", () => {
        this.#acted = this.#actsSlot.assignedNodes().length > 0;
        this.#render();
      });
      this.#head.addEventListener("click", (event) => {
        if (this.#outsideActs(event)) this.#toggle();
      });
      this.#head.addEventListener("keydown", (event) => {
        if (!isToggleKey(event) || !this.#outsideActs(event)) return;
        event.preventDefault();
        this.#toggle();
      });
      this.#render();
    }

    attributeChangedCallback(): void {
      this.#render();
    }

    #outsideActs(event: Event): boolean {
      return !event.composedPath().includes(this.#actsSlot);
    }

    #toggle(): void {
      if (!this.hasAttribute("toggles")) return;
      this.dispatchEvent(new CustomEvent(TOGGLE_EVENT, { bubbles: true, composed: true }));
    }

    #render(): void {
      const plan = rowPlan((name) => this.getAttribute(name));

      this.#one.replaceChildren(...plan.one.map((piece) =>
        span(piece.strong ? `${piece.kind} strong` : piece.kind, piece.text)));
      this.#one.hidden = plan.one.length === 0;

      this.#said.replaceChildren(...plan.two.map((piece, i) => said(piece, plan.two[i + 1])));
      this.#said.hidden = plan.two.length === 0;
      this.#acts.hidden = !this.#acted;
      this.#two.classList.toggle("acted", this.#acted);
      this.#two.hidden = plan.two.length === 0 && !this.#acted;

      this.#three.replaceChildren(...(plan.note === null ? [] : [span("said", plan.note)]));
      this.#three.hidden = plan.note === null;
      this.#head.hidden = this.#one.hidden && this.#two.hidden && this.#three.hidden;

      // Only a row that toggles takes the focus.
      if (plan.toggles) this.#head.tabIndex = 0;
      else this.#head.removeAttribute("tabindex");
    }
  }

  customElements.define(TAG, MoxiRow);
}

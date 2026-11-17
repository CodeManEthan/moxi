// <moxi-ui-row>: the two-line row a list page draws, defined by client.js so that every
// page can place it. Like the form, it is built with DOM calls and text nodes and styled
// by one adopted stylesheet in its own shadow root.
//
// It draws what its attributes carry and knows nothing else: line one is `name`, `where`
// and `fact`, then the `acts-one` slot at the right end; line two is `lead`, `lead-more`, `time` with `rest`, and
// `tail`, a middle dot between each two, then the `acts` slot at the right end; line three
// is `note`. A piece whose attribute is unset is not drawn, and neither is a line with no
// piece and no act. The default slot's content shows under the lines while `open` is set.
//
// Only a row with `toggles` can be opened by its lines: it shows a pointer over them and
// sends `moxi-row-toggle` for a click on them outside `acts-one` and `acts`, and for Enter
// or Space while its tab stop holds the focus. The tab stop is line one's pieces, as a
// button whose `aria-expanded` follows `open`; both act slots stay outside the button, so
// a screen reader still finds them. A row with no piece on line one keeps the lines as
// the stop, with no role.
// Space is taken from the page, so it does not scroll. A click that ends a selection of
// text does not toggle, so a name can be copied. The row does not open itself; the page
// sets `open`.

import { ACT_SLOTS, drawnLines, isToggleKey, ROW_ATTRIBUTES, rowPlan, TOGGLE_EVENT } from "./row-model.ts";
import type { ActSlot, Piece } from "./row-model.ts";

const TAG = "moxi-ui-row";

const CSS = `
:host { display: block; min-width: 0; font-size: 14px; color: var(--moxi-text); }
:host([hidden]) { display: none; }
:host([open]) { padding-bottom: 10px; }
*, *::before, *::after { box-sizing: border-box; }
.head { padding: 7px 0; }
:host([open]) .head { padding-bottom: 0; }
:host([toggles]) .head { cursor: pointer; }
.head:focus-visible, .l1:focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
.l1, .l2, .l3 { display: flex; justify-content: space-between; align-items: baseline; white-space: nowrap; }
/* Line one's pieces are the button; the acts-one slot sits beside it, never cut. */
.one { display: flex; align-items: baseline; line-height: 20px; white-space: nowrap; }
.l1 { flex: 1 1 auto; min-width: 0; justify-content: flex-start; line-height: 20px; }
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
.acts, .acts-one { display: inline-flex; flex: none; align-items: baseline; gap: 18px; margin-left: auto; }
.acts-one { padding-left: 14px; color: var(--moxi-text-dim); }
:host(:not([open])) slot:not([name]) { display: none; }
/* After the lines' own display, so a hidden line is not drawn. */
[hidden] { display: none; }
@media (max-width: 720px) {
  .l2 { white-space: normal; }
  .fact, .acts-one { padding-left: 10px; }
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
    #lineOne = document.createElement("div");
    #one = document.createElement("div");
    #actsOne = span("acts-one");
    #two = document.createElement("div");
    #said = span("said");
    #acts = span("acts");
    #three = document.createElement("div");
    #slots: Record<ActSlot, HTMLSlotElement> = {
      "acts-one": document.createElement("slot"),
      acts: document.createElement("slot"),
    };
    #filled = new Set<ActSlot>();

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
      this.#lineOne.className = "one";
      this.#one.className = "l1";
      this.#two.className = "l2";
      this.#three.className = "l3";
      for (const name of ACT_SLOTS) {
        const slot = this.#slots[name];
        slot.name = name;
        slot.addEventListener("slotchange", () => {
          if (slot.assignedNodes().length > 0) this.#filled.add(name);
          else this.#filled.delete(name);
          this.#render();
        });
      }
      this.#actsOne.append(this.#slots["acts-one"]);
      this.#acts.append(this.#slots.acts);
      this.#lineOne.append(this.#one, this.#actsOne);
      this.#two.append(this.#said, this.#acts);
      this.#head.append(this.#lineOne, this.#two, this.#three);
      root.append(this.#head, document.createElement("slot"));
      this.#head.addEventListener("click", (event) => {
        // A click that ends a drag over text leaves the text selected, not the row toggled.
        if (this.#outsideActs(event) && String(getSelection() ?? "") === "") this.#toggle();
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

    /** Whether an event began outside both act slots, so it is the row's. */
    #outsideActs(event: Event): boolean {
      const path = event.composedPath();
      return ACT_SLOTS.every((name) => !path.includes(this.#slots[name]));
    }

    #toggle(): void {
      if (!this.hasAttribute("toggles")) return;
      this.dispatchEvent(new CustomEvent(TOGGLE_EVENT, { bubbles: true, composed: true }));
    }

    #render(): void {
      const plan = rowPlan((name) => this.getAttribute(name));
      const lines = drawnLines(plan, this.#filled);

      this.#one.replaceChildren(...plan.one.map((piece) =>
        span(piece.strong ? `${piece.kind} strong` : piece.kind, piece.text)));
      this.#one.hidden = plan.one.length === 0;
      this.#actsOne.hidden = !this.#filled.has("acts-one");
      this.#lineOne.hidden = !lines.drawsOne;

      this.#said.replaceChildren(...plan.two.map((piece, i) => said(piece, plan.two[i + 1])));
      this.#said.hidden = plan.two.length === 0;
      const acted = this.#filled.has("acts");
      this.#acts.hidden = !acted;
      this.#two.classList.toggle("acted", acted);
      this.#two.hidden = !lines.drawsTwo;

      this.#three.replaceChildren(...(plan.note === null ? [] : [span("said", plan.note)]));
      this.#three.hidden = plan.note === null;
      this.#head.hidden = this.#lineOne.hidden && this.#two.hidden && this.#three.hidden;

      // Only a row that toggles takes the focus: on line one's pieces, as a button, or on
      // the lines as a whole when line one has no piece.
      if (plan.stop === "lines") this.#head.tabIndex = 0;
      else this.#head.removeAttribute("tabindex");
      if (plan.stop === "one") {
        this.#one.tabIndex = 0;
        this.#one.setAttribute("role", "button");
        this.#one.setAttribute("aria-expanded", plan.open ? "true" : "false");
      } else {
        for (const name of ["tabindex", "role", "aria-expanded"]) this.#one.removeAttribute(name);
      }
    }
  }

  customElements.define(TAG, MoxiRow);
}

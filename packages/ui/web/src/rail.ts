// <moxi-ui-rail> and <moxi-ui-mark>: a line of step markers with the line through what is
// done, defined by client.js so that every page can place it. Like the row, each is built
// with DOM calls and text nodes and styled by one adopted stylesheet in its own shadow root.
//
// The rail draws what its marks' attributes carry and reads nothing: no route, no module,
// no rule for what a step is. Each `<moxi-ui-mark>` child is one cell, in order. The rail
// works out which stretches of the line are solid (`railLine`) and hands each mark its
// line and the rail's size; the mark draws its marker, its word, its count and its half
// of the line. A wide mark shows its own children over the line, with no marker.
//
// A click on a marker, or Enter or Space on it, makes the rail send `moxi-rail-mark` with
// the mark's `key`. The rail opens nothing itself; the page sets `open` on the mark while
// whatever it opened shows. A quiet mark, a wide mark and every mark of a small rail are
// no button and send nothing.
//
// The three state colours are the custom properties at the top of the mark's stylesheet,
// each set from its own state token in the theme. Nothing else here names a state colour.

import { HERE, isMarkKey, MARK_ATTRIBUTES, MARK_EVENT, markName, railLine, RAIL_ATTRIBUTES, stateOf } from "./rail-model.ts";
import type { CellLine, MarkState, Stretch } from "./rail-model.ts";

const RAIL = "moxi-ui-rail";
const MARK = "moxi-ui-mark";
const SVG = "http://www.w3.org/2000/svg";

const MARK_CSS = `
:host {
  --moxi-rail-done: var(--moxi-state-done);
  --moxi-rail-yours: var(--moxi-state-yours);
  --moxi-rail-failed: var(--moxi-state-failed);
  display: block; flex: none; position: relative; box-sizing: border-box;
  width: var(--moxi-rail-cell, 56px); height: 34px; font-size: 11px; color: var(--moxi-text);
}
:host([wide]) { width: var(--moxi-rail-label, 132px); }
:host(:state(small)) { display: inline-flex; align-items: center; width: auto; height: 11px; }
:host(:state(vertical)) { width: auto; height: auto; }
:host([hidden]) { display: none; }
*, *::before, *::after { box-sizing: border-box; }

/* The two halves of the line, either side of the cell's middle. */
.before, .after { position: absolute; top: 16px; height: 0; border-top: 1px dashed var(--moxi-border-strong); }
.before { left: 0; right: 50%; }
.after { left: 50%; right: 0; }
.before.solid, .after.solid { top: 15px; border-top: 3px solid var(--moxi-rail-done); }

/* The marker: a button, or a picture when the mark is quiet or the rail small. */
.mk { position: absolute; z-index: 1; left: 50%; top: 2px; width: 34px; height: 30px; margin: 0 0 0 -17px;
      padding: 0; border: 0; border-radius: 6px; background: none; font: inherit; color: inherit; }
button.mk { cursor: pointer; }
button.mk:hover { background: var(--moxi-bg-sunken); }
button.mk:focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 0; }
.open button.mk { background: var(--moxi-accent-soft); box-shadow: inset 0 0 0 1.5px var(--moxi-accent); }
.mk svg { position: absolute; left: 9px; top: 6px; width: 16px; height: 16px; overflow: visible; }
.word { position: absolute; top: 21px; left: 50%; transform: translateX(-50%); font-size: 11px; line-height: 11px;
        color: var(--moxi-text-dim); white-space: nowrap; pointer-events: none; }
.s-done .word { color: var(--moxi-rail-done); }
.s-failed .word { color: var(--moxi-rail-failed); }
.s-yours .word { color: var(--moxi-rail-yours); font-weight: 600; }
.count { position: absolute; left: 23px; top: 2px; font-size: 11px; line-height: 11px; font-weight: 700;
         color: var(--moxi-text-dim); pointer-events: none; }
.s-yours .count { color: var(--moxi-rail-yours); }
.here { position: absolute; left: 50%; top: -1px; margin-left: -4px; width: 0; height: 0;
        border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid var(--moxi-text); }

/* The shapes, one per state. */
.p-done { fill: var(--moxi-rail-done); }
.p-none { fill: var(--moxi-text-dim); }
.p-yours { fill: var(--moxi-rail-yours); stroke: var(--moxi-bg); stroke-width: 0.6; }
.p-failed { fill: var(--moxi-rail-failed); }
.p-cross { fill: none; stroke: var(--moxi-bg-raised); stroke-width: 1.8; stroke-linecap: round; }
.p-owed { fill: var(--moxi-bg-raised); stroke: var(--moxi-text); stroke-width: 1.5; }
.p-play { fill: var(--moxi-text); }
.p-waiting { fill: var(--moxi-bg); stroke: var(--moxi-text-dim); stroke-width: 1.4; stroke-dasharray: 2.2 1.8; }
.p-todo { fill: var(--moxi-bg); stroke: var(--moxi-border-strong); stroke-width: 1.2; }
.p-child { fill: var(--moxi-bg); stroke: var(--moxi-text-dim); stroke-width: 1.3; }
.p-child-dot { fill: var(--moxi-text-dim); }

/* A wide mark: its own children over the line, which they hide where they stand. */
.wide { position: absolute; z-index: 1; inset: 0; display: flex; align-items: center; min-width: 0; overflow: hidden; }
.wide-in { display: inline-flex; align-items: center; gap: 6px; min-width: 0; max-width: 100%; padding-right: 6px;
           overflow: hidden; white-space: nowrap; font-size: 13px; color: var(--moxi-text-dim);
           background: var(--moxi-rail-bg, var(--moxi-bg)); }

/* A small rail: 11 px markers, a 7 px stretch of line before each, nothing else. */
.small .before, .small .after { position: static; flex: none; width: 7px; border-top-width: 1px; }
.small .before.solid, .small .after.solid { border-top-width: 2px; }
.small .after { width: 11px; }
.small .mk { position: static; flex: none; width: 11px; height: 11px; margin: 0; border-radius: 0; }
.small .mk svg { position: static; display: block; width: 11px; height: 11px; }
.small { display: inline-flex; align-items: center; }

/* A vertical rail: one mark a row, its label and word beside it, the line running down. */
.vertical { display: grid; grid-template-columns: 30px 1fr; column-gap: 10px; position: relative; }
.vertical .before, .vertical .after { left: 15px; right: auto; width: 0; height: auto; border-top: 0;
                                      border-left: 1px dashed var(--moxi-border-strong); }
.vertical .before { top: 0; height: 12px; }
.vertical .after { top: 12px; bottom: 0; }
.vertical .before.solid, .vertical .after.solid { left: 14px; border-top: 0; border-left: 3px solid var(--moxi-rail-done); }
.vertical .mk { position: relative; grid-column: 1; left: 0; top: 0; width: 30px; height: 24px; margin: 0; }
.vertical .mk svg { left: 7px; top: 4px; }
.vertical .text { grid-column: 2; padding: 3px 0 10px; font-size: 14px; line-height: 18px; color: var(--moxi-text); }
.vertical .text-here { font-weight: 600; }
.vertical .text-word { font-size: 13px; color: var(--moxi-text-dim); }
.vertical.s-done .text-word { color: var(--moxi-rail-done); }
.vertical.s-failed .text-word { color: var(--moxi-rail-failed); }
.vertical .wide { position: static; grid-column: 2; padding: 3px 0 10px; }
/* Last, so a hidden part is not drawn whatever display it has above. */
[hidden] { display: none; }
`;

const RAIL_CSS = `
:host { display: block; min-width: 0; }
:host([size="small"]:not([vertical])) { display: inline-block; vertical-align: middle; }
:host([hidden]) { display: none; }
.rail { display: flex; align-items: flex-start; }
:host([size="small"]:not([vertical])) .rail { display: inline-flex; align-items: center; }
:host([vertical]) .rail { flex-direction: column; align-items: stretch; }
::slotted(:not(moxi-ui-mark)) { display: none; }
`;

/** The shapes of each state's marker, drawn in a 16 by 16 box: an element, its
 * attributes, and the class that colours it. */
const SHAPES: Readonly<Record<MarkState, ReadonlyArray<readonly [string, Record<string, string>, string]>>> = {
  done: [["circle", { cx: "8", cy: "8", r: "5.5" }, "p-done"]],
  none: [["rect", { x: "4", y: "7", width: "8", height: "2", rx: "1" }, "p-none"]],
  yours: [["path", { d: "M8 1.5 14.5 8 8 14.5 1.5 8Z" }, "p-yours"]],
  failed: [["circle", { cx: "8", cy: "8", r: "6.5" }, "p-failed"],
           ["path", { d: "M5.4 5.4l5.2 5.2M10.6 5.4l-5.2 5.2" }, "p-cross"]],
  owed: [["circle", { cx: "8", cy: "8", r: "6" }, "p-owed"], ["path", { d: "M6.6 5.2v5.6L11 8Z" }, "p-play"]],
  waiting: [["circle", { cx: "8", cy: "8", r: "5.5" }, "p-waiting"]],
  todo: [["circle", { cx: "8", cy: "8", r: "3.4" }, "p-todo"]],
  child: [["rect", { x: "3", y: "3", width: "10", height: "10", rx: "2" }, "p-child"],
          ["circle", { cx: "8", cy: "8", r: "1.8" }, "p-child-dot"]],
};

/** What the rail tells each of its marks: its line and how the rail is drawn. */
interface Layout {
  line: CellLine;
  small: boolean;
  vertical: boolean;
}

const ALONE: Layout = { line: { before: null, after: null }, small: false, vertical: false };

/** What a mark lets its rail reach: its layout, and the button it draws, if any. */
interface MarkHandle {
  lay(layout: Layout): void;
  button(): HTMLButtonElement | null;
}

const marks = new WeakMap<Element, MarkHandle>();
const rails = new WeakMap<Element, () => void>();

function span(cls: string, text?: string): HTMLSpanElement {
  const node = document.createElement("span");
  node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function shape(state: MarkState): SVGSVGElement {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  for (const [tag, attrs, cls] of SHAPES[state]) {
    const node = document.createElementNS(SVG, tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
    node.setAttribute("class", cls);
    svg.append(node);
  }
  return svg;
}

function stretchClass(base: string, stretch: Stretch): string {
  return stretch === "solid" ? `${base} solid` : base;
}

/** Defines `<moxi-ui-mark>` and `<moxi-ui-rail>` once. */
export function defineRail(): void {
  if (customElements.get(RAIL)) return;
  let markSheet: CSSStyleSheet | null = null;
  let railSheet: CSSStyleSheet | null = null;

  class MoxiMark extends HTMLElement {
    #internals = this.attachInternals();
    #cell = document.createElement("div");
    #before = span("before");
    #after = span("after");
    #mk: HTMLElement | null = null;
    #wide = span("wide");
    #text = span("text");
    #layout: Layout = ALONE;

    static get observedAttributes(): string[] {
      return [...MARK_ATTRIBUTES];
    }

    constructor() {
      super();
      const root = this.attachShadow({ mode: "open", delegatesFocus: true });
      if (markSheet === null) {
        markSheet = new CSSStyleSheet();
        markSheet.replaceSync(MARK_CSS);
      }
      root.adoptedStyleSheets = [markSheet];
      const inner = span("wide-in");
      inner.append(document.createElement("slot"));
      this.#wide.append(inner);
      root.append(this.#cell);
      marks.set(this, {
        lay: (layout) => {
          this.#layout = layout;
          this.#render();
        },
        button: () => (this.#mk instanceof HTMLButtonElement ? this.#mk : null),
      });
      this.#render();
    }

    connectedCallback(): void {
      this.#changed();
    }

    disconnectedCallback(): void {
      this.#layout = ALONE;
      this.#render();
    }

    attributeChangedCallback(): void {
      this.#changed();
    }

    /** Draws again: through the rail when the mark is in one, since its line may move. */
    #changed(): void {
      const relayout = this.parentElement === null ? undefined : rails.get(this.parentElement);
      if (relayout !== undefined) relayout();
      else this.#render();
    }

    #render(): void {
      const { line, vertical } = this.#layout;
      const small = this.#layout.small && !vertical;
      const get = (name: string): string | null => this.getAttribute(name);
      const text = (name: string): string | null => {
        const value = get(name);
        return value === null || value.trim() === "" ? null : value;
      };
      const wide = this.hasAttribute("wide");
      const state = wide ? null : stateOf(get("state"));
      const states = this.#internals.states;
      for (const [name, on] of [["small", small], ["vertical", vertical]] as const) {
        if (on) states.add(name);
        else states.delete(name);
      }

      const classes = ["cell"];
      if (state !== null) classes.push(`s-${state}`);
      if (small) classes.push("small");
      if (vertical) classes.push("vertical");
      if (this.hasAttribute("open")) classes.push("open");
      this.#cell.className = classes.join(" ");
      this.#before.className = stretchClass("before", line.before);
      this.#after.className = stretchClass("after", line.after);
      this.#before.hidden = line.before === null;
      // A small rail draws one stretch before each marker; a cell with no marker draws a
      // second, in the marker's room, so the line passes through.
      this.#after.hidden = line.after === null || (small && state !== null);

      const parts: Node[] = [this.#before, this.#after];
      if (state === null) {
        this.#mk = null;
      } else {
        const pressable = !small && !this.hasAttribute("quiet");
        const tag = pressable ? "button" : "span";
        if (this.#mk === null || this.#mk.localName !== tag) {
          this.#mk = document.createElement(tag);
          if (this.#mk instanceof HTMLButtonElement) this.#mk.type = "button";
        }
        const mk = this.#mk;
        mk.className = "mk";
        const name = markName(get);
        const drawn: Node[] = [];
        if (!small && !vertical && this.hasAttribute("here")) drawn.push(span("here"));
        drawn.push(shape(state));
        const count = text("count");
        if (!small && !vertical && count !== null) drawn.push(span("count", count));
        const word = text("word");
        if (!small && !vertical && word !== null) drawn.push(span("word", word));
        for (const node of drawn) if (node instanceof HTMLElement) node.setAttribute("aria-hidden", "true");
        mk.replaceChildren(...drawn);
        if (pressable) {
          mk.removeAttribute("role");
          mk.setAttribute("aria-expanded", this.hasAttribute("open") ? "true" : "false");
        } else {
          mk.setAttribute("role", "img");
          mk.removeAttribute("aria-expanded");
        }
        if (name === null) mk.removeAttribute("aria-label");
        else mk.setAttribute("aria-label", name);
        parts.push(mk);
      }
      if (vertical && !wide) {
        const label = text("label");
        const word = text("word");
        const lines: Node[] = [];
        const first = span("text-label");
        if (label !== null) first.append(label);
        if (this.hasAttribute("here") && state !== null) first.append(" ", span("text-here", HERE));
        if (first.childNodes.length > 0) lines.push(first);
        if (word !== null) lines.push(document.createElement("br"), span("text-word", word));
        this.#text.replaceChildren(...lines);
        // The marker's own name already says it; the text is for the eye.
        this.#text.setAttribute("aria-hidden", "true");
        if (lines.length > 0) parts.push(this.#text);
      }
      if (wide) parts.push(this.#wide);
      // Put back only what changed, so a focused marker keeps the focus.
      const now = [...this.#cell.childNodes];
      if (now.length !== parts.length || parts.some((node, i) => now[i] !== node)) this.#cell.replaceChildren(...parts);
    }
  }

  class MoxiRail extends HTMLElement {
    #slot = document.createElement("slot");

    static get observedAttributes(): string[] {
      return [...RAIL_ATTRIBUTES];
    }

    constructor() {
      super();
      const root = this.attachShadow({ mode: "open" });
      if (railSheet === null) {
        railSheet = new CSSStyleSheet();
        railSheet.replaceSync(RAIL_CSS);
      }
      root.adoptedStyleSheets = [railSheet];
      const line = document.createElement("div");
      line.className = "rail";
      line.append(this.#slot);
      root.append(line);
      rails.set(this, () => this.#layout());
      this.#slot.addEventListener("slotchange", () => this.#layout());
      this.addEventListener("click", (event) => {
        const key = this.#pressed(event);
        if (key !== undefined) this.#send(key);
      });
      this.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        const key = this.#pressed(event);
        if (key === undefined) return;
        // The rail sends for the key itself, once a press: the button's own click on
        // Enter or Space would send a second, a key held down sends nothing more, and a
        // key held with a modifier sends nothing.
        event.preventDefault();
        if (isMarkKey(event)) this.#send(key);
      });
    }

    attributeChangedCallback(): void {
      this.#layout();
    }

    #marks(): HTMLElement[] {
      return [...this.children].filter((node): node is HTMLElement => node.localName === MARK);
    }

    /** Hands each mark its line and the rail's size. */
    #layout(): void {
      const children = this.#marks();
      const lines = railLine(children.map((mark) => ({
        state: mark.hasAttribute("wide") ? null : stateOf(mark.getAttribute("state")),
        settled: mark.hasAttribute("settled"),
      })));
      const small = this.getAttribute("size") === "small";
      const vertical = this.hasAttribute("vertical");
      children.forEach((mark, i) => marks.get(mark)?.lay({ line: lines[i], small, vertical }));
    }

    /** The key of the mark whose own button the event began on, `null` for a mark with
     * no key; undefined when it began anywhere else, such as a page's button in a wide
     * mark. */
    #pressed(event: Event): string | null | undefined {
      const path = event.composedPath();
      for (const mark of this.#marks()) {
        const button = marks.get(mark)?.button() ?? null;
        if (button !== null && path.includes(button)) {
          const key = mark.getAttribute("key");
          return key === null || key === "" ? null : key;
        }
      }
      return undefined;
    }

    #send(key: string | null): void {
      this.dispatchEvent(new CustomEvent(MARK_EVENT, { bubbles: true, composed: true, detail: { key } }));
    }
  }

  customElements.define(MARK, MoxiMark);
  customElements.define(RAIL, MoxiRail);
}

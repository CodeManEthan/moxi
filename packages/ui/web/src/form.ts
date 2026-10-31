// <moxi-ui-form>: the one form renderer, defined by client.js so that every page can use
// it. client.js holds no Lit, so this element is built with DOM calls and text nodes,
// never HTML strings; its styles are one adopted stylesheet in its own shadow root.
//
// In `each` mode (the settings view) it sends `moxi-change` for each change and the host
// writes it; in `submit` mode (a launch form) one `moxi-submit` with every value. The host
// shows a server's answer with `setError(name, text)` or `setMessage(name, lines, kind)`.
// It checks types as the user types; the server checks again.

import type { FieldDescriptor, FormElement, FormValue } from "./client.ts";
import { changeOf, check, heldOf, itemChange, plans, resetOf, submitted } from "./form-model.ts";
import type { Held, Plan, Toggle } from "./form-model.ts";

const TAG = "moxi-ui-form";

const CSS = `
:host { display: block; min-width: 0; font: inherit; color: var(--moxi-text); }
*, *::before, *::after { box-sizing: border-box; }
form { display: grid; gap: calc(var(--moxi-gap) * 2); margin: 0; }
.field { display: grid; gap: 4px; min-width: 0; padding-bottom: var(--moxi-gap); border-bottom: 1px solid var(--moxi-border); }
.field.off { opacity: 0.6; }
.head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--moxi-gap); }
label, .label { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); font-weight: 600; overflow-wrap: anywhere; }
.badge { padding: 0 6px; border-radius: var(--moxi-radius); background: var(--moxi-warn-soft); color: var(--moxi-warn); font-size: var(--moxi-font-size-sm); }
p { margin: 0; overflow-wrap: anywhere; }
.help { color: var(--moxi-text-dim); }
.note { color: var(--moxi-text-faint); font-size: var(--moxi-font-size-sm); }
.off-line { color: var(--moxi-warn); font-size: var(--moxi-font-size-sm); }
.msg:empty { display: none; }
.msg { font-size: var(--moxi-font-size-sm); }
.msg p { white-space: pre-wrap; }
.msg.info { color: var(--moxi-text-dim); }
.msg.warn { color: var(--moxi-warn); }
.msg.error { color: var(--moxi-error); }
input, select, textarea, button { font: inherit; color: var(--moxi-text); }
input[type="text"], input[type="number"], select, textarea {
  width: 100%; max-width: 36rem; min-width: 0; padding: 4px 8px;
  border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius); background: var(--moxi-bg);
}
textarea { min-height: 5rem; font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); }
input[aria-invalid="true"], textarea[aria-invalid="true"] { border-color: var(--moxi-error); }
input[type="checkbox"] { width: 1rem; height: 1rem; margin: 0; accent-color: var(--moxi-accent); }
.check { display: flex; align-items: center; gap: var(--moxi-gap); flex-wrap: wrap; }
button {
  padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
  background: var(--moxi-bg-raised); cursor: pointer;
}
button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
button:disabled, input:disabled, select:disabled, textarea:disabled { cursor: not-allowed; }
button.submit { justify-self: start; padding: 4px 16px; background: var(--moxi-accent); border-color: var(--moxi-accent); color: var(--moxi-bg); }
:focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
ul { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }
.item { display: flex; align-items: center; gap: var(--moxi-gap); min-width: 0; }
.item span { flex: 1; min-width: 0; font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
.add { display: flex; gap: var(--moxi-gap); min-width: 0; max-width: 36rem; }
.add input { flex: 1; }
.toggle { display: grid; gap: 2px; padding: 4px 0 4px 8px; border-left: 2px solid var(--moxi-border); }
details > summary { cursor: pointer; color: var(--moxi-text-dim); }
details > div { display: grid; gap: calc(var(--moxi-gap) * 2); margin-top: var(--moxi-gap); }
`;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, cls?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls !== undefined) node.className = cls;
  return node;
}

/** Puts `nodes` in `parent` in order, moving none that is already in place, since moving a
 * node takes the focus from it. */
function sync(parent: Element, nodes: readonly Element[]): void {
  const wanted = new Set(nodes);
  for (const child of [...parent.children]) if (!wanted.has(child)) child.remove();
  nodes.forEach((node, i) => {
    const at = parent.children[i];
    if (at !== node) parent.insertBefore(node, at ?? null);
  });
}

function button(text: string, onClick: () => void): HTMLButtonElement {
  const node = el("button", text);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

interface Row {
  sig: string;
  node: HTMLElement;
}

interface Message {
  lines: string[];
  kind: "info" | "warn" | "error";
}

/** Defines `<moxi-ui-form>` once. `installed` answers for a catalog row that `requires` a
 * module. */
export function defineForm(installed: (module: string) => boolean): void {
  if (customElements.get(TAG)) return;
  let sheet: CSSStyleSheet | null = null;
  let ids = 0;

  class MoxiForm extends HTMLElement implements FormElement {
    #fields: FieldDescriptor[] = [];
    #disabled = false;
    #submitLabel = "Submit";
    #rows = new Map<string, Row>();
    #slots = new Map<string, HTMLElement>();
    #messages = new Map<string, Message>();
    #held = new Map<string, Held>();
    #form: HTMLFormElement;
    #main: HTMLElement;
    #more: HTMLDetailsElement;
    #moreList: HTMLElement;
    #submit: HTMLButtonElement;

    static get observedAttributes(): string[] {
      return ["mode", "disabled", "submit-label"];
    }

    constructor() {
      super();
      const root = this.attachShadow({ mode: "open" });
      if (sheet === null) {
        sheet = new CSSStyleSheet();
        sheet.replaceSync(CSS);
      }
      root.adoptedStyleSheets = [sheet];
      this.#form = el("form");
      this.#form.noValidate = true;
      this.#main = el("div");
      this.#main.className = "fields";
      this.#more = el("details");
      this.#more.append(el("summary", "More"));
      this.#moreList = el("div");
      this.#more.append(this.#moreList);
      this.#submit = el("button", this.#submitLabel, "submit");
      this.#submit.type = "submit";
      this.#form.append(this.#main, this.#more, this.#submit);
      this.#form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (this.mode === "submit") this.#send();
      });
      root.append(this.#form);
      this.#render();
    }

    get fields(): FieldDescriptor[] {
      return this.#fields;
    }

    set fields(value: FieldDescriptor[]) {
      this.#fields = Array.isArray(value) ? value.filter((field) => typeof field?.name === "string") : [];
      // A value the server now gives replaces the one the user left in submit mode.
      const names = new Set(this.#fields.map((field) => field.name));
      for (const name of [...this.#held.keys()]) if (!names.has(name)) this.#held.delete(name);
      this.#render();
    }

    get mode(): "each" | "submit" {
      return this.getAttribute("mode") === "submit" ? "submit" : "each";
    }

    set mode(value: "each" | "submit") {
      this.setAttribute("mode", value === "submit" ? "submit" : "each");
    }

    get disabled(): boolean {
      return this.#disabled;
    }

    set disabled(value: boolean) {
      if (value) this.setAttribute("disabled", "");
      else this.removeAttribute("disabled");
    }

    get submitLabel(): string {
      return this.#submitLabel;
    }

    set submitLabel(value: string) {
      this.setAttribute("submit-label", String(value));
    }

    attributeChangedCallback(name: string): void {
      if (name === "disabled") this.#disabled = this.hasAttribute("disabled");
      if (name === "submit-label") this.#submitLabel = this.getAttribute("submit-label") || "Submit";
      this.#render();
    }

    setError(name: string, text: string): void {
      this.setMessage(name, [String(text)], "error");
    }

    setMessage(name: string, lines: string[], kind: "info" | "warn" | "error" = "info"): void {
      const list = (Array.isArray(lines) ? lines : [lines]).map(String).filter((line) => line !== "");
      if (list.length === 0) this.#messages.delete(name);
      else this.#messages.set(name, { lines: list, kind });
      this.#showMessage(name);
    }

    values(): Record<string, FormValue> {
      return submitted(this.#fields, this.#held, installed).values;
    }

    // --- drawing ----------------------------------------------------------------------

    #render(): void {
      if (this.#form === undefined) return;
      const mode = this.mode;
      const focused = (this.shadowRoot?.activeElement as HTMLElement | null)?.dataset?.control;
      const all = plans(this.#fields, installed);
      const keep = new Map<string, Row>();
      this.#slots.clear();
      const shown: HTMLElement[] = [];
      const more: HTMLElement[] = [];
      for (const plan of all) {
        const held = this.#held.get(plan.name) ?? heldOf(plan);
        const sig = JSON.stringify([plan, held, mode, this.#disabled]);
        const old = this.#rows.get(plan.name);
        const row = old !== undefined && old.sig === sig ? old : { sig, node: this.#row(plan, held) };
        keep.set(plan.name, row);
        // A kept row's slots are found again from its nodes.
        for (const slot of row.node.querySelectorAll<HTMLElement>(".msg")) {
          this.#slots.set(slot.dataset.name as string, slot);
        }
        (plan.hidden ? more : shown).push(row.node);
      }
      this.#rows = keep;
      sync(this.#main, shown);
      sync(this.#moreList, more);
      this.#more.hidden = more.length === 0;
      this.#submit.hidden = mode !== "submit";
      this.#submit.textContent = this.#submitLabel;
      this.#submit.disabled = this.#disabled;
      for (const name of this.#slots.keys()) this.#showMessage(name);
      // A rebuilt row gives the focus back to the control that had it.
      if (focused !== undefined && this.shadowRoot?.activeElement == null) {
        this.shadowRoot?.querySelector<HTMLElement>(`[data-control="${CSS_ESCAPE(focused)}"]`)?.focus();
      }
    }

    #showMessage(name: string): void {
      const slot = this.#slots.get(name);
      if (slot === undefined) return;
      const message = this.#messages.get(name);
      slot.className = `msg ${message?.kind ?? ""}`.trim();
      slot.replaceChildren(...(message?.lines ?? []).map((line) => el("p", line)));
      const control = slot.parentElement?.querySelector<HTMLElement>(`[data-control="${CSS_ESCAPE(name)}"]`);
      if (control) control.setAttribute("aria-invalid", String(message?.kind === "error"));
    }

    #slot(name: string): HTMLElement {
      const slot = el("div", undefined, "msg");
      slot.dataset.name = name;
      slot.setAttribute("role", "status");
      return slot;
    }

    #row(plan: Plan, held: Held): HTMLElement {
      const mode = this.mode;
      const off = this.#disabled || plan.off !== null;
      const node = el("div", undefined, plan.off !== null ? "field off" : "field");
      node.dataset.name = plan.name;
      const id = `f${(ids += 1)}`;
      const head = el("div", undefined, "head");
      const control = plan.control;
      const labelled = control.kind !== "toggles" && control.kind !== "list";
      const label = labelled ? el("label", plan.label) : el("span", plan.label, "label");
      if (labelled) (label as HTMLLabelElement).htmlFor = id;
      head.append(label);
      if (plan.posture) head.append(el("span", "permission posture", "badge"));
      node.append(head);
      if (plan.help) node.append(el("p", plan.help, "help"));

      const field = this.#fields.find((item) => item.name === plan.name) as FieldDescriptor;
      const clear = (): void => {
        if (this.#messages.get(plan.name)?.kind === "error") this.setMessage(plan.name, []);
      };
      const local = (value: string, item = false): boolean => {
        const why = check(field, value, item);
        if (why === null) {
          clear();
          return true;
        }
        this.setError(plan.name, why);
        return false;
      };
      const hold = (value: Held): void => {
        this.#held.set(plan.name, value);
      };

      switch (control.kind) {
        case "checkbox": {
          const box = el("input");
          box.type = "checkbox";
          box.id = id;
          box.checked = held.kind === "scalar" ? held.text === "true" : control.checked;
          box.disabled = off;
          box.dataset.control = plan.name;
          box.addEventListener("change", () => {
            const value = box.checked ? "true" : "false";
            if (mode === "each") this.#change(plan.name, changeOf(plan.name, value));
            else hold({ kind: "scalar", text: value });
          });
          const line = el("div", undefined, "check");
          line.append(box);
          node.append(line);
          break;
        }
        case "number":
        case "text":
        case "toml":
        case "select": {
          let input: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
          const now = held.kind === "scalar" ? held.text : "";
          if (control.kind === "select") {
            const select = el("select");
            for (const option of control.options) {
              const item = el("option", option);
              item.value = option;
              select.append(item);
            }
            select.value = now;
            input = select;
          } else if (control.kind === "toml") {
            const area = el("textarea");
            area.value = now;
            area.spellcheck = false;
            input = area;
          } else {
            const box = el("input");
            // A number is typed as text and checked here, so the browser never rewrites
            // what the user typed before the check sees it.
            box.type = "text";
            if (control.kind === "number") box.inputMode = control.step === "1" ? "numeric" : "decimal";
            if (control.kind === "text" && control.path) box.placeholder = "an absolute path";
            box.value = now;
            box.spellcheck = false;
            box.autocomplete = "off";
            input = box;
          }
          input.id = id;
          input.disabled = off;
          input.dataset.control = plan.name;
          const checked = control.kind !== "toml" && control.kind !== "select";
          input.addEventListener("input", () => {
            if (checked) local(input.value);
            if (mode === "submit") hold({ kind: "scalar", text: input.value });
          });
          input.addEventListener("change", () => {
            if (mode === "submit") {
              hold({ kind: "scalar", text: input.value });
              return;
            }
            if (checked && !local(input.value)) return;
            this.#change(plan.name, changeOf(plan.name, input.value));
          });
          node.append(input);
          break;
        }
        case "list": {
          const items = held.kind === "list" ? held.items : control.items;
          const list = el("ul");
          for (const item of items) {
            const line = el("li", undefined, "item");
            line.append(el("span", item));
            const remove = button("Remove", () => {
              if (mode === "each") {
                this.#change(plan.name, itemChange(plan.name, "remove", item));
              } else {
                const left = [...items];
                left.splice(left.indexOf(item), 1);
                hold({ kind: "list", items: left });
                this.#render();
              }
            });
            remove.disabled = off;
            remove.setAttribute("aria-label", `Remove ${item}`);
            line.append(remove);
            list.append(line);
          }
          const add = el("div", undefined, "add");
          const input = el("input");
          input.type = "text";
          input.id = id;
          input.disabled = off;
          input.spellcheck = false;
          input.autocomplete = "off";
          input.dataset.control = plan.name;
          input.setAttribute("aria-label", `Add to ${plan.label}`);
          const adding = (): void => {
            const value = input.value;
            if (value === "" || !local(value, true)) return;
            if (mode === "each") {
              this.#change(plan.name, itemChange(plan.name, "add", value));
              input.value = "";
            } else {
              hold({ kind: "list", items: [...items, value] });
              this.#render();
              this.shadowRoot?.querySelector<HTMLInputElement>(`[data-control="${CSS_ESCAPE(plan.name)}"]`)?.focus();
            }
          };
          input.addEventListener("input", () => {
            if (input.value === "") clear();
            else local(input.value, true);
          });
          input.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              adding();
            }
          });
          const go = button("Add", adding);
          go.disabled = off;
          add.append(input, go);
          node.append(list, add);
          break;
        }
        case "optional": {
          const now = held.kind === "optional" ? held : { on: control.on, text: control.text };
          const line = el("div", undefined, "check");
          const box = el("input");
          box.type = "checkbox";
          box.id = id;
          box.checked = now.on;
          box.disabled = off;
          const value = el("input");
          value.type = "text";
          value.value = now.text;
          value.disabled = off || !now.on;
          value.placeholder = "a value (optional)";
          value.setAttribute("aria-label", `${plan.label}'s value`);
          value.dataset.control = plan.name;
          const keep = (): void => {
            value.disabled = off || !box.checked;
            if (mode === "submit") hold({ kind: "optional", on: box.checked, text: value.value });
            else this.#change(plan.name, box.checked ? changeOf(plan.name, value.value) : resetOf(plan.name));
          };
          box.addEventListener("change", keep);
          value.addEventListener("change", keep);
          line.append(box, value);
          node.append(line);
          break;
        }
        case "toggles": {
          const on = held.kind === "toggles" ? held.on : {};
          for (const toggle of control.toggles) node.append(this.#toggle(plan, toggle, on, off));
          if (control.toggles.length === 0) node.append(el("p", "No entries.", "note"));
          if (control.unavailable !== null) node.append(el("p", control.unavailable, "off-line"));
          break;
        }
      }

      for (const line of plan.notes) node.append(el("p", line, "note"));
      if (plan.off !== null) node.append(el("p", plan.off, "off-line"));
      if (mode === "each" && plan.resettable) {
        const reset = button("Reset to default", () => this.#change(plan.name, resetOf(plan.name)));
        reset.disabled = off;
        node.append(reset);
      }
      node.append(this.#slot(plan.name));
      return node;
    }

    #toggle(plan: Plan, toggle: Toggle, on: Record<string, boolean>, off: boolean): HTMLElement {
      const mode = this.mode;
      const node = el("div", undefined, "toggle");
      const line = el("div", undefined, "check");
      const box = el("input");
      const id = `f${(ids += 1)}`;
      box.type = "checkbox";
      box.id = id;
      box.checked = toggle.key in on ? on[toggle.key] : toggle.checked;
      box.disabled = off;
      box.dataset.control = toggle.key;
      box.addEventListener("change", () => {
        if (mode === "each") {
          this.#change(plan.name, changeOf(toggle.key, box.checked ? "true" : "false"));
        } else {
          const held = this.#held.get(plan.name);
          const now = held?.kind === "toggles" ? { ...held.on } : {};
          now[toggle.key] = box.checked;
          this.#held.set(plan.name, { kind: "toggles", on: now });
        }
      });
      const label = el("label", toggle.label);
      label.htmlFor = id;
      line.append(box, label);
      node.append(line);
      for (const note of toggle.notes) node.append(el("p", note, "note"));
      if (mode === "each" && toggle.resettable) {
        const reset = button("Reset", () => this.#change(plan.name, resetOf(toggle.key)));
        reset.disabled = off;
        reset.setAttribute("aria-label", `Reset ${toggle.key}`);
        line.append(reset);
      }
      node.append(this.#slot(toggle.key));
      return node;
    }

    // --- sending ----------------------------------------------------------------------

    /** Sends one change of row `row`. The row is drawn again from the next `fields`, even
     * when they are the same, so a refused change doesn't stay on screen. */
    #change(row: string, detail: { name: string }): void {
      const drawn = this.#rows.get(row);
      if (drawn !== undefined) drawn.sig = "";
      this.#messages.delete(detail.name);
      this.#showMessage(detail.name);
      this.dispatchEvent(new CustomEvent("moxi-change", { detail, bubbles: true, composed: true }));
    }

    #send(): void {
      if (this.#disabled) return;
      const { values, problems } = submitted(this.#fields, this.#held, installed);
      for (const [name, text] of problems) this.setError(name, text);
      if (problems.size > 0) {
        const first = problems.keys().next().value as string;
        this.shadowRoot?.querySelector<HTMLElement>(`[data-control="${CSS_ESCAPE(first)}"]`)?.focus();
        return;
      }
      this.dispatchEvent(new CustomEvent("moxi-submit", { detail: { values }, bubbles: true, composed: true }));
    }
  }

  customElements.define(TAG, MoxiForm);
}

/** A name quoted for an attribute selector. */
function CSS_ESCAPE(name: string): string {
  return name.replace(/["\\]/g, "\\$&");
}

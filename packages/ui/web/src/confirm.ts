// <moxi-ui-confirm>: the sheet `confirm()` shows before a confirmed act. Like the form,
// it is built with DOM calls and text nodes and styled by one adopted stylesheet in its
// own shadow root.
//
// It shows the module's title, the lines the component gave and the body to be sent, with
// Cancel and Confirm. Confirm runs the send it was given once: a second click, or a click
// while the answer is awaited, does nothing. Cancel and Escape close it before anything is
// sent; once sent it stays open until the answer comes, then closes and removes itself.
//
// In its desk mode, on the phone where the act can't run, it shows the same title, lines
// and body, then the desk pointer's lines, with one Close button in place of the two. It
// opens in that mode, or switches to it when the answer is a desk pointer; Close and
// Escape then resolve the `desk` outcome.

import { MAY_HAVE_ACTED } from "./confirm-model.ts";
import type { ConfirmOutcome } from "./client.ts";
import { deskOutcome, spans } from "./gateway-model.ts";
import type { DeskPointer } from "./gateway-model.ts";

const TAG = "moxi-ui-confirm";

const CSS = `
:host { display: contents; }
*, *::before, *::after { box-sizing: border-box; }
dialog {
  width: min(32rem, calc(100vw - 32px)); max-height: calc(100vh - 32px); overflow: auto;
  padding: calc(var(--moxi-gap) * 2); border: 1px solid var(--moxi-border-strong);
  border-radius: var(--moxi-radius); background: var(--moxi-bg-raised); color: var(--moxi-text);
  font-family: var(--moxi-font-sans);
}
dialog::backdrop { background: rgb(0 0 0 / 0.4); }
h2 { margin: 0 0 var(--moxi-gap); font-size: 1.1rem; color: var(--moxi-accent); overflow-wrap: anywhere; }
p { margin: 0 0 var(--moxi-gap); overflow-wrap: anywhere; }
code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); }
.desk { color: var(--moxi-warn); }
pre {
  margin: 0 0 var(--moxi-gap); padding: var(--moxi-gap); border: 1px solid var(--moxi-border);
  border-radius: var(--moxi-radius); background: var(--moxi-bg); font-family: var(--moxi-font-mono);
  font-size: var(--moxi-font-size-sm); white-space: pre-wrap; overflow-wrap: anywhere;
}
.say { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
.say:empty { display: none; }
.buttons { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--moxi-gap); }
button {
  font: inherit; padding: 4px 16px; border: 1px solid var(--moxi-border-strong);
  border-radius: var(--moxi-radius); background: var(--moxi-bg); color: var(--moxi-text); cursor: pointer;
}
button.go { background: var(--moxi-accent); border-color: var(--moxi-accent); color: var(--moxi-bg); }
button:disabled { cursor: not-allowed; opacity: 0.6; }
:focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
`;

/** What the sheet shows. */
export interface SheetInfo {
  /** The module's title. */
  title: string;
  /** The component's lines, each a paragraph. */
  lines: readonly string[];
  /** The body to be sent, shown as indented JSON. */
  body: object;
  /** The desk pointer's lines: the sheet opens in its desk mode and sends nothing. */
  desk?: readonly string[];
}

/** The sheet: `ask` shows it and resolves once the user cancels, or confirms and `send`
 * resolves, or closes the desk pointer. */
export interface ConfirmElement extends HTMLElement {
  ask(info: SheetInfo, send?: () => Promise<ConfirmOutcome | DeskPointer>): Promise<ConfirmOutcome>;
}

declare global {
  interface HTMLElementTagNameMap {
    "moxi-ui-confirm": ConfirmElement;
  }
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, cls?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls !== undefined) node.className = cls;
  return node;
}

/** Defines `<moxi-ui-confirm>` once. */
export function defineConfirm(): void {
  if (customElements.get(TAG)) return;
  let sheet: CSSStyleSheet | null = null;
  let ids = 0;

  class MoxiConfirm extends HTMLElement implements ConfirmElement {
    #asked = false;

    ask(info: SheetInfo, send?: () => Promise<ConfirmOutcome | DeskPointer>): Promise<ConfirmOutcome> {
      if (this.#asked) throw new TypeError("a confirm sheet is asked once");
      this.#asked = true;
      const root = this.attachShadow({ mode: "open" });
      if (sheet === null) {
        sheet = new CSSStyleSheet();
        sheet.replaceSync(CSS);
      }
      root.adoptedStyleSheets = [sheet];
      const id = `moxi-confirm-${++ids}`;
      const dialog = el("dialog");
      dialog.setAttribute("aria-labelledby", id);
      const title = el("h2", info.title);
      title.id = id;
      dialog.append(title);
      for (const line of info.lines) dialog.append(el("p", line));
      dialog.append(el("pre", JSON.stringify(info.body, null, 2)));
      const say = el("p", "", "say");
      say.setAttribute("role", "status");
      const buttons = el("div", undefined, "buttons");
      const cancel = el("button", "Cancel");
      const go = el("button", "Confirm", "go");
      cancel.type = go.type = "button";
      buttons.append(cancel, go);
      dialog.append(say, buttons);
      root.append(dialog);

      return new Promise<ConfirmOutcome>((resolve) => {
        let sent = false;
        let desk: ConfirmOutcome | null = null;
        const finish = (outcome: ConfirmOutcome): void => {
          if (dialog.open) dialog.close();
          this.remove();
          resolve(outcome);
        };
        // The desk pointer: its lines before the status line, and Close alone.
        const toDesk = (lines: readonly string[]): void => {
          desk = deskOutcome(lines);
          const pointer = lines.map((line) => {
            const para = el("p", undefined, "desk");
            for (const part of spans(line)) para.append(part.code ? el("code", part.text) : part.text);
            return para;
          });
          say.before(...pointer);
          say.textContent = "";
          const close = el("button", "Close", "go");
          close.type = "button";
          close.addEventListener("click", () => finish(desk as ConfirmOutcome));
          buttons.replaceChildren(close);
          close.focus();
        };
        cancel.addEventListener("click", () => {
          if (!sent) finish({ outcome: "cancelled" });
        });
        // Escape: closes the sheet before anything is sent, and is ignored after, except
        // that it closes the desk pointer.
        dialog.addEventListener("cancel", (event) => {
          event.preventDefault();
          if (desk !== null) finish(desk);
          else if (!sent) finish({ outcome: "cancelled" });
        });
        go.addEventListener("click", () => {
          if (sent || send === undefined) return;
          sent = true;
          go.disabled = cancel.disabled = true;
          say.textContent = "Sending…";
          send().then((answer) => {
            if ("desk" in answer) toDesk(answer.desk);
            else finish(answer);
          }, () => finish({ outcome: "unknown", text: MAY_HAVE_ACTED }));
        });
        dialog.showModal();
        if (info.desk !== undefined || send === undefined) toDesk(info.desk ?? []);
      });
    }
  }

  customElements.define(TAG, MoxiConfirm);
}

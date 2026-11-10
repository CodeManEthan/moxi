// <moxi-launcher-prompt>: the launcher's piece for Home's main spot: a box to say what the
// agent should do, and the button that starts the session.
//
// Home places it in its spot and expects no report from it: a main piece is shown as it
// is. The caret is in the box as soon as the piece is drawn.

import { css, html, LitElement } from "lit";

const TAG = "moxi-launcher-prompt";

const STYLES = css`
  :host { display: block; position: relative; min-width: 0; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; }
  .box { display: flex; flex-direction: column; justify-content: space-between; height: 150px;
         padding: 16px 16px 12px; border: 1px solid var(--moxi-border); border-radius: 12px; }
  textarea { flex: 1 1 auto; width: 100%; min-height: 0; margin: 0 0 8px; padding: 0; border: 0; outline: 0;
             resize: none; background: none; color: var(--moxi-text); font: inherit; font-size: 16px;
             line-height: 1.5; }
  textarea::placeholder { color: var(--moxi-text-dim); opacity: 1; }
  .boxrow { display: flex; justify-content: space-between; align-items: center; }
  .left { display: flex; gap: 8px; align-items: center; min-width: 0; }
  .go { padding: 9px 16px; border: 0; border-radius: 7px; background: var(--moxi-accent);
        color: var(--moxi-on-accent); font: 600 14px/1 var(--moxi-font-sans); cursor: pointer; }
  .go:disabled { cursor: default; opacity: 0.6; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  textarea:focus-visible { outline: 0; }
  @media (max-width: 720px) {
    .box { height: auto; min-height: 150px; }
    textarea { min-height: 64px; }
    .boxrow { flex-wrap: wrap; justify-content: flex-end; row-gap: 10px; }
    .left { flex: 1 0 100%; }
  }
`;

export class LauncherPrompt extends LitElement {
  static styles = STYLES;

  static properties = {
    brief: { state: true },
  };

  /** The box's text. */
  brief = "";

  protected firstUpdated(): void {
    this.renderRoot.querySelector("textarea")?.focus();
  }

  render() {
    return html`<div class="box">
      <textarea aria-label="What should the agent do?" placeholder="What should the agent do?"
        .value=${this.brief}
        @input=${(event: Event) => { this.brief = (event.target as HTMLTextAreaElement).value; }}></textarea>
      <div class="boxrow"><div class="left"></div>${this.#button()}</div>
    </div>`;
  }

  /** The button's place, at the right of the box's last line. It starts nothing yet. */
  #button() {
    return html`<span class="split"><button type="button" class="go" disabled>Launch</button></span>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, LauncherPrompt);

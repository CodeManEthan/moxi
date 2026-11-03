// The style every projects component starts from: its box, text, controls and the focus
// ring, all from the shell's theme tokens.

import { css } from "lit";

export const BASE = css`
  :host { display: block; min-width: 0; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; outline-color: var(--moxi-accent); }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .dim { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .ok { color: var(--moxi-ok); }
  .warn { color: var(--moxi-warn); }
  .problem { color: var(--moxi-error); }
  .placeholder { height: 1.4em; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken); }
  button, select, input { font: inherit; color: var(--moxi-text); max-width: 100%; }
  button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg-raised); cursor: pointer; }
  button.go { background: var(--moxi-accent); border-color: var(--moxi-accent); color: var(--moxi-bg); }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button.go:hover:not(:disabled) { background: var(--moxi-accent); }
  button:disabled, select:disabled, input:disabled { cursor: not-allowed; opacity: 0.6; }
  select, input[type=text] { min-width: 0; padding: 2px 6px; border: 1px solid var(--moxi-border-strong);
                             border-radius: var(--moxi-radius); background: var(--moxi-bg); }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
`;

// The style every sessions component starts from: its box, text, controls and the focus
// ring, and the session view both components draw, all from the shell's theme tokens.

import { css } from "lit";

export const BASE = css`
  :host { display: block; min-width: 0; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; outline-color: var(--moxi-accent); }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .dim { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .warn { color: var(--moxi-warn); }
  .problem { color: var(--moxi-error); }
  .placeholder { height: 1.4em; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken); }
  button, select, input { font: inherit; color: var(--moxi-text); max-width: 100%; }
  button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg-raised); cursor: pointer; }
  button.go { background: var(--moxi-accent); border-color: var(--moxi-accent); color: var(--moxi-bg); }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button.go:hover:not(:disabled) { background: var(--moxi-accent); }
  button.link { padding: 0; border: 0; background: none; color: var(--moxi-accent); text-align: left;
                overflow-wrap: anywhere; text-decoration: underline; }
  button.link:hover:not(:disabled) { background: none; }
  button:disabled, select:disabled, input:disabled { cursor: not-allowed; opacity: 0.6; }
  select, input[type=text] { min-width: 0; padding: 2px 6px; border: 1px solid var(--moxi-border-strong);
                             border-radius: var(--moxi-radius); background: var(--moxi-bg); }
  a { color: var(--moxi-accent); overflow-wrap: anywhere; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
`;

export const VIEW = css`
  section { display: grid; gap: var(--moxi-gap); min-width: 0; margin-top: calc(var(--moxi-gap) * 2); padding: 12px;
            border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius-lg); background: var(--moxi-bg-raised); }
  section[data-part="window"] { border-left: 3px solid var(--moxi-accent); }
  section[data-state="refused"], section[data-state="failed"] { border-left-color: var(--moxi-error); }
  h2 { margin: 0; font-size: 1.1em; overflow-wrap: anywhere; }
  .bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); min-width: 0; }
  .bar h2 { flex: 1 1 12em; min-width: 0; }
  dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 2px var(--moxi-gap); margin: 0; }
  dt { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
  ol, ul { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; min-width: 0; }
  li { min-width: 0; }
  .msg { display: grid; gap: 2px; padding: 4px 0 4px 8px; border-left: 3px solid var(--moxi-border); }
  .msg[data-role="user"] { border-left-color: var(--moxi-accent); }
  .msg[data-reasoning="true"] { color: var(--moxi-text-dim); }
  .who { display: flex; flex-wrap: wrap; gap: 2px var(--moxi-gap); align-items: baseline;
         font-size: var(--moxi-font-size-sm); font-weight: 600; }
  .tag { padding: 0 6px; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken); font-weight: normal;
         overflow-wrap: anywhere; }
  .text { white-space: pre-wrap; overflow-wrap: anywhere; }
  .line { padding-left: 11px; font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .line.result { color: var(--moxi-text-dim); }
  .line.result[data-error="true"] { color: var(--moxi-error); }
  .line.request, .line.note { color: var(--moxi-text-dim); }
  .rule { display: flex; align-items: center; gap: var(--moxi-gap); color: var(--moxi-text-dim);
          font-size: var(--moxi-font-size-sm); }
  .rule::before, .rule::after { content: ""; flex: 1 1 1em; min-width: 1em; border-top: 1px solid var(--moxi-border-strong); }
  .rule > span { flex: 0 1 auto; min-width: 0; overflow-wrap: anywhere; text-align: center; }
  .rule[data-marker="compaction"], .rule[data-marker="turn_aborted"], .rule[data-marker="rollback"],
  .rule[data-marker="synthetic"] { color: var(--moxi-warn); }
  .entry { display: grid; gap: 2px; min-width: 0; padding-left: 8px; border-left: 3px solid var(--moxi-border); }
  .entry .first { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--moxi-gap); min-width: 0; }
`;

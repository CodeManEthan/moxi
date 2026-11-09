// The pieces usage's page and blocks share: the base styles, the limits meters and the
// incomplete-row flag. Each entry bundles its own copy, as each bundles its own Lit.

import { css, html, nothing, svg, unsafeCSS } from "lit";
import type { TemplateResult } from "lit";

import { SERIES_TOKENS } from "./views.ts";
import type { Meter } from "./views.ts";

export const BASE = css`
  :host { display: block; min-width: 0; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; }
  p { margin: 0 0 var(--moxi-gap); overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .dim { color: var(--moxi-text-dim); }
  .small { font-size: var(--moxi-font-size-sm); }
  .problem { padding: 8px 12px; border-radius: var(--moxi-radius); background: var(--moxi-error-soft); color: var(--moxi-error); }
  .notice { padding: 8px 12px; border-radius: var(--moxi-radius); background: var(--moxi-warn-soft); color: var(--moxi-text); }
  .flag { display: inline-block; margin-left: 4px; padding: 0 5px; border-radius: var(--moxi-radius);
          background: var(--moxi-warn-soft); color: var(--moxi-warn); font-size: var(--moxi-font-size-sm); font-weight: 600; cursor: help; }
  .num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
  .meters { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 15em), 1fr)); gap: var(--moxi-gap); }
  .meter { min-width: 0; padding: 8px 10px; border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius);
           background: var(--moxi-bg-raised); }
  .meter .head { display: flex; justify-content: space-between; gap: var(--moxi-gap); align-items: baseline; }
  .meter .name { font-weight: 600; overflow-wrap: anywhere; }
  .track { height: 8px; margin: 6px 0; border-radius: 4px; background: var(--moxi-bg-sunken); overflow: hidden; }
  .track svg { display: block; width: 100%; height: 100%; }
  .fill { fill: var(--moxi-accent); }
  .tone-warn .fill { fill: var(--moxi-warn); }
  .tone-error .fill { fill: var(--moxi-error); }
  .tone-muted .fill { fill: var(--moxi-text-faint); }
  .tone-warn .pct { color: var(--moxi-warn); }
  .tone-error .pct { color: var(--moxi-error); }
  .meter .line { font-size: var(--moxi-font-size-sm); color: var(--moxi-text-dim); overflow-wrap: anywhere; }
  .meter .line.state { color: var(--moxi-warn); }
  .meter[data-state="reset"] .line.state { color: var(--moxi-text-dim); }
`;

/** The series colours as classes, `s` and the token's index, for SVG marks and legend
 * swatches: a theme token reaches a mark through a class, since the page's policy allows
 * no inline style. */
export const SERIES = unsafeCSS(SERIES_TOKENS.map((token, i) =>
  `.s${i} { fill: var(--moxi-${token}); background: var(--moxi-${token}); }`).join("\n"));

/** The flag an incomplete row carries: a mark whose title says why. */
export function flag(lines: string[]): TemplateResult | typeof nothing {
  if (lines.length === 0) return nothing;
  return html`<span class="flag" title=${lines.join("; ")} aria-label=${`incomplete: ${lines.join("; ")}`}>!</span>`;
}

/** The limits meters, one per window. */
export function meterList(list: Meter[]): TemplateResult {
  return html`<div class="meters">${list.map((m) => html`
    <div class="meter tone-${m.tone}" data-window=${m.key} data-state=${m.state}>
      <div class="head"><span class="name">${m.title}</span><span class="pct">${m.percent}</span></div>
      <div class="track" role="meter" aria-label=${m.title} aria-valuemin="0" aria-valuemax="100"
           aria-valuenow=${Math.round(m.fill * 100)}><svg viewBox="0 0 100 8" preserveAspectRatio="none"
           aria-hidden="true">${svg`<rect class="fill" x="0" y="0" height="8" width=${(m.fill * 100).toFixed(1)}></rect>`}</svg></div>
      ${m.state !== "fresh" ? html`<div class="line state">${m.state}</div>` : nothing}
      ${m.reset ? html`<div class="line">${m.reset}</div>` : nothing}
      ${m.full ? html`<div class="line">${m.full}</div>` : nothing}
      ${m.age ? html`<div class="line">${m.age}</div>` : nothing}
      ${m.reason ? html`<div class="line">${m.reason}</div>` : nothing}
    </div>`)}</div>`;
}

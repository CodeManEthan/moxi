// <moxi-ui-servers>: the modules view's home block. Each module's page server and its
// state, kept current by the shell's topic `ui.servers`: running, starting, restarting for
// a new version, or not running (idle, or its last start failed and the next request tries
// again), with its starts, crashes and last exit, and the doctor's warn and fail cases (a
// crash in the last hour, three failed starts in a row).

import { css, html, LitElement } from "lit";
import { subscribe } from "/m/ui/client.js";

import { serverViews } from "./views.ts";

const TAG = "moxi-ui-servers";
/** How often the times shown ("3 min ago") are worked out again with no new value. */
const TICK_MS = 30_000;

export class ServersBlock extends LitElement {
  static styles = css`
    :host { display: block; min-width: 0; }
    * { box-sizing: border-box; }
    p { margin: 0; overflow-wrap: anywhere; }
    .dim { color: var(--moxi-text-dim); }
    ul { display: grid; gap: var(--moxi-gap); margin: 0; padding: 0; list-style: none; }
    li { display: grid; gap: 2px; min-width: 0; padding: 6px 10px; border: 1px solid var(--moxi-border);
         border-left-width: 3px; border-radius: var(--moxi-radius); background: var(--moxi-bg); }
    li.ok { border-left-color: var(--moxi-ok); }
    li.warn { border-left-color: var(--moxi-warn); }
    li.fail { border-left-color: var(--moxi-error); }
    .head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--moxi-gap); }
    .name { font-weight: 600; }
    .state { font-size: var(--moxi-font-size-sm); color: var(--moxi-text-dim); }
    .line { font-size: var(--moxi-font-size-sm); color: var(--moxi-text-dim); }
    .ok .state { color: var(--moxi-ok); }
    .tone-warn, .warn .state { color: var(--moxi-warn); }
    .tone-fail, .fail .state { color: var(--moxi-error); }
  `;

  static properties = {
    value: { state: true },
    now: { state: true },
  };

  value: unknown = undefined;
  now = Date.now();
  #stop: (() => void) | null = null;
  #timer: ReturnType<typeof setInterval> | undefined;

  connectedCallback(): void {
    super.connectedCallback();
    this.#stop = subscribe("ui", "servers", (data) => {
      this.value = data;
      this.now = Date.now();
    });
    this.#timer = setInterval(() => { this.now = Date.now(); }, TICK_MS);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#stop?.();
    this.#stop = null;
    clearInterval(this.#timer);
  }

  render() {
    if (this.value === undefined) return html`<p class="dim">Waiting for the shell…</p>`;
    const rows = serverViews(this.value, this.now);
    if (rows.length === 0) {
      return html`<p class="dim">No page server has run since the shell started. One starts when its panel is opened.</p>`;
    }
    return html`<ul>${rows.map((row) => html`
      <li class=${row.tone} data-module=${row.module}>
        <div class="head"><span class="name">${row.module}</span><span class="state">${row.state}</span></div>
        ${row.lines.map((line) => html`<p class=${line.tone ? `line tone-${line.tone}` : "line"}>${line.text}</p>`)}
      </li>`)}</ul>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, ServersBlock);

// <moxi-launcher-sessions>: each live launched session's state, one row per session,
// newest first. A row says where the session runs, whether it needs a look (red, with the
// word `blocked` or `failed`, since when and why), and its relaunch, recycle and
// keep-warm lines.
//
// It reads `/api/state` (every live launch's state) and follows the topic `sessions`; it
// reads `/api/launches` and follows the topic `launches` for each row's harness, target
// and directory. A launch log that can't be read leaves those out and nothing else. It
// reads both routes again when its tab is shown.

import { css, html, LitElement, nothing } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { api, navigate, subscribe } from "/m/ui/client.js";

import { Feed, parseLaunches, parseStates } from "./feed.ts";
import type { Launches, States } from "./feed.ts";
import { sessionsView } from "./views.ts";
import type { SessionRow } from "./views.ts";

const TAG = "moxi-launcher-sessions";

const STYLES = css`
  :host { display: block; min-width: 0; }
  * { box-sizing: border-box; }
  .sessions { display: grid; gap: var(--moxi-gap); min-width: 0; padding: 10px 12px;
              border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius-lg);
              background: var(--moxi-bg-raised); color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  h2 { margin: 0; font-size: 1.1em; }
  a { color: var(--moxi-accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .help, .dim { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .problem { color: var(--moxi-error); }
  ul { display: grid; gap: var(--moxi-gap); margin: 0; padding: 0; list-style: none; }
  li { display: grid; gap: 2px; min-width: 0; padding-top: var(--moxi-gap); border-top: 1px solid var(--moxi-border); }
  li.red { border-left: 3px solid var(--moxi-error); padding-left: 6px; }
  .first { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--moxi-gap); min-width: 0; }
  .label { flex: 1 1 12em; min-width: 0; overflow-wrap: anywhere; }
  .word { display: inline-block; max-width: 100%; padding: 0 6px; overflow: hidden; text-overflow: ellipsis;
          white-space: nowrap; border-radius: var(--moxi-radius); font-size: var(--moxi-font-size-sm);
          line-height: 1.6; background: var(--moxi-bg-sunken); }
  .word.ok { color: var(--moxi-accent); }
  .word.red { color: var(--moxi-error); background: var(--moxi-error-soft); font-weight: 600; }
  .word.muted { color: var(--moxi-text-dim); }
  .head { color: var(--moxi-error); font-size: var(--moxi-font-size-sm); }
  .lines { display: grid; gap: 0; margin: 0; padding: 0; list-style: none;
           color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .lines li { display: block; padding: 0; border: 0; overflow-wrap: anywhere; }
  .placeholder { height: 2.6em; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken); border-top: 0; }
`;

export class LauncherSessions extends LitElement {
  static styles = STYLES;

  #states: Feed<States> | null = null;
  #launches: Feed<Launches> | null = null;
  #stops: (() => void)[] = [];
  #shown = (): void => {
    if (document.visibilityState !== "visible") return;
    void this.#states?.refresh();
    void this.#launches?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const changed = () => this.requestUpdate();
    const states = new Feed<States>(() => api("launcher", "api/state"), parseStates, changed);
    const launches = new Feed<Launches>(() => api("launcher", "api/launches"), parseLaunches, changed);
    this.#states = states;
    this.#launches = launches;
    void states.refresh().then(() => {
      if (this.#states === states) this.#stops.push(subscribe("launcher", "sessions", (data) => states.topic(data)));
    });
    void launches.refresh().then(() => {
      if (this.#launches === launches) this.#stops.push(subscribe("launcher", "launches", (data) => launches.topic(data)));
    });
    document.addEventListener("visibilitychange", this.#shown);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#states?.close();
    this.#launches?.close();
    this.#states = null;
    this.#launches = null;
    for (const stop of this.#stops) stop();
    this.#stops = [];
    document.removeEventListener("visibilitychange", this.#shown);
  }

  render() {
    const states = this.#states;
    const launches = this.#launches;
    if (states === null || launches === null) return nothing;
    const view = sessionsView(states, launches);
    if (view.state === "gone") return nothing;
    return html`<div class="sessions" data-state=${view.state} data-red=${String(view.red)}>
      <h2><a href="/m/launcher/" @click=${this.#open}>Sessions</a></h2>
      <p class="help">Each launched session that is running, and whether it needs a look.</p>
      ${this.#status(view.state, view.detail)}
      ${view.state === "loading" ? html`<ul>${[0, 1].map(() => html`<li class="placeholder"></li>`)}</ul>` : nothing}
      ${view.rows.length ? html`<ul>${repeat(view.rows, (row) => row.request, (row) => this.#row(row))}</ul>` : nothing}
    </div>`;
  }

  #row(row: SessionRow) {
    return html`<li class=${row.red ? "red" : "ok"} data-session=${row.session} data-request=${row.request}
        data-red=${String(row.red)} data-word=${row.word ?? nothing}>
      <div class="first">
        <span class="label">${row.label}</span>
        <span class="word ${row.tone}" data-field="word">${row.text}</span>
      </div>
      ${row.cwd ? html`<code data-field="cwd">${row.cwd}</code>` : nothing}
      ${row.red || row.tone === "muted" ? html`<p class="head" data-field="head">${row.head}</p>` : nothing}
      <ul class="lines">${row.lines.map((line) => html`<li>${line}</li>`)}</ul>
    </li>`;
  }

  #status(state: string, detail: string | null) {
    switch (state) {
      case "error":
        return html`<p class="problem" data-line="error">The launcher's state could not be read: ${detail}</p>`;
      case "empty":
        return html`<p class="dim" data-line="empty">No launched session is running. Start one from the launcher's page, or with <code>moxi launcher run</code>.</p>`;
      case "failed":
        return html`<p class="problem" data-line="failed" title=${detail ?? nothing}>Can't reach the launcher's page server.</p>`;
      default:
        return nothing;
    }
  }

  #open = (event: MouseEvent): void => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate("launcher");
  };
}

if (!customElements.get(TAG)) customElements.define(TAG, LauncherSessions);

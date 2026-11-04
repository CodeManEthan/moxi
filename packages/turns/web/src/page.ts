// <moxi-turns-page route="...">: turns' panel, the live list of the sessions turns can
// reach. One row per session with its turn, its facts, its versions and its trouble, and
// its agents listed under it. It reads `/api/sessions`, then follows the topic `sessions`,
// which carries the whole list at each change. It is read-only: nothing on it acts on a
// session, and the working directory is shown, never sent.

import { css, html, LitElement, nothing } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { api, subscribe } from "/m/ui/client.js";

import { Feed, parseList } from "./feed.ts";
import type { SessionList } from "./feed.ts";
import { NOT_READ, pageView } from "./views.ts";
import type { PageView, RowView } from "./views.ts";

const TAG = "moxi-turns-page";
/** Ages ("started 5m ago") are redrawn this often. */
const TICK_MS = 1000;

const STYLES = css`
  :host { display: block; min-width: 0; }
  * { box-sizing: border-box; }
  h1 { margin: 0 0 var(--moxi-gap); font-size: 1.4em; }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .lede { margin-bottom: var(--moxi-gap); padding-left: 8px; border-left: 3px solid var(--moxi-accent);
          color: var(--moxi-text-dim); }
  .dim { color: var(--moxi-text-dim); }
  .problem { padding: 6px 10px; border-radius: var(--moxi-radius); background: var(--moxi-error-soft); color: var(--moxi-error); }
  .warn { padding: 6px 10px; border-radius: var(--moxi-radius); background: var(--moxi-warn-soft); color: var(--moxi-warn); }
  .scan { margin-top: var(--moxi-gap); }
  ul { margin: 0; padding: 0; list-style: none; }
  .sessions { display: grid; gap: var(--moxi-gap); margin-top: calc(var(--moxi-gap) * 2); }
  .session { display: grid; gap: 6px; min-width: 0; padding: 8px 12px; border: 1px solid var(--moxi-border);
             border-radius: var(--moxi-radius); background: var(--moxi-bg-raised); }
  .head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px var(--moxi-gap); min-width: 0; }
  .harness { font-weight: 600; }
  .badge { padding: 0 6px; border-radius: var(--moxi-radius); font-size: var(--moxi-font-size-sm);
           background: var(--moxi-bg-sunken); color: var(--moxi-text-dim); white-space: nowrap; }
  .turn[data-open="true"] { background: var(--moxi-accent-soft); color: var(--moxi-text); }
  .stale { background: var(--moxi-warn-soft); color: var(--moxi-warn); }
  .cwd { color: var(--moxi-text-dim); }
  .facts { font-size: var(--moxi-font-size-sm); color: var(--moxi-text-dim); }
  .versions { font-size: var(--moxi-font-size-sm); color: var(--moxi-text-faint); }
  .agents { display: grid; gap: 2px; padding-left: 10px; border-left: 2px solid var(--moxi-border);
            font-size: var(--moxi-font-size-sm); }
  .agent { display: flex; flex-wrap: wrap; gap: 2px 6px; min-width: 0; }
  .agent .kind { color: var(--moxi-text-dim); }
  .agent .desc { flex: 1 1 12em; min-width: 0; overflow-wrap: anywhere; }
`;

export class TurnsPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
    tick: { state: true },
  };

  route = "";
  tick = 0;
  #feed: Feed<SessionList> | null = null;
  #stop: (() => void) | null = null;
  #timer: ReturnType<typeof setInterval> | undefined;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#feed?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const feed = new Feed<SessionList>(() => api("turns", "api/sessions"), parseList,
                                       () => this.requestUpdate());
    this.#feed = feed;
    void feed.refresh().then(() => {
      if (this.#feed === feed) this.#stop = subscribe("turns", "sessions", (data) => feed.topic(data));
    });
    document.addEventListener("visibilitychange", this.#shown);
    this.#timer = setInterval(() => { this.tick += 1; }, TICK_MS);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#feed = null;
    this.#stop?.();
    this.#stop = null;
    document.removeEventListener("visibilitychange", this.#shown);
    clearInterval(this.#timer);
  }

  render() {
    const feed = this.#feed;
    if (feed === null) return nothing;
    const view = pageView(feed, feed.now());
    if (view.state === "gone") return nothing;
    return html`
      <h1>Live sessions</h1>
      <p class="lede">Every running session turns can reach, with its turn and its agents, read from the session itself. This panel only reads: nothing here acts on a session.</p>
      <div data-state=${view.state}>${this.#status(view)}</div>
      ${view.scan === null ? nothing : html`<p class="problem scan" data-scan>${view.scan}</p>`}
      <ul class="sessions">${repeat(view.rows, (row) => row.key, (row) => this.#row(row))}</ul>`;
  }

  #status(view: PageView) {
    switch (view.state) {
      case "loading":
        return html`<p class="dim">Reading the sessions…</p>`;
      case "scanning":
        return html`<p class="dim">turns is still looking for sessions…</p>`;
      case "failed":
        return html`<p class="problem">The sessions can't be read: ${view.failure}.</p>`;
      case "empty":
        return html`<p class="dim">No session is reachable. A session shows here while it runs with turns' endpoint.</p>`;
      default:
        return nothing;
    }
  }

  #row(row: RowView) {
    const turn = row.turn === null ? `turn ${NOT_READ}` : `${row.turn}, ${row.open ? "open" : "idle"}`;
    return html`
      <li class="session" data-key=${row.key}>
        <div class="head">
          <span class="harness">${row.harness}</span>
          <code class="id" title=${row.sessionId ?? nothing}>${row.shortId}</code>
          <span class="badge turn" data-open=${row.open === null ? "unknown" : String(row.open)}>${turn}</span>
          ${row.stale ? html`<span class="badge stale" title="The turn moved while these facts were read, so they are not one moment.">stale reading</span>` : nothing}
        </div>
        ${row.cwd === null ? nothing : html`<code class="cwd">${row.cwd}</code>`}
        <p class="facts">${row.facts.join(" · ")}</p>
        ${row.versions === null ? nothing : html`<p class="versions">${row.versions}</p>`}
        ${row.trouble === null ? nothing : html`<p class="problem trouble">${row.trouble}</p>`}
        ${row.readError === null ? nothing : html`<p class="warn read-error">${row.readError}</p>`}
        ${row.agents.length === 0 ? nothing : html`
          <ul class="agents">${repeat(row.agents, (agent) => agent.id, (agent) => html`
            <li class="agent" data-agent=${agent.id}>
              <span class="kind">${agent.kind}</span>
              <span class="state">${agent.state}</span>
              ${agent.final === null ? nothing : html`<span class="final dim">${agent.final}</span>`}
              ${agent.description === null ? nothing : html`<span class="desc">${agent.description}</span>`}
            </li>`)}</ul>`}
        ${row.agentsIncomplete ? html`<p class="dim incomplete">The harness could not list every agent, so some may be missing.</p>` : nothing}
      </li>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, TurnsPage);

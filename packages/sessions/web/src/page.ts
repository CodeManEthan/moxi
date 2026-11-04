// <moxi-sessions-page route="...">: sessions' panel. Its route names the session:
//
// - `""`, the route the menu opens: a harness choice and a session id field, which opens
//   that session's route. An id holding `/`, `?` or `#` is refused with a message, since it
//   would name another route. The page lists no sessions.
// - `<harness>/<session>`: one session, by the id its harness gave it (a Codex thread id,
//   an OpenCode `ses_...` id). An OpenCode child session and a Codex subagent thread are
//   sessions of their own, opened the same way.
// - `claude/<session>/<agent>`: one Claude Code subagent, under its parent's session.
//
// The session view is the one the window block draws (`view.ts`). Selecting a subagent, or
// the parent session, navigates to its route, so the browser's back returns here.

import { css, html, nothing } from "lit";
import { live } from "lit/directives/live.js";
import { navigate } from "/m/ui/client.js";

import { BASE, VIEW } from "./styles.ts";
import { SessionView } from "./view.ts";
import { FORM_LINE, formRoute, HARNESS_LABELS, HARNESSES, parseRoute, routeOf } from "./views.ts";
import type { EntryView, Harness, Target } from "./views.ts";

const TAG = "moxi-sessions-page";

// The harness the form last opened outlives one placement: the shell places a new page
// for each route.
let lastHarness: Harness = "claude";

const STYLES = [BASE, VIEW, css`
  h1 { margin: 0 0 var(--moxi-gap); font-size: 1.4em; }
  .lede { padding-left: 8px; border-left: 3px solid var(--moxi-accent); color: var(--moxi-text-dim); }
  form { display: flex; flex-wrap: wrap; align-items: end; gap: var(--moxi-gap); min-width: 0; }
  form label { display: grid; gap: 2px; min-width: 0; }
  form label > span { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  form label.id { flex: 1 1 14em; }
  form input[type=text] { width: 100%; }
`];

export class SessionsPage extends SessionView {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";

  #loadedFor: string | null = null;
  #harness: Harness = lastHarness;
  #id = "";
  #problem: string | null = null;

  protected willUpdate(): void {
    if (this.route === this.#loadedFor) return;
    this.#loadedFor = this.route;
    const route = parseRoute(this.route);
    this.openSession(route.view === "session" ? route.target : null);
  }

  protected pick(entry: EntryView): void {
    navigate("sessions", entry.route);
  }

  protected openParent(parent: Target): void {
    navigate("sessions", routeOf(parent));
  }

  #submit(event: Event): void {
    event.preventDefault();
    const outcome = formRoute(this.#harness, this.#id);
    if ("problem" in outcome) {
      this.#problem = outcome.problem;
      this.requestUpdate();
      return;
    }
    lastHarness = this.#harness;
    this.#problem = null;
    navigate("sessions", outcome.route);
  }

  render() {
    const route = parseRoute(this.route);
    if (this.sessionGone) return html`<p class="problem" data-line="gone">sessions is no longer installed.</p>`;
    return html`<h1>Sessions</h1>
      ${route.view === "session" ? html`<p class="lede">One session's last turns, read from its harness's own records.
          <button type="button" class="link" data-action="another" @click=${() => navigate("sessions", "")}>Open another session</button></p>
        ${this.renderSession()}`
        : html`${route.view === "unknown" ? html`<p class="problem" data-line="unknown">${route.line}</p>` : nothing}
          ${this.#form()}`}`;
  }

  #form() {
    return html`<section data-part="form">
      <p class="lede">${FORM_LINE}</p>
      <form @submit=${(event: Event) => this.#submit(event)}>
        <label><span>Harness</span>
          <select name="harness" .value=${live(this.#harness)}
            @change=${(event: Event) => { this.#harness = (event.target as HTMLSelectElement).value as Harness; this.#problem = null; this.requestUpdate(); }}>
            ${HARNESSES.map((harness) => html`<option value=${harness} ?selected=${harness === this.#harness}>${HARNESS_LABELS[harness]}</option>`)}
          </select></label>
        <label class="id"><span>Session id</span>
          <input type="text" name="session" autocomplete="off" spellcheck="false" .value=${live(this.#id)}
            @input=${(event: Event) => { this.#id = (event.target as HTMLInputElement).value; this.#problem = null; this.requestUpdate(); }}></label>
        <button type="submit" class="go" data-action="open">Open</button>
      </form>
      ${this.#problem !== null ? html`<p class="problem" data-line="problem" role="alert">${this.#problem}</p>` : nothing}
    </section>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, SessionsPage);

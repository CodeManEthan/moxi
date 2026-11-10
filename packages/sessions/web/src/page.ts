// <moxi-sessions-page route="...">: sessions' panel. Its route names what it shows:
//
// - `""`, the route the menu opens: the list of sessions (`<moxi-sessions-list>`,
//   `list-view.ts`), whose find field takes a session's id.
// - `<harness>/<session>`: one session, by the id its harness gave it (a Codex thread id,
//   an OpenCode `ses_...` id). An OpenCode child session and a Codex subagent thread are
//   sessions of their own, opened the same way.
// - `claude/<session>/<agent>`: one Claude Code subagent, under its parent's session.
//
// A route that names neither shows the list under a line saying so.
//
// The session view is the one the window block draws (`view.ts`). Selecting a subagent, or
// the parent session, navigates to its route, so the browser's back returns here.

import { css, html } from "lit";
import { navigate } from "/m/ui/client.js";

import { BASE, VIEW } from "./styles.ts";
import { SessionView } from "./view.ts";
import { parseRoute, routeOf } from "./views.ts";
import type { EntryView, Target } from "./views.ts";
import "./list-view.ts";

const TAG = "moxi-sessions-page";

const STYLES = [BASE, VIEW, css`
  h1 { margin: 0 0 var(--moxi-gap); font-size: 1.4em; }
  .lede { padding-left: 8px; border-left: 3px solid var(--moxi-accent); color: var(--moxi-text-dim); }
`];

export class SessionsPage extends SessionView {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";

  #loadedFor: string | null = null;

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

  render() {
    const route = parseRoute(this.route);
    if (route.view !== "session") {
      return html`<moxi-sessions-list .line=${route.view === "unknown" ? route.line : null}></moxi-sessions-list>`;
    }
    if (this.sessionGone) return html`<p class="problem" data-line="gone">sessions is no longer installed.</p>`;
    return html`<h1>Sessions</h1>
      <p class="lede">One session's last turns, read from its harness's own records.
        <button type="button" class="link" data-action="another" @click=${() => navigate("sessions", "")}>Open another session</button></p>
      ${this.renderSession()}`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, SessionsPage);

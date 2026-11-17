// <moxi-sessions-page route="...">: sessions' panel. Its route names what it shows:
//
// - `""`, the route the menu opens: the list of sessions (`<moxi-sessions-list>`,
//   `list-view.ts`), whose find field takes a session's id.
// - `<harness>/<session>`: one session's transcript, by the id its harness gave it (a
//   Codex thread id, an OpenCode `ses_...` id). An OpenCode child session and a Codex
//   subagent thread are sessions of their own, opened the same way.
// - `claude/<session>/<agent>`: one Claude Code subagent, under its parent's session.
//
// A route that names neither shows the list under a line saying so.
//
// A transcript is the session view the window block draws (`view.ts`): the last turns,
// "Earlier" to page back, and the session's subagents. Selecting a subagent, or the
// parent session, navigates to its route, so the browser's back returns here.
//
// Over the turns the page draws its own header, from the header's model
// (`transcript.ts`): the link back to the list, the session's name as the list names it
// and `Refresh` on one line; under it the harness, the model and from when to when, with
// `Details`, which shows and hides the header's other facts. A session the list does not
// hold (a subagent, a thread another one started) keeps the view's own heading, in the
// name's place, and its facts in the open. The name and the facts come from the answer an
// opened row of the list reads (`/api/session`), asked for the route's one session when it
// opens, when its tab is shown again and on `Refresh`, beside the turns.
//
// While the turns are on their way the page says so; a session that is not there and a
// read that fails each show the page server's reason, and only that.

import { css, html, nothing } from "lit";
import { api, navigate } from "/m/ui/client.js";

import { Feed } from "./feed.ts";
import { parseSession } from "./list-feed.ts";
import type { SessionAnswer } from "./list-feed.ts";
import { openedStatus } from "./opened.ts";
import { BASE, VIEW } from "./styles.ts";
import { detailFields, headPath, headView } from "./transcript.ts";
import { SessionView } from "./view.ts";
import { headerFields, headingOf, parseRoute, routeOf, windowView } from "./views.ts";
import type { EntryView, Target } from "./views.ts";
import "./list-view.ts";

const TAG = "moxi-sessions-page";

// The transcript at the list's width, with no accent, warning or error colour: a line
// that says something went wrong is in the text colour, and what only informs is dim. A
// record that failed says so in a word in the heavier weight (`failureWord`).
const STYLES = [BASE, VIEW, css`
  :host([data-view="session"]) { max-width: 680px; margin: 0 auto; padding: 4px 0 16px; font-size: 14px; line-height: 20px; }
  .crumb, .vhead { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; min-width: 0;
                   color: var(--moxi-text-dim); }
  .crumb { padding: 0 2px; }
  .crumb .c { min-width: 0; overflow-wrap: anywhere; }
  .crumb .sep { margin: 0 9px; }
  .crumb .nm { color: var(--moxi-text); font-weight: 600; }
  .crumb .wh { margin-left: 8px; font-size: 13px; }
  .vhead { font-size: 13px; line-height: 18px; padding: 5px 2px 0; }
  .vhead .f { min-width: 0; overflow-wrap: anywhere; }
  .crumb a, .crumb button, .vhead button { flex: none; padding: 0; border: 0; border-radius: 0; background: none;
                                           color: inherit; font: inherit; text-decoration: none; cursor: pointer; }
  .crumb button:hover:not(:disabled), .vhead button:hover:not(:disabled) { background: none; }
  .st.dim { color: var(--moxi-text-dim); }
  section { margin-top: 16px; }
  section[data-part="window"], section[data-state="refused"], section[data-state="failed"] {
    border-left: 1px solid var(--moxi-border); }
  .problem, a, button.link { color: var(--moxi-text); }
  .warn { color: var(--moxi-text-dim); }
  .msg[data-role="user"] { border-left-color: var(--moxi-border-strong); }
  .line.result[data-error="true"], .word { color: var(--moxi-text); }
  .rule[data-marker="compaction"], .rule[data-marker="turn_aborted"], .rule[data-marker="rollback"],
  .rule[data-marker="synthetic"] { color: var(--moxi-text-dim); }
`];

export class SessionsPage extends SessionView {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";

  #loadedFor: string | null = null;
  /** The opened row's answer for the one session the route names: its name and its facts. */
  #head: Feed<SessionAnswer> | null = null;
  /** Whether `Details` is open. */
  #details = false;

  protected willUpdate(): void {
    if (this.route === this.#loadedFor) return;
    this.#loadedFor = this.route;
    const route = parseRoute(this.route);
    const target = route.view === "session" ? route.target : null;
    this.#head?.close();
    this.#head = null;
    this.#details = false;
    // A subagent is not a session of the list, so its facts are not asked for.
    const path = target !== null ? headPath(target) : null;
    if (path !== null) this.#head = new Feed<SessionAnswer>(() => api("sessions", path), parseSession, () => this.requestUpdate());
    if (target !== null) this.setAttribute("data-view", "session");
    else this.removeAttribute("data-view");
    this.openSession(target);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#head?.close();
    this.#head = null;
  }

  protected refreshSession(): void {
    super.refreshSession();
    void this.#head?.refresh();
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
    return this.#transcript(route.target);
  }

  #transcript(target: Target) {
    const feed = this.windowFeed;
    const status = openedStatus(feed);
    if (this.sessionGone || feed === null || status?.state === "gone") {
      return html`<p class="st" data-line="gone" role="status">${status?.line ?? "sessions is no longer installed."}</p>`;
    }
    const head = headView(target, this.#head);
    const answer = feed.value;
    const fields = answer === null ? null
      : head.mode === "plain" ? headerFields(answer)
      : head.mode === "lines" && this.#details ? detailFields(answer, head.home) : null;
    const back = (event: MouseEvent): void => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate("sessions", head.back.route);
    };
    return html`<div data-part="head">
        <div class="crumb" data-line="crumb"><span class="c"><a href=${`/m/sessions/${head.back.route}`} data-action="back"
          @click=${back}>${head.back.text}</a>${head.name !== null ? html`<span class="sep" aria-hidden="true">›</span><span
          class="nm" data-line="name">${head.name}</span>${head.where !== null
            ? html`<span class="wh" data-line="where">${head.where}</span>` : nothing}`
          : head.mode === "plain" ? html`<span class="sep" aria-hidden="true">›</span><span class="nm"
            data-line="heading">${headingOf(answer, target)}</span>` : nothing}</span>
          <button type="button" data-action="refresh" ?disabled=${feed.reading}
            @click=${() => this.refreshSession()}>${head.refresh}</button></div>
        ${head.facts !== null || head.details !== null ? html`<div class="vhead" data-line="session">
          <span class="f" data-line="facts">${head.facts ?? ""}</span>
          ${head.details !== null ? html`<button type="button" data-action="details"
            aria-expanded=${this.#details ? "true" : "false"} @click=${() => {
              this.#details = !this.#details;
              this.requestUpdate();
            }}>${head.details}</button>` : nothing}</div>` : nothing}
      </div>
      <section data-part="window" data-state=${windowView(feed).state}>
        ${status !== null ? html`<p class=${status.state === "waiting" ? "st dim" : "st"} data-line=${status.state}
          role="status">${status.line}</p>` : nothing}
        ${answer !== null ? this.renderTurns(target, answer, fields) : nothing}
      </section>
      ${this.renderSubagents()}`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, SessionsPage);

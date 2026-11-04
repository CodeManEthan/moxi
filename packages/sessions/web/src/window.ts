// <moxi-sessions-window harness="..." session="..." agent="...">: one session's last
// turns, the block another module's page places with `place(host, "sessions", "window",
// attrs)`. `harness` and `session` name the session; `agent` names a Claude Code subagent
// under it. It reads them once, when it connects: a host that wants another session places
// a new block.
//
// It draws the same view as sessions' page (`view.ts`), with a link that opens the page at
// the session's route. Selecting a subagent, or the parent session, opens it in the block
// itself, with "Back" to return to the session before.

import { css, html, nothing } from "lit";
import { navigate } from "/m/ui/client.js";

import { BASE, VIEW } from "./styles.ts";
import { SessionView } from "./view.ts";
import { isHarness, routeOf } from "./views.ts";
import type { EntryView, Target } from "./views.ts";

const TAG = "moxi-sessions-window";

const STYLES = [BASE, VIEW, css`
  section:first-of-type { margin-top: 0; }
`];

export class SessionsWindow extends SessionView {
  static styles = STYLES;

  /** The sessions opened in the block before this one, the newest last. */
  #back: Target[] = [];
  #problem: string | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    const harness = this.getAttribute("harness") ?? "";
    const session = this.getAttribute("session") ?? "";
    const agent = this.getAttribute("agent");
    this.#back = [];
    if (!isHarness(harness) || session === "") {
      this.#problem = "This block names no session it can read: it needs a harness and a session id.";
      this.openSession(null);
    } else if (agent !== null && agent !== "" && harness !== "claude") {
      this.#problem = "Only a Claude Code subagent opens under its parent's session.";
      this.openSession(null);
    } else {
      this.#problem = null;
      this.openSession({ harness, session, agent: agent !== null && agent !== "" ? agent : null });
    }
  }

  #go(target: Target): void {
    const now = this.target;
    if (now !== null) this.#back.push(now);
    this.openSession(target);
  }

  protected pick(entry: EntryView): void {
    if (entry.target !== null) this.#go(entry.target);
  }

  protected openParent(parent: Target): void {
    this.#go(parent);
  }

  #return(): void {
    const last = this.#back.pop();
    if (last !== undefined) this.openSession(last);
  }

  render() {
    if (this.#problem !== null) {
      return html`<section data-part="window" data-state="refused"><p class="problem" data-line="refused">${this.#problem}</p></section>`;
    }
    const target = this.target;
    if (target === null) return nothing;
    if (this.sessionGone) return html`<p class="problem" data-line="gone">sessions is no longer installed.</p>`;
    const route = routeOf(target);
    const actions = html`
      ${this.#back.length ? html`<button type="button" data-action="back" @click=${() => this.#return()}>Back</button>` : nothing}
      <a href=${`/m/sessions/${route}`} data-action="panel" @click=${(event: Event) => {
        event.preventDefault();
        navigate("sessions", route);
      }}>Open in Sessions</a>`;
    return this.renderSession(actions);
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, SessionsWindow);

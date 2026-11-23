// <moxi-turns-page route="...">: turns' panel, the live list of the sessions turns can
// reach. Under the shell's top line, from top to bottom:
//
// - The first row, while the route fails (`Moxi can't read the live sessions`, what it
//   answered, and when it was last read) or the page server's last scan failed (`Moxi
//   couldn't look for sessions` and why).
// - The groups, each under its dim label at the left: `1 problem`, `<n> idle`, `<n>
//   working`. A session is a `<moxi-ui-row>` whose words come from `rows-model.ts`, with
//   `Open` (`Close` while open) in its `acts-one` slot. While the route fails the last
//   rows read stay, dim, with no acts, and none opens.
// - The empty state's line, with the reach line under it.
// - The foot: the reach line and `Settings › turns` at its right, then a dim line for each
//   of its notes (an address whose session isn't running).
//
// It places what `frame-model.ts` and `rows-model.ts` return and decides nothing about
// what is shown; every word comes from there and goes into the page as text. One row is
// open at a time, and the page writes what is open into its address (`session/<key>`), in
// place of the entry it is on, so Back leaves the page. It only reads: nothing on it acts
// on a session.
//
// It reads `/api/sessions`, then follows the topic `sessions`, which carries the whole list
// at each change, and reads the route again when its tab is shown again. With launcher
// installed, and not on the phone, it reads launcher's `/api/running` the same way, then
// its topic `running`, and a launch paired one to one with a row by session id names it
// (`launch-model.ts`); a read that fails names none, and nothing is said of it. It redraws
// once a minute, for the day words across midnight.

import { css, html, LitElement, noChange, nothing } from "lit";
import type { TemplateResult } from "lit";
import { Directive, directive, PartType } from "lit/directive.js";
import type { ElementPart, PartInfo } from "lit/directive.js";
import { repeat } from "lit/directives/repeat.js";
import { api, gateway, installed, navigate, setAddress, subscribe, WaitClock } from "/m/ui/client.js";

import { Feed, parseList } from "./feed.ts";
import type { Session, SessionList } from "./feed.ts";
import {
  addressRoute, CLOSE, COPIED, COPIED_MS, COPY, frameOf, OPEN, parseAddress, READING_AFTER_MS, readingLine, REDRAW_MS,
  SETTINGS_LINK, SETTINGS_ROUTE,
} from "./frame-model.ts";
import type { Address, Alert, Frame, ReachLine } from "./frame-model.ts";
import { launchesOf, parseRunning } from "./launch-model.ts";
import type { Running } from "./launch-model.ts";
import { COPY_ID, LABELS, openedOf } from "./opened-model.ts";
import type { AgentsLines } from "./opened-model.ts";
import { LOCAL, rowLines } from "./rows-model.ts";
import type { Group, Launch, RowLines } from "./rows-model.ts";

const TAG = "moxi-turns-page";

/** Sets each attribute of a map on the element it is bound to, and removes each one whose
 * value is null. */
class Attributes extends Directive {
  constructor(part: PartInfo) {
    super(part);
    if (part.type !== PartType.ELEMENT) throw new Error("attributes() binds to an element");
  }

  render(_values: Record<string, string | null>): unknown {
    return noChange;
  }

  update(part: ElementPart, [values]: [Record<string, string | null>]): unknown {
    for (const [name, value] of Object.entries(values)) {
      if (value === null) part.element.removeAttribute(name);
      else if (part.element.getAttribute(name) !== value) part.element.setAttribute(name, value);
    }
    return noChange;
  }
}
const attributes = directive(Attributes);

/** A row's attributes for its lines; `acts` is false for the last rows kept while the
 * route fails, which neither open nor toggle. */
function rowAttributes(lines: RowLines, open: boolean, acts: boolean): Record<string, string | null> {
  const flag = (on: boolean): string | null => (on ? "" : null);
  return {
    name: lines.name, where: lines.where, fact: lines.fact,
    lead: lines.lead, "lead-heavy": flag(lines.leadHeavy), rest: lines.rest, "rest-strong": flag(lines.restStrong),
    tail: lines.tail, note: lines.note, toggles: flag(acts), open: flag(open && acts),
  };
}

// The Sessions page's two-line rows under a dim label at the left, at its width. Text
// tokens only: the accent is the focus ring alone, and there is no warning colour.
const STYLES = css`
  :host { display: block; min-width: 0; max-width: 680px; margin: 0 auto; padding: 4px 0 16px;
          color: var(--moxi-text); font-family: var(--moxi-font-sans); font-size: 14px; line-height: 20px; }
  * { box-sizing: border-box; outline-color: var(--moxi-accent); }
  p { margin: 0; }
  button { font: inherit; color: inherit; background: none; border: 0; padding: 0; margin: 0; cursor: pointer;
           text-align: left; }
  a { color: inherit; text-decoration: none; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  code { font: 12px/18px var(--moxi-font-mono); color: var(--moxi-text); overflow-wrap: anywhere; }

  .waiting { padding: 12px 2px 0; color: var(--moxi-text-dim); }
  .alert { padding: 12px 2px 0; overflow-wrap: anywhere; }
  .alert .l1 { font-weight: 600; }
  .alert .l2 { font-size: 13px; line-height: 18px; }
  .alert .l3 { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  .grps { margin-top: 16px; border-top: 1px solid var(--moxi-border); }
  .grp { display: grid; grid-template-columns: 96px minmax(0, 1fr); border-bottom: 1px solid var(--moxi-border);
         padding: 5px 2px; }
  .grp .gk { color: var(--moxi-text-dim); padding-top: 7px; }
  .grp .gl { grid-column: 2; min-width: 0; }
  moxi-ui-row[open] + moxi-ui-row { border-top: 1px solid var(--moxi-border); padding-top: 3px; }
  .do { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  /* The last rows read while the route fails: every word dim. */
  .stale moxi-ui-row { --moxi-text: var(--moxi-text-dim); }

  .quietline { font-size: 22px; line-height: 32px; text-align: center; letter-spacing: -0.005em; padding: 150px 0 14px; }
  .first { color: var(--moxi-text-dim); line-height: 1.6; text-align: center; overflow-wrap: anywhere; }
  .first p { max-width: 520px; margin: 0 auto; }

  .foot { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; padding: 12px 2px 0;
          font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  .foot .reach { min-width: 0; overflow-wrap: anywhere; }
  .foot a { flex: none; margin-left: auto; }
  .reach button, .first button { color: var(--moxi-text); }
  /* The opened row: one line each, its dim label at the left. A busy agent's state is in
     the text colour, the rest of the agents' states dim. */
  .det { margin: 9px 0 3px; padding: 8px 0 0; border-top: 1px solid var(--moxi-border); font-size: 13px; line-height: 18px;
         cursor: auto; }
  .det .r { display: grid; grid-template-columns: 92px minmax(0, 1fr); padding: 3px 0; align-items: baseline; }
  .det .k, .det .d { color: var(--moxi-text-dim); }
  .det .f { min-width: 0; overflow-wrap: anywhere; }
  .det .f p { margin: 0; }
  .det .foot { padding: 9px 0 0 92px; }
  .det .foot button { color: var(--moxi-text); }
  .note { font-size: 13px; line-height: 18px; padding: 4px 2px 0; color: var(--moxi-text-dim); overflow-wrap: anywhere; }

  @media (max-width: 720px) {
    :host { padding-top: 10px; }
    .grp { display: block; padding: 4px 0 6px; }
    .grp .gk { padding-top: 9px; font-size: 13px; }
    .quietline { padding-top: 60px; }
    .foot { flex-wrap: wrap; }
    .det .r { grid-template-columns: minmax(0, 1fr); }
    .det .foot { padding-left: 0; }
  }
`;

export class TurnsPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";
  #feed: Feed<SessionList> | null = null;
  #stop: (() => void) | null = null;
  /** Launcher's running launches as last read; null when not read, not readable, or not
   * installed. */
  #running: Running | null = null;
  #stopRunning: (() => void) | null = null;
  #runningOut = false;
  #runningAgain = false;
  /** The address: the route as given, then each row opened or closed here. */
  #address: Address = { key: null, unknown: false };
  /** The first read's wait, for its `Reading…`. */
  #waited = new WaitClock(READING_AFTER_MS);
  /** Which copy act reads `Copied`. */
  #copied: string | null = null;
  #copyTimer: ReturnType<typeof setTimeout> | undefined;
  #redrawTimer: ReturnType<typeof setInterval> | undefined;
  #shown = (): void => {
    if (document.visibilityState !== "visible") return;
    void this.#feed?.refresh();
    void this.#readRunning();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#waited.start(() => this.requestUpdate());
    const feed = new Feed<SessionList>(() => api("turns", "api/sessions"), parseList,
                                       () => this.requestUpdate());
    this.#feed = feed;
    void feed.refresh().then(() => {
      if (this.#feed === feed) this.#stop = subscribe("turns", "sessions", (data) => feed.topic(data));
    });
    if (this.#readsLauncher()) {
      void this.#readRunning().then(() => {
        if (this.#feed === feed) {
          this.#stopRunning = subscribe("launcher", "running", (data) => {
            this.#running = parseRunning(data);
            this.requestUpdate();
          });
        }
      });
    }
    document.addEventListener("visibilitychange", this.#shown);
    this.#redrawTimer = setInterval(() => this.requestUpdate(), REDRAW_MS);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#feed = null;
    this.#stop?.();
    this.#stop = null;
    this.#stopRunning?.();
    this.#stopRunning = null;
    this.#running = null;
    document.removeEventListener("visibilitychange", this.#shown);
    this.#waited.stop();
    clearTimeout(this.#copyTimer);
    clearInterval(this.#redrawTimer);
  }

  protected willUpdate(changed: Map<PropertyKey, unknown>): void {
    if (changed.has("route")) this.#address = parseAddress(this.route);
  }

  // -- launcher ----------------------------------------------------------------------------

  /** Whether the panel reads launcher: installed, and not on the phone. */
  #readsLauncher(): boolean {
    return installed("launcher") && !gateway();
  }

  /** Reads launcher's running launches; one read at a time, and one more after it when
   * asked meanwhile. A read that fails names no row. */
  async #readRunning(): Promise<void> {
    if (!this.#readsLauncher()) return;
    if (this.#runningOut) {
      this.#runningAgain = true;
      return;
    }
    this.#runningOut = true;
    try {
      do {
        this.#runningAgain = false;
        let answer;
        try {
          answer = await api("launcher", "api/running");
        } catch {
          answer = { status: 0, data: null };
        }
        if (!this.isConnected) return;
        this.#running = answer.status === 200 ? parseRunning(answer.data) : null;
        this.requestUpdate();
      } while (this.#runningAgain);
    } finally {
      this.#runningOut = false;
    }
  }

  // -- the address -------------------------------------------------------------------------

  /** Opens `key`'s row, or closes the open one with null, and writes the address in place
   * of the entry the page is on. */
  #open(key: string | null): void {
    this.#address = { key, unknown: false };
    try {
      setAddress("turns", addressRoute(key));
    } catch {
      // turns' panel is not the one shown (the page is placed elsewhere): no address to write.
    }
    this.requestUpdate();
  }

  // -- acts --------------------------------------------------------------------------------

  /** Puts `text` on the clipboard; the act named `what` then reads `Copied` for a while. */
  async #copy(what: string, text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    this.#copied = what;
    clearTimeout(this.#copyTimer);
    this.#copyTimer = setTimeout(() => {
      this.#copied = null;
      this.requestUpdate();
    }, COPIED_MS);
    this.requestUpdate();
  }

  #copyButton(what: string, text: string, label: string = COPY) {
    return html`<button type="button" data-action=${`copy-${what}`}
      @click=${() => void this.#copy(what, text)}>${this.#copied === what ? COPIED : label}</button>`;
  }

  #link(module: string, route: string, text: string, action: string) {
    return html`<a href=${`/m/${module}/${route}`} data-action=${action}
      @click=${(event: MouseEvent) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigate(module, route);
      }}>${text}</a>`;
  }

  // -- drawing -----------------------------------------------------------------------------

  /** The panel now. */
  protected frame(): Frame | null {
    const feed = this.#feed;
    if (feed === null) return null;
    return frameOf(feed, this.#address, installed("launcher"), LOCAL.day(Date.now() / 1000));
  }

  render() {
    const frame = this.frame();
    if (frame === null || frame.state === "gone") return nothing;
    const reading = readingLine(frame, this.#waited.waitedMs());
    const today = LOCAL.day(Date.now() / 1000);
    const failed = frame.state === "failed";
    const home = this.#feed?.value?.home ?? null;
    const launches = launchesOf(this.#readsLauncher() ? this.#running : null, this.#feed?.value?.sessions ?? []);
    return html`<div data-state=${frame.state}>
      ${reading !== null ? html`<p class="waiting" data-line="reading" role="status">${reading}</p>` : nothing}
      ${frame.alert !== null ? this.#alert(frame.alert) : nothing}
      ${frame.groups.length > 0 ? html`<div class=${failed ? "grps stale" : "grps"} data-part="sessions">${
        repeat(frame.groups, (group) => group.group, (group) => html`<section class="grp" data-group=${group.group}>
          <div class="gk" data-line="label">${group.label}</div>
          <div class="gl">${repeat(group.sessions, (session) => session.key,
                                   (session) => this.#row(session, group.group, frame.open, !failed, today,
                                                          home, launches.get(session.key) ?? null))}</div>
        </section>`)}</div>` : nothing}
      ${frame.empty !== null ? html`<div class="quietline" data-line="empty">${frame.empty}</div>
        ${frame.reach !== null ? html`<div class="first" data-part="reach"><p>${this.#reach(frame.reach)}</p></div>`
          : nothing}` : nothing}
      ${frame.state === "loading" ? nothing : html`<div class="foot" data-part="foot">${frame.reach !== null
        && frame.empty === null ? html`<span class="reach" data-line="reach">${this.#reach(frame.reach)}</span>` : nothing}${
        this.#link("ui", SETTINGS_ROUTE, SETTINGS_LINK, "settings")}</div>`}
      ${frame.notes.map((line) => html`<p class="note" data-line="note" role="status">${line}</p>`)}
    </div>`;
  }

  #alert(alert: Alert) {
    return html`<div class="alert" data-part="alert" role="status">
      <p class="l1" data-line="lead">${alert.lead}</p>
      <p class="l2" data-line="rest">${alert.rest}</p>
      ${alert.lastRead !== null ? html`<p class="l3" data-line="last-read">${alert.lastRead}</p>` : nothing}
    </div>`;
  }

  /** The reach line's words, with the install command as code and `Copy` where it names one. */
  #reach(line: ReachLine): TemplateResult {
    return html`${line.text}${line.command !== null ? html`<code>${line.command}</code> ${
      this.#copyButton("command", line.command)}` : nothing}${line.after ?? nothing}`;
  }

  #row(session: Session, group: Group, openKey: string | null, acts: boolean, today: string, home: string | null,
       launch: Launch | null) {
    const open = acts && session.key === openKey;
    const lines = rowLines(session, group, today, launch);
    const toggle = (): void => this.#open(open ? null : session.key);
    return html`<moxi-ui-row data-key=${session.key} ${attributes(rowAttributes(lines, open, acts))}
        @moxi-row-toggle=${toggle}>
      ${acts ? html`<button type="button" slot="acts-one" class="do" data-action="open"
        aria-expanded=${open ? "true" : "false"} @click=${toggle}>${open ? CLOSE : OPEN}</button>` : nothing}
      ${open ? this.#opened(session, home, today) : nothing}
    </moxi-ui-row>`;
  }

  /** The opened row's lines, each under its label and only when its fact is there, then
   * `Copy id` while the session has an id. */
  #opened(session: Session, home: string | null, today: string) {
    const opened = openedOf(session, home, today);
    const line = (key: keyof typeof LABELS, body: unknown) => html`<div class="r" data-line=${key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}>
      <span class="k">${LABELS[key]}</span><div class="f">${body}</div></div>`;
    return html`<div class="det" data-part="opened">
      ${opened.now !== null ? line("now", opened.now) : nothing}
      ${opened.agents !== null ? line("agents", this.#agents(opened.agents)) : nothing}
      ${opened.promptBox !== null ? line("promptBox", opened.promptBox) : nothing}
      ${line("session", opened.session)}
      ${opened.details !== "" ? line("details", opened.details) : nothing}
      ${opened.copyId !== null ? html`<div class="foot">${this.#copyButton("id", opened.copyId, COPY_ID)}</div>`
        : nothing}
    </div>`;
  }

  #agents(agents: AgentsLines) {
    return html`${agents.entries.map((entry) => html`<p data-agent=${entry.id}>${entry.text} · <span
        class=${entry.busy ? "" : "d"} data-busy=${entry.busy ? "true" : "false"}>${entry.state}</span></p>`)}${
      agents.notKnown !== null ? html`<p class="d" data-line="not-known">${agents.notKnown}</p>` : nothing}`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, TurnsPage);

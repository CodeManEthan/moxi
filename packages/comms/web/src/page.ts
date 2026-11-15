// <moxi-comms-page route="...">: comms' page. Under the shell's top line, from top to
// bottom: the first row `The comms daemon isn't answering` (or the roster's error) while the
// roster isn't up, with `Last read <time>` once a read had answered; the group `N agents`,
// one `<moxi-ui-row>` per agent name; the empty state's sentences where there is no agent;
// the foot, `Comms is answering · read <time>` and `Comms settings`; then one dim line for
// each read that failed and for an address the page doesn't know.
//
// It places what the frame's model (`frame-model.ts`) and the rows' model
// (`agents-model.ts`) return and decides nothing about what is shown; every word comes from
// there and goes into the page as text. A row's lines, its `Message` act, its `Reply` act
// and its address all open it in place; one thing is open at a time, and the page writes
// what is open into its address with `history.replaceState` (`address-model.ts`), so Back
// leaves the page. While the roster isn't up the last rows read stay, dim, with no acts,
// and none opens.
//
// It reads `/api/roster`, then follows the topic `roster`. It reads `/api/mail-state` when
// it opens, when the roster's waiting mail or its bound names change, when its tab is shown
// again and every 30 s while the tab is visible.

import { css, html, LitElement, nothing } from "lit";
import { ifDefined } from "lit/directives/if-defined.js";
import { repeat } from "lit/directives/repeat.js";
import { api, installed, navigate, subscribe } from "/m/ui/client.js";

import { addressWrite, NOTHING_OPEN, parseAddress, sameAddress } from "./address-model.ts";
import type { Address } from "./address-model.ts";
import { parseMailState, parsePageRoster } from "./agents-model.ts";
import type { Act, MailState, PageRoster } from "./agents-model.ts";
import { Feed, parseRoster } from "./feed.ts";
import {
  ACT_WORDS, CLOSE, COPIED, COPIED_MS, COPY, DOWN_LABEL, frameModel, LAUNCH_FROM_HOME, READING_AFTER_MS, SETTINGS_LINK,
  SETTINGS_ROUTE,
} from "./frame-model.ts";
import type { Down, Empty, Frame, FrameRow, Placed } from "./frame-model.ts";
import { newestDay } from "./words.ts";

const TAG = "moxi-comms-page";
const TICK_MS = 1000;
/** How often the mail state is read while the tab is visible. */
export const MAIL_EVERY_MS = 30_000;

// The Sessions page's two-line rows under a dim label at the left, at its width. The accent
// is the focus ring alone here; what only informs is dim.
const STYLES = css`
  :host { display: block; min-width: 0; max-width: 680px; margin: 0 auto; padding: 4px 0 16px;
          color: var(--moxi-text); font-family: var(--moxi-font-sans); font-size: 14px; line-height: 20px; }
  * { box-sizing: border-box; outline-color: var(--moxi-accent); }
  p { margin: 0; }
  button { font: inherit; color: inherit; background: none; border: 0; padding: 0; margin: 0; cursor: pointer;
           text-align: left; }
  a { color: inherit; text-decoration: none; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  code { font: 12px/18px var(--moxi-font-mono); color: var(--moxi-text); }

  .waiting { padding: 12px 2px 0; color: var(--moxi-text-dim); }
  .grps { margin-top: 16px; border-top: 1px solid var(--moxi-border); }
  .grp { display: grid; grid-template-columns: 96px minmax(0, 1fr); border-bottom: 1px solid var(--moxi-border);
         padding: 5px 2px; }
  .grp .gk { color: var(--moxi-text-dim); padding-top: 7px; }
  .grp .gl { grid-column: 2; min-width: 0; }
  moxi-ui-row[open] + moxi-ui-row { border-top: 1px solid var(--moxi-border); padding-top: 3px; }
  .do { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  /* The last rows read while the daemon isn't answering: every word dim. */
  .stale moxi-ui-row { --moxi-text: var(--moxi-text-dim); }

  .down { padding: 7px 0; }
  .down .l1 { font-weight: 600; overflow-wrap: anywhere; }
  .down .l2 { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; font-size: 13px;
              line-height: 18px; color: var(--moxi-text-dim); }
  .down .l2 button { flex: none; color: var(--moxi-text); }
  .note { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); padding: 12px 2px 0; }

  .det { margin: 9px 0 3px; padding: 8px 0 0; border-top: 1px solid var(--moxi-border); font-size: 13px; line-height: 18px;
         cursor: auto; }

  .quietline { font-size: 22px; line-height: 32px; text-align: center; letter-spacing: -0.005em; padding: 150px 0 14px; }
  .first { color: var(--moxi-text-dim); line-height: 1.6; text-align: center; }
  .first p { max-width: 520px; margin: 0 auto; }
  .first p + p { margin-top: 10px; }
  .first .x { color: var(--moxi-text); border-bottom: 1px solid var(--moxi-border); }
  .first button.x { line-height: inherit; }

  .cfoot { display: flex; justify-content: space-between; gap: 14px; font-size: 13px; line-height: 18px;
           color: var(--moxi-text-dim); padding: 12px 2px 0; }
  .cfoot a { flex: none; }
  .last { font-size: 13px; line-height: 18px; padding: 12px 2px 0; overflow-wrap: anywhere; color: var(--moxi-text-dim); }

  @media (max-width: 720px) {
    :host { padding-top: 10px; }
    .grp { display: block; padding: 4px 0 6px; }
    .grp .gk { padding-top: 9px; font-size: 13px; }
    .quietline { padding-top: 60px; }
    .cfoot { padding-top: 14px; }
  }
`;

/** The key of what the mail state follows on the roster: its waiting mail and bound names. */
function mailKey(value: PageRoster): string {
  return JSON.stringify([value.waiting.map((entry) => [entry.qualified, entry.count, entry.since]),
                         value.rows.filter((row) => row.live).map((row) => row.qualified)]);
}

export class CommsPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
    tick: { state: true },
  };

  route = "";
  tick = 0;
  #feed: Feed<PageRoster> | null = null;
  #mail: Feed<MailState> | null = null;
  /** The last roster that was up, and its time on the server's clock. */
  #kept: { value: PageRoster; at: number } | null = null;
  #taken: PageRoster | null = null;
  #mailKey: string | null = null;
  #today: string | null = null;
  #at: Address = NOTHING_OPEN;
  #placed: Placed | null = null;
  #openedAt = 0;
  /** Which copy act reads `Copied`. */
  #copied: string | null = null;
  #stop: (() => void) | null = null;
  #tickTimer: ReturnType<typeof setInterval> | undefined;
  #mailTimer: ReturnType<typeof setInterval> | undefined;
  #copyTimer: ReturnType<typeof setTimeout> | undefined;
  #readingTimer: ReturnType<typeof setTimeout> | undefined;

  #shown = (): void => {
    if (document.visibilityState !== "visible") return;
    void this.#feed?.refresh();
    void this.#mail?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#openedAt = Date.now();
    const feed = new Feed<PageRoster>(() => api("comms", "api/roster"), (data) => parsePageRoster(data, parseRoster),
                                      () => this.#rosterRead());
    this.#feed = feed;
    this.#mail = new Feed<MailState>(() => api("comms", "api/mail-state"), parseMailState, () => {
      this.#today = newestDay(this.#today, this.#mail?.value?.today ?? null);
      this.requestUpdate();
    }, { clocked: false });
    void feed.refresh().then(() => {
      if (this.#feed === feed) this.#stop = subscribe("comms", "roster", (data) => feed.topic(data));
    });
    void this.#mail.refresh();
    document.addEventListener("visibilitychange", this.#shown);
    this.#tickTimer = setInterval(() => { this.tick += 1; }, TICK_MS);
    this.#readingTimer = setTimeout(() => this.requestUpdate(), READING_AFTER_MS);
    this.#mailTimer = setInterval(() => {
      if (document.visibilityState === "visible") void this.#mail?.refresh();
    }, MAIL_EVERY_MS);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#mail?.close();
    this.#feed = this.#mail = null;
    this.#stop?.();
    this.#stop = null;
    document.removeEventListener("visibilitychange", this.#shown);
    clearInterval(this.#tickTimer);
    clearInterval(this.#mailTimer);
    clearTimeout(this.#copyTimer);
    clearTimeout(this.#readingTimer);
  }

  protected willUpdate(changed: Map<PropertyKey, unknown>): void {
    if (changed.has("route")) {
      // A new placement opens what its route names, written as the page writes it.
      const named = parseAddress(this.route);
      this.#placed = null;
      this.#setAt(named);
    }
  }

  /** After every change of the roster's feed: a value that is up is kept with its time, and
   * the mail state is read again when the waiting mail or the bound names changed. */
  #rosterRead(): void {
    const feed = this.#feed;
    if (feed === null) return;
    const value = feed.value;
    const now = feed.now();
    if (value !== null && value !== this.#taken) {
      this.#taken = value;
      this.#today = newestDay(this.#today, value.today);
      if (value.state === "up" && now !== null) {
        this.#kept = { value, at: now };
        const key = mailKey(value);
        if (this.#mailKey !== null && key !== this.#mailKey) void this.#mail?.refresh();
        this.#mailKey = key;
      }
    }
    this.requestUpdate();
  }

  // -- acts --------------------------------------------------------------------------------

  /** Opens `address`, closing what was open. */
  open(address: Address): void {
    if (sameAddress(address, this.#at)) return;
    this.#placed = null;
    this.#setAt(address);
    this.requestUpdate();
  }

  #setAt(address: Address): void {
    this.#at = address;
    const path = addressWrite(address.piece === "unknown" ? NOTHING_OPEN : address, location.pathname);
    if (path !== null) history.replaceState(history.state, "", path);
  }

  #toggle(item: FrameRow): void {
    this.open(item.open ? NOTHING_OPEN : { piece: "agent", qualified: item.row.lead });
  }

  #act(act: Act): void {
    if (act.kind === "reply") this.open({ piece: "reply", message_id: act.message_id });
    else if (act.kind === "message") this.open({ piece: "agent", qualified: act.to });
  }

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

  #link(module: string, route: string, text: string, extra: { cls?: string; action: string }) {
    return html`<a class=${ifDefined(extra.cls)} href=${`/m/${module}/${route}`} data-action=${extra.action}
      @click=${(event: MouseEvent) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigate(module, route);
      }}>${text}</a>`;
  }

  // -- drawing -----------------------------------------------------------------------------

  /** The frame now; the address's placement is kept once its reads have answered. */
  protected frame(): Frame | null {
    const feed = this.#feed;
    const mail = this.#mail;
    if (feed === null || mail === null) return null;
    const frame = frameModel({
      roster: feed, kept: this.#kept, mail, now: feed.now(), today: this.#today, cache: installed("cache"),
      launcher: installed("launcher"), waitedMs: Date.now() - this.#openedAt, at: this.#at, placed: this.#placed,
    });
    this.#placed = frame.placed;
    return frame;
  }

  render() {
    const frame = this.frame();
    if (frame === null || frame.state === "gone") return nothing;
    if (frame.state === "loading") {
      return html`${frame.reading !== null ? html`<p class="waiting" data-line="reading" role="status">${frame.reading}</p>`
        : nothing}${this.#lines(frame)}`;
    }
    return html`
      ${frame.down !== null ? this.#down(frame.down) : nothing}
      ${frame.lastRead !== null ? html`<p class="note" data-line="last-read">${frame.lastRead}</p>` : nothing}
      ${frame.label !== null ? html`<div class=${frame.stale ? "grps stale" : "grps"} data-part="agents">
        <section class="grp" data-group="agents"><div class="gk" data-line="label">${frame.label}</div>
          <div class="gl">${repeat(frame.rows, (item) => item.row.name, (item) => this.#row(item, frame.stale))}</div>
        </section></div>` : nothing}
      ${frame.empty !== null ? this.#empty(frame.empty) : nothing}
      <div class="cfoot" data-part="foot"><span data-line="answering">${frame.foot ?? ""}</span>${
        this.#link("ui", SETTINGS_ROUTE, SETTINGS_LINK, { action: "settings" })}</div>
      ${this.#lines(frame)}`;
  }

  #lines(frame: Frame) {
    return frame.lines.map((line) => html`<p class="last" data-line=${`foot-${line.key}`} role="status">${line.text}</p>`);
  }

  #down(down: Down) {
    const copy = down.copy;
    return html`<div class="grps" data-part="down"><section class="grp"><div class="gk">${DOWN_LABEL}</div><div class="gl">
      <div class="down"><div class="l1" data-line="down">${down.what}</div>${down.line !== null
        ? html`<div class="l2"><span>${copy !== null ? html`<code>${copy}</code>` : nothing}${down.line}</span>${copy !== null
          ? html`<button type="button" data-action="copy-doctor" @click=${() => void this.#copy("doctor", copy)}>${
            this.#copied === "doctor" ? COPIED : COPY}</button>` : nothing}</div>` : nothing}</div>
    </div></section></div>`;
  }

  #empty(empty: Empty) {
    const command = empty.command;
    return html`<div class="quietline" data-line="empty">${empty.line}</div>
      <div class="first" data-part="empty"><p>${empty.sentence}</p>
        ${empty.launch ? html`<p>${this.#link("ui", "", LAUNCH_FROM_HOME, { cls: "x", action: "launch-home" })}</p>` : nothing}
        ${command !== null ? html`<p><code>${command}</code> <button type="button" class="x" data-action="copy-command"
          @click=${() => void this.#copy("command", command)}>${this.#copied === "command" ? COPIED : COPY}</button></p>`
          : nothing}</div>`;
  }

  #row(item: FrameRow, stale: boolean) {
    const { row } = item;
    const toggle = (): void => this.#toggle(item);
    return html`<moxi-ui-row ?toggles=${!stale} data-name=${row.name} name=${row.name} where=${ifDefined(row.where ?? undefined)}
        fact=${ifDefined(row.fact?.text)} ?fact-heavy=${row.fact?.heavy ?? false} lead=${row.line.lead}
        ?lead-heavy=${row.line.heavy} lead-more=${ifDefined(row.line.more ?? undefined)}
        note=${ifDefined(item.note ?? undefined)} ?open=${item.open} @moxi-row-toggle=${toggle}>
      ${stale ? nothing : item.open
        ? html`<button type="button" slot="acts" class="do" data-action="close" aria-expanded="true" @click=${toggle}>${
          CLOSE}</button>`
        : row.acts.filter((act) => act.kind !== "keep-warm").map((act) => html`<button type="button" slot="acts" class="do"
          data-action=${act.kind} @click=${() => this.#act(act)}>${ACT_WORDS[act.kind]}</button>`)}
      ${item.open && item.to !== null ? this.opened(item, item.to) : nothing}
    </moxi-ui-row>`;
  }

  /** What an opened agent row shows under its lines, speaking for the address `to`. */
  protected opened(item: FrameRow, to: string): unknown {
    return html`<div class="det" data-part="opened" data-to=${to}></div>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, CommsPage);

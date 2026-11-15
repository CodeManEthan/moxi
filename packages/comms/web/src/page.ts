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
// An opened row (`opened-model.ts`) speaks for one address, the lead or the one pressed: the
// addresses it can speak for, `Cache`, the address's queue in full (`Held`, `Waiting`,
// `Unread`, each message cut at four lines with `Show all`), `Waits on` with `Thread`,
// `Directs`, `Directed by`, the send box (`box-model.ts`), and `Details` folded at its foot.
// The box checks how a message would go as it opens and when its posture or `Deliver`
// changes, sends once, and offers `Take back` on each of his messages the books can still
// take back; after either act the mail state and the queue are read again. A box set to
// answer a message quotes it, and the row's `Message` act clears that.
//
// Under the agents, the messages (`today-model.ts`): `Today`, newest first, each with `Open`
// for its thread; `Earlier today · N more · Show`, which adds thirty; and `Before today ·
// N messages, back to <day> · Show`, which adds that day under its own label and moves down.
// A thread (`thread-model.ts`) opens in place under the newest message row between its two
// addresses, that row's act then `Close`; when the lists show none, it opens as a group of
// its own above them. It reads its last six, `Show earlier` adds twenty, and it is read
// again with the mail, above the newest message it holds.
//
// It reads `/api/roster`, then follows the topic `roster`. It reads `/api/mail-state`,
// today's `/api/messages` and the opened address's `/api/queue` with them, when it opens, when the roster's waiting mail
// or its bound names change, when its tab is shown again and every 30 s while the tab is
// visible, and after a send or a take-back; a newly opened address's queue is read at once.
// Today's read takes as many messages as the page shows, so a refresh leaves no gap; each
// day before today the page added is read again from its cursor, as many as it shows, so
// every message shown carries its read state as the books hold it now.

import { css, html, LitElement, nothing } from "lit";
import { ifDefined } from "lit/directives/if-defined.js";
import { repeat } from "lit/directives/repeat.js";
import { api, installed, navigate, subscribe } from "/m/ui/client.js";

import { addressWrite, NOTHING_OPEN, parseAddress, sameAddress, threadAddress } from "./address-model.ts";
import type { Address } from "./address-model.ts";
import { operatorAddresses, parseMailState, parsePageRoster } from "./agents-model.ts";
import type { Act, MailState, Owed, PageRoster } from "./agents-model.ts";
import {
  CHECK_AGAIN, DELIVER, FROM, OPERATOR, REPLY_WANTED, SEND, SendBox, TAKE_BACK, TEXT_MAX_CHARS,
} from "./box-model.ts";
import type { Choice } from "./box-model.ts";
import { failureWhy, Feed, parseRoster } from "./feed.ts";
import {
  ACT_WORDS, CLOSE, COPIED, COPIED_MS, COPY, DOWN_LABEL, frameModel, LAUNCH_FROM_HOME, READING, READING_AFTER_MS,
  readLine, SETTINGS_LINK, SETTINGS_ROUTE, THREAD_LABEL,
} from "./frame-model.ts";
import type { Down, Empty, Frame, FrameRow, Placed } from "./frame-model.ts";
import { byHim, parseQueue } from "./messages-model.ts";
import type { Queue } from "./messages-model.ts";
import { COPY_ID, DETAILS, HELP, openedAgent, SHOW_ALL, THREAD_LINK } from "./opened-model.ts";
import type { Line } from "./opened-model.ts";
import type { Said } from "./words.ts";
import {
  authorIn, EARLIER_TODAY, FIRST, joined, messagesPath, messagesView, MORE, OPEN, parseMessagesAnswer, reread,
  rereadPaths, SHOW, todayOf,
} from "./today-model.ts";
import type { Messages, ReadDay } from "./today-model.ts";
import { between, COPY_LAST_ID, SHOW_EARLIER, ThreadReads, threadView } from "./thread-model.ts";
import { newestDay, whenWords } from "./words.ts";

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
  .det .r { display: grid; grid-template-columns: 92px minmax(0, 1fr) auto; padding: 3px 0; align-items: baseline; }
  .det .k, .det .d { color: var(--moxi-text-dim); }
  .det .f { min-width: 0; overflow-wrap: anywhere; }
  .det .x { color: var(--moxi-text-dim); padding-left: 18px; }
  .det .pick { color: var(--moxi-text-dim); margin-right: 14px; }
  .det .pick[aria-pressed="true"] { color: var(--moxi-text); }
  .det .msg { white-space: pre-wrap; overflow-wrap: anywhere; }
  .det .msg.cut { display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
  .det .note { padding: 3px 0 3px 92px; }
  .det .foot { display: flex; flex-wrap: wrap; gap: 4px 18px; padding: 9px 0 0 92px; }
  .det .foot button, .det .r button { font-size: 13px; line-height: 18px; }
  .det .help { padding: 3px 0 0 92px; color: var(--moxi-text-dim); }
  .det .foot.flush { padding-left: 0; }
  .det .foot.flush button:not(.d) { color: var(--moxi-text); }

  /* A thread, opened in place under a message row: the header, then each message's line
     and its text, a rule between two. */
  .thr .cap { color: var(--moxi-text-dim); padding: 1px 0 5px; overflow-wrap: anywhere; }
  .thr .note { padding: 3px 0; }
  .thr .day { padding: 8px 0 6px; color: var(--moxi-text-dim); }
  .thr .day button { color: var(--moxi-text); }
  .thr .day button:disabled { color: var(--moxi-text-dim); cursor: default; }
  .thr .tm { padding: 7px 0 6px; }
  .thr .tm + .tm, .thr .day + .tm { border-top: 1px solid var(--moxi-border); }
  .thr .h { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .thr .h .d { color: var(--moxi-text-dim); }
  .thr .tm .msg { font-size: 14px; line-height: 20px; margin-top: 2px; max-width: 540px; }
  .thr .tm > button { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }

  /* The send box: the text, then one line of controls with Send at its right, the only
     piece in the accent; the line under it says how the message would go or went. */
  .send { position: relative; border: 1px solid var(--moxi-border-strong); border-radius: 10px; padding: 10px 12px 9px;
          margin-top: 10px; font-size: 14px; line-height: 20px; }
  .send textarea { display: block; width: 100%; min-height: 40px; resize: vertical; border: 0; outline: 0; padding: 0;
                   margin: 0; background: none; color: var(--moxi-text); font: 14px/20px var(--moxi-font-sans); }
  .send textarea::placeholder { color: var(--moxi-text-dim); opacity: 1; }
  .send .quote { padding-bottom: 8px; margin-bottom: 8px; border-bottom: 1px solid var(--moxi-border); font-size: 13px;
                 line-height: 18px; }
  .send .quote .ql { color: var(--moxi-text-dim); }
  .send .ctl { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px 14px;
               margin-top: 8px; color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; }
  .send .ctl .k { margin-right: 7px; }
  .send .ctl .sep { margin: 0 10px; }
  .send .ctl .pick { color: var(--moxi-text); }
  .send .ctl .v { margin-left: 5px; color: var(--moxi-text-dim); }
  .send .ctl .is { color: var(--moxi-text); }
  .send .go { flex: none; padding: 8px 16px; border-radius: 7px; background: var(--moxi-accent); color: var(--moxi-on-accent);
              font: 600 13px/1 var(--moxi-font-sans); }
  .send .go.off { background: none; color: var(--moxi-text-dim); box-shadow: inset 0 0 0 1px var(--moxi-border);
                  cursor: default; }
  .menu { position: absolute; z-index: 2; left: 0; bottom: calc(100% + 6px); width: min(430px, 100%); padding: 6px 0;
          background: var(--moxi-bg-raised); border: 1px solid var(--moxi-border); border-radius: 10px;
          color: var(--moxi-text); font-size: 14px; line-height: 20px; }
  .menu button { display: grid; grid-template-columns: 20px minmax(0, 1fr); width: 100%; padding: 5px 14px; }
  .menu button:hover { background: var(--moxi-bg-hover); }
  .menu .d { display: block; color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; }
  .dry { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; column-gap: 10px; padding: 8px 2px 0;
         align-items: baseline; }
  .dry .k, .dry .d { color: var(--moxi-text-dim); }
  .dry b { font-weight: 600; }
  .dry button { padding-left: 18px; color: var(--moxi-text); }
  .dry .t { min-width: 0; overflow-wrap: anywhere; }

  .msgs moxi-ui-row + moxi-ui-row { margin-top: 2px; }
  .one { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; padding: 7px 0;
         color: var(--moxi-text-dim); }
  .one button { flex: none; color: var(--moxi-text); }
  .one button:disabled { color: var(--moxi-text-dim); cursor: default; }

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
  /** Today's read, and what the page shows of today and of the days before it added. */
  #messages: Feed<Messages> | null = null;
  #todayRead: ReadDay | null = null;
  #days: ReadDay[] = [];
  /** The `Show` whose read is out, and the line of one that failed. */
  #showing: string | null = null;
  #showFailed: string | null = null;
  /** The days before today are being read again, and the line of a read that failed. */
  #rereading = false;
  #rereadFailed: string | null = null;
  /** The opened row's address, and the read of its rows in the books. */
  #queueFor: string | null = null;
  #queue: Feed<Queue> | null = null;
  /** The address the row opened in the last drawing speaks for. */
  #drawnTo: string | null = null;
  /** The thread opened in the last drawing, and its reads. */
  #drawnThread: { a: string; b: string } | null = null;
  #thread: ThreadReads | null = null;
  #details = false;
  /** The ids of the messages cut at four lines, and those shown in full. */
  #cut = new Set<string>();
  #whole = new Set<string>();
  /** The last roster that was up, and its time on the server's clock. */
  #kept: { value: PageRoster; at: number } | null = null;
  #taken: PageRoster | null = null;
  #mailKey: string | null = null;
  #today: string | null = null;
  #at: Address = NOTHING_OPEN;
  #placed: Placed | null = null;
  #openedAt = 0;
  /** The opened row's send box, the message the frame set it to answer, and which of its
   * menus is open. */
  #box: SendBox | null = null;
  #boxReplyId: string | null = null;
  #drawnReply: Owed | null = null;
  #menu: "posture" | "deliver" | null = null;
  /** The box takes the focus once it is drawn: the row was opened by `Message` or `Reply`. */
  #focusBox = false;
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
    this.#readMail();
  };

  /** Reads the mail state again, with today's messages, the days before today the page
   * added and the opened row's queue. */
  #readMail(): void {
    void this.#mail?.refresh();
    void this.#messages?.refresh();
    void this.#rereadDays();
    void this.#queue?.refresh();
    void this.#thread?.refresh();
  }

  /** Reads again each message the page shows of the days before today, so their read
   * states and `taken back` follow the books as today's do. One at a time. */
  async #rereadDays(): Promise<void> {
    if (this.#rereading || this.#days.length === 0) return;
    this.#rereading = true;
    try {
      const days = this.#days;
      const reads = await Promise.all(days.map(async (read) => {
        const results = await Promise.all(rereadPaths(read).map((path) => api("comms", path)));
        const values = results.map((result) => (result.status === 200 ? parseMessagesAnswer(result.data) : null));
        const failed = values.findIndex((value) => value === null || value.state !== "up");
        if (failed >= 0) {
          const why = values[failed]?.detail ?? failureWhy(results[failed].status);
          return { day: read.day, fresh: null, why };
        }
        return { day: read.day, fresh: values.flatMap((value) => value?.messages ?? []), why: null };
      }));
      const why = reads.find((one) => one.why !== null)?.why ?? null;
      this.#rereadFailed = why === null ? null : `The messages can't be read: ${why.replace(/\.$/, "")}.`;
      this.#days = this.#days.map((read) => {
        const fresh = reads.find((one) => one.day === read.day)?.fresh ?? null;
        return fresh === null ? read : reread(read, fresh);
      });
    } finally {
      this.#rereading = false;
      this.requestUpdate();
    }
  }

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
    const messages = new Feed<Messages>(
      () => api("comms", messagesPath({ limit: Math.max(FIRST, this.#todayRead?.messages.length ?? 0) })),
      parseMessagesAnswer, () => {
        this.#today = newestDay(this.#today, messages.value?.today ?? null);
        this.#todayRead = todayOf(messages);
        this.requestUpdate();
      }, { clocked: false });
    this.#messages = messages;
    void this.#mail.refresh();
    void messages.refresh();
    document.addEventListener("visibilitychange", this.#shown);
    this.#tickTimer = setInterval(() => { this.tick += 1; }, TICK_MS);
    this.#readingTimer = setTimeout(() => this.requestUpdate(), READING_AFTER_MS);
    this.#mailTimer = setInterval(() => {
      if (document.visibilityState === "visible") this.#readMail();
    }, MAIL_EVERY_MS);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#mail?.close();
    this.#messages?.close();
    this.#messages = null;
    this.#queue?.close();
    this.#thread?.close();
    this.#thread = null;
    this.#box?.close();
    this.#box = null;
    this.#feed = this.#mail = this.#queue = null;
    this.#queueFor = null;
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
      // A new placement opens with an empty box.
      this.#box?.close();
      this.#box = null;
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
        if (this.#mailKey !== null && key !== this.#mailKey) this.#readMail();
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
    this.#details = false;
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
    this.#focusBox = act.kind === "reply" || act.kind === "message";
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
      messagesRead: this.#messages !== null && !this.#messages.loading,
      authorOf: (id) => authorIn([this.#todayRead, ...this.#days], id),
    });
    this.#placed = frame.placed;
    return frame;
  }

  render() {
    const frame = this.frame();
    this.#drawnTo = frame?.rows.find((item) => item.open)?.to ?? null;
    this.#drawnReply = frame?.opened.kind === "agent" ? frame.opened.reply : null;
    const opened = frame?.opened;
    this.#drawnThread = opened?.kind === "thread" ? { a: opened.a, b: opened.b } : null;
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
      ${this.#messagesPart(frame.stale)}
      <div class="cfoot" data-part="foot"><span data-line="answering">${frame.foot ?? ""}</span>${
        this.#link("ui", SETTINGS_ROUTE, SETTINGS_LINK, { action: "settings" })}</div>
      ${this.#lines(frame)}`;
  }

  #lines(frame: Frame) {
    const read = this.#messages;
    const failed = read === null ? null : readLine("The messages", read);
    const lines = [...frame.lines, ...(failed !== null ? [{ key: "messages", text: failed }] : []),
                   ...(this.#showFailed !== null ? [{ key: "show", text: this.#showFailed }] : []),
                   ...(this.#rereadFailed !== null ? [{ key: "reread", text: this.#rereadFailed }] : [])];
    return lines.map((line) => html`<p class="last" data-line=${`foot-${line.key}`} role="status">${line.text}</p>`);
  }

  // -- messages ----------------------------------------------------------------------------

  /** `Today`, `Earlier today`, the days added and `Before today`; dim with no acts while the
   * roster isn't up. */
  #messagesPart(stale: boolean) {
    const rows = this.#kept?.value.rows ?? [];
    const him = new Set(["operator", ...operatorAddresses(this.#mail?.value ?? null),
                         ...[this.#todayRead, ...this.#days].flatMap((read) =>
                           (read?.messages ?? []).filter(byHim).map((message) => message.author))]);
    // The thread opens under the newest message row between its two addresses.
    const pair = stale ? null : this.#drawnThread;
    const thread = pair !== null && this.#thread !== null && this.#messages !== null && !this.#messages.loading
      ? this.#thread : null;
    const anchor = thread === null || pair === null ? null
      : [this.#todayRead, ...this.#days].flatMap((read) => read?.messages ?? [])
        .find((message) => between(message, pair.a, pair.b, him))?.message_id ?? null;
    const view = messagesView(this.#todayRead, this.#days, {
      bound: (qualified) => rows.some((row) => row.qualified === qualified && row.live), cache: installed("cache"),
      him: (tag) => him.has(tag),
    }, this.#when, this.#today);
    if (view.groups.length === 0 && view.before === null && thread === null) return nothing;
    const show = (key: string, read: () => Promise<void>) => html`<button type="button" data-action=${key}
      ?disabled=${stale || this.#showing !== null} @click=${() => void this.#show(key, read)}>${SHOW}</button>`;
    return html`<div class=${stale ? "grps msgs stale" : "grps msgs"} data-part="messages">
      ${thread !== null && anchor === null ? html`<section class="grp" data-group="thread"><div class="gk">${THREAD_LABEL}</div>
        <div class="gl">${this.#threadPart(thread, him)}</div></section>` : nothing}
      ${view.groups.map((group) => html`<section class="grp" data-group=${group.key}><div class="gk">${group.label}</div>
        <div class="gl">${repeat(group.rows, (row) => row.message_id, (row) => html`<moxi-ui-row data-message=${
          row.message_id} name=${row.name} where=${ifDefined(row.where || undefined)} fact=${row.time} lead=${row.text}
          ?open=${thread !== null && row.message_id === anchor}>${thread !== null && row.message_id === anchor
          ? html`<button type="button" slot="acts" class="do" data-action="close-thread" aria-expanded="true"
            @click=${() => this.open(NOTHING_OPEN)}>${CLOSE}</button>${this.#threadPart(thread, him)}`
          : !stale && row.thread !== null ? html`<button type="button" slot="acts" class="do" data-action="open-thread"
            @click=${() => this.open(threadAddress(row.thread?.a ?? "", row.thread?.b ?? ""))}>${OPEN}</button>` : nothing}
          </moxi-ui-row>`)}${group.show !== null && group.key !== "today" ? html`<div class="one" data-line="more"><span>${
          group.more}</span>${show(`more:${group.key}`, () => this.#readMore(group.show?.day ?? null,
                                                                              group.show?.before ?? ""))}</div>` : nothing}
        </div></section>${group.key === "today" && group.show !== null ? html`<section class="grp" data-group="earlier-today">
          <div class="gk">${EARLIER_TODAY}</div><div class="gl"><div class="one" data-line="more"><span>${group.more}</span>${
          show("more:today", () => this.#readMore(null, group.show?.before ?? ""))}</div></div></section>` : nothing}`)}
      ${view.before !== null ? html`<section class="grp" data-group="before"><div class="gk">${view.before.label}</div>
        <div class="gl"><div class="one" data-line="before"><span>${view.before.text}</span>${show("before", () =>
          this.#readDay(view.before?.earlier.day ?? "", view.before?.earlier.before ?? ""))}</div></div></section>`
        : nothing}
    </div>`;
  }

  /** An opened thread: its header, `Show earlier`, its messages oldest first, each cut at
   * four lines with `Show all`, and its foot; `Reading…` while its first read is out. */
  #threadPart(reads: ThreadReads, him: ReadonlySet<string>) {
    const held = reads.held;
    const failed = reads.failure !== null
      ? html`<p class="note" data-line="thread-failed" role="status">The thread can't be read: ${
        reads.failure.replace(/\.$/, "")}.</p>` : nothing;
    if (held === null) {
      return html`<div class="det thr" data-part="thread">${reads.loading
        ? html`<p class="note" data-line="reading" role="status">${READING}</p>` : nothing}${failed}</div>`;
    }
    const view = threadView(held, {
      a: reads.a, b: reads.b, roster: this.#kept?.value.rows ?? [], him,
      owedBy: (this.#mail?.value?.owed ?? []).map((entry) => entry.author), cache: installed("cache"), when: this.#when,
    });
    const earlier = view.earlier;
    const lastId = view.lastId;
    return html`<div class="det thr" data-part="thread" data-a=${reads.a} data-b=${reads.b ?? ""}>
      <p class="cap" data-line="thread-header">${view.header}</p>
      ${earlier !== null ? html`<p class="day" data-line="show-earlier"><button type="button" data-action="show-earlier"
        ?disabled=${reads.showing} @click=${() => void reads.showEarlier()}>${SHOW_EARLIER}</button> · ${earlier.text}</p>`
        : nothing}
      ${repeat(view.messages, (one) => one.message_id, (one) => {
        const key = `thread:${one.message_id}`;
        return html`<div class="tm" data-message=${one.message_id}><p class="h">${one.author} <span class="d">${one.rest
          }</span></p><p class=${this.#whole.has(key) ? "msg" : "msg cut"} data-message=${key}>${one.text}</p>${
          this.#cut.has(key) && !this.#whole.has(key) ? html`<button type="button" class="d" data-action="show-all"
            @click=${() => { this.#whole.add(key); this.requestUpdate(); }}>${SHOW_ALL}</button>` : nothing}</div>`;
      })}
      <div class="foot flush">${view.write.map((side) => html`<button type="button" data-action="message-side"
        data-address=${side} @click=${() => {
          this.#focusBox = true;
          this.open({ piece: "agent", qualified: side });
        }}>${ACT_WORDS.message} ${side}</button>`)}${lastId !== null ? html`<button type="button" class="d"
        data-action="copy-last-id" @click=${() => void this.#copy("last-id", lastId)}>${
        this.#copied === "last-id" ? COPIED : COPY_LAST_ID}</button>` : nothing}</div>
      ${failed}
    </div>`;
  }

  /** Runs one `Show`'s read; one at a time. */
  async #show(key: string, read: () => Promise<void>): Promise<void> {
    if (this.#showing !== null) return;
    this.#showing = key;
    this.#showFailed = null;
    this.requestUpdate();
    try {
      await read();
    } finally {
      this.#showing = null;
      this.requestUpdate();
    }
  }

  /** One read of `/api/messages` for a `Show`; null after it failed, with its line set. */
  async #page(path: string): Promise<Messages | null> {
    const result = await api("comms", path);
    const value = result.status === 200 ? parseMessagesAnswer(result.data) : null;
    if (value === null || value.state !== "up") {
      const why = value?.detail ?? failureWhy(result.status);
      this.#showFailed = `The messages can't be read: ${why.replace(/\.$/, "")}.`;
      return null;
    }
    return value;
  }

  /** `Earlier today · Show`, or a day's own `Show`: thirty more of that day below `before`. */
  async #readMore(day: string | null, before: string): Promise<void> {
    const value = await this.#page(messagesPath({ day, before, limit: MORE }));
    if (value === null) return;
    if (day === null) {
      const held = this.#todayRead;
      if (held !== null) this.#todayRead = { ...held, messages: joined(held.messages, value.messages), more: value.more };
      return;
    }
    this.#days = this.#days.map((read) => (read.day !== day ? read
      : { ...read, messages: joined(read.messages, value.messages), more: value.more }));
  }

  /** `Before today · Show`: the newest day before what the page shows, under its own label. */
  async #readDay(day: string, before: string): Promise<void> {
    const value = await this.#page(messagesPath({ day, before, limit: MORE }));
    if (value === null) return;
    this.#days = [...this.#days, { day, messages: value.messages, more: value.more, earlier: value.earlier,
                                   cursor: before }];
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
        ? html`${this.#drawnReply !== null && item.to !== null ? html`<button type="button" slot="acts" class="do"
          data-action="message" @click=${() => this.#act({ kind: "message", to: item.to as string })}>${ACT_WORDS.message
          }</button>` : nothing}<button type="button" slot="acts" class="do" data-action="close" aria-expanded="true"
          @click=${toggle}>${CLOSE}</button>`
        : row.acts.filter((act) => act.kind !== "keep-warm").map((act) => html`<button type="button" slot="acts" class="do"
          data-action=${act.kind} @click=${() => this.#act(act)}>${ACT_WORDS[act.kind]}</button>`)}
      ${item.open && item.to !== null ? this.opened(item, item.to) : nothing}
    </moxi-ui-row>`;
  }

  /** What an opened agent row shows under its lines, speaking for the address `to`: the
   * addresses it can speak for, `Cache`, the queue in full, `Waits on`, `Directs`,
   * `Directed by`, the send box, then `Details`. */
  protected opened(item: FrameRow, to: string): unknown {
    const feed = this.#feed;
    const now = feed?.now() ?? null;
    if (feed === null || now === null) return nothing;
    const roster = this.#kept?.value.rows ?? [];
    const queue = this.#queueFor === to && this.#queue !== null ? this.#queue
      : { loading: true, gone: false, failure: null, value: null };
    const view = openedAgent({
      row: item.row, to, roster, asks: this.#mail?.value?.asks ?? [], queue, now,
      when: (at) => whenWords(at, this.#today), cache: installed("cache"), details: this.#details,
    });
    const sessionId = view.sessionId;
    return html`<div class="det" data-part="opened" data-to=${to}>
      ${view.choices.length > 0 ? html`<div class="r" data-line="address"><span class="k">Address</span><span class="f">${
        view.choices.map((choice) => html`<button type="button" class="pick" aria-pressed=${choice.current ? "true" : "false"}
          data-action="address" data-address=${choice.qualified}
          @click=${() => this.open({ piece: "agent", qualified: choice.qualified })}>${choice.qualified}</button>`)}</span>
        <span></span></div>` : nothing}
      ${view.before.map((line) => this.#line(line))}
      ${view.queue.map((group) => group.items.map((one, i) => html`<div class="r" data-line=${group.label.toLowerCase()}
        data-message=${one.message_id}><span class="k">${i === 0 ? group.label : ""}</span><span class="f"><span
        class=${this.#whole.has(one.message_id) ? "msg" : "msg cut"} data-message=${one.message_id}>${one.time} from ${
        one.author} · ${one.text}</span>${this.#cut.has(one.message_id) && !this.#whole.has(one.message_id)
          ? html` <button type="button" class="d" data-action="show-all" @click=${() => {
            this.#whole.add(one.message_id);
            this.requestUpdate();
          }}>${SHOW_ALL}</button>` : nothing}</span>${this.queueAct(one, to)}</div>`))}
      ${view.more !== null ? html`<p class="note" data-line="more">${view.more}</p>` : nothing}
      ${view.queueNote !== null ? html`<p class="note" data-line="queue-note">${view.queueNote}</p>` : nothing}
      ${this.#box?.to === to ? this.#said(this.#box.view({ when: this.#when }).taken, "taken") : nothing}
      ${view.after.map((line) => this.#line(line))}
      ${this.box(item, to)}
      <div class="foot"><button type="button" class="d" data-action="details" aria-expanded=${this.#details ? "true" : "false"}
        @click=${() => { this.#details = !this.#details; this.requestUpdate(); }}>${DETAILS}</button></div>
      ${view.details !== null ? html`${view.details.map((line) => this.#line(line))}${sessionId !== null
        ? html`<div class="foot"><button type="button" class="d" data-action="copy-id"
          @click=${() => void this.#copy("id", sessionId)}>${this.#copied === "id" ? COPIED : COPY_ID}</button></div>`
        : nothing}<p class="help" data-line="help">${HELP}</p>` : nothing}
    </div>`;
  }

  /** The act at the right of one queued message: `Take back` on his own while the books can
   * still take it back. */
  protected queueAct(item: { message_id: string; takeable: boolean }, to: string): unknown {
    const box = this.#box;
    if (!item.takeable || box === null || box.to !== to) return html`<span></span>`;
    return html`<button type="button" class="x" data-action="take-back" ?disabled=${box.taking.has(item.message_id)}
      @click=${() => void box.takeBack(item.message_id)}>${TAKE_BACK}</button>`;
  }

  #when = (at: number): string => whenWords(at, this.#today);

  /** The send box of an opened row, written to `to`, and the line under it. */
  protected box(_item: FrameRow, to: string): unknown {
    const box = this.#box;
    if (box === null || box.to !== to) return nothing;
    const queue = this.#queueFor === to ? this.#queue?.value ?? null : null;
    const view = box.view({ when: this.#when, queue });
    const quote = view.quote;
    const quoteId = `quote:${box.reply?.message_id ?? ""}`;
    const deliver = view.deliver;
    return html`<div class="send" data-part="box" data-to=${to}>
        ${quote !== null ? html`<div class="quote" data-line="quote"><p class="ql">${quote.lead}</p><p
          class=${this.#whole.has(quoteId) ? "msg" : "msg cut"} data-message=${quoteId}>${quote.text}</p>${
          this.#cut.has(quoteId) && !this.#whole.has(quoteId) ? html`<button type="button" class="d" data-action="show-all"
            @click=${() => { this.#whole.add(quoteId); this.requestUpdate(); }}>${SHOW_ALL}</button>` : nothing}</div>`
          : nothing}
        <textarea data-field="text" aria-label=${view.placeholder} placeholder=${view.placeholder}
          maxlength=${TEXT_MAX_CHARS} .value=${box.text} ?readonly=${view.sending}
          @input=${(event: Event) => box.setText((event.target as HTMLTextAreaElement).value)}></textarea>
        <div class="ctl"><span class="cl"><span class="k">${FROM}</span><span class="is" data-line="from">${OPERATOR}</span><span
          class="sep">·</span><span class="k">${REPLY_WANTED}</span>${this.#pick("posture", view.posture.word,
            view.posture.menu, (value) => box.setPosture(value))}${deliver !== null ? html`<span class="sep">·</span><span
          class="k">${DELIVER}</span>${deliver.menu !== null ? this.#pick("deliver", deliver.word, deliver.menu,
            (value) => box.setHeld(value)) : html`<span class="is" data-line="deliver">${deliver.word}</span>`}` : nothing}</span>
          <button type="button" class=${view.ready ? "go" : "go off"} data-action="send" ?disabled=${!view.ready}
            @click=${() => void box.send(queue)}>${SEND}</button></div>
      </div>
      ${this.#said(view.line, "dry", () => void box.check())}`;
  }

  /** A control of the box that opens a menu: its word and `▾`, and the menu while open. */
  #pick<V>(which: "posture" | "deliver", word: string, menu: Choice<V>[], set: (value: V) => void) {
    const open = this.#menu === which;
    const close = (): void => { this.#menu = null; this.requestUpdate(); };
    return html`<button type="button" class="pick" data-action=${which} aria-haspopup="menu"
        aria-expanded=${open ? "true" : "false"} @click=${() => { this.#menu = open ? null : which; this.requestUpdate(); }}
        >${word}<span class="v" aria-hidden="true">▾</span></button>${open ? html`<div class="menu" role="menu"
        data-menu=${which} @keydown=${(event: KeyboardEvent) => { if (event.key === "Escape") close(); }}>${
        menu.map((choice) => html`<button type="button" role="menuitemradio" aria-checked=${choice.current ? "true" : "false"}
          data-choice=${String(choice.value)} @click=${() => { this.#menu = null; set(choice.value); this.requestUpdate(); }}
          ><span aria-hidden="true">${choice.current ? "✓" : ""}</span><span>${choice.label}${choice.says !== null
            ? html`<span class="d">${choice.says}</span>` : nothing}</span></button>`)}</div>` : nothing}`;
  }

  /** One act's line: its key, the heavy lead, the words in the text colour, the dim part, and
   * `Check again` when it follows. */
  #said(said: Said | null, key: string, again?: () => void) {
    if (said === null) return nothing;
    return html`<div class="dry" data-line=${key} role="status"><span class="k">${said.key ?? ""}</span><span class="t">${
      said.heavy !== null ? html`<b>${said.heavy}</b> ` : nothing}${said.text}${said.dim !== null
      ? html` <span class="d">${said.dim}</span>` : nothing}</span>${said.again && again !== undefined
      ? html`<button type="button" data-action="check-again" @click=${again}>${CHECK_AGAIN}</button>`
      : html`<span></span>`}</div>`;
  }

  /** After Send or `Take back` answered: the mail and the queue are read again. A reply that
   * was sent leaves the box answering nothing, and the address says the row alone. */
  #acted(box: SendBox): void {
    if (this.#box !== box) return;
    const placed = this.#placed;
    if (box.reply === null && placed !== null && placed.opened.kind === "agent" && placed.opened.reply !== null) {
      this.#placed = { opened: { ...placed.opened, reply: null }, line: null };
      this.#boxReplyId = null;
      this.#setAt({ piece: "agent", qualified: box.to });
    }
    this.#readMail();
    this.requestUpdate();
  }

  #line(line: Line) {
    const link = line.link;
    let end: unknown = html`<span></span>`;
    if (link?.kind === "cache") end = this.#link("cache", "", line.label, { cls: "x", action: "cache" });
    else if (link?.kind === "thread") {
      end = html`<button type="button" class="x" data-action="thread"
        @click=${() => this.open(threadAddress(link.a, link.b))}>${THREAD_LINK}</button>`;
    }
    return html`<div class="r" data-line=${line.key}><span class="k">${line.label}</span><span class="f">${line.value}${
      line.tail !== null ? html` <span class="d">· ${line.tail}</span>` : nothing}</span>${end}</div>`;
  }

  /** Follows the opened row's address with a read of its queue, and marks the messages a
   * four-line cut hides part of. */
  protected updated(changed: Map<PropertyKey, unknown>): void {
    super.updated(changed);
    const to = this.#drawnTo;
    if (to !== this.#queueFor) {
      this.#queue?.close();
      this.#queueFor = to;
      this.#cut.clear();
      this.#whole.clear();
      this.#queue = to === null ? null
        : new Feed<Queue>(() => api("comms", `api/queue?name=${encodeURIComponent(to)}`), parseQueue,
                          () => this.requestUpdate(), { clocked: false });
      void this.#queue?.refresh();
    }
    const pair = this.#drawnThread;
    if (pair?.a !== this.#thread?.a || pair?.b !== this.#thread?.b) {
      this.#thread?.close();
      this.#thread = pair === null ? null : new ThreadReads(pair.a, pair.b, {
        get: (path) => api("comms", path),
        onChange: () => {
          this.#today = newestDay(this.#today, this.#thread?.today ?? null);
          this.requestUpdate();
        },
        newestKnown: () => this.#todayRead?.messages[0]?.message_id ?? null,
      });
      void this.#thread?.refresh();
      // Its `Reading…` shows while the first read is out.
      if (this.#thread !== null) this.requestUpdate();
    }
    if ((this.#box?.to ?? null) !== to) {
      this.#box?.close();
      this.#menu = null;
      const reply = this.#drawnReply;
      this.#boxReplyId = reply?.message_id ?? null;
      if (to === null) this.#box = null;
      else {
        const box: SendBox = new SendBox(to, reply, {
          post: (path, body) => api("comms", path, body), onChange: () => this.requestUpdate(),
          onActed: () => this.#acted(box), now: () => this.#feed?.now() ?? Date.now() / 1000, cache: installed("cache"),
        });
        this.#box = box;
        void box.check();
      }
    } else if (this.#box !== null && (this.#drawnReply?.message_id ?? null) !== this.#boxReplyId) {
      this.#boxReplyId = this.#drawnReply?.message_id ?? null;
      this.#box.answer(this.#drawnReply);
    }
    if (this.#focusBox && this.#box !== null) {
      const area = this.renderRoot.querySelector<HTMLTextAreaElement>(
        `[data-part="box"][data-to="${CSS.escape(this.#box.to)}"] textarea`);
      if (area !== null) {
        this.#focusBox = false;
        area.focus();
      }
    }
    let grew = false;
    for (const node of this.renderRoot.querySelectorAll<HTMLElement>(".msg.cut")) {
      const id = node.dataset.message;
      if (id !== undefined && node.scrollHeight > node.clientHeight + 1 && !this.#cut.has(id)) {
        this.#cut.add(id);
        grew = true;
      }
    }
    if (grew) this.requestUpdate();
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, CommsPage);

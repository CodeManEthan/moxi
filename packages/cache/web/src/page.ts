// <moxi-cache-page route="...">: cache's panel. Under the shell's top line, from top to
// bottom: the Watcher row when the watcher is not reading; the groups `N warm` and `N cold`,
// each a dim count beside its rows; the dim line for the sessions with no clock time, which
// shows and hides their rows; the empty state's sentences where the page has them; the
// foot, `Last reading <time>` and `Cache settings`; then the page's last lines, one for
// each read that failed.
//
// It places what the page's model (`model.ts`) returns and decides nothing about what is
// shown: a piece the model gives as null is not drawn, and every word comes from there.
// Each session is a `<moxi-ui-row>` with `toggles`, named from `/api/names` and never by
// its id; a click on its lines, Enter or Space on its first line, or its `Open` act opens
// it in place, and the page keeps which rows are open across a refresh. An opened row
// shows its times to the second, its `Session` line with a `Sessions` link when sessions
// holds it, and `Copy id`; its model is asked from `/api/session` when it opens and not
// before.
//
// It reads `/api/clocks`, then follows the topic `clocks`, and reads the route again when
// its tab is shown; it counts each second on the server's clock, so a row turns cold at
// its time with no read. It asks `/api/names` one request at a time: when it opens, when
// the set of sessions in the records changes, when its tab is shown and every 60 seconds
// while the tab is visible. A session's row waits up to 2 seconds for a names answer that
// holds it.

import { css, html, LitElement, nothing } from "lit";
import { ifDefined } from "lit/directives/if-defined.js";
import { repeat } from "lit/directives/repeat.js";
import { api, navigate, subscribe } from "/m/ui/client.js";

import { Feed } from "./feed.ts";
import {
  Arrivals, CLOSE, COPIED, COPIED_MS, COPY, COPY_ID, commandParts, factText, keep, keyOf, ModelReads, NAMES_EVERY_MS,
  NamesFeed, namesBy, OPEN, openedModel, pageModel, parsePageClocks, SESSION_LABEL, SESSIONS_LINK, sessionPath,
  SETTINGS_LINK, SETTINGS_ROUTE, todayOf, WAITING_MS, WATCHER_LABEL,
} from "./model.ts";
import type { GroupModel, Kept, LastLine, PageClocks, PageModel, RowModel, UntimedModel, WatcherRow } from "./model.ts";

const TAG = "moxi-cache-page";
const TICK_MS = 1000;

// The Sessions page's two-line rows under a dim count at the left, at its width. No
// accent, no warning or error colour, no card: what went wrong is said in the text colour,
// and what only informs is dim.
const STYLES = css`
  :host { display: block; min-width: 0; max-width: 680px; margin: 0 auto; padding: 4px 0 16px;
          color: var(--moxi-text); font-family: var(--moxi-font-sans); font-size: 14px; line-height: 20px; }
  /* The accent is the focus ring alone, drawn only on a focused piece. */
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

  .stop { padding: 7px 0; }
  .stop .l1 { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; line-height: 20px; }
  .stop .l1 b { font-weight: 600; min-width: 0; }
  .stop .l1 button { flex: none; color: var(--moxi-text); font-size: 13px; }
  .stop .l2 { display: block; font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); overflow-wrap: anywhere; }

  /* A row opened in place: a label, what it says to the second, and a link at the right end. */
  .det { margin: 9px 0 3px; padding: 8px 0 0; border-top: 1px solid var(--moxi-border); font-size: 13px; line-height: 18px;
         cursor: auto; }
  .det .r { display: grid; grid-template-columns: 92px minmax(0, 1fr) auto; padding: 3px 0; align-items: baseline; }
  .det .k, .det .d { color: var(--moxi-text-dim); }
  .det .f { min-width: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
  .det .x { color: var(--moxi-text-dim); padding-left: 18px; }
  .det .foot { display: flex; flex-wrap: wrap; gap: 4px 18px; padding: 9px 0 0 92px; }
  .det .foot button { font-size: 13px; line-height: 18px; }
  .det .foot .d { color: var(--moxi-text-dim); }

  .one { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; padding: 7px 0;
         color: var(--moxi-text-dim); }
  .one .t { flex: none; }

  .quietline { font-size: 22px; line-height: 32px; text-align: center; letter-spacing: -0.005em; padding: 150px 0 14px; }
  .first { color: var(--moxi-text-dim); line-height: 1.6; text-align: center; }
  .first p { max-width: 520px; margin: 0 auto; }
  .grps + .first { margin-top: 40px; }

  .cfoot { display: flex; justify-content: space-between; gap: 14px; font-size: 13px; line-height: 18px;
           color: var(--moxi-text-dim); padding: 12px 2px 0; }
  .cfoot a { flex: none; }
  .last { font-size: 13px; line-height: 18px; padding: 12px 2px 0; overflow-wrap: anywhere; }
  .last.dim { color: var(--moxi-text-dim); }

  @media (max-width: 720px) {
    :host { padding-top: 10px; }
    .grp { display: block; padding: 4px 0 6px; }
    .grp .gk { padding-top: 9px; font-size: 13px; }
    .quietline { padding-top: 60px; }
    .cfoot { padding-top: 14px; }
  }
`;

export class CachePage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
    tick: { state: true },
  };

  route = "";
  tick = 0;
  #feed: Feed<PageClocks> | null = null;
  #names: NamesFeed | null = null;
  #arrivals = new Arrivals();
  /** The answer the rows are drawn from, and the value last taken from the feed. */
  #kept: Kept | null = null;
  #taken: PageClocks | null = null;
  /** The keys of the rows open. */
  #opened = new Set<string>();
  #models = new ModelReads((session) => api("cache", sessionPath(session)), () => this.requestUpdate());
  #untimedShown = false;
  #openedAt = 0;
  /** Which copy act reads `Copied`. */
  #copied: string | null = null;
  #stop: (() => void) | null = null;
  #timers: ReturnType<typeof setTimeout>[] = [];
  #tickTimer: ReturnType<typeof setInterval> | undefined;
  #namesTimer: ReturnType<typeof setInterval> | undefined;
  #dueTimer: ReturnType<typeof setTimeout> | undefined;
  #copyTimer: ReturnType<typeof setTimeout> | undefined;

  #shown = (): void => {
    if (document.visibilityState !== "visible") return;
    void this.#feed?.refresh();
    void this.#names?.ask();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#openedAt = Date.now();
    const feed = new Feed<PageClocks>(() => api("cache", "api/clocks"), parsePageClocks, () => this.#clocksRead());
    this.#feed = feed;
    const names = new NamesFeed(() => api("cache", "api/names"), () => {
      this.#arrivals.names(names.answer);
      this.requestUpdate();
    });
    this.#names = names;
    void feed.refresh().then(() => {
      if (this.#feed === feed) this.#stop = subscribe("cache", "clocks", (data) => feed.topic(data));
    });
    void names.ask();
    document.addEventListener("visibilitychange", this.#shown);
    this.#tickTimer = setInterval(() => { this.tick += 1; }, TICK_MS);
    this.#namesTimer = setInterval(() => {
      if (document.visibilityState === "visible") void this.#names?.ask();
    }, NAMES_EVERY_MS);
    this.#timers.push(setTimeout(() => this.requestUpdate(), WAITING_MS));
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#names?.close();
    this.#feed = this.#names = null;
    this.#stop?.();
    this.#stop = null;
    document.removeEventListener("visibilitychange", this.#shown);
    clearInterval(this.#tickTimer);
    clearInterval(this.#namesTimer);
    clearTimeout(this.#dueTimer);
    clearTimeout(this.#copyTimer);
    for (const timer of this.#timers) clearTimeout(timer);
    this.#timers = [];
  }

  /** After every change of the clocks feed: a new answer that is up sets when each new
   * session's row may be drawn, asks the names again when the set of sessions changed, and
   * forgets the open rows whose record went. */
  #clocksRead(): void {
    const feed = this.#feed;
    if (feed === null) return;
    this.#kept = keep(this.#kept, feed, feed.now());
    const value = this.#kept?.value ?? null;
    if (value !== null && value !== this.#taken) {
      this.#taken = value;
      this.#arrivals.clocks(value.clocks, Date.now());
      this.#names?.clocks(value.clocks);
      const keys = new Set(value.clocks.map(keyOf));
      for (const key of [...this.#opened]) if (!keys.has(key)) this.#closed(key);
    }
    this.requestUpdate();
  }

  // -- acts --------------------------------------------------------------------------------

  #toggle(row: RowModel): void {
    if (this.#opened.has(row.key)) this.#closed(row.key);
    else {
      this.#opened.add(row.key);
      this.#models.open(row.key, row.session);
    }
    this.requestUpdate();
  }

  #closed(key: string): void {
    this.#opened.delete(key);
    this.#models.close(key);
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

  #model(): PageModel | null {
    const feed = this.#feed;
    const names = this.#names;
    if (feed === null || names === null) return null;
    const nowMs = Date.now();
    return pageModel({
      feed, kept: this.#kept, now: feed.now(), names: { answer: names.answer, failed: names.failed },
      arrivals: this.#arrivals, nowMs, waitedMs: nowMs - this.#openedAt, opened: this.#opened,
      untimedShown: this.#untimedShown,
    });
  }

  render() {
    const page = this.#model();
    if (page === null || page.state === "gone") return nothing;
    if (page.state !== "up") {
      return html`${page.waiting !== null ? html`<p class="waiting" data-line="waiting" role="status">${page.waiting}</p>`
        : nothing}${this.#last(page.last)}`;
    }
    const untimedTop = page.sentences.length === 0 ? page.untimed : null;
    const top = page.watcher !== null || page.groups.length > 0 || untimedTop !== null;
    return html`
      ${top ? html`<div class="grps" data-part="groups">
        ${page.watcher !== null ? this.#watcher(page.watcher) : nothing}
        ${repeat(page.groups, (group) => group.kind, (group) => this.#group(group))}
        ${untimedTop !== null ? this.#untimed(untimedTop) : nothing}</div>` : nothing}
      ${this.#sentences(page.sentences)}
      ${page.sentences.length > 0 && page.untimed !== null
        ? html`<div class="grps" data-part="untimed-list">${this.#untimed(page.untimed)}</div>` : nothing}
      ${page.foot !== null ? html`<div class="cfoot" data-part="foot"><span data-line="reading">${page.foot.reading ?? ""}</span>${
        this.#link("ui", SETTINGS_ROUTE, SETTINGS_LINK, { action: "settings" })}</div>` : nothing}
      ${this.#last(page.last)}`;
  }

  #last(lines: LastLine[]) {
    return lines.map((line) => html`<p class=${line.dim ? "last dim" : "last"} data-line="last" role="status">${line.text}</p>`);
  }

  #sentences(sentences: string[]) {
    if (sentences.length === 0) return nothing;
    const [line, ...rest] = sentences.length > 1 ? sentences : [null, ...sentences];
    return html`${line !== null ? html`<div class="quietline" data-line="empty">${line}</div>` : nothing}
      <div class="first">${rest.map((text) => html`<p data-line="cache">${text}</p>`)}</div>`;
  }

  #watcher(row: WatcherRow) {
    const copy = row.copy;
    return html`<section class="grp" data-part="watcher"><div class="gk">${WATCHER_LABEL}</div><div class="gl">
      <div class="stop"><span class="l1"><b data-line="watcher">${row.what}</b>${copy !== null
        ? html`<button type="button" data-action="copy-command" @click=${() => void this.#copy("command", copy)}>${
          this.#copied === "command" ? COPIED : COPY}</button>` : nothing}</span>
        <span class="l2">${commandParts(row.line).map((part) => (part.code ? html`<code>${part.text}</code>` : part.text))}</span>
      </div></div></section>`;
  }

  #group(group: GroupModel) {
    return html`<section class="grp" data-group=${group.kind}><div class="gk" data-line="label">${group.label}</div>
      <div class="gl">${repeat(group.rows, (row) => row.key, (row) => this.#row(row))}</div></section>`;
  }

  #untimed(untimed: UntimedModel) {
    return html`<section class="grp" data-part="untimed"><div class="gl">
      <div class="one" data-line="untimed"><span>${untimed.line}</span><button type="button" class="t"
        data-action="untimed" aria-expanded=${this.#untimedShown ? "true" : "false"}
        @click=${() => { this.#untimedShown = !this.#untimedShown; this.requestUpdate(); }}>${untimed.act}</button></div>
      ${repeat(untimed.rows, (row) => row.key, (row) => this.#row(row))}</div></section>`;
  }

  #row(row: RowModel) {
    const toggle = (): void => this.#toggle(row);
    return html`<moxi-ui-row toggles data-key=${row.key} name=${row.name} where=${ifDefined(row.where ?? undefined)}
        fact=${factText(row)} rest=${ifDefined(row.rest ?? undefined)} ?open=${row.open} @moxi-row-toggle=${toggle}>
      <button type="button" slot="acts" class="do" data-action="open" aria-expanded=${row.open ? "true" : "false"}
        @click=${toggle}>${row.open ? CLOSE : OPEN}</button>
      ${row.open ? this.opened(row) : nothing}
    </moxi-ui-row>`;
  }

  /** What an opened row shows under its lines: `Last request`, `Goes cold` or `Went
   * cold`, `Read` for a record that is old, the `Session` line and `Copy id`. */
  protected opened(row: RowModel): unknown {
    const kept = this.#kept;
    const now = this.#feed?.now() ?? null;
    const record = kept?.value.clocks.find((one) => keyOf(one) === row.key);
    if (kept === null || now === null || record === undefined) return nothing;
    const answer = this.#names?.answer ?? null;
    const view = openedModel(record, now, namesBy(answer), answer?.home ?? null, this.#models.get(row.key),
                             todayOf(kept, answer));
    const copied = `id ${row.key}`;
    return html`<div class="det" data-part="opened">
      ${view.lines.map((line) => html`<div class="r" data-line=${line.label}><span class="k">${line.label}</span><span
        class="f">${line.value}${line.tails.map((tail) => html` <span class="d">· ${tail}</span>`)}</span><span></span></div>`)}
      <div class="r" data-line="session"><span class="k">${SESSION_LABEL}</span><span class="f">${view.session.map((part, at) =>
        html`${at > 0 ? " " : ""}<span class=${part.dim ? "d" : ""}>${at > 0 ? "· " : ""}${part.text}</span>`)}</span>${
        view.sessions !== null ? this.#link("sessions", view.sessions, SESSIONS_LINK, { cls: "x", action: "sessions" })
          : html`<span></span>`}</div>
      <div class="foot"><button type="button" class="d" data-action="copy-id"
        @click=${() => void this.#copy(copied, view.copy)}>${this.#copied === copied ? COPIED : COPY_ID}</button></div>
    </div>`;
  }

  /** Draws the page again when the next row still waiting for its name is due. */
  protected updated(changed: Map<PropertyKey, unknown>): void {
    super.updated(changed);
    clearTimeout(this.#dueTimer);
    const due = this.#arrivals.due(Date.now());
    if (due !== null) this.#dueTimer = setTimeout(() => this.requestUpdate(), due + 1);
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, CachePage);

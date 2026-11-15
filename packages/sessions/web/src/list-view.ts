// <moxi-sessions-list>: the list of sessions, which sessions' page shows at its own
// address. From top to bottom: the find field; the filter line (`Folder`, `Harness`,
// `When`, each with a menu, and the count); the groups (`Active recently`, a group per
// day, `Earlier`), each a dim label beside its rows; then a dim line for each harness
// whose sessions were not all read. A list with no session draws its empty state in the
// groups' place.
//
// It places what the list's model (`list.ts`) returns and decides nothing about what is
// shown: a piece the model gives as null is not drawn, and every word comes from there.
// It makes the requests: `/api/list` when it opens, when its tab is shown again and every
// 30 seconds while the tab is visible; `/api/more` for a group's `Show`, a day's program
// line, `Earlier`, and for each read the model asks for after a newer list; `/api/folders`
// when the Folder menu opens. The find field sends its text 250 ms after the last key.
//
// A row is a `<moxi-ui-row>` with `toggles`: a click on its lines, Enter or Space on its
// first line, or its `Open` act opens it in place, and the list keeps which rows are
// open. An opened row shows, in the row's default slot, what the opened row's model
// (`opened.ts`) makes of `/api/session`: the route is read when the row is opened and not
// before, and again after a newer list only when that list shows the session changed.
// Several rows can be open at once. `Read the transcript` goes to the session's route,
// and `Copy` puts the harness's resume command on the clipboard.

import { css, html, LitElement, nothing } from "lit";
import { ifDefined } from "lit/directives/if-defined.js";
import { live } from "lit/directives/live.js";
import { repeat } from "lit/directives/repeat.js";
import { api, gateway, installed, navigate } from "/m/ui/client.js";
import type { ApiResult } from "/m/ui/client.js";

import { Feed } from "./feed.ts";
import { parseFolders, parseList, parseMore, parseSession } from "./list-feed.ts";
import type { FoldersAnswer, ListAnswer, MoreAnswer, SessionAnswer } from "./list-feed.ts";
import {
  addMore, CLEAR, filterLine, FIND_LABEL, FIND_PLACEHOLDER, FIND_WAIT_MS, folderMenu, foldersPath, harnessMenu,
  listPath, listView, LOCAL, morePath, NARROW_FOLDERS, NO_FILTERS, queryOf, RANGE_FROM, RANGE_TO, refresh,
  REFRESH_MS, sessionPath, showMore, showMoreProgram, shownRow, statusLine, toggleOpen, toggleProgram, whenMenu,
  whenOf,
} from "./list.ts";
import type {
  EmptyView, FilterKey, FilterLine, Filters, GroupView, LineView, ListState, MenuItem, MoreRequest, Query, RowView,
  StatusLine, When,
} from "./list.ts";

import { COPIED, COPIED_MS, openedView, readAgain } from "./opened.ts";
import type { SaidView } from "./opened.ts";

const TAG = "moxi-sessions-list";

const STYLES = css`
  :host { display: block; min-width: 0; max-width: 680px; margin: 0 auto; padding: 4px 0 16px;
          color: var(--moxi-text); font-family: var(--moxi-font-sans); font-size: 14px; line-height: 20px; }
  * { box-sizing: border-box; }
  p { margin: 0; }
  button { font: inherit; color: inherit; background: none; border: 0; padding: 0; margin: 0; cursor: pointer;
           text-align: left; }
  a { color: inherit; text-decoration: none; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }

  .find { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; border: 1px solid var(--moxi-border);
          border-radius: 10px; padding: 10px 14px; font-size: 15px; line-height: 22px; color: var(--moxi-text-dim); }
  .find input { flex: 1 1 auto; min-width: 0; font: inherit; color: var(--moxi-text); background: none; border: 0;
                padding: 0; margin: 0; outline: none; }
  .find input::placeholder { color: var(--moxi-text-dim); opacity: 1; }
  .find:focus-within { border-color: var(--moxi-border-strong); }
  .find .x { flex: none; font-size: 14px; }

  .filt { display: flex; justify-content: space-between; align-items: baseline; gap: 4px 14px; color: var(--moxi-text-dim);
          padding: 0 2px; margin-top: 12px; }
  .filt .fs { display: flex; flex-wrap: wrap; align-items: baseline; min-width: 0; row-gap: 4px; }
  .filt .f { display: inline-flex; align-items: baseline; white-space: nowrap; }
  .filt .k { margin-right: 8px; }
  .filt .sep { margin: 0 12px; }
  /* A separator ends the filter before it, so a wrapped line never starts with one; the
     one that ends a line is hidden, keeping its place so no filter moves up a line. */
  .filt .f[data-end] > .sep { visibility: hidden; }
  .filt button.set { color: var(--moxi-text); }
  .filt .v { margin-left: 6px; font-size: 15px; line-height: 0; position: relative; top: 1px; color: var(--moxi-text-dim); }
  .filt .n { flex: none; white-space: nowrap; font-variant-numeric: tabular-nums; }
  .filt { position: relative; }
  .anchor { position: relative; display: inline-block; }
  /* The Folder menu drops under its button. The other two drop at the line's right end,
     so no menu reaches past the list's width whatever the filters are set to. */
  .anchor[data-filter="harness"], .anchor[data-filter="when"] { position: static; }
  .anchor[data-filter="harness"] .menu, .anchor[data-filter="when"] .menu { left: auto; right: 0; width: 280px; }
  .menu { position: absolute; z-index: 2; left: 0; top: calc(100% + 8px); width: 400px; max-height: 420px; overflow-y: auto;
          background: var(--moxi-bg); border: 1px solid var(--moxi-border-strong); border-radius: 10px; padding: 6px 0;
          color: var(--moxi-text); white-space: normal; }
  .menu hr { border: 0; border-top: 1px solid var(--moxi-border); margin: 6px 0; }
  .menu .mi { display: grid; grid-template-columns: 20px fit-content(70%) minmax(0, 1fr) auto; align-items: baseline;
              width: 100%; padding: 7px 14px; }
  .menu .mi:hover { background: var(--moxi-bg-hover); }
  .menu .l, .menu .d { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .menu .d { color: var(--moxi-text-dim); padding-left: 10px; }
  .menu .t { color: var(--moxi-text-dim); font-variant-numeric: tabular-nums; padding-left: 10px; white-space: nowrap; }
  .menu .narrow { display: block; width: 100%; font: inherit; color: var(--moxi-text); background: none; border: 0;
                  border-bottom: 1px solid var(--moxi-border); padding: 11px 14px 10px; margin: -6px 0 6px; outline: none; }
  .menu .narrow::placeholder { color: var(--moxi-text-dim); opacity: 1; }
  .menu .say { color: var(--moxi-text-dim); padding: 7px 14px 7px 34px; }
  .menu .range { display: flex; flex-wrap: wrap; gap: 8px 14px; padding: 7px 14px 7px 34px; color: var(--moxi-text-dim); }
  .menu .range label { display: inline-flex; align-items: baseline; gap: 8px; }
  .menu .range input { font: inherit; color: var(--moxi-text); background: var(--moxi-bg); border: 1px solid var(--moxi-border);
                       border-radius: 7px; padding: 2px 6px; min-width: 0; }

  .status { padding: 12px 2px 0; }
  .status.dim { color: var(--moxi-text-dim); }
  .grps { margin-top: 16px; border-top: 1px solid var(--moxi-border); }
  .grp { display: grid; grid-template-columns: 96px minmax(0, 1fr); border-bottom: 1px solid var(--moxi-border);
         padding: 5px 2px; }
  .grp .gk { color: var(--moxi-text-dim); padding-top: 7px; }
  .grp .gl { grid-column: 2; min-width: 0; }
  moxi-ui-row[open] + moxi-ui-row { border-top: 1px solid var(--moxi-border); padding-top: 3px; }
  .do { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  /* A row opened in place: label, what it says, and an act at the right end. */
  .det { margin: 9px 0 3px; padding: 8px 0 0; border-top: 1px solid var(--moxi-border); font-size: 13px; line-height: 18px;
         cursor: auto; }
  .det .r { display: grid; grid-template-columns: 76px minmax(0, 1fr) auto; padding: 3px 0; align-items: baseline; }
  .det .k, .det .d { color: var(--moxi-text-dim); }
  .det .f { min-width: 0; overflow-wrap: anywhere; }
  .det .tm { color: var(--moxi-text-dim); font-variant-numeric: tabular-nums; margin-right: 8px; }
  .det .x { font-size: 13px; line-height: 18px; color: var(--moxi-text); padding-left: 18px; }
  .det code { font: 12px/18px var(--moxi-font-mono); color: var(--moxi-text); }
  .det .sub { display: block; }
  .det .sub + .sub { margin-top: 5px; }
  .det .st { padding: 3px 0; }
  .det .st.dim { color: var(--moxi-text-dim); }
  .det .foot { display: flex; flex-wrap: wrap; gap: 4px 18px; padding: 9px 0 0 76px; }
  .det .foot a { color: var(--moxi-text); }
  .det .foot button { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  .one { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; padding: 7px 0; }
  .one .t, .one.d { color: var(--moxi-text-dim); }
  .one .t { flex: none; }
  .grps > .one { padding: 12px 2px; }
  .note { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); padding: 12px 2px 0; overflow-wrap: anywhere; }

  .quietline { font-size: 22px; line-height: 32px; text-align: center; letter-spacing: -0.005em; padding: 150px 0 14px; }
  .first { color: var(--moxi-text-dim); line-height: 1.6; text-align: center; }
  .first p { max-width: 520px; margin: 0 auto; }
  .first p + p { margin-top: 10px; }
  .first .x { color: var(--moxi-text); border-bottom: 1px solid var(--moxi-border); }

  @media (max-width: 720px) {
    :host { padding-top: 10px; }
    .filt { flex-wrap: wrap; }
    .anchor { position: static; }
    .anchor .menu, .anchor[data-filter="harness"] .menu, .anchor[data-filter="when"] .menu { left: 0; right: 0; width: auto; }
    .grp { display: block; padding: 4px 0 6px; }
    .grp .gk { padding-top: 9px; font-size: 13px; }
    .quietline { padding-top: 60px; }
  }
`;

const NOT_READ = { loading: false, gone: false, failure: null, refused: null, value: null };

export class SessionsList extends LitElement {
  static styles = STYLES;

  static properties = {
    line: { type: String },
  };

  /** A line shown above the list: why the address that led here shows the list. */
  line: string | null = null;

  #filters: Filters = NO_FILTERS;
  /** The find field's text as typed; it becomes the filters' `find` after the wait. */
  #typed = "";
  #state: ListState | null = null;
  #feed: Feed<ListAnswer> | null = null;
  /** The query the feed's newest answer was asked with, and the answer last taken. */
  #asked: Query | null = null;
  #taken: ListAnswer | null = null;
  /** The `/api/more` reads, one after another. */
  #reads: Promise<void> = Promise.resolve();
  #moreStatus: StatusLine | null = null;
  #menu: FilterKey | null = null;
  #folders: Feed<FoldersAnswer> | null = null;
  #narrow = "";
  /** The read of each opened row's session, by the row's key. */
  #sessions = new Map<string, Feed<SessionAnswer>>();
  /** Which `Copy` reads `Copied`: the row's key and what was copied. */
  #copied: string | null = null;
  #copyTimer: ReturnType<typeof setTimeout> | null = null;
  #timer: ReturnType<typeof setInterval> | null = null;
  #findTimer: ReturnType<typeof setTimeout> | null = null;

  #shown = (): void => {
    if (document.visibilityState === "visible") this.#readList();
  };
  /** The filter line's filters, watched so a wrap is found when the width changes. */
  #watched: HTMLElement | null = null;
  #lines = new ResizeObserver(() => this.#markLineEnds());
  #pressed = (event: Event): void => {
    if (this.#menu === null) return;
    const inside = event.composedPath().some((node) => node instanceof Element && node.classList.contains("anchor"));
    if (!inside) this.#openMenu(null);
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#feed = new Feed<ListAnswer>(() => this.#askList(), parseList, () => this.#listRead());
    document.addEventListener("visibilitychange", this.#shown);
    document.addEventListener("click", this.#pressed);
    this.#timer = setInterval(this.#shown, REFRESH_MS);
    this.#readList();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#lines.disconnect();
    this.#watched = null;
    this.#feed?.close();
    this.#folders?.close();
    this.#feed = this.#folders = null;
    for (const feed of this.#sessions.values()) feed.close();
    this.#sessions.clear();
    if (this.#copyTimer !== null) clearTimeout(this.#copyTimer);
    this.#copyTimer = null;
    document.removeEventListener("visibilitychange", this.#shown);
    document.removeEventListener("click", this.#pressed);
    if (this.#timer !== null) clearInterval(this.#timer);
    if (this.#findTimer !== null) clearTimeout(this.#findTimer);
    this.#timer = this.#findTimer = null;
  }

  // -- reading -----------------------------------------------------------------------------

  /** The query the filters make now. `When` is in days of the server's `today`, so it is
   * worked out again before each read. */
  #query(): Query {
    return queryOf(this.#filters, this.#state?.today ?? LOCAL.day(Date.now() / 1000));
  }

  async #askList(): Promise<ApiResult> {
    const query = this.#query();
    const answer = await api("sessions", listPath(query));
    this.#asked = query;
    return answer;
  }

  #readList(): void {
    void this.#feed?.refresh();
  }

  #listRead(): void {
    const value = this.#feed?.value ?? null;
    if (value !== null && value !== this.#taken && this.#asked !== null) {
      this.#taken = value;
      const { state, reads } = refresh(this.#state, value, this.#asked);
      this.#state = state;
      this.#moreStatus = null;
      for (const read of reads) this.#readMore(read);
      this.#readOpened();
    }
    this.requestUpdate();
  }

  /** After a newer list: reads again the session of each opened row the list shows
   * changed, and of one whose last read gave no answer. The others' answers stand. */
  #readOpened(): void {
    const state = this.#state;
    if (state === null) return;
    for (const key of state.opened) {
      const row = shownRow(state, key);
      const feed = this.#sessions.get(key) ?? null;
      if (row !== null && feed === null) this.#readSession(row.harness, row.session);
      else if (feed !== null && readAgain(feed, row)) void feed.refresh();
    }
  }

  /** Reads the session a row was opened for. */
  #readSession(harness: string, session: string): void {
    const feed = new Feed<SessionAnswer>(() => api("sessions", sessionPath(harness, session)), parseSession,
                                         () => this.requestUpdate());
    this.#sessions.set(`${harness}/${session}`, feed);
    void feed.refresh();
  }

  /** Reads `request` and the reads that follow it, after the reads already asked for. */
  #readMore(request: MoreRequest | null): void {
    if (request === null) return;
    this.#reads = this.#reads.then(() => this.#more(request));
  }

  async #more(request: MoreRequest): Promise<void> {
    let next: MoreRequest | null = request;
    while (next !== null && this.#feed !== null) {
      const state = this.#state;
      if (state === null || next.now !== state.now) return;
      const asked = next;
      const feed = new Feed<MoreAnswer>(() => api("sessions", morePath(asked, state.query)), parseMore, () => {});
      await feed.refresh();
      if (this.#state === null || this.#feed === null) return;
      if (feed.value === null) {
        if (asked.now === this.#state.now) this.#moreStatus = statusLine(feed, "more of the list");
        this.requestUpdate();
        return;
      }
      const added = addMore(this.#state, asked, feed.value);
      this.#state = added.state;
      this.#moreStatus = null;
      next = added.next;
      this.requestUpdate();
    }
  }

  // -- acts --------------------------------------------------------------------------------

  #toggleRow(row: RowView): void {
    if (this.#state === null) return;
    this.#state = toggleOpen(this.#state, row.key);
    this.#sessions.get(row.key)?.close();
    this.#sessions.delete(row.key);
    if (this.#state.opened.includes(row.key)) this.#readSession(row.harness, row.session);
    this.requestUpdate();
  }

  /** Puts `text` on the clipboard; the act named `what` then reads `Copied` for a while. */
  async #copy(what: string, text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    this.#copied = what;
    if (this.#copyTimer !== null) clearTimeout(this.#copyTimer);
    this.#copyTimer = setTimeout(() => {
      this.#copied = this.#copyTimer = null;
      this.requestUpdate();
    }, COPIED_MS);
    this.requestUpdate();
  }

  #showMore(key: string): void {
    if (this.#state !== null) this.#readMore(showMore(this.#state, key));
  }

  #showMoreProgram(key: string): void {
    if (this.#state !== null) this.#readMore(showMoreProgram(this.#state, key));
  }

  #toggleProgram(key: string): void {
    if (this.#state === null) return;
    const { state, read } = toggleProgram(this.#state, key);
    this.#state = state;
    this.#readMore(read);
    this.requestUpdate();
  }

  /** Takes the filters as the person left them, and reads the list again when they make
   * another query. */
  #setFilters(next: Filters): void {
    const before = listPath(this.#query());
    this.#filters = next;
    if (listPath(this.#query()) !== before) {
      this.#readList();
      if (this.#menu === "folder") void this.#folders?.refresh();
    }
    this.requestUpdate();
  }

  #type(text: string): void {
    this.#typed = text;
    if (this.#findTimer !== null) clearTimeout(this.#findTimer);
    this.#findTimer = setTimeout(() => {
      this.#findTimer = null;
      this.#setFilters({ ...this.#filters, find: this.#typed });
    }, FIND_WAIT_MS);
    this.requestUpdate();
  }

  #clearFind(): void {
    if (this.#findTimer !== null) clearTimeout(this.#findTimer);
    this.#findTimer = null;
    this.#typed = "";
    this.#setFilters({ ...this.#filters, find: "" });
  }

  #openMenu(key: FilterKey | null): void {
    this.#menu = key;
    this.#folders?.close();
    this.#folders = null;
    this.#narrow = "";
    if (key === "folder") {
      this.#folders = new Feed<FoldersAnswer>(() => api("sessions", foldersPath(this.#query())), parseFolders,
                                              () => this.requestUpdate());
      void this.#folders.refresh();
    }
    this.requestUpdate();
  }

  #pick(change: Partial<Filters>): void {
    this.#openMenu(null);
    this.#setFilters({ ...this.#filters, ...change });
  }

  #range(end: "from" | "to", day: string): void {
    const when = this.#filters.when;
    const kept = when.kind === "range" ? when : { kind: "range" as const, from: null, to: null };
    this.#setFilters({ ...this.#filters, when: whenOf({ ...kept, [end]: day !== "" ? day : null }) });
  }

  // -- drawing -----------------------------------------------------------------------------

  /** What an opened row shows under its lines, in the row's default slot. */
  protected opened(row: RowView): unknown {
    const state = this.#state;
    if (state === null) return nothing;
    const view = openedView(row, this.#sessions.get(row.key) ?? null, { today: state.today, roots: state.roots });
    const said = (line: SaidView | null, name: string) => (line === null ? nothing
      : html`<div class="r" data-line=${name}><span class="k">${line.label}</span><span class="f">${
          line.time !== null ? html`<span class="tm">${line.time}</span>` : nothing}${line.text}</span><span></span></div>`);
    const copy = (what: string, text: string, words: string, tone: string) => {
      const key = `${row.key} ${what}`;
      return html`<button type="button" class=${tone} data-action=${`copy-${what}`}
        @click=${() => void this.#copy(key, text)}>${this.#copied === key ? COPIED : words}</button>`;
    };
    const { status, subagents, session, resume, transcript } = view;
    return html`<div class="det" data-part="opened">
      ${status !== null ? html`<p class=${status.state === "waiting" ? "st dim" : "st"} data-line=${status.state}
        role="status">${status.line}</p>` : nothing}
      ${said(view.prompt, "you")}${said(view.reply, "reply")}
      ${subagents !== null ? html`<div class="r" data-line="subagents"><span class="k">${subagents.label}</span><span class="f">
        ${subagents.items.map((sub) => html`<span class="sub" data-line="subagent"><span>${sub.name}</span>${
          sub.facts !== null ? html` <span class="d">· ${sub.facts}</span>` : nothing}${
          sub.last !== null ? html`<br><span class="d">${sub.last}</span>` : nothing}</span>`)}
        ${subagents.notice !== null ? html`<span class="sub d" data-line="subagents-unread">${subagents.notice}</span>` : nothing}
        </span><span></span></div>` : nothing}
      ${session !== null ? html`<div class="r" data-line="session"><span class="k">${session.label}</span><span class="f">${
        session.lead}${session.rest !== null ? html` <span class="d">· ${session.rest}</span>` : nothing}</span><span></span></div>`
        : nothing}
      ${resume !== null ? html`<div class="r" data-line="resume"><span class="k">${resume.label}</span><span class="f"><code>${
        resume.command}</code>${resume.where !== null ? html`<br><span class="d" data-line="where">${resume.where}</span>` : nothing
        }</span>${copy("resume", resume.command, resume.copy, "x")}</div>` : nothing}
      <div class="foot">
        ${transcript !== null ? html`<a href=${`/m/sessions/${transcript.route}`} data-action="transcript"
          @click=${(event: MouseEvent) => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            navigate("sessions", transcript.route);
          }}>${transcript.text}</a>` : nothing}
        ${copy("id", view.copyId.id, view.copyId.text, "d")}
      </div>
    </div>`;
  }

  protected updated(): void {
    const filters = this.renderRoot.querySelector<HTMLElement>(".filt .fs");
    if (filters !== this.#watched) {
      this.#lines.disconnect();
      if (filters !== null) this.#lines.observe(filters);
      this.#watched = filters;
    }
    this.#markLineEnds();
  }

  /** Marks each filter that ends a line of the filter line, so its separator is hidden.
   * A filter ends a line when the next one starts lower, or when it is the last. Hiding
   * keeps the separator's place, so marking moves nothing and the marks hold. */
  #markLineEnds(): void {
    const items = [...(this.#watched?.querySelectorAll<HTMLElement>(":scope > .f") ?? [])];
    items.forEach((item, at) => {
      const next = items[at + 1];
      item.toggleAttribute("data-end", next === undefined || next.offsetTop >= item.offsetTop + item.offsetHeight);
    });
  }

  render() {
    const feed = this.#feed;
    const status = feed !== null ? statusLine(feed) : null;
    const state = this.#state;
    const address = this.line !== null && this.line !== ""
      ? html`<p class="status" data-line="unknown" role="status">${this.line}</p>` : nothing;
    if (status?.state === "gone") return html`${address}${this.#status(status)}`;
    if (state === null) return html`${address}${status !== null ? this.#status(status) : nothing}`;
    const view = listView(state, { launcher: installed("launcher") });
    const filters = filterLine(state, this.#filters);
    return html`${address}
      ${filters !== null ? html`${this.#find()}${this.#filt(filters, view.count)}` : nothing}
      ${status !== null ? this.#status(status) : nothing}
      ${this.#moreStatus !== null ? this.#status(this.#moreStatus) : nothing}
      ${view.empty !== null ? this.#empty(view.empty)
        : view.groups.length ? html`<div class="grps" data-part="groups">
            ${repeat(view.groups, (group) => group.key, (group) => this.#group(group))}</div>` : nothing}
      ${view.notices.map((notice) => html`<p class="note" data-line="unread" data-harness=${notice.harness}>${notice.line}${
        notice.reason !== null && !gateway() ? html`: <span data-line="reason">${notice.reason}</span>` : nothing}</p>`)}
      ${view.unlisted !== null ? html`<p class="note" data-line="unlisted">${view.unlisted}</p>` : nothing}`;
  }

  #status(status: StatusLine) {
    return html`<p class=${status.state === "waiting" ? "status dim" : "status"} data-line=${status.state}
      role="status">${status.line}</p>`;
  }

  #find() {
    return html`<div class="find" data-part="find">
      <input type="text" aria-label=${FIND_LABEL} placeholder=${FIND_PLACEHOLDER} autocomplete="off" spellcheck="false"
        .value=${live(this.#typed)} @input=${(event: Event) => this.#type((event.target as HTMLInputElement).value)}>
      ${this.#typed !== "" ? html`<button type="button" class="x" data-action="clear-find"
        @click=${() => this.#clearFind()}>${CLEAR}</button>` : nothing}
    </div>`;
  }

  #filt(line: FilterLine, count: string | null) {
    return html`<div class="filt" data-part="filters" @keydown=${(event: KeyboardEvent) => {
        if (event.key === "Escape" && this.#menu !== null) this.#openMenu(null);
      }}>
      <span class="fs">
        ${line.filters.map((filter, at) => html`<span class="f"><span class="k">${filter.label}</span><span class="anchor"
            data-filter=${filter.key}>
            <button type="button" class=${filter.set ? "set" : ""} data-action="filter" aria-haspopup="true"
              aria-label=${`${filter.label}: ${filter.value}`} aria-expanded=${this.#menu === filter.key ? "true" : "false"}
              @click=${() => this.#openMenu(this.#menu === filter.key ? null : filter.key)}>${filter.value}<span
              class="v" aria-hidden="true">▾</span></button>
            ${this.#menu === filter.key ? this.#menuOf(filter.key) : nothing}</span>${
            at < line.filters.length - 1 || line.clear !== null ? html`<span class="sep">·</span>` : nothing}</span>`)}
        ${line.clear !== null ? html`<span class="f"><button type="button" data-action="clear"
          @click=${() => this.#pick({ folder: null, harness: null, when: { kind: "any" } })}>${line.clear}</button></span>` : nothing}
      </span>
      ${count !== null ? html`<span class="n" data-line="count">${count}</span>` : nothing}
    </div>`;
  }

  #item(item: MenuItem<unknown>, pick: () => void) {
    return html`<button type="button" class="mi" role="menuitemradio" aria-checked=${item.checked ? "true" : "false"}
      @click=${pick}><span aria-hidden="true">${item.checked ? "✓" : ""}</span><span class="l">${item.label}</span><span
      class="d">${item.dim ?? ""}</span><span class="t">${item.tail ?? ""}</span></button>`;
  }

  #menuOf(key: FilterKey) {
    const state = this.#state;
    if (state === null) return nothing;
    if (key === "harness") {
      const [all, ...rest] = harnessMenu(state, this.#filters);
      return html`<div class="menu" role="menu" data-menu="harness">
        ${all !== undefined ? this.#item(all, () => this.#pick({ harness: null })) : nothing}
        ${rest.length ? html`<hr>` : nothing}
        ${rest.map((item) => this.#item(item, () => this.#pick({ harness: item.value })))}</div>`;
    }
    if (key === "when") {
      const when = whenOf(this.#filters.when);
      const day = (end: "from" | "to"): string => (when.kind === "range" ? when[end] ?? "" : "");
      return html`<div class="menu" role="menu" data-menu="when">
        ${whenMenu(this.#filters).map((item) => this.#item(item, () => this.#pick({ when: item.value as When })))}
        <hr>
        <div class="range">
          <label>${RANGE_FROM}<input type="date" name="from" .value=${live(day("from"))}
            @change=${(event: Event) => this.#range("from", (event.target as HTMLInputElement).value)}></label>
          <label>${RANGE_TO}<input type="date" name="to" .value=${live(day("to"))}
            @change=${(event: Event) => this.#range("to", (event.target as HTMLInputElement).value)}></label>
        </div></div>`;
    }
    const feed = this.#folders;
    const menu = folderMenu(feed?.value ?? null, this.#filters, this.#narrow, state.today, LOCAL, state.home);
    const status = statusLine(feed ?? NOT_READ, "the folders");
    return html`<div class="menu" role="menu" data-menu="folder">
      <input type="text" class="narrow" aria-label=${NARROW_FOLDERS} placeholder=${NARROW_FOLDERS} autocomplete="off"
        spellcheck="false" .value=${live(this.#narrow)}
        @input=${(event: Event) => { this.#narrow = (event.target as HTMLInputElement).value; this.requestUpdate(); }}>
      ${this.#item(menu.all, () => this.#pick({ folder: null }))}
      <hr>
      ${status !== null ? html`<p class="say" data-line=${status.state} role="status">${status.line}</p>` : nothing}
      ${menu.items.map((item) => this.#item(item, () => this.#pick({ folder: item.value })))}
      ${menu.line !== null ? html`<p class="say" data-line="folders">${menu.line}</p>` : nothing}</div>`;
  }

  #empty(empty: EmptyView) {
    if (empty.sentence === null) {
      return html`<div class="grps" data-part="groups"><p class="one d" data-line="empty">${empty.line}</p></div>`;
    }
    return html`<div class="quietline" data-line="empty">${empty.line}</div>
      <div class="first"><p data-line="empty-sentence">${empty.sentence}</p>
        ${empty.start !== null ? html`<p><a class="x" href="/m/ui/" data-action="home" @click=${(event: Event) => {
          event.preventDefault();
          navigate("ui", "");
        }}>${empty.start}</a></p>` : nothing}</div>`;
  }

  #line(line: LineView, name: string, act: () => void, tone = "one d", expanded: boolean | null = null) {
    return html`<div class=${tone} data-line=${name}><span>${line.text}</span><button type="button" class="t"
      data-action=${name} aria-expanded=${ifDefined(expanded === null ? undefined : expanded ? "true" : "false")}
      @click=${act}>${line.act}</button></div>`;
  }

  #group(group: GroupView) {
    const program = group.program;
    return html`<section class="grp" data-group=${group.key}>
      ${group.label !== null ? html`<div class="gk" data-line="label">${group.label}</div>` : nothing}
      <div class="gl">
        ${repeat(group.rows, (row) => row.key, (row) => this.#row(row))}
        ${group.more !== null ? this.#line(group.more, "more", () => this.#showMore(group.key)) : nothing}
        ${program !== null ? html`${this.#line(program, "program", () => this.#toggleProgram(group.key), "one d", program.open)}
          ${repeat(program.rows, (row) => row.key, (row) => this.#row(row))}
          ${program.more !== null ? this.#line(program.more, "program-more", () => this.#showMoreProgram(group.key)) : nothing}`
          : nothing}
        ${group.earlier !== null ? this.#line(group.earlier, "earlier", () => this.#showMore(group.key), "one") : nothing}
      </div>
    </section>`;
  }

  #row(row: RowView) {
    const toggle = (): void => this.#toggleRow(row);
    return html`<moxi-ui-row toggles data-key=${row.key} name=${row.name} where=${ifDefined(row.where ?? undefined)}
        fact=${ifDefined(row.fact ?? undefined)} lead=${ifDefined(row.lead ?? undefined)}
        time=${ifDefined(row.time ?? undefined)} said-by=${ifDefined(row.saidBy ?? undefined)}
        rest=${ifDefined(row.rest ?? undefined)} ?open=${row.open} @moxi-row-toggle=${toggle}>
      ${row.act !== null && !gateway() ? html`<button type="button" slot="acts" class="do" data-action="open"
        aria-expanded=${row.open ? "true" : "false"} @click=${toggle}>${row.act}</button>` : nothing}
      ${row.open ? this.opened(row) : nothing}
    </moxi-ui-row>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, SessionsList);

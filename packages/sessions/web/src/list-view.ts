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
//
// At its own address, `/m/sessions/`, the list keeps its state in the query of that
// address (`address.ts`): the filters, the find text as sent and how far each group was
// paged. It reads the address when it is placed, so Back from a transcript, a reload and
// a shared link bring the list back, and an address with a depth is read to that depth
// first (`restore.ts`). Its one writer rewrites the entry it is on after each change, and
// adds none. The rows opened in place and the scroll are kept in the history entry's own
// memory, which Back and a reload bring back and a link does not. What the address held
// that the list does not take is named in one dim line above the list. The list under
// an unknown route neither reads nor writes either.
//
// With launcher installed the list also reads launcher's `/api/running` with each read of
// the list, and at the desk follows launcher's topic `running`; the running group
// (`running.ts`, `list.ts`) is drawn first. A running row opens in place as the list's rows
// do. `Keep warm` posts the launch's request id to launcher's `/api/keep-warm` and is
// disabled until the answer and the next value of `running` have come, or for 20 s; a
// refusal's words are the row's third line until the next value.
//
// With turns installed, at the desk, the list also reads turns' `/api/sessions` with each
// read of the list and follows turns' topic `sessions`. Each running row then says what
// turns says of its session (`live.ts`): its state word and what is busy, its trouble,
// and, opened, turns' `Now`, `Agents` and `Prompt box` lines. In every state of the list
// it also links turns' page, worded with its title (`Live sessions`): at the filter line's
// right end before the count, or alone at the right of a line of its own where no filter
// line is drawn. turns' routes are not the gateway's, so the phone reads none of it and
// draws no link.
//
// With cache installed, at the desk, the list also reads cache's `/api/clocks` when it
// opens and when its tab is shown again, and follows cache's topic `clocks`. A row whose
// session has a clock record then ends its first line in `warm until 19:12` or `cold`,
// and, opened, has a `Cache` line linking cache's page (`clocks.ts`); on a running launch
// launcher's cache fact wins. The list is drawn again when a warm record's time comes, so
// the row turns cold then. A read that fails draws nothing of it. cache's routes are not
// the gateway's, so the phone reads none of it.

import { css, html, LitElement, nothing } from "lit";
import { ifDefined } from "lit/directives/if-defined.js";
import { live } from "lit/directives/live.js";
import { repeat } from "lit/directives/repeat.js";
import { api, components, gateway, installed, navigate, readLater, subscribe } from "/m/ui/client.js";

import { addressLine, LIST_PATH, memoryOf, readAddress, withMemory } from "./address.ts";
import { Feed } from "./feed.ts";
import { parseFolders, parseSession } from "./list-feed.ts";
import type { FoldersAnswer, SessionAnswer } from "./list-feed.ts";
import {
  CLEAR, filterLine, FIND_LABEL, FIND_PLACEHOLDER, FIND_WAIT_MS, folderMenu, foldersPath, harnessMenu, listPath,
  listView, LOCAL, NARROW_FOLDERS, NO_FILTERS, RANGE_FROM, RANGE_TO, REFRESH_MS, sessionPath, shownRow, statusLine, toggleOpen,
  whenMenu, whenOf,
} from "./list.ts";
import type {
  EmptyView, FilterKey, FilterLine, Filters, GroupView, LineView, MenuItem, RowView, RunningGroupView, RunningRowView,
  StatusLine, When,
} from "./list.ts";
import { ListReader, mayWrite, SavedScroll, unreadOpened } from "./restore.ts";
import type { Act, Change } from "./restore.ts";
import {
  answered, expired, KEEP_WARM, KEEP_WARM_PATH, NO_PRESSES, parseRunning, pressBody, pressed, pressNote, PRESS_WAIT_MS,
  RUNNING_PATH, RUNNING_TOPIC, runningRead, valueCame,
} from "./running.ts";
import type { Presses, RunningRead } from "./running.ts";
import { LABELS as LIVE_LABELS, LIVE_PATH, LIVE_TOPIC, liveRead, parseLive } from "./live.ts";
import type { LiveOpened, LiveRead } from "./live.ts";
import { CLOCKS_PATH, CLOCKS_TOPIC, clocksRead, clocksTopic, nextCold, serverNow } from "./clocks.ts";
import type { Clocks } from "./clocks.ts";

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
  .filt .n .x { color: var(--moxi-text); }
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
  /* Keep warm is set in the text colour: the one act a session going cold uncovered carries. */
  .do.warm { color: var(--moxi-text); }
  .do:hover:not(:disabled) { text-decoration: underline; }
  .do:disabled { color: var(--moxi-text-faint); opacity: 0.6; cursor: default; }
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

/** The user's acts that drop the saved scroll. */
const ACTS = ["wheel", "touchstart", "touchmove", "pointerdown", "keydown"] as const;
/** The scroll is written this long after scrolling stops. */
const SCROLL_WAIT_MS = 300;

export class SessionsList extends LitElement {
  static styles = STYLES;

  static properties = {
    line: { type: String },
  };

  /** A line shown above the list: why the address that led here shows the list. */
  line: string | null = null;

  /** The list's reads and what they hold: the filters, the list, its depth. */
  #reader: ListReader | null = null;
  /** The list is at its own address, so it reads and writes the address and the entry's
   * memory; under an unknown route it does neither. */
  #placed = false;
  /** What the address held that the list does not take, named in the line above it. */
  #dropped: string[] = [];
  /** The rows the entry's memory named, until the list holds rows of its own. */
  #remembered: string[] = [];
  #scroll = new SavedScroll(null);
  /** The scroll's one landing is under way or done. */
  #landing = false;
  /** The find field's text as typed; it becomes the filters' `find` after the wait. */
  #typed = "";
  #menu: FilterKey | null = null;
  #folders: Feed<FoldersAnswer> | null = null;
  #narrow = "";
  /** The read of each opened row's session, by the row's key, and the last refresh of it. */
  #sessions = new Map<string, Feed<SessionAnswer>>();
  #sessionReads = new Map<string, Promise<void>>();
  /** Which `Copy` reads `Copied`: the row's key and what was copied. */
  #copied: string | null = null;
  #copyTimer: ReturnType<typeof setTimeout> | null = null;
  #timer: ReturnType<typeof setInterval> | null = null;
  /** Cancels the find's list read waiting to be sent. */
  #cancelFind = (): void => {};
  #scrollTimer: ReturnType<typeof setTimeout> | null = null;
  /** Launcher's running launches as last read, one read at a time; and the topic's stop. */
  #running: RunningRead | null = null;
  #runningOut = false;
  #runningAgain = false;
  #stopRunning: (() => void) | null = null;
  /** turns' sessions as last read, one read at a time; and the topic's stop. */
  #turnsRead: LiveRead | null = null;
  #turnsOut = false;
  #turnsAgain = false;
  #stopTurns: (() => void) | null = null;
  /** cache's records as last read, one read at a time; the topic's stop; and the wait for
   * the next warm record's cold time. */
  #clocks: Clocks | null = null;
  #clocksOut = false;
  #clocksAgain = false;
  #stopClocks: (() => void) | null = null;
  #coldTimer: ReturnType<typeof setTimeout> | null = null;
  /** The presses of `Keep warm`, and each press's longest wait, by request id. */
  #presses: Presses = NO_PRESSES;
  #pressWaits = new Map<string, ReturnType<typeof setTimeout>>();
  /** Settles at the first answer of launcher's running launches, so the scroll lands
   * after the running group above the list is drawn; settled from the start without
   * launcher. */
  #runningFirst: Promise<void> = Promise.resolve();
  #runningCame: (() => void) | null = null;

  #shown = (): void => {
    if (document.visibilityState === "visible") this.#readList();
  };
  /** The tab is shown again: the list and cache's records are read. */
  #shownAgain = (): void => {
    if (document.visibilityState !== "visible") return;
    this.#readList();
    void this.#readClocks();
  };
  /** The filter line's filters, watched so a wrap is found when the width changes. */
  #watched: HTMLElement | null = null;
  #lines = new ResizeObserver(() => this.#markLineEnds());
  #pressed = (event: Event): void => {
    if (this.#menu === null) return;
    const inside = event.composedPath().some((node) => node instanceof Element && node.classList.contains("anchor"));
    if (!inside) this.#openMenu(null);
  };
  /** A user's act drops the saved scroll; a press or a key also writes the scroll, so a
   * band pick has it written before the shell moves. */
  #onAct = (event: Event): void => {
    const act = this.#scroll.take(event as Event & Act);
    if (act && (event.type === "pointerdown" || event.type === "keydown")) this.#remember();
  };
  #onScroll = (): void => {
    if (this.#scrollTimer !== null) clearTimeout(this.#scrollTimer);
    this.#scrollTimer = setTimeout(() => {
      this.#scrollTimer = null;
      this.#remember();
    }, SCROLL_WAIT_MS);
  };
  #onPageHide = (): void => this.#remember();

  connectedCallback(): void {
    super.connectedCallback();
    this.#placed = location.pathname === LIST_PATH;
    const address = readAddress(this.#placed ? location.search : "");
    const memory = this.#placed ? memoryOf(history.state) : { opened: [], y: null };
    this.#typed = address.filters.find;
    this.#dropped = address.dropped;
    this.#remembered = memory.opened;
    this.#scroll = new SavedScroll(memory.y);
    this.#landing = false;
    this.#reader = new ListReader({ read: (path) => api("sessions", path), onChange: (change) => this.#onChange(change) });
    document.addEventListener("visibilitychange", this.#shownAgain);
    document.addEventListener("click", this.#pressed);
    for (const type of ACTS) document.addEventListener(type, this.#onAct, { capture: true, passive: true });
    addEventListener("scroll", this.#onScroll, { passive: true });
    addEventListener("pagehide", this.#onPageHide);
    this.#timer = setInterval(this.#shown, REFRESH_MS);
    if (installed("launcher")) {
      this.#runningFirst = new Promise((done) => { this.#runningCame = done; });
      if (!gateway()) this.#stopRunning = subscribe("launcher", RUNNING_TOPIC, (data) => this.#takeRunning(data));
    }
    if (this.#readsTurns()) this.#stopTurns = subscribe("turns", LIVE_TOPIC, (data) => this.#takeTurns(data));
    if (this.#readsCache()) this.#stopClocks = subscribe("cache", CLOCKS_TOPIC, (data) => this.#takeClocks(data));
    this.#reader.place(address, memory.opened);
    void this.#readRunning();
    void this.#readTurns();
    void this.#readClocks();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#lines.disconnect();
    this.#watched = null;
    this.#reader?.close();
    this.#reader = null;
    this.#folders?.close();
    this.#folders = null;
    for (const feed of this.#sessions.values()) feed.close();
    this.#sessions.clear();
    this.#sessionReads.clear();
    if (this.#copyTimer !== null) clearTimeout(this.#copyTimer);
    this.#copyTimer = null;
    document.removeEventListener("visibilitychange", this.#shownAgain);
    document.removeEventListener("click", this.#pressed);
    for (const type of ACTS) document.removeEventListener(type, this.#onAct, { capture: true });
    removeEventListener("scroll", this.#onScroll);
    removeEventListener("pagehide", this.#onPageHide);
    if (this.#timer !== null) clearInterval(this.#timer);
    this.#cancelFind();
    if (this.#scrollTimer !== null) clearTimeout(this.#scrollTimer);
    this.#timer = this.#scrollTimer = null;
    this.#stopRunning?.();
    this.#stopRunning = null;
    this.#stopTurns?.();
    this.#stopTurns = null;
    this.#turnsRead = null;
    this.#stopClocks?.();
    this.#stopClocks = null;
    this.#clocks = null;
    if (this.#coldTimer !== null) clearTimeout(this.#coldTimer);
    this.#coldTimer = null;
    this.#ranOnce();
    for (const wait of this.#pressWaits.values()) clearTimeout(wait);
    this.#pressWaits.clear();
    this.#presses = NO_PRESSES;
  }

  // -- reading -----------------------------------------------------------------------------

  /** Reads the list and launcher's running launches: at connect, when the tab is shown
   * again and on the tick. */
  #readList(): void {
    this.#reader?.readList();
    void this.#readRunning();
    void this.#readTurns();
  }

  /** Whether the list reads turns: with turns installed, at the desk. */
  #readsTurns(): boolean {
    return installed("turns") && !gateway();
  }

  /** Reads turns' sessions, where the list reads turns; one read at a time, and one more
   * after it when another was asked for meanwhile. */
  async #readTurns(): Promise<void> {
    if (!this.#readsTurns()) {
      this.#turnsRead = null;
      return;
    }
    if (this.#turnsOut) {
      this.#turnsAgain = true;
      return;
    }
    this.#turnsOut = true;
    try {
      do {
        this.#turnsAgain = false;
        const answer = await api("turns", LIVE_PATH);
        if (!this.isConnected) return;
        this.#turnsRead = liveRead(answer);
        this.requestUpdate();
      } while (this.#turnsAgain);
    } finally {
      this.#turnsOut = false;
    }
  }

  /** A value of turns' topic `sessions`; one that can't be read reads the route again. */
  #takeTurns(data: unknown): void {
    if (!this.isConnected) return;
    const value = parseLive(data);
    if (value === null) {
      void this.#readTurns();
      return;
    }
    this.#turnsRead = { value, failure: null };
    this.requestUpdate();
  }

  /** Whether the list reads cache: with cache installed, at the desk. */
  #readsCache(): boolean {
    return installed("cache") && !gateway();
  }

  /** Reads cache's records, where the list reads cache; one read at a time, and one more
   * after it when another was asked for meanwhile. A failed read leaves nothing. */
  async #readClocks(): Promise<void> {
    if (!this.#readsCache()) {
      this.#clocks = null;
      return;
    }
    if (this.#clocksOut) {
      this.#clocksAgain = true;
      return;
    }
    this.#clocksOut = true;
    try {
      do {
        this.#clocksAgain = false;
        const answer = await api("cache", CLOCKS_PATH);
        if (!this.isConnected) return;
        this.#clocks = clocksRead(answer, Date.now() / 1000);
        this.requestUpdate();
      } while (this.#clocksAgain);
    } finally {
      this.#clocksOut = false;
    }
  }

  /** A value of cache's topic `clocks`; one that can't be read, or one before the first
   * read, reads the route again. */
  #takeClocks(data: unknown): void {
    if (!this.isConnected) return;
    const next = clocksTopic(this.#clocks, data);
    if (next === null) {
      void this.#readClocks();
      return;
    }
    this.#clocks = next;
    this.requestUpdate();
  }

  /** Draws the list again at the next warm record's cold time, so its row turns cold then. */
  #waitForCold(now: number | null): void {
    if (this.#coldTimer !== null) clearTimeout(this.#coldTimer);
    this.#coldTimer = null;
    const at = now === null ? null : nextCold(this.#clocks, now);
    if (at === null || now === null) return;
    this.#coldTimer = setTimeout(() => {
      this.#coldTimer = null;
      this.requestUpdate();
    }, Math.min(Math.max(0, at - now) * 1000 + 50, 2 ** 31 - 1));
  }

  /** Reads launcher's running launches, with launcher installed; one read at a time, and
   * one more after it when another was asked for meanwhile. */
  async #readRunning(): Promise<void> {
    if (!installed("launcher")) {
      this.#running = null;
      this.#ranOnce();
      return;
    }
    if (this.#runningOut) {
      this.#runningAgain = true;
      return;
    }
    this.#runningOut = true;
    try {
      do {
        this.#runningAgain = false;
        const answer = await api("launcher", RUNNING_PATH);
        if (!this.isConnected) return;
        this.#running = runningRead(answer);
        this.#ranOnce();
        if (this.#running?.value) this.#presses = valueCame(this.#presses, this.#running.value);
        this.requestUpdate();
      } while (this.#runningAgain);
    } finally {
      this.#runningOut = false;
    }
  }

  /** A value of the topic `running`. */
  #takeRunning(data: unknown): void {
    const value = parseRunning(data);
    if (value === null || !this.isConnected) return;
    this.#running = runningRead({ status: 200, data });
    this.#ranOnce();
    if (value.state === "up") this.#presses = valueCame(this.#presses, value);
    this.requestUpdate();
  }

  /** A press of `Keep warm`: posts the launch's request id to launcher and waits, its
   * button disabled, for the answer and the next value of `running`, or `PRESS_WAIT_MS`;
   * a press that went reads that value at once. */
  async #press(request: string): Promise<void> {
    if (this.#presses.waiting[request] !== undefined) return;
    this.#presses = pressed(this.#presses, request);
    clearTimeout(this.#pressWaits.get(request));
    this.#pressWaits.set(request, setTimeout(() => {
      this.#pressWaits.delete(request);
      this.#presses = expired(this.#presses, request);
      this.requestUpdate();
    }, PRESS_WAIT_MS));
    this.requestUpdate();
    const answer = await api("launcher", KEEP_WARM_PATH, pressBody(request));
    if (!this.isConnected) return;
    const note = pressNote(answer);
    this.#presses = answered(this.#presses, request, note);
    this.requestUpdate();
    if (note === null) void this.#readRunning();
  }

  /** The first answer of the running launches has come, or none will. */
  #ranOnce(): void {
    this.#runningCame?.();
    this.#runningCame = null;
  }

  /** After each change of what the reader holds: the opened rows' reads, the writer, the
   * scroll's landing, then a redraw. */
  #onChange(change: Change): void {
    const reader = this.#reader;
    if (reader === null) return;
    if (change === "list") this.#readOpened();
    else if (change === "more" && reader.state !== null) {
      for (const row of unreadOpened(reader.state, new Set(this.#sessions.keys()))) this.#readSession(row.harness, row.session);
    }
    this.#write();
    this.#land();
    this.requestUpdate();
  }

  /** After a newer list: reads the session of each opened row now shown that has no read,
   * and again of one the list shows changed or whose last read gave no answer. The others'
   * answers stand. */
  #readOpened(): void {
    const state = this.#reader?.state ?? null;
    if (state === null) return;
    for (const key of state.opened) {
      const row = shownRow(state, key);
      const feed = this.#sessions.get(key) ?? null;
      if (row !== null && feed === null) this.#readSession(row.harness, row.session);
      else if (feed !== null && readAgain(feed, row)) this.#sessionReads.set(key, feed.refresh());
    }
  }

  /** Reads the session a row was opened for. */
  #readSession(harness: string, session: string): void {
    const feed = new Feed<SessionAnswer>(() => api("sessions", sessionPath(harness, session)), parseSession,
                                         () => this.requestUpdate());
    const key = `${harness}/${session}`;
    this.#sessions.set(key, feed);
    this.#sessionReads.set(key, feed.refresh());
  }

  // -- the address and the entry's memory --------------------------------------------------

  /** The write guard: the list is placed at its own address and still in the panel. */
  #mayWrite(): boolean {
    return mayWrite(location.pathname, this.#placed && this.isConnected);
  }

  /** The one writer of the address: it replaces the entry the list is on, and adds none.
   * Nothing is written while a restore holds its target, or when nothing would change. */
  #write(): void {
    const reader = this.#reader;
    if (reader === null || !this.#mayWrite()) return;
    const search = reader.address();
    if (search !== null && search !== location.search) history.replaceState(history.state, "", LIST_PATH + search);
  }

  /** Writes the entry's memory, the rows opened and the scroll, beside what else it holds;
   * skipped when neither would change. Until the saved scroll has landed or been dropped,
   * the scroll written is the saved one. */
  #remember(): void {
    const reader = this.#reader;
    if (reader === null || !this.#mayWrite()) return;
    const opened = reader.state?.opened ?? this.#remembered;
    const next = withMemory(history.state, { opened, y: this.#scroll.settled ? scrollY : this.#scroll.saved });
    if (next !== null) history.replaceState(next, "", location.href);
  }

  /** The query the list carries to a transcript: the address as it stands. */
  #search(): string {
    return this.#placed ? location.search : "";
  }

  /** At the reads' first stop, once the opened rows then shown have each answered or
   * failed, scrolls once to the saved scroll, cut to the page's height, unless an act of
   * the user's came first. */
  #land(): void {
    const reader = this.#reader;
    if (reader === null || !reader.stopped || this.#landing || this.#scroll.settled) return;
    this.#landing = true;
    const state = reader.state;
    const reads = state === null ? []
      : state.opened.filter((key) => shownRow(state, key) !== null).map((key) => this.#sessionReads.get(key));
    void (async () => {
      await Promise.all(reads);
      await this.#runningFirst;
      await this.updateComplete;
      await new Promise((done) => requestAnimationFrame(done));
      if (this.#reader !== reader) return;
      const y = this.#scroll.land(document.documentElement.scrollHeight - innerHeight);
      if (y !== null) scrollTo(0, y);
    })();
  }

  // -- acts --------------------------------------------------------------------------------

  #toggleRow(row: RowView): void {
    const reader = this.#reader;
    if (reader === null || reader.state === null) return;
    reader.state = toggleOpen(reader.state, row.key);
    this.#sessions.get(row.key)?.close();
    this.#sessions.delete(row.key);
    this.#sessionReads.delete(row.key);
    if (reader.state.opened.includes(row.key)) this.#readSession(row.harness, row.session);
    this.#remember();
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

  /** Takes the filters as the person left them; the reader reads the list again when they
   * make another query. */
  #setFilters(next: Filters): void {
    const reader = this.#reader;
    if (reader === null) return;
    const before = listPath(reader.query());
    reader.setFilters(next);
    if (listPath(reader.query()) !== before && this.#menu === "folder") void this.#folders?.refresh();
    this.requestUpdate();
  }

  #filters(): Filters {
    return this.#reader?.filters ?? NO_FILTERS;
  }

  #type(text: string): void {
    this.#typed = text;
    // The page root counts the list read as out from the keystroke, so a press waits for
    // the list the find draws.
    this.#cancelFind();
    this.#cancelFind = readLater(() => this.#setFilters({ ...this.#filters(), find: this.#typed }),
                                 FIND_WAIT_MS);
    this.requestUpdate();
  }

  #clearFind(): void {
    this.#cancelFind();
    this.#typed = "";
    this.#setFilters({ ...this.#filters(), find: "" });
  }

  #openMenu(key: FilterKey | null): void {
    this.#menu = key;
    this.#folders?.close();
    this.#folders = null;
    this.#narrow = "";
    const reader = this.#reader;
    if (key === "folder" && reader !== null) {
      this.#folders = new Feed<FoldersAnswer>(() => api("sessions", foldersPath(reader.query())), parseFolders,
                                              () => this.requestUpdate());
      void this.#folders.refresh();
    }
    this.requestUpdate();
  }

  #pick(change: Partial<Filters>): void {
    this.#openMenu(null);
    this.#setFilters({ ...this.#filters(), ...change });
  }

  #range(end: "from" | "to", day: string): void {
    const when = this.#filters().when;
    const kept = when.kind === "range" ? when : { kind: "range" as const, from: null, to: null };
    this.#setFilters({ ...this.#filters(), when: whenOf({ ...kept, [end]: day !== "" ? day : null }) });
  }

  // -- drawing -----------------------------------------------------------------------------

  /** What an opened row shows under its lines, in the row's default slot. */
  protected opened(row: RowView): unknown {
    const state = this.#reader?.state ?? null;
    if (state === null) return nothing;
    const view = openedView(row, this.#sessions.get(row.key) ?? null,
                            { today: state.today, roots: state.roots, cacheLink: this.#readsCache() });
    const said = (line: SaidView | null, name: string) => (line === null ? nothing
      : html`<div class="r" data-line=${name}><span class="k">${line.label}</span><span class="f">${
          line.time !== null ? html`<span class="tm">${line.time}</span>` : nothing}${line.text}</span><span></span></div>`);
    const copy = (what: string, text: string, words: string, tone: string) => {
      const key = `${row.key} ${what}`;
      return html`<button type="button" class=${tone} data-action=${`copy-${what}`}
        @click=${() => void this.#copy(key, text)}>${this.#copied === key ? COPIED : words}</button>`;
    };
    const { status, subagents, session, cache, resume, transcript } = view;
    return html`<div class="det" data-part="opened">
      ${status !== null ? html`<p class=${status.state === "waiting" ? "st dim" : "st"} data-line=${status.state}
        role="status">${status.line}</p>` : nothing}
      ${said(view.prompt, "you")}${said(view.reply, "reply")}
      ${this.#liveLines("liveLines" in row ? (row as RunningRowView).liveLines : null)}
      ${subagents !== null ? html`<div class="r" data-line="subagents"><span class="k">${subagents.label}</span><span class="f">
        ${subagents.items.map((sub) => html`<span class="sub" data-line="subagent"><span>${sub.name}</span>${
          sub.facts !== null ? html` <span class="d">· ${sub.facts}</span>` : nothing}${
          sub.last !== null ? html`<br><span class="d">${sub.last}</span>` : nothing}</span>`)}
        ${subagents.notice !== null ? html`<span class="sub d" data-line="subagents-unread">${subagents.notice}</span>` : nothing}
        </span><span></span></div>` : nothing}
      ${session !== null ? html`<div class="r" data-line="session"><span class="k">${session.label}</span><span class="f">${
        session.lead}${session.rest !== null ? html` <span class="d">· ${session.rest}</span>` : nothing}</span><span></span></div>`
        : nothing}
      ${cache !== null ? html`<div class="r" data-line="cache"><span class="k">${cache.label}</span><span class="f">${
        cache.value}${cache.tail !== null ? html` <span class="d">· ${cache.tail}</span>` : nothing}</span>${cache.link !== null
        ? html`<a class="x" href="/m/cache/" data-action="cache" @click=${(event: MouseEvent) => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            this.#remember();
            navigate("cache", "");
          }}>${cache.link}</a>` : html`<span></span>`}</div>` : nothing}
      ${resume !== null ? html`<div class="r" data-line="resume"><span class="k">${resume.label}</span><span class="f"><code>${
        resume.command}</code>${resume.where !== null ? html`<br><span class="d" data-line="where">${resume.where}</span>` : nothing
        }</span>${copy("resume", resume.command, resume.copy, "x")}</div>` : nothing}
      <div class="foot">
        ${transcript !== null ? html`<a href=${`/m/sessions/${transcript.route}${this.#search()}`} data-action="transcript"
          @click=${(event: MouseEvent) => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            this.#remember();
            navigate("sessions", transcript.route + this.#search());
          }}>${transcript.text}</a>` : nothing}
        ${copy("id", view.copyId.id, view.copyId.text, "d")}
      </div>
    </div>`;
  }

  /** turns' lines on an opened running row: `Now`, `Agents` and `Prompt box`, each only
   * with its fact. A busy agent's state is in the text colour. */
  #liveLines(lines: LiveOpened | null) {
    if (lines === null) return nothing;
    const line = (name: string, label: string, body: unknown) =>
      html`<div class="r" data-line=${name}><span class="k">${label}</span><span class="f">${body}</span><span></span></div>`;
    const { now, agents, promptBox } = lines;
    return html`${now !== null ? line("now", LIVE_LABELS.now, now) : nothing}${agents !== null ? line("agents", LIVE_LABELS.agents,
      html`${agents.entries.map((agent) => html`<span class="sub" data-line="agent"><span class="d">${agent.text} ·</span> <span
        class=${agent.busy ? "" : "d"}>${agent.state}</span></span>`)}${agents.notKnown !== null
        ? html`<span class="sub d" data-line="agents-unknown">${agents.notKnown}</span>` : nothing}`) : nothing}${
      promptBox !== null ? line("prompt-box", LIVE_LABELS.promptBox, promptBox) : nothing}`;
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
    const reader = this.#reader;
    const status = reader !== null ? statusLine(reader.list) : null;
    const state = reader?.state ?? null;
    const said = addressLine(this.#dropped, { capped: reader?.capped ?? false });
    const address = html`${this.line !== null && this.line !== ""
      ? html`<p class="status" data-line="unknown" role="status">${this.line}</p>` : nothing}${said !== null
      ? html`<p class="status dim" data-line="address" role="status">${said}</p>` : nothing}`;
    // turns' page is linked in every state of the list: beside the count, or alone where no
    // filter line is drawn.
    const turnsTitle = this.#turnsTitle();
    const alone = turnsTitle !== null ? html`<div class="filt" data-line="turns"><span class="fs"></span><span class="n">${
      this.#turnsLink(turnsTitle)}</span></div>` : nothing;
    if (status?.state === "gone") return html`${address}${alone}${this.#status(status)}`;
    if (state === null) return html`${address}${alone}${status !== null ? this.#status(status) : nothing}`;
    const desk = !gateway();
    const clocks = this.#readsCache() ? this.#clocks : null;
    const now = clocks !== null ? serverNow(clocks, Date.now() / 1000) : null;
    this.#waitForCold(now);
    const view = listView(state, { launcher: installed("launcher"), running: this.#running, presses: this.#presses, desk,
                                   readsTurns: this.#readsTurns(), turnsRead: this.#turnsRead,
                                   cache: clocks !== null && now !== null ? { clocks, now } : null });
    const filters = filterLine(state, this.#filters());
    return html`${address}
      ${filters !== null ? html`${this.#find()}${this.#filt(filters, view.count, turnsTitle)}` : alone}
      ${status !== null ? this.#status(status) : nothing}
      ${reader?.moreStatus != null ? this.#status(reader.moreStatus) : nothing}
      ${view.empty !== null ? this.#empty(view.empty)
        : view.groups.length || view.running !== null ? html`<div class="grps" data-part="groups">
            ${view.running !== null ? this.#runningGroup(view.running, desk) : nothing}
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

  #filt(line: FilterLine, count: string | null, turnsLink: string | null) {
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
      ${count !== null || turnsLink !== null ? html`<span class="n">${turnsLink !== null ? this.#turnsLink(turnsLink)
        : nothing}${turnsLink !== null && count !== null ? html`<span class="sep">·</span>` : nothing}${count !== null
        ? html`<span data-line="count">${count}</span>` : nothing}</span>` : nothing}
    </div>`;
  }

  /** The link to turns' page, worded with its title; a plain click shows it in place. */
  #turnsLink(title: string) {
    return html`<a class="x" href="/m/turns/" data-action="turns" @click=${(event: MouseEvent) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate("turns", "");
    }}>${title}</a>`;
  }

  /** turns' page's title, where the list links it: turns' page is there, at the desk (turns'
   * routes are not the gateway's). */
  #turnsTitle(): string | null {
    if (gateway()) return null;
    return components("turns").find((one) => one.place === "page")?.title ?? null;
  }

  #item(item: MenuItem<unknown>, pick: () => void) {
    return html`<button type="button" class="mi" role="menuitemradio" aria-checked=${item.checked ? "true" : "false"}
      @click=${pick}><span aria-hidden="true">${item.checked ? "✓" : ""}</span><span class="l">${item.label}</span><span
      class="d">${item.dim ?? ""}</span><span class="t">${item.tail ?? ""}</span></button>`;
  }

  #menuOf(key: FilterKey) {
    const state = this.#reader?.state ?? null;
    if (state === null) return nothing;
    if (key === "harness") {
      const [all, ...rest] = harnessMenu(state, this.#filters());
      return html`<div class="menu" role="menu" data-menu="harness">
        ${all !== undefined ? this.#item(all, () => this.#pick({ harness: null })) : nothing}
        ${rest.length ? html`<hr>` : nothing}
        ${rest.map((item) => this.#item(item, () => this.#pick({ harness: item.value })))}</div>`;
    }
    if (key === "when") {
      const when = whenOf(this.#filters().when);
      const day = (end: "from" | "to"): string => (when.kind === "range" ? when[end] ?? "" : "");
      return html`<div class="menu" role="menu" data-menu="when">
        ${whenMenu(this.#filters()).map((item) => this.#item(item, () => this.#pick({ when: item.value as When })))}
        <hr>
        <div class="range">
          <label>${RANGE_FROM}<input type="date" name="from" .value=${live(day("from"))}
            @change=${(event: Event) => this.#range("from", (event.target as HTMLInputElement).value)}></label>
          <label>${RANGE_TO}<input type="date" name="to" .value=${live(day("to"))}
            @change=${(event: Event) => this.#range("to", (event.target as HTMLInputElement).value)}></label>
        </div></div>`;
    }
    const feed = this.#folders;
    const menu = folderMenu(feed?.value ?? null, this.#filters(), this.#narrow, state.today, LOCAL, state.home);
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
        ${group.more !== null ? this.#line(group.more, "more", () => this.#reader?.showMore(group.key)) : nothing}
        ${program !== null ? html`${this.#line(program, "program", () => this.#reader?.toggleProgram(group.key), "one d", program.open)}
          ${repeat(program.rows, (row) => row.key, (row) => this.#row(row))}
          ${program.more !== null ? this.#line(program.more, "program-more", () => this.#reader?.showMoreProgram(group.key)) : nothing}`
          : nothing}
        ${group.earlier !== null ? this.#line(group.earlier, "earlier", () => this.#reader?.showMore(group.key), "one") : nothing}
      </div>
    </section>`;
  }

  /** The launches running now: a label and their rows, or a line in their place. */
  #runningGroup(group: RunningGroupView, desk: boolean) {
    return html`<section class="grp" data-group="running">
      <div class="gk" data-line="label">${group.label}</div>
      <div class="gl">
        ${repeat(group.rows, (row) => row.key, (row) => this.#row(row, row))}
        ${group.line !== null ? html`<p class="one d" data-line="running-unread" role="status">${group.line}${
          group.reason !== null && desk ? html`: <span data-line="reason">${group.reason}</span>` : nothing}</p>` : nothing}
      </div>
    </section>`;
  }

  #row(row: RowView, running: RunningRowView | null = null) {
    const toggle = (): void => this.#toggleRow(row);
    const desk = !gateway();
    const keep = running !== null && running.keepWarm !== null && desk ? running : null;
    return html`<moxi-ui-row ?toggles=${row.act !== null || row.lines === 1} data-key=${row.key}
        data-request=${ifDefined(running?.request)} name=${row.name} where=${ifDefined(row.where ?? undefined)}
        fact=${ifDefined(row.fact ?? undefined)} ?fact-heavy=${row.factHeavy} lead=${ifDefined(row.lead ?? undefined)}
        ?lead-heavy=${row.leadHeavy} lead-more=${ifDefined(running?.leadMore ?? undefined)} time=${ifDefined(row.time ?? undefined)} said-by=${ifDefined(row.saidBy ?? undefined)}
        rest=${ifDefined(row.rest ?? undefined)} note=${ifDefined(running?.note ?? undefined)} ?open=${row.open}
        @moxi-row-toggle=${toggle}>
      ${keep !== null ? html`<button type="button" slot="acts" class="do warm" data-action="keep-warm"
        title=${keep.keepWarm as string} aria-description=${keep.keepWarm as string} ?disabled=${keep.pressing}
        @click=${() => void this.#press(keep.request)}>${KEEP_WARM}</button>` : nothing}
      ${row.act !== null && desk ? html`<button type="button" slot="acts" class="do" data-action="open"
        aria-expanded=${row.open ? "true" : "false"} @click=${toggle}>${row.act}</button>` : nothing}
      ${row.open ? this.opened(row) : nothing}
    </moxi-ui-row>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, SessionsList);

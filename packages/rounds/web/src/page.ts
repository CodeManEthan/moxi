// <moxi-rounds-page route="...">: rounds' page, every open round as one line of its steps
// in columns aligned across rounds. Top to bottom:
//
// - The waiting line: what waits on him, counted, and `Go through in order`, which walks
//   every item in the board's order.
// - `Last acted on`: the ten builds with the newest recorded act, each a card that opens
//   its build's current step under it, with its stage's steps as tabs.
// - The board: filter chips and a project picker, the column labels, then the groups,
//   each round a rail of its steps (`<moxi-ui-rail>`, from ui's client script) with a
//   sub-line per other build in flight and its folds; then the closed rounds' fold.
// - The foot: when it was read, the parked questions, and what an address named that is
//   no longer open.
//
// A marker opens its step's record in a popover; one that waits on him opens his items
// there. One thing is open at a time and the page writes it into its address, in place of
// the entry it is on, so Back leaves the page. It only reads: nothing on it acts on a
// round. Every word and every rule of what to draw comes from `model.ts`, and goes into
// the page as text. At 720 px and under it is the phone layout: rounds as buttons, each
// opening a sheet with one vertical rail.
//
// It reads `/api/rounds`, then follows the topic `rounds`, and reads the route again when
// its tab is shown again. A record is read from `/api/record` when it opens, and again
// when its round's `rev` changes while it is open. With turns installed it reads turns'
// sessions, so a builder whose worktree is one live session's folder links to its row.

import { css, html, LitElement, noChange, nothing } from "lit";
import type { TemplateResult } from "lit";
import { Directive, directive, PartType } from "lit/directive.js";
import type { ElementPart, PartInfo } from "lit/directive.js";
import { api, installed, navigate, setAddress, subscribe } from "/m/ui/client.js";

import { Feed, parseBoard, readRecord } from "./feed.ts";
import type { Board, Cell, Column, Line, Recent, RecordRead, Round } from "./feed.ts";
import {
  addressRoute, ageWord, ALL_PROJECTS, ANSWER_LEAD, arrange, AT_DESK, CANT_READ, cardView, cellAt, chips, CLOSED_FOLD,
  closedFoldText, closedWords, EMPTY_HEAD, EMPTY_TEXT, foldKey, foldsOf, footWords, holdOf, isManyProjects, isOpenNow,
  isShown, isUnreadableShown, itemAddress, itemMarker, itemsOf, itemView, KEY_COLUMNS, KEY_HERE, KEY_STATES, KEY_TITLE,
  layoutOf, LOCAL, linesShown, markAttributes, markKey, moreLabel, noLongerOpen, NOTHING_WAITS, OPEN_ROUNDS,
  parseAddress, parseMarkKey, PASSAGE_LINES, phoneWaiting, PHONE_PX, phraseShown, placePopover, POP_WIDE, POP_WIDTH,
  popoverWidth, revealBy,
  READ_PASSAGE, READING, READING_AFTER_MS, recordQuery, revealing, sessionAt, sheetMarks, sheetQuestions, SHOW_LINE,
  slugOf, stageOf, stateWords, stepHead, stepName, stepSections, STRIP_CAPTION, STRIP_EMPTY, STRIP_HEAD, stripCard,
  stripStep, stripTabs, UNREADABLE_LEAD, unreadableWords, WAITING_LEAD, waitingCounts, walkFoot, WALK_BUTTON,
  walkStep,
} from "./model.ts";
import type { Address, Context, Filter, GroupView, Held, Item, Piece, Section } from "./model.ts";

const TAG = "moxi-rounds-page";
const MODULE = "rounds";
/** How often the ages are drawn again, in ms. */
const REDRAW_MS = 60_000;

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

const DONE: Cell = { state: "done", word: null, count: null, settled: true, here: false, step_state: "done", by: null,
                     who: null };

/** Where an open popover hangs: a marker on the board, a strip card, a name, the waiting
 * line or the Key button. */
type Anchor = { kind: "board" } | { kind: "strip"; at: number };

export class RoundsPage extends LitElement {
  static properties = { route: { type: String } };

  static styles = css`
    :host { display: block; font: var(--moxi-font-size, 13px) var(--moxi-font-sans); color: var(--moxi-text);
            --moxi-rail-cell: 56px; --moxi-rail-label: 132px; position: relative; }
    button { font: inherit; color: inherit; }
    .link { background: none; border: 0; padding: 0; cursor: pointer; text-align: left; }
    .link:hover { text-decoration: underline; }
    :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
    .dim { color: var(--moxi-text-dim); }
    .mono { font-family: var(--moxi-font-mono); font-size: 12px; }
    .strong { font-weight: 600; }
    .t-done { color: var(--moxi-state-done); }
    .t-failed { color: var(--moxi-state-failed); font-weight: 600; }
    .t-dim { color: var(--moxi-text-dim); }
    .t-strong { font-weight: 600; }
    .lead { margin: 24px 0; font-size: 15px; }
    .stale { opacity: 0.55; }

    .waiting { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-radius: 8px;
               background: var(--moxi-state-yours-soft); margin-bottom: 20px; }
    .waiting.none { background: none; padding-left: 0; }
    .waiting .counts { flex: 1; }
    .waiting .n { font-weight: 600; }
    .walk { border: 1px solid var(--moxi-border-strong); background: var(--moxi-bg-raised); border-radius: 6px;
            padding: 6px 12px; cursor: pointer; }

    h2 { font-size: 15px; font-weight: 600; margin: 0; display: inline; }
    .strip-head { margin-bottom: 8px; }
    .strip { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; margin-bottom: 24px; }
    .card { display: block; text-align: left; border: 1px solid var(--moxi-border); border-radius: 8px;
            background: var(--moxi-bg-raised); padding: 7px 10px; cursor: pointer; min-width: 0; }
    .card[aria-expanded="true"] { border-color: var(--moxi-accent); box-shadow: 0 0 0 1px var(--moxi-accent); }
    .card .top, .card .bottom { display: flex; gap: 8px; align-items: center; white-space: nowrap; }
    .card .top .slug { overflow: hidden; text-overflow: ellipsis; flex: 1; }
    .card .bottom { margin-top: 3px; }
    .card .act { overflow: hidden; text-overflow: ellipsis; color: var(--moxi-text-dim); }

    .head { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; flex-wrap: wrap; }
    .head .count { color: var(--moxi-text-dim); margin-right: 14px; }
    .chip { background: none; border: 0; border-radius: 6px; padding: 4px 8px; cursor: pointer; color: var(--moxi-text-dim); }
    .chip[aria-pressed="true"] { background: var(--moxi-bg-sunken); color: var(--moxi-text); }
    .head .grow { flex: 1; }
    select, .keybtn { font: inherit; color: inherit; background: var(--moxi-bg-raised);
                      border: 1px solid var(--moxi-border); border-radius: 6px; padding: 4px 8px; }
    .keybtn { cursor: pointer; }

    .board { overflow-x: auto; }
    .inner { min-width: max-content; }
    .labels, .line { display: flex; align-items: center; }
    .labels { position: sticky; top: 0; z-index: 2; background: var(--moxi-bg); font-size: 11px;
              color: var(--moxi-text-dim); border-bottom: 1px solid var(--moxi-border); height: 26px; }
    .labels .col { width: var(--moxi-rail-cell); text-align: center; flex: none; white-space: nowrap; }
    .labels .stagecol { width: var(--moxi-rail-label); flex: none; padding-left: 4px; }
    .name { width: 180px; flex: none; position: sticky; left: 0; z-index: 1; background: inherit;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-left: 4px; }
    .labels .name { background: var(--moxi-bg); }
    .phrase { flex: 1; min-width: 120px; contain: inline-size; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
              padding-left: 12px; }
    .age { width: 44px; flex: none; text-align: right; color: var(--moxi-text-dim); padding-right: 4px; }
    .group { font-weight: 600; margin: 18px 0 4px; }
    .group .dim { font-weight: 400; margin-left: 6px; }
    .line { height: 34px; background: var(--moxi-bg); --moxi-rail-bg: var(--moxi-bg); }
    .line:hover { background: var(--moxi-bg-hover); --moxi-rail-bg: var(--moxi-bg-hover); }
    .line.open { background: var(--moxi-accent-soft); --moxi-rail-bg: var(--moxi-accent-soft); }
    .line .name button { font-size: 15px; }
    .line .project { color: var(--moxi-text-dim); margin-left: 6px; }
    moxi-ui-rail { flex: none; }
    .label { display: flex; gap: 6px; align-items: center; white-space: nowrap; overflow: hidden; padding: 0 4px;
             font-size: 13px; }
    .label .stage { color: var(--moxi-text-dim); }
    .label .branch { overflow: hidden; text-overflow: ellipsis; }
    .more { background: none; border: 0; padding: 0 2px; cursor: pointer; color: var(--moxi-text-dim); }
    .w-yours { font-weight: 600; }
    .w-bad { font-weight: 600; color: var(--moxi-state-failed); }
    .fold { display: flex; align-items: center; gap: 8px; height: 26px; padding-left: 184px; }
    .fold button { background: none; border: 0; cursor: pointer; padding: 0; }
    .unreadable .phrase { white-space: normal; }
    .closed .name { color: var(--moxi-text-dim); }
    .closedfold { margin-top: 18px; padding-left: 0; }
    .emptyhead { font-size: 18px; margin: 24px 0 6px; }

    .foot { margin-top: 24px; color: var(--moxi-text-dim); display: flex; flex-direction: column; gap: 2px; }

    .pop { position: fixed; z-index: 10; box-sizing: border-box; width: ${POP_WIDTH}px; top: -9999px; left: 0;
           max-height: min(70vh, 640px); overflow: auto;
           background: var(--moxi-bg-raised); border: 1px solid var(--moxi-border-strong); border-radius: 10px;
           box-shadow: 0 10px 30px var(--moxi-border-strong); }
    .pop.wide { width: ${POP_WIDE}px; }
    .pop .phead { position: sticky; top: 0; background: var(--moxi-bg-raised); display: flex; gap: 10px;
                  align-items: flex-start; padding: 12px 14px 10px; border-bottom: 1px solid var(--moxi-border); }
    .pop .phead .titles { flex: 1; }
    .pop .title { font-size: 15px; font-weight: 600; }
    .pop .x { background: none; border: 0; cursor: pointer; font-size: 18px; line-height: 1; padding: 0 4px; }
    .pop .body { padding: 10px 14px 12px; display: flex; flex-direction: column; gap: 12px; }
    .pop .tabs { --moxi-rail-cell: 80px; }
    .sec h3 { font-size: 13px; font-weight: 600; margin: 0 0 4px; }
    .sec .row { margin: 2px 0; overflow-wrap: anywhere; }
    .sec .foldbtn, .linkish { background: none; border: 0; padding: 0; cursor: pointer; text-decoration: underline;
                              color: var(--moxi-text-dim); }
    .well { font-family: var(--moxi-font-mono); font-size: 12px; background: var(--moxi-bg-sunken);
            border-radius: 6px; padding: 7px 10px; margin-top: 4px; overflow-wrap: anywhere; }
    .question { font-size: 15px; font-weight: 600; }
    .option { display: flex; gap: 12px; border: 1px solid var(--moxi-border); border-radius: 6px; padding: 8px 10px;
              user-select: none; }
    .option .k { color: var(--moxi-text-dim); }
    .passage { white-space: pre-wrap; border-left: 2px solid var(--moxi-border); padding-left: 10px; }
    .passage.cut { display: -webkit-box; -webkit-line-clamp: ${PASSAGE_LINES}; -webkit-box-orient: vertical;
                   overflow: hidden; }
    .pfoot { border-top: 1px solid var(--moxi-border); padding: 10px 14px; display: flex; gap: 10px;
             align-items: center; flex-wrap: wrap; }
    .pfoot .grow { flex: 1; }
    .pfoot .answer { flex-basis: 100%; }
    .walkbtn { background: none; border: 1px solid var(--moxi-border); border-radius: 4px; cursor: pointer;
               padding: 0 6px; }
    .key-row { display: flex; gap: 10px; align-items: center; }
    .key-row moxi-ui-rail { --moxi-rail-cell: 32px; }

    .phone .round { display: block; width: 100%; text-align: left; background: none; border: 0;
                    border-bottom: 1px solid var(--moxi-border); padding: 10px 2px; cursor: pointer; }
    .phone .round .top { display: flex; align-items: center; gap: 8px; }
    .phone .round .slug { font-weight: 600; font-size: 15px; display: block; margin-bottom: 4px; }
    .phone .round .grow { flex: 1; }
    .phone .round .phrase { display: block; padding: 4px 0 0; white-space: normal; min-width: 0; contain: none; }
    .phone .recent { display: flex; gap: 8px; align-items: center; padding: 6px 2px; white-space: nowrap; }
    .phone .recent .act { overflow: hidden; text-overflow: ellipsis; color: var(--moxi-text-dim); }
    .sheet { position: fixed; left: 0; right: 0; bottom: 0; max-height: 78vh; overflow: auto; z-index: 10;
             background: var(--moxi-bg-raised); border-top: 1px solid var(--moxi-border-strong);
             border-radius: 12px 12px 0 0; padding: 14px 16px 24px;
             box-shadow: 0 -10px 30px var(--moxi-border-strong); }
    .sheet .sheetslug { font-size: 15px; }
    .sheet .x { float: right; background: none; border: 0; font-size: 18px; cursor: pointer; }
  `;

  route = "";

  #feed: Feed<Board> | null = null;
  #stop: (() => void) | null = null;
  #stopSessions: (() => void) | null = null;
  #sessions: { key: string; cwd: string | null }[] = [];
  #address: Address | null = null;
  #anchor: Anchor = { kind: "board" };
  #record: { route: string; read: RecordRead | null; rev: string | null } | null = null;
  #sequence: Item[] = [];
  #held: Held | null = null;
  #filter: Filter = "all";
  #project: string | null = null;
  #unfolded = new Set<string>();
  #shown = new Set<string>();
  #notes: string[] = [];
  #slow = false;
  #phone = false;
  #media: MediaQueryList | null = null;
  #timers: number[] = [];
  #placed = false;
  /** Whether the popover just opened, so its marker may be scrolled into room once. */
  #reveal = false;

  connectedCallback(): void {
    super.connectedCallback();
    this.#media = window.matchMedia(`(max-width: ${PHONE_PX}px)`);
    this.#phone = this.#media.matches;
    this.#media.addEventListener("change", this.#onMedia);
    const feed = new Feed<Board>(() => api(MODULE, "api/rounds"), parseBoard, () => this.#onValue(feed));
    this.#feed = feed;
    void feed.refresh().then(() => {
      if (this.#feed === feed) this.#stop = subscribe(MODULE, "rounds", (data) => feed.topic(data));
    });
    if (installed("turns")) void this.#readSessions();
    this.#timers.push(window.setTimeout(() => { this.#slow = true; this.requestUpdate(); }, READING_AFTER_MS),
                      window.setInterval(() => this.requestUpdate(), REDRAW_MS));
    document.addEventListener("visibilitychange", this.#onVisible);
    window.addEventListener("keydown", this.#onKey);
    window.addEventListener("resize", this.#onMove);
    window.addEventListener("scroll", this.#onMove, true);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#feed = null;
    this.#stop?.();
    this.#stopSessions?.();
    this.#media?.removeEventListener("change", this.#onMedia);
    this.#timers.forEach((one) => { window.clearTimeout(one); window.clearInterval(one); });
    document.removeEventListener("visibilitychange", this.#onVisible);
    window.removeEventListener("keydown", this.#onKey);
    window.removeEventListener("resize", this.#onMove);
    window.removeEventListener("scroll", this.#onMove, true);
  }

  protected willUpdate(changed: Map<PropertyKey, unknown>): void {
    if (changed.has("route")) {
      this.#address = parseAddress(this.route);
      this.#anchor = { kind: "board" };
      if (this.#feed?.value) this.#openFromAddress(this.#feed.value);
    }
  }

  protected updated(): void {
    if (!this.#placed) this.#place();
  }

  #onMedia = (): void => {
    this.#phone = this.#media?.matches ?? false;
    this.requestUpdate();
  };

  #onVisible = (): void => {
    if (document.visibilityState === "visible") void this.#feed?.refresh();
  };

  #onMove = (): void => {
    if (this.#address !== null) this.#place();
  };

  async #readSessions(): Promise<void> {
    const take = (data: unknown) => {
      const rows = (data as { sessions?: unknown } | null)?.sessions;
      this.#sessions = Array.isArray(rows)
        ? rows.filter((one) => typeof one?.key === "string")
            .map((one) => ({ key: one.key as string, cwd: typeof one.cwd === "string" ? one.cwd : null }))
        : [];
      this.requestUpdate();
    };
    const answer = await api("turns", "api/sessions");
    if (answer.status === 200) take(answer.data);
    if (this.isConnected) this.#stopSessions = subscribe("turns", "sessions", take);
  }

  // --- what is open ------------------------------------------------------------------------

  #onValue(feed: Feed<Board>): void {
    const value = feed.value;
    if (value !== null) {
      this.#openFromAddress(value);
      const round = this.#record && this.#address ? this.#roundOf(this.#address, value) : null;
      if (round && this.#record && this.#record.rev !== null && round.rev !== this.#record.rev) {
        void this.#readRecord(this.#address!);
      }
    }
    this.requestUpdate();
  }

  /** The open round an address is on, if any. */
  #roundOf(address: Address, value: Board): Round | null {
    const name = address.kind === "round" || address.kind === "step" ? address.round
      : address.kind === "walk" ? address.item?.holder ?? null
      : address.kind === "key" ? null : address.holder;
    return value.rounds.find((one) => one.name === name) ?? null;
  }

  /** Opens what the address names once the board is read; an address that names
   * nothing open now opens nothing, and the foot says so. */
  #openFromAddress(value: Board): void {
    const address = this.#address;
    if (address === null) return;
    if (!isOpenNow(address, value)) {
      this.#notes = [noLongerOpen(address)];
      this.#close({ focus: false });
      return;
    }
    if (address.kind === "walk") {
      this.#sequence = itemsOf(value, this.#order(value));
      if (address.item === null) {
        this.#address = { kind: "walk", item: itemAddress(this.#sequence[0]) as Extract<Address, { id: string }> };
        setAddress(MODULE, addressRoute(this.#address));
      }
    }
    if (this.#held === null) this.#held = holdOf(arrange(value));
    const route = this.#recordRoute(this.#address!);
    if (route !== null && this.#record?.route !== route) void this.#readRecord(this.#address!);
    this.#placed = false;
    this.#reveal = true;
  }

  #recordRoute(address: Address): string | null {
    return address.kind === "walk" ? (address.item ? recordQuery(address.item) : null) : recordQuery(address);
  }

  async #readRecord(address: Address): Promise<void> {
    const route = this.#recordRoute(address);
    if (route === null) return;
    const rev = this.#feed?.value ? this.#roundOf(address, this.#feed.value)?.rev ?? null : null;
    this.#record = { route, read: this.#record?.route === route ? this.#record.read : null, rev };
    const read = await readRecord(() => api(MODULE, route));
    if (this.#record?.route !== route) return;
    if (read.kind === "gone") {
      this.#notes = [noLongerOpen(address)];
      this.#close({ focus: false });
      return;
    }
    this.#record = { route, read, rev };
    this.#placed = false;
    this.requestUpdate();
  }

  /** Opens `address`; with `sequence`, the items the popover's arrows move through. */
  #open(address: Address, { anchor = { kind: "board" } as Anchor, sequence = [] as Item[] } = {}): void {
    const value = this.#feed?.value;
    if (!value) return;
    this.#notes = [];
    this.#address = address;
    this.#anchor = anchor;
    if (address.kind !== "walk") this.#sequence = sequence;
    this.#shown.clear();
    if (this.#held === null) this.#held = holdOf(arrange(value));
    setAddress(MODULE, addressRoute(address));
    this.#openFromAddress(value);
    this.requestUpdate();
  }

  #close({ focus = true } = {}): void {
    const was = this.#address;
    this.#address = null;
    this.#record = null;
    this.#sequence = [];
    this.#held = null;
    this.#anchor = { kind: "board" };
    setAddress(MODULE, "");
    this.requestUpdate();
    if (focus && was) void this.updateComplete.then(() => (this.#anchorOf(was) as HTMLElement | null)?.focus());
  }

  #onKey = (event: KeyboardEvent): void => {
    if (this.#address === null || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Escape") {
      event.preventDefault();
      this.#close();
      return;
    }
    const target = event.composedPath()[0] as HTMLElement | undefined;
    if (target && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
    if (this.#sequence.length > 1) {
      if (event.key === "ArrowRight" || event.key === "j") this.#walk(1);
      else if (event.key === "ArrowLeft" || event.key === "k") this.#walk(-1);
    }
  };

  #walk(step: number): void {
    const at = this.#sequenceAt();
    if (at === -1) return;
    const next = walkStep(at, step, this.#sequence.length);
    if (next === at) return;
    const item = itemAddress(this.#sequence[next]) as Extract<Address, { id: string }>;
    this.#open(this.#address?.kind === "walk" ? { kind: "walk", item } : item, { sequence: this.#sequence });
  }

  #sequenceAt(): number {
    const address = this.#address;
    const item = address?.kind === "walk" ? address.item : address && "id" in address ? address : null;
    if (!item) return -1;
    return this.#sequence.findIndex((one) => one.kind === item.kind && one.holder === item.holder && one.id === item.id);
  }

  /** The rounds' names in the order the board draws them. */
  #order(value: Board): string[] {
    return arrange(value, this.#held).flatMap((view) => view.rounds.map((one) => one.name));
  }

  #onMark(event: Event, round: Round): void {
    const key = (event as CustomEvent<{ key: string | null }>).detail?.key;
    if (!key) return;
    const { column, stage } = parseMarkKey(key);
    const value = this.#feed!.value!;
    const line = round.lines[Number((event.target as HTMLElement).dataset.line ?? 0)];
    const cell = line ? cellAt(line, value.columns, column) : null;
    if (cell?.state === "yours" && stage === null) {
      const items = itemsOf(value).filter((one) => one.round === round.name && itemMarker(one)?.key === key);
      if (items.length) {
        this.#toggle(itemAddress(items[0]), { sequence: items });
        return;
      }
    }
    this.#toggle({ kind: "step", round: round.name, column, stage });
  }

  #toggle(address: Address, options: { anchor?: Anchor; sequence?: Item[] } = {}): void {
    if (this.#address && addressRoute(this.#address) === addressRoute(address)) this.#close();
    else this.#open(address, options);
  }

  // --- placing the popover -----------------------------------------------------------------

  /** The element an open address hangs from. */
  #anchorOf(address: Address): Element | null {
    const root = this.renderRoot as ShadowRoot;
    if (this.#anchor.kind === "strip") return root.querySelector(`[data-card="${this.#anchor.at}"]`);
    const value = this.#feed?.value;
    if (!value) return null;
    const esc = (text: string) => CSS.escape(text);
    if (address.kind === "key") return root.querySelector(".keybtn");
    if (address.kind === "round") return root.querySelector(`[data-name="${esc(address.round)}"]`);
    const item = address.kind === "walk" ? address.item : address.kind === "step" ? null : address;
    if (item) {
      const found = this.#sequence.find((one) => one.kind === item.kind && one.holder === item.holder && one.id === item.id)
        ?? itemsOf(value).find((one) => one.kind === item.kind && one.holder === item.holder && one.id === item.id);
      const marker = found ? itemMarker(found) : null;
      if (!marker) return root.querySelector(".waiting");
      // A round whose flow has no such step (a ruling to read back with no docket) hangs
      // the popover under its line, from the round's name.
      return root.querySelector(`moxi-ui-rail[data-round="${esc(marker.round)}"][data-line="0"] `
                                + `moxi-ui-mark[key="${esc(marker.key)}"]`)
        ?? root.querySelector(`.line [data-name="${esc(marker.round)}"]`);
    }
    if (address.kind !== "step") return null;
    const round = value.rounds.find((one) => one.name === address.round);
    if (!round) return null;
    const column = value.columns.find((one) => one.id === address.column);
    const at = round.lines.findIndex((line) => column?.part !== "stage" ? line.kind === "round" : line.stage === address.stage);
    const key = markKey(address.column, column?.part === "stage" ? address.stage : null);
    return root.querySelector(`moxi-ui-rail[data-round="${esc(round.name)}"][data-line="${Math.max(at, 0)}"] `
                              + `moxi-ui-mark[key="${esc(key)}"]`);
  }

  #place(): void {
    const root = this.renderRoot as ShadowRoot;
    const pop = root.querySelector<HTMLElement>(".pop");
    if (!pop || this.#address === null) return;
    const anchor = this.#anchorOf(this.#address);
    if (!anchor) return;
    const box = (rect: DOMRect) => ({ top: rect.top, left: rect.left, width: rect.width, height: rect.height });
    let marker = box(anchor.getBoundingClientRect());
    const row = anchor.closest(".line, .waiting, .card, .head");
    if (this.#reveal && row && this.#record?.read !== null) {
      // Once the record is in and the popover has its height: scroll a marker or a card
      // near the window's foot up, so the popover opens under it rather than beside it.
      this.#reveal = false;
      const by = revealBy(marker, pop.offsetHeight, window.innerHeight);
      if (by !== 0) {
        window.scrollBy(0, by);
        marker = box(anchor.getBoundingClientRect());
      }
    }
    const line = row ? box(row.getBoundingClientRect()) : marker;
    const at = placePopover(marker, line, { width: pop.offsetWidth, height: pop.offsetHeight },
                            { width: window.innerWidth, height: window.innerHeight });
    pop.style.top = `${at.top}px`;
    pop.style.left = `${at.left}px`;
    this.#placed = true;
  }

  // --- drawing -----------------------------------------------------------------------------

  render(): TemplateResult | typeof nothing {
    const feed = this.#feed;
    if (!feed || feed.gone) return nothing;
    const value = feed.value;
    if (value === null) {
      if (feed.failure) return html`<p class="lead">${CANT_READ}</p><p class="dim">${feed.failure.why}</p>`;
      return this.#slow ? html`<p class="dim">${READING}</p>` : nothing;
    }
    if (value.state === "error") {
      return html`<p class="lead">${CANT_READ}</p><p class="dim">${value.detail ?? ""}</p>${this.#foot(value)}`;
    }
    return this.#phone ? this.#phoneView(value) : this.#desk(value);
  }

  #ctx(value: Board): Context {
    return {
      today: value.today, home: value.home, now: this.#feed?.now() ?? null, clock: LOCAL,
      sessionAt: installed("turns") ? (worktree) => sessionAt(worktree, this.#sessions, value.home) : undefined,
      kindOf: (round, id) => {
        const found = itemsOf(value).find((one) => one.holder === round && one.id === id);
        return found ? found.kind : null;
      },
    };
  }

  #desk(value: Board): TemplateResult {
    const stale = this.#feed?.failure ? "stale" : "";
    return html`
      <div class=${stale}>
        ${this.#waitingLine(value)}
        ${this.#strip(value)}
        ${value.rounds.length + value.unreadable.length === 0 ? this.#empty(value) : this.#board(value)}
      </div>
      ${this.#foot(value)}
      ${this.#address ? this.#popover(value) : nothing}`;
  }

  #waitingLine(value: Board): TemplateResult {
    const counts = waitingCounts(value);
    if (counts.length === 0) {
      return html`<div class="waiting none">${this.#oneMark(DONE)}<span>${NOTHING_WAITS}</span></div>`;
    }
    const yours: Cell = { ...DONE, state: "yours", settled: false };
    return html`<div class="waiting">
      ${this.#oneMark(yours)}<span class="strong">${WAITING_LEAD}</span>
      <span class="counts">${counts.map((one, at) => html`${at ? ", " : ""}<span class="n">${one.n}</span>
        <span class="dim">${one.words}</span>`)}</span>
      <button type="button" class="walk" @click=${() => this.#open({ kind: "walk", item: null })}>${WALK_BUTTON}</button>
    </div>`;
  }

  #oneMark(cell: Cell): TemplateResult {
    return html`<moxi-ui-rail size="small"><moxi-ui-mark ${attributes({ state: cell.state, quiet: "",
      label: stateWords(cell) })}></moxi-ui-mark></moxi-ui-rail>`;
  }

  #smallRail(cells: readonly (Cell | null)[], label: string): TemplateResult {
    return html`<moxi-ui-rail size="small" aria-label=${label}>${cells.map((cell) => html`<moxi-ui-mark
      ${attributes(cell ? { state: cell.state, settled: cell.settled ? "" : null, quiet: "", label: stateWords(cell) }
                        : { state: null, settled: null, quiet: "", label: null })}></moxi-ui-mark>`)}</moxi-ui-rail>`;
  }

  #strip(value: Board): TemplateResult {
    const now = this.#feed?.now() ?? null;
    const open = this.#anchor.kind === "strip" && this.#address ? this.#anchor.at : -1;
    return html`<div class="strip-head"><h2>${STRIP_HEAD}</h2> <span class="dim">${STRIP_CAPTION}</span></div>
      ${value.recent.length === 0 ? html`<p class="dim">${STRIP_EMPTY}</p>` : html`<div class="strip">
        ${value.recent.map((entry, at) => {
          const card = stripCard(entry, now);
          return html`<button type="button" class="card" data-card=${at} aria-expanded=${open === at ? "true" : "false"}
              aria-label=${card.label} @click=${() => this.#openStrip(entry, at)}>
            <span class="top"><span class="slug"><span class="strong">${card.slug}</span>
              <span class="dim">${card.stage}</span></span><span class="dim">${card.age}</span></span>
            <span class="bottom">${this.#smallRail(entry.cells, card.label)}<span class="act">${card.act}</span></span>
          </button>`;
        })}</div>`}`;
  }

  #openStrip(entry: Recent, at: number): void {
    const step = stripStep(entry);
    if (step === null) return;
    const address: Address = { kind: "step", round: entry.round, column: step, stage: entry.stage };
    if (this.#anchor.kind === "strip" && this.#anchor.at === at && this.#address) this.#close();
    else this.#open(address, { anchor: { kind: "strip", at } });
  }

  #empty(value: Board): TemplateResult {
    return html`<p class="emptyhead">${EMPTY_HEAD}</p>
      <p class="dim">${EMPTY_TEXT}</p>${this.#closedFold(value)}`;
  }

  #board(value: Board): TemplateResult {
    const layout = layoutOf(value.columns);
    const groups = arrange(value, this.#held);
    const many = isManyProjects(value);
    return html`
      <div class="head">
        <h2>${OPEN_ROUNDS}</h2><span class="count">${value.rounds.length + value.unreadable.length}</span>
        ${chips(value).map((chip) => html`<button type="button" class="chip" aria-pressed=${this.#filter === chip.filter ? "true" : "false"}
            @click=${() => { this.#filter = chip.filter; this.requestUpdate(); }}>${chip.label}</button>`)}
        <span class="grow"></span>
        <select aria-label="Project" @change=${(event: Event) => {
            const picked = (event.target as HTMLSelectElement).value;
            this.#project = picked === "" ? null : picked;
            this.requestUpdate();
          }}>
          <option value="" ?selected=${this.#project === null}>${ALL_PROJECTS}</option>
          ${[...new Set(value.rounds.map((one) => one.project))].sort().map((name) => html`<option value=${name}
            ?selected=${this.#project === name}>${name}</option>`)}
        </select>
        <button type="button" class="keybtn" aria-expanded=${this.#address?.kind === "key" ? "true" : "false"}
          @click=${() => this.#toggle({ kind: "key" })}>${KEY_TITLE}</button>
      </div>
      <div class="board"><div class="inner">
        <div class="labels" aria-hidden="true"><span class="name">round</span>
          ${value.columns.map((column, at) => html`${at === layout.labelAt ? html`<span class="stagecol">stage</span>` : nothing}
            <span class="col">${column.label}</span>`)}
          ${layout.labelAt === value.columns.length ? html`<span class="stagecol">stage</span>` : nothing}
          <span class="phrase">where it is</span><span class="age"></span></div>
        ${groups.map((view) => this.#group(view, value, layout.labelAt, many))}
        ${this.#closedFold(value)}
      </div></div>`;
  }

  #group(view: GroupView, value: Board, labelAt: number, many: boolean): TemplateResult | typeof nothing {
    const rounds = view.rounds.filter((one) => isShown(one, this.#filter, this.#project));
    const unreadable = view.unreadable.filter((one) => isUnreadableShown(one, this.#filter, this.#project));
    if (rounds.length + unreadable.length === 0) return nothing;
    return html`<div class="group">${view.label}<span class="dim">${rounds.length + unreadable.length}</span></div>
      ${rounds.map((one) => this.#round(one, value, labelAt, many))}
      ${unreadable.map((one) => html`<div class="line unreadable"><span class="name">${slugOf(one.name)}</span>
        <span class="phrase"><span class="w-bad">${UNREADABLE_LEAD}</span>
          <span class="dim">${unreadableWords(one)}</span></span></div>`)}`;
  }

  #round(round: Round, value: Board, labelAt: number, many: boolean): TemplateResult {
    const shown = linesShown(round, this.#unfolded);
    const now = this.#feed?.now() ?? null;
    const folds = foldsOf(round, value.columns);
    const drawn = (line: Line): TemplateResult => {
      const at = round.lines.indexOf(line);
      const first = at === 0;
      return html`<div class="line ${this.#isOpenLine(round, line) ? "open" : ""}">
        <span class="name">${first ? html`<button type="button" class="link" data-name=${round.name}
            aria-expanded=${this.#address?.kind === "round" && this.#address.round === round.name ? "true" : "false"}
            @click=${() => this.#toggle({ kind: "round", round: round.name })}>${round.slug}</button>${many
            ? html`<span class="project">${round.project}</span>` : nothing}` : nothing}</span>
        ${this.#rail(round, line, at, value.columns, labelAt, first)}
        <span class="phrase">${phraseShown(first ? round.phrase : line.phrase, { roundLine: first }).map((part, i) => html`${i ? ", " : ""}<span
          class="w-${part.weight}">${part.text}</span>`)}</span>
        <span class="age">${first ? ageWord(round.last_at, now) : ""}</span>
      </div>`;
    };
    // A fold's lines are drawn under its own line, so the fold still shows, opened.
    const folded = new Set(folds.map((fold) => fold.fold));
    return html`${shown.filter((line) => line.fold === null || !folded.has(line.fold)).map(drawn)}${folds.map((fold) => {
      const key = foldKey(round.name, fold.fold);
      const open = this.#unfolded.has(key);
      return html`<div class="fold"><button type="button" aria-expanded=${open ? "true" : "false"}
          @click=${() => this.#flip(key)}>${open ? "▾" : "▸"} ${fold.text}</button>
        ${this.#smallRail(fold.cells, fold.text)}${fold.more ? html`<span class="dim">+${fold.more}</span>` : nothing}</div>
        ${open ? shown.filter((line) => line.fold === fold.fold).map(drawn) : nothing}`;
    })}`;
  }

  #flip(key: string): void {
    if (this.#unfolded.has(key)) this.#unfolded.delete(key);
    else this.#unfolded.add(key);
    this.requestUpdate();
  }

  #isOpenLine(round: Round, line: Line): boolean {
    const address = this.#address;
    if (address === null || this.#anchor.kind === "strip") return false;
    if (address.kind === "step" && address.round === round.name) {
      const column = this.#feed?.value?.columns.find((one) => one.id === address.column);
      return column?.part === "stage" ? round.lines.find((one) => one.stage === address.stage) === line
                                      : line.kind === "round";
    }
    const item = address.kind === "walk" ? address.item : "id" in address ? address : null;
    return item !== null && item.holder === round.name && line.kind === "round";
  }

  /** One line's rail: a mark per column, with the stage label's wide cell before the
   * first stage column. */
  #rail(round: Round, line: Line, at: number, columns: readonly Column[], labelAt: number, first: boolean): TemplateResult {
    const openKey = this.#openKey(round, line);
    const more = first ? moreLabel(round, this.#unfolded) : null;
    const label = html`<moxi-ui-mark wide><span class="label">${line.stage ? html`<span class="stage">${line.stage}</span>
        <span class="branch">${line.branch ?? ""}</span>` : nothing}${more ? html`<button type="button" class="more"
        aria-label=${more.label} aria-expanded=${this.#unfolded.has(foldKey(round.name, "more")) ? "true" : "false"}
        @click=${() => this.#flip(foldKey(round.name, "more"))}>${more.text}</button>` : nothing}</span></moxi-ui-mark>`;
    return html`<moxi-ui-rail data-round=${round.name} data-line=${at}
        @moxi-rail-mark=${(event: Event) => this.#onMark(event, round)}>${columns.map((column, i) => {
      const stage = stageOf(column, line);
      const cell = line.cells[i] ?? null;
      const key = markKey(column.id, stage);
      return html`${i === labelAt ? label : nothing}<moxi-ui-mark ${attributes(
        markAttributes(column, stage, round.slug, cell, { open: key === openKey }))}></moxi-ui-mark>`;
    })}${labelAt === columns.length ? label : nothing}</moxi-ui-rail>`;
  }

  /** The key of the mark on this line whose popover is open, if any. */
  #openKey(round: Round, line: Line): string | null {
    const address = this.#address;
    if (address === null || this.#anchor.kind === "strip" || !this.#isOpenLine(round, line)) return null;
    if (address.kind === "step") {
      const column = this.#feed?.value?.columns.find((one) => one.id === address.column);
      return markKey(address.column, column?.part === "stage" ? address.stage : null);
    }
    const item = address.kind === "walk" ? address.item : "id" in address ? address : null;
    return item ? itemMarker({ kind: item.kind, round: round.name })?.key ?? null : null;
  }

  #closedFold(value: Board): TemplateResult | typeof nothing {
    if (value.closed.length === 0) return nothing;
    const open = this.#unfolded.has(CLOSED_FOLD);
    const layout = layoutOf(value.columns);
    return html`<div class="fold closedfold"><button type="button" aria-expanded=${open ? "true" : "false"}
        @click=${() => this.#flip(CLOSED_FOLD)}>${open ? "▾" : "▸"} ${closedFoldText(value.closed)}</button></div>
      ${open ? value.closed.map((one) => html`<div class="line closed">
        <span class="name"><button type="button" class="link" data-name=${one.name}
          @click=${() => this.#toggle({ kind: "round", round: one.name })}>${slugOf(one.name)}</button></span>
        <moxi-ui-rail data-closed=${one.name}>${value.columns.map((column, i) => html`${i === layout.labelAt
          ? html`<moxi-ui-mark wide></moxi-ui-mark>` : nothing}<moxi-ui-mark ${attributes({ state: "done", settled: "",
            quiet: "", label: `${column.label}: done` })}></moxi-ui-mark>`)}</moxi-ui-rail>
        <span class="phrase dim">${closedWords(one)}</span><span class="age"></span>
      </div>`) : nothing}`;
  }

  #foot(value: Board): TemplateResult {
    const feed = this.#feed!;
    const lines = [...footWords(value, feed.readAt, feed.failure?.why ?? null, value.today), ...this.#notes];
    return html`<div class="foot">${lines.map((one) => html`<span>${one}</span>`)}</div>`;
  }

  // --- the popover -------------------------------------------------------------------------

  #popover(value: Board): TemplateResult {
    const address = this.#address!;
    const width = popoverWidth(address);
    const close = html`<button type="button" class="x" aria-label="Close" @click=${() => this.#close()}>×</button>`;
    let head: { title: string; sub: string; cell: Cell | null } = { title: "", sub: "", cell: null };
    let body: TemplateResult | typeof nothing = nothing;
    let foot: TemplateResult | typeof nothing = nothing;
    const ctx = this.#ctx(value);
    const read = this.#record?.read ?? null;
    const failed = read?.kind === "failed" ? html`<p class="dim">${read.failure.why}</p>` : nothing;
    const loading = read === null && address.kind !== "key" ? html`<p class="dim">${READING}</p>` : nothing;
    if (address.kind === "key") {
      head = { title: KEY_TITLE, sub: "", cell: null };
      body = html`${KEY_STATES.map((one) => html`<div class="key-row"><moxi-ui-rail><moxi-ui-mark
        ${attributes({ state: one.state, quiet: "", label: stateWords({ ...DONE, state: one.state }) })}></moxi-ui-mark></moxi-ui-rail>
        <span>${one.words}</span></div>`)}<p>${KEY_HERE}</p><p class="dim">${KEY_COLUMNS}</p>`;
    } else if (read?.kind === "record" && (address.kind === "step")) {
      const cell = this.#cellFor(address, value);
      const top = stepHead(read.record, cell);
      head = { ...top, cell };
      body = html`${this.#tabs(address, value)}${stepSections(read.record, ctx).map((one) => this.#section(one))}`;
      if (this.#anchor.kind === "strip") {
        foot = html`<button type="button" class="walkbtn" @click=${() => this.#showLine(address)}>${SHOW_LINE}</button>`;
      }
    } else if (read?.kind === "record" && address.kind === "round") {
      const card = cardView(read.record, ctx);
      head = { title: card.title, sub: card.sub, cell: null };
      body = html`${card.sections.map((one) => this.#section(one))}`;
    } else if (read?.kind === "record") {
      const view = itemView(read.record, ctx);
      head = { title: view.title, sub: view.sub, cell: { ...DONE, state: "yours", settled: false } };
      body = html`<div class="question">${view.text}</div>
        ${view.lines.map((one) => html`<div class="dim">${one}</div>`)}
        ${view.options.map((one) => html`<div class="option"><span class="k">${one.key}</span><span>${one.text}</span></div>`)}
        ${view.passage ? this.#passage(view.passage) : nothing}`;
      foot = html`<span class="answer">${ANSWER_LEAD}</span><div class="well answer">${view.answer}</div>`;
    } else if (address.kind === "step") {
      head = { title: stepName(address.column, address.stage), sub: address.round, cell: this.#cellFor(address, value) };
      body = html`${this.#tabs(address, value)}`;
    }
    const at = this.#sequenceAt();
    const walk = at !== -1 && this.#sequence.length > 1 ? html`<span class="grow"></span>
      <button type="button" class="walkbtn" aria-label="Previous" ?disabled=${at === 0} @click=${() => this.#walk(-1)}>‹</button>
      <span>${walkFoot(at, this.#sequence.length)}</span>
      <button type="button" class="walkbtn" aria-label="Next" ?disabled=${at === this.#sequence.length - 1}
        @click=${() => this.#walk(1)}>›</button>` : nothing;
    return html`<div class="pop ${width > POP_WIDTH ? "wide" : ""}" role="dialog" aria-label=${head.title}>
      <div class="phead">${head.cell ? this.#oneMark(head.cell) : nothing}<div class="titles">
        <div class="title">${head.title}</div><div class="dim">${head.sub}</div></div>${close}</div>
      <div class="body">${loading}${failed}${body}</div>
      ${foot !== nothing || walk !== nothing ? html`<div class="pfoot">${foot}${walk}</div>` : nothing}
    </div>`;
  }

  #cellFor(address: Extract<Address, { kind: "step" }>, value: Board): Cell | null {
    const round = value.rounds.find((one) => one.name === address.round);
    const column = value.columns.find((one) => one.id === address.column);
    if (round && column) {
      const line = column.part === "stage" ? round.lines.find((one) => one.stage === address.stage) : round.lines[0];
      if (line) return cellAt(line, value.columns, address.column);
    }
    const entry = value.recent.find((one) => one.round === address.round && one.stage === address.stage);
    if (!entry) return null;
    const at = entry.steps.indexOf(address.column);
    return at === -1 ? null : entry.cells[at] ?? null;
  }

  /** A stage step's tabs: its stage's steps as a rail, the open one marked. */
  #tabs(address: Extract<Address, { kind: "step" }>, value: Board): TemplateResult | typeof nothing {
    if (!address.stage) return nothing;
    const round = value.rounds.find((one) => one.name === address.round);
    const line = round?.lines.find((one) => one.stage === address.stage);
    const entry = value.recent.find((one) => one.round === address.round && one.stage === address.stage);
    const tabs = line ? value.columns.map((column, i) => ({ column, cell: line.cells[i] ?? null }))
                          .filter((one) => one.column.part === "stage" && one.cell !== null)
                      : entry ? stripTabs(entry, value.columns) : [];
    if (tabs.length === 0) return nothing;
    const slug = slugOf(address.round);
    return html`<moxi-ui-rail class="tabs" @moxi-rail-mark=${(event: Event) => {
        const key = (event as CustomEvent<{ key: string | null }>).detail?.key;
        if (key) this.#open({ ...address, column: parseMarkKey(key).column }, { anchor: this.#anchor });
      }}>${tabs.map(({ column, cell }) => html`<moxi-ui-mark ${attributes(cell === null ? { state: null } : {
        ...markAttributes(column, address.stage, slug, cell, { open: column.id === address.column }),
        word: column.label, count: null, here: null })}></moxi-ui-mark>`)}</moxi-ui-rail>`;
  }

  #showLine(address: Extract<Address, { kind: "step" }>): void {
    const value = this.#feed?.value;
    if (!value) return;
    const round = value.rounds.find((one) => one.name === address.round);
    let target: Element | null = null;
    if (round) {
      const shown = revealing(round, this.#filter, this.#project);
      this.#filter = shown.filter;
      this.#project = shown.project;
      const line = round.lines.find((one) => one.stage === address.stage);
      if (line?.fold) this.#unfolded.add(foldKey(round.name, line.fold));
      this.#anchor = { kind: "board" };
      this.#placed = false;
      this.requestUpdate();
      void this.updateComplete.then(() => {
        target = this.#anchorOf(address);
        target?.scrollIntoView({ block: "center" });
        this.#place();
      });
    } else {
      this.#unfolded.add(CLOSED_FOLD);
      this.#close({ focus: false });
      void this.updateComplete.then(() => {
        const root = this.renderRoot as ShadowRoot;
        root.querySelector(`[data-name="${CSS.escape(address.round)}"]`)?.scrollIntoView({ block: "center" });
      });
    }
  }

  #passage(passage: { from: string; text: string }): TemplateResult {
    const whole = this.#shown.has("passage");
    return html`<div><div class="strong">${passage.from}</div>
      <div class="passage ${whole ? "" : "cut"}">${passage.text}</div>
      ${whole ? nothing : html`<button type="button" class="linkish" @click=${() => {
        this.#shown.add("passage");
        this.requestUpdate();
      }}>${READ_PASSAGE}</button>`}</div>`;
  }

  #section(one: Section): TemplateResult {
    return html`<div class="sec">${one.head ? html`<h3>${one.head}</h3>` : nothing}
      ${one.rows.map((row) => html`<div class="row">${row.map((piece) => this.#piece(piece))}</div>`)}
      ${one.folds.map((fold) => this.#shown.has(`${one.head}|${fold.label}`)
        ? fold.rows.map((row) => html`<div class="row">${row.map((piece) => this.#piece(piece))}</div>`)
        : html`<button type="button" class="foldbtn" @click=${() => {
            this.#shown.add(`${one.head}|${fold.label}`);
            this.requestUpdate();
          }}>${fold.label}</button> `)}
      ${one.wells.map((command) => html`<div class="well">${command}</div>`)}</div>`;
  }

  #piece(piece: Piece): TemplateResult {
    const classes = `${piece.mono ? "mono" : ""} ${piece.tone ? `t-${piece.tone}` : ""}`;
    if (piece.open) {
      const to = piece.open;
      return html`<button type="button" class="linkish ${classes}" @click=${() => this.#open(to)}>${piece.text}</button> `;
    }
    if (piece.session) {
      const route = `session/${encodeURIComponent(piece.session)}`;
      return html`<a class=${classes} href=${`/m/turns/${route}`} @click=${(event: Event) => {
        event.preventDefault();
        navigate("turns", route);
      }}>${piece.text}</a>`;
    }
    return html`<span class=${classes}>${piece.text}</span>`;
  }

  // --- the phone ---------------------------------------------------------------------------

  #phoneView(value: Board): TemplateResult {
    const groups = arrange(value, this.#held);
    const now = this.#feed?.now() ?? null;
    const counted = itemsOf(value).length > 0;
    const sheet = this.#address?.kind === "round" ? value.rounds.find((one) => one.name === (this.#address as { round: string }).round)
                                                  : undefined;
    const recent = html`<div class="group">${STRIP_HEAD}</div>${value.recent.slice(0, 5).map((entry) => {
      const card = stripCard(entry, now);
      return html`<div class="recent"><span class="strong">${card.slug}</span><span class="dim">${card.stage}</span>
        ${this.#smallRail(entry.cells, card.label)}<span class="act">${card.act}</span>
        <span class="dim">${card.age}</span></div>`;
    })}`;
    return html`<div class="phone ${this.#feed?.failure ? "stale" : ""}">
      <div class="waiting ${counted ? "" : "none"}">${this.#oneMark(counted ? { ...DONE, state: "yours", settled: false }
        : DONE)}<span class="strong">${phoneWaiting(value)}</span></div>
      ${value.rounds.length + value.unreadable.length === 0 ? this.#empty(value) : nothing}
      ${groups.map((view, n) => html`<div class="group">${view.label}<span class="dim">
          ${view.rounds.length + view.unreadable.length}</span></div>
        ${view.rounds.map((round) => html`<button type="button" class="round" data-name=${round.name}
            @click=${() => this.#open({ kind: "round", round: round.name })}>
          <span class="slug">${round.slug}</span>
          <span class="top">${this.#smallRail(round.lines[0].cells, round.slug)}<span class="grow"></span>
            <span class="dim">${ageWord(round.last_at, now)}</span></span>
          <span class="phrase">${phraseShown(round.phrase).map((part, i) => html`${i ? ", " : ""}<span
            class="w-${part.weight}">${part.text}</span>`)}</span></button>`)}
        ${view.unreadable.map((one) => html`<div class="round"><span class="slug">${slugOf(one.name)}</span>
          <span class="w-bad">${UNREADABLE_LEAD}</span> <span class="dim">${unreadableWords(one)}</span></div>`)}
        ${n === 0 && view.group === "yours" ? recent : nothing}`)}
      ${groups[0]?.group !== "yours" ? recent : nothing}
      ${this.#foot(value)}
    </div>
    ${sheet ? this.#sheet(sheet, value) : nothing}`;
  }

  #sheet(round: Round, value: Board): TemplateResult {
    const questions = sheetQuestions(value, round.name);
    return html`<div class="sheet" role="dialog" aria-label=${round.slug}>
      <button type="button" class="x" aria-label="Close" @click=${() => this.#close({ focus: false })}>×</button>
      <div class="strong sheetslug">${round.slug}</div>
      <div class="dim">${round.project}</div>
      ${round.description ? html`<p>${round.description}</p>` : nothing}
      <moxi-ui-rail vertical>${sheetMarks(round, value.columns).map((mark) => html`<moxi-ui-mark
        ${attributes(mark.attributes)}></moxi-ui-mark>`)}</moxi-ui-rail>
      ${questions.length ? html`${questions.map((one) => html`<p>${one}</p>`)}<p class="dim">${AT_DESK}</p>` : nothing}
    </div>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, RoundsPage);

declare global {
  interface HTMLElementTagNameMap {
    [TAG]: RoundsPage;
  }
}

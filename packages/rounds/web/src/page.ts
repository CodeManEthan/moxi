// <moxi-rounds-page route="...">: rounds' page, every open round as one line of its steps
// in columns aligned across rounds. Top to bottom:
//
// - The waiting line: what waits on the operator, counted, and `Go through in order`,
//   which walks every item in the board's order.
// - `Last acted on`: the ten builds with the newest recorded act, each a card that opens
//   its build's current step under it, with its stage's steps as tabs.
// - The board: filter chips and a project picker, the column labels, then the groups,
//   each round a rail of its steps (`<moxi-ui-rail>`, from ui's client script) with a
//   sub-line per other build in flight and its folds; then the closed rounds' fold. A
//   line's stage name has a cell of its own before the rail, never on the rail's line.
// - The foot: when it was read, the parked questions, and what an address named that is
//   no longer open.
//
// A marker opens its step's record in a popover; one that waits on the operator opens
// the operator's items there. One thing is open at a time and the page writes it into its address, in place of
// the entry it is on, so Back leaves the page. Every word and every rule of what to draw
// comes from `model.ts`, and goes into the page as text. At 720 px and under it is the
// phone layout: rounds as buttons, each opening a sheet with one vertical rail.
//
// At the desk the operator acts from an item's popover: an answer goes into the tray,
// `Your answers` (`tray.ts`), kept in the browser's storage, and `Send` posts the tray to
// `/api/answers`; the popover and the tray's row then show each receipt. A step of a
// stage with an open build offers `Drop this build…`, behind a confirm read from
// `/api/drop`, posted to `/api/drop/build`. The foot's parked questions open their list.
// Nothing acts at phone width.
//
// It reads `/api/rounds`, then follows the topic `rounds`, and reads the route again when
// its tab is shown again. A record is read from `/api/record` when it opens, and again
// when its round's `rev` changes while it is open; a sent own-words answer's question is
// read again when its round's `rev` changes, and a sent correction finds the ruling that
// supersedes the one it corrects among the rulings waiting to be read back. With turns
// installed it reads turns' sessions, so a builder whose worktree is one live session's
// folder links to its row.
// It reads `/api/agents` once, and again when the To picker opens: the agents comms
// knows, and whether the page server sends through comms at all.

import { css, html, LitElement, noChange, nothing } from "lit";
import type { TemplateResult } from "lit";
import { Directive, directive, PartType } from "lit/directive.js";
import type { ElementPart, PartInfo } from "lit/directive.js";
import { api, installed, navigate, setAddress, subscribe } from "/m/ui/client.js";

import { failureWhy, Feed, parseBoard, readRecord } from "./feed.ts";
import type { Board, Cell, Column, Line, Recent, RecordRead, RecordValue, Round } from "./feed.ts";
import {
  addressRoute, ageWord, ALL_PROJECTS, arrange, AT_DESK, CANT_READ, cardView, cellAt, chips, CLOSED_FOLD,
  closedFoldText, closedWords, EMPTY_HEAD, EMPTY_TEXT, foldKey, foldsOf, footWords, holdOf, isManyProjects, isOpenNow,
  isShown, isUnreadableShown, itemAddress, itemMarker, itemsOf, itemView, KEY_COLUMNS, KEY_HERE, KEY_STATES, KEY_TITLE,
  LOCAL, linesShown, markAttributes, markKey, moreLabel, noLongerOpen, NOTHING_WAITS, OPEN_ROUNDS,
  parseAddress, parseMarkKey, PASSAGE_LINES, phoneWaiting, PHONE_PX, phraseShown, placePopover, POP_WIDE, POP_WIDTH,
  popoverWidth, revealBy,
  READ_PASSAGE, READING, READING_AFTER_MS, recordQuery, revealing, rulingQuery, sessionAt, sheetMarks, sheetQuestions, SHOW_LINE,
  slugOf, stageOf, stateWords, stepHead, stepName, stepSections, STRIP_CAPTION, STRIP_EMPTY, STRIP_HEAD, stripCard, stripKey,
  stripStep, stripTabs, UNREADABLE_LEAD, unreadableWords, WAITING_LEAD, waitingCounts, walkFoot, WALK_BUTTON,
  walkStep, dropLines, dropReceipt, hasOpenBuild, leadFromProject, leadOf, PARKED_HEAD, parkedGroups, parkedWords, parkingWords,
  preparedView, RECOMMENDED,
} from "./model.ts";
import type { Address, Context, DropFacts, Filter, GroupView, Held, Item, ItemKind, ItemView, Piece, Section } from "./model.ts";
import {
  bodyOf, chipOf, correctionFound, draftOf, EACH, eachLeadWords, isAnswerable, isPageKind, kindWords, messageWords,
  leadsKnown, mintId, overCap, postsOf, readBackOf, readKey, receiptLines, recipientWords, RULE_LINE, RULE_LINE_ALONE,
  sameRuling, sendable, sendWords, Tray, trayKey,
} from "./tray.ts";
import type { Draft, Kind, Reach, Receipt, ReceiptLine, RulingOf, Store } from "./tray.ts";

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
type Anchor = { kind: "board" } | { kind: "strip"; card: string } | { kind: "parked"; key: string };

/** An item a popover answers. */
type ItemRef = { kind: ItemKind; holder: string; id: string };

/** What the open popover's answer form holds: the choice made (an option, accept or
 * reject, a read-back, a done job), the words typed, and which box is open. */
interface Form {
  choice: { kind: Kind; chose: string | null } | null;
  text: string;
  /** `correct` while `Not what I said…`'s box is open, with the ruling it corrects. */
  correcting: string | null;
}

const EMPTY_FORM: Form = { choice: null, text: "", correcting: null };

/** The drop's confirm while it is open: its act id, minted when it opens, its facts and
 * the drop's receipt. */
interface DropState {
  round: string;
  stage: string;
  act: string;
  facts: DropFacts | null;
  why: string | null;
  receipt: Receipt | null;
  busy: boolean;
  /** Why the drop's post got no answer; `Drop again` posts it once more under its act id. */
  lost: string | null;
}

/** The browser's storage, or null where it can't be reached. */
function browserStore(): Store | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

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

    .board { overflow-x: auto; --board-line: 38px; }
    .inner { min-width: max-content; }
    .labels, .line { display: flex; align-items: center; }
    .labels { position: sticky; top: 0; z-index: 2; background: var(--moxi-bg); font-size: 11px;
              color: var(--moxi-text-dim); border-bottom: 1px solid var(--moxi-border); height: 26px; }
    .labels .col { width: var(--moxi-rail-cell); text-align: center; flex: none; white-space: nowrap; }
    .stagecell { width: var(--moxi-rail-label); flex: none; box-sizing: border-box; padding: 0 4px; display: flex;
                 gap: 6px; align-items: center; white-space: nowrap; overflow: hidden; }
    .name { width: 180px; flex: none; position: sticky; left: 0; z-index: 1; background: inherit;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-left: 4px; }
    .labels .name { background: var(--moxi-bg); }
    .phrase { flex: 1; min-width: 120px; contain: inline-size; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
              padding-left: 12px; }
    .age { width: 44px; flex: none; text-align: right; color: var(--moxi-text-dim); padding-right: 4px; }
    .group { font-weight: 600; margin: 18px 0 4px; }
    .group .dim { font-weight: 400; margin-left: 6px; }
    /* A board line is 4 px taller than its 34 px rail, which it centres, so a line's ▼ (1 px over its rail)
       stays 5 px clear of the step words under the rail of the line above. */
    .line { height: var(--board-line); background: var(--moxi-bg); --moxi-rail-bg: var(--moxi-bg); }
    .line:hover { background: var(--moxi-bg-hover); --moxi-rail-bg: var(--moxi-bg-hover); }
    .line.open { background: var(--moxi-accent-soft); --moxi-rail-bg: var(--moxi-accent-soft); }
    .line .name button { font-size: 15px; }
    .line .project { color: var(--moxi-text-dim); margin-left: 6px; }
    moxi-ui-rail { flex: none; }
    .line .stagecell { font-size: 13px; }
    .stagecell .stage { color: var(--moxi-text-dim); }
    .stagecell .branch { overflow: hidden; text-overflow: ellipsis; min-width: 0; }
    .more { background: none; border: 0; padding: 0 2px; cursor: pointer; color: var(--moxi-text-dim); }
    .w-yours { font-weight: 600; }
    .w-bad { font-weight: 600; color: var(--moxi-state-failed); }
    .fold { display: flex; align-items: center; gap: 8px; height: 26px; padding-left: 184px; }
    .fold button { background: none; border: 0; cursor: pointer; padding: 0; }
    .line.unreadable { height: auto; min-height: var(--board-line); padding: 6px 0; box-sizing: border-box; }
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

    .btn { border: 1px solid var(--moxi-border-strong); background: var(--moxi-bg-raised); border-radius: 6px;
           padding: 5px 10px; cursor: pointer; }
    .btn.accent { background: var(--moxi-accent); border-color: var(--moxi-accent); color: var(--moxi-bg); }
    .btn:disabled { opacity: 0.5; cursor: default; }
    .acts { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; flex-basis: 100%; }
    .option.pick { cursor: pointer; background: none; text-align: left; width: 100%; font: inherit; color: inherit; }
    .option.pick[aria-pressed="true"] { border-color: var(--moxi-accent); box-shadow: 0 0 0 1px var(--moxi-accent); }
    .option .rec { margin-left: auto; color: var(--moxi-text-dim); font-size: 11px; white-space: nowrap; }
    textarea { font: inherit; color: inherit; background: var(--moxi-bg); border: 1px solid var(--moxi-border);
               border-radius: 6px; padding: 6px 8px; width: 100%; box-sizing: border-box; min-height: 54px; resize: vertical; }
    .receipt { flex-basis: 100%; display: flex; flex-direction: column; gap: 2px; overflow-wrap: anywhere; }
    .tray { border: 1px solid var(--moxi-border-strong); border-radius: 8px; padding: 10px 14px; margin-bottom: 20px;
            display: flex; flex-direction: column; gap: 8px; }
    .tray .rule { color: var(--moxi-text-dim); }
    .tray .trow { border-top: 1px solid var(--moxi-border); padding-top: 8px; display: flex; flex-direction: column; gap: 3px; }
    .tray .trow .top { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
    .tray .chipline { color: var(--moxi-text-dim); overflow-wrap: anywhere; }
    .tray .sendrow { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .parked { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; color: var(--moxi-text); }
    .parked .ph { font-weight: 600; margin-top: 4px; }
    .parkedrow { text-align: left; }
    .confirm { border: 1px solid var(--moxi-border-strong); border-radius: 8px; padding: 10px 12px;
               display: flex; flex-direction: column; gap: 8px; }
  `;

  route = "";

  #feed: Feed<Board> | null = null;
  #stop: (() => void) | null = null;
  #stopSessions: (() => void) | null = null;
  #sessions: { key: string; cwd: string | null }[] = [];
  #address: Address | null = null;
  #anchor: Anchor = { kind: "board" };
  /** The open record: its route, what was read, its round's `rev` then, and `#acts` when
   * its read began. */
  #record: { route: string; read: RecordRead | null; rev: string | null; seen: number } | null = null;
  /** How many times a send answered the open item; a record read before the last of them
   * may not show that answer yet. */
  #acts = 0;
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
  #tray: Tray = new Tray(null, "");
  #to = EACH;
  #agents: string[] | null = null;
  /** Whether comms is installed, as the page server finds it; null until it is read. */
  #comms: boolean | null = null;
  #agentsWhy: string | null = null;
  #sending = false;
  #sendNote: string | null = null;
  #form: Form = { ...EMPTY_FORM };
  #editing: string | null = null;
  #parkedOpen = false;
  #drop: DropState | null = null;
  /** Each sent own-words answer's and correction's `readKey` when its record was last read. */
  #readAt = new Map<string, string>();

  connectedCallback(): void {
    super.connectedCallback();
    this.#media = window.matchMedia(`(max-width: ${PHONE_PX}px)`);
    this.#phone = this.#media.matches;
    this.#media.addEventListener("change", this.#onMedia);
    this.#tray = new Tray(browserStore(), trayKey(location.origin));
    const feed = new Feed<Board>(() => api(MODULE, "api/rounds"), parseBoard, () => this.#onValue(feed));
    this.#feed = feed;
    void feed.refresh().then(() => {
      if (this.#feed === feed) this.#stop = subscribe(MODULE, "rounds", (data) => feed.topic(data));
    });
    if (installed("turns")) void this.#readSessions();
    window.addEventListener("storage", this.#onStorage);
    void this.#readAgents();
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
    window.removeEventListener("storage", this.#onStorage);
  }

  /** Another tab of this page server changed the tray. */
  #onStorage = (): void => {
    this.#tray.load();
    this.requestUpdate();
  };

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
      void this.#readFound(value);
    }
    this.requestUpdate();
  }

  /** Reads again, for each sent own-words answer and correction whose holder changed
   * (`readKey`), the record its row reads from: the question's, so the row reads recorded
   * once the agent's ruling appears; for a correction the corrected ruling's, which gives
   * the newest ruling that replaces it, read back or not. */
  async #readFound(value: Board): Promise<void> {
    const sent = this.#tray.sent.filter((one) => (one.kind === "words" || one.kind === "correct")
      && one.receipt?.message?.id);
    for (const draft of sent) {
      const key = readKey(draft.holder, value);
      if (this.#readAt.get(draft.act) === key) continue;
      this.#readAt.set(draft.act, key);
      const route = draft.kind === "words" ? recordQuery({ kind: "question", holder: draft.holder, id: draft.item })!
        : rulingQuery(draft.holder, draft.item);
      const read = await readRecord(() => api(MODULE, route));
      if (read.kind !== "record") continue;
      if (draft.kind === "words") this.#takeFound(draft.holder, draft.item, read.record);
      else this.#takeCorrection(draft.act, correctionFound(draft, read.record));
    }
  }

  /** Puts on a sent correction the ruling that replaces the one it corrects. One found
   * stays when a later read finds none. */
  #takeCorrection(act: string, found: RulingOf | null): void {
    const draft = this.#tray.sent.find((one) => one.act === act);
    if (!draft || found === null || sameRuling(draft.found, found)) return;
    this.#tray.setFound(act, found);
    this.requestUpdate();
  }

  /** Puts the ruling a question's record holds on its sent own-words drafts. */
  #takeFound(holder: string, item: string, record: RecordValue): void {
    const ruling = record.ruling ?? null;
    const sent = this.#tray.sent.filter((one) => one.kind === "words" && one.holder === holder && one.item === item);
    let changed = false;
    for (const one of sent) {
      if (ruling && record.item?.state !== "parked" && one.found?.id !== ruling.id) {
        this.#tray.setFound(one.act, ruling);
        changed = true;
      }
    }
    if (changed) this.requestUpdate();
  }

  /** Whether the tray holds a draft or a receipt of the item an address names, so its
   * popover stays open after the item stops waiting. */
  #trayHolds(address: Address): boolean {
    const item = this.#itemOf(address);
    return item !== null && this.#tray.of(item.holder, item.id) !== null;
  }

  #itemOf(address: Address | null): ItemRef | null {
    if (address === null) return null;
    if (address.kind === "walk") return address.item;
    return "id" in address ? address : null;
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
    if (!isOpenNow(address, value) && !this.#trayHolds(address)) {
      this.#notes = [noLongerOpen(address)];
      this.#close({ focus: false });
      return;
    }
    if (address.kind === "walk") {
      // The walk keeps its items while it is open, so an item answered stays its place.
      const item = address.item;
      if (item === null || !this.#sequence.some((one) => one.kind === item.kind && one.holder === item.holder
                                                       && one.id === item.id)) {
        this.#sequence = itemsOf(value, this.#order(value));
      }
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
    const seen = this.#acts;
    const before = this.#record?.route === route ? this.#record : null;
    this.#record = { route, read: before?.read ?? null, rev, seen: before?.seen ?? seen };
    const read = await readRecord(() => api(MODULE, route));
    // A read begun before a later one that has landed already is older than what shows.
    if (this.#record?.route !== route || (this.#record.read !== null && this.#record.seen > seen)) return;
    if (read.kind === "gone" && this.#record?.read?.kind === "record" && this.#trayHolds(address)) {
      // An item answered from the page keeps its popover, on the record it had, for the receipt.
      this.#record = { ...this.#record, rev };
      return;
    }
    if (read.kind === "record" && read.record.what === "question" && read.record.item) {
      this.#takeFound(read.record.item.holder ?? "", read.record.item.id, read.record);
    }
    if (read.kind === "gone") {
      this.#notes = [noLongerOpen(address)];
      this.#close({ focus: false });
      return;
    }
    this.#record = { route, read, rev, seen };
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
    this.#form = { ...EMPTY_FORM };
    this.#drop = null;
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
    this.#form = { ...EMPTY_FORM };
    this.#drop = null;
    setAddress(MODULE, "");
    this.requestUpdate();
    if (focus && was) void this.updateComplete.then(() => (this.#anchorOf(was) as HTMLElement | null)?.focus());
  }

  #onKey = (event: KeyboardEvent): void => {
    if (this.#address === null || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Escape") {
      event.preventDefault();
      if (this.#drop !== null) {
        this.#drop = null;
        this.requestUpdate();
      } else {
        this.#close();
      }
      return;
    }
    const target = event.composedPath()[0] as HTMLElement | undefined;
    if (target && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
    if (this.#answering() && this.#onAnswerKey(event, target)) return;
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
    if (this.#anchor.kind === "strip") return root.querySelector(`[data-card="${CSS.escape(this.#anchor.card)}"]`);
    if (this.#anchor.kind === "parked") return root.querySelector(`[data-parked="${CSS.escape(this.#anchor.key)}"]`);
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
    const row = anchor.closest(".line, .waiting, .card, .head, .parkedrow");
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
        ${this.#trayView(value)}
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
    const open = this.#anchor.kind === "strip" && this.#address ? this.#anchor.card : null;
    return html`<div class="strip-head"><h2>${STRIP_HEAD}</h2> <span class="dim">${STRIP_CAPTION}</span></div>
      ${value.recent.length === 0 ? html`<p class="dim">${STRIP_EMPTY}</p>` : html`<div class="strip">
        ${value.recent.map((entry) => {
          const card = stripCard(entry, now);
          const key = stripKey(entry);
          return html`<button type="button" class="card" data-card=${key} aria-expanded=${open === key ? "true" : "false"}
              aria-label=${card.label} @click=${() => this.#openStrip(entry)}>
            <span class="top"><span class="slug"><span class="strong">${card.slug}</span>
              <span class="dim">${card.stage}</span></span><span class="dim">${card.age}</span></span>
            <span class="bottom">${this.#smallRail(entry.cells, card.label)}<span class="act">${card.act}</span></span>
          </button>`;
        })}</div>`}`;
  }

  #openStrip(entry: Recent): void {
    const step = stripStep(entry);
    if (step === null) return;
    const address: Address = { kind: "step", round: entry.round, column: step, stage: entry.stage };
    const card = stripKey(entry);
    if (this.#anchor.kind === "strip" && this.#anchor.card === card && this.#address) this.#close();
    else this.#open(address, { anchor: { kind: "strip", card } });
  }

  #empty(value: Board): TemplateResult {
    return html`<p class="emptyhead">${EMPTY_HEAD}</p>
      <p class="dim">${EMPTY_TEXT}</p>${this.#closedFold(value)}`;
  }

  #board(value: Board): TemplateResult {
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
        <div class="labels" aria-hidden="true"><span class="name">round</span><span class="stagecell">stage</span>
          ${value.columns.map((column) => html`<span class="col">${column.label}</span>`)}
          <span class="phrase">where it is</span><span class="age"></span></div>
        ${groups.map((view) => this.#group(view, value, many))}
        ${this.#closedFold(value)}
      </div></div>`;
  }

  #group(view: GroupView, value: Board, many: boolean): TemplateResult | typeof nothing {
    const rounds = view.rounds.filter((one) => isShown(one, this.#filter, this.#project));
    const unreadable = view.unreadable.filter((one) => isUnreadableShown(one, this.#filter, this.#project));
    if (rounds.length + unreadable.length === 0) return nothing;
    return html`<div class="group">${view.label}<span class="dim">${rounds.length + unreadable.length}</span></div>
      ${rounds.map((one) => this.#round(one, value, many))}
      ${unreadable.map((one) => html`<div class="line unreadable"><span class="name">${slugOf(one.name)}</span>
        <span class="phrase"><span class="w-bad">${UNREADABLE_LEAD}</span>
          <span class="dim">${unreadableWords(one)}</span></span></div>`)}`;
  }

  #round(round: Round, value: Board, many: boolean): TemplateResult {
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
        ${this.#stageCell(round, line, first)}${this.#rail(round, line, at, value.columns)}
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
    if (address === null || this.#anchor.kind !== "board") return false;
    if (address.kind === "step" && address.round === round.name) {
      const column = this.#feed?.value?.columns.find((one) => one.id === address.column);
      return column?.part === "stage" ? round.lines.find((one) => one.stage === address.stage) === line
                                      : line.kind === "round";
    }
    const item = address.kind === "walk" ? address.item : "id" in address ? address : null;
    return item !== null && item.holder === round.name && line.kind === "round";
  }

  /** A line's stage cell, beside its rail and never on it: the stage's id and branch, and
   * on the round line the `+N` that unfolds the round's other stages. */
  #stageCell(round: Round, line: Line, first: boolean): TemplateResult {
    const more = first ? moreLabel(round, this.#unfolded) : null;
    return html`<span class="stagecell">${line.stage ? html`<span class="stage">${line.stage}</span>
        <span class="branch">${line.branch ?? ""}</span>` : nothing}${more ? html`<button type="button" class="more"
        aria-label=${more.label} aria-expanded=${this.#unfolded.has(foldKey(round.name, "more")) ? "true" : "false"}
        @click=${() => this.#flip(foldKey(round.name, "more"))}>${more.text}</button>` : nothing}</span>`;
  }

  /** One line's rail: a mark per column and nothing else, so its line runs unbroken. */
  #rail(round: Round, line: Line, at: number, columns: readonly Column[]): TemplateResult {
    const openKey = this.#openKey(round, line);
    return html`<moxi-ui-rail data-round=${round.name} data-line=${at}
        @moxi-rail-mark=${(event: Event) => this.#onMark(event, round)}>${columns.map((column, i) => {
      const stage = stageOf(column, line);
      const cell = line.cells[i] ?? null;
      const key = markKey(column.id, stage);
      return html`<moxi-ui-mark ${attributes(
        markAttributes(column, stage, round.slug, cell, { open: key === openKey }))}></moxi-ui-mark>`;
    })}</moxi-ui-rail>`;
  }

  /** The key of the mark on this line whose popover is open, if any. */
  #openKey(round: Round, line: Line): string | null {
    const address = this.#address;
    if (address === null || this.#anchor.kind !== "board" || !this.#isOpenLine(round, line)) return null;
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
    return html`<div class="fold closedfold"><button type="button" aria-expanded=${open ? "true" : "false"}
        @click=${() => this.#flip(CLOSED_FOLD)}>${open ? "▾" : "▸"} ${closedFoldText(value.closed)}</button></div>
      ${open ? value.closed.map((one) => html`<div class="line closed">
        <span class="name"><button type="button" class="link" data-name=${one.name}
          @click=${() => this.#toggle({ kind: "round", round: one.name })}>${slugOf(one.name)}</button></span>
        <span class="stagecell"></span><moxi-ui-rail data-closed=${one.name}>${value.columns.map((column) => html`<moxi-ui-mark
          ${attributes({ state: "done", settled: "", quiet: "", label: `${column.label}: done` })}></moxi-ui-mark>`)}</moxi-ui-rail>
        <span class="phrase dim">${closedWords(one)}</span><span class="age"></span>
      </div>`) : nothing}`;
  }

  #foot(value: Board): TemplateResult {
    const feed = this.#feed!;
    const lines = [...footWords(value, feed.readAt, feed.failure?.why ?? null, value.today), ...this.#notes];
    const parked = parkedWords(value);
    // The parked questions' list opens from the foot at the desk only: the phone does not act.
    return html`<div class="foot">${lines.map((one) => (one === parked && !this.#phone
      ? html`<span><button type="button" class="linkish" aria-expanded=${this.#parkedOpen ? "true" : "false"}
          @click=${() => { this.#parkedOpen = !this.#parkedOpen; this.requestUpdate(); }}>${one}</button></span>`
      : html`<span>${one}</span>`))}
      ${this.#parkedOpen && !this.#phone ? this.#parkedList(value) : nothing}</div>`;
  }

  #parkedList(value: Board): TemplateResult {
    return html`<div class="parked" role="list" aria-label=${PARKED_HEAD}>
      ${parkedGroups(value, this.#order(value)).map((group) => html`<div class="ph">${group.holder}</div>
        ${group.rows.map((row) => {
          const key = `${group.holder} ${row.id}`;
          return html`<div role="listitem"><button type="button" class="link parkedrow" data-parked=${key}
              @click=${() => this.#open({ kind: "question", holder: group.holder, id: row.id },
                                        { anchor: { kind: "parked", key } })}>
            <span class="strong">${row.id}</span> ${row.text}</button>
            ${row.until ? html`<span class="dim">${row.until}</span>` : nothing}</div>`;
        })}`)}</div>`;
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
      body = html`${this.#tabs(address, value)}${stepSections(read.record, ctx).map((one) => this.#section(one))}
        ${this.#drop ? this.#dropConfirm(value) : nothing}`;
      const round = value.rounds.find((one) => one.name === address.round);
      const droppable = round && hasOpenBuild(round, value.columns, address.stage) && this.#drop === null;
      if (this.#anchor.kind === "strip" || droppable) {
        foot = html`${this.#anchor.kind === "strip" ? html`<button type="button" class="walkbtn"
            @click=${() => this.#showLine(address)}>${SHOW_LINE}</button>` : nothing}
          ${droppable ? html`<button type="button" class="btn" @click=${() => void this.#openDrop(address.round, address.stage!)}>
            Drop this build…</button>` : nothing}`;
      }
    } else if (read?.kind === "record" && address.kind === "round") {
      const card = cardView(read.record, ctx);
      head = { title: card.title, sub: card.sub, cell: null };
      body = html`${card.sections.map((one) => this.#section(one))}`;
    } else if (read?.kind === "record") {
      const view = itemView(read.record, ctx);
      const item = this.#itemOf(address)!;
      head = { title: view.title, sub: view.sub, cell: { ...DONE, state: "yours", settled: false } };
      body = html`<div class="question">${view.text}</div>
        ${view.lines.map((one) => html`<div class="dim">${one}</div>`)}
        ${this.#itemBody(item, read.record, view, value)}
        ${view.passage ? this.#passage(view.passage) : nothing}`;
      foot = this.#itemFoot(item, read.record, view, value);
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

  // --- acting ------------------------------------------------------------------------------

  #reach(value: Board): Reach {
    return { comms: this.#hasComms(), to: this.#to, leadOf: leadsKnown(leadOf(value), this.#tray.drafts),
             fromProject: leadFromProject(value) };
  }

  /** Whether the page server sends through comms: what `/api/agents` said, else whether
   * the shell lists comms. */
  #hasComms(): boolean {
    return this.#comms ?? installed("comms");
  }

  /** Whether the open popover is an item's with a form to answer it; none at phone width. */
  #answering(): boolean {
    const item = this.#itemOf(this.#address);
    const read = this.#record?.read;
    return !this.#phone && item !== null && read?.kind === "record" && this.#answerable(item, read.record);
  }

  /** Whether the item's popover offers its form now (`isAnswerable`). */
  #answerable(item: ItemRef, record: RecordValue): boolean {
    return isAnswerable(item.kind, record.item?.state ?? "open", this.#tray.of(item.holder, item.id),
                        { fresh: this.#record?.seen === this.#acts });
  }

  /** Enter adds the answer to the tray; an option's key, or 1 to 4, picks it. */
  #onAnswerKey(event: KeyboardEvent, target: HTMLElement | undefined): boolean {
    if (target?.tagName === "BUTTON") return false;
    const read = this.#record?.read;
    const item = this.#itemOf(this.#address);
    if (!item || read?.kind !== "record") return false;
    if (event.key === "Enter") {
      event.preventDefault();
      this.#add(item, read.record);
      return true;
    }
    const options = item.kind === "question" && read.record.item?.state !== "parked" ? read.record.item?.options ?? [] : [];
    const at = /^[1-4]$/.test(event.key) ? Number(event.key) - 1 : -1;
    const option = options.find((one) => one.key === event.key) ?? options[at];
    if (!option) return false;
    event.preventDefault();
    this.#form = { ...this.#form, choice: { kind: "pick", chose: option.key } };
    this.requestUpdate();
    return true;
  }

  /** The draft the open form makes; null when it holds no answer. */
  #formDraft(item: ItemRef, record: RecordValue): Draft | null {
    const form = this.#form;
    const about = record.item?.text ?? "";
    const text = form.text.trim();
    if (form.correcting) return text ? draftOf(item.holder, form.correcting, "correct", { text, about }) : null;
    if (item.kind === "question" && record.item?.state === "parked") return draftOf(item.holder, item.id, "unpark", { about });
    if (form.choice) {
      return draftOf(item.holder, item.id, form.choice.kind, {
        chose: form.choice.chose, about, text: form.choice.kind === "done" && this.#hasComms() ? text : null });
    }
    if (item.kind === "question" && text) return draftOf(item.holder, item.id, "words", { text, about });
    return null;
  }

  /** Adds the form's answer to the tray; in the walk, moves on to the next item. */
  #add(item: ItemRef, record: RecordValue, draft: Draft | null = this.#formDraft(item, record)): Draft | null {
    if (draft === null) return null;
    this.#tray.add(draft);
    this.#form = { ...EMPTY_FORM };
    if (this.#address?.kind === "walk" && this.#sequenceAt() < this.#sequence.length - 1) this.#walk(1);
    this.requestUpdate();
    return draft;
  }

  async #sendNow(item: ItemRef, record: RecordValue, draft: Draft | null = this.#formDraft(item, record)): Promise<void> {
    if (draft === null) return;
    this.#tray.add(draft);
    this.#form = { ...EMPTY_FORM };
    await this.#send([draft]);
  }

  /** Posts drafts to the answers route with the To choice, in as many posts as its caps
   * need, and takes the receipts. A draft over a cap of its own stays in the tray with
   * its row saying why. A post that gets no answer leaves its drafts and the ones after
   * it as they were: their act ids make a second try safe. */
  async #send(drafts: readonly Draft[]): Promise<void> {
    if (this.#sending || drafts.length === 0) return;
    this.#sending = true;
    this.#sendNote = null;
    this.requestUpdate();
    try {
      const going = this.#tray.toSend(drafts.map((one) => one.act));
      for (const post of postsOf(going, this.#to)) {
        const answer = await api(MODULE, "api/answers", bodyOf(post, this.#to));
        const receipts = answer.data?.receipts;
        if (answer.status !== 200 || !Array.isArray(receipts)) {
          this.#sendNote = `Not sent: ${failureWhy(answer.status)}. The answers stay in the tray; Send tries them again.`;
          break;
        }
        this.#tray.take(receipts, Array.isArray(answer.data.messages) ? answer.data.messages : []);
        this.#readAnswered(post);
      }
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  /** When a send answered the open item, reads its record again, and offers its form
   * only from that read on. */
  #readAnswered(sent: readonly Draft[]): void {
    const item = this.#itemOf(this.#address);
    if (item === null || !sent.some((one) => one.holder === item.holder && one.item === item.id)) return;
    this.#acts += 1;
    void this.#readRecord(this.#address!);
  }

  async #readAgents(): Promise<void> {
    const answer = await api(MODULE, "api/agents");
    const names = answer.data?.agents;
    this.#agents = answer.status === 200 && Array.isArray(names) ? names.filter((one) => typeof one === "string") : [];
    this.#agentsWhy = answer.status === 200 ? answer.data?.reason ?? null : failureWhy(answer.status);
    if (answer.status === 200 && typeof answer.data?.comms === "boolean") this.#comms = answer.data.comms;
    this.requestUpdate();
  }

  /** The To picker: each round's lead, or one agent comms knows. */
  #picker(value: Board): TemplateResult | typeof nothing {
    if (!this.#hasComms()) return nothing;
    const names = [...new Set([...(this.#agents ?? []), ...(this.#to === EACH ? [] : [this.#to])])];
    const notLed = this.#to === EACH ? [] : [...new Set(this.#tray.drafts.map((one) => one.holder))]
      .filter((holder) => this.#reach(value).leadOf(holder) !== this.#to);
    return html`<label>To <select aria-label="Send to" @focus=${() => void this.#readAgents()} @change=${(event: Event) => {
        this.#to = (event.target as HTMLSelectElement).value;
        this.requestUpdate();
      }}><option value=${EACH} ?selected=${this.#to === EACH}>${eachLeadWords(this.#tray.drafts, this.#reach(value))}</option>
      ${names.map((name) => html`<option value=${name} ?selected=${this.#to === name}>${name}</option>`)}</select></label>
      ${this.#agentsWhy ? html`<span class="dim">${this.#agentsWhy}</span>` : nothing}
      ${notLed.length ? html`<span class="dim">${this.#to} does not lead ${notLed.join(", ")}</span>` : nothing}`;
  }

  #trayView(value: Board): TemplateResult | typeof nothing {
    const tray = this.#tray;
    if (tray.drafts.length + tray.sent.length === 0 && this.#sendNote === null) return nothing;
    const reach = this.#reach(value);
    const going = sendable(tray.drafts, reach);
    return html`<section class="tray" aria-label="Your answers">
      <div><h2>Your answers</h2> <span class="dim">${tray.drafts.length}</span></div>
      <div class="rule">${reach.comms ? RULE_LINE : RULE_LINE_ALONE}</div>
      ${tray.drafts.map((draft) => this.#trayRow(draft, value, reach))}
      <div class="sendrow">${this.#picker(value)}<span class="grow"></span>
        <button type="button" class="btn accent" ?disabled=${this.#sending || going.length === 0}
          @click=${() => void this.#send(going)}>${sendWords(tray.drafts, reach)}</button></div>
      ${this.#sendNote ? html`<div class="t-failed">${this.#sendNote}</div>` : nothing}
      ${tray.sent.length + tray.value.messages.length ? html`<div><span class="strong">Sent</span>
        <button type="button" class="linkish" @click=${() => { tray.clearSent(); this.requestUpdate(); }}>Clear</button></div>
        ${tray.sent.map((draft) => html`<div class="trow" data-act=${draft.act}>
          <div class="top"><span class="strong">${draft.holder} ${draft.item}</span><span>${kindWords(draft)}</span>
            <span class="dim">${draft.receipt?.state ?? ""}</span></div>
          ${this.#receiptView(draft, value)}</div>`)}
        ${tray.value.messages.length ? html`<div class="messages" aria-label="Messages sent">${tray.value.messages.map(
          (one) => html`<div class=${one.refused ? "t-failed" : "dim"}>${messageWords(one, { today: value.today })}</div>`)}
        </div>` : nothing}` : nothing}
    </section>`;
  }

  #trayRow(draft: Draft, value: Board, reach: Reach): TemplateResult {
    const tray = this.#tray;
    const notTold = draft.receipt?.state === "recorded-not-told";
    const editing = this.#editing === draft.act;
    const to = recipientWords(draft, reach);
    const over = overCap(draft);
    const sendsAlone = over === null && (reach.comms ? to !== null : isPageKind(draft.kind));
    return html`<div class="trow" data-act=${draft.act}>
      <div class="top"><span class="strong">${draft.holder} ${draft.item}</span><span>${kindWords(draft)}</span>
        ${draft.text && !editing ? html`<span>"${draft.text}"</span>` : nothing}</div>
      ${editing ? html`<textarea aria-label="Edit your words" .value=${draft.text ?? ""} @change=${(event: Event) => {
          tray.edit(draft.act, (event.target as HTMLTextAreaElement).value);
          this.#editing = null;
          this.requestUpdate();
        }}></textarea>` : nothing}
      <div class="chipline">${chipOf(draft, reach)}</div>
      ${over ? html`<div class="t-failed">${over}</div>` : nothing}
      ${draft.receipt ? this.#receiptView(draft, value) : nothing}
      <div class="acts">
        ${draft.text !== null && !notTold ? html`<button type="button" class="linkish"
          @click=${() => { this.#editing = editing ? null : draft.act; this.requestUpdate(); }}>Edit</button>` : nothing}
        ${notTold ? html`<button type="button" class="btn" ?disabled=${this.#sending} @click=${() => void this.#send([draft])}>
          Send again</button>` : sendsAlone ? html`<button type="button" class="linkish" ?disabled=${this.#sending}
          @click=${() => void this.#send([draft])}>${reach.comms ? `Send now to ${to}` : "Record now"}</button>`
          : nothing}
        ${notTold ? nothing : html`<button type="button" class="linkish" @click=${() => { tray.remove(draft.act); this.requestUpdate(); }}>
          Remove</button>`}
      </div></div>`;
  }

  /** A receipt's lines, with what they offer: Send again, and the read-back. */
  #receiptView(draft: Draft, value: Board): TemplateResult {
    const lines = receiptLines(draft, { today: value.today });
    const line = (one: ReceiptLine) => html`<div class="${one.mono ? "mono well" : ""} ${one.tone ? `t-${one.tone}` : ""}">
      ${one.text}</div>${one.offer === "send-again" ? html`<div class="acts"><button type="button" class="btn"
        ?disabled=${this.#sending} @click=${() => void this.#send([draft])}>Send again</button></div>` : nothing}
      ${one.offer === "read-back" ? this.#readBack(draft) : nothing}`;
    return html`<div class="receipt">${lines.map(line)}</div>`;
  }

  /** `That's what I said` and `Not what I said…` on a recorded answer. */
  #readBack(draft: Draft): TemplateResult | typeof nothing {
    const ruling = readBackOf(draft);
    if (ruling === null) return nothing;
    const said = this.#tray.of(draft.holder, ruling);
    // A read-back recorded, told or not, is not offered again.
    if (said?.kind === "read-back" && ["recorded", "recorded-not-told"].includes(said.receipt?.state ?? "")) {
      return nothing;
    }
    const correcting = this.#form.correcting === ruling;
    return html`<div class="acts">
      <button type="button" class="btn" ?disabled=${this.#sending}
        @click=${() => void this.#send([this.#addDraft(draftOf(draft.holder, ruling, "read-back", { about: draft.about }))])}>
        That's what I said</button>
      <button type="button" class="btn" aria-expanded=${correcting ? "true" : "false"} @click=${() => {
          this.#form = { ...EMPTY_FORM, correcting: correcting ? null : ruling };
          this.requestUpdate();
        }}>Not what I said…</button></div>
      ${correcting ? html`<textarea aria-label="What you said" .value=${this.#form.text} @input=${(event: Event) => {
          this.#form = { ...this.#form, text: (event.target as HTMLTextAreaElement).value };
        }}></textarea><div class="acts"><button type="button" class="btn accent" @click=${() => {
          const text = this.#form.text.trim();
          if (!text) return;
          this.#addDraft(draftOf(draft.holder, ruling, "correct", { text, about: draft.about }));
          this.#form = { ...EMPTY_FORM };
        }}>Add to answers</button></div>` : nothing}`;
  }

  #addDraft(draft: Draft): Draft {
    this.#tray.add(draft);
    this.requestUpdate();
    return draft;
  }

  /** An item's popover body: a question's prepared answer and its options to pick, or a
   * parked question's parking. */
  #itemBody(item: ItemRef, record: RecordValue, view: ItemView, value: Board): TemplateResult {
    const parked = item.kind === "question" ? parkingWords(record, value.today) : "";
    const lead = leadOf(value)(item.holder);
    const prepared = item.kind === "question" ? preparedView(record.item?.prepared, lead) : null;
    const open = item.kind === "question" && record.item?.state !== "parked"
      && (record.item?.state ?? "open") === "open";
    const chosen = this.#form.choice?.kind === "pick" ? this.#form.choice.chose : null;
    return html`${parked ? html`<div>${parked}</div>${record.ruling?.record ? html`<div class="mono dim">
        ${record.ruling.record}</div>` : nothing}` : nothing}
      ${prepared ? html`<div class="dim">${prepared.line}</div>` : nothing}
      ${view.options.map((one) => open && item.kind === "question"
        ? html`<button type="button" class="option pick" aria-pressed=${chosen === one.key ? "true" : "false"}
            @click=${() => { this.#form = { ...this.#form, choice: { kind: "pick", chose: one.key } }; this.requestUpdate(); }}>
            <span class="k">${one.key}</span><span>${one.text}</span>
            ${prepared?.recommend === one.key ? html`<span class="rec">${RECOMMENDED}</span>` : nothing}</button>`
        : html`<div class="option"><span class="k">${one.key}</span><span>${one.text}</span>
            ${prepared?.recommend === one.key ? html`<span class="rec">${RECOMMENDED}</span>` : nothing}</div>`)}`;
  }

  /** An item's popover foot: the tray's row or receipt for it, then the form that answers
   * it, by kind. */
  #itemFoot(item: ItemRef, record: RecordValue, view: ItemView, value: Board): TemplateResult {
    const reach = this.#reach(value);
    const comms = reach.comms;
    const row = this.#tray.of(item.holder, item.id);
    const state = record.item?.state ?? "open";
    const answerable = this.#answerable(item, record);
    const choose = (kind: Kind, label: string) => html`<button type="button" class="btn"
        aria-pressed=${this.#form.choice?.kind === kind ? "true" : "false"}
        @click=${() => { this.#form = { ...this.#form, choice: { kind, chose: null } }; this.requestUpdate(); }}>${label}</button>`;
    const box = (label: string) => html`<textarea aria-label=${label} placeholder=${label} .value=${this.#form.text}
        @input=${(event: Event) => { this.#form = { ...this.#form, text: (event.target as HTMLTextAreaElement).value };
                                     this.requestUpdate(); }}></textarea>`;
    let form: TemplateResult | typeof nothing = nothing;
    if (answerable && item.kind === "question" && state === "parked") {
      form = html`<div class="acts"><span>Unpark takes the parking back: the question is open again.</span></div>`;
    } else if (answerable && item.kind === "question") {
      form = box("Or answer in your own words");
    } else if (answerable && item.kind === "decision") {
      form = html`<div class="acts">${choose("accept", "Accept")}${choose("reject", "Reject")}</div>`;
    } else if (answerable && item.kind === "shown") {
      form = html`<div class="acts">${choose("read-back", "That's what I said")}<button type="button" class="btn"
          aria-expanded=${this.#form.correcting ? "true" : "false"} @click=${() => {
            this.#form = { ...EMPTY_FORM, correcting: this.#form.correcting ? null : item.id };
            this.requestUpdate();
          }}>Not what I said…</button></div>${this.#form.correcting ? box("What you said") : nothing}`;
    } else if (answerable && item.kind === "wait") {
      form = html`<div class="acts">${choose("done", "I've done it")}</div>${comms && this.#form.choice?.kind === "done"
        ? box("A note for the lead (optional)") : nothing}`;
    }
    const draft = answerable ? this.#formDraft(item, record) : null;
    const to = draft ? recipientWords(draft, reach) : null;
    const canNow = draft !== null && (comms ? to !== null : isPageKind(draft.kind));
    const parkedNow = item.kind === "question" && state === "parked";
    const acts = answerable ? html`<div class="acts">
        <button type="button" class="btn accent" ?disabled=${draft === null} @click=${() => this.#add(item, record)}>
          ${parkedNow ? "Unpark" : "Add to answers"}</button>
        ${canNow ? html`<button type="button" class="btn" ?disabled=${this.#sending}
          @click=${() => void this.#sendNow(item, record)}>${comms ? `Send now to ${to}` : "Record now"}</button>`
          : nothing}
        ${item.kind === "question" && state === "open" ? html`${comms ? html`<button type="button" class="btn"
            @click=${() => this.#add(item, record, draftOf(item.holder, item.id, "explain",
                                                            { text: this.#form.text, about: view.text }))}>
            Ask the lead to explain</button>` : nothing}
          <button type="button" class="btn" @click=${() => this.#add(item, record, draftOf(item.holder, item.id, "park",
                                                                                          { about: view.text }))}>
            Park</button>` : nothing}
      </div>` : nothing;
    const inTray = row && !row.receipt ? html`<div class="receipt"><span>In your answers: ${kindWords(row)}${row.text
        ? ` "${row.text}"` : ""}</span><span class="dim">${chipOf(row, reach)}</span></div>` : nothing;
    return html`${inTray}${row?.receipt ? this.#receiptView(row, value) : nothing}
      ${row?.receipt?.state === "kept" ? this.#keptActs(row, value, reach) : nothing}
      ${answerable ? html`<div class="answer">${form}</div>${acts}` : nothing}`;
  }

  /** A kept draft's To picker in its popover, and `Send now` once it has a recipient. */
  #keptActs(row: Draft, value: Board, reach: Reach): TemplateResult {
    const to = recipientWords(row, reach);
    return html`<div class="sendrow">${this.#picker(value)}${to !== null && overCap(row) === null
      ? html`<button type="button" class="btn" ?disabled=${this.#sending} @click=${() => void this.#send([row])}>
          Send now to ${to}</button>` : nothing}</div>`;
  }

  async #openDrop(round: string, stage: string): Promise<void> {
    const act = mintId();
    this.#drop = { round, stage, act, facts: null, why: null, receipt: null, busy: false, lost: null };
    this.requestUpdate();
    const enc = encodeURIComponent;
    const answer = await api(MODULE, `api/drop?round=${enc(round)}&stage=${enc(stage)}`);
    if (this.#drop?.act !== act) return;
    if (answer.status === 200 && answer.data) this.#drop = { ...this.#drop, facts: answer.data as DropFacts };
    else this.#drop = { ...this.#drop, why: failureWhy(answer.status) };
    this.#placed = false;
    this.requestUpdate();
  }

  async #dropNow(): Promise<void> {
    const drop = this.#drop;
    if (drop === null || drop.busy) return;
    this.#drop = { ...drop, busy: true };
    this.requestUpdate();
    const answer = await api(MODULE, "api/drop/build", { round: drop.round, stage: drop.stage, act: drop.act });
    if (this.#drop?.act !== drop.act) return;
    if (answer.status !== 200 || !answer.data) {
      // The drop may have run: Drop again posts the same act id, which runs it at most once.
      this.#drop = { ...this.#drop, busy: false, lost: failureWhy(answer.status) };
      this.requestUpdate();
      return;
    }
    const receipt = answer.data as Receipt;
    this.#drop = { ...this.#drop, busy: false, lost: null, receipt };
    if (receipt.state === "refused") void this.#feed?.refresh();
    this.requestUpdate();
  }

  #dropConfirm(value: Board): TemplateResult {
    const drop = this.#drop!;
    const keep = html`<button type="button" class="btn" @click=${() => { this.#drop = null; this.requestUpdate(); }}>
      ${drop.receipt ? "Close" : "Keep it"}</button>`;
    if (drop.receipt) {
      return html`<div class="confirm" role="status">${dropReceipt(drop.receipt, value.today, value.home)
        .map((one) => html`<div>${one}</div>`)}<div class="acts">${keep}</div></div>`;
    }
    if (drop.why) return html`<div class="confirm"><div class="t-failed">${drop.why}</div><div class="acts">${keep}</div></div>`;
    if (drop.facts === null) return html`<div class="confirm dim">${READING}</div>`;
    if (!drop.facts.open) {
      return html`<div class="confirm"><div>${drop.facts.reason ?? "No open build to drop."}</div>
        <div class="acts">${keep}</div></div>`;
    }
    return html`<div class="confirm" role="group" aria-label="Drop ${drop.stage}'s build">
      ${dropLines(drop.facts, value.today, value.home).map((one) => html`<div class="sec"><h3>${one.head}</h3>
        ${one.rows.map((row) => html`<div class="row">${row}</div>`)}</div>`)}
      ${drop.lost ? html`<div class="t-failed">The drop got no answer (${drop.lost}), so it may or may not have run.
        Drop again sends the same drop once more, and it runs at most once.</div>` : nothing}
      <div class="acts"><button type="button" class="btn" ?disabled=${drop.busy}
        @click=${() => void this.#dropNow()}>${drop.lost ? "Drop again" : `Drop ${drop.stage}'s build`}</button>${keep}</div></div>`;
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

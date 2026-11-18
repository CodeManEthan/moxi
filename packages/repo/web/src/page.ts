// <moxi-repo-page route="...">: repo's Git hooks page. Under the shell's top line, from top
// to bottom, each group with its label in the left column:
//
// - `Hooks`: the verdict and `read <time>` at its right, then each thing that needs him or
//   warns him, in three lines (what, where, the fix with `Copy` or one sentence), past five
//   items the warnings folded to one line with `Show`. An item made from a record has
//   `Show` for the record's whole text.
// - `Repos`: one `<moxi-ui-row>` per repo, its pieces in run order, heavy words at the
//   right of one in trouble, its news on lines two and three, `Open` in the row's
//   `acts-one` slot; then, with projects, the dim line of projects with no hooks.
// - `Pieces`: how many pieces installed modules offer, folded to one line with `Show`, and
//   each declaration that can't be offered.
//
// The models (`items-model.ts`, `rows-model.ts`, `pieces-model.ts`, `frame-model.ts`)
// give every word; this file places them. No accent: bad news rises by its order and
// weight 600.
//
// The address: the route opens the page (`""`), a repo (`repo/<name>`), its `Add a piece`
// list (`repo/<name>/add`), a project's first repo (`project/<key>`), `Add a repo` (`add`)
// or `Pieces` shown (`pieces`). A route is opened once the repos are read; a name that is
// no repo opens the page with one dim line at its foot. One thing is open at a time, and
// the page rewrites the browser's address with `history.replaceState` whenever what is
// open changes.
//
// The states: nothing for 400 ms, then `Reading…`; a read that fails, its first line and
// the last value dim with no acts; the listing's error, its detail in the verdict's
// place; a 404, nothing drawn.
//
// It reads `/api/repos` when it opens, when its tab is shown again, and every 60 seconds
// while shown.

import { css, html, LitElement, noChange, nothing } from "lit";
import { Directive, directive, PartType } from "lit/directive.js";
import type { ElementPart, PartInfo } from "lit/directive.js";
import { repeat } from "lit/directives/repeat.js";
import { api, navigate } from "/m/ui/client.js";

import {
  actRequest, addRows, answerView, confirmView, folderLine, planFailWords, planPath, refreshCommand, rowAddTitle, transportView,
} from "./acts-model.ts";
import type { Act, AnswerView, ConfirmView } from "./acts-model.ts";
import { Feed, parseAnswer, parsePlanned } from "./feed.ts";
import type { Command, Plan, RecordRow, Repos } from "./feed.ts";
import {
  NOTHING_OPEN, noSuchRepo, pageState, parseRoute, pathToWrite, READING, READING_AFTER_MS, REFRESH_MS, rowAddress,
  rowNamed, SERVER_ERROR,
} from "./frame-model.ts";
import type { Address, PageState } from "./frame-model.ts";
import { foldedLine, itemsOf, timeWords, verdict } from "./items-model.ts";
import type { Item, Verdict } from "./items-model.ts";
import { openedView, turnOffView } from "./opened-model.ts";
import type { AutoView, EventView, OpenedView, PieceLine, RulesView, TodayLine } from "./opened-model.ts";
import { piecesView } from "./pieces-model.ts";
import type { PiecesView } from "./pieces-model.ts";
import { noHooksActs, reposView } from "./rows-model.ts";
import type { ReposView, RowView } from "./rows-model.ts";
import { offeredOrder } from "./pieces-model.ts";
import { homeForm, LOCAL } from "./words.ts";

const TAG = "moxi-repo-page";

/** The words the page draws itself. */
const HOOKS = "Hooks";
const REPOS = "Repos";
const PIECES = "Pieces";
const OPEN = "Open";
const CLOSE = "Close";
const SHOW = "Show";
const HIDE = "Hide";
const COPY = "Copy";
const COPIED = "Copied";
const TURN_OFF = "Turn off";
const OR_REMOVE = "Or remove";
const SETTINGS = "Settings";
const TODAY = "Today";
const AUTO_COMMIT = "Auto-commit";
const RULES = "Rules";
const GIT = "Git";
const COMMANDS = "Commands";
const SETTINGS_LINK = "Settings › repo";
const ADD = "Add";
const ADD_A_PIECE = "Add a piece";
const REFRESH = "Refresh";
const TURN_ON = "Turn on";
const CANCEL = "Cancel";
const WRITES = "Writes";
const THEN = "Then";
const LEAVES = "Leaves";
/** `Add a piece` with every offered piece already in the repo. */
const NONE_TO_ADD = "Every piece offered is here already.";
const ADD_A_REPO = "Add a repo";
const FOLDER = "Folder";
/** How long after typing stops the folder's plan is read, in milliseconds. */
const FOLDER_MS = 400;
/** How long a `Copy` reads `Copied`, in milliseconds. */
const COPIED_MS = 1500;

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

const flag = (on: boolean): string | null => (on ? "" : null);

const STYLES = css`
  :host { display: block; min-width: 0; max-width: 680px; margin: 0 auto; padding: 4px 0 16px;
          color: var(--moxi-text); font-family: var(--moxi-font-sans); font-size: 14px; line-height: 20px; }
  /* The accent is the focus ring alone, drawn only on a focused piece. */
  * { box-sizing: border-box; outline-color: var(--moxi-accent); }
  p { margin: 0; overflow-wrap: anywhere; }
  button { font: inherit; color: inherit; background: none; border: 0; padding: 0; margin: 0; cursor: pointer;
           text-align: left; }
  button:disabled { cursor: not-allowed; color: var(--moxi-text-dim); }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  code { font: 12px/18px var(--moxi-font-mono); color: var(--moxi-text); overflow-wrap: anywhere; }
  pre { font: 12px/18px var(--moxi-font-mono); color: var(--moxi-text); margin: 4px 0 2px; padding: 7px 10px;
        border: 1px solid var(--moxi-border); border-radius: 7px; white-space: pre-wrap; overflow-wrap: anywhere; }
  .d { color: var(--moxi-text-dim); }
  .h { font-weight: 600; }
  .sm { font-size: 13px; line-height: 18px; }

  p.state, p.foot { margin: 16px 0 0; padding: 0 2px; overflow-wrap: anywhere; }
  p.state.dim, p.foot { color: var(--moxi-text-dim); }
  .grps { margin-top: 16px; border-top: 1px solid var(--moxi-border); }
  /* The last value kept while the repos can't be read: every word dim, the rows' own
     included, since each row draws its words in the text token. */
  .grps.kept { --moxi-text: var(--moxi-text-dim); color: var(--moxi-text-dim); }
  .grp { display: grid; grid-template-columns: 96px minmax(0, 1fr); border-bottom: 1px solid var(--moxi-border);
         padding: 12px 2px; }
  .grp.rows { padding: 5px 2px; }
  .grp .gk { color: var(--moxi-text-dim); }
  .grp.rows .gk { padding-top: 7px; }
  .grp .gk small { display: block; font-size: 13px; line-height: 18px; margin-top: 3px; }
  .grp .gk small button { color: var(--moxi-text); }
  .grp .gl { grid-column: 2; min-width: 0; }

  .line { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; min-width: 0; }
  .line > :first-child { min-width: 0; overflow-wrap: anywhere; }
  .line > .r { flex: none; text-align: right; font-variant-numeric: tabular-nums; }
  .act { flex: none; font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  .act.on { color: var(--moxi-text); }

  .prob { padding-top: 14px; }
  .cmd { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; }
  .cmd .lbl { margin-right: 8px; }

  moxi-ui-row > button.do { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  moxi-ui-row[open] + moxi-ui-row { border-top: 1px solid var(--moxi-border); padding-top: 3px; }
  .one { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; padding: 7px 0;
         color: var(--moxi-text-dim); }
  .one button { flex: none; color: var(--moxi-text-dim); }

  /* The opened repo: one labelled row each, the label dim at the left, an act at the
     right; a line's first phrase in the text's colour and the rest dim. */
  .det { min-width: 0; margin: 9px 0 3px; padding-top: 8px; border-top: 1px solid var(--moxi-border);
         font-size: 13px; line-height: 18px; cursor: auto; }
  .det .r { display: grid; grid-template-columns: 118px minmax(0, 1fr); padding: 3px 0; align-items: baseline; }
  .det .r > .k { color: var(--moxi-text-dim); }
  .det .r > .f { min-width: 0; }
  .det .line + .line, .det .f > .sub + .line { margin-top: 3px; }
  .det .sub { display: block; color: var(--moxi-text-dim); overflow-wrap: anywhere; }
  .det .tm { display: inline-block; min-width: 46px; font-variant-numeric: tabular-nums; }
  .det .foot { display: flex; flex-wrap: wrap; gap: 4px 18px; padding: 9px 0 0 118px; }
  .det .foot button { color: var(--moxi-text-dim); }
  .det .foot button.on { color: var(--moxi-text); }
  .det .cmds { padding: 6px 0 0 118px; }
  .det .cmds .cmd + .cmd { margin-top: 4px; }
  /* Turn off: the one block in the warning colour, its Copy buttons too. */
  .off { margin: 6px 0 4px; padding: 8px 0 8px 12px; border-left: 2px solid var(--moxi-warn); color: var(--moxi-warn); }
  .off p + p, .off .cmd { margin-top: 4px; }
  .off .q { font-weight: 600; font-size: 14px; line-height: 20px; }
  .off code { color: var(--moxi-warn); }
  .off .act { color: var(--moxi-warn); }
  .pc { padding-top: 10px; }
  .pc .d.git { margin-left: 6px; }
  .said { padding-top: 10px; color: var(--moxi-text-dim); }
  .cmd .acts { flex: none; display: inline-flex; gap: 14px; }
  moxi-ui-row > button.do.on { color: var(--moxi-text); }

  /* The acts in place: the question heavy, each line under its label, the button and
     Cancel; the answer under the question. No accent and no warning colour. */
  .cf { margin: 10px 0 4px; border-top: 1px solid var(--moxi-border); padding-top: 10px; }
  .prob .cf { margin-top: 8px; }
  .cf .q { font-weight: 600; padding-bottom: 4px; }
  .cf .cr { display: grid; grid-template-columns: 76px minmax(0, 1fr) auto; padding: 3px 0; align-items: baseline;
            font-size: 13px; line-height: 18px; }
  .det .cf .cr { grid-template-columns: 118px minmax(0, 1fr) auto; }
  .cf .cr > .k { color: var(--moxi-text-dim); }
  .cf .cr > .f { min-width: 0; overflow-wrap: anywhere; }
  .cf .cr > .x { padding-left: 18px; }
  .cf .cr .cmd { margin-top: 2px; }
  .cf .btns { display: flex; gap: 18px; align-items: center; padding: 10px 0 2px; }
  button.btn { color: var(--moxi-text); border: 1px solid var(--moxi-border-strong); border-radius: 7px; padding: 5px 13px; }
  button.btn:disabled { color: var(--moxi-text-dim); border-color: var(--moxi-border); }
  button.lnk { color: var(--moxi-text-dim); }
  .ans { padding: 2px 0; font-size: 13px; line-height: 18px; }
  /* Add a repo: at the top of Repos, closed off from the rows under it. */
  .cf.add { border-top: 0; margin: 4px 0 4px; padding: 4px 0 10px; border-bottom: 1px solid var(--moxi-border); }
  .cf .sub { display: block; color: var(--moxi-text-dim); overflow-wrap: anywhere; margin-top: 3px; }
  input.fld { font: 12px/18px var(--moxi-font-mono); color: var(--moxi-text); background: none; width: 250px;
              max-width: 100%; border: 1px solid var(--moxi-border-strong); border-radius: 7px; padding: 4px 9px; }
  .tick { display: inline-flex; align-items: baseline; gap: 6px; margin-right: 16px; white-space: nowrap; }
  button.sw { position: relative; flex: none; width: 23px; height: 13px; border-radius: 7px; align-self: center;
              border: 1px solid var(--moxi-text-dim); }
  button.sw::after { content: ""; position: absolute; top: 2px; left: 2px; width: 7px; height: 7px; border-radius: 50%;
                     background: var(--moxi-text-dim); }
  button.sw[aria-checked="true"] { background: var(--moxi-text); border-color: var(--moxi-text); }
  button.sw[aria-checked="true"]::after { left: 12px; background: var(--moxi-bg); }
  .one .t { display: inline-flex; flex-wrap: wrap; justify-content: flex-end; gap: 4px 14px; }
  .ans > * + * { margin-top: 3px; }

  @media (max-width: 720px) {
    :host { padding-top: 10px; }
    .det .r { grid-template-columns: minmax(0, 1fr); }
    .det .foot, .det .cmds { padding-left: 0; }
    .det .cf .cr { grid-template-columns: 76px minmax(0, 1fr) auto; }
    .grp { display: block; padding: 8px 0 10px; }
    .grp.rows { padding: 4px 0 6px; }
    /* The label above its lines, its act beside it at the right. */
    .grp .gk { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; font-size: 13px; }
    .grp.rows .gk { padding-top: 9px; }
    .grp .gk small { margin-top: 0; }
    .grp:not(.rows) .gk { padding-bottom: 4px; }
  }
`;

/** An act in place: its confirm, drawn from the plan read as it opens, then its answer. */
interface Acting {
  act: Act;
  /** The repo's root, or the folder typed in `Add a repo`. */
  root: string;
  /** The pieces picked or turned on, by id. */
  picked: string[];
  /** Where it is drawn: `foot:<root>` under an opened repo's foot, `item:<key>` under an
   * item in `Hooks`, `row:<root>` under a closed row, or `add` in `Add a repo`. */
  at: string;
  /** The repo's name in its question. */
  repo: string;
  plan: Plan | null;
  /** Why the plan can't be read. */
  failed: string | null;
  /** From press to answer. */
  sending: boolean;
  /** The answer's outcome once answered, `unread` when no readable answer came. */
  outcome: string | null;
  answer: AnswerView | null;
}

/** Where an act goes when the place it opened at is no longer drawn: `Hooks`' foot. */
const HOOKS_END = "hooks";

/** What the page draws from one value: the items and verdict, the rows and `Pieces`. */
interface Drawn {
  value: Repos;
  items: Item[];
  verdict: Verdict;
  repos: ReposView;
  pieces: PiecesView;
}

export class RepoPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";

  #feed: Feed | null = null;
  /** The drawing of the value last read, made once per value. */
  #drawn: Drawn | null = null;
  /** The route given, opened once the repos are read; null once it is. */
  #pending: Address | null = null;
  /** The key (root) of the repo that is open. */
  #open: string | null = null;
  /** The open repo's `Add a piece` list is shown. */
  #openAdd = false;
  /** `Add a repo` is open. */
  #adding = false;
  /** `Add a repo`'s folder, as typed. */
  #folder = "";
  /** The pieces switched on in `Add a repo`, in run order. */
  #addPicked: string[] = [];
  /** `Pieces` is shown. */
  #piecesShown = false;
  /** The warnings folded under the verdict are shown. */
  #foldShown = false;
  /** The items whose record's whole text is shown, by item key. */
  #recordsShown = new Set<string>();
  /** The pieces whose settings are shown, by `<root>\n<id>`. */
  #settingsShown = new Set<string>();
  /** The opened repo's `Rules` lines are shown. */
  #rulesShown = false;
  /** The opened repo's `Commands` are shown. */
  #commandsShown = false;
  /** The piece whose `Turn off` block is open, as `<root>\n<id>`. */
  #turnOff: string | null = null;
  /** The act open in place, or null. */
  #acting: Acting | null = null;
  /** The pieces picked in the open repo's `Add a piece`, in run order. */
  #picked: string[] = [];
  /** An address that named no repo: the dim line at the foot. */
  #unknown: string | null = null;
  /** The opened row takes the view once drawn. */
  #scrollTo = false;
  /** Which `Copy` reads `Copied`. */
  #copied: string | null = null;
  #since = Date.now();
  #readingTimer: ReturnType<typeof setTimeout> | undefined;
  #refreshTimer: ReturnType<typeof setInterval> | undefined;
  #copyTimer: ReturnType<typeof setTimeout> | undefined;
  #folderTimer: ReturnType<typeof setTimeout> | undefined;

  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#feed?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#since = Date.now();
    this.#pending = parseRoute(this.route);
    this.#feed = new Feed(() => api("repo", "api/repos"), () => this.#read());
    void this.#feed.refresh();
    this.#readingTimer = setTimeout(() => this.requestUpdate(), READING_AFTER_MS);
    this.#refreshTimer = setInterval(() => {
      if (document.visibilityState === "visible") void this.#feed?.refresh();
    }, REFRESH_MS);
    document.addEventListener("visibilitychange", this.#shown);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#feed = null;
    clearTimeout(this.#readingTimer);
    clearInterval(this.#refreshTimer);
    clearTimeout(this.#copyTimer);
    clearTimeout(this.#folderTimer);
    document.removeEventListener("visibilitychange", this.#shown);
  }

  protected willUpdate(changed: Map<PropertyKey, unknown>): void {
    if (changed.has("route") && this.isConnected) {
      this.#pending = parseRoute(this.route);
      this.#openPending();
    }
  }

  protected updated(): void {
    if (!this.#scrollTo) return;
    this.#scrollTo = false;
    this.renderRoot.querySelector("moxi-ui-row[open]")?.scrollIntoView({ block: "nearest" });
  }

  /** The repos were read: an open repo no longer listed closes, and a route given before
   * they were read is opened. */
  #read(): void {
    const drawn = this.#drawing();
    if (drawn !== null && this.#feed?.failure === null && this.#open !== null
        && !drawn.repos.rows.some((row) => row.key === this.#open && row.repo !== null)) {
      this.#close();
      this.#write();
    }
    this.#openPending();
    this.requestUpdate();
  }

  /** The drawing of the value last read, or null before one is read. */
  #drawing(): Drawn | null {
    const value = this.#feed?.value ?? null;
    if (value === null || value.state !== "up") return null;
    if (this.#drawn?.value === value) return this.#drawn;
    const home = value.home;
    const items = itemsOf(value, { home });
    this.#drawn = { value, items, verdict: verdict(items, value.repos), repos: reposView(value, { home }),
                    pieces: piecesView(value) };
    return this.#drawn;
  }

  // -- the address -------------------------------------------------------------------------

  /** What is open, as an address. */
  #address(): Address {
    if (this.#open !== null) {
      const row = this.#drawing()?.repos.rows.find((one) => one.key === this.#open);
      if (row !== undefined) return rowAddress(row, this.#openAdd);
    }
    if (this.#adding) return { piece: "add" };
    return this.#piecesShown ? { piece: "pieces" } : NOTHING_OPEN;
  }

  /** Puts what is open in the browser's address, rewriting the entry the page is on. */
  #write(): void {
    const path = pathToWrite(this.#address(), location.pathname);
    if (path !== null) history.replaceState(history.state, "", path);
  }

  /** Opens the route given, once the repos are read. */
  #openPending(): void {
    const address = this.#pending;
    const drawn = this.#drawing();
    if (address === null || drawn === null || this.#feed?.failure !== null) return;
    this.#pending = null;
    this.#unknown = null;
    this.#close();
    if (address.piece === "pieces") this.#piecesShown = true;
    else if (address.piece === "add") this.#adding = true;
    else if (address.piece !== "list") {
      const row = rowNamed(address, drawn.repos.rows, drawn.value.home);
      if (row === null) this.#unknown = noSuchRepo(address);
      else {
        this.#open = row.key;
        this.#openAdd = address.piece === "repo-add";
        this.#scrollTo = true;
      }
    }
    this.#write();
    this.requestUpdate();
  }

  /** Nothing is open. */
  #close(): void {
    this.#open = null;
    this.#rulesShown = false;
    this.#commandsShown = false;
    this.#turnOff = null;
    this.#openAdd = false;
    this.#picked = [];
    this.#acting = null;
    this.#adding = false;
    this.#folder = "";
    this.#addPicked = [];
    clearTimeout(this.#folderTimer);
    this.#piecesShown = false;
  }

  /** `Open` or `Close`, or the row's own lines: the repo opens, or closes, and the address
   * says so. */
  #toggle(row: RowView): void {
    const open = this.#open === row.key;
    this.#close();
    if (!open) this.#open = row.key;
    this.#unknown = null;
    this.#write();
    this.requestUpdate();
  }

  /** `Show` or `Hide` on `Pieces`. */
  #togglePieces(): void {
    const shown = this.#piecesShown;
    this.#close();
    this.#piecesShown = !shown;
    this.#unknown = null;
    this.#write();
    this.requestUpdate();
  }

  // -- copying -----------------------------------------------------------------------------

  /** Puts `text` on the clipboard; the `Copy` named `what` then reads `Copied` for a while. */
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

  #copyButton(what: string, text: string) {
    return html`<button type="button" class="act" data-action="copy" data-copy=${what}
      @click=${() => void this.#copy(what, text)}>${this.#copied === what ? COPIED : COPY}</button>`;
  }

  // -- the acts in place -------------------------------------------------------------------

  /** Opens an act's confirm at `at` and reads its plan. */
  #startAct(act: Act, root: string, picked: string[], at: string, repo: string): void {
    const acting: Acting = {
      act, root, picked, at, repo, plan: null, failed: null, sending: false, outcome: null, answer: null,
    };
    this.#acting = acting;
    if (act !== "add") this.#picked = [];
    void this.#readPlan(acting);
    this.requestUpdate();
  }

  /** An act on a closed row or an item: whatever is open closes first. */
  #startOutside(act: Act, root: string, at: string, repo: string): void {
    this.#close();
    this.#unknown = null;
    this.#write();
    this.#startAct(act, root, [], at, repo);
  }

  async #readPlan(acting: Acting): Promise<void> {
    const result = await api("repo", planPath(acting.act, acting.root, acting.picked));
    if (this.#acting !== acting) return;
    const planned = result.status === 200 ? parsePlanned(result.data) : null;
    if (planned === null) acting.failed = planFailWords({ status: result.status });
    else if (planned.plan === null) acting.failed = planFailWords({ detail: planned.detail ?? SERVER_ERROR });
    else acting.plan = planned.plan;
    this.requestUpdate();
  }

  /** Sends the act with the digest of the plan its confirm drew, and draws the answer. */
  async #send(acting: Acting): Promise<void> {
    const digest = acting.plan?.digest ?? null;
    if (digest === null || acting.sending) return;
    acting.sending = true;
    this.requestUpdate();
    const request = actRequest(acting.act, acting.root, acting.picked, digest);
    const result = await api("repo", request.path, request.body);
    const answer = result.status === 200 ? parseAnswer(result.data) : null;
    const value = this.#feed?.value ?? null;
    const view = answer === null ? transportView(result.status) : answerView(answer, {
      home: value?.home ?? null, command: refreshCommand(value?.repos.find((repo) => repo.root === acting.root)),
    });
    if (view.reread) void this.#feed?.refresh();
    if (this.#acting !== acting) return;
    acting.sending = false;
    acting.outcome = answer?.outcome ?? "unread";
    acting.answer = view;
    if (view.plan !== null) acting.plan = view.plan;
    else if (acting.act === "add") this.#picked = [];
    this.requestUpdate();
  }

  /** `Cancel`, or `Close` under an answer: the act closes, and its picks with it. */
  #cancel(acting: Acting): void {
    if (this.#acting !== acting) return;
    if (acting.at === "add") {
      this.#toggleAdding(null);
      return;
    }
    this.#acting = null;
    if (acting.act === "add") this.#picked = [];
    this.requestUpdate();
  }

  /** `Add a piece` at an opened repo's foot: its list shows or folds, and the address
   * says so. */
  #toggleAddList(): void {
    this.#openAdd = !this.#openAdd;
    this.#picked = [];
    if (this.#acting?.act === "add") this.#acting = null;
    this.#write();
    this.requestUpdate();
  }

  /** `Add` or `Picked` in `Add a piece`: the confirm is read again for what is picked. */
  #pick(row: RowView, id: string, order: readonly string[]): void {
    const picked = new Set(this.#picked);
    if (picked.has(id)) picked.delete(id);
    else picked.add(id);
    this.#picked = order.filter((one) => picked.has(one));
    if (this.#picked.length === 0) {
      if (this.#acting?.act === "add") this.#acting = null;
      this.requestUpdate();
      return;
    }
    this.#startAct("add", row.key, this.#picked, `foot:${row.key}`, row.name);
  }

  /** `Add a repo` under the `Repos` label, or a project's `Add` on the `No hooks:` line
   * (`folder` its root): the panel opens, with the folder filled and its plan read when
   * one is given; pressed again with none, it closes. */
  #toggleAdding(folder: string | null): void {
    const open = this.#adding;
    this.#close();
    this.#unknown = null;
    if (!open || folder !== null) {
      this.#adding = true;
      if (folder !== null) {
        this.#folder = folder;
        this.#planFolder();
      }
    }
    this.#write();
    this.requestUpdate();
  }

  /** The folder typed: its plan is read once typing stops for a while. Until then no plan
   * is drawn, so the button never sends a plan for a folder the field no longer names. */
  #typed(text: string): void {
    this.#folder = text;
    if (this.#acting?.at === "add") this.#acting = null;
    clearTimeout(this.#folderTimer);
    this.#folderTimer = setTimeout(() => this.#planFolder(), FOLDER_MS);
    this.requestUpdate();
  }

  /** A piece switched on or off in `Add a repo`: the plan is read again. */
  #switchPiece(id: string, order: readonly string[]): void {
    const picked = new Set(this.#addPicked);
    if (picked.has(id)) picked.delete(id);
    else picked.add(id);
    this.#addPicked = order.filter((one) => picked.has(one));
    clearTimeout(this.#folderTimer);
    this.#planFolder();
  }

  /** Reads the plan of adding the folder typed with the pieces switched on. */
  #planFolder(): void {
    if (!this.#adding) return;
    const folder = this.#folder.trim();
    if (folder === "") {
      if (this.#acting?.at === "add") this.#acting = null;
      this.requestUpdate();
      return;
    }
    this.#startAct("add-repo", folder, this.#addPicked, "add", folder);
  }

  /** Where the open act is drawn: where it opened while that is drawn, else `Hooks`' foot. */
  #placeOf(acting: Acting, drawn: Drawn): string {
    const at = acting.at;
    if (at === "add") return this.#adding ? at : HOOKS_END;  // drawn by `Add a repo` itself
    if (at.startsWith("foot:")) return this.#open === at.slice(5) ? at : HOOKS_END;
    if (at.startsWith("row:")) {
      const key = at.slice(4);
      return this.#open !== key && drawn.repos.rows.some((row) => row.key === key) ? at : HOOKS_END;
    }
    const key = at.slice(5);
    const v = drawn.verdict;
    const drawnItems = this.#foldShown ? [...v.shown, ...v.folded] : v.shown;
    return drawnItems.some((item) => itemKey(item) === key) ? at : HOOKS_END;
  }

  // -- drawing -----------------------------------------------------------------------------

  render() {
    const feed = this.#feed;
    if (feed === null) return nothing;
    const state = pageState(feed, Date.now() - this.#since);
    if (state.state === "gone") return nothing;
    const drawn = state.drawn ? this.#drawing() : null;
    return html`<section data-part="page" data-state=${state.state}>
      ${state.line !== null && state.state !== "error" ? html`<p class="state ${state.state === "loading" ? "dim" : ""}"
        data-line=${state.state === "loading" ? "reading" : "failed"} role="status">${state.line}</p>` : nothing}
      ${state.state === "error" ? html`<div class="grps">${this.#hooksError(state)}</div>` : nothing}
      ${drawn !== null ? html`<div class=${state.acts ? "grps" : "grps kept"}>
        ${this.#hooks(drawn, feed.now(), state.acts)}${this.#repos(drawn, state.acts)}${this.#pieces(drawn, state.acts)}
      </div>` : nothing}
      ${this.#unknown !== null && state.acts ? html`<p class="foot" data-line="unknown">${this.#unknown}</p>` : nothing}
    </section>`;
  }

  /** `Hooks` with the listing's error in the verdict's place. */
  #hooksError(state: PageState) {
    return html`<section class="grp" data-group="hooks"><div class="gk">${HOOKS}</div><div class="gl">
      <p data-line="error">${state.line}</p></div></section>`;
  }

  // -- Hooks -------------------------------------------------------------------------------

  #hooks(drawn: Drawn, now: number | null, acts: boolean) {
    const v = drawn.verdict;
    const folded = foldedLine(v);
    return html`<section class="grp" data-group="hooks"><div class="gk">${HOOKS}</div><div class="gl">
      <div class="line"><span data-line="verdict"><span class=${v.heavy ? "h" : ""}>${v.head}</span>${v.rest === null
        ? nothing : v.heavy ? html`<span>${v.rest}</span>` : html` <span class="d">${v.rest}</span>`}</span>${now === null
        ? nothing : html`<span class="r d" data-line="read">read ${LOCAL.time(now)}</span>`}</div>
      ${repeat(v.shown, (item) => itemKey(item), (item) => this.#item(item, drawn, acts))}
      ${folded !== null ? html`<div class="line prob" data-line="folded"><span class="d">${folded}</span><button type="button"
        class="act on" data-action="show-warnings" aria-expanded=${this.#foldShown ? "true" : "false"}
        @click=${() => { this.#foldShown = !this.#foldShown; this.requestUpdate(); }}>${this.#foldShown ? HIDE : SHOW}</button></div>
        ${this.#foldShown ? repeat(v.folded, (item) => itemKey(item), (item) => this.#item(item, drawn, acts))
          : nothing}` : nothing}
      ${this.#actAt(HOOKS_END, drawn, acts)}
    </div></section>`;
  }

  /** One item in three lines: the title and its time, the dim line with `Show` for its
   * record, and the fix. */
  #item(item: Item, drawn: Drawn, acts: boolean) {
    const key = itemKey(item);
    const today = drawn.value.today;
    const need = item.weight === "need";
    const time = timeWords(item, today, LOCAL);
    const record = item.record;
    const shown = record !== null && this.#recordsShown.has(key);
    return html`<div class="prob" data-item=${item.source} data-weight=${item.weight} data-repo=${item.repo.root}>
      <div class="line"><span class=${need ? "h" : ""} data-line="title">${item.title}</span>${time !== null
        ? html`<span class="r ${need ? "h" : "d"}" data-line="time">${time}</span>` : nothing}</div>
      ${item.dim.length > 0 || record !== null ? html`<div class="line sm"><span class="d" data-line="dim">${
        item.dim.join(" · ")}</span>${record !== null ? html`<button type="button" class="act" data-action="show-record"
        aria-expanded=${shown ? "true" : "false"} @click=${() => this.#toggleRecord(key)}>${shown ? HIDE : SHOW}</button>`
        : nothing}</div>` : nothing}
      ${shown && record !== null ? recordText(record) : nothing}
      ${this.#fix(item, key, acts)}
      ${this.#actAt(`item:${key}`, drawn, acts)}
    </div>`;
  }

  /** An item's fix: a label over a command with `Copy`, one sentence, or the `Refresh`
   * act, which opens its confirm under the item. */
  #fix(item: Item, key: string, acts: boolean) {
    const fix = item.fix;
    if (fix === null) return nothing;
    const refreshing = this.#acting !== null && this.#acting.at === `item:${key}`;
    const refresh = fix.refresh && acts ? html`<button type="button" class="act ${refreshing ? "on" : ""}"
      data-action="refresh" @click=${() => this.#startOutside("refresh", item.repo.root, `item:${key}`, item.name)}>${
      REFRESH}</button>` : nothing;
    if (fix.text === null) return fix.refresh ? html`<div class="line sm" data-line="fix"><span></span>${refresh}</div>` : nothing;
    if (!fix.copy) return html`<div class="line sm" data-line="fix"><span>${fix.text}</span>${refresh}</div>`;
    return html`<div class="cmd sm" data-line="fix"><span>${fix.label !== null
      ? html`<span class="d lbl">${fix.label}</span>` : nothing}<code>${fix.text}</code></span><span class="acts">${
      this.#copyButton(`item:${key}`, fix.text)}${refresh}</span></div>`;
  }

  #toggleRecord(key: string): void {
    if (this.#recordsShown.has(key)) this.#recordsShown.delete(key);
    else this.#recordsShown.add(key);
    this.requestUpdate();
  }

  // -- Repos -------------------------------------------------------------------------------

  #repos(drawn: Drawn, acts: boolean) {
    const view = drawn.repos;
    const noHooks = view.noHooks;
    return html`<section class="grp rows" data-group="repos"><div class="gk">${REPOS}${acts ? html`<small><button
        type="button" data-action="add-repo" aria-expanded=${this.#adding ? "true" : "false"}
        @click=${() => this.#toggleAdding(null)}>${ADD_A_REPO}</button></small>` : nothing}</div><div class="gl">
      ${this.#adding && acts ? this.#adder(drawn, acts) : nothing}
      ${repeat(view.rows, (row) => row.key, (row) => this.#row(row, drawn, acts))}
      ${noHooks !== null ? html`<div class="one" data-line="no-hooks"><span>${noHooks.text}</span>${acts
        ? html`<span class="t">${noHooksActs(noHooks).map((act) => html`<button type="button" data-action="add"
          data-project=${act.project.project} @click=${() => this.#toggleAdding(homeForm(act.project.root,
                                                                                         drawn.value.home))}>${
          act.label}</button>`)}</span>` : nothing}</div>`
        : nothing}
    </div></section>`;
  }

  #row(row: RowView, drawn: Drawn, acts: boolean) {
    const open = acts && this.#open === row.key;
    const values = {
      name: row.name, where: row.where === "" ? null : row.where, fact: row.fact, "fact-heavy": flag(row.factHeavy),
      lead: row.lead, note: row.note, toggles: flag(acts && row.open), open: flag(open),
    };
    const rowAct = this.#acting !== null && this.#acting.at === `row:${row.key}` ? this.#acting.act : null;
    return html`<moxi-ui-row data-key=${row.key} ${attributes(values)}
        @moxi-row-toggle=${() => { if (acts && row.open) this.#toggle(row); }}>
      ${acts && !open && row.refresh ? html`<button type="button" slot="acts-one" class="do ${rowAct === "refresh" ? "on" : ""}"
        data-action="refresh" @click=${() => this.#startOutside("refresh", row.key, `row:${row.key}`, row.name)}>${
        REFRESH}</button>` : nothing}
      ${acts && !open && row.add ? html`<button type="button" slot="acts-one" class="do ${rowAct === "add-repo" ? "on" : ""}"
        data-action="add" @click=${() => this.#startOutside("add-repo", row.key, `row:${row.key}`, row.name)}>${
        ADD}</button>` : nothing}
      ${acts && row.open ? html`<button type="button" slot="acts-one" class="do" data-action="open"
        aria-expanded=${open ? "true" : "false"} @click=${() => this.#toggle(row)}>${open ? CLOSE : OPEN}</button>`
        : nothing}
      ${open && row.repo !== null ? this.#opened(row, drawn) : nothing}
    </moxi-ui-row>${this.#actAt(`row:${row.key}`, drawn, acts)}`;
  }

  // -- the opened repo ---------------------------------------------------------------------

  #opened(row: RowView, drawn: Drawn) {
    const repo = row.repo;
    if (repo === null) return nothing;
    const value = drawn.value;
    const view = openedView(repo, row.items, value.offered, { home: value.home, today: value.today });
    return html`<div class="det" data-part="opened">
      ${view.events.map((one) => this.#event(row, one, drawn))}
      ${view.today.length > 0 ? html`<div class="r" data-part="today"><span class="k">${TODAY}</span><div class="f">
        ${view.today.map((line) => this.#todayLine(row, line))}</div></div>` : nothing}
      ${view.autos.length > 0 ? html`<div class="r" data-part="auto-commit"><span class="k">${AUTO_COMMIT}</span>
        <div class="f">${view.autos.map((auto) => this.#auto(row, auto))}</div></div>` : nothing}
      ${view.rules !== null ? this.#rules(view.rules) : nothing}
      <div class="r" data-part="git"><span class="k">${GIT}</span><div class="f"><span data-line="git">${view.git}</span>
        ${view.moved !== null ? html`<span class="sub" data-line="moved">${view.moved}</span>` : nothing}</div></div>
      ${this.#foot(row, view, drawn)}
    </div>`;
  }

  #event(row: RowView, one: EventView, drawn: Drawn) {
    return html`<div class="r" data-event=${one.event}><span class="k">${one.label}</span><div class="f">
      ${one.pieces.map((line) => this.#pieceLine(row, line, drawn))}
      ${one.chain !== null ? html`<span class="sub" data-line="chain">${one.chain}</span>` : nothing}
      ${one.own !== null ? html`<span class="sub" data-line="own">${one.own}</span>` : nothing}
    </div></div>`;
  }

  #pieceLine(row: RowView, line: PieceLine, drawn: Drawn) {
    const key = `${row.key}\n${line.piece.id}`;
    const [head, ...rest] = line.text.split(" · ");
    const settingsShown = this.#settingsShown.has(key);
    const offOpen = this.#turnOff === key;
    return html`<div data-piece=${line.piece.id}>
      <div class="line"><span><span data-line="piece">${head}</span>${rest.length > 0
        ? html`<span class="d"> · ${rest.join(" · ")}</span>` : nothing}</span>${line.act === "turn-off"
        ? html`<button type="button" class="act ${offOpen ? "on" : ""}" data-action="turn-off"
          aria-expanded=${offOpen ? "true" : "false"} @click=${() => this.#toggleTurnOff(key)}>${TURN_OFF}</button>`
        : line.act === "turn-on" ? html`<button type="button" class="act ${this.#turningOn(row, line) ? "on" : ""}"
          data-action="turn-on" @click=${() => this.#startAct("enable", row.key, [line.piece.id], `foot:${row.key}`,
                                                              row.name)}>${TURN_ON}</button>`
        : nothing}</div>
      ${line.settings.length > 0 && line.tucked ? html`<div class="line"><span class="d">${SETTINGS}</span><button
        type="button" class="act" data-action="show-settings" aria-expanded=${settingsShown ? "true" : "false"}
        @click=${() => this.#toggleIn(this.#settingsShown, key)}>${settingsShown ? HIDE : SHOW}</button></div>` : nothing}
      ${line.settings.length > 0 && (!line.tucked || settingsShown)
        ? line.settings.map((setting) => html`<span class="sub" data-line="setting">${setting}</span>`) : nothing}
      ${offOpen ? this.#turnOffBlock(row, line, drawn) : nothing}
    </div>`;
  }

  #turnOffBlock(row: RowView, line: PieceLine, drawn: Drawn) {
    if (row.repo === null) return nothing;
    const view = turnOffView(line, row.repo, drawn.value.offered, drawn.value.home);
    const key = `${row.key}\n${line.piece.id}`;
    return html`<div class="off" data-part="turn-off">
      <p class="q">${view.title}</p>
      ${view.stops !== null ? html`<p>${view.stops}</p>` : nothing}
      <p>${view.stays}</p>
      ${this.#command(TURN_OFF, view.toggle, `off:${key}`)}
      ${this.#command(OR_REMOVE, view.remove, `remove:${key}`)}
      <p data-line="note">${view.note}</p>
    </div>`;
  }

  /** A command with its label and `Copy`. */
  #command(label: string, command: Command, what: string) {
    return html`<div class="cmd" data-line="command"><span><span class="lbl">${label}</span> <code>${command.command}</code></span>${
      this.#copyButton(what, command.command)}</div>`;
  }

  #todayLine(row: RowView, line: TodayLine) {
    const key = `today:${row.key}\n${line.record.kind}\n${line.record.at}\n${line.record.piece ?? line.record.spec}`;
    const shown = this.#recordsShown.has(key);
    return html`<div data-line="today"><div class="line"><span><span class="tm d">${line.time}</span>${line.what}</span>
        <button type="button" class="act" data-action="show-record" aria-expanded=${shown ? "true" : "false"}
          @click=${() => this.#toggleRecord(key)}>${shown ? HIDE : SHOW}</button></div>
      <span class="sub"><span class="tm"></span>${line.first}</span>
      ${shown ? recordText(line.record) : nothing}</div>`;
  }

  #auto(row: RowView, auto: AutoView) {
    const key = `${row.key}\n${auto.spec.name}`;
    return html`<div data-spec=${auto.spec.name}>
      <div class="line"><span><span>${auto.spec.name}</span>${auto.state !== null
        ? html`<span class="d" data-line="state"> ${auto.state}</span>` : nothing}</span></div>
      ${auto.lines.map((line) => html`<span class="sub" data-line="auto">${line}</span>`)}
      ${this.#command(auto.run.label, auto.run, `run:${key}`)}
      ${this.#command(auto.forget.label, auto.forget, `forget:${key}`)}
    </div>`;
  }

  #rules(rules: RulesView) {
    const shown = this.#rulesShown;
    return html`<div class="r" data-part="rules"><span class="k">${RULES}</span><div class="f">
      <div class="line"><span data-line="rules">${rules.head}</span>${rules.lines.length > 0 ? html`<button type="button"
        class="act" data-action="show-rules" aria-expanded=${shown ? "true" : "false"}
        @click=${() => { this.#rulesShown = !shown; this.requestUpdate(); }}>${shown ? HIDE : SHOW}</button>` : nothing}</div>
      ${shown && rules.lines.length > 0 ? html`<pre data-line="rules-text">${rules.lines.join("\n")}</pre>
        ${rules.after.map((line) => html`<span class="sub">${line}</span>`)}` : nothing}
    </div></div>`;
  }

  #foot(row: RowView, view: OpenedView, drawn: Drawn) {
    const shown = this.#commandsShown;
    const acting = this.#acting;
    const footAct = acting !== null && acting.at === `foot:${row.key}` ? acting.act : null;
    return html`<div class="foot" data-part="foot">
        <button type="button" class=${this.#openAdd ? "on" : ""} data-action="add-piece"
          aria-expanded=${this.#openAdd ? "true" : "false"} @click=${() => this.#toggleAddList()}>${ADD_A_PIECE}</button>
        <button type="button" class=${footAct === "refresh" ? "on" : ""} data-action="refresh"
          @click=${() => this.#startAct("refresh", row.key, [], `foot:${row.key}`, row.name)}>${REFRESH}</button>
        ${view.project !== null ? html`<button type="button" data-action="project"
          @click=${() => navigate("projects", `open/${encodeURIComponent(view.project ?? "")}`)}>Project ${view.project}</button>`
          : nothing}
        <button type="button" data-action="settings" @click=${() => navigate("ui", "settings/repo")}>${SETTINGS_LINK}</button>
        <button type="button" data-action="show-commands" aria-expanded=${shown ? "true" : "false"}
          @click=${() => { this.#commandsShown = !shown; this.requestUpdate(); }}>${COMMANDS} · ${shown ? HIDE : SHOW}</button>
      </div>
      ${shown ? html`<div class="cmds" data-part="commands">${view.commands.map((command) =>
        this.#command(command.label, command, `command:${row.key}\n${command.label}`))}</div>` : nothing}
      ${this.#openAdd ? this.#addList(row, drawn) : nothing}
      ${this.#actAt(`foot:${row.key}`, drawn, true)}`;
  }

  /** Whether `Turn on`'s confirm for this piece is open. */
  #turningOn(row: RowView, line: PieceLine): boolean {
    const acting = this.#acting;
    return acting !== null && acting.act === "enable" && acting.root === row.key && acting.picked[0] === line.piece.id;
  }

  #toggleTurnOff(key: string): void {
    this.#turnOff = this.#turnOff === key ? null : key;
    this.requestUpdate();
  }

  #toggleIn(set: Set<string>, key: string): void {
    if (set.has(key)) set.delete(key);
    else set.add(key);
    this.requestUpdate();
  }

  // -- Add a repo --------------------------------------------------------------------------

  /** `Add a repo`: the folder, what the plan says of it, each offered piece as a switch,
   * then the plan's lines and `Add <n> pieces` or `Add to the list`, and `Cancel`. */
  #adder(drawn: Drawn, acts: boolean) {
    const acting = this.#acting !== null && this.#acting.at === "add" ? this.#acting : null;
    const order = offeredOrder(drawn.value.offered);
    const picked = new Set(this.#addPicked);
    const plan = acting?.plan ?? null;
    const said = acting === null ? null : acting.failed ?? (plan === null ? READING : folderLine(plan));
    const asking = acting === null || acting.outcome === null || acting.outcome === "changed";
    const view = acting === null || plan === null || plan.refusal !== null ? null : confirmView(plan, {
      act: "add-repo", repo: acting.repo, picked: acting.picked, offered: drawn.value.offered, home: drawn.value.home,
    });
    return html`<div class="cf add" data-part="add-repo" role="group" aria-label=${ADD_A_REPO}>
      <p class="q">${ADD_A_REPO}</p>
      <div class="cr"><span class="k">${FOLDER}</span><span class="f"><input class="fld" type="text" data-line="folder"
          aria-label=${FOLDER} spellcheck="false" autocomplete="off" .value=${this.#folder}
          @input=${(event: Event) => this.#typed((event.target as HTMLInputElement).value)}>${said !== null
          ? html`<span class="sub" data-line="folder-said">${said}</span>` : nothing}</span></div>
      ${order.length > 0 ? html`<div class="cr"><span class="k">${PIECES}</span><span class="f">${order.map((piece) =>
        html`<span class="tick" data-piece=${piece.id}><button type="button" class="sw" role="switch"
          aria-checked=${picked.has(piece.id) ? "true" : "false"} aria-label=${piece.name} data-action="switch"
          @click=${() => this.#switchPiece(piece.id, order.map((one) => one.id))}></button>${piece.name}</span>`)}</span></div>`
        : nothing}
      ${acting !== null && acting.outcome === "changed" ? this.#answer(acting) : nothing}
      ${acting !== null && asking && view !== null ? this.#planLines(acting, view) : nothing}
      ${acting !== null && !asking ? this.#answer(acting) : nothing}
      <div class="btns">${asking && view?.go != null && acting !== null ? html`<button type="button" class="btn"
          data-action="go" ?disabled=${!acts || acting.sending} @click=${() => void this.#send(acting)}>${view.go}</button>`
          : nothing}
        <button type="button" class="lnk" data-action="cancel" @click=${() => this.#toggleAdding(null)}>${
          asking ? CANCEL : CLOSE}</button></div>
    </div>`;
  }

  // -- confirms and answers ----------------------------------------------------------------

  /** The open act, when it is drawn at `at`. */
  #actAt(at: string, drawn: Drawn, acts: boolean) {
    const acting = this.#acting;
    if (acting === null || acting.at === "add" || this.#placeOf(acting, drawn) !== at) return nothing;
    return this.#confirm(acting, drawn, acts);
  }

  /** The confirm in place: its question, the plan's lines (or why it can't be read), the
   * button and `Cancel`; once answered, the answer under the question and `Close`. After
   * `changed` the answer's line comes first and the new plan's confirm under it. */
  #confirm(acting: Acting, drawn: Drawn, acts: boolean) {
    const value = drawn.value;
    const view = acting.plan === null ? null : confirmView(acting.plan, {
      act: acting.act, repo: acting.repo, picked: acting.picked, offered: value.offered, home: value.home,
    });
    const asking = acting.outcome === null || acting.outcome === "changed";
    const title = acting.act !== "add-repo" ? view?.title ?? null : acting.at === "add" ? null : rowAddTitle(acting.repo);
    return html`<div class="cf" data-part="confirm" data-act=${acting.act} role="group" aria-label=${title ?? acting.act}>
      ${title !== null ? html`<p class="q" data-line="question">${title}</p>` : nothing}
      ${acting.outcome === "changed" ? this.#answer(acting) : nothing}
      ${asking ? this.#planLines(acting, view) : this.#answer(acting)}
      <div class="btns">${asking && view?.go != null ? html`<button type="button" class="btn" data-action="go"
          ?disabled=${!acts || acting.sending} @click=${() => void this.#send(acting)}>${view.go}</button>` : nothing}
        <button type="button" class="lnk" data-action="cancel" @click=${() => this.#cancel(acting)}>${
          asking ? CANCEL : CLOSE}</button></div>
    </div>`;
  }

  /** A plan's lines: `Writes`, `Then` with any line to add to another tool's hook, and
   * `Leaves`; or the plan's refusal; or `Reading…`, or why the plan can't be read. */
  #planLines(acting: Acting, view: ConfirmView | null) {
    if (acting.failed !== null) return html`<p class="sm" data-line="plan-failed">${acting.failed}</p>`;
    if (view === null) return html`<p class="sm d" data-line="reading">${READING}</p>`;
    if (view.refusal !== null) return html`<p class="sm" data-line="refusal">${view.refusal}</p>`;
    const key = `${acting.at}\n${acting.act}`;
    return html`${view.writes.map((line, at) => labelled(at === 0 ? WRITES : "", line, "writes"))}
      ${view.then.map((line, at) => labelled(at === 0 ? THEN : "", line, "then", false))}
      ${view.chain.map((chain, at) => html`<div class="cr" data-line="chain"><span class="k">${
        view.then.length === 0 && at === 0 ? THEN : ""}</span><span class="f">${chain.text}<span class="cmd"><code>${
        chain.line}</code>${this.#copyButton(`chain:${key}\n${at}`, chain.line)}</span></span></div>`)}
      ${view.leaves !== null ? labelled(LEAVES, view.leaves, "leaves") : nothing}`;
  }

  /** An act's answer: its first line, the verb's lines, what to copy, and after `partial`
   * the `Refresh` act with the refresh command. */
  #answer(acting: Acting) {
    const view = acting.answer;
    if (view === null) return nothing;
    const key = `${acting.at}\n${acting.act}`;
    return html`<div class="ans" data-part="answer" data-outcome=${acting.outcome ?? ""}>
      ${view.head !== null ? html`<p class=${view.heavy ? "h" : ""} data-line="answer">${view.head}</p>` : nothing}
      ${view.lines.map((line) => html`<p class=${view.dim ? "d" : ""} data-line="said">${line}</p>`)}
      ${view.copies.map((copy, at) => html`<div data-line="copy">${copy.text === copy.copy
        ? html`<div class="cmd"><span>${copy.text}</span>${this.#copyButton(`answer:${key}\n${at}`, copy.copy)}</div>`
        : html`<p>${copy.text}</p><div class="cmd"><code>${copy.copy}</code>${
          this.#copyButton(`answer:${key}\n${at}`, copy.copy)}</div>`}</div>`)}
      ${view.refresh ? html`<div class="line"><button type="button" class="act on" data-action="refresh"
        @click=${() => this.#startAct("refresh", acting.root, [], acting.at, acting.repo)}>${REFRESH}</button></div>`
        : nothing}
      ${view.command !== null ? this.#command(view.command.label, view.command, `answer:${key}\nrefresh`) : nothing}
    </div>`;
  }

  /** The open repo's `Add a piece`: each offered piece not in it, with its place and doc,
   * and `Add` or `Picked`. */
  #addList(row: RowView, drawn: Drawn) {
    const repo = row.repo;
    if (repo === null) return nothing;
    const rows = addRows(repo, drawn.value.offered, new Set(this.#picked));
    const order = rows.map((one) => one.piece.id);
    return html`<div class="cf" data-part="add-piece">
      <p class="q" data-line="question">${ADD_A_PIECE} to ${row.name}</p>
      ${rows.length === 0 ? html`<p class="sm d" data-line="none">${NONE_TO_ADD}</p>` : rows.map((one) => html`<div
        class="cr" data-piece=${one.piece.id}><span class="k">${one.name}</span><span class="f d">${one.where} · ${
        one.doc}</span><button type="button" class="x act ${one.picked ? "on" : ""}" data-action="pick"
        aria-pressed=${one.picked ? "true" : "false"} @click=${() => this.#pick(row, one.piece.id, order)}>${
        one.button}</button></div>`)}
    </div>`;
  }

  // -- Pieces ------------------------------------------------------------------------------

  #pieces(drawn: Drawn, _acts: boolean) {
    const view = drawn.pieces;
    const shown = this.#piecesShown;
    return html`<section class="grp" data-group="pieces"><div class="gk">${PIECES}</div><div class="gl">
      <div class="line"><span><span data-line="count">${view.count}</span>${view.names !== ""
        ? html`<span class="d" data-line="names"> · ${view.names}</span>` : nothing}</span>${view.rows.length > 0
        ? html`<button type="button" class="act" data-action="show-pieces" aria-expanded=${shown ? "true" : "false"}
          @click=${() => this.#togglePieces()}>${shown ? HIDE : SHOW}</button>` : nothing}</div>
      ${shown ? html`${repeat(view.rows, (row) => row.piece.id, (row) => html`<div class="pc" data-piece=${row.piece.id}>
          <div class="line"><span>${row.name} <span class="d">${row.from}</span></span><span class="r d sm"
            data-line="where">${row.where}</span></div>
          <p class="sm"><span>${row.event}</span><span class="d git">${row.git}</span><span class="d"> · ${row.doc}</span></p>
        </div>`)}
        ${view.sentence !== null ? html`<p class="said sm" data-line="sentence">${view.sentence}</p>` : nothing}` : nothing}
      ${view.refused.map((line) => html`<p class="said sm" data-line="refused">${line}</p>`)}
    </div></section>`;
  }
}

/** One key per item: its repo, what it was made from and its title. */
function itemKey(item: Item): string {
  return `${item.repo.root}\n${item.source}\n${item.title}`;
}

/** A confirm's row: its label at the left, then the line, its first phrase in the text's
 * colour and the rest (from the first ` · `) dim. */
function labelled(label: string, line: string, part: string, split = true) {
  const cut = split ? line.indexOf(" · ") : -1;
  return html`<div class="cr" data-line=${part}><span class="k">${label}</span><span class="f">${cut < 0 ? line
    : html`${line.slice(0, cut)}<span class="d">${line.slice(cut)}</span>`}</span></div>`;
}

/** A record's whole text, as `Show` opens it. */
function recordText(record: RecordRow) {
  return html`<pre data-line="record">${record.text}</pre>`;
}

if (!customElements.get(TAG)) customElements.define(TAG, RepoPage);

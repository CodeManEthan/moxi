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
import { api } from "/m/ui/client.js";

import { Feed } from "./feed.ts";
import type { RecordRow, Repos } from "./feed.ts";
import {
  NOTHING_OPEN, noSuchRepo, pageState, parseRoute, pathToWrite, READING_AFTER_MS, REFRESH_MS, rowAddress, rowNamed,
} from "./frame-model.ts";
import type { Address, PageState } from "./frame-model.ts";
import { foldedLine, itemsOf, timeWords, verdict } from "./items-model.ts";
import type { Item, Verdict } from "./items-model.ts";
import { piecesView } from "./pieces-model.ts";
import type { PiecesView } from "./pieces-model.ts";
import { reposView } from "./rows-model.ts";
import type { ReposView, RowView } from "./rows-model.ts";
import { LOCAL } from "./words.ts";

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

  .pc { padding-top: 10px; }
  .pc .d.git { margin-left: 6px; }
  .said { padding-top: 10px; color: var(--moxi-text-dim); }

  @media (max-width: 720px) {
    :host { padding-top: 10px; }
    .grp { display: block; padding: 8px 0 10px; }
    .grp.rows { padding: 4px 0 6px; }
    /* The label above its lines, its act beside it at the right. */
    .grp .gk { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; font-size: 13px; }
    .grp.rows .gk { padding-top: 9px; }
    .grp .gk small { margin-top: 0; }
    .grp:not(.rows) .gk { padding-bottom: 4px; }
  }
`;

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
  /** `Pieces` is shown. */
  #piecesShown = false;
  /** The warnings folded under the verdict are shown. */
  #foldShown = false;
  /** The items whose record's whole text is shown, by item key. */
  #recordsShown = new Set<string>();
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
    this.#openAdd = false;
    this.#adding = false;
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
      ${repeat(v.shown, (item) => itemKey(item), (item) => this.#item(item, drawn.value.today, acts))}
      ${folded !== null ? html`<div class="line prob" data-line="folded"><span class="d">${folded}</span><button type="button"
        class="act on" data-action="show-warnings" aria-expanded=${this.#foldShown ? "true" : "false"}
        @click=${() => { this.#foldShown = !this.#foldShown; this.requestUpdate(); }}>${this.#foldShown ? HIDE : SHOW}</button></div>
        ${this.#foldShown ? repeat(v.folded, (item) => itemKey(item), (item) => this.#item(item, drawn.value.today, acts))
          : nothing}` : nothing}
    </div></section>`;
  }

  /** One item in three lines: the title and its time, the dim line with `Show` for its
   * record, and the fix. */
  #item(item: Item, today: string | null, _acts: boolean) {
    const key = itemKey(item);
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
      ${this.#fix(item, key)}
    </div>`;
  }

  #fix(item: Item, key: string) {
    const fix = item.fix;
    if (fix === null || fix.text === null) return nothing;
    if (!fix.copy) return html`<p class="sm" data-line="fix">${fix.text}</p>`;
    return html`<div class="cmd sm" data-line="fix"><span>${fix.label !== null
      ? html`<span class="d lbl">${fix.label}</span>` : nothing}<code>${fix.text}</code></span>${
      this.#copyButton(`item:${key}`, fix.text)}</div>`;
  }

  #toggleRecord(key: string): void {
    if (this.#recordsShown.has(key)) this.#recordsShown.delete(key);
    else this.#recordsShown.add(key);
    this.requestUpdate();
  }

  // -- Repos -------------------------------------------------------------------------------

  #repos(drawn: Drawn, acts: boolean) {
    const view = drawn.repos;
    return html`<section class="grp rows" data-group="repos"><div class="gk">${REPOS}</div><div class="gl">
      ${repeat(view.rows, (row) => row.key, (row) => this.#row(row, acts))}
      ${view.noHooks !== null ? html`<div class="one" data-line="no-hooks"><span>${view.noHooks.text}</span></div>`
        : nothing}
    </div></section>`;
  }

  #row(row: RowView, acts: boolean) {
    const open = acts && this.#open === row.key;
    const values = {
      name: row.name, where: row.where === "" ? null : row.where, fact: row.fact, "fact-heavy": flag(row.factHeavy),
      lead: row.lead, note: row.note, toggles: flag(acts && row.open), open: flag(open),
    };
    return html`<moxi-ui-row data-key=${row.key} ${attributes(values)}
        @moxi-row-toggle=${() => { if (acts && row.open) this.#toggle(row); }}>
      ${acts && row.open ? html`<button type="button" slot="acts-one" class="do" data-action="open"
        aria-expanded=${open ? "true" : "false"} @click=${() => this.#toggle(row)}>${open ? CLOSE : OPEN}</button>`
        : nothing}
      ${open ? html`<div data-part="opened"></div>` : nothing}
    </moxi-ui-row>`;
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

/** A record's whole text, as `Show` opens it. */
function recordText(record: RecordRow) {
  return html`<pre data-line="record">${record.text}</pre>`;
}

if (!customElements.get(TAG)) customElements.define(TAG, RepoPage);

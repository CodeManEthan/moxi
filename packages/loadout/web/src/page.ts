// <moxi-loadout-page route="...">: loadout's panel. Under the shell's top line, from top to
// bottom, each group with its label in the left column:
//
// - `This page`, only while the harness homes' note is set: the page's acts are off, and
//   each act shows as the command he would type, with `Copy`.
// - `Placed`: the verdict, then each problem in three lines (what, where, the fix and its
//   act), then the counts per harness; with nothing needing him, the counts sit at the
//   verdict's right.
// - `Folder`: the loadout folder, its files git hasn't committed, and `Settings › loadout`.
// - `Your items`, a group per module whose skills it took over, then each project
//   section: one row per item and a column per harness of the harness table, each row
//   with `Open`; past twelve items a section folds by kind, and a kind opens in place with
//   its items. The global file is Your items' last row.
// - The dim foot lines: the view's notes, why the folder can't be read, and the line for an
//   address whose item is gone.
//
// One thing is open at a time: an item (its From line, a line per harness with its act,
// Remove), a kind, or the global file (its mode, its files, Turn off and its confirm). The
// page writes what is open into its address with `history.replaceState`.
//
// It places what the models (`placed-model.ts`, `items-model.ts`, `opened-model.ts`,
// `job-model.ts`, `frame-model.ts`) return; every word comes from there. No accent and no
// warning colour: a problem rises by its order and weight 600.
//
// It reads `/api/view` and `/api/folder` when it opens, when its tab is shown again, every
// 60 seconds while shown, and after a job ends. An act runs as a page job: the page sends
// it, follows the job's status path, and keeps every act's control off while one runs. One
// dim line under the act names the command and how it ended, with `Show` for the job's
// lines and rows not fine, and what the job kept with the way back, open; it stays until
// the next act ends. The page keeps the job's path in the tab's `sessionStorage` until the
// job ends, so after a reload it follows the job again. When a job ends the page reads
// again and sends `CHANGED` on the document, so every loadout component reads again too.

import { css, html, LitElement, nothing } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { api, navigate } from "/m/ui/client.js";

import { CHANGED, Feed, parseJob, parseSourceDir, parseView } from "./feed.ts";
import type { Job, SourceDir, View } from "./feed.ts";
import { commandOf, globalOffAct, installAct, jobId } from "./acts-model.ts";
import type { PageAct } from "./acts-model.ts";
import {
  closed, goneLineOf, itemAddress, NOTHING_OPEN, openKind, opensItem, parseRoute, pathToWrite, sameAddress,
} from "./address-model.ts";
import type { Address } from "./address-model.ts";
import {
  actCommand, COPIED, COPIED_MS, COPY, emptyGlobal, emptyLines, failedLine, fixLine, folderView, footLines,
  GLOBAL_PLACE, GONE, INSTALL_ALL_PLACE, itemPlace, jobPlace, noteGroup, pageState, PLACED_PLACE, problemPlace,
  READING, READING_AFTER_MS, REFRESH_MS, SETTINGS_LINK, SETTINGS_ROUTE,
} from "./frame-model.ts";
import { itemsView, scopeOf } from "./items-model.ts";
import type { CellView, GlobalRow, GroupView, ItemRow, KindRow, Tone } from "./items-model.ts";
import { followLine, keptLines, showView } from "./job-model.ts";
import type { Unfollowed } from "./job-model.ts";
import { fromParts, openedGlobal, openedItem, replaceArgv } from "./opened-model.ts";
import type { HarnessLine, OpenedGlobal, OpenedItem } from "./opened-model.ts";
import { INSTALL_ALL, placedView } from "./placed-model.ts";
import type { PlacedView, Problem, ProblemAct } from "./placed-model.ts";
import { harnessWord, kindFolded } from "./words.ts";
import { jobRefusal, parseKept, startedOf } from "./views.ts";
import type { Kept } from "./views.ts";

const TAG = "moxi-loadout-page";

/** How often a running job is read. */
const JOB_POLL_MS = 1000;
/** After this many failed reads in a row the page stops reading a job. */
const JOB_TRIES = 5;
/** The `sessionStorage` key of the job the page follows. */
const KEPT = "moxi-loadout-job";

/** Keeps `job` as the one the tab follows, or forgets it. Without storage, the page
 * follows a job only while it stays open. */
function keep(job: Kept | null): void {
  try {
    if (job === null) sessionStorage.removeItem(KEPT);
    else sessionStorage.setItem(KEPT, JSON.stringify(job));
  } catch {
    // Storage is off or full.
  }
}

/** The job the tab follows, if one is kept. */
function kept(): Kept | null {
  try {
    return parseKept(sessionStorage.getItem(KEPT));
  } catch {
    return null;
  }
}

/** One act the page sent: the command it runs, where on the page it was pressed, and its
 * job as last read; or why it didn't start. */
interface Sent {
  command: string;
  place: string | null;
  job: Job | null;
  stopped: Unfollowed;
  /** Why the act didn't start; null once it did. */
  said: string | null;
}

const TONES: Record<Tone, string> = { plain: "", dim: "d", heavy: "h" };

// The Sessions page's groups at its width: a dim label at the left, text tokens only.
const STYLES = css`
  :host { display: block; min-width: 0; max-width: 680px; margin: 0 auto; padding: 4px 0 16px;
          color: var(--moxi-text); font-family: var(--moxi-font-sans); font-size: 14px; line-height: 20px; }
  /* The accent is the focus ring alone, drawn only on a focused piece. */
  * { box-sizing: border-box; outline-color: var(--moxi-accent); }
  p { margin: 0; overflow-wrap: anywhere; }
  button { font: inherit; color: inherit; background: none; border: 0; padding: 0; margin: 0; cursor: pointer;
           text-align: left; }
  button:disabled { cursor: not-allowed; color: var(--moxi-text-dim); }
  a { color: inherit; text-decoration: none; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  code { font: 12px/18px var(--moxi-font-mono); color: var(--moxi-text); overflow-wrap: anywhere; }
  pre { font: 12px/18px var(--moxi-font-mono); color: var(--moxi-text); margin: 6px 0 2px; padding: 7px 10px;
        border: 1px solid var(--moxi-border); border-radius: 7px; white-space: pre; overflow-x: auto; }
  .d, .dim { color: var(--moxi-text-dim); }
  .h { font-weight: 600; }

  .waiting { padding: 12px 2px 0; color: var(--moxi-text-dim); }
  .grps { margin-top: 16px; border-top: 1px solid var(--moxi-border); }
  .grp { display: grid; grid-template-columns: 96px minmax(0, 1fr); border-bottom: 1px solid var(--moxi-border);
         padding: 12px 2px; }
  .grp .gk { color: var(--moxi-text-dim); }
  .grp .gk .n { display: block; font-size: 13px; line-height: 18px; }
  .grp .gl { grid-column: 2; min-width: 0; }

  .line { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; min-width: 0; }
  .line > :first-child { min-width: 0; overflow-wrap: anywhere; }
  .line > .r { flex: none; text-align: right; }
  .sm { font-size: 13px; line-height: 18px; }
  .act { flex: none; font-size: 13px; line-height: 18px; color: var(--moxi-text); }
  .act.d { color: var(--moxi-text-dim); }

  .prob { padding-top: 14px; }
  .prob .l1 b { font-weight: 600; }
  .counts { padding-top: 14px; }
  .cmd { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; padding-top: 2px; }

  /* The item grid: a line per item, the name, a fixed column per harness, then Open. */
  .ir { display: flex; align-items: baseline; gap: 12px; padding: 4px 0; }
  .ir.hd { color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; padding: 0 0 6px; }
  .ir .nm { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
  .ir .nm .k { margin-left: 8px; }
  .ir .c { flex: 0 0 94px; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ir .c.k { font-variant-numeric: tabular-nums; }
  .ir .o { flex: 0 0 44px; text-align: right; font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  .two { margin-top: -4px; padding-bottom: 4px; font-size: 13px; line-height: 18px; }
  .kopen { border-top: 1px solid var(--moxi-border); border-bottom: 1px solid var(--moxi-border); margin: 3px 0 5px;
           padding: 2px 0 3px; }

  /* An opened item or global file, under its row. */
  .det { margin: 5px 0 6px; padding: 8px 0 10px; border-top: 1px solid var(--moxi-border);
         border-bottom: 1px solid var(--moxi-border); font-size: 13px; line-height: 18px; }
  .det .r { display: grid; grid-template-columns: 92px minmax(0, 1fr) auto; gap: 0 14px; padding: 3px 0;
            align-items: baseline; }
  .det .r .k { color: var(--moxi-text-dim); }
  .det .r .f { min-width: 0; overflow-wrap: anywhere; }
  .det .r .f .sub { display: block; color: var(--moxi-text-dim); }
  .det .r .x { color: var(--moxi-text-dim); }
  .det .r .x.act { color: var(--moxi-text); }
  .det .r .f .cmd { padding-top: 4px; }
  .det .foot { display: flex; flex-wrap: wrap; gap: 4px 18px; padding: 9px 0 0 106px; }
  .det .foot + .foot { padding-top: 4px; }
  .det .foot .cmd { flex-basis: 100%; }
  .tri { display: inline-flex; flex-wrap: wrap; margin: -5px 0 -5px -8px; }
  .tri > * { padding: 5px 7px; border: 1px solid transparent; border-radius: 6px; color: var(--moxi-text-dim);
             white-space: nowrap; }
  .tri > .s { color: var(--moxi-text); border-color: var(--moxi-border); }
  .cf { margin: 10px 0 2px 106px; border-top: 1px solid var(--moxi-border); padding-top: 10px; }
  .cf .q { font-size: 14px; line-height: 20px; font-weight: 600; padding-bottom: 4px; }
  .cf .btns { display: flex; gap: 18px; align-items: center; padding: 10px 0 2px; }
  .btn { font-size: 14px; line-height: 20px; color: var(--moxi-text); border: 1px solid var(--moxi-border);
         border-radius: 7px; padding: 5px 13px; }
  .lnk { font-size: 14px; line-height: 20px; color: var(--moxi-text-dim); }

  /* The line under an act once its job ends, what it kept, and what Show opens. */
  .job { padding-top: 6px; font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  .job .line { gap: 18px; }
  .job .kept { padding-top: 4px; }
  .job .kept .back { padding-top: 2px; }
  .job .more { padding: 4px 0 0 12px; }
  .job .more .p + .p { padding-top: 4px; }
  .job .more .p .w { font-weight: 600; color: var(--moxi-text); }

  .quietline { font-size: 22px; line-height: 32px; text-align: center; letter-spacing: -0.005em; padding: 120px 0 14px; }
  .first { color: var(--moxi-text-dim); line-height: 1.6; text-align: center; }
  .first p { max-width: 520px; margin: 0 auto; }
  .first + .grps { margin-top: 40px; }
  .foot { font-size: 13px; line-height: 18px; padding: 12px 2px 0; color: var(--moxi-text-dim); overflow-wrap: anywhere; }

  @media (max-width: 720px) {
    :host { padding-top: 10px; }
    .grp { display: block; padding: 8px 0 10px; }
    .grp .gk { padding-bottom: 6px; font-size: 13px; }
    .grp .gk .n { display: inline; margin-left: 8px; }
    .ir { gap: 8px; }
    .ir .c { flex-basis: 72px; font-size: 13px; }
    .det .r { grid-template-columns: 76px minmax(0, 1fr) auto; gap: 0 10px; }
    .det .foot { padding-left: 86px; }
    .cf { margin-left: 0; }
    .quietline { padding-top: 60px; }
  }
`;

export class LoadoutPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";

  #view: Feed<View> | null = null;
  #dir: Feed<SourceDir> | null = null;
  #openedAt = 0;

  /** What is open, and the dim foot line for an address whose item is gone. */
  #at: Address = NOTHING_OPEN;
  #goneSaid: string | null = null;
  /** The address came from the route and the view hasn't been read against it yet. */
  #fromRoute = false;
  /** The opened global file's Turn off confirm is open. */
  #confirmOff = false;
  /** The opened global file's files whose diff `Show` opened, by harness. */
  #diffs = new Set<string>();

  /** The job the page follows, and whether it has ended. */
  #job: Feed<Job> | null = null;
  #jobEnded = true;
  #jobFails = 0;
  #jobTimer: ReturnType<typeof setTimeout> | undefined;
  /** An act is being sent. */
  #sending = false;
  /** The act whose job runs, and the last act that ended (or didn't start). */
  #running: Sent | null = null;
  #last: Sent | null = null;
  /** `Show` is open under the last act's line. */
  #showing = false;

  /** Which copy act reads `Copied`. */
  #copied: string | null = null;
  #copyTimer: ReturnType<typeof setTimeout> | undefined;
  #waitTimer: ReturnType<typeof setTimeout> | undefined;
  #refreshTimer: ReturnType<typeof setInterval> | undefined;

  #shown = (): void => {
    if (document.visibilityState === "visible") this.#readAll();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#openedAt = Date.now();
    const changed = (): void => this.requestUpdate();
    this.#view = new Feed<View>(() => api("loadout", "api/view"), parseView, changed);
    this.#dir = new Feed<SourceDir>(() => api("loadout", "api/folder"), parseSourceDir, changed);
    this.#readAll();
    document.addEventListener("visibilitychange", this.#shown);
    this.#waitTimer = setTimeout(() => this.requestUpdate(), READING_AFTER_MS);
    this.#refreshTimer = setInterval(() => {
      if (document.visibilityState === "visible") this.#readAll();
    }, REFRESH_MS);
    const job = kept();
    if (job !== null) {
      this.#running = { command: job.label, place: job.anchor, job: null, stopped: null, said: null };
      this.#follow(job.path);
    }
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    for (const feed of [this.#view, this.#dir, this.#job]) feed?.close();
    this.#view = this.#dir = null;
    this.#job = null;
    clearTimeout(this.#jobTimer);
    clearTimeout(this.#copyTimer);
    clearTimeout(this.#waitTimer);
    clearInterval(this.#refreshTimer);
    document.removeEventListener("visibilitychange", this.#shown);
  }

  protected willUpdate(changed: Map<PropertyKey, unknown>): void {
    if (changed.has("route")) {
      this.#goneSaid = null;
      this.#fromRoute = true;
      this.#setAt(parseRoute(this.route));
    }
    // An address whose item is gone opens the page with nothing open, and says so; an
    // item that goes while it is open (a Remove) just closes.
    const view = this.#view?.value ?? null;
    if (view !== null && view.problem === null) {
      const gone = goneLineOf(this.#at, view);
      if (gone !== null) {
        if (this.#fromRoute) this.#goneSaid = gone;
        this.#setAt(NOTHING_OPEN);
      }
      this.#fromRoute = false;
    }
  }

  #readAll(): void {
    void this.#view?.refresh();
    void this.#dir?.refresh();
  }

  /** Whether an act is on its way or its job runs: every act's control is off then. */
  get #busy(): boolean {
    return this.#sending || !this.#jobEnded;
  }

  // -- what is open ----------------------------------------------------------------------

  /** Opens `address`, closing what was open, and writes it as the browser's address. */
  #open(address: Address): void {
    if (sameAddress(address, this.#at)) return;
    this.#goneSaid = null;
    this.#fromRoute = false;
    this.#setAt(address);
    this.requestUpdate();
  }

  #setAt(address: Address): void {
    if (!sameAddress(address, this.#at)) {
      this.#confirmOff = false;
      this.#diffs = new Set();
    }
    this.#at = address;
    const path = pathToWrite(address, location.pathname);
    if (path !== null) history.replaceState(history.state, "", path);
  }

  // -- acts ------------------------------------------------------------------------------

  async #act(act: PageAct, place: string | null): Promise<void> {
    if (this.#busy) return;
    this.#sending = true;
    const command = commandOf(act.argv);
    this.requestUpdate();
    try {
      const answer = await api("loadout", act.path, act.body);
      const started = startedOf(answer.status, answer.data);
      if (started.kind === "job") {
        this.#running = { command, place, job: null, stopped: null, said: null };
        keep({ path: started.path, label: command, anchor: place });
        this.#follow(started.path);
      } else {
        this.#last = { command, place, job: null, stopped: null, said: started.text };
        this.#showing = false;
      }
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  /** Follows the job at status path `path` until it ends. */
  #follow(path: string): void {
    this.#job?.close();
    clearTimeout(this.#jobTimer);
    this.#jobEnded = false;
    this.#jobFails = 0;
    const feed: Feed<Job> = new Feed<Job>(() => api("loadout", path), parseJob, () => this.#jobRead(feed));
    this.#job = feed;
    void feed.refresh();
  }

  #jobRead(feed: Feed<Job>): void {
    if (feed !== this.#job || this.#jobEnded) return;
    this.#jobFails = feed.failure !== null ? this.#jobFails + 1 : 0;
    const running = this.#running;
    if (running !== null && feed.value !== null) running.job = feed.value;
    const state = feed.value?.state ?? "running";
    // A refused read (no such job) ends it; one that gave no answer may not have.
    const refusal = jobRefusal(feed.failure);
    const lost = feed.gone || this.#jobFails >= JOB_TRIES;
    if (state === "running" && !lost && refusal === null) {
      this.#jobTimer = setTimeout(() => { void feed.refresh(); }, JOB_POLL_MS);
    } else {
      this.#jobEnded = true;
      // A job that may still run stays kept, so a reload tries it again.
      if (state !== "running" || refusal !== null) keep(null);
      if (running !== null) {
        if (state === "running") {
          running.stopped = refusal !== null ? { refused: refusal }
            : { lost: feed.failure?.why ?? (feed.gone ? "loadout went away" : "no answer") };
        }
        this.#last = running;
        this.#running = null;
        this.#showing = false;
      }
      this.#readAll();
      document.dispatchEvent(new CustomEvent(CHANGED));
    }
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

  /** An act as he would type it, with `Copy`, while the note is set. */
  #typed(argv: readonly string[], key: string) {
    const command = commandOf(argv);
    return html`<div class="cmd"><code data-line="command">${command}</code>${this.#copyButton(key, command)}</div>`;
  }

  // -- drawing -----------------------------------------------------------------------------

  render() {
    const feed = this.#view;
    if (feed === null) return nothing;
    const state = pageState(feed, Date.now() - this.#openedAt);
    if (state === "gone") return html`<p class="waiting" data-line="gone" role="status">${GONE}</p>`;
    if (state === "waiting") return nothing;
    if (state === "reading") return html`<p class="waiting" data-line="reading" role="status">${READING}</p>`;
    if (state === "failed") {
      return html`<p class="foot" data-line="failed" role="status">${failedLine(feed.failure?.why ?? "")}</p>`;
    }
    const view = feed.value!;
    const dir = this.#dir?.value ?? null;
    const typed = view.note !== null;
    const note = noteGroup(view);
    const notes = footLines(view, dir);
    if (feed.failure !== null) notes.push(failedLine(feed.failure.why));
    if (this.#goneSaid !== null) notes.push(this.#goneSaid);
    const placed = state === "up" ? placedView(view) : null;
    const opened = this.#at.piece === "item" ? openedItem(view, this.#at.section, this.#at.kind, this.#at.name) : null;
    const drawn = new Set<string>();
    for (const problem of placed?.problems ?? []) drawn.add(problemPlace(problem.key));
    if (placed !== null && placed.problems.length > 0) drawn.add(INSTALL_ALL_PLACE);
    if (opened !== null) drawn.add(itemPlace(JSON.stringify([opened.item.scope, opened.item.kind, opened.item.name])));
    if (state === "up" && this.#at.piece === "global") drawn.add(GLOBAL_PLACE);
    // The last act's line stays until the next act ends; the running act's line takes its
    // place where both go to the same place.
    const places = new Map<string | null, Sent>();
    for (const sent of [this.#last, this.#running]) {
      if (sent !== null) places.set(jobPlace(sent.place, drawn, { placed: placed !== null }), sent);
    }
    const ctx: Ctx = { view, typed, opened, places };
    const groups = html`
      ${note !== null ? html`<section class="grp" data-group="note"><div class="gk">${note.label}</div><div class="gl">
        <div class="line"><span class="h" data-line="note-heading">${note.heading}</span><span class="r d sm">${note.aside}</span></div>
        <p class="d" data-line="note">${note.note}</p>
        <p class="d" data-line="note-last">${note.last}</p></div></section>` : nothing}
      ${state === "problem" ? html`<section class="grp" data-group="placed"><div class="gk">Placed</div>
        <div class="gl"><p data-line="problem">${view.problem}</p></div></section>` : nothing}
      ${placed !== null ? this.#placed(placed, ctx) : nothing}
      ${this.#folder(view, dir, state === "empty")}
      ${state === "up" ? this.#items(ctx) : nothing}
      ${state === "empty" ? this.#emptyGlobal(view) : nothing}`;
    const empty = state === "empty" ? emptyLines(view) : null;
    return html`
      ${empty !== null ? html`<div class="quietline" data-line="empty">${empty.line}</div>
        <div class="first"><p data-line="empty-how">${empty.parts.map((part) => (part.code
          ? html`<code>${part.text}</code>` : part.text))}</p></div>` : nothing}
      <div class="grps" data-part="groups">${groups}</div>
      ${this.#jobsAt(null, ctx)}
      ${notes.map((line) => html`<p class="foot" data-line="foot" role="status">${line}</p>`)}`;
  }

  // -- the line under an act -------------------------------------------------------------

  /** The line under the act the page put at `place`, if any. */
  #jobsAt(place: string | null, ctx: Ctx) {
    const sent = ctx.places.get(place);
    return sent === undefined ? nothing : this.#jobBlock(sent, ctx.view);
  }

  #jobBlock(sent: Sent, view: View) {
    const ended = sent !== this.#running;
    if (sent.said !== null) {
      return html`<div class="job" data-part="job" data-place=${sent.place ?? ""}><p data-line="job">${
        `${sent.command}: ${sent.said}`}</p></div>`;
    }
    const line = followLine(sent.job, sent.command, view, sent.stopped);
    const job = sent.job;
    const kept = ended && job !== null ? keptLines(job.outcome?.kept ?? [], view.home) : [];
    const show = ended && this.#showing && job !== null ? showView(job, view) : null;
    return html`<div class="job" data-part="job" data-place=${sent.place ?? ""}>
      <div class="line"><span data-line="job">${line.line}</span>${ended && line.hasMore
        ? html`<button type="button" class="act d" data-action="show" aria-expanded=${this.#showing ? "true" : "false"}
            @click=${() => { this.#showing = !this.#showing; this.requestUpdate(); }}>${this.#showing ? "Hide" : "Show"}</button>`
        : nothing}</div>
      ${show !== null ? html`<div class="more" data-part="show">
        ${show.problems.map((p) => html`<div class="p"><div class="line"><span>${p.sentence}</span><span class="w">${p.word}</span></div>
          <p>${p.where}</p><p>${p.fix}</p></div>`)}
        ${show.lines.map((text) => html`<p data-line="show-line">${text}</p>`)}</div>` : nothing}
      ${kept.map((k, at) => html`<div class="kept" data-part="kept">
        <div class="line"><span class="t" data-line="kept">${k.text}</span>${this.#copyButton(`kept:${at}`, k.copy)}</div>
        <div class="line back"><span data-line="back">${k.before !== null ? `${k.before} ` : ""}<code>${k.back}</code> ${k.after}</span>${
          this.#copyButton(`back:${at}`, k.back)}</div></div>`)}
    </div>`;
  }

  // -- Placed --------------------------------------------------------------------------------

  /** A problem's act: its button, or with the note set nothing here (the command shows
   * under the fix). A Replace opens its confirm, which this page doesn't draw yet. */
  #actButton(act: ProblemAct | null, typed: boolean, place: string, { quiet = false } = {}) {
    if (act === null || typed || act.verb !== "install") return nothing;
    return html`<button type="button" class="act ${quiet ? "d" : ""}" data-action="install" ?disabled=${this.#busy}
      @click=${() => void this.#act(installAct(act.scope), place)}>${act === INSTALL_ALL ? "Install all" : "Install"}</button>`;
  }

  #command(act: ProblemAct | null, key: string) {
    if (act === null) return nothing;
    const command = actCommand(act);
    return html`<div class="cmd"><code data-line="command">${command}</code>${this.#copyButton(key, command)}</div>`;
  }

  #placed(placed: PlacedView, ctx: Ctx) {
    const typed = ctx.typed;
    const fine = placed.problems.length === 0;
    const allAtVerdict = !fine && placed.installAll === "verdict";
    return html`<section class="grp" data-group="placed"><div class="gk">Placed</div><div class="gl">
      <div class="line"><span class=${placed.heavy ? "h" : ""} data-line="verdict">${placed.verdict}</span>${fine
        ? html`<span class="r d" data-line="counts">${placed.counts}</span>`
        : allAtVerdict ? this.#actButton(INSTALL_ALL, typed, INSTALL_ALL_PLACE) : nothing}</div>
      ${allAtVerdict && typed ? this.#command(INSTALL_ALL, "install-all") : nothing}
      ${allAtVerdict ? this.#jobsAt(INSTALL_ALL_PLACE, ctx) : nothing}
      ${repeat(placed.problems, (problem) => problem.key, (problem) => this.#problem(problem, ctx))}
      ${fine ? nothing : html`<div class="line counts sm"><span class="d" data-line="counts">${placed.counts}</span>${
        allAtVerdict ? nothing : this.#actButton(INSTALL_ALL, typed, INSTALL_ALL_PLACE, { quiet: true })}</div>
        ${!allAtVerdict && typed ? this.#command(INSTALL_ALL, "install-all") : nothing}
        ${allAtVerdict ? nothing : this.#jobsAt(INSTALL_ALL_PLACE, ctx)}`}
      ${this.#jobsAt(PLACED_PLACE, ctx)}
    </div></section>`;
  }

  #problem(problem: Problem, ctx: Ctx) {
    const typed = ctx.typed;
    const place = problemPlace(problem.key);
    return html`<div class="prob" data-problem=${problem.key} data-word=${problem.word}>
      <div class="line l1"><span data-line="sentence">${problem.sentence}</span><b class="r" data-line="word">${problem.word}</b></div>
      <p class="d sm" data-line="where">${problem.where}</p>
      <div class="line sm"><span class="d" data-line="fix">${fixLine(problem, { typed })}${problem.fixNote !== null
        ? html` <span class="d" data-line="fix-note">${problem.fixNote}</span>` : nothing}</span>${
        problem.copy !== null ? this.#copyButton(`copy:${problem.key}`, problem.copy) : this.#actButton(problem.act, typed, place)}</div>
      ${typed ? this.#command(problem.act, problem.key) : nothing}
      ${this.#jobsAt(place, ctx)}
    </div>`;
  }

  // -- Folder ----------------------------------------------------------------------------------

  #folder(view: View, dir: SourceDir | null, empty: boolean) {
    const folder = folderView(view, dir, { empty });
    if (folder === null) return nothing;
    return html`<section class="grp" data-group="folder"><div class="gk">Folder</div><div class="gl">
      <div class="line"><span><span data-line="folder">${folder.path}</span>${folder.line !== null
        ? html`<span class="d" data-line="uncommitted">  ${folder.line}</span>` : nothing}</span>
        <a class="r d sm" href=${`/m/ui/${SETTINGS_ROUTE}`} data-action="settings"
           @click=${(event: MouseEvent) => {
             if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
             event.preventDefault();
             navigate("ui", SETTINGS_ROUTE);
           }}>${SETTINGS_LINK}</a></div>
      ${folder.names !== null ? html`<p class="d sm" data-line="names">${folder.names}</p>` : nothing}
    </div></section>`;
  }

  // -- the items -------------------------------------------------------------------------------

  #items(ctx: Ctx) {
    const items = itemsView(ctx.view);
    return repeat(items.groups, (group) => group.key, (group, at) => this.#group(group, items.harnesses, ctx,
                                                                             { head: at === 0 }));
  }

  #group(group: GroupView, harnesses: readonly string[], ctx: Ctx, { head }: { head: boolean }) {
    const scope = scopeOf(group.section);
    const openKindHere = group.kinds === null ? null : openKind(this.#at, scope);
    return html`<section class="grp" data-group=${group.key}><div class="gk"><span data-line="label">${group.label}</span>
      <span class="n" data-line="count">${group.count}</span></div><div class="gl">
      ${group.empty !== null ? html`<p class="d" data-line="empty">${group.empty}</p>` : html`<div class="grid">
        ${head ? html`<div class="ir hd"><span class="nm"></span>${harnesses.map((harness) => html`<span class="c"
          data-harness=${harness}>${harnessWord(harness)}</span>`)}<span class="o"></span></div>` : nothing}
        ${group.kinds !== null
          ? group.kinds.map((kind) => this.#kind(kind, scope, kind.kind === openKindHere, ctx))
          : group.rows.map((row) => this.#item(row, ctx, { folded: false }))}
        ${group.global !== null ? this.#global(group.global, ctx) : nothing}
      </div>`}</div></section>`;
  }

  #cell(cell: CellView) {
    return html`<span class="c ${TONES[cell.tone]}" data-harness=${cell.harness} data-word=${cell.word ?? ""}>${cell.word ?? ""}</span>`;
  }

  /** `Open`, or `Close` on what is open. */
  #openButton(open: boolean, address: Address, back: Address, what: string) {
    return html`<button type="button" class="o" data-action=${open ? "close" : "open"} aria-expanded=${open ? "true" : "false"}
      aria-label=${`${open ? "Close" : "Open"} ${what}`}
      @click=${() => this.#open(open ? back : address)}>${open ? "Close" : "Open"}</button>`;
  }

  #item(row: ItemRow, ctx: Ctx, { folded }: { folded: boolean }) {
    const scope = row.item.scope;
    const open = opensItem(this.#at, scope, row.kind, row.name) && ctx.opened !== null;
    return html`<div class="ir" data-item=${row.key}><span class="nm">${row.name}<span class="k d">${
      folded ? "" : row.kindWord}</span></span>
      ${row.cells.map((cell) => this.#cell(cell))}${this.#openButton(open, itemAddress(scope, row.kind, row.name),
                                                                     closed(this.#at, folded), row.name)}</div>
      ${row.second !== null ? html`<p class="two d" data-line="second">${row.second}</p>` : nothing}
      ${open ? this.#opened(ctx.opened!, row.key, ctx) : nothing}`;
  }

  #kind(kind: KindRow, scope: string, open: boolean, ctx: Ctx) {
    return html`<div class="ir" data-kind=${kind.kind}><span class="nm">${kindFolded(kind.kind)}<span class="k d">${
      kind.rows.length}</span></span>
      ${kind.cells.map((cell) => html`<span class="c k ${TONES[cell.tone]}" data-harness=${cell.harness}>${cell.text ?? ""}</span>`)}${
        this.#openButton(open, { piece: "kind", section: scope, kind: kind.kind }, NOTHING_OPEN, kind.label)}</div>
      ${open ? html`<div class="kopen" data-part="kind">${kind.rows.map((row) => this.#item(row, ctx, { folded: true }))}</div>`
        : nothing}`;
  }

  #global(row: GlobalRow, ctx: Ctx) {
    const open = this.#at.piece === "global";
    return html`<div class="ir" data-item="global"><span class="nm">${row.label}<span class="k d" data-line="mode">${
      row.mode}</span></span>
      ${row.cells.map((cell) => this.#cell(cell))}${this.#openButton(open, { piece: "global" }, NOTHING_OPEN, row.label)}</div>
      ${open ? this.#openedGlobal(openedGlobal(ctx.view), ctx) : nothing}`;
  }

  // -- the opened item -------------------------------------------------------------------------

  #opened(opened: OpenedItem, key: string, ctx: Ctx) {
    const from = fromParts(opened.from);
    const place = itemPlace(key);
    const typed = ctx.typed;
    return html`<div class="det" data-part="opened" data-item=${key}>
      <div class="r"><span class="k">From</span><span class="f" data-line="from">${from.lead}${from.rest !== null
        ? html` <span class="d">· ${from.rest}</span>` : nothing}</span><span></span></div>
      ${opened.lines.map((line) => this.#harnessLine(line, typed))}
      ${opened.enableAll !== null ? html`<div class="foot">${typed ? nothing
        : html`<button type="button" class="act" data-action="enable-all" ?disabled=${this.#busy}
            @click=${() => void this.#act(opened.enableAll!.act, place)}>${opened.enableAll.label}</button>`}
        ${opened.enableAll.note !== "" ? html`<span class="d" data-line="enable-all">${opened.enableAll.note}</span>` : nothing}
        ${typed ? this.#typed(opened.enableAll.act.argv, `${key}:enable-all`) : nothing}</div>` : nothing}
      <div class="foot">${typed ? html`<span class="d">${opened.remove.label}</span>`
        : html`<button type="button" class="act d" data-action="remove" ?disabled=${this.#busy}
            @click=${() => void this.#act(opened.remove.act, place)}>${opened.remove.label}</button>`}
        <span class="d" data-line="remove">${opened.remove.note}</span>
        ${typed ? this.#typed(opened.remove.act.argv, `${key}:remove`) : nothing}</div>
      ${this.#jobsAt(place, ctx)}
    </div>`;
  }

  #harnessLine(line: HarnessLine, typed: boolean) {
    const heavy = line.word !== null && line.word !== "placed" && line.word !== "off";
    const sub = [line.why, ...line.notes].filter((text): text is string => text !== null && text !== "");
    // A line that can be replaced offers Replace (its confirm comes later); else its toggle.
    const toggle = line.replace === null ? line.toggle : null;
    const argv = line.replace !== null ? replaceArgv(line.replace) : toggle?.act.argv ?? null;
    return html`<div class="r" data-harness=${line.harness}><span class="k">${line.label}</span><span class="f">${
      line.word !== null ? html`<span class=${heavy ? "h" : ""} data-line="word">${line.word}</span>` : nothing}${
      line.where !== null ? html` <span class="d" data-line="where">· ${line.where}</span>` : nothing}${
      sub.length > 0 ? html`<span class="sub" data-line="why">${sub.join(" ")}</span>` : nothing}${
      typed && argv !== null ? this.#typed(argv, `${line.harness}:act`) : nothing}</span>${
      toggle !== null && !typed ? html`<button type="button" class="x" data-action=${toggle.label === "Turn on" ? "turn-on" : "turn-off"}
        ?disabled=${this.#busy} @click=${() => void this.#act(toggle.act, this.#openedPlace())}>${toggle.label}</button>`
        : html`<span></span>`}</div>`;
  }

  /** The place of the item open now. */
  #openedPlace(): string | null {
    const at = this.#at;
    return at.piece === "item" ? itemPlace(JSON.stringify([at.section, at.kind, at.name])) : null;
  }

  // -- the opened global file ------------------------------------------------------------------

  #openedGlobal(global: OpenedGlobal, ctx: Ctx) {
    const typed = ctx.typed;
    const off = (): void => { this.#confirmOff = true; this.requestUpdate(); };
    return html`<div class="det" data-part="opened" data-item="global">
      ${global.from !== null ? html`<div class="r"><span class="k">From</span><span class="f" data-line="from">${
        global.from.replace(/^From /, "")}</span><span></span></div>` : nothing}
      <div class="r"><span class="k">Mode</span><span class="f"><span class="tri" role="group" aria-label="Mode">${
        global.modes.map((mode) => mode.current
          ? html`<span class="s" data-mode=${mode.mode} aria-current="true">${mode.label}</span>`
          : typed ? html`<span data-mode=${mode.mode}>${mode.label}</span>`
          : html`<button type="button" data-mode=${mode.mode} ?disabled=${this.#busy}
              @click=${() => (mode.act === null ? off() : void this.#act(mode.act, GLOBAL_PLACE))}>${mode.label}</button>`)}
      </span>${typed ? global.modes.filter((mode) => !mode.current).map((mode) => this.#typed(
        mode.act === null ? globalOffAct("").argv : mode.act.argv, `mode:${mode.mode}`)) : nothing}</span><span></span></div>
      <div class="r"><span class="k"></span><span class="f d" data-line="mode-line">${global.line}</span><span></span></div>
      ${global.files.map((file) => {
        const shown = this.#diffs.has(file.harness);
        return html`<div class="r" data-harness=${file.harness}><span class="k">${file.label}</span><span class="f"><span
          data-line="file">${file.file}</span> <span class="d">${[file.adds, file.next].filter((t) => t !== null)
            .map((t) => `· ${t}`).join(" ")}</span>${shown && file.diff !== null
            ? html`<pre data-line="diff">${file.diff}</pre>` : nothing}</span>${file.diff !== null
          ? html`<button type="button" class="x" data-action="diff" aria-expanded=${shown ? "true" : "false"} @click=${() => {
              const next = new Set(this.#diffs);
              if (shown) next.delete(file.harness);
              else next.add(file.harness);
              this.#diffs = next;
              this.requestUpdate();
            }}>${shown ? "Hide" : "Show"}</button>` : html`<span></span>`}</div>`;
      })}
      ${global.turnOff !== null ? html`<div class="foot">${typed ? html`<span class="d">${global.turnOff.label}</span>`
        : html`<button type="button" class="act d" data-action="global-off" ?disabled=${this.#busy || this.#confirmOff}
            @click=${off}>${global.turnOff.label}</button>`}
        <span class="d" data-line="turn-off">${global.turnOff.note}</span>
        ${typed ? this.#typed(globalOffAct("").argv, "global-off") : nothing}</div>` : nothing}
      ${this.#confirmOff && !typed ? html`<div class="cf" data-part="confirm" role="group" aria-label="Turn off">
        <p class="q" data-line="question">${global.turnOff?.confirm ?? ""}</p>
        <div class="btns"><button type="button" class="btn" data-action="confirm-off" ?disabled=${this.#busy} @click=${() => {
          this.#confirmOff = false;
          void this.#act(globalOffAct(jobId(crypto.getRandomValues(new Uint8Array(16)))), GLOBAL_PLACE);
        }}>Turn off</button><button type="button" class="lnk" data-action="cancel" @click=${() => {
          this.#confirmOff = false;
          this.requestUpdate();
        }}>Cancel</button></div></div>` : nothing}
      ${this.#jobsAt(GLOBAL_PLACE, ctx)}
    </div>`;
  }

  #emptyGlobal(view: View) {
    const global = emptyGlobal(view);
    return html`<section class="grp" data-group="global"><div class="gk">Global file</div><div class="gl">
      <p><span data-line="mode">${global.mode}</span>${global.line !== null
        ? html`<span class="d" data-line="mode-line">  ${global.line}</span>` : nothing}</p></div></section>`;
  }
}

/** What one drawing of the page shares between its parts. */
interface Ctx {
  view: View;
  /** The note is set: every act shows as its command. */
  typed: boolean;
  /** The item open now, or null. */
  opened: OpenedItem | null;
  /** The line under an act, by the place it goes (null: the foot). */
  places: Map<string | null, Sent>;
}

if (!customElements.get(TAG)) customElements.define(TAG, LoadoutPage);

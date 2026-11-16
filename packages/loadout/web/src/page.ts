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
//   section: one row per item and a column per harness of the harness table; past twelve
//   items a section folds by kind. The global file is Your items' last row.
// - The dim foot lines: the view's notes, and why the folder can't be read.
//
// It places what the models (`placed-model.ts`, `items-model.ts`, `frame-model.ts`)
// return; every word comes from there. No accent and no warning colour: a problem rises
// by its order and weight 600.
//
// It reads `/api/view` and `/api/folder` when it opens, when its tab is shown again, every
// 60 seconds while shown, and after a job ends. An act runs as a page job: the page sends
// it, follows the job's status path, and keeps every act's control off while one runs. It
// keeps that path in the tab's `sessionStorage` until the job ends, so after a reload it
// follows the job again. When a job ends the page reads again and sends `CHANGED` on the
// document, so every loadout component reads again too.

import { css, html, LitElement, nothing } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { api, navigate } from "/m/ui/client.js";

import { CHANGED, Feed, parseJob, parseSourceDir, parseView } from "./feed.ts";
import type { Job, SourceDir, View } from "./feed.ts";
import { installAct } from "./acts-model.ts";
import type { PageAct } from "./acts-model.ts";
import {
  actCommand, COPIED, COPIED_MS, COPY, emptyGlobal, emptyLines, failedLine, fixLine, folderView, footLines, GONE,
  noteGroup, pageState, READING, READING_AFTER_MS, REFRESH_MS, SETTINGS_LINK, SETTINGS_ROUTE,
} from "./frame-model.ts";
import { itemsView } from "./items-model.ts";
import type { CellView, GlobalRow, GroupView, ItemRow, KindRow, Tone } from "./items-model.ts";
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

  /* The item grid: a line per item, the name and then a fixed column per harness. */
  .ir { display: flex; align-items: baseline; gap: 12px; padding: 4px 0; }
  .ir.hd { color: var(--moxi-text-dim); font-size: 13px; line-height: 18px; padding: 0 0 6px; }
  .ir .nm { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
  .ir .nm .k { margin-left: 8px; }
  .ir .c { flex: 0 0 94px; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .two { margin-top: -4px; padding-bottom: 4px; font-size: 13px; line-height: 18px; }

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

  /** The job the page follows, and whether it has ended. */
  #job: Feed<Job> | null = null;
  #jobEnded = true;
  #jobFails = 0;
  #jobTimer: ReturnType<typeof setTimeout> | undefined;
  /** An act is being sent. */
  #sending = false;
  /** Why the last act didn't start. */
  #said: string | null = null;

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
    if (job !== null) this.#follow(job.path, job.label);
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

  #readAll(): void {
    void this.#view?.refresh();
    void this.#dir?.refresh();
  }

  /** Whether an act is on its way or its job runs: every act's control is off then. */
  get #busy(): boolean {
    return this.#sending || !this.#jobEnded;
  }

  // -- acts ------------------------------------------------------------------------------

  async #act(act: PageAct): Promise<void> {
    if (this.#busy) return;
    this.#sending = true;
    this.#said = null;
    this.requestUpdate();
    try {
      const answer = await api("loadout", act.path, act.body);
      const started = startedOf(answer.status, answer.data);
      if (started.kind === "job") this.#follow(started.path, act.argv.join(" "));
      else this.#said = started.text;
    } finally {
      this.#sending = false;
      this.requestUpdate();
    }
  }

  /** Follows the job at status path `path` until it ends. */
  #follow(path: string, label: string): void {
    this.#job?.close();
    clearTimeout(this.#jobTimer);
    this.#jobEnded = false;
    this.#jobFails = 0;
    keep({ path, label });
    const feed: Feed<Job> = new Feed<Job>(() => api("loadout", path), parseJob, () => this.#jobRead(feed));
    this.#job = feed;
    void feed.refresh();
  }

  #jobRead(feed: Feed<Job>): void {
    if (feed !== this.#job || this.#jobEnded) return;
    this.#jobFails = feed.failure !== null ? this.#jobFails + 1 : 0;
    const state = feed.value?.state ?? "running";
    // A refused read (no such job) ends it; one that gave no answer may not have.
    const refused = jobRefusal(feed.failure) !== null;
    const lost = feed.gone || this.#jobFails >= JOB_TRIES;
    if (state === "running" && !lost && !refused) {
      this.#jobTimer = setTimeout(() => { void feed.refresh(); }, JOB_POLL_MS);
    } else {
      this.#jobEnded = true;
      // A job that may still run stays kept, so a reload tries it again.
      if (state !== "running" || refused) keep(null);
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
    if (this.#said !== null) notes.push(this.#said);
    const groups = html`
      ${note !== null ? html`<section class="grp" data-group="note"><div class="gk">${note.label}</div><div class="gl">
        <div class="line"><span class="h" data-line="note-heading">${note.heading}</span><span class="r d sm">${note.aside}</span></div>
        <p class="d" data-line="note">${note.note}</p>
        <p class="d" data-line="note-last">${note.last}</p></div></section>` : nothing}
      ${state === "problem" ? html`<section class="grp" data-group="placed"><div class="gk">Placed</div>
        <div class="gl"><p data-line="problem">${view.problem}</p></div></section>` : nothing}
      ${state === "up" ? this.#placed(placedView(view), typed) : nothing}
      ${this.#folder(view, dir, state === "empty")}
      ${state === "up" ? this.#items(view) : nothing}
      ${state === "empty" ? this.#emptyGlobal(view) : nothing}`;
    const empty = state === "empty" ? emptyLines(view) : null;
    return html`
      ${empty !== null ? html`<div class="quietline" data-line="empty">${empty.line}</div>
        <div class="first"><p data-line="empty-how">${empty.parts.map((part) => (part.code
          ? html`<code>${part.text}</code>` : part.text))}</p></div>` : nothing}
      <div class="grps" data-part="groups">${groups}</div>
      ${notes.map((line) => html`<p class="foot" data-line="foot" role="status">${line}</p>`)}`;
  }

  /** A problem's act: its button, or with the note set nothing here (the command shows
   * under the fix). A Replace opens its confirm, which this page doesn't draw yet. */
  #actButton(act: ProblemAct | null, typed: boolean, { quiet = false } = {}) {
    if (act === null || typed || act.verb !== "install") return nothing;
    return html`<button type="button" class="act ${quiet ? "d" : ""}" data-action="install" ?disabled=${this.#busy}
      @click=${() => void this.#act(installAct(act.scope))}>${act === INSTALL_ALL ? "Install all" : "Install"}</button>`;
  }

  #command(act: ProblemAct | null, key: string) {
    if (act === null) return nothing;
    const command = actCommand(act);
    return html`<div class="cmd"><code data-line="command">${command}</code>${this.#copyButton(key, command)}</div>`;
  }

  #placed(placed: PlacedView, typed: boolean) {
    const fine = placed.problems.length === 0;
    const allAtVerdict = !fine && placed.installAll === "verdict";
    return html`<section class="grp" data-group="placed"><div class="gk">Placed</div><div class="gl">
      <div class="line"><span class=${placed.heavy ? "h" : ""} data-line="verdict">${placed.verdict}</span>${fine
        ? html`<span class="r d" data-line="counts">${placed.counts}</span>`
        : allAtVerdict ? this.#actButton(INSTALL_ALL, typed) : nothing}</div>
      ${allAtVerdict && typed ? this.#command(INSTALL_ALL, "install-all") : nothing}
      ${repeat(placed.problems, (problem) => problem.key, (problem) => this.#problem(problem, typed))}
      ${fine ? nothing : html`<div class="line counts sm"><span class="d" data-line="counts">${placed.counts}</span>${
        allAtVerdict ? nothing : this.#actButton(INSTALL_ALL, typed, { quiet: true })}</div>
        ${!allAtVerdict && typed ? this.#command(INSTALL_ALL, "install-all") : nothing}`}
    </div></section>`;
  }

  #problem(problem: Problem, typed: boolean) {
    return html`<div class="prob" data-problem=${problem.key} data-word=${problem.word}>
      <div class="line l1"><span data-line="sentence">${problem.sentence}</span><b class="r" data-line="word">${problem.word}</b></div>
      <p class="d sm" data-line="where">${problem.where}</p>
      <div class="line sm"><span class="d" data-line="fix">${fixLine(problem, { typed })}${problem.fixNote !== null
        ? html` <span class="d" data-line="fix-note">${problem.fixNote}</span>` : nothing}</span>${
        problem.copy !== null ? this.#copyButton(`copy:${problem.key}`, problem.copy) : this.#actButton(problem.act, typed)}</div>
      ${typed ? this.#command(problem.act, problem.key) : nothing}
    </div>`;
  }

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

  #items(view: View) {
    const items = itemsView(view);
    return repeat(items.groups, (group) => group.key, (group, at) => this.#group(group, items.harnesses,
                                                                             { head: at === 0 }));
  }

  #group(group: GroupView, harnesses: readonly string[], { head }: { head: boolean }) {
    return html`<section class="grp" data-group=${group.key}><div class="gk"><span data-line="label">${group.label}</span>
      <span class="n" data-line="count">${group.count}</span></div><div class="gl">
      ${group.empty !== null ? html`<p class="d" data-line="empty">${group.empty}</p>` : html`<div class="grid">
        ${head ? html`<div class="ir hd"><span class="nm"></span>${harnesses.map((harness) => html`<span class="c"
          data-harness=${harness}>${harnessWord(harness)}</span>`)}</div>` : nothing}
        ${group.kinds !== null ? group.kinds.map((kind) => this.#kind(kind)) : group.rows.map((row) => this.#item(row))}
        ${group.global !== null ? this.#global(group.global) : nothing}
      </div>`}</div></section>`;
  }

  #cell(cell: CellView) {
    return html`<span class="c ${TONES[cell.tone]}" data-harness=${cell.harness} data-word=${cell.word ?? ""}>${cell.word ?? ""}</span>`;
  }

  #item(row: ItemRow) {
    return html`<div class="ir" data-item=${row.key}><span class="nm">${row.name}<span class="k d">${row.kindWord}</span></span>
      ${row.cells.map((cell) => this.#cell(cell))}</div>
      ${row.second !== null ? html`<p class="two d" data-line="second">${row.second}</p>` : nothing}`;
  }

  #kind(kind: KindRow) {
    return html`<div class="ir" data-kind=${kind.kind}><span class="nm">${kindFolded(kind.kind)}<span class="k d">${
      kind.rows.length}</span></span>
      ${kind.cells.map((cell) => html`<span class="c ${TONES[cell.tone]}" data-harness=${cell.harness}>${cell.text ?? ""}</span>`)}</div>`;
  }

  #global(row: GlobalRow) {
    return html`<div class="ir" data-item="global"><span class="nm">${row.label}<span class="k d" data-line="mode">${
      row.mode}</span></span>
      ${row.cells.map((cell) => this.#cell(cell))}</div>`;
  }

  #emptyGlobal(view: View) {
    const global = emptyGlobal(view);
    return html`<section class="grp" data-group="global"><div class="gk">Global file</div><div class="gl">
      <p><span data-line="mode">${global.mode}</span>${global.line !== null
        ? html`<span class="d" data-line="mode-line">  ${global.line}</span>` : nothing}</p></div></section>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, LoadoutPage);

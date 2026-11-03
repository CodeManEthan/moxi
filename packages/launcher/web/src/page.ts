// <moxi-launcher-page route="...">: the launcher's panel. Three parts:
//
// - Launch: one preset to start from (a seat, a project preset or none), and the launch
//   form, the UI shell's form renderer over `/api/catalog`, filled from `/api/form` with
//   the values the preset and the user's defaults give. Choosing a preset draws a new form;
//   changing the harness keeps the form and fills the new harness's rows. "Preview" sends
//   the whole form to `/api/preview`: each field with a value, and each field left empty
//   as a row to leave out. It answers with the argv, the environment stamps, the warnings,
//   the rows left out and every refusal the launch's check finds. A launch in a slot or headless is then
//   launched with the preview's ticket (`/api/launch`, 202); its row arrives by the
//   stream's topic `launches`, and a refusal met before the row was written is read from
//   the 202's status path. A launch in a terminal is never run by the page: the preview
//   gives the `moxi launcher run` command to copy.
// - Sessions: the launcher's `sessions` block, placed as any host places it: each running
//   launched session's recycle and keep-warm state, and whether it needs a look.
// - Launches: the launch log's newest rows from the topic `launches`, with a `state`
//   component placed beside each live one (the topic `sessions` says which are live).
// - Past sessions: `/api/sessions`, read again when either topic moves. A slot session
//   resumes through `/api/resume` (202, then followed by its status path); a terminal one
//   is answered with the `moxi launcher resume` command to copy.

import { css, html, LitElement, nothing } from "lit";
import { keyed } from "lit/directives/keyed.js";
import { repeat } from "lit/directives/repeat.js";
import { api, place, subscribe } from "/m/ui/client.js";
import type { FieldDescriptor, FormElement, FormSubmit, FormValue } from "/m/ui/client.js";

import { Feed, parseLaunches, parseStates } from "./feed.ts";
import type { Launches, Refusal, States } from "./feed.ts";
import { when } from "./views.ts";
import {
  choiceKey, choiceOf, chosenPreset, commandLine, emptyGroups, fieldsFor, filledFields, FOLLOW_FOR_MS, FOLLOW_MS,
  followedOf, formPath, HARNESS, keepLabel, launchBody, launchesView, NO_PRESET, olderPath, outcomeOf,
  parseCatalog, parseForm, parsePasts, parsePresets, parsePreview, pastView, presetLines, presetProblems,
  presetsOf, refusalLine, requestOf,
} from "./panel.ts";
import type {
  Catalog, Choice, Followed, Form, LaunchBody, LaunchItem, Outcome, Past, PastItem, Pasts, Presets, Preview,
} from "./panel.ts";

const TAG = "moxi-launcher-page";
/** How long after a topic moves the past sessions are read again, so a burst reads once. */
const PAST_DELAY_MS = 300;

const STYLES = css`
  :host { display: block; min-width: 0; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; }
  h1 { margin: 0 0 calc(var(--moxi-gap) * 2); font-size: 1.4em; }
  h2 { margin: 0; font-size: 1.15em; }
  h3 { margin: 0; font-size: 1em; }
  section { display: grid; gap: calc(var(--moxi-gap) * 1.5); min-width: 0; margin-bottom: calc(var(--moxi-gap) * 3);
            padding: 12px; border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius-lg);
            background: var(--moxi-bg-raised); }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .block { display: block; padding: 6px 8px; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken);
           white-space: pre-wrap; word-break: break-all; }
  .help, .dim { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .lede { padding-left: 8px; border-left: 3px solid var(--moxi-accent); }
  .warn { color: var(--moxi-warn); }
  .problem { color: var(--moxi-error); }
  .ok { color: var(--moxi-accent); }
  ul { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; min-width: 0; }
  .choose { display: flex; flex-wrap: wrap; gap: var(--moxi-gap) calc(var(--moxi-gap) * 2); min-width: 0; }
  .choose label { display: grid; gap: 2px; flex: 1 1 12em; min-width: 0; font-size: var(--moxi-font-size-sm); font-weight: 600; }
  .choose label.check { display: flex; align-items: center; gap: var(--moxi-gap); flex-basis: 100%; font-weight: 400; }
  select, button { font: inherit; color: var(--moxi-text); }
  select { width: 100%; min-width: 0; max-width: 36rem; padding: 4px 8px; border: 1px solid var(--moxi-border-strong);
           border-radius: var(--moxi-radius); background: var(--moxi-bg); }
  input[type="checkbox"] { width: 1rem; height: 1rem; margin: 0; accent-color: var(--moxi-accent); }
  button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg-raised); cursor: pointer; }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button:disabled { cursor: not-allowed; opacity: 0.6; }
  button.go { justify-self: start; padding: 4px 16px; background: var(--moxi-accent); border-color: var(--moxi-accent); color: var(--moxi-bg); }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  .preview { display: grid; gap: var(--moxi-gap); min-width: 0; padding-top: var(--moxi-gap); border-top: 1px solid var(--moxi-border); }
  .refused li { padding: 4px 8px; border-left: 3px solid var(--moxi-error); background: var(--moxi-error-soft);
                color: var(--moxi-error); overflow-wrap: anywhere; }
  .copy { display: grid; gap: 4px; min-width: 0; }
  .copy .row { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); }
  .stamps li, .warnings li { overflow-wrap: anywhere; }
  details > summary { cursor: pointer; color: var(--moxi-text-dim); }
  .list > li { display: grid; gap: 2px; min-width: 0; padding: 6px 0; border-top: 1px solid var(--moxi-border); }
  .list > li.mine { border-left: 3px solid var(--moxi-accent); padding-left: 6px; }
  .first { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--moxi-gap); min-width: 0; }
  .label { flex: 1 1 12em; min-width: 0; overflow-wrap: anywhere; }
  .tag { padding: 0 6px; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken);
         font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .cell { display: inline-block; max-width: 100%; min-width: 0; }
  .more { justify-self: start; }
  .sessions { min-width: 0; margin-bottom: calc(var(--moxi-gap) * 3); }
  .sessions:empty { display: none; }
`;

/** A launch or resume this panel started, followed until its row or a refusal shows. */
interface Follow {
  request: string;
  path: string;
  /** For a resume, the past session's launch it resumes. */
  of: string | null;
  started: number;
  state: Followed;
  /** Followed no longer: its row or refusal came, or its time ran out. */
  done: boolean;
}

/** What a resume button answered, by the past session's launch. */
interface Resumed {
  busy: boolean;
  outcome: Outcome | null;
}

export class LauncherPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";

  #catalog: Feed<Catalog> | null = null;
  #presets: Feed<Presets> | null = null;
  #launches: Feed<Launches> | null = null;
  #states: Feed<States> | null = null;
  #pasts: Feed<Pasts> | null = null;
  #older: Past[] = [];
  #olderBusy = false;
  #stops: (() => void)[] = [];
  #pastTimer: ReturnType<typeof setTimeout> | undefined;
  #followTimer: ReturnType<typeof setInterval> | undefined;

  // The form: the preset chosen, the `/api/form` answer it is filled from, the harness it
  // is drawn for, its fields, and the withheld texts with the ones whose keep box is ticked.
  #choice: Choice = NO_PRESET;
  #filled: Form | null = null;
  #formFailure: string | null = null;
  /** Counts `/api/form` reads, so only the latest one's answer is drawn. */
  #asked = 0;
  /** The harness a read in flight asks for, or "". */
  #asking = "";
  /** Counts the form elements drawn: a new preset choice draws a new one. */
  #element = 0;
  #harness = "";
  #fields: FieldDescriptor[] = [];
  #drawn: FieldDescriptor[] | null = null;
  #withheld = new Set<string>();
  #keep = new Set<string>();
  #errored = new Set<string>();
  #offPosture = false;

  // The preview, the body it was made for, and what launching it answered.
  #previewing = false;
  #preview: Preview | null = null;
  #previewFailure: string | null = null;
  #previewed: LaunchBody | null = null;
  #current = "";
  #launching = false;
  #outcome: Outcome | null = null;
  #follows = new Map<string, Follow>();
  #resumes = new Map<string, Resumed>();
  #copied = "";
  // The `sessions` block: placed once, and whether the shell could place it.
  #sessionsPlaced = false;
  #sessionsMissing = false;

  #shown = (): void => {
    if (document.visibilityState !== "visible") return;
    void this.#launches?.refresh();
    void this.#states?.refresh();
    void this.#pasts?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const changed = () => this.requestUpdate();
    const catalog = new Feed<Catalog>(() => api("launcher", "api/catalog"), parseCatalog, () => this.#catalogRead());
    const presets = new Feed<Presets>(() => api("launcher", "api/presets"), parsePresets, changed);
    const launches = new Feed<Launches>(() => api("launcher", "api/launches"), parseLaunches, () => this.#launchesRead());
    const states = new Feed<States>(() => api("launcher", "api/state"), parseStates, changed);
    const pasts = new Feed<Pasts>(() => api("launcher", "api/sessions"), parsePasts, changed);
    this.#catalog = catalog;
    this.#presets = presets;
    this.#launches = launches;
    this.#states = states;
    this.#pasts = pasts;
    void catalog.refresh();
    void presets.refresh();
    void this.#readForm(true);
    void pasts.refresh();
    void launches.refresh().then(() => {
      if (this.#launches !== launches) return;
      this.#stops.push(subscribe("launcher", "launches", (data) => {
        launches.topic(data);
        this.#pastsLater();
      }));
    });
    void states.refresh().then(() => {
      if (this.#states !== states) return;
      this.#stops.push(subscribe("launcher", "sessions", (data) => {
        states.topic(data);
        this.#pastsLater();
      }));
    });
    document.addEventListener("visibilitychange", this.#shown);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    for (const feed of [this.#catalog, this.#presets, this.#launches, this.#states, this.#pasts]) feed?.close();
    this.#catalog = null;
    this.#presets = null;
    this.#pasts = null;
    this.#launches = null;
    this.#states = null;
    for (const stop of this.#stops) stop();
    this.#stops = [];
    clearTimeout(this.#pastTimer);
    clearInterval(this.#followTimer);
    this.#followTimer = undefined;
    document.removeEventListener("visibilitychange", this.#shown);
  }

  // --- reading --------------------------------------------------------------------------

  #catalogRead(): void {
    this.#refill();
    this.requestUpdate();
  }

  /**
   * Reads `/api/form` for the chosen preset, for `harness` when one is asked. A new choice
   * (`fresh`) draws a new form element from the answer, each withheld text's keep box
   * ticked; a harness change keeps the element, so the user's edits to the rows both
   * harnesses have stay, and fills the new harness's rows.
   */
  async #readForm(fresh: boolean, harness = ""): Promise<void> {
    const asked = (this.#asked += 1);
    this.#asking = harness;
    if (fresh) {
      this.#filled = null;
      this.#formFailure = null;
      this.#refill();
      this.requestUpdate();
    }
    const result = await api("launcher", formPath(this.#choice, harness));
    if (asked !== this.#asked || !this.isConnected) return;
    this.#asking = "";
    const form = result.status === 200 ? parseForm(result.data) : null;
    if (form === null || form.state === "error") {
      this.#formFailure = form?.detail
        ?? (result.status === 0 ? "no answer from the shell" : `the launcher's page server answered ${result.status}`);
      this.requestUpdate();
      return;
    }
    this.#formFailure = null;
    const withheld = new Set(form.rows.filter((row) => row.withheld).map((row) => row.name));
    this.#keep = new Set([...withheld].filter((name) => fresh || !this.#withheld.has(name) || this.#keep.has(name)));
    this.#withheld = withheld;
    if (fresh) this.#element += 1;
    this.#filled = form;
    this.#harness = form.harness;
    this.#refill();
    this.requestUpdate();
  }

  /** The form's fields from the catalog and the `/api/form` answer, a new array each time;
   * the form keeps what the user left in each row that stays. */
  #refill(): void {
    const catalog = this.#catalog?.value ?? null;
    this.#fields = this.#filled === null ? [] : filledFields(fieldsFor(catalog, this.#harness), this.#filled, this.#choice);
  }

  #launchesRead(): void {
    const rows = this.#launches?.value?.launches ?? [];
    for (const follow of this.#follows.values()) {
      const row = rows.find((item) => item.request_id === follow.request);
      if (row !== undefined) {
        follow.state = { kind: "row", row };
        follow.done = true;
      }
    }
    this.requestUpdate();
  }

  #pastsLater(): void {
    clearTimeout(this.#pastTimer);
    this.#pastTimer = setTimeout(() => { void this.#pasts?.refresh(); }, PAST_DELAY_MS);
  }

  #form(): FormElement | null {
    return this.renderRoot.querySelector("moxi-ui-form");
  }

  // --- the form -------------------------------------------------------------------------

  /** Each event inside the form: once the form has taken it, see what changed. */
  #poke = (): void => {
    setTimeout(() => this.#mark(), 0);
  };

  /** After the user changed the form: a new harness is filled from `/api/form`, a group's
   * other rows are emptied, and the request is worked out again. */
  #mark(values?: Record<string, FormValue>): void {
    const form = this.#form();
    if (form === null) return;
    const now = values ?? form.values();
    const chosen = now[HARNESS];
    const catalog = this.#catalog?.value ?? null;
    if (typeof chosen === "string" && chosen === this.#harness && this.#asking !== "") {
      // Set back before the other harness's rows came: that read is no longer wanted.
      this.#asked += 1;
      this.#asking = "";
    } else if (typeof chosen === "string" && chosen !== this.#harness && chosen !== this.#asking
               && catalog?.harnesses.some((item) => item.name === chosen)) {
      void this.#readForm(false, chosen);
    }
    this.#settle(now);
  }

  /** Applies the group rule to what the form holds now and works out the request again. */
  #settle(now: Record<string, FormValue>): void {
    const grouped = emptyGroups(this.#fields, now, this.#withheld, this.#keep);
    const keepChanged = grouped.keep.size !== this.#keep.size;
    this.#keep = grouped.keep;
    if (grouped.fields !== this.#fields) {
      // Drawn next; `updated` then works the request out from the emptied rows.
      this.#fields = grouped.fields;
      this.requestUpdate();
      return;
    }
    const current = JSON.stringify(this.#body(now));
    if (current !== this.#current || keepChanged) {
      this.#current = current;
      this.requestUpdate();
    }
  }

  #body(values: Record<string, FormValue>): LaunchBody {
    return launchBody(this.#choice, requestOf(this.#fields, values, this.#withheld, this.#keep), this.#offPosture);
  }

  #choose(event: Event): void {
    this.#choice = choiceOf((event.target as HTMLSelectElement).value);
    void this.#readForm(true);
  }

  #keepChanged(name: string, event: Event): void {
    if ((event.target as HTMLInputElement).checked) this.#keep.add(name);
    else this.#keep.delete(name);
    this.#mark();
    this.requestUpdate();
  }

  #offChanged(event: Event): void {
    this.#offPosture = (event.target as HTMLInputElement).checked;
    this.#mark();
  }

  async #submitted(event: CustomEvent<FormSubmit>): Promise<void> {
    if (this.#previewing) return;
    this.#mark(event.detail.values);
    const body = this.#body(event.detail.values);
    this.#previewing = true;
    this.#previewFailure = null;
    this.#outcome = null;
    this.requestUpdate();
    const result = await api("launcher", "api/preview", body);
    this.#previewing = false;
    const preview = result.status === 200 ? parsePreview(result.data) : null;
    if (preview === null) {
      this.#preview = null;
      this.#previewed = null;
      this.#previewFailure = result.status === 0 ? "no answer from the shell"
        : `the launcher's page server answered ${result.status}${result.data?.error ? `: ${result.data.error}` : ""}`;
    } else {
      this.#preview = preview;
      this.#previewed = body;
      this.#current = JSON.stringify(body);
    }
    this.#markFields(preview?.refused ?? []);
    this.requestUpdate();
  }

  /** Shows each refusal that names a row under that row of the form. */
  #markFields(refused: Refusal[]): void {
    const form = this.#form();
    if (form === null) return;
    for (const name of this.#errored) form.setMessage(name, []);
    this.#errored.clear();
    const names = new Set(this.#fields.map((field) => field.name));
    for (const found of refused) {
      if (found.row === null || !names.has(found.row)) continue;
      form.setError(found.row, refusalLine(found));
      this.#errored.add(found.row);
    }
  }

  async #launch(): Promise<void> {
    const preview = this.#preview;
    const body = this.#previewed;
    if (this.#launching || preview?.ticket == null || body === null) return;
    this.#launching = true;
    this.requestUpdate();
    const result = await api("launcher", "api/launch", { ...body, ticket: preview.ticket });
    this.#launching = false;
    const outcome = outcomeOf(result);
    this.#outcome = outcome;
    if (outcome.kind === "started") {
      this.#follow(outcome.status.request, outcome.status.path, null);
      // A ticket launches once.
      this.#preview = { ...preview, ticket: null };
    }
    this.requestUpdate();
  }

  // --- following a started launch or resume ---------------------------------------------

  #follow(request: string, path: string, of: string | null): void {
    const known = (this.#launches?.value?.launches ?? []).find((row) => row.request_id === request);
    this.#follows.set(request, { request, path, of, started: Date.now(), done: known !== undefined,
                                 state: known ? { kind: "row", row: known } : { kind: "waiting" } });
    void this.#check(request);
    if (this.#followTimer === undefined) {
      this.#followTimer = setInterval(() => this.#tick(), FOLLOW_MS);
    }
  }

  #open(follow: Follow): boolean {
    return !follow.done && (follow.state.kind === "waiting" || follow.state.kind === "failed");
  }

  #tick(): void {
    let open = 0;
    for (const follow of this.#follows.values()) {
      if (!this.#open(follow)) continue;
      if (Date.now() - follow.started > FOLLOW_FOR_MS) {
        follow.state = { kind: "failed", text: "no row and no refusal came for it in time; look for it in the launches below" };
        follow.done = true;
        this.requestUpdate();
        continue;
      }
      open += 1;
      void this.#check(follow.request);
    }
    if (open === 0) {
      clearInterval(this.#followTimer);
      this.#followTimer = undefined;
    }
  }

  async #check(request: string): Promise<void> {
    const follow = this.#follows.get(request);
    if (follow === undefined || !this.#open(follow)) return;
    const state = followedOf(await api("launcher", follow.path));
    if (!this.#open(follow)) return;
    follow.state = state;
    follow.done = state.kind === "row" || state.kind === "refused";
    this.requestUpdate();
  }

  // --- resuming ---------------------------------------------------------------------------

  async #resume(item: PastItem): Promise<void> {
    if (item.session === null || this.#resumes.get(item.request)?.busy) return;
    this.#resumes.set(item.request, { busy: true, outcome: null });
    this.requestUpdate();
    const outcome = outcomeOf(await api("launcher", "api/resume", { session: item.session }));
    this.#resumes.set(item.request, { busy: false, outcome });
    if (outcome.kind === "started") this.#follow(outcome.status.request, outcome.status.path, item.request);
    this.requestUpdate();
  }

  async #more(): Promise<void> {
    const shown = [...(this.#pasts?.value?.sessions ?? []), ...this.#older];
    const path = olderPath(shown);
    if (path === null || this.#olderBusy) return;
    this.#olderBusy = true;
    this.requestUpdate();
    const result = await api("launcher", path);
    this.#olderBusy = false;
    const page = result.status === 200 ? parsePasts(result.data) : null;
    if (page?.state === "up") this.#older = [...this.#older, ...page.sessions];
    this.requestUpdate();
  }

  async #copy(text: string, key: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      this.#copied = key;
    } catch {
      this.#copied = `!${key}`;
    }
    this.requestUpdate();
  }

  // --- drawing ----------------------------------------------------------------------------

  render() {
    return html`<h1>Launcher</h1>
      ${this.#launchPart()}
      <div class="sessions" data-part="sessions"></div>
      ${this.#sessionsMissing ? html`<p class="warn" data-line="sessions-missing">The running sessions' state could not be shown.</p>` : nothing}
      ${this.#launchesPart()}
      ${this.#pastPart()}`;
  }

  #launchPart() {
    const feed = this.#catalog;
    const catalog = feed?.value ?? null;
    let status: unknown = nothing;
    if (feed === null || (feed.loading && catalog === null)) status = html`<p class="dim">Reading the catalog…</p>`;
    else if (feed.gone) status = html`<p class="problem">The launcher is no longer installed.</p>`;
    else if (feed.failure !== null) status = html`<p class="problem" data-line="failed">The catalog could not be read: ${feed.failure.why}.</p>`;
    else if (catalog?.state === "error") status = html`<p class="problem" data-line="error">The catalog could not be read: ${catalog.detail}</p>`;
    const fields = this.#fields;
    const filled = this.#filled;
    let form: unknown = nothing;
    if (this.#formFailure !== null) {
      form = html`<p class="problem" data-line="form-failed">The form could not be filled: ${this.#formFailure}.</p>`;
    } else if (filled === null && catalog?.state === "up") {
      form = html`<p class="dim">Filling the form…</p>`;
    }
    return html`<section class="launch" data-part="launch">
      <h2>Launch</h2>
      <p class="help lede">Start from a seat or a project preset: its values and your defaults fill the form. Change what you want, empty a field to leave it out of this launch, and preview the launch before you start it. Nothing here changes the preset.</p>
      ${status}
      ${this.#choosePart()}
      ${form}
      ${filled?.refused ? html`<p class="problem" data-line="form-refused" data-code=${filled.refused.code}>${refusalLine(filled.refused)}. The form shows the catalog's defaults.</p>` : nothing}
      ${filled !== null ? this.#keepPart(filled) : nothing}
      ${filled !== null && fields.length ? keyed(this.#element, html`<moxi-ui-form mode="submit" submit-label="Preview" .fields=${fields}
          @moxi-submit=${(event: CustomEvent<FormSubmit>) => void this.#submitted(event)}
          @input=${this.#poke} @click=${this.#poke} @keydown=${this.#poke}></moxi-ui-form>`) : nothing}
      ${this.#previewPart()}
    </section>`;
  }

  #choosePart() {
    const presets = this.#presets?.value ?? null;
    const seats = presetsOf(presets, "seat");
    const projects = presetsOf(presets, "project");
    const key = choiceKey(this.#choice);
    const chosen = chosenPreset(presets, this.#choice);
    const problems = presetProblems(presets, this.#choice.kind === "seat" ? this.#choice.name : "",
                                    this.#choice.kind === "project" ? this.#choice.name : "");
    const failed = this.#presets?.failure ?? null;
    const option = (kind: "seat" | "project", name: string) => {
      const value = choiceKey({ kind, name });
      return html`<option value=${value} ?selected=${value === key}>${name}</option>`;
    };
    return html`<div class="choose">
        <label>Start from
          <select data-control="preset" @change=${(event: Event) => this.#choose(event)}>
            <option value="" ?selected=${key === ""}>no preset</option>
            ${seats.length ? html`<optgroup label="Seats">${seats.map((item) => option("seat", item.name))}</optgroup>` : nothing}
            ${projects.length ? html`<optgroup label="Project presets">${projects.map((item) => option("project", item.name))}</optgroup>` : nothing}
          </select>
        </label>
        <label class="check"><input type="checkbox" data-control="off_posture" .checked=${this.#offPosture}
          @change=${(event: Event) => this.#offChanged(event)}>Allow a permission posture off your default</label>
      </div>
      ${failed !== null ? html`<p class="warn">The presets could not be read: ${failed.why}.</p>` : nothing}
      ${presets?.state === "error" ? html`<p class="warn">The presets could not be read: ${presets.detail}</p>` : nothing}
      ${chosen !== null ? html`<details data-preset=${`${chosen.kind}.${chosen.name}`}>
        <summary>What ${chosen.kind === "seat" ? "the seat" : "the project preset"} ${chosen.name} sets${chosen.harness ? ` (${chosen.harness})` : ""}</summary>
        <ul class="dim">${presetLines(chosen).map((line) => html`<li><code>${line}</code></li>`)}</ul>
      </details>` : nothing}
      ${problems.length ? html`<ul>${problems.map((item) => html`<li class=${item.level === "error" ? "problem" : "warn"}>
        ${item.where === "file" ? "presets file" : item.where}${item.key ? ` ${item.key}` : ""}: ${item.problem}. ${item.hint}</li>`)}</ul>` : nothing}`;
  }

  /** A keep box for each withheld text: ticked, an empty field keeps the preset's text;
   * unticked, the text is left out. */
  #keepPart(filled: Form) {
    const shown = new Set(this.#fields.map((field) => field.name));
    const rows = filled.rows.filter((row) => row.withheld && shown.has(row.name));
    if (rows.length === 0) return nothing;
    return html`<div class="choose keeps">${rows.map((row) => html`<label class="check">
      <input type="checkbox" data-control="keep" data-row=${row.name} .checked=${this.#keep.has(row.name)}
        @change=${(event: Event) => this.#keepChanged(row.name, event)}>${keepLabel(row, this.#choice)}</label>`)}</div>`;
  }

  #previewPart() {
    const preview = this.#preview;
    const parts: unknown[] = [];
    if (this.#previewing) parts.push(html`<p class="dim">Previewing…</p>`);
    if (this.#previewFailure !== null) parts.push(html`<p class="problem" data-line="preview-failed">The preview failed: ${this.#previewFailure}.</p>`);
    if (preview !== null && !this.#previewing) {
      const stale = this.#previewed !== null && JSON.stringify(this.#previewed) !== this.#current;
      parts.push(html`<div class="preview" data-part="preview" data-target=${preview.target ?? nothing}>
        <h3>Preview</h3>
        ${preview.refused.length ? html`<p class="problem">This launch is refused:</p>
          <ul class="refused">${preview.refused.map((found) => html`<li data-code=${found.code}>${refusalLine(found)}</li>`)}</ul>` : nothing}
        ${preview.harness ? html`<p>${preview.harness}${preview.mode ? ` (${preview.mode})` : ""}${preview.target ? `, in ${preview.target === "slot" ? "a slot" : preview.target === "headless" ? "the background (headless)" : "a terminal"}` : ""}</p>` : nothing}
        ${preview.argv.length ? html`<p class="dim">It runs:</p><code class="block" data-field="argv">${commandLine(preview.argv)}</code>` : nothing}
        ${preview.stamps.length ? html`<p class="dim">With these in its environment:</p>
          <ul class="stamps">${preview.stamps.map((stamp) => html`<li><code>${stamp.name}=${stamp.value}</code></li>`)}</ul>` : nothing}
        ${preview.first_turn ? html`<details><summary>Its first turn</summary><code class="block">${preview.first_turn}</code></details>` : nothing}
        ${preview.not_applied.length ? html`<p class="dim">Not applied: ${preview.not_applied.join(", ")}</p>` : nothing}
        ${preview.left_out.length ? html`<p class="dim" data-line="left-out">Left out: ${preview.left_out.join(", ")}</p>` : nothing}
        ${preview.warnings.length ? html`<ul class="warnings">${preview.warnings.map((line) => html`<li class="warn">${line}</li>`)}</ul>` : nothing}
        ${preview.command ? html`<div class="copy" data-field="command">
          <p>A launch in a terminal runs in a terminal of your own, not from this page. Copy the command and run it there:</p>
          ${this.#copyable(preview.command, "preview")}
        </div>` : nothing}
        ${stale ? html`<p class="warn" data-line="stale">The form has changed since this preview. Preview again to launch it.</p>`
          : preview.ticket ? html`<button class="go" data-action="launch" ?disabled=${this.#launching} @click=${() => void this.#launch()}>Launch</button>
            ${preview.expires_at ? html`<p class="dim">This preview can be launched until ${when(preview.expires_at)}.</p>` : nothing}` : nothing}
      </div>`);
    }
    if (this.#outcome !== null) parts.push(this.#outcomePart(this.#outcome));
    return parts;
  }

  #outcomePart(outcome: Outcome) {
    switch (outcome.kind) {
      case "started": {
        const follow = this.#follows.get(outcome.status.request);
        return html`<div data-part="launched" data-request=${outcome.status.request}>
          <p class="ok">Launch ${outcome.status.request} was handed to its job.</p>
          ${follow ? this.#followPart(follow) : nothing}
        </div>`;
      }
      case "refused":
        return html`<div data-part="launched"><p class="problem" data-code=${outcome.refusal.code}>The launch was refused: ${refusalLine(outcome.refusal)}</p>
          ${outcome.command ? this.#copyable(outcome.command, "launch") : nothing}</div>`;
      case "used":
        return html`<p class="warn" data-part="launched">${outcome.text}</p>`;
      case "command":
        return html`<div data-part="launched">${this.#copyable(outcome.command, "launch")}</div>`;
      default:
        return html`<p class="problem" data-part="launched">The launch could not be sent: ${outcome.text}.</p>`;
    }
  }

  #followPart(follow: Follow) {
    const state = follow.state;
    switch (state.kind) {
      case "row":
        return html`<p data-follow="row">Its row is in the launches below: ${state.row.state}${state.row.reason ? ` (${state.row.reason})` : ""}.</p>`;
      case "refused":
        return html`<p class="problem" data-follow="refused" data-code=${state.refusal.code}>It was refused before it started: ${refusalLine(state.refusal)}</p>`;
      case "waiting":
        return html`<p class="dim" data-follow="waiting">Starting…</p>`;
      default:
        return html`<p class="warn" data-follow="failed">Its progress can't be read: ${state.text}.</p>`;
    }
  }

  #copyable(command: string, key: string) {
    const copied = this.#copied === key ? html`<span class="ok">Copied.</span>`
      : this.#copied === `!${key}` ? html`<span class="warn">Select the command and copy it.</span>` : nothing;
    return html`<div class="copy"><code class="block" data-copy=${key}>${command}</code>
      <div class="row"><button data-action="copy" @click=${() => void this.#copy(command, key)}>Copy</button>${copied}</div></div>`;
  }

  #launchesPart() {
    const launches = this.#launches;
    if (launches === null) return nothing;
    const mine = new Set([...this.#follows.values()].filter((follow) => follow.of === null).map((follow) => follow.request));
    const view = launchesView(launches, this.#states, mine);
    if (view.state === "gone") return nothing;
    let status: unknown = nothing;
    if (view.state === "loading") status = html`<p class="dim">Reading the launch log…</p>`;
    else if (view.state === "empty") status = html`<p class="dim" data-line="empty">Nothing has been launched yet.</p>`;
    else if (view.state === "error") status = html`<p class="problem" data-line="error">The launch log could not be read: ${view.detail}</p>`;
    else if (view.state === "failed") status = html`<p class="problem" data-line="failed" title=${view.detail ?? nothing}>Can't reach the launcher's page server.</p>`;
    return html`<section class="launches" data-part="launches" data-state=${view.state}>
      <h2>Launches</h2>
      <p class="help">The newest launches, as they happen. A running session shows its state beside it.</p>
      ${status}
      ${view.rows.length ? html`<ul class="list">${repeat(view.rows, (row) => row.request, (row) => this.#launchRow(row))}</ul>` : nothing}
    </section>`;
  }

  #launchRow(row: LaunchItem) {
    return html`<li class=${row.mine ? "mine" : ""} data-request=${row.request} data-live=${String(row.live)}>
      <div class="first">
        <span class="label">${row.label}</span>
        <span class="tag" data-field="state">${row.state}</span>
        ${row.live && row.session ? html`<span class="cell" data-session=${row.session}></span>` : nothing}
      </div>
      <code data-field="cwd">${row.cwd}</code>
      <p class="dim">${row.when}${row.preset ? ` · ${row.preset}` : ""} · launch ${row.request}</p>
      ${row.reason ? html`<p class="dim">${row.reason}</p>` : nothing}
    </li>`;
  }

  #pastPart() {
    const pasts = this.#pasts;
    if (pasts === null) return nothing;
    const view = pastView(pasts, this.#older);
    if (view.state === "gone") return nothing;
    let status: unknown = nothing;
    if (view.state === "loading") status = html`<p class="dim">Reading the past sessions…</p>`;
    else if (view.state === "empty") status = html`<p class="dim" data-line="empty">No past session yet.</p>`;
    else if (view.state === "error") status = html`<p class="problem" data-line="error">The past sessions could not be read: ${view.detail}</p>`;
    else if (view.state === "failed") status = html`<p class="problem" data-line="failed" title=${view.detail ?? nothing}>Can't reach the launcher's page server.</p>`;
    return html`<section class="past" data-part="past" data-state=${view.state}>
      <h2>Past sessions</h2>
      <p class="help">Each launch's session, newest first. A session in a slot resumes from here; one in a terminal gives the command to resume it in a terminal of your own.</p>
      ${status}
      ${view.rows.length ? html`<ul class="list">${repeat(view.rows, (row) => row.request, (row) => this.#pastRow(row))}</ul>` : nothing}
      ${view.rows.length ? html`<button class="more" data-action="older" ?disabled=${this.#olderBusy} @click=${() => void this.#more()}>Older</button>` : nothing}
    </section>`;
  }

  #pastRow(row: PastItem) {
    const resumed = this.#resumes.get(row.request);
    const outcome = resumed?.outcome ?? null;
    let said: unknown = nothing;
    if (outcome !== null) {
      switch (outcome.kind) {
        case "command":
          said = html`<p>Run this in a terminal of your own:</p>${this.#copyable(outcome.command, `resume:${row.request}`)}`;
          break;
        case "started": {
          const follow = this.#follows.get(outcome.status.request);
          said = html`<p class="ok">Resuming as launch ${outcome.status.request}.</p>${follow ? this.#followPart(follow) : nothing}`;
          break;
        }
        case "refused":
          said = html`<p class="problem" data-code=${outcome.refusal.code}>${refusalLine(outcome.refusal)}</p>`;
          break;
        default:
          said = html`<p class="problem">${outcome.text}</p>`;
      }
    }
    const label = row.resume === "slot" ? "Resume" : "Command to resume";
    return html`<li data-request=${row.request} data-session=${row.session ?? nothing} data-resume=${row.resume}>
      <div class="first">
        <span class="label">${row.label}</span>
        <span class="tag" data-field="live">${row.live === "unknown" ? "live ?" : row.live}</span>
        ${row.resume !== "none" ? html`<button data-action="resume" ?disabled=${resumed?.busy ?? false}
            @click=${() => void this.#resume(row)}>${label}</button>` : nothing}
      </div>
      <code data-field="cwd">${row.cwd}</code>
      <p class="dim">${row.when}${row.session ? ` · session ${row.session}` : ""}</p>
      ${row.why ? html`<p class="dim">${row.why}</p>` : nothing}
      <div data-field="resumed">${said}</div>
    </li>`;
  }

  /** After each draw: the request worked out again once the form has new fields, the
   * `sessions` block placed once, and a `state` component placed in each new live row's
   * cell. A cell holds
   * the id its launch row was first known by, which a clear or a compact doesn't change;
   * the component follows the launch through the session's later ids, so a cell is placed
   * once. */
  protected updated(changed: Map<PropertyKey, unknown>): void {
    super.updated(changed);
    const form = this.#form();
    // Lit sets `fields` again on every draw, so the page's own array says when the fields
    // really changed: newly filled rows may need a group's other rows emptied again.
    if (form !== null && this.#fields !== this.#drawn) {
      this.#drawn = this.#fields;
      this.#settle(form.values());
    }
    const host = this.renderRoot.querySelector(".sessions");
    if (host !== null && !this.#sessionsPlaced) {
      this.#sessionsPlaced = true;
      const missing = (): void => {
        this.#sessionsMissing = true;
        this.requestUpdate();
      };
      place(host, "launcher", "sessions").then((block) => { if (block === null) missing(); }, missing);
    }
    for (const cell of this.renderRoot.querySelectorAll<HTMLElement>(".cell")) {
      const session = cell.dataset.session ?? "";
      if (!session || cell.dataset.placed === session) continue;
      cell.dataset.placed = session;
      place(cell, "launcher", "state", { session }).catch(() => null);
    }
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, LauncherPage);

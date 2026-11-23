// <moxi-ui-page>: the shell's own panel. With `route` "" it is Home, drawn by
// <moxi-ui-home>. With `route` "modules" it is the modules view: every module's block
// that asks for home and has no seat on Home (each card says `Reading…` until its block
// first takes up height), then every installed module with its state,
// its page and its components, and a Check that shows the doctor's rows for it, then the
// shell's page servers block. With
// `route` "settings" or under it, it is the Settings page, drawn by <moxi-ui-settings>,
// where ui's Phone group pairs a phone.
//
// On the phone it leaves out what only the desk reaches: Check and the Settings page,
// whose routes the gateway doesn't serve. The views carry no title of
// their own: the shell's top line names the page.
//
// It reads only the shell's own routes and topics, through client.js. The shell places a
// new element for each navigation, so `route` is read once. The modules view's
// `data-reading` mark on the page root is the sum of its own reads and its blocks':
// each block's placing and every read a block sends go through client.js, which counts
// them all.

import { css, html, LitElement, nothing } from "lit";
import type { PropertyValues } from "lit";
import { api, components, gateway, installed, navigate, place, subscribe } from "/m/ui/client.js";

import "./home.ts";
import "./settings.ts";
import { placeOnceMore, scriptFailure } from "./place-model.ts";
import { doctorRows, homeBlocks, moduleLines, moduleOrder, moduleState } from "./views.ts";
import type { DoctorRow, ModuleView } from "./views.ts";

const TAG = "moxi-ui-page";
/** The block the modules view places last, under the Installed list: its list of page
 * servers is tall and comes over the push stream, so it may be drawn after the rest, and
 * placed last it moves nothing already drawn when it grows. */
const LAST_BLOCK = "ui.servers";
interface Check {
  busy: boolean;
  rows: DoctorRow[];
  error: string | null;
}

/** What the view shows for an answer that is not a 200. */
function failed(status: number, data: any, what: string): string {
  if (status === 0) return `No answer from the shell, so ${what} could not be read.`;
  if (status === 403) return "This browser is signed out.";
  const text = data?.refused ?? data?.conflict ?? data?.error;
  return typeof text === "string" && text ? text : `The shell answered ${status} for ${what}.`;
}

const STYLES = css`
  :host { display: block; min-width: 0; }
  * { box-sizing: border-box; }
  h1 { margin: 0 0 calc(var(--moxi-gap) * 2); font-size: 1.4em; }
  h2 { margin: 0 0 var(--moxi-gap); font-size: 1.1em; }
  h3 { margin: 0; font-size: 1em; }
  p { margin: 0; overflow-wrap: anywhere; }
  a { color: var(--moxi-accent); }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  section, .card { min-width: 0; }
  .bar { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--moxi-gap) calc(var(--moxi-gap) * 2);
         margin-bottom: calc(var(--moxi-gap) * 2); }
  .grid { display: grid; gap: calc(var(--moxi-gap) * 2); grid-template-columns: repeat(auto-fill, minmax(min(100%, 22rem), 1fr));
          margin-bottom: calc(var(--moxi-gap) * 3); }
  .list { display: grid; gap: var(--moxi-gap); margin-bottom: calc(var(--moxi-gap) * 3); }
  .card { display: grid; gap: 6px; padding: 10px 12px; border: 1px solid var(--moxi-border);
          border-radius: var(--moxi-radius-lg); background: var(--moxi-bg-raised); }
  .head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--moxi-gap); }
  .head .grow { flex: 1; }
  .dim { color: var(--moxi-text-dim); }
  .small { font-size: var(--moxi-font-size-sm); }
  .tag { padding: 0 6px; border-radius: var(--moxi-radius); font-size: var(--moxi-font-size-sm); }
  .ok { color: var(--moxi-ok); }
  .off { color: var(--moxi-text-dim); }
  .warn { color: var(--moxi-warn); }
  .fail { color: var(--moxi-error); }
  .tag.ok { background: var(--moxi-add-soft); }
  .tag.warn { background: var(--moxi-warn-soft); }
  .tag.fail { background: var(--moxi-error-soft); }
  .tag.off { background: var(--moxi-bg-sunken); }
  .actions { display: flex; flex-wrap: wrap; gap: var(--moxi-gap); align-items: center; }
  button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg); color: var(--moxi-text); font: inherit; cursor: pointer; }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button:disabled { cursor: progress; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  ul.rows { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }
  ul.rows li { display: grid; gap: 2px; padding: 4px 8px; border-left: 3px solid var(--moxi-border); }
  ul.rows li.ok { border-left-color: var(--moxi-ok); }
  ul.rows li.warn { border-left-color: var(--moxi-warn); }
  ul.rows li.fail { border-left-color: var(--moxi-error); }
  ul.rows li > * { color: var(--moxi-text); }
  .problem { padding: 8px 12px; border-radius: var(--moxi-radius); background: var(--moxi-error-soft); color: var(--moxi-error); }
`;

/** The `Reading…` line a block's card shows until the block placed in `host` first takes
 * up height, so a card is never drawn empty while its block's script and first read are
 * out. */
function readingLine(host: HTMLElement): HTMLElement {
  const line = document.createElement("p");
  line.className = "small dim";
  line.dataset.line = "reading";
  line.textContent = "Reading…";
  const seen = new ResizeObserver(() => {
    if (host.offsetHeight === 0) return;
    seen.disconnect();
    line.remove();
  });
  seen.observe(host);
  return line;
}

export class UiPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
    modules: { state: true },
    checks: { state: true },
  };

  route = "";
  modules: { error: string | null; modules: ModuleView[] } | null = null;
  checks = new Map<string, Check>();
  #view: "home" | "modules" | "settings" | "none" = "none";
  #route = "";
  #stops: Array<() => void> = [];
  #placed = "";
  #placing = 0;

  connectedCallback(): void {
    super.connectedCallback();
    const route = (this.getAttribute("route") ?? this.route ?? "").replace(/^\/+|\/+$/g, "");
    if (route === "") {
      this.#view = "home";
    } else if (route === "modules") {
      this.#view = "modules";
      this.#stops.push(subscribe("ui", "modules", (data) => { this.modules = data; }));
      // Asked for with the modules, so the stream's first open brings the page servers too
      // and the servers block, placed once the modules come, mostly draws its list with its
      // first paint.
      this.#stops.push(subscribe("ui", "servers", () => {}));
    } else if (route === "settings" || route.startsWith("settings/")) {
      this.#view = "settings";
      this.#route = route;
    } else {
      this.#view = "none";
    }
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    for (const stop of this.#stops) stop();
    this.#stops = [];
  }

  render() {
    if (this.#view === "home") return html`<moxi-ui-home></moxi-ui-home>`;
    if (this.#view === "modules") return this.#modulesView();
    if (this.#view === "settings") return this.#settingsView();
    return html`<h1>Nothing is here</h1>
      <p>${this.#link("ui", "modules", "Open the modules view")}.</p>`;
  }

  protected updated(changed: PropertyValues): void {
    super.updated(changed);
    if (this.#view === "modules" && changed.has("modules")) void this.#placeHome();
  }

  #link(module: string, route: string, text: string) {
    return html`<a href=${`/m/${module}/${route}`} @click=${(event: MouseEvent) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate(module, route);
    }}>${text}</a>`;
  }

  // --- the modules view -----------------------------------------------------------------

  #modulesView() {
    const value = this.modules;
    const mods = value === null ? [] : moduleOrder(value.modules);
    return html`
      ${gateway() ? nothing : html`<div class="bar">${this.#link("ui", "settings", "Settings")}</div>`}
      ${value?.error ? html`<p class="problem">${value.error}</p>` : nothing}
      <div class="grid" id="home"></div>
      <h2>Installed</h2>
      ${value === null ? html`<p class="dim">Reading the modules…</p>` : nothing}
      ${value !== null && mods.length === 0 ? html`<p class="dim">No module is listed.</p>` : nothing}
      <div class="list">${mods.map((mod) => this.#moduleCard(mod))}</div>
      <div class="grid" id="last"></div>`;
  }

  #moduleCard(mod: ModuleView) {
    const state = moduleState(mod);
    const check = this.checks.get(mod.name);
    const panel = mod.name !== "ui" && installed(mod.name)
      && components(mod.name).some((comp) => comp.name === "page");
    return html`<div class="card" data-module=${mod.name}>
      <div class="head">
        <h3>${mod.name}</h3>
        ${mod.version ? html`<span class="dim small">${mod.version}</span>` : nothing}
        <span class="grow"></span>
        <span class="tag ${state.tone}">${state.text}</span>
      </div>
      ${mod.summary ? html`<p class="dim">${mod.summary}</p>` : nothing}
      ${moduleLines(mod).map((line) => html`<p class="small ${line.tone ?? ""}">${line.text}</p>`)}
      <div class="actions">
        ${panel ? this.#link(mod.name, "", "Open its panel") : nothing}
        ${gateway() ? nothing : html`<button type="button" ?disabled=${check?.busy === true}
          @click=${() => this.#check(mod.name)}>${check?.busy ? "Checking…" : "Check"}</button>`}
      </div>
      ${check !== undefined && !check.busy ? this.#checkRows(check) : nothing}
    </div>`;
  }

  #checkRows(check: Check) {
    if (check.error) return html`<p class="small fail">${check.error}</p>`;
    if (check.rows.length === 0) return html`<p class="small dim">The doctor has no rows for it.</p>`;
    return html`<ul class="rows">${doctorRows(check.rows).map((row) => html`
      <li class=${row.tone}>
        <div class="head"><span class="tag ${row.tone}">${row.tone}</span><code>${row.name}</code></div>
        <p class="small">${row.detail}</p>
        ${row.fix ? html`<p class="small dim">Fix: <code>${row.fix}</code></p>` : nothing}
      </li>`)}</ul>`;
  }

  async #check(module: string): Promise<void> {
    this.checks = new Map(this.checks).set(module, { busy: true, rows: [], error: null });
    const answer = await api("ui", `api/doctor?module=${encodeURIComponent(module)}`);
    let result: Check;
    if (answer.status === 200 && Array.isArray(answer.data?.rows)) {
      result = { busy: false, rows: answer.data.rows, error: answer.data.error ?? null };
    } else {
      result = { busy: false, rows: [], error: failed(answer.status, answer.data, "the doctor") };
    }
    this.checks = new Map(this.checks).set(module, result);
  }

  /** Places every block again when the set of them changes: each in the grid at the top,
   * and `LAST_BLOCK` under the Installed list. */
  async #placeHome(): Promise<void> {
    const home = this.renderRoot.querySelector("#home");
    const last = this.renderRoot.querySelector("#last");
    if (home === null || last === null || this.modules === null) return;
    const blocks = homeBlocks(this.modules.modules).filter((block) => installed(block.module));
    const sig = JSON.stringify(blocks.map((block) => [block.module, block.name,
      components(block.module).find((comp) => comp.name === block.name)?.url ?? ""]));
    if (sig === this.#placed) return;
    this.#placed = sig;
    const mine = (this.#placing += 1);
    home.replaceChildren();
    last.replaceChildren();
    for (const block of blocks) {
      const card = document.createElement("section");
      card.className = "card";
      card.dataset.block = `${block.module}.${block.name}`;
      const title = document.createElement("h2");
      title.textContent = block.title;
      const host = document.createElement("div");
      card.append(title, readingLine(host), host);
      (card.dataset.block === LAST_BLOCK ? last : home).append(card);
      // A script that gave no element and answers fine gets one more try; one with no
      // script declared has nothing to fetch again.
      const url = components(block.module).find((comp) => comp.name === block.name)?.url;
      const load = await placeOnceMore(() => place(host, block.module, block.name),
                                       async () => (url === undefined ? "no script" : await scriptFailure(block.module, url)),
                                       () => mine === this.#placing);
      if (load === null) return;
      if (!("placed" in load)) {
        const line = document.createElement("p");
        line.className = "small warn";
        line.textContent = `${block.module}'s ${block.name} could not be shown.`;
        host.append(line);
      }
    }
  }

  // --- the settings page ----------------------------------------------------------------

  #settingsView() {
    if (gateway()) {
      return html`<p class="dim" data-line="desk">Settings are changed at the desk, on the computer's Moxi page.</p>
        <div class="bar">${this.#link("ui", "", "Home")}</div>`;
    }
    return html`<moxi-ui-settings route=${this.#route}></moxi-ui-settings>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, UiPage);

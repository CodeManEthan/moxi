// <moxi-ui-page>: the shell's own panel. With `route` "" it is the modules view: every
// module's home block, then every installed module with its state, its page and its
// components, and a Check that shows the doctor's rows for it, then the Gateway section
// that pairs a phone. With `route` "settings"
// (or "settings/<module>" for one module) it is the settings view: a section per module
// drawn by <moxi-ui-form>, each change written through `moxi config` by the shell.
//
// On the phone it leaves out what only the desk reaches: Check, the Gateway section and
// the settings, whose routes the gateway doesn't serve.
//
// It reads only the shell's own routes and topics, through client.js. The shell places a
// new element for each navigation, so `route` is read once.

import { css, html, LitElement, nothing } from "lit";
import type { PropertyValues } from "lit";
import { api, components, gateway, installed, navigate, place, setTheme, subscribe } from "/m/ui/client.js";
import type { FieldDescriptor, FormChange, FormElement, ThemeMode } from "/m/ui/client.js";

import "./gateway-desk.ts";
import { placeOnceMore, scriptFailure } from "./place-model.ts";
import { doctorRows, homeBlocks, moduleLines, moduleOrder, moduleState } from "./views.ts";
import type { DoctorRow, ModuleView } from "./views.ts";

const TAG = "moxi-ui-page";
const NAME = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const CORE = "moxi";
const MODES: readonly string[] = ["system", "light", "dark"];

interface Section {
  module: string;
  keys: FieldDescriptor[];
  error: string | null;
  fix: string | null;
  readonly: boolean;
}

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
  .bar h1 { margin: 0; }
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
  .settings { display: grid; gap: calc(var(--moxi-gap) * 3); }
`;

export class UiPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
    modules: { state: true },
    checks: { state: true },
    sections: { state: true },
    settingsError: { state: true },
  };

  route = "";
  modules: { error: string | null; modules: ModuleView[] } | null = null;
  checks = new Map<string, Check>();
  sections: Section[] | null = null;
  settingsError: string | null = null;
  #view: "modules" | "settings" | "none" = "none";
  #only: string | null = null;
  #stop: (() => void) | null = null;
  #placed = "";
  #placing = 0;

  connectedCallback(): void {
    super.connectedCallback();
    const route = (this.getAttribute("route") ?? this.route ?? "").replace(/^\/+|\/+$/g, "");
    if (route === "") {
      this.#view = "modules";
      this.#stop = subscribe("ui", "modules", (data) => { this.modules = data; });
    } else if (route === "settings" || route.startsWith("settings/")) {
      this.#view = "settings";
      const only = route.slice("settings/".length);
      this.#only = NAME.test(only) ? only : null;
      if (!gateway()) void this.#readSettings();
    } else {
      this.#view = "none";
    }
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#stop?.();
    this.#stop = null;
  }

  render() {
    if (this.#view === "modules") return this.#modulesView();
    if (this.#view === "settings") return this.#settingsView();
    return html`<h1>Nothing is here</h1>
      <p>${this.#link("ui", "", "Open the modules view")}.</p>`;
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
      <div class="bar"><h1>Modules</h1>${gateway() ? nothing : this.#link("ui", "settings", "Settings")}</div>
      ${value?.error ? html`<p class="problem">${value.error}</p>` : nothing}
      <div class="grid" id="home"></div>
      <h2>Installed</h2>
      ${value === null ? html`<p class="dim">Reading the modules…</p>` : nothing}
      ${value !== null && mods.length === 0 ? html`<p class="dim">No module is listed.</p>` : nothing}
      <div class="list">${mods.map((mod) => this.#moduleCard(mod))}</div>
      ${gateway() ? nothing : html`<moxi-ui-gateway></moxi-ui-gateway>`}`;
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

  /** Places every home block again when the set of them changes. */
  async #placeHome(): Promise<void> {
    const home = this.renderRoot.querySelector("#home");
    if (home === null || this.modules === null) return;
    const blocks = homeBlocks(this.modules.modules).filter((block) => installed(block.module));
    const sig = JSON.stringify(blocks.map((block) => [block.module, block.name,
      components(block.module).find((comp) => comp.name === block.name)?.url ?? ""]));
    if (sig === this.#placed) return;
    this.#placed = sig;
    const mine = (this.#placing += 1);
    home.replaceChildren();
    for (const block of blocks) {
      const card = document.createElement("section");
      card.className = "card";
      card.dataset.block = `${block.module}.${block.name}`;
      const title = document.createElement("h2");
      title.textContent = block.title;
      const host = document.createElement("div");
      card.append(title, host);
      home.append(card);
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

  // --- the settings view ----------------------------------------------------------------

  #settingsView() {
    const title = this.#only ? `Settings: ${this.#only}` : "Settings";
    if (gateway()) {
      return html`<div class="bar"><h1>${title}</h1>${this.#link("ui", "", "Modules")}</div>
        <p class="dim">Settings are changed at the desk, on the computer's Moxi page.</p>`;
    }
    return html`
      <div class="bar"><h1>${title}</h1>
        ${this.#only ? this.#link("ui", "settings", "Every module's settings") : nothing}
        ${this.#link("ui", "", "Modules")}</div>
      ${this.settingsError ? html`<p class="problem">${this.settingsError}</p>` : nothing}
      ${this.sections === null && !this.settingsError ? html`<p class="dim">Reading the settings…</p>` : nothing}
      <div class="settings">${(this.sections ?? []).map((section) => this.#section(section))}</div>`;
  }

  #section(section: Section) {
    const name = section.module === CORE ? "moxi (the core)" : section.module;
    return html`<section class="card" data-module=${section.module}>
      <h2>${name}</h2>
      ${section.error ? html`<p class="problem">${section.error}</p>` : nothing}
      ${section.error && section.fix ? html`<p class="small dim">Fix: <code>${section.fix}</code></p>` : nothing}
      ${section.readonly && !section.error ? html`<p class="small warn">These settings can't be changed here now.</p>` : nothing}
      ${section.keys.length === 0 && !section.error ? html`<p class="dim">No settings.</p>` : nothing}
      ${section.keys.length ? html`<moxi-ui-form mode="each" .fields=${section.keys} ?disabled=${section.readonly}
          @moxi-change=${(event: CustomEvent<FormChange>) => this.#write(section.module, event)}></moxi-ui-form>` : nothing}
    </section>`;
  }

  async #readSettings(module?: string): Promise<void> {
    const target = module ?? this.#only;
    const path = target ? `api/settings?module=${encodeURIComponent(target)}` : "api/settings";
    const answer = await api("ui", path);
    if (answer.status !== 200 || !Array.isArray(answer.data?.sections)) {
      this.settingsError = failed(answer.status, answer.data, "the settings");
      return;
    }
    const got: Section[] = answer.data.sections;
    if (module === undefined) {
      this.sections = got;
      this.settingsError = answer.data.error ?? null;
      return;
    }
    const fresh = got.find((section) => section.module === module);
    if (fresh === undefined) return;
    this.sections = (this.sections ?? []).map((section) => (section.module === module ? fresh : section));
  }

  /** One change from a section's form: written by `moxi config set` or `unset` through the
   * shell, its answer shown under the key, and the section read again for the value and
   * source the file now gives. */
  async #write(module: string, event: CustomEvent<FormChange>): Promise<void> {
    const form = event.currentTarget as FormElement;
    const change = event.detail;
    const body: Record<string, string> = { module, key: change.name };
    if (!change.reset) {
      for (const field of ["value", "add", "remove"] as const) {
        if (typeof change[field] === "string") body[field] = change[field] as string;
      }
    }
    const answer = await api("ui", change.reset ? "api/settings/unset" : "api/settings/set", body);
    const data = answer.data;
    if (answer.status === 200 && data?.ok) {
      const lines: string[] = Array.isArray(data.lines) ? data.lines : [];
      if (data.warning) {
        form.setMessage(change.name, [data.warning, ...lines, ...(data.fix ? [`Fix: ${data.fix}`] : [])], "warn");
      } else {
        form.setMessage(change.name, lines.length ? lines : [change.reset ? "Reset to the default." : "Saved."], "info");
      }
    } else if (answer.status === 0) {
      form.setError(change.name, "No answer from the shell; the change may not be written.");
    } else {
      form.setError(change.name, failed(answer.status, data, "the change"));
    }
    await this.#readSettings(module);
    if (module === "ui" && (change.name === "theme.mode" || change.name === "theme.palette")) {
      const keys = this.sections?.find((section) => section.module === "ui")?.keys;
      const now = (name: string): unknown => {
        const key = keys?.find((item) => item.name === name);
        return typeof key?.value === "string" ? key.value : key?.default;
      };
      const mode = now("theme.mode");
      const theme = now("theme.palette");
      if (typeof mode === "string" && MODES.includes(mode)) {
        setTheme(mode as ThemeMode, typeof theme === "string" ? theme : undefined);
      }
    }
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, UiPage);

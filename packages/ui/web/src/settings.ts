// <moxi-ui-settings route="settings…">: the Settings page at the desk, placed by
// <moxi-ui-page> on its settings routes.
//
// Two columns: the list of modules with settings (the core, ui, then A to Z, from the
// read's `listed`), each with its count of changed settings once its section is in; and
// the pane, one module's settings at a time: its rows by type, the keys marked `common`
// first and the rest behind `More`, under their sub-heads, and its foot. The rows,
// counts and words come from `settings-model.ts`; the addresses from `find-model.ts`.
//
// The page reads the shown module first (`GET /api/settings?module=`), draws the list
// from `listed`, then reads every other listed module two at a time, and reads them all
// again when its tab shows again. It writes its own address through `setAddress` as a
// module or `More` opens. When the shell draws the panel again in place (a modules or
// session event), the new element takes over what the old one held, so nothing the
// person opened is lost.

import { css, html, LitElement, nothing } from "lit";
import type { PropertyValues } from "lit";
import { live } from "lit/directives/live.js";
import { styleMap } from "lit/directives/style-map.js";
import { api, components, installed, setAddress } from "/m/ui/client.js";

import { addressOf, parseAddress } from "./find-model.ts";
import type { Address } from "./find-model.ts";
import {
  blocksOf, CORE, listEntries, LOCKED, moduleOrder, moreWords, NOTHING_HERE, paneKeys, paneState, PATH_HINT, READING,
  READING_AFTER_MS, readFailed, rowOf, splitMore, totalChanged, unreadableWords,
} from "./settings-model.ts";
import type { Control, Row, Section, Setting } from "./settings-model.ts";

const TAG = "moxi-ui-settings";
/** How many module reads run at once. */
const READS_AT_ONCE = 2;
/** How long `Copied` shows after a key or command is copied. */
const COPIED_MS = 2000;

/** What the page holds, and what a page placed again in its place takes over. */
interface Held {
  /** The read's listed modules, null until the first answer. */
  listed: string[] | null;
  sections: Map<string, Section>;
  /** The read's own error: the module snapshot is not ready or is off. */
  error: string | null;
  /** The address shown, null until the first answer. */
  address: Address | null;
}

/** What the page held when the shell took it out with the address unchanged: the shell is
 * drawing it again in place, and the element it places next takes this over. */
let carried: { path: string; held: Held } | null = null;

const STYLES = css`
  :host { display: block; min-width: 0; max-width: 680px; margin: 0 auto; font-size: var(--moxi-font-size); }
  * { box-sizing: border-box; }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); }
  button { font: inherit; color: inherit; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  .dim { color: var(--moxi-text-dim); }
  .link { padding: 0; border: 0; background: none; color: var(--moxi-text); cursor: pointer; }
  .link.dim { color: var(--moxi-text-dim); }
  .cols { display: grid; grid-template-columns: 96px minmax(0, 1fr); border-top: 1px solid var(--moxi-border);
          padding: 0 2px; }
  .mods { position: sticky; top: 8px; align-self: start; padding: 12px 12px 0 0; font-size: 14px; min-width: 0; }
  .mods a { display: flex; justify-content: space-between; align-items: baseline; gap: 6px; line-height: 28px;
            color: var(--moxi-text-dim); text-decoration: none; }
  .mods a[aria-current] { color: var(--moxi-text); }
  .mods a i { font-style: normal; font-size: 13px; color: var(--moxi-text-dim); font-variant-numeric: tabular-nums; }
  .mods .under { display: block; font-size: 12px; line-height: 16px; color: var(--moxi-text); margin: -5px 0 5px; }
  .mods .total { display: block; font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); margin-top: 10px;
                 padding-top: 10px; border-top: 1px solid var(--moxi-border); }
  .mods .error { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); margin-top: 10px; }
  .pane { min-width: 0; padding-top: 5px; }
  .set { padding: 7px 0; }
  .l1 { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; min-height: 28px;
        font-size: 14px; line-height: 20px; column-gap: 14px; row-gap: 4px; }
  .lb { min-width: 0; overflow-wrap: anywhere; }
  .key { font-size: 12px; line-height: 18px; color: var(--moxi-text-dim); margin-left: 10px; cursor: copy; }
  .key.own { font-size: 14px; line-height: 20px; color: var(--moxi-text); margin-left: 0; }
  .copied { font-size: 12px; margin-left: 8px; }
  .l2 { display: flex; flex-wrap: wrap; justify-content: space-between; column-gap: 18px; font-size: 13px;
        line-height: 18px; color: var(--moxi-text-dim); margin-top: -1px; }
  .l2 .said { max-width: 440px; flex: 0 1 auto; min-width: 0; overflow-wrap: anywhere; }
  .l2 .nt { margin-left: auto; }
  .l2 code { color: var(--moxi-text); font-size: 12px; overflow-wrap: anywhere; }
  .ctl { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; }
  .fld { border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius); padding: 3px 9px; font: inherit;
         font-size: 14px; line-height: 20px; background: none; color: var(--moxi-text); }
  .fld.num { width: 88px; text-align: right; font-variant-numeric: tabular-nums; }
  .fld.txt { width: min(190px, 100%); }
  .fld:disabled, button:disabled { opacity: 0.6; cursor: not-allowed; }
  select.fld { text-align: left; }
  .unit { color: var(--moxi-text-dim); font-size: 13px; }
  .tri { display: inline-flex; flex-wrap: wrap; font-size: 13px; line-height: 1; }
  .tri button { padding: 5px 7px; border: 1px solid transparent; border-radius: var(--moxi-radius); background: none;
                color: var(--moxi-text-dim); cursor: pointer; }
  .tri button[aria-checked="true"] { color: var(--moxi-text); border-color: var(--moxi-border-strong); }
  .sw { display: inline-block; position: relative; width: 26px; height: 14px; padding: 0; border-radius: 7px;
        border: 1px solid var(--moxi-text-dim); background: none; cursor: pointer; vertical-align: -2px; }
  .sw::after { content: ""; position: absolute; top: 2px; left: 2px; width: 8px; height: 8px; border-radius: 50%;
               background: var(--moxi-text-dim); }
  .sw[aria-checked="true"] { background: var(--moxi-text); border-color: var(--moxi-text); }
  .sw[aria-checked="true"]::after { left: 14px; background: var(--moxi-bg); }
  .toks { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 13px; }
  .tok { padding: 4px 7px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius); }
  .wide { display: block; border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius); padding: 5px 9px;
          font-size: 13px; line-height: 18px; margin: 3px 0 2px; overflow-wrap: anywhere; }
  .entries { display: grid; gap: 2px; padding: 2px 0 2px 16px; }
  .entry { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; column-gap: 14px;
           font-size: 14px; line-height: 20px; min-height: 26px; }
  .entry .l2 { flex-basis: 100%; }
  .table { display: grid; column-gap: 8px; row-gap: 6px; align-items: center; font-size: 13px; line-height: 18px;
           padding: 4px 0; overflow-x: auto; }
  .table .h { color: var(--moxi-text-dim); }
  .table .fld { font-size: 13px; padding: 3px 8px; min-width: 0; width: 100%; }
  .subh { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); border-top: 1px solid var(--moxi-border);
          margin-top: 6px; padding: 11px 0 2px; }
  .pane > .subh:first-child { border-top: 0; margin-top: 0; padding-top: 7px; }
  .more { display: flex; flex-wrap: wrap; justify-content: space-between; column-gap: 14px;
          border-top: 1px solid var(--moxi-border); margin-top: 6px; padding: 11px 0 10px; font-size: 14px;
          line-height: 20px; color: var(--moxi-text-dim); }
  .more b { font-weight: 400; color: var(--moxi-text); margin-right: 10px; }
  .more[data-open] + .subh { border-top: 0; margin-top: 0; padding-top: 0; }
  .note { font-size: 14px; line-height: 20px; color: var(--moxi-text-dim); padding: 12px 0; }
  .unreadable { display: grid; gap: 6px; padding: 12px 0; font-size: 14px; line-height: 20px; }
  .unreadable .fix { color: var(--moxi-text-dim); }
  .unreadable code { color: var(--moxi-text); overflow-wrap: anywhere; }
  .foot { display: flex; flex-wrap: wrap; justify-content: space-between; column-gap: 18px; font-size: 13px;
          line-height: 18px; color: var(--moxi-text-dim); border-top: 1px solid var(--moxi-border); margin-top: 6px;
          padding: 12px 0 28px; }
  .foot .file { overflow-wrap: anywhere; min-width: 0; }
  .foot a { color: var(--moxi-text); text-decoration: none; }
  .missing { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); padding-bottom: 28px; }
  .failed { display: grid; gap: var(--moxi-gap); justify-items: start; padding: 12px 0; }
  .failed button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
                   background: none; cursor: pointer; }
  @media (max-width: 720px) {
    .cols { grid-template-columns: minmax(0, 1fr); }
    .mods { position: static; display: flex; flex-wrap: wrap; column-gap: 14px; padding: 8px 0;
            border-bottom: 1px solid var(--moxi-border); }
    .mods a { line-height: 24px; }
    .mods .under { margin: 0; align-self: center; }
    .mods .total, .mods .error { flex-basis: 100%; margin-top: 4px; padding-top: 4px; }
  }
`;

/** The module an address names, read before the read says which modules are listed. */
function namedIn(route: string): string {
  const parts = route.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean);
  return parts[0] === "settings" && parts[1] ? parts[1] : CORE;
}

export class UiSettings extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "settings";
  #held: Held = { listed: null, sections: new Map(), error: null, address: null };
  /** The read failed as a whole: its words, and Try again. */
  #failed: string | null = null;
  /** The pane says it is reading once a read has taken this long. */
  #slow = false;
  #slowTimer: ReturnType<typeof setTimeout> | null = null;
  /** The key or command whose `Copied` shows, and its timer. */
  #copied: string | null = null;
  #copiedTimer: ReturnType<typeof setTimeout> | null = null;
  /** The path the page is at, for telling a redraw in place from a move away. */
  #path = "";
  #reads = 0;
  readonly #onVisible = (): void => {
    if (document.visibilityState === "visible" && this.#held.listed !== null) void this.#readAll();
  };

  connectedCallback(): void {
    super.connectedCallback();
    const route = (this.getAttribute("route") ?? this.route ?? "settings").replace(/^\/+|\/+$/g, "");
    this.route = route;
    this.#path = location.pathname;
    document.addEventListener("visibilitychange", this.#onVisible);
    const taken = carried !== null && carried.path === location.pathname ? carried.held : null;
    carried = null;
    if (taken !== null && taken.listed !== null) {
      this.#held = { ...taken, address: parseAddress(route, taken.listed) };
      void this.#readAll();
    } else {
      void this.#readFirst();
    }
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    document.removeEventListener("visibilitychange", this.#onVisible);
    for (const timer of [this.#slowTimer, this.#copiedTimer]) if (timer !== null) clearTimeout(timer);
    carried = location.pathname === this.#path ? { path: this.#path, held: this.#held } : null;
  }

  // --- reading ----------------------------------------------------------------------------

  /** The shown module first, then every other listed module. */
  async #readFirst(): Promise<void> {
    this.#failed = null;
    this.#startSlow();
    const shown = await this.#read(namedIn(this.route));
    if (!shown) return;
    const listed = this.#held.listed ?? [];
    this.#held.address = parseAddress(this.route, listed);
    this.requestUpdate();
    if (!this.#held.sections.has(this.#held.address.module) && !(await this.#read(this.#held.address.module))) return;
    await this.#readRest(moduleOrder(listed).filter((name) => !this.#held.sections.has(name)));
  }

  /** Every listed module again, the shown one first. */
  async #readAll(): Promise<void> {
    const shown = this.#held.address?.module ?? CORE;
    if (!(await this.#read(shown))) return;
    const listed = this.#held.listed ?? [];
    await this.#readRest(moduleOrder(listed).filter((name) => name !== shown));
  }

  async #readRest(names: string[]): Promise<void> {
    const queue = [...names];
    const worker = async (): Promise<void> => {
      for (let name = queue.shift(); name !== undefined; name = queue.shift()) {
        if (!(await this.#read(name))) return;
      }
    };
    await Promise.all(Array.from({ length: READS_AT_ONCE }, worker));
  }

  /** Reads one module's section; false when the read failed as a whole. */
  async #read(module: string): Promise<boolean> {
    this.#reads += 1;
    const answer = await api("ui", `api/settings?module=${encodeURIComponent(module)}`);
    this.#reads -= 1;
    const data = answer.data;
    if (answer.status !== 200 || !Array.isArray(data?.sections) || !Array.isArray(data?.listed)) {
      // A read after the page has its list keeps what it shows.
      if (this.#held.listed === null) this.#failed = readFailed(answer.status, data);
      this.requestUpdate();
      return false;
    }
    const listed: string[] = data.listed.filter((name: unknown): name is string => typeof name === "string");
    this.#held.listed = listed;
    this.#held.error = typeof data.error === "string" && data.error !== "" ? data.error : null;
    for (const section of data.sections as Section[]) {
      if (typeof section?.module === "string") this.#held.sections.set(section.module, section);
    }
    for (const name of [...this.#held.sections.keys()]) {
      if (!listed.includes(name)) this.#held.sections.delete(name);
    }
    this.requestUpdate();
    return true;
  }

  #startSlow(): void {
    this.#slow = false;
    if (this.#slowTimer !== null) clearTimeout(this.#slowTimer);
    this.#slowTimer = setTimeout(() => {
      this.#slowTimer = null;
      this.#slow = true;
      this.requestUpdate();
    }, READING_AFTER_MS);
  }

  #retry(): void {
    this.#held = { listed: null, sections: new Map(), error: null, address: null };
    this.requestUpdate();
    void this.#readFirst();
  }

  // --- moving within the page ---------------------------------------------------------------

  /** Shows `address`, writing the page's own address for it. */
  #open(address: Address): void {
    this.#held.address = address;
    try {
      setAddress("ui", addressOf(address));
      this.#path = location.pathname;
    } catch {
      // The panel shown is not ui's (the page placed by hand): the address stays.
    }
    this.requestUpdate();
  }

  #openModule(event: MouseEvent, module: string): void {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    this.#open({ view: "module", module, more: false, pair: false, missing: null });
  }

  #toggleMore(): void {
    const at = this.#held.address;
    if (at !== null) this.#open({ ...at, more: !at.more, missing: null });
  }

  #copy(text: string): void {
    void navigator.clipboard?.writeText(text).catch(() => undefined);
    this.#copied = text;
    if (this.#copiedTimer !== null) clearTimeout(this.#copiedTimer);
    this.#copiedTimer = setTimeout(() => {
      this.#copiedTimer = null;
      this.#copied = null;
      this.requestUpdate();
    }, COPIED_MS);
    this.requestUpdate();
  }

  // --- drawing ------------------------------------------------------------------------------

  protected updated(changed: PropertyValues): void {
    super.updated(changed);
    if (this.#held.address !== null && this.#slowTimer !== null && this.#held.sections.has(this.#held.address.module)) {
      clearTimeout(this.#slowTimer);
      this.#slowTimer = null;
    }
  }

  render() {
    if (this.#failed !== null) {
      return html`<div class="failed" data-part="failed">
        <p data-line="failed">${this.#failed}</p>
        <button type="button" @click=${() => this.#retry()}>Try again</button></div>`;
    }
    const at = this.#held.address;
    return html`<div class="cols">
      ${this.#list()}
      <section class="pane" data-part="pane" data-module=${at?.module ?? nothing}>${this.#pane()}</section>
    </div>`;
  }

  #list() {
    const { listed, sections, error, address } = this.#held;
    if (listed === null) return html`<nav class="mods" data-part="list" aria-label="Modules" data-reading></nav>`;
    const total = totalChanged(listed, sections, false);
    // While a listed module's section is out, the list says so to whoever reads its marks.
    return html`<nav class="mods" data-part="list" aria-label="Modules" ?data-reading=${total === null}>
      ${listEntries(listed, sections, false).map((entry) => {
        const current = address?.view === "module" && address.module === entry.module;
        return html`<a href=${`/m/ui/settings/${entry.module}`} data-list=${entry.module}
            aria-current=${current ? "page" : nothing} @click=${(event: MouseEvent) => this.#openModule(event, entry.module)}
          >${entry.module}${entry.changed !== null
            ? html`<i title=${`${entry.changed} changed from the default`}>${entry.changed}</i>` : nothing}</a>
          ${entry.unreadable ? html`<span class="under" data-line="unreadable">can't be read</span>` : nothing}`;
      })}
      ${total !== null && total > 0 ? html`<span class="total" data-line="changed">${total} changed</span>` : nothing}
      ${error !== null ? html`<p class="error" data-line="error">${error}</p>` : nothing}
    </nav>`;
  }

  #pane() {
    const at = this.#held.address;
    if (at === null) return this.#slow ? html`<p class="note" data-line="reading">${READING}</p>` : nothing;
    const section = this.#held.sections.get(at.module);
    const missing = at.missing !== null ? html`<p class="missing" data-line="missing">${at.missing}</p>` : nothing;
    switch (paneState(section)) {
      case "reading":
        return html`${this.#slow ? html`<p class="note" data-line="reading">${READING}</p>` : nothing}${missing}`;
      case "unreadable": {
        const it = section as Section;
        return html`<div class="unreadable" data-part="unreadable">
            <p data-line="unreadable">${unreadableWords(it)}</p>
            ${it.fix ? html`<p class="fix" data-line="fix">Fix: <code>${it.fix}</code>
              ${this.#copyButton(it.fix)}</p>` : nothing}
          </div>${this.#foot(it)}${missing}`;
      }
      case "empty":
        return html`<p class="note" data-line="empty">${NOTHING_HERE}</p>${this.#foot(section as Section)}${missing}`;
      default:
        return html`${this.#rows(section as Section, at)}${this.#foot(section as Section)}${missing}`;
    }
  }

  #rows(section: Section, at: Address) {
    const keys = paneKeys(section.module, section.keys);
    const { first, more } = splitMore(keys);
    const off = section.readonly;
    if (more === null) return this.#blocks(first, off);
    const words = moreWords(more);
    return html`${this.#blocks(first, off)}
      <div class="more" data-part="more" ?data-open=${at.more}>
        <span><b>More</b><span data-line="more">${words.text}${words.changed ? ` · ${words.changed}` : ""}</span></span>
        <button type="button" class="link" aria-expanded=${String(at.more)} @click=${() => this.#toggleMore()}
          >${at.more ? "Hide" : "Show"}</button>
      </div>
      ${at.more ? this.#blocks(more, off) : nothing}`;
  }

  #blocks(keys: Setting[], off: boolean) {
    return blocksOf(keys).map((block) => html`
      ${block.group ? html`<div class="subh" data-group=${block.group}>${block.group}</div>` : nothing}
      ${block.keys.map((key) => this.#row(rowOf(key), off))}`);
  }

  #row(row: Row, off: boolean) {
    const locked = off || row.locked;
    const copied = this.#copied === row.name ? html`<span class="copied" data-line="copied">Copied</span>` : nothing;
    const key = html`<code class=${row.labelled ? "key" : "key own"} title="Copy the key" role="button" tabindex="0"
        data-line="key" @click=${() => this.#copy(row.name)}
        @keydown=${(event: KeyboardEvent) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); this.#copy(row.name); } }}
      >${row.name}</code>`;
    const said = row.lines.length > 0 || row.defaults !== null;
    return html`<div class="set" data-key=${row.name} ?data-locked=${locked}>
      <div class="l1">
        <span class="lb">${row.labelled ? html`<span data-line="label">${row.label}</span>${key}` : key}${copied}</span>
        <span class="ctl">${this.#control(row, locked)}</span>
      </div>
      ${row.control.kind === "switches" ? nothing : this.#under(row, locked)}
      ${said ? html`<div class="l2">
        <span class="said">${row.lines.map((line, i) => html`${i > 0 ? " " : ""}<span data-line="line">${line}</span>`)}</span>
        ${row.defaults !== null ? html`<span class="nt" data-line="default">default ${row.defaults.words}${row.defaults.reset
          ? html` · <button type="button" class="link" data-act="reset">Reset</button>` : nothing}</span>` : nothing}
      </div>` : nothing}
      ${row.terminal !== null ? html`<div class="l2"><span class="said" data-line="terminal">${row.terminal.text}</span>
        ${this.#copyButton(row.terminal.command)}</div>` : nothing}
      ${row.control.kind === "switches" ? this.#under(row, locked) : nothing}
    </div>`;
  }

  #copyButton(text: string) {
    return this.#copied === text ? html`<span class="copied" data-line="copied">Copied</span>`
      : html`<button type="button" class="link" data-act="copy" @click=${() => this.#copy(text)}>Copy</button>`;
  }

  /** The control at the right of a row's first line. */
  #control(row: Row, locked: boolean) {
    const control: Control = row.control;
    const name = row.name;
    switch (control.kind) {
      case "switch":
        return html`<button type="button" class="sw" role="switch" aria-checked=${String(control.on)} aria-label=${row.label}
          data-control=${name} ?disabled=${locked}></button>`;
      case "choices":
        return html`<span class="tri" role="radiogroup" aria-label=${row.label} data-control=${name}>
          ${control.options.map((option) => html`<button type="button" role="radio" data-value=${option.value}
            aria-checked=${String(option.value === control.selected)} ?disabled=${locked}>${option.words}</button>`)}</span>`;
      case "pick":
        return html`<select class="fld" aria-label=${row.label} data-control=${name} ?disabled=${locked}
          .value=${live(control.selected)}>${control.options.map((option) => html`<option value=${option.value}
            ?selected=${option.value === control.selected}>${option.words}</option>`)}</select>`;
      case "number":
        return html`<input class="fld num" type="text" inputmode=${control.whole ? "numeric" : "decimal"}
          aria-label=${row.label} data-control=${name} .value=${live(control.text)} ?disabled=${locked}>
          ${control.unit ? html`<span class="unit">${control.unit}</span>` : nothing}`;
      case "text":
        return html`<input class="fld txt" type="text" aria-label=${row.label} data-control=${name}
          placeholder=${control.path ? PATH_HINT : nothing} .value=${live(control.text)} ?disabled=${locked}>`;
      case "long":
        return html`<button type="button" class="link" data-control=${name} data-act="edit" ?disabled=${locked}>Edit</button>`;
      case "names":
        return html`<span class="toks" data-control=${name}>${control.items.length === 0
          ? html`<span class="dim" data-line="none">none</span>`
          : control.items.map((item) => html`<span class="tok" data-item=${item}>${item}</span>`)}
          <button type="button" class="link dim" data-act="add" ?disabled=${locked}>Add</button></span>`;
      case "lines":
        return html`<span class="dim" data-control=${name}>${control.items.length} ${control.items.length === 1 ? "line" : "lines"}
          · <button type="button" class="link" data-act="edit" ?disabled=${locked}>Edit</button></span>`;
      case "rows":
        return html`<button type="button" class="link dim" data-control=${name} data-act="add" ?disabled=${locked}>Add a row</button>`;
      case "switches":
        return nothing;
      case "toml":
        return html`<button type="button" class="link" data-control=${name} data-act="edit" ?disabled=${locked}>Edit</button>`;
      case "word":
        return html`<span data-control=${name} data-line="word">${control.text}</span>`;
    }
  }

  /** What a row draws under its first line: a long text's first line, a list's first
   * item, a table of rows, a switch table's entries. */
  #under(row: Row, locked: boolean) {
    const control = row.control;
    switch (control.kind) {
      case "long":
        return html`<span class="wide" data-line="first">${control.first}</span>`;
      case "lines":
        return control.first ? html`<span class="wide" data-line="first">${control.first}</span>` : nothing;
      case "rows":
        return html`<div class="table" role="table" aria-label=${row.label}
            style=${styleMap({ "grid-template-columns": `repeat(${control.columns.length}, minmax(0, 1fr)) auto` })}>
          ${control.columns.map((column) => html`<span class="h" role="columnheader">${column}</span>`)}<span></span>
          ${control.rows.map((item, i) => html`${control.columns.map((column) => html`<input class="fld" type="text"
              aria-label=${`${column}, row ${i + 1}`} data-cell=${`${i}.${column}`} ?disabled=${locked}
              .value=${live(cellText(item[column]))}>`)}
            <button type="button" class="link dim" data-act="remove" ?disabled=${locked}>Remove</button>`)}
        </div>`;
      case "switches":
        return html`<div class="entries">${control.toggles.map((toggle) => html`<div class="entry" data-entry=${toggle.key}>
          <span>${toggle.label}</span>
          <button type="button" class="sw" role="switch" aria-checked=${String(toggle.on)} aria-label=${`${row.label} ${toggle.label}`}
            data-control=${toggle.key} ?disabled=${locked || toggle.locked}></button>
          ${toggle.line !== null || toggle.locked ? html`<span class="l2">${[toggle.line, toggle.locked ? LOCKED : null]
            .filter((line) => line !== null).map((line) => html`<span data-line="line">${line}</span> `)}</span>` : nothing}
        </div>`)}</div>`;
      default:
        return nothing;
    }
  }

  #foot(section: Section) {
    const page = this.#pageOf(section.module);
    return html`<div class="foot" data-part="foot">
      <span class="file" data-line="file">${section.file ? `Kept in ${section.file}` : ""}</span>
      ${page !== null ? html`<a href=${`/m/${section.module}/${page.route}`} data-module=${section.module}
        data-route=${page.route} data-line="page">${page.title}</a>` : nothing}
    </div>`;
  }

  /** The module's own page by the band's name for it; ui's is Modules. */
  #pageOf(module: string): { route: string; title: string } | null {
    if (module === "ui") return { route: "modules", title: "Modules" };
    if (!installed(module)) return null;
    const page = components(module).find((comp) => comp.name === "page");
    return page ? { route: "", title: page.title } : null;
  }
}

/** A table cell's value as the text in its field. */
function cellText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

if (!customElements.get(TAG)) customElements.define(TAG, UiSettings);

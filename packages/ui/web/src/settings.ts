// <moxi-ui-settings route="settings…">: the Settings page at the desk, placed by
// <moxi-ui-page> on its settings routes.
//
// Two columns: the list of modules with settings (the core, ui, then A to Z, from the
// read's `listed`), each with its count of changed settings once its section is in; and
// the pane, one module's settings at a time: its rows by type, the keys marked `common`
// first and the rest behind `More`, under their sub-heads, and its foot. The rows,
// counts and words come from `settings-model.ts`; the addresses from `find-model.ts`.
//
// Nothing is written until Save. A change is held per module (`save-model.ts`): its row
// is heavier and says what it was, with Undo; `Reset` holds the default the same way.
// The Save line at the pane's foot sends the shown module's changes and nothing else
// (`POST /api/settings/save`), says what each answer was, and turns into a confirm when
// a change can hurt. While any change is held the page holds the shell (`holdLeave`), so
// nothing leaves it without asking in the Save line's place.
//
// The page reads the shown module first (`GET /api/settings?module=`), draws the list
// from `listed`, then reads every other listed module two at a time, and reads them all
// again when its tab shows again, keeping the changes held. It writes its own address
// through `setAddress` as a module or `More` opens. When the shell draws the panel again
// in place (a modules or session event), the new element takes over what the old one
// held, the changes and the Save line's words among it, so nothing is lost.

import { css, html, LitElement, nothing } from "lit";
import type { PropertyValues } from "lit";
import { live } from "lit/directives/live.js";
import { styleMap } from "lit/directives/style-map.js";
import { api, components, holdLeave, installed, setAddress, setTheme } from "/m/ui/client.js";
import type { ThemeMode } from "/m/ui/client.js";

import { addressOf, parseAddress } from "./find-model.ts";
import type { Address } from "./find-model.ts";
import {
  allSaved, answerWords, changeLabel, clockOf, confirmOf, heldWords, leaveAsk, numberValue, Pending, same, saved,
  shownKey, staysWarned, transportWords,
} from "./save-model.ts";
import type { SaveResult } from "./save-model.ts";
import {
  blocksOf, CORE, defaultOf, listEntries, LOCKED, moduleOrder, moreWords, NOTHING_HERE, paneKeys, paneState, PATH_HINT,
  READING, READING_AFTER_MS, readFailed, rowOf, splitMore, totalChanged, unreadableWords, valueOf,
} from "./settings-model.ts";
import type { Control, Row, Section, Setting, Toggle } from "./settings-model.ts";

const TAG = "moxi-ui-settings";
/** How many module reads run at once. */
const READS_AT_ONCE = 2;
/** How long `Copied` shows after a key or command is copied. */
const COPIED_MS = 2000;
/** ui's keys the shell document's colours follow, applied at once when a save writes one. */
const THEME_KEYS = ["theme.mode", "theme.palette"];
const MODES: readonly string[] = ["system", "light", "dark"];

/** What the page holds, and what a page placed again in its place takes over. */
interface Held {
  /** The read's listed modules, null until the first answer. */
  listed: string[] | null;
  sections: Map<string, Section>;
  /** The read's own error: the module snapshot is not ready or is off. */
  error: string | null;
  /** The address shown, null until the first answer. */
  address: Address | null;
  /** The changes not saved, per module, and the words a save or a read left beside rows. */
  pending: Pending;
  /** The Save line's words after a save's answer, per module, until the module's next change. */
  said: Map<string, string>;
}

function fresh(): Held {
  return { listed: null, sections: new Map(), error: null, address: null, pending: new Pending(), said: new Map() };
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
  .foot.after { border-top: 0; margin-top: 0; }
  .set.un .lb { font-weight: 600; }
  .set.un .lb .key { font-weight: 400; }
  .set.un .fld { border-color: var(--moxi-border-strong); }
  .set.dg .lb, .l2 .w { color: var(--moxi-warn); }
  .set.dg .lb .key { color: var(--moxi-text-dim); }
  .set.dg .sw[aria-checked="true"] { background: var(--moxi-warn); border-color: var(--moxi-warn); }
  .l2 b { font-weight: 600; color: var(--moxi-text); }
  .l2 .w b { color: var(--moxi-warn); }
  .entry.un > span:first-child { font-weight: 600; }
  .mods a.u { font-weight: 600; color: var(--moxi-text); }
  .tok { display: inline-flex; align-items: center; gap: 4px; }
  .tok .link { color: var(--moxi-text-dim); font-size: 12px; line-height: 1; }
  .fld.add { width: 120px; font-size: 13px; padding: 3px 7px; text-align: left; }
  textarea.fld { display: block; width: 100%; min-height: 5rem; margin: 3px 0 2px; font-family: var(--moxi-font-mono);
                 font-size: 13px; line-height: 18px; resize: vertical; }
  .items { display: grid; gap: 4px; margin: 3px 0 2px; }
  .items .item { display: flex; align-items: center; gap: 8px; }
  .items .fld { flex: 1 1 auto; min-width: 0; font-size: 13px; }
  .save { position: sticky; bottom: 0; z-index: 1; display: flex; flex-wrap: wrap; justify-content: space-between;
          align-items: center; gap: 8px 18px; border-top: 1px solid var(--moxi-border); margin-top: 6px; padding: 12px 0;
          font-size: 14px; line-height: 20px; color: var(--moxi-text-dim); background: var(--moxi-bg); }
  .save.conf { align-items: flex-start; }
  .save .msg { flex: 1 1 240px; min-width: 0; overflow-wrap: anywhere; }
  .save .msg b { font-weight: 600; color: var(--moxi-text); }
  .save .msg b.w { color: var(--moxi-warn); }
  .save .msg span { display: block; }
  .save .btns { display: flex; flex-wrap: wrap; align-items: center; gap: 18px; flex: none; }
  .btn { font-weight: 600; font-size: 14px; line-height: 1; border: 1px solid var(--moxi-border);
         border-radius: var(--moxi-radius); padding: 8px 16px; background: none; color: var(--moxi-text-dim);
         cursor: pointer; }
  .btn.acc { background: var(--moxi-accent); border-color: var(--moxi-accent); color: var(--moxi-on-accent); }
  .btn.wn { color: var(--moxi-warn); border-color: var(--moxi-warn); }
  .btn.stay { color: var(--moxi-text); }
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
  #held: Held = fresh();
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
  /** The module whose save is running. */
  #saving: string | null = null;
  /** The module whose Save line asks to confirm a change that can hurt. */
  #confirm: string | null = null;
  /** The shell's move waiting on the leave ask, while it shows. */
  #leave: (() => void) | null = null;
  /** The move to make once a save asked for by `Save and leave` writes every change. */
  #leaving: (() => void) | null = null;
  /** The rows whose Edit is open, and the row whose Add field is, as `<module>/<key>`. */
  #editing = new Set<string>();
  #adding: string | null = null;
  /** What was typed in a number field or a table's cell, by `<module>/<key>` (and the
   * cell), shown while it reads as the value held, so the typing is not read back over. */
  #typed = new Map<string, string>();
  /** Releases the page's hold on the shell; null while it holds none. */
  #release: (() => void) | null = null;
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
      this.#syncHold();
      void this.#readAll();
    } else {
      void this.#readFirst();
    }
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    document.removeEventListener("visibilitychange", this.#onVisible);
    for (const timer of [this.#slowTimer, this.#copiedTimer]) if (timer !== null) clearTimeout(timer);
    const release = this.#release;
    this.#release = null;
    release?.();
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

  /** Reads one module's section; false when the read failed as a whole. A change held for
   * a key the read finds moved says so at once (`Pending.refresh`). */
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
      if (typeof section?.module !== "string") continue;
      this.#held.sections.set(section.module, section);
      if (section.error === null && Array.isArray(section.keys)) this.#held.pending.refresh(section.module, section.keys);
    }
    for (const name of [...this.#held.sections.keys()]) {
      if (!listed.includes(name)) this.#held.sections.delete(name);
    }
    this.#syncHold();
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
    this.#held = fresh();
    this.#syncHold();
    this.requestUpdate();
    void this.#readFirst();
  }

  // --- moving within the page ---------------------------------------------------------------

  /** Shows `address`, writing the page's own address for it. */
  #open(address: Address): void {
    this.#held.address = address;
    this.#confirm = null;
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

  // --- changing and saving --------------------------------------------------------------------

  /** Takes the hold on the shell while any change is held, and releases it when none is. */
  #syncHold(): void {
    const held = this.#held.pending.total() > 0;
    if (held && this.#release === null) {
      this.#release = holdLeave((go) => this.#ask(go));
    } else if (!held && this.#release !== null) {
      const release = this.#release;
      this.#release = null;
      this.#leave = null;
      release();
    }
  }

  /** A change was made, undone or dropped in `module`: its Save line says what is held now. */
  #changed(module: string): void {
    this.#held.said.delete(module);
    if (this.#confirm === module) this.#confirm = null;
    this.#syncHold();
    this.requestUpdate();
  }

  #edit(module: string, key: Setting, value: unknown): void {
    this.#held.pending.edit(module, key, value);
    this.#changed(module);
  }

  /** A table's TOML text: the text the read gave drops the change. */
  #editToml(module: string, key: Setting, text: string): void {
    if (text === (typeof key.value === "string" ? key.value : "")) this.#held.pending.undo(module, key.name);
    else this.#held.pending.edit(module, key, text);
    this.#changed(module);
  }

  /** The list `key` holds as shown: the held one, else the one read. */
  #items(module: string, key: Setting): unknown[] {
    const value = valueOf(shownKey(this.#held.pending, module, key));
    return Array.isArray(value) ? [...value] : [];
  }

  /** A list item typed for `key`: a number for a list of numbers when it reads as one. */
  #item(key: Setting, text: string): unknown {
    return key.items === "int" || key.items === "float" ? numberValue(text) : text;
  }

  #addItem(module: string, key: Setting, text: string): void {
    this.#adding = null;
    if (text.trim() === "") {
      this.requestUpdate();
      return;
    }
    this.#edit(module, key, [...this.#items(module, key), this.#item(key, text.trim())]);
  }

  #toggleEdit(at: string): void {
    if (this.#editing.has(at)) this.#editing.delete(at);
    else this.#editing.add(at);
    this.requestUpdate();
  }

  /** Saves `module`'s changes, after the confirm when one can hurt. Gives the answer's
   * results, or null when nothing was sent or no answer could be read. */
  async #save(module: string, confirmed = false): Promise<SaveResult[] | null> {
    const section = this.#held.sections.get(module);
    if (section === undefined || this.#saving !== null) return null;
    const keys = section.keys;
    const changes = this.#held.pending.changes(module, keys);
    if (changes.length === 0) return null;
    if (!confirmed && confirmOf(keys, changes) !== null) {
      this.#confirm = module;
      this.requestUpdate();
      return null;
    }
    this.#confirm = null;
    this.#saving = module;
    this.#held.said.delete(module);
    this.requestUpdate();
    const answer = await api("ui", "api/settings/save", this.#held.pending.body(module, keys));
    const data = answer.data;
    let results: SaveResult[] | null = null;
    if (answer.status === 200 && Array.isArray(data?.results)) {
      results = data.results as SaveResult[];
      this.#held.pending.answer(module, keys, results);
      const at = typeof data.saved_at === "number" ? data.saved_at : Date.now() / 1000;
      this.#held.said.set(module, answerWords(keys, results, clockOf(at)));
    } else {
      this.#held.said.set(module, transportWords(answer.status));
    }
    this.#saving = null;
    this.requestUpdate();
    // After any answer the module is read again; the hold is released once that is in.
    await this.#read(module);
    if (module === "ui" && results?.some((result) => saved(result) && THEME_KEYS.includes(result.key))) {
      this.#applyTheme();
    }
    this.#syncHold();
    this.requestUpdate();
    return results;
  }

  /** The shell document's colours from ui's `theme.*` as the read now gives them. */
  #applyTheme(): void {
    const keys = this.#held.sections.get("ui")?.keys ?? [];
    const now = (name: string): string | undefined => {
      const key = keys.find((item) => item.name === name);
      const value = key === undefined ? undefined : valueOf(key) ?? defaultOf(key);
      return typeof value === "string" ? value : undefined;
    };
    const mode = now("theme.mode");
    if (mode === undefined || !MODES.includes(mode)) return;
    try {
      setTheme(mode as ThemeMode, now("theme.palette"));
    } catch {
      // A theme name setTheme refuses keeps the colours shown.
    }
  }

  /** The confirm's button: saves what it confirmed, then leaves when `Save and leave` asked. */
  async #confirmed(module: string): Promise<void> {
    const results = await this.#save(module, true);
    this.#leaveIfSaved(results);
  }

  #goBack(): void {
    this.#confirm = null;
    this.#leaving = null;
    this.requestUpdate();
  }

  #discard(module: string): void {
    this.#held.pending.discard(module);
    this.#changed(module);
  }

  /** The shell asks to move off the page: the Save line asks in its own words. */
  #ask(go: () => void): void {
    this.#leave = go;
    this.#confirm = null;
    this.requestUpdate();
    void this.updateComplete.then(() => {
      this.renderRoot.querySelector("[data-part=save]")?.scrollIntoView({ block: "nearest" });
    });
  }

  #stay(): void {
    this.#leave = null;
    this.requestUpdate();
  }

  /** `Leave without saving`: every change dropped, then the move. */
  #leaveWithout(): void {
    const go = this.#leave;
    this.#leave = null;
    for (const module of this.#held.pending.modules()) this.#held.pending.discard(module);
    this.#held.said.clear();
    // The move releases the hold itself.
    this.#release = null;
    go?.();
  }

  /** `Save and leave`: leaves only when every change was written or the file held it. */
  async #saveAndLeave(module: string): Promise<void> {
    this.#leaving = this.#leave;
    this.#leave = null;
    const results = await this.#save(module);
    this.#leaveIfSaved(results);
  }

  /** After a save `Save and leave` asked for: leaves when every change was written or the
   * file held it; otherwise stays, the answer shown. While the confirm shows, it waits. */
  #leaveIfSaved(results: SaveResult[] | null): void {
    if (this.#confirm !== null) return;
    const go = this.#leaving;
    this.#leaving = null;
    if (go !== null && results !== null && allSaved(results) && this.#held.pending.total() === 0) go();
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
    const { listed, sections, error, address, pending } = this.#held;
    if (listed === null) return html`<nav class="mods" data-part="list" aria-label="Modules" data-reading></nav>`;
    const total = totalChanged(listed, sections, false);
    // While a listed module's section is out, the list says so to whoever reads its marks.
    return html`<nav class="mods" data-part="list" aria-label="Modules" ?data-reading=${total === null}>
      ${listEntries(listed, sections, false).map((entry) => {
        const current = address?.view === "module" && address.module === entry.module;
        const held = pending.count(entry.module);
        return html`<a href=${`/m/ui/settings/${entry.module}`} data-list=${entry.module} class=${held > 0 ? "u" : ""}
            aria-current=${current ? "page" : nothing} @click=${(event: MouseEvent) => this.#openModule(event, entry.module)}
          >${entry.module}${entry.changed !== null
            ? html`<i title=${`${entry.changed} changed from the default`}>${entry.changed}</i>` : nothing}</a>
          ${held > 0 ? html`<span class="under" data-line="unsaved">${held} not saved</span>` : nothing}
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
    // The leave ask shows whatever the pane holds.
    const ask = this.#leave !== null ? this.#leaveLine(at.module) : nothing;
    switch (paneState(section)) {
      case "reading":
        return html`${this.#slow ? html`<p class="note" data-line="reading">${READING}</p>` : nothing}${ask}${missing}`;
      case "unreadable": {
        const it = section as Section;
        return html`<div class="unreadable" data-part="unreadable">
            <p data-line="unreadable">${unreadableWords(it)}</p>
            ${it.fix ? html`<p class="fix" data-line="fix">Fix: <code>${it.fix}</code>
              ${this.#copyButton(it.fix)}</p>` : nothing}
          </div>${ask}${this.#foot(it, ask !== nothing)}${missing}`;
      }
      case "empty":
        return html`<p class="note" data-line="empty">${NOTHING_HERE}</p>${ask}${this.#foot(section as Section, ask !== nothing)}${missing}`;
      default:
        return html`${this.#rows(section as Section, at)}${this.#saveLine(section as Section)}${this.#foot(section as Section, true)}${missing}`;
    }
  }

  #rows(section: Section, at: Address) {
    const keys = paneKeys(section.module, section.keys);
    const { first, more } = splitMore(keys);
    const off = section.readonly || this.#saving === section.module;
    if (more === null) return this.#blocks(section, first, off);
    const words = moreWords(more);
    return html`${this.#blocks(section, first, off)}
      <div class="more" data-part="more" ?data-open=${at.more}>
        <span><b>More</b><span data-line="more">${words.text}${words.changed ? ` · ${words.changed}` : ""}</span></span>
        <button type="button" class="link" aria-expanded=${String(at.more)} @click=${() => this.#toggleMore()}
          >${at.more ? "Hide" : "Show"}</button>
      </div>
      ${at.more ? this.#blocks(section, more, off) : nothing}`;
  }

  #blocks(section: Section, keys: Setting[], off: boolean) {
    return blocksOf(keys).map((block) => html`
      ${block.group ? html`<div class="subh" data-group=${block.group}>${block.group}</div>` : nothing}
      ${block.keys.map((key) => this.#row(section, key, off))}`);
  }

  #row(section: Section, key: Setting, off: boolean) {
    const module = section.module;
    const pending = this.#held.pending;
    const shown = shownKey(pending, module, key);
    const row: Row = rowOf(shown);
    const locked = off || row.locked;
    const held = pending.heldLine(module, section.keys, row.name);
    const note = pending.noteOf(module, row.name);
    const warned = held?.hurts === true || staysWarned(key, valueOf(shown));
    const copied = this.#copied === row.name ? html`<span class="copied" data-line="copied">Copied</span>` : nothing;
    const name = html`<code class=${row.labelled ? "key" : "key own"} title="Copy the key" role="button" tabindex="0"
        data-line="key" @click=${() => this.#copy(row.name)}
        @keydown=${(event: KeyboardEvent) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); this.#copy(row.name); } }}
      >${row.name}</code>`;
    const said = note !== undefined || row.lines.length > 0 || row.defaults !== null || held !== null;
    const classes = ["set", held !== null ? "un" : "", warned ? "dg" : ""].filter(Boolean).join(" ");
    return html`<div class=${classes} data-key=${row.name} ?data-locked=${locked} ?data-held=${held !== null}>
      <div class="l1">
        <span class="lb">${row.labelled ? html`<span data-line="label">${row.label}</span>${name}` : name}${copied}</span>
        <span class="ctl">${this.#control(module, key, row, locked)}</span>
      </div>
      ${row.control.kind === "switches" ? nothing : this.#under(module, key, row, locked)}
      ${said ? html`<div class="l2">
        <span class="said">${note !== undefined ? this.#note(note.text, note.copy)
          : row.lines.map((line, i) => html`${i > 0 ? " " : ""}<span data-line="line">${line}</span>`)}</span>
        ${held !== null ? this.#heldLine(module, row.name, held.text, held.hurts)
          : row.defaults !== null ? html`<span class="nt" data-line="default">default ${row.defaults.words}${row.defaults.reset
          ? html` · <button type="button" class="link" data-act="reset" ?disabled=${locked}
              @click=${() => { pending.reset(module, key); this.#changed(module); }}>Reset</button>` : nothing}</span>` : nothing}
      </div>` : nothing}
      ${row.terminal !== null ? html`<div class="l2"><span class="said" data-line="terminal">${row.terminal.text}</span>
        ${this.#copyButton(row.terminal.command)}</div>` : nothing}
      ${row.control.kind === "switches" ? this.#under(module, key, row, locked) : nothing}
    </div>`;
  }

  /** The words a save or a read left beside a row, `Not saved.` set off. */
  #note(text: string, copy: string | null) {
    const lead = text.startsWith("Not saved.") ? "Not saved." : null;
    return html`<span data-line="note">${lead !== null ? html`<b>${lead}</b>${text.slice(lead.length)}` : text}</span>${copy
      ? html` ${this.#copyButton(copy)}` : nothing}`;
  }

  /** `not saved · was <value> · Undo` under a row that holds a change; in the warning colour
   * when it can hurt. */
  #heldLine(module: string, name: string, text: string, hurts: boolean) {
    const [head, ...rest] = text.split(" · ");
    const words = head === "not saved" ? html`<b>${head}</b>${rest.length > 0 ? ` · ${rest.join(" · ")}` : ""}` : text;
    return html`<span class="nt" data-line="held"><span class=${hurts ? "w" : ""}>${words}</span> · <button type="button"
      class="link" data-act="undo" ?disabled=${this.#saving === module}
      @click=${() => { this.#held.pending.undo(module, name); this.#changed(module); }}>Undo</button></span>`;
  }

  #copyButton(text: string) {
    return this.#copied === text ? html`<span class="copied" data-line="copied">Copied</span>`
      : html`<button type="button" class="link" data-act="copy" @click=${() => this.#copy(text)}>Copy</button>`;
  }

  /** The control at the right of a row's first line. */
  #control(module: string, key: Setting, row: Row, locked: boolean) {
    const control: Control = row.control;
    const name = row.name;
    const at = `${module}/${name}`;
    switch (control.kind) {
      case "switch":
        return html`<button type="button" class="sw" role="switch" aria-checked=${String(control.on)} aria-label=${row.label}
          data-control=${name} ?disabled=${locked} @click=${() => this.#edit(module, key, !control.on)}></button>`;
      case "choices":
        return html`<span class="tri" role="radiogroup" aria-label=${row.label} data-control=${name}>
          ${control.options.map((option) => html`<button type="button" role="radio" data-value=${option.value}
            aria-checked=${String(option.value === control.selected)} ?disabled=${locked}
            @click=${() => this.#edit(module, key, option.value)}>${option.words}</button>`)}</span>`;
      case "pick":
        return html`<select class="fld" aria-label=${row.label} data-control=${name} ?disabled=${locked}
          .value=${live(control.selected)} @change=${(event: Event) => this.#edit(module, key,
            (event.target as HTMLSelectElement).value)}>${control.options.map((option) => html`<option
            value=${option.value} ?selected=${option.value === control.selected}>${option.words}</option>`)}</select>`;
      case "number":
        return html`<input class="fld num" type="text" inputmode=${control.whole ? "numeric" : "decimal"}
          aria-label=${row.label} data-control=${name} ?disabled=${locked}
          .value=${live(this.#typedOr(at, numberValue, valueOf(shownKey(this.#held.pending, module, key)), control.text))}
          @input=${(event: Event) => {
            const text = (event.target as HTMLInputElement).value;
            this.#typed.set(at, text);
            this.#edit(module, key, numberValue(text));
          }}>
          ${control.unit ? html`<span class="unit">${control.unit}</span>` : nothing}`;
      case "text":
        return html`<input class="fld txt" type="text" aria-label=${row.label} data-control=${name}
          placeholder=${control.path ? PATH_HINT : nothing} .value=${live(control.text)} ?disabled=${locked}
          @input=${(event: Event) => this.#edit(module, key, (event.target as HTMLInputElement).value)}>`;
      case "long":
      case "lines":
      case "toml": {
        const open = this.#editing.has(at);
        const edit = html`<button type="button" class="link" data-act="edit" aria-expanded=${String(open)}
          ?disabled=${locked && !open} @click=${() => this.#toggleEdit(at)}>${open ? "Done" : "Edit"}</button>`;
        if (control.kind === "lines") {
          return html`<span class="dim" data-control=${name}>${control.items.length}
            ${control.items.length === 1 ? "line" : "lines"} · ${edit}</span>`;
        }
        return html`<span data-control=${name}>${edit}</span>`;
      }
      case "names": {
        const items = this.#items(module, key);
        return html`<span class="toks" data-control=${name}>${control.items.length === 0
          ? html`<span class="dim" data-line="none">none</span>`
          : control.items.map((item, i) => html`<span class="tok" data-item=${item}>${item}<button type="button"
              class="link" aria-label=${`Remove ${item}`} data-act="remove" ?disabled=${locked}
              @click=${() => this.#edit(module, key, items.filter((_, j) => j !== i))}>×</button></span>`)}
          ${this.#adding === at
            ? html`<input class="fld add" type="text" aria-label=${`Add to ${row.label}`} data-act="item"
                @keydown=${(event: KeyboardEvent) => this.#addKey(event, module, key)}
                @blur=${(event: FocusEvent) => this.#addItem(module, key, (event.target as HTMLInputElement).value)}>`
            : html`<button type="button" class="link dim" data-act="add" ?disabled=${locked}
                @click=${() => this.#startAdding(at)}>Add</button>`}</span>`;
      }
      case "rows":
        return html`<button type="button" class="link dim" data-control=${name} data-act="add" ?disabled=${locked}
          @click=${() => this.#edit(module, key, [...this.#items(module, key),
            Object.fromEntries(control.columns.map((column) => [column, ""]))])}>Add a row</button>`;
      case "switches":
        return nothing;
      case "word":
        return html`<span data-control=${name} data-line="word">${control.text}</span>`;
    }
  }

  /** The text typed at `at` while it reads as `value`, else `text`. */
  #typedOr(at: string, read: (text: string) => unknown, value: unknown, text: string): string {
    const typed = this.#typed.get(at);
    return typed !== undefined && same(read(typed), value) ? typed : text;
  }

  #startAdding(at: string): void {
    this.#adding = at;
    this.requestUpdate();
    void this.updateComplete.then(() => (this.renderRoot.querySelector("input[data-act=item]") as HTMLInputElement | null)?.focus());
  }

  /** Enter adds the item typed, Escape leaves it. */
  #addKey(event: KeyboardEvent, module: string, key: Setting): void {
    const input = event.target as HTMLInputElement;
    if (event.key === "Enter") {
      event.preventDefault();
      const text = input.value;
      input.value = "";
      this.#addItem(module, key, text);
    } else if (event.key === "Escape") {
      event.preventDefault();
      input.value = "";
      this.#adding = null;
      this.requestUpdate();
    }
  }

  /** What a row draws under its first line: a long text's first line (the whole text
   * while Edit is open), a list's first item (a field per item while open), a table's TOML
   * while open, a table of rows, a switch table's entries. */
  #under(module: string, key: Setting, row: Row, locked: boolean) {
    const control = row.control;
    const open = this.#editing.has(`${module}/${row.name}`);
    switch (control.kind) {
      case "long":
        return open ? html`<textarea class="fld" aria-label=${row.label} data-field=${row.name} ?disabled=${locked}
            .value=${live(control.text)} @input=${(event: Event) => this.#edit(module, key,
              (event.target as HTMLTextAreaElement).value)}></textarea>`
          : html`<span class="wide" data-line="first">${control.first}</span>`;
      case "toml":
        return open ? html`<textarea class="fld" aria-label=${row.label} data-field=${row.name} ?disabled=${locked}
            .value=${live(control.text)} @input=${(event: Event) => this.#editToml(module, key,
              (event.target as HTMLTextAreaElement).value)}></textarea>` : nothing;
      case "lines": {
        if (!open) return control.first ? html`<span class="wide" data-line="first">${control.first}</span>` : nothing;
        const items = this.#items(module, key);
        return html`<div class="items" data-field=${row.name}>${control.items.map((item, i) => html`<div class="item">
            <input class="fld" type="text" aria-label=${`${row.label}, line ${i + 1}`} ?disabled=${locked}
              .value=${live(item)} @input=${(event: Event) => this.#edit(module, key, items.map((old, j) =>
                (j === i ? this.#item(key, (event.target as HTMLInputElement).value) : old)))}>
            <button type="button" class="link dim" data-act="remove" ?disabled=${locked}
              @click=${() => this.#edit(module, key, items.filter((_, j) => j !== i))}>Remove</button></div>`)}
          <span><button type="button" class="link dim" data-act="add" ?disabled=${locked}
            @click=${() => this.#edit(module, key, [...items, ""])}>Add a line</button></span></div>`;
      }
      case "rows": {
        const items = this.#items(module, key);
        const read = (key.json !== undefined ? valueOf(key) : null) as Record<string, unknown>[] | null;
        // A cell whose value read is no string takes what is typed as JSON when it reads as JSON.
        const parse = (i: number, column: string) => (text: string): unknown => {
          const before = Array.isArray(read) ? read[i]?.[column] : undefined;
          if (before === undefined || typeof before === "string") return text;
          try {
            return JSON.parse(text);
          } catch {
            return text;
          }
        };
        const cell = (i: number, column: string, text: string): void => {
          this.#typed.set(`${module}/${row.name}/${i}.${column}`, text);
          const value = parse(i, column)(text);
          this.#edit(module, key, items.map((item, j) => (j === i ? { ...(item as object), [column]: value } : item)));
        };
        return html`<div class="table" role="table" aria-label=${row.label}
            style=${styleMap({ "grid-template-columns": `repeat(${control.columns.length}, minmax(0, 1fr)) auto` })}>
          ${control.columns.map((column) => html`<span class="h" role="columnheader">${column}</span>`)}<span></span>
          ${control.rows.map((item, i) => html`${control.columns.map((column) => html`<input class="fld" type="text"
              aria-label=${`${column}, row ${i + 1}`} data-cell=${`${i}.${column}`} ?disabled=${locked}
              .value=${live(this.#typedOr(`${module}/${row.name}/${i}.${column}`, parse(i, column), item[column],
                cellText(item[column])))}
              @input=${(event: Event) => cell(i, column, (event.target as HTMLInputElement).value)}>`)}
            <button type="button" class="link dim" data-act="remove" ?disabled=${locked}
              @click=${() => this.#edit(module, key, items.filter((_, j) => j !== i))}>Remove</button>`)}
        </div>`;
      }
      case "switches":
        return html`<div class="entries">${control.toggles.map((toggle) => this.#entry(module, key, row, toggle, locked))}</div>`;
      default:
        return nothing;
    }
  }

  /** One entry of a switch table: its switch, its lines, and what it holds not saved. */
  #entry(module: string, key: Setting, row: Row, toggle: Toggle, locked: boolean) {
    const pending = this.#held.pending;
    const section = this.#held.sections.get(module);
    const held = pending.heldLine(module, section?.keys ?? [key], toggle.key);
    const note = pending.noteOf(module, toggle.key);
    const entry = (Array.isArray(key.entries) ? key.entries : []).find((item) => item?.key === toggle.key);
    const lines = [toggle.line, toggle.locked ? LOCKED : null].filter((line): line is string => line !== null);
    return html`<div class=${held !== null ? "entry un" : "entry"} data-entry=${toggle.key}>
      <span>${toggle.label}</span>
      <button type="button" class="sw" role="switch" aria-checked=${String(toggle.on)} aria-label=${`${row.label} ${toggle.label}`}
        data-control=${toggle.key} ?disabled=${locked || toggle.locked}
        @click=${() => { pending.editEntry(module, key, entry?.name ?? toggle.label, !toggle.on); this.#changed(module); }}
      ></button>
      ${lines.length > 0 || held !== null || note !== undefined ? html`<span class="l2">
        <span class="said">${note !== undefined ? this.#note(note.text, note.copy)
          : lines.map((line) => html`<span data-line="line">${line}</span> `)}</span>
        ${held !== null ? this.#heldLine(module, toggle.key, held.text, held.hurts) : nothing}</span>` : nothing}
    </div>`;
  }

  /** The Save line at the pane's foot: what is held, or what the last save answered, with
   * Discard and Save; the confirm in its place when a change can hurt, and the leave ask
   * when the shell asks to move. */
  #saveLine(section: Section) {
    const module = section.module;
    if (this.#leave !== null) return this.#leaveLine(module);
    const keys = section.keys;
    const pending = this.#held.pending;
    const changes = pending.changes(module, keys);
    if (this.#confirm === module) {
      const confirm = confirmOf(keys, changes);
      if (confirm !== null) {
        return html`<div class="save conf" data-part="save" data-state="confirm">
          <p class="msg" data-line="save">${confirm.asks.map((ask, i) => html`${i > 0 ? " " : ""}<b class="w"
            data-line="ask">${ask.ask}</b> ${ask.confirm}`)} ${confirm.rest}</p>
          <span class="btns"><button type="button" class="link" @click=${() => this.#goBack()}>${confirm.back}</button>
            <button type="button" class="btn wn" @click=${() => void this.#confirmed(module)}>${confirm.button}</button></span>
        </div>`;
      }
    }
    const saving = this.#saving === module;
    const said = this.#held.said.get(module);
    const text = saving || said === undefined ? heldWords(keys, changes, saving) : said;
    const [head, ...rest] = text.split(" · ");
    const bold = /^\d+ (not saved|of \d+ saved)/.exec(head);
    const words = bold !== null
      ? html`<b>${bold[0]}</b>${head.slice(bold[0].length)}${rest.length > 0 ? ` · ${rest.join(" · ")}` : ""}` : text;
    const open = changes.length > 0 && !saving;
    return html`<div class="save" data-part="save" data-state=${saving ? "saving" : open ? "held" : "rest"}>
      <p class="msg" data-line="save">${words}</p>
      <span class="btns">${open ? html`<button type="button" class="link" @click=${() => this.#discard(module)}
          >Discard</button>` : nothing}
        <button type="button" class=${open ? "btn acc" : "btn"} ?disabled=${!open} @click=${() => void this.#save(module)}
          >Save</button></span>
    </div>`;
  }

  /** The ask when the shell would move off the page with changes held. */
  #leaveLine(shown: string) {
    const pending = this.#held.pending;
    const labels = (module: string): string[] => {
      const keys = this.#held.sections.get(module)?.keys ?? [];
      return pending.changes(module, keys).map((change) => changeLabel(keys, change.name));
    };
    const ask = leaveAsk(pending, shown, labels);
    if (ask === null) return nothing;
    const [question, ...lines] = ask.lines;
    const one = ask.buttons.includes("Save and leave");
    const [lead, ...tail] = one ? question.split("? ") : [question.replace(/\?$/, ""), ""];
    return html`<div class="save conf" data-part="save" data-state="leave">
      <p class="msg" data-line="save"><b>${lead}?</b>${one ? ` ${tail.join("? ")}` : nothing}
        ${lines.map((line) => html`<span data-line="leave">${line}</span>`)}</p>
      <span class="btns">${one
        ? html`<button type="button" class="link" @click=${() => this.#stay()}>Stay</button>
          <button type="button" class="link" @click=${() => this.#leaveWithout()}>Leave without saving</button>
          <button type="button" class="btn acc" @click=${() => void this.#saveAndLeave(shown)}>Save and leave</button>`
        : html`<button type="button" class="link" @click=${() => this.#leaveWithout()}>Leave without saving</button>
          <button type="button" class="btn stay" @click=${() => this.#stay()}>Stay</button>`}</span>
    </div>`;
  }

  #foot(section: Section, after = false) {
    const page = this.#pageOf(section.module);
    return html`<div class=${after ? "foot after" : "foot"} data-part="foot">
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

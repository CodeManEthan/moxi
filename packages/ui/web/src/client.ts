// /m/ui/client.js: the component contract's one shared script.
//
// Every component imports it by that absolute URL, which every build leaves external, so
// one copy runs in the shell's one document and every module's components share it. It
// holds no Lit. Within contract 1 its exports only grow: a new export, a new theme token
// or a new optional declaration field. Removing or renaming one is contract 2.
//
// A component reaches data only through `api` and `subscribe`, for its own module, and
// links to another module only through `navigate` or `place`, and only while
// `installed` says that module is there. It runs a confirmed act only through `confirm`.
//
// It also defines `<moxi-ui-form>`, the one form renderer, and `<moxi-ui-confirm>`, the
// confirm sheet, so every page can use them.

import { defineConfirm } from "./confirm.ts";
import { isConfirmed, mintKey, outcomeOf, SIGNED_OUT } from "./confirm-model.ts";
import { defineForm } from "./form.ts";
import { fetchOpener, TabPush } from "./push.ts";
import { apiTarget, checkAttributes, checkName, panelTarget, scriptUrl } from "./rules.ts";
import { boot } from "./shell.ts";

/** The component contract version this script speaks. */
export const CONTRACT: number = 1;

/** One attribute a component declares; a host passes it as an HTML attribute. */
export interface AttributeInfo {
  name: string;
  type: "string" | "int" | "bool" | "enum";
  /** An enum's words; empty for the other types. */
  values: string[];
  required: boolean;
  doc: string;
}

/** One component a module declares, as the shell checked it. */
export interface ComponentInfo {
  name: string;
  /** The custom element's name, `moxi-<module>-<name>`. */
  element: string;
  /** The asset that defines the element. */
  script: string;
  /** The script's URL, `/m/<module>/<script>`. */
  url: string;
  title: string;
  doc: string;
  contract: number;
  place: "page" | "block" | "inline";
  /** Whether the shell's home view may place it. */
  home: boolean;
  attributes: AttributeInfo[];
}

/** An answer from `api`. `status` is 0 when no answer came (a network failure). `data`
 * is the parsed JSON body, or null when the body is empty or not JSON. */
export interface ApiResult {
  status: number;
  data: any;
}

/**
 * What `confirm` resolves with. `accepted` carries the status path the module gave,
 * relative to its own page (`/api/status?job=...`); `refused` the answer's status and a
 * line of text; `unknown` a line of text. `cancelled` means the sheet was closed and
 * nothing was sent.
 */
export type ConfirmOutcome =
  | { outcome: "accepted"; status: string }
  | { outcome: "refused"; code: number; text: string }
  | { outcome: "unknown"; text: string }
  | { outcome: "cancelled" };

/** The theme modes: `system` follows the browser's colour scheme. */
export type ThemeMode = "system" | "light" | "dark";

/** One entry of a switch table, as `moxi config show --json` lists it. */
export interface FieldEntry {
  /** The entry's own name. */
  name: string;
  /** What a write names: `<table key>.<entry>`. */
  key: string;
  value: boolean;
  /** `default` while no file names the entry, else `file` or `env`. */
  source: string;
  /** The units and wraps that name the entry as their switch. */
  named_by?: string[];
  /** When a change applies. */
  mark?: string;
}

/**
 * One field of `<moxi-ui-form>`, in `moxi.config.Key`'s shape: the settings view gives it
 * with its value, source and mark; a launcher's catalog row adds its own fields. Every
 * field past `name` and `type` is optional. Values are spelled as the command line
 * spells them: `true`/`false`, a number, the plain string, or one TOML value for a table.
 */
export interface FieldDescriptor {
  name: string;
  /** `bool`, `int`, `float`, `string`, `path`, `enum`, `list` or `table`. */
  type: string;
  default?: string | null;
  /** Help text. */
  doc?: string;
  /** An enum's words. */
  values?: string[] | null;
  read?: string;
  env?: string | null;
  needs?: string | null;
  /** A list's item type. */
  items?: string | null;
  /** The current value. A list may give its items here, and a switch with an optional
   * value gives `true` for on with no value. */
  value?: string | string[] | boolean | null;
  /** A list's current items. */
  value_items?: string[] | null;
  /** `default`, `file` or `env`. */
  source?: string;
  /** When a change applies, as a line to show. */
  mark?: string;
  /** A line to show under the field. */
  note?: string | null;
  /** The field's `needs` module is absent: it is shown off, with `note` saying why. */
  needs_absent?: boolean;
  /** Drawn as one toggle per entry of `entries`. */
  switch_table?: boolean;
  entries?: FieldEntry[] | null;
  /** `"unavailable"` when the entries named only by the module's units could not be
   * listed; `reason` then says why. */
  declarations?: string | null;
  reason?: string | null;
  /** A catalog row: may be given more than once (a list control). */
  repeatable?: boolean;
  /** A catalog row: a switch plus an optional value. */
  optional_value?: boolean;
  /** A catalog row: shown under "more". */
  hidden?: boolean;
  /** A catalog row: shown off unless this module is installed. */
  requires?: string | null;
  /** A catalog row the launcher owns: not offered. */
  owned?: boolean;
  /** A catalog row of the permission posture: marked so. */
  posture?: boolean;
  flag?: string | null;
  since?: string | null;
}

/**
 * The detail of `moxi-change`, which `<moxi-ui-form mode="each">` sends for each change:
 * exactly one of `value` (a new value, or a switch-table entry's `true`/`false` under its
 * `<table key>.<entry>` name), `add` or `remove` (a list item), or `reset` (back to the
 * default).
 */
export interface FormChange {
  name: string;
  value?: string;
  add?: string;
  remove?: string;
  reset?: true;
}

/** A value of `moxi-submit`: a field's text (null when empty or a switch is off), or a
 * list's items. */
export type FormValue = string | string[] | null;

/** The detail of `moxi-submit`, which `<moxi-ui-form mode="submit">` sends once with
 * every field that is not off. A switch table gives each entry under its
 * `<table key>.<entry>` name. */
export interface FormSubmit {
  values: Record<string, FormValue>;
}

/**
 * `<moxi-ui-form>`, the one form renderer, defined by this script. Set `fields`; choose
 * `mode` (`each`, the default, or `submit`) by attribute or property. The host makes each
 * write and shows the server's answer with `setError` or `setMessage`; a message stays
 * until the user next changes that field.
 */
export interface FormElement extends HTMLElement {
  fields: FieldDescriptor[];
  mode: "each" | "submit";
  /** Every control off, as for a settings file that won't parse. */
  disabled: boolean;
  /** The submit button's text in `submit` mode. */
  submitLabel: string;
  /** Shows `text` as the error of field (or switch-table entry) `name`. */
  setError(name: string, text: string): void;
  /** Shows `lines` under field (or entry) `name`; a warning or error is marked so. */
  setMessage(name: string, lines: string[], kind?: "info" | "warn" | "error"): void;
  /** The values `moxi-submit` would send now. */
  values(): Record<string, FormValue>;
}

declare global {
  interface HTMLElementTagNameMap {
    "moxi-ui-form": FormElement;
  }
}

/** The `localStorage` item that holds the session key the sign-in page stored. */
const KEY_STORAGE = "moxi-ui-key";
const DEFINE_MS = 5_000;
const MODES: readonly string[] = ["system", "light", "dark"];

interface ModuleRow {
  name: string;
  state: string;
  page: boolean;
  /** The paths of its confirmed acts, from its page's export. */
  confirmed?: string[];
  components: ComponentInfo[];
  refused: Array<{ name: string; reason: string }>;
}

interface ModulesValue {
  contracts: number[];
  modules: ModuleRow[];
}

let view: ModulesValue = { contracts: [CONTRACT], modules: [] };
let signedOut = false;
const watchers = new Set<() => void>();
const sessionWatchers = new Set<(signedIn: boolean) => void>();

function key(): string | null {
  try {
    return localStorage.getItem(KEY_STORAGE);
  } catch {
    return null;
  }
}

function setSignedOut(out: boolean): void {
  if (signedOut === out) return;
  signedOut = out;
  for (const fn of [...sessionWatchers]) fn(!out);
}

function markSignedOut(): void {
  setSignedOut(true);
}

function row(module: string): ModuleRow | undefined {
  return view.modules.find((item) => item.name === module);
}

function take(value: unknown): void {
  if (typeof value !== "object" || value === null) return;
  const { contracts, modules } = value as Partial<ModulesValue>;
  if (!Array.isArray(modules)) return;
  view = {
    contracts: Array.isArray(contracts) && contracts.length ? contracts : [CONTRACT],
    modules: modules.filter((item) => typeof item?.name === "string"),
  };
  for (const fn of [...watchers]) fn();
}

const push = new TabPush(fetchOpener(key), markSignedOut);

// --- the contract -----------------------------------------------------------------------

/** Whether `module` is installed and present. */
export function installed(module: string): boolean {
  return row(module)?.state === "present";
}

/** The components `module` declares that the shell can place; empty when it is not
 * installed. */
export function components(module: string): ComponentInfo[] {
  if (!installed(module)) return [];
  return (row(module)?.components ?? []).map((item) => ({
    ...item,
    attributes: item.attributes.map((attr) => ({ ...attr, values: [...attr.values] })),
  }));
}

function confirmedAct(module: string, target: string): boolean {
  return isConfirmed(row(module)?.confirmed ?? [], module, target);
}

/**
 * A request to `module`'s page, `path` relative to `/m/<module>/` (`"api/items?x=1"`).
 * With `body` it is a POST of that object as JSON; without, a GET. It sends the session
 * key, never retries, and never throws for an answer: a refusal is its status, and a
 * network failure is status 0. It throws a TypeError for a path that leaves
 * `/m/<module>/`, a body that is not an object, or a confirmed act's path, which only
 * `confirm` sends, after the user confirms it.
 */
export async function api(module: string, path: string, body?: object): Promise<ApiResult> {
  const target = apiTarget(module, path, location.origin);
  if (body !== undefined && (typeof body !== "object" || body === null)) {
    throw new TypeError("api body is an object, sent as JSON");
  }
  if (confirmedAct(module, target)) {
    throw new TypeError(`api path ${JSON.stringify(path)} is a confirmed act of ${module}: send it with confirm`);
  }
  const value = key();
  if (!value) {
    markSignedOut();
    return { status: 403, data: null };
  }
  const headers: Record<string, string> = { "X-Moxi-Key": value, Accept: "application/json" };
  const init: RequestInit = { headers, credentials: "same-origin", cache: "no-store" };
  if (body !== undefined) {
    init.method = "POST";
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  return await fetched(target, init);
}

async function fetched(target: string, init: RequestInit): Promise<ApiResult> {
  let answer: Response;
  let text: string;
  try {
    answer = await fetch(target, init);
    text = await answer.text();
  } catch {
    return { status: 0, data: null };
  }
  // The shell's refusal of the cookie or the key is an empty 403.
  if (answer.status === 403 && text === "") markSignedOut();
  let data: any = null;
  if (text !== "" && (answer.headers.get("Content-Type") ?? "").includes("json")) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  return { status: answer.status, data };
}

/**
 * Runs `module`'s confirmed act at `path` (relative to `/m/<module>/`) with `body`, once
 * the user confirms it. It shows `<moxi-ui-confirm>`, a sheet with the module's title,
 * the component's `sheet` lines and the body. On Confirm it mints a key from 32 random
 * bytes, posts the body once with it as `Idempotency-Key`, and resolves with the
 * outcome: `{outcome: "accepted", status}` with the module's status path (relative to
 * its page), `{outcome: "refused", code, text}` when the act was not done, or
 * `{outcome: "unknown", text}` when the module may have acted. Cancel resolves
 * `{outcome: "cancelled"}` and sends nothing. It never retries and never throws for an
 * answer. It throws a TypeError for a path that leaves `/m/<module>/` or is not one of
 * the module's confirmed acts, a body that is not an object, or lines that are not
 * strings.
 */
export async function confirm(module: string, path: string, body: object,
                              sheet: readonly string[] = []): Promise<ConfirmOutcome> {
  const target = apiTarget(module, path, location.origin);
  if (typeof body !== "object" || body === null) throw new TypeError("confirm's body is an object, sent as JSON");
  if (!Array.isArray(sheet) || !sheet.every((line) => typeof line === "string")) {
    throw new TypeError("confirm's sheet is a list of lines");
  }
  if (!confirmedAct(module, target)) {
    throw new TypeError(`confirm path ${JSON.stringify(path)} is not a confirmed act of ${module}`);
  }
  // The bytes are fixed now: what the sheet shows is what is sent, once.
  const data = JSON.stringify(body);
  const title = components(module).find((item) => item.name === "page")?.title ?? module;
  const element = document.createElement("moxi-ui-confirm");
  document.body.append(element);
  return await element.ask({ title, lines: [...sheet], body }, async () => {
    const value = key();
    if (!value) {
      markSignedOut();
      return { outcome: "refused", code: 403, text: SIGNED_OUT };
    }
    const headers: Record<string, string> = {
      "X-Moxi-Key": value, Accept: "application/json", "Content-Type": "application/json",
      "Idempotency-Key": mintKey(),
    };
    const answer = await fetched(target, { method: "POST", headers, body: data,
                                           credentials: "same-origin", cache: "no-store" });
    return outcomeOf(answer.status, answer.data);
  });
}

/**
 * Calls `fn` with each value of `module`'s topic `topic`: first its current value, then
 * each value that replaces it. Returns the function that ends the subscription; call it
 * in `disconnectedCallback`.
 */
export function subscribe(module: string, topic: string, fn: (data: any) => void): () => void {
  checkName("module", module);
  checkName("topic", topic);
  if (typeof fn !== "function") throw new TypeError("subscribe takes a function");
  return push.hub.add(`${module}.${topic}`, fn);
}

/**
 * Places `module`'s component `name` at the end of `host`, with `attrs` as its HTML
 * attributes, once its script has defined it. Gives the element, or null when the
 * module is not installed, does not declare the component, asks for a contract this
 * shell does not place, or its script fails to load or to define the element. Throws a
 * TypeError for an attribute the component does not declare, a value not of its type,
 * or a required one left out. A page also takes `route`, its part of the URL after
 * `/m/<module>/`, which the shell gives it.
 */
export async function place(host: Element, module: string, name: string,
                            attrs: Record<string, string> = {}): Promise<Element | null> {
  checkName("module", module);
  checkName("component", name);
  const info = components(module).find((item) => item.name === name);
  if (info === undefined || !view.contracts.includes(info.contract)) return null;
  checkAttributes(info, attrs);
  const url = scriptUrl(info, module, location.origin);
  if (url === null) return null;
  try {
    await import(url);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const late = new Promise<false>((resolve) => { timer = setTimeout(() => resolve(false), DEFINE_MS); });
    const defined = await Promise.race([customElements.whenDefined(info.element).then(() => true), late]);
    clearTimeout(timer);
    if (!defined) return null;
  } catch (err) {
    console.error(`moxi: ${module}'s ${name} could not load`, err);
    return null;
  }
  const element = document.createElement(info.element);
  for (const [attr, value] of Object.entries(attrs)) element.setAttribute(attr, value);
  host.append(element);
  return element;
}

let showPanel: ((module: string, route: string) => void) | null = null;

/** Shows `module`'s panel at `route` (relative to `/m/<module>/`) in the shell's one
 * document, with a new history entry. */
export function navigate(module: string, route = ""): void {
  const target = panelTarget(module, route, location.origin);
  if (showPanel === null) {
    location.assign(target.url);
    return;
  }
  history.pushState(null, "", target.url);
  showPanel(module, target.route);
}

/** Sets the shell document's theme mode until it is next loaded, when the shell sets it
 * from the `ui` setting `theme.mode`. For the settings view, after it writes that
 * setting. */
export function setTheme(mode: ThemeMode): void {
  if (!MODES.includes(mode)) throw new TypeError(`theme mode ${JSON.stringify(mode)} is not system, light or dark`);
  document.documentElement.dataset.theme = mode;
}

// --- start ------------------------------------------------------------------------------

defineForm(installed);
defineConfirm();
push.start();
addEventListener("storage", (event) => {
  // A sign-in in another tab of this browser stores a new key.
  if (event.key === KEY_STORAGE && event.newValue) {
    setSignedOut(false);
    push.reset();
  }
});
// Read the modules once before any component runs, so `installed` and `components`
// answer from the first call; the topic keeps them current.
{
  const first = await api("ui", "api/modules");
  if (first.status === 200) take(first.data);
  push.hub.add("ui.modules", take);
}

if (document.getElementById("moxi-shell") !== null) {
  boot({
    installed,
    place,
    modules: () => view.modules.filter((item) => item.state === "present").map((item) => ({
      name: item.name,
      page: components(item.name).find((comp) => comp.name === "page") ?? null,
      refused: item.refused.find((comp) => comp.name === "page")?.reason ?? null,
    })),
    known: (module) => row(module) !== undefined,
    onModules: (fn) => watchers.add(fn),
    onSession: (fn) => {
      sessionWatchers.add(fn);
      if (signedOut) fn(false);
    },
    onState: (fn) => push.hub.onState(fn),
    state: (module) => push.hub.state(module),
    setNavigator: (fn) => { showPanel = fn; },
  });
}

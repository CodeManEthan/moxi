// The types of the UI module's shared script, /m/ui/client.js, which every page imports
// by that absolute URL and every build leaves external. tools/web/tsconfig.base.json maps
// the URL to this file. Written by `npm run build` from packages/ui/web/src/client.ts;
// edit that file, not this one.

/** The component contract version this script speaks. */
export declare const CONTRACT: number;
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
    /** Whether the shell's own views may place it. */
    home: boolean;
    /** Where Home places a `home` block: its one main spot or a row under it. Null for a
     * block of the modules view and for a component that is no home block. */
    home_as: "main" | "row" | null;
    /** A page's group in the band of pages; null on a component that is no page. */
    group: "daily" | "setup" | null;
    /** From 0 to 999, lower first: a page's place in its group, a main piece's rank or a
     * row's place among rows. Null on any other component. */
    order: number | null;
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
 * nothing was sent. `desk` means the phone can't run the act here, so the sheet pointed at
 * the desk and nothing reached the module; `text` is what it said.
 */
export type ConfirmOutcome = {
    outcome: "accepted";
    status: string;
} | {
    outcome: "refused";
    code: number;
    text: string;
} | {
    outcome: "unknown";
    text: string;
} | {
    outcome: "cancelled";
} | {
    outcome: "desk";
    text: string;
};
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
/** Whether this page came through the gateway, on the phone. A component hides there what
 * the phone can't reach, such as a desk-only act. */
export declare function gateway(): boolean;
/** Whether the phone runs confirmed acts here: true on the gateway only while the shell
 * mounts them, as it stood when the page loaded. Where it is false a component may label
 * an act "at the desk"; `confirm` then shows where to do it. */
export declare function gatewayActs(): boolean;
/** Whether `module` is installed and present. */
export declare function installed(module: string): boolean;
/** The components `module` declares that the shell can place; empty when it is not
 * installed. */
export declare function components(module: string): ComponentInfo[];
/**
 * A request to `module`'s page, `path` relative to `/m/<module>/` (`"api/items?x=1"`).
 * With `body` it is a POST of that object as JSON; without, a GET. It sends the session
 * key, never retries, and never throws for an answer: a refusal is its status, and a
 * network failure is status 0. It throws a TypeError for a path that leaves
 * `/m/<module>/`, a body that is not an object, or a confirmed act's path, which only
 * `confirm` sends, after the user confirms it.
 */
export declare function api(module: string, path: string, body?: object): Promise<ApiResult>;
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
 *
 * On the phone, where `gatewayActs` is true, the gateway first stores the act and the
 * sheet shows the body it stored; Confirm has the gateway send it, once. Where
 * `gatewayActs` is false, or the gateway answers that acts are no longer mounted, the
 * sheet has no Confirm: it says to do the act at the desk and why, sends nothing further,
 * and its Close resolves `{outcome: "desk", text}`.
 */
export declare function confirm(module: string, path: string, body: object, sheet?: readonly string[]): Promise<ConfirmOutcome>;
/**
 * Calls `fn` with each value of `module`'s topic `topic`: first its current value, then
 * each value that replaces it. Returns the function that ends the subscription; call it
 * in `disconnectedCallback`.
 */
export declare function subscribe(module: string, topic: string, fn: (data: any) => void): () => void;
/**
 * Places `module`'s component `name` at the end of `host`, with `attrs` as its HTML
 * attributes, once its script has defined it. Gives the element, or null when the
 * module is not installed, does not declare the component, asks for a contract this
 * shell does not place, or its script fails to load or to define the element. Throws a
 * TypeError for an attribute the component does not declare, a value not of its type,
 * or a required one left out. A page also takes `route`, its part of the URL after
 * `/m/<module>/`, which the shell gives it.
 */
export declare function place(host: Element, module: string, name: string, attrs?: Record<string, string>): Promise<Element | null>;
/** Shows `module`'s panel at `route` (relative to `/m/<module>/`) in the shell's one
 * document, with a new history entry. */
export declare function navigate(module: string, route?: string): void;
/** Sets the shell document's theme mode and, when `name` is given, its theme, until the
 * document is next loaded, when the shell sets both from the `ui` settings `theme.mode`
 * and `theme.palette`. A name theme.css has no block for shows the default theme. For the
 * settings view, after it writes either setting. */
export declare function setTheme(mode: ThemeMode, name?: string): void;

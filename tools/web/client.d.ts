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
    /** The modules whose presence takes a page's entry out of the band; its address still
     * opens it. Null on a component that is no page. */
    band_unless: string[] | null;
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
    /** The shell refuses a write to the entry for this run: it is shown off. */
    locked?: boolean;
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
    /** The shell refuses a write to the key for this run: it is shown off, still readable. */
    locked?: boolean;
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
/**
 * `<moxi-ui-row>`, the two-line row of a list, defined by this script. It draws what its
 * attributes carry and nothing for one that is unset:
 *
 * - line one: `name`; `where`, dim, after it; `fact` at the right end, dim, or in the text
 *   colour at weight 600 with `fact-heavy`. When the line is too narrow the name and
 *   where are cut before the fact is.
 * - line two, a middle dot between each two phrases: `lead` (dim, or weight 600 with
 *   `lead-heavy`); `lead-more`; `time` and `rest` as one phrase, with `You:` before the
 *   rest when `said-by` is `you`, the rest dim or in the text colour with `rest-strong`;
 *   `tail`. The slot `acts` is the line's right end.
 * - line three: `note`.
 * - `open`: the default slot's content shows under the lines.
 * - `toggles`: the lines show a pointer, and a click on them outside `acts` sends
 *   `moxi-row-toggle`, which bubbles and carries no detail. Line one is the tab stop, a
 *   button whose `aria-expanded` follows `open`; Enter or Space on it sends the event
 *   too. The row does not open itself: the page sets `open`.
 */
export interface RowElement extends HTMLElement {
}
/**
 * `<moxi-ui-rail>`, a line of step markers with the line through what is done, defined by
 * this script. Each `<moxi-ui-mark>` child is one cell, in order. It draws what its marks
 * carry and reads nothing:
 *
 * - a mark's `state` is one of `done`, `none`, `yours`, `failed`, `owed`, `waiting`,
 *   `todo` and `child`, each its own shape; unset, the cell draws no marker and the line
 *   passes through. `word` is written under the marker, `count` at its corner, and
 *   `label` is its accessible name, used as given (else the state in words). `key` is
 *   opaque and comes back in the event.
 * - a mark's flags: `settled`, `here` (`▼` over the marker), `open` (a ring and
 *   `aria-expanded`, while the page shows what the marker opened), `quiet` (drawn, not a
 *   button) and `wide` (the label's width, showing the mark's own children over the line).
 * - the line is solid, in `--moxi-state-done`, from the first marker through each settled
 *   one up to and including the first not settled, and dashed after it.
 * - cells are `--moxi-rail-cell` (56px) wide, a wide one `--moxi-rail-label` (132px).
 * - a click, Enter or Space on a marker sends `moxi-rail-mark` from the rail, bubbling and
 *   crossing shadow roots, with `{key}`. The rail opens nothing itself.
 * - `size="small"`: 11 px markers, no words or counts, no buttons. `vertical`: one mark a
 *   row, its label and word beside it, the line running down.
 */
export interface RailElement extends HTMLElement {
}
/** `<moxi-ui-mark>`, one cell of `<moxi-ui-rail>`; its attributes are listed there. Its
 * `focus()` focuses its marker. */
export interface MarkElement extends HTMLElement {
}
/** The detail of `moxi-rail-mark`: the pressed mark's `key`, null when it has none. */
export interface RailMark {
    key: string | null;
}
declare global {
    interface HTMLElementTagNameMap {
        "moxi-ui-form": FormElement;
        "moxi-ui-row": RowElement;
        "moxi-ui-rail": RailElement;
        "moxi-ui-mark": MarkElement;
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
 * Runs `read` after `ms` milliseconds: a read the page has decided on but waits to send,
 * such as a list read again a while after a topic value, or a field's value read once
 * typing stops. The page root's mark counts it from now until a turn after what `read`
 * gives (its promise) has settled, so a press waits through the wait as well as the read.
 * Gives the function that cancels it: before `read` has run, `read` never runs and the
 * mark lets it go; after, it does nothing. Call it where the page would `clearTimeout`,
 * and in `disconnectedCallback`.
 */
export declare function readLater(read: () => unknown, ms: number): () => void;
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
 * document, with a new history entry. While a page holds (`holdLeave`), it asks that page
 * first and moves only when the page says go. */
export declare function navigate(module: string, route?: string): void;
/**
 * Holds the page shown in place while it has changes not saved. Every move off it (a link
 * in the shell, `navigate`, Back and Forward) then calls `ask(go)` and does nothing else;
 * the page asks in its own words and calls `go()` to leave, which releases the hold and
 * makes the move. The shell's own redraw on a modules or session event waits for the
 * release, and a load of another document gets the browser's own prompt. One page holds
 * at a time. Gives the function that releases the hold: call it when no change is left
 * and in `disconnectedCallback`.
 */
export declare function holdLeave(ask: (go: () => void) => void): () => void;
/**
 * Writes the page's own address, `route` relative to `/m/<module>/`, in place of the
 * current history entry, keeping its state, so Back leaves the page. The shell takes the
 * page as still the one placed: it neither draws it again nor asks it. Throws a TypeError
 * for a module whose panel is not the one shown, or a route that leaves `/m/<module>/`.
 */
export declare function setAddress(module: string, route?: string): void;
/** Sets the shell document's theme mode and, when `name` is given, its theme, until the
 * document is next loaded, when the shell sets both from the `ui` settings `theme.mode`
 * and `theme.palette`. A name theme.css has no block for shows the default theme. For the
 * settings view, after it writes either setting. */
export declare function setTheme(mode: ThemeMode, name?: string): void;
/**
 * How long a page has waited since it was connected, for a line it shows only after a
 * while (`Reading…` once its first read has been out 400 ms). `start(onDue)`, from
 * `connectedCallback`, notes the time and arms a timer that calls `onDue` after `dueMs`;
 * `waitedMs()`, from `render`, is the time since on the monotonic clock
 * (`performance.now()`), and never less than `dueMs` once that timer has fired, so the
 * render the timer asks for always finds the wait due; `stop()`, from
 * `disconnectedCallback`, clears the timer.
 */
export declare class WaitClock {
    #private;
    readonly dueMs: number;
    constructor(dueMs: number);
    start(onDue: () => void): void;
    stop(): void;
    waitedMs(): number;
}

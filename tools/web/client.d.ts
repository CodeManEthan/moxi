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
/** The theme modes: `system` follows the browser's colour scheme. */
export type ThemeMode = "system" | "light" | "dark";
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
 * `/m/<module>/` or a body that is not an object.
 */
export declare function api(module: string, path: string, body?: object): Promise<ApiResult>;
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
/** Sets the shell document's theme mode until it is next loaded, when the shell sets it
 * from the `ui` setting `theme.mode`. For the settings view, after it writes that
 * setting. */
export declare function setTheme(mode: ThemeMode): void;

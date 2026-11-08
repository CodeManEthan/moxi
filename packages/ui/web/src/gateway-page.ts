// What the gateway's pairing and sign-in pages share: their look, a few DOM helpers, and
// the sign-in's last step. Those pages load before the phone holds a session, so they can
// read no asset that needs one, theme.css included, and the policy allows no inline style:
// each page adopts this stylesheet from its script. Its colours are read from tokens.json
// when the page is built, and follow the browser's scheme: the light set on a light
// system, the dark set on a dark one.

import tokens from "../tokens.json" with { type: "json" };
import { HOME, KEY_STORAGE } from "./passkey.ts";

type Colour = "bg" | "bg-raised" | "border" | "text" | "text-dim" | "accent" | "error";

const SETS: Record<string, { mode: string }> = tokens.$sets;

/** The name of tokens.json's set shown in `mode`. */
function set(mode: "light" | "dark"): string {
  const name = Object.keys(SETS).find((key) => SETS[key].mode === mode);
  if (name === undefined) throw new Error(`tokens.json has no ${mode} set`);
  return name;
}

const LIGHT = set("light");
const DARK = set("dark");

/** A theme colour as `light-dark()` of its light set's and dark set's values. */
function colour(name: Colour): string {
  const values: Record<string, string> = tokens[name].$extensions.moxi.sets;
  return `light-dark(${values[LIGHT]}, ${values[DARK]})`;
}

export const CSS = `
:root { color-scheme: light dark; }
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0; padding: 16px; background: ${colour("bg")}; color: ${colour("text")};
  font: 16px/1.45 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 28rem; margin: 0 auto; display: grid; gap: 16px; }
h1 { margin: 0; font-size: 1.4rem; }
p { margin: 0; overflow-wrap: anywhere; }
.dim { color: ${colour("text-dim")}; font-size: 0.9rem; }
.problem { color: ${colour("error")}; }
label { display: grid; gap: 4px; }
input {
  font: inherit; width: 100%; padding: 10px 12px; border: 1px solid ${colour("border")};
  border-radius: 6px; background: ${colour("bg-raised")}; color: inherit;
}
.buttons { display: grid; gap: 12px; }
button {
  font: inherit; min-height: 48px; padding: 10px 16px; border: 1px solid ${colour("accent")};
  border-radius: 10px; background: ${colour("bg")}; color: ${colour("accent")}; cursor: pointer;
}
button.go { background: ${colour("accent")}; color: ${colour("bg")}; }
button:disabled { opacity: 0.6; cursor: not-allowed; }
:focus-visible { outline: 2px solid ${colour("accent")}; outline-offset: 2px; }
[hidden] { display: none !important; }
`;

/** Adopts the pages' stylesheet. */
export function style(): void {
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(CSS);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
}

/** A new element with `text` and `cls`. */
export function el<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, cls?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls !== undefined) node.className = cls;
  return node;
}

/** A POST of `body` as JSON to an open route: `{status, data}`, status 0 when no answer
 * came. It never throws. */
export async function post(path: string, body: object): Promise<{ status: number; data: any }> {
  try {
    const answer = await fetch(path, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    const text = await answer.text();
    let data: any = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = null;
    }
    return { status: answer.status, data };
  } catch {
    return { status: 0, data: null };
  }
}

/** The last step of a sign-in: keep the key the answer gave, then open Moxi. False when
 * the answer holds no key or the browser keeps nothing. */
export function signedIn(data: any): boolean {
  if (typeof data?.key !== "string" || data.key === "") return false;
  try {
    localStorage.setItem(KEY_STORAGE, data.key);
  } catch {
    return false;
  }
  location.replace(HOME);
  return true;
}

/** The page's status line: `text`, marked a problem or not. */
export function say(line: HTMLElement, text: string, problem = false): void {
  line.textContent = text;
  line.classList.toggle("problem", problem);
}

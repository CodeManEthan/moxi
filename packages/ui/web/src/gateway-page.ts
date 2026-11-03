// What the gateway's pairing and sign-in pages share: their look, a few DOM helpers, and
// the sign-in's last step. Those pages load before the phone holds a session, so they can
// read no asset that needs one, theme.css included, and the policy allows no inline style:
// each page adopts this stylesheet from its script. Its colours are tokens.json's.

import { HOME, KEY_STORAGE } from "./passkey.ts";

/** The theme values these pages use, as `light-dark()` pairs from tokens.json. */
export const COLOURS = {
  bg: ["#ffffff", "#14171c"],
  "bg-raised": ["#ffffff", "#1a1e25"],
  border: ["#d9dde3", "#2b313b"],
  text: ["#14181f", "#e6e9ee"],
  "text-dim": ["#5b6472", "#a0a9b6"],
  accent: ["#2f6fd0", "#6ea8ff"],
  error: ["#c0392b", "#ff8a7a"],
} as const;

function colour(name: keyof typeof COLOURS): string {
  const [light, dark] = COLOURS[name];
  return `light-dark(${light}, ${dark})`;
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

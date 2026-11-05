// One more try at placing a component whose script did not load, used by the shell for a
// module's panel and by the modules view for each home block. A browser that replaces the
// page's worker during a load can drop the script's fetch while nothing is wrong with the
// script. A failed module fetch is not kept, for the script or for one it imports, so the
// next import fetches it again at the same URL.

import { failurePanel } from "./views.ts";

/** A failure a fetch of the script named: the error panel's title and lines. */
export type Named = { title: string; lines: string[] };

/** What a load came to: the element placed, or the failure `check` named (null when the
 * script answered fine and the second try gave no element either); null when another load
 * began meanwhile. */
export type Placing<F> = { placed: Element } | { failure: F | null } | null;

/**
 * Places with `place`, and when that gives no element, asks `check` why. A failure `check`
 * names is the answer, with no second try. When it finds the script fine, `place` gets one
 * more try, and no third. `current` says whether this load is still the one shown; when it
 * isn't, anything placed is removed and the answer is null.
 */
export async function placeOnceMore<F>(place: () => Promise<Element | null>,
                                       check: () => Promise<F | null>,
                                       current: () => boolean): Promise<Placing<F>> {
  // undefined: this load is no longer the one shown.
  const attempt = async (): Promise<Element | null | undefined> => {
    let placed: Element | null = null;
    try {
      placed = await place();
    } catch (err) {
      console.error(err);
    }
    if (!current()) {
      placed?.remove();
      return undefined;
    }
    return placed;
  };
  const first = await attempt();
  if (first === undefined) return null;
  if (first !== null) return { placed: first };
  const found = await check();
  if (!current()) return null;
  if (found !== null) return { failure: found };
  const second = await attempt();
  if (second === undefined) return null;
  if (second !== null) return { placed: second };
  return { failure: null };
}

/** The failure a fetch of `module`'s script at `url` names (an asset needs the cookie
 * only), or null when the answer names none. */
export async function scriptFailure(module: string, url: string): Promise<Named | null> {
  let answer: Response;
  try {
    answer = await fetch(url, { credentials: "same-origin", cache: "no-store" });
  } catch {
    return failurePanel(module, 0, null);
  }
  if (answer.ok) return null;
  let data: unknown = null;
  try {
    data = await answer.json();
  } catch {
    data = null;
  }
  return failurePanel(module, answer.status, data);
}

// The Settings page's Find, its changed view and its addresses, with no DOM.
//
// Find filters every drawn key of every listed module, `More` keys included, by its label
// (or key), its dotted key, its line (or doc) and its value in words; the changed view
// lists every drawn key whose value is not the default. Both group the rows by module in
// the list's order and count against each module's drawn keys, the same set the pane and
// the list's counts use. A module whose section has not arrived is searched once it does;
// until every one has, the words say how many are still being read.

import {
  CORE, PHONE_MODULE, changedCount, countOf, drawnKeys, labelOf, linesOf, moduleOrder, readKeys, valueOf, valueWords,
} from "./settings-model.ts";
import type { Section, Setting } from "./settings-model.ts";

/** How many rows a module shows in Find's results before `Show them`. */
export const FIND_ROWS = 4;
/** The words at the right of Find's count: what it reads. */
export const FIND_READS = "names, keys, descriptions and values";
export const FIND_HELP = "Search reads every installed module's settings. Pick a module at the left to see all of its settings.";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** How a key matched, best first: its label (or key, without one), its dotted key, then
 * its sentence or value. */
export const RANK_LABEL = 0;
export const RANK_KEY = 1;
export const RANK_WORDS = 2;
type Rank = typeof RANK_LABEL | typeof RANK_KEY | typeof RANK_WORDS;

/** One module's group of rows in Find or the changed view. */
export interface Group {
  module: string;
  /** `<matches> of <count>` (Find), `<changed> of <count>` (the changed view). */
  tally: string;
  /** The rows to draw, in order. */
  keys: Setting[];
  /** `<k> more in <module> · Show them`, or null when every row shows. */
  more: string | null;
}

/** Find's answer for one text. */
export interface Found {
  /** `<n> settings in <m> modules`. */
  count: string;
  groups: Group[];
  /** The `elsewhere` sentences of the keys it matched that another page edits. */
  elsewhere: string[];
  /** `No setting matches “<text>”`, once every section is in and nothing matched; else null. */
  none: string | null;
  /** `still reading <k> modules` while a listed module's section has not arrived; else null. */
  reading: string | null;
}

function lower(text: unknown): string {
  return typeof text === "string" ? text.toLowerCase() : "";
}

/** How `key` matches `text` (lower case), or null when it doesn't. */
export function rankOf(key: Setting, text: string): Rank | null {
  if (lower(labelOf(key)).includes(text)) return RANK_LABEL;
  if (lower(key.name).includes(text)) return RANK_KEY;
  const words = [...linesOf(key), valueWords(key, valueOf(key))];
  return words.some((line) => lower(line).includes(text)) ? RANK_WORDS : null;
}

/** `text` cut into its parts, each marked when it matches `query`, for the underline. */
export function hits(text: string, query: string): { text: string; hit: boolean }[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") return [{ text, hit: false }];
  const out: { text: string; hit: boolean }[] = [];
  const haystack = text.toLowerCase();
  let at = 0;
  for (let found = haystack.indexOf(needle); found >= 0; found = haystack.indexOf(needle, at)) {
    if (found > at) out.push({ text: text.slice(at, found), hit: false });
    out.push({ text: text.slice(found, found + needle.length), hit: true });
    at = found + needle.length;
  }
  if (at < text.length) out.push({ text: text.slice(at), hit: false });
  return out;
}

function reading(listed: readonly string[], sections: ReadonlyMap<string, Section>): string | null {
  const left = [...new Set(listed)].filter((name) => !sections.has(name)).length;
  return left === 0 ? null : `still reading ${plural(left, "module")}`;
}

/**
 * Find's results for `query` over the listed modules' sections that have arrived. `phone`
 * says whether the Phone group shows (ui's `gateway.*` keys are drawn keys only then);
 * `shown` names the modules whose every match is shown (`Show them` pressed).
 */
export function find(query: string, listed: readonly string[], sections: ReadonlyMap<string, Section>, phone: boolean,
                     shown: ReadonlySet<string> = new Set()): Found {
  const text = query.trim().toLowerCase();
  const groups: Group[] = [];
  const elsewhere: string[] = [];
  let n = 0;
  for (const module of moduleOrder(listed)) {
    const section = sections.get(module);
    if (!section) continue;
    const keys = readKeys(section);
    for (const key of keys) {
      const sentence = typeof key.elsewhere === "string" ? key.elsewhere : "";
      if (sentence !== "" && rankOf(key, text) !== null && !elsewhere.includes(sentence)) elsewhere.push(sentence);
    }
    const matches = drawnKeys(module, keys, phone)
      .map((key, i) => ({ key, i, rank: rankOf(key, text) }))
      .filter((match): match is { key: Setting; i: number; rank: Rank } => match.rank !== null)
      .sort((a, b) => a.rank - b.rank || a.i - b.i)
      .map((match) => match.key);
    if (matches.length === 0) continue;
    n += matches.length;
    const all = shown.has(module) || matches.length <= FIND_ROWS;
    groups.push({
      module,
      tally: `${matches.length} of ${countOf(section, phone)}`,
      keys: all ? matches : matches.slice(0, FIND_ROWS),
      more: all ? null : `${matches.length - FIND_ROWS} more in ${module} · Show them`,
    });
  }
  const still = reading(listed, sections);
  return {
    count: `${plural(n, "setting")} in ${plural(groups.length, "module")}`,
    groups,
    elsewhere,
    none: n === 0 && still === null ? `No setting matches “${query.trim()}”` : null,
    reading: still,
  };
}

/** The changed view: every drawn key whose value is not the default, by module. */
export interface ChangedView {
  /** `<n> settings changed from the default, in <m> modules`. */
  count: string;
  /** `Back to <module>`. */
  back: string;
  groups: Group[];
  reading: string | null;
}

/** The changed view over the sections that have arrived; `back` is the module it returns to. */
export function changedView(listed: readonly string[], sections: ReadonlyMap<string, Section>, phone: boolean,
                            back: string): ChangedView {
  const groups: Group[] = [];
  let n = 0;
  for (const module of moduleOrder(listed)) {
    const section = sections.get(module);
    if (!section) continue;
    const keys = drawnKeys(module, readKeys(section), phone).filter((key) => key.changed === true);
    if (keys.length === 0) continue;
    n += keys.length;
    groups.push({ module, tally: `${changedCount(section, phone)} of ${countOf(section, phone)}`, keys, more: null });
  }
  return {
    count: `${plural(n, "setting")} changed from the default, in ${plural(groups.length, "module")}`,
    back: `Back to ${back}`,
    groups,
    reading: reading(listed, sections),
  };
}

// --- addresses ------------------------------------------------------------------------------------

/** The changed view's address word. */
export const CHANGED = "changed";

/** What an address under `settings` opens. */
export interface Address {
  /** The changed view, or one module's settings. */
  view: "module" | "changed";
  /** The module shown, or the one the changed view returns to. */
  module: string;
  /** `More` shown. */
  more: boolean;
  /** `Pair a phone` opened (ui only). */
  pair: boolean;
  /** `<name> has no settings here.` for an address naming a module not listed; else null. */
  missing: string | null;
}

/**
 * The page's `route` (`settings`, `settings/<module>`, `settings/<module>/more`,
 * `settings/ui/pair`, `settings/changed`) as what it opens, given the modules the read
 * lists. A module named `changed` that is listed wins over the changed view.
 */
export function parseAddress(route: string, listed: readonly string[]): Address {
  const parts = route.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean);
  const base: Address = { view: "module", module: CORE, more: false, pair: false, missing: null };
  if (parts[0] !== "settings" || parts.length === 1) return base;
  const name = parts[1];
  const part = parts[2] ?? null;
  if (!listed.includes(name)) {
    if (name === CHANGED && parts.length === 2) return { ...base, view: "changed" };
    return { ...base, missing: `${name} has no settings here.` };
  }
  return { ...base, module: name, more: part === "more", pair: part === "pair" && name === PHONE_MODULE };
}

/** The address the page writes for what it shows. */
export function addressOf(address: Pick<Address, "view" | "module" | "more" | "pair">): string {
  if (address.view === "changed") return `settings/${CHANGED}`;
  if (address.module === CORE && !address.more) return "settings";
  if (address.pair && address.module === PHONE_MODULE) return `settings/${PHONE_MODULE}/pair`;
  return `settings/${address.module}${address.more ? "/more" : ""}`;
}

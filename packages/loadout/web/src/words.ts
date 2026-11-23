// The words loadout's page writes: kinds, harnesses, states, lists and paths. No DOM, so the
// Node tests read it. The kind words are one table for this page and loadout's
// project-facts component.

import type { ViewRow, ViewSection } from "./feed.ts";

/** Each kind as a sentence names one item of it, and as a folded section names them all. */
export const KIND_WORDS: Readonly<Record<string, { one: string; folded: string }>> = {
  skill: { one: "skill", folded: "Skills" },
  agent: { one: "agent", folded: "Agents" },
  hook: { one: "hook", folded: "Hooks" },
  permissions: { one: "permission rule", folded: "Permission rules" },
  mcp: { one: "MCP server", folded: "MCP servers" },
};

/** The kind as a sentence names one item of it: `skill`, `permission rule`; an unknown
 * kind by its own name. */
export function kindWord(kind: string): string {
  return Object.hasOwn(KIND_WORDS, kind) ? KIND_WORDS[kind].one : kind;
}

/** The kind as a folded section names its items: `Skills`, `Permission rules`. */
export function kindFolded(kind: string): string {
  return Object.hasOwn(KIND_WORDS, kind) ? KIND_WORDS[kind].folded : kind;
}

const HARNESS_WORDS: Record<string, string> = { claude: "Claude Code", codex: "Codex", opencode: "OpenCode" };

/** Each harness as the Cache and Usage pages name one: `Claude Code`, `Codex`,
 * `OpenCode`; any other by its own name. */
export function harnessWord(harness: string): string {
  return Object.hasOwn(HARNESS_WORDS, harness) ? HARNESS_WORDS[harness] : harness;
}

/** `A`, `A and B`, `A, B and C`. */
export function joinAnd(words: readonly string[]): string {
  if (words.length <= 1) return words[0] ?? "";
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** Item names as a refused section's line names them: `A and B`, past three `A, B, C and
 * <n> more`. */
export function namesLine(names: readonly string[]): string {
  if (names.length <= 3) return joinAnd(names);
  return `${names.slice(0, 3).join(", ")} and ${names.length - 3} more`;
}

/** Harnesses joined as a line names them: `Claude Code, Codex and OpenCode`. */
export function harnessesLine(harnesses: readonly string[]): string {
  return joinAnd(harnesses.map(harnessWord));
}

const COUNT_WORDS = ["", "", "both", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];

/** `for Codex` for one harness, `for both` for two, `for all three` and on for more. */
export function forHarnesses(harnesses: readonly string[]): string {
  if (harnesses.length === 1) return `for ${harnessWord(harnesses[0])}`;
  if (harnesses.length === 2) return "for both";
  const count = COUNT_WORDS[harnesses.length] ?? String(harnesses.length);
  return `for all ${count}`;
}

/** `1 item`, `3 items`. */
export function counted(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

function isWindowsHome(home: string): boolean {
  return /^[A-Za-z]:/.test(home) || home.startsWith("\\\\");
}

/**
 * A path as the page writes it: `~` when it is `home`, `~/rest` (`~\rest`) when it
 * continues `home` after a separator, else in full; in full too with no `home`. A Windows
 * home is compared without case, and takes either separator; any other takes `/` and is
 * compared as it is. `/home/you2` is not under `/home/you`.
 */
export function homeForm(path: string, home: string | null): string {
  if (home === null || home === "") return path;
  const windows = isWindowsHome(home);
  const seps = windows ? /[\\/]+$/ : /\/+$/;
  const base = home.replace(seps, "");
  if (base === "") return path;
  const same = (a: string, b: string): boolean => (windows ? a.toLowerCase() === b.toLowerCase() : a === b);
  if (path.length < base.length || !same(path.slice(0, base.length), base)) return path;
  const rest = path.slice(base.length);
  if (rest === "" || (windows ? /^[\\/]+$/ : /^\/+$/).test(rest)) return "~";
  const sep = rest[0];
  if (sep !== "/" && !(windows && sep === "\\")) return path;
  return `~${rest}`;
}

/** The user section's label. */
export const YOUR_ITEMS = "Your items";

/** A section as the page labels it: `Your items`, a key section by its key, a path
 * section by its root in `~` form. */
export function sectionWord(section: Pick<ViewSection, "name" | "where" | "root">, home: string | null): string {
  if (section.where === "user") return YOUR_ITEMS;
  if (section.where === "key") return section.name;
  return homeForm(section.root ?? section.name, home);
}

/** The state words, in the order Placed lists its problems: changed by hand first. */
export const WORD_ORDER = ["changed", "in the way", "not placed", "out of date", "would be committed",
                           "can't be read"] as const;

/** A row's state word: `placed`, `off`, else the word its problem and cell show. A
 * `blocked` row reads `changed` when loadout's own placement was edited where it stands,
 * else `in the way`. A row of a section that can't be placed reads `not placed`. */
export function stateWord(row: Pick<ViewRow, "state" | "edited">, { refused = false } = {}): string {
  if (row.state === "ok") return "placed";
  if (row.state === "off") return "off";
  if (refused) return "not placed";
  switch (row.state) {
    case "missing": return "not placed";
    case "modified": return "changed";
    case "blocked": return row.edited ? "changed" : "in the way";
    case "stale": return "out of date";
    case "would-commit": return "would be committed";
    case "bad-source": return "can't be read";
    default: return row.state;
  }
}

/** Where a word stands in `WORD_ORDER`; an unknown word after every known one. */
export function wordRank(word: string): number {
  const at = (WORD_ORDER as readonly string[]).indexOf(word);
  return at < 0 ? WORD_ORDER.length : at;
}

/** A path's folder: everything before its last separator. */
export function parentOf(path: string): string {
  const at = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  if (at < 0) return path;
  return at === 0 ? path.slice(0, 1) : path.slice(0, at);
}

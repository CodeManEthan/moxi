// What an opened item and the opened global file show, from `/api/view`: where the item
// comes from, a line per harness with its word, where it stands and why, its notes and
// its act; the global file's mode, its files and Turn off. No DOM, so the Node tests read
// it.

import type { View, ViewGlobalFile, ViewItem, ViewRow } from "./feed.ts";
import { globalModeAct, removeAct, sectionOf, toggleAct } from "./acts-model.ts";
import type { PageAct } from "./acts-model.ts";
import { cellsOf, indexRows, refusedNames } from "./items-model.ts";
import { harnessesLine, harnessWord, homeForm, sectionWord, stateWord } from "./words.ts";

/** What a Replace on a harness line opens: the confirm for that row. */
export interface ReplaceOpen {
  /** What `--replace` names. */
  id: string;
  /** The section the confirm opens under. */
  section: string;
  row: ViewRow;
}

/** One harness's line in the opened item. */
export interface HarnessLine {
  harness: string;
  /** `Claude Code`. */
  label: string;
  /** `placed`, `changed by hand`, …; null for a harness with only a note. */
  word: string | null;
  /** Where it stands: `a link at ~/…`, `an entry in ~/…`; null where the table gives none. */
  where: string | null;
  why: string | null;
  notes: string[];
  /** `Turn off`, `Turn on` or none. */
  toggle: { label: string; act: PageAct } | null;
  replace: ReplaceOpen | null;
}

export interface OpenedItem {
  item: ViewItem;
  from: string;
  lines: HarnessLine[];
  /** For an item off for every harness: `Turn on for every harness` and the dim line naming
   * the harnesses that come back; else null. */
  enableAll: { label: string; act: PageAct; note: string } | null;
  remove: { label: string; note: string; act: PageAct };
}

/** A hook's command with each `home` followed by a separator written `~`. */
export function tildeCommand(command: string, home: string | null): string {
  if (home === null || home === "") return command;
  const base = home.replace(/[\\/]+$/, "");
  if (base === "") return command;
  let out = "";
  let at = 0;
  for (;;) {
    const hit = command.indexOf(base, at);
    if (hit < 0) return out + command.slice(at);
    const sep = command[hit + base.length];
    out += command.slice(at, hit) + (sep === "/" || sep === "\\" ? "~" : base);
    at = hit + base.length;
  }
}

/** The opened item's first line. */
export function fromLine(item: ViewItem, home: string | null): string {
  if (item.module !== null) return `From ${item.module}`;
  const what = item.what;
  if (item.kind === "hook" && what !== null && what.command !== null) {
    const command = tildeCommand(what.command, home);
    const tool = what.matcher === null ? "tool" : what.matcher;
    const when = what.event === "PreToolUse" ? `before each ${tool} call`
      : what.event === "PostToolUse" ? `after each ${tool} call` : `on ${what.event ?? "its event"}`;
    return `From loadout.toml · runs ${command} ${when}`;
  }
  return `From ${homeForm(item.source, home)}`;
}

/** The word, where and why of one row, by the opened item's table. */
export function rowWords(row: ViewRow, home: string | null, { refused = false } = {}): {
  word: string; where: string | null; why: string | null;
} {
  const target = row.target === null ? null : homeForm(row.target, home);
  const harness = harnessWord(row.harness ?? "");
  if (refused && row.state !== "ok" && row.state !== "off") {
    return { word: "not placed", where: target, why: "Nothing in its section can be placed now; Placed says why." };
  }
  switch (row.state) {
    case "ok":
      if (row.form === "link" || row.form === "junction") return { word: "placed", where: `a link at ${target}`, why: null };
      if (row.form === "copy") return { word: "placed", where: `a copy at ${target}`, why: null };
      if (row.form === "generated") return { word: "placed", where: `${target}, made from the source`, why: null };
      if (row.form === "entry") return { word: "placed", where: `an entry in ${target}`, why: null };
      return { word: "placed", where: target, why: null };
    case "missing": return { word: "not placed", where: target, why: "Install places it here." };
    case "modified":
      if (row.form === "entry") {
        return { word: "changed by hand", where: `an entry in ${target}`,
                 why: "Someone edited loadout's entry after loadout wrote it, so loadout leaves it alone." };
      }
      return { word: "changed by hand", where: target,
               why: `${harness} reads its own form of an agent file, which loadout makes from the source. `
                 + "Someone edited that file, so loadout leaves it alone." };
    case "blocked":
      if (row.edited && row.form === "copy") {
        return { word: "changed by hand", where: `a copy at ${target}`,
                 why: `${harness}'s copy was edited, so loadout leaves it alone.` };
      }
      if (row.edited) {
        return { word: "changed by hand", where: target,
                 why: "A file now stands where loadout's link was, so loadout leaves it alone." };
      }
      return { word: "in the way", where: target,
               why: "Something that isn't loadout's is there, so loadout leaves it alone." };
    case "stale": return { word: "out of date", where: target, why: "Install places the new version." };
    case "would-commit": return { word: "would be committed", where: target, why: row.reason || null };
    case "bad-source": return { word: "can't be read", where: null, why: row.reason || null };
    default: return { word: stateWord(row), where: target, why: row.reason || null };
  }
}

/** The sentence of one note on an item's harness, or null for a code the line doesn't show. */
export function noteLine(code: string, harness: string): string | null {
  const word = harnessWord(harness);
  if (code === "trust") return `${word} runs a new hook only once you trust it in ${word}, and loadout can't tell whether you have.`;
  if (code === "unconfirmed") {
    return `Experimental: no run has confirmed yet that ${word} reads the file as loadout writes it.`;
  }
  if (code === "no-hooks") return "OpenCode takes no hooks";
  return null;
}

/** The section's name inside a sentence: `your items`, a key, or a path in `~` form. */
export function sectionInLine(scope: string, view: View): string {
  if (scope === "user") return "your items";
  const section = view.sections.find((s) => s.where !== "user" && s.name === scope);
  return section === undefined ? scope : sectionWord(section, view.home);
}

/** The item `kind` `name` in section `scope` opened, or null when no section lists it. */
export function openedItem(view: View, scope: string, kind: string, name: string): OpenedItem | null {
  const item = view.items.find((i) => i.scope === scope && i.kind === kind && i.name === name);
  if (item === undefined) return null;
  const refused = refusedNames(view).has(scope);
  const cells = cellsOf(item, view.harnesses, indexRows(view.rows), refused);
  const section = sectionOf(scope);
  const allOff = item.disabled.includes("*");
  const notes = scope === "user" ? view.item_notes.filter((n) => n.kind === kind && n.name === name) : [];
  const lines: HarnessLine[] = [];
  for (const cell of cells) {
    const own = notes.map((n) => n.harness === cell.harness ? noteLine(n.code, n.harness) : null)
      .filter((line): line is string => line !== null);
    if (cell.word === null && own.length === 0) continue;
    const row = cell.row;
    const off = cell.word === "off";
    let words: { word: string | null; where: string | null; why: string | null };
    if (off) {
      words = { word: "off", where: null, why: allOff ? "you switched it off for every harness"
        : `you switched it off for ${harnessWord(cell.harness)}` };
    } else if (row === null) {
      words = { word: null, where: null, why: null };
    } else {
      words = rowWords(row, view.home, { refused });
    }
    if (row !== null && row.prior) own.unshift("It was in your file before loadout, so it stays yours");
    const toggle = allOff || cell.word === null ? null
      : off ? { label: "Turn on", act: toggleAct(kind, name, cell.harness, section, { on: true }) }
      : { label: "Turn off", act: toggleAct(kind, name, cell.harness, section, { on: false }) };
    const id = row === null ? null : `${kind}:${name}@${cell.harness}`;
    const replace = row !== null && row.replaceable && id !== null && row.applies !== null
      ? { id, section: scope, row } : null;
    lines.push({ harness: cell.harness, label: harnessWord(cell.harness), ...words, notes: own, toggle, replace });
  }
  let enableAll: OpenedItem["enableAll"] = null;
  if (allOff) {
    const listed = item.harnesses ?? view.harnesses;
    const back = listed.filter((h) => !item.disabled.includes(h));
    enableAll = { label: "Turn on for every harness", act: toggleAct(kind, name, "*", section, { on: true }),
                  note: back.length === 0 ? "" : `${harnessesLine(back)} come back` };
  }
  return { item, from: fromLine(item, view.home), lines, enableAll,
           remove: { label: `Remove from ${sectionInLine(scope, view)}`, note: "its files stay in the folder",
                     act: removeAct(kind, name, section) } };
}

// -- the global file ------------------------------------------------------------------------

/** Each mode's sentence, and the one for a manifest that chooses none. */
export const MODE_LINES = {
  managed: "Managed: your own text stays, and loadout adds its part to each file.",
  "if-absent": "Only where absent: where a harness has no global file, loadout places global.md there.",
  off: "Off: loadout touches no global file.",
  none: "The manifest chooses no global mode yet, so it reads off and loadout touches no global file.",
} as const;

/** What loadout adds to a global file, by its form. */
export const FORM_WORDS: Readonly<Record<string, string>> = {
  line: "one line that names global.md",
  block: "a marked block",
  instructions: "one instructions entry",
  placed: "placed where it had none",
  left: "it has its own file, so loadout leaves it",
};

/** The confirm's words before Turn off. */
export const TURN_OFF_CONFIRM = "Turn off takes loadout's part out of each file and leaves your text";

export interface GlobalFileLine {
  harness: string;
  label: string;
  file: string;
  /** What loadout adds; null when the form isn't known. */
  adds: string | null;
  /** `the next install changes nothing`, or `the next install changes it`. */
  next: string;
  /** The diff `Show` opens, when the next install changes the file. */
  diff: string | null;
}

export interface OpenedGlobal {
  /** `From ~/my-loadout/global.md`; null when the manifest names no global text. */
  from: string | null;
  /** The three choices; `Off` opens the confirm, as Turn off does. */
  modes: Array<{ mode: "off" | "managed" | "if-absent"; label: string; current: boolean; act: PageAct | null }>;
  line: string;
  files: GlobalFileLine[];
  /** Turn off and its dim line; null while the manifest chooses off. */
  turnOff: { label: string; note: string; confirm: string } | null;
}

function fileLine(file: ViewGlobalFile, home: string | null): GlobalFileLine {
  const changes = file.changes;
  return { harness: file.harness, label: harnessWord(file.harness), file: homeForm(file.file, home),
           adds: file.form === null ? null : FORM_WORDS[file.form] ?? null,
           next: changes ? "the next install changes it" : "the next install changes nothing",
           diff: changes && file.diff !== "" ? file.diff : null };
}

/** The opened global file. Off and Turn off open the same confirm; the page sends
 * `globalOffAct` once it is confirmed. */
export function openedGlobal(view: View): OpenedGlobal {
  const g = view.global;
  const current = g.chosen ? g.mode : null;
  const modes = ([["off", "Off"], ["managed", "Managed"], ["if-absent", "Only where absent"]] as const)
    .map(([mode, label]) => ({ mode, label, current: current === mode,
                               act: mode === "off" || current === mode ? null : globalModeAct(mode) }));
  const line = !g.chosen ? MODE_LINES.none
    : g.mode === "managed" || g.mode === "if-absent" || g.mode === "off" ? MODE_LINES[g.mode] : MODE_LINES.off;
  return { from: g.source === null ? null : `From ${homeForm(g.source, view.home)}`, modes, line,
           files: g.files.map((file) => fileLine(file, view.home)),
           turnOff: g.chosen && g.mode === "off" ? null
             : { label: "Turn off", note: "takes loadout's part out of each file and leaves your text",
                 confirm: TURN_OFF_CONFIRM } };
}

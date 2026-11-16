// The item grid as the page draws it, from `/api/view`: a group per section (and per
// module whose skills the user section took over), a row per item with a cell per harness
// of the harness table, the global file as the last row of Your items, and past twelve
// items a section folded by kind. No DOM, so the Node tests read it.

import type { ItemNote, View, ViewItem, ViewRow, ViewSection } from "./feed.ts";
import {
  counted, harnessWord, kindFolded, KIND_WORDS, kindWord, sectionWord, stateWord, YOUR_ITEMS,
} from "./words.ts";

/** A section folds by kind when it lists more items than this. */
export const FOLD_PAST = 12;

/** How a cell's word is drawn: `plain` (placed), `dim` (off) or `heavy` (weight 600). */
export type Tone = "plain" | "dim" | "heavy";

/** One item for one harness of the table. */
export interface CellView {
  harness: string;
  /** `placed`, `off`, the state word, or null for a blank cell (the item isn't listed for
   * that harness, or loadout places nothing for it there). */
  word: string | null;
  tone: Tone;
  /** The row the cell is drawn from: the item's own, or for a permission item's Codex cell
   * the Codex rules file's row that holds it; null for a blank cell, or for one off with no
   * row. */
  row: ViewRow | null;
}

/** One item's row in the grid. */
export interface ItemRow {
  /** `[section, kind, name]` as JSON: one key per item. */
  key: string;
  kind: string;
  name: string;
  /** The kind as the row says it. */
  kindWord: string;
  item: ViewItem;
  cells: CellView[];
  /** The row's second line: Codex's trust note on a hook, else null. */
  second: string | null;
}

/** One kind of a folded section. */
export interface KindRow {
  kind: string;
  /** `Skills 34`. */
  label: string;
  cells: Array<{ harness: string; text: string | null; tone: Tone }>;
  rows: ItemRow[];
}

/** The global file's row, the last of Your items. */
export interface GlobalRow {
  label: string;
  /** `managed`, `only where absent` or `off`. */
  mode: string;
  cells: CellView[];
}

/** One group of the grid: a section, or the skills a module took over. */
export interface GroupView {
  /** The section's name, or `module:<name>` for a module's group. */
  key: string;
  label: string;
  /** `8 items`: the global file counts as one in Your items. */
  count: string;
  section: ViewSection;
  module: string | null;
  rows: ItemRow[];
  /** The kinds, when the group lists more than `FOLD_PAST` items; else null. */
  kinds: KindRow[] | null;
  global: GlobalRow | null;
  /** `It lists no item.` for a group with no item, else null. */
  empty: string | null;
}

export interface ItemsView {
  /** The harness table's names: one column each. */
  harnesses: string[];
  groups: GroupView[];
}

/** The key of one item: its section (`user` or a project section's name), kind and name. */
export const itemKey = (scope: string | null, kind: string | null, name: string | null): string =>
  JSON.stringify([scope, kind, name]);

/** A row is the Codex rules file's: the one that holds permission items. */
export const isRulesRow = (row: ViewRow): boolean => row.covers !== null;

/** The section an item or row belongs to, as rows name it: `user` or the section's name. */
export const scopeOf = (section: ViewSection): string => section.where === "user" ? "user" : section.name;

/** The rows of each listed item, by `itemKey`, with each Codex rules file row filed under
 * every permission item it holds. */
export interface RowIndex {
  own: Map<string, ViewRow[]>;
  /** The rules file's row that holds each permission item, by `itemKey`. */
  rules: Map<string, ViewRow>;
}

export function indexRows(rows: readonly ViewRow[]): RowIndex {
  const own = new Map<string, ViewRow[]>();
  const rules = new Map<string, ViewRow>();
  for (const row of rows) {
    if (isRulesRow(row)) {
      for (const name of row.covers ?? []) rules.set(itemKey(row.scope, "permissions", name), row);
      continue;
    }
    const key = itemKey(row.scope, row.kind, row.name);
    const list = own.get(key);
    if (list) list.push(row);
    else own.set(key, [row]);
  }
  return { own, rules };
}

/** The sections that can't be placed this run, by name. */
export function refusedNames(view: Pick<View, "sections">): Set<string> {
  return new Set(view.sections.filter((s) => s.where !== "user" && s.refused !== null).map((s) => s.name));
}

function cellOf(harness: string, row: ViewRow | null, refused: boolean): CellView {
  if (row === null) return { harness, word: null, tone: "plain", row };
  const word = stateWord(row, { refused });
  const tone: Tone = word === "placed" ? "plain" : word === "off" ? "dim" : "heavy";
  return { harness, word, tone, row };
}

/** The item's cells, one per harness of the table. A harness the item has no row for is
 * blank, or `off` when the item is listed for it and switched off there. */
export function cellsOf(item: ViewItem, harnesses: readonly string[], index: RowIndex,
                        refused: boolean): CellView[] {
  const key = itemKey(item.scope, item.kind, item.name);
  const own = index.own.get(key) ?? [];
  return harnesses.map((harness) => {
    let row = own.find((r) => r.harness === harness) ?? null;
    if (row === null && item.kind === "permissions") {
      const rules = index.rules.get(key) ?? null;
      if (rules !== null && rules.harness === harness) row = rules;
    }
    if (row !== null) return cellOf(harness, row, refused);
    const listed = item.harnesses === null || item.harnesses.includes(harness);
    if (listed && (item.disabled.includes("*") || item.disabled.includes(harness))) {
      return { harness, word: "off", tone: "dim", row: null };
    }
    return cellOf(harness, null, refused);
  });
}

/** The trust note's words for a hook on `harness`. */
export function trustLine(harness: string): string {
  const word = harnessWord(harness);
  return `${word} runs a new hook only once you trust it in ${word}`;
}

/** An item's second line: the trust note, when it has one. Notes are on the user section's
 * items, since only those write entries. */
function secondOf(item: ViewItem, notes: readonly ItemNote[]): string | null {
  if (item.scope !== "user") return null;
  const note = notes.find((n) => n.code === "trust" && n.kind === item.kind && n.name === item.name);
  return note === undefined ? null : trustLine(note.harness);
}

function itemRow(item: ViewItem, view: View, index: RowIndex, refused: boolean): ItemRow {
  return { key: itemKey(item.scope, item.kind, item.name), kind: item.kind, name: item.name,
           kindWord: kindWord(item.kind), item, cells: cellsOf(item, view.harnesses, index, refused),
           second: secondOf(item, view.item_notes) };
}

/** One harness's cell of a folded kind: `<n> placed`, `<n> · <k> off`, or `<placed> of
 * <n>` at weight 600; null when no item of the kind is listed for the harness. */
export function kindCell(cells: readonly CellView[]): { text: string | null; tone: Tone } {
  const off = cells.filter((cell) => cell.word === "off").length;
  const on = cells.filter((cell) => cell.word !== null && cell.word !== "off");
  const placed = on.filter((cell) => cell.word === "placed").length;
  if (on.length === 0) return off === 0 ? { text: null, tone: "plain" } : { text: `${off} off`, tone: "dim" };
  if (placed < on.length) return { text: `${placed} of ${on.length}`, tone: "heavy" };
  return { text: off === 0 ? `${on.length} placed` : `${on.length} · ${off} off`, tone: "plain" };
}

/** The kinds of a folded group, in `KIND_WORDS`' order, each with its rows. */
export function foldKinds(rows: readonly ItemRow[], harnesses: readonly string[]): KindRow[] {
  const kinds = [...Object.keys(KIND_WORDS), ...rows.map((row) => row.kind)];
  const out: KindRow[] = [];
  for (const kind of new Set(kinds)) {
    const own = rows.filter((row) => row.kind === kind);
    if (own.length === 0) continue;
    const cells = harnesses.map((harness, at) => ({ harness, ...kindCell(own.map((row) => row.cells[at])) }));
    out.push({ kind, label: `${kindFolded(kind)} ${own.length}`, cells, rows: own });
  }
  return out;
}

/** The global file's mode as its row says it. */
export function modeWord(mode: string, chosen: boolean): string {
  if (!chosen || mode === "off") return "off";
  return mode === "if-absent" ? "only where absent" : mode;
}

/** The global file's row: its mode and a cell per harness it has a row for. */
export function globalRow(view: View): GlobalRow {
  const rows = view.rows.filter((row) => row.kind === "global");
  const cells = view.harnesses.map((harness) => cellOf(harness, rows.find((row) => row.harness === harness) ?? null,
                                                       false));
  return { label: "Global file", mode: modeWord(view.global.mode, view.global.chosen), cells };
}

const USER_SECTION: ViewSection = { name: "user", where: "user", root: null, refused: null };

function group(key: string, label: string, section: ViewSection, module: string | null,
               rows: ItemRow[], global: GlobalRow | null, harnesses: readonly string[]): GroupView {
  const kinds = rows.length > FOLD_PAST ? foldKinds(rows, harnesses) : null;
  const count = counted(rows.length + (global === null ? 0 : 1), "item");
  return { key, label, count, section, module, rows, kinds, global,
           empty: rows.length === 0 ? "It lists no item." : null };
}

/**
 * The grid: Your items (the folder's own items, then the global file), a group per module
 * whose skills the user section took over, by module name, then each project section in
 * the manifest's order. Within a group, items keep the view's order.
 */
export function itemsView(view: View): ItemsView {
  const index = indexRows(view.rows);
  const refused = refusedNames(view);
  const user = view.sections.find((s) => s.where === "user") ?? USER_SECTION;
  const groups: GroupView[] = [];
  const mine = view.items.filter((item) => item.scope === "user");
  const folder = mine.filter((item) => item.module === null).map((item) => itemRow(item, view, index, false));
  groups.push(group("user", YOUR_ITEMS, user, null, folder, globalRow(view), view.harnesses));
  const modules = [...new Set(mine.map((item) => item.module).filter((m): m is string => m !== null))].sort();
  for (const module of modules) {
    const rows = mine.filter((item) => item.module === module).map((item) => itemRow(item, view, index, false));
    groups.push(group(`module:${module}`, module, user, module, rows, null, view.harnesses));
  }
  for (const section of view.sections) {
    if (section.where === "user") continue;
    const scope = scopeOf(section);
    const rows = view.items.filter((item) => item.scope === scope)
      .map((item) => itemRow(item, view, index, refused.has(section.name)));
    groups.push(group(section.name, sectionWord(section, view.home), section, null, rows, null, view.harnesses));
  }
  return { harnesses: [...view.harnesses], groups };
}

/** One harness's counts: items listed for it and not off (the global file as one), and of
 * those the ones placed. */
export interface HarnessCount {
  harness: string;
  listed: number;
  placed: number;
}

/** The counts per harness, in the table's order, over every group's items and the global
 * file. A harness nothing is listed for is left out. */
export function harnessCounts(items: ItemsView): HarnessCount[] {
  const out: HarnessCount[] = [];
  items.harnesses.forEach((harness, at) => {
    let listed = 0;
    let placed = 0;
    for (const g of items.groups) {
      const cells = [...g.rows.map((row) => row.cells[at]), ...(g.global === null ? [] : [g.global.cells[at]])];
      for (const cell of cells) {
        if (cell.word === null || cell.word === "off") continue;
        listed += 1;
        if (cell.word === "placed") placed += 1;
      }
    }
    if (listed > 0) out.push({ harness, listed, placed });
  });
  return out;
}

/** The counts line: with nothing needing him `Claude Code 10 · Codex 9`, else `Claude
 * Code 9 of 10 placed · Codex 7 of 9`. */
export function countsLine(counts: readonly HarnessCount[], fine: boolean): string {
  return counts.map((count, at) => {
    const word = harnessWord(count.harness);
    if (fine) return `${word} ${count.listed}`;
    return `${word} ${count.placed} of ${count.listed}${at === 0 ? " placed" : ""}`;
  }).join(" · ");
}

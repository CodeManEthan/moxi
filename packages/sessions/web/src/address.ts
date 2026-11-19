// The list's address: its filters, its find text and how far it was paged, in the query of
// the list's own route, `/m/sessions/`, worked out with no DOM so the Node tests can read
// it. Also the history entry's memory of the rows opened in place and the scroll, which a
// shared link does not carry.
//
//     /m/sessions/?folder=/work/atlas&harness=claude&when=7d&find=atlas&more=recent:1,earlier:2&program=2026-11-09:1
//
// - The keys come in that order, and each is written only off its default, so one state
//   has one address and nothing set is the bare route.
// - `when` is what was picked: `today`, `7d`, `30d`, or a range of days `a..b`, either end
//   left open.
// - `find` is the text as it was sent: trimmed, two characters or more, cut at 256.
// - `more` is how many `Show` reads each group had past what the list answer gave, and
//   `program` how many pages of a day's opened program line were read. A count of pages,
//   not a place: the list is read again to it from a fresh answer.
//
// Reading takes what the list's own controls could have set and drops the rest, each
// dropped value named in the one line above the list.
//
// Every string here goes into the page as text.

import { findSent, whenOf } from "./list.ts";
import type { Filters, When } from "./list.ts";
import { isHarness } from "./views.ts";

/** The list's route: the one path whose query is the list's address. */
export const LIST_PATH = "/m/sessions/";

/** How many pages of one group, or of one day's program line, were read. */
export interface DepthItem {
  group: string;
  pages: number;
}

/** How far the list was paged: `more` per group, `program` per day. */
export interface Depth {
  more: DepthItem[];
  program: DepthItem[];
}

export const NO_DEPTH: Depth = { more: [], program: [] };

/** The list's state as its address holds it. */
export interface Address {
  filters: Filters;
  depth: Depth;
}

/** An address as read: what the list takes, and each value it does not, as written. */
export interface ReadAddress extends Address {
  dropped: string[];
}

/** The keys, in the order they are written. */
export const KEYS = ["folder", "harness", "when", "find", "more", "program"] as const;

// -- days and groups ----------------------------------------------------------------------

/** Whether `text` is a calendar day, `YYYY-MM-DD`, that exists. */
export function isDay(text: string): boolean {
  const found = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (found === null) return false;
  const [year, month, day] = [Number(found[1]), Number(found[2]), Number(found[3])];
  const d = new Date(Date.UTC(year, month - 1, day));
  return d.getUTCFullYear() === year && d.getUTCMonth() === month - 1 && d.getUTCDate() === day;
}

/** Whether `group` may stand in `more`: the list's own group keys. */
export const isMoreGroup = (group: string): boolean =>
  group === "recent" || group === "found" || group === "earlier" || group === "undated" || isDay(group);

/** Whether `group` may stand in `program`: a day, whose program line it names. The
 * sessions with no change time are a day too, `undated`, as the list's answers give it. */
export const isProgramGroup = (group: string): boolean => group === "undated" || isDay(group);

/** Where a group stands in the list, top to bottom: `recent` (or `found`), the days newest
 * first, `undated`, then `earlier`. */
function groupRank(group: string): number {
  if (group === "recent") return 0;
  if (group === "found") return 1;
  if (group === "undated") return 3;
  if (group === "earlier") return 4;
  return 2;
}

/** Orders two groups as the list shows them. */
export function listOrder(a: string, b: string): number {
  const rank = groupRank(a) - groupRank(b);
  if (rank !== 0) return rank;
  return a === b ? 0 : a > b ? -1 : 1;
}

const ordered = (items: DepthItem[]): DepthItem[] =>
  items.filter((item) => item.pages > 0).sort((a, b) => listOrder(a.group, b.group));

// -- writing ------------------------------------------------------------------------------

/** A value as the address writes it: percent-encoded, with a space as `+` and `/ : , @`
 * as themselves, so a folder reads as a path. */
export function encodeValue(text: string): string {
  return encodeURIComponent(text).replace(/%20/g, "+").replace(/%2F/g, "/").replace(/%3A/g, ":")
    .replace(/%2C/g, ",").replace(/%40/g, "@");
}

/** `When` as the address writes it; null for any time. */
export function whenText(when: When): string | null {
  const set = whenOf(when);
  if (set.kind === "today") return "today";
  if (set.kind === "last") return `${set.days}d`;
  if (set.kind === "range") return `${set.from ?? ""}..${set.to ?? ""}`;
  return null;
}

const itemsText = (items: DepthItem[]): string => items.map((item) => `${item.group}:${item.pages}`).join(",");

/** The filters' keys and the find text as sent, the query in the address's words, with no
 * depth: two states make the same query when these are the same. */
export function queryWords(filters: Filters): string {
  return addressText({ filters, depth: NO_DEPTH });
}

/**
 * The address's query for `address`: `""` when nothing is set, else `?` and the keys in
 * their order, each only off its default. Depth items are written in list order, and an
 * item of no pages is left out.
 */
export function addressText(address: Address): string {
  const { filters, depth } = address;
  const keys: [string, string | null][] = [
    ["folder", filters.folder !== null && filters.folder !== "" ? filters.folder : null],
    ["harness", filters.harness !== null && filters.harness !== "" ? filters.harness : null],
    ["when", whenText(filters.when)],
    ["find", findSent(filters.find)],
    ["more", ordered(depth.more).length > 0 ? itemsText(ordered(depth.more)) : null],
    ["program", ordered(depth.program).length > 0 ? itemsText(ordered(depth.program)) : null],
  ];
  const text = keys.filter(([, value]) => value !== null)
    .map(([key, value]) => `${key}=${encodeValue(value as string)}`).join("&");
  return text === "" ? "" : `?${text}`;
}

// -- reading ------------------------------------------------------------------------------

/** `when` as the address gives it, or null when it is not one of its forms. */
export function parseWhen(text: string): When | null {
  if (text === "today") return { kind: "today" };
  if (text === "7d") return { kind: "last", days: 7 };
  if (text === "30d") return { kind: "last", days: 30 };
  const range = /^(.*)\.\.(.*)$/.exec(text);
  if (range === null) return null;
  const [from, to] = [range[1], range[2]];
  if ((from !== "" && !isDay(from)) || (to !== "" && !isDay(to))) return null;
  return whenOf({ kind: "range", from: from !== "" ? from : null, to: to !== "" ? to : null });
}

/** The items of `more` or `program`: those that parse and name a group allowed there,
 * each group once; a group given twice is dropped both times. */
function readItems(key: "more" | "program", text: string, allowed: (group: string) => boolean,
                   dropped: string[]): DepthItem[] {
  if (text === "") return [];
  const parsed = text.split(",").map((item) => {
    const found = /^([^:]+):([1-9]\d*)$/.exec(item);
    return { item, value: found !== null && allowed(found[1]) ? { group: found[1], pages: Number(found[2]) } : null };
  });
  const times = new Map<string, number>();
  for (const { value } of parsed) if (value !== null) times.set(value.group, (times.get(value.group) ?? 0) + 1);
  const items: DepthItem[] = [];
  for (const { item, value } of parsed) {
    if (value !== null && times.get(value.group) === 1) items.push(value);
    else dropped.push(`${key}=${item}`);
  }
  return items;
}

/**
 * What the list takes from the query `search` (`location.search`, with or without its
 * `?`), and what it drops.
 *
 * A value the list's own controls could have set is taken: a folder as an absolute path,
 * one of the three harnesses, a `when` of its forms (`..` is any time), find as given,
 * since the field shows it as typed and sends what it sends. Everything else is dropped
 * and named as written: a key the list does not know, a key given twice, a value of none
 * of those forms, and a `more` or `program` item that does not parse, names a group not
 * allowed there or a group given twice (the good items beside it are kept). No count is
 * refused for its size.
 */
export function readAddress(search: string): ReadAddress {
  const params = [...new URLSearchParams(search)];
  const times = new Map<string, number>();
  for (const [key] of params) times.set(key, (times.get(key) ?? 0) + 1);
  const filters: Filters = { folder: null, harness: null, when: { kind: "any" }, find: "" };
  let depth: Depth = NO_DEPTH;
  const dropped: string[] = [];
  for (const [key, value] of params) {
    const named = `${key}=${value}`;
    if (!(KEYS as readonly string[]).includes(key) || times.get(key) !== 1) {
      dropped.push(named);
      continue;
    }
    if (key === "folder") {
      if (value.startsWith("/")) filters.folder = value;
      else dropped.push(named);
    } else if (key === "harness") {
      if (isHarness(value)) filters.harness = value;
      else dropped.push(named);
    } else if (key === "when") {
      const when = parseWhen(value);
      if (when !== null) filters.when = when;
      else dropped.push(named);
    } else if (key === "find") {
      filters.find = value;
    } else if (key === "more") {
      depth = { ...depth, more: readItems("more", value, isMoreGroup, dropped) };
    } else {
      depth = { ...depth, program: readItems("program", value, isProgramGroup, dropped) };
    }
  }
  return { filters, depth, dropped };
}

/** How many `/api/more` reads one restore asks at most. */
export const RESTORE_READS = 40;

/** The line's sentence for a restore that met its cap. */
export const CAPPED = `This address asked for more of the list than one restore reads (${RESTORE_READS} pages); `
  + "it is shown as far as those reached.";

/** The dim line above the list: what the address held that the list does not take, and,
 * once a restore met its cap, that it did. Null when there is neither. */
export function addressLine(dropped: string[], { capped = false }: { capped?: boolean } = {}): string | null {
  const parts: string[] = [];
  if (dropped.length > 0) {
    const listed = dropped.length === 1 ? dropped[0]
      : `${dropped.slice(0, -1).join(", ")} and ${dropped[dropped.length - 1]}`;
    parts.push(`This address held ${listed}, which the list does not take; the list is shown without `
               + `${dropped.length === 1 ? "it" : "them"}.`);
  }
  if (capped) parts.push(CAPPED);
  return parts.length > 0 ? parts.join(" ") : null;
}

// -- the entry's memory -------------------------------------------------------------------

/** The key of `history.state` the list keeps its entry's memory under. */
export const MEMORY_KEY = "moxi.sessions.list";

/** The rows opened in place, by key, and the document's scroll. */
export interface Memory {
  opened: string[];
  y: number | null;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** The memory an entry's `history.state` holds; nothing opened and no scroll when it
 * holds none that reads. */
export function memoryOf(state: unknown): Memory {
  const kept = isRecord(state) ? state[MEMORY_KEY] : undefined;
  if (!isRecord(kept)) return { opened: [], y: null };
  const opened = Array.isArray(kept.opened) ? kept.opened.filter((key): key is string => typeof key === "string") : [];
  const y = typeof kept.y === "number" && Number.isFinite(kept.y) && kept.y >= 0 ? kept.y : null;
  return { opened, y };
}

/** `state` with `memory` under its key and everything else it held kept; null when it
 * already holds that memory, so nothing need be written. */
export function withMemory(state: unknown, memory: Memory): Record<string, unknown> | null {
  const now = memoryOf(state);
  if (now.y === memory.y && now.opened.length === memory.opened.length
      && now.opened.every((key, at) => key === memory.opened[at])) return null;
  return { ...(isRecord(state) ? state : {}), [MEMORY_KEY]: { opened: [...memory.opened], y: memory.y } };
}

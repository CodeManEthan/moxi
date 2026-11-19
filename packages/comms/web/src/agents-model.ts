// The Comms page's agent rows worked out with no DOM, no Lit and no request, so the Node
// tests read them. From the roster, `/api/mail-state` and, with launcher, each address's
// running launch, it says which rows there are, what each row's lines say, which address
// each speaks for, their order and what goes to `Other names`.
//
// - One row per agent name, the part of an address before `@`. A row gathers every address
//   the page knows with that name: the roster's rows and `waiting` names, and the authors
//   of `owed` messages. A name none of whose addresses has a session, mail or an `owed`
//   message is no row. The operator is never a row: an address the server marks as his
//   is left out, though mail to him still shows in the message lists.
// - The lead address is the one the row's single facts speak for: line one's `where` and
//   cache fact, the bound cases of line two and the box's `to`.
// - A row's case is the highest of `CASES` any of its addresses meets; the bound cases are
//   read off the lead alone. The second case, when there is one, leads line three.
// - Every other address with mail or a session gets a piece of line three. An address Home
//   names in full gets one even when it is the lead, so the page and Home name the same
//   mail by the same name.
//
// Times are worded by the `when` the caller passes; every string here goes into the page as
// text.

import type { CacheFact, Roster, RosterRow } from "./feed.ts";
import { parseCacheFact } from "./feed.ts";
import type { Launched } from "./launched-model.ts";
import { LOCAL, namesWords } from "./mail-model.ts";

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const isTable = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const isString = (value: unknown): value is string => typeof value === "string";

// --- the reads ----------------------------------------------------------------------------

/** One address with mail queued, as the roster sends it: its count and its oldest message's
 * time, epoch seconds. Bound or not. */
export interface Waiting {
  qualified: string;
  count: number;
  since: number;
}

/** `/api/roster` (without `now`) and the topic `roster`, as the page reads them. */
export interface PageRoster extends Roster {
  waiting: Waiting[];
  /** The page server's local date, `YYYY-MM-DD`; null when the value carried none (the
   * topic carries none). */
  today: string | null;
}

const isDay = (value: unknown): value is string => isString(value) && /^\d{4}-\d{2}-\d{2}$/.test(value);

/** One `waiting` entry, or null when it is not one. */
function parseWaiting(data: unknown): Waiting | null {
  if (!isTable(data)) return null;
  const { qualified, count, since } = data;
  if (!isString(qualified) || qualified === "" || !Number.isInteger(count) || (count as number) < 1 || !finite(since)) {
    return null;
  }
  return { qualified, count: count as number, since };
}

/** The page's value of the route and the topic, or null when it isn't one. An entry of
 * `waiting` that is not one is left out. */
export function parsePageRoster(data: unknown, parse: (data: unknown) => Roster | null): PageRoster | null {
  const value = parse(data);
  if (value === null) return null;
  const { waiting, today } = data as Record<string, unknown>;
  const entries = value.state === "up" && Array.isArray(waiting) ? waiting.map(parseWaiting) : [];
  return {
    ...value,
    waiting: entries.filter((entry): entry is Waiting => entry !== null),
    today: isDay(today) ? today : null,
  };
}

/** An author of waiting mail; `isOperator` is the server's mark for the operator
 * (`is_operator`), whatever name the directory gives him. */
export interface Author {
  qualified: string;
  isOperator: boolean;
}

/** An address with a row in the books: how many rows wait, how many are in its session,
 * and the authors of the waiting ones, oldest first. */
export interface Queued {
  qualified: string;
  waiting: number;
  handed: number;
  from: Author[];
}

/** A message to the operator that waits for his reply. */
export interface Owed {
  message_id: string;
  author: string;
  at: number;
  text: string;
}

/** An open ask between two agents; `read` is false while the awaited name has not read it. */
export interface Ask {
  asker: string;
  awaited: string;
  message_id: string;
  at: number;
  read: false | null;
}

/** An address's newest message within the read, by it or to it. */
export interface Last {
  qualified: string;
  at: number;
}

/** `/api/mail-state`, as the page reads it. */
export interface MailState {
  state: "up" | "error";
  detail: string | null;
  today: string | null;
  queued: Queued[];
  owed: Owed[];
  asks: Ask[];
  last: Last[];
  /** The names that are the operator. */
  operator_names: string[];
}

const isId = (value: unknown): value is string => isString(value) && /^\d+$/.test(value);
const count = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0;

function keep<T>(data: unknown, parse: (item: Record<string, unknown>) => T | null): T[] {
  if (!Array.isArray(data)) return [];
  return data.map((item) => (isTable(item) ? parse(item) : null)).filter((item): item is T => item !== null);
}

function parseAuthor({ qualified, is_operator: isOperator }: Record<string, unknown>): Author | null {
  return isString(qualified) && typeof isOperator === "boolean" ? { qualified, isOperator } : null;
}

/** The answer of `/api/mail-state`, or null when it isn't one. An entry that is not one is
 * left out. */
export function parseMailState(data: unknown): MailState | null {
  if (!isTable(data)) return null;
  const { state, detail, today } = data;
  if (state !== "up" && state !== "error") return null;
  if (detail !== null && detail !== undefined && !isString(detail)) return null;
  const empty = { state: state as MailState["state"], detail: detail ?? null, today: isDay(today) ? today : null };
  if (state === "error") return { ...empty, queued: [], owed: [], asks: [], last: [], operator_names: [] };
  return {
    ...empty,
    queued: keep(data.queued, ({ qualified, waiting, handed, from }) =>
      (isString(qualified) && count(waiting) && count(handed) && Array.isArray(from)
        ? { qualified, waiting, handed, from: keep(from, parseAuthor) } : null)),
    owed: keep(data.owed, ({ message_id, author, at, text }) =>
      (isId(message_id) && isString(author) && finite(at) && isString(text) ? { message_id, author, at, text } : null))
      .sort((a, b) => a.at - b.at),
    asks: keep(data.asks, ({ asker, awaited, message_id, at, read }) =>
      (isString(asker) && isString(awaited) && isId(message_id) && finite(at) && (read === false || read === null)
        ? { asker, awaited, message_id, at, read } : null)),
    last: keep(data.last, ({ qualified, at }) => (isString(qualified) && finite(at) ? { qualified, at } : null)),
    operator_names: Array.isArray(data.operator_names) ? data.operator_names.filter(isString) : [],
  };
}

/** The addresses the server marks as the operator: its `operator_names`, and every author
 * of waiting mail it marks. Empty before the mail state is read. */
export function operatorAddresses(mail: MailState | null): Set<string> {
  if (mail === null || mail.state !== "up") return new Set();
  const marked = mail.queued.flatMap((entry) => entry.from.filter((author) => author.isOperator))
    .map((author) => author.qualified);
  return new Set([...mail.operator_names, ...marked]);
}

export type { Launched };

// --- the rows -----------------------------------------------------------------------------

/** A line's right end, and whether it is heavy. */
export interface Fact {
  text: string;
  heavy: boolean;
}

/** Line two: its lead, whether the lead is heavy, and the phrase after it. */
export interface Line {
  lead: string;
  heavy: boolean;
  more: string | null;
}

/** The kinds of waiting, highest first. */
export const CASES = ["owed", "mail", "uncovered", "held", "awaiting", "idle"] as const;
export type Case = (typeof CASES)[number];

/** What the page knows of one address. */
export interface Address {
  qualified: string;
  /** The part before `@`. */
  name: string;
  /** Its roster row, or null for an address the roster does not list. */
  row: RosterRow | null;
  /** A session holds it. */
  bound: boolean;
  /** The roster's `waiting` entry. */
  waiting: Waiting | null;
  /** Its row in the books' summary. */
  queued: Queued | null;
  /** The messages it wrote to the operator that wait for his reply, oldest first. */
  owed: Owed[];
  /** Its newest message within the last day. */
  last: number | null;
  /** Its running launch, with launcher installed. */
  launched: Launched | null;
}

/** A piece of line three that speaks for one address; pressing it opens the row with the box
 * written to that address. */
export interface AddressLine {
  qualified: string;
  text: string;
}

/** An act of an agent row: `Reply` writes to the asker's own address and answers its oldest
 * `owed` message; `Message` writes to the lead; `Keep warm` presses launcher's act. */
export type Act =
  | { kind: "reply"; to: string; message_id: string }
  | { kind: "message"; to: string }
  | { kind: "keep-warm"; request_id: string; title: string };

/** One agent row. */
export interface AgentRow {
  name: string;
  /** The address its single facts speak for. */
  lead: string;
  /** Every address of the name, by qualified name. */
  addresses: Address[];
  /** Null only for a row kept for its open box (`Options.keep`) that meets no case yet. */
  case: Case | null;
  /** The second-highest case the name meets, or null. */
  second: Case | null;
  where: string | null;
  fact: Fact | null;
  line: Line;
  /** Line three: the second case's words (or null), then the address lines. */
  note: { second: string | null; lines: AddressLine[] };
  acts: Act[];
}

/** A name or address in `Other names`: a standing address none of whose name's addresses
 * has a session, mail or an `owed` message; a scoped address with neither mail nor a session
 * whose name has a row; a send-only name, which takes no mail, with the module that sends
 * by it. */
export interface OtherName {
  qualified: string;
  /** Who sends under a send-only name: its module, or `a program` for a name a program
   * registered; null else. */
  sentBy: string | null;
  /** `Message` writes to it; false for a send-only name. */
  writable: boolean;
}

/** The agents part of the page: `N agents` is `rows.length`. */
export interface Agents {
  rows: AgentRow[];
  others: OtherName[];
}

/** What the rows read beyond the two answers. */
export interface Options {
  /** The server's time now, epoch seconds. */
  now: number;
  /** A time as the page words it. */
  when?: (at: number) => string;
  /** The cache module is installed: without it, held mail reads `not read yet`. */
  hasCache: boolean;
  /** Each address's running launch, with launcher installed; null without it. */
  launched?: ReadonlyMap<string, Launched> | null;
  /** The address the open box writes to while it holds a Send's answer: its name stays a
   * row whatever its case, so the box and its answer stay drawn. */
  keep?: string | null;
}

const nameOf = (qualified: string): string => qualified.split("@")[0];
const isBare = (qualified: string): boolean => !qualified.includes("@");
const byText = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

/** `1 message` or `n messages`. */
export function messages(n: number): string {
  return n === 1 ? "1 message" : `${n} messages`;
}

/** Authors as a line names them: the one the server marks the operator as `you`, each
 * other by the part before `@` unless two share it, then in full. */
export function authorsWords(authors: readonly Author[]): string {
  const shorts = authors.filter((author) => !author.isOperator).map((author) => nameOf(author.qualified));
  const named = authors.map((author) => {
    if (author.isOperator) return "you";
    const short = nameOf(author.qualified);
    return shorts.filter((other) => other === short).length === 1 ? short : author.qualified;
  });
  return namesWords([...new Set(named)]);
}

/** The cache fact as comms words it: `warm until <time>`, or `cold` (a warm read whose time
 * has passed is cold). */
export function cacheWords(cache: CacheFact | null, now: number, when: (at: number) => string): Fact | null {
  if (cache === null) return null;
  if (cache.kind === "cold" || cache.until <= now) return { text: "cold", heavy: false };
  return { text: `warm until ${when(cache.until)}`, heavy: false };
}

/** An address's cache fact: launcher's when it has one, else comms'. */
function factOf(address: Address, now: number, when: (at: number) => string): Fact | null {
  return address.launched?.fact ?? cacheWords(address.row?.cache ?? null, now, when);
}

/** The addresses of every name the page knows, by name, but the operator's. */
function gather(roster: PageRoster, mail: MailState | null, launched: ReadonlyMap<string, Launched> | null,
                keep: string | null): Map<string, Address[]> {
  const him = operatorAddresses(mail);
  const addresses = new Map<string, Address>();
  const address = (qualified: string): Address => {
    let found = addresses.get(qualified);
    if (found === undefined) {
      found = { qualified, name: nameOf(qualified), row: null, bound: false, waiting: null, queued: null, owed: [],
                last: null, launched: launched?.get(qualified) ?? null };
      addresses.set(qualified, found);
    }
    return found;
  };
  for (const row of roster.rows.filter((entry) => !him.has(entry.qualified))) {
    const found = address(row.qualified);
    found.row = row;
    found.bound = row.live;
  }
  for (const entry of roster.waiting.filter((waiting) => !him.has(waiting.qualified))) {
    address(entry.qualified).waiting = entry;
  }
  for (const entry of (mail?.owed ?? []).filter((owed) => !him.has(owed.author))) address(entry.author).owed.push(entry);
  if (keep !== null && !him.has(keep)) address(keep);
  const known = (qualified: string): Address | undefined => addresses.get(qualified);
  for (const entry of mail?.queued ?? []) {
    const found = known(entry.qualified);
    if (found !== undefined) found.queued = entry;
  }
  for (const entry of mail?.last ?? []) {
    const found = known(entry.qualified);
    if (found !== undefined) found.last = Math.max(found.last ?? -Infinity, entry.at);
  }
  const names = new Map<string, Address[]>();
  for (const found of [...addresses.values()].sort((a, b) => byText(a.qualified, b.qualified))) {
    names.set(found.name, [...(names.get(found.name) ?? []), found]);
  }
  return names;
}

/** The rows that wait at an address: the roster's count, else the books'. */
const heldCount = (address: Address): number => address.waiting?.count ?? address.queued?.waiting ?? 0;
/** Mail no session takes: what waits at an address no session holds. */
const unboundMail = (address: Address): number => (address.bound ? 0 : heldCount(address));
/** It holds mail: rows that wait, or, at a bound address, rows in its session. */
const holdsMail = (address: Address): boolean =>
  heldCount(address) > 0 || (address.bound && (address.queued?.handed ?? 0) > 0);

/** The address a row's single facts speak for: the bare address when bound; else the bound
 * address with the newest `bound_at`; else the bare address; else the one with the oldest
 * waiting mail; else the one with the oldest `owed` message. */
export function leadOf(addresses: readonly Address[]): Address {
  const bare = addresses.find((address) => isBare(address.qualified));
  if (bare?.bound) return bare;
  const bound = addresses.filter((address) => address.bound)
    .sort((a, b) => (b.row?.bound_at ?? -Infinity) - (a.row?.bound_at ?? -Infinity) || byText(a.qualified, b.qualified));
  if (bound.length > 0) return bound[0];
  if (bare !== undefined) return bare;
  const mail = addresses.filter((address) => address.waiting !== null)
    .sort((a, b) => (a.waiting as Waiting).since - (b.waiting as Waiting).since || byText(a.qualified, b.qualified));
  if (mail.length > 0) return mail[0];
  const books = addresses.find(holdsMail);
  if (books !== undefined) return books;
  const owed = addresses.filter((address) => address.owed.length > 0)
    .sort((a, b) => a.owed[0].at - b.owed[0].at || byText(a.qualified, b.qualified));
  return owed[0] ?? addresses[0];
}

/** The addresses Home's Comms row names in full: those no session holds with mail waiting,
 * when another such address shares the part before `@`. */
export function namedInFull(roster: PageRoster): Set<string> {
  const bound = new Set(roster.rows.filter((row) => row.live).map((row) => row.qualified));
  const unbound = roster.waiting.filter((entry) => !bound.has(entry.qualified)).map((entry) => entry.qualified);
  return new Set(unbound.filter((qualified) => unbound.filter((other) => nameOf(other) === nameOf(qualified)).length > 1));
}

/** The cases a name meets, highest first. The bound cases are read off the lead alone;
 * `idle` (nothing waiting) is met only when the lead is bound and the name meets no case
 * but `owed`: no mail waits at any of its addresses and the lead is not uncovered, holds no
 * rows and awaits no one. */
function casesOf(addresses: readonly Address[], lead: Address): Case[] {
  const met: Case[] = [];
  if (addresses.some((address) => address.owed.length > 0)) met.push("owed");
  if (addresses.some((address) => unboundMail(address) > 0)) met.push("mail");
  if (lead.bound) {
    if (lead.launched?.uncovered) met.push("uncovered");
    if (heldCount(lead) > 0) met.push("held");
    if ((lead.row?.awaiting.length ?? 0) > 0) met.push("awaiting");
    if (met.filter((kind) => kind !== "owed").length === 0) met.push("idle");
  }
  return met;
}

/** The authors `from` names for some addresses: each one's authors only when the books'
 * waiting rows number the roster's count there, oldest first, each once. */
function fromOf(addresses: readonly Address[]): Author[] {
  const kept = addresses.filter((address) =>
    address.queued !== null && address.waiting !== null && address.queued.waiting === address.waiting.count);
  const ordered = [...kept].sort((a, b) => (a.waiting as Waiting).since - (b.waiting as Waiting).since);
  const seen = new Map<string, Author>();
  for (const author of ordered.flatMap((address) => (address.queued as Queued).from)) {
    if (!seen.has(author.qualified)) seen.set(author.qualified, author);
  }
  return [...seen.values()];
}

interface Words {
  now: number;
  when: (at: number) => string;
  hasCache: boolean;
}

/** A case's fact and line two, for a name whose addresses are `addresses`, whose lead is
 * `lead`, and whose addresses with their own line three are `lined`. */
function wordsOf(kind: Case, addresses: readonly Address[], lead: Address, lined: ReadonlySet<string>,
                 asks: readonly Ask[], words: Words): { fact: Fact | null; line: Line } {
  const { when } = words;
  const cacheFact = (): Fact | null => factOf(lead, words.now, when);
  switch (kind) {
    case "owed": {
      const oldest = Math.min(...addresses.flatMap((address) => address.owed.map((entry) => entry.at)));
      return { fact: { text: "waiting for your reply", heavy: true },
               line: { lead: `wrote to you ${when(oldest)}, wants one reply`, heavy: false, more: null } };
    }
    case "mail": {
      const meeting = addresses.filter((address) => unboundMail(address) > 0);
      const n = meeting.reduce((sum, address) => sum + unboundMail(address), 0);
      const anyBound = addresses.some((address) => address.bound);
      const lead = !anyBound ? "not running"
        : meeting.length === 1 ? `${meeting[0].qualified} isn't running` : `${meeting.length} addresses aren't running`;
      const unlined = meeting.filter((address) => !lined.has(address.qualified));
      const sinces = unlined.flatMap((address) => (address.waiting !== null ? [address.waiting.since] : []));
      const from = fromOf(unlined);
      const parts = [...(sinces.length > 0 ? [`since ${when(Math.min(...sinces))}`] : []),
                     ...(from.length > 0 ? [`from ${authorsWords(from)}`] : [])];
      const more = parts.length > 0 ? parts.join(", ") : null;
      return { fact: { text: `${messages(n)} waiting`, heavy: true }, line: { lead, heavy: true, more } };
    }
    case "uncovered": {
      const launched = lead.launched as Launched;
      return { fact: launched.fact, line: { lead: launched.waits ?? "", heavy: true, more: null } };
    }
    case "held": {
      const n = heldCount(lead);
      const from = lead.waiting !== null && lead.queued !== null && lead.queued.waiting === lead.waiting.count
        ? lead.queued.from : [];
      const since = lead.waiting?.since ?? null;
      let more: string | null = null;
      if (from.length > 0) more = `from ${authorsWords(from)}${since !== null ? ` ${when(since)}` : ""}`;
      else if (since !== null) more = `since ${when(since)}`;
      const held = words.hasCache ? "held until its next turn" : "not read yet";
      return { fact: cacheFact(), line: { lead: `${messages(n)} ${held}`, heavy: false, more } };
    }
    case "awaiting": {
      const awaited = (lead.row as RosterRow).awaiting;
      const found = asks.filter((ask) => ask.asker === lead.qualified && awaited.includes(ask.awaited))
        .sort((a, b) => a.at - b.at);
      const ask = found[0] ?? null;
      const name = nameOf(ask?.awaited ?? awaited[0]);
      return { fact: cacheFact(),
               line: { lead: `waiting on ${name}'s reply${ask !== null ? ` since ${when(ask.at)}` : ""}`, heavy: false,
                       more: ask?.read === false ? `${name} hasn't read it yet` : null } };
    }
    default:
      return { fact: cacheFact(),
               line: { lead: "no mail waiting", heavy: false,
                       more: lead.last !== null ? `last message ${when(lead.last)}` : null } };
  }
}

/** A case's words as one piece of line three: its fact and its line two joined by ` · `. */
function joined(fact: Fact | null, line: Line): string {
  return [fact?.text, line.lead, line.more].filter((part): part is string => !!part).join(" · ");
}

/** The address lines of a row: unbound ones oldest mail first, then bound ones by name. */
function linesOf(addresses: readonly Address[], lead: Address, full: ReadonlySet<string>, words: Words)
  : AddressLine[] {
  const { when } = words;
  const lined = addresses.filter((address) =>
    (address !== lead && (address.bound || holdsMail(address))) || (!address.bound && full.has(address.qualified)));
  const unbound = lined.filter((address) => !address.bound)
    .sort((a, b) => (a.waiting?.since ?? Infinity) - (b.waiting?.since ?? Infinity) || byText(a.qualified, b.qualified));
  const bound = lined.filter((address) => address.bound).sort((a, b) => byText(a.qualified, b.qualified));
  const out: AddressLine[] = [];
  for (const address of unbound) {
    const n = heldCount(address);
    const from = fromOf([address]);
    let text = `${address.qualified} · ${messages(n)}`;
    if (address.waiting !== null) text += ` since ${when(address.waiting.since)}`;
    if (from.length > 0) text += `${address.waiting !== null ? "," : ""} from ${authorsWords(from)}`;
    out.push({ qualified: address.qualified, text });
  }
  for (const address of bound) {
    const parts = [address.qualified];
    const fact = factOf(address, words.now, when);
    if (fact !== null) parts.push(fact.text);
    const n = heldCount(address);
    if (n > 0) parts.push(`${messages(n)} ${words.hasCache ? "held" : "not read yet"}`);
    out.push({ qualified: address.qualified, text: parts.join(" · ") });
  }
  return out;
}

/** The acts of a row, in the order they are drawn. */
function actsOf(kind: Case | null, addresses: readonly Address[], lead: Address): Act[] {
  const message: Act = { kind: "message", to: lead.qualified };
  if (kind === "owed") {
    const oldest = addresses.flatMap((address) => address.owed).sort((a, b) => a.at - b.at)[0];
    return [{ kind: "reply", to: oldest.author, message_id: oldest.message_id }, message];
  }
  if (kind === "uncovered" && lead.launched?.coverable) {
    return [{ kind: "keep-warm", request_id: lead.launched.request_id, title: lead.launched.title ?? "" }, message];
  }
  return [message];
}

/** Line one's dim words for the lead: comms' own words for its binding when its kind says what
 * it is (the connector's), else where launcher says its launch runs; null with neither. */
function whereOf(lead: Address): string | null {
  return lead.row?.via ?? lead.launched?.where ?? null;
}

function rowOf(name: string, addresses: Address[], full: ReadonlySet<string>, asks: readonly Ask[], words: Words)
  : AgentRow {
  const lead = leadOf(addresses);
  const [first = null, second = null] = casesOf(addresses, lead);
  const lines = linesOf(addresses, lead, full, words);
  const lined = new Set(lines.map((line) => line.qualified));
  // A row kept for its open box that meets no case claims nothing yet.
  const top = first === null ? { fact: null, line: { lead: "", heavy: false, more: null } }
    : wordsOf(first, addresses, lead, lined, asks, words);
  let note: string | null = null;
  if (second !== null) {
    const carried = second === "mail"
      && addresses.filter((address) => unboundMail(address) > 0).every((address) => lined.has(address.qualified));
    if (!carried) {
      const next = wordsOf(second, addresses, lead, lined, asks, words);
      note = joined(next.fact, next.line);
    }
  }
  return {
    name, lead: lead.qualified, addresses, case: first, second,
    where: whereOf(lead),
    fact: top.fact, line: top.line,
    note: { second: note, lines },
    acts: actsOf(first, addresses, lead),
  };
}

/** Line three as one text: the second case first, then the address lines, `; `-joined. */
export function noteText(row: AgentRow): string | null {
  const parts = [...(row.note.second !== null ? [row.note.second] : []), ...row.note.lines.map((line) => line.text)];
  return parts.length > 0 ? parts.join("; ") : null;
}

/** Which of the four groups of the order a row is in. */
function groupOf(row: AgentRow): number {
  if (row.case === "owed") return 0;
  if (row.case === "mail") return 1;
  if (row.case === "uncovered") return 2;
  return 3;
}

/** The row's key within its group: the oldest `owed` message, the oldest mail no session
 * takes, the lead's time (uncovered), or the newest message of any address, newest first. */
function keyOf(row: AgentRow): number {
  switch (groupOf(row)) {
    case 0:
      return Math.min(...row.addresses.flatMap((address) => address.owed.map((entry) => entry.at)));
    case 1:
      return Math.min(...row.addresses.filter((address) => unboundMail(address) > 0)
        .map((address) => address.waiting?.since ?? Infinity));
    case 2:
      return 0;
    default: {
      const last = row.addresses.map((address) => address.last).filter((at): at is number => at !== null);
      return last.length > 0 ? -Math.max(...last) : Infinity;
    }
  }
}

/** The order: rows whose case is an `owed` message, oldest first; rows with mail no session
 * takes, oldest first; rows whose lead is in launcher's uncovered case; then the rest by their
 * newest message, newest first, then those with none. Ties by name. */
export function order(rows: readonly AgentRow[]): AgentRow[] {
  return [...rows].sort((a, b) => groupOf(a) - groupOf(b) || keyOf(a) - keyOf(b) || byText(a.name, b.name));
}

/** Who sends under a send-only row's name: its module, `a program` for a name a program
 * registered, or null for one nothing holds. */
function sentBy(row: RosterRow): string | null {
  if (row.holder === "program") return "a program";
  return row.module ?? null;
}

/**
 * The agent rows and `Other names` for a roster that is up and, once read, the mail state.
 * A name none of whose addresses has a session, mail or an `owed` message is no row: its
 * roster addresses go to `Other names`, as does a scoped address with neither mail nor a
 * session whose name has a row. A send-only name is always in `Other names`. The name of
 * `options.keep` is a row whatever it meets.
 */
export function agentsOf(roster: PageRoster, mail: MailState | null, options: Options): Agents {
  const words: Words = { now: options.now, when: options.when ?? LOCAL.time, hasCache: options.hasCache };
  const kept = mail !== null && mail.state === "up" ? mail : null;
  const keep = options.keep ?? null;
  const names = gather(roster, kept, options.launched ?? null, keep);
  const full = namedInFull(roster);
  const rows: AgentRow[] = [];
  const others: OtherName[] = [];
  const other = (address: Address): void => {
    const row = address.row as RosterRow;
    const sendOnly = row.kind === "send-only";
    others.push({ qualified: address.qualified, sentBy: sendOnly ? sentBy(row) : null, writable: !sendOnly });
  };
  for (const [name, all] of names) {
    const sendOnly = all.filter((address) => address.row?.kind === "send-only" && !address.bound
      && !holdsMail(address) && address.owed.length === 0 && address.qualified !== keep);
    const addresses = all.filter((address) => !sendOnly.includes(address));
    sendOnly.forEach(other);
    if (addresses.length === 0) continue;
    const isRow = addresses.some((address) => address.bound || holdsMail(address) || address.owed.length > 0
      || address.qualified === keep);
    if (!isRow) {
      addresses.filter((address) => address.row !== null).forEach(other);
      continue;
    }
    const idle = addresses.filter((address) =>
      !isBare(address.qualified) && address.row !== null && !address.bound && !holdsMail(address)
      && address.owed.length === 0 && address.qualified !== keep);
    const lead = leadOf(addresses);
    const under = addresses.filter((address) => address === lead || !idle.includes(address));
    idle.filter((address) => address !== lead).forEach(other);
    rows.push(rowOf(name, under, full, kept?.asks ?? [], words));
  }
  others.sort((a, b) => byText(a.qualified, b.qualified));
  return { rows: order(rows), others };
}

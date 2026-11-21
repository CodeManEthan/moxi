// What rounds' page says and decides, worked out with no DOM so the Node tests can read
// it: times and ages, each marker's attributes and accessible name, the groups and the
// order they keep while a popover is open, the filters and folds, what waits on him and
// the order the walk takes it in, the addresses both ways, every popover's sections, the
// key, the strip, the foot and the phone's words. The page draws what this returns and
// decides nothing about a step: the board gives every cell, and this only words it. Every
// string goes into the page as text, never as markup. The clock is passed in, so the
// tests read the same words in any zone and locale.

import type {
  Board, Cell, Closed, Column, Entry, Happened, Job, Line, PhrasePart, Recent, RecordValue, Round, Shown,
  Unreadable,
} from "./feed.ts";

// --- times ---------------------------------------------------------------------------------

/** How the page reads a moment (epoch seconds): its time of day as shown, and its day. */
export interface Clock {
  /** The hour and minute. */
  time(at: number): string;
  /** The day, `YYYY-MM-DD`. */
  day(at: number): string;
}

const pad = (n: number): string => String(n).padStart(2, "0");

/** This browser's zone and its own way of writing a time. */
export const LOCAL: Clock = {
  time: (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  day: (at) => {
    const d = new Date(at * 1000);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  },
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** An ISO time as epoch seconds; null when there is none or it can't be read. */
export function epoch(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms / 1000;
}

/** A day (`YYYY-MM-DD`) as `Nov 3`. */
export function dateWord(day: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  return match ? `${MONTHS[Number(match[2]) - 1]} ${Number(match[3])}` : day;
}

/** A moment: its clock time today (`11:40`), else its date and time (`Nov 3 11:40`); `-`
 * when there is none. */
export function timeWord(iso: string | null | undefined, today: string, clock: Clock = LOCAL): string {
  const at = epoch(iso);
  if (at === null) return "-";
  const day = clock.day(at);
  return day === today ? clock.time(at) : `${dateWord(day)} ${clock.time(at)}`;
}

/** How long ago a moment was: `7m` under an hour, `7h` under two days, else `2d`. */
export function ageWord(iso: string | null | undefined, now: number | null): string {
  const at = epoch(iso);
  if (at === null || now === null) return "";
  const minutes = Math.max(0, Math.floor((now - at) / 60));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/** `n thing` or `n things`. */
export function count(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** A commit id cut to seven characters. */
export function shortCommit(commit: string | null | undefined): string {
  return commit ? commit.slice(0, 7) : "-";
}

/** A path in `~` form when it is under `home`. */
export function tildePath(path: string, home: string | null): string {
  if (!home) return path;
  const base = home.replace(/\/+$/, "");
  if (path === base) return "~";
  return path.startsWith(`${base}/`) ? `~${path.slice(base.length)}` : path;
}

// --- columns and markers -------------------------------------------------------------------

/** Where the board's columns stand: the stage label's wide cell sits before the first
 * stage column. */
export interface Layout {
  columns: Column[];
  /** The index of the first stage column, where the stage label goes; the column count
   * when there is none. */
  labelAt: number;
}

export function layoutOf(columns: readonly Column[]): Layout {
  const at = columns.findIndex((one) => one.part === "stage");
  return { columns: [...columns], labelAt: at === -1 ? columns.length : at };
}

/** A marker's key, sent back by the rail: the column, and the stage for a stage step. */
export function markKey(column: string, stage: string | null): string {
  return stage ? `${column}|${stage}` : column;
}

export function parseMarkKey(key: string): { column: string; stage: string | null } {
  const at = key.indexOf("|");
  return at === -1 ? { column: key, stage: null } : { column: key.slice(0, at), stage: key.slice(at + 1) };
}

/** A step's name as the page says it: `check of B1`, `design`, `close`. */
export function stepName(column: Column | string, stage: string | null): string {
  const label = typeof column === "string" ? column : column.label;
  return stage ? `${label} of ${stage}` : label;
}

/** The stage a cell in `column` of `line` belongs to: the line's stage for a stage
 * column, none for a round or end column. */
export function stageOf(column: Column, line: Line | Recent): string | null {
  return column.part === "stage" ? line.stage : null;
}

/** A state in words. */
export function stateWords(cell: Cell): string {
  switch (cell.state) {
    case "done": return "done";
    case "none": return cell.word === "skipped" ? "skipped" : "done, nothing owed";
    case "yours": return cell.count ? `waiting on you, ${cell.count}` : "waiting on you";
    case "failed": return "failed";
    case "owed": return `owed by ${cell.who ? `${cell.by ?? "an agent"} (${cell.who})` : cell.by ?? "an agent"}`;
    case "waiting": return "waiting";
    case "child": return "built in a round of its own";
    case "todo": return "not reached yet";
    default: return cell.state;
  }
}

/** A marker's accessible name: the step, the stage, the round and the state in words
 * (`check of B1, billing-export: failed, red`). */
export function cellLabel(column: Column, stage: string | null, slug: string, cell: Cell): string {
  const words = stateWords(cell);
  const word = cell.word && !words.includes(cell.word) ? `, ${cell.word}` : "";
  return `${stepName(column, stage)}, ${slug}: ${words}${word}`;
}

/** A mark's attributes for one cell; every key is set, null for an attribute left off. */
export function markAttributes(column: Column, stage: string | null, slug: string, cell: Cell | null,
                               { open = false, quiet = false }: { open?: boolean; quiet?: boolean } = {})
  : Record<string, string | null> {
  const flag = (on: boolean): string | null => (on ? "" : null);
  if (cell === null) {
    return { state: null, word: null, count: null, label: null, key: null, settled: null, here: null,
             open: null, quiet: null, wide: null };
  }
  return {
    state: cell.state, word: cell.word, count: cell.count ? String(cell.count) : null,
    label: cellLabel(column, stage, slug, cell), key: markKey(column.id, stage),
    settled: flag(cell.settled), here: flag(cell.here), open: flag(open), quiet: flag(quiet), wide: null,
  };
}

/** The cell a line holds at a column; null where it has none. */
export function cellAt(line: Line | Recent, columns: readonly Column[], column: string): Cell | null {
  const at = columns.findIndex((one) => one.id === column);
  return at === -1 ? null : line.cells[at] ?? null;
}

// --- phrases -------------------------------------------------------------------------------

/** How many of a phrase's parts the board shows. */
export const PHRASE_SHOWN = 3;

/** A phrase's parts as the board shows them: the first three; `nothing owed` for none. */
export function phraseShown(phrase: readonly PhrasePart[]): PhrasePart[] {
  return phrase.length === 0 ? [{ text: "nothing owed", weight: "quiet" }] : phrase.slice(0, PHRASE_SHOWN);
}

/** A phrase as one line of text. */
export function phraseText(phrase: readonly PhrasePart[]): string {
  return phraseShown(phrase).map((one) => one.text).join(", ");
}

// --- groups, filters and folds -------------------------------------------------------------

/** The groups in the order the board draws them. */
export const GROUPS = ["yours", "stuck", "moving", "wrapping"] as const;
export type Group = (typeof GROUPS)[number];

const GROUP_LABELS: Record<Group, string> = {
  yours: "Your turn", stuck: "Stuck or red", moving: "Moving", wrapping: "Wrapping up",
};
const CHIP_LABELS: Record<Group, string> = {
  yours: "Your turn", stuck: "Stuck", moving: "Moving", wrapping: "Wrapping up",
};

export function groupLabel(group: Group): string {
  return GROUP_LABELS[group];
}

/** A filter: every round, or one group's. */
export type Filter = "all" | Group;

/** The filter chips with their counts: `All`, `Your turn 14`, `Stuck 3`. */
export function chips(board: Board): { filter: Filter; label: string }[] {
  const n = (group: Group) => board.rounds.filter((one) => one.group === group).length
    + (group === "stuck" ? board.unreadable.length : 0);
  return [{ filter: "all", label: "All" },
          ...GROUPS.map((group) => ({ filter: group, label: `${CHIP_LABELS[group]} ${n(group)}` }))];
}

/** The projects the picker lists: each with an open round, in name order. */
export function projectsOf(board: Board): string[] {
  return [...new Set(board.rounds.map((one) => one.project))].sort();
}

export const ALL_PROJECTS = "All projects";

/** The open rounds' head: `Open rounds` and how many. */
export function openCount(board: Board): string {
  return String(board.rounds.length + board.unreadable.length);
}

/** One group as drawn: its rounds, and for `stuck` the rounds that can't be read. */
export interface GroupView {
  group: Group;
  label: string;
  rounds: Round[];
  unreadable: Unreadable[];
}

/** The order the board keeps while a popover is open: each group's round names. */
export type Held = { group: Group; names: string[] }[];

const isGroup = (word: string): word is Group => (GROUPS as readonly string[]).includes(word);

/** The groups to draw, with none empty. With `held`, the rounds keep the group and place
 * they had when it was taken, so nothing moves under the pointer: a round's cells are
 * the new value's, a round no longer open leaves, and a round new since goes at the end
 * of its own group. */
export function arrange(board: Board, held: Held | null = null): GroupView[] {
  const byName = new Map(board.rounds.map((one) => [one.name, one]));
  const placed = new Map<Group, Round[]>(GROUPS.map((group) => [group, []]));
  const seen = new Set<string>();
  for (const { group, names } of held ?? []) {
    for (const name of names) {
      const one = byName.get(name);
      if (one && !seen.has(name)) {
        placed.get(group)!.push(one);
        seen.add(name);
      }
    }
  }
  for (const one of board.rounds) {
    if (seen.has(one.name)) continue;
    placed.get(isGroup(one.group) ? one.group : "moving")!.push(one);
  }
  return GROUPS.map((group) => ({ group, label: groupLabel(group), rounds: placed.get(group)!,
                                  unreadable: group === "stuck" ? board.unreadable : [] }))
    .filter((view) => view.rounds.length + view.unreadable.length > 0);
}

/** The order to keep from the groups as drawn. */
export function holdOf(groups: readonly GroupView[]): Held {
  return groups.map((view) => ({ group: view.group, names: view.rounds.map((one) => one.name) }));
}

/** Whether a round shows under the filter and the project picked (null for all). */
export function isShown(round: Round, filter: Filter, project: string | null): boolean {
  return (filter === "all" || round.group === filter) && (project === null || round.project === project);
}

/** Whether an unreadable round shows: under `All` and `Stuck`, and under a project pick
 * only when its name is in that project. */
export function isUnreadableShown(one: Unreadable, filter: Filter, project: string | null): boolean {
  return (filter === "all" || filter === "stuck")
    && (project === null || one.name === project || one.name.startsWith(`${project}/`));
}

/** The filter and project that show `round`: each one that hides it set to the value
 * that shows it, the other left alone. */
export function revealing(round: Round, filter: Filter, project: string | null)
  : { filter: Filter; project: string | null } {
  return { filter: filter === "all" || filter === round.group ? filter : "all",
           project: project === null || project === round.project ? project : null };
}

/** A fold's key in the set of what is unfolded. */
export function foldKey(name: string, fold: string): string {
  return `${name}|${fold}`;
}

/** The key that closes the closed rounds' fold. */
export const CLOSED_FOLD = "closed";

/** The lines a round draws, given what is unfolded. */
export function linesShown(round: Round, unfolded: ReadonlySet<string>): Line[] {
  return round.lines.filter((line) => line.fold === null || unfolded.has(foldKey(round.name, line.fold)));
}

/** The `+N` beside the lead stage's label, and its accessible name; null with nothing
 * folded under it. */
export function moreLabel(round: Round, unfolded: ReadonlySet<string>): { text: string; label: string } | null {
  const n = round.folds.more;
  if (n === 0) return null;
  const open = unfolded.has(foldKey(round.name, "more"));
  return { text: open ? "−" : `+${n}`,
           label: open ? `Fold ${count(n, "other stage")} of ${round.slug}`
                       : `Show ${count(n, "other stage")} of ${round.slug}` };
}

/** How many markers a fold's small rail draws before `+N`. */
export const FOLD_MARKS = 14;

/** The folds under a round with more than four other stages: `7 builds landed` and `31
 * stages not started: 14 ready to start, 9 held by questions, 9 built in child rounds`,
 * each with the cells of its small rail (at most 14) and how many more there are. */
export function foldsOf(round: Round, columns: readonly Column[])
  : { fold: string; text: string; cells: Cell[]; more: number }[] {
  const made = [];
  const cellsOf = (fold: string, column: string) => {
    const all = round.lines.filter((line) => line.fold === fold)
      .map((line) => cellAt(line, columns, column)).filter((one): one is Cell => one !== null);
    return { cells: all.slice(0, FOLD_MARKS), more: Math.max(0, all.length - FOLD_MARKS) };
  };
  const { landed, not_started: later } = round.folds;
  if (landed > 0) {
    const doneMarks = Array.from({ length: Math.min(landed, FOLD_MARKS) },
                                 (): Cell => ({ state: "done", word: null, count: null, settled: true, here: false,
                                                step_state: "done", by: null, who: null }));
    made.push({ fold: "landed", text: count(landed, "build landed", "builds landed"), cells: doneMarks,
                more: Math.max(0, landed - FOLD_MARKS) });
  }
  if (later.n > 0) {
    const parts = [later.ready ? `${later.ready} ready to start` : "",
                   later.held ? `${later.held} held by questions` : "",
                   later.child ? `${later.child} built in child rounds` : ""].filter(Boolean);
    const head = count(later.n, "stage not started", "stages not started");
    made.push({ fold: "not-started", text: parts.length ? `${head}: ${parts.join(", ")}` : head,
                ...cellsOf("not-started", "commission") });
  }
  return made;
}

/** The closed rounds' fold: `20 closed rounds`. */
export function closedFoldText(closed: readonly Closed[]): string {
  return count(closed.length, "closed round");
}

/** A closed round's words: `closed Nov 20`, with how it ended when that wasn't a close. */
export function closedWords(one: Closed, clock: Clock = LOCAL): string {
  const at = epoch(one.ended_at);
  const when = at === null ? "closed" : `closed ${dateWord(clock.day(at))}`;
  return one.how && one.how !== "closed" ? `${when}, ${one.how}` : when;
}

/** The slug of a round's name (`project/slug`). */
export function slugOf(name: string): string {
  const at = name.indexOf("/");
  return at === -1 ? name : name.slice(at + 1);
}

/** Whether the board names more than one project, so a round line says its project. */
export function isManyProjects(board: Board): boolean {
  return new Set(board.rounds.map((one) => one.project)).size > 1;
}

/** An unreadable round's words: its name, what the page says of it, and rounds' reason
 * and fix where the phrase goes. */
export const UNREADABLE_LEAD = "Moxi can't read this round";
export function unreadableWords(one: Unreadable): string {
  return one.fix ? `${one.reason} ${one.fix}` : one.reason;
}

// --- what waits on him ---------------------------------------------------------------------

/** The four kinds of his items, as the address and the record route name them. */
export type ItemKind = "question" | "decision" | "wait" | "shown";

/** One item of his, with where it is drawn. */
export interface Item {
  kind: ItemKind;
  /** The round (`<project>/<slug>`) or the project that holds it. */
  holder: string;
  id: string;
  text: string | null;
  /** The open round whose marker it opens at, or null for a project-level item. */
  round: string | null;
}

/** The column whose marker an item opens at. */
export const ITEM_COLUMN: Record<ItemKind, string> = {
  question: "docket", shown: "docket", decision: "ratify", wait: "close",
};

const ITEM_ORDER: ItemKind[] = ["question", "shown", "decision", "wait"];

/** Every item of his, in the walk's order: the rounds in the board's order, and in each
 * its questions, its rulings to read back, its decisions and its jobs; then the items of
 * a project, or of a round not on the board, in that order by kind. */
export function itemsOf(board: Board, order: readonly string[] = board.rounds.map((one) => one.name)): Item[] {
  const open = new Set(board.rounds.map((one) => one.name));
  const all: Item[] = [];
  const add = (kind: ItemKind, holder: string, id: string, text: string | null) =>
    all.push({ kind, holder, id, text, round: open.has(holder) ? holder : null });
  board.waiting.questions.forEach((one: Entry) => add("question", one.holder, one.id, one.text));
  board.waiting.not_shown.forEach((back) => back.rows.forEach((one: Shown) =>
    add("shown", one.holder, one.id, one.text)));
  board.waiting.decisions.forEach((one: Entry) => add("decision", one.holder, one.id, one.text));
  board.waiting.waits.forEach((one: Job) => add("wait", one.round, one.id, one.text));
  const place = new Map(order.map((name, at) => [name, at]));
  const rank = (item: Item) => (item.round !== null && place.has(item.round) ? place.get(item.round)! : order.length);
  return all.map((item, at) => ({ item, at }))
    .sort((a, b) => rank(a.item) - rank(b.item)
           || ITEM_ORDER.indexOf(a.item.kind) - ITEM_ORDER.indexOf(b.item.kind) || a.at - b.at)
    .map(({ item }) => item);
}

/** The waiting line's counts, each a number and its words, in order; a kind with none
 * is left out: `38` `questions in 10 rounds`, `4` `decisions`, `5` `jobs`, `3` `rulings to
 * read back`. */
export function waitingCounts(board: Board): { n: string; words: string }[] {
  const items = itemsOf(board);
  const of = (kind: ItemKind) => items.filter((one) => one.kind === kind);
  const questions = of("question");
  const rounds = new Set(questions.map((one) => one.holder)).size;
  const made = [];
  if (questions.length) {
    made.push({ n: String(questions.length),
                words: `${questions.length === 1 ? "question" : "questions"} in ${count(rounds, "round")}` });
  }
  const decisions = of("decision").length;
  if (decisions) made.push({ n: String(decisions), words: decisions === 1 ? "decision" : "decisions" });
  const jobs = of("wait").length;
  if (jobs) made.push({ n: String(jobs), words: jobs === 1 ? "job" : "jobs" });
  const back = of("shown").length;
  if (back) made.push({ n: String(back), words: back === 1 ? "ruling to read back" : "rulings to read back" });
  return made;
}

export const WAITING_LEAD = "Waiting on you";
export const NOTHING_WAITS = "Nothing waits on you.";
export const WALK_BUTTON = "Go through in order";

/** The phone's waiting line: `50 wait on you in 14 rounds`. */
export function phoneWaiting(board: Board): string {
  const items = itemsOf(board);
  if (items.length === 0) return NOTHING_WAITS;
  const holders = new Set(items.map((one) => one.holder)).size;
  return `${items.length} ${items.length === 1 ? "waits" : "wait"} on you in ${count(holders, "round")}`;
}

/** The walk's foot: `1 of 38`. */
export function walkFoot(at: number, n: number): string {
  return `${at + 1} of ${n}`;
}

/** Where the walk goes from `at` by `step` (1 or -1); it stops at either end. */
export function walkStep(at: number, step: number, n: number): number {
  return Math.min(Math.max(at + step, 0), Math.max(n - 1, 0));
}

// --- addresses -----------------------------------------------------------------------------

/** What the page has open, as its address names it. */
export type Address =
  | { kind: "round"; round: string }
  | { kind: "step"; round: string; column: string; stage: string | null }
  | { kind: ItemKind; holder: string; id: string }
  | { kind: "walk"; item: { kind: ItemKind; holder: string; id: string } | null }
  | { kind: "key" };

const ITEM_KINDS: readonly string[] = ["question", "decision", "wait", "shown"];
const enc = encodeURIComponent;

function holderSegments(holder: string): string {
  const at = holder.indexOf("/");
  return at === -1 ? `${enc(holder)}/-` : `${enc(holder.slice(0, at))}/${enc(holder.slice(at + 1))}`;
}

/** The address of what is open, each segment encoded; `""` for nothing open. */
export function addressRoute(address: Address | null): string {
  if (address === null) return "";
  switch (address.kind) {
    case "round": return `round/${holderSegments(address.round)}`;
    case "step": return `step/${holderSegments(address.round)}/${enc(address.column)}`
      + (address.stage ? `/${enc(address.stage)}` : "");
    case "walk": return address.item ? `walk/${addressRoute(address.item)}` : "walk";
    case "key": return "key";
    default: return `${address.kind}/${holderSegments(address.holder)}/${enc(address.id)}`;
  }
}

/** The address a route names, or null for one that names nothing the page opens. */
export function parseAddress(route: string): Address | null {
  const parts = route.replace(/^\/+|\/+$/g, "").split("/");
  let words: string[];
  try {
    words = parts.map((one) => decodeURIComponent(one));
  } catch {
    return null;
  }
  const holder = (project: string, slug: string) => (slug === "-" ? project : `${project}/${slug}`);
  const [kind, project, slug, ...rest] = words;
  if (kind === "key" && words.length === 1) return { kind: "key" };
  if (kind === "walk") {
    if (words.length === 1) return { kind: "walk", item: null };
    const item = parseAddress(parts.slice(1).join("/"));
    return item && ITEM_KINDS.includes(item.kind) ? { kind: "walk", item: item as Extract<Address, { id: string }> }
                                                  : null;
  }
  if (!project || !slug) return null;
  if (kind === "round" && rest.length === 0 && slug !== "-") return { kind: "round", round: `${project}/${slug}` };
  if (kind === "step" && slug !== "-" && (rest.length === 1 || rest.length === 2) && rest[0]) {
    return { kind: "step", round: `${project}/${slug}`, column: rest[0], stage: rest[1] || null };
  }
  if (ITEM_KINDS.includes(kind) && rest.length === 1 && rest[0]) {
    return { kind: kind as ItemKind, holder: holder(project, slug), id: rest[0] };
  }
  return null;
}

/** The record route's query for what an address names; null for the walk and the key. */
export function recordQuery(address: Address): string | null {
  const query = (pairs: Record<string, string | null>) => Object.entries(pairs)
    .filter(([, value]) => value).map(([key, value]) => `${key}=${enc(value!)}`).join("&");
  switch (address.kind) {
    case "round": return `api/record?${query({ what: "card", round: address.round })}`;
    case "step": return `api/record?${query({ what: "step", round: address.round, column: address.column,
                                               stage: address.stage })}`;
    case "walk": case "key": return null;
    default: return `api/record?${query({ what: address.kind, round: address.holder, id: address.id })}`;
  }
}

const ITEM_NOUNS: Record<ItemKind, string> = {
  question: "Question", decision: "Decision", wait: "Job", shown: "Ruling",
};

/** What an address names, for the foot when it is no longer open:
 * `<what> is no longer open`. */
export function noLongerOpen(address: Address): string {
  switch (address.kind) {
    case "round": return `${address.round} is no longer open`;
    case "step": return `${stepName(address.column, address.stage)} on ${address.round} is no longer open`;
    case "walk": return "Nothing waits on you; the walk is no longer open";
    case "key": return "The key is no longer open";
    default: return `${ITEM_NOUNS[address.kind]} ${address.id} of ${address.holder} is no longer open`;
  }
}

/** Whether an address still names something the board holds. A step's round is open,
 * a card's round is open or closed, and an item is waiting. */
export function isOpenNow(address: Address, board: Board): boolean {
  const items = itemsOf(board);
  const hasItem = (kind: ItemKind, holder: string, id: string) =>
    items.some((one) => one.kind === kind && one.holder === holder && one.id === id);
  switch (address.kind) {
    case "round": return board.rounds.some((one) => one.name === address.round)
      || board.closed.some((one) => one.name === address.round);
    case "step": return board.rounds.some((one) => one.name === address.round)
      || board.recent.some((one) => one.round === address.round && one.stage === address.stage);
    case "walk": return items.length > 0 && (address.item === null
      || hasItem(address.item.kind, address.item.holder, address.item.id));
    case "key": return true;
    default: return hasItem(address.kind, address.holder, address.id);
  }
}

/** An item's address. */
export function itemAddress(item: Item): Address {
  return { kind: item.kind, holder: item.holder, id: item.id };
}

/** Where an item opens on the board: its round's round line and the column of its
 * marker; null for a project-level item, which opens under the waiting line. */
export function itemMarker(item: Pick<Item, "kind" | "round">): { round: string; key: string } | null {
  return item.round === null ? null : { round: item.round, key: markKey(ITEM_COLUMN[item.kind], null) };
}

// --- popovers ------------------------------------------------------------------------------

/** A piece of a popover's line: text, in mono for a command, path or commit; `tone`
 * for a done or failed word or a dim one; `open` an address it opens; `session` a Live
 * sessions row it links to. */
export interface Piece {
  text: string;
  mono?: boolean;
  tone?: "done" | "failed" | "dim" | "strong";
  open?: Address;
  session?: string;
}
export type Row = Piece[];

/** A section of a popover: its head, its lines, the lines folded behind a button, and
 * commands in a mono well. */
export interface Section {
  head: string | null;
  rows: Row[];
  folds: { label: string; rows: Row[] }[];
  wells: string[];
}

const section = (head: string | null, rows: Row[] = [], folds: Section["folds"] = [],
                 wells: string[] = []): Section => ({ head, rows, folds, wells });

/** How many events a popover shows before `All N events`. */
export const EVENTS_SHOWN = 4;
/** How many pending CI jobs show before they fold. */
export const PENDING_SHOWN = 3;

/** What the page needs to word a record. */
export interface Context {
  today: string;
  home: string | null;
  now: number | null;
  clock?: Clock;
  /** A Live sessions row whose folder is this worktree, when exactly one is. */
  sessionAt?: (worktree: string) => string | null;
  /** The kind of one of his items in a round, by its id. */
  kindOf?: (round: string, id: string) => ItemKind | null;
}

function eventsSection(events: readonly Happened[], ctx: Context): Section | null {
  if (events.length === 0) return null;
  const row = (one: Happened): Row => [
    { text: timeWord(one.at, ctx.today, ctx.clock), mono: true, tone: "dim" },
    { text: [one.type ?? "event", one.of].filter(Boolean).join(" ") },
    ...(one.by ? [{ text: one.by, tone: "dim" as const }] : []),
  ];
  const cut = Math.max(0, events.length - EVENTS_SHOWN);
  return section("What happened", events.slice(cut).map(row),
                 cut ? [{ label: `All ${events.length} events`, rows: events.slice(0, cut).map(row) }] : []);
}

const CI_ORDER = ["red", "pending", "green"];

function ciSection(ci: NonNullable<RecordValue["ci"]>, ctx: Context): Section {
  const head = `CI at ${shortCommit(ci.commit)}, read ${timeWord(ci.at, ctx.today, ctx.clock)}: ${ci.reading ?? "not read"}`;
  const jobs = [...ci.jobs].sort((a, b) => CI_ORDER.indexOf(a.reading ?? "") - CI_ORDER.indexOf(b.reading ?? ""));
  const row = (job: (typeof jobs)[number]): Row => [
    { text: job.reading ?? "-", tone: job.reading === "red" ? "failed" : job.reading === "green" ? "done" : "dim" },
    { text: job.name ?? job.workflow ?? "a job" },
  ];
  const shown = jobs.filter((one) => one.reading !== "green" && one.reading !== "pending").map(row);
  const pending = jobs.filter((one) => one.reading === "pending");
  const green = jobs.filter((one) => one.reading === "green");
  const folds = [];
  if (pending.length > PENDING_SHOWN) folds.push({ label: `${count(pending.length, "job")} pending`, rows: pending.map(row) });
  else shown.push(...pending.map(row));
  if (green.length) folds.push({ label: `${count(green.length, "green job")}`, rows: green.map(row) });
  return section(head, shown, folds);
}

function runRows(run: NonNullable<RecordValue["runs"]>[number], ctx: Context): Row[] {
  const passed = run.exit_code === 0;
  const what = run.exit_code === null ? "ran" : passed ? "passed" : `failed (exit ${run.exit_code})`;
  const took = run.duration_s === null ? "" : ` in ${Math.round(run.duration_s)} s`;
  const rows: Row[] = [[
    { text: `${run.step ?? "check"} ` },
    { text: what, tone: run.exit_code === null ? undefined : passed ? "done" : "failed" },
    { text: `${took} at ` }, { text: shortCommit(run.commit), mono: true },
    { text: `, ${timeWord(run.at, ctx.today, ctx.clock)}` },
  ]];
  if (run.output) rows.push([{ text: tildePath(run.output, ctx.home), mono: true, tone: "dim" }]);
  return rows;
}

function findingsWords(findings: { blocker: number; major: number; minor: number; note: number }): string {
  const parts = (["blocker", "major", "minor", "note"] as const)
    .filter((key) => findings[key] > 0).map((key) => `${findings[key]} ${key}`);
  return parts.length ? parts.join(", ") : "no findings";
}

function verdictRows(verdict: NonNullable<RecordValue["verdicts"]>[number], ctx: Context): Row[] {
  const head: Row = [
    { text: verdict.verdict ?? "no verdict",
      tone: verdict.verdict === "fold-first" ? "failed" : verdict.verdict === "publishable" ? "done" : "strong" },
    { text: [verdict.version !== null ? ` on v${verdict.version}` : "",
             verdict.pass ? `, pass ${verdict.pass}` : "",
             verdict.at ? `, ${timeWord(verdict.at, ctx.today, ctx.clock)}` : "",
             verdict.by ? `, by ${verdict.by}` : "",
             verdict.model ? ` (${verdict.model})` : ""].join("") },
  ];
  const rows: Row[] = [head, [{ text: `Findings: ${findingsWords(verdict.findings)}`
    + (verdict.open ? `; ${verdict.open} open` : "") }]];
  if (verdict.report) rows.push([{ text: verdict.report, mono: true, tone: "dim" }]);
  return rows;
}

const SEVERITY_ORDER = ["blocker", "major", "minor", "note"];

function factRow(fact: { key: string; value: string }, ctx: Context, facts: readonly { key: string; value: string }[])
  : Row {
  const mono = ["worktree", "branch", "target", "head"].includes(fact.key);
  const value: Piece = { text: fact.key === "worktree" ? tildePath(fact.value, ctx.home) : fact.value, mono };
  if (fact.key === "builder" && ctx.sessionAt) {
    const worktree = facts.find((one) => one.key === "worktree")?.value;
    const session = worktree ? ctx.sessionAt(worktree) : null;
    if (session) value.session = session;
  }
  return [{ text: `${fact.key} `, tone: "dim" }, value];
}

/** A step record's sections, in the record's order: facts, CI, checks run, verdicts, open
 * findings, drift, finish gates, decisions, his items, settled questions, fixes owed,
 * what the close waits for, the next step, and what happened. */
export function stepSections(record: RecordValue, ctx: Context): Section[] {
  const made: (Section | null)[] = [];
  const round = record.round ?? "";
  if (record.facts?.length) made.push(section(null, record.facts.map((one) => factRow(one, ctx, record.facts!))));
  if (record.ci) made.push(ciSection(record.ci, ctx));
  if (record.runs?.length) made.push(section("Checks run", record.runs.flatMap((one) => runRows(one, ctx))));
  if (record.verdicts?.length) {
    made.push(section(record.verdicts.length === 1 ? "Verdict" : "Verdicts",
                      record.verdicts.flatMap((one) => verdictRows(one, ctx))));
  }
  const findings = record.findings;
  if (findings && (findings.open.length || findings.rest)) {
    const open = [...findings.open].sort((a, b) =>
      SEVERITY_ORDER.indexOf(a.severity ?? "note") - SEVERITY_ORDER.indexOf(b.severity ?? "note"));
    const row = (one: (typeof open)[number]): Row => [
      { text: `${one.id} `, mono: true },
      { text: `${one.severity ?? "finding"}: `, tone: one.severity === "blocker" || one.severity === "major" ? "failed" : "strong" },
      { text: one.text ?? "" },
    ];
    const rows = open.map(row);
    if (findings.rest) rows.push([{ text: `and ${findings.rest} not open`, tone: "dim" }]);
    made.push(section(open.length ? `${count(open.length, "open finding")}` : "No open finding", rows));
  }
  const drift = record.drift;
  if (drift) {
    const rows: Row[] = [];
    if (drift.why) rows.push([{ text: drift.why }]);
    if (drift.moved !== null) rows.push([{ text: `The target moved ${count(drift.moved, "commit")}.` }]);
    if (drift.meeting.length) rows.push([{ text: "Both changed: ", tone: "dim" }, { text: drift.meeting.join(", "), mono: true }]);
    if (drift.shared.length) rows.push([{ text: "Shared: ", tone: "dim" }, { text: drift.shared.join(", "), mono: true }]);
    made.push(section(drift.owed ? "Drift: a re-check is owed" : "Drift", rows));
  }
  const gates = record.gates;
  if (gates && (gates.unmet.length || gates.actions.length)) {
    const rows: Row[] = gates.unmet.flatMap((one) => {
      const row: Row[] = [[{ text: one.number !== null ? `Gate ${one.number}: ` : "", tone: "strong" }, { text: one.reason }]];
      if (one.fix) row.push([{ text: one.fix, mono: true, tone: "dim" }]);
      return row;
    });
    rows.push(...gates.actions.map((one): Row => [
      { text: `${one.kind ?? "action"} ` }, { text: one.state ?? "", tone: "dim" },
      ...(one.landing ? [{ text: ` ${one.landing}`, mono: true }] : []),
    ]));
    made.push(section(gates.unmet.length ? `Finish gates: ${gates.unmet.length} unmet` : "Finish", rows));
  }
  if (record.decisions) {
    const { n, majors, unruled } = record.decisions;
    made.push(section("Decisions", [[{ text: `${count(n, "decision")}, ${count(majors, "major")}, ${unruled} unruled` }]]));
  }
  if (record.items?.length) {
    made.push(section("Waiting on you", record.items.map((id): Row => {
      const kind = ctx.kindOf?.(round, id) ?? null;
      return [kind ? { text: id, open: { kind, holder: round, id } } : { text: id }];
    })));
  }
  if (record.settled?.length) {
    made.push(section("Settled", record.settled.map((one): Row => [
      { text: `${one.id} `, tone: "strong" }, { text: one.text ?? "" },
      ...(one.chose ? [{ text: `; chose ${one.chose}`, tone: "dim" as const }] : []),
      ...(one.ruling ? [{ text: `. ${one.ruling}`, tone: "dim" as const }] : []),
    ])));
  }
  if (record.fixes?.length) {
    made.push(section("Fixes owed", record.fixes.map((one): Row => [
      { text: one.id, mono: true }, { text: `, owed since ${timeWord(one.since, ctx.today, ctx.clock)}`, tone: "dim" },
    ])));
  }
  if (record.close?.length) made.push(section("The close waits for", record.close.map((one): Row => [{ text: one }])));
  if (record.next) made.push(nextSection(record, ctx));
  made.push(eventsSection(record.events ?? [], ctx));
  return made.filter((one): one is Section => one !== null);
}

function nextSection(record: RecordValue, _ctx: Context): Section {
  const next = record.next!;
  const who = next.by ? `, owed by ${next.by}${next.who ? ` (${next.who})` : ""}` : "";
  const head = `Next: ${stepName(record.column ?? "", record.stage ?? null)}, ${next.state}${who}`;
  const rows: Row[] = next.waits.flatMap((one) => {
    const row: Row[] = [[{ text: "Waits: ", tone: "dim" }, { text: one.reason }]];
    if (one.fix) row.push([{ text: one.fix, mono: true, tone: "dim" }]);
    return row;
  });
  if (next.questions.length) rows.push([{ text: `Held by ${next.questions.join(", ")}`, tone: "dim" }]);
  return section(head, rows, [], next.then);
}

/** A step popover's head: the step and stage, then the round and the state in words. */
export function stepHead(record: RecordValue, cell: Cell | null): { title: string; sub: string } {
  const name = record.round ?? "";
  const state = cell ? stateWords(cell) : record.state ?? "";
  const word = record.word && !state.includes(record.word) ? `, ${record.word}` : "";
  return { title: stepName(record.column ?? "", record.stage ?? null), sub: `${name}, ${state}${word}` };
}

/** A round's card: its head and sections. */
export function cardView(record: RecordValue, ctx: Context): { title: string; sub: string; sections: Section[] } {
  const facts: Row[] = [];
  const fact = (key: string, value: string | null | undefined, mono = false) => {
    if (value) facts.push([{ text: `${key} `, tone: "dim" }, { text: value, mono }]);
  };
  fact("flow", record.flow);
  fact("opened", record.opened ? timeWord(record.opened, ctx.today, ctx.clock) : null);
  fact("standing", record.standing);
  const stages = record.stages;
  if (stages) {
    fact("stages", [`${stages.finished} finished`, `${stages.building} building`,
                    `${stages.not_started} not started`].join(", "));
  }
  if (record.rulings !== undefined) fact("rulings", String(record.rulings));
  const made: Section[] = [];
  if (record.description) made.push(section(null, [[{ text: record.description }]]));
  made.push(section(null, facts));
  if (record.ended) {
    made.push(section(`Ended ${timeWord(record.ended.at, ctx.today, ctx.clock)}, ${record.ended.how ?? "closed"}`,
                      (record.ends ?? []).map((one): Row => [{ text: `${one.stage} `, tone: "strong" },
                                                             { text: one.how, tone: one.how === "landed" ? "done" : "dim" }])));
  } else if (record.phrase?.length) {
    made.push(section("Where it is", [record.phrase.map((one): Piece => ({
      text: one === record.phrase![record.phrase!.length - 1] ? one.text : `${one.text}, `,
      tone: one.weight === "bad" ? "failed" : one.weight === "yours" ? "strong" : undefined }))]));
  }
  if (record.newest_rulings?.length) {
    made.push(section("Newest rulings", record.newest_rulings.map((one): Row => [
      { text: `${one.id} `, tone: "strong" }, { text: one.text ?? "" },
      { text: ` ${timeWord(one.at, ctx.today, ctx.clock)}`, tone: "dim" },
    ])));
  }
  const events = eventsSection(record.events ?? [], ctx);
  if (events) made.push(events);
  return { title: slugOf(record.name ?? ""), sub: record.project ?? "", sections: made };
}

/** What a decision is, in one line. */
export const DECISION_IS = "A decision the build made that its design didn't, for you to accept or reject.";
export const FORECLOSES = "It closes off a capability.";
export const HOLDS_CLOSE = "It holds the round's close.";
export const ANSWER_LEAD = "To answer: tell the round's lead. The lead records it with";
export const READ_PASSAGE = "Read the whole passage";
export const PASSAGE_LINES = 7;

/** A waiting item's popover: its head, its text, its lines, its options, its passage and
 * the command that records an answer. */
export interface ItemView {
  title: string;
  sub: string;
  text: string;
  lines: string[];
  options: { key: string; text: string }[];
  passage: { from: string; text: string } | null;
  answer: string;
}

/** Where a pointer points, as a path: `file:versions/design-v3.md#Q2` reads
 * `versions/design-v3.md`. */
export function pointerPath(pointer: string): string {
  return pointer.replace(/^file:/, "").replace(/#.*$/, "");
}

/** The stages a question holds, in words: `Holds B1`, `Holds every stage`; empty when it
 * holds none. */
export function holdsWords(item: { blocks?: string[]; blocks_word?: string | null }): string {
  if (item.blocks_word === "all") return "Holds every stage";
  if (item.blocks?.length) return `Holds ${item.blocks.join(", ")}`;
  return "";
}

export function itemView(record: RecordValue, ctx: Context): ItemView {
  const item = record.item ?? ({} as NonNullable<RecordValue["item"]>);
  const kind = (record.what ?? "question") as ItemKind;
  const holder = item.holder ?? item.round ?? "";
  const asked = (verb: string) => (item.at ? `${verb} ${ageWord(item.at, ctx.now)} ago` : "");
  const lines: string[] = [];
  let options: ItemView["options"] = [];
  let passage: ItemView["passage"] = null;
  if (kind === "question") {
    lines.push([holdsWords(item), asked("asked")].filter(Boolean).join("; ") + ".");
    options = (item.options ?? []).map((one) => ({ key: one.key, text: one.text }));
    if (record.passage && item.from) passage = { from: `From ${pointerPath(item.from)}`, text: record.passage };
  } else if (kind === "decision") {
    lines.push(DECISION_IS);
    const from = [item.from ? `From ${item.from}` : "", item.by ? `found by ${item.by}` : "",
                  item.stage ? `on ${item.stage}` : ""].filter(Boolean).join(", ");
    if (from) lines.push(`${from}.`);
    if (item.forecloses) lines.push(FORECLOSES);
    options = (item.options ?? []).map((one) => ({ key: one.key, text: one.text }));
  } else if (kind === "wait") {
    const from = [item.by ? `Asked by ${item.by}` : "Asked", item.report ? `in ${item.report}` : "",
                  item.at ? timeWord(item.at, ctx.today, ctx.clock) : ""].filter(Boolean).join(" ");
    lines.push(`${from}.`, HOLDS_CLOSE);
  } else {
    if (item.record) lines.push(`Recorded ${item.record}.`);
    if (item.at) lines.push(`Ruled ${timeWord(item.at, ctx.today, ctx.clock)}.`);
  }
  const noun = kind === "shown" ? `Ruling ${item.id}, to read back` : `${ITEM_NOUNS[kind]} ${item.id}`;
  return { title: noun, sub: holder.includes("/") ? slugOf(holder) : `${holder}, the project`,
           text: item.text ?? "", lines: lines.filter((one) => one !== "."), options, passage,
           answer: record.answer ?? "" };
}

// --- the key -------------------------------------------------------------------------------

/** The key's lines: each state's marker and its words, then what `▼` means and the
 * columns. */
export const KEY_STATES: readonly { state: string; words: string }[] = [
  { state: "done", words: "Done. The solid line runs through what is done, up to where the round is." },
  { state: "none", words: "Done with nothing owed, or skipped." },
  { state: "yours", words: "Waiting on you, with how many. Opens the question, decision or job." },
  { state: "failed", words: "Failed or sent back: a red check, red CI, a fold-first." },
  { state: "owed", words: "Owed by an agent; the record names which one." },
  { state: "waiting", words: "Waiting on something else first: a question, another stage, a gate." },
  { state: "todo", words: "Not reached yet." },
  { state: "child", words: "Built in a round of its own." },
];
export const KEY_HERE = "▼ marks where a line is: on a build's line, its stage's next step; on the round line, "
  + "the round's own step and its lead stage's next step, which can be two.";
export const KEY_COLUMNS = "Columns: the round's own steps, each stage's steps, then ratify, retro and the close. "
  + "One line per build in flight; landed and unstarted stages are folded.";
export const KEY_TITLE = "Key";

// --- the strip -----------------------------------------------------------------------------

export const STRIP_HEAD = "Last acted on";
export const STRIP_CAPTION = "the ten builds with the newest recorded step, all projects; not live activity";
export const STRIP_EMPTY = "No build is on record yet.";
export const SHOW_LINE = "Show its line on the board";

/** The step a strip card opens: its stage's first step not settled, else its last. */
export function stripStep(entry: Recent): string | null {
  const steps = entry.steps.map((id, at) => ({ id, cell: entry.cells[at] ?? null }))
    .filter((one) => one.cell !== null);
  if (steps.length === 0) return null;
  return (steps.find((one) => !one.cell!.settled) ?? steps[steps.length - 1]).id;
}

/** A strip card's words: the slug and stage, its age, and the act. */
export function stripCard(entry: Recent, now: number | null): { slug: string; stage: string; age: string; act: string;
                                                                   label: string } {
  const slug = slugOf(entry.round);
  const age = ageWord(entry.act_at, now);
  return { slug, stage: entry.stage, age, act: entry.act,
           label: `${slug} ${entry.stage}, ${entry.act}${age ? `, ${age} ago` : ""}` };
}

/** A strip card's tabs: each of its stage's steps, with its cell. */
export function stripTabs(entry: Recent, columns: readonly Column[]): { column: Column; cell: Cell | null }[] {
  return entry.steps.map((id, at) => ({
    column: columns.find((one) => one.id === id) ?? { id, label: id, part: "stage" },
    cell: entry.cells[at] ?? null,
  }));
}

// --- the page's states and foot ------------------------------------------------------------

export const READING = "Reading…";
/** How long the page waits before it says `Reading…`. */
export const READING_AFTER_MS = 400;
export const CANT_READ = "Moxi can't read rounds";
export const EMPTY_HEAD = "No round is open";
export const EMPTY_TEXT = "A round opens when a lead runs moxi rounds open on your go. Its rail appears here at once, "
  + "with the design's steps and a line per build.";
export const OPEN_ROUNDS = "Open rounds";

/** The foot: `Read 11:40`, the parked questions, and the last read's failure while the
 * board shown is an older one. */
export function footWords(board: Board | null, readAt: number | null, failed: string | null, today: string,
                          clock: Clock = LOCAL): string[] {
  const made: string[] = [];
  if (readAt !== null) {
    const at = new Date(readAt * 1000).toISOString();
    made.push(failed ? `Read ${timeWord(at, today, clock)}; the last read failed: ${failed}`
                     : `Read ${timeWord(at, today, clock)}`);
  }
  if (board && board.waiting.parked > 0) made.push(count(board.waiting.parked, "parked question"));
  return made;
}

// --- placing a popover ---------------------------------------------------------------------

export interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

/** How far a popover stands off its marker, and off the window's edge, in px. */
export const GAP_PX = 6;

/** Where a popover goes, in the window's coordinates: under its marker's line, else over
 * it, else beside the marker, never over the line itself; kept inside the window's width.
 * `line` is the marker's line; `size` the popover's. */
export function placePopover(marker: Box, line: Box, size: { width: number; height: number },
                             view: { width: number; height: number }): { top: number; left: number } {
  const left = Math.min(Math.max(marker.left + marker.width / 2 - size.width / 2, GAP_PX),
                        Math.max(GAP_PX, view.width - size.width - GAP_PX));
  const under = line.top + line.height + GAP_PX;
  if (under + size.height <= view.height) return { top: under, left };
  const over = line.top - GAP_PX - size.height;
  if (over >= 0) return { top: over, left };
  const top = Math.min(Math.max(GAP_PX, view.height - size.height - GAP_PX),
                       Math.max(GAP_PX, marker.top + marker.height / 2 - size.height / 2));
  const right = marker.left + marker.width + GAP_PX;
  if (right + size.width <= view.width) return { top, left: right };
  return { top, left: Math.max(GAP_PX, marker.left - GAP_PX - size.width) };
}

/** A popover's width: 560 px for a question or a decision, 460 px otherwise. */
export function popoverWidth(address: Address | null): number {
  return address && (address.kind === "question" || address.kind === "decision"
                     || (address.kind === "walk" && address.item !== null
                         && ["question", "decision"].includes(address.item.kind))) ? 560 : 460;
}

// --- Live sessions -------------------------------------------------------------------------

/** A folder compared as written: `~` expanded and no trailing slash. */
export function folderKey(path: string, home: string | null): string {
  const full = home && (path === "~" || path.startsWith("~/")) ? `${home.replace(/\/+$/, "")}${path.slice(1)}` : path;
  return full.length > 1 ? full.replace(/\/+$/, "") : full;
}

/** The Live sessions row whose folder is `worktree`, when exactly one is; else null. */
export function sessionAt(worktree: string, sessions: readonly { key: string; cwd: string | null }[],
                          home: string | null): string | null {
  const want = folderKey(worktree, home);
  const found = sessions.filter((one) => one.cwd !== null && folderKey(one.cwd, home) === want);
  return found.length === 1 ? found[0].key : null;
}

// --- the phone -----------------------------------------------------------------------------

/** The width at and under which the page is the phone layout, in px. */
export const PHONE_PX = 720;
export const AT_DESK = "Answer these at your desk.";
/** How many builds in flight the phone's sheet draws. */
export const SHEET_BUILDS = 3;

/** The phone sheet's vertical rail: the round line's steps, then up to three builds in
 * flight, each step a row named `check of B1`, with `here` only on the step the round is
 * at. */
export function sheetMarks(round: Round, columns: readonly Column[]): { attributes: Record<string, string | null> }[] {
  const lines = [round.lines[0], ...round.lines.filter((line) => line.kind === "build").slice(0, SHEET_BUILDS)];
  const at = round.step;
  const made = [];
  for (const [n, line] of lines.entries()) {
    for (const [i, column] of columns.entries()) {
      if (n > 0 && column.part !== "stage") continue;
      const cell = line.cells[i] ?? null;
      if (cell === null) continue;
      const stage = stageOf(column, line);
      const isHere = n === 0 && at !== null && at.step === column.id
        && (column.part !== "stage" || at.stage === stage);
      made.push({ attributes: { ...markAttributes(column, stage, round.slug, { ...cell, here: isHere }, { quiet: true }),
                                label: stepName(column, stage) } });
    }
  }
  return made;
}

/** The questions the sheet lists as text: `Q2 When main itself is red…`. */
export function sheetQuestions(board: Board, round: string): string[] {
  return board.waiting.questions.filter((one) => one.holder === round).map((one) => `${one.id} ${one.text ?? ""}`.trim());
}

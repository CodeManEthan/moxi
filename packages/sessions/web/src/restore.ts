// How the list reads, worked out with no DOM so the Node tests can drive it: which
// answers it takes, how deep it reads when an address asks for a depth, and what its
// one writer puts in the address. The list element (`list-view.ts`) holds a `ListReader`,
// gives it the shell's `api`, draws what it holds and writes what `address()` returns.
//
// The rule. The list's state is its query (the filters and the find text as sent), the
// depth of that query's list (the pages read past the list answer, per group and per
// program line) and the rows opened in place. A read's answer changes the list only if it
// was asked with the current query; an `/api/more` answer only if it was also asked of the
// list answer shown (its `now`) and, for a restore's read, while that restore still holds
// its target. Otherwise, and when it fails, a read changes nothing and lets nothing go.
// Only the writer puts depth into the address, from what the list then shows.
//
// The restore. An address with a depth is a target: the list reads toward it one
// `/api/more` read at a time, with the calls `Show` makes, and while it holds its target
// the writer writes no address. It lets the target go when the list has read to it, when
// its 40th read has answered or failed, or at an act of his (a `Show`, a program line's
// `Show` or `Hide`, a query change).

import { Feed } from "./feed.ts";
import { parseList, parseMore } from "./list-feed.ts";
import type { ListAnswer, ListRow, MoreAnswer } from "./list-feed.ts";
import {
  addMore, listPath, LOCAL, morePath, queryOf, refresh, showMore, showMoreProgram, shownRow, statusLine,
  toggleProgram,
} from "./list.ts";
import type { Filters, ListState, MoreRequest, Query, StatusLine, Zone } from "./list.ts";
import { addressText, LIST_PATH, NO_DEPTH, queryWords, RESTORE_READS } from "./address.ts";
import type { Address, Depth } from "./address.ts";

/** What a read of a route answers: its status and its body. */
export interface Answered {
  status: number;
  data: unknown;
}

// -- the depth a list shows, and the plan toward a target --------------------------------------

/** The depth `state` shows: each group's pages past the list answer, `Earlier`'s, and
 * each opened program line's pages (1 once it is open). */
export function depthOf(state: ListState): Depth {
  const more = state.groups.filter((group) => group.key !== "earlier" && group.pages > 0)
    .map((group) => ({ group: group.key, pages: group.pages }));
  if (state.earlier !== null && state.earlier.pages > 0) more.push({ group: "earlier", pages: state.earlier.pages });
  const program = state.groups.filter((group) => group.program?.open === true)
    .map((group) => ({ group: group.key, pages: Math.max(1, group.program?.pages ?? 0) }));
  return { more, program };
}

/** The restore's next read: the list with a program line opened when the read opens it,
 * and the read. */
export interface Planned {
  state: ListState;
  read: MoreRequest;
}

/**
 * The next read toward `target` over `state`: for the first target item in list order
 * whose group the list holds with fewer pages than its count and more to read. A program
 * item whose line is not open opens it, and its first read, from the line's start, is its
 * page 1. A day `Earlier` brings sits above `Earlier`, so it has a read once the page that
 * brings it has landed. Null when no item has a read left: each is at its count, at its
 * group's end, or its group is not in the list; the target is then reached.
 */
export function nextRead(state: ListState, target: Depth): Planned | null {
  const more = new Map(target.more.map((item) => [item.group, item.pages]));
  const program = new Map(target.program.map((item) => [item.group, item.pages]));
  for (const group of state.groups) {
    const pages = more.get(group.key);
    const read = (asked: MoreRequest | null): Planned | null => (asked === null ? null : { state, read: asked });
    if (group.key === "earlier") {
      if (pages !== undefined && state.earlier !== null && state.earlier.pages < pages) {
        const planned = read(showMore(state, "earlier"));
        if (planned !== null) return planned;
      }
      continue;
    }
    if (pages !== undefined && group.pages < pages) {
      const planned = read(showMore(state, group.key));
      if (planned !== null) return planned;
    }
    const lines = program.get(group.key);
    const line = group.program;
    if (lines === undefined || line === null || line.count <= 0) continue;
    if (!line.open) {
      const opened = toggleProgram(state, group.key);
      if (opened.read !== null) return { state: opened.state, read: opened.read };
      continue;
    }
    if (line.pages === 0) {
      return read({ group: group.key, kind: "program", before: null, now: state.now, pages: 1, check: false });
    }
    if (line.pages < lines) {
      const planned = read(showMoreProgram(state, group.key));
      if (planned !== null) return planned;
    }
  }
  return null;
}

/** The opened rows the list now shows whose session has no read yet: `read` holds the
 * keys that have one. A key the list does not show is not among them. */
export function unreadOpened(state: ListState, read: ReadonlySet<string>): ListRow[] {
  return state.opened.filter((key) => !read.has(key)).map((key) => shownRow(state, key))
    .filter((row): row is ListRow => row !== null);
}

// -- the writer's decision -----------------------------------------------------------------

/** What the writer knows when it runs. */
export interface WriterView {
  filters: Filters;
  /** The list shown; null before any answer. */
  state: ListState | null;
  /** The list shown is an answer to the current query. */
  current: boolean;
  /** A restore holds its target. */
  holding: boolean;
  /** An `/api/more` read is running or waiting. */
  reading: boolean;
  /** The depth the address holds now. */
  standing: Depth;
}

/**
 * What the one writer puts in the address: null while a restore holds its target, when it
 * writes nothing. Else the current query's keys and with them: no depth while the list
 * shown is another query's or none has landed; the depth the address holds while a read
 * runs or waits, since a group read back to its depth shows less mid-way than it is
 * reading to; otherwise the depth the list shows.
 */
export function writeOf(view: WriterView): Address | null {
  if (view.holding) return null;
  const depth = view.state === null || !view.current ? NO_DEPTH : view.reading ? view.standing : depthOf(view.state);
  return { filters: view.filters, depth };
}

/** The write guard: the writer writes only while the list is placed and the document is
 * at the list's own path, so a read landing after he moved on rewrites no other address. */
export const mayWrite = (pathname: string, placed: boolean): boolean => placed && pathname === LIST_PATH;

// -- the scroll ----------------------------------------------------------------------------

/** What the scroll decision reads of an event. */
export interface Act {
  type: string;
  key?: string;
  altKey?: boolean;
  metaKey?: boolean;
}

const MODIFIERS = new Set(["Alt", "AltGraph", "Control", "Meta", "Shift", "CapsLock", "Fn", "OS", "Super", "Hyper"]);

/** Whether `event` is an act of his that drops the saved scroll: a wheel, a touch, a
 * pointer pressed, or a key that is not a modifier alone and is not held with Alt or Meta
 * (`Alt+Left` is Back). A `scroll` event never is: the browser scrolls on its own. */
export function isAct(event: Act): boolean {
  if (event.type === "wheel" || event.type === "touchstart" || event.type === "touchmove" || event.type === "pointerdown") {
    return true;
  }
  if (event.type !== "keydown") return false;
  return !MODIFIERS.has(event.key ?? "") && event.altKey !== true && event.metaKey !== true;
}

/**
 * The saved scroll, from placing to the one moment it is used. An act of his first drops
 * it. At the restore's first stop, once the opened rows then shown are read, `land` gives
 * where to scroll once, cut to the page's height, or null when it was dropped or there is
 * none. Until it has landed or been dropped, the list writes no `y`, so the browser's own
 * early scroll cannot overwrite the saved one.
 */
export class SavedScroll {
  #y: number | null;
  #settled = false;

  constructor(y: number | null) {
    this.#y = y;
  }

  /** It has landed or been dropped: `y` is the document's from here on. */
  get settled(): boolean {
    return this.#settled;
  }

  /** The `y` the memory holds until it is settled. */
  get saved(): number | null {
    return this.#y;
  }

  /** Takes an event; an act of his drops the scroll. Returns whether it was an act. */
  take(event: Act): boolean {
    const act = isAct(event);
    if (act && !this.#settled) {
      this.#settled = true;
      this.#y = null;
    }
    return act;
  }

  /** Where to scroll, at most `most`; null when nothing is to be done. Settles it. */
  land(most: number): number | null {
    if (this.#settled) return null;
    this.#settled = true;
    return this.#y === null ? null : Math.max(0, Math.min(this.#y, most));
  }
}

// -- the reader ----------------------------------------------------------------------------

/** A restore's hold on its target. */
export interface Restore {
  target: Depth;
  /** `/api/more` reads asked while it held its target. */
  reads: number;
  /** A read failed since the last list answer: no read is planned until the next. */
  failed: boolean;
}

/** A list answer, with what it was asked with. */
interface Asked {
  answer: ListAnswer;
  query: Query;
  generation: number;
}

/** What a change of the reader was: a list answer taken, a page added, or neither. */
export type Change = "list" | "more" | null;

export interface ReaderOptions {
  /** Reads one of sessions' routes. */
  read: (path: string) => Promise<Answered>;
  /** Called after each change of what the reader holds. */
  changed: (change: Change) => void;
  zone?: Zone;
}

/**
 * The list's reads, under the rule above. `place` starts it from an address; `readList`
 * asks `/api/list` (its tab shown, the tick); the acts are his.
 */
export class ListReader {
  filters: Filters;
  state: ListState | null = null;
  /** The line for a failed `/api/more`, while it stands. */
  moreStatus: StatusLine | null = null;
  /** The restore holding its target; null when none does. */
  restore: Restore | null = null;
  /** A restore met its cap. */
  capped = false;
  /** The restore's reads have first stopped: the target let go, or a read failed. With no
   * target, the first list answer was taken or failed. */
  stopped = false;
  readonly list: Feed<Asked>;

  #read: (path: string) => Promise<Answered>;
  #changed: (change: Change) => void;
  #zone: Zone;
  /** Counts query changes; a read carries the one it was asked under. */
  #generation = 0;
  /** The generation of the list shown; -1 before any. */
  #shown = -1;
  #asking: { query: Query; generation: number } | null = null;
  #taken: Asked | null = null;
  #queue: Promise<void> = Promise.resolve();
  /** `/api/more` reads waiting or running. */
  #pending = 0;
  #standing: Depth = NO_DEPTH;
  #closed = false;

  constructor({ read, changed, zone = LOCAL }: ReaderOptions) {
    this.#read = read;
    this.#changed = changed;
    this.#zone = zone;
    this.filters = { folder: null, harness: null, when: { kind: "any" }, find: "" };
    this.list = new Feed<Asked>(() => this.#askList(), (data) => this.#parseList(data), () => this.#listRead());
  }

  /** Starts from `address` and the rows the entry's memory names, and reads the list. */
  place(address: Address, opened: string[] = []): void {
    this.filters = address.filters;
    this.#standing = address.depth;
    const target = address.depth.more.length + address.depth.program.length > 0 ? address.depth : null;
    this.restore = target === null ? null : { target, reads: 0, failed: false };
    this.#opened = [...opened];
    this.readList();
  }

  /** Stops every change: a later answer is dropped. */
  close(): void {
    this.#closed = true;
    this.list.close();
  }

  /** The query the filters make now. `When` is in days of the server's `today`, so it is
   * worked out again before each read. */
  query(): Query {
    return queryOf(this.filters, this.state?.today ?? this.#zone.day(Date.now() / 1000), this.#zone);
  }

  /** Whether an `/api/more` read is running or waiting. */
  get reading(): boolean {
    return this.#pending > 0;
  }

  /** What the one writer puts in the address now; null while a restore holds its target.
   * The depth it names becomes the depth the address holds. */
  address(): string | null {
    const written = writeOf({
      filters: this.filters, state: this.state, current: this.#shown === this.#generation,
      holding: this.restore !== null, reading: this.#pending > 0, standing: this.#standing,
    });
    if (written === null) return null;
    this.#standing = written.depth;
    return addressText(written);
  }

  readList(): void {
    if (!this.#closed) void this.list.refresh();
  }

  // -- his acts ----------------------------------------------------------------------------

  /** Takes the filters as he left them. A change of the query in the address's words is
   * a new state, read from the top: a held target is let go, the old query's reads change
   * nothing, and no depth is written until a list of the new query is read. A pick that
   * makes the same query changes nothing. */
  setFilters(next: Filters): void {
    const same = queryWords(next) === queryWords(this.filters);
    this.filters = next;
    if (same) return;
    this.#generation += 1;
    this.#standing = NO_DEPTH;
    this.moreStatus = null;
    this.#letGo();
    this.readList();
    this.#changed(null);
  }

  /** A group's `Show`, or `Earlier`'s. */
  showMore(key: string): void {
    if (this.state === null) return;
    this.#letGo();
    this.#queueRead(showMore(this.state, key), null);
    this.#changed(null);
  }

  /** The `Show` under a day's opened program sessions. */
  showMoreProgram(key: string): void {
    if (this.state === null) return;
    this.#letGo();
    this.#queueRead(showMoreProgram(this.state, key), null);
    this.#changed(null);
  }

  /** A day's program line's `Show` or `Hide`. */
  toggleProgram(key: string): void {
    if (this.state === null) return;
    this.#letGo();
    const { state, read } = toggleProgram(this.state, key);
    this.state = state;
    this.#queueRead(read, null);
    this.#changed(null);
  }

  // -- the list ----------------------------------------------------------------------------

  /** The rows the entry's memory named, kept until the first list answer carries them. */
  #opened: string[] = [];

  async #askList(): Promise<Answered> {
    const asking = { query: this.query(), generation: this.#generation };
    const answer = await this.#read(listPath(asking.query));
    this.#asking = asking;
    return answer;
  }

  #parseList(data: unknown): Asked | null {
    const answer = parseList(data);
    return answer === null || this.#asking === null ? null : { answer, ...this.#asking };
  }

  #listRead(): void {
    if (this.#closed) return;
    const feed = this.list;
    const value = feed.value;
    if (value !== null && value !== this.#taken) {
      this.#taken = value;
      if (value.generation === this.#generation) {
        this.#take(value);
        return;
      }
    } else if (!feed.loading && !feed.reading && (feed.failure !== null || feed.refused !== null)) {
      if (this.restore !== null) this.restore.failed = true;
      this.stopped = true;
    }
    this.#changed(null);
  }

  #take(asked: Asked): void {
    const restart = this.#shown !== asked.generation;
    const kept = this.state === null ? null : { ...this.state, opened: this.state.opened };
    const { state, reads } = refresh(kept, asked.answer, asked.query, { restart });
    this.state = this.#opened.length > 0 && kept === null ? { ...state, opened: this.#opened } : state;
    this.#opened = [];
    this.#shown = asked.generation;
    this.moreStatus = null;
    if (this.restore !== null) this.restore.failed = false;
    for (const read of reads) this.#queueRead(read, this.restore);
    if (this.restore === null) this.stopped = true;
    this.#changed("list");
    this.#plan();
  }

  // -- `/api/more` -------------------------------------------------------------------------

  #queueRead(request: MoreRequest | null, restore: Restore | null): void {
    if (request === null) return;
    const generation = this.#generation;
    this.#pending += 1;
    this.#queue = this.#queue.then(() => this.#more(request, generation, restore)).finally(() => {
      this.#pending -= 1;
      if (this.#closed) return;
      if (this.#pending === 0) {
        this.#changed(null);
        this.#plan();
      }
    });
  }

  /** Whether a read asked under `generation`, for `restore`, may still change the list. */
  #current(generation: number, restore: Restore | null): boolean {
    return generation === this.#generation && (restore === null || this.restore === restore);
  }

  async #more(request: MoreRequest, generation: number, restore: Restore | null): Promise<void> {
    let next: MoreRequest | null = request;
    while (next !== null && !this.#closed) {
      const state = this.state;
      if (state === null || next.now !== state.now || !this.#current(generation, restore)) return;
      const asked: MoreRequest = next;
      const query = state.query;
      if (restore !== null) restore.reads += 1;
      const feed = new Feed<MoreAnswer>(() => this.#read(morePath(asked, query)), parseMore, () => {});
      await feed.refresh();
      if (this.#closed || this.state === null) return;
      if (feed.value === null) {
        if (this.#current(generation, restore) && asked.now === this.state.now) {
          this.moreStatus = statusLine(feed, "more of the list");
          if (restore !== null) {
            restore.failed = true;
            this.stopped = true;
          }
        }
        this.#capped(restore);
        this.#changed(null);
        return;
      }
      if (!this.#current(generation, restore)) {
        this.#capped(restore);
        return;
      }
      const added = addMore(this.state, asked, feed.value);
      this.state = added.state;
      this.moreStatus = null;
      next = added.next;
      this.#capped(restore);
      this.#changed("more");
    }
  }

  // -- the restore -------------------------------------------------------------------------

  /** Asks the restore's next read when the list shown is the current query's, no read is
   * running or waiting and none failed since the last list answer; lets the target go
   * when there is none. */
  #plan(): void {
    const restore = this.restore;
    const state = this.state;
    if (restore === null || state === null || this.#closed) return;
    if (this.#shown !== this.#generation || this.#pending > 0 || restore.failed) return;
    if (restore.reads >= RESTORE_READS) {
      this.#letGo({ capped: nextRead(state, restore.target) !== null });
      return;
    }
    const planned = nextRead(state, restore.target);
    if (planned === null) {
      this.#letGo();
      return;
    }
    this.state = planned.state;
    this.#queueRead(planned.read, restore);
    this.#changed(null);
  }

  /** At the restore's 40th read, answered or failed: the target is let go, named as the
   * cap only when the list had not read to it. */
  #capped(restore: Restore | null): void {
    if (restore === null || this.restore !== restore || restore.reads < RESTORE_READS || this.state === null) return;
    this.#letGo({ capped: nextRead(this.state, restore.target) !== null });
  }

  #letGo({ capped = false }: { capped?: boolean } = {}): void {
    if (this.restore === null) return;
    this.restore = null;
    this.stopped = true;
    if (capped) this.capped = true;
    this.#changed(null);
  }
}


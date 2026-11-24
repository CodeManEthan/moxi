// How loadout's `project-facts` component reads and reports, with no DOM so the Node tests
// drive it with a stubbed read and timer. The element (`project-facts.ts`) starts it while
// it is connected and the document shows, pauses it while the document is hidden, and
// closes it when it is removed.

import type { ApiResult } from "/m/ui/client.js";

import { factsReport, parseGiven } from "./facts-model.ts";
import type { Given, Report } from "./facts-model.ts";
import { Feed, parseListing } from "./feed.ts";
import type { Listing } from "./feed.ts";

/** How often `/api/list` is read again while the document shows, in milliseconds. */
export const READ_EVERY_MS = 60_000;

/** Calls `call` every `ms` milliseconds until the function it returns is called. */
export type Repeat = (ms: number, call: () => void) => () => void;

const repeatTimer: Repeat = (ms, call) => {
  const timer = setInterval(call, ms);
  return () => clearInterval(timer);
};

/**
 * Reads `/api/list` on `start()` and every minute after until `pause()`, and hands
 * `onReport` what `factsReport` makes of the last read for the given projects: after the
 * first read since it was made, then only when that changed. A read that fails, a 404, or
 * a list loadout can't read reports null; the next good read puts the facts back. After
 * `close()` it reads and reports nothing more, and a read still running is dropped.
 */
export class FactsReads {
  #feed: Feed<Listing>;
  #onReport: (detail: Report | null) => void;
  #repeat: Repeat;
  #stop: (() => void) | null = null;
  #given: Given[] = [];
  #open: string | null = null;
  /** The last report sent, as JSON; undefined until the first read has ended. */
  #last: string | undefined = undefined;
  #closed = false;

  constructor(read: () => Promise<ApiResult>, onReport: (detail: Report | null) => void,
              { repeat = repeatTimer, wait }: { repeat?: Repeat; wait?: (ms: number) => Promise<void> } = {}) {
    this.#onReport = onReport;
    this.#repeat = repeat;
    this.#feed = new Feed<Listing>(read, parseListing, () => this.#report(), wait === undefined ? {} : { wait });
  }

  /** Whether the timer runs. */
  get running(): boolean {
    return this.#stop !== null;
  }

  /** Reads now, then every minute until paused. */
  start(): Promise<void> {
    if (this.#closed) return Promise.resolve();
    if (this.#stop === null) this.#stop = this.#repeat(READ_EVERY_MS, () => void this.#feed.refresh());
    return this.#feed.refresh();
  }

  /** Stops the timer; a read already running still reports. */
  pause(): void {
    this.#stop?.();
    this.#stop = null;
  }

  /** Stops the timer and drops every later answer. */
  close(): void {
    this.pause();
    this.#feed.close();
    this.#closed = true;
  }

  /** The Projects page's `projects` attribute and the opened key; reports again from the
   * last read when one has ended. */
  setGiven(projects: string | null, open: string | null): void {
    this.#given = parseGiven(projects);
    this.#open = open;
    if (this.#last !== undefined) this.#report();
  }

  #report(): void {
    const feed = this.#feed;
    if (this.#closed || feed.loading) return;
    const good = feed.failure === null && !feed.gone ? feed.value : null;
    const detail = factsReport(good, this.#given, this.#open);
    const text = JSON.stringify(detail);
    if (text === this.#last) return;
    this.#last = text;
    this.#onReport(detail);
  }
}

// How usage's `project-facts` component reads and reports, with no DOM so the Node tests
// drive it with a stubbed read and timer. The element (`project-facts.ts`) starts it while
// it is connected and the document shows, pauses it while the document is hidden, and
// closes it when it is removed.

import type { ApiResult } from "/m/ui/client.js";

import { addDays, isDay, LOCAL } from "./days.ts";
import { factsReport, parseGiven } from "./facts-model.ts";
import type { Given, Reads, Report } from "./facts-model.ts";
import { Feed, parseReport } from "./feed.ts";
import type { ProjectRow, Report as UsageReport } from "./feed.ts";

/** How often the reads are made again while the document shows, in milliseconds. */
export const READ_EVERY_MS = 60_000;

/** Calls `call` every `ms` milliseconds until the function it returns is called. */
export type Repeat = (ms: number, call: () => void) => () => void;

const repeatTimer: Repeat = (ms, call) => {
  const timer = setInterval(call, ms);
  return () => clearInterval(timer);
};

type Rows = UsageReport<ProjectRow>;

/** `/api/projects` from `since` on. */
export function sincePath(since: string): string {
  return `api/projects?${new URLSearchParams([["since", since]]).toString()}`;
}

/** A feed's value while its last read was good; null after a read that failed, was
 * refused or found no route, and before its first. */
function good(feed: Feed<Rows> | null): Rows | null {
  if (feed === null || feed.loading) return null;
  return feed.failure === null && !feed.gone && feed.refused === null ? feed.value : null;
}

/**
 * Reads `/api/projects` from today and from 7 days back on `start()`, and from 30 days back
 * too while a project is open (and at once when one opens); then all of them every minute
 * until `pause()`. Hands `onReport` what `factsReport` makes of the last good reads for the
 * given projects: after the first read since it was made, then only when that changed. A
 * read that fails drops what it fed, and the next good one puts it back. After `close()` it
 * reads and reports nothing more, and a read still running is dropped.
 */
export class FactsReads {
  #read: (path: string) => Promise<ApiResult>;
  #onReport: (detail: Report | null) => void;
  #repeat: Repeat;
  #wait: ((ms: number) => Promise<void>) | undefined;
  #today: () => string;
  #day: Feed<Rows>;
  #week: Feed<Rows>;
  #month: Feed<Rows> | null = null;
  #stop: (() => void) | null = null;
  #given: Given[] = [];
  #open: string | null = null;
  /** The last report sent, as JSON; undefined until the first read has ended. */
  #last: string | undefined = undefined;
  #closed = false;

  constructor(read: (path: string) => Promise<ApiResult>, onReport: (detail: Report | null) => void,
              { repeat = repeatTimer, wait, today = () => LOCAL.day(Date.now() / 1000) }:
              { repeat?: Repeat; wait?: (ms: number) => Promise<void>; today?: () => string } = {}) {
    this.#read = read;
    this.#onReport = onReport;
    this.#repeat = repeat;
    this.#wait = wait;
    this.#today = today;
    this.#day = this.#feed(() => this.#countDay());
    this.#week = this.#feed(() => addDays(this.#countDay(), -6) ?? this.#countDay());
  }

  /** Whether the timer runs. */
  get running(): boolean {
    return this.#stop !== null;
  }

  /** The opened key it was given. */
  get open(): string | null {
    return this.#open;
  }

  /** Reads now, then every minute until paused. */
  start(): Promise<void> {
    if (this.#closed) return Promise.resolve();
    if (this.#stop === null) this.#stop = this.#repeat(READ_EVERY_MS, () => void this.#readAll());
    return this.#readAll();
  }

  /** Stops the timer; a read already running still reports. */
  pause(): void {
    this.#stop?.();
    this.#stop = null;
  }

  /** Stops the timer and drops every later answer. */
  close(): void {
    this.pause();
    for (const feed of [this.#day, this.#week, this.#month]) feed?.close();
    this.#closed = true;
  }

  /** The Projects page's `projects` attribute and the opened key. A newly opened key is
   * read from 30 days back at once, while the timer runs; with none open, that read and
   * what it fed are dropped. Reports again from the last reads when one has ended. */
  setGiven(projects: string | null, open: string | null): void {
    this.#given = parseGiven(projects);
    const opened = open !== this.#open;
    this.#open = open;
    if (opened) {
      this.#month?.close();
      this.#month = open === null ? null : this.#feed(() => addDays(this.#countDay(), -29) ?? this.#countDay());
      if (this.#month !== null && this.#stop !== null) void this.#month.refresh();
    }
    if (this.#last !== undefined) this.#report();
  }

  /** The day the reads count from: this browser's, or the newest day an answer carried. */
  #countDay(): string {
    const days = [this.#today(), this.#day?.value?.today, this.#week?.value?.today].filter(isDay).sort();
    return days[days.length - 1];
  }

  #feed(since: () => string): Feed<Rows> {
    return new Feed<Rows>(() => this.#read(sincePath(since())), parseReport<ProjectRow>, () => this.#report(),
                          this.#wait === undefined ? {} : { wait: this.#wait });
  }

  #readAll(): Promise<void> {
    const feeds = [this.#day, this.#week, this.#month].filter((feed): feed is Feed<Rows> => feed !== null);
    return Promise.all(feeds.map((feed) => feed.refresh())).then(() => undefined);
  }

  #report(): void {
    if (this.#closed) return;
    // The first report waits until today's and the week's reads have both ended, so a
    // half-read state is never sent as the whole.
    if (this.#last === undefined && (this.#day.loading || this.#week.loading)) return;
    const reads: Reads = { today: good(this.#day), week: good(this.#week), month: good(this.#month) };
    const detail = factsReport(reads, this.#given, this.#open);
    const text = JSON.stringify(detail);
    if (text === this.#last) return;
    this.#last = text;
    this.#onReport(detail);
  }
}

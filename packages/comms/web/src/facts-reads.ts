// How comms' `project-facts` component reads and reports, with no DOM so the Node tests
// drive it with a stubbed read and topic. The element (`project-facts.ts`) starts it while
// it is connected and the document shows, pauses it while the document is hidden, and
// closes it when it is removed.

import type { ApiResult } from "/m/ui/client.js";

import { factsReport, parseGiven } from "./facts-model.ts";
import type { Given, Report } from "./facts-model.ts";
import { Feed, parseRoster } from "./feed.ts";
import type { Roster } from "./feed.ts";

/** The topic comms publishes the roster on. */
export const TOPIC = "roster";

/** Follows `topic`, calling `listen` with each event, until the function it returns is
 * called. */
export type Follow = (topic: string, listen: (data: unknown) => void) => () => void;

/**
 * Reads `/api/roster` on `start()`, then follows the topic `roster` until `pause()`, and
 * hands `onReport` what `factsReport` makes of the roster for the given projects: after the
 * first read since it was made, then only when that changed. A read that fails, a 404, or
 * a roster comms can't say (`offline`, `error`) reports null; the next good read or event
 * puts the facts back. After `close()` it reads and reports nothing more, and a read still
 * running is dropped.
 */
export class FactsReads {
  #feed: Feed<Roster>;
  #follow: Follow;
  #onReport: (detail: Report | null) => void;
  #stop: (() => void) | null = null;
  #given: Given[] = [];
  #open: string | null = null;
  /** The last report sent, as JSON; undefined until the first read has ended. */
  #last: string | undefined = undefined;
  #closed = false;
  /** Whether `start()` was called after the last `pause()`. */
  #started = false;

  constructor(read: () => Promise<ApiResult>, follow: Follow, onReport: (detail: Report | null) => void,
              { wait }: { wait?: (ms: number) => Promise<void> } = {}) {
    this.#follow = follow;
    this.#onReport = onReport;
    this.#feed = new Feed<Roster>(read, parseRoster, () => this.#report(), wait === undefined ? {} : { wait });
  }

  /** Whether it follows the topic. */
  get following(): boolean {
    return this.#stop !== null;
  }

  /** Reads now, then follows the topic until paused. */
  start(): Promise<void> {
    if (this.#closed) return Promise.resolve();
    this.#started = true;
    const feed = this.#feed;
    return feed.refresh().then(() => {
      if (!this.#closed && this.#stop === null && this.#started) {
        this.#stop = this.#follow(TOPIC, (data) => feed.topic(data));
      }
    });
  }

  /** Stops following; a read already running still reports. */
  pause(): void {
    this.#started = false;
    this.#stop?.();
    this.#stop = null;
  }

  /** Stops following and drops every later answer. */
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

// When the launcher's `project-facts` component reads, with no DOM so the Node tests can
// drive it: project-facts.ts gives it the shell's `api` and `subscribe` and a timer, and
// starts and stops it as the element is connected and the document shown.
//
// While it runs it reads `/api/running`, `/api/presets` and `/api/catalog` when started and
// every 30 s, and follows the launcher's `running` topic, which carries `/api/running`'s
// value. For the opened project it reads `/api/last` and `/api/name` when started, when
// the opened project changes and with each 30 s read; an answer for a project no longer
// opened is dropped. It subscribes to no topic but the launcher's own.

import type { ApiResult } from "/m/ui/client.js";

import { failureWhy, Feed } from "./feed.ts";
import type { Last } from "./fill-model.ts";
import { parseLast } from "./fill-model.ts";
import { nameUp, upOnly } from "./facts-model.ts";
import type { Reads } from "./facts-model.ts";
import type { Named } from "./name-model.ts";
import { parseName } from "./name-model.ts";
import type { Catalog, Presets } from "./panel.ts";
import { parseCatalog, parsePresets } from "./panel.ts";
import type { Running } from "./running-model.ts";
import { parseRunning } from "./running-model.ts";

/** How often every route is read again while the reads run, in milliseconds. */
export const EVERY_MS = 30_000;
/** The launcher's topic that carries `/api/running`'s value. */
export const RUNNING_TOPIC = "running";

/** What the reads are given: the launcher's routes, its topics, and a repeating timer. */
export interface Shell {
  read(path: string): Promise<ApiResult>;
  subscribe(topic: string, fn: (data: unknown) => void): () => void;
  every(ms: number, fn: () => void): () => void;
}

/** A feed's value while its last read was good, else null. */
function good<V>(feed: Feed<V> | null): V | null {
  return feed === null || feed.loading || feed.failure !== null || feed.gone ? null : feed.value;
}

/** The two reads of the opened project, and the key they are for. */
class Opened {
  readonly key: string;
  last: Feed<Last>;
  name: Feed<Named>;

  constructor(key: string, shell: Shell, onChange: () => void) {
    this.key = key;
    const query = new URLSearchParams({ project: key }).toString();
    this.last = new Feed<Last>(() => shell.read(`api/last?${query}`), parseLast, onChange);
    this.name = new Feed<Named>(() => shell.read(`api/name?${query}`),
                                (data) => parseName(data, failureWhy(200)), onChange);
  }

  refresh(): void {
    void this.last.refresh();
    void this.name.refresh();
  }

  close(): void {
    this.last.close();
    this.name.close();
  }
}

/**
 * The component's reads. `onChange` is called after every read that finishes and every
 * topic event while it runs, and once when the opened project changes.
 */
export class FactsReads {
  #shell: Shell;
  #onChange: () => void;
  #running: Feed<Running>;
  #presets: Feed<Presets>;
  #catalog: Feed<Catalog>;
  #opened: Opened | null = null;
  #stopTopic: (() => void) | null = null;
  #stopTimer: (() => void) | null = null;
  #closed = false;

  constructor(shell: Shell, onChange: () => void) {
    this.#shell = shell;
    this.#onChange = () => {
      if (!this.#closed) onChange();
    };
    this.#running = new Feed<Running>(() => shell.read("api/running"), parseRunning, this.#onChange);
    this.#presets = new Feed<Presets>(() => shell.read("api/presets"), parsePresets, this.#onChange);
    this.#catalog = new Feed<Catalog>(() => shell.read("api/catalog"), parseCatalog, this.#onChange);
  }

  /** Whether the reads are running: started and not paused or closed. */
  get following(): boolean {
    return this.#stopTimer !== null;
  }

  /** Whether any read has finished since the reads were made. */
  get read(): boolean {
    return !this.#running.loading || !this.#presets.loading || !this.#catalog.loading
      || (this.#opened !== null && (!this.#opened.last.loading || !this.#opened.name.loading));
  }

  /** The opened project's key, or null. */
  get open(): string | null {
    return this.#opened?.key ?? null;
  }

  /** Each route's last good value; the opened project's reads only while they are its. */
  reads(): Reads {
    return {
      running: upOnly(good(this.#running)),
      presets: upOnly(good(this.#presets)),
      catalog: upOnly(good(this.#catalog)),
      last: upOnly(good(this.#opened?.last ?? null)),
      name: nameUp(good(this.#opened?.name ?? null)),
      turns: null,
    };
  }

  /** Reads every route once, then follows the topic and reads again every `EVERY_MS`. */
  start(): void {
    if (this.#closed || this.following) return;
    this.#readAll();
    this.#stopTimer = this.#shell.every(EVERY_MS, () => this.#readAll());
    const running = this.#running;
    this.#stopTopic = this.#shell.subscribe(RUNNING_TOPIC, (data) => running.topic(data));
  }

  /** Stops the timer and the topic; the values read so far are kept. */
  pause(): void {
    this.#stopTopic?.();
    this.#stopTopic = null;
    this.#stopTimer?.();
    this.#stopTimer = null;
  }

  /** Stops everything for good: a later answer or event changes nothing. */
  close(): void {
    this.pause();
    this.#closed = true;
    this.#running.close();
    this.#presets.close();
    this.#catalog.close();
    this.#opened?.close();
    this.#opened = null;
  }

  /** The opened project is now `key` (null for none): its reads replace the last one's. */
  setOpen(key: string | null): void {
    if (this.#closed || key === this.open) return;
    this.#opened?.close();
    this.#opened = key === null ? null : new Opened(key, this.#shell, this.#onChange);
    if (this.following) this.#opened?.refresh();
    this.#onChange();
  }

  #readAll(): void {
    void this.#running.refresh();
    void this.#presets.refresh();
    void this.#catalog.refresh();
    this.#opened?.refresh();
  }
}

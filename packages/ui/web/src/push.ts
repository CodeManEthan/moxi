// The push channel's browser side: one stream per browser, however many shell tabs it has.
//
// Every tab asks the Web Lock `moxi-ui-push`; the tab that holds it is the leader and
// holds the one `GET /m/ui/api/push?topics=...` stream. Every tab sends its topic set and
// whether it is visible on the BroadcastChannel `moxi-ui-push`. The leader asks for the
// union, reopens the stream 50 ms after the union last changed, and passes each event to
// every tab. It closes the stream after 10 s with no visible tab and opens it again when
// one is shown. When the leader tab closes, the lock goes to another tab, which asks every
// tab for its topics and opens the stream. Every topic carries state (its first event is
// its current value, each later one replaces it), so a reopen or a new leader loses
// nothing.

import { SseParser } from "./sse.ts";

export const CHANNEL = "moxi-ui-push";
export const LOCK = "moxi-ui-push";
/** Each tab holds a lock of this prefix and its id while it lives, so the leader can drop
 * the topics of a tab that went away without saying so. */
export const TAB_LOCK = "moxi-ui-tab-";
export const PUSH_URL = "/m/ui/api/push";
export const REOPEN_MS = 50;
export const HIDDEN_MS = 10_000;
/** The waits before each reconnect after a failure, the last one repeated. */
export const BACKOFF_MS = [1_000, 2_000, 5_000, 10_000, 30_000];
/** The wait after the shell's `busy` when it names none. */
export const BUSY_MS = 5_000;
/** A stream that brings nothing for this long is taken as dead; the shell sends a
 * heartbeat every 20 s. */
export const STALL_MS = 45_000;
/** The event the shell adds for each module's upstream: `{module, state, detail}`. */
export const STATE_EVENT = "moxi.state";
const PRUNE_MS = 5_000;

/** How a stream ended. */
export type Ending =
  | { kind: "closed" }
  | { kind: "network" }
  | { kind: "stalled" }
  | { kind: "status"; status: number; error: string | null; retryAfter: number | null };

export interface StreamHandlers {
  event(name: string, raw: string): void;
  end(ending: Ending): void;
}

/** Opens one stream for `topics`. After `close()` no handler is called. */
export type Opener = (topics: readonly string[], handlers: StreamHandlers) => { close(): void };

export interface Clock {
  set(fn: () => void, ms: number): unknown;
  clear(handle: unknown): void;
}

export const realClock: Clock = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

/** One module's upstream state, as the shell reports it. */
export interface UpstreamState {
  module: string;
  state: "up" | "starting" | "down";
  detail: string | null;
}

/** `raw` as an UpstreamState, or null when it is not one. */
export function upstreamState(raw: string): UpstreamState | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null) return null;
  const { module, state, detail } = value as Record<string, unknown>;
  if (typeof module !== "string" || !["up", "starting", "down"].includes(state as string)) return null;
  return { module, state: state as UpstreamState["state"], detail: typeof detail === "string" ? detail : null };
}

// --- the leader -------------------------------------------------------------------------

export interface LeaderHooks {
  open: Opener;
  clock: Clock;
  /** An event for every tab, this one included. */
  deliver(name: string, raw: string): void;
  /** The last values a tab asked for, sent to that tab alone. */
  replay(tab: string, events: Array<[string, string]>): void;
  /** The shell refused the session: no stream until `reset()`. */
  signedOut(): void;
}

interface Tab {
  topics: Set<string>;
  visible: boolean;
}

/** The leader's state: the tabs, the union of their topics, and the one stream. It does
 * no I/O of its own; `hooks` open the stream and reach the tabs. */
export class Leader {
  private tabs = new Map<string, Tab>();
  private stream: { key: string; token: object; handle: { close(): void } | null } | null = null;
  private values = new Map<string, string>();
  private reopenTimer: unknown = null;
  private hiddenTimer: unknown = null;
  private retryTimer: unknown = null;
  private dormant = false;
  private refused: string | null = null;
  private failures = 0;
  private out = false;
  private stopped = false;

  private hooks: LeaderHooks;

  constructor(hooks: LeaderHooks) {
    this.hooks = hooks;
  }

  /** Whether a stream is open or being opened, and for which topics. */
  get open(): string | null {
    return this.stream?.key ?? null;
  }

  tabIds(): string[] {
    return [...this.tabs.keys()];
  }

  /** A tab's topic set and visibility, sent whenever either changes. The tab gets the
   * last value of each of its topics at once. */
  setTab(id: string, topics: readonly string[], visible: boolean): void {
    const before = this.key();
    const set = new Set(topics);
    this.tabs.set(id, { topics: set, visible });
    const events: Array<[string, string]> = [];
    for (const [name, raw] of this.values) {
      if (set.has(name) || name.startsWith(`${STATE_EVENT} `)) {
        events.push([name.startsWith(`${STATE_EVENT} `) ? STATE_EVENT : name, raw]);
      }
    }
    if (events.length) this.hooks.replay(id, events);
    this.visibility();
    if (this.key() !== before) this.schedule();
  }

  dropTab(id: string): void {
    if (!this.tabs.delete(id)) return;
    this.visibility();
    this.schedule();
  }

  /** Sign-in again, or a new key: try the stream again at once. */
  reset(): void {
    this.out = false;
    this.refused = null;
    this.failures = 0;
    this.clear("retryTimer");
    this.reconcile();
  }

  /** Close the stream and every timer for good. */
  stop(): void {
    this.stopped = true;
    for (const name of ["reopenTimer", "hiddenTimer", "retryTimer"] as const) this.clear(name);
    this.closeStream();
  }

  /** The union of every tab's topics, sorted and comma-joined. */
  key(): string {
    const all = new Set<string>();
    for (const tab of this.tabs.values()) for (const topic of tab.topics) all.add(topic);
    return [...all].sort().join(",");
  }

  private clear(name: "reopenTimer" | "hiddenTimer" | "retryTimer"): void {
    if (this[name] !== null) this.hooks.clock.clear(this[name]);
    this[name] = null;
  }

  private visibility(): void {
    const shown = [...this.tabs.values()].some((tab) => tab.visible);
    if (shown) {
      this.clear("hiddenTimer");
      if (this.dormant) {
        this.dormant = false;
        this.schedule();
      }
    } else if (this.hiddenTimer === null && !this.dormant) {
      this.hiddenTimer = this.hooks.clock.set(() => {
        this.hiddenTimer = null;
        this.dormant = true;
        this.reconcile();
      }, HIDDEN_MS);
    }
  }

  private schedule(): void {
    this.clear("reopenTimer");
    this.reopenTimer = this.hooks.clock.set(() => {
      this.reopenTimer = null;
      this.reconcile();
    }, REOPEN_MS);
  }

  private closeStream(): void {
    const stream = this.stream;
    this.stream = null;
    stream?.handle?.close();
  }

  private reconcile(): void {
    const key = this.key();
    const want = !this.stopped && !this.out && !this.dormant && key !== "" && key !== this.refused;
    if (this.stream !== null && (!want || this.stream.key !== key)) this.closeStream();
    if (!want || this.stream !== null || this.retryTimer !== null) return;
    const topics = new Set(key.split(","));
    for (const name of [...this.values.keys()]) {
      if (!name.startsWith(`${STATE_EVENT} `) && !topics.has(name)) this.values.delete(name);
    }
    const token = {};
    const stream: { key: string; token: object; handle: { close(): void } | null } = { key, token, handle: null };
    this.stream = stream;
    const mine = () => this.stream?.token === token;
    const handle = this.hooks.open([...topics], {
      event: (name, raw) => {
        if (!mine()) return;
        this.failures = 0;
        if (name === STATE_EVENT) {
          const state = upstreamState(raw);
          if (state === null) return;
          this.values.set(`${STATE_EVENT} ${state.module}`, raw);
        } else {
          this.values.set(name, raw);
        }
        this.hooks.deliver(name, raw);
      },
      end: (ending) => {
        if (!mine()) return;
        this.stream = null;
        this.ended(key, ending);
      },
    });
    if (mine()) stream.handle = handle;
    else handle.close();
  }

  private ended(key: string, ending: Ending): void {
    let wait: number;
    if (ending.kind === "status" && ending.status === 403) {
      this.out = true;
      this.hooks.signedOut();
      return;
    }
    if (ending.kind === "status" && ending.status === 400) {
      // A client bug, which a retry would only repeat: wait for the union to change.
      console.error(`moxi: the shell refused the push topics ${key}: ${ending.error ?? "400"}`);
      this.refused = key;
      return;
    }
    if (ending.kind === "status" && ending.status === 503 && ending.error === "busy") {
      wait = (ending.retryAfter ?? BUSY_MS / 1000) * 1000;
    } else {
      wait = BACKOFF_MS[Math.min(this.failures, BACKOFF_MS.length - 1)];
      this.failures += 1;
    }
    this.retryTimer = this.hooks.clock.set(() => {
      this.retryTimer = null;
      this.reconcile();
    }, wait);
  }
}

// --- one tab's subscriptions ------------------------------------------------------------

type Listener = { fn: (data: any) => void };

/** One tab's subscribers, by full topic name, and the last value each topic had. */
export class Hub {
  private subs = new Map<string, Set<Listener>>();
  private last = new Map<string, string>();
  private states = new Map<string, UpstreamState>();
  private stateFns = new Set<(state: UpstreamState) => void>();

  private changed: () => void;

  /** `changed` runs when the set of topics this tab asks for changes. */
  constructor(changed: () => void) {
    this.changed = changed;
  }

  add(name: string, fn: (data: any) => void): () => void {
    let set = this.subs.get(name);
    const fresh = set === undefined;
    if (set === undefined) {
      set = new Set();
      this.subs.set(name, set);
    }
    const listener: Listener = { fn };
    set.add(listener);
    const raw = this.last.get(name);
    if (raw !== undefined) {
      const data = JSON.parse(raw);
      queueMicrotask(() => {
        if (set.has(listener)) call(listener.fn, data);
      });
    }
    if (fresh) this.changed();
    return () => {
      if (!set.delete(listener) || set.size > 0 || this.subs.get(name) !== set) return;
      this.subs.delete(name);
      this.last.delete(name);
      this.changed();
    };
  }

  /** Every topic some subscriber of this tab asks for, sorted. */
  topics(): string[] {
    return [...this.subs.keys()].sort();
  }

  /** An event from the stream: each subscriber of its topic gets the parsed value,
   * unless it is the value they last got. */
  deliver(name: string, raw: string): void {
    if (name === STATE_EVENT) {
      const state = upstreamState(raw);
      if (state === null) return;
      this.states.set(state.module, state);
      for (const fn of [...this.stateFns]) call(fn, state);
      return;
    }
    const set = this.subs.get(name);
    if (set === undefined || this.last.get(name) === raw) return;
    let data: unknown;
    try {
      data = JSON.parse(raw);
    } catch {
      return;
    }
    this.last.set(name, raw);
    for (const listener of [...set]) call(listener.fn, data);
  }

  state(module: string): UpstreamState | null {
    return this.states.get(module) ?? null;
  }

  onState(fn: (state: UpstreamState) => void): () => void {
    this.stateFns.add(fn);
    return () => this.stateFns.delete(fn);
  }
}

function call<T>(fn: (value: T) => void, value: T): void {
  try {
    fn(value);
  } catch (err) {
    console.error(err);
  }
}

// --- the stream -------------------------------------------------------------------------

/** An Opener that reads the push stream with fetch, sending the key `key()` gives. */
export function fetchOpener(key: () => string | null): Opener {
  return (topics, handlers) => {
    const abort = new AbortController();
    let closed = false;
    let stall: ReturnType<typeof setTimeout> | undefined;
    const finish = (ending: Ending) => {
      if (closed) return;
      closed = true;
      clearTimeout(stall);
      abort.abort();
      handlers.end(ending);
    };
    const alive = () => {
      clearTimeout(stall);
      stall = setTimeout(() => finish({ kind: "stalled" }), STALL_MS);
    };
    (async () => {
      const value = key();
      if (!value) {
        finish({ kind: "status", status: 403, error: null, retryAfter: null });
        return;
      }
      let answer: Response;
      try {
        alive();
        answer = await fetch(`${PUSH_URL}?topics=${topics.join(",")}`, {
          headers: { "X-Moxi-Key": value, Accept: "text/event-stream" },
          credentials: "same-origin",
          cache: "no-store",
          signal: abort.signal,
        });
      } catch {
        finish({ kind: "network" });
        return;
      }
      if (answer.status !== 200 || answer.body === null) {
        let error: string | null = null;
        try {
          const data = await answer.json();
          if (typeof data?.error === "string") error = data.error;
        } catch {
          // An empty or non-JSON refusal names no reason.
        }
        const after = Number.parseInt(answer.headers.get("Retry-After") ?? "", 10);
        finish({ kind: "status", status: answer.status, error,
                 retryAfter: Number.isFinite(after) && after >= 0 ? after : null });
        return;
      }
      const parser = new SseParser();
      const reader = answer.body.pipeThrough(new TextDecoderStream()).getReader();
      try {
        for (;;) {
          const { done, value: chunk } = await reader.read();
          if (closed) return;
          if (done) break;
          alive();
          for (const event of parser.feed(chunk)) {
            if (closed) return;
            handlers.event(event.event, event.data);
          }
        }
      } catch {
        finish({ kind: "network" });
        return;
      }
      finish({ kind: "closed" });
    })();
    return {
      close() {
        if (closed) return;
        closed = true;
        clearTimeout(stall);
        abort.abort();
      },
    };
  };
}

// --- one tab ----------------------------------------------------------------------------

type Message =
  | { t: "tab"; tab: string; topics: string[]; visible: boolean }
  | { t: "bye"; tab: string }
  | { t: "ask" }
  | { t: "event"; name: string; raw: string; to?: string }
  | { t: "signedout" };

function newId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }
}

/** This tab's side of the channel: its subscribers, and the leader while it holds the
 * lock. */
export class TabPush {
  readonly id = newId();
  readonly hub: Hub;
  private leader: Leader | null = null;
  private channel: BroadcastChannel | null = null;
  private opener: Opener;
  private onSignedOut: () => void;

  constructor(opener: Opener, onSignedOut: () => void) {
    this.opener = opener;
    this.onSignedOut = onSignedOut;
    this.hub = new Hub(() => this.announce());
  }

  start(): void {
    if (typeof BroadcastChannel === "function") {
      this.channel = new BroadcastChannel(CHANNEL);
      this.channel.onmessage = (event) => this.receive(event.data);
    }
    document.addEventListener("visibilitychange", () => this.announce());
    // A page that leaves says so, so the leader drops its topics at once; one kept in the
    // back-forward cache says so again when it is shown. A leader's stream and lock end
    // with its page.
    addEventListener("pagehide", () => {
      if (this.leader === null) this.post({ t: "bye", tab: this.id });
    });
    addEventListener("pageshow", (event) => {
      if (event.persisted) this.announce();
    });
    const locks = navigator.locks;
    if (locks === undefined || this.channel === null) {
      // Without both, this tab holds a stream of its own.
      this.lead();
    } else {
      void locks.request(`${TAB_LOCK}${this.id}`, () => new Promise(() => {}));
      void locks.request(LOCK, () => {
        this.lead();
        return new Promise(() => {});
      });
    }
    this.announce();
  }

  /** A new key was stored, by a sign-in in this browser: try again. */
  reset(): void {
    this.leader?.reset();
  }

  private lead(): void {
    this.leader = new Leader({
      open: this.opener,
      clock: realClock,
      deliver: (name, raw) => {
        this.hub.deliver(name, raw);
        this.post({ t: "event", name, raw });
      },
      replay: (tab, events) => {
        for (const [name, raw] of events) {
          if (tab === this.id) this.hub.deliver(name, raw);
          else this.post({ t: "event", name, raw, to: tab });
        }
      },
      signedOut: () => {
        this.onSignedOut();
        this.post({ t: "signedout" });
      },
    });
    this.post({ t: "ask" });
    this.announce();
    if (navigator.locks !== undefined) setInterval(() => void this.prune(), PRUNE_MS);
  }

  private async prune(): Promise<void> {
    const leader = this.leader;
    if (leader === null) return;
    const seen = await navigator.locks.query();
    const live = new Set([...(seen.held ?? []), ...(seen.pending ?? [])].map((lock) => lock.name));
    for (const tab of leader.tabIds()) {
      if (tab !== this.id && !live.has(`${TAB_LOCK}${tab}`)) leader.dropTab(tab);
    }
  }

  private announce(): void {
    const topics = this.hub.topics();
    const visible = document.visibilityState === "visible";
    if (this.leader !== null) this.leader.setTab(this.id, topics, visible);
    else this.post({ t: "tab", tab: this.id, topics, visible });
  }

  private post(message: Message): void {
    try {
      this.channel?.postMessage(message);
    } catch {
      // A closing channel drops the message; the leader's prune catches up.
    }
  }

  private receive(message: Message): void {
    if (typeof message !== "object" || message === null) return;
    switch (message.t) {
      case "tab":
        if (typeof message.tab === "string" && Array.isArray(message.topics)) {
          this.leader?.setTab(message.tab, message.topics.filter((t) => typeof t === "string"),
                              message.visible === true);
        }
        break;
      case "bye":
        if (typeof message.tab === "string") this.leader?.dropTab(message.tab);
        break;
      case "ask":
        this.announce();
        break;
      case "event":
        if (typeof message.name === "string" && typeof message.raw === "string"
            && (message.to === undefined || message.to === this.id)) {
          this.hub.deliver(message.name, message.raw);
        }
        break;
      case "signedout":
        this.onSignedOut();
        break;
    }
  }
}

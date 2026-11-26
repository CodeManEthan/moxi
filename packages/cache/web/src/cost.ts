// What going cold costs, from usage's facts, as the Cache page draws it, with no DOM so the
// Node tests can drive it. With usage installed the page reads usage's `/api/session` for
// each timed session, which gives the whole prompt of the session's newest request (its
// context) and the model of the main file's newest requests, and `/api/prices` for those
// models' prices, in dollars per million tokens.
//
// A turn sent once the cache is cold writes the whole context into the cache again: its
// tokens times the model's cache-write price for the lifetime the record counts (one hour,
// or five minutes); sent while it is warm it reads them at the cache-read price. A price
// tier that a prompt of that size pays takes the place of the model's own prices. Where any
// of these is not known (no context, no model, a lifetime with no price of its own, a price
// usage does not have) there is no cost, and the page draws none.
//
// Without usage, or with a read failing, there is no cost: a failed read of one session
// drops what an earlier read of it said, and a failed read of the prices drops every cost.
//
// Every string here goes into the page as text.

import type { ApiResult } from "/m/ui/client.js";

/** What usage says of one session: its context and the model that ran it. */
export interface UsageFacts {
  tokens: number | null;
  model: string | null;
}

/** One model's prices, dollars per million tokens, null where unknown, and its tiers. */
export interface Rate {
  cache_read: number | null;
  cache_write_5m: number | null;
  cache_write_1h: number | null;
  /** The tiers a prompt over `above` tokens pays, their prices in place of the model's. */
  tiers: { above: number; cache_read: number | null; cache_write_5m: number | null; cache_write_1h: number | null }[];
}

/** The prices by model, as asked. */
export type Rates = ReadonlyMap<string, Rate>;

const isTable = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const price = (value: unknown): number | null =>
  (typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null);
const filled = (value: unknown): string | null =>
  (typeof value === "string" && value.trim() !== "" ? value.trim() : null);

/** usage's route for one session, relative to usage's page. */
export function usageSessionPath(harness: string, session: string): string {
  return `api/session?${new URLSearchParams({ session, harness })}`;
}

/** usage's route for the prices of `models`, relative to usage's page. */
export function pricesPath(models: readonly string[]): string {
  return `api/prices?${new URLSearchParams({ models: models.join(",") })}`;
}

/** The most models one read of the prices asks for. */
export const PRICES_MAX = 50;

/**
 * What a value of usage's `/api/session` says of the session: `context.tokens`, and the
 * model of the main file's newest rows (`agent` `main`, the newest `last_ts`). Null for a
 * body that is not such a value.
 */
export function parseUsageSession(data: unknown): UsageFacts | null {
  if (!isTable(data) || !Array.isArray(data.rows)) return null;
  const context = isTable(data.context) ? data.context : null;
  const tokens = context !== null && typeof context.tokens === "number" && Number.isFinite(context.tokens)
    && context.tokens > 0 ? context.tokens : null;
  let model: string | null = null;
  let newest = -Infinity;
  for (const row of data.rows) {
    if (!isTable(row) || row.agent !== "main") continue;
    const name = filled(row.model);
    const last = typeof row.last_ts === "number" && Number.isFinite(row.last_ts) ? row.last_ts : -Infinity;
    if (name !== null && (model === null || last > newest)) {
      model = name;
      newest = last;
    }
  }
  return { tokens, model };
}

function parseRate(value: unknown): Rate | null {
  if (!isTable(value)) return null;
  const tiers: Rate["tiers"] = [];
  for (const tier of Array.isArray(value.tiers) ? value.tiers : []) {
    if (!isTable(tier) || !isTable(tier.conditions) || !isTable(tier.prices)) continue;
    const { above_tokens: above, service_tier: service, speed } = tier.conditions;
    // A tier that asks for a service tier or a speed is one a request chooses; only a tier
    // by the prompt's size follows from the context alone.
    if (typeof above !== "number" || !Number.isFinite(above) || service != null || speed != null) continue;
    tiers.push({ above, cache_read: price(tier.prices.cache_read), cache_write_5m: price(tier.prices.cache_write_5m),
                 cache_write_1h: price(tier.prices.cache_write_1h) });
  }
  return { cache_read: price(value.cache_read), cache_write_5m: price(value.cache_write_5m),
           cache_write_1h: price(value.cache_write_1h), tiers };
}

/**
 * The prices of `asked` from a value of usage's `/api/prices?models=`, whose `models` come
 * in the order asked. Null for a body that is not such a value, or one that answers for
 * another number of models.
 */
export function parseRates(data: unknown, asked: readonly string[]): Rates | null {
  if (!isTable(data) || !Array.isArray(data.models) || data.models.length !== asked.length) return null;
  const found = new Map<string, Rate>();
  asked.forEach((model, position) => {
    const rate = parseRate((data.models as unknown[])[position]);
    if (rate !== null) found.set(model, rate);
  });
  return found;
}

/** What going cold costs one session. */
export interface Cost {
  tokens: number;
  /** Dollars the next turn pays to write the context again once cold. */
  cold: number;
  /** Dollars it pays to read it while warm; null where the read price is not known. */
  warm: number | null;
}

/**
 * The cost of going cold for a record whose clock counts `ttl` seconds: the context's
 * tokens at the cache-write price of that lifetime (3600 one hour, 300 five minutes; any
 * other lifetime has no price of its own), from the tier the prompt's size pays, else the
 * model's own. Null where any part is not known.
 */
export function costOf(ttl: number | null, facts: UsageFacts | null, rates: Rates | null): Cost | null {
  if (facts === null || facts.tokens === null || facts.model === null || rates === null) return null;
  const write = ttl === 3600 ? "cache_write_1h" : ttl === 300 ? "cache_write_5m" : null;
  const rate = rates.get(facts.model);
  if (write === null || rate === undefined) return null;
  const tokens = facts.tokens;
  let prices: Omit<Rate["tiers"][number], "above"> = rate;
  let above = -Infinity;
  for (const tier of rate.tiers) {
    if (tokens > tier.above && tier.above > above) {
      prices = tier;
      above = tier.above;
    }
  }
  const per = prices[write];
  if (per === null) return null;
  const read = prices.cache_read;
  return { tokens, cold: (tokens * per) / 1e6, warm: read === null ? null : (tokens * read) / 1e6 };
}

// --- the words --------------------------------------------------------------------------

/** Dollars as the page says them: `$2.62`, `under $0.01` below a cent. */
export function moneyWords(dollars: number): string {
  if (dollars < 0.005) return "under $0.01";
  return `$${dollars.toFixed(2)}`;
}

/** A token count in short: `655k`, `1.2M`, `900`. */
export function tokensWords(tokens: number): string {
  if (tokens < 1000) return String(Math.round(tokens));
  if (tokens < 999_500) return `${Math.round(tokens / 1000)}k`;
  const millions = tokens / 1e6;
  return `${millions < 9.95 ? millions.toFixed(1).replace(/\.0$/, "") : Math.round(millions)}M`;
}

/** Line two's cost: `about $2.62 to re-read if cold` on a warm row, `about $1.82 to
 * re-read on its next turn` on a cold one. */
export function restCost(cost: Cost, cold: boolean): string {
  return `about ${moneyWords(cost.cold)} to re-read ${cold ? "on its next turn" : "if cold"}`;
}

export const IF_COLD_LABEL = "If cold";
export const ESTIMATE = "estimate";
export const USAGE_LINK = "Usage";

/** The opened `If cold` line's value: `Next turn re-reads 655k tokens: about $2.62, or
 * $0.07 warm`, without the warm price where it is not known. */
export function ifColdWords(cost: Cost): string {
  const warm = cost.warm === null ? "" : `, or ${moneyWords(cost.warm)} warm`;
  return `Next turn re-reads ${tokensWords(cost.tokens)} tokens: about ${moneyWords(cost.cold)}${warm}`;
}

/** usage's address of one session: its Sessions section with the session opened. */
export function usageRoute(harness: string, session: string): string {
  return ["sessions", harness, session].map(encodeURIComponent).join("/");
}

// --- the reads ----------------------------------------------------------------------------

/** One session to ask usage about: the record's key, harness and session, and its last
 * request, which says when its context last grew. */
export interface Asked {
  key: string;
  harness: string;
  session: string;
  last: number | null;
}

/**
 * usage's facts of the timed sessions, and the prices of their models, read one request
 * at a time. `sessions(list)` asks for each session it has not asked about, and again for
 * one whose last request moved; a session that left is forgotten. `again()` asks for every
 * session once more. A read that gives no value drops what was held for that session, and
 * a failed read of the prices drops them all. The prices are asked whenever the set of
 * models changes. `onChange` is called after every answer.
 */
export class UsageReads {
  #facts = new Map<string, UsageFacts>();
  #lasts = new Map<string, number | null>();
  #queue: Asked[] = [];
  #sessions = new Map<string, Asked>();
  #rates: Rates | null = null;
  #models = "";
  #ratesAgain = false;
  #running = false;
  #closed = false;
  #read: (path: string) => Promise<ApiResult>;
  #onChange: () => void;

  constructor(read: (path: string) => Promise<ApiResult>, onChange: () => void) {
    this.#read = read;
    this.#onChange = onChange;
  }

  close(): void {
    this.#closed = true;
  }

  /** usage's facts of the record `key`; null where none were read. */
  facts(key: string): UsageFacts | null {
    return this.#facts.get(key) ?? null;
  }

  get rates(): Rates | null {
    return this.#rates;
  }

  /** The timed sessions now. */
  sessions(list: readonly Asked[]): void {
    const keys = new Set(list.map((one) => one.key));
    for (const key of [...this.#sessions.keys()]) {
      if (keys.has(key)) continue;
      this.#sessions.delete(key);
      this.#facts.delete(key);
      this.#lasts.delete(key);
    }
    for (const one of list) {
      this.#sessions.set(one.key, one);
      if (this.#lasts.has(one.key) && this.#lasts.get(one.key) === one.last) continue;
      this.#lasts.set(one.key, one.last);
      this.#ask(one);
    }
    this.#pump();
  }

  /** Asks for every session once more, and the prices. */
  again(): void {
    for (const one of this.#sessions.values()) this.#ask(one);
    this.#ratesAgain = true;
    this.#pump();
  }

  #ask(one: Asked): void {
    if (!this.#queue.some((queued) => queued.key === one.key)) this.#queue.push(one);
  }

  async #pump(): Promise<void> {
    if (this.#running || this.#closed) return;
    this.#running = true;
    try {
      while (!this.#closed) {
        const one = this.#queue.shift();
        if (one !== undefined) {
          const answer = await this.#get(usageSessionPath(one.harness, one.session));
          if (this.#closed) return;
          if (!this.#sessions.has(one.key)) continue;
          const facts = answer.status === 200 ? parseUsageSession(answer.data) : null;
          if (facts === null) this.#facts.delete(one.key);
          else this.#facts.set(one.key, facts);
          this.#onChange();
          continue;
        }
        const models = [...new Set([...this.#facts.values()].map((facts) => facts.model)
          .filter((model): model is string => model !== null))].sort().slice(0, PRICES_MAX);
        const set = models.join("\n");
        if (set === this.#models && !this.#ratesAgain) break;
        this.#models = set;
        this.#ratesAgain = false;
        if (models.length === 0) {
          this.#rates = null;
          this.#onChange();
          continue;
        }
        const answer = await this.#get(pricesPath(models));
        if (this.#closed) return;
        this.#rates = answer.status === 200 ? parseRates(answer.data, models) : null;
        this.#onChange();
      }
    } finally {
      this.#running = false;
    }
  }

  async #get(path: string): Promise<ApiResult> {
    try {
      return await this.#read(path);
    } catch {
      return { status: 0, data: null };
    }
  }
}

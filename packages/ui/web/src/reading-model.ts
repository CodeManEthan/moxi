// The count of reads still out that decide the page's layout, as client.ts marks it on the
// page root (`data-reading="<n>"`, gone at none). DOM-free: when a read ends, and how to
// wait, are given.
//
// The rule: a read counts from when it is sent until its answer has been drawn, and a read
// a page defers behind a wait counts from when it is scheduled. An end is taken off the
// count only on `later`, a turn after the answer, so the render the answer's continuation
// makes (a Lit update is a microtask) lands before the mark goes.

/** Runs `fn` after `ms` milliseconds; `setTimeout` in the browser. */
export type Later = (fn: () => void, ms: number) => unknown;

/** How long a topic's first value counts as a read out, at most. A topic its module has
 * never published has no first value, and its page must still settle. */
export const FIRST_VALUE_MS = 5_000;

export class ReadingCount {
  /** The reads out now. */
  count = 0;

  #onChange: (count: number) => void;
  #later: Later;

  /** `onChange` gets the count each time it changes. */
  constructor(onChange: (count: number) => void, later: Later) {
    this.#onChange = onChange;
    this.#later = later;
  }

  /** One read sent. Gives the function that ends it, which takes it off the count a turn
   * later; a second call does nothing. */
  begin(): () => void {
    this.count += 1;
    this.#onChange(this.count);
    let ended = false;
    return () => {
      if (ended) return;
      ended = true;
      this.#later(() => {
        this.count -= 1;
        this.#onChange(this.count);
      }, 0);
    };
  }

  /** `read` run after `ms`, counted as a read out from now until a turn after what it
   * gives (its promise) has settled, so its answer is drawn first. Gives the function that
   * cancels it: before `read` has run, `read` never runs and the count lets it go; after,
   * it does nothing, and the read counts until it ends. */
  deferred(read: () => unknown, ms: number): () => void {
    const end = this.begin();
    let due = true;
    this.#later(() => {
      if (!due) return;
      due = false;
      let ran: unknown;
      try {
        ran = read();
      } catch (exc) {
        end();
        throw exc;
      }
      void Promise.resolve(ran).finally(end);
    }, ms);
    return () => {
      if (!due) return;
      due = false;
      end();
    };
  }

  /** `fn` as a topic's listener whose first value counts as a read out: from now until
   * that first value, `stopped()` or `FIRST_VALUE_MS`, whichever comes first. */
  firstValue<T>(fn: (data: T) => void): { listener: (data: T) => void; stopped: () => void } {
    const end = this.begin();
    this.#later(end, FIRST_VALUE_MS);
    return {
      listener: (data: T) => {
        end();
        fn(data);
      },
      stopped: end,
    };
  }
}

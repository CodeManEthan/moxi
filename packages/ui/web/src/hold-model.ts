// The gate every move off a page runs through, and the hold a page takes on it while it
// has changes not saved. DOM-free: the shell gives it the browser's history and the
// address shown, and each draw.
//
// The rule: a page that holds is never replaced or left without its say. With no hold a
// move is made at once. With one, the gate calls the page's `ask(go)` and does nothing
// else; `go()` releases the hold and then makes the move. A shell redraw on a modules or
// session event waits while a page holds: the panel is marked stale and drawn when the
// page releases the hold itself (a release by `go()` is a move, which draws anyway).

/** A page's ask before it is left: it shows its own question and calls `go` to leave. */
export type Ask = (go: () => void) => void;

/** The part of the browser's history the gate uses. */
export interface Steps {
  readonly state: unknown;
  pushState(data: unknown, unused: string, url?: string | URL | null): void;
  replaceState(data: unknown, unused: string, url?: string | URL | null): void;
  back(): void;
}

interface Holder {
  ask: Ask;
}

export class Gate {
  readonly #steps: Steps;
  readonly #address: () => string;
  #holder: Holder | null = null;
  #stale: (() => void) | null = null;
  #placed: { path: string; state: unknown };

  /** `address` gives the path, search and hash the browser shows now. */
  constructor(steps: Steps, address: () => string) {
    this.#steps = steps;
    this.#address = address;
    this.#placed = { path: address(), state: steps.state };
  }

  /** Whether a page holds. */
  get held(): boolean {
    return this.#holder !== null;
  }

  /** Whether a redraw is waiting for the hold's release. */
  get stale(): boolean {
    return this.#stale !== null;
  }

  /** The address the page shown is at: the one the shell drew, or the one the page last
   * wrote for itself. */
  get placed(): string {
    return this.#placed.path;
  }

  /**
   * Takes the hold for the page shown, with `ask` called for each move asked while it
   * holds. One page holds at a time: a second take replaces the first, whose release then
   * does nothing. Gives the function that releases it; a release with a redraw waiting
   * draws the panel.
   */
  hold(ask: Ask): () => void {
    if (typeof ask !== "function") throw new TypeError("holdLeave takes a function");
    const holder: Holder = { ask };
    this.#holder = holder;
    return () => {
      if (this.#holder !== holder) return;
      this.#holder = null;
      const redraw = this.#stale;
      this.#stale = null;
      redraw?.();
    };
  }

  /** Makes the move `act` now with no hold; with one, asks the page and makes it only
   * when the page calls `go`, once, after the hold is released. */
  move(act: () => void): void {
    const holder = this.#holder;
    if (holder === null) {
      act();
      return;
    }
    let gone = false;
    holder.ask(() => {
      if (gone) return;
      gone = true;
      if (this.#holder === holder) {
        this.#holder = null;
        this.#stale = null;
        act();
      } else {
        // The page released the hold meanwhile, or another page took it: ask again.
        this.move(act);
      }
    });
  }

  /**
   * Back or Forward has moved the address already. With no hold, `draw` draws where it
   * landed. With one, the page's address is put back with a new entry and the page asked;
   * `go()` steps back onto the entry the person was going to, whose own popstate, with no
   * hold, draws it. The put-back entry stays after it, so Forward from there returns.
   */
  onPopstate(draw: () => void): void {
    if (this.#holder === null) {
      draw();
      return;
    }
    this.#steps.pushState(this.#placed.state, "", this.#placed.path);
    this.move(() => this.#steps.back());
  }

  /** The shell drew the panel at the address shown now. */
  markDrawn(): void {
    this.#placed = { path: this.#address(), state: this.#steps.state };
    this.#stale = null;
  }

  /** The page writes its own address `path`: the entry is replaced, its state kept, and
   * the page is still the one placed. */
  setAddress(path: string): void {
    const state = this.#steps.state;
    this.#steps.replaceState(state, "", path);
    this.#placed = { path: this.#address(), state };
  }

  /** A modules or session event asks for `redraw` when `changed`: now with no hold, else
   * once the page releases it. */
  refresh(changed: boolean, redraw: () => void): void {
    if (!changed) return;
    if (this.#holder === null) {
      redraw();
      return;
    }
    this.#stale = redraw;
  }
}

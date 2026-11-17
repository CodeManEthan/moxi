// The page's talk with its page server: the state, the draft's preview, Save, and the
// two acts on Claude Code's line. It takes the request function as an argument, so the
// Node tests drive it with a fake one.
//
// A 409 from a preview or a Save means the saved config moved under the draft: the page
// reads `/api/state`, takes the new file as its base with the draft's changes kept over
// it, and previews again, so what it shows is again what Save would write.
//
// While the saved config can't be read, Save is off and Claude Code's line prints
// `config?`, so that is the line the preview shows, with no render asked for.

import { Draft, parseState } from "./draft.ts";
import type { State } from "./draft.ts";

/** What `api` from client.js resolves with. */
export interface Answer {
  status: number;
  data: any;
}

export type Api = (path: string, body?: object) => Promise<Answer>;

/** A rendered preview. */
export interface Preview {
  lines: string[];
  ms: number | null;
  notes: string[];
  problem: string | null;
}

/** A line for the person: `ok` after Save or an act, `warn` when the base moved, `error`
 * when something was refused or failed. */
export interface Message {
  kind: "ok" | "warn" | "error";
  text: string;
}

export const MOVED = "The saved config changed since the page read it; your changes are kept over it.";

/** What Claude Code's line prints while its saved config can't be read, as the render
 * prints it. */
export const UNREADABLE_LINE = "config?";
export const UNREADABLE_NOTE = "Claude Code's line shows this until the saved config can be read.";

function failed(what: string, answer: Answer): string {
  return answer.status === 0
    ? `${what} failed: the page server could not be reached.`
    : `${what} failed: the page server answered ${answer.status}.`;
}

function said(answer: Answer): string | null {
  const data = answer.data;
  if (data && typeof data.refused === "string") return data.refused;
  if (data && typeof data.conflict === "string") return data.conflict;
  return null;
}

function parsePreview(data: any): Preview | null {
  if (!data || !Array.isArray(data.lines) || !Array.isArray(data.notes)) return null;
  return {
    lines: data.lines.filter((line: unknown) => typeof line === "string"),
    ms: typeof data.ms === "number" ? data.ms : null,
    notes: data.notes.filter((note: unknown) => typeof note === "string"),
    problem: typeof data.problem === "string" ? data.problem : null,
  };
}

export class Sync {
  state: State | null = null;
  draft: Draft | null = null;
  /** The last preview of the draft as it is now; null while none has come back. While
   * the saved config can't be read, the line Claude Code prints in that state. */
  preview: Preview | null = null;
  /** Why the draft as it is now is refused (a 422), or null. */
  refused: string | null = null;
  /** Why the page has nothing to show (its first read failed), or null. */
  failure: string | null = null;
  /** The module went away (a 404): nothing is drawn. */
  gone = false;
  message: Message | null = null;
  /** A Save or an act is in flight. */
  busy = false;
  #api: Api;
  #changed: () => void;
  #asked = 0;

  constructor(api: Api, changed: () => void) {
    this.#api = api;
    this.#changed = changed;
  }

  /** Reads the state. The first read makes the draft; a later one takes the new file as
   * the base when it moved, keeping the draft's changes, and says whether it moved. */
  async read(): Promise<boolean> {
    const answer = await this.#api("api/state");
    if (answer.status === 404) {
      this.gone = true;
      this.#changed();
      return false;
    }
    const state = answer.status === 200 ? parseState(answer.data) : null;
    if (state === null) {
      if (this.state === null) this.failure = failed("Reading the status line's config", answer);
      this.#changed();
      return false;
    }
    this.failure = null;
    const moved = this.#take(state);
    this.#changed();
    return moved;
  }

  /** Takes `state` as the page's: the first one makes the draft; a later one with a new
   * base rebases the draft over it. Says whether the base moved. */
  #take(state: State): boolean {
    const moved = this.draft !== null && this.draft.base !== state.base;
    const wasUnreadable = this.state?.problem != null;
    this.state = state;
    if (this.draft === null) this.draft = new Draft(state);
    else if (moved) this.draft.rebase(state);
    // The line shown while the config couldn't be read is not a preview of the draft.
    if (state.problem !== null) this.#unreadable();
    else if (wasUnreadable) this.preview = null;
    return moved;
  }

  /** The preview while the saved config can't be read: the line Claude Code prints. A
   * render still in flight is dropped when it answers. */
  #unreadable(): void {
    this.#asked++;
    this.preview = { lines: [UNREADABLE_LINE], ms: null, notes: [UNREADABLE_NOTE], problem: null };
    this.refused = null;
  }

  /** Previews the draft as it is now. An answer to an older draft is dropped. Nothing is
   * rendered while the saved config can't be read. */
  async previewNow(again = true): Promise<void> {
    const draft = this.draft;
    if (draft === null || this.state?.problem) return;
    const asked = ++this.#asked;
    const answer = await this.#api("api/preview", draft.body());
    if (asked !== this.#asked) return;
    if (answer.status === 200) {
      this.preview = parsePreview(answer.data);
      this.refused = null;
    } else if (answer.status === 422) {
      this.preview = null;
      this.refused = said(answer) ?? failed("The preview", answer);
    } else if (answer.status === 409 && again) {
      await this.read();
      this.message = { kind: "warn", text: MOVED };
      await this.previewNow(false);
      return;
    } else {
      this.preview = { lines: [], ms: null, notes: [], problem: failed("The preview", answer) };
    }
    this.#changed();
  }

  /** Writes the draft. On success the draft is the saved config. */
  async save(): Promise<void> {
    const draft = this.draft;
    if (draft === null || !draft.dirty || this.busy) return;
    this.busy = true;
    this.#changed();
    try {
      const answer = await this.#api("api/save", draft.body());
      const state = answer.status === 200 ? parseState(answer.data) : null;
      if (state !== null) {
        this.state = state;
        this.draft = new Draft(state);
        this.message = { kind: "ok", text: "Saved. Claude Code's line shows it from its next render." };
      } else if (answer.status === 409) {
        await this.read();
        this.message = { kind: "warn", text: `${MOVED} Look it over and save again.` };
      } else {
        this.message = { kind: "error", text: said(answer) ?? failed("Saving", answer) };
      }
    } finally {
      this.busy = false;
    }
    this.#changed();
    await this.previewNow();
  }

  /** Drops the draft's changes. */
  discard(): void {
    this.draft?.discard();
    this.message = null;
    this.#changed();
    void this.previewNow();
  }

  /** `use` or `restore`, on the saved config: refused here while the draft differs. */
  async act(name: "use" | "restore"): Promise<void> {
    if (this.draft === null || this.draft.dirty || this.busy) return;
    this.busy = true;
    this.#changed();
    let moved = false;
    try {
      const answer = await this.#api(`api/${name}`, {});
      const state = answer.status === 200 ? parseState(answer.data) : null;
      if (state !== null) {
        moved = this.#take(state);
        const lines = Array.isArray(answer.data.said) ? answer.data.said : [];
        this.message = { kind: "ok", text: lines.join(" ") };
      } else {
        this.message = { kind: "error", text: said(answer) ?? failed("The change", answer) };
        moved = await this.read();
      }
    } finally {
      this.busy = false;
    }
    this.#changed();
    if (moved) await this.previewNow();
  }

  /** The page's window came back: read the state, and preview again when the file moved
   * under the draft. */
  async refocus(): Promise<void> {
    if (this.busy) return;
    if (await this.read()) await this.previewNow();
  }
}

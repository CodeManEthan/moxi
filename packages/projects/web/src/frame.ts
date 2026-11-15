// The page frame's bookkeeping, with no DOM so the Node tests can drive it: which modules'
// facts components the page places, the `projects` attribute it gives them, the reports
// it holds, drawn at most once a frame, and when the list's first order lets go.

import { readReport } from "./facts.ts";
import type { Given, Report } from "./facts.ts";
import type { Listed } from "./feed.ts";
import { orderHeld } from "./list-model.ts";

/**
 * The modules whose facts component the page places, by name: each module the shell's
 * list (`ui.modules`' value) gives as present and for which `declares` is true. A module
 * listed twice is skipped. A value that holds no list gives none.
 */
export function factsModules(value: unknown, declares: (module: string) => boolean): string[] {
  const modules = typeof value === "object" && value !== null ? (value as { modules?: unknown }).modules : null;
  if (!Array.isArray(modules)) return [];
  const listed = new Map<string, number>();
  const present = new Set<string>();
  for (const item of modules) {
    if (typeof item !== "object" || item === null) continue;
    const { name, state } = item as { name?: unknown; state?: unknown };
    if (typeof name !== "string" || name === "") continue;
    listed.set(name, (listed.get(name) ?? 0) + 1);
    if (state === "present") present.add(name);
  }
  return [...present].filter((name) => listed.get(name) === 1 && declares(name)).sort();
}

/** The `projects` attribute's list: every project the page holds, roots absolute. */
export function givenOf(projects: readonly Listed[]): Given[] {
  return projects.map(({ key, root, lead, associated }) => ({ key, root, lead, associated: [...associated] }));
}

/** Runs a function once, at the next frame. */
export type Schedule = (draw: () => void) => void;

/**
 * The reports the page holds, one per module, each the last its component sent. A report
 * is read when it is heard, against the projects and the open key the component was
 * given; what it refuses is logged once per module. Reports heard within one frame are
 * drawn together: the first asks `schedule` for the next frame, and at that frame every
 * report heard since is taken in and `draw` is called once.
 */
export class HeldReports {
  #drawn = new Map<string, Report | null>();
  #heard = new Map<string, Report | null>();
  #logged = new Set<string>();
  #asked = false;
  #schedule: Schedule;
  #draw: () => void;
  #log: (module: string, refused: string[]) => void;

  constructor(schedule: Schedule, draw: () => void, log: (module: string, refused: string[]) => void) {
    this.#schedule = schedule;
    this.#draw = draw;
    this.#log = log;
  }

  /** `module`'s component reported `detail`, given `given` and `open`. */
  heard(module: string, detail: unknown, given: readonly Given[], open: string | null): void {
    const read = readReport(detail, given, open);
    if (read.refused.length > 0 && !this.#logged.has(module)) {
      this.#logged.add(module);
      this.#log(module, read.refused);
    }
    this.#heard.set(module, read.report);
    this.#ask();
  }

  /** `module`'s component was taken away: its report goes with it. */
  forget(module: string): void {
    const held = this.#heard.delete(module);
    if (this.#drawn.delete(module) || held) this.#ask();
  }

  /** How many modules' reports have been drawn, a null one counted. */
  get reported(): number {
    return this.#drawn.size;
  }

  /** Whether `module` has reported, its report drawn. */
  has(module: string): boolean {
    return this.#drawn.has(module);
  }

  /** The reports drawn, by module name, the null ones left out. */
  reports(): Report[] {
    return [...this.#drawn].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .flatMap(([, report]) => (report === null ? [] : [report]));
  }

  #ask(): void {
    if (this.#asked) return;
    this.#asked = true;
    this.#schedule(() => {
      this.#asked = false;
      for (const [module, report] of this.#heard) this.#drawn.set(module, report);
      this.#heard.clear();
      this.#draw();
    });
  }
}

/**
 * The list's first order: held, by key, from when the page opened until every placed
 * component has reported once or `HOLD_MS` has passed; once let go it stays let go.
 * `placed` is null while the page doesn't yet know which components it places.
 */
export class FirstOrder {
  #since: number;
  #held = true;

  constructor(sinceMs: number) {
    this.#since = sinceMs;
  }

  held(placed: number | null, reported: number, nowMs: number): boolean {
    if (this.#held && !orderHeld(placed ?? Infinity, reported, nowMs - this.#since)) this.#held = false;
    return this.#held;
  }
}

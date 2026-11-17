// What the page shows for its state and draft, worked out with no DOM so the Node tests
// can read it. Every string here goes into the page as text.

import { canMove, Draft, offItems, rowsOf } from "./draft.ts";
import type { State, Values } from "./draft.ts";

/** Each base field's label, in plain words. */
export const FIELD_LABELS: Readonly<Record<string, string>> = {
  dir: "Folder",
  branch: "Git branch",
  model: "Model",
  effort: "Effort level",
  context: "Context used",
  session: "Session id",
  transcript: "Transcript file",
  lines: "Lines added and removed",
  cost: "Session cost",
};

/** The options below the lists: each key's label, in plain words. */
export const OPTION_LABELS = {
  separator: "Text between items",
  color: "Colours",
  "dir.parts": "Folder parts kept",
  "context.warn_at": "Context turns yellow at (%)",
  "context.alert_at": "Context turns red at (%)",
} as const;

export const HOLD = "Save or discard first";

/** One item as a list draws it. `detail` is its `show` name for a base field, or a
 * module's summary. */
export interface ItemView {
  name: string;
  label: string;
  detail: string;
  module: boolean;
  up: boolean;
  down: boolean;
}

/** Claude Code's line, and the two acts on it. */
export interface ClaudeView {
  text: string;
  command: string | null;
  problem: string | null;
  useShown: boolean;
  restoreShown: boolean;
  /** Both acts are held while the draft has unsaved changes. */
  held: boolean;
}

export interface PageView {
  rows: [ItemView[], ItemView[]];
  off: ItemView[];
  values: Values;
  /** Save and Discard show only while the draft differs from the saved config. */
  dirty: boolean;
  /** How many settings the draft changes, as the bar beside Save says it. */
  countText: string;
  /** False when the saved config can't be read: nothing can be saved over it. */
  editable: boolean;
  problem: string | null;
  claude: ClaudeView;
}

function itemView(state: State, show: readonly string[], name: string,
                  drawn: (item: string) => boolean): ItemView {
  const module = state.modules.find((m) => m.name === name);
  const isField = state.fields.includes(name);
  const moves = canMove(show, name, drawn);
  return {
    name,
    label: isField ? FIELD_LABELS[name] ?? name : name,
    detail: isField ? name : module?.summary ?? "",
    module: !isField,
    up: moves.up,
    down: moves.down,
  };
}

/** Whether `item` is drawn: a base field, or a module the state lists. */
export function drawnBy(state: State): (item: string) => boolean {
  const names = new Set([...state.fields, ...state.modules.map((m) => m.name)]);
  return (item) => names.has(item);
}

function claudeView(state: State, dirty: boolean): ClaudeView {
  const c = state.claude;
  let text: string;
  if (!c.set) text = "Claude Code shows no status line now.";
  else if (c.moxis && c.here) text = "Claude Code uses this status line.";
  else if (c.moxis) text = "Claude Code uses Moxi's status line from another install:";
  else text = "Claude Code runs your own status line:";
  return {
    text,
    command: c.set && !(c.moxis && c.here) ? c.command : null,
    problem: c.problem,
    useShown: !(c.moxis && c.here),
    restoreShown: c.moxis && c.saved,
    held: dirty,
  };
}

/** The page as it is drawn from `state` and `draft`. */
export function pageView(state: State, draft: Draft): PageView {
  const values = draft.values();
  const drawn = drawnBy(state);
  const [first, second] = rowsOf(values.show, drawn);
  const item = (name: string) => itemView(state, values.show, name, drawn);
  const off = offItems(values.show, state.fields, state.modules)
    .map((name) => ({ ...item(name), up: false, down: false }));
  const count = draft.count;
  return {
    rows: [first.map(item), second.map(item)],
    off,
    values,
    dirty: draft.dirty,
    // The count is of settings, not edits: five moves in the line are one setting, `show`.
    countText: count === 1 ? "1 setting changed" : `${count} settings changed`,
    editable: state.problem === null,
    problem: state.problem,
    claude: claudeView(state, draft.dirty),
  };
}

/** The line under the preview: its time. */
export function timeText(ms: number | null): string {
  return ms === null ? "" : `Rendered in ${ms.toFixed(1)} ms`;
}

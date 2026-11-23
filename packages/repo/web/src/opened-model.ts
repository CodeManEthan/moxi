// A repo opened in place, from `/api/repos`: its events and pieces, `Today`, its
// auto-commits, `Rules`, `Git`, the foot, and the `Turn off` block. Each part is drawn only
// when its fact is there. No DOM, so the Node tests read it.

import type { Command, Offered, PieceRow, RecordRow, RepoRow, SpecRow } from "./feed.ts";
import type { Item } from "./items-model.ts";
import { runOrder, shownNames, specNews } from "./rows-model.ts";
import {
  byGitOrder, type Clock, counted, eventLabel, firstLine, homeForm, LOCAL, pieceName, recordSeconds, repoName, whenWord,
} from "./words.ts";

/** One piece on its event's row. */
export interface PieceLine {
  piece: PieceRow;
  /** `secret-gate`, or its id where two shown pieces share a name. */
  name: string;
  /** `secret-gate · leaves out a file that holds a secret`, `secret-gate is off · …`, or
   * `prose-check · no installed module declares it`. */
  text: string;
  /** `Turn off` on an enabled piece, `Turn on` on one that is off; none on a piece no
   * module declares. */
  act: "turn-off" | "turn-on" | null;
  /** Its settings, `<key>: <values>`. */
  settings: string[];
  /** The settings are behind `Settings · Show` (repo's own pieces). */
  tucked: boolean;
}

/** One event's row. */
export interface EventView {
  event: string;
  /** `Before a commit`. */
  label: string;
  pieces: PieceLine[];
  /** `then its own hook, <hook>`, last; null for none. */
  own: string | null;
  /** In launcher mode, with the line in place: `run from a line in <hook>`. */
  chain: string | null;
}

/** One line of `Today`. */
export interface TodayLine {
  record: RecordRow;
  time: string;
  /** `prose-check broke`, `the auto-commit was refused`, `the push to origin failed`. */
  what: string;
  /** The record's first line. */
  first: string;
}

/** One auto-commit. */
export interface AutoView {
  spec: SpecRow;
  /** `refused since 18:45 · last commit 18:30`; null when there is nothing to say. */
  state: string | null;
  /** The dim lines under it. */
  lines: string[];
  run: Command;
  forget: Command;
}

/** `Rules`. */
export interface RulesView {
  /** `1 line a committer is told before each commit`, or the line for none. */
  head: string;
  /** The lines, shown under `Show`; empty when there are none. */
  lines: string[];
  /** What follows the lines once shown. */
  after: string[];
}

export interface OpenedView {
  events: EventView[];
  today: TodayLine[];
  autos: AutoView[];
  rules: RulesView | null;
  /** `2.56.0 · hooks run from the repo's own git config · /home/you/code/atlas-api`. */
  git: string;
  /** A moved shim that is fine, said dim under `Git`; null for none. */
  moved: string | null;
  /** The project link's key, when the repo is a project's. */
  project: string | null;
  commands: Command[];
}

/** The words for each git hook mode. */
const MODE_WORDS: Readonly<Record<string, string>> = {
  config: "hooks run from the repo's own git config",
  shim: "hooks run from scripts in its hooks folder",
  launcher: "hooks run from a line in another tool's hook",
};

/** The opened repo's words that are not facts. */
export const NO_MODULE = "no installed module declares it";
export const RULES_SAID = "What moxi repo rules prints for a committer here.";
export const RULES_PROOF = "Where commit-proof is added it ends with a Commit-Check line for what is staged; the page "
  + "leaves that line out.";
export const RULES_NONE = "none set: nothing is printed for a committer here";
export const RULES_ONLY_PROOF = "Only the Commit-Check line is printed for a committer here; the page leaves it out.";

/** A piece's settings as `<key>: <values>`. */
export function settingLines(piece: Pick<PieceRow, "settings">): string[] {
  return piece.settings.map((setting) => `${setting.key}: ${setting.values.join(", ")}`);
}

/** The events with pieces, in git's order, each with its pieces in run order. */
export function eventsOf(repo: Pick<RepoRow, "pieces" | "events" | "mode">, offered: readonly Offered[],
                         home: string | null = null): EventView[] {
  const pieces = runOrder(repo.pieces);
  const names = shownNames(pieces);
  const docs = new Map(offered.map((one) => [one.id, one.doc]));
  const events = [...new Set(pieces.map((piece) => piece.event).filter((event): event is string => event !== null))]
    .sort(byGitOrder);
  return events.map((event) => {
    const row = repo.events.find((one) => one.event === event) ?? null;
    const lines = pieces.filter((piece) => piece.event === event).map((piece): PieceLine => {
      const name = names.get(piece.id) ?? piece.id;
      const doc = docs.get(piece.id) ?? null;
      let text: string;
      if (!piece.declared) text = `${name} · ${NO_MODULE}`;
      else {
        const head = piece.enabled ? name : `${name} is off`;
        text = doc === null || doc === "" ? head : `${head} · ${doc}`;
      }
      return {
        piece, name, text, act: !piece.declared ? null : piece.enabled ? "turn-off" : "turn-on",
        settings: settingLines(piece), tucked: piece.id.startsWith("repo/"),
      };
    });
    return {
      event, label: eventLabel(event), pieces: lines,
      own: row?.existing_hook ? `then its own hook, ${homeForm(row.existing_hook, home)}` : null,
      chain: repo.mode === "launcher" && row?.chain_line === "present"
        ? `run from a line in ${row.hook === null ? "its hook" : homeForm(row.hook, home)}` : null,
    };
  });
}

/** What an open record says happened, in a few words. */
export function recordWhat(record: RecordRow, repo: Pick<RepoRow, "specs">, names: Map<string, string>): string {
  const spec = repo.specs.find((one) => one.name === record.spec) ?? null;
  if (record.kind === "piece") {
    return `${record.piece === null ? "a piece" : names.get(record.piece) ?? pieceName(record.piece)} broke`;
  }
  if (record.kind === "push") return `the push to ${spec?.remote ?? "its remote"} failed`;
  // A stale index lock's record is the open one even under a refusal, as in its item.
  if (spec?.index_stale) return "the auto-commit failed";
  if (spec?.refused_since != null) return "the auto-commit was refused";
  if (spec?.error_since != null) return "the auto-commit failed";
  return "the auto-commit said something";
}

/** The repo's open records whose `at` is today, newest first. A record whose `at` doesn't
 * read is not one of them. */
export function todayLines(repo: Pick<RepoRow, "records" | "specs" | "pieces">, today: string | null,
                           clock: Clock = LOCAL): TodayLine[] {
  if (today === null) return [];
  const names = shownNames(repo.pieces);
  return repo.records
    .map((record) => ({ record, at: recordSeconds(record.at) }))
    .filter((one): one is { record: RecordRow; at: number } => one.at !== null && clock.day(one.at) === today)
    .sort((a, b) => b.at - a.at)
    .map(({ record, at }) => ({ record, time: clock.time(at), what: recordWhat(record, repo, names),
                                first: firstLine(record.text) }));
}

/** One auto-commit: its state words, then dim, its last commit, each branch pushed (or
 * that it doesn't push), its last pass when it has no item, and that it ran unsaved. */
export function autoView(spec: SpecRow, items: readonly Item[], today: string | null, clock: Clock = LOCAL): AutoView {
  const at = (time: number): string => whenWord(time, today, clock);
  const news = specNews(spec, today, clock);
  const prefix = `Auto-commit ${spec.name}: `;
  const lines: string[] = [];
  if (spec.last_commit !== null) {
    lines.push(`last commit ${spec.last_commit.slice(0, 7)}${spec.last_commit_time === null ? ""
      : ` at ${at(spec.last_commit_time)}`}`);
  }
  if (spec.remote === null) lines.push("doesn't push");
  else for (const pushed of spec.pushed) lines.push(`${pushed.branch} pushed ${pushed.sha.slice(0, 7)}`);
  if (spec.status !== null && !items.some((item) => item.spec === spec)) {
    const when = spec.time === null ? "" : ` at ${at(spec.time)}`;
    lines.push(`last pass ${spec.status}${when}${spec.line ? `: ${spec.line}` : ""}`);
  }
  if (!spec.saved) lines.push("ran without being saved");
  return { spec, state: news === null ? null : news.startsWith(prefix) ? news.slice(prefix.length) : news, lines,
           run: spec.run, forget: spec.forget };
}

/** `Rules`, or null when its lines can't be built. */
export function rulesView(repo: Pick<RepoRow, "rules" | "proof">): RulesView | null {
  if (repo.rules === null) return null;
  if (repo.rules.length === 0) {
    return { head: repo.proof ? RULES_ONLY_PROOF : RULES_NONE, lines: [], after: [] };
  }
  return {
    head: `${counted(repo.rules.length, "line")} a committer is told before each commit`,
    lines: repo.rules, after: repo.proof ? [RULES_SAID, RULES_PROOF] : [RULES_SAID],
  };
}

/** `Git`: its version, the mode in words, pinned, and the root in full. */
export function gitLine(repo: Pick<RepoRow, "git_version" | "mode" | "pinned" | "root">): string {
  const parts: string[] = [];
  if (repo.git_version !== null) parts.push(repo.git_version);
  if (repo.mode !== null) parts.push(Object.hasOwn(MODE_WORDS, repo.mode) ? MODE_WORDS[repo.mode] : repo.mode);
  if (repo.pinned) parts.push("pinned");
  parts.push(repo.root);
  return parts.join(" · ");
}

/** The opened repo. */
export function openedView(repo: RepoRow, items: readonly Item[], offered: readonly Offered[],
                           { home = null, today = null, clock = LOCAL }:
                           { home?: string | null; today?: string | null; clock?: Clock } = {}): OpenedView {
  const moved = repo.problems.find((problem) => problem.code === "shim-moved" && problem.state === "ok") ?? null;
  return {
    events: eventsOf(repo, offered, home),
    today: todayLines(repo, today, clock),
    autos: repo.specs.map((spec) => autoView(spec, items, today, clock)),
    rules: rulesView(repo),
    git: gitLine(repo),
    moved: moved?.detail ?? null,
    project: repo.project,
    commands: repo.commands,
  };
}

/** The `Turn off` block: it runs nothing, and gives the two commands to copy. */
export interface TurnOffView {
  title: string;
  stops: string | null;
  stays: string;
  toggle: Command;
  remove: Command;
  note: string;
}

export const TURN_OFF_NOTE = "Taking a check away is done at your terminal. This page adds checks and turns them on.";

export function turnOffView(line: PieceLine, repo: Pick<RepoRow, "project" | "root">, offered: readonly Offered[],
                            home: string | null = null): TurnOffView {
  const doc = offered.find((one) => one.id === line.piece.id)?.doc ?? "";
  return {
    title: `Turn ${line.name} off in ${repoName(repo, home)}?`,
    stops: doc === "" ? null : `Stops: ${doc}`,
    stays: "Stays: its place and settings · Turn on brings it back as it was",
    toggle: line.piece.toggle, remove: line.piece.remove, note: TURN_OFF_NOTE,
  };
}

// The `Repos` list from `/api/repos`: one `<moxi-ui-row>` per repo, with the words of its
// lines and its acts, and the projects with no hooks after them. No DOM, so the Node tests
// read it.

import type { PieceRow, RepoRow, Repos, SpecRow, Unindexed } from "./feed.ts";
import { type Item, itemOrder, repoItems } from "./items-model.ts";
import {
  byGitOrder, canRefuse, type Clock, eventWords, LOCAL, pieceName, recordSeconds, refuseWord, repoName, whenWord,
} from "./words.ts";

/** One repo's row. */
export interface RowView {
  /** The repo's root: one key per row. */
  key: string;
  /** The repo, or null for a project's repo with no hooks drawn as a row. */
  repo: RepoRow | null;
  /** The project with no hooks the row stands for, when it stands for one. */
  unindexed: Unindexed | null;
  name: string;
  /** Line one's `where`: the pieces in run order. */
  where: string;
  /** Line one's fact at the right, or null. */
  fact: string | null;
  factHeavy: boolean;
  /** Line two, the first piece of news, or null. */
  lead: string | null;
  /** Line three, the rest of the news joined ` · `, or null. */
  note: string | null;
  /** The row has `Open`. */
  open: boolean;
  /** The row has `Refresh`: one of its items' fixes is a refresh. */
  refresh: boolean;
  /** The row has `Add`: a repo with no piece, or one not in repo's list. */
  add: boolean;
  /** The repo's folder is gone. */
  gone: boolean;
  /** Its items, in `Hooks`' order. */
  items: Item[];
}

/** The dim line after the rows: `No hooks: <keys>`, each project with its `Add`. */
export interface NoHooks {
  text: string;
  projects: Unindexed[];
}

export interface ReposView {
  rows: RowView[];
  /** Null without projects, with none to name, or when each is a row of its own. */
  noHooks: NoHooks | null;
}

/** A repo's pieces in run order: events in git's order (a piece with no event after them),
 * then `order`, then id. */
export function runOrder(pieces: readonly PieceRow[]): PieceRow[] {
  return [...pieces].sort((a, b) => {
    if (a.event !== b.event) {
      if (a.event === null) return 1;
      if (b.event === null) return -1;
      return byGitOrder(a.event, b.event);
    }
    return a.order - b.order || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  });
}

/** Each piece's shown name: its name, or its id where two shown pieces share a name. */
export function shownNames(pieces: readonly Pick<PieceRow, "id">[]): Map<string, string> {
  const count = new Map<string, number>();
  for (const piece of pieces) count.set(pieceName(piece.id), (count.get(pieceName(piece.id)) ?? 0) + 1);
  return new Map(pieces.map((piece) => {
    const name = pieceName(piece.id);
    return [piece.id, (count.get(name) ?? 0) > 1 ? piece.id : name];
  }));
}

/** Line one's `where`: each piece by name in run order (`<name> off` for one that is off),
 * `, then its own hook` after the last piece of an event whose own git hook runs after
 * them, then `its own hook <event words>` for each event with no piece but a hook of his. */
export function whereLine(repo: Pick<RepoRow, "pieces" | "events" | "own_hooks">): string {
  const pieces = runOrder(repo.pieces);
  const names = shownNames(pieces);
  const own = new Set(repo.events.filter((event) => event.existing_hook !== null).map((event) => event.event));
  const parts = pieces.map((piece, at) => {
    const name = names.get(piece.id) ?? piece.id;
    let text = piece.enabled ? name : `${name} off`;
    const last = at === pieces.length - 1 || pieces[at + 1].event !== piece.event;
    if (last && piece.event !== null && own.has(piece.event)) text += ", then its own hook";
    return text;
  });
  for (const event of [...repo.own_hooks].sort(byGitOrder)) parts.push(`its own hook ${eventWords(event)}`);
  return parts.join(" · ");
}

/** A need item's short words for its row's fact. */
export function needWords(item: Item): string | null {
  switch (item.source) {
    case "no-module": {
      const event = item.problem?.event ?? "";
      return canRefuse(event) ? `refuses every ${refuseWord(event)}` : "a piece has no module";
    }
    case "refused": return "auto-commit refused";
    case "failed": return "auto-commit failed";
    case "held": return "auto-commit held up";
    case "push": return "can't push";
    case "piece": return "a piece broke";
    case "problem": return item.problem?.code === "unreadable" ? null : "hooks can't run";
    default: return null;
  }
}

/** Line one's fact: heavy, a need's short words; else, at normal weight, `the folder is
 * gone`, `can't be read`, `no hooks` (in the list with no piece) or `not in the list` (a
 * project's repo with pieces that isn't). */
export function factOf(repo: RepoRow, items: readonly Item[]): { fact: string | null; heavy: boolean } {
  for (const item of items) {
    if (item.weight !== "need") continue;
    const words = needWords(item);
    if (words !== null) return { fact: words, heavy: true };
  }
  if (repo.problems.some((problem) => problem.code === "gone")) return { fact: "the folder is gone", heavy: false };
  if (repo.problems.some((problem) => problem.code === "unreadable")) return { fact: "can't be read", heavy: false };
  if (repo.indexed && repo.pieces.length === 0) return { fact: "no hooks", heavy: false };
  if (!repo.indexed && repo.pieces.length > 0) return { fact: "not in the list", heavy: false };
  return { fact: null, heavy: false };
}

/** A spec's news: refused or failed since, else its last commit and whether it's pushed.
 * `pushed` only for a spec with a remote, no branch left unpushed and no push failing. */
export function specNews(spec: SpecRow, today: string | null, clock: Clock = LOCAL): string | null {
  const at = (time: number): string => whenWord(time, today, clock);
  const last = spec.last_commit_time === null ? null : `last commit ${at(spec.last_commit_time)}`;
  const head = `Auto-commit ${spec.name}: `;
  if (spec.refused_since !== null) return head + [`refused since ${at(spec.refused_since)}`, last].filter(Boolean).join(" · ");
  if (spec.error_since !== null) return head + [`failed since ${at(spec.error_since)}`, last].filter(Boolean).join(" · ");
  if (last === null) return null;
  const pushed = spec.remote !== null && spec.unpushed.length === 0 && spec.push_failed.length === 0;
  return head + last + (pushed ? ", pushed" : "");
}

/** The row's news in order: each open `piece` record's break, then each spec's line. */
export function newsOf(repo: RepoRow, names: Map<string, string>, today: string | null, clock: Clock = LOCAL): string[] {
  const out: string[] = [];
  for (const record of repo.records) {
    if (record.kind !== "piece") continue;
    const name = record.piece === null ? "a piece" : names.get(record.piece) ?? pieceName(record.piece);
    const time = recordSeconds(record.at);
    out.push(time === null ? `${name} broke` : `${name} broke at ${whenWord(time, today, clock)}`);
  }
  for (const spec of repo.specs) {
    const line = specNews(spec, today, clock);
    if (line !== null) out.push(line);
  }
  return out;
}

/** One repo's row. */
export function repoRow(repo: RepoRow, { home = null, today = null, clock = LOCAL }:
  { home?: string | null; today?: string | null; clock?: Clock } = {}): RowView {
  const items = repoItems(repo, { home, today, clock }).sort(itemOrder);
  const { fact, heavy } = factOf(repo, items);
  const news = newsOf(repo, shownNames(repo.pieces), today, clock);
  const gone = repo.problems.some((problem) => problem.code === "gone");
  return {
    key: repo.root, repo, unindexed: null, name: repoName(repo, home), where: whereLine(repo), fact, factHeavy: heavy,
    lead: news[0] ?? null, note: news.length > 1 ? news.slice(1).join(" · ") : null, open: true,
    refresh: items.some((item) => item.fix?.refresh === true), add: fact === "no hooks" || fact === "not in the list",
    gone, items,
  };
}

/** A project's repo with no hooks, drawn as a row of its own. */
export function unindexedRow(project: Unindexed): RowView {
  return { key: project.root, repo: null, unindexed: project, name: project.project, where: "", fact: "no hooks",
           factHeavy: false, lead: null, note: null, open: false, refresh: false, add: true, gone: false, items: [] };
}

const compare = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

/** Repos with a need first, by their first item; then the rest, project keys A to Z, then
 * `~` folders A to Z; a gone repo last. */
export function rowOrder(a: RowView, b: RowView): number {
  const gone = Number(a.gone) - Number(b.gone);
  if (gone !== 0) return gone;
  const needA = a.items.find((item) => item.weight === "need");
  const needB = b.items.find((item) => item.weight === "need");
  if (needA !== undefined && needB !== undefined) return itemOrder(needA, needB);
  if (needA !== undefined || needB !== undefined) return needA !== undefined ? -1 : 1;
  const folder = (row: RowView): number => Number(row.unindexed === null && row.repo?.project === null);
  return folder(a) - folder(b) || compare(a.name, b.name);
}

/** The `Repos` list: a row per listed repo in `rowOrder`, then the projects with no hooks.
 * With no repo in the list, each of those is a row reading `no hooks`; else one dim line
 * names them. Without projects there is neither. */
export function reposView(value: Pick<Repos, "repos" | "today" | "unindexed">,
                          { home = null, clock = LOCAL }: { home?: string | null; clock?: Clock } = {}): ReposView {
  const rows = value.repos.map((repo) => repoRow(repo, { home, today: value.today, clock }));
  const projects = [...(value.unindexed ?? [])].sort((a, b) => compare(a.project, b.project));
  if (!value.repos.some((repo) => repo.indexed)) {
    rows.push(...projects.map(unindexedRow));
    return { rows: rows.sort(rowOrder), noHooks: null };
  }
  rows.sort(rowOrder);
  if (projects.length === 0) return { rows, noHooks: null };
  return { rows, noHooks: { text: `No hooks: ${projects.map((p) => p.project).join(", ")}`, projects } };
}

/** The `Add` buttons of the `No hooks:` line, each opening `Add a repo` with that project's
 * root filled: `Add` for one project, `Add <key>` for each of several. */
export function noHooksActs(noHooks: NoHooks): { label: string; project: Unindexed }[] {
  const one = noHooks.projects.length === 1;
  return noHooks.projects.map((project) => ({ label: one ? "Add" : `Add ${project.project}`, project }));
}

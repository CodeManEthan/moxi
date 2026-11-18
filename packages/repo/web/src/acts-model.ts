// The words of the acts in place: `Add a piece`'s list with each piece's place, the
// confirm drawn from a plan (every item it would write, and nothing it doesn't hold), and
// the answer an act gets. No DOM, so the Node tests read them.

import { failureWhy } from "./feed.ts";
import type { Answer, Command, Offered, PieceRow, Plan, PlannedPiece, RepoRow, WriteItem } from "./feed.ts";
import { offeredOrder } from "./pieces-model.ts";
import { shownNames } from "./rows-model.ts";
import { byGitOrder, counted, eventWords, homeForm, joinAnd, pieceName } from "./words.ts";

/** Which act a confirm is for: `add` is `Add a piece` in an opened repo, `add-repo` is `Add
 * a repo` (and a row's `Add`), `enable` is `Turn on`. */
export type Act = "add" | "add-repo" | "enable" | "refresh";

/** The commit-proof piece: a merge or rebase under way is refused until its message
 * carries the line. */
const COMMIT_PROOF = "repo/commit-proof";

/** A text as a sentence: its own end stop kept, else a full stop added. */
export function sentence(text: string): string {
  const trimmed = text.trimEnd();
  return /[.!?:]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

// -- Add a piece --------------------------------------------------------------------------

/** Where a piece would run among the pieces registered on its event, by `order` then id:
 * `first` with none there, `before <x>` ahead of every one, else `after <y>`, the one just
 * ahead of it. `names` gives each piece's shown name. */
export function placeWords(piece: Pick<Offered, "id" | "event" | "order">, registered: readonly PieceRow[],
                           names: ReadonlyMap<string, string> = new Map()): string {
  const others = registered.filter((other) => other.event === piece.event && other.id !== piece.id);
  if (others.length === 0) return "first";
  const all = [...others, piece].map(({ id, order }) => ({ id, order }))
    .sort((a, b) => a.order - b.order || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const at = all.findIndex((other) => other.id === piece.id);
  const name = (id: string): string => names.get(id) ?? pieceName(id);
  return at === 0 ? `before ${name(all[1].id)}` : `after ${name(all[at - 1].id)}`;
}

/** One row of `Add a piece`. */
export interface AddRow {
  piece: Offered;
  name: string;
  /** `on the message, after commit-rules`. */
  where: string;
  doc: string;
  picked: boolean;
  /** `Add`, or `Picked` once picked. */
  button: string;
}

/** `Add a piece`: each offered piece not registered in the repo, in run order, with its
 * place among that event's pieces. */
export function addRows(repo: Pick<RepoRow, "pieces">, offered: readonly Offered[],
                        picked: ReadonlySet<string> = new Set()): AddRow[] {
  const here = new Set(repo.pieces.map((piece) => piece.id));
  const rows = offeredOrder(offered.filter((piece) => !here.has(piece.id)));
  const names = shownNames([...repo.pieces, ...rows]);
  return rows.map((piece) => {
    const on = picked.has(piece.id);
    return {
      piece, name: names.get(piece.id) ?? piece.name, doc: piece.doc, picked: on, button: on ? "Picked" : "Add",
      where: `${eventWords(piece.event)}, ${placeWords(piece, repo.pieces, names)}`,
    };
  });
}

// -- the confirm --------------------------------------------------------------------------

/** A line he adds to another tool's hook, with `Copy` for the line. */
export interface ChainView {
  text: string;
  line: string;
}

/** A confirm drawn from a plan. */
export interface ConfirmView {
  /** `Add commit-proof to atlas-api?`; null for `Add a repo`, which has its folder line. */
  title: string | null;
  /** The plan's refusal as a sentence: no lines and no button then. */
  refusal: string | null;
  /** When the act would change nothing, the one line that says so: no button then, and
   * `Close`. */
  idle: string | null;
  /** Each line after `Writes`, by place. */
  writes: string[];
  /** The lines after `Then`. */
  then: string[];
  /** Under `Then`: in launcher mode, each line the pieces need in another tool's hook. */
  chain: ChainView[];
  /** The line after the label Leaves. */
  leaves: string | null;
  /** The button that sends the act; null when the plan refuses. */
  go: string | null;
  cancel: string;
  /** What the act sends back, so the verb writes only what this confirm drew. */
  digest: string | null;
}

/** What a confirm needs beside its plan. */
export interface ConfirmContext {
  act: Act;
  /** The repo's name on the page. */
  repo: string;
  /** The pieces he picked (`add`, `add-repo`) or turns on (`enable`), by id. */
  picked: readonly string[];
  /** The pieces offered: each one's name and doc. */
  offered: readonly Offered[];
  home: string | null;
}

/** Each source of hooks, as the opened repo's `Git` row words its mode. */
const SOURCES: Readonly<Record<string, string>> = {
  config: "the repo's own git config",
  shim: "scripts in its hooks folder",
};
const sourceOf = (mode: string): string => (Object.hasOwn(SOURCES, mode) ? SOURCES[mode] : mode);

const ENTRY = "hook.moxi-repo-";
const PIECE = "moxi-repo.piece.";
const MODE = "moxi-repo.mode";
const MODE_SET = "moxi-repo.mode-set";
const MOVED = "moxi-repo.moved";
/** The keys a declaration gives, with the `enabled` a new registration gets. */
const REGISTRATION = new Set(["event", "target", "fail", "order", "enabled"]);

/** A piece's setting or registration key's last part: `fail` of
 * `moxi-repo.piece.repo/commit-rules.fail`. */
function varOf(key: string, id: string): string {
  return key.slice(PIECE.length + id.length + 1);
}

/** The events of `items` in run order, in words: `before a commit and on the message`. */
function eventsWords(items: readonly WriteItem[]): string {
  const events = [...new Set(items.map((item) => item.event ?? ""))].filter(Boolean).sort(byGitOrder);
  return joinAnd(events.map(eventWords));
}

/** The words of one seed: `salt`, drawn as it is written, or `tag-pattern set to <value>`. */
function seedWords(item: WriteItem, id: string): string {
  const name = varOf(item.key ?? "", id);
  return item.to === null ? name : `${name} set to ${item.to}`;
}

/** The `Writes` lines of a plan, and the index of every item they draw. */
export function writesOf(plan: Pick<Plan, "writes" | "pieces">, { act, offered = [], home = null }:
  { act: Act; offered?: readonly Offered[]; home?: string | null }): { lines: string[]; drawn: Set<number> } {
  const drawn = new Set<number>();
  const writes = plan.writes.map((item, at) => ({ item, at }));
  const take = (test: (item: WriteItem) => boolean): { item: WriteItem; at: number }[] => {
    const found = writes.filter(({ item, at }) => !drawn.has(at) && test(item));
    for (const { at } of found) drawn.add(at);
    return found;
  };
  const named = new Map(offered.map((piece) => [piece.id, piece.name]));
  const nameOf = (id: string): string => named.get(id) ?? pieceName(id);
  const config: string[] = [];

  for (const piece of plan.pieces) config.push(...pieceParts(piece, act, nameOf(piece.id), take));
  const entries = take((item) => item.place === "config" && (item.key ?? "").startsWith(ENTRY));
  const set = entries.filter(({ item }) => item.to !== null).map(({ item }) => item);
  const unset = entries.filter(({ item }) => item.to === null).map(({ item }) => item);
  if (set.length > 0) config.push(`the hook entries for ${eventsWords(set)}`);
  if (unset.length > 0) config.push(`takes away the hook entries for ${eventsWords(unset)}`);
  const mode = take((item) => item.place === "config" && item.key === MODE)[0]?.item;
  const pin = take((item) => item.place === "config" && item.key === MODE_SET)[0]?.item;
  const pinned = pin === undefined || pin.to === null ? null : pin.to === "auto" ? "as git's version picks" : "pinned";
  if (mode !== undefined && mode.to !== null) {
    const words = `hooks run from ${sourceOf(mode.to)}`;
    const was = mode.from === null ? "" : ` now, not from ${sourceOf(mode.from)}`;
    config.push(words + was + (pinned === null ? "" : `, ${pinned}`));
  } else if (pinned !== null) {
    config.push(pinned === "pinned" ? `its mode pinned to ${pin?.to}` : "its mode as git's version picks");
  }
  for (const { item } of take((item) => item.place === "config" && item.key === MOVED)) {
    config.push(item.to === null ? "notes that none of its own hooks is moved aside"
      : `notes its own hooks moved aside (${item.to})`);
  }
  for (const { item } of take((item) => item.place === "config")) {
    const was = item.from === null ? "" : ` (it reads ${item.from} here)`;
    config.push(item.to === null ? `takes out ${item.key}${was}` : `sets ${item.key} to ${item.to}${was}`);
  }

  const hooks: string[] = [];
  const shims = take((item) => item.place === "hooks" && item.key === "shim").map(({ item }) => item);
  const placed = shims.filter((item) => item.to !== null);
  const gone = shims.filter((item) => item.to === null);
  if (placed.length > 0) hooks.push(`scripts for ${eventsWords(placed)}`);
  if (gone.length > 0) hooks.push(`takes away the scripts for ${eventsWords(gone)}`);
  for (const { item } of take((item) => item.place === "hooks" && item.key === "hook")) {
    const event = item.event ?? "";
    hooks.push((item.to ?? "").endsWith(".local")
      ? `moves its own ${event} hook to ${event}.local, where it still runs after the pieces`
      : `moves its own ${event} hook back from ${event}.local`);
  }
  for (const { item } of take((item) => item.place === "hooks" && item.key === "launcher")) {
    hooks.push(item.to === null ? "takes away its launcher" : "a launcher");
  }
  for (const { item } of take((item) => item.place === "hooks")) {
    hooks.push(item.to === null ? `takes away ${homeForm(item.from ?? "", home)}` : homeForm(item.to, home));
  }

  const index = take((item) => item.place === "index").map(({ item }) => item);
  // Chain lines are what he adds, drawn under `Then`.
  take((item) => item.place === "chain");
  // An item of a place this page doesn't know is drawn as it is.
  const other = take(() => true).map(({ item }) =>
    [item.place, item.key, item.from === null ? null : `from ${item.from}`, item.to === null ? null : `to ${item.to}`]
      .filter(Boolean).join(" "));

  const lines: string[] = [];
  const repo = act === "add-repo" ? "that repo's" : "this repo's";
  if (config.length > 0) lines.push(`into ${repo} git config · ${config.join(" · ")}`);
  if (hooks.length > 0) lines.push(`in its hooks folder · ${hooks.join(" · ")}`);
  for (const item of index) {
    lines.push(item.from === null || item.from === item.to ? "into repo's list"
      : `into repo's list (it was listed at ${homeForm(item.from, home)})`);
  }
  lines.push(...other);
  if (act === "add-repo" && config.length === 0 && hooks.length === 0 && other.length === 0 && index.length > 0) {
    return { lines: ["nothing in the repo; it joins this list"], drawn };
  }
  if (lines.length === 0) lines.push("nothing; it is already so");
  return { lines, drawn };
}

/** The `Writes` parts of one planned piece: `<name>, new` with its seeds, `<name> turned
 * on`, or `<name>, rewritten` with each declared key it puts back. */
function pieceParts(piece: PlannedPiece, act: Act, name: string,
                    take: (test: (item: WriteItem) => boolean) => { item: WriteItem; at: number }[]): string[] {
  const own = (item: WriteItem): boolean => item.place === "config" && (item.key ?? "").startsWith(`${PIECE}${piece.id}.`);
  const parts: string[] = [];
  if (piece.new) {
    const items = take(own).map(({ item }) => item);
    const seeds = items.filter((item) => item.seed).map((item) => seedWords(item, piece.id));
    const why = piece.implied_by === null ? "" : ` (${piece.implied_by} is on)`;
    parts.push(`${name}, new${why}${seeds.length > 0 ? `, and its ${joinAnd(seeds)}` : ""}`);
    return parts;
  }
  if (act === "enable") {
    const on = take((item) => own(item) && varOf(item.key ?? "", piece.id) === "enabled");
    if (on.length > 0) parts.push(`${name} turned on`);
  }
  const back = take((item) => own(item) && REGISTRATION.has(varOf(item.key ?? "", piece.id)) && item.to !== null);
  if (back.length > 0) parts.push(`${name}, rewritten`);
  for (const { item } of back) {
    const key = varOf(item.key ?? "", piece.id);
    const was = item.from === null ? "it isn't set here" : `it reads ${item.from} here`;
    parts.push(`puts ${name}'s ${key} back to ${item.to} (${was})`);
  }
  for (const { item } of take(own)) {
    const key = varOf(item.key ?? "", piece.id);
    parts.push(item.to === null ? `takes out ${name}'s ${key}` : `sets ${name}'s ${key} to ${item.to}`);
  }
  return parts;
}

/** Whether a plan writes something of a piece: it is new, or the plan sets or takes out a
 * key of its own. */
function writesPiece(plan: Pick<Plan, "writes">, piece: PlannedPiece): boolean {
  return piece.new || plan.writes.some((item) => item.place === "config"
    && (item.key ?? "").startsWith(`${PIECE}${piece.id}.`));
}

/** The pieces an act sends: those he picked that the plan writes. A picked piece the plan
 * lists but writes nothing of (another hand put it in since he picked it, and a `changed`
 * answer brought the new plan) is left out, so the verb's answer names only what the
 * question named. The plan's writes are the same without it, so its digest still holds. */
export function sentPieces(plan: Pick<Plan, "writes" | "pieces">, picked: readonly string[]): string[] {
  const idle = new Set(plan.pieces.filter((piece) => !writesPiece(plan, piece)).map((piece) => piece.id));
  return picked.filter((id) => !idle.has(id));
}

/** What a confirm says in place of its lines when the act would change nothing. */
export const IDLE = "Nothing to write: it is already so.";

/** Whether the act would change nothing: its plan writes nothing (a chain line is his to
 * add, not a write), or he picked pieces to add or turn on and it writes none of them. */
function idleOf(plan: Pick<Plan, "writes" | "pieces">, act: Act, picked: readonly string[]): boolean {
  if (plan.writes.every((item) => item.place === "chain")) return true;
  return (act === "add" || act === "enable") && picked.length > 0 && sentPieces(plan, picked).length === 0;
}

/** The pieces a confirm names: those the act sends; with none left, every one he
 * picked. */
export function namedPieces(plan: Pick<Plan, "writes" | "pieces">, picked: readonly string[]): string[] {
  const kept = sentPieces(plan, picked);
  return kept.length > 0 ? kept : [...picked];
}

/** The confirm of an act, drawn from its plan. The question and button name what the plan
 * writes, so after `changed` they follow the new plan. An act that would change nothing is
 * not offered: one line says so, with `Close` and no button. */
export function confirmView(plan: Plan, context: ConfirmContext): ConfirmView {
  const { act, repo, offered, home } = context;
  const picked = namedPieces(plan, context.picked);
  const named = new Map(offered.map((piece) => [piece.id, piece]));
  const nameOf = (id: string): string => named.get(id)?.name ?? pieceName(id);
  const names = joinAnd(picked.map(nameOf));
  const title = act === "add" ? `Add ${names} to ${repo}?`
    : act === "enable" ? `Turn ${names} on in ${repo}?`
    : act === "refresh" ? `Refresh ${repo}?` : null;
  const view: ConfirmView = {
    title, refusal: null, idle: null, writes: [], then: [], chain: [], leaves: null, go: null, cancel: "Cancel",
    digest: null,
  };
  if (plan.refusal !== null) return { ...view, refusal: sentence(plan.refusal.text) };
  view.chain = plan.writes.filter((item) => item.place === "chain" && item.to !== null).map((item) => ({
    text: `the pieces run once this line is in ${homeForm(item.from ?? "", home)}:`, line: item.to ?? "",
  }));
  if (idleOf(plan, act, context.picked)) return { ...view, idle: IDLE, cancel: "Close" };

  view.writes = writesOf(plan, { act, offered, home }).lines;
  const docs = (act === "enable" ? picked : plan.pieces.filter((piece) => piece.new).map((piece) => piece.id))
    .filter((id) => named.has(id));
  view.then = docs.length === 1 ? [named.get(docs[0])?.doc ?? ""]
    : docs.map((id) => `${nameOf(id)}: ${named.get(id)?.doc ?? ""}`);
  if (plan.in_progress !== null && plan.pieces.some((piece) => piece.id === COMMIT_PROOF)) {
    view.then.push(`A ${plan.in_progress} is in progress here: its commit is refused until its message carries the `
                   + "Commit-Check line.");
  }

  const moves = plan.writes.some((item) => item.place === "hooks" && item.key === "hook");
  const added = plan.pieces.filter((piece) => writesPiece(plan, piece)).length;
  if (act === "add-repo") {
    view.leaves = plan.own_hooks.length > 0 && !moves
      ? "its files alone · a hook it already has keeps running, after the pieces" : "its files alone";
  } else {
    view.leaves = (moves ? "the repo's files" : "the repo's files and its other hooks as they are")
      + (act === "add" ? " · taking it out again is a terminal command" : "");
  }
  view.go = act === "add" ? `Add ${names}`
    : act === "enable" ? "Turn it on"
    : act === "refresh" ? "Refresh"
    : added === 0 ? "Add to the list" : `Add ${counted(added, "piece")}`;
  view.digest = plan.digest;
  return view;
}

/** The question over a closed row's `Add`, which puts the repo in repo's list with the
 * pieces its settings imply. */
export function rowAddTitle(repo: string): string {
  return `Add ${repo}?`;
}

/** The repo's `Refresh` command, for `partial`'s answer; null when it has none. */
export function refreshCommand(repo: Pick<RepoRow, "commands"> | null | undefined): Command | null {
  return repo?.commands.find((command) => command.label === "Refresh") ?? null;
}

/** `Add a repo`'s line under the folder: `a git repo · git 2.56.0`, or why it can't be
 * added. */
export function folderLine(plan: Pick<Plan, "is_repo" | "git_version" | "refusal">): string {
  if (plan.refusal !== null) return sentence(plan.refusal.text);
  if (!plan.is_repo) return "not a git repo";
  return plan.git_version === null ? "a git repo" : `a git repo · git ${plan.git_version}`;
}

/** What shows when a plan can't be read: the page server's own words with `state` `error`,
 * else what went wrong with the request. */
export function planFailWords(failure: { detail: string } | { status: number }): string {
  if ("detail" in failure) return sentence(failure.detail);
  return `The plan can't be read: ${failureWhy(failure.status)}.`;
}

// -- answers ------------------------------------------------------------------------------

/** What an act's answer shows under its confirm. */
export interface AnswerView {
  /** The first line, or null. */
  head: string | null;
  heavy: boolean;
  /** The lines under it. */
  lines: string[];
  /** The lines are the verb's report, drawn dim. */
  dim: boolean;
  /** Problems and chain lines still needed, each with `Copy`. */
  copies: { text: string; copy: string }[];
  /** After `partial`: the `Refresh` act, and its command to copy when the repo's row has
   * one. */
  refresh: boolean;
  command: Command | null;
  /** After `changed`: the new plan, whose confirm is drawn in place. */
  plan: Plan | null;
  /** The page reads `/api/repos` again. */
  reread: boolean;
}

const blank: AnswerView = {
  head: null, heavy: false, lines: [], dim: false, copies: [], refresh: false, command: null, plan: null, reread: true,
};

/** Each chain line an answer says is still needed, with `Copy`. */
function chainCopies(answer: Pick<Answer, "chain">, home: string | null): { text: string; copy: string }[] {
  return answer.chain.filter((chain) => !chain.present).map((chain) => ({
    text: `the pieces run once this line is in ${homeForm(chain.hook, home)}:`, copy: chain.line,
  }));
}

/** An act's answer in words: `done` (the verb's lines dim, its problems and needed chain
 * lines with `Copy`), `nothing`, `changed` (the new plan), `refused`, `partial` (with
 * Refresh) and `failed`. `command` is the repo's refresh command, when its row has one. */
export function answerView(answer: Answer, { home = null, command = null }:
  { home?: string | null; command?: Command | null } = {}): AnswerView {
  const text = answer.refusal === null ? null : sentence(answer.refusal.text);
  switch (answer.outcome) {
    case "done":
      return { ...blank, lines: answer.lines, dim: true,
               copies: [...answer.problems.map((problem) => ({ text: problem, copy: problem })),
                        ...chainCopies(answer, home)] };
    case "nothing":
      return { ...blank, head: "Nothing to write: it is already so.", copies: chainCopies(answer, home) };
    case "changed":
      return { ...blank, head: "This changed since you looked:", plan: answer.plan };
    case "partial":
      return { ...blank, head: "It may be partly done.", heavy: true,
               lines: [...(text === null ? [] : [text]), "Refresh puts back every entry repo keeps here."],
               refresh: true, command };
    case "refused":
      return { ...blank, head: "Not done.", heavy: true, lines: text === null ? [] : [text] };
    default:
      return { ...blank, head: "Not done.", heavy: true, lines: ["repo's page server hit an error; its log has the details."] };
  }
}

/** What an act says when its request got no readable answer: with no answer (status 0),
 * a 502 or 504 (the page server didn't answer, and may have acted) or a 200 that can't be
 * read, it may have been done, and the page reads the repos again; else the shell's
 * status. */
export function transportView(status: number): AnswerView {
  if (status === 0 || status === 200 || status === 502 || status === 504) {
    return { ...blank, head: "It may have been done." };
  }
  const head = status === 400 ? "The shell refused the request (400)." : `The shell answered ${status}.`;
  return { ...blank, head, reread: false };
}

// -- requests -----------------------------------------------------------------------------

/** The plan's path for an act: `api/plan?act=…&root=…&pieces=…`; `Add a repo` plans an add. */
export function planPath(act: Act, root: string, picked: readonly string[]): string {
  const query = new URLSearchParams({ act: act === "add-repo" ? "add" : act, root });
  if (picked.length > 0) query.set("pieces", picked.join(","));
  return `api/plan?${query.toString()}`;
}

/** The act's path and body: the plan's digest sent back, so the verb writes only what the
 * confirm drew. */
export function actRequest(act: Act, root: string, picked: readonly string[], digest: string):
  { path: string; body: Record<string, unknown> } {
  if (act === "enable") return { path: "api/enable", body: { root, piece: picked[0], digest } };
  if (act === "refresh") return { path: "api/refresh", body: { root, digest } };
  return { path: "api/add", body: { root, pieces: [...picked], digest } };
}

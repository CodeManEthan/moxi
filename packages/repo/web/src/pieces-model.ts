// `Pieces`: what each piece installed modules offer does and where it is in, folded to one
// line, and the sentence its fail policies make. No DOM, so the Node tests read it.

import type { Offered, Repos } from "./feed.ts";
import { byGitOrder, counted, eventWords, joinAnd } from "./words.ts";

/** One offered piece's row. */
export interface PieceView {
  piece: Offered;
  name: string;
  /** `from notes`. */
  from: string;
  /** `in 4 repos, off in 1`, `in no repo`. */
  where: string;
  /** `before a commit`; git's name is drawn dim after it. */
  event: string;
  git: string;
  doc: string;
}

export interface PiecesView {
  /** `6 offered`. */
  count: string;
  /** `commit-rules, commit-proof, secret-gate and 3 more`; empty with none offered. */
  names: string;
  rows: PieceView[];
  /** What happens when a piece breaks; null with none offered. */
  sentence: string | null;
  /** `<id> can't be offered: <why>`, one per refused declaration. */
  refused: string[];
}

/** Offered pieces in run order: events in git's order, then `order`, then id. */
export function offeredOrder(pieces: readonly Offered[]): Offered[] {
  return [...pieces].sort((a, b) => byGitOrder(a.event, b.event) || a.order - b.order
    || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

/** Where a piece is registered across the listed repos: `in 2 repos`, `in 4 repos, off in
 * 1`, `in no repo`. */
export function whereWords(id: string, repos: Repos["repos"]): string {
  const held = repos.flatMap((repo) => repo.pieces.filter((piece) => piece.id === id));
  if (held.length === 0) return "in no repo";
  const off = held.filter((piece) => !piece.enabled).length;
  return `in ${counted(held.length, "repo")}${off > 0 ? `, off in ${off}` : ""}`;
}

/** The fold line's names: the first three, `and <k> more` past them. */
export function namesLine(names: readonly string[]): string {
  if (names.length <= 3) return names.join(", ");
  return `${names.slice(0, 3).join(", ")} and ${names.length - 3} more`;
}

/** The sentence from the fail policies: closed pieces refuse the commit or push, open ones
 * let it go on and leave a record; and that a piece is in no repo until added. */
export function failSentence(pieces: readonly Offered[]): string | null {
  if (pieces.length === 0) return null;
  const open = pieces.filter((piece) => piece.fail === "open").map((piece) => piece.name);
  const closed = pieces.length - open.length;
  const lead = "If one of these breaks, ";
  const goes = `${joinAnd(open)} ${open.length === 1 ? "lets it go on and leaves" : "let it go on and leave"} a record`;
  let text: string;
  if (closed > 0 && open.length > 0) text = `${lead}the commit or push is refused; ${goes}`;
  else if (closed > 0) text = `${lead}the commit or push is refused`;
  else text = `${lead}${goes}`;
  return `${text}. A piece is in no repo until you add it there.`;
}

export function piecesView(value: Pick<Repos, "offered" | "refused" | "repos">): PiecesView {
  const pieces = offeredOrder(value.offered);
  return {
    count: `${pieces.length} offered`,
    names: namesLine(pieces.map((piece) => piece.name)),
    rows: pieces.map((piece) => ({
      piece, name: piece.name, from: `from ${piece.module}`, where: whereWords(piece.id, value.repos),
      event: eventWords(piece.event), git: piece.event, doc: piece.doc,
    })),
    sentence: failSentence(pieces),
    refused: value.refused.map((row) => `${row.id} can't be offered: ${row.why}`),
  };
}

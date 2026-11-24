// The tray of the operator's answers on rounds' page: each draft with its act id, what
// the row says of who records it, the body `POST /api/answers` takes, what each receipt
// says, and the tray kept in the browser's storage across a reload, one per page
// server's address. No DOM and no Lit here, so the Node tests drive it with a stub
// storage.
//
// On Send the page records an option picked, a decision accepted or rejected, a ruling
// read back, a job done, a park and an unpark itself; the operator's own words, a
// correction and a request to explain go in the message to the chosen agent, who words
// and records them. A draft whose round has no recipient stays in the tray.

import { timeWord } from "./model.ts";
import type { Clock } from "./model.ts";
import type { Waiting } from "./feed.ts";

/** The kinds of draft, as the answers route takes them. */
export type Kind = "pick" | "accept" | "reject" | "read-back" | "done" | "park" | "unpark"
  | "words" | "correct" | "explain";

/** The kinds the page records on Send. */
export const PAGE_KINDS: readonly Kind[] = ["pick", "accept", "reject", "read-back", "done", "park", "unpark"];
/** The kinds the chosen agent words and records, or that need no record. */
export const AGENT_KINDS: readonly Kind[] = ["words", "correct", "explain"];

/** The To choice that sends each draft to its own round's lead. */
export const EACH = "each";

/** What the answers route says of one draft, as `page.py`'s `RECEIPT` declares it. */
export interface Receipt {
  act: string;
  tell?: string | null;
  holder: string;
  item: string;
  kind: string;
  /** `recorded`, `recorded-not-told`, `sent`, `refused` or `kept`. */
  state: string;
  reason?: string | null;
  fix?: string | null;
  busy?: boolean;
  to?: string | null;
  record?: string | null;
  at?: string | null;
  ruling?: { id: string | null; text: string | null } | null;
  marked?: string[];
  already?: boolean;
  done?: string | null;
  closed?: boolean;
  dropped?: string | null;
  again?: boolean;
  repeat?: boolean;
  left?: number | null;
  freed?: string[];
  message?: { to: string | null; at: string | null; id: string | null; delivery: string | null } | null;
  not_told?: string | null;
  command?: string | null;
  branch?: string | null;
  tip?: string | null;
  removed?: string[];
  kept?: string[];
  refs?: string[];
  unscanned?: string[];
}

/** One message a send made, or comms' refusal of it. */
export interface Message {
  to: string;
  at: string | null;
  id: string | null;
  delivery: string | null;
  refused: string | null;
}

/** The ruling a question's record holds, as `/api/record` gives it. */
export interface RulingOf {
  id: string;
  text: string | null;
  record: string | null;
  actor: string | null;
  at: string | null;
  shown: boolean;
}

/** One answer in the tray. */
export interface Draft {
  /** The act id, minted when the draft is made. */
  act: string;
  /** The round (`<project>/<slug>`), or the project for a project's item. */
  holder: string;
  /** The item it answers: `Q<n>`, `C<n>`, `B<n>.M<k>`, `B<n>.W<k>`. */
  item: string;
  kind: Kind;
  chose: string | null;
  /** The operator's words: an answer, a correction, a done job's note or what to explain. */
  text: string | null;
  /** The id of a send again, minted at the click that sends it again. */
  tell: string | null;
  /** What the item says, for the row. */
  about: string;
  /** The last answer to this draft; null for one never sent. */
  receipt: Receipt | null;
  /** For a sent own-words answer, the ruling the agent recorded once it appears. */
  found: RulingOf | null;
}

/** The tray's value as it is kept. */
export interface TrayValue {
  drafts: Draft[];
  sent: Draft[];
  messages: Message[];
}

/** What the page knows that a row's words need. */
export interface Reach {
  /** Whether comms is installed. */
  comms: boolean;
  /** The To choice: `EACH` or one agent's name. */
  to: string;
  /** A holder's lead's name; null for none, undefined when the page can't tell. */
  leadOf: (holder: string) => string | null | undefined;
}

/** The shape of an act id: 43 characters of `[A-Za-z0-9_-]`. */
export const ACT_ID = /^[A-Za-z0-9_-]{43}$/;

/** A fresh act id: 32 random bytes in base64url, 43 characters. */
export function mintId(random: (bytes: Uint8Array<ArrayBuffer>) => Uint8Array = (bytes) => crypto.getRandomValues(bytes))
  : string {
  const bytes = random(new Uint8Array(32));
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** A new draft. */
export function draftOf(holder: string, item: string, kind: Kind,
                        { chose = null, text = null, about = "" }: { chose?: string | null; text?: string | null;
                                                                       about?: string } = {},
                        mint: () => string = mintId): Draft {
  return { act: mint(), holder, item, kind, chose, text: text?.trim() ? text.trim() : null, tell: null, about,
           receipt: null, found: null };
}

/** Whether the page records this draft's kind. */
export function isPageKind(kind: string): boolean {
  return (PAGE_KINDS as readonly string[]).includes(kind);
}

/** The draft's kind and answer in words, for its row: `Pick b`, `Your words`. */
export function kindWords(draft: Pick<Draft, "kind" | "chose">): string {
  switch (draft.kind) {
    case "pick": return `Pick ${draft.chose ?? ""}`.trim();
    case "accept": return "Accept";
    case "reject": return "Reject";
    case "read-back": return "That's what I said";
    case "done": return "I've done it";
    case "park": return "Park";
    case "unpark": return "Unpark";
    case "words": return "Your words";
    case "correct": return "Not what I said";
    case "explain": return "Ask the lead to explain";
  }
}

/** Who a draft goes to: its round's lead under `EACH`, else the agent picked; null for
 * none, undefined when the page can't tell (the server finds it). */
export function recipientOf(draft: Pick<Draft, "holder">, reach: Reach): string | null | undefined {
  return reach.to === EACH ? reach.leadOf(draft.holder) : reach.to;
}

/** The one line over the drafts that says who records what. */
export const RULE_LINE = "On Send the page records picks, decisions, read-backs, done jobs, parks and unparks at once. "
  + "Your own words and explain requests go to the agent to word and record.";
/** The rule line with no comms installed. */
export const RULE_LINE_ALONE = "The page records picks, decisions, read-backs, done jobs, parks and unparks. "
  + "Nothing sends your own words: a lead records them with the command shown.";

/** The words for a draft with no recipient. */
export function noLeadWords(holder: string): string {
  return `No lead for ${holder}: pick an agent to send it to`;
}

/** The command a lead runs to record an own-words answer or a correction, `PTR` its
 * pointer. */
export function leadCommand(draft: Pick<Draft, "holder" | "item" | "kind" | "text">): string | null {
  const said = `"${(draft.text ?? "").replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, " ")}"`;
  if (draft.kind === "words") return `moxi rounds rule ${draft.holder} ${draft.item} --text ${said} --record PTR`;
  if (draft.kind === "correct") {
    return `moxi rounds rule ${draft.holder} --text ${said} --supersedes ${draft.item} --record PTR`;
  }
  return null;
}

/** What a tray row says under the draft's words of who records it. */
export function chipOf(draft: Draft, reach: Reach): string {
  if (draft.receipt?.state === "recorded-not-told") return "recorded, not told: Send again";
  if (!reach.comms) return isPageKind(draft.kind) ? "page records it" : leadCommand(draft) ?? "nothing to record";
  const to = recipientOf(draft, reach);
  if (to === null) return noLeadWords(draft.holder);
  const agent = to ?? "the lead";
  if (isPageKind(draft.kind)) return "page records it on Send";
  if (draft.kind === "explain") return `goes to ${agent}; nothing to record`;
  return `${agent} words and records it`;
}

/** The drafts a Send carries: with comms, each with a recipient; with none, the page's
 * kinds alone. */
export function sendable(drafts: readonly Draft[], reach: Reach): Draft[] {
  return drafts.filter((one) => (reach.comms ? recipientOf(one, reach) !== null : isPageKind(one.kind)));
}

/** The Send button's words. */
export function sendWords(drafts: readonly Draft[], reach: Reach): string {
  const n = sendable(drafts, reach).length;
  if (!reach.comms) return `Record ${n} ${n === 1 ? "answer" : "answers"}`;
  return "Send";
}

/** The answers route's body: the drafts as lists of one length, an empty string for
 * none, and the To choice. */
export function bodyOf(drafts: readonly Draft[], to: string): Record<string, string | string[]> {
  return {
    act: drafts.map((one) => one.act), holder: drafts.map((one) => one.holder),
    item: drafts.map((one) => one.item), kind: drafts.map((one) => one.kind),
    chose: drafts.map((one) => one.chose ?? ""), text: drafts.map((one) => one.text ?? ""),
    tell: drafts.map((one) => one.tell ?? ""), to,
  };
}

/** A storage the tray keeps itself in: the browser's `localStorage`, or a stub. */
export interface Store {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** The storage key of the tray of the page server at `origin`. */
export function trayKey(origin: string): string {
  return `moxi-rounds-tray:${origin}`;
}

const isDraft = (one: unknown): one is Draft => {
  const d = one as Partial<Draft> | null;
  return !!d && typeof d.act === "string" && ACT_ID.test(d.act) && typeof d.holder === "string"
    && typeof d.item === "string" && typeof d.kind === "string"
    && ([...PAGE_KINDS, ...AGENT_KINDS] as string[]).includes(d.kind);
};

/** The drafts the tray holds, kept in `store` under `key` after each change. A store that
 * can't be read or written leaves the tray in memory only. */
export class Tray {
  #store: Store | null;
  #key: string;
  value: TrayValue = { drafts: [], sent: [], messages: [] };

  constructor(store: Store | null, key: string) {
    this.#store = store;
    this.#key = key;
    this.load();
  }

  /** Reads the tray back from its storage. */
  load(): void {
    let kept: Partial<TrayValue> | null = null;
    try {
      const text = this.#store?.getItem(this.#key) ?? null;
      kept = text ? JSON.parse(text) : null;
    } catch {
      kept = null;
    }
    const list = (items: unknown): Draft[] => (Array.isArray(items) ? items.filter(isDraft)
      .map((one) => ({ ...one, chose: one.chose ?? null, text: one.text ?? null, tell: one.tell ?? null,
                       about: one.about ?? "", receipt: one.receipt ?? null, found: one.found ?? null })) : []);
    this.value = { drafts: list(kept?.drafts), sent: list(kept?.sent),
                   messages: Array.isArray(kept?.messages) ? kept!.messages : [] };
  }

  #save(): void {
    try {
      this.#store?.setItem(this.#key, JSON.stringify(this.value));
    } catch {
      // A browser that keeps nothing still has the tray until the page is left.
    }
  }

  get drafts(): Draft[] {
    return this.value.drafts;
  }

  get sent(): Draft[] {
    return this.value.sent;
  }

  /** Adds a draft in place of the item's draft never sent, if any: one answer per item
   * waits in the tray. */
  add(draft: Draft): void {
    const same = (one: Draft) => one.holder === draft.holder && one.item === draft.item && one.receipt === null;
    this.value.drafts = [...this.value.drafts.filter((one) => !same(one)), draft];
    this.#save();
  }

  remove(act: string): void {
    this.value.drafts = this.value.drafts.filter((one) => one.act !== act);
    this.#save();
  }

  /** Sets a draft's words. */
  edit(act: string, text: string): void {
    this.value.drafts = this.value.drafts.map((one) => (one.act === act ? { ...one, text: text.trim() || null } : one));
    this.#save();
  }

  /** Gives each recorded-not-told draft in `acts` a `tell` id of its own, kept until its
   * message is taken; the drafts as they go now. */
  toSend(acts: readonly string[], mint: () => string = mintId): Draft[] {
    this.value.drafts = this.value.drafts.map((one) => (acts.includes(one.act)
      && one.receipt?.state === "recorded-not-told" && one.tell === null ? { ...one, tell: mint() } : one));
    this.#save();
    return acts.map((act) => this.value.drafts.find((one) => one.act === act)).filter((one): one is Draft => !!one);
  }

  /** Takes a send's answers: a recorded or sent draft moves to Sent; a recorded one not
   * told stays a row with Send again; a refused or kept one stays a draft with its
   * reason. */
  take(receipts: readonly Receipt[], messages: readonly Message[] = []): void {
    const by = new Map(receipts.map((one) => [one.act, one]));
    const stay: Draft[] = [];
    const moved: Draft[] = [];
    for (const one of this.value.drafts) {
      const receipt = by.get(one.act);
      if (!receipt) {
        stay.push(one);
        continue;
      }
      const notTold = one.receipt?.state === "recorded-not-told";
      if (receipt.state === "recorded" || receipt.state === "sent") {
        // A send again answers with the record it read; the first receipt's facts stay.
        moved.push({ ...one, tell: null, receipt: notTold ? { ...one.receipt!, ...receipt } : receipt });
      } else if (receipt.state === "recorded-not-told") {
        stay.push({ ...one, receipt: notTold ? { ...one.receipt!, ...receipt, state: "recorded-not-told" } : receipt });
      } else if (notTold) {
        // A send again refused or kept: the record stands, still not told.
        stay.push({ ...one, tell: null, receipt: { ...one.receipt!, not_told: receipt.reason ?? one.receipt!.not_told } });
      } else {
        stay.push({ ...one, receipt });
      }
    }
    this.value.drafts = stay;
    this.value.sent = [...this.value.sent, ...moved];
    this.value.messages = [...this.value.messages, ...messages];
    this.#save();
  }

  /** Clears Sent. */
  clearSent(): void {
    this.value.sent = [];
    this.value.messages = [];
    this.#save();
  }

  /** Sets the ruling found for a sent draft. */
  setFound(act: string, found: RulingOf | null): void {
    this.value.sent = this.value.sent.map((one) => (one.act === act ? { ...one, found } : one));
    this.#save();
  }

  /** The newest row of an item, a draft before a sent one; null for none. */
  of(holder: string, item: string): Draft | null {
    const match = (one: Draft) => one.holder === holder && one.item === item;
    return [...this.value.drafts].reverse().find(match) ?? [...this.value.sent].reverse().find(match) ?? null;
  }
}

/** Whether an item's popover offers the form that answers it. A question offers it by
 * its record's state, whatever the tray holds for it: an open question its answer form,
 * a parked one `Unpark`. It waits only while its record is older than the last answer
 * the page took for it (`fresh` false), since that record may not show the answer yet.
 * Any other item offers it while no answer of its that did something stands in the tray. */
export function isAnswerable(kind: string, state: string, row: Pick<Draft, "receipt"> | null,
                             { fresh }: { fresh: boolean }): boolean {
  if (kind === "question") return fresh && (state === "open" || state === "parked");
  return !(row?.receipt && !["refused", "kept"].includes(row.receipt.state));
}

/** A line of a receipt: its text, in mono for a pointer or a command, and the act it
 * offers, if any. */
export interface ReceiptLine {
  text: string;
  mono?: boolean;
  tone?: "done" | "failed" | "dim";
  offer?: "send-again" | "read-back";
}

/** The receipt's lines, for the popover and the tray's row alike. */
export function receiptLines(draft: Draft, { today, clock }: { today: string; clock?: Clock }): ReceiptLine[] {
  const receipt = draft.receipt;
  if (receipt === null) return [];
  const time = (at: string | null | undefined) => (at ? timeWord(at, today, clock) : "");
  const lines: ReceiptLine[] = [];
  const state = receipt.state;
  if (state === "kept") return [{ text: receipt.reason ?? "Kept in the tray", tone: "dim" }];
  if (state === "refused") {
    lines.push({ text: receipt.busy ? `The round is busy: ${receipt.reason ?? ""}`.trim() : receipt.reason ?? "Refused",
                 tone: "failed" });
    if (receipt.fix) lines.push({ text: "The lead can run:", tone: "dim" }, { text: receipt.fix, mono: true });
    return lines;
  }
  const message = receipt.message ?? null;
  if (isPageKind(draft.kind)) {
    lines.push({ text: `${kindWords(draft)}${draft.text && draft.kind === "done" ? `: "${draft.text}"` : ""}` });
    lines.push({ text: `Recorded by this page at ${time(receipt.at)}`, tone: "dim" });
    if (receipt.record) lines.push({ text: receipt.record, mono: true });
    if (state === "recorded-not-told") {
      lines.push({ text: `Not told: ${receipt.not_told ?? ""}`.trim(), tone: "failed", offer: "send-again" });
    } else if (message) {
      lines.push({ text: `Told ${message.to} at ${time(message.at)}, comms ${message.id ?? ""}`.trim(), tone: "dim" });
    }
    const ruled = receipt.ruling;
    if (draft.kind === "pick" || draft.kind === "accept" || draft.kind === "reject") {
      if (ruled?.id) lines.push({ text: `Recorded as ${ruled.id}: ${ruled.text ?? ""}`.trim(), tone: "done",
                                  offer: "read-back" });
    } else if (draft.kind === "read-back") {
      lines.push({ text: `${(receipt.marked ?? [draft.item]).join(", ")} marked as read back`, tone: "done" });
    } else if (draft.kind === "park") {
      lines.push({ text: `${draft.item} parked`, tone: "done" });
    } else if (draft.kind === "unpark") {
      lines.push({ text: `${draft.item} unparked: open again`, tone: "done" });
    } else if (draft.kind === "done") {
      lines.push({ text: `${receipt.done ?? draft.item} marked done`, tone: "done" });
      if (receipt.closed) lines.push({ text: "The round closed." });
    }
    const freed = receipt.freed ?? [];
    if (freed.length) lines.push({ text: `${freed.join(", ")} can start` });
    else if (typeof receipt.left === "number" && draft.kind !== "unpark") {
      lines.push({ text: `${receipt.left} ${receipt.left === 1 ? "question" : "questions"} left` });
    }
    return lines;
  }
  const to = message?.to ?? receipt.to ?? "the agent";
  if (draft.kind === "explain") {
    lines.push({ text: `Asked ${to} to explain at ${time(message?.at)}, comms ${message?.id ?? ""}. `
                       + "The question stays open; the reply comes by message." });
    return lines;
  }
  lines.push({ text: `"${draft.text ?? ""}"` });
  lines.push({ text: `Sent to ${to} at ${time(message?.at)}, comms ${message?.id ?? ""}. That message is the pointer.`,
               tone: "dim" });
  const found = draft.found;
  const pointer = message?.id ? `comms:${message.id}` : null;
  if (found && pointer && found.record === pointer) {
    lines.push({ text: `Recorded as ${found.id}: ${found.text ?? ""}`.trim(), tone: "done", offer: "read-back" });
    if (found.actor) lines.push({ text: `by ${found.actor}`, tone: "dim" });
  } else if (found) {
    lines.push({ text: `Ruled as ${found.id} from ${found.record ?? "no pointer"}, not from this message`,
                 offer: "read-back" });
  } else {
    lines.push({ text: `With ${to}, not recorded yet` });
    if (receipt.command) lines.push({ text: receipt.command, mono: true });
  }
  return lines;
}

/** The ruling a receipt's read-back marks: the ruling recorded, or the one the agent
 * recorded; null for none. */
export function readBackOf(draft: Draft): string | null {
  if (draft.kind === "words" || draft.kind === "correct") return draft.found?.id ?? null;
  return draft.receipt?.ruling?.id ?? null;
}

/** For a sent correction, the ruling that supersedes the one it corrects, as the rulings
 * waiting to be read back list it; the newest when there are several, null for none yet. */
export function correctionFound(draft: Draft, waiting: Waiting): RulingOf | null {
  if (draft.kind !== "correct") return null;
  let found: RulingOf | null = null;
  for (const back of waiting.not_shown) {
    for (const row of back.rows) {
      if (row.holder !== draft.holder || row.supersedes !== draft.item) continue;
      if (found !== null && (row.at ?? "") < (found.at ?? "")) continue;
      found = { id: row.id, text: row.text ?? row.rules ?? null, record: row.record ?? null, actor: null,
                at: row.at ?? null, shown: false };
    }
  }
  return found;
}

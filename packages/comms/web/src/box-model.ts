// The send box of an opened agent row, worked out with no DOM: what it holds, the check
// that runs before Send, Send itself and `Take back`, and what each control and line says.
// The acts go through a `post` the page passes in, so the Node tests drive the box with a
// stub. Every string here goes into the page as text.
//
// - The check runs when the box is made, again when the posture or `Deliver` changes, and on
//   `Check again`. One runs at a time; a check asked for while one is out runs once more
//   after it, and the last answer is kept. It carries the text as it stands.
// - Send takes the accent only while the box has text and the last check answered
//   `would-send`, and is pressed once: it is out until its answer. `sent` empties the box
//   and drops the message it answered; every other answer keeps the text.
// - `Take back` is pressed once per message, and its answer's line stays until the next.

import type { ApiResult } from "/m/ui/client.js";

import type { Owed } from "./agents-model.ts";
import type { Queue } from "./messages-model.ts";
import {
  checkWords, firstPosture, nameOf, POSTURES, sendWords, takeBackWords, transportWords,
} from "./words.ts";
import type { Recipient, Said, SendAnswer, TakeAnswer } from "./words.ts";

export type Posture = (typeof POSTURES)[number]["posture"];

/** The most characters the box takes: the route's cap on `text`. */
export const TEXT_MAX_CHARS = 16_000;
export const FROM = "From";
export const OPERATOR = "operator";
export const REPLY_WANTED = "Reply wanted";
export const DELIVER = "Deliver";
export const NOW = "now";
export const NEXT_TURN = "at its next turn";
export const SEND = "Send";
export const CHECK_AGAIN = "Check again";
export const TAKE_BACK = "Take back";
/** The two choices of `Deliver`, in the menu's order. */
export const DELIVERIES = [{ held: false, word: NOW }, { held: true, word: NEXT_TURN }] as const;

/** A request to one of comms' acts, `path` relative to `/m/comms/`. */
export type Post = (path: string, body: object) => Promise<ApiResult>;

/** What `check` and `send` answer, or null when it isn't that. */
export function parseSendAnswer(data: unknown): SendAnswer | null {
  if (typeof data !== "object" || data === null) return null;
  const { outcome, message_id, recipients, refusal } = data as Record<string, unknown>;
  if (typeof outcome !== "string" || !Array.isArray(recipients)) return null;
  if (message_id !== null && (typeof message_id !== "string" || !/^\d+$/.test(message_id))) return null;
  const kept: Recipient[] = [];
  for (const one of recipients) {
    if (typeof one !== "object" || one === null) return null;
    const { qualified, delivery, cold_at } = one as Record<string, unknown>;
    if (typeof qualified !== "string" || typeof delivery !== "string") return null;
    if (cold_at !== null && (typeof cold_at !== "number" || !Number.isFinite(cold_at))) return null;
    kept.push({ qualified, delivery, cold_at });
  }
  const code = parseRefusal(refusal);
  if (code === undefined) return null;
  return { outcome, message_id, recipients: kept, refusal: code };
}

/** A refusal: `{code}`, null, or undefined when it is neither. */
function parseRefusal(data: unknown): { code: string } | null | undefined {
  if (data === null) return null;
  if (typeof data !== "object" || data === undefined) return undefined;
  const { code } = data as Record<string, unknown>;
  return typeof code === "string" ? { code } : undefined;
}

/** What `take-back` answers, or null when it isn't that. */
export function parseTakeAnswer(data: unknown): TakeAnswer | null {
  if (typeof data !== "object" || data === null) return null;
  const { outcome, withdrawn, delivered, expired, refusal } = data as Record<string, unknown>;
  const names = (value: unknown): value is string[] =>
    Array.isArray(value) && value.every((item) => typeof item === "string");
  if (typeof outcome !== "string" || !names(withdrawn) || !names(delivered) || !names(expired)) return null;
  const code = parseRefusal(refusal);
  if (code === undefined) return null;
  return { outcome, withdrawn, delivered, expired, refusal: code };
}

/** One act's answer: the status, the answer read from it (null when the status isn't 200 or
 * the body isn't the act's), and for a send the server's time it came. */
interface Answered<A> {
  status: number;
  answer: A | null;
}

/** Whether the last check held the message for a cold cache. */
const heldCold = (answer: SendAnswer | null): boolean =>
  answer !== null && answer.outcome === "would-send" && answer.recipients.some((one) => one.delivery === "held-cold");

/** The words of a line the box shows, by the act that gave it. */
export function checkLine(answered: Answered<SendAnswer>, when: (at: number) => string): Said {
  return answered.status === 200 && answered.answer !== null ? checkWords(answered.answer, when)
    : transportWords("check", answered.status);
}

export function sendLine(answered: Answered<SendAnswer> & { at: number }, when: (at: number) => string,
                         waiting: number | null): Said {
  return answered.status === 200 && answered.answer !== null ? sendWords(answered.answer, answered.at, when, waiting)
    : transportWords("send", answered.status);
}

export function takeLine(answered: Answered<TakeAnswer>): Said {
  return answered.status === 200 && answered.answer !== null ? takeBackWords(answered.answer)
    : transportWords("take-back", answered.status);
}

/** How many of the address's messages now wait for it, from its queue read after a send:
 * the listed rows still waiting and every one past the cap, the newest being there. */
export function waitingIn(queue: Queue | null, to: string): number | null {
  if (queue === null || queue.state !== "up") return null;
  const listed = queue.messages.filter((message) =>
    (message.rows.find((row) => row.qualified === to) ?? message.rows[0])?.state === "waiting").length;
  return listed + queue.more;
}

/** One entry of a menu: what it says, what it sets, and whether it is the one set. */
export interface Choice<V> {
  value: V;
  label: string;
  says: string | null;
  current: boolean;
}

/** What the box shows. */
export interface BoxView {
  /** The text area's placeholder: `Message <address>`. */
  placeholder: string;
  /** `Answering <author>'s <time> message:` and its text, while the box answers one. */
  quote: { lead: string; text: string } | null;
  /** `Reply wanted`: the posture's word and the menu of four. */
  posture: { word: string; menu: Choice<Posture>[] };
  /** `Deliver`, with cache only: its word and its menu, or no menu when the last check held
   * the message for a cold cache. */
  deliver: { word: string; menu: Choice<boolean>[] | null } | null;
  /** Send takes the accent; otherwise it is an outline. */
  ready: boolean;
  /** Send is out, so it can't be pressed. */
  sending: boolean;
  /** The line under the box: the last check's or Send's. */
  line: Said | null;
  /** The line of the last `Take back`. */
  taken: Said | null;
}

export interface BoxOptions {
  post: Post;
  /** Called after every change. */
  onChange: () => void;
  /** Called after Send or `Take back` has an answer, of any kind, so the page reads the mail
   * and the queue again. */
  onActed: (act: "send" | "take-back") => void;
  /** The server's time now, epoch seconds. */
  now: () => number;
  /** The cache module is installed: `Deliver` is offered and `held` can be set. */
  cache: boolean;
}

/** The box written to one address, set to answer `reply` or none. */
export class SendBox {
  readonly to: string;
  text = "";
  posture: Posture;
  /** `Deliver at its next turn`: the message is held for the recipient's next turn. */
  held = false;
  reply: Owed | null;
  checking = false;
  sending = false;
  /** The ids of the messages whose `Take back` is out. */
  taking = new Set<string>();
  checked: Answered<SendAnswer> | null = null;
  sent: (Answered<SendAnswer> & { at: number }) | null = null;
  taken: Answered<TakeAnswer> | null = null;
  /** Which act gave the line under the box. */
  lineFrom: "check" | "send" | null = null;
  /** The queue read when Send answered, so the count waits for the read after it. */
  queueAtSend: Queue | null = null;

  #options: BoxOptions;
  #again = false;
  #closed = false;

  constructor(to: string, reply: Owed | null, options: BoxOptions) {
    this.to = to;
    this.reply = reply;
    this.posture = firstPosture(reply !== null);
    this.#options = options;
  }

  /** Drops every later answer. */
  close(): void {
    this.#closed = true;
  }

  get closed(): boolean {
    return this.#closed;
  }

  /** Send can be pressed: the box has text, the last check answered `would-send`, and no
   * Send is out. */
  get ready(): boolean {
    return this.text.trim() !== "" && this.checked?.answer?.outcome === "would-send" && !this.sending;
  }

  /** What `check` and `send` post. */
  body(): Record<string, unknown> {
    const body: Record<string, unknown> = { to: this.to, text: this.text, posture: this.posture,
                                            held: this.#options.cache && this.held };
    if (this.reply !== null) body.reply_to = this.reply.message_id;
    return body;
  }

  setText(text: string): void {
    if (this.sending) return;
    this.text = text.slice(0, TEXT_MAX_CHARS);
    this.#options.onChange();
  }

  setPosture(posture: Posture): void {
    if (posture === this.posture) return;
    this.posture = posture;
    void this.check();
  }

  setHeld(held: boolean): void {
    if (held === this.held || !this.#options.cache) return;
    this.held = held;
    void this.check();
  }

  /** Sets the box to answer `reply`, or to answer none; the posture starts again from it. */
  answer(reply: Owed | null): void {
    if ((reply?.message_id ?? null) === (this.reply?.message_id ?? null)) return;
    this.reply = reply;
    this.posture = firstPosture(reply !== null);
    void this.check();
  }

  /** Runs the check; a call while one is out runs one more after it. */
  async check(): Promise<void> {
    if (this.checking) {
      this.#again = true;
      return;
    }
    this.checking = true;
    this.#options.onChange();
    try {
      do {
        this.#again = false;
        const result = await this.#options.post("api/check", this.body());
        if (this.#closed) return;
        this.checked = { status: result.status, answer: result.status === 200 ? parseSendAnswer(result.data) : null };
        this.lineFrom = "check";
      } while (this.#again);
    } finally {
      this.checking = false;
    }
    if (!this.#closed) this.#options.onChange();
  }

  /** Sends the box's message, once. */
  async send(queue: Queue | null): Promise<void> {
    if (!this.ready) return;
    this.sending = true;
    this.#options.onChange();
    const result = await this.#options.post("api/send", this.body());
    if (this.#closed) return;
    this.sending = false;
    const answer = result.status === 200 ? parseSendAnswer(result.data) : null;
    this.sent = { status: result.status, answer, at: this.#options.now() };
    this.lineFrom = "send";
    this.queueAtSend = queue;
    if (answer?.outcome === "sent") {
      this.text = "";
      this.reply = null;
    }
    this.#options.onChange();
    this.#options.onActed("send");
  }

  /** Takes back the message `messageId`, once. */
  async takeBack(messageId: string): Promise<void> {
    if (this.taking.has(messageId)) return;
    this.taking.add(messageId);
    this.#options.onChange();
    const result = await this.#options.post("api/take-back", { message_id: messageId });
    if (this.#closed) return;
    this.taking.delete(messageId);
    this.taken = { status: result.status, answer: result.status === 200 ? parseTakeAnswer(result.data) : null };
    this.#options.onChange();
    this.#options.onActed("take-back");
  }

  /** What the box shows, with `queue` the address's queue as the page holds it now. */
  view({ when, queue = null }: { when: (at: number) => string; queue?: Queue | null }): BoxView {
    const checkAnswer = this.checked?.answer ?? null;
    let deliver: BoxView["deliver"] = null;
    if (this.#options.cache) {
      deliver = heldCold(checkAnswer) ? { word: NEXT_TURN, menu: null }
        : { word: this.held ? NEXT_TURN : NOW,
            menu: DELIVERIES.map((one) => ({ value: one.held, label: one.word, says: null, current: one.held === this.held })) };
    }
    let line: Said | null = null;
    if (this.lineFrom === "check" && this.checked !== null) line = checkLine(this.checked, when);
    else if (this.lineFrom === "send" && this.sent !== null) {
      const read = queue !== null && queue !== this.queueAtSend ? waitingIn(queue, this.to) : null;
      line = sendLine(this.sent, when, read);
    }
    const word = POSTURES.find((one) => one.posture === this.posture)?.word ?? this.posture;
    return {
      placeholder: `Message ${this.to}`,
      quote: this.reply === null ? null
        : { lead: `Answering ${nameOf(this.reply.author)}'s ${when(this.reply.at)} message:`, text: this.reply.text },
      posture: { word, menu: POSTURES.map((one) => ({ value: one.posture, label: one.label, says: one.says,
                                                      current: one.posture === this.posture })) },
      deliver,
      ready: this.ready,
      sending: this.sending,
      line,
      taken: this.taken === null ? null : takeLine(this.taken),
    };
  }
}

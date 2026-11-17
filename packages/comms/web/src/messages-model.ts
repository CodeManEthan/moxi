// The messages comms' page server sends, read with no DOM: one shape for every message on
// the page, and the reads that carry them. Every id is a string of digits and is never
// worked on as a number. Every string here goes into the page as text.

import type { Delivery } from "./words.ts";

/** One message, as every read of the page sends it. */
export interface Message {
  message_id: string;
  /** Epoch seconds. */
  at: number;
  /** The author's qualified name. */
  author: string;
  /** `operator` when the operator wrote it. */
  author_tier: string;
  /** The recipient tags as line zero writes them. */
  to: string[];
  posture: string | null;
  reply_to: string | null;
  text: string;
  withdrawn: boolean;
  /** Each recipient's row in the books; a recipient with none has read it. */
  rows: Delivery[];
}

/** `/api/queue?name=`: one address's rows, oldest first, and how many more there are. */
export interface Queue {
  state: "up" | "error";
  detail: string | null;
  messages: Message[];
  more: number;
}

const isTable = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const isString = (value: unknown): value is string => typeof value === "string";
const isId = (value: unknown): value is string => isString(value) && /^\d+$/.test(value);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

function parseDelivery(data: unknown): Delivery | null {
  if (!isTable(data)) return null;
  const { qualified, state, held, takeable } = data;
  if (!isString(qualified) || !isString(state)) return null;
  return { qualified, state, held: held === true, takeable: takeable === true };
}

/** One message, or null when it isn't one. */
export function parseMessage(data: unknown): Message | null {
  if (!isTable(data)) return null;
  const { message_id, at, author, author_tier, to, posture, reply_to, text, withdrawn, rows } = data;
  if (!isId(message_id) || !finite(at) || !isString(author) || !isString(text) || !Array.isArray(to)) return null;
  if (reply_to !== null && reply_to !== undefined && !isId(reply_to)) return null;
  return {
    message_id, at, author, author_tier: isString(author_tier) ? author_tier : "", to: to.filter(isString),
    posture: isString(posture) ? posture : null, reply_to: reply_to ?? null, text, withdrawn: withdrawn === true,
    rows: Array.isArray(rows) ? rows.map(parseDelivery).filter((row): row is Delivery => row !== null) : [],
  };
}

/** A list of messages; an entry that isn't one is left out. */
export function parseMessages(data: unknown): Message[] {
  return Array.isArray(data) ? data.map(parseMessage).filter((message): message is Message => message !== null) : [];
}

/** The answer of `/api/queue`, or null when it isn't one. */
export function parseQueue(data: unknown): Queue | null {
  if (!isTable(data)) return null;
  const { state, detail, messages, more } = data;
  if (state !== "up" && state !== "error") return null;
  if (detail !== null && detail !== undefined && !isString(detail)) return null;
  return { state, detail: detail ?? null, messages: state === "up" ? parseMessages(messages) : [],
           more: Number.isInteger(more) && (more as number) > 0 ? more as number : 0 };
}

/** Whether the operator wrote it. */
export const byHim = (message: Message): boolean => message.author_tier === "operator";

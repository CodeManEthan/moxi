// The parts of the pairing and sign-in pages that need no document: base64url, the
// passkey options the gateway sends as JSON turned into what `navigator.credentials`
// takes, a new credential or an assertion turned back into JSON for the gateway, the
// name a phone suggests for itself, and what each refusal says.

/** The `localStorage` item that holds the session key, as the shell's script reads it. */
export const KEY_STORAGE = "moxi-ui-key";
/** Where a signed-in phone goes. */
export const HOME = "/m/ui/";
/** The longest device name the gateway takes. */
export const NAME_MAX = 64;

/** `bytes` as base64url without padding. */
export function toB64url(bytes: ArrayBuffer | ArrayBufferView): string {
  const view = bytes instanceof ArrayBuffer ? new Uint8Array(bytes)
    : new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let text = "";
  for (const byte of view) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The bytes of base64url `text`, padded or not. Throws a TypeError for anything else. */
export function fromB64url(text: unknown): ArrayBuffer {
  if (typeof text !== "string" || !/^[A-Za-z0-9_-]*={0,2}$/.test(text)) {
    throw new TypeError("not base64url text");
  }
  const plain = text.replace(/=+$/, "").replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(plain + "=".repeat((4 - (plain.length % 4)) % 4));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

type Json = Record<string, any>;

function descriptors(list: unknown): PublicKeyCredentialDescriptor[] | undefined {
  if (!Array.isArray(list)) return undefined;
  return list.map((item: Json) => ({ ...item, id: fromB64url(item.id) }) as PublicKeyCredentialDescriptor);
}

/** The creation options of a pairing's begin, as `navigator.credentials.create` takes them. */
export function creationOptions(json: Json): PublicKeyCredentialCreationOptions {
  const options: Json = { ...json, challenge: fromB64url(json.challenge), user: { ...json.user, id: fromB64url(json.user?.id) } };
  const exclude = descriptors(json.excludeCredentials);
  if (exclude === undefined) delete options.excludeCredentials;
  else options.excludeCredentials = exclude;
  return options as PublicKeyCredentialCreationOptions;
}

/** The request options of a sign-in's begin, as `navigator.credentials.get` takes them. */
export function requestOptions(json: Json): PublicKeyCredentialRequestOptions {
  const options: Json = { ...json, challenge: fromB64url(json.challenge) };
  const allow = descriptors(json.allowCredentials);
  if (allow === undefined) delete options.allowCredentials;
  else options.allowCredentials = allow;
  return options as PublicKeyCredentialRequestOptions;
}

/** A new passkey or an assertion as the JSON the gateway checks: every byte string in
 * base64url, the shape of WebAuthn's own `toJSON()`. */
export function credentialJson(credential: PublicKeyCredential): Json {
  const response = credential.response as AuthenticatorAttestationResponse & AuthenticatorAssertionResponse;
  const out: Json = { clientDataJSON: toB64url(response.clientDataJSON) };
  if ("attestationObject" in response && response.attestationObject) {
    out.attestationObject = toB64url(response.attestationObject);
    if (typeof response.getTransports === "function") out.transports = response.getTransports();
  } else {
    out.authenticatorData = toB64url(response.authenticatorData);
    out.signature = toB64url(response.signature);
    if (response.userHandle) out.userHandle = toB64url(response.userHandle);
  }
  return {
    id: credential.id,
    rawId: toB64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? null,
    clientExtensionResults: typeof credential.getClientExtensionResults === "function"
      ? credential.getClientExtensionResults() : {},
    response: out,
  };
}

/** The name a phone suggests for itself: the model when the browser tells it, else what
 * the user agent says it is. */
export function defaultName(userAgent: string, model: string | null = null): string {
  const named = (model ?? "").trim();
  if (named) return named.slice(0, NAME_MAX);
  if (/iPad/.test(userAgent)) return "iPad";
  if (/iPhone/.test(userAgent)) return "iPhone";
  if (/Android/.test(userAgent)) return "Android phone";
  return "Phone";
}

/** The text an answer's `error` field holds, or null. */
export function errorText(data: unknown): string | null {
  if (typeof data !== "object" || data === null) return null;
  const error = (data as Json).error;
  return typeof error === "string" && error ? error : null;
}

export const PAIR_AGAIN = "Pair again from Moxi on the computer: the Gateway section of its modules view, or the command moxi ui gateway pair.";

/** What the pairing page says for an answer from begin or finish that isn't a 200. */
export function pairRefusal(step: "begin" | "finish", status: number, data: unknown): string {
  if (status === 0) return "No answer from the computer. Check that it's awake and try again.";
  if (status === 403) {
    return step === "begin"
      ? `This pairing code was used or has expired; a code works once, for two minutes. ${PAIR_AGAIN}`
      : `This pairing wasn't accepted: it expired, or the passkey didn't check out. ${PAIR_AGAIN}`;
  }
  if (status === 503) return "Moxi on the computer has no gateway address right now. Run moxi doctor ui there.";
  return errorText(data) ?? `The computer answered ${status}.`;
}

/** What the sign-in page says for an answer from begin or finish that isn't a 200. */
export function signinRefusal(status: number, data: unknown): string {
  if (status === 0) return "No answer from the computer. Check that it's awake and try again.";
  if (status === 403) {
    return "Moxi didn't accept this passkey. The phone may have been removed, or the gateway's "
      + `address may have changed since it paired. ${PAIR_AGAIN}`;
  }
  if (status === 503) return "Moxi on the computer has no gateway address right now. Run moxi doctor ui there.";
  return errorText(data) ?? `The computer answered ${status}.`;
}

/** What a page says when `navigator.credentials` threw. */
export function passkeyFailure(error: unknown): string {
  const name = typeof error === "object" && error !== null ? (error as { name?: unknown }).name : null;
  if (name === "NotAllowedError") return "No passkey was used: the prompt was closed or timed out. Try again.";
  if (name === "InvalidStateError") return "This phone already holds a passkey for this Moxi. Sign in with it instead.";
  if (name === "SecurityError") return "The browser refused a passkey for this address.";
  return "The passkey prompt failed. Try again.";
}

/** A new `n` for the sign-in page's URL: 16 hex digits, so no cached answer matches it. */
export function freshN(fill: (bytes: Uint8Array<ArrayBuffer>) => Uint8Array = (bytes) => crypto.getRandomValues(bytes)): string {
  return [...fill(new Uint8Array(8))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

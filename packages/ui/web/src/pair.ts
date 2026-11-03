// /pair.js: the pairing page's script. The page is opened from the QR code the computer
// shows, `/pair?n=...#c=<code>`. It takes the code from the fragment and drops it from the
// address, asks for the phone's name, and spends the code with `/api/pair/begin`. Then it
// makes a passkey and finishes with it, or, on a browser with no platform authenticator,
// offers to pair without one. Each step is a tap, so the passkey prompt runs in a gesture.

import { el, post, say, signedIn, style } from "./gateway-page.ts";
import {
  creationOptions, credentialJson, defaultName, NAME_MAX, PAIR_AGAIN, pairRefusal, passkeyFailure,
} from "./passkey.ts";

const NO_PASSKEY_NOTE = "Without a passkey this phone signs in again only by a new QR pairing.";

async function model(): Promise<string | null> {
  const data = (navigator as Navigator & { userAgentData?: { getHighEntropyValues(hints: string[]): Promise<{ model?: string }> } }).userAgentData;
  if (data === undefined) return null;
  try {
    return (await data.getHighEntropyValues(["model"])).model ?? null;
  } catch {
    return null;
  }
}

async function platformPasskey(): Promise<boolean> {
  if (typeof PublicKeyCredential !== "function") return false;
  try {
    return await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch {
    return false;
  }
}

async function start(): Promise<void> {
  style();
  const main = document.getElementById("moxi-pair") as HTMLElement;
  const line = el("p");
  line.setAttribute("role", "status");
  const code = new URLSearchParams(location.hash.slice(1)).get("c");
  // The code never stays in the address, the history or a bookmark.
  history.replaceState(null, "", location.pathname);
  if (!code) {
    main.append(line);
    say(line, `This page needs the pairing code the computer's QR code carries. ${PAIR_AGAIN}`, true);
    return;
  }

  const label = el("label", "A name for this phone");
  const name = el("input");
  name.maxLength = NAME_MAX;
  name.required = true;
  name.autocomplete = "off";
  name.value = defaultName(navigator.userAgent, await model());
  label.append(name);
  const buttons = el("div", undefined, "buttons");
  const next = el("button", "Continue", "go");
  next.type = "button";
  buttons.append(next);
  main.append(label, buttons, line);

  let pairing: string | null = null;
  let options: PublicKeyCredentialCreationOptions | null = null;

  const finish = async (credential: object | null): Promise<void> => {
    say(line, "Pairing…");
    const answer = await post("/api/pair/finish", { pairing, credential });
    if (answer.status === 200 && signedIn(answer.data)) return;
    buttons.replaceChildren();
    say(line, answer.status === 200 ? "This browser keeps no data for this page, so it can't stay signed in."
                                    : pairRefusal("finish", answer.status, answer.data), true);
  };

  next.addEventListener("click", async () => {
    const chosen = name.value.trim();
    if (!chosen) {
      say(line, "Give the phone a name.", true);
      return;
    }
    next.disabled = name.disabled = true;
    say(line, "Checking the pairing code…");
    const answer = await post("/api/pair/begin", { code, name: chosen });
    if (answer.status !== 200 || typeof answer.data?.pairing !== "string") {
      // A refused name can be fixed here; a spent or expired code can't.
      const retry = answer.status === 400;
      next.disabled = name.disabled = !retry;
      say(line, pairRefusal("begin", answer.status, answer.data), true);
      return;
    }
    pairing = answer.data.pairing;
    try {
      options = creationOptions(answer.data.options);
    } catch {
      options = null;
    }
    buttons.replaceChildren();
    if (options !== null && await platformPasskey()) {
      const make = el("button", "Create a passkey", "go");
      make.type = "button";
      buttons.append(make);
      say(line, "Moxi will ask this phone to make a passkey for it. Unlock the phone when it asks.");
      make.addEventListener("click", async () => {
        make.disabled = true;
        let made: Credential | null;
        try {
          made = await navigator.credentials.create({ publicKey: options as PublicKeyCredentialCreationOptions });
        } catch (error) {
          make.disabled = false;
          say(line, passkeyFailure(error), true);
          return;
        }
        if (made === null) {
          make.disabled = false;
          say(line, passkeyFailure(null), true);
          return;
        }
        await finish(credentialJson(made as PublicKeyCredential));
      });
    } else {
      const plain = el("button", "Pair without a passkey", "go");
      plain.type = "button";
      buttons.append(plain);
      main.insertBefore(el("p", NO_PASSKEY_NOTE, "dim"), line);
      say(line, "This browser can't make a passkey on this phone.");
      plain.addEventListener("click", async () => {
        plain.disabled = true;
        await finish(null);
      });
    }
  });
}

void start();

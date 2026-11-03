// /signin.js: the sign-in page's script. It fetches a challenge as the page loads (a begin
// stores nothing on the computer, and its challenge lives a minute), so the button's tap
// goes straight to the passkey prompt, then sends the assertion to `/api/signin/finish`.

import { el, post, say, signedIn, style } from "./gateway-page.ts";
import { credentialJson, PAIR_AGAIN, passkeyFailure, requestOptions, signinRefusal } from "./passkey.ts";

/** A challenge older than this is fetched again before the prompt. */
const FRESH_MS = 45_000;

async function start(): Promise<void> {
  style();
  const main = document.getElementById("moxi-signin") as HTMLElement;
  const buttons = el("div", undefined, "buttons");
  const go = el("button", "Sign in with a passkey", "go");
  go.type = "button";
  buttons.append(go);
  const line = el("p");
  line.setAttribute("role", "status");
  main.append(buttons, line,
              el("p", `A phone paired without a passkey signs in again only by a new pairing. ${PAIR_AGAIN}`, "dim"));
  // The address drops `n`, which only kept a cached answer from matching.
  history.replaceState(null, "", location.pathname);

  if (typeof PublicKeyCredential !== "function" || navigator.credentials === undefined) {
    go.disabled = true;
    say(line, "This browser has no passkeys here.", true);
    return;
  }

  let options: PublicKeyCredentialRequestOptions | null = null;
  let fetchedAt = 0;
  const fetchOptions = async (): Promise<boolean> => {
    const answer = await post("/api/signin/begin", {});
    if (answer.status !== 200) {
      say(line, signinRefusal(answer.status, answer.data), true);
      return false;
    }
    try {
      options = requestOptions(answer.data.options);
    } catch {
      say(line, "The computer's answer couldn't be read. Reload the page.", true);
      return false;
    }
    fetchedAt = Date.now();
    return true;
  };
  const ready = fetchOptions();

  go.addEventListener("click", async () => {
    go.disabled = true;
    say(line, "");
    if (!(await ready) || options === null || Date.now() - fetchedAt > FRESH_MS) {
      if (!(await fetchOptions())) {
        go.disabled = false;
        return;
      }
    }
    let asserted: Credential | null;
    try {
      asserted = await navigator.credentials.get({ publicKey: options as PublicKeyCredentialRequestOptions });
    } catch (error) {
      go.disabled = false;
      say(line, passkeyFailure(error), true);
      return;
    }
    // Each challenge is spent by one sign-in; the next try fetches another.
    fetchedAt = 0;
    if (asserted === null) {
      go.disabled = false;
      say(line, passkeyFailure(null), true);
      return;
    }
    say(line, "Signing in…");
    const answer = await post("/api/signin/finish", { credential: credentialJson(asserted as PublicKeyCredential) });
    if (answer.status === 200 && signedIn(answer.data)) return;
    go.disabled = false;
    say(line, answer.status === 200 ? "This browser keeps no data for this page, so it can't stay signed in."
                                    : signinRefusal(answer.status, answer.data), true);
  });
}

void start();

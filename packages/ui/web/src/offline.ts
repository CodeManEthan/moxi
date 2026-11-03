// The offline page, /offline.js. The gateway's worker shows the page in place of one it
// couldn't load, its root saying which case it saw and when the gateway last answered.
// It says so, and tries again every 30 seconds while it is visible, or at a tap.

import { bandText } from "./gateway-model.ts";
import { el, style } from "./gateway-page.ts";
import { offlineInfo, RETRY_MS } from "./worker-model.ts";

style();
const main = document.getElementById("moxi-offline");
if (main !== null) {
  const { reach, last } = offlineInfo(document.documentElement.dataset);
  const again = el("button", "Try again", "go");
  again.type = "button";
  again.addEventListener("click", () => location.reload());
  const buttons = el("div", undefined, "buttons");
  buttons.append(again);
  main.append(el("p", bandText(reach, location.hostname, last)), buttons,
              el("p", "This page tries again every 30 seconds while it's open.", "dim"));
  setInterval(() => {
    if (document.visibilityState === "visible") location.reload();
  }, RETRY_MS);
}

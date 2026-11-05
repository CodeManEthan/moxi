// The shell document's own script, run by client.js in the one document: the menu, and
// the panel for the URL. For `/m/<module>/<route>` it places `<moxi-<module>-page
// route="<route>">`. Moving between panels is `history.pushState`; the push stream and
// the menu stay. Everything is built with DOM calls and text nodes, never HTML strings.
// On the phone it leaves out Settings, which only the desk reaches, and its signed-out
// notice links to the gateway's sign-in page.

import type { ComponentInfo } from "./client.ts";
import { freshN } from "./passkey.ts";
import type { UpstreamState } from "./push.ts";
import { placeOnceMore, scriptFailure } from "./place-model.ts";
import type { Named } from "./place-model.ts";
import { NAME } from "./rules.ts";

/** One installed module as the menu sees it. */
export interface PanelModule {
  name: string;
  /** Its component named `page`, when the shell can place it. */
  page: ComponentInfo | null;
  /** Why its `page` was refused, when it was. */
  refused: string | null;
}

/** What the shell takes from client.js. */
export interface ShellDeps {
  /** The page came through the gateway, on the phone. */
  gateway: boolean;
  installed(module: string): boolean;
  known(module: string): boolean;
  place(host: Element, module: string, name: string, attrs?: Record<string, string>): Promise<Element | null>;
  modules(): PanelModule[];
  onModules(fn: () => void): void;
  onSession(fn: (signedIn: boolean) => void): void;
  onState(fn: (state: UpstreamState) => void): void;
  state(module: string): UpstreamState | null;
  setNavigator(fn: (module: string, route: string) => void): void;
}

/** The module and route the URL path names, or null for a path outside `/m/<module>/`. */
export function where(pathname: string): { module: string; route: string } | null {
  const match = /^\/m\/([^/]+)\/(.*)$/.exec(pathname);
  if (match === null || !NAME.test(match[1])) return null;
  return { module: match[1], route: match[2] };
}

/** The menu's entries: each module with a page, the shell's own first, then Settings, at
 * the desk only (`desk` false leaves it out). */
export function menuEntries(modules: PanelModule[], desk = true): Array<{ module: string; route: string; title: string }> {
  const pages = modules.filter((item) => item.page !== null)
    .sort((a, b) => (a.name === "ui" ? -1 : b.name === "ui" ? 1 : a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  const entries = pages.map((item) => ({ module: item.name, route: "", title: (item.page as ComponentInfo).title }));
  if (desk && modules.some((item) => item.name === "ui" && item.page !== null)) {
    entries.push({ module: "ui", route: "settings", title: "Settings" });
  }
  return entries;
}

/** What a panel load came to: the element placed, or the error panel to show; null when
 * another load began meanwhile. */
export type PanelLoad = { placed: Element } | { error: Named } | null;

/**
 * Places `module`'s panel with `place`, with `check` asking a fetch of its script why it
 * gave no element, and one more try when the script answers fine (`placeOnceMore`). A
 * failure `check` names is the error panel; after the second try it is that the panel did
 * not load.
 */
export async function loadPanel(module: string, place: () => Promise<Element | null>,
                                check: () => Promise<Named | null>,
                                current: () => boolean): Promise<PanelLoad> {
  const load = await placeOnceMore(place, check, current);
  if (load === null || "placed" in load) return load;
  return { error: load.failure ?? { title: `${module}'s panel did not load`,
    lines: [`${module}'s panel script could not be loaded. Run moxi doctor ${module} to see why.`] } };
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

export function boot(deps: ShellDeps): void {
  const root = document.getElementById("moxi-shell") as HTMLElement;
  const list = root.querySelector("#moxi-menu ul") as HTMLElement;
  const button = root.querySelector("#moxi-menu-button") as HTMLButtonElement;
  const panel = root.querySelector("#moxi-panel") as HTMLElement;
  const notice = root.querySelector("#moxi-notice") as HTMLElement;
  const status = root.querySelector("#moxi-status") as HTMLElement;
  let shown = "";
  let seq = 0;

  const signature = (): string => {
    const at = where(location.pathname);
    if (at === null) return "none";
    const item = deps.modules().find((mod) => mod.name === at.module);
    return [location.pathname, deps.installed(at.module), deps.known(at.module),
            item?.page?.url ?? "", item?.page?.contract ?? "", item?.refused ?? ""].join("|");
  };

  const say = (title: string, ...lines: string[]): void => {
    seq += 1;
    const box = el("section");
    box.className = "moxi-message";
    box.append(el("h1", title), ...lines.map((line) => el("p", line)));
    panel.replaceChildren(box);
    document.title = `${title} - Moxi`;
  };

  const showStatus = (): void => {
    const at = where(location.pathname);
    const state = at === null ? null : deps.state(at.module);
    if (state !== null && state.state === "down" && at !== null) {
      status.textContent = `${at.module}'s page server is down${state.detail ? `: ${state.detail}` : ""}.`;
      status.hidden = false;
    } else {
      status.hidden = true;
    }
  };

  const renderPanel = async (): Promise<void> => {
    shown = signature();
    showStatus();
    const at = where(location.pathname);
    if (at === null) {
      say("Nothing is here", "Open a panel from the menu.");
      return;
    }
    const { module, route } = at;
    if (!deps.installed(module)) {
      if (deps.known(module)) {
        say(`${module} is not working`, `${module} is installed but can't be loaded. Run moxi doctor ${module} to see why.`);
      } else {
        say(`No module named ${module}`, `No module named ${module} is installed.`);
      }
      return;
    }
    const item = deps.modules().find((mod) => mod.name === module);
    if (item?.page == null) {
      say(`${module} has no panel`, item?.refused ? `Its panel can't be shown: ${item.refused}.`
        : `${module} declares no panel.`);
      return;
    }
    seq += 1;
    const mine = seq;
    panel.replaceChildren();
    document.title = `${item.page.title} - Moxi`;
    const url = item.page.url;
    // A script that did not load may be a start that failed: the proxy answers its 502
    // with the exit and the log, and a second fetch within 10 s gets the same answer
    // with no new start.
    const load = await loadPanel(module, () => deps.place(panel, module, "page", { route }),
                                 () => scriptFailure(module, url), () => mine === seq);
    if (load !== null && "error" in load) say(load.error.title, ...load.error.lines);
  };

  const renderMenu = (): void => {
    const at = where(location.pathname);
    const items = menuEntries(deps.modules(), !deps.gateway).map((entry) => {
      const link = el("a", entry.title);
      link.href = `/m/${entry.module}/${entry.route}`;
      link.dataset.module = entry.module;
      link.dataset.route = entry.route;
      const current = at !== null && at.module === entry.module
        && (entry.route === "" ? !(entry.module === "ui" && at.route.startsWith("settings"))
          : at.route.startsWith(entry.route));
      if (current) link.setAttribute("aria-current", "page");
      const item = el("li");
      item.append(link);
      return item;
    });
    list.replaceChildren(...items);
  };

  const setOpen = (open: boolean): void => {
    root.classList.toggle("moxi-menu-open", open);
    button.setAttribute("aria-expanded", String(open));
  };

  const go = (): void => {
    setOpen(false);
    renderMenu();
    void renderPanel();
  };

  root.addEventListener("click", (event) => {
    const link = (event.target as Element).closest?.("a[data-module]") as HTMLAnchorElement | null;
    if (link === null || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (link.pathname !== location.pathname) history.pushState(null, "", link.pathname);
    go();
  });
  button.addEventListener("click", () => setOpen(!root.classList.contains("moxi-menu-open")));
  addEventListener("popstate", go);
  deps.setNavigator(go);
  deps.onModules(() => {
    renderMenu();
    if (signature() !== shown) void renderPanel();
  });
  deps.onState(showStatus);
  // On the phone the way back in is the gateway's sign-in page, with a new `n` each time
  // so no cached answer serves it.
  const signIn = el("a", "Sign in");
  if (deps.gateway) {
    signIn.href = `/signin?n=${freshN()}`;
    signIn.addEventListener("click", () => { signIn.href = `/signin?n=${freshN()}`; });
    notice.replaceChildren("This phone is signed out of Moxi. ", signIn);
  }
  deps.onSession((signedIn) => {
    if (deps.gateway) signIn.href = `/signin?n=${freshN()}`;
    notice.hidden = signedIn;
    if (signedIn) void renderPanel();
  });
  renderMenu();
  void renderPanel();
}

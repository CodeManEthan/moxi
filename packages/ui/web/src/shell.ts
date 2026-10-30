// The shell document's own script, run by client.js in the one document: the menu, and
// the panel for the URL. For `/m/<module>/<route>` it places `<moxi-<module>-page
// route="<route>">`. Moving between panels is `history.pushState`; the push stream and
// the menu stay. Everything is built with DOM calls and text nodes, never HTML strings.

import type { ComponentInfo } from "./client.ts";
import type { UpstreamState } from "./push.ts";
import { NAME } from "./rules.ts";
import { failurePanel } from "./views.ts";

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

/** The menu's entries: each module with a page, the shell's own first, then Settings. */
export function menuEntries(modules: PanelModule[]): Array<{ module: string; route: string; title: string }> {
  const pages = modules.filter((item) => item.page !== null)
    .sort((a, b) => (a.name === "ui" ? -1 : b.name === "ui" ? 1 : a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  const entries = pages.map((item) => ({ module: item.name, route: "", title: (item.page as ComponentInfo).title }));
  if (modules.some((item) => item.name === "ui" && item.page !== null)) {
    entries.push({ module: "ui", route: "settings", title: "Settings" });
  }
  return entries;
}

/** The error panel for a page script at `url` that did not load, from a fetch of it
 * (an asset needs the cookie only), or null when the answer names no failure. */
async function failure(url: string): Promise<{ title: string; lines: string[] } | null> {
  const module = where(new URL(url, location.origin).pathname)?.module ?? "";
  let answer: Response;
  try {
    answer = await fetch(url, { credentials: "same-origin", cache: "no-store" });
  } catch {
    return failurePanel(module, 0, null);
  }
  if (answer.ok) return null;
  let data: unknown = null;
  try {
    data = await answer.json();
  } catch {
    data = null;
  }
  return failurePanel(module, answer.status, data);
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
    let placed: Element | null = null;
    try {
      placed = await deps.place(panel, module, "page", { route });
    } catch (err) {
      console.error(err);
    }
    if (mine !== seq) {
      placed?.remove();
      return;
    }
    if (placed !== null) return;
    // A script that did not load may be a start that failed: the proxy answers its 502
    // with the exit and the log, and a second fetch within 10 s gets the same answer
    // with no new start.
    const panelFailure = await failure(item.page.url);
    if (mine !== seq) return;
    if (panelFailure !== null) say(panelFailure.title, ...panelFailure.lines);
    else say(`${module}'s panel did not load`, `${module}'s panel script could not be loaded. Run moxi doctor ${module} to see why.`);
  };

  const renderMenu = (): void => {
    const at = where(location.pathname);
    const items = menuEntries(deps.modules()).map((entry) => {
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
  deps.onSession((signedIn) => {
    notice.hidden = signedIn;
    if (signedIn) void renderPanel();
  });
  renderMenu();
  void renderPanel();
}

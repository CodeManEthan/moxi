// The shell document's own script, run by client.js in the one document: the top line,
// the band of pages and the panel for the URL. For `/m/<module>/<route>` it places
// `<moxi-<module>-page route="<route>">`. Moving between panels is `history.pushState`;
// the push stream and the top line stay. Everything is built with DOM calls and text
// nodes, never HTML strings. On the phone it hides Settings, which only the desk
// reaches, and its signed-out notice links to the gateway's sign-in page.

import type { ComponentInfo } from "./client.ts";
import { freshN } from "./passkey.ts";
import type { UpstreamState } from "./push.ts";
import { placeOnceMore, scriptFailure } from "./place-model.ts";
import type { Named } from "./place-model.ts";
import { NAME } from "./rules.ts";

/** One installed module as the band sees it. */
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

/** A page's group in the band: the pages of a day's work, then the pages that set Moxi up. */
export type Group = "daily" | "setup";

export const GROUPS: readonly Group[] = ["daily", "setup"];

/** One name in the band. */
export interface BandEntry {
  module: string;
  route: string;
  title: string;
  group: Group;
}

/**
 * The band's entries, in the order shown: the shell's own Home first in `daily`, each
 * module's placed page by its `order` and then its title within its group, and the
 * shell's own Modules last in `setup`. A group that is not `daily` reads as `setup` and
 * an order that is no number as 500. Settings is not in the band; the top line holds it.
 */
export function bandEntries(modules: PanelModule[]): BandEntry[] {
  const pages = modules.filter((item) => item.name !== "ui" && item.page !== null).map((item) => {
    const page = item.page as ComponentInfo;
    const group: Group = page.group === "daily" ? "daily" : "setup";
    return { module: item.name, route: "", title: page.title, group,
             order: typeof page.order === "number" ? page.order : 500 };
  }).sort((a, b) => a.order - b.order || (a.title < b.title ? -1 : a.title > b.title ? 1 : 0)
                    || (a.module < b.module ? -1 : 1));
  const of = (group: Group): BandEntry[] => pages.filter((item) => item.group === group)
    .map(({ module, route, title }) => ({ module, route, title, group }));
  return [{ module: "ui", route: "", title: "Home", group: "daily" }, ...of("daily"),
          ...of("setup"), { module: "ui", route: "modules", title: "Modules", group: "setup" }];
}

/** Whether the band's `entry` is the page at `at`. No entry is current on a settings
 * route, or on a route of the shell's own that is neither Home nor Modules. */
export function isCurrent(entry: { module: string; route: string }, at: { module: string; route: string } | null): boolean {
  if (at === null || at.module !== entry.module) return false;
  return entry.module === "ui" ? at.route.replace(/\/+$/, "") === entry.route : true;
}

/**
 * The name the top line gives the page at `at`: Home, Modules or Settings on the shell's
 * own routes, another module's `page` title on its routes. Null when no page is there to
 * name, and the panel holds a message of the shell's own.
 */
export function pageName(at: { module: string; route: string } | null, modules: PanelModule[]): string | null {
  if (at === null) return null;
  if (at.module === "ui") {
    const route = at.route.replace(/\/+$/, "");
    if (route === "") return "Home";
    if (route === "modules") return "Modules";
    return route === "settings" || route.startsWith("settings/") ? "Settings" : null;
  }
  return modules.find((item) => item.name === at.module)?.page?.title ?? null;
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
  const top = root.querySelector(".moxi-top") as HTMLElement;
  const band = root.querySelector("#moxi-pages") as HTMLElement;
  const button = root.querySelector("#moxi-pages-button") as HTMLButtonElement;
  const settings = root.querySelector("#moxi-settings") as HTMLAnchorElement;
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
            item?.page?.url ?? "", item?.page?.contract ?? "", item?.page?.title ?? "",
            item?.refused ?? ""].join("|");
  };

  const say = (title: string, ...lines: string[]): void => {
    seq += 1;
    const box = el("section");
    box.className = "moxi-message";
    box.append(el("h1", title), ...lines.map((line) => el("p", line)));
    panel.replaceChildren(box);
    name(null);
  };

  /** The top line's name for the page, with its caret; `Pages` when none is shown. */
  const name = (title: string | null): void => {
    const caret = el("span", "▾");
    caret.className = "moxi-caret";
    caret.setAttribute("aria-hidden", "true");
    button.replaceChildren(title ?? "Pages", caret);
    document.title = title === null ? "Moxi" : `${title} - Moxi`;
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
      say("Nothing is here", "Open a page from the top line.");
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
    name(pageName(at, deps.modules()));
    const url = item.page.url;
    // A script that did not load may be a start that failed: the proxy answers its 502
    // with the exit and the log, and a second fetch within 10 s gets the same answer
    // with no new start.
    const load = await loadPanel(module, () => deps.place(panel, module, "page", { route }),
                                 () => scriptFailure(module, url), () => mine === seq);
    if (load !== null && "error" in load) say(load.error.title, ...load.error.lines);
  };

  const renderBand = (): void => {
    const at = where(location.pathname);
    const entries = bandEntries(deps.modules());
    const groups = GROUPS.map((group) => entries.filter((entry) => entry.group === group))
      .filter((items) => items.length > 0).map((items) => {
        const box = el("span");
        box.className = "moxi-pages-group";
        box.dataset.group = items[0].group;
        box.append(...items.map((entry) => {
          const link = el("a", entry.title);
          link.href = `/m/${entry.module}/${entry.route}`;
          link.dataset.module = entry.module;
          link.dataset.route = entry.route;
          if (isCurrent(entry, at)) link.setAttribute("aria-current", "page");
          return link;
        }));
        return box;
      });
    band.replaceChildren(...groups);
    if (at !== null && at.module === "ui" && pageName(at, []) === "Settings") {
      settings.setAttribute("aria-current", "page");
    } else {
      settings.removeAttribute("aria-current");
    }
  };

  const isOpen = (): boolean => !band.hidden;
  const setOpen = (open: boolean): void => {
    band.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
  };

  const go = (): void => {
    setOpen(false);
    renderBand();
    void renderPanel();
  };

  root.addEventListener("click", (event) => {
    const link = (event.target as Element).closest?.("a[data-module]") as HTMLAnchorElement | null;
    if (link === null || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (link.pathname !== location.pathname) history.pushState(null, "", link.pathname);
    const picked = band.contains(link);
    go();
    // A pick hides the name that held the focus; the button that opened the band takes it.
    if (picked) button.focus();
  });
  button.addEventListener("click", () => setOpen(!isOpen()));
  // The open band closes on Escape, which hands the focus back to its button, and on a
  // click or a focus that lands outside the top line and the band.
  const outside = (event: Event): void => {
    const target = event.target as Node | null;
    if (isOpen() && target !== null && !top.contains(target) && !band.contains(target)) setOpen(false);
  };
  document.addEventListener("click", outside);
  document.addEventListener("focusin", outside);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !isOpen()) return;
    setOpen(false);
    button.focus();
  });
  addEventListener("popstate", go);
  deps.setNavigator(go);
  deps.onModules(() => {
    renderBand();
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
  settings.hidden = deps.gateway;
  name(pageName(where(location.pathname), deps.modules()));
  renderBand();
  void renderPanel();
}

// The page's address and the filtered view's join, with no DOM so the Node tests drive
// them.
//
// The address is the route after `/m/loadout/`: `""` nothing open; `item/<section>/<kind>/<name>`
// an opened item; `global` the global file opened; `kind/<section>/<kind>` a folded kind
// opened; `add/<kind>/<name>` Add opened on a file no section lists; `add` Add by name;
// `replace/<section>/<id>` Replace's confirm; `project/<key>` the filtered view. One thing is
// open at a time, and the page writes its address with `history.replaceState`.
//
// A part keeps letters, digits, `.`, `_`, `~`, `:`, `@` and `-` as they are and writes every
// other byte of its UTF-8 as `!` and two hex digits (`~/code/app` is `~!2Fcode!2Fapp`). So no
// address the page writes holds a `%`, which the shell refuses in a path it is asked to load:
// each one loads when it is reloaded, pasted or opened from a bookmark. A part read back is
// percent-decoded first, so a link that URI-encodes its parts opens the same thing.

import type { Project, Section, View } from "./feed.ts";
import { itemsView } from "./items-model.ts";
import type { GroupView } from "./items-model.ts";
import { placedView } from "./placed-model.ts";
import type { PlacedView, Problem } from "./placed-model.ts";
import { homeForm, kindFolded, kindWord, sectionWord } from "./words.ts";

export type Address =
  | { piece: "page" }
  | { piece: "item"; section: string; kind: string; name: string }
  | { piece: "global" }
  | { piece: "kind"; section: string; kind: string }
  | { piece: "add"; kind: string | null; name: string | null }
  | { piece: "replace"; section: string; id: string }
  | { piece: "project"; key: string };

export const NOTHING_OPEN: Address = { piece: "page" };

/** The bytes a part keeps as they are; the shell loads a path of these and `/`. */
const KEPT = /^[A-Za-z0-9._~:@-]$/;
const UTF8 = new TextEncoder();

/** `part` as the address writes it. */
export function encodePart(part: string): string {
  let out = "";
  for (const ch of part) {
    if (KEPT.test(ch)) out += ch;
    else for (const byte of UTF8.encode(ch)) out += `!${byte.toString(16).toUpperCase().padStart(2, "0")}`;
  }
  return out;
}

/** The part an address's `text` writes, or null when it writes none. */
export function decodePart(text: string): string | null {
  let plain: string;
  try {
    plain = decodeURIComponent(text);
  } catch {
    return null;
  }
  if (!/^(?:[^!]|![0-9A-Fa-f]{2})*$/.test(plain)) return null;
  const bytes: number[] = [];
  for (const piece of plain.split(/(![0-9A-Fa-f]{2})/)) {
    if (piece.startsWith("!")) bytes.push(parseInt(piece.slice(1), 16));
    else bytes.push(...UTF8.encode(piece));
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(bytes));
  } catch {
    return null;
  }
}

function decoded(parts: readonly string[]): string[] | null {
  const out = parts.map(decodePart);
  return out.some((part) => part === null || part === "") ? null : out as string[];
}

/** The address a route names; a route naming nothing the page opens is the page. */
export function parseRoute(route: string): Address {
  const parts = route.replace(/^\/+|\/+$/g, "").split("/");
  const [head, ...rest] = parts;
  const args = decoded(rest);
  if (args === null) return NOTHING_OPEN;
  if (head === "global" && args.length === 0) return { piece: "global" };
  if (head === "add" && args.length === 0) return { piece: "add", kind: null, name: null };
  if (head === "add" && args.length === 2) return { piece: "add", kind: args[0], name: args[1] };
  if (head === "item" && args.length === 3) return { piece: "item", section: args[0], kind: args[1], name: args[2] };
  if (head === "kind" && args.length === 2) return { piece: "kind", section: args[0], kind: args[1] };
  if (head === "replace" && args.length === 2) return { piece: "replace", section: args[0], id: args[1] };
  if (head === "project" && args.length === 1) return { piece: "project", key: args[0] };
  return NOTHING_OPEN;
}

const enc = (...parts: string[]): string => parts.map(encodePart).join("/");

/** The route of `address`. */
export function routeOf(address: Address): string {
  switch (address.piece) {
    case "page": return "";
    case "global": return "global";
    case "add": return address.kind === null || address.name === null ? "add" : `add/${enc(address.kind, address.name)}`;
    case "item": return `item/${enc(address.section, address.kind, address.name)}`;
    case "kind": return `kind/${enc(address.section, address.kind)}`;
    case "replace": return `replace/${enc(address.section, address.id)}`;
    case "project": return `project/${enc(address.key)}`;
  }
}

/** Whether two addresses name the same thing. */
export function sameAddress(a: Address, b: Address): boolean {
  return a.piece === b.piece && routeOf(a) === routeOf(b);
}

/** The opened item `kind` `name` of section `section` (`user` or a project section's name). */
export function itemAddress(section: string, kind: string, name: string): Address {
  return { piece: "item", section, kind, name };
}

/** Whether `address` opens the item `kind` `name` of section `section`. */
export function opensItem(address: Address, section: string, kind: string, name: string): boolean {
  return address.piece === "item" && address.section === section && address.kind === kind && address.name === name;
}

/** The kind a folded group of section `section` shows open: the kind `address` opens there,
 * or the kind of the item it opens there, since that item is drawn among its kind's rows. */
export function openKind(address: Address, section: string): string | null {
  if ((address.piece === "kind" || address.piece === "item") && address.section === section) return address.kind;
  return null;
}

/** What `Close` leaves open: an item opened inside its kind closes to the kind, anything
 * else to nothing. */
export function closed(address: Address, folded: boolean): Address {
  if (address.piece === "item" && folded) return { piece: "kind", section: address.section, kind: address.kind };
  return NOTHING_OPEN;
}

/** The path the browser's address bar shows for `address`. */
export function addressPath(address: Address): string {
  return `/m/loadout/${routeOf(address)}`;
}

/** The path to write in the browser's address for `address`, rewriting the entry it is on:
 * null when the address already says it, or the document is not at the page. */
export function pathToWrite(address: Address, pathname: string): string | null {
  if (!/^\/m\/loadout(\/|$)/.test(pathname)) return null;
  const path = addressPath(address);
  return path === pathname ? null : path;
}

/**
 * Whether what `address` names is on the page now: null when it is (or the page has
 * nothing open), else the dim line the page shows at its foot as it opens with nothing
 * open. `unlisted` holds `<kind>/<name>` for each file Not listed shows; Add on a file
 * is checked against it, and a filtered view against `projects`.
 */
export function goneLine(address: Address, view: View, problems: readonly Problem[],
                         unlisted: ReadonlySet<string> | null, projects: readonly Project[] | null): string | null {
  const named = (section: string): string => {
    const found = view.sections.find((s) => (s.where === "user" ? "user" : s.name) === section);
    return found === undefined ? section : sectionWord(found, view.home);
  };
  switch (address.piece) {
    case "page":
    case "global":
      return null;
    case "add":
      if (address.kind === null || unlisted === null || unlisted.has(`${address.kind}/${address.name}`)) return null;
      return `${address.name} is no longer a file no section lists.`;
    case "item":
      if (view.items.some((i) => i.scope === address.section && i.kind === address.kind && i.name === address.name)) {
        return null;
      }
      return `${named(address.section)} no longer lists the ${kindWord(address.kind)} ${address.name}.`;
    case "kind":
      if (view.items.some((i) => i.scope === address.section && i.kind === address.kind)) return null;
      return `${named(address.section)} lists no ${kindFolded(address.kind).toLowerCase()} now.`;
    case "replace":
      if (problems.some((p) => p.act?.verb === "replace" && p.act.id === address.id && p.act.section === address.section)) {
        return null;
      }
      if (view.rows.some((r) => r.replaceable && (r.scope ?? "user") === address.section
                                && `${r.kind}:${r.name}@${r.harness}` === address.id)) {
        return null;
      }
      return `Nothing at ${address.id} needs Replace now.`;
    case "project":
      if (projects === null || projectNamed(projects, address.key) !== null) return null;
      return `No project is named ${address.key}.`;
  }
}

/** `goneLine` for what the page judges from the view and the folder read: a filtered
 * view's project is judged with it, so here it is never gone; Add on a file is judged
 * only once the folder is read (`unlisted` not null). */
export function goneLineOf(address: Address, view: View, problems: readonly Problem[] = [],
                           unlisted: ReadonlySet<string> | null = null): string | null {
  return goneLine(address, view, problems, unlisted, null);
}

// -- the filtered view -----------------------------------------------------------------------

/** The project `key` names, matched ignoring case, an exact match first; null for none. */
export function projectNamed(projects: readonly Project[], key: string): Project | null {
  const exact = projects.find((p) => p.key === key);
  if (exact !== undefined) return exact;
  const lower = key.toLowerCase();
  return projects.find((p) => p.key.toLowerCase() === lower) ?? null;
}

/** The project's section: the key section matching its key ignoring case, or the path
 * section whose root is the project's root exactly; null when it has none. `view` is
 * `/api/view`'s or `/api/list`'s: only its sections are read. */
export function sectionFor(project: Project, view: { sections: readonly Section[] }): string | null {
  const lower = project.key.toLowerCase();
  const keyed = view.sections.find((s) => s.where === "key" && s.name.toLowerCase() === lower);
  if (keyed !== undefined) return keyed.name;
  const rooted = view.sections.find((s) => s.where === "path" && s.root === project.root);
  return rooted === undefined ? null : rooted.name;
}

/** The filter line's fixed words. */
export const PROJECT_LABEL = "Project";
export const ALL_OF_LOADOUT = "All of loadout";
/** The link under the project's own group, to the project on the Projects page. */
export const PROJECT_LINK = "Project";

/** `view` cut to what sessions in a project load: Your items (the global file and the
 * skills modules took over among them) and the project's section `section`. Verdict, problems
 * and counts read from it count only those. */
export function narrowed(view: View, section: string | null): View {
  const keep = (scope: string | null): boolean => scope === "user" || (section !== null && scope === section);
  return {
    ...view,
    sections: view.sections.filter((s) => s.where === "user" || s.name === section),
    items: view.items.filter((item) => keep(item.scope)),
    rows: view.rows.filter((row) => keep(row.scope)),
  };
}

export interface FilteredView {
  project: Project;
  /** The project's key, set in the filter line's picker. */
  key: string;
  /** `what sessions in <root> load`, the root in `~` form. */
  where: string;
  /** Placed over the project's section and Your items only. */
  placed: PlacedView;
  /** The project's section as `<key>  <n> of its own`, or null when it has none. */
  own: { label: string; count: string; group: GroupView } | null;
  /** `Yours too`: Your items, the global file among them. */
  yours: { label: string; count: string; group: GroupView };
  /** The skills modules took over, which the project's sessions load too. */
  modules: GroupView[];
}

/** The filtered view of project `key`, or null when no project has that key. Folder and
 * Not listed are hidden there. */
export function filteredView(key: string, projects: readonly Project[], view: View): FilteredView | null {
  const project = projectNamed(projects, key);
  if (project === null) return null;
  const section = sectionFor(project, view);
  const cut = narrowed(view, section);
  const items = itemsView(cut);
  const mine = items.groups.find((g) => g.key === "user") as GroupView;
  const group = section === null ? undefined : items.groups.find((g) => g.module === null && g.key === section);
  const own = group === undefined ? null : { label: project.key, count: `${group.rows.length} of its own`, group };
  return {
    project, key: project.key, where: `what sessions in ${homeForm(project.root, view.home)} load`,
    placed: placedView(cut, items), own, yours: { label: "Yours too", count: mine.count, group: mine },
    modules: items.groups.filter((g) => g.module !== null),
  };
}

/** One project in the filter line's picker. */
export interface ProjectChoice {
  key: string;
  /** Its root in `~` form, dim. */
  root: string;
  picked: boolean;
}

/** The filter line's picker: each project, the one shown picked. */
export function projectChoices(projects: readonly Project[], shown: Project, view: View): ProjectChoice[] {
  return projects.map((p) => ({ key: p.key, root: homeForm(p.root, view.home), picked: p.key === shown.key }));
}

/** The words for a filtered view's address while `/api/projects` can't say which projects
 * there are (`cause` busy, unreadable or error): the page shows all of loadout. */
export function unfilteredLine(key: string, cause: string): string {
  return cause === "busy" ? `Projects is busy, so this shows all of loadout, not only what ${key} loads.`
    : `Projects can't be read, so this shows all of loadout, not only what ${key} loads.`;
}

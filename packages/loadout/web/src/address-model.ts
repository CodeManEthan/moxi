// The page's address and the filtered view's join, with no DOM so the Node tests drive
// them.
//
// The address is the route after `/m/loadout/`: `""` nothing open; `item/<section>/<kind>/<name>`
// an opened item; `global` the global file opened; `kind/<section>/<kind>` a folded kind
// opened; `add/<kind>/<name>` Add opened on a file no section lists; `add` Add by name;
// `replace/<section>/<id>` Replace's confirm; `project/<key>` the filtered view. Each part is
// URI-encoded. One thing is open at a time, and the page writes its address with
// `history.replaceState`.

import type { Project, View } from "./feed.ts";
import type { GroupView, ItemsView } from "./items-model.ts";
import type { PlacedView, Problem } from "./placed-model.ts";
import { kindFolded, kindWord, sectionWord } from "./words.ts";

export type Address =
  | { piece: "page" }
  | { piece: "item"; section: string; kind: string; name: string }
  | { piece: "global" }
  | { piece: "kind"; section: string; kind: string }
  | { piece: "add"; kind: string | null; name: string | null }
  | { piece: "replace"; section: string; id: string }
  | { piece: "project"; key: string };

export const NOTHING_OPEN: Address = { piece: "page" };

function decoded(parts: readonly string[]): string[] | null {
  try {
    const out = parts.map((part) => decodeURIComponent(part));
    return out.some((part) => part === "") ? null : out;
  } catch {
    return null;
  }
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

const enc = (...parts: string[]): string => parts.map(encodeURIComponent).join("/");

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

// -- the filtered view -----------------------------------------------------------------------

/** The project `key` names, matched ignoring case, an exact match first; null for none. */
export function projectNamed(projects: readonly Project[], key: string): Project | null {
  const exact = projects.find((p) => p.key === key);
  if (exact !== undefined) return exact;
  const lower = key.toLowerCase();
  return projects.find((p) => p.key.toLowerCase() === lower) ?? null;
}

/** The project's section: the key section matching its key ignoring case, or the path
 * section whose root is the project's root exactly; null when it has none. */
export function sectionFor(project: Project, view: View): string | null {
  const lower = project.key.toLowerCase();
  const keyed = view.sections.find((s) => s.where === "key" && s.name.toLowerCase() === lower);
  if (keyed !== undefined) return keyed.name;
  const rooted = view.sections.find((s) => s.where === "path" && s.root === project.root);
  return rooted === undefined ? null : rooted.name;
}

export interface FilteredView {
  project: Project;
  /** `Project <key> ▾ · what sessions in <root> load`. */
  line: string;
  /** The problems of the project's section and Your items. */
  problems: Problem[];
  /** The project's section as `<key>  <n> of its own`, or null when it has none. */
  own: { label: string; count: string; group: GroupView } | null;
  /** `Yours too`: Your items, the global file among them. */
  yours: { label: string; count: string; group: GroupView };
}

/** The filtered view of project `key`, or null when no project has that key. Folder and
 * Not listed are hidden there. */
export function filteredView(key: string, projects: readonly Project[], view: View, items: ItemsView,
                             placed: PlacedView): FilteredView | null {
  const project = projectNamed(projects, key);
  if (project === null) return null;
  const section = sectionFor(project, view);
  const keep = new Set(["user", ...(section === null ? [] : [section])]);
  const problems = placed.problems.filter((p) => p.section !== null && keep.has(p.section));
  const mine = items.groups.find((g) => g.key === "user") as GroupView;
  const group = section === null ? undefined : items.groups.find((g) => g.module === null && g.key === section);
  const own = group === undefined ? null
    : { label: project.key, count: `${group.rows.length} of its own`, group };
  return {
    project, line: `Project ${project.key} ▾ · what sessions in ${homeFormOf(project.root, view)} load`, problems,
    own, yours: { label: "Yours too", count: mine.count, group: mine },
  };
}

function homeFormOf(path: string, view: View): string {
  return sectionWord({ name: path, where: "path", root: path }, view.home);
}

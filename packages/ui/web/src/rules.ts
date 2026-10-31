// The checks client.js makes before it sends or places anything. Each throws a TypeError
// for a call no component should make, so a mistake shows where it was made.

import type { ComponentInfo } from "./client.ts";

/** A module, topic or component name: lowercase words of letters and digits joined by -. */
export const NAME = /^[a-z0-9]+(-[a-z0-9]+)*$/;

export function checkName(what: string, name: unknown): asserts name is string {
  if (typeof name !== "string" || !NAME.test(name)) {
    throw new TypeError(`${what} ${JSON.stringify(name)} is not a name of lowercase words joined by -`);
  }
}

/** The path and query `api` requests for `path` under `/m/<module>/`. A path that is
 * absolute, names another origin, or climbs out of `/m/<module>/` is refused. */
export function apiTarget(module: string, path: unknown, origin: string): string {
  checkName("module", module);
  const base = new URL(`/m/${module}/`, origin);
  if (typeof path !== "string" || path.startsWith("/") || path.includes("\\")) {
    throw new TypeError(`api path ${JSON.stringify(path)} is not relative to /m/${module}/`);
  }
  const url = new URL(path, base);
  if (url.origin !== base.origin || !url.pathname.startsWith(base.pathname)) {
    throw new TypeError(`api path ${JSON.stringify(path)} leaves /m/${module}/`);
  }
  return url.pathname + url.search;
}

/** The URL `navigate` shows for `route` under `/m/<module>/`, and the route without its
 * leading slashes. */
export function panelTarget(module: string, route: unknown, origin: string): { url: string; route: string } {
  checkName("module", module);
  if (typeof route !== "string") throw new TypeError("navigate's route is a string");
  const clean = route.replace(/^\/+/, "");
  const url = new URL(`/m/${module}/${clean}`, origin);
  if (url.origin !== origin || !url.pathname.startsWith(`/m/${module}/`)) {
    throw new TypeError(`route ${JSON.stringify(route)} leaves /m/${module}/`);
  }
  return { url: url.pathname + url.search + url.hash, route: clean };
}

/** Checks `attrs` against what `info` declares: each one declared and of its type, and
 * every required one given. A page also takes `route`, which the shell gives it. */
export function checkAttributes(info: ComponentInfo, attrs: Record<string, unknown>): void {
  for (const [name, value] of Object.entries(attrs)) {
    const where = `${info.element}'s attribute ${name}`;
    if (typeof value !== "string") throw new TypeError(`${where} is given as a string`);
    if (info.place === "page" && name === "route") continue;
    const spec = info.attributes.find((attr) => attr.name === name);
    if (spec === undefined) throw new TypeError(`${where} is not declared`);
    const ok = spec.type === "int" ? /^-?[0-9]+$/.test(value)
      : spec.type === "bool" ? value === "true" || value === "false"
        : spec.type === "enum" ? spec.values.includes(value) : true;
    if (!ok) throw new TypeError(`${where} is a ${spec.type}, not ${JSON.stringify(value)}`);
  }
  for (const spec of info.attributes) {
    if (spec.required && !Object.hasOwn(attrs, spec.name)) {
      throw new TypeError(`${info.element} needs its attribute ${spec.name}`);
    }
  }
}

/** The component's script URL, or null when it is not under `/m/<module>/` here. */
export function scriptUrl(info: ComponentInfo, module: string, origin: string): string | null {
  let url: URL;
  try {
    url = new URL(info.url, origin);
  } catch {
    return null;
  }
  return url.origin === origin && url.pathname.startsWith(`/m/${module}/`) ? url.href : null;
}

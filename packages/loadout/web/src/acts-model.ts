// The acts the page sends: each its route relative to loadout's page, its body, and the
// verb's words as he would type them, which the page shows in place of the act while the
// harness homes' note is set. No DOM, so the Node tests read it.

import type { ActScope, ProblemAct } from "./placed-model.ts";
import { actArgv, scopeFlags } from "./placed-model.ts";

/** One act: its route, its body, and the verb's words after `moxi loadout`. */
export interface PageAct {
  path: string;
  body: Record<string, unknown>;
  argv: string[];
}

/** How a section is named to an act: the user section, or a project section by name. */
export type Section = { user: true } | { project: string };

/** The section of `scope` (`user` or a project section's name). */
export function sectionOf(scope: string): Section {
  return scope === "user" ? { user: true } : { project: scope };
}

/** `install` under `scope`: `--user`, one project, or every section. */
export function installAct(scope: ActScope): PageAct {
  const body = "user" in scope ? { user: true } : "project" in scope ? { projects: [scope.project] } : {};
  return { path: "api/install", body, argv: ["install", ...scopeFlags(scope)] };
}

/** `install --replace <id>` under `scope`, with the job id the page minted for the press. */
export function replaceAct(id: string, scope: ActScope, job: string): PageAct {
  const sections = "user" in scope ? { user: true } : "project" in scope ? { project: scope.project } : {};
  return { path: "api/replace", body: { replace: id, ...sections, job },
           argv: ["install", `--replace=${id}`, ...scopeFlags(scope)] };
}

/** The act a problem's fix sends; a Replace takes the press's job id. */
export function problemAct(act: ProblemAct, job: string): PageAct {
  if (act.verb === "install") return installAct(act.scope);
  return { ...replaceAct(act.id ?? "", act.scope, job), argv: actArgv(act) };
}

const flagsOf = (section: Section): string[] => "user" in section ? ["--user"] : [`--project=${section.project}`];

/** `enable` (`on`) or `disable` of `<kind>:<name>@<harness>` in one section. */
export function toggleAct(kind: string, name: string, harness: string, section: Section, { on }: { on: boolean }): PageAct {
  const id = `${kind}:${name}@${harness}`;
  const verb = on ? "enable" : "disable";
  return { path: `api/${verb}`, body: { id, ...section }, argv: [verb, id, ...flagsOf(section)] };
}

/** `remove <kind> <name>` from one section: the user section is the one with no project. */
export function removeAct(kind: string, name: string, section: Section): PageAct {
  const project = "project" in section ? { project: section.project } : {};
  return { path: "api/remove", body: { kind, name, ...project },
           argv: ["remove", kind, name, ..."project" in section ? [`--project=${section.project}`] : []] };
}

/** `add <kind> <name>` to one section: Your items with `section` null, else that project
 * section (or a project's key, which gives it a section). */
export function addAct(kind: string, name: string, section: string | null): PageAct {
  return { path: "api/add", body: { kind, name, ...(section === null ? {} : { project: section }) },
           argv: ["add", kind, name, ...(section === null ? [] : [`--project=${section}`])] };
}

/** `global mode <mode>` for `managed` or `if-absent`. */
export function globalModeAct(mode: "managed" | "if-absent"): PageAct {
  return { path: "api/global-mode", body: { mode }, argv: ["global", "mode", mode] };
}

/** `global remove`: loadout's part out of each global file, the mode off. */
export function globalOffAct(job: string): PageAct {
  return { path: "api/global-remove", body: { job }, argv: ["global", "remove"] };
}

/** A fresh job id for one press: 32 hex characters from `bytes` (16 random bytes). */
export function jobId(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

/** One word as a shell takes it: as it is when it holds nothing a shell reads, else in
 * single quotes. */
export function shellWord(word: string): string {
  if (/^[A-Za-z0-9_@%+=:,./~-]+$/.test(word) && !word.startsWith("~")) return word;
  return `'${word.replaceAll("'", "'\\''")}'`;
}

/** The command an act runs, as he would type it: `moxi loadout <argv>`. */
export function commandOf(argv: readonly string[]): string {
  return ["moxi", "loadout", ...argv.map(shellWord)].join(" ");
}

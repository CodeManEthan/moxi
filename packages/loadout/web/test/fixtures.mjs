// A `/api/view` answer to build tests from: the full install the drawing shows, with
// helpers to change one row or item.

export const HOME = "/home/me";
export const FOLDER = `${HOME}/my-loadout`;
export const HARNESSES = ["claude", "codex", "opencode"];

export const USER = { name: "user", where: "user", root: null, refused: null };
export const ATLAS = { name: "atlas-api", where: "key", root: null, refused: null };

export const item = (over) => ({
  kind: "skill", name: "tidy-notes", scope: "user", harnesses: null,
  source: `${FOLDER}/skills/tidy-notes`, disabled: [], module: null, what: null, ...over,
});

const TARGETS = {
  claude: `${HOME}/.claude`, codex: `${HOME}/.codex`, opencode: `${HOME}/.config/opencode`,
};

export const row = (over) => {
  const base = {
    scope: "user", kind: "skill", name: "tidy-notes", harness: "claude", state: "ok", reason: "",
    target: null, replaceable: false, form: "link", edited: false, covers: null, prior: false,
    applies: "section", ignores: false, ...over,
  };
  if (!("target" in over) && base.harness in TARGETS && base.kind && base.name) {
    base.target = `${TARGETS[base.harness]}/${base.kind}s/${base.name}`;
  }
  return base;
};

/** Rows for one item on each harness, all `ok` unless `states` says otherwise. */
export const rowsFor = (kind, name, harnesses, states = {}, over = {}) =>
  harnesses.map((harness) => row({ kind, name, harness, state: states[harness] ?? "ok", ...over }));

export const globalFile = (over) => ({
  file: `${HOME}/.claude/CLAUDE.md`, harness: "claude", parts: [], changes: false, diff: "",
  placed: false, state: "ok", form: "line", ...over,
});

/** The full install: seven items in Your items, the global file managed, two items in
 * atlas-api. Everything placed unless a test changes it. */
export function fullView(over = {}) {
  const items = [
    item({ name: "tidy-notes" }),
    item({ name: "review-diff", harnesses: ["claude", "codex"] }),
    item({ name: "write-changelog", disabled: ["codex"] }),
    item({ name: "release-checklist" }),
    item({ kind: "agent", name: "code-reviewer", source: `${FOLDER}/agents/code-reviewer.md` }),
    item({ kind: "hook", name: "guard-rm", source: `${FOLDER}/loadout.toml`,
           what: { event: "PreToolUse", matcher: "Bash", command: `python3 ${FOLDER}/hooks/guard_rm.py`,
                   url: null, allow: null, ask: null, deny: null } }),
    item({ kind: "permissions", name: "git-reads", source: `${FOLDER}/loadout.toml` }),
    item({ name: "release-checklist", scope: "atlas-api" }),
    item({ kind: "agent", name: "api-lead", scope: "atlas-api", source: `${FOLDER}/agents/api-lead.md` }),
  ];
  const rows = [
    ...rowsFor("skill", "tidy-notes", HARNESSES),
    ...rowsFor("skill", "review-diff", ["claude", "codex"]),
    ...rowsFor("skill", "write-changelog", HARNESSES, { codex: "off" }),
    ...rowsFor("skill", "release-checklist", HARNESSES),
    row({ kind: "agent", name: "code-reviewer", harness: "claude" }),
    row({ kind: "agent", name: "code-reviewer", harness: "codex", form: "generated" }),
    row({ kind: "agent", name: "code-reviewer", harness: "opencode", form: "generated" }),
    row({ kind: "hook", name: "guard-rm", harness: "claude", form: "entry", target: `${HOME}/.claude/settings.json` }),
    row({ kind: "hook", name: "guard-rm", harness: "codex", form: "entry", target: `${HOME}/.codex/hooks.json` }),
    row({ kind: "permissions", name: "git-reads", harness: "claude", form: "entry",
          target: `${HOME}/.claude/settings.json` }),
    row({ kind: "permissions", name: "*", harness: "codex", form: "entry", covers: ["git-reads"],
          target: `${HOME}/.codex/rules/loadout.rules` }),
    row({ kind: "permissions", name: "git-reads", harness: "opencode", form: "entry",
          target: `${HOME}/.config/opencode/opencode.json` }),
    ...rowsFor("global", "global", HARNESSES, {}, { form: "entry", applies: "whole" }),
    ...rowsFor("skill", "release-checklist", HARNESSES, {}, { scope: "atlas-api" }),
    ...rowsFor("agent", "api-lead", HARNESSES, {}, { scope: "atlas-api" }),
  ];
  return {
    problem: null, lines: [], notes: [], counts: {}, note: null, home: HOME, folder: FOLDER,
    manifest: `${FOLDER}/loadout.toml`, harnesses: [...HARNESSES], resolved_all: true,
    sections: [USER, ATLAS], items, rows,
    item_notes: [{ kind: "hook", name: "guard-rm", harness: "codex", code: "trust" },
                 { kind: "hook", name: "guard-rm", harness: "opencode", code: "no-hooks" }],
    global: { source: `${FOLDER}/global.md`, mode: "managed", chosen: true,
              files: HARNESSES.map((harness) => globalFile({ harness })) },
    ...over,
  };
}

/** `view` with the row matching `match` changed by `over`. */
export function withRow(view, match, over) {
  let hit = false;
  const rows = view.rows.map((r) => {
    if (Object.entries(match).every(([key, value]) => r[key] === value)) {
      hit = true;
      return { ...r, ...over };
    }
    return r;
  });
  if (!hit) throw new Error(`no row matches ${JSON.stringify(match)}`);
  return { ...view, rows };
}

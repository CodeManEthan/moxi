"""The announcement of Moxi's loadout module, which moxi.modules reads.

loadout places the user's own skills, agent files, hooks, permission rules, MCP servers
and global instruction file for each harness (Claude Code, Codex and OpenCode), from one
folder the user keeps and its manifest, `loadout.toml`. Skills and agent files go through
the core's one placer, `moxi.place`. It offers no API to other modules.
"""

ANNOUNCES = 1
INTERFACES = frozenset()
API = None
#: The verb `moxi loadout`: install, status, add, remove, enable, disable, list.
CLI = "moxi_loadout.cli:main"
SUMMARY = 'Your own skills, agents and settings, placed for each harness: Claude Code, Codex and OpenCode.'
OPTIONAL = {
    "projects": "Name a project section by its project key, not only by its path.",
    "repo": "A commit to your loadout folder reinstalls it.",
    "ui": "The loadout page in the browser UI shell: items, states, the global file and "
          "installs.",
}
GATED = {}
#: CLAUDE_CONFIG_DIR, CODEX_HOME and XDG_CONFIG_HOME are the harnesses' own, read where
#: the harness homes are found.
INTERNAL_ENV = {
    "MOXI_LOADOUT_CALLER": "set to page-job by loadout's page jobs, so what the run says "
                           "is worded for the browser page",
}
#: The machine settings: the loadout folder, whether a commit to it reinstalls it (with
#: repo), backups kept, each harness's project folders.
SETTINGS = "settings.toml"
#: The module's whole loader over a config text: the schema, then the project folders.
CONFIG_CHECK = "moxi_loadout.settings:check_text"
#: On every install and upgrade: the example manifest into an empty folder, the opencode
#: harness row, every module's own skill placements taken over, the global mode asked
#: when the manifest has none (only at a terminal), and, with repo, the git hook pieces
#: registered in the folder's work tree. It places nothing.
SETUP = "moxi_loadout.setup:setup"
#: At `moxi uninstall loadout`, before anything is removed: everything loadout placed or
#: wrote taken out (harness entries, the global part, placements, ignore files, the
#: opencode row), module skills from a module's own yes handed back to it; or, when
#: something was changed since loadout wrote it, a refusal that removes nothing.
TEARDOWN = "moxi_loadout.teardown:teardown"
SEGMENT = None
#: loadout's page server, which the browser UI shell starts. Nothing here imports it.
PAGE = "moxi_loadout.page:PAGE"
#: The page, and the drift block the shell's home places: how many items aren't ok,
#: linking to the page.
COMPONENTS = {
    "page": {"script": "page.js", "title": "Loadout", "contract": 1, "place": "page",
             "group": "setup", "order": 10,
             "doc": "Your items for each harness, their states, the global file and installs"},
    "drift": {"script": "drift.js", "title": "Loadout drift", "contract": 1, "place": "block",
              "home": True, "doc": "How many of your items aren't ok, linking to the page"},
}
#: loadout's doctor rows: the manifest, each harness's items and entries not ok, the
#: global file, hook programs, the projects link, the git hook link to repo, Codex's
#: switches, modules gone.
DOCTOR = "moxi_loadout.doctor:rows"
#: The loadout folder is the user's data, often a git clone (0755), so it is not private
#: and never rebuilt. The ledger records what loadout placed and wrote. Generated agent
#: files are made again from their sources. Backups can hold MCP tokens, so they are
#: private; loadout prunes them to `backups.keep` per harness file.
STORES = {
    "source": {"root": "data", "path": "source", "kind": "dir", "private": False,
               "retention": "keep", "rebuildable": False, "lazy": True},
    "ledger": {"root": "state", "path": "ledger.json", "kind": "file", "private": True,
               "retention": "keep", "rebuildable": False, "lazy": True},
    "generated": {"root": "state", "path": "generated", "kind": "dir", "private": True,
                  "retention": "keep", "rebuildable": True, "lazy": True},
    "backups": {"root": "data", "path": "backups", "kind": "dir", "private": True,
                "retention": "keep", "rebuildable": False, "lazy": True},
}

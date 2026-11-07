"""The announcement of Moxi's repo module, which moxi.modules reads.

repo runs the git hook pieces other modules declare (in the extension group
`moxi.repo.git_hooks`) in the repos a user names, and keeps one shared committer.
"""

INTERFACES = frozenset({1})
API = "moxi_repo.api"
CLI = "moxi_repo.cli:main"
SUMMARY = "Git hooks run as declared pieces, and the one shared committer."
OPTIONAL = {
    "ui": "The git hooks page in the browser UI shell.",
    "projects": "Project keys: a project key works where a command takes a path, and "
                "listings show each repo's key. Without it repos are named by path.",
}
GATED = {}
INTERNAL_ENV = {
    "MOXI_REPO_COMMITTER": "set by the committer on its own commits, so a git hook piece "
                           "knows which spec is committing",
}
SETTINGS = "settings.toml"
# The schema can't tell a regular expression that won't compile; this loader can.
CONFIG_CHECK = "moxi_repo.settings:check_text"
# On every `moxi install repo`: refresh every indexed repo, so an install that moved the
# environment rewrites every entry, shim and launcher.
SETUP = "moxi_repo.install:setup"
# On `moxi uninstall repo`, before anything is removed: take out of every indexed repo what
# repo wrote there, and clear repo's doctor records.
TEARDOWN = "moxi_repo.install:teardown"
DOCTOR = "moxi_repo.checks:rows"
SEGMENT = None
#: repo's page server, which the browser UI shell starts. Nothing here imports it.
PAGE = "moxi_repo.page:PAGE"
#: What repo offers the browser UI shell: its panel, which reads and runs nothing.
COMPONENTS = {
    "page": {"script": "page.js", "title": "Git hooks", "contract": 1, "place": "page",
             "doc": "Each repo's git hook pieces, committer specs and open records"},
}
STORES = {
    "repos": {"root": "state", "path": "repos.json", "kind": "file", "private": True,
              "retention": "keep", "rebuildable": False, "lazy": True},
    "records": {"root": "state", "path": "records.json", "kind": "file", "private": True,
                "retention": "keep", "rebuildable": False, "lazy": True},
}

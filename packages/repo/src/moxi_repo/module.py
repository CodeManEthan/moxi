"""The announcement of Moxi's repo module, which moxi.modules reads.

repo runs the git hook pieces other modules declare (in the extension group
`moxi.repo.git_hooks`) in the repos a user names, and keeps one shared committer.
"""

INTERFACES = frozenset({1})
API = "moxi_repo.api"
CLI = "moxi_repo.cli:main"
SUMMARY = "Git hooks run as declared pieces, and the one shared committer."
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {
    "MOXI_REPO_COMMITTER": "set by the committer on its own commits, so a git hook piece "
                           "knows which spec is committing",
}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = None
STORES = {
    "repos": {"root": "state", "path": "repos.json", "kind": "file", "private": True,
              "retention": "keep", "rebuildable": False, "lazy": True},
    "records": {"root": "state", "path": "records.json", "kind": "file", "private": True,
                "retention": "keep", "rebuildable": False, "lazy": True},
}

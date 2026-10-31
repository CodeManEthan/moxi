"""Moxi's repo module: git hooks run as declared pieces, and the one shared committer.

The names below are exported lazily (a module `__getattr__`), so the git hook runner,
`python -m moxi_repo.run`, loads neither the committer nor `moxi.proc`. No exported name
is the name of a module in this package: importing a submodule binds it over a package
attribute of the same name, and `__getattr__` is asked only for a missing one.
"""

#: Each exported name -> the module it comes from.
EXPORTS = {
    "git": "moxi_repo.gitrun",
    "changes": "moxi_repo.gitrun",
    "literal_pathspec": "moxi_repo.gitrun",
    "Change": "moxi_repo.gitrun",
    "commit": "moxi_repo.committer",
    "commit_saved": "moxi_repo.committer",
    "save_spec": "moxi_repo.committer",
    "remove_spec": "moxi_repo.committer",
    "Spec": "moxi_repo.committer",
    "CommitResult": "moxi_repo.committer",
    "PrepareContext": "moxi_repo.committer",
    "Prepared": "moxi_repo.committer",
    "HookContext": "moxi_repo.pieces",
    "Outcome": "moxi_repo.pieces",
}

__all__ = sorted(EXPORTS)


def __getattr__(name):
    module = EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'moxi_repo' has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module), name)


def __dir__():
    return sorted({*globals(), *EXPORTS})

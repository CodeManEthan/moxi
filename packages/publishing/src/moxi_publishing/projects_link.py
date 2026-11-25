"""publishing's link with projects (contribution interface 3): the two questions
`moxi projects add` puts for a new project whose root is in a git repo, and the acts on
their answers.

- `clean_history`: keep the repo's history publishable? Suggested yes; a yes runs
  `clean-history enable` on it.
- `push_approval`: ask for approval before agents push to some branches? Suggested no;
  a yes puts `approval_branches`, which branches (no suggestion), and a non-empty answer
  runs `approval enable` with them. An empty one turns nothing on.

The `project_add.*` settings answer a question for the machine: `"yes"` or `"no"` is the
question's preset, so the add asks nothing and acts on it, in a terminal or not. A
`"yes"` for push approval with `project_add.approval_branches` empty leaves the branch
question open: a terminal add asks it, any other names the command. While publishing's
config can't be read, both questions are asked.

`questions` does local reads only: whether the root, or a folder above it, holds `.git`,
or the add's `--create` makes a git repo there. It never reads a repo's git config, so a
repo that already keeps clean history is asked again; the answer says so and changes
nothing.
"""

from __future__ import annotations

import os
import re

INTERFACE = 3

CLEAN = "clean_history"
APPROVAL = "push_approval"
BRANCHES = "approval_branches"
YES, NO = "yes", "no"

#: What separates the branch patterns in an answer.
_SEPARATORS = re.compile(r"[\s,]+")


def _in_git_repo(root: str, mint: object) -> bool:
    if mint == "git":
        return True
    here = os.path.abspath(root)
    while True:
        if os.path.lexists(os.path.join(here, ".git")):
            return True
        up = os.path.dirname(here)
        if up == here:
            return False
        here = up


def _arg(path: str) -> str:
    """`path` as one argument of a command line printed for the user to run."""
    if path and not re.search(r"[\s\"'$`\\]", path.replace(os.sep, "/")):
        return path
    return '"' + path.replace('"', '\\"') + '"'


def _settings() -> tuple[str, str, list[str]]:
    """The three `project_add.*` settings; asking for both while the config can't be
    read."""
    from moxi.config import ConfigError

    from moxi_publishing import settings

    try:
        values = settings.load()
    except ConfigError:
        return "ask", "ask", []
    branches = [str(b).strip() for b in values[settings.BRANCHES_AT_ADD]]
    return (values[settings.CLEAN_AT_ADD], values[settings.APPROVAL_AT_ADD],
            [b for b in branches if b])


def _preset(setting: str) -> str | None:
    return setting if setting in (YES, NO) else None


def questions(fields):
    """The questions for a project at `fields["root"]`, or None when it is in no git
    repo."""
    root = fields["root"]
    if not _in_git_repo(root, fields.get("mint")):
        return None
    clean, approval, branches = _settings()
    where = _arg(root)
    return [
        {"key": CLEAN,
         "text": "Keep this repo's history publishable (clean commit messages, no "
                 "private text)?",
         "choices": [YES, NO], "suggested": YES, "preset": _preset(clean),
         "later": f"moxi publishing clean-history enable {where}"},
        {"key": APPROVAL,
         "text": "Ask for approval before agents push to some branches?",
         "choices": [YES, NO], "suggested": NO, "preset": _preset(approval),
         "later": f"moxi publishing approval enable {where} --branch PATTERN"},
        {"key": BRANCHES,
         "text": "Which branches need approval (patterns such as main or release/*, "
                 "separated by spaces; empty for none)?",
         "after": [APPROVAL, YES],
         "preset": " ".join(branches) if approval == YES and branches else None,
         "later": f"moxi publishing approval enable {where} --branch PATTERN"},
    ]


def answer(project, key, value):
    """Act on `value` for question `key` about `project`, just added. Returns what it did;
    raises `moxi_publishing.flag.Failure` when the act fails."""
    from moxi_publishing import approval, clean, flag

    if key == CLEAN:
        if value != YES:
            return "clean history left off"
        root = flag.resolve(project.root)
        if flag.is_clean(root):
            return f"{root} already keeps a clean history"
        return clean.enable(project.root)[0]
    if key == BRANCHES:
        patterns = [p for p in _SEPARATORS.split(value.strip()) if p]
        if not patterns:
            return "push approval left off: no branch was named"
        return approval.enable(project.root, patterns)[-1]
    return None

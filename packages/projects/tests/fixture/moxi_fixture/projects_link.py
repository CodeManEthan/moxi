"""The fixture's link with projects: while a file named `hold` is in the fixture's state
directory, it refuses to remove any project; while one named `asks` is there, an add puts
two questions, and each answer is kept as a line of `answers.jsonl` beside it."""

import json

from moxi.paths import Layout

INTERFACE = 3


def _state():
    return Layout("fixture").state_dir()


def refuse_remove(project):
    if (_state() / "hold").exists():
        return f"the fixture holds {project.key}; let it go on the fixture's page"
    return None


def questions(fields):
    if not (_state() / "asks").exists():
        return None
    return [
        {"key": "tidy", "text": "Keep this folder tidy?", "choices": ["yes", "no"],
         "suggested": "yes", "later": "moxi fixture tidy " + fields["root"]},
        {"key": "where", "text": "Which shelf?", "after": ["tidy", "yes"],
         "later": "moxi fixture shelf NAME"},
    ]


def answer(project, key, value):
    with open(_state() / "answers.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps([project.key, key, value]) + "\n")
    return f"kept {key} = {value}"

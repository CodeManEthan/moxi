"""A recall's first search and its fork's brief.

**The query (`keywords`).** The prompt's words as memory's index cuts them, each kept
once, less the common words of `COMMON` and words of fewer than `MIN_CHARS` characters.
A prompt that leaves none runs no first search.

**The first search (`prefetch`).** It searches past sessions with `moxi_memory.api.search`
for the keywords and keeps the best `subconscious.prefetch_hits` hits, leaving out the
session itself (the fork holds its whole conversation) and subagent rows; and it reads
the notes: the files under the scope's notes directories whose text holds the most of
the keywords, at most `NOTES_SHOWN`, each with the lines around its matches.
`subconscious.scope` says where: `project` searches the session's project and its notes
directory; `everywhere` the whole index and the notes root; `project-first` the
project first, and the whole index and the notes root only when the project's sessions
and its notes both found nothing. A session with no project label searches everywhere.

**The brief (`brief`).** The fork's task, from `TEMPLATE`: the prompt or topic, the first
search's hits, where the fork may look further and within what limits, and how it
answers.

Standard library at import; the search is imported at use.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

#: Words a query leaves out.
COMMON = frozenset(
    "a an and are as at be but by can could do does for from had has have how i if in "
    "into is it its let's lets me my of on or our so that the their them then there "
    "these this those to up was we were what when where which who why will with would "
    "you your again last time now just put make use way want don't dont need still "
    "please should".split())
#: Words shorter than this are left out of a query.
MIN_CHARS = 3
#: The most notes files the first search shows.
NOTES_SHOWN = 3
#: The most notes files it reads, and the largest it reads, in bytes.
NOTES_READ_MAX = 5000
NOTES_FILE_MAX_BYTES = 1 << 20
#: The most matching lines it shows of one notes file, with one line around each.
NOTES_LINES = 3
#: How many characters of the prompt the answer's heading quotes.
PROMPT_HEAD_CHARS = 80
#: The scopes.
PROJECT = "project"
PROJECT_FIRST = "project-first"
EVERYWHERE = "everywhere"

NO_KEYWORD = "(no first search: the prompt holds no keyword to search for)"
NO_HITS = "(the first search found nothing)"
NO_SEARCH = "(no first search: subconscious.prefetch_hits is 0)"

TEMPLATE = """\
You are a recall fork of this session. You have its whole conversation so far. {start}
Your one job: find facts from past work that the main agent needs for this and does not
already have in this conversation, and hand them over.

{what}:
<<<
{prompt}
>>>
{recalled}A first search of past sessions and notes, already run for you:
<<<
{prefetch}
>>>

Answer from these hits if they hold what is needed. Only if they don't, you may look
further:
- Past sessions: moxi memory search "<a few keywords>"{scope_args}
  It matches any keyword, so search with the distinctive words, not a sentence; add
  --results to search tool output too. To read around a hit, run the moxi sessions read
  line printed under the hits. Skip any hit from session {session_id}: it is this
  conversation, which you already have.
{notes}{widen}
Limits: at most {max_tools} tool calls and about {limit_s} seconds in all. Only read:
change no file, send no message, start no agent, and run no command that waits or follows
a file. A shell call is one command, or several joined by |, each starting with one of:
{fork_commands}. No ;, &&, redirection, variable or cd. To read part of a file, use the
Read tool. Calls that do more are refused, and once your time is up every call is
refused: then answer at once from what you have.

Your answer is your last message, and the main agent reads it as it stands.
{first}{stream}
- If nothing you found is relevant, or the conversation already has it, answer with the
  single word NOTHING.
- Otherwise begin with the line: Recalled from past sessions and notes for "{prompt_head}":
  then at most {max_points} points, one or two lines each. Each point states the fact
  itself: the decision and its reason, the value, the path, the command, what failed and
  why. Write it so the main agent never needs to open the source.
- After a point you may add where it came from in brackets, for example
  [session 3f2a91c0, 2026-11-12] or [notes/handoffs/2026-11-18-build.md]. A citation
  never stands in for the fact.
- No preamble, no advice on how to answer, nothing the conversation already says.
"""

START_PROMPT = ("The user has just sent the prompt below, and the main agent is about to "
                "answer it while you work.")
START_ASKED = ("The main agent has asked for a recall on the topic below, and keeps "
               "working while you work.")
NOTES_LINE = ("- Notes: the files under {notes_dirs}. Search them with grep and read the "
              "files that match.\n")
FIRST = "Give a first answer as soon as you have something."
STREAM = (" After each answer you are asked to keep digging: then answer again each time "
          "you find something new, with only what is new since your last answer. End an "
          "answer with the line End of recall. when you have what is needed or nothing "
          "more is worth looking for, and you are not asked again.")
WIDEN_NOTES = ("These search this project only. Run a search wider only for keywords "
               "whose search of this project's sessions and whose grep of its notes have "
               "both found nothing: then search again without --project and grep "
               "{notes_root}.\n")
WIDEN = ("These search this project only. Run a search wider only for keywords whose "
         "search of this project found nothing: then search again without --project.\n")
RECALLED = ("Already recalled for the main agent, which has it: context only. Do not "
            "recall for it and do not repeat it.\n<<<\n{recalled_lines}\n>>>\n\n")


def keywords(text: str) -> list[str]:
    """The first search's keywords in `text`, in order, each once."""
    from .search import MAX_TOKENS, _token_char

    out: list[str] = []
    seen: set[str] = set()
    word: list[str] = []
    for ch in f"{text or ''} ":
        if _token_char(ch) or ch == "'":
            word.append(ch)
            continue
        token = "".join(word).strip("'")
        word.clear()
        folded = token.casefold()
        if (len(token) < MIN_CHARS or folded in COMMON or folded in seen):
            continue
        seen.add(folded)
        out.append(token)
        if len(out) >= MAX_TOKENS:
            break
    return out


@dataclass
class Prefetch:
    """What the first search found. `keywords`, the query; `hits`, the sessions' hits;
    `notes`, (path, lines) for each notes file shown; `widened`, whether it searched past
    the project; `project`, the project label it searched in, or None; `notes_dirs`, the
    notes directories it read; `notes_root`, the notes root when notes gives one;
    `error`, why the sessions could not be searched, or None."""

    keywords: list[str]
    hits: list = field(default_factory=list)
    notes: list[tuple[str, list[str]]] = field(default_factory=list)
    widened: bool = False
    project: str | None = None
    notes_dirs: list[str] = field(default_factory=list)
    notes_root: str | None = None
    error: str | None = None

    @property
    def count(self) -> int:
        return len(self.hits) + len(self.notes)


def _sessions(words: list[str], session_id: str, project: str | None, limit: int,
              search) -> list:
    if limit < 1:
        return []
    result = search(" ".join(words), project=project, limit=limit + 50)
    return [h for h in result.hits if h.session_id != session_id][:limit]


def _note_files(roots: list[str]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                files.append(Path(dirpath) / name)
                if len(files) >= NOTES_READ_MAX:
                    return files
    return files


def _notes(words: list[str], roots: list[str]) -> list[tuple[str, list[str]]]:
    folded = [w.casefold() for w in words]
    scored = []
    for path in _note_files(roots):
        try:
            if path.stat().st_size > NOTES_FILE_MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        low = text.casefold()
        score = sum(1 for w in folded if w in low)
        if score:
            scored.append((-score, str(path), text))
    scored.sort()
    out = []
    for _score, path, text in scored[:NOTES_SHOWN]:
        lines = text.splitlines()
        shown: list[int] = []
        for i, line in enumerate(lines):
            if any(w in line.casefold() for w in folded):
                shown.append(i)
                if len(shown) >= NOTES_LINES:
                    break
        keep = sorted({j for i in shown for j in (i - 1, i, i + 1) if 0 <= j < len(lines)})
        out.append((path, [f"{j + 1}: {lines[j]}" for j in keep]))
    return out


def notes_places(cwd: str | None, override: str | None) -> tuple[str | None, str | None]:
    """(the session's notes directory, the notes root), each None where notes gives
    none or is not installed."""
    from . import links

    link = links.notes()
    if not link.ready:
        return None, None
    project_dir = root = None
    try:
        if cwd and os.path.isabs(cwd):
            found = link.api.notes_dir(cwd, override=override).dir
            project_dir = str(found) if found is not None else None
    except Exception:  # noqa: BLE001 - notes that can't say give no directory
        project_dir = None
    try:
        root = str(link.api.root())
    except Exception:  # noqa: BLE001
        root = None
    return project_dir, root


def prefetch(text: str, *, session_id: str, cwd: str | None, notes_dir: str | None,
             scope: str, hits: int, search=None, places=None) -> Prefetch:
    """The first search for prompt or topic `text`. See the module's docstring."""
    from .labels import project_label

    words = keywords(text)
    project = None
    try:
        project = project_label(cwd) if cwd else None
    except (OSError, ValueError):
        project = None
    project_dir, root = (places or notes_places)(cwd, notes_dir)
    found = Prefetch(words, project=project, notes_root=root)
    if not words or hits < 1:
        return found
    if search is None:
        from .api import search
    wide = scope == EVERYWHERE or project is None
    if not wide:
        found.notes_dirs = [project_dir] if project_dir else []
        try:
            found.hits = _sessions(words, session_id, project, hits, search)
        except Exception as exc:  # noqa: BLE001 - a search that fails is said in the brief
            found.error = str(exc) or type(exc).__name__
        found.notes = _notes(words, found.notes_dirs)
        if found.count or found.error or scope == PROJECT:
            return found
        found.widened = True
    found.project = None
    found.notes_dirs = [root] if root else []
    try:
        found.hits = _sessions(words, session_id, None, hits, search)
    except Exception as exc:  # noqa: BLE001
        found.error = str(exc) or type(exc).__name__
    found.notes = _notes(words, found.notes_dirs)
    return found


def prefetch_text(found: Prefetch, hits: int) -> str:
    """The first search as the brief shows it."""
    if not found.keywords or hits < 1:
        return NO_KEYWORD if not found.keywords else NO_SEARCH
    from .cli import read_line
    from .clock import show

    lines: list[str] = []
    if found.error:
        lines.append(f"(past sessions could not be searched: {found.error})")
    for hit in found.hits:
        when = show(hit.time) if hit.time is not None else "-"
        lines.append(f"- session {hit.session_id} ({hit.harness}), {when}, project "
                     f"{hit.project or '-'}: {hit.snippet}")
        lines.append(f"  {read_line(hit)}")
    for path, note_lines in found.notes:
        lines.append(f"- notes {path}")
        lines += [f"  {line}" for line in note_lines]
    return "\n".join(lines) if lines else NO_HITS


def split_recalled(text: str, sent: frozenset[str] | set[str]) -> tuple[str, list[str]]:
    """`text` less its lines that, stripped, equal a line memory sent (`sent`), and
    those lines, stripped: memory's own earlier answers a prompt carries, context for
    the fork and never what it recalls for."""
    kept, recalled = [], []
    for line in str(text).splitlines():
        if line.strip() and line.strip() in sent:
            recalled.append(line.strip())
        else:
            kept.append(line)
    return "\n".join(kept), recalled


def brief(*, start: str, text: str, session_id: str, found: Prefetch,
          settings: Mapping[str, object], recalled: list[str] | None = None) -> str:
    """The fork's brief. See the module's docstring."""
    import shlex

    from .recalls import ASKED

    hits = int(settings["prefetch_hits"])
    scoped = found.project is not None
    scope_args = f" --project {shlex.quote(found.project)}" if scoped else ""
    notes_line = (NOTES_LINE.format(notes_dirs=", ".join(found.notes_dirs))
                  if found.notes_dirs else "")
    widen = ""
    if scoped and settings["scope"] == PROJECT_FIRST:
        widen = (WIDEN_NOTES.format(notes_root=found.notes_root)
                 if found.notes_dirs and found.notes_root else WIDEN)
    rounds = int(settings["max_rounds"])
    return TEMPLATE.format(
        start=START_ASKED if start == ASKED else START_PROMPT,
        what="The topic" if start == ASKED else "The prompt",
        prompt=text,
        recalled=(RECALLED.format(recalled_lines="\n".join(recalled)) if recalled else ""),
        prefetch=prefetch_text(found, hits),
        scope_args=scope_args,
        session_id=session_id,
        notes=notes_line,
        widen=widen,
        max_tools=settings["max_tools"],
        limit_s=settings["limit_s"],
        fork_commands=", ".join(settings["fork_commands"]),
        first=FIRST if rounds > 1 else "",
        stream=STREAM if rounds > 1 else "",
        prompt_head=text[:PROMPT_HEAD_CHARS],
        max_points=settings["max_points"])


__all__ = ["COMMON", "MIN_CHARS", "NOTES_SHOWN", "PROJECT", "PROJECT_FIRST", "EVERYWHERE",
           "NO_KEYWORD", "NO_HITS", "NO_SEARCH", "TEMPLATE", "keywords", "Prefetch", "notes_places",
           "prefetch", "prefetch_text", "split_recalled", "brief"]

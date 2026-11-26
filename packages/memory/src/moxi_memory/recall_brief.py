"""A recall's first search and its fork's brief.

**The query (`keywords`).** The prompt's words as memory's index cuts them, each kept
once, less the common words of `COMMON` and words of fewer than `MIN_CHARS` characters.
A prompt that leaves none runs no first search.

**The first search (`prefetch`).** It runs in parts (`Part`), each searching past
sessions with `moxi_memory.api.search` for the keywords and keeping the best
`subconscious.prefetch_hits` hits, leaving out the session itself (the fork holds its
whole conversation) and subagent rows, and reading the notes: the Markdown files under
the part's notes directories whose text holds the most of the keywords, at most
`NOTES_SHOWN` of each part, each with its first `NOTES_LINES` matching lines and one line
around each, every shown line cut at `NOTES_LINE_CHARS` characters. Every file under a
directory is read, however many there are, and only its score is kept; the files shown
are read again for their lines. Directories and files whose names start with a dot are
not read; a file larger than `NOTES_FILE_MAX_BYTES`, or one that isn't valid UTF-8, is
skipped; files that match alike are ordered by path. A notes directory that doesn't exist is none.

`subconscious.scope` says where. `project`: one part, the session's project and its
notes directory. `everywhere`: one part, the whole index and the notes root.
`project-first`: two parts, both run every time: the project's, then everything beyond
it, which is the whole index's best hits less the sessions the project's part showed and
the notes root's best files less the files it showed (a file known by
`os.path.normcase(os.path.realpath(path))`). A session with no project label has no
project part and searches everywhere under any scope.

**The brief (`brief`).** The fork's task, from `TEMPLATE`: the prompt or topic, the first
search's hits (the project's part first under its own heading when there are two), where
the fork may look further and within what limits, and how it answers. Under
`project-first` the fork's own searches run without `--project`, and the brief names the
project and its notes directory so the fork can tell the project's hits from the rest.

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
#: The most notes files each part of the first search shows.
NOTES_SHOWN = 3
#: The notes files it reads: Markdown files, up to this size in bytes.
NOTES_SUFFIX = ".md"
NOTES_FILE_MAX_BYTES = 1 << 20
#: How many characters of a notes line it shows.
NOTES_LINE_CHARS = 200
#: The most matching lines it shows of one notes file, with one line around each.
NOTES_LINES = 3
#: How many characters of the prompt the answer's heading quotes.
PROMPT_HEAD_CHARS = 80
#: The most hits one search of the first search asks for, while the hits it leaves out
#: (this session's, or sessions an earlier part showed) leave fewer than it keeps.
SEARCH_ASK_MAX = 2000
#: The scopes.
PROJECT = "project"
PROJECT_FIRST = "project-first"
EVERYWHERE = "everywhere"

NO_KEYWORD = "(no first search: the prompt holds no keyword to search for)"
NO_HITS = "(the first search found nothing)"
NO_PART_HITS = "(found nothing here)"
#: The parts of the first search.
PROJECT_PART = "project"
BEYOND_PART = "beyond"
PROJECT_HEADING = "In this project, {project}:"
BEYOND_HEADING = "Beyond this project:"
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
{notes}{project_line}
Limits: at most {max_tools} tool calls and about {limit_s} seconds in all. Only read:
change no file, send no message, start no agent, and run no command that waits or follows
a file. A shell call is one command, or several joined by |, each starting with one of:
{fork_commands}. No ;, & or &&, redirection, variable or cd, and no backslash, (, ), {{, }},
`, $ or ! anywhere, even inside quotes: for one word or another use grep -E "one|other",
since a | inside quotes is part of the pattern, spell a repeat out (xx, not x{{2}}), or
use the Grep tool, which takes any pattern. To read part of a file, use the Read tool. Calls that do more are refused, and once your
time is up every call is refused: then answer at once from what you have.

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
FIRST = ("Give a first answer as soon as you have one useful point: when the first search "
         "above already holds one, answer from it at once, before any tool call.")
STREAM = (" After each answer you are asked to keep digging: then answer again each time "
          "you find something new, with only what is new since your last answer. End an "
          "answer with the line End of recall. when you have what is needed or nothing "
          "more is worth looking for, and you are not asked again.")
PROJECT_LINE = ("These search past this project too. This project is {project}: its hits "
                "are this project's own record, and --project {project_arg} searches its "
                "sessions alone.")
PROJECT_NOTES = " Its own notes are under {project_notes}."
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
class Part:
    """One part of the first search: its name (`PROJECT_PART` or `BEYOND_PART`), the
    sessions' hits, (path, lines) for each notes file shown, and why the sessions could
    not be searched, or None."""

    name: str
    hits: list = field(default_factory=list)
    notes: list[tuple[str, list[str]]] = field(default_factory=list)
    error: str | None = None

    @property
    def count(self) -> int:
        return len(self.hits) + len(self.notes)


@dataclass
class Prefetch:
    """What the first search found. `keywords`, the query; `scope`, the scope it ran
    under (`everywhere` for a session with no project label); `parts`, its parts in the
    order the brief shows them; `project`, the session's project label, or None;
    `project_dir`, the project's notes directory when it exists; `notes_root`, the notes
    root when notes gives one; `notes_dirs`, the notes directories the brief names for
    the fork's own greps."""

    keywords: list[str]
    scope: str = EVERYWHERE
    parts: list[Part] = field(default_factory=list)
    project: str | None = None
    project_dir: str | None = None
    notes_root: str | None = None
    notes_dirs: list[str] = field(default_factory=list)

    def part(self, name: str) -> Part | None:
        return next((p for p in self.parts if p.name == name), None)

    @property
    def hits(self) -> list:
        return [hit for part in self.parts for hit in part.hits]

    @property
    def notes(self) -> list[tuple[str, list[str]]]:
        return [note for part in self.parts for note in part.notes]

    @property
    def count(self) -> int:
        return sum(part.count for part in self.parts)

    def counts(self) -> dict[str, int | None]:
        """Each part's hit count (sessions and notes files) for the log: the project's
        and the one beyond it, None for a part this search had none of."""
        out: dict[str, int | None] = {PROJECT_PART: None, BEYOND_PART: None}
        for part in self.parts:
            out[part.name] = part.count
        return out


def path_key(path: str | os.PathLike) -> str:
    """How the first search knows a notes file or directory: its resolved path,
    case-folded where the system's paths are."""
    return os.path.normcase(os.path.realpath(path))


def within(path: str, root: str) -> bool:
    """Whether `path` is `root` or lies under it, both known by `path_key`."""
    path, root = path_key(path), path_key(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives
        return False


def _sessions(words: list[str], session_id: str, project: str | None, limit: int,
              search, *, skip: frozenset[str] = frozenset()) -> list:
    """The best `limit` hits for `words`, none of this session's or of a session in
    `skip`; asks for more hits while the ones left out leave fewer than `limit`."""
    if limit < 1:
        return []
    asked = limit + 50
    while True:
        result = search(" ".join(words), project=project, limit=asked)
        kept = [h for h in result.hits
                if h.session_id != session_id and h.session_id not in skip][:limit]
        if len(kept) >= limit or len(result.hits) < asked or asked >= SEARCH_ASK_MAX:
            return kept
        asked = min(asked * 4, SEARCH_ASK_MAX)


def _note_files(roots: list[str]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if name.startswith(".") or not name.lower().endswith(NOTES_SUFFIX):
                    continue
                files.append(Path(dirpath) / name)
    return files


def _notes(words: list[str], roots: list[str], *,
           skip: frozenset[str] = frozenset()) -> list[tuple[str, list[str]]]:
    folded = [w.casefold() for w in words]
    scored = []
    seen: set[str] = set(skip)
    for path in _note_files(roots):
        key = path_key(path)
        if key in seen:
            continue
        seen.add(key)
        text = _note_text(path)
        if text is None:
            continue
        low = text.casefold()
        score = sum(1 for w in folded if w in low)
        if score:
            scored.append((-score, str(path)))
    scored.sort()
    out = []
    for _score, path in scored:
        text = _note_text(Path(path))
        if text is None:
            continue
        lines = text.splitlines()
        shown: list[int] = []
        for i, line in enumerate(lines):
            if any(w in line.casefold() for w in folded):
                shown.append(i)
                if len(shown) >= NOTES_LINES:
                    break
        keep = sorted({j for i in shown for j in (i - 1, i, i + 1) if 0 <= j < len(lines)})
        out.append((path, [f"{j + 1}: {lines[j][:NOTES_LINE_CHARS]}" for j in keep]))
        if len(out) >= NOTES_SHOWN:
            break
    return out


def _note_text(path: Path) -> str | None:
    """Notes file `path`'s text, or None when it is past `NOTES_FILE_MAX_BYTES`, can't
    be read or isn't UTF-8."""
    try:
        if path.stat().st_size > NOTES_FILE_MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def notes_places(cwd: str | None, override: str | None) -> tuple[str | None, str | None]:
    """(the session's notes directory, the notes root), each None where notes gives
    none, where it does not exist, or where notes is not installed."""
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
    return _existing(project_dir), _existing(root)


def _existing(path: str | None) -> str | None:
    return path if path and os.path.isdir(path) else None


def _run_part(part: Part, words: list[str], session_id: str, project: str | None,
              hits: int, search, notes_dirs: list[str], *,
              skip_sessions: frozenset[str] = frozenset(),
              skip_notes: frozenset[str] = frozenset()) -> Part:
    try:
        part.hits = _sessions(words, session_id, project, hits, search, skip=skip_sessions)
    except Exception as exc:  # noqa: BLE001 - a search that fails is said in the brief
        part.error = str(exc) or type(exc).__name__
    part.notes = _notes(words, notes_dirs, skip=skip_notes)
    return part


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
    project_dir, root = _existing(project_dir), _existing(root)
    if project is None:
        scope = EVERYWHERE
    found = Prefetch(words, scope=scope, project=project, project_dir=project_dir,
                     notes_root=root)
    if scope == PROJECT:
        found.notes_dirs = [project_dir] if project_dir else []
    else:
        found.notes_dirs = [root] if root else []
        if project_dir and not (root and within(project_dir, root)):
            found.notes_dirs.append(project_dir)
    if not words or hits < 1:
        return found
    if search is None:
        from .api import search
    if scope == PROJECT:
        found.parts = [_run_part(Part(PROJECT_PART), words, session_id, project, hits,
                                 search, found.notes_dirs)]
    elif scope == EVERYWHERE:
        found.parts = [_run_part(Part(BEYOND_PART), words, session_id, None, hits, search,
                                 found.notes_dirs)]
    else:
        own = _run_part(Part(PROJECT_PART), words, session_id, project, hits, search,
                        [project_dir] if project_dir else [])
        beyond = _run_part(Part(BEYOND_PART), words, session_id, None, hits, search,
                           [root] if root else [],
                           skip_sessions=frozenset(h.session_id for h in own.hits),
                           skip_notes=frozenset(path_key(p) for p, _ in own.notes))
        found.parts = [own, beyond]
    return found


def _part_lines(part: Part) -> list[str]:
    from .cli import read_line
    from .clock import show

    lines: list[str] = []
    if part.error:
        lines.append(f"(past sessions could not be searched: {part.error})")
    for hit in part.hits:
        when = show(hit.time) if hit.time is not None else "-"
        lines.append(f"- session {hit.session_id} ({hit.harness}), {when}, project "
                     f"{hit.project or '-'}: {hit.snippet}")
        lines.append(f"  {read_line(hit)}")
    for path, note_lines in part.notes:
        lines.append(f"- notes {path}")
        lines += [f"  {line}" for line in note_lines]
    return lines


def prefetch_text(found: Prefetch, hits: int) -> str:
    """The first search as the brief shows it: with two parts, each under its heading,
    the project's first, a part that found nothing saying so."""
    if not found.keywords or hits < 1:
        return NO_KEYWORD if not found.keywords else NO_SEARCH
    if len(found.parts) < 2:
        lines = [line for part in found.parts for line in _part_lines(part)]
        return "\n".join(lines) if lines else NO_HITS
    out: list[str] = []
    for part in found.parts:
        heading = (PROJECT_HEADING.format(project=found.project)
                   if part.name == PROJECT_PART else BEYOND_HEADING)
        out.append(heading)
        out += _part_lines(part) or [NO_PART_HITS]
    return "\n".join(out)


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
    project = found.project if found.scope != EVERYWHERE else None
    scope_args = (f" --project {shlex.quote(project)}"
                  if project is not None and found.scope == PROJECT else "")
    notes_line = (NOTES_LINE.format(notes_dirs=", ".join(found.notes_dirs))
                  if found.notes_dirs else "")
    project_line = ""
    if project is not None and found.scope == PROJECT_FIRST:
        project_line = PROJECT_LINE.format(project=project,
                                           project_arg=shlex.quote(project))
        if found.project_dir:
            project_line += PROJECT_NOTES.format(project_notes=found.project_dir)
        project_line += "\n"
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
        project_line=project_line,
        max_tools=settings["max_tools"],
        limit_s=settings["limit_s"],
        fork_commands=", ".join(settings["fork_commands"]),
        first=FIRST if rounds > 1 else "",
        stream=STREAM if rounds > 1 else "",
        prompt_head=text[:PROMPT_HEAD_CHARS],
        max_points=settings["max_points"])


__all__ = ["COMMON", "MIN_CHARS", "NOTES_SHOWN", "NOTES_SUFFIX", "NOTES_LINE_CHARS",
           "PROJECT", "PROJECT_FIRST", "EVERYWHERE", "PROJECT_PART", "BEYOND_PART",
           "NO_KEYWORD", "NO_HITS", "NO_SEARCH", "NO_PART_HITS", "TEMPLATE", "keywords",
           "Part", "Prefetch", "path_key", "within", "notes_places", "prefetch",
           "prefetch_text", "split_recalled", "brief"]

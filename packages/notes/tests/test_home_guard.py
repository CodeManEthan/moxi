"""The home-paths guard: a write under the notes root that spells the home folder or the
login gets one line after it; nothing else does, and it never refuses."""

from __future__ import annotations

import importlib
import io

import moxi.config
import pytest
from moxi_turns import Allow, ToolCall

from moxi_notes import homeguard, setup
from moxi_notes.guards import GUARDS

from gate_kit import allow_line, notes_repo, private_names
from notes_kit import configure, git_env, home

HOME = "/home/quillon"
LOGIN = "quillon"
ID = "0123abcd-1111-2222-3333-444455556666"
LINE = Allow(homeguard.LINE)


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)


def root():
    path = home() / "notes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write(text: str, *, path=None, tool: str = "Write", harness: str = "claude",
          home_dir: str = HOME, cwd=None, agent: str | None = None) -> object:
    target = str(root() / "a.md") if path is None else str(path)
    fields = {("claude", "Write"): ("file_path", "content"),
              ("claude", "Edit"): ("file_path", "new_string"),
              ("opencode", "write"): ("filePath", "content"),
              ("opencode", "edit"): ("filePath", "newString")}[(harness, tool)]
    call = ToolCall(harness=harness, session_id="s1", agent_id=agent, tool=tool,
                    input={fields[0]: target, fields[1]: text}, cwd=cwd or str(home()),
                    env={"HOME": home_dir})
    return homeguard.judge(call)


def patch(text: str, *, cwd: str) -> object:
    call = ToolCall(harness="codex", session_id="s1", agent_id=None, tool="apply_patch",
                    input={"command": text}, cwd=cwd, env={"HOME": HOME})
    return homeguard.judge(call)


# -- what it tells -------------------------------------------------------------------------

def test_a_write_under_the_root_spelling_home_gets_the_line():
    assert write(f"see {HOME}/x\n") == LINE


def test_a_session_project_name_or_a_bare_login_gets_the_line():
    assert write(f"see -home-{LOGIN}-x/{ID}\n") == LINE
    assert write(f"ask {LOGIN} about it\n") == LINE
    assert write(f"ask {LOGIN.upper()} about it\n") == LINE


def test_every_covered_tool_main_loop_and_subagent():
    assert write(f"{HOME}/x", tool="Edit") == LINE
    assert write(f"{HOME}/x", tool="write", harness="opencode") == LINE
    assert write(f"{HOME}/x", tool="edit", harness="opencode") == LINE
    assert write(f"{HOME}/x", agent="agent-1") == LINE


def test_outside_the_root_or_with_neither_it_says_nothing():
    assert write(f"see {HOME}/x\n", path=home() / "elsewhere.md") is None
    assert write("see ~/x and session " + ID + "\n") is None


def test_a_short_login_turns_only_the_login_check_off():
    assert write("qu was here\n", home_dir="/home/qu") is None
    assert write("see /home/qu/x\n", home_dir="/home/qu") == LINE


def test_a_match_glued_to_a_letter_or_digit_gets_nothing():
    assert write(f"{LOGIN}2 and x{LOGIN} and {LOGIN}é\n") is None
    assert write(f"{HOME}2/x\n") is None


def test_it_tells_and_never_refuses():
    found = write(f"{HOME}/x")
    assert isinstance(found, Allow) and found.line == homeguard.LINE


def test_a_private_allowance_for_the_login_does_not_quiet_it():
    repo_root = notes_repo()
    private_names(LOGIN)
    (repo_root / ".git" / "secrets-allow-private").write_text(
        allow_line("*", "private-names/private-name", f"text:{LOGIN}"), encoding="utf-8")
    assert write(f"ask {LOGIN} about it\n", path=repo_root / "b.md") == LINE


# -- Rule PF -------------------------------------------------------------------------------

def pointers() -> str:
    return f"see ~/notes/{LOGIN}/x and {LOGIN}/core/y\n"


def test_pointers_into_a_real_login_dir_with_no_prefix_get_nothing():
    (root() / LOGIN / "core").mkdir(parents=True)
    assert write(pointers()) is None


def test_pointers_with_the_prefix_set_to_the_login_get_nothing():
    (root() / LOGIN / "core").mkdir(parents=True)
    configure(prefix=LOGIN)
    assert write(pointers()) is None


def test_pointers_into_a_linked_login_dir_under_another_prefix_get_the_line():
    (root() / "home" / "core").mkdir(parents=True)
    (root() / LOGIN).symlink_to("home")
    configure(prefix="home")
    assert write(pointers()) == LINE


def test_a_login_slash_name_that_is_no_entry_gets_the_line():
    (root() / LOGIN / "core").mkdir(parents=True)
    assert write(f"clone {LOGIN}/repo\n") == LINE


# -- apply_patch ---------------------------------------------------------------------------

def test_patch_hunks_outside_the_root_are_ignored_and_relative_paths_join_cwd():
    outside = (f"*** Begin Patch\n*** Add File: {home()}/elsewhere.md\n+{HOME}/x\n"
               "*** End Patch\n")
    assert patch(outside, cwd=str(root())) is None
    inside = (f"*** Begin Patch\n*** Update File: sub/a.md\n@@\n-old\n+{HOME}/x\n"
              "*** End Patch\n")
    assert patch(inside, cwd=str(root())) == LINE
    assert patch(inside, cwd=str(home())) is None
    removed_only = (f"*** Begin Patch\n*** Update File: a.md\n@@\n-{HOME}/x\n+~/x\n"
                    "*** End Patch\n")
    assert patch(removed_only, cwd=str(root())) is None


# -- the guard as turns loads it ------------------------------------------------------------

def test_the_guard_value_and_its_switch():
    from moxi.units import switch_problem

    # turns' guard loader, which turns' own endpoint runs (as sessions' and usage's tests reach it)
    read_switch = importlib.import_module("moxi_turns.guards").read_switch

    from moxi_notes import settings

    (guard,) = GUARDS
    assert guard.problems() == []
    assert guard.name == "home-paths" and guard.switch == "guard.home_paths"
    assert guard.judge == "moxi_notes.homeguard:judge" and guard.on_error == "allow"
    assert switch_problem(settings.schema(), "guard.home_paths") is None
    assert read_switch("notes", "guard.home_paths") == (False, None)
    configure(guard__home_paths=True)
    assert read_switch("notes", "guard.home_paths") == (True, None)


def test_a_config_that_will_not_load_fails_open(monkeypatch):
    from moxi_notes import settings

    def broken():
        raise moxi.config.ConfigError(None, "root", "is broken", "fix it")

    monkeypatch.setattr(settings, "read", broken)
    assert write(f"{HOME}/x") is None


# -- setup's question ----------------------------------------------------------------------

class Keyboard:
    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.asked: list[str] = []

    def __call__(self, question: str) -> str:
        self.asked.append(question)
        return self.answers.pop(0) if self.answers else ""


def setting(key: str):
    from moxi_notes import settings

    return moxi.config.load("notes", settings.schema())[key]


def guard_questions(keys: Keyboard) -> list[str]:
    return [q for q in keys.asked if q.startswith("Tell an agent")]


def test_setup_asks_with_yes_as_the_default_and_enter_turns_it_on():
    notes_repo()
    keys = Keyboard()
    out = io.StringIO()
    assert setup.setup(ask=keys, out=out) == 0, out.getvalue()
    (question,) = guard_questions(keys)
    assert question.endswith("[Y/n] ")
    assert "With it off, nothing warns when such a note is written." in question
    assert setting("guard.home_paths") is True


def test_a_no_says_nothing_warns_and_is_the_next_default():
    notes_repo()
    keys = Keyboard("", "", "n")
    out = io.StringIO()
    assert setup.setup(ask=keys, out=out) == 0, out.getvalue()
    assert setting("guard.home_paths") is False
    assert setup.GUARD_OFF in out.getvalue()
    assert "nothing warns" in setup.GUARD_OFF
    keys = Keyboard()
    setup.setup(ask=keys, out=io.StringIO())
    assert guard_questions(keys)[0].endswith("[y/N] ")
    assert setting("guard.home_paths") is False


def test_with_nobody_at_the_keyboard_it_stays_off_and_says_nothing_warns():
    notes_repo()
    out = io.StringIO()
    assert setup.setup(ask=None, out=out) == 0
    assert setting("guard.home_paths") is False
    assert setup.GUARD_OFF in out.getvalue()

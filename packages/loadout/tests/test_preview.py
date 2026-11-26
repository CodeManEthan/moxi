"""What a Replace of one row would do, read before it runs: its case, its diff from the
code the write uses (equal to what the write then makes), where the copy goes, the other
changes the run makes, the bounds on what it reads, and that it writes nothing."""

from __future__ import annotations

import difflib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import loadout_kit as kit
import pytest
from test_displaced import EDITS, generated, lo, opencode_row, settings  # noqa: F401
from test_entries import HOOK
from test_would_commit import git, work_tree

from moxi_loadout import displaced, harness, install, preview
from moxi_loadout.planner import Scope

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="the fake harness CLIs are POSIX scripts")


def write(loadout_dir: Path, text: str) -> None:
    kit.manifest(loadout_dir, textwrap.dedent(text))


def unified(before: bytes | str, after: bytes | str, a_name, b_name=None) -> str:
    """A diff as the preview writes it."""
    def text(value):
        return value.decode("utf-8") if isinstance(value, bytes) else value
    lines = difflib.unified_diff(text(before).splitlines(), text(after).splitlines(),
                                 str(a_name), str(b_name or a_name), lineterm="")
    return "".join(line + "\n" for line in lines)


def snapshot(root: Path) -> dict[str, bytes | str]:
    """Every file and link under `root`, by path: its bytes, or where a link points."""
    out: dict[str, bytes | str] = {}
    for top, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(top, name)
            if os.path.islink(path):
                out[path] = os.readlink(path)
            elif os.path.isfile(path):
                out[path] = Path(path).read_bytes()
            else:
                out[path] = "dir"
    return out


# -- keeps: a generated file -------------------------------------------------------------

@pytest.mark.parametrize("edit", ["edited", "header-less"])
def test_a_generated_file_not_loadouts_keeps_and_its_diff_is_what_replace_writes(lo, edit):
    write(lo, '[user]\nagents = ["helper"]\n')
    assert install.install().code == 0
    if edit == "edited":
        with open(generated(), "a") as fh:
            fh.write("# mine\n")
    else:
        generated().write_text("name = 'mine'\n")
    before = generated().read_bytes()
    shown = preview.replace_preview("agent:helper@codex", "user")
    assert (shown["case"], shown["label"], shown["target"]) == ("keeps", "file",
                                                                str(generated()))
    assert shown["kept_in"] == str(displaced.root())
    assert (shown["others"], shown["skipped"]) == (0, [])
    assert install.install(Scope(user=True), replace=["agent:helper@codex"]).code == 0
    assert shown["diff"] == unified(before, generated().read_bytes(), generated())
    assert shown["lines"] == len(shown["diff"].splitlines()) > 0


@pytest.mark.parametrize("how", ["a folder", "unreadable", "a link"])
def test_a_generated_file_the_act_cant_read_has_no_case(lo, tmp_path, how):
    write(lo, '[user]\nagents = ["helper"]\n')
    assert install.install().code == 0
    path = generated()
    path.unlink()
    if how == "a folder":
        path.mkdir()
    elif how == "unreadable":
        if os.geteuid() == 0:
            pytest.skip("root reads a file whatever its mode")
        path.write_text("name = 'mine'\n")
        path.chmod(0)
    else:
        mine = tmp_path / "mine.toml"
        mine.write_text("name = 'mine'\n")
        path.symlink_to(mine)
    try:
        shown = preview.replace_preview("agent:helper@codex", "user")
        assert (shown["case"], shown["diff"]) == (None, "")
        # The act then refuses it, keeping nothing.
        out = install.install(Scope(user=True), replace=["agent:helper@codex"])
        assert out.code == 3 and out.kept == []
    finally:
        if how == "unreadable":
            path.chmod(0o600)


def test_a_global_text_past_the_bound_is_named_large_and_not_read(lo, monkeypatch):
    from moxi_loadout import globalfile

    (lo / "global.claude.md").write_text("Claude only.\n")
    write(lo, '[global]\nmode = "if-absent"\n')
    assert install.install().code == 0
    with open(harness.global_file("claude"), "a") as fh:
        fh.write("My own line.\n")
    shared = lo / "global.md"
    shared.write_text("x" * (preview.FILE_MAX_BYTES + 1))
    reads = []
    real = globalfile.read
    monkeypatch.setattr(globalfile, "read", lambda path, *a, **kw: reads.append(str(path))
                        or real(path, *a, **kw))
    install.status_plan()
    planned = reads.count(str(shared))
    reads.clear()
    shown = preview.replace_preview("global:global@claude")
    assert shown["case"] == "keeps"
    assert {"path": str(shared), "why": "large"} in shown["skipped"]
    # The preview reads it no more than the plan it builds on does.
    assert reads.count(str(shared)) == planned


# -- keeps: an entry -----------------------------------------------------------------------

@pytest.mark.parametrize("kind", list(EDITS))
def test_an_edited_entry_keeps_and_its_diff_is_the_bytes_the_write_makes(lo, kind):
    opencode_row()
    text, edit = EDITS[kind]
    write(lo, text)
    assert install.install().code == 0
    path, ident = edit()
    before = path.read_bytes()
    whole = ident.startswith("global:")
    shown = preview.replace_preview(ident, "user")
    assert (shown["case"], shown["label"], shown["target"]) == ("keeps", "file", str(path))
    scope = Scope() if whole else Scope(user=True)
    out = install.install(scope, replace=[ident])
    assert out.code == 0, out.text()
    assert shown["diff"] == unified(before, path.read_bytes(), path)
    assert shown["diff"]


def test_an_entry_loadout_did_not_write_keeps(lo):
    opencode_row()
    path = harness.settings_file("opencode")
    path.parent.mkdir(parents=True)
    path.write_text('{"mcp": {"docs": {"type": "local", "command": ["mine"]}}}')
    before = path.read_bytes()
    write(lo, EDITS["JSON mcp server"][0])
    shown = preview.replace_preview("mcp:docs@opencode", "user")
    assert shown["case"] == "keeps"
    assert install.install(Scope(user=True), replace=["mcp:docs@opencode"]).code == 0
    assert shown["diff"] == unified(before, path.read_bytes(), path)


def test_a_cli_written_server_shows_its_entry_as_found_and_as_sent(lo):
    write(lo, '[user]\nmcp = ["docs"]\n[mcp.docs]\ncommand = "uvx"\nharnesses = ["claude"]\n')
    assert install.install().code == 0
    path = harness.claude_json()
    data = json.loads(path.read_text())
    data["mcpServers"]["docs"]["command"] = "mine"
    path.write_text(json.dumps(data))
    shown = preview.replace_preview("mcp:docs@claude", "user")
    assert (shown["case"], shown["label"], shown["target"]) == ("keeps", "entry", str(path))
    assert "-  \"command\": \"mine\"" in shown["diff"]
    assert "+  \"command\": \"uvx\"" in shown["diff"]
    assert str(path) in shown["diff"].splitlines()[0]


# -- moves --------------------------------------------------------------------------------

def test_an_occupant_moves_and_its_diff_is_each_file_against_its_source(lo):
    write(lo, '[user]\nskills = ["review"]\n')
    kit.claude().mkdir(parents=True)
    target = kit.claude() / "review"
    kit.skill(kit.claude(), "review", "mine\n")
    (target / "notes.md").write_text("only mine\n")
    shown = preview.replace_preview("skill:review@claude", "user")
    assert (shown["case"], shown["target"]) == ("moves", str(target))
    source = lo / "skills" / "review"
    assert shown["diff"] == (
        unified((target / "SKILL.md").read_text(), (source / "SKILL.md").read_text(),
                target / "SKILL.md", source / "SKILL.md")
        + unified("only mine\n", "", target / "notes.md", source / "notes.md"))


def test_a_file_saved_over_the_link_moves_against_its_source(lo):
    write(lo, '[user]\nskills = ["review"]\n')
    assert install.install().code == 0
    target = kit.claude() / "review"
    target.unlink()
    kit.skill(kit.claude(), "review", "saved over\n")
    shown = preview.replace_preview("skill:review@claude", "user")
    assert shown["case"] == "moves"
    assert "-saved over" in shown["diff"] and "+Do the thing." in shown["diff"]


def test_an_edited_copy_moves(lo):
    from moxi import config

    config.set_value("moxi", "skills.form", "copy", by="test")
    write(lo, '[user]\nskills = ["review"]\n')
    assert install.install().code == 0
    target = kit.claude() / "review"
    (target / "SKILL.md").write_text("mine\n")
    shown = preview.replace_preview("skill:review@claude", "user")
    assert shown["case"] == "moves"
    assert "-mine" in shown["diff"]


@pytest.mark.slow
def test_a_tracked_occupant_has_no_replace_to_preview(lo, tmp_path):
    root = work_tree(tmp_path / "app")
    write(lo, f'[user]\nskills = ["other"]\n[project."{root}"]\nskills = ["review", "more"]\n'
              f'[skill.review]\nharnesses = ["codex"]\n[skill.more]\nharnesses = ["codex"]\n'
              f'[skill.other]\nharnesses = ["codex"]\n')
    kit.skill(lo / "skills", "more")
    kit.skill(lo / "skills", "other")
    skill_dir = root / ".agents" / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("theirs\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "a skill")
    shown = preview.replace_preview("skill:review@codex", str(root))
    assert (shown["case"], shown["target"]) == (None, None)


# -- the scope and the other changes ------------------------------------------------------

def test_others_counts_the_changes_waiting_in_the_acts_scope(lo, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    kit.skill(lo / "skills", "lint")
    write(lo, f'[user]\nagents = ["helper"]\n[project."{proj}"]\nskills = ["lint"]\n')
    assert install.install().code == 0
    with open(generated(), "a") as fh:
        fh.write("# mine\n")
    write(lo, f'[user]\nagents = ["helper"]\nskills = ["lint"]\n[project."{proj}"]\n'
              'skills = ["lint"]\n[skill.lint]\nharnesses = ["claude"]\n')
    (proj / ".claude" / "skills" / "lint").unlink()
    shown = preview.replace_preview("agent:helper@codex", "user")
    assert shown["case"] == "keeps"
    assert shown["others"] == 1   # the user's lint for claude; the project's is not counted


def test_the_global_part_is_worked_out_under_a_whole_run(lo):
    opencode_row()
    write(lo, EDITS["managed global part"][0] + '[user]\nhooks = ["guard-rm"]\n' + HOOK)
    assert install.install().code == 0
    path, ident = EDITS["managed global part"][1]()
    data = json.loads(settings().read_text())
    data["hooks"] = {}
    settings().write_text(json.dumps(data))   # the hook is missing again: a whole run makes it
    shown = preview.replace_preview(ident)
    assert shown["case"] == "keeps" and shown["others"] == 1


def test_a_row_that_is_not_replaceable_has_no_case(lo):
    write(lo, '[user]\nskills = ["review"]\n')
    assert install.install().code == 0
    for text, section in (("skill:review@claude", "user"), ("skill:nothing@claude", "user"),
                          ("skill:review@claude", "elsewhere")):
        shown = preview.replace_preview(text, section)
        assert (shown["case"], shown["target"], shown["diff"], shown["lines"]) == (
            None, None, "", 0)
        assert shown["kept_in"] == str(displaced.root())


# -- the bounds ---------------------------------------------------------------------------

@pytest.fixture
def occupant(lo):
    """A skill folder in the way of the user's `review` for claude."""
    write(lo, '[user]\nskills = ["review"]\n')
    kit.claude().mkdir(parents=True)
    return kit.skill(kit.claude(), "review", "mine\n")


def test_a_link_inside_the_occupant_is_named_not_followed(occupant, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("never read\n")
    (occupant / "link.txt").symlink_to(secret)
    shown = preview.replace_preview("skill:review@claude", "user")
    assert {"path": str(occupant / "link.txt"), "why": "link"} in shown["skipped"]
    assert "never read" not in shown["diff"]


def test_an_occupant_that_is_a_link_is_named_not_followed(lo, tmp_path):
    write(lo, '[user]\nskills = ["review"]\n')
    kit.claude().mkdir(parents=True)
    elsewhere = kit.skill(tmp_path / "elsewhere", "review", "never read\n")
    (kit.claude() / "review").symlink_to(elsewhere)
    shown = preview.replace_preview("skill:review@claude", "user")
    assert shown["case"] == "moves"
    assert shown["skipped"] == [{"path": str(kit.claude() / "review"), "why": "link"}]
    assert "never read" not in shown["diff"]


def test_a_large_file_and_a_binary_one_are_named_with_no_lines(occupant):
    (occupant / "big.md").write_text("x\n" * (preview.FILE_MAX_BYTES // 2 + 1))
    (occupant / "blob.bin").write_bytes(b"\xff\xfe\x00")
    shown = preview.replace_preview("skill:review@claude", "user")
    assert {"path": str(occupant / "big.md"), "why": "large"} in shown["skipped"]
    assert {"path": str(occupant / "blob.bin"), "why": "binary"} in shown["skipped"]
    assert "big.md" not in shown["diff"] and "blob.bin" not in shown["diff"]
    assert "-mine" in shown["diff"]


def test_a_tree_past_its_files_is_named_many(occupant):
    for i in range(preview.TREE_MAX_FILES + 5):
        (occupant / f"f{i:03}.md").write_text(f"{i}\n")
    shown = preview.replace_preview("skill:review@claude", "user")
    assert {"path": str(occupant), "why": "many"} in shown["skipped"]
    assert shown["lines"] > 0


def test_past_the_total_a_file_is_named_large(occupant):
    size = preview.FILE_MAX_BYTES - 1024
    for i in range(preview.TOTAL_MAX_BYTES // size + 2):
        (occupant / f"part{i}.md").write_text("y" * size)
    shown = preview.replace_preview("skill:review@claude", "user")
    assert any(s["why"] == "large" for s in shown["skipped"])


# -- writes nothing -----------------------------------------------------------------------

def test_a_preview_writes_nothing(lo, tmp_path):
    opencode_row()
    write(lo, '[user]\nagents = ["helper"]\nskills = ["review"]\nhooks = ["guard-rm"]\n' + HOOK)
    assert install.install().code == 0
    with open(generated(), "a") as fh:
        fh.write("# mine\n")
    EDITS["hook"][1]()
    (kit.claude() / "review").unlink()
    kit.skill(kit.claude(), "review", "saved over\n")
    before = snapshot(tmp_path)
    for text in ("agent:helper@codex", "hook:guard-rm@claude", "skill:review@claude"):
        assert preview.replace_preview(text, "user")["case"] is not None
    assert snapshot(tmp_path) == before


# -- the page server's budget -------------------------------------------------------------

RSS_PROBE = r'''
from moxi_loadout import page, preview
shown = preview.replace_preview("skill:review@claude", "user")
assert shown["case"] == "moves", shown
assert {s["why"] for s in shown["skipped"]} >= {"large", "many", "binary", "link"}, shown
# The high-water mark of this process's own memory: ru_maxrss would carry the forking
# parent's over the exec.
with open("/proc/self/status") as fh:
    print(next(line.split()[1] for line in fh if line.startswith("VmHWM:")))
'''


@pytest.mark.slow
@pytest.mark.skipif(sys.platform != "linux", reason="reads /proc/self/status")
def test_a_preview_at_every_bound_stays_under_the_page_servers_budget(occupant, tmp_path):
    from moxi_loadout import page

    secret = tmp_path / "secret.txt"
    secret.write_text("never read\n")
    (occupant / "link.txt").symlink_to(secret)
    (occupant / "blob.bin").write_bytes(b"\xff\xfe\x00")
    size = preview.FILE_MAX_BYTES - 1024
    for i in range(preview.TOTAL_MAX_BYTES // size + 2):
        (occupant / f"part{i}.md").write_text("y" * size)
    (occupant / "huge.md").write_text("z" * (8 * preview.FILE_MAX_BYTES))
    for i in range(preview.TREE_MAX_FILES):
        (occupant / f"z{i:03}.md").write_text(f"{i}\n")   # read last, past the others
    done = subprocess.run([sys.executable, "-c", RSS_PROBE], capture_output=True, text=True,
                          env=os.environ.copy(), timeout=120)
    assert done.returncode == 0, done.stderr
    budget_mib = int(page.BUDGET["rss"].rstrip("M"))
    assert int(done.stdout.split()[-1]) < budget_mib * 1024

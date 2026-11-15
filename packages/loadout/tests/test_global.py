"""The global instruction file: managed per harness, placed where absent, and taken out
again leaving the user's own text as it was."""

from __future__ import annotations

import json
import os

import loadout_kit as kit
import pytest

from moxi_loadout import cli, harness, install
from moxi_loadout.planner import Scope

TEXT = "Be brief.\n"


@pytest.fixture
def lo(folder):
    kit.configure(folder)
    (folder / "global.md").write_text(TEXT)
    from moxi import config

    config.add_item("moxi", "harnesses", {"name": "opencode",
                                          "skills": "~/.config/opencode/skills",
                                          "agents": "~/.config/opencode/agents"}, by="test")
    return folder


def mode(folder, name):
    kit.manifest(folder, f'[global]\nmode = "{name}"\n')


def test_managed_adds_each_part_and_off_takes_exactly_it_out(lo):
    claude = harness.global_file("claude")
    claude.parent.mkdir(parents=True)
    mine = "# My rules\n\nNo emojis.\n"
    claude.write_text(mine)
    mode(lo, "managed")
    out = install.install()
    assert out.code == 0, out.text()
    assert claude.read_text() == mine + f"@{lo / 'global.md'}\n"
    codex = harness.global_file("codex").read_text()
    assert codex.startswith(f"<!-- moxi loadout: from {lo / 'global.md'}; edit there -->\n")
    assert "Be brief." in codex and codex.rstrip().endswith("<!-- moxi loadout end -->")
    opencode = json.loads(harness.settings_file("opencode").read_text())
    assert opencode["instructions"] == [str(lo / "global.md")]
    # The global text changes: the Codex block follows; an edited block is left.
    (lo / "global.md").write_text("Be briefer.\n")
    assert install.install().code == 0
    assert "Be briefer." in harness.global_file("codex").read_text()
    assert cli.main(["global", "remove"]) == 0
    assert claude.read_text() == mine
    assert not harness.global_file("codex").exists()    # nothing else was in it
    assert "instructions" not in json.loads(harness.settings_file("opencode").read_text())


def test_an_edited_codex_block_is_modified(lo):
    mode(lo, "managed")
    install.install()
    path = harness.global_file("codex")
    path.write_text(path.read_text().replace("Be brief.", "Be long."))
    out = install.install()
    assert out.code == 3
    assert any(r.kind == "global" and r.harness == "codex" and r.state == "modified"
               for r in out.rows)
    assert install.install(replace=["global:global@codex"]).code == 0
    assert "Be brief." in path.read_text()


def test_if_absent_places_where_there_is_none_and_leaves_an_existing_file(lo):
    codex = harness.global_file("codex")
    codex.parent.mkdir(parents=True)
    codex.write_text("theirs\n")
    mode(lo, "if-absent")
    out = install.install()
    assert out.code == 0, out.text()
    claude = harness.global_file("claude")
    assert os.path.realpath(claude) == str(lo / "global.md")
    assert codex.read_text() == "theirs\n"
    # if-absent to managed: the placed file goes first, so nothing is written through it.
    assert cli.main(["global", "mode", "managed"]) == 0
    assert (lo / "global.md").read_text() == TEXT
    assert not claude.is_symlink()
    assert claude.read_text() == f"@{lo / 'global.md'}\n"
    # managed back to if-absent: the managed part goes first, then the file is placed.
    assert cli.main(["global", "mode", "if-absent"]) == 0
    assert os.path.realpath(claude) == str(lo / "global.md")
    assert codex.read_text() == "theirs\n"


def test_scoped_runs_never_touch_the_global_file(lo, tmp_path):
    mode(lo, "managed")
    assert install.install(Scope(user=True)).code == 0
    assert not harness.global_file("claude").exists()


def test_a_global_text_that_cant_be_read_leaves_the_parts_as_they_stand(lo):
    mode(lo, "managed")
    install.install()
    before = harness.global_file("claude").read_text()
    (lo / "global.md").unlink()
    out = install.install()
    assert out.code == 1
    assert harness.global_file("claude").read_text() == before


def test_show_prints_what_install_would_make(lo, capsys):
    mode(lo, "managed")
    assert cli.main(["global", "show", "--diff"]) == 0
    text = capsys.readouterr().out
    assert "global mode managed" in text and f"+@{lo / 'global.md'}" in text
    assert not harness.global_file("claude").exists()


@pytest.mark.parametrize("name", ["managed", "if-absent"])
def test_the_preview_call_is_what_show_prints_as_json(lo, capsys, name):
    mode(lo, name)
    harness.global_file("codex").parent.mkdir(parents=True, exist_ok=True)
    harness.global_file("codex").write_text("# Mine\n")
    preview = install.global_preview()
    assert preview["mode"] == name and preview["files"]
    assert cli.main(["global", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == preview
    assert not harness.global_file("claude").exists()


def test_no_mode_in_the_manifest_reads_off_with_a_line(lo):
    kit.manifest(lo, "[user]\n")
    out = install.install()
    assert out.code == 0
    assert any("global mode MODE" in n for n in out.notes)
    assert not harness.global_file("claude").exists()


def test_a_global_file_not_in_utf8_comes_back_byte_equal(lo):
    mine = b"# Mes r\xe8gles\r\n\nPas d'\xe9mojis.\n"   # Latin-1, not UTF-8
    for name in ("claude", "codex"):
        path = harness.global_file(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(mine)
    mode(lo, "managed")
    assert install.install().code == 0
    claude = harness.global_file("claude").read_bytes()
    assert claude.startswith(mine) and claude.endswith(f"@{lo / 'global.md'}\n".encode())
    assert install.status().code == 0
    assert cli.main(["global", "remove"]) == 0
    for name in ("claude", "codex"):
        assert harness.global_file(name).read_bytes() == mine


# -- the part files ------------------------------------------------------------------------

def test_a_part_is_named_after_the_shared_file(tmp_path):
    from moxi_loadout.globalfile import part_path

    assert part_path(tmp_path / "global.md", "claude") == tmp_path / "global.claude.md"
    assert part_path(tmp_path / "rules" / "team.md", "codex") == \
        tmp_path / "rules" / "team.codex.md"
    assert part_path(tmp_path / "global", "opencode") == tmp_path / "global.opencode"


def test_a_part_is_in_use_while_a_file_is_at_its_path(tmp_path):
    from moxi_loadout.globalfile import parts

    shared = tmp_path / "global.md"
    shared.write_text(TEXT)
    assert parts(shared) == {}
    (tmp_path / "global.codex.md").write_text("")
    assert parts(shared) == {"codex": tmp_path / "global.codex.md"}


def test_a_file_named_like_a_mistyped_part_is_noted(tmp_path):
    from moxi_loadout.globalfile import mistyped

    shared = tmp_path / "global.md"
    for name in ("global.md", "global.claud.md", "global.old.md", "global.v2.md",
                 "global.cloud.md", "global.gemini.md", "global.claude.md"):
        (tmp_path / name).write_text("x\n")
    found = mistyped(shared, ["claude", "codex", "opencode", "gemini"])
    assert [p.name for p in found] == ["global.claud.md", "global.gemini.md"]


def test_a_mistyped_part_is_a_note_and_changes_no_exit_code(lo):
    mode(lo, "managed")
    (lo / "global.claud.md").write_text("Never read.\n")
    (lo / "global.old.md").write_text("Kept aside.\n")
    out = install.install()
    assert out.code == 0, out.text()
    assert any("global.claud.md" in n for n in out.notes)
    assert not any("global.old.md" in n for n in out.notes)
    assert "Never read." not in harness.global_file("claude").read_text()


# -- the real-path rule --------------------------------------------------------------------

def _rows(out, state):
    return {r.harness for r in out.rows if r.kind == "global" and r.state == state}


def test_two_harness_files_that_are_one_file_are_left_blocked(lo):
    claude, codex = harness.global_file("claude"), harness.global_file("codex")
    claude.parent.mkdir(parents=True)
    codex.parent.mkdir(parents=True)
    mine = "# Mine\n"
    claude.write_text(mine)
    codex.symlink_to(claude)
    mode(lo, "managed")
    for _ in range(2):
        out = install.install()
        assert out.code == 3, out.text()
        assert _rows(out, "blocked") == {"claude", "codex"}
        assert claude.read_text() == mine and codex.is_symlink()
    assert any(str(codex) in line and str(claude) in line and "separate files" in line
               for line in out.text())
    assert install.status().code == 1


def test_global_remove_stands_on_harness_files_that_became_one_file(lo):
    mode(lo, "managed")
    assert install.install().code == 0
    claude, codex = harness.global_file("claude"), harness.global_file("codex")
    codex.unlink()
    codex.symlink_to(claude)
    before = claude.read_bytes()
    assert cli.main(["global", "remove"]) == 3
    assert claude.read_bytes() == before and codex.is_symlink()


def test_uninstall_refuses_while_a_recorded_global_file_is_another_harness_file(lo):
    from moxi import modules

    from moxi_loadout import teardown

    mode(lo, "managed")
    assert install.install().code == 0
    claude, codex = harness.global_file("claude"), harness.global_file("codex")
    before = claude.read_bytes()
    codex.unlink()
    codex.symlink_to(claude)
    with pytest.raises(modules.Refused) as exc:
        teardown.teardown(say=lambda line: None)
    assert "separate files" in str(exc.value)
    assert claude.read_bytes() == before


def test_a_harness_file_that_is_another_harness_part_is_blocked(lo):
    (lo / "global.claude.md").write_text("Claude only.\n")
    codex = harness.global_file("codex")
    codex.parent.mkdir(parents=True)
    codex.symlink_to(lo / "global.claude.md")
    mode(lo, "managed")
    out = install.install()
    assert out.code == 3, out.text()
    assert _rows(out, "blocked") == {"codex"}
    assert (lo / "global.claude.md").read_text() == "Claude only.\n"
    assert any("claude's part" in line for line in out.text())


@pytest.mark.parametrize("to", ["global.md", "global.codex.md"])
def test_a_link_to_the_shared_file_or_the_own_part_is_left_with_a_line(lo, to):
    (lo / "global.codex.md").write_text("Codex only.\n")
    codex = harness.global_file("codex")
    codex.parent.mkdir(parents=True)
    codex.symlink_to(lo / to)
    mode(lo, "managed")
    out = install.install()
    assert out.code == 0, out.text()
    assert (lo / "global.md").read_text() == TEXT
    assert (lo / "global.codex.md").read_text() == "Codex only.\n"
    assert sum(f"{codex} links to" in line for line in out.lines) == 1

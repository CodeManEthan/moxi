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


# -- managed: Claude's lines and the Codex block -------------------------------------------

def test_claude_gets_the_shared_line_then_its_part_line(lo):
    claude = harness.global_file("claude")
    claude.parent.mkdir(parents=True)
    mine = "# Mine\n"
    claude.write_text(mine)
    (lo / "global.claude.md").write_text("Claude only.\n")
    (lo / "global.codex.md").write_text("Codex only.\n")
    mode(lo, "managed")
    assert install.install().code == 0
    shared, part = f"@{lo / 'global.md'}", f"@{lo / 'global.claude.md'}"
    assert claude.read_text() == f"{mine}{shared}\n{part}\n"
    assert "codex" not in claude.read_text()
    # A line the user moved still reads ok; a deleted shared line comes back at the end.
    claude.write_text(f"{part}\n{mine}{shared}\n")
    assert install.status().code == 0
    claude.write_text(f"{mine}{part}\n")
    assert install.install().code == 0
    assert claude.read_text() == f"{mine}{part}\n{shared}\n"
    # The part deleted: its line goes, the shared line stays.
    (lo / "global.claude.md").unlink()
    assert install.install().code == 0
    assert claude.read_text() == f"{mine}{shared}\n"
    assert cli.main(["global", "remove"]) == 0
    assert claude.read_text() == mine


def test_the_codex_block_holds_the_shared_text_then_its_part(lo):
    (lo / "global.codex.md").write_text("Codex only.\n\n")
    (lo / "global.claude.md").write_text("Claude only.\n")
    mode(lo, "managed")
    assert install.install().code == 0
    codex = harness.global_file("codex")
    shared, part = lo / "global.md", lo / "global.codex.md"
    assert codex.read_text() == (f"<!-- moxi loadout: from {shared} and {part}; edit there "
                                 "-->\nBe brief.\n\nCodex only.\n<!-- moxi loadout end -->\n")
    # The part edited: the block follows.
    part.write_text("Codex, changed.\n")
    assert install.install().code == 0
    assert "Codex, changed." in codex.read_text()
    # An empty part is named and adds nothing.
    part.write_text("")
    assert install.install().code == 0
    assert codex.read_text() == (f"<!-- moxi loadout: from {shared} and {part}; edit there "
                                 "-->\nBe brief.\n<!-- moxi loadout end -->\n")
    # Edited by hand: modified, then --replace rewrites it.
    codex.write_text(codex.read_text().replace("Be brief.", "Be long."))
    out = install.install()
    assert out.code == 3 and _rows(out, "modified") == {"codex"}
    assert install.install(replace=["global:global@codex"]).code == 0
    assert "Be brief." in codex.read_text()


def test_no_harness_file_names_or_holds_another_harness_part(lo):
    for h in ("claude", "codex", "opencode"):
        (lo / f"global.{h}.md").write_text(f"SENTINEL-{h}\n")
    mode(lo, "managed")
    assert install.install().code == 0
    for h in ("claude", "codex"):
        text = harness.global_file(h).read_text()
        if h == "codex":
            assert "SENTINEL-codex" in text
        for other in ("claude", "codex", "opencode"):
            if other != h:
                assert f"SENTINEL-{other}" not in text
                assert f"global.{other}.md" not in text
    opencode = harness.settings_file("opencode").read_text()
    assert "global.claude.md" not in opencode and "global.codex.md" not in opencode


def test_the_assembled_text_drops_empty_files(lo):
    from moxi_loadout.globalfile import assemble, block

    assert assemble(["", "Part.\n\n"]) == "Part."
    assert assemble(["\n", "  \n"]) == ""
    assert assemble(["Shared.\n", "Part.\n"]) == "Shared.\n\nPart."
    assert block(["/a", "/b"], "") == ("<!-- moxi loadout: from /a and /b; edit there -->\n"
                                       "<!-- moxi loadout end -->")
    (lo / "global.md").write_text("")
    (lo / "global.codex.md").write_text("Codex only.\n")
    mode(lo, "managed")
    assert install.install().code == 0
    text = harness.global_file("codex").read_text()
    assert text.endswith("edit there -->\nCodex only.\n<!-- moxi loadout end -->\n")


def test_a_built_block_of_an_empty_shared_text_is_updated_once(lo):
    (lo / "global.md").write_text("")
    mode(lo, "managed")
    codex = harness.global_file("codex")
    codex.parent.mkdir(parents=True)
    built = (f"<!-- moxi loadout: from {lo / 'global.md'}; edit there -->\n\n"
             "<!-- moxi loadout end -->")
    codex.write_text(built + "\n")
    from moxi_loadout import ledger

    data = ledger.read()
    data["entries"].append({"item": "global:global", "harness": "codex", "file": str(codex),
                            "kind": "global-block", "key": ["block"], "written": built,
                            "prior": False})
    ledger.write(data)
    out = install.install()
    assert out.code == 0, out.text()
    assert any("updated global global for codex" in line for line in out.lines)
    assert codex.read_text() == built.replace("-->\n\n", "-->\n") + "\n"
    assert not any("codex" in line for line in install.install().lines)


def test_an_unreadable_part_stands_only_its_harness(lo):
    mode(lo, "managed")
    assert install.install().code == 0
    before = harness.global_file("codex").read_bytes()
    (lo / "global.codex.md").mkdir()
    (lo / "global.claude.md").write_text("Claude only.\n")
    out = install.install()
    assert out.code == 1
    assert _rows(out, "bad-source") == {"codex"}
    assert harness.global_file("codex").read_bytes() == before
    assert "global.claude.md" in harness.global_file("claude").read_text()


@pytest.mark.parametrize("start", [None, ""])
def test_an_absent_or_empty_harness_file_is_written_then_removed(lo, start):
    claude = harness.global_file("claude")
    if start is not None:
        claude.parent.mkdir(parents=True)
        claude.write_text(start)
    (lo / "global.claude.md").write_text("Claude only.\n")
    mode(lo, "managed")
    assert install.install().code == 0
    assert claude.read_text().count("@") == 2
    assert cli.main(["global", "remove"]) == 0
    assert not claude.exists()


# -- if-absent: the generated file ---------------------------------------------------------

def _gen(h):
    from moxi_loadout.globalfile import generated_path

    return generated_path(h)


def test_if_absent_places_a_generated_file_for_a_harness_with_a_part(lo):
    from moxi_loadout.globalfile import generated_state

    (lo / "global.claude.md").write_text("Claude only.\n")
    mode(lo, "if-absent")
    out = install.install()
    assert out.code == 0, out.text()
    claude, codex = harness.global_file("claude"), harness.global_file("codex")
    assert os.path.realpath(codex) == str(lo / "global.md")
    assert os.path.realpath(claude) == str(_gen("claude"))
    first, rest = _gen("claude").read_text().split("\n", 1)
    assert first.startswith(f"<!-- moxi loadout: from {lo / 'global.md'} and "
                            f"{lo / 'global.claude.md'}; sha256 ")
    assert rest == "Be brief.\n\nClaude only.\n"
    assert generated_state(_gen("claude")) == "ok"
    assert install.status().code == 0
    # The part deleted: re-pointed to the shared file, and the generated file deleted.
    (lo / "global.claude.md").unlink()
    out = install.install()
    assert out.code == 0, out.text()
    assert os.path.realpath(claude) == str(lo / "global.md")
    assert not _gen("claude").exists()


def test_an_empty_assembled_text_is_the_marker_line_alone(lo):
    (lo / "global.md").write_text("")
    (lo / "global.codex.md").write_text("\n")
    mode(lo, "if-absent")
    assert install.install().code == 0
    text = _gen("codex").read_text()
    assert text.count("\n") == 1 and text.startswith("<!-- moxi loadout: from ")


def test_a_hand_edit_to_the_generated_file_is_kept_until_replace(lo):
    from moxi_loadout import stores

    (lo / "global.claude.md").write_text("Claude only.\n")
    mode(lo, "if-absent")
    assert install.install().code == 0
    claude = harness.global_file("claude")
    with open(claude, "a") as f:
        f.write("My own line.\n")
    edited = _gen("claude").read_bytes()
    out = install.install()
    assert out.code == 3 and _rows(out, "modified") == {"claude"}
    reason = next(r.reason for r in out.rows if r.state == "modified")
    assert "global.claude.md" in reason and "--replace global:global@claude" in reason
    assert _gen("claude").read_bytes() == edited
    # The part deleted while edited: the placement is kept, not re-pointed.
    (lo / "global.claude.md").unlink()
    assert install.install().code == 3
    assert os.path.realpath(claude) == str(_gen("claude"))
    assert _gen("claude").read_bytes() == edited
    # --replace backs the edit up and places afresh.
    (lo / "global.claude.md").write_text("Claude only.\n")
    out = install.install(replace=["global:global@claude"])
    assert out.code == 0, out.text()
    assert "My own line." not in _gen("claude").read_text()
    backups = list((stores.backups_dir() / "claude").iterdir())
    assert any(b.read_bytes() == edited for b in backups)


def test_an_edited_generated_file_no_placement_uses_is_left_with_a_note(lo):
    (lo / "global.claude.md").write_text("Claude only.\n")
    mode(lo, "if-absent")
    assert install.install().code == 0
    with open(_gen("claude"), "a") as f:
        f.write("Mine.\n")
    os.unlink(harness.global_file("claude"))
    harness.global_file("claude").write_text("theirs\n")
    out = install.install()
    assert _gen("claude").exists()
    assert any(str(_gen("claude")) in n and "edited by hand" in n for n in out.notes)


def test_a_lost_ledger_still_reads_the_generated_file_by_its_marker(lo):
    (lo / "global.codex.md").write_text("Codex only.\n")
    mode(lo, "if-absent")
    assert install.install().code == 0
    from moxi_loadout import ledger

    ledger.write({"placed": {}, "entries": []})
    assert install.install().code == 0
    assert install.status().code == 0
    assert os.path.realpath(harness.global_file("codex")) == str(_gen("codex"))


def test_a_run_killed_after_the_generated_file_is_written_ends_ok(lo, monkeypatch):
    from moxi_loadout import ledger

    (lo / "global.codex.md").write_text("Codex only.\n")
    mode(lo, "if-absent")
    with monkeypatch.context() as m:
        m.setattr(ledger, "write", lambda data: None)
        install.install()
    out = install.install()
    assert out.code == 0, out.text()
    assert not any(r.state == "modified" for r in out.rows)


def test_an_occupied_harness_file_gets_nothing_generated(lo):
    (lo / "global.claude.md").write_text("Claude only.\n")
    claude = harness.global_file("claude")
    claude.parent.mkdir(parents=True)
    claude.write_text("")
    mode(lo, "if-absent")
    assert install.install().code == 0
    assert claude.read_text() == "" and not _gen("claude").exists()
    claude.unlink()
    out = install.install()
    assert out.code == 0, out.text()
    assert os.path.realpath(claude) == str(_gen("claude"))


def test_a_user_link_reaching_a_re_pointed_placement_is_blocked(lo):
    claude, codex = harness.global_file("claude"), harness.global_file("codex")
    codex.parent.mkdir(parents=True)
    codex.symlink_to(claude)
    mode(lo, "if-absent")
    assert install.install().code == 0
    assert os.path.realpath(codex) == str(lo / "global.md")
    (lo / "global.claude.md").write_text("Claude only.\n")
    out = install.install()
    assert out.code == 3, out.text()
    assert _rows(out, "blocked") == {"claude", "codex"}
    assert os.readlink(codex) == str(claude)
    assert os.path.realpath(claude) == str(lo / "global.md")
    assert not _gen("claude").exists()


def test_mode_changes_with_parts_leave_nothing_of_the_old_mode(lo):
    for h in ("claude", "codex"):
        (lo / f"global.{h}.md").write_text(f"{h} only.\n")
    claude = harness.global_file("claude")
    mode(lo, "if-absent")
    assert install.install().code == 0
    assert os.path.realpath(claude) == str(_gen("claude"))
    assert cli.main(["global", "mode", "managed"]) == 0
    assert not claude.is_symlink() and claude.read_text().count("@") == 2
    assert not _gen("claude").exists()
    assert (lo / "global.claude.md").read_text() == "claude only.\n"
    assert cli.main(["global", "mode", "if-absent"]) == 0
    assert os.path.realpath(claude) == str(_gen("claude"))
    assert cli.main(["global", "remove"]) == 0
    assert not os.path.lexists(claude) and not _gen("claude").exists()
    assert cli.main(["global", "mode", "managed"]) == 0
    assert cli.main(["global", "remove"]) == 0
    assert not os.path.lexists(claude)


def test_if_absent_to_managed_keeps_an_edited_generated_file_placed(lo):
    from moxi import modules

    from moxi_loadout import teardown

    (lo / "global.claude.md").write_text("Claude only.\n")
    mode(lo, "if-absent")
    assert install.install().code == 0
    with open(_gen("claude"), "a") as f:
        f.write("Mine.\n")
    edited = _gen("claude").read_bytes()
    assert cli.main(["global", "mode", "managed"]) == 3
    claude = harness.global_file("claude")
    assert os.path.realpath(claude) == str(_gen("claude"))
    assert _gen("claude").read_bytes() == edited
    assert cli.main(["global", "remove"]) == 3
    assert _gen("claude").read_bytes() == edited
    with pytest.raises(modules.Refused) as exc:
        teardown.teardown(say=lambda line: None)
    assert "global:global@claude" in str(exc.value)
    assert _gen("claude").read_bytes() == edited


# -- moving an existing install ------------------------------------------------------------

def _instructions_notes(out):
    warns = [line for level, line in out.global_lines if level == "warn"]
    lines = [n for n in out.lines if "instructions entry" in n]
    assert lines == [n for n in warns if "instructions entry" in n]
    return lines


def test_an_instructions_entry_naming_the_global_text_is_noted_and_left(lo):
    (lo / "global.codex.md").write_text("Codex only.\n")
    home = harness.opencode_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "rules.md").write_text("Theirs.\n")
    jsonc = home / "opencode.jsonc"
    tilde = "~/" + os.path.relpath(lo / "global.codex.md", os.path.expanduser("~"))
    body = ('{\n  // the user\'s own config\n  "instructions": [\n'
            f'    "{tilde}",  /* a part, by ~ */\n    "rules.md",\n  ],\n}}\n')
    jsonc.write_text(body)
    plain = home / "opencode.json"
    plain.write_text(json.dumps({"instructions": [str(lo / "global.*")]}) + "\n")
    before = plain.read_bytes()
    mode(lo, "if-absent")
    out = install.install()
    assert out.code == 0, out.text()
    notes = _instructions_notes(out)
    assert len(notes) == 2, notes
    assert any(str(jsonc) in n and tilde in n for n in notes)
    assert any(str(plain) in n and "global.*" in n for n in notes)
    assert not any("rules.md" in n for n in notes)
    # Read, never written; and noted again on a later status.
    assert jsonc.read_text() == body and plain.read_bytes() == before
    assert len(_instructions_notes(install.status())) == 2


def test_a_relative_instructions_entry_resolves_against_the_opencode_home(lo):
    home = harness.opencode_home()
    home.mkdir(parents=True, exist_ok=True)
    os.symlink(lo, home / "folder")
    (home / "opencode.json").write_text('{"instructions": ["folder/global.md"]}\n')
    mode(lo, "managed")
    assert len(_instructions_notes(install.status())) == 1
    mode(lo, "off")
    assert _instructions_notes(install.status()) == []


def test_an_instructions_entry_loadout_recorded_is_not_noted(lo):
    # The built managed form records the shared path in instructions; leaving managed
    # removes it, with no note to delete it by hand.
    mode(lo, "managed")
    install.install()
    mode(lo, "if-absent")
    out = install.install()
    assert out.code == 0, out.text()
    assert _instructions_notes(out) == []
    assert "instructions" not in json.loads(harness.settings_file("opencode").read_text())


def test_the_built_claude_and_codex_forms_take_their_parts_in_one_install(lo):
    claude = harness.global_file("claude")
    claude.parent.mkdir(parents=True)
    mine = "# Mine\n"
    claude.write_text(mine)
    codex = harness.global_file("codex")
    codex.parent.mkdir(parents=True)
    codex.write_text("# Codex mine\n")
    mode(lo, "managed")
    assert install.install().code == 0    # the shared-only form, as built
    built = codex.read_text()
    (lo / "global.claude.md").write_text("Claude only.\n")
    (lo / "global.codex.md").write_text("Codex only.\n")
    out = install.install()
    assert out.code == 0, out.text()
    shared, cpart, xpart = (str(lo / n) for n in ("global.md", "global.claude.md",
                                                  "global.codex.md"))
    assert claude.read_text() == mine + f"@{shared}\n@{cpart}\n"
    assert built.startswith("# Codex mine\n\n<!-- moxi loadout: from ")
    assert codex.read_text() == "# Codex mine\n\n" + (
        f"<!-- moxi loadout: from {shared} and {xpart}; edit there -->\n"
        "Be brief.\n\nCodex only.\n<!-- moxi loadout end -->\n")
    assert cli.main(["global", "remove"]) == 0
    assert claude.read_text() == mine and codex.read_text() == "# Codex mine\n"


# -- show, status and the doctor -----------------------------------------------------------

def _doctor_global():
    from moxi_loadout import doctor

    return next(r for r in doctor.rows() if r.name == "global")


def test_show_names_the_files_each_harness_reads(lo, capsys):
    (lo / "global.claude.md").write_text("Claude only.\n")
    mode(lo, "managed")
    assert cli.main(["global", "show", "--json"]) == 0
    files = {f["harness"]: f for f in json.loads(capsys.readouterr().out)["files"]}
    assert files["claude"]["parts"] == [str(lo / "global.md"), str(lo / "global.claude.md")]
    assert files["codex"]["parts"] == [str(lo / "global.md")]
    assert files["claude"]["file"] == str(harness.global_file("claude"))
    assert cli.main(["global", "show"]) == 0
    text = capsys.readouterr().out
    assert f"claude reads {lo / 'global.md'} and {lo / 'global.claude.md'}\n" in text
    assert f"codex reads {lo / 'global.md'}, no part\n" in text
    # If-absent names them too; off names none.
    mode(lo, "if-absent")
    placed = {f["harness"]: f for f in install.global_preview()["files"]}
    assert placed["claude"]["parts"] == [str(lo / "global.md"), str(lo / "global.claude.md")]
    mode(lo, "off")
    assert all(f["parts"] == [] for f in install.global_preview()["files"])


def test_the_doctor_row_names_each_harness_state_and_parts(lo):
    (lo / "global.claude.md").write_text("Claude only.\n")
    (lo / "global.claud.md").write_text("Never read.\n")
    (lo / "global.old.md").write_text("Kept aside.\n")
    mode(lo, "managed")
    assert install.install().code == 0
    row = _doctor_global()
    assert row.state == "ok", row.detail
    assert row.detail.startswith("mode managed: claude ok (global.md, global.claude.md), "
                                 "codex ok (global.md), opencode ok (global.md)")
    assert "global.claud.md" in row.detail and "global.old.md" not in row.detail


def test_a_codex_override_file_warns_that_the_block_doesnt_reach_codex(lo):
    mode(lo, "managed")
    override = harness.codex_home() / "AGENTS.override.md"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("\n")    # only whitespace: Codex reads AGENTS.md
    assert install.install().code == 0
    assert _doctor_global().state == "ok"
    override.write_text("Override.\n")
    out = install.status()
    assert out.code == 0 and any("AGENTS.override.md" in line for line in out.lines)
    row = _doctor_global()
    assert row.state == "warn" and "AGENTS.override.md" in row.detail
    assert override.read_text() == "Override.\n"
    mode(lo, "off")
    assert install.install().code == 0
    assert _doctor_global().state == "ok"


def test_harness_files_that_are_one_file_warn_in_the_doctor_row(lo):
    mode(lo, "managed")
    claude, codex = harness.global_file("claude"), harness.global_file("codex")
    claude.parent.mkdir(parents=True)
    claude.write_text("Mine.\n")
    codex.parent.mkdir(parents=True)
    os.symlink(claude, codex)
    install.install()
    row = _doctor_global()
    assert row.state == "warn" and "are one file" in row.detail
    assert str(claude) in row.detail and str(codex) in row.detail

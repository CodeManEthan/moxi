"""The agent converters and their header, and the verbs as `moxi loadout` runs them."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib

import loadout_kit as kit
import pytest

from moxi_loadout import convert, manifest
from moxi_loadout.cli import main


# -- conversions ---------------------------------------------------------------------------

def test_codex_conversion_names_what_it_drops():
    text, notes = convert.codex("helper", {"name": "helper", "description": "Helps.",
                                           "tools": "Read, Grep", "model": "opus"},
                                "Be kind.\n", {"model": "o4"})
    data = tomllib.loads(text)
    assert data["name"] == "helper" and data["model"] == "o4"
    assert data["sandbox_mode"] == "read-only"
    assert data["developer_instructions"] == "Be kind.\n"
    assert "dropped tools" in notes and "dropped model" in notes


def test_opencode_conversion_keeps_the_description_and_adds_its_keys():
    import yaml

    text, notes = convert.opencode("helper", {"name": "helper", "description": "Helps.",
                                              "tools": "Read"}, "Be kind.\n",
                                   {"model": "anthropic/x"})
    head, _, body = text[4:].partition("\n---\n")
    fields = yaml.safe_load(head)
    assert fields == {"description": "Helps.", "mode": "subagent", "model": "anthropic/x"}
    assert body.strip() == "Be kind." and notes == ["dropped tools"]


@pytest.mark.parametrize("text", ["plain toml = 1\n", "---\ndescription: x\n---\nbody\n"])
def test_the_header_reads_ok_and_nothing_else_does(tmp_path, text):
    path = tmp_path / "f"
    path.write_bytes(convert.with_header("/src/a.md", text))
    assert convert.state(path) == "ok"
    assert path.read_text().startswith("---\n# generated" if text.startswith("---") else "#")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("more\n")
    assert convert.state(path) == "modified"
    path.write_text(text)
    assert convert.state(path) == "modified"  # no header: made or rewritten by hand
    path.unlink()
    assert convert.state(path) is None
    path.mkdir()
    assert convert.state(path) == "modified"  # can't be read as a file


@pytest.mark.parametrize("text, why", [("no frontmatter\n", "has no frontmatter"),
                                        ("---\nname: x\n", "unclosed"),
                                        ("---\n- a\n---\n", "not a mapping")])
def test_frontmatter_that_does_not_read_is_refused(tmp_path, text, why):
    path = tmp_path / "a.md"
    path.write_text(text)
    with pytest.raises(convert.FrontmatterError, match=why):
        convert.read(path)


# -- the verbs -----------------------------------------------------------------------------

@pytest.fixture
def lo(loadout_dir):
    kit.configure(loadout_dir)
    kit.skill(loadout_dir / "skills", "review")
    kit.skill(loadout_dir / "skills", "other")
    kit.manifest(loadout_dir, "# my loadout\n[user]\nskills = [\"other\"]\n")
    return loadout_dir


def test_add_writes_the_manifest_then_installs_only_that_item(lo, capsys):
    assert main(["add", "skill", "review"]) == 0
    out = capsys.readouterr().out
    assert "listed skill review in user for every harness" in out
    assert (kit.claude() / "review").is_symlink()
    assert not os.path.lexists(kit.claude() / "other")
    assert "# my loadout" in (lo / "loadout.toml").read_text()


def test_add_for_a_harness_lists_and_places_it_for_that_harness_only(lo, capsys):
    assert main(["add", "skill", "review", "--harness", "claude"]) == 0
    assert "listed skill review in user for claude\n" in capsys.readouterr().out
    assert (kit.claude() / "review").is_symlink()
    assert not os.path.lexists(kit.codex_skills() / "review")
    found = manifest.load()
    assert found.harnesses("skill", "review") is None
    assert "skill:review@codex" in [str(i) for i in found.user.disabled]
    assert main(["add", "skill", "review"]) == 0
    assert ("user lists skill review already, for claude; enable and disable change its "
            "harnesses") in capsys.readouterr().out


def test_remove_then_add_for_every_harness_places_it_for_every_harness(lo, capsys):
    main(["add", "skill", "review", "--harness", "claude"])
    assert main(["remove", "skill", "review"]) == 0
    assert main(["add", "skill", "review"]) == 0
    assert "listed skill review in user for every harness" in capsys.readouterr().out
    assert (kit.claude() / "review").is_symlink()
    assert (kit.codex_skills() / "review").is_symlink()
    assert manifest.load().user.disabled == ()


def test_add_for_a_harness_with_no_row_is_a_usage_error(lo, capsys):
    before = (lo / "loadout.toml").read_bytes()
    assert main(["add", "skill", "review", "--harness", "kestrel"]) == 2
    assert "has no row kestrel" in capsys.readouterr().err
    assert (lo / "loadout.toml").read_bytes() == before


def test_disable_and_remove_install_their_item_and_leave_no_lock_file(lo, capsys):
    main(["install"])
    assert main(["disable", "skill:other@codex"]) == 0
    assert not os.path.lexists(kit.codex_skills() / "other")
    assert (kit.claude() / "other").is_symlink()
    assert main(["remove", "skill", "other"]) == 0
    assert not os.path.lexists(kit.claude() / "other")
    assert sorted(os.listdir(lo)) == ["loadout.toml", "skills"]


def test_status_json_and_its_exit_codes(lo, capsys):
    assert main(["status", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert {r["state"] for r in data["rows"]} == {"missing"} and data["exit"] == 1
    assert main(["install"]) == 0
    capsys.readouterr()
    assert main(["status"]) == 0
    assert capsys.readouterr().out.strip().endswith("loadout: 2 ok")


def test_usage_and_manifest_errors_exit_2(lo, capsys):
    assert main(["install", "--bogus"]) == 2
    assert main(["add", "widget", "x"]) == 2
    (lo / "loadout.toml").write_text("[user]\nskils = []\n")
    assert main(["status"]) == 2
    assert "did you mean skills" in capsys.readouterr().err
    assert main(["status", "--project", "nowhere"]) == 2


def test_list_prints_every_item(lo, capsys):
    assert main(["list", "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)
    assert entries == [{"kind": "skill", "name": "other", "scope": "user", "harnesses": None,
                        "source": str(lo / "skills" / "other"), "disabled": []}]


def test_python_dash_m_runs_the_cli(lo):
    run = subprocess.run([sys.executable, "-m", "moxi_loadout", "status", "--json"],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 1, run.stderr
    assert json.loads(run.stdout)["exit"] == 1


def test_install_replace_asks_at_a_terminal_and_accept_diff_skips_the_question(
        lo, capsys, monkeypatch):
    import io

    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    kit.claude().mkdir(parents=True)
    kit.skill(kit.claude(), "review", "mine\n")
    target = kit.claude() / "review"

    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr(sys, "stdin", Tty("n\n"))
    monkeypatch.setattr(sys, "stderr", Tty())
    assert main(["install", "--replace", "skill:review@claude"]) != 0
    assert "differs from what would be placed" in sys.stderr.getvalue()
    assert not target.is_symlink()
    # With --json nothing is asked, and the replace is refused naming the flag.
    monkeypatch.setattr(sys, "stdin", Tty("y\n"))
    assert main(["install", "--replace", "skill:review@claude", "--json"]) != 0
    assert "pass --accept-diff" in capsys.readouterr().out
    assert main(["install", "--replace", "skill:review@claude", "--accept-diff",
                 "--json"]) == 0
    assert target.is_symlink()

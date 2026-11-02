"""The agent converters and their header, and the verbs as `moxi loadout` runs them."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib

import loadout_kit as kit
import pytest

from moxi_loadout import convert
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
def test_the_header_reads_ok_until_the_file_is_edited(tmp_path, text):
    path = tmp_path / "f"
    path.write_bytes(convert.with_header("/src/a.md", text))
    assert convert.state(path) == "ok"
    assert path.read_text().startswith("---\n# generated" if text.startswith("---") else "#")
    with open(path, "a", encoding="utf-8") as f:
        f.write("more\n")
    assert convert.state(path) == "modified"
    path.write_text(text)
    assert convert.state(path) is None


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
def lo(folder):
    kit.configure(folder)
    kit.skill(folder / "skills", "review")
    kit.skill(folder / "skills", "other")
    kit.manifest(folder, "# my loadout\n[user]\nskills = [\"other\"]\n")
    return folder


def test_add_writes_the_manifest_then_installs_only_that_item(lo, capsys):
    assert main(["add", "skill", "review"]) == 0
    out = capsys.readouterr().out
    assert "listed skill review in user" in out
    assert (kit.claude() / "review").is_symlink()
    assert not os.path.lexists(kit.claude() / "other")
    assert "# my loadout" in (lo / "loadout.toml").read_text()


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

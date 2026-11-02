"""loadout's doctor rows, and their place in the core's doctor run."""

from __future__ import annotations

import os
import sys
import time

import loadout_kit as kit
import pytest

from moxi_loadout import doctor, install


@pytest.fixture
def lo(folder):
    kit.configure(folder)
    kit.skill(folder / "skills", "review")
    return folder


def by_name(rows) -> dict:
    return {r.name: r for r in rows}


def test_a_clean_install_reads_ok(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\n[global]\nmode = "off"\n')
    assert install.install().code == 0
    rows = by_name(doctor.rows())
    assert rows["manifest"].state == "ok" and "1 item listed" in rows["manifest"].detail
    assert rows["harness claude"].state == "ok"
    assert rows["harness codex"].state == "ok"
    assert rows["global"].state == "ok" and rows["global"].detail.startswith("mode off")
    assert rows["hooks"].state == "off"
    assert rows["codex"].state == "ok"


def test_no_manifest_yet_is_ok(folder):
    kit.configure(folder)
    rows = by_name(doctor.rows())
    assert rows["manifest"].state == "ok" and "yet" in rows["manifest"].detail
    assert "no [global] mode" in rows["global"].detail


def test_an_unknown_key_fails_and_is_named(lo):
    kit.manifest(lo, '[user]\nskils = ["review"]\n')
    rows = list(doctor.rows())
    assert [r.name for r in rows] == ["manifest"]
    assert rows[0].state == "fail" and "skils" in rows[0].detail
    assert "did you mean" in rows[0].detail


def test_a_harness_with_items_not_ok_warns(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    occupied = kit.claude() / "review"
    occupied.mkdir(parents=True)
    (occupied / "SKILL.md").write_text("theirs\n")
    rows = by_name(doctor.rows())
    assert rows["harness claude"].state == "warn"
    assert "blocked" in rows["harness claude"].detail
    assert rows["harness claude"].fix == "moxi loadout status"
    assert rows["harness codex"].state == "warn"
    assert rows["harness codex"].fix == "moxi loadout install"


def test_a_modified_entry_warns_for_its_harness(lo):
    kit.manifest(lo, '[user]\nhooks = ["guard"]\n[hook.guard]\nevent = "PreToolUse"\n'
                     'matcher = "Bash"\ncommand = "true"\nharnesses = ["claude"]\n')
    assert install.install().code == 0
    import json

    path = kit.claude().parent / "settings.json"
    data = json.loads(path.read_text())
    data["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 99
    path.write_text(json.dumps(data))
    rows = by_name(doctor.rows())
    assert rows["harness claude"].state == "warn"
    assert "modified" in rows["harness claude"].detail


def test_a_hook_program_that_does_not_resolve_warns(lo):
    kit.manifest(lo, '[user]\nhooks = ["guard", "gone"]\n'
                     '[hook.guard]\nevent = "PreToolUse"\ncommand = "true"\n'
                     '[hook.gone]\nevent = "PreToolUse"\n'
                     'command = "X=1 python3 {source}/hooks/gone.py"\n')
    row = by_name(doctor.rows())["hooks"]
    assert row.state == "warn"
    assert "hook gone" in row.detail and "gone.py is not there" in row.detail
    (lo / "hooks").mkdir()
    (lo / "hooks" / "gone.py").write_text("")
    assert by_name(doctor.rows())["hooks"].state == "ok"


@pytest.mark.parametrize("command", [
    "~/bin/guard.sh",                      # the shell expands the home
    "$HOME/bin/guard.sh",                  # and its variables
    "cd /nowhere-made-up && guard.sh",     # a builtin, then an operator
    "exit 0",
    "guard.sh | tee /nowhere-made-up/log",
])
def test_a_hook_command_the_shell_runs_does_not_warn(lo, command):
    script = os.path.join(os.environ["HOME"], "bin", "guard.sh")
    os.makedirs(os.path.dirname(script))
    with open(script, "w") as f:
        f.write("#!/bin/sh\n")
    os.chmod(script, 0o755)
    kit.manifest(lo, '[user]\nhooks = ["guard"]\n[hook.guard]\nevent = "PreToolUse"\n'
                     f'command = "{command}"\n')
    row = by_name(doctor.rows())["hooks"]
    assert row.state == "ok", row.detail


def test_a_key_section_without_projects_warns(lo, site):
    kit.manifest(lo, '[project.app]\nskills = ["review"]\n')
    row = by_name(doctor.rows())["projects"]
    assert row.state == "warn" and "app" in row.detail
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    assert by_name(doctor.rows())["projects"].state == "off"


def test_codex_switches_are_noted(lo):
    kit.manifest(lo, '[user]\nhooks = ["guard"]\n[hook.guard]\nevent = "PreToolUse"\n'
                     'command = "true"\n')
    config = kit.codex_agents().parent / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('external-agent-import-sync-enabled = true\n'
                      '[agents]\nenabled = false\n')
    row = by_name(doctor.rows())["codex"]
    assert row.state == "ok"
    assert "agents.enabled = false" in row.detail
    assert "desktop import is on" in row.detail
    assert "trust it in codex: guard" in row.detail


def test_an_adopted_skill_whose_module_is_gone_reads_off(lo, site):
    kit.manifest(lo, '[user]\nskills = ["launcher/moxi-handoff"]\n')
    row = by_name(doctor.rows())["module launcher"]
    assert row.state == "off"
    assert "launcher/moxi-handoff" in row.detail
    assert row.fix == "moxi install launcher"


def test_the_announcement_names_the_rows(lo):
    import importlib

    from moxi import modules
    from moxi.doctor import Row

    found = modules.load("loadout")
    assert found.doctor == "moxi_loadout.doctor:rows"
    module, _, name = found.doctor.partition(":")
    rows = list(getattr(importlib.import_module(module), name)())
    assert rows and all(isinstance(r, Row) and r.module == "loadout" for r in rows)


def test_the_doctor_gives_git_short_calls(lo, monkeypatch):
    from moxi import proc

    seen = []
    real = proc.run

    def run(argv, **kwargs):
        if os.path.basename(str(argv[0])) == "git":
            seen.append(kwargs["timeout"])
        return real(argv, **kwargs)

    monkeypatch.setattr(proc, "run", run)
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    list(doctor.rows())
    assert seen and max(seen) <= doctor.GIT_SECONDS


@pytest.mark.timed
@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in git is a shell script")
def test_the_rows_finish_inside_the_doctors_time_while_git_hangs(lo, tmp_path, monkeypatch):
    bin_dir = tmp_path / "slow-bin"
    bin_dir.mkdir()
    slow = bin_dir / "git"
    slow.write_text("#!/bin/sh\nsleep 60\n")
    slow.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    start = time.monotonic()
    rows = by_name(doctor.rows())
    took = time.monotonic() - start
    assert took < 8, took
    assert rows["would-commit"].state == "warn"

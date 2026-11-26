"""install and status: the whole manifest planned, the steps in scope applied, and every
other placement left as it was."""

from __future__ import annotations

import os
from pathlib import Path

import loadout_kit as kit
import pytest
from moxi.paths import link_target
from moxi_dev.testing import toml_string

from moxi_loadout import install, ledger, planner
from moxi_loadout.planner import Resolution, Scope


@pytest.fixture
def lo(loadout_dir):
    kit.configure(loadout_dir)
    kit.skill(loadout_dir / "skills", "review")
    kit.agent(loadout_dir / "agents" / "helper.md", "helper", "tools: Read, Grep\n")
    return loadout_dir


def states(outcome) -> dict:
    return {(r.kind, r.name, r.harness, r.scope): r.state for r in outcome.rows}


def project(tmp_path, name="app"):
    root = tmp_path / name
    root.mkdir()
    return root


def test_install_places_skills_and_agents_for_each_harness(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\nagents = ["helper"]\n')
    before = install.status()
    assert before.code == 1
    assert set(states(before).values()) == {"missing"}
    out = install.install()
    assert out.code == 0, out.text()
    assert kit.placed(kit.claude() / "review", lo / "skills" / "review")
    assert kit.placed(kit.codex_skills() / "review", lo / "skills" / "review")
    assert kit.placed(kit.claude("agents") / "helper.md", lo / "agents" / "helper.md")
    # The Codex agent is generated, and goes to the Codex home's agents folder.
    toml = kit.codex_agents() / "helper.toml"
    from moxi_loadout import stores

    assert kit.placed(toml)
    assert os.path.realpath(toml).startswith(os.path.realpath(stores.generated_dir()))
    assert 'name = "helper"' in toml.read_text()
    after = install.status()
    assert after.code == 0 and set(states(after).values()) == {"ok"}
    assert any("dropped tools" in n for n in after.notes)
    records = ledger.read()["placed"]
    assert {(r["item"], r["harness"]) for r in records.values()} == {
        ("review", "claude"), ("review", "codex"), ("helper", "claude"), ("helper", "codex")}


def test_harness_list_and_disabled_choose_the_harnesses(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\nagents = ["helper"]\n'
                     'disabled = ["agent:helper@codex"]\n'
                     '[skill.review]\nharnesses = ["claude"]\n')
    out = install.install()
    assert out.code == 0
    assert kit.placed(kit.claude() / "review")
    assert not os.path.lexists(kit.codex_skills() / "review")
    assert not os.path.lexists(kit.codex_agents() / "helper.toml")
    assert states(out)[("agent", "helper", "codex", "user")] == "off"


def test_install_user_leaves_project_placements_as_they_are(lo, tmp_path):
    root = project(tmp_path)
    kit.skill(lo / "skills", "deploy")
    kit.manifest(lo, f'[user]\nskills = ["review"]\n[project.{toml_string(root)}]\nskills = ["deploy"]\n')
    out = install.install(Scope(user=True))
    assert out.code == 0
    assert kit.placed(kit.claude() / "review")
    assert not os.path.lexists(root / ".claude" / "skills" / "deploy")
    out = install.install(Scope(projects=(str(root),)))
    assert out.code == 0
    assert kit.placed(root / ".claude" / "skills" / "deploy")
    assert kit.placed(root / ".agents" / "skills" / "deploy")


def test_one_items_scope_applies_only_its_steps(lo):
    kit.skill(lo / "skills", "other")
    kit.manifest(lo, '[user]\nskills = ["review", "other"]\n')
    out = install.install(Scope(item=("skill", "review")))
    assert out.code == 0
    assert kit.placed(kit.claude() / "review")
    assert not os.path.lexists(kit.claude() / "other")


def test_a_dropped_item_is_removed_and_a_remove_outside_the_scope_waits(lo, tmp_path):
    kit.skill(lo / "skills", "other")
    kit.manifest(lo, '[user]\nskills = ["review", "other"]\n')
    install.install()
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    root = project(tmp_path)
    kit.manifest(lo, f'[user]\nskills = ["review"]\n[project.{toml_string(root)}]\n')
    assert install.install(Scope(projects=(str(root),))).code == 0
    assert kit.placed(kit.claude() / "other")
    status = install.status()
    assert any(r.state == "stale" and r.name == "other" for r in status.rows)
    assert any(r.name == "other" and r.reason == "no longer wanted; install removes it"
               for r in status.rows)
    assert install.install().code == 0
    assert not os.path.lexists(kit.claude() / "other")
    assert kit.placed(kit.claude() / "review")
    assert {r["item"] for r in ledger.read()["placed"].values()} == {"review"}


def test_a_disabled_harness_removes_only_that_placement(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    install.install()
    kit.manifest(lo, '[user]\nskills = ["review"]\ndisabled = ["skill:review@codex"]\n')
    assert install.install().code == 0
    assert not os.path.lexists(kit.codex_skills() / "review")
    assert kit.placed(kit.claude() / "review")


@pytest.mark.parametrize("break_it", ["delete", "clash", "name"])
def test_a_bad_source_is_carried_with_its_placement_kept(lo, tmp_path, break_it):
    import shutil

    root = project(tmp_path)
    kit.skill(lo / "skills", "deploy")
    kit.manifest(lo, f'[user]\nskills = ["review"]\n[project.{toml_string(root)}]\nskills = ["deploy"]\n')
    assert install.install().code == 0
    if break_it == "delete":
        shutil.rmtree(lo / "skills" / "review")
        broken = ("skill", "review", "claude", "user")
    elif break_it == "clash":
        kit.unplace(root / ".claude" / "skills" / "deploy")
        kit.skill(root / ".claude" / "skills", "deploy")
        broken = ("skill", "deploy", "claude", str(root))
    else:
        (lo / "skills" / "review" / "SKILL.md").write_text("---\nname: other\n---\n")
        broken = ("skill", "review", "claude", "user")
    out = install.install()
    assert out.code == 1
    assert states(out)[broken] == "bad-source"
    assert os.path.lexists(kit.claude() / "review")
    assert os.path.lexists(root / ".agents" / "skills" / "deploy")


def test_a_held_item_is_kept_in_place_and_linked_relatively(lo, tmp_path):
    root = project(tmp_path)
    held = kit.skill(root / ".claude" / "skills", "own")
    kit.agent(root / ".claude" / "agents" / "lead.md", "lead")
    kit.manifest(lo, f'[project.{toml_string(root)}]\nskills = ["own"]\nagents = ["lead"]\n')
    out = install.install()
    assert out.code == 0, out.text()
    assert held.is_dir() and not held.is_symlink()
    link = root / ".agents" / "skills" / "own"
    assert link_target(link) == os.path.join("..", "..", ".claude", "skills", "own")
    assert kit.placed(root / ".codex" / "agents" / "lead.toml")
    assert set(states(install.status()).values()) == {"ok"}


def test_a_project_item_from_the_loadout_dir_is_not_the_projects_own(lo, tmp_path):
    from moxi import config

    config.set_value("moxi", "skills.form", "copy", by="test")
    root = project(tmp_path)
    kit.manifest(lo, f'[project.{toml_string(root)}]\nskills = ["review"]\nagents = ["helper"]\n')
    assert install.install().code == 0
    copied = root / ".claude" / "skills" / "review"
    assert copied.is_dir() and not copied.is_symlink()
    for _ in range(2):
        out = install.install()
        assert out.code == 0, out.text()
        assert set(states(out).values()) == {"ok"}
    assert install.status().code == 0
    kit.skill(lo / "skills", "review", "Changed.\n")
    assert install.install().code == 0
    assert "Changed." in (copied / "SKILL.md").read_text()


def test_a_hook_listed_in_a_project_section_is_refused_with_a_line(lo, tmp_path):
    root = project(tmp_path)
    kit.manifest(lo, f'[project.{toml_string(root)}]\nhooks = ["guard"]\npermissions = ["reads"]\n'
                     'mcp = ["docs"]\n'
                     '[hook.guard]\nevent = "PreToolUse"\ncommand = "true"\n'
                     '[permissions.reads]\nallow = [{ shell = ["git", "status"] }]\n'
                     '[mcp.docs]\ncommand = "docs-mcp"\n')
    for out in (install.status(), install.install()):
        said = "\n".join(out.text())
        for kind, name in (("hook", "guard"), ("permissions", "reads"), ("mcp", "docs")):
            assert f"{kind} {name} is written at user scope only" in said, said


def test_two_project_sections_place_one_name_each(lo, tmp_path):
    a, b = project(tmp_path, "a"), project(tmp_path, "b")
    kit.manifest(lo, f'[project.{toml_string(a)}]\nskills = ["review"]\n'
                     f'[project.{toml_string(b)}]\nskills = ["review"]\n')
    assert install.install().code == 0
    assert kit.placed(a / ".claude" / "skills" / "review")
    assert kit.placed(b / ".claude" / "skills" / "review")


def test_an_occupant_is_blocked_until_replace_moves_it_aside(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    kit.claude().mkdir(parents=True)
    kit.skill(kit.claude(), "review", "mine\n")
    out = install.install()
    assert out.code == 3
    assert states(out)[("skill", "review", "claude", "user")] == "blocked"
    [row] = [r for r in install.status().rows if (r.name, r.harness) == ("review", "claude")]
    assert row.replaceable and row.as_json()["replaceable"] is True
    assert install.install(replace=["skill:review@claude"]).code == 0
    assert not any(r.replaceable for r in install.status().rows)
    assert kit.placed(kit.claude() / "review")
    # A --replace that names nothing refused is refused.
    out = install.install(replace=["skill:nothing@claude"])
    assert out.code == 3 and any("nothing of loadout's" in line for line in out.lines)


def test_a_hand_edited_generated_agent_is_left_until_replaced_then_kept(lo):
    from moxi_loadout import stores

    kit.manifest(lo, '[user]\nagents = ["helper"]\n')
    install.install()
    toml = kit.codex_agents() / "helper.toml"
    with open(toml, "a", encoding="utf-8") as fh:
        fh.write("# my edit\n")
    edited = toml.read_bytes()
    out = install.status()
    assert states(out)[("agent", "helper", "codex", "user")] == "modified"
    assert [r.replaceable for r in out.rows if r.state == "modified"] == [True]
    assert out.kept == []
    assert install.install().code == 3
    assert "# my edit" in toml.read_text()
    out = install.install(replace=["agent:helper@codex"])
    assert out.code == 0
    assert "# my edit" not in toml.read_text()
    [kept] = out.kept
    generated = stores.generated_dir() / "codex" / "helper.toml"
    assert (kept["target"], kept["how"], kept["id"]) == (str(generated), "copied",
                                                         "agent:helper@codex")
    assert Path(kept["copy"]).read_bytes() == edited


def test_a_dropped_agents_generated_file_is_deleted_unless_edited(lo):
    from moxi_loadout import stores

    kit.manifest(lo, '[user]\nagents = ["helper"]\n')
    assert install.install().code == 0
    generated = stores.generated_dir() / "codex" / "helper.toml"
    assert generated.is_file()
    kit.manifest(lo, '[user]\n')
    assert install.install().code == 0
    assert not os.path.lexists(generated)
    # An edited one stays, with a note naming it.
    kit.manifest(lo, '[user]\nagents = ["helper"]\n')
    assert install.install().code == 0
    with open(generated, "a", encoding="utf-8") as fh:
        fh.write("# my edit\n")
    kit.manifest(lo, '[user]\n')
    out = install.install()
    assert out.code == 0, out.text()
    assert "# my edit" in generated.read_text()
    assert any(str(generated) in n and "delete it" in n for n in out.notes), out.notes
    # A carried agent's generated file is still in use, so it stays.
    kit.manifest(lo, '[user]\nagents = ["helper"]\n')
    assert install.install(replace=["agent:helper@codex"]).code == 0
    os.unlink(lo / "agents" / "helper.md")
    assert install.install().code == 1
    assert generated.is_file()


def test_a_changed_source_regenerates_the_agent(lo):
    kit.manifest(lo, '[user]\nagents = ["helper"]\n')
    install.install()
    kit.agent(lo / "agents" / "helper.md", "helper", "model: opus\n")
    assert states(install.status())[("agent", "helper", "codex", "user")] == "stale"
    install.install()
    assert states(install.status())[("agent", "helper", "codex", "user")] == "ok"


def test_a_key_section_refused_carries_its_items(lo, tmp_path, site):
    root = project(tmp_path)
    kit.manifest(lo, '[project.app]\nskills = ["review"]\n')
    assert install.install(resolver=lambda key: Resolution(str(root))).code == 0
    target = root / ".claude" / "skills" / "review"
    assert kit.placed(target)
    for code in (3, 75):
        refused = install.install(resolver=lambda key: Resolution(None, "no", code))
        assert refused.code == code
        assert kit.placed(target)
    # Without projects, a key section is refused and nothing is removed.
    absent = install.install()
    assert absent.code == 3 and kit.placed(target)
    assert any("moxi install projects" in line for line in absent.text())
    # Outside the scope, the refusal is a line, not a code.
    kit.manifest(lo, '[user]\nskills = ["review"]\n[project.app]\nskills = ["review"]\n')
    assert install.install(Scope(user=True)).code == 0


def test_a_lost_ledger_still_removes_nothing_it_carries(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    install.install()
    import shutil

    from moxi_loadout import stores

    os.unlink(stores.ledger_path())

    shutil.rmtree(lo / "skills" / "review")
    assert install.install().code == 1
    assert os.path.lexists(kit.claude() / "review")


def test_a_moved_loadout_dir_updates_its_links(lo, tmp_path):
    import shutil

    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    install.install()
    moved = tmp_path / "moved"
    shutil.move(lo, moved)
    kit.configure(moved)
    assert install.install().code == 0
    assert kit.placed(kit.claude() / "review", moved / "skills" / "review")


def _windows_long_form(monkeypatch, link: Path, target: Path) -> None:
    """`link` reads as a link to `target` in the form Windows reads one back, `\\\\?\\C:\\...`
    (a plain file stands in for it, so the row runs where the account can't make one)."""
    monkeypatch.setattr(os.path, "islink",
                        lambda p: os.path.normcase(os.fspath(p)) == os.path.normcase(str(link)))
    monkeypatch.setattr(os, "readlink", lambda p: "\\\\?\\" + str(target))


@pytest.mark.skipif(os.name != "nt", reason="the long form is how Windows reads a link back")
def test_a_link_into_the_moved_tree_reads_as_moved_in_windows_long_form(tmp_path,
                                                                        monkeypatch):
    from types import SimpleNamespace

    old = tmp_path / "loadout"
    link = tmp_path / "links" / "review"
    link.parent.mkdir()
    link.write_text("")
    _windows_long_form(monkeypatch, link, old / "skills" / "review")
    step = SimpleNamespace(target=link, occupant=link)
    assert install._moved(step, {str(link)}, old)


@pytest.mark.skipif(os.name != "nt", reason="the long form is how Windows reads a link back")
def test_a_link_in_windows_long_form_chains_to_the_path_it_was_made_from(tmp_path,
                                                                        monkeypatch):
    target = tmp_path / "global.md"
    target.write_text("x")
    link = tmp_path / "CLAUDE.md"
    link.write_text("")
    _windows_long_form(monkeypatch, link, target)
    assert planner._chain(link) == [str(target)]


def test_entries_written_before_an_interrupt_are_recorded(lo, monkeypatch):
    import json

    kit.manifest(lo, '[user]\nhooks = ["guard"]\n'
                     '[hook.guard]\nevent = "PreToolUse"\ncommand = "guard-cmd"\n')
    real = planner.build

    def stopped(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(planner, "build", stopped)
    with pytest.raises(KeyboardInterrupt):
        install.install()
    monkeypatch.setattr(planner, "build", real)
    settings = kit.claude("settings.json")
    assert "guard-cmd" in settings.read_text()
    records = [r for r in ledger.read()["entries"] if r.get("kind") == "hook"]
    assert records and not any(r.get("prior") for r in records)
    # So the next install keeps it as loadout's, and a drop removes it.
    assert install.install().code == 0
    kit.manifest(lo, '[user]\n')
    assert install.install().code == 0
    assert "guard-cmd" not in json.dumps(json.loads(settings.read_text()))


def test_dry_run_changes_nothing(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    out = install.install(dry_run=True)
    assert out.code == 0
    assert not os.path.lexists(kit.claude() / "review")


def test_install_exits_75_while_another_holds_the_install_lock(lo, monkeypatch):
    import subprocess
    import sys
    import time

    from moxi_loadout import stores

    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    lock = stores.install_lock()
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys, time\nfrom moxi import files\n"
         "with files.lock(sys.argv[1]):\n    print('held', flush=True)\n    time.sleep(30)",
         str(lock)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        monkeypatch.setattr(install, "INSTALL_WAIT", 0)
        start = time.monotonic()
        assert install.install().code == 75
        assert time.monotonic() - start < 10
    finally:
        holder.kill()
        holder.wait()
    assert not os.path.lexists(kit.claude() / "review")


def test_exit_codes_combine_error_over_refused_over_busy():
    assert planner.exit_code([75, 3, 1]) == 1
    assert planner.exit_code([75, 3]) == 3
    assert planner.exit_code([75, 0]) == 75
    assert planner.exit_code([]) == 0

"""install and status: the whole manifest planned, the steps in scope applied, and every
other placement left as it was."""

from __future__ import annotations

import os

import loadout_kit as kit
import pytest

from moxi_loadout import install, ledger, planner
from moxi_loadout.planner import Resolution, Scope


@pytest.fixture
def lo(folder):
    kit.configure(folder)
    kit.skill(folder / "skills", "review")
    kit.agent(folder / "agents" / "helper.md", "helper", "tools: Read, Grep\n")
    return folder


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
    assert os.readlink(kit.claude() / "review") == str(lo / "skills" / "review")
    assert os.readlink(kit.codex_skills() / "review") == str(lo / "skills" / "review")
    assert os.readlink(kit.claude("agents") / "helper.md") == str(lo / "agents" / "helper.md")
    # The Codex agent is generated, and goes to the Codex home's agents folder.
    toml = kit.codex_agents() / "helper.toml"
    from moxi_loadout import stores

    assert toml.is_symlink()
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
    assert (kit.claude() / "review").is_symlink()
    assert not os.path.lexists(kit.codex_skills() / "review")
    assert not os.path.lexists(kit.codex_agents() / "helper.toml")
    assert states(out)[("agent", "helper", "codex", "user")] == "off"


def test_install_user_leaves_project_placements_as_they_are(lo, tmp_path):
    root = project(tmp_path)
    kit.skill(lo / "skills", "deploy")
    kit.manifest(lo, f'[user]\nskills = ["review"]\n[project."{root}"]\nskills = ["deploy"]\n')
    out = install.install(Scope(user=True))
    assert out.code == 0
    assert (kit.claude() / "review").is_symlink()
    assert not os.path.lexists(root / ".claude" / "skills" / "deploy")
    out = install.install(Scope(projects=(str(root),)))
    assert out.code == 0
    assert (root / ".claude" / "skills" / "deploy").is_symlink()
    assert (root / ".agents" / "skills" / "deploy").is_symlink()


def test_one_items_scope_applies_only_its_steps(lo):
    kit.skill(lo / "skills", "other")
    kit.manifest(lo, '[user]\nskills = ["review", "other"]\n')
    out = install.install(Scope(item=("skill", "review")))
    assert out.code == 0
    assert (kit.claude() / "review").is_symlink()
    assert not os.path.lexists(kit.claude() / "other")


def test_a_dropped_item_is_removed_and_a_remove_outside_the_scope_waits(lo, tmp_path):
    kit.skill(lo / "skills", "other")
    kit.manifest(lo, '[user]\nskills = ["review", "other"]\n')
    install.install()
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    root = project(tmp_path)
    kit.manifest(lo, f'[user]\nskills = ["review"]\n[project."{root}"]\n')
    assert install.install(Scope(projects=(str(root),))).code == 0
    assert (kit.claude() / "other").is_symlink()
    status = install.status()
    assert any(r.state == "stale" and r.name == "other" for r in status.rows)
    assert install.install().code == 0
    assert not os.path.lexists(kit.claude() / "other")
    assert (kit.claude() / "review").is_symlink()
    assert {r["item"] for r in ledger.read()["placed"].values()} == {"review"}


def test_a_disabled_harness_removes_only_that_placement(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    install.install()
    kit.manifest(lo, '[user]\nskills = ["review"]\ndisabled = ["skill:review@codex"]\n')
    assert install.install().code == 0
    assert not os.path.lexists(kit.codex_skills() / "review")
    assert (kit.claude() / "review").is_symlink()


@pytest.mark.parametrize("break_it", ["delete", "clash", "name"])
def test_a_bad_source_is_carried_with_its_placement_kept(lo, tmp_path, break_it):
    import shutil

    root = project(tmp_path)
    kit.skill(lo / "skills", "deploy")
    kit.manifest(lo, f'[user]\nskills = ["review"]\n[project."{root}"]\nskills = ["deploy"]\n')
    assert install.install().code == 0
    if break_it == "delete":
        shutil.rmtree(lo / "skills" / "review")
        broken = ("skill", "review", "claude", "user")
    elif break_it == "clash":
        os.unlink(root / ".claude" / "skills" / "deploy")
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
    kit.manifest(lo, f'[project."{root}"]\nskills = ["own"]\nagents = ["lead"]\n')
    out = install.install()
    assert out.code == 0, out.text()
    assert held.is_dir() and not held.is_symlink()
    link = root / ".agents" / "skills" / "own"
    assert os.readlink(link) == os.path.join("..", "..", ".claude", "skills", "own")
    assert (root / ".codex" / "agents" / "lead.toml").is_symlink()
    assert set(states(install.status()).values()) == {"ok"}


def test_a_project_item_from_the_folder_copied_is_not_the_projects_own(lo, tmp_path):
    from moxi import config

    config.set("moxi", "skills.form", "copy", by="test")
    root = project(tmp_path)
    kit.manifest(lo, f'[project."{root}"]\nskills = ["review"]\nagents = ["helper"]\n')
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


def test_two_project_sections_place_one_name_each(lo, tmp_path):
    a, b = project(tmp_path, "a"), project(tmp_path, "b")
    kit.manifest(lo, f'[project."{a}"]\nskills = ["review"]\n'
                     f'[project."{b}"]\nskills = ["review"]\n')
    assert install.install().code == 0
    assert (a / ".claude" / "skills" / "review").is_symlink()
    assert (b / ".claude" / "skills" / "review").is_symlink()


def test_an_occupant_is_blocked_until_replace_moves_it_aside(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    kit.claude().mkdir(parents=True)
    kit.skill(kit.claude(), "review", "mine\n")
    out = install.install()
    assert out.code == 3
    assert states(out)[("skill", "review", "claude", "user")] == "blocked"
    assert install.install(replace=["skill:review@claude"]).code == 0
    assert (kit.claude() / "review").is_symlink()
    # A --replace that names nothing refused is refused.
    out = install.install(replace=["skill:nothing@claude"])
    assert out.code == 3 and any("nothing of loadout's" in line for line in out.lines)


def test_a_hand_edited_generated_agent_is_left_until_replaced(lo):
    kit.manifest(lo, '[user]\nagents = ["helper"]\n')
    install.install()
    toml = kit.codex_agents() / "helper.toml"
    with open(toml, "a", encoding="utf-8") as f:
        f.write("# my edit\n")
    out = install.status()
    assert states(out)[("agent", "helper", "codex", "user")] == "modified"
    assert install.install().code == 3
    assert "# my edit" in toml.read_text()
    assert install.install(replace=["agent:helper@codex"]).code == 0
    assert "# my edit" not in toml.read_text()


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
    assert target.is_symlink()
    for code in (3, 75):
        refused = install.install(resolver=lambda key: Resolution(None, "no", code))
        assert refused.code == code
        assert target.is_symlink()
    # Without projects, a key section is refused and nothing is removed.
    absent = install.install()
    assert absent.code == 3 and target.is_symlink()
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


def test_a_moved_folder_updates_its_links(lo, tmp_path):
    import shutil

    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    install.install()
    moved = tmp_path / "moved"
    shutil.move(lo, moved)
    kit.configure(moved)
    assert install.install().code == 0
    assert os.readlink(kit.claude() / "review") == str(moved / "skills" / "review")


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

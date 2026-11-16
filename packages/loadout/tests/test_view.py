"""The view's facts about each row of `status`: how loadout places it, whether its
placement was edited where it stands, what the Codex rules file holds, whose entry it
was first, the sections under which install applies it, and whether install's ignore
file covers it."""

from __future__ import annotations

import os
import subprocess

import loadout_kit as kit
import pytest

from moxi_loadout import harness, install, stores, view
from moxi_loadout.planner import Resolution, Scope

GLOBS = ("packages/loadout/src/moxi_loadout/view.py",
         "packages/loadout/src/moxi_loadout/install.py",
         "packages/loadout/tests/test_view.py")

HOOK = '''
[hook.guard-rm]
event = "PreToolUse"
matcher = "Bash"
command = "python3 {source}/hooks/guard_rm.py"
'''
PERMS = '''
[permissions.git-reads]
allow = [{ shell = ["git", "status"] }]
[permissions.ls-reads]
allow = [{ shell = ["ls"] }]
'''


@pytest.fixture
def lo(loadout_dir):
    kit.configure(loadout_dir)
    kit.skill(loadout_dir / "skills", "review")
    kit.agent(loadout_dir / "agents" / "helper.md", "helper")
    return loadout_dir


def rows(**kw) -> dict:
    """Each row of `status` with the view's facts, by (kind, name, harness, scope)."""
    done = install.status_plan(**kw)
    return {(r.kind, r.name, r.harness, r.scope): {**r.as_json(), **f}
            for r, f in zip(done.outcome.rows, view.row_facts(done))}


def project(tmp_path, name="app"):
    root = tmp_path / name
    root.mkdir()
    return root


def test_each_row_comes_with_its_facts_and_the_rows_are_statuss(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    done = install.status_plan()
    assert [r.as_json() for r in done.outcome.rows] == [
        r.as_json() for r in install.status().rows]
    facts = view.row_facts(done)
    assert len(facts) == len(done.outcome.rows) == len(done.sources)
    assert all(set(f) == {"form", "edited", "covers", "prior", "applies", "ignores"}
               for f in facts)


# -- form -----------------------------------------------------------------------------------

def test_form_says_how_loadout_places_each_item(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\nagents = ["helper"]\nhooks = ["guard-rm"]\n'
                     + HOOK)
    found = rows()
    assert found[("skill", "review", "claude", "user")]["form"] == "link"
    assert found[("agent", "helper", "claude", "user")]["form"] == "link"
    assert found[("agent", "helper", "codex", "user")]["form"] == "generated"
    assert found[("hook", "guard-rm", "claude", "user")]["form"] == "entry"


def test_form_is_copy_where_skills_are_copied(lo):
    from moxi import config

    config.set_value("moxi", "skills.form", "copy", by="test")
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    assert rows()[("skill", "review", "claude", "user")]["form"] == "copy"


def test_a_removal_keeps_the_form_it_was_placed_in(lo):
    kit.skill(lo / "skills", "other")
    kit.manifest(lo, '[user]\nskills = ["review", "other"]\n')
    assert install.install().code == 0
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    gone = rows()[("skill", "other", "claude", "user")]
    assert (gone["state"], gone["form"]) == ("stale", "link")


# -- edited ---------------------------------------------------------------------------------

def test_an_edited_copy_reads_edited(lo):
    from moxi import config

    config.set_value("moxi", "skills.form", "copy", by="test")
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    assert install.install().code == 0
    (kit.claude() / "review" / "SKILL.md").write_text("mine\n")
    row = rows()[("skill", "review", "claude", "user")]
    assert (row["state"], row["form"], row["edited"]) == ("blocked", "copy", True)


def test_a_skill_saved_over_loadouts_link_reads_edited(lo):
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    assert install.install().code == 0
    target = kit.claude() / "review"
    target.unlink()
    kit.skill(kit.claude(), "review", "saved over\n")
    row = rows()[("skill", "review", "claude", "user")]
    assert (row["state"], row["form"], row["edited"]) == ("blocked", "link", True)


def test_a_link_pointing_elsewhere_and_a_strangers_skill_are_not_edited(lo, tmp_path):
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    assert install.install().code == 0
    elsewhere = kit.skill(tmp_path / "elsewhere", "review")
    target = kit.claude() / "review"
    target.unlink()
    target.symlink_to(elsewhere)
    row = rows()[("skill", "review", "claude", "user")]
    assert (row["state"], row["edited"]) == ("blocked", False)
    # A folder that was never loadout's is in the way, not edited.
    kit.skill(lo / "skills", "fresh")
    kit.skill(kit.claude(), "fresh", "theirs\n")
    kit.manifest(lo, '[user]\nskills = ["review", "fresh"]\n')
    row = rows()[("skill", "fresh", "claude", "user")]
    assert (row["state"], row["edited"]) == ("blocked", False)
    assert not any(r["edited"] for r in rows().values() if r["state"] != "blocked")


# -- covers and prior -----------------------------------------------------------------------

def test_the_codex_rules_file_row_names_the_items_it_holds(lo):
    kit.manifest(lo, '[user]\npermissions = ["git-reads", "ls-reads"]\n' + PERMS)
    found = rows()
    assert found[("permissions", "*", "codex", "user")]["covers"] == ["git-reads",
                                                                        "ls-reads"]
    assert found[("permissions", "git-reads", "claude", "user")]["covers"] is None


def test_an_entry_the_user_had_first_reads_prior(lo):
    settings = harness.claude_home() / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"permissions": {"allow": ["Bash(git status:*)"]}}')
    kit.manifest(lo, '[user]\npermissions = ["git-reads"]\n'
                     '[permissions.git-reads]\nallow = [{ shell = ["git", "status"] }]\n')
    assert install.install().code == 0
    found = rows()
    assert found[("permissions", "git-reads", "claude", "user")]["prior"] is True
    assert found[("permissions", "*", "codex", "user")]["prior"] is False


# -- applies --------------------------------------------------------------------------------

def changed(before: dict, after: dict, key) -> bool:
    return key not in after or after[key]["state"] != before[key]["state"]


def test_a_section_row_is_applied_by_a_run_naming_its_section(lo, tmp_path):
    root = project(tmp_path)
    kit.manifest(lo, f'[user]\nskills = ["review"]\nhooks = ["guard-rm"]\n'
                     f'[project."{root}"]\nagents = ["helper"]\n' + HOOK)
    before = rows()
    user = ("skill", "review", "claude", "user")
    hook = ("hook", "guard-rm", "claude", "user")
    proj = ("agent", "helper", "claude", str(root))
    assert [before[k]["applies"] for k in (user, hook, proj)] == ["section"] * 3
    install.install(Scope(projects=(str(root),)))
    after = rows()
    assert changed(before, after, proj)
    assert not changed(before, after, user) and not changed(before, after, hook)
    install.install(Scope(user=True))
    after = rows()
    assert changed(before, after, user) and changed(before, after, hook)


def test_the_global_file_is_applied_only_by_a_whole_run(lo):
    (lo / "global.md").write_text("Be brief.\n")
    kit.manifest(lo, '[global]\nmode = "if-absent"\n[user]\nskills = ["review"]\n')
    before = rows()
    placed = ("global", "global", "claude", "user")
    assert before[placed]["applies"] == "whole"
    install.install(Scope(user=True))
    assert not changed(before, rows(), placed)
    install.install()
    assert changed(before, rows(), placed)


def test_the_managed_global_part_is_applied_only_by_a_whole_run(lo):
    (lo / "global.md").write_text("Be brief.\n")
    kit.manifest(lo, '[global]\nmode = "managed"\n[user]\nskills = ["review"]\n')
    before = rows()
    managed = [k for k, r in before.items() if k[0] == "global"]
    assert managed and {before[k]["form"] for k in managed} == {"entry"}
    assert {before[k]["applies"] for k in managed} == {"whole"}
    install.install(Scope(user=True))
    assert not any(changed(before, rows(), k) for k in managed)
    install.install()
    assert all(changed(before, rows(), k) for k in managed)


def test_a_removal_is_applied_under_its_section_or_by_a_whole_run(lo, tmp_path):
    root = project(tmp_path)
    kit.skill(lo / "skills", "other")
    kit.manifest(lo, f'[user]\nskills = ["review", "other"]\n'
                     f'[project."{root}"]\nskills = ["other"]\n')
    assert install.install().code == 0
    # Dropped from the user section, which the manifest still has: its section's run.
    # The project section dropped whole: only a whole run removes what it placed.
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    before = rows()
    user = ("skill", "other", "claude", "user")
    proj = ("skill", "other", "claude", str(root))
    assert (before[user]["applies"], before[proj]["applies"]) == ("section", "whole")
    install.install(Scope(user=True))
    after = rows()
    assert changed(before, after, user) and not changed(before, after, proj)
    install.install()
    assert changed(before, rows(), proj)


def test_a_removal_with_no_record_waits_for_every_section_to_resolve(lo, tmp_path, site):
    kit.skill(lo / "skills", "other")
    kit.manifest(lo, '[user]\nskills = ["review", "other"]\n')
    assert install.install().code == 0
    os.unlink(stores.ledger_path())
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    gone = ("skill", "other", "claude", None)
    found = rows()
    assert found[gone]["applies"] == "whole"
    # A key section refused: no run removes it, and no section's own row is applied.
    kit.manifest(lo, '[user]\nskills = ["review"]\n[project.app]\nskills = ["review"]\n')
    before = rows()
    assert before[gone]["applies"] is None
    assert before[("skill", "review", "claude", "app")]["applies"] is None
    for scope in (Scope(), Scope(user=True)):
        install.install(scope)
        assert not changed(before, rows(), gone)


def test_resolved_all_is_the_plans(lo, site):
    kit.manifest(lo, '[user]\nskills = ["review"]\n[project.app]\nskills = ["review"]\n')
    assert install.status_plan().plan.resolved_all is False
    assert install.status_plan(resolver=lambda key: Resolution(str(lo))).plan.resolved_all
    kit.manifest(lo, '[user]\nskills = ["review"]\n')
    assert install.status_plan().plan.resolved_all is True


# -- ignores --------------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_a_would_commit_row_install_covers_reads_ignores(lo, tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    kit.manifest(lo, f'[project."{root}"]\nskills = ["review"]\n')
    assert install.install().code == 0
    (root / ".claude" / "skills" / ".gitignore").unlink()
    row = rows()[("skill", "review", "claude", str(root))]
    assert (row["state"], row["ignores"]) == ("would-commit", True)
    assert not any(r["ignores"] for r in rows().values() if r["state"] != "would-commit")

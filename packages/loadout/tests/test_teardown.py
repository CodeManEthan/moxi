"""loadout's uninstall: everything it placed or wrote taken out, module skills from a
module's own yes handed back, or a refusal before anything is removed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import loadout_kit as kit
import pytest
from test_entries import FAKE_CLAUDE, FAKE_CODEX

from moxi_loadout import harness, harness_row, ignores, install, ledger, stores, teardown

HOOK = '''
[hook.guard-rm]
event = "PreToolUse"
matcher = "Bash"
command = "python3 {source}/hooks/guard_rm.py"
timeout = 10
'''
PERMS = '''
[permissions.git-reads]
allow = [{ shell = ["git", "status"] }]
'''
MCP = '''
[mcp.docs]
command = "uvx"
args = ["docs-mcp"]
'''
EVERYTHING = ('[user]\nskills = ["review"]\nagents = ["helper"]\nhooks = ["guard-rm"]\n'
              'permissions = ["git-reads"]\nmcp = ["docs"]\n[global]\nmode = "managed"\n'
              + HOOK + PERMS + MCP)
MINE = "# My rules\n"
#: The fake harness CLIs here and in the shared fence are POSIX scripts.
posix = pytest.mark.skipif(sys.platform == "win32",
                           reason="the fake harness CLIs are POSIX scripts")
pytestmark = posix


@pytest.fixture
def lo(folder, tmp_path, monkeypatch):
    """The loadout folder with a skill, an agent and a global text; harness CLIs that
    write the files they own; the opencode row added as SETUP adds it."""
    kit.configure(folder)
    kit.skill(folder / "skills", "review")
    kit.agent(folder / "agents" / "helper.md", "helper")
    (folder / "global.md").write_text("Be brief.\n")
    bin_dir = tmp_path / "fake-mcp-bin"
    bin_dir.mkdir()
    for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
        path = bin_dir / name
        path.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    harness_row.add(say=lambda line: None)
    return folder


def read(path: Path):
    return json.loads(path.read_text())


def settings() -> Path:
    return harness.claude_home() / "settings.json"


def said():
    out: list[str] = []
    return out, out.append


def installed(folder: Path, text: str = EVERYTHING) -> None:
    kit.manifest(folder, text)
    settings().parent.mkdir(parents=True, exist_ok=True)
    settings().write_text('{\n  "model": "opus"\n}\n')
    harness.global_file("claude").write_text(MINE)
    out = install.install()
    assert out.code == 0, out.text()


def refusal(capture) -> str:
    from moxi import modules

    with pytest.raises(modules.Refused) as exc:
        teardown.teardown(say=capture)
    return exc.value.reason


def test_a_kept_entry_running_from_the_folder_is_found_in_any_path_form():
    windows = "C:\\Users\\sample\\loadout"
    hook = {"kind": "hook", "written": {"matcher": "Bash", "hooks": [
        {"type": "command", "command": f"python {windows}\\hooks\\guard.py"}]}}
    assert teardown._runs_from(hook, [windows]) == windows
    mcp = {"kind": "mcp", "written": {"command": "uvx", "args": ["/srv/loadout/x"]}}
    assert teardown._runs_from(mcp, ["/srv/loadout"]) == "/srv/loadout"
    assert teardown._runs_from({"kind": "permission", "written": "/srv/loadout"},
                               ["/srv/loadout"]) is None


def test_the_announcement_names_the_teardown():
    from moxi import modules

    assert modules.load("loadout").teardown == "moxi_loadout.teardown:teardown"


def test_uninstall_takes_out_everything_loadout_wrote(lo):
    from moxi import place

    installed(lo)
    placed = [p.path for p in place.status("loadout")]
    assert placed and harness.rules_file().exists()
    lines, say = said()
    assert teardown.teardown(say=say) == 0, lines
    for path in placed:
        assert not os.path.lexists(path)
    assert place.status("loadout") == []
    assert read(settings()) == {"model": "opus"}
    assert read(harness.claude_json())["mcpServers"] == {}
    codex = tomllib.loads((harness.codex_home() / "config.toml").read_text())
    assert codex.get("mcp_servers", {}) == {}
    assert not read(harness.codex_home() / "hooks.json").get("hooks")
    assert not harness.rules_file().exists()
    assert harness.global_file("claude").read_text() == MINE
    assert not harness.global_file("codex").exists()
    opencode = harness.settings_file("opencode")
    assert not opencode.exists() or "instructions" not in read(opencode)
    assert "opencode" not in place.harnesses()[0]
    data = ledger.read()
    assert data["placed"] == {} and data["entries"] == []
    assert "loadout: removed the opencode row from the core's harness table" in lines
    # A second run finds nothing left to do.
    assert teardown.teardown(say=lambda line: None) == 0


def test_an_edited_entry_refuses_and_nothing_is_removed(lo):
    from moxi import place

    installed(lo)
    data = read(settings())
    data["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 60
    settings().write_text(json.dumps(data))
    generated = stores.generated_dir() / "codex" / "helper.toml"
    generated.write_text(generated.read_text() + "# mine\n")
    before = (settings().read_bytes(), stores.ledger_path().read_bytes(),
              harness.claude_json().read_bytes(), place.status("loadout"))
    reason = refusal(lambda line: None)
    assert "nothing was removed" in reason
    assert "hook guard-rm for claude" in reason
    assert "--replace hook:guard-rm@claude" in reason
    assert f"{generated} was edited by hand" in reason
    assert "--replace agent:helper@codex" in reason
    assert (settings().read_bytes(), stores.ledger_path().read_bytes(),
            harness.claude_json().read_bytes(), place.status("loadout")) == before
    # The fix the line names, then uninstall goes through.
    assert install.install(replace=["hook:guard-rm@claude", "agent:helper@codex"]).code == 0
    assert teardown.teardown(say=lambda line: None) == 0
    assert read(settings()) == {"model": "opus"}


def test_an_edited_generated_file_no_agent_uses_is_named_to_delete(lo):
    kit.manifest(lo, '[user]\nagents = ["helper"]\n')
    assert install.install().code == 0
    generated = stores.generated_dir() / "codex" / "helper.toml"
    with open(generated, "a", encoding="utf-8") as f:
        f.write("# mine\n")
    kit.manifest(lo, '[user]\n')
    assert install.install().code == 0
    reason = refusal(lambda line: None)
    assert f"{generated} was edited by hand" in reason
    assert "--replace" not in reason and "delete it" in reason
    generated.unlink()
    assert teardown.teardown(say=lambda line: None) == 0


def test_an_entry_the_user_had_stays_and_one_running_from_the_folder_is_named(lo):
    kit.manifest(lo, '[user]\nhooks = ["guard-rm"]\n' + HOOK)
    group = {"matcher": "Bash", "hooks": [{"type": "command", "timeout": 10,
                                           "command": f"python3 {lo}/hooks/guard_rm.py"}]}
    settings().parent.mkdir(parents=True)
    settings().write_text(json.dumps({"hooks": {"PreToolUse": [group]}}))
    assert install.install().code == 0
    assert {r["prior"] for r in ledger.read()["entries"] if r["harness"] == "claude"
            and r["kind"] == "hook"} == {True}
    lines, say = said()
    assert teardown.teardown(say=say) == 0, lines
    assert read(settings())["hooks"]["PreToolUse"] == [group]
    assert any(line.startswith("loadout: hook guard-rm for claude stays in")
               and line.endswith(f"it runs from {lo}") for line in lines)
    assert ledger.read()["entries"] == []


def test_the_opencode_row_stays_while_skills_harnesses_names_it(lo):
    from moxi import config, place

    config.set("moxi", "skills.harnesses", ["claude", "opencode"], by="test")
    lines, say = said()
    assert teardown.teardown(say=say) == 0
    assert "opencode" in place.harnesses()[0]
    assert any("skills.harnesses names opencode" in line for line in lines)
    assert harness_row.record_of(ledger.read()) is None


def test_an_edited_opencode_row_stays_as_the_users(lo):
    from moxi import config, place

    config.remove_item("moxi", "harnesses", harness_row.wanted(), by="test")
    mine = dict(harness_row.wanted(), agents="~/oc-agents")
    config.add_item("moxi", "harnesses", mine, by="test")
    lines, say = said()
    assert teardown.teardown(say=say) == 0
    assert str(place.harnesses()[0]["opencode"].agents).endswith("oc-agents")
    assert any("was edited since loadout added it" in line for line in lines)


def test_an_opencode_row_the_user_had_first_stays(folder):
    from moxi import config, place

    kit.configure(folder)
    config.add_item("moxi", "harnesses", harness_row.wanted(), by="test")
    harness_row.add(say=lambda line: None)
    assert harness_row.record_of(ledger.read())["prior"] is True
    assert teardown.teardown(say=lambda line: None) == 0
    assert "opencode" in place.harnesses()[0]


def core_config() -> dict:
    from moxi import paths

    path = Path(paths.core().config_file())
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@pytest.mark.parametrize("had_key", [False, True])
def test_a_harnesses_key_setup_made_is_unset_at_uninstall(folder, had_key):
    from moxi import config

    kit.configure(folder)
    default = [{"name": "claude", "skills": "", "agents": ""},
               {"name": "codex", "skills": "", "agents": ""}]
    if had_key:
        config.set("moxi", "harnesses", default, by="test")
    harness_row.add(say=lambda line: None)
    assert [r["name"] for r in core_config()["harnesses"]] == ["claude", "codex", "opencode"]
    lines, say = said()
    assert teardown.teardown(say=say) == 0, lines
    if had_key:
        assert core_config()["harnesses"] == default
    else:
        assert "harnesses" not in core_config()


def test_a_harnesses_key_changed_since_setup_is_kept(folder):
    from moxi import config

    kit.configure(folder)
    harness_row.add(say=lambda line: None)
    config.add_item("moxi", "harnesses", {"name": "other", "skills": "~/o", "agents": ""},
                    by="test")
    assert teardown.teardown(say=lambda line: None) == 0
    assert [r["name"] for r in core_config()["harnesses"]] == ["claude", "codex", "other"]


def test_a_missing_cli_refuses_while_its_server_is_there(lo, monkeypatch):
    import shutil

    installed(lo, '[user]\nmcp = ["docs"]\n' + MCP)
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n: None if n == "claude" else real(n))
    reason = refusal(lambda line: None)
    assert "no claude on PATH" in reason and "remove the server by hand" in reason
    assert "docs" in read(harness.claude_json())["mcpServers"]
    # Removed by hand: nothing is left for the CLI to do, and uninstall goes through.
    data = read(harness.claude_json())
    del data["mcpServers"]["docs"]
    harness.claude_json().write_text(json.dumps(data))
    assert teardown.teardown(say=lambda line: None) == 0
    assert ledger.read()["entries"] == []


def test_a_harness_file_that_doesnt_read_refuses(lo):
    installed(lo, '[user]\nhooks = ["guard-rm"]\n' + HOOK)
    settings().write_text("// mine\n" + settings().read_text())
    reason = refusal(lambda line: None)
    assert str(settings()) in reason and "--skip-teardown" in reason


def test_a_failed_removal_keeps_what_is_left_for_a_second_run(lo, monkeypatch):
    from moxi import proc

    installed(lo, '[user]\nskills = ["review"]\nmcp = ["docs"]\n' + MCP)
    real = proc.run

    def stuck(argv, **kwargs):
        if "remove" in argv and os.path.basename(argv[0]) == "claude":
            return subprocess.CompletedProcess(argv, 1, b"", b"busy")
        return real(argv, **kwargs)

    monkeypatch.setattr(proc, "run", stuck)
    result = teardown.teardown(say=lambda line: None)
    assert isinstance(result, str)
    assert "still in" in result and "run moxi uninstall loadout again" in result
    assert [r["harness"] for r in ledger.read()["entries"]] == ["claude"]
    assert not os.path.lexists(kit.claude() / "review")
    monkeypatch.setattr(proc, "run", real)
    assert teardown.teardown(say=lambda line: None) == 0
    assert read(harness.claude_json())["mcpServers"] == {}
    assert ledger.read()["entries"] == []


# -- module skills ------------------------------------------------------------------------

def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: A module skill.\n---\nUse it.\n"


@pytest.fixture
def demo(site, folder):
    """A module `demo` shipping skills moxi-demo and moxi-other, and the loadout folder."""
    package = site.module("demo", SKILLS="'skills'", files={
        "skills/moxi-demo/SKILL.md": skill_md("moxi-demo"),
        "skills/moxi-other/SKILL.md": skill_md("moxi-other")})
    kit.configure(folder)
    return site.root / package / "skills"


def both_kinds(skills, folder):
    """moxi-demo placed by the module after a yes and taken over; moxi-other placed from
    the manifest as the module and taken over seeded."""
    from moxi import place
    from moxi.place import Item

    place.apply(place.plan("demo", [Item(skills / "moxi-demo", "claude", "moxi-demo")]),
                say=lambda line: None)
    assert install.install().code == 0
    kit.manifest(folder, '[user]\nskills = ["demo/moxi-demo", "demo/moxi-other"]\n'
                         '[skill."demo/moxi-demo"]\nharnesses = ["claude"]\n'
                         '[skill."demo/moxi-other"]\nharnesses = ["claude"]\n')
    assert install.install().code == 0
    assert kit.handed("demo")[str(kit.claude() / "moxi-other")]["seeded"] is True


def demo_plan(skills, name):
    from moxi import place
    from moxi.place import Item

    return place.plan("demo", [Item(skills / name, "claude", name)])[0]


def test_a_skill_from_the_modules_yes_goes_back_and_a_seeded_one_is_removed(demo, folder):
    both_kinds(demo, folder)
    lines, say = said()
    assert teardown.teardown(say=say) == 0, lines
    yes, seeded = kit.claude() / "moxi-demo", kit.claude() / "moxi-other"
    assert kit.owner_of(yes) == "demo" and os.path.islink(yes)
    assert str(yes) not in kit.handed("demo")
    assert "loadout: handed skill moxi-demo for claude back to demo" in lines
    assert not any("was seeded" in line for line in lines)
    assert not os.path.lexists(seeded) and kit.owner_of(seeded) is None
    # The module's next install keeps its own and asks about the seeded one, which plans
    # `add` and is not counted as the module's own.
    assert demo_plan(demo, "moxi-demo").action == "keep"
    assert demo_plan(demo, "moxi-other").action == "add"
    assert kit.handed("demo")[str(seeded)]["seeded"] is True


def test_a_lost_ledger_still_ends_right(demo, folder):
    both_kinds(demo, folder)
    stores.ledger_path().unlink()
    lines, say = said()
    assert teardown.teardown(say=say) == 0, lines
    assert kit.owner_of(kit.claude() / "moxi-demo") == "demo"
    # With no record to say it was seeded, the core refuses its hand-back with a line.
    assert "loadout: moxi-other was seeded; loadout removes it" in lines
    assert not os.path.lexists(kit.claude() / "moxi-other")
    assert kit.owner_of(kit.claude() / "moxi-other") is None


# -- project trees ------------------------------------------------------------------------

GLOBS = ("packages/loadout/src/moxi_loadout/teardown.py",
         "packages/loadout/src/moxi_loadout/ignores.py",
         "packages/loadout/tests/test_teardown.py")


@pytest.fixture
def app(folder, tmp_path):
    kit.configure(folder)
    kit.skill(folder / "skills", "review")
    root = tmp_path / "app"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    kit.manifest(folder, f'[project."{root}"]\nskills = ["review"]\n')
    assert install.install().code == 0
    return root


def untracked(root) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "status", "--porcelain",
                          "--untracked-files=all"], capture_output=True, text=True,
                         check=True).stdout
    return [line[3:] for line in out.splitlines()]


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_a_project_tree_is_left_as_it_was(app):
    folder = app / ".claude" / "skills"
    assert ignores.state(folder / ".gitignore") == "ours"
    assert teardown.teardown(say=lambda line: None) == 0
    assert not os.path.lexists(folder / "review")
    assert not os.path.lexists(folder / ".gitignore")
    assert not os.path.lexists(app / ".agents" / "skills" / ".gitignore")
    assert untracked(app) == []
    assert ignores.records(ledger.read()) == {}


@pytest.mark.slow
@pytest.mark.touches(*GLOBS)
def test_an_edited_ignore_file_refuses(app):
    path = app / ".claude" / "skills" / ".gitignore"
    path.write_text(path.read_text() + "/mine\n")
    reason = refusal(lambda line: None)
    assert f"{path} was edited since loadout wrote it" in reason
    assert os.path.islink(app / ".claude" / "skills" / "review")

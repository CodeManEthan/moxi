"""The harness writers: hooks, permission rules and MCP servers per harness, each entry
found by its identity, written once per file, and removed only when loadout added it."""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import loadout_kit as kit
import pytest

from moxi_loadout import entries, harness, install, ledger, stores
from moxi_loadout.planner import Scope

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
deny = [{ shell = ["rm", "-rf"] }]
claude = { allow = ["WebFetch(domain:docs.python.org)"] }
'''

FAKE_CLAUDE = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
home = os.environ.get("CLAUDE_CONFIG_DIR")
path = Path(home) / ".claude.json" if home else Path.home() / ".claude.json"
data = json.loads(path.read_text()) if path.exists() else {}
servers = data.setdefault("mcpServers", {})
if args[:2] == ["mcp", "add-json"]:
    value = json.loads(args[-1])
    value.setdefault("type", "stdio")
    servers[args[-2]] = value
elif args[:2] == ["mcp", "remove"]:
    servers.pop(args[-1], None)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2))
'''

FAKE_CODEX = r'''
import json, os, sys, tomllib
from pathlib import Path
args = sys.argv[1:]
path = Path(os.environ["CODEX_HOME"]) / "config.toml"
data = tomllib.loads(path.read_text()) if path.exists() else {}
servers = data.setdefault("mcp_servers", {})
if args[:2] == ["mcp", "add"]:
    name, rest, server, env = args[2], args[3:], {}, {}
    while rest:
        flag = rest.pop(0)
        if flag == "--env":
            k, _, v = rest.pop(0).partition("=")
            env[k] = v
        elif flag == "--url":
            server["url"] = rest.pop(0)
        elif flag == "--":
            server["command"], server["args"] = rest[0], rest[1:]
            rest = []
    if env:
        server["env"] = env
    servers[name] = server
elif args[:2] == ["mcp", "remove"]:
    servers.pop(args[2], None)
out = []
for name, s in servers.items():
    out.append(f"[mcp_servers.{name}]")
    out += [f"{k} = {json.dumps(v)}" for k, v in s.items() if k != "env"]
    if "env" in s:
        out.append(f"[mcp_servers.{name}.env]")
        out += [f"{k} = {json.dumps(v)}" for k, v in s["env"].items()]
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("\n".join(out) + "\n")
'''


@pytest.fixture
def lo(folder, tmp_path, monkeypatch):
    kit.configure(folder)
    bin_dir = tmp_path / "fake-mcp-bin"
    bin_dir.mkdir()
    for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
        path = bin_dir / name
        path.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    return folder


def write(folder: Path, text: str) -> None:
    kit.manifest(folder, textwrap.dedent(text))


def settings() -> Path:
    return harness.claude_home() / "settings.json"


def read(path: Path):
    return json.loads(path.read_text())


def opencode_row() -> None:
    from moxi import config

    config.add_item("moxi", "harnesses", {"name": "opencode",
                                          "skills": "~/.config/opencode/skills",
                                          "agents": "~/.config/opencode/agents"}, by="test")


def states(outcome) -> dict:
    return {(r.kind, r.name, r.harness): r.state for r in outcome.rows}


def test_hooks_and_permissions_go_to_each_harness_one_write_per_file(lo, monkeypatch):
    write(lo, '[user]\nhooks = ["guard-rm"]\npermissions = ["git-reads"]\n' + HOOK + PERMS)
    settings().parent.mkdir(parents=True)
    settings().write_text('{\n    "model": "opus",\n    "permissions": {"allow": ["Read"]}\n}\n')
    writes = []
    real = harness.write
    monkeypatch.setattr(harness, "write", lambda doc, *a, **k: (writes.append(doc.path),
                                                                 real(doc, *a, **k))[1])
    out = install.install()
    assert out.code == 0, out.text()
    data = read(settings())
    assert data["model"] == "opus"
    group = data["hooks"]["PreToolUse"][0]
    assert group["matcher"] == "Bash"
    assert group["hooks"][0] == {"type": "command", "timeout": 10,
                                 "command": f"python3 {lo}/hooks/guard_rm.py"}
    assert data["permissions"]["allow"] == ["Read", "Bash(git status:*)",
                                            "WebFetch(domain:docs.python.org)"]
    assert data["permissions"]["deny"] == ["Bash(rm -rf:*)"]
    assert settings().read_text().startswith('{\n    "model"')   # four spaces kept
    codex_hooks = read(harness.codex_home() / "hooks.json")
    assert codex_hooks["hooks"]["PreToolUse"][0] == group
    rules = harness.rules_file().read_text()
    assert 'prefix_rule(pattern=["git", "status"], decision="allow")' in rules
    assert 'prefix_rule(pattern=["rm", "-rf"], decision="forbidden")' in rules
    assert writes.count(settings()) == 1
    assert any("trust" in n for n in out.notes)
    records = ledger.read()["entries"]
    assert {r["kind"] for r in records} == {"hook", "permission", "rules"}
    # A second run finds everything in place and writes nothing.
    writes.clear()
    again = install.install()
    assert again.code == 0 and writes == []
    assert set(install.status().rows and [r.state for r in install.status().rows]) == {"ok"}


def test_a_raised_timeout_is_modified_and_replace_rewrites_it(lo):
    write(lo, '[user]\nhooks = ["guard-rm"]\n' + HOOK)
    install.install()
    data = read(settings())
    data["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 60
    settings().write_text(json.dumps(data))
    out = install.install()
    assert out.code == 3
    assert states(out)[("hook", "guard-rm", "claude")] == "modified"
    assert read(settings())["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] == 60
    assert len(read(settings())["hooks"]["PreToolUse"]) == 1   # never a second group
    out = install.install(replace=["hook:guard-rm@claude"])
    assert out.code == 0, out.text()
    assert read(settings())["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] == 10


def test_an_edited_command_is_modified(lo):
    write(lo, '[user]\nhooks = ["guard-rm"]\n' + HOOK)
    install.install()
    data = read(settings())
    data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "true"
    settings().write_text(json.dumps(data))
    assert states(install.status())[("hook", "guard-rm", "claude")] == "modified"


def test_a_permission_string_the_user_had_is_kept_as_theirs(lo):
    settings().parent.mkdir(parents=True)
    settings().write_text('{"permissions": {"allow": ["Bash(git status:*)"]}}')
    write(lo, '[user]\npermissions = ["git-reads"]\n[permissions.git-reads]\n'
              'allow = [{ shell = ["git", "status"] }]\n[permissions.git-reads.claude]\n'
              'allow = []\n')
    assert install.install().code == 0
    record = [r for r in ledger.read()["entries"] if r["kind"] == "permission"][0]
    assert record["prior"] is True
    write(lo, '[user]\npermissions = []\n')
    assert install.install().code == 0
    assert read(settings())["permissions"]["allow"] == ["Bash(git status:*)"]
    assert [r for r in ledger.read()["entries"] if r["kind"] == "permission"] == []


def test_one_string_from_two_items_goes_with_its_last_record(lo):
    two = ('[permissions.a]\nallow = [{ shell = ["ls"] }]\n'
           '[permissions.b]\nallow = [{ shell = ["ls"] }]\n')
    write(lo, '[user]\npermissions = ["a", "b"]\n' + two)
    assert install.install().code == 0
    assert read(settings())["permissions"]["allow"] == ["Bash(ls:*)"]
    write(lo, '[user]\npermissions = ["b"]\n' + two)
    install.install()
    assert read(settings())["permissions"]["allow"] == ["Bash(ls:*)"]
    write(lo, '[user]\npermissions = []\n' + two)
    install.install()
    assert "permissions" not in read(settings())


def test_disabled_and_dropped_items_remove_only_theirs(lo):
    write(lo, '[user]\nhooks = ["guard-rm"]\npermissions = ["git-reads"]\n' + HOOK + PERMS)
    install.install()
    write(lo, '[user]\nhooks = ["guard-rm"]\npermissions = ["git-reads"]\n'
              'disabled = ["hook:guard-rm@codex"]\n' + HOOK + PERMS)
    out = install.install()
    assert out.code == 0, out.text()
    assert states(out)[("hook", "guard-rm", "codex")] == "off"
    assert "hooks" not in read(harness.codex_home() / "hooks.json")
    assert read(settings())["hooks"]["PreToolUse"]
    write(lo, '[user]\nhooks = ["guard-rm"]\n' + HOOK + PERMS)
    install.install()
    assert "permissions" not in read(settings())
    assert not harness.rules_file().exists()
    assert read(settings())["hooks"]["PreToolUse"]


def test_a_lost_ledger_recovers_every_equal_entry(lo):
    write(lo, '[user]\nhooks = ["guard-rm"]\n' + HOOK)
    install.install()
    stores.ledger_path().unlink()
    out = install.install()
    assert out.code == 0
    assert len(read(settings())["hooks"]["PreToolUse"]) == 1


def test_json_with_comments_is_refused_and_its_entries_stand(lo):
    settings().parent.mkdir(parents=True)
    settings().write_text('{\n  // mine\n  "model": "opus"\n}\n')
    before = settings().read_bytes()
    write(lo, '[user]\nhooks = ["guard-rm"]\n' + HOOK)
    out = install.install()
    assert out.code == 3
    assert settings().read_bytes() == before
    assert any("not plain JSON" in line for line in out.lines)
    assert read(harness.codex_home() / "hooks.json")["hooks"]   # the other harness goes on


def test_a_file_changed_while_planning_is_not_written(lo, monkeypatch):
    write(lo, '[user]\nhooks = ["guard-rm"]\n' + HOOK)
    settings().parent.mkdir(parents=True)
    settings().write_text("{}")
    real = entries._edit

    def edit(doc, a):
        if doc.path == settings():
            settings().write_text('{"theirs": 1}')
        real(doc, a)

    monkeypatch.setattr(entries, "_edit", edit)
    out = install.install()
    assert out.code == 1
    assert read(settings()) == {"theirs": 1}
    assert any("changed while planning" in line for line in out.lines)
    assert not [r for r in ledger.read()["entries"] if r["harness"] == "claude"]


def test_codex_rules_file_edited_by_hand_is_modified(lo):
    write(lo, '[user]\npermissions = ["git-reads"]\n' + PERMS)
    install.install()
    harness.rules_file().write_text(harness.rules_file().read_text() + "# mine\n")
    out = install.install()
    assert out.code == 3
    assert states(out)[("permissions", "*", "codex")] == "modified"


@pytest.mark.parametrize("given", ["permissions:git-reads@codex", "permissions:git-reads@*"])
def test_replace_by_any_permissions_id_takes_the_edited_rules_file_back(lo, given):
    write(lo, '[user]\npermissions = ["git-reads"]\n' + PERMS)
    install.install()
    wrote = harness.rules_file().read_text()
    harness.rules_file().write_text(wrote + "# mine\n")
    row = [r for r in install.status().rows if r.name == "*"][0]
    assert "--replace permissions:git-reads@codex" in row.reason
    out = install.install(replace=[given])
    assert out.code == 0, out.text()
    assert harness.rules_file().read_text() == wrote
    # Once no item writes rules, an edited file is removed by the same form.
    harness.rules_file().write_text(wrote + "# mine\n")
    write(lo, '[user]\npermissions = []\n' + PERMS)
    row = [r for r in install.status().rows if r.name == "*"][0]
    assert row.state == "modified" and "--replace permissions:" in row.reason
    assert install.install(replace=[given]).code == 0
    assert not harness.rules_file().exists()


def test_mcp_through_the_clis_read_back(lo):
    write(lo, '[user]\nmcp = ["docs"]\n[mcp.docs]\ncommand = "uvx"\n'
              'args = ["docs-mcp", "--root", "{source}/docs"]\nenv = { TOKEN = "t" }\n')
    out = install.install()
    assert out.code == 0, out.text()
    claude = read(harness.claude_json())["mcpServers"]["docs"]
    assert claude["args"] == ["docs-mcp", "--root", f"{lo}/docs"]
    import tomllib

    codex = tomllib.loads((harness.codex_home() / "config.toml").read_text())
    assert codex["mcp_servers"]["docs"]["env"] == {"TOKEN": "t"}
    # The value the CLI normalised (a `type` added) reads ok.
    assert set(r.state for r in install.status().rows) == {"ok"}
    rec = [r for r in ledger.read()["entries"] if r["harness"] == "claude"][0]
    assert rec["written"]["type"] == "stdio" and "type" in rec["sent"]
    write(lo, '[user]\nmcp = []\n')
    assert install.install().code == 0
    assert read(harness.claude_json())["mcpServers"] == {}


def test_a_missing_cli_refuses_that_harness_mcp(lo, monkeypatch):
    import shutil

    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n: None if n == "codex" else real(n))
    write(lo, '[user]\nmcp = ["docs"]\n[mcp.docs]\ncommand = "uvx"\n')
    out = install.install()
    assert out.code == 3
    assert states(out)[("mcp", "docs", "codex")] == "blocked"
    assert states(out)[("mcp", "docs", "claude")] == "ok"


def test_opencode_permissions_and_mcp_go_in_opencode_json(lo):
    opencode_row()
    write(lo, '[user]\npermissions = ["git-reads"]\nmcp = ["docs"]\nhooks = ["guard-rm"]\n'
              + HOOK + PERMS + '[permissions.git-reads.opencode]\nedit = "ask"\n'
              '[mcp.docs]\nurl = "https://example.invalid/mcp"\n'
              'harnesses = ["opencode"]\n')
    out = install.install()
    assert out.code == 0, out.text()
    data = read(harness.settings_file("opencode"))
    assert data["permission"] == {"bash": {"git status*": "allow", "rm -rf*": "deny"},
                                  "edit": "ask"}
    assert data["mcp"]["docs"] == {"type": "remote", "url": "https://example.invalid/mcp"}
    assert any("not for opencode" in n for n in out.notes)
    data["permission"]["edit"] = "allow"
    harness.settings_file("opencode").write_text(json.dumps(data))
    assert states(install.status())[("permissions", "git-reads", "opencode")] == "modified"


def test_status_says_which_harness_facts_no_run_has_confirmed(lo):
    write(lo, '[user]\nhooks = ["guard-rm"]\n' + HOOK + 'harnesses = ["claude"]\n')
    assert not [n for n in install.status().notes if "not yet confirmed" in n]
    opencode_row()
    write(lo, '[user]\nhooks = ["guard-rm"]\npermissions = ["git-reads"]\n' + HOOK + PERMS)
    notes = [n for n in install.status().notes if "not yet confirmed by a run" in n]
    assert len(notes) == 2
    assert any("hooks.json" in n and "rules" in n and "trust" in n for n in notes)
    assert any("opencode.json" in n for n in notes)


def test_another_value_under_the_key_is_blocked_then_taken_over(lo):
    opencode_row()
    path = harness.settings_file("opencode")
    path.parent.mkdir(parents=True)
    path.write_text('{"mcp": {"docs": {"type": "local", "command": ["mine"]}}}')
    write(lo, '[user]\nmcp = ["docs"]\n[mcp.docs]\nurl = "https://example.invalid/m"\n'
              'harnesses = ["opencode"]\n')
    out = install.install()
    assert out.code == 3 and states(out)[("mcp", "docs", "opencode")] == "blocked"
    out = install.install(replace=["mcp:docs@opencode"])
    assert out.code == 0
    assert read(path)["mcp"]["docs"]["url"] == "https://example.invalid/m"
    backups = list((stores.backups_dir() / "opencode").iterdir())
    assert backups and stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert b"mine" in backups[0].read_bytes()


def test_backups_are_pruned_to_keep(lo):
    from moxi import config

    config.set("loadout", "backups.keep", "2", by="test")
    for n in range(4):
        cmd = f"echo {n}"
        write(lo, f'[user]\nhooks = ["h"]\n[hook.h]\nevent = "Stop"\ncommand = "{cmd}"\n'
                  'harnesses = ["claude"]\n')
        install.install()
    assert len(list((stores.backups_dir() / "claude").iterdir())) == 2


def test_scoped_runs_apply_only_their_entries(lo, tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    write(lo, f'[user]\nhooks = ["guard-rm"]\npermissions = ["git-reads"]\n'
              f'[project."{root}"]\n' + HOOK + PERMS)
    assert install.install(Scope(projects=(str(root),))).code == 0
    assert not settings().exists()
    assert install.install(Scope(item=("hook", "guard-rm"))).code == 0
    assert "permissions" not in read(settings())
    assert install.install(Scope(user=True)).code == 0
    assert read(settings())["permissions"]


def test_no_lock_file_lands_in_a_harness_home(lo):
    write(lo, '[user]\nhooks = ["guard-rm"]\nmcp = ["docs"]\n' + HOOK +
              '[mcp.docs]\ncommand = "uvx"\n')
    install.install()
    for home in (harness.claude_home(), harness.codex_home()):
        assert not [p for p in home.rglob("*.lock")]


SHAPES = {
    # OpenCode's published config takes a whole permission as one action, and a tool's
    # permission as one action in place of an object of patterns.
    "opencode-permission-one-action": ("opencode", '{"permission": "ask"}'),
    "opencode-bash-one-action": ("opencode", '{"permission": {"bash": "ask"}}'),
    # Claude's hooks hold a list of groups per event, never an object.
    "claude-hook-event-object": ("claude", '{"hooks": {"PreToolUse": {}}}'),
}


@pytest.mark.parametrize("shape", SHAPES)
def test_a_file_shaped_otherwise_is_refused_for_its_harness_alone(lo, shape):
    opencode_row()
    harness_name, text = SHAPES[shape]
    path = harness.settings_file(harness_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    before = path.read_bytes()
    kit.skill(lo / "skills", "review")
    write(lo, '[user]\nskills = ["review"]\nhooks = ["guard-rm"]\n'
              'permissions = ["git-reads"]\n' + HOOK + PERMS)
    out = install.install()
    assert out.code == 3, out.text()
    assert path.read_bytes() == before
    said = [line for line in out.lines if str(path) in line]
    assert len(said) == 1 and "loadout leaves it" in said[0]
    assert {r.state for r in out.rows if r.harness == harness_name
            and r.kind in ("hook", "permissions")} == {"blocked"}
    # Every other harness's entries are written and recorded, and the skill is placed.
    recorded = {(r["harness"], r["kind"]) for r in ledger.read()["entries"]}
    other = "codex" if harness_name == "claude" else "claude"
    assert (other, "hook") in recorded
    assert not any(h == harness_name for h, _ in recorded)
    assert (kit.claude() / "review").exists()
    # Once the file reads as loadout expects, the next install writes it.
    path.write_text("{}")
    assert install.install().code == 0


TWO = ('[permissions.a]\nallow = [{ shell = ["git", "status"] }]\nharnesses = ["claude"]\n'
       '[permissions.b]\nallow = [{ shell = ["git", "status"] }]\nharnesses = ["claude"]\n')
SHARED = "Bash(git status:*)"


def allowed() -> list:
    return read(settings()).get("permissions", {}).get("allow", [])


def recorded_items() -> set:
    return {r["item"] for r in ledger.read()["entries"] if r["kind"] == "permission"}


def test_a_shared_entry_added_alone_is_recorded_and_outlives_its_first_item(lo):
    write(lo, '[user]\npermissions = ["a"]\n' + TWO)
    assert install.install().code == 0
    # Adding b runs b's scope alone: b's entry is a's, already there, and b keeps a record.
    write(lo, '[user]\npermissions = ["a", "b"]\n' + TWO)
    assert install.install(Scope(item=("permissions", "b"))).code == 0
    assert recorded_items() == {"permissions:a", "permissions:b"}
    # Removing a runs a's scope alone, and b still wants the entry.
    write(lo, '[user]\npermissions = ["b"]\n' + TWO)
    assert install.install(Scope(item=("permissions", "a"))).code == 0
    assert allowed() == [SHARED]
    assert install.status().code == 0
    write(lo, '[user]\npermissions = []\n' + TWO)
    assert install.install().code == 0
    assert "permissions" not in read(settings())


def test_a_shared_entry_whose_leader_is_pending_is_written_by_its_own_scope(lo):
    write(lo, '[user]\npermissions = ["a"]\n' + TWO)
    install.install()
    settings().write_text("{}")   # a's entry is gone: a would add it again
    write(lo, '[user]\npermissions = ["a", "b"]\n' + TWO)
    assert install.install(Scope(item=("permissions", "b"))).code == 0
    assert allowed() == [SHARED]
    assert "permissions:b" in recorded_items()
    assert install.status().code == 0


@pytest.mark.parametrize("how", ["remove", "disable"])
def test_a_scoped_run_never_takes_an_entry_another_listed_item_wants(lo, how):
    write(lo, '[user]\npermissions = ["a"]\n' + TWO)
    install.install()
    # b is listed by hand and has never run; a goes, by its own scope.
    gone = 'disabled = ["permissions:a@claude"]\n' if how == "disable" else ""
    listed = '"a", "b"' if how == "disable" else '"b"'
    write(lo, f'[user]\npermissions = [{listed}]\n{gone}' + TWO)
    assert install.install(Scope(item=("permissions", "a"))).code == 0
    assert allowed() == [SHARED]
    assert install.status().code == 0
    # A whole run hands the entry to b, which then takes it out with its last record.
    assert install.install().code == 0
    assert recorded_items() == {"permissions:b"}
    write(lo, f'[user]\npermissions = []\n' + TWO)
    assert install.install().code == 0
    assert "permissions" not in read(settings())


def test_a_gone_hook_is_not_taken_for_an_edit_of_another_items_group(lo):
    hooks = ('[hook.a]\nevent = "PreToolUse"\nmatcher = "Bash"\ncommand = "echo a"\n'
             'harnesses = ["claude"]\n'
             '[hook.b]\nevent = "PreToolUse"\nmatcher = "Bash"\ncommand = "echo b"\n'
             'harnesses = ["claude"]\n')
    write(lo, '[user]\nhooks = ["a", "b"]\n' + hooks)
    assert install.install().code == 0

    def drop_a():
        data = read(settings())
        data["hooks"]["PreToolUse"] = [g for g in data["hooks"]["PreToolUse"]
                                       if g["hooks"][0]["command"] != "echo a"]
        settings().write_text(json.dumps(data))

    drop_a()
    assert states(install.status())[("hook", "a", "claude")] == "missing"
    assert install.install().code == 0
    assert len(read(settings())["hooks"]["PreToolUse"]) == 2
    drop_a()
    write(lo, '[user]\nhooks = ["b"]\n' + hooks)
    assert install.install().code == 0
    assert [r["item"] for r in ledger.read()["entries"]] == ["hook:b"]

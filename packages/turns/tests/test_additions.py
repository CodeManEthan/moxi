"""What a launch adds to its harness: turns' own additions, the contributors' group, the
tool-permission rendering, the doctor's rows, and the hosts' one call."""

from __future__ import annotations

import pytest

import moxi_turns as turns
from moxi import doctor as moxi_doctor
from moxi_turns import additions, codex, opencode
from moxi_turns.additions import Contribution, LaunchAdditions


class Entry:
    """A stand-in for one entry of the group: `target` is what loading it gives, or an
    exception to raise."""

    def __init__(self, name, target):
        self.name = name
        self._target = target

    def load(self):
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


@pytest.fixture(autouse=True)
def roots(turns_env):
    """Every test keeps its runtime and config under the test's own roots."""
    return turns_env


@pytest.fixture
def group(monkeypatch):
    """The group `launch_additions` and the doctor read, set by the test."""
    holder = {"entries": []}
    real = additions.load

    def load(entries=None):
        return real(holder["entries"] if entries is None else entries)

    monkeypatch.setattr(additions, "load", load)
    return holder


def contributes(answer):
    seen = []

    def additions_fn(request):
        seen.append(request)
        return answer(request) if callable(answer) else answer

    additions_fn.seen = seen
    return additions_fn


def adds(harness, **kw):
    kw.setdefault("cwd", "/work")
    kw.setdefault("env", {"HOME": "/home/someone"})
    return turns.launch_additions(harness, **kw)


# -- turns' own additions --------------------------------------------------------------


def test_the_public_api_has_the_call_and_not_the_plugin_dirs():
    assert "launch_additions" in turns.__all__ and "claude_plugin_dirs" not in turns.__all__
    assert not hasattr(turns, "claude_plugin_dirs")
    assert turns.LaunchAdditions is LaunchAdditions and turns.Contribution is Contribution


def test_claude_gets_both_plugin_dirs_after_endpoint_json_is_written(group, claude_root):
    got = adds("claude")
    runner = (claude_root / "moxi-turns").resolve()
    gate = (claude_root / "moxi-turns-gate").resolve()
    assert got.argv == ("--plugin-dir", str(runner), "--plugin-dir", str(gate))
    assert (runner / "endpoint.json").is_file()
    assert got.env == {} and got.options == {} and got.notes == ()


def test_claude_raises_when_the_plugin_cannot_be_set_up(group, claude_root):
    import shutil

    shutil.rmtree(claude_root / "moxi-turns")
    with pytest.raises(turns.TurnsError):
        adds("claude")


@pytest.mark.parametrize("harness", ["codex", "opencode"])
def test_the_hosts_get_no_argv_and_no_options(group, harness, claude_root):
    got = adds(harness)
    assert got == LaunchAdditions(harness)
    assert not (claude_root / "moxi-turns" / "endpoint.json").exists()


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_commands_render_the_grant_in_a_fixed_order(group, harness):
    assert adds(harness, commands=("compact", "exit", "compact")).env == {
        "MOXI_TURNS_COMMANDS": "exit,compact"}
    assert "MOXI_TURNS_COMMANDS" not in adds(harness, commands=()).env


def test_a_command_or_harness_turns_does_not_know_is_refused(group):
    with pytest.raises(ValueError, match="stop"):
        adds("codex", commands=("exit", "stop"))
    with pytest.raises(TypeError):
        adds("codex", commands="exit")
    with pytest.raises(ValueError, match="gemini"):
        adds("gemini")


# -- contributions ---------------------------------------------------------------------


def test_a_contributor_gets_the_launch_read_only(group):
    fn = contributes(None)
    group["entries"] = [Entry("mymod", fn)]
    got = adds("codex", cwd="/work/here", env={"A": "1"}, session_id="s-9")
    assert got.notes == ()
    (request,) = fn.seen
    assert (request.harness, request.cwd) == ("codex", "/work/here")
    assert request.session_id == "s-9"
    assert dict(request.env) == {"A": "1"}
    with pytest.raises(TypeError):
        request.env["B"] = "2"


def test_claude_rules_become_allowed_tools_words_after_the_plugin_dirs(group):
    group["entries"] = [
        Entry("zeta", contributes(Contribution(tool_permissions={
            "claude": ("Bash(git diff:*)",)}))),
        Entry("alpha", contributes({"tool_permissions": {
            "claude": ["mcp__alpha__read", "Read"], "opencode": ("read",)}})),
    ]
    got = adds("claude")
    assert got.argv[:4][0::2] == ("--plugin-dir", "--plugin-dir")
    assert got.argv[4:] == ("--allowedTools=mcp__alpha__read", "--allowedTools=Read",
                            "--allowedTools=Bash(git diff:*)")
    assert got.options == {} and got.notes == ()


def test_opencode_rules_become_one_permission_fragment(group):
    group["entries"] = [
        Entry("alpha", contributes(Contribution(tool_permissions={
            "opencode": ("bash git diff*", "bash git status", "webfetch")}))),
        Entry("beta", contributes(Contribution(tool_permissions={
            "opencode": ("bash ls *", "edit"), "claude": ("Edit",)}))),
        Entry("gamma", contributes(Contribution(tool_permissions={
            "opencode": ("webfetch https://example.org/*",)}))),
    ]
    got = adds("opencode")
    assert got.argv == () and got.notes == ()
    assert got.options == {"permission": {
        "bash": {"git diff*": "allow", "git status": "allow", "ls *": "allow"},
        "webfetch": "allow", "edit": "allow"}}


def test_codex_rules_skip_the_contribution_with_a_note_and_a_doctor_row(group):
    group["entries"] = [Entry("mymod", contributes(Contribution(tool_permissions={
        "codex": ("git diff",), "claude": ("Bash(git diff:*)",)})))]
    got = adds("codex")
    assert got == LaunchAdditions("codex", notes=got.notes)
    (note,) = got.notes
    assert "mymod" in note and "Codex" in note
    # The same contribution still goes in on Claude.
    assert adds("claude").argv[-1] == "--allowedTools=Bash(git diff:*)"
    row = {r.name: r for r in additions.rows()}["additions mymod"]
    assert row.state == "warn" and "codex:" in row.detail and "adds to claude" in row.detail


def test_mcp_servers_skip_the_whole_contribution(group):
    group["entries"] = [Entry("mymod", contributes(Contribution(
        mcp_servers={"mymod": {"command": "x"}},
        tool_permissions={"claude": ("Read",)})))]
    got = adds("claude")
    assert not any(word.startswith("--allowedTools") for word in got.argv)
    (note,) = got.notes
    assert "MCP" in note and "mymod" in note
    assert {r.name: r for r in additions.rows()}["additions mymod"].state == "warn"


@pytest.mark.parametrize("permissions", [
    {"claude": ("",)},
    {"claude": ("Read", "   ")},
    {"claude": ("Read", 3)},
    {"claude": "Read"},
    {"gemini": ("Read",)},
])
def test_a_bad_rule_refuses_the_contribution_on_every_harness(group, permissions):
    group["entries"] = [
        Entry("bad", contributes(Contribution(tool_permissions={
            **permissions, "opencode": ("edit",)}))),
        Entry("good", contributes(Contribution(tool_permissions={"opencode": ("read",)}))),
    ]
    got = adds("opencode")
    assert got.options == {"permission": {"read": "allow"}}
    (note,) = got.notes
    assert note.startswith("additions of bad skipped")
    row = {r.name: r for r in additions.rows()}["additions bad"]
    assert row.state == "fail"


@pytest.mark.parametrize("answer", [
    {"tool_permissions": {}, "hooks": ["x"]},
    ("claude", "Read"),
    Contribution(tool_permissions=("Read",)),
])
def test_a_malformed_contribution_is_skipped(group, answer):
    group["entries"] = [Entry("mymod", contributes(answer))]
    (note,) = adds("codex").notes
    assert "mymod" in note


def test_a_contributor_that_raises_costs_only_its_own_additions(group):
    def boom(request):
        raise RuntimeError("no luck")

    group["entries"] = [Entry("bad", boom),
                        Entry("good", contributes(Contribution(tool_permissions={
                            "claude": ("Read",)})))]
    got = adds("claude")
    assert got.argv[-1] == "--allowedTools=Read"
    (note,) = got.notes
    assert "bad" in note and "no luck" in note
    row = {r.name: r for r in additions.rows()}["additions bad"]
    assert row.state == "fail" and "no luck" in row.detail


def test_entries_that_do_not_load_are_notes_and_failed_rows(group):
    group["entries"] = [Entry("broken", ImportError("no module named broken")),
                        Entry("notfn", {"tool_permissions": {}}),
                        Entry("twice", contributes(None)),
                        Entry("twice", contributes(None)),
                        Entry("Not A Name", contributes(None)),
                        Entry("fine", contributes(None))]
    got = adds("codex")
    assert len(got.notes) == 4
    assert all(note.startswith("additions of ") and " are off: " in note
               for note in got.notes)
    rows = {r.name: r for r in additions.rows()}
    for name in ("broken", "notfn", "twice", "Not A Name"):
        assert rows[f"additions {name}"].state == "fail", name
    assert rows["additions fine"].state == "ok"
    assert rows["additions fine"].detail == "adds nothing here"


def test_the_doctor_carries_the_additions_rows(group):
    from moxi_turns import doctor

    group["entries"] = [Entry("broken", ImportError("gone"))]
    rows = [r for r in doctor.check() if r.name == "additions broken"]
    assert len(rows) == 1 and isinstance(rows[0], moxi_doctor.Row)
    assert rows[0].module == "turns" and rows[0].state == "fail"


def test_the_group_reads_the_installed_entry_points(monkeypatch):
    from moxi import modules

    asked = []

    def extensions(name):
        asked.append(name)
        return [Entry("mymod", contributes(None))]

    monkeypatch.setattr(modules, "extensions", extensions)
    loaded = additions.load()
    assert asked == ["moxi.turns.additions"]
    assert [c.module for c in loaded.contributors] == ["mymod"]


# -- the hosts -------------------------------------------------------------------------


class _Stop(Exception):
    """Raised where a host would start its endpoint: what it was built with is all a
    test needs."""


@pytest.fixture
def calls(monkeypatch):
    """Counts the hosts' calls of `launch_additions`, answering with `answer`."""
    holder = {"count": 0, "asked": [], "answer": None}

    def launch_additions(harness, **kw):
        holder["count"] += 1
        holder["asked"].append((harness, kw))
        return holder["answer"] or LaunchAdditions(harness)

    monkeypatch.setattr(additions, "launch_additions", launch_additions)
    return holder


@pytest.fixture
def built(monkeypatch):
    """The harness each host built, its launch stopped before anything starts."""
    holder = {}

    class Endpoint:
        def __init__(self, harness, **kw):
            holder["harness"] = harness
            raise _Stop

    def run(harness):
        holder["harness"] = harness
        raise _Stop

    monkeypatch.setattr(codex, "Endpoint", Endpoint)
    monkeypatch.setattr(opencode, "_run", run)
    return holder


def _launch(harness, tmp_path, **kw):
    host = codex if harness == "codex" else opencode
    with pytest.raises(_Stop):
        host.launch(tmp_path, **kw)


@pytest.mark.parametrize("harness", ["codex", "opencode"])
def test_a_host_asks_once_without_additions(harness, tmp_path, calls, built):
    calls["answer"] = LaunchAdditions(harness, env={"MOXI_TURNS_COMMANDS": "compact"},
                                      notes=("additions of x skipped: why",))
    _launch(harness, tmp_path, env={"MINE": "1"})
    assert calls["count"] == 1
    (asked_harness, kw), = calls["asked"]
    assert asked_harness == harness and kw["cwd"] == tmp_path.resolve()
    assert kw["env"]["MINE"] == "1"
    assert built["harness"].env["MOXI_TURNS_COMMANDS"] == "compact"


@pytest.mark.parametrize("harness", ["codex", "opencode"])
def test_a_host_never_asks_when_given_additions(harness, tmp_path, calls, built):
    given = LaunchAdditions(harness, env={"MOXI_TURNS_COMMANDS": "exit", "MINE": "theirs"})
    _launch(harness, tmp_path, env={"MINE": "mine"}, additions=given)
    assert calls["count"] == 0
    env = built["harness"].env
    assert env["MOXI_TURNS_COMMANDS"] == "exit" and env["MINE"] == "theirs"


def test_opencode_options_join_under_the_callers(tmp_path, calls, built):
    given = LaunchAdditions("opencode", options={"permission": {
        "edit": "allow", "bash": {"git diff*": "allow"}, "webfetch": "allow"}})
    mine = {"model": "p/m",
            "permission": {"external_directory": "allow", "webfetch": "deny"}}
    _launch("opencode", tmp_path, options=mine, additions=given)
    assert built["harness"].options == {"model": "p/m", "permission": {
        "external_directory": "allow", "webfetch": "deny", "edit": "allow",
        "bash": {"git diff*": "allow"}}}
    assert mine == {"model": "p/m", "permission": {"external_directory": "allow",
                                                   "webfetch": "deny"}}


def test_codex_options_join_under_the_callers(tmp_path, calls, built):
    given = LaunchAdditions("codex", options={"config": {"a": {"b": 1}, "c": 2}})
    _launch("codex", tmp_path, options={"sandbox": "read-only", "config": {"c": 3}},
            additions=given)
    assert built["harness"].options == {"sandbox": "read-only",
                                        "config": {"a": {"b": 1}, "c": 3}}


@pytest.mark.parametrize("harness,options", [("codex", {"threadId": "t"}),
                                             ("opencode", {"instructions": ["x"]})])
def test_a_key_turns_owns_in_the_additions_is_refused(harness, options, tmp_path, calls,
                                                     built):
    with pytest.raises(ValueError, match="turns owns"):
        (codex if harness == "codex" else opencode).launch(
            tmp_path, additions=LaunchAdditions(harness, options=options))
    assert "harness" not in built


def test_opencode_refuses_additions_naming_its_own_variables(tmp_path, calls, built):
    with pytest.raises(ValueError, match="OPENCODE_SERVER_PASSWORD"):
        opencode.launch(tmp_path, additions=LaunchAdditions(
            "opencode", env={"OPENCODE_SERVER_PASSWORD": "x"}))


@pytest.mark.parametrize("given,error", [
    (LaunchAdditions("claude"), ValueError),
    (LaunchAdditions("codex", argv=("--flag",)), ValueError),
    ({"env": {}}, TypeError),
])
def test_additions_a_host_cannot_apply_are_refused(given, error, tmp_path, calls, built):
    with pytest.raises(error):
        codex.launch(tmp_path, additions=given)
    assert calls["count"] == 0 and "harness" not in built

"""The CLI's verbs parse, and each one not built yet says so and exits 1."""

from __future__ import annotations

import json

import pytest

from moxi import exit as codes

from moxi_launcher import cli

PARSES = [
    (["run"], {"verb": "run", "seat": None, "project_preset": None, "rest": []}),
    (["run", "--seat", "planner"], {"seat": "planner"}),
    (["run", "--project-preset", "moxi", "--off-posture"],
     {"project_preset": "moxi", "off_posture": True}),
    (["run", "--model", "opus", "--seat", "planner", "--", "--verbose"],
     {"seat": "planner", "rest": ["--model", "opus", "--", "--verbose"]}),
    # Nothing is abbreviated: --project is the catalog's row, kept for it in rest.
    (["run", "--project", "moxi"], {"project_preset": None, "rest": ["--project", "moxi"]}),
    (["preview", "--off", "--seat", "s"], {"off_posture": False, "rest": ["--off"]}),
    (["preview", "--seat", "planner", "--json"], {"verb": "preview", "json": True}),
    (["log"], {"verb": "log", "limit": None, "before": None, "json": False}),
    (["log", "--limit", "5", "--before", "2026-10-30T00:00:00Z", "--json"],
     {"limit": 5, "before": "2026-10-30T00:00:00Z", "json": True}),
    (["presets"], {"verb": "presets", "action": "list", "name": None}),
    (["presets", "show", "planner"], {"action": "show", "name": "planner"}),
    (["presets", "check", "--json"], {"action": "check", "json": True}),
    (["presets", "--project", "moxi"], {"action": "list", "project": "moxi"}),
    (["catalog"], {"verb": "catalog", "harness": None, "json": False}),
    (["catalog", "codex", "--json"], {"harness": "codex", "json": True}),
    (["handoff", "dir"], {"verb": "handoff", "what": "dir"}),
    (["handoffs", "--key", "planner"], {"verb": "handoffs", "key": "planner"}),
    (["deliver", "r-1", "--pid", "4242"],
     {"verb": "deliver", "request_id": "r-1", "pid": 4242}),
]


@pytest.mark.parametrize(("argv", "expected"), PARSES, ids=[" ".join(a) for a, _ in PARSES])
def test_each_verb_parses(argv, expected):
    args = vars(cli.parse(argv))
    assert {key: args[key] for key in expected} == expected


def test_every_verb_has_a_handler():
    assert set(cli.VERBS) == {"run", "preview", "log", "presets", "catalog", "handoff",
                              "handoffs", "deliver"}


REFUSED = [
    [],
    ["nope"],
    ["run", "--seat", "a", "--project-preset", "b"],
    ["log", "--bogus"],
    ["log", "--limit", "many"],
    ["presets", "show"],
    ["presets", "list", "planner"],
    ["handoff"],
    ["handoff", "file"],
    ["deliver", "r-1"],
]


@pytest.mark.parametrize("argv", REFUSED, ids=[" ".join(a) or "(none)" for a in REFUSED])
def test_bad_arguments_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as raised:
        cli.parse(argv)
    assert raised.value.code == codes.USAGE


@pytest.mark.parametrize("argv", [["run"], ["preview"], ["log"], ["presets"],
                                  ["handoff", "dir"], ["handoffs"],
                                  ["deliver", "r-1", "--pid", "1"]],
                         ids=lambda a: a[0])
def test_a_verb_not_built_yet_says_so_and_exits_1(argv, capsys):
    assert cli.main(argv) == codes.ERROR
    assert capsys.readouterr().err == f"moxi launcher {argv[0]}: not built yet\n"


def test_catalog_lists_every_group_with_its_rows(capsys):
    assert cli.main(["catalog"]) == codes.OK
    out = capsys.readouterr().out
    groups = [line for line in out.splitlines() if not line.startswith(" ")]
    assert groups == ["launcher", "claude", "codex"]
    assert "--permission-mode" in out
    assert "set by the launcher" in out


def test_catalog_json_for_one_harness(capsys):
    assert cli.main(["catalog", "codex", "--json"]) == codes.OK
    data = json.loads(capsys.readouterr().out)
    assert list(data) == ["launcher", "codex"]
    rows = {row["name"]: row for row in data["codex"]}
    assert rows["approval"]["modes"] == {"interactive": "-a"}
    assert rows["cwd"]["owned"] is True and rows["cwd"]["option"] is None
    assert rows["sandbox"]["option"] == "--sandbox"


def test_catalog_of_an_unknown_harness_exits_2(capsys):
    assert cli.main(["catalog", "opencode"]) == codes.USAGE
    assert "no harness 'opencode'" in capsys.readouterr().err

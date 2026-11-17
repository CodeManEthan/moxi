"""What the catalog says to the browser's launch options: each row's group, subgroup and
label, the values that can hurt, the rows never filled from an earlier launch, and what a
row needs to be drawn or typed; and how the page server sends them for one install."""

from __future__ import annotations

import pytest
from test_page import catalog_answer

from moxi_launcher import catalog, pagehandlers, slots
from moxi_launcher.catalog import CatalogError

META = """
[catalog]
harness = "codex"
checked = "1.0.0"

[catalog.help.codex]
command = ["codex", "--help"]
modes = ["interactive"]

[catalog.help.exec]
command = ["codex", "exec", "--help"]
modes = ["exec"]
"""
ROW = '[x]\ntype = "string"\ndefault = ""\ndoc = "d"\nflag = "-x"\nmodes = ["interactive"]\n'
PICK = ROW + 'values = ["a", "b"]\n'
SWITCH = '[x]\ntype = "bool"\ndefault = false\ndoc = "d"\nflag = "-x"\nmodes = ["interactive"]\n'

#: Where each settable row is drawn: group, then subgroup, then the rows in it.
ASSIGNED = {
    ("model", None): {"harness", "model", "effort"},
    ("access", "Mode"): {"permission_mode", "sandbox", "approval"},
    ("access", "Reach"): {"add_dir", "trust_cwd"},
    ("tools", "Built-in"): {"tools", "allowed_tools", "disallowed_tools"},
    ("tools", "MCP"): {"mcp_config"},
    ("tools", "Web & browser"): {"search"},
    ("seat", "Role"): {"agent"},
    ("seat", "Comms"): {"identity", "scope", "director", "posture"},
    ("seat", "Instructions"): {"instructions", "instructions_file"},
    ("seat", "Name & notes"): {"name", "native_name", "notes_dir"},
    ("seat", None): {"handoff"},
    ("runs-in", "Remote"): {"remote_control"},
    ("lifecycle", "Context"): {"warn_at", "recycle_at_limit", "limit_at"},
    ("lifecycle", "Cache"): {"keep_warm", "recycle_on_expiry"},
    ("config", "Layers"): {"profile", "config"},
    ("config", "Pass-through"): {"extra_flags"},
    ("config", None): {"scratch"},
}
#: The settable rows with no group: another control of the page sets each.
ELSEWHERE = {"cwd", "project", "target", "slot", "brief", "brief_file"}
#: The rows never filled from an earlier launch.
NOT_CARRIED = {"cwd", "project", "scratch", "brief", "brief_file", "handoff", "target", "slot",
               "name"}
LABELS = {
    "harness": "Harness", "model": "Model", "effort": "Effort / reasoning",
    "cwd": "Folder", "project": "Project", "scratch": "Scratch folder",
    "identity": "Comms identity", "scope": "Comms scope", "director": "Directors",
    "posture": "Start posture", "instructions": "Standing instructions (inline)",
    "instructions_file": "Standing instructions (file)", "brief": "Brief",
    "brief_file": "Brief from a file", "handoff": "Handoff file as first turn",
    "target": "Where it runs", "slot": "Slot", "extra_flags": "Extra harness flags",
    "name": "Session name", "notes_dir": "Notes folder", "warn_at": "Warn at context mark",
    "keep_warm": "Keep warm", "recycle_on_expiry": "Recycle on cache expiry",
    "recycle_at_limit": "Recycle at limit", "limit_at": "Limit mark",
    "agent": "Agent role", "tools": "Built-in tool set", "allowed_tools": "Allowed tools",
    "disallowed_tools": "Disallowed tools", "permission_mode": "Permission mode",
    "add_dir": "Extra folders", "remote_control": "Remote Control",
    "native_name": "Native name", "mcp_config": "MCP servers for this launch",
    "sandbox": "Sandbox", "approval": "Approval", "profile": "Config profile",
    "search": "Web search", "config": "Config overrides key=value",
    "trust_cwd": "Trust the launch folder",
}


def parse(text: str):
    return catalog.parse_text(text + META, harness="codex", source="test.toml")["x"]


def every_row() -> list[catalog.Row]:
    return [*catalog.launcher(), *(row for harness in catalog.HARNESSES
                                   for row in catalog.load(harness))]


@pytest.fixture(autouse=True)
def fresh_handlers(monkeypatch):
    """Each test logs each failure class anew, and reads no module as installed unless it
    says so."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(slots, "links", lambda name: False)


def installed(monkeypatch, *names):
    monkeypatch.setattr(slots, "links", lambda name: name in names)


# --- the fields, parsed and refused -----------------------------------------------------------

def test_a_row_without_the_fields_has_no_place_and_carries():
    row = parse(ROW)
    assert (row.group, row.sub, row.label, row.typed_with) == (None, None, None, None)
    assert (row.danger, row.carry, row.shown_with) == ((), True, ())


def test_each_field_is_parsed():
    row = parse(PICK + 'group = "access"\nsub = "Mode"\nlabel = "The pick"\ndanger = ["b"]\n'
                'carry = false\nshown_with = ["comms", "projects"]\ntyped_with = "comms"\n')
    assert (row.group, row.sub, row.label) == ("access", "Mode", "The pick")
    assert (row.danger, row.carry) == (("b",), False)
    assert (row.shown_with, row.typed_with) == (("comms", "projects"), "comms")
    assert parse(ROW + 'shown_with = "comms"\n').shown_with == ("comms",)
    # A string row with no listed values takes any word as a value that can hurt.
    assert parse(ROW + 'danger = ["anything"]\n').danger == ("anything",)


BAD = [
    (ROW + 'group = "danger"\n', "has group 'danger'; the groups are model, access"),
    (ROW + "group = 3\n", "has group 3"),
    (ROW + 'sub = "Mode"\n', "has a sub but no group"),
    (ROW + 'group = "access"\nsub = ""\n', "has sub ''"),
    (ROW + 'group = "access"\nsub = 3\n', "has sub 3"),
    (ROW + 'label = " "\n', "has label ' '"),
    (ROW + 'label = "two\\nlines"\n', "has label"),
    (ROW + 'label = ["a"]\n', "has label"),
    (ROW + 'danger = "b"\n', "has danger 'b', which is not a list of distinct strings"),
    (ROW + "danger = []\n", "which is not a list of distinct strings"),
    (ROW + 'danger = ["b", "b"]\n', "which is not a list of distinct strings"),
    (ROW + "danger = [1]\n", "which is not a list of distinct strings"),
    (PICK + 'danger = ["c"]\n', "which names a value outside its values"),
    (SWITCH + 'danger = ["true"]\n', "has danger, which only an enum or a string row takes"),
    (ROW + 'carry = "no"\n', "has carry 'no', not a bool"),
    (ROW + 'shown_with = "Comms"\n', "has shown_with 'Comms', which is not a module name"),
    (ROW + "shown_with = []\n", "has shown_with \\[\\], which is not a module name or a list"),
    (ROW + 'shown_with = ["comms", "comms"]\n', "has shown_with"),
    (PICK + 'typed_with = ["comms"]\n', "has typed_with \\['comms'\\], which is not a module"),
    (PICK + 'typed_with = "Comms"\n', "has typed_with 'Comms', which is not a module name"),
    (ROW + 'typed_with = "comms"\n', "which only a string row with values takes"),
    (SWITCH + 'typed_with = "comms"\n', "which only a string row with values takes"),
]


@pytest.mark.parametrize(("text", "said"), BAD, ids=[f"{i}: {s}" for i, (_, s) in enumerate(BAD)])
def test_a_malformed_field_is_refused(text, said):
    with pytest.raises(CatalogError, match=said) as raised:
        parse(text)
    assert (raised.value.source, raised.value.row) == ("test.toml", "x")


def test_the_fields_are_catalog_fields_and_the_groups_are_in_drawn_order():
    assert set(catalog.PANEL_FIELDS) <= set(catalog.FIELDS)
    assert catalog.PANEL_FIELDS == ("group", "sub", "label", "danger", "carry", "shown_with",
                                    "typed_with")
    assert catalog.GROUPS == ("model", "access", "tools", "seat", "runs-in", "lifecycle",
                              "config")


# --- the shipped assignment -------------------------------------------------------------------

def test_every_settable_row_has_its_group_or_is_set_by_another_control():
    placed: dict[tuple, set] = {}
    for row in every_row():
        if not row.settable:
            continue
        if row.group is None:
            assert row.name in ELSEWHERE and row.sub is None, row.name
        else:
            assert row.name not in ELSEWHERE, row.name
            placed.setdefault((row.group, row.sub), set()).add(row.name)
    # A row to come may be missing from a group; none may sit where the table lacks it.
    assert placed == ASSIGNED


def test_a_row_the_launcher_sets_itself_takes_none_of_the_fields():
    owned = [row for row in every_row() if not row.settable]
    assert owned
    for row in owned:
        assert (row.group, row.sub, row.label, row.typed_with) == (None,) * 4, row.name
        assert (row.danger, row.carry, row.shown_with) == ((), True, ()), row.name


def test_every_settable_row_has_its_label():
    labels = {row.name: row.label for row in every_row() if row.settable}
    assert labels == LABELS
    # A row both harnesses have is called the same by both.
    for row in every_row():
        assert not row.settable or row.label == LABELS[row.name], (row.harness, row.name)


def test_only_the_two_widest_grants_are_values_that_can_hurt():
    assert {(row.harness, row.name): row.danger for row in every_row() if row.danger} == {
        ("claude", "permission_mode"): ("bypassPermissions",),
        ("codex", "sandbox"): ("danger-full-access",)}


def test_only_the_rows_another_control_owns_are_never_filled_from_an_earlier_launch():
    assert {row.name for row in every_row() if not row.carry} == NOT_CARRIED


def test_the_comms_rows_and_the_project_are_drawn_only_with_their_module():
    assert {row.name: row.shown_with for row in every_row() if row.shown_with} == {
        "identity": ("comms",), "scope": ("comms",), "director": ("comms",),
        "project": ("projects",)}
    assert {row.name: row.typed_with for row in every_row() if row.typed_with} == {
        "posture": "comms"}
    # Drawing a row only with a module is no launch rule: the row stays offered without it.
    for name in ("identity", "scope", "project", "posture"):
        assert catalog.launcher()[name].requires is None, name


# --- what the page server sends ---------------------------------------------------------------

def test_a_field_says_where_it_is_drawn_and_what_it_is_called():
    claude, codex = (catalog_answer()[harness] for harness in ("claude", "codex"))
    mode = claude["permission_mode"]
    assert (mode["group"], mode["sub"], mode["label"]) == ("access", "Mode", "Permission mode")
    assert mode["danger"] == ["bypassPermissions"] and mode["carry"] is True
    assert mode["modes"] == ["interactive", "print"]
    assert codex["sandbox"]["danger"] == ["danger-full-access"]
    assert codex["approval"]["danger"] == [] and codex["approval"]["modes"] == ["interactive"]
    assert codex["sandbox"]["modes"] == ["interactive", "exec", "host"]
    box = claude["brief"]
    assert (box["group"], box["sub"], box["label"], box["carry"]) == (None, None, "Brief", False)
    assert box["modes"] == [] and box["danger"] == []
    assert (claude["handoff"]["group"], claude["handoff"]["sub"]) == ("seat", None)
    for answer in (claude, codex):
        for name, sent in answer.items():
            assert sent["label"] == LABELS[name], name


def test_a_row_with_no_label_is_called_by_its_name():
    plain = catalog.parse_text(ROW.replace("[x]", "[some_row]") + META, harness="codex",
                               source="test.toml")["some_row"]
    assert pagehandlers.field(plain, lambda name: True, "codex")["label"] == "Some row"


def test_without_comms_or_projects_their_rows_are_not_shown_and_posture_is_not_typed():
    claude = catalog_answer()["claude"]
    for name in ("identity", "scope", "project"):
        assert claude[name]["needs_absent"] is False and claude[name]["shown"] is False, name
    assert claude["director"]["needs_absent"] is True and claude["director"]["shown"] is False
    posture = claude["posture"]
    assert (posture["shown"], posture["typed"]) == (True, False)
    assert posture["values"] == ["operator", "go"]
    # A row that needs no module is shown and typed; one whose module is absent is not shown.
    assert (claude["model"]["shown"], claude["model"]["typed"]) == (True, True)
    assert claude["keep_warm"]["needs_absent"] is True and claude["keep_warm"]["shown"] is False


def test_with_comms_its_rows_are_shown_and_posture_is_typed(monkeypatch):
    installed(monkeypatch, "comms")
    claude = catalog_answer()["claude"]
    for name in ("identity", "scope", "director"):
        assert claude[name]["shown"] is True, name
    assert claude["posture"]["typed"] is True
    assert claude["project"]["shown"] is False


def test_with_projects_the_project_row_is_shown(monkeypatch):
    installed(monkeypatch, "projects")
    claude = catalog_answer()["claude"]
    assert claude["project"]["shown"] is True
    assert claude["identity"]["shown"] is False and claude["posture"]["typed"] is False

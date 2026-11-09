"""statusline's doctor rows (the bytecode row's cases are with the install listener's)."""

from statusline_kit import write_config

from moxi.doctor import Row
from moxi_statusline import doctor


def test_rows_are_rows_in_order():
    rows = doctor.rows()
    assert all(isinstance(r, Row) and r.module == "statusline" for r in rows)
    assert [r.name for r in rows] == ["bytecode", "sample render", "show", "module names"]


def test_a_config_that_cant_be_read_is_one_warn_row_and_the_rest_read_the_default():
    write_config('show = ["row", "dir"]\n')
    rows = doctor.rows()
    assert rows[0].name == "config"
    assert rows[0].state == "warn"
    assert "'row' first" in rows[0].detail
    assert [r.name for r in rows[1:]] == ["bytecode", "sample render", "show",
                                          "module names"]


def test_the_sample_render_against_the_budget():
    row = doctor.sample_row(floor=10_000.0)
    assert row.state == "ok"
    assert "the budget is 30000.0 ms (3 times the 10000.0 ms start" in row.detail
    row = doctor.sample_row(floor=0.001)
    assert row.state == "warn"
    assert row.fix == "moxi statusline preview"


def test_the_sample_render_fails_when_the_command_is_not_installed(monkeypatch):
    from moxi_statusline import preview

    monkeypatch.setattr(preview, "SCRIPT", "moxi-statusline-nothing")
    row = doctor.sample_row(floor=10.0)
    assert row.state == "fail"
    assert "moxi-statusline-nothing is not installed" in row.detail


def test_show_items_with_nothing_behind_them(seen):
    seen.module("alpha", "def from_input(p, n):\n    return None\n")
    seen.module("beta")
    assert doctor.show_row(["alpha"]).state == "ok"
    row = doctor.show_row(["alpha", "beta", "nosuchmodule"])
    assert row.state == "warn"
    assert "beta (no status line segment)" in row.detail
    assert "nosuchmodule (not installed)" in row.detail
    assert "alpha" not in row.detail


def test_an_installed_module_named_like_a_base_field(seen):
    assert doctor.names_row().state == "ok"
    seen.module("branch", "def from_input(p, n):\n    return None\n")
    row = doctor.names_row()
    assert row.state == "warn"
    assert row.detail.endswith(": branch")

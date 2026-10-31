"""The launcher's doctor rows: one per harness, the `--help` diff's verdict."""

from __future__ import annotations

from moxi.doctor import Row

from moxi_launcher import catalog, doctor

from test_catalog import FakeHarness, expected_flags, help_text, matching


def rows_for(read) -> dict[str, Row]:
    return {row.name: row for row in doctor.rows(read=read)}


def test_one_row_per_harness_and_ok_when_the_help_matches():
    helps = {**matching("claude"), **matching("codex")}
    rows = rows_for(FakeHarness(helps, version="2.1.314"))
    assert set(rows) == {"catalog.claude", "catalog.codex"}
    assert all(row.module == "launcher" and row.state == "ok" for row in rows.values())
    assert rows["catalog.codex"].detail == ("the catalog matches codex --help and codex "
                                            "exec --help (codex 2.1.314)")


def test_a_removed_flag_fails_with_a_fix():
    helps = {**matching("claude"), **matching("codex")}
    helps[("claude", "--help")] = help_text(expected_flags("claude", "claude")
                                            - {"--permission-mode"})
    row = rows_for(FakeHarness(helps))["catalog.claude"]
    assert row.state == "fail"
    assert "claude --help no longer lists --permission-mode" in row.detail
    assert row.fix


def test_a_removed_flag_fails_though_the_other_help_could_not_be_read():
    helps = {**matching("claude"), **matching("codex")}
    helps[("codex", "--help")] = help_text(expected_flags("codex", "codex") - {"-m"})
    fake = FakeHarness(helps)

    def read(argv):
        if tuple(argv) == ("codex", "exec", "--help"):
            raise OSError("permission denied")
        return fake(argv)

    row = rows_for(read)["catalog.codex"]
    assert row.state == "fail" and row.fix
    assert "codex --help no longer lists -m" in row.detail
    assert "permission denied" in row.detail


def test_an_added_flag_warns_and_names_it():
    helps = {**matching("claude"), **matching("codex")}
    helps[("codex", "exec", "--help")] = help_text(expected_flags("codex", "exec")
                                                   | {"--new-one"})
    row = rows_for(FakeHarness(helps))["catalog.codex"]
    assert row.state == "warn"
    assert "codex exec --help lists flags the catalog does not know: --new-one" in row.detail


def test_many_flags_are_cut_to_a_count():
    new = {f"--new-{i:02d}" for i in range(12)}
    helps = {**matching("claude"), **matching("codex")}
    helps[("claude", "--help")] = help_text(expected_flags("claude", "claude") | new)
    row = rows_for(FakeHarness(helps))["catalog.claude"]
    assert row.detail.count("--new-") == doctor.SHOWN
    assert "and 4 more" in row.detail


def test_a_harness_not_on_path_is_off_and_one_that_cannot_run_warns():
    assert rows_for(FakeHarness({}, missing=True))["catalog.claude"].state == "off"
    row = rows_for(FakeHarness({}, fails=True))["catalog.codex"]
    assert row.state == "warn" and "could not run" in row.detail


def test_the_announcement_names_these_rows():
    from moxi_launcher import module

    assert module.DOCTOR == "moxi_launcher.doctor:rows"
    assert catalog.HARNESSES == ("claude", "codex")

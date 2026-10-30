"""The bump tool's parts that need neither the network nor Go, and the Go sampler where Go
is on the path. The tool is dev-only and not in the sdist; these tests skip without it."""

from __future__ import annotations

import datetime
import importlib.util
import shutil
import tomllib
from pathlib import Path

import pytest

from moxi_secrets import engine

TOOL = Path(__file__).resolve().parent.parent / "tools" / "bump_gitleaks.py"

if not TOOL.exists():
    pytest.skip("the bump tool is not in this tree (an sdist leaves it out)",
                allow_module_level=True)

_spec = importlib.util.spec_from_file_location("bump_gitleaks", TOOL)
bump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump)


def release_files(minors=range(10, 15), extra=()) -> list[str]:
    """Wheel file names shaped as google-re2 1.1.20251105's are."""
    names = []
    for m in minors:
        tag = f"cp3{m}-cp3{m}"
        for mac in (13, 14, 15):
            for arch in ("arm64", "x86_64"):
                names.append(f"google_re2-1.1-1-{tag}-macosx_{mac}_0_{arch}.whl")
        names.append(f"google_re2-1.1-1-{tag}-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl")
        names.append(f"google_re2-1.1-1-{tag}-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl")
        names.append(f"google_re2-1.1-1-{tag}-win32.whl")
        names.append(f"google_re2-1.1-1-{tag}-win_amd64.whl")
        if m >= 11:
            names.append(f"google_re2-1.1-1-{tag}-win_arm64.whl")
    return names + list(extra) + ["google_re2-1.1.tar.gz"]


# -- step 8: the wheel tags and re2_wheel_fits ----------------------------------------------

def test_wheel_tags_expand_compressed_tags_and_skip_the_sdist():
    tags = bump.wheel_tags([
        "google_re2-1.1-1-cp312-cp312-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl",
        "google_re2-1.1.tar.gz",
    ])
    assert tags == [("cp312", "cp312", "manylinux_2_26_aarch64"),
                    ("cp312", "cp312", "manylinux_2_28_aarch64")]


def test_the_check_agrees_with_the_release_it_was_written_from():
    tags = bump.wheel_tags(release_files())
    found = bump.wheel_check(tags, engine.re2_wheel_fits, engine.RE2_BOUND, bump.marker_bound())
    assert found["unsafe"] == []
    assert found["bound"] == engine.RE2_BOUND == bump.marker_bound()
    assert found["bound_ok"]
    # The check asks for glibc 2.28 where the wheels take 2.26 (aarch64) and 2.27
    # (x86_64): it refuses only there, never passes where no wheel fits.
    assert found["stricter"]
    assert all(" glibc 2.26 " in s or " glibc 2.27 " in s for s in found["stricter"])


def test_a_new_wheel_shows_as_a_refusal_and_a_raised_bound():
    tags = bump.wheel_tags(release_files(range(10, 16), extra=[
        "google_re2-1.1-1-cp312-cp312-musllinux_1_2_x86_64.whl",
        "google_re2-1.1-1-cp313-cp313t-manylinux_2_28_x86_64.whl",
    ]))
    found = bump.wheel_check(tags, engine.re2_wheel_fits, engine.RE2_BOUND, bump.marker_bound())
    assert found["bound"] == (3, 16)
    assert not found["bound_ok"]
    assert "CPython 3.12 musl x86_64 64-bit" in found["stricter"]
    assert "CPython 3.13 free-threaded glibc 2.39 x86_64 64-bit" in found["stricter"]
    assert "CPython 3.15 glibc 2.39 x86_64 64-bit" in found["stricter"]


def test_a_check_that_passes_without_a_wheel_is_unsafe():
    tags = bump.wheel_tags(release_files())
    found = bump.wheel_check(tags, lambda facts: True, engine.RE2_BOUND, bump.marker_bound())
    assert "CPython 3.12 musl x86_64 64-bit" in found["unsafe"]
    assert "CPython 3.12 glibc 2.17 x86_64 64-bit" in found["unsafe"]
    assert any(s.startswith("PyPy ") for s in found["unsafe"])


def test_wheel_covers_reads_each_platform_tag():
    linux = {"implementation": "CPython", "version": (3, 12), "gil_disabled": False,
             "system": "Linux", "machine": "x86_64", "bits": 64, "libc": "glibc 2.27",
             "mac": ""}
    assert bump.wheel_covers(("cp312", "cp312", "manylinux_2_27_x86_64"), linux)
    assert not bump.wheel_covers(("cp312", "cp312", "manylinux_2_28_x86_64"), linux)
    assert not bump.wheel_covers(("cp313", "cp313", "manylinux_2_27_x86_64"), linux)
    assert bump.wheel_covers(("cp311", "abi3", "manylinux2014_x86_64"), linux)
    assert not bump.wheel_covers(("cp312", "cp312", "musllinux_1_2_x86_64"), linux)
    mac = {**linux, "system": "Darwin", "machine": "arm64", "libc": None, "mac": "13.6"}
    assert bump.wheel_covers(("cp312", "cp312", "macosx_13_0_arm64"), mac)
    assert bump.wheel_covers(("cp312", "cp312", "macosx_11_0_universal2"), mac)
    assert not bump.wheel_covers(("cp312", "cp312", "macosx_14_0_arm64"), mac)
    win32 = {**linux, "system": "Windows", "machine": "AMD64", "bits": 32, "libc": None}
    assert bump.wheel_covers(("cp312", "cp312", "win32"), win32)
    assert not bump.wheel_covers(("cp312", "cp312", "win_amd64"), win32)


# -- steps 2 and 3: the pin and the rule changes --------------------------------------------

def test_the_pin_the_tool_writes_is_the_committed_layout():
    committed = (bump.RULES / "upstream" / "PIN").read_text("utf-8")
    data = tomllib.loads(committed)
    written = bump.pin_text(data["tag"], data["commit"], data["sha256"], data["fetched"])
    assert written == committed
    assert isinstance(data["fetched"], datetime.date)


def test_rule_changes_name_added_removed_and_changed_ids():
    def sets(rules):
        return {"sets": {"credentials": {"rules": rules}, "broad": {"rules": []}}}

    old = sets([{"id": "a", "x": 1}, {"id": "b", "x": 1}])
    new = sets([{"id": "b", "x": 2}, {"id": "c", "x": 1}])
    assert bump.rule_changes(old, new)["credentials"] == {
        "added": ["c"], "removed": ["a"], "changed": ["b"]}
    assert bump.rule_changes({}, new)["credentials"]["added"] == ["b", "c"]


# -- step 4: the comparison ----------------------------------------------------------------

def test_secret_span_takes_the_group_gitleaks_takes():
    whole, one, two = (0, 10), (2, 4), (5, 9)
    assert bump.secret_span([whole], 0) == whole
    assert bump.secret_span([whole, one, two], 0) == one
    assert bump.secret_span([whole, (-1, -1), two], 0) == two
    assert bump.secret_span([whole, (3, 3), (-1, -1)], 0) == whole
    assert bump.secret_span([whole, one, two], 2) == two
    assert bump.secret_span([whole, one], 3) == (-1, -1)


def test_inside_skips_absent_spans():
    assert bump.inside([(2, 3), (-1, -1)], [(0, 5)])
    assert not bump.inside([(4, 6)], [(0, 5)])
    assert bump.inside([], [])


def _comparison(pattern: str):
    module = engine.re2_module()
    if module is None:
        pytest.skip("google-re2 is not installed")
    from moxi_secrets._generate import translate

    pats = [{"id": "p", "owner": "some-rule", "kind": "allow", "group": 0,
             "pattern": pattern, "fallback": translate(pattern)}]
    return bump.Comparison(pats, engine.Re2Engine(module), engine.python(), None,
                           engine.Text)


def go_row(pattern: str, text: str) -> dict:
    """A row as the sampler writes it, here from RE2 (which equals Go on these)."""
    rx = engine.Re2Engine(engine.re2_module()).compile(pattern)
    data = text.encode()
    return {"id": "p", "kind": "plain", "text": text, "found": rx.search(data) is not None,
            "matches": bump.matches(rx, data, 0)}


def test_a_row_where_every_engine_agrees_fails_nothing():
    c = _comparison(r"k(e+)y")
    c.row(go_row(r"k(e+)y", "é key and keey"))
    assert not c.failures and c.rows["plain"] == 1


def test_a_row_where_go_differs_fails_both_engines_for_the_owner():
    c = _comparison(r"k(e+)y")
    row = go_row(r"k(e+)y", "a key")
    row["matches"] = [[2, 5, 2, 4]]
    c.row(row)
    assert set(c.failures["some-rule"]) == {"re2 allow plain", "fallback allow plain"}


def test_a_dotless_i_widens_the_fallback_within_the_allowance():
    c = _comparison("(?i)ki")
    c.row(go_row("(?i)ki", "kı"))
    assert not c.failures and c.allowed == 1


# -- step 5: the cost shapes ----------------------------------------------------------------

def test_the_cost_shapes_have_the_asked_size_and_repeat_from_the_seed():
    first = bump.shapes(["alpha", "beta", "alpha"], 50_000)
    assert [name for name, _ in first] == [name for name, _ in bump.shapes(["x"], 10)]
    assert len(first) == 8
    for name, text in first:
        assert 40_000 <= len(text) <= 50_000, name
    assert first == bump.shapes(["alpha", "beta", "alpha"], 50_000)


# -- the Go sampler -------------------------------------------------------------------------

@pytest.mark.slow
def test_the_sampler_agrees_with_both_engines_on_a_few_rules():
    if shutil.which("go") is None:
        pytest.skip("Go is not on the path")
    if engine.re2_module() is None:
        pytest.skip("google-re2 is not installed")
    import json

    data = json.loads((bump.RULES / "sets.json").read_bytes())
    rules = [r for r in data["sets"]["credentials"]["rules"]
             if r["id"] in ("github-pat", "kubernetes-secret-yaml")]
    small = {"allowlists": [], "sets": {"credentials": {"rules": rules},
                                        "broad": {"rules": []}}}
    pats = bump.patterns(small)
    c = bump.Comparison(pats, engine.Re2Engine(engine.re2_module()), engine.python(),
                        bump.scan_module().run_gitleaks, engine.Text)
    for row in bump.go_rows(pats, 3, 7):
        c.row(row)
    assert not c.failures
    assert c.rows["plain"] == 3 * len(pats)

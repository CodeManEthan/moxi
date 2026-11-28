"""The check lock's settings: `check_lock.at_once` and `check_lock.need_mib`, and
`memory_mib` in a project's `check` and in its `ci.local`, which fills `{memory_mib}` in
that table's own command lines."""

from __future__ import annotations

import pytest
from moxi import config

from moxi_rounds import checking, ci, settings


def refusal(text: str) -> config.ConfigError:
    with pytest.raises(config.ConfigError) as caught:
        settings.check_text(text)
    return caught.value


CI = ('ci = {{ workflows = ["ci.yml"], local = {{ command = "run-local {{memory_mib}}", '
      'timeout_s = 60{local} }} }}')


def project(check: str = "", local: str = "", *, queue: bool = False) -> str:
    scoped = ', scoped = "make part {base} {memory_mib}"' if queue else ""
    land = 'land = "queue"\n' if queue else ""
    return (f'[projects.proj-a]\n{land}check = {{ command = "make check {{memory_mib}}"'
            f'{scoped}, timeout_s = 60{check} }}\n' + CI.format(local=local) + "\n")


def test_the_check_lock_reads_one_at_a_time_and_the_sum_by_default(write_config):
    write_config("")
    read = settings.load()
    assert read["check_lock.at_once"] == 1
    assert read["check_lock.need_mib"] == 0


def test_the_check_lock_keys_read_as_set(write_config):
    write_config("[check_lock]\nat_once = 2\nneed_mib = 12000\n")
    read = settings.load()
    assert (read["check_lock.at_once"], read["check_lock.need_mib"]) == (2, 12000)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_at_once_below_one_is_refused(value):
    error = refusal(f"[check_lock]\nat_once = {value}\n")
    assert error.key == "check_lock.at_once"
    assert "at least 1" in error.problem


def test_at_once_that_is_not_whole_is_refused():
    error = refusal("[check_lock]\nat_once = 1.5\n")
    assert "check_lock.at_once" in str(error)


def test_need_mib_below_zero_is_refused():
    error = refusal("[check_lock]\nneed_mib = -1\n")
    assert error.key == "check_lock.need_mib"
    assert "at least 0" in error.problem


def test_need_mib_that_is_not_whole_is_refused():
    error = refusal("[check_lock]\nneed_mib = 1.5\n")
    assert "check_lock.need_mib" in str(error)


@pytest.mark.parametrize("value", ["63", "0", "-5", "100.5", "true", '"8128"'])
def test_a_checks_memory_mib_below_64_or_not_whole_is_refused(value):
    error = refusal(project(check=f", memory_mib = {value}"))
    assert error.key == "projects.proj-a.check.memory_mib"
    assert "at least 64" in error.problem


@pytest.mark.parametrize("value", ["63", "1.5"])
def test_ci_locals_memory_mib_below_64_or_not_whole_is_refused(value):
    error = refusal(project(local=f", memory_mib = {value}"))
    assert error.key == "projects.proj-a.ci.local.memory_mib"


def test_memory_mib_of_64_is_taken():
    settings.check_text(project(check=", memory_mib = 64", local=", memory_mib = 64"))


def test_memory_mib_fills_each_tables_own_command_lines(write_config):
    write_config(project(check=", memory_mib = 8128", local=", memory_mib = 2048",
                         queue=True))
    read = settings.load()
    assert checking.declared(read, "proj-a") == ["make", "check", "8128"]
    assert checking.scoped(read, "proj-a") == ["make", "part", "{base}", "8128"]
    found = ci.declared(read, "proj-a")
    assert found.local == ("run-local", "2048")
    assert found.local_memory_mib == 2048


def test_with_no_memory_mib_the_placeholder_is_left_as_written(write_config):
    write_config(project(queue=True))
    read = settings.load()
    assert checking.declared(read, "proj-a") == ["make", "check", "{memory_mib}"]
    assert checking.scoped(read, "proj-a") == ["make", "part", "{base}", "{memory_mib}"]
    found = ci.declared(read, "proj-a")
    assert found.local == ("run-local", "{memory_mib}")
    assert found.local_memory_mib is None


def test_the_fill_keeps_the_other_placeholders_for_the_build():
    words = settings.fill_memory(["x", "{memory_mib}", "{tip}"], {"memory_mib": 100})
    assert checking.fill(words, {"tip": "abc"}) == ["x", "100", "abc"]

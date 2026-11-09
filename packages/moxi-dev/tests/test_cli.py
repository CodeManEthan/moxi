"""The `moxi-dev` command reads the verb and hands the rest to that verb's module."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from moxi_dev import check, cli, contract, new, rename, stamp


def test_help_lists_every_verb():
    exe = shutil.which("moxi-dev")
    assert exe, "moxi-dev is not installed in this environment"
    proc = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert all(verb in proc.stdout for verb in ("check", "contract", "new", "rename",
                                                "stamp"))


@pytest.mark.parametrize("verb, module", [("check", check), ("contract", contract),
                                          ("new", new), ("rename", rename),
                                          ("stamp", stamp)])
def test_each_verb_gets_the_arguments_after_it(monkeypatch, verb, module):
    seen = []
    monkeypatch.setattr(module, "main", lambda argv: seen.append(argv) or 7)
    assert cli.main([verb, "a", "--b"]) == 7
    assert seen == [["a", "--b"]]


def test_no_verb_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main([])
    assert stop.value.code == 2


def test_contract_checks_a_module_directory_from_the_shell(tmp_path):
    exe = shutil.which("moxi-dev")
    assert exe, "moxi-dev is not installed in this environment"
    proc = subprocess.run([exe, "contract", "--staged", str(tmp_path)], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 2
    assert proc.stderr.startswith("moxi-dev contract: ")

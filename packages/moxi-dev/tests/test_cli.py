"""The `moxi-dev` command reads the verb and hands the rest to that verb's module."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from moxi_dev import check, cli, new


def test_help_lists_both_verbs():
    exe = shutil.which("moxi-dev")
    assert exe, "moxi-dev is not installed in this environment"
    proc = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "check" in proc.stdout and "new" in proc.stdout


@pytest.mark.parametrize("verb, module", [("check", check), ("new", new)])
def test_each_verb_gets_the_arguments_after_it(monkeypatch, verb, module):
    seen = []
    monkeypatch.setattr(module, "main", lambda argv: seen.append(argv) or 7)
    assert cli.main([verb, "a", "--b"]) == 7
    assert seen == [["a", "--b"]]


def test_no_verb_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main([])
    assert stop.value.code == 2
